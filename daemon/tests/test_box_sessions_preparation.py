"""PT-189: korzenie pudełek, deduplikacja i przygotowanie zależności bez ucieczki na hosta."""
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tide_daemon import codex_homes
from tide_daemon.box import homes
from tide_daemon.box.incus import Incus
from tide_daemon.box.runner import BoxRunner
from tide_daemon.box.settings import BoxSettings
from tide_daemon.tasks import dotnet, preparation, stepstop
from tide_daemon.tasks.runner import HostRunner
from tide_daemon.worktrees import SessionIndex
from .test_worktrees_sessions import LogFixture, NOW, DAY


class BoxSessionTest(LogFixture):
    def setUp(self):
        super().setUp()
        self.env = {"XDG_DATA_HOME": os.path.join(self.tmp, "data")}
        self.patcher = mock.patch.dict(os.environ, self.env)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.home = os.path.join(self.env["XDG_DATA_HOME"], "tide", "box", "proj", "home")
        os.makedirs(self.home)

    def test_claude_in_both_roots_links_to_same_worktree_without_duplicates(self):
        wt = "/srv/proj-worktrees/pt-189"
        host = self.claude_session("session", wt, NOW - DAY, title="Host")
        boxed = os.path.join(self.home, ".claude", os.path.relpath(host, self.claude))
        os.makedirs(os.path.dirname(boxed))
        shutil.copyfile(host, boxed)
        os.utime(boxed, (NOW, NOW))
        index = SessionIndex(self.claude, self.codex)
        self.assertEqual(len(index.refresh(NOW)), 1)
        self.assertEqual(index.link([(wt, wt)])[wt].session_id, "session")
        os.unlink(host)
        self.assertEqual(index.refresh(NOW)[0].cwds, (wt,))
        self.assertEqual(stepstop.find_claude_log(self.claude + "/projects", "session"), boxed)

    def test_codex_box_and_k3_homes_are_discovered_on_each_refresh(self):
        wt = "/srv/proj-worktrees/pt-189"
        host = self.codex_session("thread", wt, NOW - DAY)
        boxed = os.path.join(self.home, ".codex", "steps", "step-hash", os.path.relpath(host, self.codex))
        index = SessionIndex(self.claude, self.codex)
        os.makedirs(os.path.dirname(boxed))
        shutil.copyfile(host, boxed)
        os.utime(boxed, (NOW, NOW))
        roots = codex_homes.default_homes(dict(os.environ, CODEX_HOME=self.codex))
        self.assertIn(os.path.join(self.home, ".codex", "steps", "step-hash"), roots)
        self.assertEqual(len(index.refresh(NOW)), 1)
        self.assertEqual(stepstop.find_codex_log([self.codex], wt, "thread"), boxed)
        os.unlink(host)
        self.assertEqual(index.link([(wt, wt)])[wt].session_id, "thread")
        self.assertEqual(len(index.refresh(NOW)), 1)

    def test_box_symlinks_never_read_host_logs(self):
        self.claude_session("host", "/srv/proj", NOW - DAY)
        os.symlink(self.claude, os.path.join(self.home, ".claude"))
        os.symlink(self.codex, os.path.join(self.home, ".codex"))
        self.assertEqual(homes.claude_projects(), [])
        self.assertEqual(codex_homes.box_homes(), [])
        self.assertEqual(SessionIndex("/missing", "/missing").refresh(NOW), [])
        os.unlink(os.path.join(self.home, ".codex"))
        os.makedirs(os.path.join(self.home, ".codex"))
        os.symlink(os.path.join(self.codex, "sessions"), os.path.join(self.home, ".codex", "sessions"))
        self.assertEqual(SessionIndex("/missing", "/missing").refresh(NOW), [])


class PreparationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "proj")
        self.wt = self.repo + "-worktrees/pt-189"
        self.step = self.wt + ".step"
        os.makedirs(self.wt)
        os.makedirs(os.path.join(self.step, "in"))
        os.makedirs(os.path.join(self.step, "out"))
        self.calls = []
        self.runner = BoxRunner(BoxSettings(project="user-1000"), "proj",
                                incus=Incus("user-1000", binary="fake-incus", run=self.fake_run),
                                proxy_base="http://10.0.0.1:18080", env={"PATH": "/usr/bin", "HOME": self.tmp.name})
        self.bg = SimpleNamespace(repo=self.repo, cwd=self.wt, step_dir=self.step,
                                  env=dict(self.runner.base_env(), HTTPS_PROXY="http://tide:private-key@10.0.0.1:18080"),
                                  prompt_file=os.path.join(self.step, "in", "prompt.txt"), stdin="Zadanie")

    def fake_run(self, argv, **kwargs):
        with open(os.path.join(self.step, "in", "prepare-env")) as fh:
            env = dict(line.rstrip("\n").split("=", 1) for line in fh)
        self.assertEqual(os.stat(os.path.join(self.step, "in", "prepare-env")).st_mode & 0o777, 0o600)
        self.calls.append((argv, kwargs, env))
        if "command -v node" in argv[-1]:
            out = "/home/agent/.nvm/versions/node/v22.0.0\n"
        elif "ms-playwright" in argv[-1]:
            out = "/home/agent/.cache/ms-playwright/chromium-123/chrome-linux/chrome\n"
        else:
            out = "gotowe\n"
        return subprocess.CompletedProcess(argv, 0, out, "")

    def test_node_and_dotnet_use_selected_runner_and_private_environment(self):
        for name in ("package.json", "package-lock.json", "App.sln"):
            with open(os.path.join(self.wt, name), "w") as fh:
                fh.write("{}")
        with mock.patch.dict(os.environ, {"NUGET_PACKAGES": "/host/packages", "SECRET_HOST": "secret"}):
            preparation.prepare(self.runner, self.bg)
        npm = next(c for c in self.calls if "ci" in c[0])
        nuget = next(c for c in self.calls if "restore" in c[0])
        self.assertIn("/home/agent/.nvm/versions/node/v22.0.0/bin/npm", npm[0])
        self.assertIn(os.path.join(self.wt, "App.sln"), nuget[0])
        for argv, kwargs, env in self.calls:
            self.assertEqual(argv[:4], ["fake-incus", "exec", "--project", "user-1000"])
            self.assertIn(self.wt, argv)
            self.assertEqual(env["HOME"], "/home/agent")
            self.assertNotIn("private-key", " ".join(argv))
            self.assertNotIn("SECRET_HOST", env)
            self.assertNotIn("HTTPS_PROXY", kwargs["env"])
        self.assertEqual(nuget[2]["NUGET_PACKAGES"], "/home/agent/.nuget/packages")
        self.assertTrue(self.bg.env["CHROME_BIN"].startswith("/home/agent/"))
        self.assertFalse(os.path.exists(os.path.join(self.step, "in", "prepare-env")))

    def test_host_runner_keeps_argv_environment_and_paths(self):
        result = subprocess.CompletedProcess([], 0, "ok", "")
        with mock.patch("subprocess.run", return_value=result) as run:
            self.assertEqual(dotnet.restore(self.wt, runner=HostRunner()), (True, "ok"))
        self.assertEqual(run.call_args.args[0], ["dotnet", "restore"])
        self.assertEqual(run.call_args.kwargs["cwd"], self.wt)
        with mock.patch.dict(os.environ, {"NUGET_PACKAGES": self.tmp.name}):
            self.assertEqual(dotnet.packages_dir("/host"), self.tmp.name)

    def test_box_command_failure_or_timeout_does_not_run_on_host(self):
        with mock.patch.object(self.runner.incus, "_run", side_effect=subprocess.TimeoutExpired("incus", 1)), \
                mock.patch("subprocess.run") as host:
            ok, tail = dotnet.restore(self.wt, runner=self.runner, background=self.bg)
        self.assertFalse(ok)
        self.assertIn("TimeoutExpired", tail)
        host.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.step, "in", "prepare-env")))
