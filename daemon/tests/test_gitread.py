"""Utwardzony runner odczytu gita (K-36, PT-348) na prawdziwych tymczasowych repozytoriach."""
import ast
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tide_daemon import gitread
from tide_daemon.gitread import read
from tide_daemon.gitread.runner import GitReadError, GitReader
from tide_daemon.worktrees.git import Git, Result
from .gitrepos import commit, git

PACKAGE = Path(gitread.__file__).parent
HOOKS = ("applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
         "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge",
         "pre-push", "pre-receive", "update", "post-receive", "post-update", "push-to-checkout",
         "pre-auto-gc", "post-rewrite", "sendemail-validate", "fsmonitor-watchman", "post-index-change",
         "reference-transaction")


def executable(path: Path, text: str) -> str:
    path.write_text(text)
    path.chmod(0o755)
    return str(path)


def make_repo(root: Path) -> Path:
    """master: a.txt, b.txt; card: dwa commity ponad master."""
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "master")
    commit(repo, "a.txt", "alfa\n")
    commit(repo, "b.txt", "beta\n")
    git(repo, "checkout", "-q", "-b", "card")
    commit(repo, "a.txt", "alfa\nzmiana\n", "change a")
    commit(repo, "c.txt", "gamma\n", "add c")
    return repo


def arm_traps(repo: Path, root: Path) -> Path:
    """Konfiguracja repozytorium wskazująca wszędzie skrypt-pułapkę; zwraca ścieżkę pliku-znacznika."""
    marker = root / "trap-ran"
    trap = executable(root / "trap.sh", f'#!/bin/sh\necho "$0 $*" >> "{marker}"\nexit 0\n')
    hooks = root / "hooks"
    hooks.mkdir()
    for name in HOOKS:
        executable(hooks / name, Path(trap).read_text())
        executable(repo / ".git" / "hooks" / name, Path(trap).read_text())
    for key, value in (("diff.external", trap), ("diff.trap.textconv", trap), ("diff.trap.command", trap),
                       ("filter.trap.clean", trap), ("filter.trap.smudge", trap), ("filter.trap.required", "true"),
                       ("filter.proc.process", trap), ("core.fsmonitor", trap), ("core.hooksPath", str(hooks)),
                       ("log.showSignature", "true"), ("gpg.program", trap), ("core.pager", trap),
                       ("pager.log", trap), ("pager.diff", trap), ("diff.submodule", "diff")):
        git(repo, "config", key, value)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("*.txt diff=trap filter=trap\n*.bin filter=proc\n")
    return marker


def dirty(repo: Path) -> None:
    """Zmiana treści i sama zmiana stat (git porównałby treść przez filtr i chciałby odświeżyć indeks)."""
    with open(repo / "a.txt", "a", encoding="utf-8") as fh:
        fh.write("roboczo\n")
    later = time.time() + 5
    os.utime(repo / "b.txt", (later, later))


def refs_and_index(repo: Path) -> tuple:
    gitdir = repo / ".git"
    refs = sorted((str(p.relative_to(gitdir)), p.read_bytes()) for p in (gitdir / "refs").rglob("*") if p.is_file())
    packed = (gitdir / "packed-refs").read_bytes() if (gitdir / "packed-refs").exists() else b""
    index = gitdir / "index"
    return refs, packed, (gitdir / "HEAD").read_bytes(), index.stat().st_mtime_ns, index.read_bytes()


def every_read(reader: GitReader, repo: str) -> None:
    """Każde polecenie z kryteriów, także z łatką (`-p`), i każda publiczna funkcja odczytu."""
    read.rev_parse(reader, repo, "card")
    read.merge_base(reader, repo, "master", "card")
    read.rev_list(reader, repo, ["master..card"])
    read.log(reader, repo, ["card"], max_count=5)
    read.diff(reader, repo, "master", "card")
    read.diff(reader, repo, "master")
    read.status(reader, repo)
    reader.run(repo, "log", "-p", "--stat", revs=["card"])
    reader.run(repo, "show", revs=["card"])
    reader.run(repo, "show", revs=["card:a.txt"])
    reader.run(repo, "diff", revs=["HEAD"])
    reader.run(repo, "diff", "--cached")
    reader.run(repo, "for-each-ref", "--format=%(refname) %(objectname)")


class TrapConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        if self._testMethodName == "test_reads_do_not_change_refs_or_index":
            # Przygotowanie zwykłym gitem przed pułapkami: świeży indeks, refy spakowane i luźne.
            git(self.repo, "update-index", "--refresh")
            git(self.repo, "pack-refs", "--all")
            git(self.repo, "branch", "loose", "master")
        self.marker = arm_traps(self.repo, self.root)
        self.reader = GitReader(timeout=30)
        self.addCleanup(self.reader.close)
        self.addCleanup(self._tmp.cleanup)

    def test_control_plain_git_runs_the_trap(self):
        """Pułapka jest prawdziwa: zwykły git uruchamia ją z tej konfiguracji."""
        dirty(self.repo)
        subprocess.run(["git", "-C", str(self.repo), "diff", "master"], capture_output=True,
                       env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, timeout=30)
        self.assertTrue(self.marker.exists())

    def test_no_read_runs_the_trap(self):
        dirty(self.repo)
        every_read(self.reader, str(self.repo))
        self.assertFalse(self.marker.exists(), self.marker.read_text() if self.marker.exists() else "")

    def test_reads_do_not_change_refs_or_index(self):
        dirty(self.repo)
        before = refs_and_index(self.repo)
        time.sleep(0.05)
        every_read(self.reader, str(self.repo))
        self.assertEqual(before, refs_and_index(self.repo))

    def test_disabled_filter_reads_raw_content(self):
        dirty(self.repo)
        out = read.status(self.reader, str(self.repo))
        self.assertIn(" M a.txt", out.split("\0"))
        self.assertNotIn(" M b.txt", out.split("\0"))  # sama zmiana stat, treść bez zmian
        text = read.diff(self.reader, str(self.repo), "card")
        self.assertIn("+roboczo", text)
        self.assertNotIn("b.txt", text)
        self.assertFalse(self.marker.exists())

    def test_filter_name_that_cannot_be_disabled_is_refused(self):
        git(self.repo, "config", "filter.a=b.clean", str(self.root / "trap.sh"))
        with self.assertRaises(GitReadError) as caught:
            read.status(self.reader, str(self.repo))
        self.assertEqual(caught.exception.code, "unsafe-config")
        self.assertFalse(self.marker.exists())


class RunnerBoundaryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.calls = self.root / "calls"
        # Pośrednik zapisuje argv i otoczenie, potem uruchamia prawdziwego gita.
        self.spy = executable(self.root / "spy-git",
                              f'#!/bin/sh\nprintf "%s\\n" "$@" > "{self.calls}.argv"\nenv > "{self.calls}.env"\n'
                              f'echo x >> "{self.calls}"\nexec /usr/bin/git "$@"\n')

    def reader(self, **kwargs):
        reader = GitReader(executable=self.spy, **kwargs)
        self.addCleanup(reader.close)
        return reader

    def test_environment_and_options(self):
        polluted = {"GIT_DIR": "/nonexistent", "GIT_EXTERNAL_DIFF": "/bin/false", "GIT_TERMINAL_PROMPT": "1",
                    "GIT_CONFIG_PARAMETERS": "'diff.external'='/bin/false'", "GIT_OPTIONAL_LOCKS": "1"}
        with mock.patch.dict(os.environ, polluted):
            reader = self.reader()
        self.assertTrue(read.diff(reader, str(self.repo), "master", "card"))
        env = dict(line.split("=", 1) for line in Path(f"{self.calls}.env").read_text().splitlines() if "=" in line)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")
        for name in ("GIT_DIR", "GIT_EXTERNAL_DIFF", "GIT_CONFIG_PARAMETERS"):
            self.assertNotIn(name, env)
        argv = Path(f"{self.calls}.argv").read_text().splitlines()
        for option in ("--no-ext-diff", "--no-textconv", "core.fsmonitor=false", "--end-of-options"):
            self.assertIn(option, argv)
        self.assertLess(argv.index("--end-of-options"), argv.index("master"))
        self.assertLess(argv.index("card"), argv.index("--"))

    def test_timeout_is_a_readable_error(self):
        slow = executable(self.root / "slow-git", "#!/bin/sh\nexec sleep 30\n")
        reader = GitReader(executable=slow, timeout=0.3)
        self.addCleanup(reader.close)
        started = time.monotonic()
        with self.assertRaises(GitReadError) as caught:
            read.rev_parse(reader, str(self.repo), "card")
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertIn("przekroczył limit czasu 0.3 s", str(caught.exception))

    def test_dash_values_are_bad_request_and_git_does_not_run(self):
        reader = self.reader()
        target = self.root / "x"
        value = f"--output={target}"
        repo = str(self.repo)
        for call in (lambda: read.rev_parse(reader, repo, value), lambda: read.merge_base(reader, repo, "card", value),
                     lambda: read.rev_list(reader, repo, [value]), lambda: read.log(reader, repo, [value]),
                     lambda: read.diff(reader, repo, value), lambda: read.diff(reader, repo, "master", value),
                     lambda: read.log(reader, repo, ["card"], paths=[""])):
            with self.assertRaises(GitReadError) as caught:
                call()
            self.assertEqual(caught.exception.code, "bad-request")
        self.assertFalse(target.exists())
        self.assertFalse(self.calls.exists())

    def test_dash_path_is_a_literal_path(self):
        (self.repo / "-p").write_text("plik\n")
        git(self.repo, "add", "--", "-p")
        git(self.repo, "commit", "-q", "-m", "dash")
        commits = read.log(self.reader(), str(self.repo), ["card"], paths=["-p"])
        self.assertEqual([c["subject"] for c in commits], ["dash"])

    def test_commands_and_options_that_break_the_rules_are_refused(self):
        reader = self.reader()
        repo = str(self.repo)
        for command, options in (("fetch", ()), ("config", ()), ("diff", ("--output=x",)), ("diff", ("--ext-diff",)),
                                 ("log", ("--textconv",)), ("log", ("--show-signature",)), ("diff", ("--no-index",)),
                                 ("log", ("--format=%G?",)), ("diff", ("-cfoo",)), ("log", ("card",)),
                                 ("diff", ("--submodule=diff",)), ("diff", ("--ignore-submodules=none",))):
            with self.assertRaises(GitReadError) as caught:
                reader.run(repo, command, *options)
            self.assertEqual(caught.exception.code, "bad-request", (command, options))
        with self.assertRaises(GitReadError) as caught:
            reader.run("relative/repo", "status")
        self.assertEqual(caught.exception.code, "bad-request")
        self.assertFalse(self.calls.exists())

    def test_failure_and_missing_values(self):
        reader = self.reader()
        repo = str(self.repo)
        self.assertIsNone(read.rev_parse(reader, repo, "nie-ma-takiej"))
        with self.assertRaises(GitReadError) as caught:
            read.rev_list(reader, repo, ["nie-ma-takiej"])
        self.assertEqual(caught.exception.code, "failed")

    def test_pool_and_close_cancels(self):
        reader = self.reader()
        future = reader.submit(read.rev_parse, str(self.repo), "card")
        self.assertEqual(future.result(timeout=30), git(self.repo, "rev-parse", "card").strip())
        slow = executable(self.root / "slow-git", "#!/bin/sh\nexec sleep 30\n")
        slow_reader = GitReader(executable=slow, timeout=60)
        pending = slow_reader.submit(read.rev_parse, str(self.repo), "card")
        time.sleep(0.2)
        started = time.monotonic()
        slow_reader.close()
        self.assertLess(time.monotonic() - started, 10)
        with self.assertRaises(GitReadError) as caught:
            pending.result(timeout=10)
        self.assertEqual(caught.exception.code, "cancelled")


