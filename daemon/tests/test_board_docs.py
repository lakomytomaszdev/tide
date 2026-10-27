"""Plan i specyfikacja karty do panelu (PT-5, część B): board/docs.py i BoardMonitor.card_docs.

- plan po kluczu `card` (niezakończony przed zakończonym, starsze w `others`), kopia z worktree,
  plan koordynatora bez klucza, plany projektu z `ai-docs: tide` w `<tablica>/docs/<projekt>/plans`,
- specyfikacja z pola `spec` (względna do repozytorium, bezwzględna w tablicy), zapas po nazwie pliku
  i po `card: K-12`, gotowa przed szkicem,
- bezpieczeństwo: dowiązanie poza katalogi projektu i tablicy, plik spoza nich, nie-Markdown, FIFO,
  plik ponad 512 KB (ucięty z dopiskiem), brak pliku,
- treść do pokazania: bez obrazów i surowego HTML, bez tabeli ticketów i tytułu, sekcja
  „Co przetestować ręcznie”, pamięć po czasie zmiany pliku.
Repozytoria i tablica są tymczasowe — nigdy ~/DevWorkspace/tide-board.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from .gitrepos import commit, git
from .privatebus import app, wait_until
from tide_daemon.board import docs as docs_mod
from tide_daemon.board.docs import CardDocs, manual_section, strip_ticket_table


def plan_text(title, card="", status="active", updated="2026-10-27T10:00:00+01:00", rows=(), body=""):
    head = ["---", "tide-plan: 1", f"title: {title}", "tracker: local", "ticket-prefix: PT", "coordinator: claude",
            f"status: {status}", f"updated: {updated}"]
    if card:
        head.append(f"card: {card}")
    table = ["| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |", "| --- | --- | --- | --- | --- | --- |"]
    table += [f"| {t} | {ti} | {st} | {br} | | {no} |" for t, ti, st, br, no in rows]
    return "\n".join(head + ["---", "", f"# {title}", "", "## Cel", "", "Cel planu.", "", *table, "", body, ""])


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="tide-card-docs-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "ws", "tide")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "master")
        commit(self.repo, "README.md", "tide\n")
        self.board = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(self.board, "docs"))
        self.docs = CardDocs(self.board)
        self.project = {"slug": "tide", "repo": self.repo, "aiDocs": "repo"}
        self.plans = os.path.join(self.repo, "docs", "agents", "plans")
        self.specs = os.path.join(self.repo, "docs", "agents", "specs")

    def read(self, card=None, project="default", coordinator=None):
        card = {"id": "K-12", "spec": "", "worktree": "", "derived": None, **(card or {})}
        return self.docs.read(card, self.project if project == "default" else project, coordinator)


class PlanLinkTest(Fixture):
    def test_plan_by_card_key_prefers_active_and_lists_older(self):
        write(os.path.join(self.plans, "2026-10-20-stary.md"),
              plan_text("Stary plan", "K-12", status="done", updated="2026-10-20T10:00:00+01:00"))
        write(os.path.join(self.plans, "2026-10-27-nowy.md"),
              plan_text("Podgląd planu", "k-12", rows=[("PT-1", "Daemon", "gotowe", "feat/pt-1", "review OK"),
                                                       ("PT-2", "Panel", "w-toku", "feat/pt-2", "")]))
        write(os.path.join(self.plans, "2026-10-27-inna.md"), plan_text("Inna karta", "K-13"))
        write(os.path.join(self.plans, "README.md"), "# nie plan\n")
        out = self.read()
        self.assertTrue(out["ok"])
        plan = out["plan"]
        self.assertEqual((plan["title"], plan["status"], plan["link"], plan["where"], plan["card"]),
                         ("Podgląd planu", "active", "card", "repo", "K-12"))
        self.assertEqual(plan["path"], os.path.join(self.plans, "2026-10-27-nowy.md"))
        self.assertEqual(plan["repo"], self.repo)
        self.assertEqual(plan["tickets"], [
            {"id": "PT-1", "title": "Daemon", "state": "gotowe", "branch": "feat/pt-1", "agent": "", "notes": "review OK"},
            {"id": "PT-2", "title": "Panel", "state": "w-toku", "branch": "feat/pt-2", "agent": "", "notes": ""}])
        self.assertNotIn("| Ticket |", plan["markdown"], "tabela ticketów jest osobno")
        self.assertNotIn("# Podgląd planu", plan["markdown"], "tytuł jest w nagłówku panelu")
        self.assertIn("Cel planu.", plan["markdown"])
        self.assertEqual(out["others"], [{"kind": "plan", "path": os.path.join(self.plans, "2026-10-20-stary.md"),
                                          "title": "Stary plan", "status": "done",
                                          "updated": "2026-10-20T10:00:00+01:00"}])
        self.assertIsNone(out["spec"])
        self.assertEqual(out["warnings"], [])

    def test_worktree_copy_with_newer_update_wins(self):
        write(os.path.join(self.plans, "2026-10-27-plan.md"), plan_text("Plan w master", "K-12"))
        commit(self.repo, "docs/agents/plans/2026-10-27-plan.md", plan_text("Plan w master", "K-12"))
        tree = os.path.join(self.tmp, "ws", "tide-wt", "k-12")
        git(self.repo, "worktree", "add", "-q", "-b", "claude/k-12-podglad", tree)
        write(os.path.join(tree, "docs", "agents", "plans", "2026-10-27-plan.md"),
              plan_text("Plan w worktree", "K-12", updated="2026-10-27T12:00:00+01:00"))
        self.assertEqual(docs_mod.worktrees(self.repo), [tree])
        out = self.read()
        self.assertEqual((out["plan"]["title"], out["plan"]["where"]), ("Plan w worktree", "worktree"))
        self.assertEqual(out["others"], [], "ta sama nazwa pliku to jeden plan")

    def test_coordinator_plan_without_card_key(self):
        path = write(os.path.join(self.plans, "2026-10-27-koordynator.md"), plan_text("Plan koordynatora"))
        coordinator = {"id": "claude:1", "cwd": self.repo, "plan": {"path": path}}
        out = self.read({"derived": {"coordinator": "claude:1", "tickets": ["PT-2"]}}, coordinator=coordinator)
        self.assertEqual((out["plan"]["title"], out["plan"]["link"]), ("Plan koordynatora", "coordinator"))
        self.assertEqual(out["plan"]["cardTickets"], ["PT-2"])
        # plan koordynatora poza repozytorium projektu i tablicą — nie pokazujemy go
        outside = write(os.path.join(self.tmp, "gdzie-indziej", "2026-10-27-x.md"), plan_text("Obcy"))
        out = self.read(coordinator={"id": "claude:2", "cwd": self.repo, "plan": {"path": outside}})
        self.assertIsNone(out["plan"])
        self.assertEqual(out["warnings"], ["plan koordynatora leży poza repozytorium projektu i tablicą — pominięty"])

    def test_card_key_wins_over_coordinator_plan(self):
        mine = write(os.path.join(self.plans, "2026-10-27-karta.md"), plan_text("Plan karty", "K-12"))
        other = write(os.path.join(self.plans, "2026-10-27-koordynator.md"), plan_text("Plan koordynatora"))
        out = self.read({"derived": {"coordinator": "claude:1"}},
                        coordinator={"id": "claude:1", "cwd": self.repo, "plan": {"path": other}})
        self.assertEqual((out["plan"]["path"], out["plan"]["link"]), (mine, "card"))

    def test_board_docs_plans_for_ai_docs_tide(self):
        project = {"slug": "dom", "repo": "", "aiDocs": "tide"}
        folder = os.path.join(self.board, "docs", "dom")
        write(os.path.join(folder, "plans", "2026-10-27-dom.md"), plan_text("Plan w tablicy", "K-12"))
        write(os.path.join(folder, "plans", "2026-10-27-obcy.md"), plan_text("Plan innej karty", "K-3"))
        write(os.path.join(self.board, "docs", "inny", "plans", "2026-10-27-x.md"), plan_text("Inny projekt", "K-12"))
        write(os.path.join(folder, "specs", "k-12-dom.md"), "---\ncard: K-12\nstatus: szkic\n---\n\n# Spec domu\n\nTreść.\n")
        out = self.read(project=project)
        self.assertEqual((out["plan"]["title"], out["plan"]["where"], out["plan"]["repo"]), ("Plan w tablicy", "board", ""))
        self.assertEqual((out["spec"]["title"], out["spec"]["status"], out["spec"]["ready"]), ("Spec domu", "szkic", False))
        # karta bez projektu — docs/_inbox
        write(os.path.join(self.board, "docs", "_inbox", "plans", "2026-10-27-inbox.md"), plan_text("Inbox", "K-12"))
        self.assertEqual(self.read(project=None)["plan"]["title"], "Inbox")

    def test_board_docs_and_repo_together(self):
        write(os.path.join(self.board, "docs", "tide", "plans", "2026-10-27-tablica.md"),
              plan_text("Plan z tablicy", "K-12", updated="2026-10-27T15:00:00+01:00"))
        write(os.path.join(self.plans, "2026-10-26-repo.md"), plan_text("Plan z repo", "K-12"))
        out = self.read()
        self.assertEqual(out["plan"]["title"], "Plan z tablicy")
        self.assertEqual([o["title"] for o in out["others"]], ["Plan z repo"])

    def test_no_documents(self):
        out = self.read()
        self.assertEqual(out, {"ok": True, "card": "K-12", "plan": None, "spec": None, "others": [], "warnings": []})
        missing = {"slug": "nic", "repo": os.path.join(self.tmp, "nie-ma"), "aiDocs": "repo"}
        self.assertIsNone(self.read(project=missing)["plan"])


class SpecTest(Fixture):
    def test_spec_field_relative_to_repo(self):
        write(os.path.join(self.repo, "docs", "spec.md"),
              "---\nstatus: gotowa\nupdated: 2026-10-27T09:00:00+01:00\n---\n\n# Specyfikacja\n\n## Wymagania\n\nA.\n")
        out = self.read({"spec": "docs/spec.md"})
        spec = out["spec"]
        self.assertEqual((spec["title"], spec["status"], spec["ready"], spec["link"], spec["updated"]),
                         ("Specyfikacja", "gotowa", True, "field", "2026-10-27T09:00:00+01:00"))
        self.assertTrue(spec["markdown"].startswith("## Wymagania"))

    def test_spec_field_absolute_in_board(self):
        path = write(os.path.join(self.board, "docs", "tide", "specs", "k-12-x.md"), "# Spec z tablicy\n\nB.\n")
        out = self.read({"spec": path})
        self.assertEqual((out["spec"]["path"], out["spec"]["where"], out["spec"]["status"]), (path, "board", ""))

    def test_spec_field_outside_or_missing_falls_back_to_name(self):
        outside = write(os.path.join(self.tmp, "obcy", "spec.md"), "# obcy\n")
        write(os.path.join(self.specs, "k-12-podglad.md"), "---\nstatus: gotowa\n---\n# Po nazwie\n")
        out = self.read({"spec": outside})
        self.assertEqual((out["spec"]["title"], out["spec"]["link"]), ("Po nazwie", "name"))
        self.assertEqual(out["warnings"], [f"specyfikacja z karty ({outside}) leży poza repozytorium projektu i tablicą"
                                           " — pominięta"])
        out = self.read({"spec": "docs/nie-ma.md"})
        self.assertEqual(out["warnings"], ["specyfikacja z karty (docs/nie-ma.md) — nie ma takiego pliku"])
        self.assertEqual(out["spec"]["title"], "Po nazwie")
        out = self.read({"spec": "/etc/passwd"})
        self.assertNotIn("root:", json.dumps(out))

    def test_fallback_by_name_and_card_key_ready_first(self):
        write(os.path.join(self.specs, "k-12-szkic.md"), "---\nstatus: szkic\n---\n# Szkic\n")
        write(os.path.join(self.specs, "eksport.md"), "---\ncard: K-12\nstatus: gotowa\n---\n# Po kluczu\n")
        write(os.path.join(self.specs, "k-120-inna.md"), "# Inna karta\n")
        write(os.path.join(self.specs, "k-1.md"), "# K-1\n")
        os.utime(os.path.join(self.specs, "k-12-szkic.md"), (2_000_000_000, 2_000_000_000))
        out = self.read()
        self.assertEqual((out["spec"]["title"], out["spec"]["link"]), ("Po kluczu", "card"), "gotowa przed nowszym szkicem")
        self.assertEqual([(o["kind"], o["title"], o["status"]) for o in out["others"]], [("spec", "Szkic", "szkic")])


class SafetyTest(Fixture):
    def test_symlink_leaving_roots_is_ignored(self):
        secret = write(os.path.join(self.tmp, "sekret", "2026-10-27-sekret.md"), plan_text("Sekret", "K-12"))
        os.makedirs(self.plans)
        os.symlink(secret, os.path.join(self.plans, "2026-10-27-sekret.md"))
        os.symlink(os.path.dirname(secret), os.path.join(self.repo, "docs", "agents", "specs"))
        write(os.path.join(self.tmp, "sekret", "k-12.md"), "# sekret\n")
        out = self.read()
        self.assertIsNone(out["plan"])
        self.assertIsNone(out["spec"])
        self.assertNotIn("Sekret", json.dumps(out, ensure_ascii=False))
        # dowiązanie wewnątrz repozytorium działa
        inside = write(os.path.join(self.repo, "notes", "plan.md"), plan_text("Wewnątrz", "K-12"))
        os.symlink(inside, os.path.join(self.plans, "2026-10-27-wewnatrz.md"))
        self.assertEqual(self.read()["plan"]["title"], "Wewnątrz")

    def test_only_regular_markdown_files(self):
        os.makedirs(self.specs)
        os.mkfifo(os.path.join(self.specs, "k-12-fifo.md"))
        write(os.path.join(self.repo, "skrypt.sh"), "#!/bin/sh\necho x\n")
        out = self.read({"spec": "skrypt.sh"})
        self.assertIsNone(out["spec"])
        self.assertEqual(out["warnings"], ["specyfikacja z karty (skrypt.sh) leży poza repozytorium projektu i tablicą"
                                           " — pominięta"])

    def test_oversize_is_truncated_with_note(self):
        line = "Linia planu z treścią, która się powtarza — ąęś.\n"
        big = plan_text("Duży plan", "K-12", rows=[("PT-1", "A", "todo", "", "")]) + line * 12_000
        self.assertGreater(len(big.encode()), docs_mod.DOC_MAX)
        write(os.path.join(self.plans, "2026-10-27-duzy.md"), big)
        plan = self.read()["plan"]
        self.assertTrue(plan["truncated"])
        self.assertEqual([t["id"] for t in plan["tickets"]], ["PT-1"])
        self.assertTrue(plan["markdown"].endswith(docs_mod.TRUNCATED_NOTE))
        self.assertLess(len(plan["markdown"].encode()), docs_mod.DOC_MAX + 1000)
        self.assertNotIn("�", plan["markdown"])

    def test_cache_reads_file_again_only_after_change(self):
        path = write(os.path.join(self.plans, "2026-10-27-plan.md"), plan_text("Pierwszy", "K-12"))
        self.read()
        real_open = open
        opened = []

        def spy(file, *args, **kwargs):
            opened.append(file)
            return real_open(file, *args, **kwargs)

        with mock.patch("builtins.open", spy):
            self.assertEqual(self.read()["plan"]["title"], "Pierwszy")
            self.assertNotIn(path, opened, "plik bez zmian — z pamięci")
            write(path, plan_text("Drugi tytuł", "K-12"))
            os.utime(path, (2_000_000_000, 2_000_000_000))
            self.assertEqual(self.read()["plan"]["title"], "Drugi tytuł")
        self.assertIn(path, opened)

    def test_display_is_computed_once_per_file_version(self):
        path = write(os.path.join(self.plans, "2026-10-27-plan.md"), plan_text("Plan", "K-12"))
        with mock.patch.object(docs_mod, "manual_section", wraps=docs_mod.manual_section) as spy:
            self.read()
            self.read()
            self.assertEqual(spy.call_count, 1, "treść do pokazania z pamięci, póki plik się nie zmienił")
            write(path, plan_text("Plan 2", "K-12"))
            os.utime(path, (2_000_000_000, 2_000_000_000))
            self.assertEqual(self.read()["plan"]["title"], "Plan 2")
            self.assertEqual(spy.call_count, 2)


class MarkdownTest(unittest.TestCase):
    def test_markdown_is_source_without_sanitising(self):
        # Oczyszczanie robi okno parserem Qt (apps/board/backend.py: render_markdown) — tu treść źródłowa.
        with tempfile.TemporaryDirectory(prefix="tide-card-docs-raw-") as tmp:
            write(os.path.join(tmp, "docs", "_inbox", "specs", "k-5.md"), "# T\n\na ![x](http://e/x.png) <b>c</b>\n")
            spec = CardDocs(tmp).read({"id": "K-5"})["spec"]
        self.assertEqual(spec["markdown"], "a ![x](http://e/x.png) <b>c</b>")

    def test_pathological_long_lines_are_linear(self):
        # Linia 512 KB z „![”, „<!--”, „[”, „](”, „`a”, „**” — wcześniej wyrażenia kwadratowe (minuty).
        import time
        for unit in ("![", "<!--", "[", "](", "`a", "**", "<http://", "| Ticket | Tytuł | Stan |"):
            line = unit * ((docs_mod.DOC_MAX - 300) // len(unit))
            with tempfile.TemporaryDirectory(prefix="tide-card-docs-long-") as tmp:
                write(os.path.join(tmp, "docs", "_inbox", "plans", "2026-10-27-x.md"),
                      plan_text("Długi", "K-1", rows=[("PT-1", line[:100_000], "todo", "", "")]) + line + "\n")
                write(os.path.join(tmp, "docs", "_inbox", "specs", "k-1.md"), "## Co przetestować ręcznie\n\n" + line)
                start = time.monotonic()
                out = CardDocs(tmp).read({"id": "K-1"})
                elapsed = time.monotonic() - start
            self.assertTrue(out["plan"] and out["spec"], unit)
            self.assertLess(elapsed, 5.0, f"{unit!r}: {elapsed:.2f} s")

    def test_manual_section_and_ticket_table(self):
        text = ("## Zakres\n\nA\n\n## Co przetestować ręcznie i jak\n\n1. Otwórz panel.\n\n### Szczegóły\n\nx\n\n"
                "```\n## Co przetestować ręcznie w kodzie\n```\n\n## Dalej\n\nB\n")
        rest, manual = manual_section(text)
        self.assertEqual(manual, "1. Otwórz panel.\n\n### Szczegóły\n\nx\n\n```\n## Co przetestować ręcznie w kodzie\n```")
        self.assertIn("## Dalej", rest)
        self.assertNotIn("Otwórz panel", rest)
        self.assertEqual(manual_section("bez sekcji"), ("bez sekcji", ""))
        body = "przed\n\n| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |\n|---|---|---|---|---|---|\n| A | b | todo | | | |\n\npo\n" \
               "\n| Inna | tabela |\n|---|---|\n| 1 | 2 |\n"
        self.assertEqual(strip_ticket_table(body), "przed\n\n\npo\n\n| Inna | tabela |\n|---|---|\n| 1 | 2 |\n")

    def test_manual_section_is_shown_separately(self):
        with tempfile.TemporaryDirectory(prefix="tide-card-docs-md-") as tmp:
            board = os.path.join(tmp, "board")
            write(os.path.join(board, "docs", "_inbox", "specs", "k-5.md"),
                  "# Spec\n\n## Wymagania\n\nA\n\n## Co przetestować ręcznie\n\n- Kliknij <b>x</b>.\n")
            spec = CardDocs(board).read({"id": "K-5"})["spec"]
        self.assertEqual(spec["manualTest"], "- Kliknij <b>x</b>.")
        self.assertNotIn("Co przetestować", spec["markdown"])


class MonitorCardDocsTest(unittest.TestCase):
    """BoardMonitor.card_docs: odczyt w wątku roboczym, odpowiedź w wątku głównym, plan koordynatora."""

    def setUp(self):
        app()
        from tide_daemon.board.store import BoardStore
        from tide_daemon.board_monitor import BoardMonitor
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="tide-card-docs-mon-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        config = os.path.join(self.tmp, "gitconfig")
        write(config, "[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": config, "GIT_CONFIG_NOSYSTEM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = os.path.join(self.tmp, "ws", "dom")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "master")
        self.plan = write(os.path.join(self.repo, "docs", "agents", "plans", "2026-10-27-dom.md"), plan_text("Plan domu"))
        self.boards = [{"id": "claude:abc", "cwd": self.repo, "repoPath": self.repo, "plan": {"path": self.plan}}]
        derived = {"column": "progress", "coordinator": "claude:abc", "session": "claude:abc", "tickets": []}
        self.monitor = BoardMonitor(BoardStore(os.path.join(self.tmp, "board")), poll_ms=60_000,
                                    derive=lambda card: derived if card["id"] == "K-1" else None,
                                    coordinators=lambda: self.boards)
        self.addCleanup(self.monitor.stop)
        self.monitor.create_card(json.dumps({"title": "Dom", "repo": self.repo}))
        self.monitor.create_card(json.dumps({"title": "Bez planu"}))

    def ask(self, card_id):
        replies = []
        self.monitor.card_docs(card_id, replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=5))
        return json.loads(replies[0])

    def test_reply_after_worker_with_coordinator_plan(self):
        out = self.ask("k-1")
        self.assertEqual((out["ok"], out["card"], out["plan"]["title"], out["plan"]["link"]),
                         (True, "K-1", "Plan domu", "coordinator"))
        self.assertIsNone(self.ask("K-2")["plan"])

    def test_requests_for_the_same_card_share_one_read(self):
        calls = []
        real = self.monitor.docs.read

        def slow(*args):
            calls.append(args[0]["id"])
            import time
            time.sleep(0.3)
            return real(*args)

        self.monitor.docs.read = slow
        replies = []
        for _ in range(4):
            self.monitor.card_docs("K-1", replies.append)   # odświeżanie co 5 s i Changed w czasie odczytu
        self.monitor.card_docs("K-2", replies.append)
        self.assertTrue(wait_until(lambda: len(replies) == 5, timeout=5))
        self.assertEqual(sorted(calls), ["K-1", "K-2"], "jeden odczyt na kartę, reszta czeka na ten sam wynik")
        self.assertEqual(len({r for r in replies if "Plan domu" in r}), 1)
        self.assertEqual(sum("Plan domu" in r for r in replies), 4)
        self.monitor.card_docs("K-1", replies.append)   # po odpowiedzi — nowy odczyt
        self.assertTrue(wait_until(lambda: len(replies) == 6, timeout=5))
        self.assertEqual(calls.count("K-1"), 2)

    def test_errors(self):
        replies = []
        self.monitor.card_docs("K-99", replies.append)
        self.monitor.card_docs("abc", replies.append)
        self.assertEqual([json.loads(r)["error"] for r in replies], ["not-found", "bad-request"], "od razu, bez wątku")
        # koordynatorzy niedostępni — plan z klucza i specyfikacja dalej działają
        self.monitor.coordinators = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        self.assertIsNone(self.ask("K-1")["plan"])


if __name__ == "__main__":
    unittest.main()
