"""BoxRunner (PT-169, #6): argv `incus exec`, `in/env` 0600, pełne NO_PROXY, urządzenia dyskowe pudełka, ustawienia
agentów w pudełku, `kill` i opakowanie `tide-step`.

Wszystko na atrapie `incus` z fakebin.py (stan w katalogu tymczasowym) — prawdziwy Incus ani systemd nie są
potrzebne. `tide-step` biegnie lokalnie przez `/bin/sh`.
"""
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from tide_daemon.box import runner as br
from tide_daemon.box import state as bs
from tide_daemon.box.incus import Incus
from tide_daemon.box.settings import BoxSettings
from tide_daemon.tasks import agentcmd, local
from tide_daemon.tasks.runner import HostRunner

from . import gitrepos
from .fakebin import calls, make_env

PROXY = "http://10.173.184.1:18080"
BRIDGE = "10.173.184.1"
MARKER = "znacznik-tokenu-PT169"
SID = "11111111-2222-3333-4444-555555555555"
FAKE_SYSTEMD_RUN = '#!/bin/sh\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n'


class BoxRunnerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="tide-boxrunner-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = make_env(self.tmp)
        env.update({"TIDE_BOARD_DIR": os.path.join(self.tmp, "board"),
                    "XDG_STATE_HOME": os.path.join(self.tmp, "state")})
        self.env = env
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home = env["HOME"]
        self.repo = os.path.join(self.home, "DevWorkspace", "proj")
        self.wt_root = self.repo + "-worktrees"
        self.worktree = os.path.join(self.wt_root, "pt-1")
        self.step_dir = os.path.join(self.wt_root, "pt-1.step")
        os.makedirs(self.repo)
        gitrepos.git(self.repo, "init", "-q", "-b", "master")
        gitrepos.commit(self.repo, "a.txt")
        gitrepos.git(self.repo, "worktree", "add", "-q", "-b", "feat/pt-1", self.worktree)
        self.token_file = os.path.join(self.tmp, "token")
        with open(self.token_file, "w", encoding="utf-8") as fh:
            fh.write(MARKER + "\n")
        self.fake_incus = os.path.join(self.tmp, "fakebin", "incus")
        self.incus = Incus("user-1000", binary=self.fake_incus)
        self.incus.launch("tide-base", "proj")
        self.settings = BoxSettings(project="user-1000")
        self.popen_calls = []
        # systemctl „w pudełku” (po `env -i` — ścieżka dziennika wpisana w skrypt): menedżer użytkownika gotowy
        # po liczbie nieudanych prób z pliku `<licznik>.need` (domyślnie od razu); prawdziwy systemctl hosta nie rusza.
        self.counter = os.path.join(self.tmp, "manager-tries")
        self.box_systemctl = os.path.join(self.tmp, "fakebin", "box", "systemctl-agent")
        with open(self.box_systemctl, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/sh\necho "box-systemctl $*" >> {shlex.quote(env["TIDE_FAKE_LOG"])}\n'
                     f'case "$*" in *show-environment*) echo x >> {shlex.quote(self.counter)}; '
                     f'[ "$(wc -l < {shlex.quote(self.counter)})" -gt "$(cat {shlex.quote(self.counter)}.need '
                     f'2>/dev/null || echo 0)" ] ;; esac\n')
        os.chmod(self.box_systemctl, 0o755)

    def step(self, tool="claude", **kw):
        args = dict(tool=tool, binary=tool, prompt="Zrób ticket", worktree=self.worktree, repo=self.repo,
                    step_dir=self.step_dir, step_id="s1", session_id=SID, codex_mcp=(), branch="feat/pt-1",
                    tmp_root=os.path.join(self.tmp, "tmp"))
        args.update(kw)
        return agentcmd.Step(**args)

    def popen(self, argv, **kw):
        self.popen_calls.append({"argv": list(argv), **kw})
        return mock.Mock(returncode=None)

    def runner(self, **kw):
        args = dict(incus=self.incus, proxy_base=PROXY, popen=self.popen, env=dict(os.environ),
                    systemctl=self.box_systemctl, sleep=lambda s: None)
        args.update(kw)
        return br.BoxRunner(self.settings, "proj", **args)

    def adds(self, box="proj"):
        out = []
        for line in calls(self.env):
            argv = shlex.split(line)
            if argv[1:4] == ["config", "device", "add"] and box in argv:
                out.append(argv)
        return out

    def env_file(self):
        with open(os.path.join(self.step_dir, "in", "env"), encoding="utf-8") as fh:
            return dict(line.split("=", 1) for line in fh.read().splitlines())


