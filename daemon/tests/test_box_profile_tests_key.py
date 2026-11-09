"""Klucz `tests` w schemacie profilu pudełka K-38 (PT-321, zgodność K-69 z K-38).

Profil `projects/<slug>/box.yaml` czytają i zapisują dwie strony: K-38 (`box/profile.py`, `yamlmini`) i kontrakt
testów K-69 (`BoardStore`, `board/project_tests.py`). Testy sprawdzają, że zapis K-38 nie gubi ani nie zmienia
kontraktu i jego zatwierdzenia, że błąd bloku `tests` nie unieważnia profilu, że sam kontrakt nie udaje
zatwierdzonego profilu K-38 oraz że obie strony serializują się wspólną blokadą Tablicy.
"""
import itertools
import json
import os
import threading
import unittest
from unittest import mock

from tide_daemon.board import project_tests
from tide_daemon.box import profile as bp
from tide_daemon.box import yamlmini
from tide_daemon.box.profile import ProfileError
from tide_daemon.box.settings import BoxSettings

from . import test_cardflow_environment as tce
from .test_board_core import BoardCase

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "box-profile", "box.yaml")
CONTRACT = {"changed": "make test-changed BASE={base}", "full": "make test"}
OTHER = {"changed": "make test-changed BASE={base} JOBS=2", "full": "make test"}
K38 = "tide-box: 1\napt:\n  - build-essential\nverify: [\"make --version\"]\n"
# Uszkodzone bloki: wiele linii (ucieczka \n w napisie JSON), changed bez {base}, nieznany parametr.
DAMAGED = {
    "multiline": ('tests:\n  full: "make\\ntest"\n', {"full": "make\ntest"}),
    "no-base": ('tests:\n  changed: "make test-changed"\n', {"changed": "make test-changed"}),
    "unknown-param": ('tests:\n  changed: "make c {base} {other}"\n', {"changed": "make c {base} {other}"}),
}
# Regresje generowanych poleceń z test_board_project_tests.py: końcowe ukośniki, cytowanie, komentarz poza napisem.
FRAGMENTS = ('"', "'", "\\", "\\\\", "\\\\\\", "#", "  ", "żółć 日本 🐚")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def regression_commands():
    for left, right in itertools.product(FRAGMENTS, repeat=2):
        # Brzegi polecenia przycina validate_tests; odstępy wewnątrz są danymi.
        yield ('pytest -k "  ' + left + right + '  " path' + left + right).strip()
    for count in range(1, 5):
        for suffix in ("", '" # wewnątrz napisu'):
            yield "pytest path" + "\\" * count + suffix


