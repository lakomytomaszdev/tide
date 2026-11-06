"""PT-191: CLI przez prawdziwy BoxRunner i atrapę Incusa, bez agenta i sieci."""
import io
import json
import os
import subprocess
from unittest import mock

from tide_daemon.box import builder as bb, cli, command, profile as bp, runner as br, state as bs
from tide_daemon.box.ports import PortPool
from .test_box_runner import BoxRunnerCase, FAKE_SYSTEMD_RUN
from . import gitrepos


class FakeProxy:
    listen, port = "10.173.184.1", 18080

    def __init__(self):
        self.calls, self.steps = [], {}

    def register_step(self, ip, owner, key, log):
        self.steps[owner] = (ip, key, log)

    def revoke_step(self, owner):
        self.steps.pop(owner, None)

    def set_box_policy(self, ip, domains, ollama, log, **kw):
        self.calls.append((ip, list(domains), ollama, log, kw))

    def clear_box_policy(self, ip):
        self.calls.append(("clear", ip))


class CommandTest(BoxRunnerCase):
    def setUp(self):
        super().setUp()
        self.profile = {"tide-box": 1, "network": {"domains": ["registry.npmjs.org"]},
                        "ports": {"pool": [49123, 49142], "env": ["APP_PORT"]}}
        bp.save_project("proj", self.profile)
        self.proxy = FakeProxy()
        self.builder = bb.Builder(self.settings, incus=self.incus, policy=self.proxy)
        self.builder.steps_source = bs.running_steps
        self.builder._attach_home("proj")
        self.builder.apply_nic_filter("proj")
        stamp = self.builder.stamp(self.profile)
        self.incus.config_set("proj", {bs.RECIPE_KEY: json.dumps(stamp)})
        self.incus.snapshot_create("proj", bs.SNAPSHOT)
        bs.record("proj", "box.ready", "proj", hash=stamp["hash"], duration=0.1)
        self.incus.start("proj")
        self.launches = []
        self.unit_state = "inactive"
        self.spawn_error = False
        self.systemd = os.path.join(self.tmp, "box-systemd-run")
        with open(self.systemd, "w") as fh:
            fh.write(FAKE_SYSTEMD_RUN)
        os.chmod(self.systemd, 0o755)
        self.real_runner = br.BoxRunner
        for patcher in (mock.patch.object(command.base, "ensure_proxy", return_value=(self.proxy, False)),
                        mock.patch.object(command, "project_repo", return_value=self.repo),
                        mock.patch.object(command.br, "BoxRunner", side_effect=self.command_runner)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def command_runner(self, settings, slug, **kw):
        kw.update(systemd_run=self.systemd, systemctl=self.box_systemctl,
                  popen=self.spawn)
        runner = self.real_runner(settings, slug, **kw)
        runner.unit_state = lambda box, unit: self.unit_state
        return runner

    def spawn(self, argv, **kw):
        self.launches.append((argv, kw))
        if self.spawn_error:
            raise OSError("spawn failed")
        return subprocess.Popen(argv, **kw)

    def run_cli(self, argv=None):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(argv or ["run", "proj", "--worktree", self.worktree, "--", "/usr/bin/env"],
                        out=out, err=err, builder=self.builder)
        return code, out.getvalue(), err.getvalue()

    def env_for_launch(self):
        argv = self.launches[-1][0]
        folder = next(a.partition("=")[2] for a in argv if a.startswith("TIDE_STEP="))
        with open(os.path.join(folder, "in", "env")) as fh:
            env = dict(line.rstrip("\n").split("=", 1) for line in fh)
        return folder, env

    def test_run_uses_step_policy_devices_ports_and_no_agent_tokens(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "host-secret", "OPENAI_API_KEY": "host-secret",
                                         "GH_TOKEN": "host-secret", "DOCKER_HOST": "unix:///host/docker.sock"}):
            code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        folder, env = self.env_for_launch()
        self.assertIn("HTTPS_PROXY=http://tide:", out)
        self.assertEqual(env["NO_PROXY"], br.box_no_proxy(self.proxy.listen))
        self.assertEqual(env["TIDE_PORT_BASE"], "49123")
        self.assertEqual(env["APP_PORT"], "49123")
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GH_TOKEN", "DOCKER_HOST"):
            self.assertNotIn(name, env)
        argv, kw = self.launches[-1]
        self.assertNotIn("host-secret", str((argv, kw["env"])))
        self.assertNotIn(env["HTTPS_PROXY"], argv)
        self.assertNotIn("49123", argv)
        self.assertEqual(argv[-1], "/usr/bin/env")
        self.assertIn("--user", argv)
        self.assertIn("registry.npmjs.org", self.proxy.calls[0][1])
        self.assertFalse(self.proxy.steps)
        devices = self.incus.device_show("proj")
        for name in ("tide-git", "tide-git-hooks", "tide-git-config", "tide-git-info", "tide-worktrees"):
            self.assertIn(name, devices)
        self.assertEqual(devices["tide-git-config"]["readonly"], "true")
        self.assertFalse(os.path.exists(os.path.join(folder, "in", "settings.json")))
        self.assertEqual(bs.running_steps("proj", "proj"), [])
        self.assertEqual(PortPool("proj", "proj").acquire("later", self.profile)["PORT"], "49123")

    def test_separator_preserves_argv_and_exit_status(self):
        tail = ["/bin/sh", "-c", 'printf "%s\\n" "$@"; exit 37', "args", "--card", "a b", "$(false)", ""]
        code, out, err = self.run_cli(["run", "proj", "--worktree", self.worktree, "--", *tail])
        self.assertEqual(code, 37, err)
        self.assertEqual(out, "--card\na b\n$(false)\n\n")
        self.assertEqual(self.launches[-1][0][-len(tail):], tail)

    def test_default_wrapper_matches_runner_without_relying_on_the_image_version(self):
        code, out, err = self.run_cli(["run", "proj", "--worktree", self.worktree,
                                       "--", "/bin/sh", "-c", "printf compatible; exit 23"])
        self.assertEqual(code, 23, err)
        self.assertEqual(out, "compatible")
        folder, _env = self.env_for_launch()
        wrapper = os.path.join(folder, "in", "bin", "tide-step")
        with open(wrapper) as actual, open(br.TIDE_STEP_SCRIPT) as expected:
            self.assertEqual(actual.read(), expected.read())
        self.assertIn(wrapper, self.launches[-1][0])

    def test_card_mounts_only_accepted_prepared_copies_readonly_and_removes_them(self):
        bp.save_card("proj", "K-38", {"tide-box": 1, "packs": ["logs"], "accepted_red": ["pack:logs"]})
        source = os.path.join(os.path.dirname(br.host_home_dir("proj")), "packs", "k-38", "logs")
        os.makedirs(source)
        # Zachowujemy argv device add, bo po zakończeniu urządzenie ma zostać zdjęte.
        add = self.incus.device_add
        with mock.patch.object(self.incus, "device_add", wraps=add) as added:
            code, out, err = self.run_cli(["run", "proj", "--card", "K-38", "--worktree", self.worktree,
                                           "--", "/bin/true"])
        self.assertEqual(code, 0, err)
        packs = [c for c in added.call_args_list if c.args[1].startswith("tide-pack-")]
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0].args[3], {"source": source, "path": "/packs/logs", "readonly": True})
        self.assertNotIn("tide-pack-k-38-logs", self.incus.device_show("proj"))

    def test_unaccepted_pack_is_never_mounted(self):
        bp.save_card("proj", "K-38", {"tide-box": 1, "packs": ["logs"]})
        code, _, err = self.run_cli(["run", "proj", "--card", "K-38", "--worktree", self.worktree,
                                     "--", "/bin/true"])
        self.assertEqual(code, 0, err)
        self.assertFalse(any(k.startswith("tide-pack-") for k in self.incus.device_show("proj")))

    def test_spawn_failure_releases_ports_and_policy(self):
        self.spawn_error = True
        code, _, err = self.run_cli()
        self.assertEqual(code, 1, err)
        self.assertEqual(bs.running_steps("proj", "proj"), [])
        self.assertFalse(self.proxy.steps)
        self.assertEqual(PortPool("proj", "proj").acquire("later", self.profile)["PORT"], "49123")

    def test_unknown_unit_keeps_reservations_and_next_run_recovers_after_confirmed_end(self):
        self.unit_state = "unknown"
        code, _, err = self.run_cli()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(bs.running_steps("proj", "proj")), 1)
        self.assertIn("rezerwacja pozostaje", err)
        self.unit_state = "inactive"
        code, _, err = self.run_cli()
        self.assertEqual(code, 0, err)
        self.assertEqual(bs.running_steps("proj", "proj"), [])

    def test_main_repository_gets_separate_worktree_and_never_mounts_main_tree(self):
        with mock.patch.object(command.os, "getcwd", return_value=self.repo):
            code, _, err = self.run_cli(["run", "proj", "--", "/bin/pwd"])
        self.assertEqual(code, 0, err)
        folder, env = self.env_for_launch()
        self.assertNotEqual(env["TIDE_CWD"], self.repo)
        self.assertTrue(env["TIDE_CWD"].startswith(self.repo + "-worktrees/"))
        self.assertEqual(gitrepos.git(env["TIDE_CWD"], "branch", "--show-current").split("/")[0], "box-run")

    def test_missing_or_empty_separator_is_argument_error(self):
        for argv in (["run", "proj"], ["run", "proj", "--"]):
            self.assertEqual(self.run_cli(argv)[0], 2)
        self.assertFalse(self.launches)

    def test_missing_nic_filter_and_changed_clean_snapshot_refuse_to_launch(self):
        self.incus.device_set("proj", "eth0", {"security.ipv4_filtering": False})
        code, _, err = self.run_cli()
        self.assertEqual(code, 1, err)
        self.assertFalse(self.launches)
        self.builder.apply_nic_filter("proj")
        self.incus.config_set("proj", {bs.RECIPE_KEY: "wrong"})
        self.incus.snapshot_create("proj", "clean", reuse=True)
        self.incus.config_set("proj", {bs.RECIPE_KEY: json.dumps(self.builder.stamp(self.profile))})
        code, _, err = self.run_cli()
        self.assertEqual(code, 1, err)
        self.assertIn("migawki clean", err)
        self.assertFalse(self.launches)

    def test_exhausted_pool_does_not_launch_or_mutate_proxy(self):
        pool = PortPool("proj", "proj")
        pool.acquire("K-1/first", self.profile)
        pool.acquire("K-2/second", self.profile)
        code, _, err = self.run_cli()
        self.assertEqual(code, 3, err)
        self.assertFalse(self.launches)
        self.assertEqual(self.proxy.calls, [])
        self.assertEqual(pool.acquire("K-1/first", self.profile)["PORT"], "49123")

    def test_prepared_pack_symlink_refuses_to_launch(self):
        bp.save_card("proj", "K-38", {"tide-box": 1, "packs": ["logs"], "accepted_red": ["pack:logs"]})
        root = os.path.join(os.path.dirname(br.host_home_dir("proj")), "packs", "k-38")
        os.makedirs(root)
        os.symlink(self.repo, os.path.join(root, "logs"))
        code, _, err = self.run_cli(["run", "proj", "--card", "K-38", "--worktree", self.worktree,
                                     "--", "/bin/true"])
        self.assertEqual(code, 1, err)
        self.assertFalse(self.launches)

    def test_ref_guard_reports_foreign_ref_without_restoring_and_still_cleans_resources(self):
        code, _, err = self.run_cli(["run", "proj", "--worktree", self.worktree, "--", "/usr/bin/git",
                                     "update-ref", "-d", "refs/heads/master"])
        self.assertEqual(code, 1, err)
        self.assertIn("refs.guard", err)
        self.assertIn("refs/heads/master", err)
        self.assertNotIn("refs/heads/master", gitrepos.git(self.repo, "for-each-ref", "--format=%(refname)"))
        self.assertEqual(bs.running_steps("proj", "proj"), [])
        self.assertFalse(self.proxy.steps)
        self.assertEqual(self.proxy.calls[-1][0], "clear")

    def test_legal_commit_of_other_card_during_command_is_preserved(self):
        other = os.path.join(self.tmp, "other-card")
        gitrepos.git(self.repo, "worktree", "add", "-b", "card/k-55", other)
        spawn = self.spawn
        tips = []

        def concurrent_commit(argv, **kw):
            gitrepos.commit(other, "plan.md", "legalny zapis innej karty\n")
            tips.append(gitrepos.git(other, "rev-parse", "HEAD").strip())
            return spawn(argv, **kw)

        with mock.patch.object(self, "spawn", side_effect=concurrent_commit):
            code, _, err = self.run_cli()
        self.assertEqual(code, 1, err)  # nierozpoznana zmiana jest raportowana
        self.assertIn("refs/heads/card/k-55", err)
        self.assertEqual(gitrepos.git(other, "rev-parse", "HEAD").strip(), tips[0])

    def test_per_card_profile_uses_copy_and_the_same_card_policy(self):
        bp.save_card("proj", "K-38", {"tide-box": 1, "box": "per-card",
                                       "network": {"domains": ["extra.example.org"]}})
        code, _, err = self.run_cli(["run", "proj", "--card", "K-38", "--worktree", self.worktree,
                                     "--", "/bin/true"])
        self.assertEqual(code, 0, err)
        self.assertIn("extra.example.org", self.proxy.calls[0][1])
        self.assertEqual(self.proxy.calls[0][-1]["box"], "proj-k-38")
        self.assertIn("proj-k-38", self.launches[-1][0])
