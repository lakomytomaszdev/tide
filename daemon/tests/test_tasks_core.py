"""Nowe zadanie dla agenta — rdzeń bez D-Bus, na prawdziwych tymczasowych repozytoriach.

- polecenie agenta (argv): cudzysłowy, nowe linie, znaki narodowe, myślnik na początku, zrzuty —
  sprawdzone tak, jak dociera do programu: launch.py uruchamia atrapę agenta przez /bin/sh,
- nazwa gałęzi z polecenia (naming.py) i propozycja z -2 przy zajętej nazwie,
- worktree od lokalnej gałęzi głównej (master nietknięty), katalog według konwencji repozytorium,
  `.claude/worktrees` w lokalnym exclude, a nie w śledzonym .gitignore,
- odmowy bez żadnych zmian: gałąź główna, istniejąca gałąź, zła nazwa, zmiany na gałęzi głównej,
  brak gałęzi głównej, brak programu agenta, repozytorium spoza katalogu projektów, zrzut spoza
  katalogu zadań, jedno słowo,
- wycofanie: worktree i gałąź znikają, gałąź z nowymi commitami zostaje,
- pliki zadań: przejęcie najwyżej raz, anulowane się nie uruchamia,
- wybór okna Konsoli i tryb karta/okno na atrapach.
Prawdziwy claude ani codex nigdy nie startuje — w PATH są atrapy zapisujące argv.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from . import DAEMON_DIR, gitrepos
from tide_daemon.processes import ProcessInfo, ProcessTable
from tide_daemon.tasks import Planner, TaskError, TaskFiles, agent_argv, list_repos, task_title, worktree_dir
from tide_daemon.tasks.terminal import LAUNCHER, PROFILE_NAME, TaskTerminal, pick_window, profile_path
from tide_daemon.worktrees import Launchers, humanize_branch, guess_tool
from tide_daemon.worktrees.git import Git
from tide_daemon.worktrees.naming import branch_from_prompt, slug_from_prompt

FAKE_AGENT = """#!/usr/bin/python3
import json, os, sys
with open(os.environ["TIDE_FAKE_AGENT_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"name": os.path.basename(sys.argv[0]), "argv": sys.argv[1:], "cwd": os.getcwd()},
                        ensure_ascii=False) + "\\n")