class SchemaTest(unittest.TestCase):
    """Schemat bez Tablicy: klucz, hash przepisu, karta, scalenie, delegacja reguły."""

    def test_fixture_reads_with_tests(self):
        got = bp.parse_project_file(read(FIXTURE), "plasma-tide")
        self.assertEqual(got.tests_error, "")
        self.assertEqual(got.profile["tests"], CONTRACT)
        self.assertEqual(got.profile["apt"], ["build-essential", "python3-pytest"])
        self.assertEqual(got.tests, project_tests.load_tests(read(FIXTURE))[0])
        self.assertFalse(got.is_new)

    def test_recipe_hash_ignores_tests(self):
        base = bp.parse_project_file(read(FIXTURE)).profile
        for tests in ({"changed": "make c {base}", "full": ""}, {"changed": "", "full": "pytest"}, None):
            with self.subTest(tests=tests):
                other = {k: v for k, v in base.items() if k != "tests"}
                if tests is not None:
                    other["tests"] = tests
                self.assertEqual(bp.recipe_stamp(other, "t", "a"), bp.recipe_stamp(base, "t", "a"))
                self.assertEqual(bp.diff(other, bp.recipe_stamp(base, "t", "a"), "t", "a"), [])

    def test_card_scope_rejects_tests(self):
        with self.assertRaises(ProfileError) as ctx:
            bp.validate({"tide-box": 1, "tests": {"full": "make test"}}, "card")
        self.assertIn("tests: nieznany klucz", str(ctx.exception))
        with self.assertRaises(ProfileError):
            bp.parse('tide-box: 1\ntests:\n  full: "make test"\n', "card")

    def test_merge_carries_project_tests_unchanged(self):
        project = bp.validate({"tide-box": 1, "apt": ["git"], "tests": CONTRACT}, "project")
        merged = bp.merge(project, {"tide-box": 1, "apt": ["curl"]}).profile
        self.assertEqual(merged["tests"], CONTRACT)
        self.assertEqual(merged["apt"], ["git", "curl"])

    def test_validation_delegates_to_validate_tests(self):
        for name, (_block, data) in DAMAGED.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as rule:
                    project_tests.validate_tests(data)
                with self.assertRaises(ProfileError) as ctx:
                    bp.validate({"tide-box": 1, "tests": data}, "project")
                self.assertEqual(ctx.exception.errors, [str(rule.exception)])

    def test_damaged_block_keeps_k38_profile_and_reports_tests_error(self):
        for name, (block, data) in DAMAGED.items():
            with self.subTest(name=name):
                text = K38 + block
                got = bp.parse_project_file(text)
                with self.assertRaises(ValueError) as rule:
                    project_tests.validate_tests(data)
                self.assertEqual(got.tests_error, project_tests.load_tests(text)[1])
                self.assertIn(str(rule.exception), got.tests_error)
                self.assertNotIn("tests", got.profile)
                self.assertEqual(got.profile["apt"], ["build-essential"])
                self.assertEqual(got.tests_block, block)

    def test_tests_only_profile_is_new_for_k38(self):
        for text in ('tide-box: 1\ntests:\n  full: "make test"\n', "tide-box: 1\nproject: app\n", "tide-box: 1\n"):
            with self.subTest(text=text):
                self.assertTrue(bp.parse_project_file(text).is_new)
        self.assertFalse(bp.parse_project_file("tide-box: 1\nbox: per-project\n").is_new)

    def test_yamlmini_reads_every_generated_contract(self):
        for command in regression_commands():
            for comment in ("", "  # komentarz poza napisem: \\\" ' #"):
                with self.subTest(command=command, comment=comment):
                    text = project_tests.save_tests(K38, {"full": command}).rstrip("\n") + comment + "\n"
                    self.assertEqual(yamlmini.loads(text)["tests"], {"full": command})
                    got = bp.parse_project_file(text)
                    self.assertEqual((got.profile["tests"]["full"], got.tests_error), (command, ""))

    def test_yamlmini_single_quotes_and_hash(self):
        for command in ("it's # ok", 'pytest -k "it\'s # case"', "it's '' # ok", "pytest path\\"):
            with self.subTest(command=command):
                scalar = "'" + command.replace("'", "''") + "'"
                self.assertEqual(yamlmini.loads(f"full: {scalar}  # komentarz\n"), {"full": command})


class BoardProfileCase(BoardCase):
    """Tablica z gitem; K-38 i BoardStore piszą ten sam katalog (`TIDE_BOARD_DIR` = korzeń Tablicy)."""

    def setUp(self):
        super().setUp()
        self.state = os.path.join(self.tmp, "state")
        patcher = mock.patch.dict(os.environ, {"TIDE_BOARD_DIR": self.root, "XDG_STATE_HOME": self.state})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.approvals = os.path.join(self.state, "tide", "test-contracts.json")
        self.store.upsert_project({"slug": "app", "name": "App"})

    def box(self):
        return os.path.join(self.root, "projects", "app", "box.yaml")

    def project(self):
        return {p["slug"]: p for p in self.store.snapshot()["projects"]}["app"]

    def approve(self, tests):
        result = self.store.approve_project_tests({"slug": "app", "tests": tests})
        self.assertTrue(result["testsApproved"], result)
        return result

    def k38_update(self, **changes):
        profile = bp.load_project("app") or {"tide-box": 1}
        return bp.save_project("app", {**profile, **changes})

    def assert_contract(self, tests, approved=True):
        self.assertEqual(project_tests.load_tests(read(self.box())), ({**project_tests.empty(), **tests}, ""))
        project = self.project()
        self.assertEqual((project["tests"], project["testsError"]), ({**project_tests.empty(), **tests}, ""))
        self.assertEqual(project["testsApproved"], approved)


