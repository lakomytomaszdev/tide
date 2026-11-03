"""Kontrola numerów ticketów (coordination/numbering.py, repoplans.py), lista projektów YouTracka,
pasek na tablicy, pozycja w raporcie porannym i tools/check_plans.py — na fixtures, bez sieci
(YouTrack to atrapa serwera HTTP na 127.0.0.1).
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import coordfix as F
from .gitrepos import git
from tide_daemon.coordination import board as board_mod, numbering, plan as plan_mod, reservations, youtrack
from tide_daemon.coordination.model import Coordinator
from tide_daemon.coordination.repoplans import RepoPlans
from tide_daemon.coordination.scanner import CoordinationScanner
from tide_daemon.reports import plans as report_plans, standup

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HEADER = "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |\n| --- | --- | --- | --- | --- | --- |\n"


def plan_text(title, rows, tracker="local", prefix="PT", project="", status="active",
              updated="2026-10-27T10:00:00+01:00") -> str:
    url = "http://yt.local" if tracker == "youtrack" else ""
    body = "".join(f"| {ident} | {name} | todo | | | |\n" for ident, name in rows)
    return (f"---\ntide-plan: 1\ntitle: {title}\ntracker: {tracker}\ntracker-url: {url}\n"
            f"tracker-project: {project}\nticket-prefix: {prefix}\ncoordinator: claude\nstatus: {status}\n"
            f"updated: {updated}\n---\n\n{HEADER}{body}")


def plan(path, title, rows, **kwargs):
    return plan_mod.parse(plan_text(title, rows, **kwargs), path)


def numbered(prefix, first, last, word="Zadanie"):
    return [(f"{prefix}-{n}", f"{word} {n}") for n in range(first, last + 1)]


def kinds(warnings):
    return [(w["kind"], w["severity"], w["ticket"] or w["prefix"]) for w in warnings]


class ConflictsTest(unittest.TestCase):
    def test_same_ticket_different_titles_in_two_plans_even_when_done(self):
        old = plan("/r/docs/agents/plans/2026-10-20-old.md", "Stary", [("PT-3", "Eksport CSV")], status="done")
        new = plan("/r/docs/agents/plans/2026-10-27-new.md", "Nowy", [("PT-03", "Ciemny motyw")])
        (warning,) = numbering.conflicts([old, new])
        self.assertEqual(kinds([warning]), [("ticket-title", "hard", "PT-3")])
        self.assertEqual(warning["plans"], ["2026-10-20-old", "2026-10-27-new"])
        self.assertIn("„Eksport CSV” (2026-10-20-old)", warning["text"])
        self.assertIn("„Ciemny motyw” (2026-10-27-new)", warning["text"])

    def test_real_case_plan_in_other_worktree_occupies_numbers(self):
        # Plan w worktree pt-13-local-models zajmuje PT-13…21, a nowy plan chciał zacząć od PT-13.
        models = plan("/ws/plasma-tide-worktrees/pt-13-local-models/docs/agents/plans/2026-10-27-local-models.md",
                      "Lokalne modele", numbered("PT", 13, 21, "Model"), updated="2026-10-27T19:00:00+01:00")
        board = plan("/ws/plasma-tide-worktrees/board/docs/agents/plans/2026-10-27-board-and-docker.md",
                     "Tablica pracy i Docker", numbered("PT", 1, 12, "Tablica"))
        other = plan("/ws/plasma-tide/docs/agents/plans/2026-10-28-next.md", "Następny",
                     [("PT-13", "Coś innego"), ("PT-14", "Jeszcze coś")])
        warnings = numbering.conflicts([models, board, other])
        self.assertEqual(kinds(numbering.hard(warnings)), [("ticket-title", "hard", "PT-13"),
                                                           ("ticket-title", "hard", "PT-14")])
        self.assertEqual(numbering.conflicts([models, board]), [], "ciągła numeracja w dwóch planach jest OK")

    def test_same_title_and_copies_of_one_plan_are_not_conflicts(self):
        first = plan("/r/docs/agents/plans/2026-10-26-a.md", "A", [("PT-1", "Tablica"), ("PT-2", "Docker")])
        carry = plan("/r/docs/agents/plans/2026-10-27-b.md", "B", [("PT-2", "Docker."), ("PT-3", "Okno")])
        # Ten sam plik w innym worktree i na gałęzi — starsza wersja z innym tytułem ticketu.
        copy = plan("/wt/docs/agents/plans/2026-10-27-b.md", "B", [("PT-3", "Okno sesji"), ("PT-4", "Raport")],
                    updated="2026-10-27T09:00:00+01:00")
        branch = plan("feat/pt-3:docs/agents/plans/2026-10-27-b.md", "B", [("PT-3", "Okno w QML")])
        # Przemianowany plik z tym samym tytułem planu.
        renamed = plan("/r/docs/agents/plans/2026-10-27-b-renamed.md", "B", [("PT-3", "Okno (stare)")])
        self.assertEqual(numbering.conflicts([first, carry, copy, branch, renamed]), [])

    def test_two_youtrack_plans_may_phrase_one_ticket_differently(self):
        a = plan("/r/docs/agents/plans/2026-10-26-a.md", "A", [("LOG-7", "Filtr")], tracker="youtrack",
                 prefix="LOG", project="LOG")
        b = plan("/r/docs/agents/plans/2026-10-27-b.md", "B", [("LOG-7", "Filtr wartości logu")], tracker="youtrack",
                 prefix="LOG", project="LOG")
        self.assertEqual(numbering.conflicts([a, b], tracker_projects={"LOG"}), [])

    def test_local_prefix_that_is_a_youtrack_project(self):
        local = plan("/r/docs/agents/plans/2026-10-26-board.md", "Tablica", numbered("TIDE", 1, 3), prefix="TIDE")
        self.assertEqual(numbering.conflicts([local]), [], "bez listy projektów kontrola jest pominięta")
        self.assertEqual(numbering.conflicts([local], tracker_projects={"LOG", "LEGAL"}), [])
        (warning,) = numbering.conflicts([local], tracker_projects={"log", "TIDE"})
        self.assertEqual(kinds([warning]), [("local-prefix-tracker", "hard", "TIDE")])
        self.assertIn("Prefiks lokalny TIDE (2026-10-26-board) jest kluczem projektu w YouTracku", warning["text"])
        declared = {"prefix": "CB", "tracker": "local", "project": "", "source": "CLAUDE.md"}
        (warning,) = numbering.conflicts([], tracker_projects={"CB", "CHEF"}, declared=declared)
        self.assertEqual((warning["kind"], warning["plans"]), ("local-prefix-tracker", ["CLAUDE.md"]))
        self.assertEqual(numbering.conflicts([], tracker_projects={"CHEF"}, declared=declared), [],
                         "lokalny CB i pusty projekt CHEF w YouTracku to nie kolizja numerów")

    def test_youtrack_alias_without_migration_map(self):
        alias = plan("/r/docs/agents/plans/2026-10-27-la035.md", "LA-035", [("LA-035", "Art. 58")],
                     tracker="youtrack", prefix="LA", project="LEGAL")
        (warning,) = numbering.conflicts([alias], tracker_projects={"LEGAL"})
        self.assertEqual(kinds([warning]), [("prefix-alias", "hard", "LA")])
        self.assertIn("ticket-prefix LA ≠ tracker-project LEGAL", warning["text"])
        self.assertEqual(numbering.conflicts([alias], aliases={"LA-35": "LEGAL-35"}), [],
                         "mapa migracji wyłącza ostrzeżenie aliasu")
        same = plan("/r/docs/agents/plans/2026-10-27-legal.md", "LEGAL", [("LEGAL-35", "Art. 58")],
                    tracker="youtrack", prefix="LEGAL", project="LEGAL")
        self.assertEqual(numbering.conflicts([same], tracker_projects={"LEGAL"}), [])
        declared = {"prefix": "LA", "tracker": "youtrack", "project": "LEGAL", "source": "AGENTS.md"}
        self.assertEqual(kinds(numbering.conflicts([], declared=declared)), [("prefix-alias", "hard", "LA")])

    def test_new_local_plan_restarting_from_one_is_a_soft_warning(self):
        old = plan("/r/docs/agents/plans/2026-10-20-old.md", "Stary", numbered("PT", 10, 12), status="done")
        new = plan("/r/docs/agents/plans/2026-10-27-new.md", "Nowy", [("PT-1", "Raport"), ("PT-2", "Widżet")])
        (warning,) = numbering.conflicts([old, new])
        self.assertEqual(kinds([warning]), [("numbering-restart", "soft", "PT-1")])
        self.assertIn("następny numer to PT-13", warning["text"])
        self.assertEqual(numbering.hard([warning]), [])
        done = plan("/r/docs/agents/plans/2026-10-27-new.md", "Nowy", [("PT-1", "Raport")], status="done")
        self.assertEqual(numbering.conflicts([old, done]), [], "zakończonego planu już nie poprawiamy")
        good = plan("/r/docs/agents/plans/2026-10-27-new.md", "Nowy", [("PT-12", "Zadanie 12"), ("PT-13", "Raport")])
        self.assertEqual(numbering.conflicts([old, good]), [], "przeniesiony ticket i dalsza numeracja są OK")

    def test_steps_without_ids_and_none_tracker_are_ignored(self):
        steps = plan("/r/docs/agents/plans/2026-10-27-steps.md", "Kroki", [("krok 1", "A"), ("krok 2", "B")],
                     tracker="none", prefix="")
        self.assertEqual(numbering.conflicts([steps], tracker_projects={"PT"}), [])

    def test_declared_prefix_from_claude_md_section(self):
        text = ("# Projekt\n\nticket-prefix: XX poza sekcją\n\n## Plan pracy agentów (Tide)\n\n"
                "2. Frontmatter: `tide-plan: 1`, `tracker: youtrack`, `tracker-url: http://yt`, "
                "`tracker-project: LEGAL`, `ticket-prefix: LA`, `coordinator`.\n\n## Inne\n\nticket-prefix: ZZ\n")
        self.assertEqual(numbering.declared_prefix(text), {"prefix": "LA", "tracker": "youtrack", "project": "LEGAL"})
        english = "## Agent work plan (Tide)\n\n2. Frontmatter: `tracker: local`, empty `tracker-url` and " \
                  "`tracker-project`, `ticket-prefix: CB`, `coordinator`.\n"
        self.assertEqual(numbering.declared_prefix(english), {"prefix": "CB", "tracker": "local", "project": ""})
        self.assertEqual(numbering.declared_prefix("# Bez sekcji\nticket-prefix: AB\n"),
                         {"prefix": "", "tracker": "", "project": ""})


class RepoPlansTest(unittest.TestCase):
    """Prawdziwe repozytorium git: główny katalog, worktree z niezacommitowanym planem, gałąź z planem."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-numbering-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = os.path.join(self.tmp, "ws", "proj")
        os.makedirs(os.path.join(self.repo, plan_mod.PLANS_DIR))
        git(self.repo, "init", "-q", "-b", "master")
        self.write(self.repo, "2026-10-26-board.md", plan_text("Tablica", numbered("PT", 1, 12)))
        with open(os.path.join(self.repo, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write("## Plan pracy agentów (Tide)\n\n2. Frontmatter: `tracker: local`, `ticket-prefix: PT`.\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        # Gałąź z planem, którego nie ma w żadnym katalogu roboczym.
        git(self.repo, "checkout", "-q", "-b", "feat/pt-30-branch-only")
        self.write(self.repo, "2026-10-27-branch-only.md", plan_text("Tylko na gałęzi", numbered("PT", 30, 31)))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "plan")
        git(self.repo, "checkout", "-q", "master")
        # Worktree z niezacommitowanym planem PT-13…21 i kopią planu z głównego katalogu.
        self.worktree = os.path.join(self.tmp, "ws", "proj-worktrees", "pt-13-local-models")
        git(self.repo, "worktree", "add", "-q", "-b", "feat/pt-13-local-models", self.worktree)
        self.write(self.worktree, "2026-10-27-local-models.md", plan_text("Lokalne modele", numbered("PT", 13, 21)))

    def write(self, root, name, text):
        with open(os.path.join(root, plan_mod.PLANS_DIR, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_plans_from_every_worktree_and_branch_counted_once(self):
        plans, declared = RepoPlans(ttl=0).read([self.repo])
        names = sorted(os.path.basename(p.path) for p in plans)
        self.assertEqual(names, ["2026-10-26-board.md", "2026-10-27-branch-only.md", "2026-10-27-local-models.md"],
                         "ten sam plan w głównym katalogu, worktree i gałęziach liczy się raz")
        branch = next(p for p in plans if "branch-only" in p.path)
        self.assertTrue(branch.path.startswith("feat/pt-30-branch-only:docs/agents/plans/"))
        self.assertEqual(declared, {"prefix": "PT", "tracker": "local", "project": "", "source": "CLAUDE.md"})
        self.assertEqual(numbering.conflicts(plans, declared=declared), [])
        # Inny plan w głównym katalogu chce zacząć od PT-13 — numer zajmuje plan z worktree.
        self.write(self.repo, "2026-10-28-next.md", plan_text("Następny", [("PT-13", "Raport")]))
        plans, declared = RepoPlans(ttl=0).read([self.worktree, self.repo])
        self.assertEqual(kinds(numbering.conflicts(plans, declared=declared)), [("ticket-title", "hard", "PT-13")])

    def test_cache_and_no_git(self):
        calls = []

        def counting(root, args, stdin=None):
            calls.append(args[0])
            from tide_daemon.coordination.repoplans import run_git
            return run_git(root, args, stdin)
        now = [100.0]
        reader = RepoPlans(git=counting, clock=lambda: now[0], ttl=60)
        reader.read([self.repo])
        first = len(calls)
        reader.read([self.repo])
        self.assertEqual(len(calls), first, "wynik repozytorium trzymany przez ttl")
        now[0] += 61
        reader.read([self.repo])
        self.assertEqual(calls[first:].count("ls-tree"), 0, "znane commity bez ponownego ls-tree")
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(os.path.join(plain, plan_mod.PLANS_DIR))
        self.write(plain, "2026-10-27-x.md", plan_text("X", [("AB-1", "a")], prefix="AB"))
        plans, declared = RepoPlans(ttl=0).read([plain])
        self.assertEqual([p.title for p in plans], ["X"], "bez gita — sam katalog")
        self.assertEqual(declared, {})


class FakeProjects(BaseHTTPRequestHandler):
    calls: list = []
    status = 200

    def do_GET(self):
        FakeProjects.calls.append((self.command, self.path, self.headers.get("Authorization")))
        if FakeProjects.status != 200:
            self.send_response(FakeProjects.status)
            self.end_headers()
            return
        body = json.dumps([{"shortName": "LOG", "$type": "Project"}, {"shortName": "tide"}, {"name": "x"}]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TrackerProjectsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-numbering-yt-")
        self.addCleanup(shutil.rmtree, self.tmp)
        FakeProjects.calls, FakeProjects.status = [], 200
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProjects)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        token = os.path.join(self.tmp, "token")
        with open(token, "w", encoding="utf-8") as fh:
            fh.write(F.TOKEN_SENTINEL)
        self.config = {"url": self.url, "tokenFile": token, "tokenVar": ""}

    def test_project_keys_get_only_cached_for_an_hour(self):
        now = [F.NOW]
        cache = youtrack.YouTrackCache(config_loader=lambda: dict(self.config), clock=lambda: now[0])
        keys, error = cache.projects()
        self.assertEqual((keys, error), ({"LOG", "TIDE"}, ""))
        method, path, auth = FakeProjects.calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(path.startswith("/api/admin/projects?fields=shortName"))
        self.assertEqual(auth, "Bearer " + F.TOKEN_SENTINEL)
        now[0] += youtrack.TTL + 1
        cache.projects()
        self.assertEqual(len(FakeProjects.calls), 1, "lista projektów odświeżana rzadko")
        now[0] += youtrack.PROJECTS_TTL
        FakeProjects.status = 401
        keys, error = cache.projects()
        self.assertEqual((keys, error), ({"LOG", "TIDE"}, "YouTrack odpowiedział 401"), "ostatnia dobra lista")
        self.assertNotIn(F.TOKEN_SENTINEL, error)
        now[0] += youtrack.PROJECTS_RETRY - 1
        cache.projects()
        self.assertEqual(len(FakeProjects.calls), 2, "po błędzie — przerwa")

    def test_without_token_nothing_is_sent(self):
        self.config["tokenFile"] = os.path.join(self.tmp, "nie-ma")
        keys, error = youtrack.YouTrackCache(config_loader=lambda: dict(self.config)).projects()
        self.assertIsNone(keys)
        self.assertTrue(error.startswith("brak tokenu YouTracka (sprawdzone: "), error)
        self.assertEqual(FakeProjects.calls, [])


class TrackerStub:
    def __init__(self, keys):
        self.keys = keys
        self.asked = 0

    def projects(self, force=False):
        self.asked += 1
        return self.keys, ""

    def get(self, url, project, force=False):
        return None, {"kind": "youtrack", "url": url, "project": project, "available": False, "error": "x"}


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-numbering-board-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_board_and_summary_carry_warnings(self):
        coord = Coordinator(id="claude:x", tool="claude", path="/x.jsonl", session_id="x", cwd="/r")
        warning = {"kind": "ticket-title", "severity": "hard", "ticket": "PT-3", "prefix": "PT",
                   "plans": ["a", "b"], "text": "PT-3 ma różne tytuły"}
        board = board_mod.build(coord, warnings=[warning], now=F.NOW)
        self.assertEqual(board["warnings"], [warning])
        self.assertEqual(board_mod.summary(board)["warnings"], [warning])
        self.assertEqual(board_mod.build(coord, now=F.NOW)["warnings"], [])

    def test_scanner_warnings_ask_youtrack_only_for_local_plans(self):
        repo = os.path.join(self.tmp, "repo")
        folder = os.path.join(repo, plan_mod.PLANS_DIR)
        os.makedirs(folder)
        with open(os.path.join(folder, "2026-10-26-board.md"), "w", encoding="utf-8") as fh:
            fh.write(plan_text("Tablica", numbered("TIDE", 1, 2), prefix="TIDE"))
        tracker = TrackerStub({"TIDE"})
        scanner = CoordinationScanner(self.tmp, self.tmp, clock=lambda: F.NOW, tracker=tracker)
        self.assertEqual(kinds(scanner.warnings([repo])), [("local-prefix-tracker", "hard", "TIDE")])
        self.assertEqual(kinds(CoordinationScanner(self.tmp, self.tmp, clock=lambda: F.NOW).warnings([repo])), [],
                         "bez YouTracka kontrola prefiksu jest pominięta")
        with open(os.path.join(folder, "2026-10-26-board.md"), "w", encoding="utf-8") as fh:
            fh.write(plan_text("Tablica", [("LOG-1", "a")], tracker="youtrack", prefix="LOG", project="LOG"))
        other = TrackerStub({"LOG"})
        CoordinationScanner(self.tmp, self.tmp, clock=lambda: F.NOW, tracker=other).warnings([repo])
        self.assertEqual(other.asked, 0, "bez planów lokalnych lista projektów niepotrzebna")


class ReportTest(unittest.TestCase):
    def test_attach_sets_conflicts_and_waiting_shows_them(self):
        tmp = tempfile.mkdtemp(prefix="tide-numbering-report-")
        self.addCleanup(shutil.rmtree, tmp)
        folder = os.path.join(tmp, plan_mod.PLANS_DIR)
        os.makedirs(folder)
        with open(os.path.join(folder, "2026-10-20-old.md"), "w", encoding="utf-8") as fh:
            fh.write(plan_text("Stary", [("PT-3", "Eksport CSV")], status="done"))
        with open(os.path.join(folder, "2026-10-27-new.md"), "w", encoding="utf-8") as fh:
            fh.write(plan_text("Nowy", [("PT-3", "Ciemny motyw")]))
        project = types.SimpleNamespace(name="proj", path=tmp, is_repo=True, plans=[])
        report_plans.attach([project], 0, F.NOW, None, TrackerStub({"LOG"}))
        self.assertEqual(kinds(project.conflicts), [("ticket-title", "hard", "PT-3")])
        quiet = types.SimpleNamespace(name="x", path=None, is_repo=False, plans=[])
        report_plans.attach([quiet], 0, F.NOW, None, None)
        self.assertEqual(quiet.conflicts, [])

        fact = {"project": "proj", "tickets": [], "units": [], "questions": [], "plans": [],
                "conflicts": [w["text"] for w in project.conflicts]}
        rules = standup.rules(fact)
        self.assertEqual(rules["waiting"], [], "konflikt numerów to nie „czeka” — nikt na niego nie stoi")
        attention = rules["attention"]
        self.assertEqual(len(attention), 1)
        self.assertTrue(attention[0]["text"].startswith("Konflikt numerów: PT-3 ma różne tytuły"), attention)
        model = {"proj": {"headline": "x", "theme": "", "done": [], "doing": [], "next": [], "chores": [],
                          "waiting": [{"ref": "", "text": "Odpowiedz na pytanie o termin"}]}}
        merged = standup.merge(model, [fact])["proj"]
        self.assertEqual(merged["waiting"], [], "„Czeka na mnie” zawsze z reguł (PT-23 C): pozycja modelu bez ref odpada")
        self.assertEqual([i["text"][:17] for i in merged["attention"]], ["Konflikt numerów:"],
                         "model faktów o konfliktach nie dostaje — pozycja z reguł zawsze jest, w „Uwaga”")


class ReservationTest(unittest.TestCase):
    """tools/next_id.py i reservations.py na tymczasowym rejestrze (nigdy na prawdziwym)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-next-id-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.registry = os.path.join(self.tmp, "state", "tide", "ticket-ids.json")
        self.env = dict(os.environ, XDG_STATE_HOME=os.path.join(self.tmp, "state"))
        self.repo = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.repo, plan_mod.PLANS_DIR))
        git(self.repo, "init", "-q", "-b", "master")
        with open(os.path.join(self.repo, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write("## Plan pracy agentów (Tide)\n\n2. Frontmatter: `tracker: local`, `ticket-prefix: PT`.\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        self.worktree = os.path.join(self.tmp, "proj-wt")
        git(self.repo, "worktree", "add", "-q", "-b", "feat/pt-13", self.worktree)
        os.makedirs(os.path.join(self.worktree, plan_mod.PLANS_DIR))  # pusty katalog nie jest w commicie
        with open(os.path.join(self.worktree, plan_mod.PLANS_DIR, "2026-10-27-models.md"), "w", encoding="utf-8") as fh:
            fh.write(plan_text("Modele", numbered("PT", 13, 21)))

    def next_id(self, *args, repo=None):
        return subprocess.run(["/usr/bin/python3", os.path.join(REPO, "tools", "next_id.py"), repo or self.repo, *args],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def test_retry_after_registry_write_returns_original_reservation(self):
        first = reservations.reserve(self.repo, count=2, title="Raport", path=self.registry,
                                     request_key="card-K-12-reserve")
        retried = reservations.reserve(self.worktree, count=2, title="Raport", path=self.registry,
                                       request_key="card-K-12-reserve")
        self.assertEqual(first["ids"], ["PT-22", "PT-23"])
        self.assertEqual(retried["ids"], first["ids"])
        self.assertEqual(retried["numbers"], first["numbers"])
        with open(self.registry, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["reservations"]), 2)

    def test_imported_floor_keeps_old_id_and_retry_is_idempotent(self):
        first = reservations.reserve(self.repo, title="Nowy ticket", path=self.registry,
                                     request_key="K-1:import:#1", at_least=30)
        retried = reservations.reserve(self.repo, title="Nowy ticket", path=self.registry,
                                       request_key="K-1:import:#1", at_least=30)
        self.assertEqual(first["ids"], ["PT-31"])
        self.assertEqual(retried["ids"], first["ids"])

    def test_parallel_retries_of_one_request_reserve_only_once(self):
        barrier = threading.Barrier(8)

        def reserve_once(_):
            barrier.wait(timeout=30)
            return reservations.reserve(self.repo, title="Karta", path=self.registry,
                                        request_key="K-1:launch-1")

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve_once, range(8)))
        self.assertEqual([item["ids"] for item in results], [["PT-22"]] * 8)
        with open(self.registry, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["reservations"]), 1)

    def test_key_reuse_for_different_title_or_count_is_refused(self):
        first = reservations.reserve(self.repo, title="A" * 200 + "X", path=self.registry,
                                     request_key="K-1:launch-1")
        for title, count in (("A" * 200 + "Y", 1), ("A" * 200 + "X", 2)):
            with self.subTest(title=title[-1], count=count):
                with self.assertRaises(reservations.ReservationError):
                    reservations.reserve(self.repo, title=title, count=count, path=self.registry,
                                         request_key="K-1:launch-1")
        self.assertEqual(reservations.reserve(self.repo, title="Nowa", path=self.registry)["ids"], ["PT-23"])
        self.assertEqual(first["ids"], ["PT-22"])

    def test_invalid_request_keys_do_not_write_registry(self):
        for key in (" K-1", "K-1 ", "K 1", "K\n1", "X" * 129, 123):
            with self.subTest(key=key):
                with self.assertRaises(reservations.ReservationError):
                    reservations.reserve(self.repo, path=self.registry, request_key=key)
        self.assertFalse(os.path.exists(self.registry))

    def test_calls_without_key_keep_allocating_new_numbers(self):
        first = reservations.reserve(self.repo, title="Karta", path=self.registry)
        second = reservations.reserve(self.repo, title="Karta", path=self.registry)
        keyed = reservations.reserve(self.repo, title="Karta", path=self.registry, request_key="K-1:launch-1")
        self.assertEqual([first["ids"], second["ids"], keyed["ids"]],
                         [["PT-22"], ["PT-23"], ["PT-24"]])
        self.assertEqual(reservations.reserve(self.repo, title="Karta", path=self.registry,
                                              request_key="K-1:launch-1")["ids"], ["PT-24"])

    def test_empty_registry_is_seeded_from_plans_and_reservations_continue(self):
        dry = self.next_id("--dry-run")
        self.assertEqual((dry.returncode, dry.stdout), (0, "PT-22\n"), dry.stderr)
        self.assertIn("nic nie zarezerwowano", dry.stderr)
        self.assertFalse(os.path.exists(self.registry), "--dry-run nie tworzy rejestru")
        first = self.next_id("--count", "2", "--title", "Raport tygodniowy")
        self.assertEqual(first.stdout, "PT-22\nPT-23\n", first.stderr)
        self.assertEqual(os.stat(self.registry).st_mode & 0o777, 0o600)
        from_worktree = self.next_id("--title", "Okno", repo=self.worktree)
        self.assertEqual(from_worktree.stdout, "PT-24\n", "worktree dzieli numerację z głównym katalogiem")
        with open(self.registry, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual([(r["repo"], r["prefix"], r["number"], r["title"]) for r in data["reservations"]],
                         [(self.repo, "PT", 22, "Raport tygodniowy"), (self.repo, "PT", 23, "Raport tygodniowy"),
                          (self.repo, "PT", 24, "Okno")])
        self.assertTrue(all(r["ts"] for r in data["reservations"]))
        self.assertEqual(self.next_id("--prefix", "XY").stdout, "XY-1\n", "nowy prefiks zaczyna od 1")

    def test_parallel_processes_get_different_numbers(self):
        procs = [subprocess.Popen(["/usr/bin/python3", os.path.join(REPO, "tools", "next_id.py"), self.repo,
                                   "--title", f"Zadanie {i}"], env=self.env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for i in range(8)]
        got = []
        for proc in procs:
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
            got.append(out.strip())
        self.assertEqual(sorted(got, key=lambda k: int(k.split("-")[1])), [f"PT-{n}" for n in range(22, 30)])
        with open(self.registry, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["reservations"]), 8, "żadna rezerwacja nie zginęła")

    def test_youtrack_prefix_is_refused(self):
        with open(os.path.join(self.repo, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write("## Plan pracy agentów (Tide)\n\n2. `tracker: youtrack`, `tracker-project: LOG`, "
                     "`ticket-prefix: LOG`.\n")
        refused = self.next_id()
        self.assertEqual(refused.returncode, 1)
        self.assertIn("numer nadaje YouTrack — załóż ticket tam", refused.stderr)
        self.assertEqual(refused.stdout, "")
        self.assertFalse(os.path.exists(self.registry), "odmowa nie tworzy rejestru")
        with open(os.path.join(self.worktree, plan_mod.PLANS_DIR, "2026-10-28-app5.md"), "w", encoding="utf-8") as fh:
            fh.write(plan_text("APP5", [("APP5-3", "a")], tracker="youtrack", prefix="APP5", project="APP5"))
        refused = self.next_id("--prefix", "APP5")
        self.assertIn("numer nadaje YouTrack", refused.stderr)

    def test_plan_number_reserved_for_another_title(self):
        reservations = [{"prefix": "PT", "number": 22, "title": "Docker w tablicy"},
                        {"prefix": "PT", "number": 23, "title": ""}]
        vivaldi = plan("/r/docs/agents/plans/2026-10-27-vivaldi.md", "Vivaldi", [("PT-22", "Vivaldi w Tide"),
                                                                               ("PT-23", "Ikona")])
        (warning,) = numbering.conflicts([vivaldi], reservations=reservations)
        self.assertEqual(kinds([warning]), [("reserved-elsewhere", "soft", "PT-22")])
        self.assertIn("zarezerwowany dla „Docker w tablicy”", warning["text"])
        docker = plan("/r/docs/agents/plans/2026-10-27-docker.md", "Docker", [("PT-22", "Docker w tablicy.")])
        self.assertEqual(numbering.conflicts([docker], reservations=reservations), [], "rezerwacja tego tytułu")
        # To samo przez narzędzie: rejestr z rezerwacją i plan, który wziął numer bez rezerwacji.
        self.assertEqual(self.next_id("--title", "Docker w tablicy").stdout, "PT-22\n")
        with open(os.path.join(self.repo, plan_mod.PLANS_DIR, "2026-10-27-vivaldi.md"), "w", encoding="utf-8") as fh:
            fh.write(plan_text("Vivaldi", [("PT-22", "Vivaldi w Tide")]))
        out = subprocess.run(["/usr/bin/python3", os.path.join(REPO, "tools", "check_plans.py"), self.tmp,
                              "--no-youtrack"], env=self.env, capture_output=True, text=True, timeout=60)
        # Sam inny tytuł to ostrzeżenie łagodne (28.10: rezerwacja po angielsku, wiersz planu po polsku).
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PT-22 („Vivaldi w Tide”, 2026-10-27-vivaldi) jest zarezerwowany dla „Docker w "
                      "tablicy”", out.stdout)
        self.assertNotIn("[KOLIZJA] PT-22", out.stdout)


class CheckPlansToolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-check-plans-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = os.path.join(self.tmp, "proj")
        self.folder = os.path.join(self.repo, plan_mod.PLANS_DIR)
        os.makedirs(self.folder)
        git(self.repo, "init", "-q", "-b", "master")
        with open(os.path.join(self.folder, "2026-10-26-a.md"), "w", encoding="utf-8") as fh:
            fh.write(plan_text("A", numbered("PT", 1, 3)))
        os.makedirs(os.path.join(self.tmp, "not-a-repo"))

    def run_tool(self, *extra):
        env = dict(os.environ, XDG_STATE_HOME=os.path.join(self.tmp, "state"))  # pusty rejestr rezerwacji
        return subprocess.run(["/usr/bin/python3", os.path.join(REPO, "tools", "check_plans.py"), self.tmp,
                               "--no-youtrack", *extra], env=env, capture_output=True, text=True, timeout=60)

    def test_exit_code_and_output(self):
        ok = self.run_tool()
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("proj: 1 plan — OK", ok.stdout)
        with open(os.path.join(self.folder, "2026-10-27-b.md"), "w", encoding="utf-8") as fh:
            fh.write(plan_text("B", [("PT-2", "Coś innego")]))
        bad = self.run_tool()
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("[KOLIZJA] PT-2 ma różne tytuły", bad.stdout)
        self.assertIn("kolizje twarde: 1", bad.stdout)
        data = json.loads(self.run_tool("--json").stdout)
        self.assertEqual([w["kind"] for w in data[0]["warnings"]], ["ticket-title"])
        missing = subprocess.run(["/usr/bin/python3", os.path.join(REPO, "tools", "check_plans.py"),
                                  os.path.join(self.tmp, "nie-ma")], capture_output=True, text=True, timeout=30)
        self.assertEqual(missing.returncode, 2)


class ProjectColumnPlanFileTest(unittest.TestCase):
    """Standard 1.3: plik planu z kolumną Projekt (pusta komórka, nieznany slug) w Sesjach, raporcie i check-plans."""

    HEADER13 = ("| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery | Projekt |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-project-column-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = os.path.join(self.tmp, "proj")
        self.folder = os.path.join(self.repo, plan_mod.PLANS_DIR)
        os.makedirs(self.folder)
        git(self.repo, "init", "-q", "-b", "master")
        text = plan_text("Wiele projektów", []).replace(HEADER, "") + self.HEADER13 + (
            "| PT-1 | Pierwszy | todo | | | | | | | Plasma-Tide |\n"
            "| PT-2 | Drugi | todo | | | | | | PT-1 | |\n"
            "| PT-3 | Trzeci | todo | | | | | | | nieznany-slug |\n")
        with open(os.path.join(self.folder, "2026-10-27-multi.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "plan")

    def test_repoplans_numbering_report_and_check_plans(self):
        from tide_daemon.coordination import repoplans
        plans, declared = RepoPlans(ttl=0).read([self.repo])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].errors, [])
        self.assertEqual([(r.ticket, r.project) for r in plans[0].rows],
                         [("PT-1", "plasma-tide"), ("PT-2", ""), ("PT-3", "nieznany-slug")])
        self.assertEqual(numbering.conflicts(plans, declared=declared), [])
        self.assertEqual(repoplans.check([self.repo], RepoPlans(ttl=0)), [])

        found = plan_mod.find_plans([self.repo])
        self.assertEqual([(p.errors, len(p.rows)) for p in found], [([], 3)])

        project = types.SimpleNamespace(name="proj", path=self.repo, is_repo=True, plans=[])
        report_plans.attach([project], 0, F.NOW + 10 ** 9, None, None)
        self.assertEqual(len(project.plans), 1)
        entry = project.plans[0]
        self.assertEqual(entry["errors"], [], "raport nie zgłasza ostrzeżeń formatu")
        self.assertEqual([t["id"] for t in entry["all"]], ["PT-1", "PT-2", "PT-3"])
        self.assertEqual(project.conflicts, [])

        run = subprocess.run(["/usr/bin/python3", os.path.join(REPO, "tools", "check_plans.py"), self.tmp,
                              "--no-youtrack", "--json"],
                             env=dict(os.environ, XDG_STATE_HOME=os.path.join(self.tmp, "state")),
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)[0]["warnings"], [])


if __name__ == "__main__":
    unittest.main()
