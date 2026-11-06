"""Kontrakt /Tasks (org.tide.Daemon.Tasks) na prywatnej magistrali z prawdziwą usługą.

Usługa działa na tymczasowych repozytoriach, z tymczasowym HOME, XDG_* i atrapami w PATH:
- konsole — zapisuje wywołanie i uruchamia polecenie z -e (launch.py) w katalogu z --workdir,
  albo nie robi nic, gdy istnieje plik „martwa Konsola” (sprawdzenie wycofania),
- systemd-run — uruchamia to, co po `--` (bez prawdziwego menedżera usług),
- claude, codex — zapisują argv i katalog do pliku; SHELL=/bin/true kończy kartę po agencie.
Profilu Konsoli nie ma, więc zadanie zawsze startuje w „nowym oknie” atrapy.
"""
import json
import os
import shutil
import tempfile
import unittest

from . import gitrepos
from .privatebus import SERVICE, PrivateBus, requires_dbus, wait_until
from .test_tasks_core import FAKE_AGENT, TRICKY, write_exec

PATH, IFACE = "/Tasks", "org.tide.Daemon.Tasks"

FAKE_KONSOLE = """#!/bin/bash
printf 'konsole' >> "$TIDE_FAKE_LOG"; printf ' [%s]' "$@" >> "$TIDE_FAKE_LOG"; echo >> "$TIDE_FAKE_LOG"
[ -e "$TIDE_FAKE_KONSOLE_DEAD" ] && exit 0
workdir=.
while [ $# -gt 0 ]; do
    case "$1" in
        --workdir) workdir="$2"; shift 2 ;;
        -p) shift 2 ;;
        -e) shift; break ;;
        *) shift ;;
    esac
done
cd "$workdir" && exec "$@"
"""
FAKE_SYSTEMD_RUN = """#!/bin/bash
while [ $# -gt 0 ] && [ "$1" != "--" ]; do shift; done
shift
exec "$@"
"""


