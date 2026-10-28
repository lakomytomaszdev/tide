"""„Tylko specyfikacja” jako wejście, nie tryb (PT-77) po stronie tablicy, bez Qt, i przez D-Bus:

- karty i projekty z `mode: spec` czytamy jako wejście „Tylko specyfikacja” bez błędów walidacji i bez
  przepisywania plików (odczyt niczego nie zapisuje),
- RenderLaunch przyjmuje wejście (`entry`) i tryb (Pełny, Lekki tylko przy Planowaniu); stare żądanie z samym
  `mode: spec` działa jak dotąd,
- zapis uruchomienia karty i domyślne wejście projektu przez `entry`, kolejne wejścia dochodzą bez przebudowy,
- szablony: nietknięty dawny zaczyn spec podmieniamy, zmieniony przez użytkownika zostaje (z ostrzeżeniem),
- D-Bus na prywatnej szynie: polecenie z każdego wejścia i trybu jest bajt w bajt takie jak przed zmianą
  (fixtures/launch-pt77/commands.json, wygenerowane z kodu sprzed PT-77).
Każdy test ma własną tablicę i konfigurację gita — prawdziwej tablicy nikt nie dotyka.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from . import FIXTURES
from .privatebus import SERVICE, PrivateBus, requires_dbus, wait_until
from .test_board_core import BoardCase, git_out
from .test_board_dbus import NOW_TEXT, git_config
from tide_daemon.board import flows
from tide_daemon.board.model import BoardError

def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


LEGACY_SPEC_CARD = ("---\nid: K-3\ntitle: Stara specyfikacja\nproject: dom\ncolumn: todo\norder: 1000\n"
                    "created: 2026-10-20T10:00:00+02:00\nupdated: 2026-10-20T10:00:00+02:00\ncheck: auto\n"
                    "mode: spec\ntool: claude\nlaunched: 2026-10-20T10:00:00+02:00\n---\n\nOpis.\n")
LEGACY_SPEC_PROJECT = "---\nname: Dom\nrepo: {repo}\ntags:\nai-docs: tide\ntool: claude\nmode: spec\n---\n"


class ReadingTest(BoardCase):
    def setUp(self):
        super().setUp()
        self.repo_dir = self.repo("dom")
        self.store.create_card({"title": "Pierwsza"})  # zakłada tablicę
        self.write("projects/dom.md", LEGACY_SPEC_PROJECT.format(repo=self.repo_dir))
        self.write("cards/K-3.md", LEGACY_SPEC_CARD)
        git_out(self.root, "add", "-A")
        git_out(self.root, "commit", "-qm", "stara tablica")

    def test_legacy_spec_mode_reads_as_entry_without_errors(self):
        snap = self.store.snapshot()
        self.assertEqual(snap["errors"], [])
        card = {c["id"]: c for c in snap["cards"]}["K-3"]
        self.assertEqual((card["entry"], card["mode"]), ("spec", ""), "wejście, nie tryb")
        project = snap["projects"][0]
        self.assertEqual((project["entry"], project["mode"]), ("spec", "full"),
                         "domyślne wejście projektu; tryb Planowania zostaje domyślny")
        self.assertEqual(snap["entries"], ["planning", "spec"])
        self.assertEqual(snap["modes"], ["full", "light"], "tryb to tylko Pełny albo Lekki")

    def test_reading_does_not_rewrite_files(self):
        before = (self.card_text("K-3"), read(os.path.join(self.root, "projects", "dom.md")))
        head = git_out(self.root, "rev-parse", "HEAD")
        self.store.snapshot()
        self.store.render_launch("K-3", {})
        self.store.mark_ready_specs()
        self.store.migrate_flows()
        after = (self.card_text("K-3"), read(os.path.join(self.root, "projects", "dom.md")))
        self.assertEqual(before, after)
        self.assertEqual(git_out(self.root, "rev-parse", "HEAD"), head)
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")

    def test_cards_without_launch_and_planning_cards(self):
        self.write("cards/K-4.md", LEGACY_SPEC_CARD.replace("K-3", "K-4").replace("mode: spec", "mode: light"))
        cards = {c["id"]: c for c in self.store.snapshot()["cards"]}
        self.assertEqual((cards["K-1"]["entry"], cards["K-1"]["mode"]), ("", ""), "karta bez uruchomienia")
        self.assertEqual((cards["K-4"]["entry"], cards["K-4"]["mode"]), ("planning", "light"))

    def test_explicit_entry_wins_over_legacy_mode_and_is_validated(self):
        self.write("cards/K-4.md", LEGACY_SPEC_CARD.replace("K-3", "K-4").replace("mode: spec", "mode: full\nentry: spec"))
        self.write("cards/K-5.md", LEGACY_SPEC_CARD.replace("K-3", "K-5").replace("mode: spec", "entry: turbo"))
        snap = self.store.snapshot()
        cards = {c["id"]: c for c in snap["cards"]}
        self.assertEqual((cards["K-4"]["entry"], cards["K-4"]["mode"]), ("spec", "full"))
        self.assertEqual(cards["K-5"]["entry"], "")
        self.assertEqual([e["message"] for e in snap["errors"]], ["K-5.md: nieznane wejście „turbo” (planning, spec)"])


class RenderLaunchTest(BoardCase):
    def setUp(self):
        super().setUp()
        self.repo_dir = self.repo("dom")
        self.store.upsert_project({"repo": self.repo_dir, "tool": "claude", "mode": "light"})
        self.store.create_card({"title": "Zadanie", "project": "dom"})

    def text(self, **request):
        return self.store.render_launch("K-1", request)

    def test_entry_requests_give_the_same_command_as_modes_before(self):
        self.assertEqual(self.text(entry="spec")["text"], self.text(mode="spec")["text"])
        self.assertEqual(self.text(entry="planning", mode="full")["text"], self.text(mode="full")["text"])
        self.assertEqual(self.text(entry="planning", mode="light")["text"], self.text(mode="light")["text"])
        self.assertEqual(self.text(entry="spec", mode="full")["text"], self.text(mode="spec")["text"],
                         "tryb dotyczy tylko Planowania — przy Tylko specyfikacja jest pomijany")

    def test_result_names_entry_and_mode(self):
        spec = self.text(entry="spec")
        self.assertEqual((spec["entry"], spec["mode"]), ("spec", ""))
        planning = self.text(entry="planning", mode="full")
        self.assertEqual((planning["entry"], planning["mode"]), ("planning", "full"))
        legacy = self.text(mode="spec")
        self.assertEqual((legacy["entry"], legacy["mode"]), ("spec", ""), "stare żądanie to wejście Tylko specyfikacja")

    def test_defaults_come_from_the_project(self):
        flow = self.text()
        self.assertEqual((flow["entry"], flow["mode"]), ("planning", "light"))
        self.store.upsert_project({"slug": "dom", "entry": "spec"})
        flow = self.text()
        self.assertEqual((flow["entry"], flow["mode"], flow["text"]), ("spec", "", self.text(mode="spec")["text"]))
        self.assertEqual(self.text(entry="planning")["mode"], "light", "tryb projektu przy Planowaniu")

    def test_legacy_project_mode_spec_is_the_default_entry(self):
        self.write("projects/dom.md", LEGACY_SPEC_PROJECT.format(repo=self.repo_dir))
        self.assertEqual(self.text()["text"], self.text(mode="spec")["text"])
        self.assertEqual(self.text(entry="planning")["mode"], "full")

    def test_errors(self):
        for request in ({"entry": "turbo"}, {"entry": "planning", "mode": "spec"}, {"mode": "turbo"}, {"entry": 3}):
            with self.assertRaises(BoardError, msg=request):
                self.store.render_launch("K-1", request)


class WritingTest(BoardCase):
    def setUp(self):
        super().setUp()
        self.repo_dir = self.repo("dom")
        self.store.upsert_project({"repo": self.repo_dir})
        self.store.create_card({"title": "Zadanie", "project": "dom", "column": "todo"})

    def test_launch_fields_carry_entry(self):
        self.store.update_card("K-1", {"entry": "spec", "mode": "", "tool": "claude", "worktree": "", "launched": "2026-10-28T10:00:00+01:00"})
        card = self.cards()["K-1"]
        self.assertEqual((card["entry"], card["mode"]), ("spec", ""))
        self.assertIn("entry: spec", self.card_text("K-1"))
        self.assertNotIn("mode: spec", self.card_text("K-1"))
        self.store.update_card("K-1", {"entry": "planning", "mode": "light"})
        card = self.cards()["K-1"]
        self.assertEqual((card["entry"], card["mode"]), ("planning", "light"))
        for bad in ({"entry": "turbo"}, {"mode": "turbo"}, {"entry": "planning", "mode": "spec"}):
            with self.assertRaises(BoardError, msg=bad):
                self.store.update_card("K-1", bad)

    def test_legacy_mode_spec_in_a_request_is_the_entry(self):
        self.store.update_card("K-1", {"mode": "spec"})
        card = self.cards()["K-1"]
        self.assertEqual((card["entry"], card["mode"]), ("spec", ""))
        self.assertNotIn("mode: spec", self.card_text("K-1"), "do pliku nie wraca stary zapis")
        created = self.store.create_card({"title": "Druga", "mode": "spec"})
        self.assertEqual(self.cards()[created["id"]]["entry"], "spec")

    def test_project_default_entry(self):
        self.store.upsert_project({"slug": "dom", "entry": "spec"})
        project = self.store.snapshot()["projects"][0]
        self.assertEqual((project["entry"], project["mode"]), ("spec", "full"))
        self.store.upsert_project({"slug": "dom", "entry": "planning", "mode": "light"})
        project = self.store.snapshot()["projects"][0]
        self.assertEqual((project["entry"], project["mode"]), ("planning", "light"))
        with self.assertRaises(BoardError):
            self.store.upsert_project({"slug": "dom", "entry": "turbo"})
        with self.assertRaises(BoardError):
            self.store.upsert_project({"slug": "dom", "mode": "turbo"})

    def test_legacy_project_mode_spec_is_cleaned_only_when_the_user_changes_it(self):
        self.write("projects/dom.md", LEGACY_SPEC_PROJECT.format(repo=self.repo_dir))
        self.store.upsert_project({"slug": "dom", "tool": "codex"})
        self.assertIn("mode: spec", read(os.path.join(self.root, "projects", "dom.md")),
                      "zmiana innego pola nie rusza starego zapisu")
        self.store.upsert_project({"slug": "dom", "entry": "planning"})
        text = read(os.path.join(self.root, "projects", "dom.md"))
        self.assertNotIn("mode: spec", text)
        self.assertEqual(self.store.snapshot()["projects"][0]["entry"], "planning")

    def test_ready_spec_marks_cards_launched_with_either_spelling(self):
        specs = os.path.join(self.root, "docs", "dom", "specs")
        os.makedirs(specs)
        self.store.create_card({"title": "Stara", "project": "dom", "column": "todo"})
        self.write("cards/K-2.md", self.card_text("K-2").replace("column: todo\n", "column: todo\nmode: spec\n"))
        self.store.update_card("K-1", {"entry": "spec"})
        for num in (1, 2):
            with open(os.path.join(specs, f"k-{num}-spec.md"), "w", encoding="utf-8") as fh:
                fh.write(f"---\ncard: K-{num}\nstatus: gotowa\n---\n# S\n")
        self.assertEqual(self.store.mark_ready_specs(), ["K-1", "K-2"])


class ModeMeansPlanningTest(BoardCase):
    """Stare żądanie z samym trybem Pełny albo Lekki, bez `entry`, znaczy Planowanie — jedna zasada dla
    RenderLaunch, RenderFlow, UpdateCard, CreateCard i UpsertProject, także gdy domyślne wejście to spec."""

    def setUp(self):
        super().setUp()
        self.repo_dir = self.repo("dom")
        self.store.create_card({"title": "Zadanie", "column": "todo"})
        self.write("projects/dom.md", LEGACY_SPEC_PROJECT.format(repo=self.repo_dir))
        self.store.update_card("K-1", {"project": "dom"})

    def test_render_launch_and_flow(self):
        for mode in ("full", "light"):
            for flow in (self.store.render_launch("K-1", {"mode": mode}), self.store.render_flow("K-1", mode)):
                self.assertEqual((flow["entry"], flow["mode"]), ("planning", mode))
        self.assertEqual(self.store.render_launch("K-1", {})["entry"], "spec")
        self.assertEqual(self.store.render_launch("K-1", {"entry": "spec", "mode": "full"})["entry"], "spec")

    def test_upsert_project(self):
        self.assertTrue(self.store.upsert_project({"slug": "dom", "mode": "full"})["changed"])
        project = self.store.snapshot()["projects"][0]
        self.assertEqual((project["entry"], project["mode"]), ("planning", "full"))
        self.assertNotIn("mode: spec", read(os.path.join(self.root, "projects", "dom.md")))

    def test_update_and_create_card(self):
        self.store.update_card("K-1", {"entry": "spec"})
        self.store.update_card("K-1", {"mode": "full"})
        card = self.cards()["K-1"]
        self.assertEqual((card["entry"], card["mode"]), ("planning", "full"), "bez niespójnego entry: spec, mode: full")
        self.store.update_card("K-1", {"entry": "spec", "mode": "full"})
        self.assertEqual(self.cards()["K-1"]["entry"], "spec", "jawne entry wygrywa")
        created = self.store.create_card({"title": "Druga", "mode": "light"})
        self.assertEqual(self.cards()[created["id"]]["entry"], "planning")


class TemplatesTest(BoardCase):
    def setUp(self):
        super().setUp()
        self.store.create_card({"title": "Grabie"})
        self.store.upsert_project({"name": "Dom"})
        with open(os.path.join(FIXTURES, "launch-pt77", "spec-flow-before.md"), encoding="utf-8", newline="") as fh:
            self.before = fh.read()

    def test_seed_declares_the_entry_and_keeps_the_body(self):
        self.assertEqual(flows.body_of(flows.SEED_FLOWS["spec"]), flows.body_of(self.before))
        self.assertIn("entry: spec", flows.SEED_FLOWS["spec"])
        self.assertNotIn("mode: spec", flows.SEED_FLOWS["spec"])
        self.assertEqual(flows.FLOW_KEYS, ("full", "light", "spec"))
        self.assertTrue(flows.outdated("spec", self.before))

    def test_untouched_seed_is_replaced_and_user_edit_is_kept_with_a_warning(self):
        self.write("flows/spec.md", self.before)
        self.write("flows/dom/spec.md", self.before + "\nMoja dopiska.\n")
        git_out(self.root, "add", "-A")
        git_out(self.root, "commit", "-qm", "stara tablica")
        self.assertEqual(self.store.migrate_flows(), [os.path.join("flows", "spec.md")])
        with open(os.path.join(self.root, "flows", "spec.md"), encoding="utf-8", newline="") as fh:
            self.assertEqual(fh.read(), flows.SEED_FLOWS["spec"])
        with open(os.path.join(self.root, "flows", "dom", "spec.md"), encoding="utf-8", newline="") as fh:
            self.assertEqual(fh.read(), self.before + "\nMoja dopiska.\n", "szablon użytkownika nietknięty")
        messages = [e["message"] for e in self.store.snapshot()["errors"]]
        self.assertEqual(len(messages), 1, messages)
        self.assertIn("flows/dom/spec.md", messages[0])
        self.assertIn("entry: spec", messages[0])
        self.assertIn("zostaje bez zmian", messages[0])
        self.assertEqual(self.store.migrate_flows(), [], "drugi raz — nic")

    def test_edited_board_template_is_kept_and_still_used(self):
        edited = self.before.replace("Tryb „Tylko specyfikacja”", "Moje wejście")
        self.write("flows/spec.md", edited)
        self.assertEqual(self.store.migrate_flows(), [])
        flow = self.store.render_launch("K-1", {"entry": "spec"})
        self.assertIn("Moje wejście", flow["text"])
        self.assertTrue(any("flows/spec.md" in e["message"] for e in self.store.snapshot()["errors"]))

    def test_current_seed_and_planning_templates_give_no_warning(self):
        self.write("flows/full.md", flows.SEED_FLOWS["full"].replace("Tryb", "Mój tryb"))
        self.assertEqual(self.store.snapshot()["errors"], [])


COMMANDS = os.path.join(FIXTURES, "launch-pt77", "commands.json")
SPEC_PROJECT = os.path.join(FIXTURES, "launch-pt77", "spec-project.json")  # z kodu sprzed PT-77, projekt `mode: spec`


@requires_dbus
class SameCommandDBusTest(unittest.TestCase):
    """Polecenie z każdego wejścia i trybu przez D-Bus jest takie jak przed zmianą."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-board-entry-")
        cls.ws = os.path.join(cls.tmp, "ws")
        env = git_config(cls.tmp)
        for name, docs in (("mobileapp", False), ("plasma-tide", True)):
            path = os.path.join(cls.ws, name)
            os.makedirs(os.path.join(path, "docs", "agents") if docs else path)
            subprocess.run(["git", "-C", path, "init", "-q"], check=True, env=dict(os.environ, **env))
        cls.service = PrivateBus("tide-board-entry")
        cls.daemon = cls.service.spawn_daemon(
            "--claude-dir", os.path.join(cls.tmp, "claude"), "--codex-dir", os.path.join(cls.tmp, "codex"),
            "--no-processes", "--no-notifications", "--now", NOW_TEXT, "--workspace", cls.ws,
            "--board-dir", os.path.join(cls.ws, "tide-board"), extra_env=env)
        cls.call("UpsertProject", {"repo": os.path.join(cls.ws, "mobileapp"), "tool": "claude"})
        cls.call("UpsertProject", {"repo": os.path.join(cls.ws, "plasma-tide"), "tool": "codex"})
        cls.call("CreateCard", {"title": "Historia diagnoz z filtrem po VIN", "project": "mobileapp",
                                "description": "Opis {title}"})
        cls.call("CreateCard", {"title": "Okno Dockera", "project": "plasma-tide"})
        with open(COMMANDS, encoding="utf-8") as fh:
            cls.golden = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def call(cls, member, *args):
        from PyQt6.QtDBus import QDBusMessage
        args = [json.dumps(a) if isinstance(a, dict) else a for a in args]
        reply = cls.service.call(SERVICE, "/Board", "org.tide.Daemon.Board", member, *args)
        assert reply.type() == QDBusMessage.MessageType.ReplyMessage, reply.errorMessage()
        return json.loads(reply.arguments()[0])

    def test_every_entry_and_mode_gives_the_command_from_before(self):
        # (stare żądanie z samym trybem, nowe żądanie z wejściem i trybem)
        for flow_key, new in (("full", {"entry": "planning", "mode": "full"}),
                              ("light", {"entry": "planning", "mode": "light"}),
                              ("spec", {"entry": "spec"})):
            for card in ("K-1", "K-2"):
                for check in ("auto", "manual"):
                    want = self.golden[f"{card}/{flow_key}/{check}"]
                    for request in ({"mode": flow_key, "check": check}, dict(new, check=check)):
                        reply = self.call("RenderLaunch", card, request)
                        self.assertTrue(reply["ok"], reply)
                        got = reply["text"].replace(self.ws, "{WS}")
                        self.assertEqual(got, want, (card, flow_key, check, request))

    def normal(self, text: str, card: str) -> str:
        """Tekst bez tymczasowej ścieżki i z numerem karty z fixture (K-1)."""
        return text.replace(self.ws, "{WS}").replace(card, "K-1").replace(card.lower(), "k-1")

    def test_project_defaulting_to_spec_keeps_old_requests_meaning(self):
        # Projekt ze starym `mode: spec` i z nowym `entry: spec`: {} to spec, a sam tryb to Planowanie — jak przed zmianą.
        repo = os.path.join(self.ws, "specdom")
        os.makedirs(repo)
        subprocess.run(["git", "-C", repo, "init", "-q"], check=True, env=dict(os.environ, **git_config(self.tmp)))
        self.call("UpsertProject", {"repo": repo, "tool": "claude"})
        project_file = os.path.join(self.ws, "tide-board", "projects", "specdom.md")
        with open(project_file, encoding="utf-8") as fh:
            base = fh.read()
        self.call("CreateCard", {"title": "Zadanie domowe", "project": "specdom"})
        with open(SPEC_PROJECT, encoding="utf-8") as fh:
            golden = json.load(fh)
        card = [c for c in self.call("Snapshot")["cards"] if c["title"] == "Zadanie domowe"][0]["id"]
        for header in ("mode: spec", "entry: spec"):
            with open(project_file, "w", encoding="utf-8") as fh:
                fh.write(base.replace("mode: full", header))
            self.assertTrue(wait_until(lambda: self.call("Snapshot")["projects"][-1]["entry"] == "spec", timeout=10))
            for name, request in (("default", {}), ("full", {"mode": "full"}), ("light", {"mode": "light"})):
                reply = self.call("RenderLaunch", card, request)
                self.assertTrue(reply["ok"], reply)
                self.assertEqual(self.normal(reply["text"], card), golden[f"specdom/{name}"], (header, name))
            flow = self.call("RenderFlow", card, "full")
            self.assertEqual(self.normal(flow["text"], card), golden["specdom/full"], header)

    def test_launch_is_recorded_as_entry(self):
        self.assertTrue(self.call("UpdateCard", "K-1", {"entry": "spec", "mode": "", "tool": "claude"})["ok"])
        card = {c["id"]: c for c in self.call("Snapshot")["cards"]}["K-1"]
        self.assertEqual((card["entry"], card["mode"]), ("spec", ""))


if __name__ == "__main__":
    unittest.main()