class ArgvTest(BoxRunnerCase):
    def test_staged_wrapper_symlink_refuses_start_without_touching_its_target(self):
        folder = os.path.join(self.step_dir, "in", "bin")
        os.makedirs(folder)
        target = os.path.join(self.tmp, "wrapper-target")
        with open(target, "w") as fh:
            fh.write("nie ruszać")
        os.symlink(target, os.path.join(folder, "tide-step"))
        with self.assertRaises(agentcmd.StepError):
            self.runner().start(self.step())
        with open(target) as fh:
            self.assertEqual(fh.read(), "nie ruszać")
        self.assertFalse(self.popen_calls)

    def test_tide_test_is_executable_on_agent_path_and_writes_only_step_output(self):
        bg, _ = self.runner().start(self.step())
        env = self.env_file()
        wrapper = shutil.which("tide-test", path=env["PATH"])
        self.assertEqual(wrapper, os.path.join(self.step_dir, "in", "bin", "tide-test"))
        self.assertFalse(os.path.exists(os.path.join(self.step_dir, "out", "tests")))
        result = subprocess.run([wrapper, "/bin/sh", "-c", "echo '3 passed'; exit 7"],
                                env=env, cwd=bg.cwd, capture_output=True)
        self.assertEqual(result.returncode, 7, result.stderr)
        with open(os.path.join(self.step_dir, "out", "tests", "summary.json")) as fh:
            row = json.load(fh)[0]
        self.assertEqual((row["passed"], row["failed"], row["exit_code"]), (3, 0, 7))
        self.assertFalse(os.path.exists(os.path.join(self.worktree, "tests")))
    def test_allocated_ports_only_reach_env_and_docker_host_is_removed(self):
        from tide_daemon.box.ports import PortPool
        env = PortPool("proj", "proj").acquire("K-1/s1", {
            "ports": {"pool": [49123, 49142], "env": ["APP_PORT"]}})
        runner = self.runner(step_env=lambda s: {**env, "DOCKER_HOST": "unix:///host/docker.sock"})
        self.assertNotIn("DOCKER_HOST", runner.env(self.step()))
        runner.start(self.step())
        values = self.env_file()
        for key, value in env.items():
            self.assertEqual(values[key], value)
            self.assertNotIn(value, " ".join(self.popen_calls[0]["argv"]))
        self.assertNotIn("DOCKER_HOST", values)
        self.assertNotIn("DOCKER_HOST", self.popen_calls[0]["env"])

    def test_argv_runs_the_step_as_agent_service_without_any_in_env_value(self):
        mac = local.LocalAgent("mac", "http://mac.lan:11434", "openai", "qwen3-coder:30b", 65536, self.token_file)
        runner = self.runner(step_env=lambda s: {"E2E_PORT": "4301", "TIDE_SECRET_MARK": MARKER + "-env"})
        bg, _proc = runner.start(self.step("codex", local=mac))
        argv = self.popen_calls[0]["argv"]
        self.assertEqual(argv[:4], [self.fake_incus, "exec", "--project", "user-1000"])
        cut = argv.index("--")
        head, tail = argv[:cut], argv[cut + 1:]
        self.assertIn("proj", head)
        for pair in (["--user", "1000"], ["--group", "1000"], ["--cwd", self.worktree]):
            at = head.index(pair[0])
            self.assertEqual(head[at:at + 2], pair)
        self.assertNotIn("--env", argv)
        self.assertEqual(tail[:2], ["/usr/bin/env", "-i"])
        self.assertIn(f"TIDE_STEP={self.step_dir}", tail)
        run = tail.index(br.SYSTEMD_RUN)
        self.assertEqual(tail[run + 1], "--user")
        self.assertIn("--unit=tide-step-s1.service", tail)
        self.assertIn("--wait", tail)
        self.assertIn("CPUWeight=40", tail)
        self.assertEqual(tail[tail.index("--", run) + 1:][:2],
                         ["/bin/sh", os.path.join(self.step_dir, "in", "bin", "tide-step")])
        # Żadna wartość z in/env (token z pliku, zmienne kroku) nie występuje w argv.
        values = self.env_file()
        self.assertEqual(values[local.TOKEN_ENV], MARKER)
        self.assertEqual(values["TIDE_SECRET_MARK"], MARKER + "-env")
        self.assertEqual(values["E2E_PORT"], "4301")
        self.assertFalse([a for a in argv if MARKER in a])
        self.assertFalse([v for v in self.popen_calls[0]["env"].values() if MARKER in v])
        self.assertEqual(self.popen_calls[0]["stdin"], subprocess.DEVNULL)
        st = os.stat(os.path.join(self.step_dir, "in", "env"))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        self.assertEqual(st.st_uid, os.getuid())
        self.assertEqual(bg.unit, "tide-step-s1")

    def test_old_env_file_and_symlink_are_replaced_with_a_fresh_0600_file(self):
        inp = os.path.join(self.step_dir, "in")
        os.makedirs(inp)
        target = os.path.join(self.tmp, "victim")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("nie ruszać\n")
        os.symlink(target, os.path.join(inp, "env"))
        self.runner().start(self.step())
        self.assertFalse(os.path.islink(os.path.join(inp, "env")))
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "nie ruszać\n")

    # Review rundy 2, MAJOR-1: katalog kroku jest rw w pudełku — zapis usługi nie idzie za dowiązaniem.

    def victim(self, name="victim", text="nie ruszać\n"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def assert_refused(self, step=None):
        with self.assertRaises(agentcmd.StepError):
            self.runner().start(step or self.step())
        self.assertEqual(self.popen_calls, [])

    def test_in_dir_symlink_is_refused_and_target_dir_untouched(self):
        target = os.path.join(self.tmp, "victim-dir")
        os.makedirs(target)
        os.makedirs(self.step_dir)
        os.symlink(target, os.path.join(self.step_dir, "in"))
        self.assert_refused()
        self.assertEqual(os.listdir(target), [])

    def test_step_dir_symlink_is_refused(self):
        # Cel w `<repo>-worktrees` (przechodzi sprawdzenie `realpath` w devices_for) — odmawia dopiero StepDir.
        target = os.path.join(self.wt_root, "other.step")
        os.makedirs(target)
        os.symlink(target, self.step_dir)
        self.assert_refused()
        self.assertEqual(os.listdir(target), [])

    def test_prompt_symlink_is_refused_and_target_untouched(self):
        target = self.victim()
        os.makedirs(os.path.join(self.step_dir, "in"))
        os.symlink(target, os.path.join(self.step_dir, "in", "prompt.txt"))
        self.assert_refused()
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "nie ruszać\n")

    def test_prompt_hardlink_is_refused_and_target_untouched(self):
        target = self.victim()
        os.makedirs(os.path.join(self.step_dir, "in"))
        os.link(target, os.path.join(self.step_dir, "in", "settings.json"))
        self.assert_refused()
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "nie ruszać\n")

    def test_exec_log_symlink_is_refused_and_target_untouched(self):
        target = self.victim()
        os.makedirs(os.path.join(self.step_dir, "out"))
        os.symlink(target, os.path.join(self.step_dir, "out", br.EXEC_LOG))
        self.assert_refused()
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "nie ruszać\n")

    def test_existing_regular_files_are_rewritten(self):
        self.runner().start(self.step())
        with open(os.path.join(self.step_dir, "in", "prompt.txt"), "w", encoding="utf-8") as fh:
            fh.write("stara treść, dłuższa niż nowy prompt\n")
        self.runner().start(self.step(step_id="s2"))
        with open(os.path.join(self.step_dir, "in", "prompt.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "Zrób ticket")
        self.assertEqual(len(self.popen_calls), 2)

    def test_newline_in_a_value_is_refused(self):
        with self.assertRaises(agentcmd.StepError):
            self.runner(step_env=lambda s: {"X": "a\nB=b"}).start(self.step())
        self.assertEqual(self.popen_calls, [])

    def test_claude_local_token_goes_to_a_0600_file_read_by_api_key_helper(self):
        mac = local.LocalAgent("mac", "http://mac.lan:11434", "openai", "qwen3-coder:30b", 65536, self.token_file)
        bg, _ = self.runner().start(self.step(local=mac))
        token = os.path.join(self.step_dir, "in", br.LOCAL_TOKEN_FILE)
        self.assertEqual(stat.S_IMODE(os.stat(token).st_mode), 0o600)
        settings = json.loads(bg.files[os.path.join(self.step_dir, "in", "settings.json")])
        self.assertEqual(settings["apiKeyHelper"], f"cat -- {token}")
        self.assertIn(token, settings["sandbox"]["filesystem"]["denyRead"])
        self.assertFalse([a for a in self.popen_calls[0]["argv"] if MARKER in a])
        self.assertNotIn(MARKER, json.dumps(bg.files))


class ProxyEnvTest(BoxRunnerCase):
    WANT = ("localhost", "127.0.0.1", "::1", "172.16.0.0/12", "host.docker.internal", BRIDGE)

    def test_constant_is_exported_from_box_runner(self):
        from tide_daemon.box.runner import BOX_NO_PROXY, box_no_proxy
        self.assertEqual(BOX_NO_PROXY, "localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal")
        self.assertEqual(box_no_proxy(BRIDGE), BOX_NO_PROXY + "," + BRIDGE)
        self.assertNotIn("*", box_no_proxy(BRIDGE))
        with self.assertRaises(ValueError):
            box_no_proxy("*")

    def check(self, env):
        self.assertEqual(env["HTTPS_PROXY"], PROXY)
        self.assertEqual(env["HTTP_PROXY"], PROXY)
        self.assertEqual(env["https_proxy"], PROXY)
        items = env["NO_PROXY"].split(",")
        self.assertEqual(tuple(items), self.WANT)
        for item in self.WANT:
            self.assertIn(item, items)
        self.assertEqual(env["no_proxy"], env["NO_PROXY"])

    def test_in_env_and_env_have_proxy_and_full_no_proxy(self):
        runner = self.runner()
        env = runner.env(self.step())
        self.check(env)
        self.assertEqual(env["HOME"], "/home/agent")
        self.assertEqual(env["USER"], "agent")
        self.assertEqual(env["PATH"].split(os.pathsep),
                         [os.path.join(self.step_dir, "in", "bin"), *br.BOX_PATH.split(os.pathsep)])
        runner.start(self.step())
        self.check(self.env_file())
        self.assertEqual(self.env_file()["TIDE_CWD"], self.worktree)

    def test_host_node_and_chromium_paths_do_not_reach_the_box(self):
        node_home = os.path.join(self.home, ".nvm", "versions", "node", "v22.1.0")
        chrome_bin = os.path.join(self.home, ".cache", "ms-playwright", "chromium-1200", "chrome-linux", "chrome")
        step = self.step(node_home=node_home, chrome_bin=chrome_bin)
        runner = self.runner()
        env = runner.env(step)
        runner.start(step)
        for name, values in (("env(step)", env), ("in/env", self.env_file())):
            with self.subTest(name):
                self.assertNotIn("CHROME_BIN", values)
                # Katalog domowy hosta może pojawić się tylko jako przedrostek repozytorium albo <repo>-worktrees
                # (te same ścieżki w pudełku); ~/.nvm, ~/.cache itp. — nigdy.
                for key, value in values.items():
                    for piece in value.split(os.pathsep):
                        if self.home in piece:
                            self.assertTrue(piece.startswith((self.repo, self.wt_root)),
                                            f"{key}={value} wskazuje katalog domowy hosta")
                self.assertNotIn(node_home, values["PATH"])
                self.assertEqual(values["PATH"].split(os.pathsep),
                                 [os.path.join(self.step_dir, "in", "bin"), *br.BOX_PATH.split(os.pathsep)])
        bstep = runner.box_step(step)
        self.assertEqual((bstep.node_home, bstep.chrome_bin), ("", ""))

    def test_proxy_base_from_running_proxy_and_from_bridge(self):
        running = mock.Mock(listen="10.9.9.1", port=18081)
        with mock.patch("tide_daemon.box.proxy.running", return_value=running):
            self.assertEqual(self.runner(proxy_base="").proxy_base(), "http://10.9.9.1:18081")
        with mock.patch("tide_daemon.box.proxy.running", return_value=None), \
                mock.patch("tide_daemon.box.proxy.bridge_address", return_value="10.9.9.2"):
            self.assertEqual(self.runner(proxy_base="").proxy_base(), "http://10.9.9.2:18080")
        with mock.patch("tide_daemon.box.proxy.running", return_value=None), \
                mock.patch("tide_daemon.box.proxy.bridge_address", return_value=None):
            with self.assertRaises(agentcmd.StepError):
                self.runner(proxy_base="").start(self.step())


class DevicesTest(BoxRunnerCase):
    def device_map(self, box="proj"):
        return {a[7]: (a[8], dict(x.split("=", 1) for x in a[9:])) for a in self.adds(box)}

    def test_first_step_adds_git_overlays_worktrees_home_and_raw_idmap(self):
        gitrepos.git(self.repo, "config", "extensions.worktreeConfig", "true")
        cfg = os.path.join(self.repo, ".git", "worktrees", "pt-1", "config.worktree")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write("[core]\n")
        self.runner().start(self.step())
        devices = self.device_map()
        git = os.path.join(self.repo, ".git")
        want = {
            "tide-git": (git, False), "tide-git-hooks": (os.path.join(git, "hooks"), True),
            "tide-git-config": (os.path.join(git, "config"), True), "tide-git-info": (os.path.join(git, "info"), True),
            "tide-wtcfg-pt-1": (cfg, True), "tide-worktrees": (self.wt_root, False),
        }
        for name, (source, ro) in want.items():
            kind, values = devices[name]
            self.assertEqual(kind, "disk")
            self.assertEqual(values["source"], source)
            self.assertEqual(values["path"], source)          # identyczne ścieżki
            self.assertEqual(values.get("readonly") == "true", ro, name)
            self.assertNotIn("shift", values)                  # P-4: raw.idmap zamiast shift=true
        home = devices["tide-home"][1]
        self.assertEqual(home["path"], "/home/agent")
        self.assertEqual(home["source"], br.host_home_dir("proj"))
        self.assertEqual(stat.S_IMODE(os.stat(home["source"]).st_mode), 0o700)
        # Katalog kroku leży w <repo>-worktrees — zamontowany razem z nim, bez osobnego urządzenia.
        self.assertTrue(self.step_dir.startswith(self.wt_root + os.sep))
        self.assertNotIn("tide-step-s1", devices)
        # Główny worktree projektu i katalog domowy hosta nigdy.
        for _kind, values in devices.values():
            self.assertNotEqual(values["source"], self.repo)
            self.assertNotEqual(values["source"], self.home)
        # Incus montuje `disk` posortowane po `path` (nie po kolejności `device add`): nakładki są nad `.git`, bo ich
        # ścieżki w pudełku leżą pod `.git/`. raw.idmap ustawione.
        for name in ("tide-git-hooks", "tide-git-config", "tide-git-info", "tide-wtcfg-pt-1"):
            self.assertTrue(devices[name][1]["path"].startswith(git + os.sep), name)
            self.assertGreater(devices[name][1]["path"], devices["tide-git"][1]["path"])
        self.assertEqual(self.incus.config_get("proj", "raw.idmap"), self.runner().idmap())

    def test_without_worktree_config_extension_no_config_worktree_overlays_and_no_restart(self):
        """MAJOR-1 (review runda 1): bez `extensions.worktreeConfig` git nie czyta `config.worktree` — nakładki zbędne,
        a nowy worktree z tym plikiem nie zatrzymuje pudełka (kroki projektu biegną równolegle)."""
        self.assertFalse(br.worktree_config_enabled(os.path.join(self.repo, ".git")))
        cfg = os.path.join(self.repo, ".git", "worktrees", "pt-1", "config.worktree")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write("[core]\n")
        # Dane obu worktrees powstają przed migawką strażnika (późniejszy obcy config
        # byłby naruszeniem PT-170 niezależnie od wyłączonej flagi extensions.worktreeConfig).
        other = os.path.join(self.wt_root, "pt-2")
        gitrepos.git(self.repo, "worktree", "add", "-q", "-b", "feat/pt-2", other)
        with open(os.path.join(self.repo, ".git", "worktrees", "pt-2", "config.worktree"), "w") as fh:
            fh.write("[core]\n")
        self.runner().start(self.step())
        self.assertFalse([n for n in self.device_map() if n.startswith("tide-wtcfg")])
        bs.mark_in_use("proj", "proj", "s1")
        self.runner().start(self.step(worktree=other, step_dir=other + ".step", step_id="s2", branch="feat/pt-2"))
        self.assertEqual(len([line for line in calls(self.env) if line.startswith("incus stop")]), 1)
        self.assertEqual(len(self.popen_calls), 2)

    def test_with_extension_step_worktree_gets_an_empty_config_worktree_under_overlay(self):
        """MAJOR-1: przy włączonym rozszerzeniu agent mógłby założyć `config.worktree` (np. z `core.hooksPath`) dla
        worktree kroku — usługa zakłada pusty plik przed montowaniem, więc nakładka ro go obejmuje."""
        gitrepos.git(self.repo, "config", "extensions.worktreeConfig", "true")
        self.assertTrue(br.worktree_config_enabled(os.path.join(self.repo, ".git")))
        cfg = os.path.join(self.repo, ".git", "worktrees", "pt-1", "config.worktree")
        self.assertFalse(os.path.exists(cfg))
        self.runner().start(self.step())
        self.assertTrue(os.path.isfile(cfg))
        self.assertEqual(os.path.getsize(cfg), 0)
        values = self.device_map()["tide-wtcfg-pt-1"][1]
        self.assertEqual(values["source"], cfg)
        self.assertEqual(values["readonly"], "true")

    def test_with_extension_symlinked_config_worktree_is_refused(self):
        gitrepos.git(self.repo, "config", "extensions.worktreeConfig", "true")
        target = os.path.join(self.worktree, "evil.cfg")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("[core]\n\thooksPath = /tmp/x\n")
        os.symlink(target, os.path.join(self.repo, ".git", "worktrees", "pt-1", "config.worktree"))
        with self.assertRaises(agentcmd.StepError):
            self.runner().start(self.step())
        self.assertEqual(self.popen_calls, [])

    def test_with_extension_admin_dir_swapped_for_symlink_after_realpath_is_refused(self):
        """Review rundy 2: `.git/worktrees/<wt>` podmienione na dowiązanie po sprawdzeniu `realpath` (wyścig
        symulowany `realpath` bez rozwijania) — plik nie powstaje w celu dowiązania."""
        gitrepos.git(self.repo, "config", "extensions.worktreeConfig", "true")
        admin = os.path.join(self.repo, ".git", "worktrees", "pt-1")
        target = os.path.join(self.tmp, "victim-admin")
        os.rename(admin, target)
        os.symlink(target, admin)
        with mock.patch("os.path.realpath", os.path.abspath):
            with self.assertRaises(agentcmd.StepError):
                br.BoxRunner._ensure_worktree_config(os.path.join(self.repo, ".git"), self.worktree)
        self.assertFalse(os.path.exists(os.path.join(target, "config.worktree")))

    def test_symlinked_hooks_dir_is_refused_as_a_mount_source(self):
        """Review rundy 2, NIT-1: Incus dostaje literalne `source`, więc składnik-dowiązanie jest odrzucany."""
        hooks = os.path.join(self.repo, ".git", "hooks")
        shutil.rmtree(hooks)
        target = os.path.join(self.tmp, "victim-hooks")
        os.makedirs(target)
        os.symlink(target, hooks)
        with self.assertRaisesRegex(agentcmd.StepError, "dowiązanie"):
            self.runner().devices_for(self.step())

    def test_with_extension_pointer_outside_git_worktrees_is_refused(self):
        gitrepos.git(self.repo, "config", "extensions.worktreeConfig", "true")
        fake_admin = os.path.join(self.wt_root, "admin")
        os.makedirs(fake_admin)
        with open(os.path.join(self.worktree, ".git"), "w", encoding="utf-8") as fh:
            fh.write(f"gitdir: {fake_admin}\n")
        with self.assertRaises(agentcmd.StepError):
            self.runner().devices_for(self.step())
        self.assertFalse(os.path.exists(os.path.join(fake_admin, "config.worktree")))

    def test_step_dir_outside_worktrees_root_is_refused(self):
        with self.assertRaises(agentcmd.StepError):
            self.runner().start(self.step(step_dir=os.path.join(self.tmp, "elsewhere.step")))
        self.assertEqual(self.adds(), [])
        self.assertEqual(self.popen_calls, [])

    def test_stopped_box_refuses_the_step_with_a_clear_error(self):
        self.runner().start(self.step())
        self.incus.stop("proj")
        with self.assertRaisesRegex(agentcmd.StepError, "zatrzymane"):
            self.runner().start(self.step(step_id="s2"))
        self.assertEqual(len(self.popen_calls), 1)

    def test_incus_error_while_adding_devices_is_a_step_error(self):
        runner = self.runner()
        with mock.patch.object(runner.incus, "device_add", side_effect=br.IncusError(["incus"], 1, "boom")):
            with self.assertRaises(agentcmd.StepError):
                runner.start(self.step())
        self.assertEqual(self.popen_calls, [])

    def test_box_is_stopped_while_file_overlays_are_added(self):
        self.runner().start(self.step())
        lines = [shlex.split(line)[1:] for line in calls(self.env) if line.startswith("incus ")]
        subs = [a[0] if a[0] != "config" else " ".join(a[:3] if a[1] == "device" else a[:2]) for a in lines]
        stop, start = subs.index("stop"), len(subs) - 1 - subs[::-1].index("start")
        first_add = subs.index("config device add")
        self.assertLess(stop, subs.index("config set"))
        self.assertLess(stop, first_add)
        self.assertGreater(start, len(subs) - 1 - subs[::-1].index("config device add"))
        self.assertEqual(self.incus.instance("proj")["status"], "Running")

    def manager_calls(self):
        return [i for i, line in enumerate(calls(self.env)) if line == "box-systemctl --user show-environment"]

    def test_after_restart_the_step_waits_for_the_agent_user_manager(self):
        """Próba ręczna PT-169: tuż po starcie pudełka `systemd-run --user` kończył się kodem 1 (brak menedżera)."""
        with open(self.counter + ".need", "w", encoding="utf-8") as fh:
            fh.write("2\n")
        self.runner().start(self.step())
        lines = calls(self.env)
        last_start = max(i for i, line in enumerate(lines) if line.startswith("incus start"))
        tries = self.manager_calls()
        self.assertEqual(len(tries), 3)
        self.assertGreater(tries[0], last_start)
        self.assertEqual(len(self.popen_calls), 1)
        argv = shlex.split([line for line in lines if "show-environment" in line and line.startswith("incus")][0])
        self.assertEqual(argv[argv.index("--user") + 1], "1000")

    def test_manager_that_never_comes_up_refuses_the_step(self):
        with open(self.counter + ".need", "w", encoding="utf-8") as fh:
            fh.write("100000\n")
        self.settings = BoxSettings(project="user-1000", ip_wait_seconds=0)
        with self.assertRaises(agentcmd.StepError):
            self.runner().start(self.step())
        self.assertEqual(self.popen_calls, [])

    def test_no_restart_no_wait(self):
        self.runner().start(self.step())
        before = len(self.manager_calls())
        self.runner().start(self.step(step_id="s2"))
        self.assertEqual(len(self.manager_calls()), before)

    def test_hooks_and_info_dirs_are_created_before_mounting(self):
        shutil.rmtree(os.path.join(self.repo, ".git", "hooks"))
        self.runner().start(self.step())
        self.assertTrue(os.path.isdir(os.path.join(self.repo, ".git", "hooks")))
        self.assertIn("tide-git-hooks", self.device_map())

    def test_second_step_does_not_add_devices_again(self):
        self.runner().start(self.step())
        before = len(self.adds())
        other = os.path.join(self.wt_root, "pt-2")
        gitrepos.git(self.repo, "worktree", "add", "-q", "-b", "feat/pt-2", other)
        self.runner().start(self.step(worktree=other, step_dir=other + ".step", step_id="s2", branch="feat/pt-2"))
        self.assertEqual(len(self.adds()), before)
        self.assertFalse([line for line in calls(self.env) if line.startswith("incus stop")][1:])

    def test_parallel_first_steps_add_devices_once(self):
        other = os.path.join(self.wt_root, "pt-2")
        gitrepos.git(self.repo, "worktree", "add", "-q", "-b", "feat/pt-2", other)
        steps = [self.step(), self.step(worktree=other, step_dir=other + ".step", step_id="s2", branch="feat/pt-2")]
        errors = []
        barrier = threading.Barrier(2)

        def go(step):
            try:
                barrier.wait()
                self.runner().start(step)
            except Exception as exc:        # pragma: no cover - zapis do asercji
                errors.append(exc)

        threads = [threading.Thread(target=go, args=(s,)) for s in steps]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        names = [a[7] for a in self.adds()]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("tide-git", names)

    def test_lock_serialises_device_check(self):
        """Bez blokady drugi wątek widzi listę urządzeń sprzed dodania (wyścig) — blokada plikowa go wstrzymuje."""
        runner = self.runner()
        seen = []
        original = runner._ensure_locked

        def slow(step, wanted):
            seen.append(threading.current_thread().name)
            return original(step, wanted)

        runner._ensure_locked = slow
        lock = br.lock_path("proj")
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        import fcntl
        with open(lock, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            t = threading.Thread(target=runner.ensure_devices, args=(self.step(),), name="w")
            t.start()
            t.join(0.5)
            self.assertTrue(t.is_alive())
            self.assertEqual(seen, [])
            fcntl.flock(fh, fcntl.LOCK_UN)
        t.join(30)
        self.assertEqual(seen, ["w"])

    def test_per_card_devices_go_to_the_card_copy(self):
        card_box = bs.card_box_name("proj", "K-38")
        self.incus.launch("tide-base", card_box)
        runner = self.runner(card="K-38", per_card=True)
        self.assertEqual(runner.box, card_box)
        runner.start(self.step())
        self.assertIn("tide-git", self.device_map(card_box))
        self.assertEqual(self.adds("proj"), [])
        argv = self.popen_calls[0]["argv"]
        self.assertEqual(argv[argv.index("exec") + 3], card_box)

    def test_existing_home_volume_from_builder_is_kept(self):
        self.incus.device_add("proj", "home", "disk", {"pool": "tide", "source": "proj-home", "path": "/home/agent"})
        self.runner().start(self.step())
        self.assertNotIn("tide-home", self.device_map())

    def test_worktree_outside_worktrees_root_and_main_worktree_are_refused(self):
        with self.assertRaises(agentcmd.StepError):
            self.runner().devices_for(self.step(worktree=self.repo, step_dir=self.repo + ".step"))
        runner = self.runner()
        with self.assertRaises(agentcmd.StepError):
            runner._check_sources([br.Device("x", self.repo)], os.path.realpath(self.repo))
        with self.assertRaises(agentcmd.StepError):
            runner._check_sources([br.Device("x", self.home)], os.path.realpath(self.repo))
        with self.assertRaises(agentcmd.StepError):
            runner._check_sources([br.Device("x", os.path.join(self.repo, "src"))], os.path.realpath(self.repo))
        self.assertEqual(self.popen_calls, [])

    def test_running_steps_block_a_stop_for_new_file_overlay(self):
        bs.mark_in_use("proj", "proj", "inny-krok")
        with self.assertRaises(agentcmd.StepError):
            self.runner().start(self.step())
        self.assertEqual(self.popen_calls, [])


class ClaudeSettingsTest(BoxRunnerCase):
    def settings_of(self, bg):
        return json.loads(bg.files[os.path.join(self.step_dir, "in", "settings.json")])

    def test_box_settings_deny_agent_credentials_and_have_no_domains(self):
        step = self.step(allowed_tools=("Read", "Bash(make test*)", "Bash(git status *)"),
                         enforce_command_policy=True, network_domains=("registry.npmjs.org",))
        bg, _ = self.runner().start(step)
        sandbox = self.settings_of(bg)["sandbox"]
        self.assertTrue(sandbox["enabled"])
        self.assertTrue(sandbox["failIfUnavailable"])
        for path in ("~/.claude", "~/.codex", "/packs"):
            self.assertIn(path, sandbox["filesystem"]["denyRead"])
        self.assertNotIn("~", sandbox["filesystem"]["denyRead"])
        self.assertIn(os.path.join(self.step_dir, "in", "env"), sandbox["filesystem"]["denyRead"])
        self.assertEqual(sandbox["network"]["allowedDomains"], [])
        argv = bg.argv
        tools = argv[argv.index("--allowedTools") + 1:]
        self.assertFalse([t for t in tools if t.startswith("Bash(")])
        self.assertIn("Read", tools)
        self.assertIn("Bash", tools)
        self.assertTrue(sandbox["autoAllowBashIfSandboxed"])
        # Plik ustawień jest w katalogu kroku (pudełko), nie w ustawieniach hosta.
        self.assertTrue(os.path.isfile(os.path.join(self.step_dir, "in", "settings.json")))

    def test_sandbox_disabled_variant_writes_k1_risk_to_card_journal_once(self):
        events = []
        runner = self.runner(sandbox=False, journal=lambda kind, fields: events.append((kind, fields)))
        bg, _ = runner.start(self.step())
        sandbox = self.settings_of(bg)["sandbox"]
        self.assertFalse(sandbox["enabled"])
        self.assertFalse(sandbox["failIfUnavailable"])
        self.assertIn("~/.claude", sandbox["filesystem"]["denyRead"])
        runner.start(self.step(step_id="s2"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "box.risk")
        self.assertIn("ryzyko K1", events[0][1]["text"])

    def test_default_follows_p2_result(self):
        self.assertTrue(br.BOX_SANDBOX)
        self.assertTrue(self.runner().sandbox)

    def test_codex_keeps_workspace_write_sandbox(self):
        bg, _ = self.runner().start(self.step("codex"))
        self.assertEqual(bg.argv[bg.argv.index("--sandbox") + 1], "workspace-write")


class KillTest(BoxRunnerCase):
    def setUp(self):
        super().setUp()
        # systemctl w „pudełku”: is-active odpowiada stanem z pliku, kill go zmienia.
        self.state_file = os.path.join(self.tmp, "unit-active")
        # `env -i` przed systemctl czyści zmienne, więc ścieżka dziennika jest wpisana w skrypt.
        self.log_line = f'#!/bin/sh\necho "box-systemctl $*" >> {shlex.quote(self.env["TIDE_FAKE_LOG"])}\n'
        box_ctl = os.path.join(self.tmp, "fakebin", "box", "systemctl-unit")
        with open(box_ctl, "w", encoding="utf-8") as fh:
            # is-active jak prawdziwy systemctl: 0 — aktywna, 3 — nieaktywna (`incus exec` przekazuje kod).
            fh.write(self.log_line + f'case "$*" in *is-active*) [ -f {shlex.quote(self.state_file)} ] || exit 3 ;; '
                                     f'*kill*) rm -f {shlex.quote(self.state_file)} ;; esac\n')
        os.chmod(box_ctl, 0o755)
        self.box_ctl = box_ctl

    def test_kill_calls_systemctl_user_kill_through_incus_exec(self):
        open(self.state_file, "w").close()
        runner = self.runner(systemctl=self.box_ctl)
        self.assertTrue(runner.kill({"unit": "tide-step-s1"}, grace=1))
        lines = calls(self.env)
        kill = [line for line in lines if line.startswith("incus exec") and " kill " in line]
        self.assertTrue(kill)
        argv = shlex.split(kill[0])
        self.assertIn("--project", argv)
        self.assertIn("proj", argv)
        self.assertEqual(argv[argv.index("--user") + 1], "1000")
        self.assertIn("--user", argv[argv.index(self.box_ctl):])
        self.assertIn("tide-step-s1.service", argv)
        self.assertIn("box-systemctl --user kill --signal=SIGTERM tide-step-s1.service", lines)

    def test_kill_escalates_to_sigkill_after_grace(self):
        with open(self.box_ctl, "w", encoding="utf-8") as fh:
            fh.write(self.log_line + 'case "$*" in *is-active*) exit 0 ;; esac\n')
        runner = self.runner(systemctl=self.box_ctl, sleep=lambda s: None)
        self.assertFalse(runner.kill({"unit": "tide-step-s1"}, grace=0))
        self.assertIn("box-systemctl --user kill --signal=SIGKILL tide-step-s1.service", calls(self.env))

    def test_client_error_is_not_taken_for_a_stopped_unit(self):
        """MAJOR-2 (review runda 1): kod 1 z `incus exec` (błąd klienta, incusd niedostępny) to stan nieznany —
        `kill` nie może zgłosić zatrzymania, `alive` nie może zgłosić końca."""
        with open(self.box_ctl, "w", encoding="utf-8") as fh:
            fh.write(self.log_line + 'exit 1\n')
        runner = self.runner(systemctl=self.box_ctl)
        self.assertEqual(runner.unit_state("proj", "tide-step-s1.service"), "unknown")
        self.assertFalse(runner.kill({"unit": "tide-step-s1"}, grace=0))
        self.assertTrue(runner.alive({"unit": "tide-step-s1"}))

    def test_box_stopped_before_kill_is_unknown_not_dead(self):
        open(self.state_file, "w").close()
        runner = self.runner(systemctl=self.box_ctl)
        self.incus.stop("proj")
        self.assertFalse(runner.kill({"unit": "tide-step-s1"}, grace=0))
        self.assertTrue(runner.alive({"unit": "tide-step-s1"}))

    def test_inactive_unit_is_dead_and_not_alive(self):
        runner = self.runner(systemctl=self.box_ctl)
        self.assertEqual(runner.unit_state("proj", "tide-step-s1.service"), "inactive")
        self.assertFalse(runner.alive({"unit": "tide-step-s1"}))
        self.assertTrue(runner.kill({"unit": "tide-step-s1"}, grace=0))

    def test_kill_uses_box_from_step_entry_and_validates_names(self):
        runner = self.runner(systemctl=self.box_ctl)
        self.incus.launch("tide-base", "proj-k-38")
        runner.kill({"unit": "tide-step-s1", "box": "proj-k-38"}, grace=0)
        self.assertTrue([line for line in calls(self.env) if line.startswith("incus exec") and "proj-k-38" in line])
        with self.assertRaises(agentcmd.StepError):
            runner.kill({"unit": "tide-step-s1; rm -rf /"}, grace=0)


class EndToEndTest(BoxRunnerCase):
    """Krok przez atrapę `incus exec` → atrapę systemd-run → prawdziwe `tide-step` (lokalnie) → polecenie."""

    def test_step_runs_in_worktree_with_in_env_and_writes_exit_code(self):
        fake_run = os.path.join(self.tmp, "systemd-run-box")
        with open(fake_run, "w", encoding="utf-8") as fh:
            fh.write(FAKE_SYSTEMD_RUN)
        os.chmod(fake_run, 0o755)
        runner = self.runner(popen=subprocess.Popen, systemd_run=fake_run,
                             tide_step=("/bin/sh", br.TIDE_STEP_SCRIPT),
                             step_env=lambda s: {"TIDE_SECRET_MARK": MARKER})

        def use_command(bg):
            bg.argv = ["/bin/sh", "-c", 'pwd; echo "mark=$TIDE_SECRET_MARK home=$HOME"; cat; exit 3']

        bg, proc = runner.start(self.step(), before_start=use_command)
        self.assertEqual(proc.wait(timeout=30), 3)
        with open(bg.log_file, encoding="utf-8") as fh:
            log = fh.read()
        self.assertIn(os.path.realpath(self.worktree), log)
        self.assertIn(f"mark={MARKER} home=/home/agent", log)
        self.assertIn("Zrób ticket", log)
        with open(bg.exit_file, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "3")
        self.assertFalse([line for line in calls(self.env) if MARKER in line])


class DemoArgsTest(unittest.TestCase):
    def test_options_after_slug_are_not_swallowed_by_the_command(self):
        """Próba ręczna PT-169: `--push-tide-step` po slugu trafiało do polecenia, a krok szukał
        /usr/local/bin/tide-step."""
        args, command = br.demo_args(["tide-proto", "--worktree", "/w", "--push-tide-step", "--card", "K-38",
                                      "--branch", "ticket/probe",
                                      "--", "/bin/sh", "-c", "echo --worktree"])
        self.assertEqual((args.slug, args.worktree, args.push_tide_step, args.card),
                         ("tide-proto", "/w", True, "K-38"))
        self.assertEqual(args.branch, "ticket/probe")
        self.assertEqual(command, ["/bin/sh", "-c", "echo --worktree"])

    def test_command_is_required_after_double_dash(self):
        with mock.patch("sys.stderr"):
            for argv in (["tide-proto", "true"], ["tide-proto", "--"]):
                with self.subTest(argv=argv), self.assertRaises(SystemExit):
                    br.demo_args(argv)

    def test_demo_requires_explicit_expected_branch(self):
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            br.demo_args(["tide-proto", "--", "true"])


class TideStepTest(unittest.TestCase):
    """`tide-step` lokalnie, bez Incusa."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="tide-step-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.step = os.path.join(self.tmp, "wt.step")
        self.cwd = os.path.join(self.tmp, "wt")
        os.makedirs(os.path.join(self.step, "in"))
        os.makedirs(self.cwd)
        with open(os.path.join(self.step, "in", "prompt.txt"), "w", encoding="utf-8") as fh:
            fh.write("polecenie z pliku\n")

    def write_env(self, text, mode=0o600):
        path = os.path.join(self.step, "in", "env")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(path, mode)

    def run_step(self, *command):
        env = {"TIDE_STEP": self.step, "PATH": "/usr/bin:/bin", "LEAK": "z-zewnatrz"}
        return subprocess.run(["/bin/sh", br.TIDE_STEP_SCRIPT, *command], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30)

    def log(self):
        with open(os.path.join(self.step, "out", "step.log"), encoding="utf-8") as fh:
            return fh.read()

    def test_exports_env_runs_in_cwd_and_passes_exit_code(self):
        self.write_env(f"PATH=/usr/bin:/bin\nTIDE_CWD={self.cwd}\nTOKEN=a b $HOME `id` ;x\n\n# komentarz\n"
                       "EQ=k=v\n")
        proc = self.run_step("/bin/sh", "-c", 'pwd; printf "%s|%s|%s\\n" "$TOKEN" "$EQ" "${LEAK:-brak}"; cat; '
                                              'exit 5')
        self.assertEqual(proc.returncode, 5, proc.stderr)
        log = self.log()
        self.assertIn(os.path.realpath(self.cwd), log)
        self.assertIn("a b $HOME `id` ;x|k=v|brak", log)
        self.assertIn("polecenie z pliku", log)
        with open(os.path.join(self.step, "in", "exit-code"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "5")

    def test_refuses_env_with_mode_other_than_0600(self):
        for mode in (0o644, 0o640, 0o400):
            with self.subTest(mode=oct(mode)):
                self.write_env(f"TIDE_CWD={self.cwd}\n", mode)
                proc = self.run_step("/bin/true")
                self.assertEqual(proc.returncode, 125)
                self.assertIn("600", proc.stderr)
                self.assertFalse(os.path.exists(os.path.join(self.step, "in", "exit-code")))

    def test_refuses_missing_file_symlink_and_bad_lines(self):
        proc = self.run_step("/bin/true")
        self.assertEqual(proc.returncode, 125)
        real = os.path.join(self.tmp, "real-env")
        with open(real, "w", encoding="utf-8") as fh:
            fh.write("A=1\n")
        os.chmod(real, 0o600)
        os.symlink(real, os.path.join(self.step, "in", "env"))
        self.assertEqual(self.run_step("/bin/true").returncode, 125)
        os.unlink(os.path.join(self.step, "in", "env"))
        for bad in ("1A=x\n", "A-B=x\n", "BEZROWNOSCI\n", "=x\n"):
            with self.subTest(bad=bad):
                self.write_env(bad)
                self.assertEqual(self.run_step("/bin/true").returncode, 125)

    def test_success_writes_zero(self):
        self.write_env(f"PATH=/usr/bin:/bin\nTIDE_CWD={self.cwd}\n")
        proc = self.run_step("/bin/true")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(self.step, "in", "exit-code"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "0")


if __name__ == "__main__":
    unittest.main()
