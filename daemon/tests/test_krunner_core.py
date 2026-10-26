"""Rdzeń wtyczki KRunnera: dopasowanie i ranking, polecenia uruchomienia (bez prawdziwych procesów)."""
import os
import tempfile
import threading
import unittest

from . import history
from .fakebin import make_env
from tide_daemon.krunner import search
from tide_daemon.krunner.index import SessionIndex
from tide_daemon.krunner.runner import HIGH, LOW, MODERATE, KRunnerCore, LazyIndex
from tide_daemon.worktrees.launchers import KEEP_SHELL, Launchers

U = history.codex_uid


class RecordingLaunchers(Launchers):
    """Prawdziwe budowanie poleceń, ale spawn tylko zapisuje, co by uruchomił."""

    def __init__(self, env):
        super().__init__(env=env, path=env["PATH"])
        self.spawned = []

    def spawn(self, argv, path):
        if not argv or not os.path.isdir(path):
            return False
        self.spawned.append((argv, path))
        return True


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-krunner-core-")
        self.addCleanup(history.remove, self.tmp)
        self.p = history.install(self.tmp)
        self.env = make_env(self.tmp)
        self.fake = os.path.join(self.tmp, "fakebin")
        self.index = SessionIndex(self.p["claude"], self.p["codex"], clock=lambda: history.NOW)
        self.index.refresh()
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.home, "DevWorkspace", "plasma-tide")
        self.wt = os.path.join(self.repo, ".claude", "worktrees", "agent-1")
        for path in (self.repo, self.wt):
            os.makedirs(path)
        self.trees = [
            {"repo": "plasma-tide", "path": self.repo, "name": "plasma-tide", "branch": "master", "isMain": True,
             "missing": False, "lastChangeTs": 1.0},
            {"repo": "plasma-tide", "path": self.wt, "name": "agent-1", "branch": "feat/krunner-sessions",
             "isMain": False, "missing": False, "lastChangeTs": 2.0},
            {"repo": "LogViewer", "path": os.path.join(self.home, "DevWorkspace", "LogViewer"),
             "name": "LogViewer", "branch": "main", "isMain": True, "missing": True, "lastChangeTs": 0.0},
        ]
        self.live = []
        self.activated = []
        self.launchers = RecordingLaunchers(self.env)
        self.core = KRunnerCore(self.index, lambda: self.trees, self.launchers, live=lambda: self.live,
                                activate=self.activate, clock=lambda: history.NOW, home=self.home)
        self.activate_result = True

    def activate(self, session_id):
        self.activated.append(session_id)
        return self.activate_result

    def ids(self, query):
        return [m["id"] for m in self.core.match(query)]

    # --- dopasowanie -----------------------------------------------------------------------

    def test_session_match_text_and_subtext(self):
        m = self.core.match("login")[0]
        self.assertEqual(m["id"], "session:claude:h-titled")
        self.assertEqual(m["text"], "Claude · alpha · Login form fix")
        self.assertEqual(m["properties"]["subtext"], "feat/login · 3 dni temu")
        self.assertEqual(m["properties"]["category"], "Sesje agentów")
        self.assertEqual(m["icon"], "utilities-terminal")
        self.assertEqual(m["properties"]["actions"], ["terminal", "rider"])

    def test_codex_session_is_labelled(self):
        m = self.core.match("uploader")[0]
        self.assertEqual(m["text"], "Codex · beta · Uploader retries")
        self.assertEqual(m["properties"]["subtext"], "feat/uploader · 2 dni temu")
        self.assertEqual(m["icon"], "openai")

    def test_matches_project_branch_and_title_case_and_accent_insensitive(self):
        self.assertIn("session:claude:h-prompt", self.ids("PARSER"))      # gałąź i tytuł
        self.assertIn(f"session:codex:{U(104)}", self.ids("krunner docs"))  # dwa słowa, różne pola
        self.assertIn(f"session:codex:{U(104)}", self.ids("dócs"))
        self.assertEqual(search.fold("Łódź Żółć"), "lodz zolc")

    def test_every_word_must_match(self):
        self.assertEqual(self.ids("login uploader"), [])

    def test_ranking_prefers_better_hit_then_recency(self):
        ids = [i for i in self.ids("alpha") if i.startswith("session:")]
        # Wszystkie trzy trafiają w projekt tak samo — kolejność od najświeższej.
        self.assertEqual(ids, ["session:claude:h-prompt", "session:claude:h-titled", "session:claude:h-custom"])
        # Trafienie w początek tytułu bije trafienie w środek, mimo starszej sesji.
        self.assertEqual(self.ids("release")[0], "session:claude:h-custom")

    def test_session_without_directory_is_not_offered(self):
        self.assertNotIn("session:claude:h-gone", self.ids("deleted worktree"))
        self.assertNotIn("session:claude:h-gone", self.ids("ai"))

    def test_short_queries_are_ignored(self):
        self.assertEqual(self.core.match("lo"), [])
        self.assertEqual(self.core.match("   "), [])

    def test_ai_prefix_shows_only_sessions(self):
        ids = self.ids("ai tide")
        self.assertEqual(ids, [])  # „tide” jest tylko w projektach i worktrees
        ids = self.ids("ai beta")
        self.assertTrue(ids and all(i.startswith("session:codex:") for i in ids))
        recent = self.ids("ai")
        self.assertEqual(recent[:3], [f"session:codex:{U(102)}", "session:claude:h-prompt", f"session:codex:{U(101)}"])

    def test_projects_and_worktrees(self):
        matches = {m["id"]: m for m in self.core.match("plasma-tide")}
        project = matches[f"project:{self.repo}"]
        self.assertEqual(project["text"], "plasma-tide")
        self.assertEqual(project["properties"]["subtext"], "Otwórz projekt · ~/DevWorkspace/plasma-tide")
        self.assertEqual((project["icon"], project["categoryRelevance"]), ("folder-git", HIGH))
        self.assertEqual(project["properties"]["actions"], ["rider"])
        wt = self.core.match("krunner-sess")[0]
        self.assertEqual(wt["id"], f"worktree:{self.wt}")
        self.assertEqual(wt["text"], "feat/krunner-sessions")
        self.assertEqual(wt["properties"]["subtext"],
                         "Worktree · plasma-tide · ~/DevWorkspace/plasma-tide/.claude/worktrees/agent-1")
        self.assertEqual(wt["properties"]["category"], "Worktrees")
        self.assertNotIn("project:" + self.trees[2]["path"], self.ids("LogViewer"))  # katalogu nie ma

    def test_category_relevance_follows_hit_quality(self):
        self.assertEqual(self.core.match("plasma")[0]["categoryRelevance"], MODERATE)
        self.assertEqual({m["categoryRelevance"] for m in self.core.match("lasma")}, {LOW})
        for m in self.core.match("alpha"):
            self.assertGreaterEqual(m["relevance"], 0.0)
            self.assertLessEqual(m["relevance"], 1.0)

    def test_live_session_mentions_window(self):
        self.live = [{"id": "claude:h-titled", "state": "waiting", "canActivate": True}]
        self.assertEqual(self.core.match("login")[0]["properties"]["subtext"],
                         "feat/login · 3 dni temu · przejdź do okna")

    def test_actions_depend_on_available_programs(self):
        self.assertEqual([a[0] for a in self.core.actions()], ["terminal", "rider"])
        os.remove(os.path.join(self.fake, "rider"))
        core = KRunnerCore(self.index, lambda: self.trees, RecordingLaunchers(self.env), home=self.home)
        self.assertEqual(core.actions(), [("terminal", "Otwórz katalog w terminalu", "utilities-terminal")])
        self.assertEqual(core.match("plasma-tide")[0]["properties"]["actions"], [])

    def test_ago_text(self):
        now = history.NOW
        self.assertEqual([search.ago_text(now - s, now) for s in (5, 600, 7200, 86400, 3 * 86400, 20 * 86400,
                                                                     70 * 86400)],
                         ["teraz", "10 min temu", "2 h temu", "wczoraj", "3 dni temu", "2 tyg. temu", "2 mies. temu"])

    # --- uruchomienie ----------------------------------------------------------------------

    def scoped(self, *argv):
        return [os.path.join(self.fake, "systemd-run"), "--user", "--scope", "--collect", "--quiet", "--",
                *argv]

    def test_run_resumes_claude_session_in_its_directory(self):
        self.assertTrue(self.core.run("session:claude:h-titled", ""))
        konsole, claude = os.path.join(self.fake, "konsole"), os.path.join(self.fake, "claude")
        self.assertEqual(self.launchers.spawned, [(self.scoped(
            konsole, "--workdir", self.p["alpha"], "-e", "/bin/sh", "-c", KEEP_SHELL,
            claude, "--resume", "h-titled"), self.p["alpha"])])
        self.assertEqual(self.activated, [])  # sesja nie działa — nie ma do czego przechodzić

    def test_run_resumes_codex_session(self):
        self.assertTrue(self.core.run(f"session:codex:{U(101)}", ""))
        argv, path = self.launchers.spawned[0]
        self.assertEqual(argv[-3:], [os.path.join(self.fake, "codex"), "resume", U(101)])
        self.assertEqual(path, self.p["beta"])

    def test_run_live_session_activates_its_window(self):
        self.live = [{"id": "claude:h-titled", "state": "working", "canActivate": True}]
        self.assertTrue(self.core.run("session:claude:h-titled", ""))
        self.assertEqual(self.activated, ["claude:h-titled"])
        self.assertEqual(self.launchers.spawned, [])

    def test_run_live_session_falls_back_to_resume(self):
        self.live = [{"id": "claude:h-titled", "state": "working", "canActivate": True}]
        self.activate_result = False
        self.assertTrue(self.core.run("session:claude:h-titled", ""))
        self.assertEqual(len(self.launchers.spawned), 1)

    def test_run_session_actions(self):
        self.core.run("session:claude:h-titled", "terminal")
        self.core.run("session:claude:h-titled", "rider")
        self.assertEqual([a for a, _ in self.launchers.spawned], [
            self.scoped(os.path.join(self.fake, "konsole"), "--workdir", self.p["alpha"]),
            self.scoped(os.path.join(self.fake, "rider"), self.p["alpha"]),
        ])

    def test_run_project_and_worktree(self):
        self.assertTrue(self.core.run(f"project:{self.repo}", ""))
        self.assertTrue(self.core.run(f"worktree:{self.wt}", "rider"))
        self.assertEqual([a for a, _ in self.launchers.spawned], [
            self.scoped(os.path.join(self.fake, "konsole"), "--workdir", self.repo),
            self.scoped(os.path.join(self.fake, "rider"), self.wt),
        ])

    def test_run_refuses_unknown_targets(self):
        for match_id in (f"project:{self.tmp}", f"worktree:{self.repo}", f"project:{self.wt}",
                         "session:claude:nie-ma", "session:claude:h-gone", "nonsense", ""):
            self.assertFalse(self.core.run(match_id, ""), match_id)
        self.assertEqual(self.launchers.spawned, [])