class SaveProjectTest(BoardProfileCase):
    def test_fixture_load_and_k38_round_trip_keep_contract_and_approval(self):
        self.write("projects/app/box.yaml", read(FIXTURE).replace("plasma-tide", "app"))
        self.approve(CONTRACT)
        loaded = bp.load_project("app")
        self.assertEqual(loaded["tests"], CONTRACT)
        block = project_tests.split_block(read(self.box()))[1]
        stored = read_bytes(self.approvals)
        bp.save_project("app", {**loaded, "apt": ["git"], "tests": OTHER})   # `tests` z wejścia jest pomijany
        text = read(self.box())
        self.assertEqual(project_tests.split_block(text)[1], block)
        self.assertNotIn("testsApproved", text)
        self.assertEqual(read_bytes(self.approvals), stored)
        self.assertEqual(bp.load_project("app")["apt"], ["git"])
        self.assert_contract(CONTRACT)
        self.approve(OTHER)                                                  # save_tests po zapisie K-38
        self.assert_contract(OTHER)
        self.assertEqual(bp.load_project("app")["apt"], ["git"])

    def test_unapproved_contract_stays_unapproved(self):
        self.write("projects/app/box.yaml", K38)
        self.store.upsert_project({"slug": "app", "tests": CONTRACT})
        self.assert_contract(CONTRACT, approved=False)
        self.k38_update(apt=["curl"])
        self.assert_contract(CONTRACT, approved=False)
        self.assertFalse(os.path.exists(self.approvals))

    def test_damaged_block_is_kept_byte_for_byte(self):
        for name, (block, _data) in DAMAGED.items():
            with self.subTest(name=name):
                commented = block.replace("tests:\n", "tests:   # ręczna edycja\n  # komentarz w bloku\n")
                self.write("projects/app/box.yaml", K38 + "# poza blokiem\n" + commented)
                self.k38_update(apt=["git"])
                self.assertEqual(project_tests.split_block(read(self.box()))[1], commented)
                got = bp.read_project("app")
                self.assertEqual(got.profile["apt"], ["git"])
                self.assertTrue(got.tests_error)

    def test_unreadable_file_is_never_overwritten(self):
        for text in (K38 + "apt: [\n" + 'tests:\n  full: "make test"\n',        # zły YAML poza blokiem
                     K38.replace("apt:", "nieznany: 1\napt:") + 'tests:\n  full: "make test"\n',
                     "tide-box: 1\nproject: inny\n",
                     "  tide-box: 1\n  tests:\n    full: make\n"):             # tests poza blokiem K-69
            with self.subTest(text=text):
                self.write("projects/app/box.yaml", text)
                before = read_bytes(self.box())
                with self.assertRaises(ProfileError) as ctx:
                    bp.save_project("app", {"tide-box": 1, "apt": ["git"]})
                self.assertIn("zapis odrzucony", str(ctx.exception))
                self.assertEqual(read_bytes(self.box()), before)

    def test_empty_k38_approval_is_not_new_and_keeps_tests_only_block(self):
        self.store.upsert_project({"slug": "app", "tests": CONTRACT})
        self.assertIsNone(bp.load_project("app"))
        self.assertTrue(bp.read_project("app").is_new)
        bp.save_project("app", {"tide-box": 1})
        self.assertEqual(bp.load_project("app")["box"], "per-project")
        self.assert_contract(CONTRACT, approved=False)

    def test_regressions_survive_k38_write_and_partial_board_update(self):
        for command in regression_commands():
            with self.subTest(command=command):
                self.write("projects/app/box.yaml", K38)
                self.approve({"changed": "", "full": command})
                self.k38_update(apt=["git"])
                self.assertEqual(bp.read_project("app").tests_error, "")
                self.assertEqual(bp.load_project("app")["tests"]["full"], command)
                self.assert_contract({"full": command})
                self.approve({"changed": CONTRACT["changed"]})               # częściowa aktualizacja Tablicy
                self.k38_update(apt=["curl"])
                expected = {"changed": CONTRACT["changed"], "full": command}
                self.assertEqual(bp.load_project("app")["tests"], expected)
                self.assert_contract(expected)


class InterleavingTest(BoardProfileCase):
    """Obie kolejności przeplotu zapisu K-38 i zapisu kontraktu: wspólna blokada Tablicy, świeży odczyt pod nią."""

    def setUp(self):
        super().setUp()
        self.write("projects/app/box.yaml", K38)
        self.approve(CONTRACT)

    def test_k38_first_then_contract(self):
        stale = bp.load_project("app")
        entered, gate = threading.Event(), threading.Event()
        real = bp._write_atomic
        stored = read_bytes(self.approvals)

        def held_write(path, text):
            entered.set()
            self.assertTrue(gate.wait(10))
            real(path, text)

        with mock.patch.object(bp, "_write_atomic", held_write):
            k38 = threading.Thread(target=bp.save_project, args=("app", {**stale, "apt": ["git"]}))
            k38.start()
            self.assertTrue(entered.wait(10))
            self.assertEqual(read_bytes(self.approvals), stored)
            board = threading.Thread(target=self.store.approve_project_tests, args=({"slug": "app", "tests": OTHER},))
            board.start()
            board.join(0.3)
            self.assertTrue(board.is_alive(), "zapis kontraktu musi czekać na blokadę zapisu K-38")
            gate.set()
            k38.join(10)
            board.join(10)
        self.assertEqual(bp.load_project("app")["apt"], ["git"])
        self.assert_contract(OTHER)

    def test_contract_first_then_k38(self):
        stale = bp.load_project("app")                     # profil przeczytany przed zmianą kontraktu
        with self.store.transaction():
            k38 = threading.Thread(target=bp.save_project, args=("app", {**stale, "apt": ["git"]}))
            k38.start()
            self.approve(OTHER)                             # wielowejściowo w tej samej transakcji
            k38.join(0.3)
            self.assertTrue(k38.is_alive(), "zapis K-38 musi czekać na transakcję Tablicy")
            stored = read_bytes(self.approvals)
        k38.join(10)
        self.assertEqual(read_bytes(self.approvals), stored)
        self.assertEqual(bp.load_project("app")["apt"], ["git"])
        self.assertEqual(bp.load_project("app")["tests"], OTHER)
        self.assert_contract(OTHER)

    def test_k38_write_inside_board_transaction_does_not_deadlock(self):
        with self.store.transaction():
            self.k38_update(apt=["git"])
        self.assert_contract(CONTRACT)


