"""PT-189: migracja domu zachowuje stare dane, izolację kart i dzienniki po resecie."""
import io
import json
import os
import subprocess
import tarfile
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tide_daemon.box import home, state as bs
from tide_daemon.box.builder import BuildError
from tide_daemon.box.incus import IncusError
from tide_daemon.worktrees import SessionIndex
from .test_box_builder import BoxCase


class HomeMigrationTest(BoxCase):
    def test_attach_preserves_multiline_idmap_and_full_config_from_json(self):
        builder = self.builder()
        builder.settings = replace(builder.settings, agent_uid=1001, agent_gid=1002)
        builder.incus.init("tide-base", "plasma-tide")
        builder.incus.config_set("plasma-tide", {"raw.idmap": "uid 1000 1001\ngid 1000 1002"})
        run = builder.incus._run
        edits = []

        def cli(argv, **kwargs):
            if argv[1:3] == ["config", "show"]:
                return subprocess.CompletedProcess(argv, 0, 'config:\n  raw.idmap: |-\n'
                                                   '    uid 1000 1001\n    gid 1000 1002\n', "")
            if argv[1:3] == ["config", "edit"]:
                edits.append(json.loads(kwargs["input"]))
            result = run(argv, **kwargs)
            if argv[1] == "list":
                instances = json.loads(result.stdout)
                for instance in instances:
                    instance.update(architecture="x86_64", profiles=["tide"], ephemeral=False,
                                    description="Pudełko", stateful=False)
                result.stdout = json.dumps(instances)
            return result

        builder.incus._run = cli
        builder.ensure_home("plasma-tide")
        self.assertEqual(edits[-1]["config"]["raw.idmap"], f"uid {os.getuid()} 1001\ngid {os.getgid()} 1002")
        self.assertEqual(edits[-1]["devices"]["home"]["source"], home.path("plasma-tide"))
        self.assertEqual(edits[-1]["profiles"], ["tide"])
        self.assertEqual(edits[-1]["description"], "Pudełko")
        self.assertEqual(edits[-1]["architecture"], "x86_64")
        self.assertNotIn("expanded_devices", edits[-1])

    def legacy(self, card=""):
        name = bs.card_box_name("plasma-tide", card) if card else "plasma-tide"
        incus = self.builder().incus
        incus.init("tide-base", name)
        volume = bs.home_volume(name)
        incus.volume_create("tide", volume)
        incus.device_add(name, "home", "disk", {"pool": "tide", "source": volume, "path": bs.HOME_PATH})
        incus.snapshot_create(name, "clean")
        incus.start(name)
        source = os.path.join(self.env["TIDE_FAKE_INCUS_STATE"], "volumes", volume)
        os.makedirs(source)
        return name, source

    def write(self, root, rel, text):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def test_volume_export_migrates_logs_and_reset_preserves_newer_host_data(self):
        name, source = self.legacy()
        wt = "/srv/proj-worktrees/pt-189"
        log = self.write(source, ".claude/projects/-srv-proj-worktrees-pt-189/session.jsonl",
                         json.dumps({"type": "user", "cwd": wt, "sessionId": "session",
                                     "message": {"content": "Eksport"}}) + "\n")
        os.symlink("session.jsonl", log + ".link")
        b = self.builder()
        b.ensure_home("plasma-tide")
        target = home.path("plasma-tide")
        host_log = os.path.join(target, os.path.relpath(log, source))
        self.assertEqual(Path(log).read_text(), Path(host_log).read_text())
        self.assertEqual(os.readlink(host_log + ".link"), "session.jsonl")
        index = SessionIndex("/missing", "/missing")
        self.assertEqual([s.session_id for s in index.refresh(0)], ["session"])
        self.assertEqual(index.link([(wt, wt)])[wt].session_id, "session")
        self.write(target, "after-migration.txt", "Nowa praca")
        b.reset("plasma-tide")  # stara migawka ponownie wskazuje stary wolumen
        self.assertEqual(Path(target, "after-migration.txt").read_text(), "Nowa praca")
        self.assertEqual(b.incus.device_show(name)["home"]["source"], target)
        self.assertEqual(len([a for a in self.incus_calls() if a[:3] == ["storage", "volume", "export"]]), 1)
        self.assertIn("tide/plasma-tide-home", self.fake_state()["volumes"])
        self.assertTrue(os.path.isfile(log))
        receipt = os.path.join(os.path.dirname(target), "home-migration.json")
        with open(receipt) as fh:
            archive = os.path.join(json.load(fh)["staging"], "volume.tar")
        self.assertTrue(os.path.isfile(archive))
        b.destroy("plasma-tide")
        self.assertTrue(os.path.isfile(host_log))
        self.assertIn("tide/plasma-tide-home", self.fake_state()["volumes"])

    def test_project_and_card_volume_data_are_kept_separate(self):
        for card in ("", "K-38", "K-39"):
            name, source = self.legacy(card)
            self.write(source, "owner", card or "projekt")
            self.builder().ensure_home("plasma-tide", card)
            target = home.path("plasma-tide", card)
            self.assertEqual(Path(target, "owner").read_text(), card or "projekt")
            self.assertEqual(self.builder().incus.device_show(name)["home"]["source"], target)

    def test_busy_box_and_export_failure_leave_original_device_and_data(self):
        name, source = self.legacy()
        self.write(source, "important", "dane")
        original = self.builder().incus.device_show(name)["home"]
        bs.mark_in_use("plasma-tide", name, "PT-1")
        with self.assertRaises(BuildError):
            self.builder().ensure_home("plasma-tide")
        self.assertEqual(self.builder().incus.instance(name)["status"], "Running")
        bs.mark_released("plasma-tide", name, "PT-1")
        builder = self.builder()
        with mock.patch.object(builder.incus, "volume_export", side_effect=IncusError(["export"], 1, "awaria")):
            with self.assertRaises(IncusError):
                builder.ensure_home("plasma-tide")
        self.assertEqual(self.builder().incus.device_show(name)["home"], original)
        self.assertTrue(os.path.isfile(os.path.join(source, "important")))
        self.assertFalse(os.path.exists(home.path("plasma-tide")))

    def test_existing_host_data_are_never_overwritten_by_old_volume(self):
        name, source = self.legacy()
        target = home.path("plasma-tide")
        self.write(target, "owner", "host")
        self.write(source, "owner", "volume")
        with self.assertRaises(BuildError):
            self.builder().ensure_home("plasma-tide")
        self.assertEqual(Path(target, "owner").read_text(), "host")
        self.assertEqual(Path(source, "owner").read_text(), "volume")
        self.assertEqual(self.builder().incus.device_show(name)["home"]["source"], "plasma-tide-home")

    def test_restart_after_published_copy_reuses_receipt_and_keeps_changes(self):
        name, source = self.legacy()
        self.write(source, "owner", "volume")
        b = self.builder()
        # Awaria po opublikowaniu danych, przed zmianą urządzenia.
        with mock.patch.object(b.incus, "config_set", side_effect=OSError("awaria")):
            with self.assertRaises(OSError):
                b.ensure_home("plasma-tide")
        self.write(home.path("plasma-tide"), "owner", "nowa praca")
        self.builder().ensure_home("plasma-tide")
        self.assertEqual(Path(home.path("plasma-tide"), "owner").read_text(), "nowa praca")

    def test_restart_before_publish_reuses_finished_export(self):
        self.legacy()
        with mock.patch.object(home.os, "rename", side_effect=OSError("awaria")):
            with self.assertRaises(OSError):
                self.builder().ensure_home("plasma-tide")
        self.builder().ensure_home("plasma-tide")
        exports = [a for a in self.incus_calls() if a[:3] == ["storage", "volume", "export"]]
        self.assertEqual(len(exports), 1)
        self.assertTrue(os.path.isdir(home.path("plasma-tide")))

    def test_failed_mount_change_keeps_old_device_and_can_be_retried(self):
        name, _source = self.legacy()
        builder = self.builder()
        original = builder.incus.device_show(name)["home"]
        with mock.patch.object(builder.incus, "device_replace", side_effect=IncusError(["edit"], 1, "awaria")):
            with self.assertRaises(IncusError):
                builder.ensure_home("plasma-tide")
        self.assertEqual(builder.incus.device_show(name)["home"], original)
        builder.ensure_home("plasma-tide")
        self.assertEqual(builder.incus.device_show(name)["home"]["source"], home.path("plasma-tide"))

    def test_corrupt_export_leaves_original_mount(self):
        name, _source = self.legacy()
        builder = self.builder()
        original = builder.incus.device_show(name)["home"]

        def corrupt(_pool, _volume, archive):
            Path(archive).write_text("uszkodzony eksport")

        with mock.patch.object(builder.incus, "volume_export", side_effect=corrupt):
            with self.assertRaisesRegex(BuildError, "archiwum"):
                builder.ensure_home("plasma-tide")
        self.assertEqual(builder.incus.device_show(name)["home"], original)

    def test_symlink_in_host_home_path_is_rejected(self):
        self.legacy()
        target = home.path("plasma-tide")
        os.makedirs(os.path.dirname(target))
        os.symlink(self.tmp, target)
        with self.assertRaises(BuildError):
            self.builder().ensure_home("plasma-tide")

    def test_archive_cannot_write_through_links_or_escape_into_host(self):
        for kind in ("traversal", "link-parent", "hardlink", "special"):
            with self.subTest(kind=kind):
                archive = os.path.join(self.tmp, kind + ".tar")
                dest = os.path.join(self.tmp, kind)
                os.mkdir(dest)
                with tarfile.open(archive, "w") as tar:
                    root = tarfile.TarInfo("backup/volume")
                    root.type = tarfile.DIRTYPE
                    tar.addfile(root)
                    entry = tarfile.TarInfo("backup/volume/p")
                    if kind == "traversal":
                        entry.name = "backup/volume/../../outside"
                    elif kind == "link-parent":
                        entry.type, entry.linkname = tarfile.SYMTYPE, self.tmp
                    elif kind == "hardlink":
                        entry.type, entry.linkname = tarfile.LNKTYPE, "../../outside"
                    else:
                        entry.type = tarfile.FIFOTYPE
                    tar.addfile(entry)
                    if kind == "link-parent":
                        child = tarfile.TarInfo("backup/volume/p/outside")
                        tar.addfile(child, io.BytesIO())
                with self.assertRaises(home.HomeError):
                    home.unpack(archive, dest)
                self.assertFalse(os.path.exists(os.path.join(self.tmp, "outside")))
