"""PT-264: dokładny manifest i efektywne urządzenia na atrapie Incusa."""
import json
import os
import subprocess
from pathlib import Path
from unittest import mock

from .test_box_runner import BoxRunnerCase, PROXY
from . import gitrepos
from tide_daemon.box import exposure, mounts, packs, state as bs
from tide_daemon.box.runner import BoxRunner, MountsBusy, host_home_dir
from tide_daemon.tasks import agentcmd, inputs


class ProjectMountTests(BoxRunnerCase):
    def setUp(self):
        super().setUp()
        self.slug = exposure.PROJECT
        self.incus.launch("tide-base", self.slug)
        self.incus.device_override(self.slug, self.settings.nic, bs.nic_flags(self.settings))
        # Atrapa dzieli globalny slot z pudełkiem historycznego fixture.
        self.incus.stop("proj")
        self.original = Path(self.tmp) / "source.txt"
        self.original.write_text("dane syntetyczne")
        self.spec = {"name": "data", "from": str(self.original)}
        self.copy = packs.create_snapshot(self.slug, "K-38", self.spec)
        self.service = exposure.ExposureService(self.slug, [self.repo], {self.slug: [self.repo]})
        self.profile = {"tide-box": 1, "project": self.slug, "packs": [self.spec]}
        proposal = self.service.preview(self.profile, [self.copy.id])
        self.record = self.service.approve(self.profile, [self.copy.id], expected=proposal["expected"], accepted=True)
        self.newrunner = BoxRunner(self.settings, self.slug, card="K-38", incus=self.incus, proxy_base=PROXY,
                                   env=dict(os.environ), systemctl=self.box_systemctl, sleep=lambda _: None,
                                   popen=self.popen, mount_steps=lambda box: [])
        self.chosen = self.step(exposure_inputs=inputs.Selection(self.slug, self.record["version"], (self.copy.id,)))

    def test_manifest_uses_readonly_administration_and_exact_project_devices(self):
        manifest = self.record["manifest"]
        self.assertEqual(manifest["mountContract"], 2)
        self.assertIn({"source": self.repo + "/.git", "target": self.repo + "/.git", "mode": "ro", "kind": "git"},
                      manifest["mounts"])
        self.newrunner.ensure_devices(self.chosen)
        devices = self.incus.instance(self.slug)["expanded_devices"]
        expected = {mounts.device_name(m): mounts.values(m) for m in manifest["mounts"]}
        for name, value in expected.items():
            self.assertTrue(mounts.equal(devices[name], value), (name, devices[name]))
        self.assertEqual(self.newrunner.ensure_devices(self.chosen), [])
        sources = [d.get("source") for d in devices.values()]
        self.assertNotIn(os.path.dirname(self.repo), sources)
        self.assertNotIn(self.home, sources)
        self.assertNotIn(str(self.original), sources)
        self.assertNotIn(exposure.root(self.slug), sources)

    def test_changed_inherited_or_local_devices_refuse_without_repair_or_launch(self):
        for entry in ({"source": self.home, "path": "/host"},
                      {"source": "/run/docker.sock", "path": "/run/docker.sock"},
                      {"source": exposure.root(self.slug), "path": "/host-state"}):
            with self.subTest(entry=entry):
                self.incus.device_add(self.slug, "foreign", "disk", entry)
                with self.assertRaisesRegex(agentcmd.StepError, "urządzenie"):
                    self.newrunner.ensure_devices(self.chosen)
                self.incus.device_remove(self.slug, "foreign")
        # expanded_devices stanowi źródło prawdy także dla obcego urządzenia profilu.
        original = self.incus.instance
        def inherited(name):
            inst = original(name)
            inst["expanded_devices"]["socket"] = {"type": "unix-char", "source": "/dev/kvm"}
            return inst
        with mock.patch.object(self.incus, "instance", side_effect=inherited):
            with self.assertRaises(agentcmd.StepError):
                self.newrunner.ensure_devices(self.chosen)
        self.assertFalse(self.popen_calls)

    def test_existing_mount_name_does_not_hide_a_different_source(self):
        self.newrunner.ensure_devices(self.chosen)
        mount = self.record["manifest"]["mounts"][0]
        self.incus.device_set(self.slug, mounts.device_name(mount), {"source": self.home})
        with self.assertRaises(agentcmd.StepError):
            self.newrunner.ensure_devices(self.chosen)

    def test_revocation_and_foreign_selection_refuse_before_step_writes(self):
        exposure.revoke(self.slug, self.record["version"])
        with mock.patch.object(self.newrunner, "_background") as bg:
            with self.assertRaises(agentcmd.StepError):
                self.newrunner.start(self.chosen)
            bg.assert_not_called()
        self.assertFalse(os.path.exists(self.step_dir))

    def test_live_step_blocks_mount_change_without_resetting_other_cards(self):
        self.newrunner.mount_steps = lambda box: ["other-card/step"]
        with self.assertRaises(MountsBusy):
            self.newrunner.ensure_devices(self.chosen)
        self.assertEqual(self.incus.instance(self.slug)["status"], "Running")

    def test_copy_is_detached_before_deletion_idempotently_without_reset(self):
        self.newrunner.ensure_devices(self.chosen)
        owned = mounts.ProjectMounts(self.newrunner)
        self.assertTrue(packs.delete_snapshot(self.slug, self.copy.id, confirm_detached=owned.detach_snapshot))
        self.assertFalse(packs.delete_snapshot(self.slug, self.copy.id, confirm_detached=owned.detach_snapshot))
        self.assertEqual(self.incus.instance(self.slug)["status"], "Running")
        self.assertFalse(any(d.get("path") == "/packs/data"
                             for d in self.incus.instance(self.slug)["expanded_devices"].values()))

    def test_readwrite_project_and_other_card_git_remain_allowed(self):
        # To kontrola wspólnego Git, nie symulacja kernela/ro w atrapie Incusa.
        self.newrunner.ensure_devices(self.chosen)
        path = Path(self.repo + "-worktrees") / "second"
        gitrepos.git(self.repo, "worktree", "add", "-q", "-b", "card/second", str(path))
        gitrepos.commit(str(path), "second.txt", "wspólne obiekty")
        from tide_daemon.worktrees.git import Git
        result = Git().run(self.worktree, "show", "card/second:second.txt")
        self.assertTrue(result.ok, result.err)
        self.assertEqual(result.out, "wspólne obiekty")

    def test_box_git_view_commits_without_changing_host_administration_and_syncs_index(self):
        self.newrunner.ensure_devices(self.chosen)
        from tide_daemon.box import gitview
        from tide_daemon.worktrees.git import Git
        admin = Path((Path(self.worktree) / ".git").read_text().strip()[8:])
        original_head = (admin / "HEAD").read_bytes()
        original_index = (admin / "index").read_bytes()
        (Path(self.worktree) / "box.txt").write_text("z pudełka")
        env = {**gitrepos.ENV, "HOME": host_home_dir(self.slug)}
        for args in (("add", "box.txt"), ("commit", "-m", "add box data")):
            run = subprocess.run(["/usr/bin/python3", gitview.__file__, "-C", self.worktree, *args],
                                 env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual((admin / "HEAD").read_bytes(), original_head)
        self.assertEqual((admin / "index").read_bytes(), original_index)
        result = Git().run(self.worktree, "status", "--porcelain")
        self.assertTrue(result.ok, result.err)
        self.assertEqual(result.out, "")
        self.assertEqual(Git().run(self.worktree, "show", "HEAD:box.txt").out, "z pudełka")

    def test_hardlinked_host_file_is_not_exposed_by_a_canonical_workspace(self):
        victim = Path(self.tmp) / "outside-project.txt"
        victim.write_text("nie zmieniaj")
        os.link(victim, Path(self.worktree) / "alias.txt")
        with self.assertRaisesRegex(agentcmd.StepError, "hardlink"):
            self.newrunner.ensure_devices(self.chosen)
        self.assertEqual(victim.read_text(), "nie zmieniaj")

    def test_legacy_manifest_requires_a_fresh_project_consent(self):
        legacy = json.loads(json.dumps(self.record))
        legacy["manifest"].pop("mountContract")
        legacy["manifest"]["mounts"][0]["mode"] = "rw"
        legacy["digest"] = exposure._hash(legacy["manifest"])
        legacy["version"] = "1:" + legacy["digest"]
        # Hostowa historia poprzedniej wersji kontraktu, nie plik otrzymany z pudełka.
        with exposure._store(self.slug) as fd:
            exposure._write(fd, "manifest-1.json", legacy, replace=True)
            exposure._write(fd, "state.json", {"schema": 1, "generation": 1, "active": True, "record": legacy}, replace=True)
        proposal = self.service.preview(self.profile, [self.copy.id])
        with self.assertRaisesRegex(exposure.ExposureError, "świadoma"):
            self.service.approve(self.profile, [self.copy.id], expected=proposal["expected"], accepted=False)
        approved = self.service.approve(self.profile, [self.copy.id], expected=proposal["expected"], accepted=True)
        self.assertNotEqual(approved["version"], legacy["version"])
        self.assertEqual(approved["manifest"]["mountContract"], 2)
        with self.assertRaises(agentcmd.StepError):
            self.newrunner.ensure_devices(self.chosen)

    def test_new_worktree_after_mount_gets_a_fresh_host_index_after_box_commit(self):
        self.newrunner.ensure_devices(self.chosen)
        from tide_daemon.box import gitview
        from tide_daemon.worktrees.git import Git
        wt = Path(self.repo + "-worktrees") / "late"
        gitrepos.git(self.repo, "worktree", "add", "-q", "-b", "card/late", str(wt))
        (wt / "late.txt").write_text("późny worktree")
        env = {**gitrepos.ENV, "HOME": host_home_dir(self.slug)}
        for args in (("add", "late.txt"), ("commit", "-m", "add late data")):
            run = subprocess.run(["/usr/bin/python3", gitview.__file__, "-C", str(wt), *args],
                                 env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
        admin = Path((wt / ".git").read_text().strip()[8:])
        self.assertFalse((admin / "tide-index-tip").exists())
        status = Git().run(str(wt), "status", "--porcelain")
        self.assertTrue(status.ok, status.err)
        self.assertEqual(status.out, "")

    def test_raw_git_detached_private_head_is_reported_and_its_commit_is_preserved(self):
        self.newrunner.ensure_devices(self.chosen)
        from tide_daemon.box import gitview, refguard
        from tide_daemon.worktrees.git import Git
        admin = Path((Path(self.worktree) / ".git").read_text().strip()[8:])
        boxhome = host_home_dir(self.slug)
        refguard.begin(self.repo, "K-38", "detached", str(admin), branch="feat/pt-1",
                       worktree=self.worktree, box_view_home=boxhome)
        env = {**gitrepos.ENV, "HOME": boxhome}
        initial = subprocess.run(["/usr/bin/python3", gitview.__file__, "-C", self.worktree, "status", "--porcelain"],
                                 env=env, capture_output=True, text=True)
        self.assertEqual(initial.returncode, 0, initial.stderr)
        view = Path(boxhome) / gitview.relative_admin(admin)
        rawenv = {**env, "GIT_DIR": str(view), "GIT_COMMON_DIR": self.repo + "/.git", "GIT_WORK_TREE": self.worktree}
        def raw(*args):
            return subprocess.run(["/usr/bin/git", *args], env=rawenv, check=True, capture_output=True, text=True).stdout.strip()
        raw("checkout", "--detach", "HEAD")
        (Path(self.worktree) / "detached.txt").write_text("zachowany commit")
        raw("add", "detached.txt")
        raw("commit", "-m", "add detached data")
        oid = raw("rev-parse", "HEAD")
        report = refguard.finish(self.repo, "K-38", "detached")
        self.assertIn("box-view/HEAD", report["files"])
        self.assertEqual(report["boxViewHead"], oid)
        self.assertEqual((view / "HEAD").read_text(), "ref: refs/heads/feat/pt-1\n")
        self.assertEqual(Git().run(self.repo, "show", oid + ":detached.txt").out, "zachowany commit")
