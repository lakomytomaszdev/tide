"""Uruchamianie agenta z karty (PT-4) po stronie tablicy, bez Qt i z BoardMonitor:

- RenderLaunch z prawdziwymi szablonami trybów: pełny, lekki, tylko spec; dokumenty AI w tablicy
  (ścieżka bezwzględna, „nie commituj”) i w repozytorium (docs/agents w katalogu roboczym),
  narzędzie i gałąź z okna, błędy,
- migracja szablonów: dawny zaczyn (bajt w bajt) dostaje nową treść, plik zmieniony przez
  użytkownika i nadpisanie projektu zostają; dawny zaczyn nigdy nie trafia do polecenia,
- pola ostatniego uruchomienia karty (mode, tool, worktree, launched) z walidacją,
- znacznik „specyfikacja gotowa”: karta w trybie spec z `status: gotowa` w specyfikacji wraca
  do Backlogu z `spec`; szkic, inny tryb i inna karta — bez zmian,
- BoardMonitor: migracja przy starcie, znacznik przy synchronizacji, ShowWindow("launch:K-12").
Każdy test ma własną tablicę i konfigurację gita — prawdziwej tablicy nikt nie dotyka.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from . import FIXTURES
from .privatebus import app, wait_until
from .test_board_core import BoardCase, git_out
from tide_daemon.board import flows
from tide_daemon.board.model import BoardError

OLD_SEEDS = os.path.join(FIXTURES, "flows-pt1")


def old_seed(mode: str) -> str:
    with open(os.path.join(OLD_SEEDS, f"{mode}.md"), encoding="utf-8", newline="") as fh:
        return fh.read()


class TemplatesTest(BoardCase):
    def setUp(self):
        super().setUp()
        self.tide_repo = self.repo("mobileapp")
        self.repo_docs = self.repo("plasma-tide", agents_docs=True)
        self.store.upsert_project({"repo": self.tide_repo, "tool": "claude", "mode": "full"})
        self.store.upsert_project({"repo": self.repo_docs, "tool": "codex", "mode": "light"})
        self.store.create_card({"title": "Historia diagnoz z filtrem po VIN", "project": "mobileapp",
                                "description": "Filtr po VIN {title} i {\"json\": 1}"})
        self.store.create_card({"title": "Okno Dockera", "project": "plasma-tide"})

    def test_full_mode_with_docs_in_board(self):
        flow = self.store.render_launch("K-1", {})
        docs = os.path.join(self.root, "docs", "mobileapp")
        self.assertEqual((flow["mode"], flow["tool"], flow["aiDocs"], flow["docsDir"]), ("full", "claude", "tide", docs))
        self.assertEqual(flow["branch"], "claude/k-1-historia-diagnoz-filtrem-vin")
        self.assertEqual(flow["template"], os.path.join(self.root, "flows", "full.md"))
        self.assertEqual(flow["specFile"], os.path.join(docs, "specs", "k-1-historia-diagnoz-filtrem-vin.md"))
        self.assertTrue(os.path.isdir(docs), "katalog dokumentów w tablicy założony dla --add-dir")
        text = flow["text"]
        self.assertTrue(text.startswith("Karta K-1: Historia diagnoz z filtrem po VIN\n"))
        self.assertIn(f"Repozytorium: {self.tide_repo}\n", text)
        self.assertIn("Gałąź: claude/k-1-historia-diagnoz-filtrem-vin\n", text)
        self.assertIn("Filtr po VIN {title} i {\"json\": 1}", text, "opis nie jest szablonem")
        self.assertNotIn("tide-flow", text)
        self.assertNotRegex(text, r"\{(card_id|docs_dir|docs_note|tool|spec|spec_file|repo|branch)\}")
        self.assertIn("skilla `grilling`", text)
        self.assertIn("Codex", text, "dla Codeksa grilling opisany słowami")
        self.assertIn(f"`{docs}/specs/k-1-historia-diagnoz-filtrem-vin.md`", text)
        self.assertIn("card: K-1", text)
        self.assertIn(f"`{docs}/plans/RRRR-MM-DD-<slug>.md`", text)
        self.assertIn("`coordinator: claude`", text)
        self.assertIn("agent-plan-standard.md, wersja 1.1", text)
        self.assertIn(f"next-id REPO={self.tide_repo} TITLE=", text)
        self.assertIn("`tracker: local`", text)
        self.assertIn("check-plans", text)
        self.assertIn("TDD", text)
        self.assertIn("skill `tdd`", text)
        self.assertIn("review", text)
        self.assertIn("Niczego tam nie commituj", text, "pliki tablicy commituje usługa")
        self.assertIn("Istniejąca specyfikacja karty: brak", text)
        self.assertIn("Review AI jest obowiązkowe", text)
        self.assertIn("sprawdzenie: auto", text, "domyślnie bez testu ręcznego")
        self.assertIn("zasady projektu (CLAUDE.md albo AGENTS.md) na to pozwalają", text)
        self.assertNotIn("Co przetestować ręcznie i jak", text)
        self.assertEqual(flow["check"], "auto")

    def test_manual_check_asks_for_checklist_in_every_mode(self):
        docs = os.path.join(self.root, "docs", "mobileapp")
        for mode in ("full", "light"):
            flow = self.store.render_launch("K-1", {"mode": mode, "check": "manual"})
            self.assertEqual(flow["check"], "manual")
            text = flow["text"]
            self.assertIn("sprawdzenie: manual", text, mode)
            self.assertIn(f"w {docs}/plans/", text, mode)
            self.assertIn("„Co przetestować ręcznie i jak”", text, mode)
            self.assertIn("czeka na mój test ręczny", text, mode)
            self.assertIn("Nie scalaj", text, mode)
            self.assertIn("review AI", text, mode)
        spec = self.store.render_launch("K-1", {"mode": "spec", "check": "manual"})["text"]
        self.assertIn("manual — po review AI test ręczny użytkownika", spec)
        self.assertIn("„Co przetestować ręcznie i jak”", spec)
        self.assertIn("zleć jej review AI", spec)
        with self.assertRaises(BoardError):
            self.store.render_launch("K-1", {"check": "czasem"})

    def test_light_mode_with_docs_in_repo_and_codex(self):
        flow = self.store.render_launch("K-2", {})
        self.assertEqual((flow["mode"], flow["tool"], flow["aiDocs"]), ("light", "codex", "repo"))
        self.assertEqual(flow["docsDir"], os.path.join(self.repo_docs, "docs", "agents"))
        self.assertEqual(flow["branch"], "codex/k-2-okno-dockera")
        text = flow["text"]
        self.assertIn("Plany i specyfikacje: docs/agents\n", text, "w katalogu roboczym (worktree), nie w głównym")
        self.assertIn("`docs/agents/plans/RRRR-MM-DD-<slug>.md`", text)
        self.assertIn("`docs/agents/specs/k-2-okno-dockera.md`", text)
        self.assertIn("1–3 ticketami", text)
        self.assertIn("`coordinator: codex`", text)
        self.assertIn("docs(plan)", text, "plan w repozytorium commitowany z pracą")
        self.assertNotIn("Niczego tam nie commituj", text)
        self.assertFalse(os.path.exists(os.path.join(self.root, "docs", "plasma-tide")), "w tablicy nic nie powstaje")

    def test_spec_mode_without_branch_and_code(self):
        self.store.update_card("K-1", {"spec": "/stara/specyfikacja.md"})
        text = self.store.render_launch("K-1", {"mode": "spec"})["text"]
        self.assertIn("Gałąź: bez gałęzi — katalog projektu\n", text)
        self.assertIn("Tylko specyfikacja", text)
        self.assertIn("Bez zmian w kodzie", text)
        self.assertIn("status: szkic", text)
        self.assertIn("`status: gotowa`", text)
        self.assertIn("Commit: niczego nie commitujesz — plik specyfikacji w tablicy commituje usługa Tide.", text,
                      "ai-docs: tide — commituje usługa")
        self.assertNotIn("Zakładasz go według standardu", text, "bez planu i ticketów")
        self.assertIn("Istniejąca specyfikacja karty: /stara/specyfikacja.md", text)

    def test_spec_mode_in_repo_commits_only_the_spec(self):
        text = self.store.render_launch("K-2", {"mode": "spec"})["text"]
        self.assertIn("zacommituj wyłącznie jej plik na bieżącej gałęzi", text)
        self.assertIn("`git add docs/agents/specs/k-2-okno-dockera.md`", text)
        self.assertIn('`git commit -m "docs(spec): K-2 <short English title>"`', text)
        self.assertIn("po angielsku", text)
        self.assertIn("bez żadnych podpisów i wzmianek o AI", text)
        self.assertIn("Co-Authored-By", text)
        self.assertIn("bez push", text)
        self.assertNotIn("niczego nie commitujesz", text)
        self.assertNotIn("{spec_commit}", self.store.render_launch("K-2", {"mode": "full"})["text"])

    def test_window_choice_overrides_tool_and_branch(self):
        flow = self.store.render_launch("K-1", {"mode": "light", "tool": "codex"})
        self.assertEqual((flow["mode"], flow["tool"], flow["branch"]),
                         ("light", "codex", "codex/k-1-historia-diagnoz-filtrem-vin"))
        self.assertIn("`coordinator: codex`", flow["text"])
        flow = self.store.render_launch("K-1", {"tool": "claude", "branch": "feat/k-1-vin"})
        self.assertEqual(flow["branch"], "feat/k-1-vin")
        self.assertIn("Gałąź: feat/k-1-vin\n", flow["text"])
        self.assertEqual(self.store.render_flow("K-1", "")["branch"], "claude/k-1-historia-diagnoz-filtrem-vin",
                         "RenderFlow to RenderLaunch z samym trybem")
        for request in ({"tool": "gemini"}, {"mode": "turbo"}, {"model": "x"}, {"branch": "a\x01b"}):
            with self.assertRaises(BoardError, msg=request):
                self.store.render_launch("K-1", request)
        with self.assertRaises(BoardError):
            self.store.render_launch("K-9", {})


class MigrationTest(BoardCase):
    def setUp(self):
        super().setUp()
        self.store.create_card({"title": "Grabie"})  # zakłada tablicę z obecnymi szablonami
        self.store.upsert_project({"name": "Dom"})
        for mode in flows.FLOW_KEYS:
            self.write(f"flows/{mode}.md", old_seed(mode))
        self.write("flows/dom/full.md", old_seed("full"))  # nadpisanie projektu — własność użytkownika
        git_out(self.root, "add", "-A")
        git_out(self.root, "commit", "-qm", "stara tablica")

    def test_fixture_matches_known_seeds(self):
        for mode in flows.FLOW_KEYS:
            self.assertTrue(flows.outdated(mode, old_seed(mode)), mode)
            self.assertFalse(flows.outdated(mode, flows.SEED_FLOWS[mode]), mode)
            self.assertFalse(flows.outdated(mode, old_seed(mode) + "\n"), "jeden bajt więcej — zmieniony")

    def test_old_seed_is_never_rendered_even_before_migration(self):
        flow = self.store.render_launch("K-1", {"mode": "full"})
        self.assertEqual(flow["template"], "builtin")
        self.assertNotIn("Szablon tymczasowy", flow["text"])
        self.assertIn("skilla `grilling`", flow["text"])
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "", "odczyt niczego nie zapisuje")

    def test_untouched_seeds_are_replaced_and_user_edits_kept(self):
        self.write("flows/light.md", old_seed("light").replace("potem praca.", "potem praca. Mój dopisek."))
        git_out(self.root, "commit", "-qam", "moja zmiana")
        changed = self.store.migrate_flows()
        self.assertEqual(sorted(changed), [os.path.join("flows", "full.md"), os.path.join("flows", "spec.md")])
        self.assertEqual(git_out(self.root, "log", "-1", "--format=%s").strip(), "flows: update templates")
        self.assertEqual(git_out(self.root, "show", "--name-only", "--format=", "HEAD").split(),
                         ["flows/full.md", "flows/spec.md"])
        with open(os.path.join(self.root, "flows", "full.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), flows.SEED_FLOWS["full"])
        with open(os.path.join(self.root, "flows", "light.md"), encoding="utf-8") as fh:
            self.assertIn("Mój dopisek.", fh.read(), "szablon zmieniony przez użytkownika zostaje")
        with open(os.path.join(self.root, "flows", "dom", "full.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), old_seed("full"), "nadpisań projektów nie ruszamy")
        self.assertIn("Mój dopisek.", self.store.render_launch("K-1", {"mode": "light"})["text"])
        self.assertEqual(self.store.migrate_flows(), [], "drugi raz — nic")
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")

    def test_no_board_or_busy_repo_is_left_alone(self):
        from tide_daemon.board.store import BoardStore
        empty = BoardStore(os.path.join(self.tmp, "nie-ma"))
        self.assertEqual(empty.migrate_flows(), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "nie-ma")))
        with mock.patch.object(self.store.repo, "busy", return_value="revert"):
            self.assertEqual(self.store.migrate_flows(), [])
        self.assertEqual(self.store.render_launch("K-1", {})["template"], "builtin")


class LaunchFieldsTest(BoardCase):
    def test_launch_fields_are_validated_and_in_snapshot(self):
        self.store.create_card({"title": "Grabie"})
        self.assertNotIn("worktree:", self.card_text("K-1"), "karta bez uruchomienia nie ma tych pól")
        result = self.store.update_card("K-1", {"branch": "claude/k-1-grabie", "worktree": "/r-worktrees/k-1-grabie",
                                                "mode": "full", "tool": "claude",
                                                "launched": "2026-10-27T12:00:00+01:00"})
        self.assertTrue(result["changed"])
        self.assertEqual(self.log()[-1], "K-1: update branch, entry, mode, tool, worktree, launched")
        card = self.store.snapshot()["cards"][0]
        self.assertEqual((card["branch"], card["worktree"], card["mode"], card["tool"], card["launched"]),
                         ("claude/k-1-grabie", "/r-worktrees/k-1-grabie", "full", "claude", "2026-10-27T12:00:00+01:00"))
        self.assertGreater(card["launchedTs"], 0)
        self.assertEqual(card["branchSlug"], "k-1-grabie", "część proponowanej gałęzi bez narzędzia")
        for bad in ({"mode": "turbo"}, {"tool": "gemini"}, {"worktree": "wzgledna"}, {"launched": "wczoraj"}):
            with self.assertRaises(BoardError, msg=bad):
                self.store.update_card("K-1", bad)
        self.store.update_card("K-1", {"branch": "", "worktree": "", "mode": "spec"})
        card = self.store.snapshot()["cards"][0]
        self.assertEqual((card["branch"], card["worktree"], card["entry"], card["mode"]), ("", "", "spec", ""),
                         "dawne mode: spec w żądaniu to wejście Tylko specyfikacja (PT-77)")
        self.store.create_card({"title": "Z polami", "mode": "light", "tool": "codex"})
        self.assertIn("mode: light", self.card_text("K-2"))
        self.assertNotIn("worktree:", self.card_text("K-2"))


class SpecReadyTest(BoardCase):
    SPEC = "---\ncard: {card}\nstatus: {status}\n---\n\n# Specyfikacja\n"

    def setUp(self):
        super().setUp()
        self.tide_repo = self.repo("mobileapp")
        self.repo_docs = self.repo("plasma-tide", agents_docs=True)
        self.store.upsert_project({"repo": self.tide_repo})
        self.store.upsert_project({"repo": self.repo_docs})
        self.store.create_card({"title": "Na górze Backlogu"})                                 # K-1
        self.store.create_card({"title": "Historia VIN", "project": "mobileapp", "column": "todo"})  # K-2
        self.store.create_card({"title": "Okno Dockera", "project": "plasma-tide", "column": "todo"})  # K-3
        self.store.create_card({"title": "Pełny", "project": "mobileapp", "column": "todo"})     # K-4
        self.store.update_card("K-2", {"mode": "spec", "tool": "claude"})
        self.store.update_card("K-3", {"mode": "spec", "tool": "codex"})
        self.store.update_card("K-4", {"mode": "full", "tool": "claude", "branch": "claude/k-4-pelny"})

    def spec(self, folder, name, card, status):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.SPEC.format(card=card, status=status))
        return path

    def test_ready_spec_moves_card_back_with_marker(self):
        board_specs = os.path.join(self.root, "docs", "mobileapp", "specs")
        draft = self.spec(board_specs, "k-2-historia-vin.md", "K-2", "szkic")
        self.spec(board_specs, "k-4-pelny.md", "K-4", "gotowa")       # tryb pełny — bez znacznika
        self.spec(board_specs, "k-20-inna.md", "K-20", "gotowa")      # inna karta (prefiks k-2!)
        self.assertEqual(self.store.mark_ready_specs(), [])
        self.spec(board_specs, "k-2-historia-vin.md", "K-2", "gotowa")
        self.store.sync()
        self.assertEqual(self.store.mark_ready_specs(), ["K-2"])
        self.assertEqual(self.log()[-1], "K-2: specification ready")
        cards = {c["id"]: c for c in self.store.snapshot()["cards"]}
        self.assertEqual((cards["K-2"]["column"], cards["K-2"]["spec"]), ("backlog", draft))
        backlog = [c["id"] for c in self.store.snapshot()["cards"] if c["column"] == "backlog"]
        self.assertEqual(backlog[0], "K-2", "na górze Backlogu")
        self.assertEqual((cards["K-4"]["column"], cards["K-4"]["spec"]), ("todo", ""))
        self.assertEqual(self.store.mark_ready_specs(), [], "drugi raz — nic")

    def test_repo_docs_and_card_key_in_frontmatter(self):
        specs = os.path.join(self.repo_docs, "docs", "agents", "specs")
        path = self.spec(specs, "okno-dockera.md", "k-3", "ready")
        self.assertEqual(self.store.mark_ready_specs(), ["K-3"])
        card = {c["id"]: c for c in self.store.snapshot()["cards"]}["K-3"]
        self.assertEqual((card["column"], card["spec"]), ("backlog", path))

    def test_card_moved_by_hand_is_not_touched(self):
        self.spec(os.path.join(self.root, "docs", "mobileapp", "specs"), "k-2.md", "K-2", "gotowa")
        self.store.move_card("K-2", "done", 0)
        self.store.sync()
        self.assertEqual(self.store.mark_ready_specs(), [])


class MonitorLaunchTest(unittest.TestCase):
    def setUp(self):
        app()
        from tide_daemon.board.store import BoardStore
        from tide_daemon.board_monitor import BoardMonitor
        self.tmp = tempfile.mkdtemp(prefix="tide-board-launch-mon-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        config = os.path.join(self.tmp, "gitconfig")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write("[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": config, "GIT_CONFIG_NOSYSTEM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = os.path.join(self.tmp, "board")
        self.store = BoardStore(self.root)
        self.shown = []

        class Launchers:
            def show_view(inner, view, item=""):
                self.shown.append((view, item))
                return True

        self.monitor = BoardMonitor(self.store, debounce_ms=200, poll_ms=60_000, launchers=Launchers())
        self.addCleanup(self.monitor.stop)

    def test_start_migrates_and_sync_marks_ready_spec(self):
        self.store.create_card({"title": "Spec", "column": "todo", "mode": "spec"})
        with open(os.path.join(self.root, "flows", "spec.md"), "w", encoding="utf-8") as fh:
            fh.write(old_seed("spec"))
        git_out(self.root, "commit", "-qam", "stary zaczyn")
        self.monitor.start()
        with open(os.path.join(self.root, "flows", "spec.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), flows.SEED_FLOWS["spec"])
        os.makedirs(os.path.join(self.root, "docs", "_inbox", "specs"))
        wait_until(lambda: False, timeout=0.3)
        with open(os.path.join(self.root, "docs", "_inbox", "specs", "k-1-spec.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ncard: K-1\nstatus: gotowa\n---\n")
        self.assertTrue(wait_until(lambda: json.loads(self.monitor.snapshot_json())["cards"][0]["column"] == "backlog",
                                   timeout=5), git_out(self.root, "log", "--format=%s"))
        subjects = git_out(self.root, "log", "--format=%s").splitlines()
        self.assertEqual(subjects[:2], ["K-1: specification ready", "sync external changes"])
        self.assertIn("flows: update templates", subjects)
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")

    def test_show_window_with_launch_intent_and_render_launch(self):
        self.assertTrue(self.monitor.show_window("launch:k-12"))
        self.assertTrue(self.monitor.show_window("K-3"))
        self.assertTrue(self.monitor.show_window("launch:"))
        self.assertTrue(self.monitor.show_window("launch:--view"))
        self.assertEqual(self.shown, [("board", "launch:K-12"), ("board", "K-3"), ("board", ""), ("board", "")])
        self.assertEqual(json.loads(self.monitor.render_launch("K-1", "{}"))["error"], "not-found")
        self.assertEqual(json.loads(self.monitor.render_launch("K-1", "[]"))["error"], "bad-request")
        self.monitor.create_card(json.dumps({"title": "Karta"}))
        reply = json.loads(self.monitor.render_launch("K-1", json.dumps({"mode": "light", "tool": "codex"})))
        self.assertEqual((reply["ok"], reply["mode"], reply["tool"]), (True, "light", "codex"))


if __name__ == "__main__":
    unittest.main()