class LazyIndexTest(unittest.TestCase):
    class SlowIndex:
        def __init__(self):
            self.built = False
            self.calls = 0
            self.release = threading.Event()

        def refresh(self):
            self.release.wait(5)
            self.calls += 1
            self.built = True

    def test_first_request_waits_for_build_later_ones_do_not(self):
        index = self.SlowIndex()
        done = threading.Event()
        clock = [100.0]
        lazy = LazyIndex(index, post=lambda fn: (fn(), done.set()), stale_after=10, monotonic=lambda: clock[0])
        answered = []
        lazy.request(lambda: answered.append(1))
        lazy.request(lambda: answered.append(2))
        self.assertEqual(answered, [])  # indeks się buduje — odpowiedź czeka
        index.release.set()
        self.assertTrue(done.wait(5))
        self.assertEqual((answered, index.calls), ([1, 2], 1))

        done.clear()
        lazy.request(lambda: answered.append(3))  # świeży — bez odświeżania
        self.assertEqual((answered[-1], index.calls), (3, 1))
        clock[0] += 11
        lazy.request(lambda: answered.append(4))  # stary — odpowiedź od razu, odświeżenie w tle
        self.assertEqual(answered[-1], 4)
        self.assertTrue(done.wait(5))
        self.assertEqual(index.calls, 2)


if __name__ == "__main__":
    unittest.main()