@requires_dbus
@unittest.skipUnless(shutil.which("git"), "brak gita")
class TasksContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-tasks-dbus-")
        cls.root = os.path.join(cls.tmp, "work")
        cls.alpha = os.path.join(cls.root, "alpha")
        os.makedirs(cls.alpha)
        gitrepos.git(cls.alpha, "init", "-q", "-b", "master")
        gitrepos.commit(cls.alpha, "README.md", "alpha\n")
        cls.beta = os.path.join(cls.root, "beta")
        os.makedirs(cls.beta)
        gitrepos.git(cls.beta, "init", "-q", "-b", "main")
        gitrepos.commit(cls.beta, "b.txt")
        cls.bin = os.path.join(cls.tmp, "bin")
        os.makedirs(cls.bin)
        for name in ("claude", "codex"):
            write_exec(os.path.join(cls.bin, name), FAKE_AGENT)
        write_exec(os.path.join(cls.bin, "konsole"), FAKE_KONSOLE)
        write_exec(os.path.join(cls.bin, "systemd-run"), FAKE_SYSTEMD_RUN)
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.home)
        cls.agent_log = os.path.join(cls.tmp, "agent.log")
        cls.calls = os.path.join(cls.tmp, "calls.log")
        cls.dead = os.path.join(cls.tmp, "martwa-konsola")
        cls.data = os.path.join(cls.tmp, "data")
        cls.board = os.path.join(cls.tmp, "tide-board")
        cls.service = PrivateBus("tide-tasks-test")
        cls.service.spawn_daemon(
            "--claude-dir", os.path.join(cls.tmp, "claude"), "--codex-dir", os.path.join(cls.tmp, "codex"),
            "--no-processes", "--no-notifications", "--no-krunner", "--no-idle", "--workspace", cls.root,
            "--board-dir", cls.board,
            extra_env={"PATH": cls.bin + os.pathsep + "/usr/bin:/bin", "HOME": cls.home,
                       "XDG_DATA_HOME": cls.data, "XDG_RUNTIME_DIR": os.path.join(cls.tmp, "run"),
                       "XDG_CONFIG_HOME": os.path.join(cls.tmp, "config"), "SHELL": "/bin/true",
                       "TIDE_FAKE_LOG": cls.calls, "TIDE_FAKE_AGENT_LOG": cls.agent_log,
                       "TIDE_FAKE_KONSOLE_DEAD": cls.dead, "TIDE_TASK_CLAIM_TIMEOUT": "2",
                       "TIDE_NEW_TASK_WINDOW": os.path.join(cls.tmp, "brak-okna.py"),
                       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, member, *args, timeout=30000):
        from PyQt6.QtDBus import QDBusMessage
        reply = self.service.call(SERVICE, PATH, IFACE, member, *args, timeout=timeout)
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        return reply.arguments()[0]

    def start(self, **request):
        before = len(self.agent_calls())
        result = json.loads(self.call("Start", json.dumps(request, ensure_ascii=False)))
        if result.get("ok"):
            # Start potwierdza przejęcie pliku, zanim exec uruchomi atrapę agenta.
            # Czekamy na obserwowany skutek, aby wpis nie trafił do kolejnego scenariusza.
            self.assertTrue(wait_until(lambda: len(self.agent_calls()) >= before + 1, timeout=5),
                            "Przejęte zadanie nie zapisało wywołania agenta w ciągu 5 s.")
        return result

    def agent_calls(self):
        try:
            with open(self.agent_log, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh]
        except OSError:
            return []

    def test_1_repos_defaults_and_suggestion(self):
        repos = json.loads(self.call("Repos"))
        self.assertEqual(sorted(r["name"] for r in repos), ["alpha", "beta"])
        alpha = next(r for r in repos if r["name"] == "alpha")
        self.assertEqual((alpha["path"], alpha["mainBranch"], alpha["branch"], alpha["dirty"]),
                         (self.alpha, "master", "master", 0))
        self.assertEqual(alpha["worktreeDir"], os.path.join(self.alpha, ".claude", "worktrees"))
        defaults = json.loads(self.call("Defaults"))
        self.assertEqual(defaults["tools"], {"claude": True, "codex": True})
        self.assertTrue(defaults["terminal"])
        self.assertEqual((defaults["repoPath"], defaults["tool"], defaults["newWorktree"]), ("", "claude", True))
        suggestion = json.loads(self.call("SuggestBranch", self.alpha, "codex", "Popraw liczenie VIN-ów"))
        self.assertEqual((suggestion["ok"], suggestion["branch"], suggestion["base"]),
                         (True, "codex/popraw-liczenie-vin-ow", "master"))
        self.assertEqual(suggestion["path"], os.path.join(self.alpha, ".claude", "worktrees", "popraw-liczenie-vin-ow"))
        bad = json.loads(self.call("SuggestBranch", "/nie/ma", "codex", "x y"))
        self.assertEqual((bad["ok"], bad["error"]), (False, "unknown-repo"))
        self.assertFalse(self.call("ShowDialog"))

    def test_2_start_creates_worktree_and_runs_agent_with_exact_prompt(self):
        result = self.start(repoPath=self.alpha, tool="claude", prompt=TRICKY, newWorktree=True, branch="", images=[])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "window")
        worktree = os.path.join(self.alpha, ".claude", "worktrees", "napraw-cudzyslowy-apostrofy-rm-rf-id")
        self.assertEqual((result["worktree"], result["cwd"]), (worktree, worktree))
        self.assertEqual(result["branch"], "claude/napraw-cudzyslowy-apostrofy-rm-rf-id")
        self.assertEqual(result["title"], "alpha · claude/napraw-cudzyslowy-apostrofy-rm-rf-id")
        self.assertTrue(os.path.isdir(worktree))
        runs = self.agent_calls()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["name"], "claude")
        self.assertEqual(runs[0]["argv"], ["--", TRICKY.strip()])
        self.assertEqual(os.path.realpath(runs[0]["cwd"]), os.path.realpath(worktree))
        with open(self.calls, encoding="utf-8") as fh:
            konsole = fh.read()
        self.assertIn(f"[--workdir] [{worktree}] [-p] [tabtitle={result['title']}] [-e]", konsole)
        self.assertNotIn("cudzysłowy", konsole, "polecenie nie trafia do wiersza poleceń Konsoli")
        self.assertEqual(gitrepos.git(self.alpha, "rev-parse", "master"),
                         gitrepos.git(worktree, "rev-parse", "HEAD"))
        # Wybór zapamiętany.
        defaults = json.loads(self.call("Defaults"))
        self.assertEqual((defaults["repoPath"], defaults["tool"], defaults["newWorktree"]),
                         (self.alpha, "claude", True))

    def test_3_launch_failure_rolls_back(self):
        open(self.dead, "w").close()
        try:
            result = self.start(repoPath=self.beta, tool="codex", prompt="Zadanie, które nie wystartuje",
                                newWorktree=True, branch="codex/nie-wystartuje")
        finally:
            os.unlink(self.dead)
        self.assertEqual((result["ok"], result["error"]), (False, "launch"), result)
        self.assertIn("nic nie zostało utworzone", result["message"])
        self.assertFalse(os.path.exists(os.path.join(self.beta, ".claude", "worktrees", "nie-wystartuje")))
        self.assertNotIn("codex/nie-wystartuje", gitrepos.branches(self.beta))
        self.assertEqual(gitrepos.git(self.beta, "worktree", "list", "--porcelain").count("worktree "), 1)
        self.assertFalse([c for c in self.agent_calls() if c["name"] == "codex"])

    def test_4_refusals_and_main_checkout(self):
        result = self.start(repoPath=self.beta, tool="codex", prompt="Popraw coś ważnego", newWorktree=True,
                            branch="main")
        self.assertEqual((result["ok"], result["error"]), (False, "protected-branch"))
        self.assertEqual(json.loads(self.call("Start", "to nie json"))["error"], "bad-request")
        self.assertEqual(self.start(repoPath=self.beta, tool="codex", prompt="x")["error"], "one-word")
        before = len(self.agent_calls())
        result = self.start(repoPath=self.beta, tool="codex", prompt="Przejrzyj README w głównym katalogu",
                            newWorktree=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["cwd"], result["worktree"], result["branch"]), (self.beta, "", "main"))
        run = self.agent_calls()[before]
        self.assertEqual((run["name"], run["argv"]), ("codex", ["--", "Przejrzyj README w głównym katalogu"]))

    def test_5_add_dirs_from_board_for_both_tools(self):
        docs = os.path.join(self.board, "docs", "alpha")
        os.makedirs(docs, exist_ok=True)
        before = len(self.agent_calls())
        result = self.start(repoPath=self.alpha, tool="codex", prompt="Karta K-1: plan w tablicy", newWorktree=True,
                            branch="codex/k-1-plan", addDirs=[docs])
        self.assertTrue(result["ok"], result)
        result = self.start(repoPath=self.alpha, tool="claude", prompt="Karta K-1: tylko specyfikacja",
                            newWorktree=False, addDirs=[docs])
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["cwd"], result["worktree"]), (self.alpha, ""))
        runs = self.agent_calls()[before:]
        self.assertEqual([(r["name"], r["argv"]) for r in runs],
                         [("codex", ["--add-dir", docs, "--", "Karta K-1: plan w tablicy"]),
                          ("claude", ["--add-dir", docs, "--", "Karta K-1: tylko specyfikacja"])])
        refused = self.start(repoPath=self.alpha, tool="claude", prompt="Zapisz coś poza tablicą", newWorktree=False,
                             addDirs=[self.beta])
        self.assertEqual((refused["ok"], refused["error"]), (False, "bad-add-dir"))
        self.assertEqual(len(self.agent_calls()), before + 2, "odmowa nie uruchamia agenta")


if __name__ == "__main__":
    unittest.main()
