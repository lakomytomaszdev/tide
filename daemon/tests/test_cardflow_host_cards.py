"""PT-291 (K-38 F3): kartki hosta zgodne z przypiętym master po wąskiej normalizacji treści.

Wzorce `fixtures/host-cards-master/` powstały z modułów master (`hostcards.MASTER_SHA`, odczyt z obiektów gita)
dla danych z `hostcards.inputs`; test ponownie generuje je z master, więc nie są kopią wyniku K-38. Kartki
HostRunnera ticket/fix/review/close dla trzech klas projektów z F1 muszą być równe master po normalizacji
i zamkniętej liście zatwierdzonych różnic (`hostcards.APPROVED`). Dodatki pudełka (tide-test, artefakty,
ograniczenie outside) zależą wyłącznie od zapisanego runnera kroku — sprawdzane też w silniku na atrapach.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tide_daemon.box import refguard
from tide_daemon.box import runner as br
from tide_daemon.cardflow import boxstep, handoff

from . import hostcards
from .boxflow import SLUG, BoxFlow
from .privatebus import wait_until

BOX_MARKERS = ("tide-test", "Artefakty kroku wykonawcy", "świeży artefakt = dowód", "Ograniczenie pudełka",
               "W pudełku wolno", "Testy w pudełku")
HOST_OUTSIDE = '{"result":"outside","command":"jedno pełne polecenie"'


class CardsCase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="pt291-cards-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        hostcards.make_worktrees(self.root)

    def k38(self) -> dict:
        return {name: hostcards.normalize(text, self.root) for name, text in hostcards.render(handoff, self.root).items()}


class MasterProvenanceTest(CardsCase):
    def test_provenance_pins_master_sha_and_module_blobs(self):
        with open(hostcards.PROVENANCE, encoding="utf-8") as fh:
            provenance = json.load(fh)
        self.assertEqual(provenance["masterSha"], hostcards.MASTER_SHA)
        self.assertEqual(provenance["blobs"], hostcards.blob_ids())
        self.assertEqual(sorted(provenance["cards"]), sorted(hostcards.card_names()))
        # Wzorzec jest commitem master zintegrowanym z gałęzią K-38 (przodek bieżącego HEAD).
        hostcards.git("merge-base", "--is-ancestor", hostcards.MASTER_SHA, "HEAD")

    def test_fixtures_are_fresh_master_output_not_k38_copies(self):
        fresh = hostcards.master_cards(self.root)
        self.assertEqual(sorted(fresh), sorted(hostcards.card_names()))
        for name in hostcards.card_names():
            with self.subTest(name=name):
                self.assertEqual(hostcards.read_fixture(name), fresh[name])
        k38 = self.k38()
        # Co najmniej kartki z zatwierdzonymi różnicami są inne niż wynik gałęzi — wzorzec nie jest jego kopią;
        # różnią się wyłącznie kartki objęte zatwierdzoną różnicą (K-38: ticket/review, K-69: linia źródła).
        differing = [n for n in hostcards.card_names() if k38[n] != fresh[n]]
        self.assertTrue(differing)
        self.assertTrue(all(hostcards.approved_for(n) for n in differing), differing)

    def test_master_module_is_read_from_git_objects(self):
        with tempfile.TemporaryDirectory() as target:
            module = hostcards.load_master(target)
            self.assertTrue(module.__file__.startswith(target))
            self.assertIsNot(module, handoff)
            self.assertFalse(hasattr(module, "runner_policy"))       # to naprawdę master, nie bieżący kod


class HostCardsMatchMasterTest(CardsCase):
    def test_host_cards_match_master_after_normalization(self):
        k38 = self.k38()
        for name in hostcards.card_names():
            with self.subTest(name=name):
                self.assertEqual(hostcards.mismatch(name, hostcards.read_fixture(name), k38[name]), "")

    def test_host_cards_keep_master_test_and_outside_contract(self):
        k38 = self.k38()
        for name in hostcards.card_names():
            with self.subTest(name=name):
                text = k38[name]
                self.assertIn(HOST_OUTSIDE, text)
                self.assertIn("Przed poleceniem spoza listy dozwolonych zapisz wynik `denied`", text)
                for marker in BOX_MARKERS:
                    self.assertNotIn(marker, text)
        self.assertIn("make test-changed BASE=0123456789abcdef0123456789abcdef01234567", k38["plasma-tide/ticket"])
        self.assertIn("przed oddaniem uruchom raz pełny `make test`", k38["bez-profilu/ticket"])
        self.assertIn("Przed napisaniem podsumowania uruchom raz pełny `make test`", k38["proj/close"])

    def test_host_policy_is_master_text_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as target:
            master = hostcards.load_master(target)
        for project, spec in hostcards.PROJECTS.items():
            for step_dir in ("", os.path.join(self.root, "wt", project) + ".step"):
                with self.subTest(project=project, step_dir=step_dir):
                    # Jedyna zatwierdzona różnica polityki: zdanie o `exclusive` (PT-376, hostcards.POLICY_APPROVED).
                    want = hostcards.expected_policy(
                        master.command_policy(spec["allowed"], spec["denied"], step_dir=step_dir))
                    self.assertEqual(handoff.command_policy(spec["allowed"], spec["denied"], step_dir=step_dir), want)
                    # Host także przy profilu pudełka (enabled=true, projekt z profilem, krok na hoście).
                    self.assertEqual(handoff.runner_policy(handoff.HOST_RUNNER, spec["allowed"], spec["denied"],
                                                           step_dir=step_dir,
                                                           profile={"verify": ["make test"]}), want)


class RunnerSectionsTest(unittest.TestCase):
    def test_box_policy_has_wrapper_verify_and_outside_restriction(self):
        text = handoff.runner_policy(handoff.BOX_RUNNER, ["npm test"], ["npm publish"], step_dir="/s",
                                     profile={"verify": ["./check --quick"]})
        for part in ("W pudełku wolno opakować", "- tide-test sh -c './check --quick'", "Ograniczenie pudełka",
                     "nie ma wyniku `outside`", "zgłoś polecenie (`denied`)"):
            self.assertIn(part, text)
        self.assertNotIn(HOST_OUTSIDE, text)
        self.assertNotIn(handoff.HOST_BOUNDARY, text)

    def test_unknown_runner_is_rejected(self):
        with self.assertRaises(ValueError):
            handoff.runner_policy("", [], [])
        with self.assertRaises(ValueError):
            handoff.review_evidence("bwrap", {"runner": "box", "stepDir": "/x"})

    def test_review_evidence_only_for_box_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "step", "out", "tests")
            os.makedirs(out)
            with open(os.path.join(out, "1.log"), "w") as fh:
                fh.write("4 passed\n")
            source = {"runner": "box", "stepDir": os.path.join(tmp, "step")}
            self.assertEqual(handoff.review_evidence(handoff.HOST_RUNNER, source), "")
            self.assertIn("świeży artefakt = dowód", handoff.review_evidence(handoff.BOX_RUNNER, source))
            # Review w pudełku po autorze na hoście: autor nie ma artefaktów pudełka.
            self.assertEqual(handoff.review_evidence(handoff.BOX_RUNNER, {"stepDir": source["stepDir"]}), "")


class NormalizerSensitivityTest(CardsCase):
    """Normalizacja usuwa tylko nazwane różnice; zmiana treści (polecenia, outside, uprawnienia) zawsze pada."""

    def setUp(self):
        super().setUp()
        self.cards = self.k38()

    def fails(self, name, text):
        self.assertNotEqual(hostcards.mismatch(name, hostcards.read_fixture(name),
                                               hostcards.normalize(text, self.root)), "")

    def test_only_line_endings_whitespace_and_tmp_root_are_normalized(self):
        name = "plasma-tide/ticket"
        raw = hostcards.render(handoff, self.root)[name]
        noisy = "\r\n".join(line + " \t" for line in raw.split("\n")) + "\n\n"
        self.assertEqual(hostcards.mismatch(name, hostcards.read_fixture(name),
                                            hostcards.normalize(noisy, self.root)), "")
        self.assertEqual([n for n, _ in hostcards.VARIABLES], ["tmp-root", "tide-resources"])
        # Inny katalog niż katalog tymczasowy przebiegu nie jest zmienną daną.
        self.fails(name, raw.replace(self.root, "/home/inny"))

    def test_changed_test_command_fails(self):
        for name, old, new in (
                ("plasma-tide/ticket", "make test-changed BASE=0123456789abcdef", "make test BASE=0123456789abcdef"),
                ("plasma-tide/review", "/usr/bin/python3 tools/test_runner.py --modules", "make test"),
                ("bez-profilu/fix", "przed oddaniem uruchom raz pełny `make test`", "przed oddaniem uruchom `make lint`"),
                ("proj/close", "uruchom raz pełny `make test`", "uruchom `npm test`")):
            with self.subTest(name=name):
                text = hostcards.render(handoff, self.root)[name]
                self.assertIn(old, text)
                self.fails(name, text.replace(old, new, 1))

    def test_changed_outside_instruction_fails(self):
        for name in hostcards.card_names():
            with self.subTest(name=name):
                text = hostcards.render(handoff, self.root)[name]
                self.fails(name, text.replace('"result":"outside"', '"result":"denied"', 1))
                self.fails(name, text.replace(handoff.HOST_OUTSIDE, handoff.BOX_OUTSIDE, 1))
                self.fails(name, text.replace("(`denied` albo `outside`)", "(`denied`)", 1))

    def test_changed_permissions_or_dropped_paragraph_fails(self):
        name = "plasma-tide/fix"
        text = hostcards.render(handoff, self.root)[name]
        self.fails(name, text.replace("- make install\n", "", 1))
        self.fails(name, text.replace("- make test\n", "- make test\n- make install\n", 1))
        self.fails(name, text.replace("Polecenia zabronionego nie uruchamiaj i nie proś o zgodę na nie.", "", 1))
        self.fails(name, text + handoff.BOX_WRAPPED)

    def test_approved_difference_is_exact(self):
        name = "proj/review"
        text = hostcards.render(handoff, self.root)[name]
        desk = "- " + hostcards.DESK + "\n"
        self.assertIn(desk, text)
        self.fails(name, text.replace(desk, "", 1))                       # brak zatwierdzonej linii
        self.fails(name, text.replace(desk, desk + desk, 1))              # podwojona
        self.fails(name, text.replace(desk, desk.replace("pulpitu", "ekranu"), 1))
        with self.assertRaises(AssertionError):
            hostcards.expected(name, "kartka bez fragmentu master")


class HostReviewAfterBoxAuthorTest(BoxFlow):
    """Silnik: review i poprawka na hoście po autorze z pudełka (pudełko wyłączone w trakcie, partia 1)
    dostają kartkę hosta — bez spisu artefaktów pudełka i bez instrukcji tide-test."""

    def of_kind(self, card_id, kind):
        return next(s for s in self.of(card_id, "step.started") if s["kind"] == kind)

    def test_host_review_and_fix_after_box_author_get_host_cards(self):
        self.build_box({"tide-box": 1, "verify": ["./custom-check --quick"]})
        changed = json.loads(self.board.upsert_project(json.dumps({
            "slug": SLUG, "allowedCommands": ["npm test"], "deniedCommands": ["npm publish"]})))
        self.assertTrue(changed["ok"])
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: review-requests 1")
        configure = boxstep.configure_background
        prepared = []

        def prepare(*args, **kwargs):
            configure(*args, **kwargs)
            if prepared:
                return
            tests = os.path.join(args[-1].step_dir, "out", "tests")
            os.makedirs(tests, exist_ok=True)
            for name, content in (("1.log", "4 passed\n"), ("summary.json", "[]")):
                with open(os.path.join(tests, name), "w") as fh:
                    fh.write(content)
            prepared.append(tests)
            self.box_enabled = False          # właściciel wyłącza pudełka; kolejne kroki idą na hosta

        def clean_tip(repo, branch, path, *, untracked=True):
            return self.engine.git.sha(repo, f"refs/heads/{branch}")

        # Odczyt `extensions.worktreeConfig` i strażnik refów (PT-170/PT-266) wołają git w bwrap, którego
        # piaskownica kroku nie tworzy. Ten test dotyczy wyłącznie treści kartek: obie bariery mają własne testy,
        # tu są atrapami (rozszerzenie „wyłączone”, strażnik bez migawki i bez naprawy).
        with mock.patch.object(boxstep, "configure_background", side_effect=prepare), \
                mock.patch.object(br, "worktree_config_enabled", return_value=False), \
                mock.patch.object(refguard, "begin", return_value=None), \
                mock.patch.object(refguard, "cancel", return_value=None), \
                mock.patch.object(refguard, "finish", return_value={}), \
                mock.patch.object(self.engine.git, "clean_tip", side_effect=clean_tip):
            reply = self.start(card_id)
            self.assertTrue(reply["ok"], reply)
            self.assertTrue(wait_until(lambda: len(self.of(card_id, "step.finished")) >= 3, timeout=90),
                            json.dumps(self.events(card_id), ensure_ascii=False, indent=1))
        author = self.of_kind(card_id, "ticket")
        self.assertEqual(author.get("runner"), "box")
        with tempfile.TemporaryDirectory() as target:
            master = hostcards.load_master(target)
        for kind in ("review", "fix"):
            with self.subTest(kind=kind):
                step = self.of_kind(card_id, kind)
                self.assertNotEqual(step.get("runner"), "box", step)
                with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
                    prompt = fh.read()
                for marker in BOX_MARKERS:
                    self.assertNotIn(marker, prompt)
                self.assertNotIn(prepared[0], prompt)
                self.assertIn(master.command_policy(["npm test"], ["npm publish"], step_dir=step["stepDir"]), prompt)


class HostStepWithBoxesEnabledTest(BoxFlow):
    """Silnik: `box.enabled: true`, krok na hoście (projekt bez profilu) — lista poleceń hosta równa master."""

    def test_host_runner_with_boxes_enabled_gets_master_policy(self):
        changed = json.loads(self.board.upsert_project(json.dumps({
            "slug": SLUG, "allowedCommands": ["npm test"], "deniedCommands": ["npm publish"]})))
        self.assertTrue(changed["ok"])
        self.assertTrue(self.box_enabled)
        card_id = self.card()
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "step.finished"), timeout=60))
        step = self.of(card_id, "step.started")[0]
        self.assertNotEqual(step.get("runner"), "box", step)
        with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            prompt = fh.read()
        with tempfile.TemporaryDirectory() as target:
            master = hostcards.load_master(target)
        self.assertIn(master.command_policy(["npm test"], ["npm publish"], step_dir=step["stepDir"]), prompt)
        for marker in BOX_MARKERS:
            self.assertNotIn(marker, prompt)


if __name__ == "__main__":
    unittest.main()


# --- Tide public copy: skipped tests (fix-tests.py) ---
import unittest as _pub_unittest


def _pub_skip(reason, targets):
    g = globals()
    for name in targets:
        cls, _, meth = name.partition('.')
        klass = g.get(cls)
        if klass is None:
            continue
        if not meth:
            g[cls] = _pub_unittest.skip(reason)(klass)
        elif hasattr(klass, meth):
            setattr(klass, meth, _pub_unittest.skip(reason)(getattr(klass, meth)))


_pub_skip('public copy: history rewritten during anonymization; the test reads commits of the private repository', [
    'HostCardsMatchMasterTest.test_host_policy_is_master_text_byte_for_byte',
    'HostReviewAfterBoxAuthorTest.test_host_review_and_fix_after_box_author_get_host_cards',
    'HostStepWithBoxesEnabledTest.test_host_runner_with_boxes_enabled_gets_master_policy',
    'MasterProvenanceTest.test_fixtures_are_fresh_master_output_not_k38_copies',
    'MasterProvenanceTest.test_master_module_is_read_from_git_objects',
    'MasterProvenanceTest.test_provenance_pins_master_sha_and_module_blobs',
])
# --- end of public copy block ---