"""
TRICKY = 'Napraw "cudzysłowy" i \'apostrofy\'; $(rm -rf /) `id` && echo $HOME\nDruga linia: zażółć gęślą jaźń 🐈 \\n'


def write_exec(path: str, text: str) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o755)
    return path


def fake_bin(folder: str, names=("claude", "codex")) -> str:
    os.makedirs(folder, exist_ok=True)
    for name in names:
        write_exec(os.path.join(folder, name), FAKE_AGENT)
    return folder


def quiet_git() -> Git:
    """Git bez globalnej konfiguracji użytkownika (hooki, aliasy) — jak w gitrepos.py."""
    git = Git()
    git.env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    return git


def run_launcher(task_file: str, env: dict, cwd: str | None = None, args=None):
    argv = [sys.executable, LAUNCHER] + (["--task", task_file] if args is None else args)
    return subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=30)


class ArgvTest(unittest.TestCase):
    def test_codex_gets_images_and_prompt_after_double_dash(self):
        argv = agent_argv("codex", "/snap/bin/codex", "-p zrób to", ["/t/a.png", "/t/b c.png"])
        self.assertEqual(argv, ["/snap/bin/codex", "--image", "/t/a.png", "--image", "/t/b c.png", "--", "-p zrób to"])

    def test_claude_reads_images_through_add_dir_and_prompt(self):
        argv = agent_argv("claude", "/bin/claude", "Popraw nagłówek", ["/t/1/a.png", "/t/1/b.png"])
        self.assertEqual(argv[:4], ["/bin/claude", "--add-dir", "/t/1", "--"])
        self.assertEqual(len(argv), 5)
        self.assertTrue(argv[4].startswith("Popraw nagłówek\n\nZałączone zrzuty ekranu"))
        self.assertTrue(argv[4].endswith("/t/1/a.png\n/t/1/b.png"))
        self.assertEqual(agent_argv("claude", "c", "a b"), ["c", "--", "a b"])
        with self.assertRaises(TaskError):
            agent_argv("vim", "v", "a b")

    def test_add_dirs_for_both_tools(self):
        docs = "/b/docs/mobileapp"
        self.assertEqual(agent_argv("codex", "codex", "a b", ["/t/a.png"], [docs]),
                         ["codex", "--add-dir", docs, "--image", "/t/a.png", "--", "a b"])
        argv = agent_argv("claude", "claude", "a b", ["/t/1/a.png"], [docs])
        self.assertEqual(argv[:6], ["claude", "--add-dir", docs, "--add-dir", "/t/1", "--"])
        self.assertEqual(agent_argv("claude", "claude", "a b", (), [docs, "/b/docs/x"]),
                         ["claude", "--add-dir", docs, "--add-dir", "/b/docs/x", "--", "a b"])

    def test_prompt_reaches_the_agent_unchanged_through_launcher(self):
        """Polecenie z cudzysłowami, $(…), nową linią i emoji to jeden argument — bez powłoki po drodze."""
        with tempfile.TemporaryDirectory(prefix="tide-argv-") as tmp:
            agent = write_exec(os.path.join(tmp, "claude"), FAKE_AGENT)
            log = os.path.join(tmp, "agent.log")
            files = TaskFiles(os.path.join(tmp, "run"))
            images = [os.path.join(tmp, "zrzut 1.png")]
            argv = agent_argv("claude", agent, "-" + TRICKY, images)
            task = files.write({"cwd": tmp, "argv": argv, "title": "x"})
            env = dict(os.environ, TIDE_FAKE_AGENT_LOG=log, SHELL="/bin/true")
            run = run_launcher(task, env)
            self.assertEqual(run.returncode, 0, run.stderr)
            with open(log, encoding="utf-8") as fh:
                got = json.loads(fh.readline())
            self.assertEqual(got["argv"], argv[1:])
            self.assertEqual(got["argv"][-1].split("\n\n")[0], "-" + TRICKY)
            self.assertEqual(os.path.realpath(got["cwd"]), os.path.realpath(tmp))
            self.assertTrue(TaskFiles.claimed(task))
            self.assertEqual(os.listdir(files.folder), [], "plik zadania znika po przejęciu")
            # Drugie przejęcie tego samego pliku nic nie uruchamia.
            again = run_launcher(task, env)
            self.assertIn("nie ma zadania", again.stdout)
            with open(log, encoding="utf-8") as fh:
                self.assertEqual(len(fh.readlines()), 1)

    def test_cancelled_task_never_starts_and_profile_mode_matches_cwd(self):
        with tempfile.TemporaryDirectory(prefix="tide-claim-") as tmp:
            agent = write_exec(os.path.join(tmp, "codex"), FAKE_AGENT)
            log = os.path.join(tmp, "agent.log")
            here, other = os.path.join(tmp, "here"), os.path.join(tmp, "other")
            os.makedirs(here)
            os.makedirs(other)
            runtime = os.path.join(tmp, "xdg")
            files = TaskFiles(os.path.join(runtime, "tide", "tasks"))
            env = dict(os.environ, TIDE_FAKE_AGENT_LOG=log, SHELL="/bin/true", XDG_RUNTIME_DIR=runtime)
            cancelled = files.write({"cwd": here, "argv": [agent, "--", "wycofane zadanie"], "title": ""})
            self.assertTrue(TaskFiles.cancel(cancelled))
            self.assertFalse(TaskFiles.claimed(cancelled), "anulowane nie liczy się jako przejęte")
            self.assertIn("nie ma zadania", run_launcher(cancelled, env).stdout)
            # Tryb profilu (bez --task): tylko zadanie z katalogu karty.
            for_other = files.write({"cwd": other, "argv": [agent, "--", "inne"], "title": ""})
            for_here = files.write({"cwd": here, "argv": [agent, "--", "tutaj start"], "title": ""})
            run = run_launcher("", env, cwd=here, args=[])
            self.assertEqual(run.returncode, 0, run.stderr)
            with open(log, encoding="utf-8") as fh:
                lines = [json.loads(line) for line in fh]
            self.assertEqual([line["argv"] for line in lines], [["--", "tutaj start"]])
            self.assertTrue(TaskFiles.claimed(for_here))
            self.assertFalse(TaskFiles.claimed(for_other))
            self.assertFalse(TaskFiles.cancel(for_here), "przejętego nie da się już anulować")

    def test_title(self):
        self.assertEqual(task_title("plasma-tide", "claude/fix-x"), "plasma-tide · claude/fix-x")
        self.assertEqual(task_title("a;b", "100%\nx"), "ab · 100x")


class NamingTest(unittest.TestCase):
    def test_slug_and_branch(self):
        cases = [
            ("Popraw liczenie VIN-ów w wyszukiwarce, proszę", "popraw-liczenie-vin-ow-wyszukiwarce"),
            ("Zażółć gęślą jaźń", "zazolc-gesla-jazn"),
            ('Add "quotes" and\nnewlines to the parser', "add-quotes-newlines-parser"),
            ("-p --help", "p-help"),
            ("!!! ???", "zadanie"),
            ("w i z", "w-i-z"),
            ("jeden dwa trzy cztery pięć sześć siedem", "jeden-dwa-trzy-cztery-piec-szesc"),
        ]
        for prompt, slug in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(slug_from_prompt(prompt), slug)
        self.assertLessEqual(len(slug_from_prompt("x" * 200 + " y")), 48)
        self.assertEqual(branch_from_prompt("Popraw nagłówek listy", "codex"), "codex/popraw-naglowek-listy")
        self.assertEqual(branch_from_prompt("Popraw nagłówek listy", "claude"), "claude/popraw-naglowek-listy")
        # Konwencja naming.py: znacznik narzędzia z prefiksu, narzędzie z prefiksu przed położeniem.
        self.assertEqual(humanize_branch("codex/popraw-naglowek-listy")[0], "codex")
        self.assertEqual(guess_tool("/r/.claude/worktrees/popraw", "codex"), "codex")


@unittest.skipUnless(shutil.which("git"), "brak gita")
class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-tasks-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "work")
        self.alpha = os.path.join(self.root, "alpha")             # master, worktrees w .claude/worktrees
        os.makedirs(self.alpha)
        gitrepos.git(self.alpha, "init", "-q", "-b", "master")
        gitrepos.commit(self.alpha, "README.md", "alpha\n")
        self.beta = os.path.join(self.root, "beta")               # main, worktrees w beta-worktrees
        os.makedirs(self.beta)
        gitrepos.git(self.beta, "init", "-q", "-b", "main")
        gitrepos.commit(self.beta, "b.txt")
        gitrepos.git(self.beta, "checkout", "-q", "-b", "feat/old")
        os.makedirs(self.beta + "-worktrees")
        self.gamma = os.path.join(self.root, "gamma")             # tylko trunk — brak gałęzi głównej
        os.makedirs(self.gamma)
        gitrepos.git(self.gamma, "init", "-q", "-b", "trunk")
        gitrepos.commit(self.gamma, "g.txt")
        self.bin = fake_bin(os.path.join(self.tmp, "bin"))
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.env = {"PATH": self.bin, "HOME": self.home, "XDG_RUNTIME_DIR": os.path.join(self.tmp, "run"),
                    "XDG_DATA_HOME": os.path.join(self.tmp, "data"), "XDG_STATE_HOME": os.path.join(self.tmp, "state")}
        self.planner = Planner(self.root, git=quiet_git(), env=self.env)
        self.master_sha = self.sha(self.alpha, "master")

    def sha(self, repo, ref):
        return gitrepos.git(repo, "rev-parse", ref).strip()

    def request(self, **changes):
        base = {"repoPath": self.alpha, "tool": "claude", "prompt": "Popraw nagłówek listy", "newWorktree": True,
                "branch": "", "images": []}
        base.update(changes)
        return base

    def assert_refused(self, code, request, repo=None):
        if repo is None:
            asked = request.get("repoPath") if isinstance(request, dict) else ""
            repo = asked if asked and os.path.isdir(asked) else self.alpha
        tasks = os.path.join(self.env["XDG_RUNTIME_DIR"], "tide", "tasks")
        files = lambda: sorted(os.listdir(tasks)) if os.path.isdir(tasks) else []  # noqa: E731
        pending = files()
        before = (gitrepos.branches(repo), gitrepos.git(repo, "worktree", "list", "--porcelain"))
        with self.assertRaises(TaskError) as ctx:
            self.planner.prepare(request)
        self.assertEqual(ctx.exception.code, code, ctx.exception.message)
        self.assertTrue(ctx.exception.message)
        after = (gitrepos.branches(repo), gitrepos.git(repo, "worktree", "list", "--porcelain"))
        self.assertEqual(before, after, "odmowa nie może niczego utworzyć")
        self.assertEqual(files(), pending, "odmowa nie zapisuje pliku zadania")
        return ctx.exception

    def test_new_worktree_from_master_without_touching_master(self):
        plan = self.planner.prepare(self.request(prompt='Popraw "nagłówek"\nlisty'))
        expected = os.path.join(self.alpha, ".claude", "worktrees", "popraw-naglowek-listy")
        self.assertEqual((plan.worktree, plan.cwd, plan.branch, plan.base), (expected, expected,
                                                                            "claude/popraw-naglowek-listy", "master"))
        self.assertEqual(plan.title, "alpha · claude/popraw-naglowek-listy")
        self.assertEqual(plan.argv, [os.path.join(self.bin, "claude"), "--", 'Popraw "nagłówek"\nlisty'])
        self.assertTrue(os.path.isfile(os.path.join(expected, "README.md")))
        self.assertEqual(self.sha(expected, "HEAD"), self.master_sha)
        self.assertEqual(self.sha(self.alpha, "master"), self.master_sha, "master nietknięty")
        self.assertEqual(gitrepos.git(self.alpha, "symbolic-ref", "--short", "HEAD").strip(), "master")
        self.assertEqual(gitrepos.git(self.alpha, "status", "--porcelain"), "", "główny katalog czysty")
        with open(os.path.join(self.alpha, ".git", "info", "exclude"), encoding="utf-8") as fh:
            self.assertIn("/.claude/worktrees/\n", fh.read())
        self.assertFalse(os.path.exists(os.path.join(self.alpha, ".gitignore")))
        with open(plan.task_file, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"cwd": expected, "argv": plan.argv, "title": plan.title})
        self.assertEqual(oct(os.stat(plan.task_file).st_mode & 0o777), "0o600")
        # Ta sama prośba drugi raz: propozycja przeskakuje zajętą nazwę.
        self.assertEqual(self.planner.suggest(self.alpha, "claude", "Popraw nagłówek listy")["branch"],
                         "claude/popraw-naglowek-listy-2")

    def test_exclude_is_not_duplicated_and_ignored_dir_is_left_alone(self):
        with open(os.path.join(self.alpha, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write(".claude/\n")
        gitrepos.git(self.alpha, "add", ".gitignore")
        gitrepos.git(self.alpha, "commit", "-q", "-m", "ignore")
        self.planner.prepare(self.request())
        with open(os.path.join(self.alpha, ".git", "info", "exclude"), encoding="utf-8") as fh:
            self.assertNotIn("/.claude/worktrees/", fh.read())

    def test_sibling_worktrees_dir_and_codex(self):
        self.assertEqual(worktree_dir(self.beta), self.beta + "-worktrees")
        plan = self.planner.prepare(self.request(repoPath=self.beta, tool="codex", branch="fix/raport-vin"))
        self.assertEqual(plan.worktree, os.path.join(self.beta + "-worktrees", "fix-raport-vin"))
        self.assertEqual((plan.branch, plan.base), ("fix/raport-vin", "main"))
        self.assertEqual(plan.argv, [os.path.join(self.bin, "codex"), "--", "Popraw nagłówek listy"])
        self.assertEqual(gitrepos.git(self.beta, "symbolic-ref", "--short", "HEAD").strip(), "feat/old")

    def test_main_checkout_mode_uses_repo_as_is(self):
        with open(os.path.join(self.alpha, "README.md"), "a", encoding="utf-8") as fh:
            fh.write("zmiana\n")
        plan = self.planner.prepare(self.request(newWorktree=False))
        self.assertEqual((plan.cwd, plan.worktree, plan.branch), (self.alpha, "", "master"))
        self.assertEqual(gitrepos.git(self.alpha, "worktree", "list", "--porcelain").count("worktree "), 1)

    def test_refusals_change_nothing(self):
        for branch in ("master", "main", "develop", "HEAD"):
            with self.subTest(branch=branch):
                self.assert_refused("protected-branch", self.request(branch=branch))
        self.assert_refused("protected-branch", self.request(repoPath=self.beta, branch="main"))
        for branch in ("zła nazwa", "a..b", "-x", "x.lock", "@{-1}"):
            with self.subTest(branch=branch):
                self.assert_refused("bad-branch", self.request(branch=branch))
        gitrepos.git(self.alpha, "branch", "feat/istnieje")
        self.assert_refused("branch-exists", self.request(branch="feat/istnieje"))
        os.makedirs(os.path.join(self.alpha, ".claude", "worktrees", "zajety"))
        self.assert_refused("path-exists", self.request(branch="claude/zajety"))
        self.assert_refused("no-base", self.request(repoPath=self.gamma))
        self.assert_refused("unknown-repo", self.request(repoPath=os.path.join(self.tmp, "gdzie-indziej")))
        self.assert_refused("unknown-repo", self.request(repoPath=""))
        self.assert_refused("bad-tool", self.request(tool="vim"))
        self.assert_refused("empty-prompt", self.request(prompt="   \n"))
        self.assert_refused("one-word", self.request(prompt="doctor"))
        self.assert_refused("prompt-too-long", self.request(prompt="a " * 20_000))
        self.assert_refused("bad-prompt", self.request(prompt="a\0 b"))
        outside = os.path.join(self.tmp, "obraz.png")
        open(outside, "wb").close()
        self.assert_refused("bad-image", self.request(images=[outside]))
        with self.assertRaises(TaskError) as ctx:
            self.planner.prepare("nie słownik")
        self.assertEqual(ctx.exception.code, "bad-request")

    def test_dirty_master_refuses_new_worktree(self):
        with open(os.path.join(self.alpha, "README.md"), "a", encoding="utf-8") as fh:
            fh.write("zmiana\n")
        error = self.assert_refused("dirty-base", self.request())
        self.assertIn("master", error.message)

    def test_missing_cli(self):
        os.unlink(os.path.join(self.bin, "codex"))
        self.assertEqual(self.planner.tools(), {"claude": True, "codex": False})
        error = self.assert_refused("missing-cli", self.request(tool="codex"))
        self.assertIn("codex", error.message)
        # Zapasowo ~/.local/bin (instalator Claude Code).
        local = os.path.join(self.home, ".local", "bin")
        fake_bin(local, ("codex",))
        self.assertEqual(self.planner.find_tool("codex"), os.path.join(local, "codex"))

    def test_no_terminal(self):
        planner = Planner(self.root, git=quiet_git(), env=self.env, terminal_available=lambda: False)
        with self.assertRaises(TaskError) as ctx:
            planner.prepare(self.request())
        self.assertEqual(ctx.exception.code, "no-terminal")

    def test_images_inside_task_dir(self):
        folder = os.path.join(self.env["XDG_DATA_HOME"], "tide", "tasks", "20261026-120000")
        os.makedirs(folder)
        shot = os.path.join(folder, "zrzut-1.png")
        open(shot, "wb").close()
        plan = self.planner.prepare(self.request(images=[shot, shot], tool="codex"))
        self.assertEqual(plan.argv[1:4], ["--image", shot, "--"])
        notes = os.path.join(folder, "notatki.txt")
        open(notes, "w").close()
        self.assert_refused("bad-image", self.request(images=[notes], branch="claude/inna"))

    def test_add_dirs_only_inside_board_docs(self):
        board = os.path.join(self.tmp, "tide-board")
        docs = os.path.join(board, "docs", "mobileapp")
        os.makedirs(docs)
        os.makedirs(os.path.join(board, "cards"))
        outside = os.path.join(self.tmp, "poza")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(board, "docs", "skrot"))
        planner = Planner(self.root, git=quiet_git(), env=self.env, board_root=board)
        plan = planner.prepare(self.request(tool="codex", addDirs=[docs, docs + "/"], branch="codex/k-1-vin"))
        self.assertEqual(plan.argv[1:4], ["--add-dir", os.path.realpath(docs), "--"])
        self.planner = planner
        for bad in ([os.path.join(board, "cards")], [board], [os.path.join(board, "docs")], ["docs/mobileapp"],
                    [os.path.join(board, "docs", "skrot")], [os.path.join(board, "docs", "nie-ma")],
                    [os.path.join(board, "docs", "mobileapp", "..", "..", "cards")], "zle", [1], [docs] * 5):
            self.assert_refused("bad-add-dir", self.request(addDirs=bad, branch="claude/inna"))
        no_board = Planner(self.root, git=quiet_git(), env=self.env, board_root="")
        with self.assertRaises(TaskError) as ctx:
            no_board.prepare(self.request(addDirs=[docs], branch="claude/inna"))
        self.assertEqual(ctx.exception.code, "bad-add-dir")

    def test_rollback_removes_worktree_and_fresh_branch_only(self):
        plan = self.planner.prepare(self.request())
        self.assertEqual(self.planner.rollback(plan), [])
        self.assertFalse(os.path.exists(plan.worktree))
        self.assertNotIn(plan.branch, gitrepos.branches(self.alpha))
        self.assertEqual(self.sha(self.alpha, "master"), self.master_sha)
        self.assertFalse(TaskFiles.claimed(plan.task_file))
        self.assertFalse(os.path.exists(plan.task_file))
        # Gałąź, na której już coś powstało, zostaje.
        plan = self.planner.prepare(self.request(branch="claude/z-commitem"))
        gitrepos.commit(plan.worktree, "praca.txt")
        problems = self.planner.rollback(plan)
        self.assertIn("claude/z-commitem", gitrepos.branches(self.alpha))
        self.assertTrue(any("nowe commity" in p for p in problems), problems)
        self.assertIn("master", gitrepos.branches(self.alpha))

    def test_remembers_last_choice(self):
        self.assertEqual(self.planner.last_choice(), {"repoPath": "", "tool": "claude", "newWorktree": True,
                                                      "model": "cloud"})
        plan = self.planner.prepare(self.request(repoPath=self.beta, tool="codex", newWorktree=False))
        self.planner.remember(plan)
        self.assertEqual(self.planner.last_choice(), {"repoPath": self.beta, "tool": "codex", "newWorktree": False,
                                                      "model": "cloud"})
        defaults = self.planner.defaults()
        self.assertEqual(defaults["tools"], {"claude": True, "codex": True})
        self.assertEqual(defaults["imagesDir"], os.path.join(self.env["XDG_DATA_HOME"], "tide", "tasks"))

    def test_repositories_skip_bare(self):
        bare = os.path.join(self.root, "bare.git")
        gitrepos.git(self.root, "init", "-q", "--bare", bare)
        self.assertEqual(sorted(self.planner.repositories()), sorted([self.alpha, self.beta, self.gamma]))


class RepoListTest(unittest.TestCase):
    def test_sorted_by_recent_activity_of_any_worktree(self):
        items = [
            {"repo": "alpha", "repoPath": "/w/alpha", "path": "/w/alpha", "isMain": True, "branch": "master",
             "mainBranch": "master", "dirty": 0, "lastActivityTs": 100, "missing": False},
            {"repo": "alpha", "repoPath": "/w/alpha", "path": "/w/alpha/.claude/worktrees/x", "isMain": False,
             "lastActivityTs": 900},
            {"repo": "beta", "repoPath": "/w/beta", "path": "/w/beta", "isMain": True, "branch": "main",
             "mainBranch": "main", "dirty": 2, "lastActivityTs": 500, "missing": False},
            {"repo": "Gamma", "repoPath": "/w/gamma", "path": "/w/gamma", "isMain": True, "branch": "",
             "mainBranch": "", "dirty": 0, "lastActivityTs": 500, "missing": False},
        ]
        repos = list_repos(items)
        self.assertEqual([r["name"] for r in repos], ["alpha", "beta", "Gamma"])
        self.assertEqual(repos[0]["lastActivityTs"], 900)
        self.assertEqual(repos[1]["dirty"], 2)
        self.assertEqual(repos[0]["worktreeDir"], "/w/alpha/.claude/worktrees")


class FakeKonsole:
    def __init__(self, session=7):
        self.session = session
        self.calls = []

    def new_session(self, service, window, profile, directory):
        self.calls.append(("new", service, window, profile, directory))
        return self.session

    def set_title(self, service, session, title):
        self.calls.append(("title", service, session, title))


class FakeLaunchers:
    def __init__(self, terminal="/usr/bin/konsole"):
        self.terminal = terminal
        self.spawned = []

    def _scoped(self, argv):
        return ["systemd-run", "--"] + argv

    def spawn(self, argv, path):
        self.spawned.append((argv, path))
        return True


class TerminalTest(unittest.TestCase):
    def table(self):
        return ProcessTable([
            ProcessInfo(100, 1, "konsole", 10.0), ProcessInfo(101, 100, "bash", 11.0),
            ProcessInfo(200, 1, "konsole", 20.0), ProcessInfo(201, 200, "zsh", 25.0),
            ProcessInfo(202, 200, "bash", 30.0),  # najmłodsza karta — ale bez zmiennych Konsoli
            ProcessInfo(300, 1, "bash", 99.0),
        ])

    def envs(self, pid):
        return {101: {"KONSOLE_DBUS_SERVICE": ":1.5", "KONSOLE_DBUS_WINDOW": "/Windows/1"},
                201: {"KONSOLE_DBUS_SERVICE": ":1.9", "KONSOLE_DBUS_WINDOW": "/Windows/2"}}.get(pid, {})

    def plan(self):
        from tide_daemon.tasks import Plan
        return Plan(repo_path="/w/a", repo_name="a", tool="claude", prompt="p q", cwd="/w/a/.claude/worktrees/x",
                    branch="claude/x", title="a · claude/x", argv=["claude", "--", "p q"], task_file="/run/t.task")

    def test_pick_most_recent_konsole_tab(self):
        self.assertEqual(pick_window(self.table(), self.envs), {"service": ":1.9", "window": "/Windows/2", "pid": 200})
        self.assertIsNone(pick_window(ProcessTable([ProcessInfo(5, 1, "bash", 1.0)]), self.envs))

    def test_tab_when_profile_installed_otherwise_window(self):
        with tempfile.TemporaryDirectory(prefix="tide-term-") as tmp:
            env = {"XDG_DATA_HOME": tmp, "HOME": tmp}
            konsole, launchers, activated = FakeKonsole(), FakeLaunchers(), []
            terminal = TaskTerminal(launchers, konsole, probe=self.table, activate=activated.append, env=env,
                                    read_env=self.envs)
            self.assertEqual(terminal.open(self.plan()), "window", "bez profilu — nowe okno")
            argv, cwd = launchers.spawned[0]
            self.assertEqual(argv, ["systemd-run", "--", "/usr/bin/konsole", "--workdir", "/w/a/.claude/worktrees/x",
                                    "-p", "tabtitle=a · claude/x", "-e", "/usr/bin/python3", "-s", LAUNCHER,
                                    "--task", "/run/t.task"])
            self.assertNotIn("p q", " ".join(argv), "polecenie nie trafia do wiersza poleceń Konsoli")
            os.makedirs(os.path.dirname(profile_path(env)))
            open(profile_path(env), "w").close()
            self.assertEqual(terminal.open(self.plan()), "tab")
            self.assertEqual(konsole.calls, [("new", ":1.9", "/Windows/2", PROFILE_NAME, "/w/a/.claude/worktrees/x"),
                                             ("title", ":1.9", 7, "a · claude/x")])
            self.assertEqual(activated[0]["konsole"]["session"], "/Sessions/7")
            konsole.session = None  # Konsole odmówiła karty — zostaje nowe okno
            self.assertEqual(terminal.open(self.plan()), "window")
            self.assertEqual(TaskTerminal(FakeLaunchers(terminal=""), None).open(self.plan()), "")

    def test_window_started_before_the_profile_gets_a_new_window(self):
        # Konsola wczytuje profile przy starcie; stare okno wzięłoby profil domyślny i otworzyło
        # gołą powłokę. Wtedy zadanie idzie do nowego okna, a karta w ogóle nie powstaje.
        with tempfile.TemporaryDirectory(prefix="tide-term-") as tmp:
            env = {"XDG_DATA_HOME": tmp, "HOME": tmp}
            os.makedirs(os.path.dirname(profile_path(env)))
            open(profile_path(env), "w").close()
            konsole, launchers = FakeKonsole(), FakeLaunchers()
            konsole.knows_profile = lambda service, window, profile: False
            terminal = TaskTerminal(launchers, konsole, probe=self.table, env=env, read_env=self.envs)
            self.assertEqual(terminal.open(self.plan()), "window")
            self.assertEqual(konsole.calls, [])
            konsole.knows_profile = lambda service, window, profile: profile == PROFILE_NAME
            self.assertEqual(terminal.open(self.plan()), "tab")


class NewTagTest(unittest.TestCase):
    """Znacznik „nowe”: sesja z okna „Nowe zadanie” przez 10 min, bez sesji sprzed startu."""

    def test_annotate_marks_only_new_sessions_from_the_task(self):
        from .privatebus import app
        from tide_daemon.task_monitor import NEW_TAG_S, TaskMonitor
        from tide_daemon.tasks import Plan
        app()
        with tempfile.TemporaryDirectory(prefix="tide-newtag-") as tmp:
            before = [{"id": "claude:old", "tool": "claude", "cwd": tmp, "state": "idle"}]
            monitor = TaskMonitor(planner=None, terminal=None, sessions_source=lambda: before)
            plan = Plan(repo_path=tmp, repo_name="r", tool="claude", prompt="a b", cwd=tmp, branch="", title="",
                        argv=[])
            monitor._record(plan)
            started = monitor.recent[0]["ts"]
            sessions = before + [{"id": "claude:new", "tool": "claude", "cwd": tmp, "state": "working"},
                                 {"id": "codex:x", "tool": "codex", "cwd": tmp, "state": "working"},
                                 {"id": "claude:else", "tool": "claude", "cwd": "/inny", "state": "working"}]
            monitor.annotate(sessions, now=started + 5)
            self.assertEqual([s["id"] for s in sessions if s.get("newTask")], ["claude:new"])
            later = [{"id": "claude:new", "tool": "claude", "cwd": tmp, "state": "working"}]
            monitor.annotate(later, now=started + NEW_TAG_S + 1)
            self.assertNotIn("newTask", later[0])
            self.assertEqual(monitor.recent, [])
            monitor.stop()


class LaunchersTest(unittest.TestCase):
    def test_task_dialog_script(self):
        with tempfile.TemporaryDirectory(prefix="tide-dialog-") as tmp:
            script = os.path.join(tmp, "tide-new-task.py")
            launchers = Launchers({"HOME": tmp, "TIDE_NEW_TASK_WINDOW": script}, path="")
            self.assertFalse(launchers.show_task_dialog())
            open(script, "w").close()
            self.assertEqual(launchers.task_dialog, script)
            default = Launchers({"HOME": tmp, "XDG_DATA_HOME": os.path.join(tmp, "d")}, path="")
            self.assertEqual(default.task_dialog_script, os.path.join(tmp, "d", "tide", "new-task", "tide-new-task.py"))


if __name__ == "__main__":
    unittest.main()