class EnvironmentConsentTest(tce.EnvBase):
    """Zatwierdzenie środowiska karty (envconsent) na Tablicy, która jest też Tablicą pudełek."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"TIDE_BOARD_DIR": self.root})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.flow.stop()
        self.launched = []
        self.engine.box_launcher = lambda slug, profile, action: self.launched.append((slug, profile, action))
        self.engine.approve_plan = lambda card: {"ok": True, "id": card}
        self.card_id = self.card(description="Dodaj eksport.")
        journal = self.engine.journal
        journal.append(self.card_id, "card.started", entry="plan", executor="Sonnet", program="claude",
                       provider="anthropic")
        journal.append(self.card_id, "plan.proposed", title="Plan", prefix="PT", planner="Opus", box=None, rows=[
            {"workId": "#1", "title": "A", "blockers": [], "executor": "Sonnet", "reviewer": "Opus",
             "class": "standard", "note": ""}])
        journal.append(self.card_id, "plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])

    def box(self):
        return os.path.join(self.root, "projects", tce.SLUG, "box.yaml")

    def section(self):
        return self.engine.derived({"id": self.card_id, "column": "todo"})["flow"]["plan"]["environment"]

    def project(self):
        return {p["slug"]: p for p in self.store.snapshot()["projects"]}[tce.SLUG]

    def approve_contract(self, tests=CONTRACT):
        result = self.store.approve_project_tests({"slug": tce.SLUG, "tests": tests})
        self.assertTrue(result["testsApproved"], result)

    def test_tests_only_project_gets_proposal_like_project_without_profile(self):
        expected = self.section()
        self.approve_contract()
        block = project_tests.split_block(read(self.box()))[1]
        section = self.section()
        self.assertTrue(section["newProject"])
        self.assertEqual([i["id"] for i in section["items"]], [i["id"] for i in expected["items"]])
        self.assertEqual(section["question"], expected["question"])
        reply = self.engine.approve_environment(self.card_id, "tide-box: 1\n", False)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(project_tests.split_block(read(self.box()))[1], block)
        self.assertIsNotNone(bp.load_project(tce.SLUG))
        self.assertFalse(self.section()["newProject"])
        project = self.project()
        self.assertEqual((project["tests"], project["testsApproved"]), (CONTRACT, True))

    def test_enabled_and_disabled_approval_keep_approved_contract(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                self.engine.box_settings_source = lambda enabled=enabled: BoxSettings(enabled=enabled)
                bp.save_project(tce.SLUG, {"tide-box": 1, "network": {"domains": ["api.example.org"]}})
                self.approve_contract()
                before = read(self.box())
                reply = self.engine.approve_environment(self.card_id, "tide-box: 1\n", False)
                self.assertTrue(reply["ok"], reply)
                self.assertEqual(read(self.box()), before)
                project = self.project()
                self.assertEqual((project["tests"], project["testsApproved"]), (CONTRACT, True))

    def test_damaged_tests_and_approval_change_nothing_in_profile(self):
        bp.save_project(tce.SLUG, {"tide-box": 1, "network": {"domains": ["api.example.org"]}})
        with open(self.box(), "a", encoding="utf-8") as fh:
            fh.write(DAMAGED["no-base"][0])
        before = read_bytes(self.box())
        section = self.section()
        self.assertFalse(section["newProject"])
        self.assertTrue(any("testsError" in note for note in section["notes"]), section["notes"])
        reply = self.engine.approve_environment(self.card_id, "tide-box: 1\n", False)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(read_bytes(self.box()), before)

    def test_unreadable_profile_is_refused_without_overwrite(self):
        os.makedirs(os.path.dirname(self.box()), exist_ok=True)
        with open(self.box(), "w", encoding="utf-8") as fh:
            fh.write('tide-box: 1\napt: [\ntests:\n  full: "make test"\n')
        before = read_bytes(self.box())
        reply = self.engine.approve_environment(self.card_id, "tide-box: 1\n", False)
        self.assertFalse(reply["ok"], reply)
        self.assertIn("plik bez zmian", reply["message"])
        self.assertEqual(read_bytes(self.box()), before)


if __name__ == "__main__":
    unittest.main()