class ReadResultsTest(unittest.TestCase):
    def test_public_reads_on_real_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = make_repo(Path(folder))
            reader = GitReader()
            self.addCleanup(reader.close)
            path = str(repo)
            master, card = (git(repo, "rev-parse", name).strip() for name in ("master", "card"))
            self.assertEqual(read.rev_parse(reader, path, "card"), card)
            self.assertEqual(read.merge_base(reader, path, "master", "card"), master)
            self.assertEqual(len(read.rev_list(reader, path, ["master..card"])), 2)
            commits = read.log(reader, path, ["card"], max_count=2)
            self.assertEqual([c["subject"] for c in commits], ["add c", "change a"])
            self.assertEqual(commits[1]["parents"], [master])
            self.assertEqual(set(commits[0]), set(read.LOG_FIELDS))
            text = read.diff(reader, path, "master", "card")
            self.assertIn("+zmiana", text)
            self.assertIn("diff --git a/c.txt b/c.txt", text)
            self.assertEqual(read.diff(reader, path, "master", "card", paths=["c.txt"]).count("diff --git"), 1)
            self.assertEqual(read.status(reader, path), "")


class SingleRunnerTest(unittest.TestCase):
    FORBIDDEN = {"subprocess", "os.system", "os.popen", "os.spawn", "os.exec", "os.posix_spawn", "pty"}

    def test_only_runner_may_start_processes(self):
        """Statycznie: żaden plik modułu poza runnerem nie importuje ani nie woła `subprocess`/`os.*` procesów,
        a runner uruchamia gita tylko przez `Git.run` (też bez `subprocess`)."""
        files = sorted(PACKAGE.glob("*.py"))
        self.assertIn(PACKAGE / "runner.py", files)
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found += [a.name for a in node.names if a.name.split(".")[0] in ("subprocess", "pty")]
                elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in ("subprocess", "pty"):
                    found.append(node.module)
                elif isinstance(node, ast.ImportFrom) and node.module == "os":
                    found += [a.name for a in node.names if a.name.startswith(("system", "popen", "spawn", "exec"))]
                elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os" \
                        and node.attr.startswith(("system", "popen", "spawn", "exec", "posix_spawn", "fork")):
                    found.append("os." + node.attr)
            self.assertEqual(found, [], path.name)
        runner = (PACKAGE / "runner.py").read_text(encoding="utf-8")
        self.assertIn("self._git.run(", runner)
        self.assertEqual(runner.count("._git.run("), 1)
        for path in files:
            if path.name != "runner.py":
                self.assertNotIn("Git(", path.read_text(encoding="utf-8"), path.name)
                self.assertNotIn("_git", path.read_text(encoding="utf-8"), path.name)

    def test_every_public_read_goes_through_the_runner(self):
        calls = []

        class FakeReader:
            def run(self, repo, command, *options, revs=(), paths=(), ok_codes=(0,), timeout=None):
                calls.append(command)
                return Result(0, "", "")

        arguments = {"rev_parse": ("/r", "card"), "merge_base": ("/r", "a", "b"), "rev_list": ("/r", ["a"]),
                     "log": ("/r", ["a"]), "diff": ("/r", "a", "b"), "status": ("/r",)}
        self.assertEqual(set(read.__all__), set(arguments))

        def refuse(*args, **kwargs):
            raise AssertionError("proces poza runnerem")

        with mock.patch("subprocess.run", refuse), mock.patch("subprocess.Popen", refuse), \
                mock.patch("os.system", refuse), mock.patch("os.popen", refuse), \
                mock.patch.object(Git, "run", refuse):
            for name in read.__all__:
                before = len(calls)
                getattr(read, name)(FakeReader(), *arguments[name])
                self.assertEqual(len(calls), before + 1, name)

    def test_runner_cannot_be_given_an_environment_or_git(self):
        reader = GitReader()
        self.addCleanup(reader.close)
        with self.assertRaises(TypeError):
            GitReader(env={})
        with self.assertRaises(TypeError):
            GitReader(git=Git())
        self.assertEqual(reader._git.env["GIT_TERMINAL_PROMPT"], "0")


if __name__ == "__main__":
    unittest.main()
