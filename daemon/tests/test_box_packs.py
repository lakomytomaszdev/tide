"""Paczki: granica źródeł, filtrowanie, zamrożona kopia i rzeczywiste argv atrapy Incusa."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tide_daemon.box import packs, profile as bp, state as bs
from tide_daemon.cardflow import envconsent
from .test_box_runner import BoxRunnerCase


class PacksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        patch = mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_DATA_HOME": str(self.home / "data")})
        patch.start()
        self.addCleanup(patch.stop)
        self.source = self.home / "sessions"
        self.source.mkdir()

    def spec(self, **extra):
        return {"name": "logs", "from": str(self.source / "*.jsonl"), **extra}

    def write(self, name, content="bez sekretów"):
        path = self.source / name
        path.write_text(content)
        return path

    def test_spec_yaml_roundtrip_and_project_scope(self):
        text = "tide-box: 1\npacks:\n  - name: logs\n    from: ~/.claude/projects/proj/*.jsonl\n    since: 2026-11-03\n"
        card = bp.parse(text, "card")
        self.assertEqual(bp.parse(bp.dump(card), "card"), card)
        self.assertEqual(bp.items(card), [("pack:logs", bp.RED)])
        self.assertNotIn("packs", bp.merge({"tide-box": 1}, card).profile)
        card["accepted_red"] = ["pack:logs"]
        self.assertEqual(bp.merge({"tide-box": 1}, card).profile["packs"], card["packs"])
        with self.assertRaises(bp.ProfileError):
            bp.parse(text, "project")

    def test_schema_rejects_bad_specs(self):
        for entry in ({"name": "../x", "from": "~/x"}, {"name": "logs"},
                      self.spec(since="yesterday"), self.spec(files="a"), self.spec(extra=True)):
            with self.subTest(entry=entry), self.assertRaises(bp.ProfileError):
                bp.validate({"tide-box": 1, "packs": [entry]}, "card")

    def test_credentials_directories_and_whole_claude_are_rejected(self):
        for path in ("~/.ssh", "~/.ssh/*", "~/.gnupg/**", "~/.config", "~/.config/*", "~/.claude",
                     "~/.claude/**", "~/.claude/*", "~/.claude/.credentials.json"):
            with self.subTest(path=path), self.assertRaises(bp.ProfileError):
                bp.validate({"tide-box": 1, "packs": [{"name": "x", "from": path}]}, "card")

    def test_all_secret_patterns_block_without_disclosing_values(self):
        secrets = ["sk-ant-secret", "ghp_secret", "glpat-secret", "-----BEGIN RSA PRIVATE KEY-----",
                   "Authorization: Bearer secret", "password=secret"]
        for idx, secret in enumerate(secrets):
            self.write(f"{idx}.jsonl", secret)
        report, _ = packs.inspect(self.spec())
        self.assertTrue(report["blocked"])
        self.assertEqual(len(report["hits"]), 6)
        self.assertNotIn("Bearer secret", json.dumps(report))
        with self.assertRaises(packs.PackError):
            packs.materialize("proj", "K-38", [self.spec()])
        self.assertFalse(os.path.exists(packs.root("proj", "K-38")))
        safe = self.write("safe.jsonl")
        self.assertFalse(packs.inspect(self.spec(files=[str(safe)]))[0]["blocked"])

    def test_since_and_first_twenty_paths_in_consent(self):
        for n in range(24):
            self.write(f"{n:02}.jsonl", "abc")
        old = self.write("old.jsonl")
        os.utime(old, (0, 0))
        spec = self.spec(since="2026-11-03")
        row = envconsent._profile_items({"packs": [spec]}, {}, "karta")[0]
        self.assertEqual(row["risk"], "red")
        self.assertEqual(row["pack"]["count"], 24)
        self.assertEqual(row["pack"]["size"], 72)
        self.assertEqual(len(row["pack"]["paths"]), 20)
        self.assertIn("filtr, nie gwarancja", row["note"])

    def test_symlink_file_and_parent_are_never_copied(self):
        real = self.write("real.jsonl")
        (self.source / "link.jsonl").symlink_to(real)
        with self.assertRaises(packs.PackError):
            packs.inspect(self.spec())
        alias = self.home / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(packs.PackError):
            packs.inspect({"name": "logs", "from": str(alias / "real.jsonl")})

    def test_copy_is_frozen_and_device_uses_copy_only(self):
        source = self.write("a.jsonl", "pierwsza wersja")
        spec = self.spec()
        packs.materialize("proj", "K-38", [spec])
        source.write_text("inna wersja password=secret")
        device = packs.devices("proj", "K-38", [spec])[0]
        self.assertEqual(device.path, "/packs/logs")
        self.assertTrue(device.readonly)
        copied = Path(device.source) / str(source).lstrip("/")
        self.assertEqual(copied.read_text(), "pierwsza wersja")
        copied.chmod(0o600)
        copied.write_text("podmienione")
        with self.assertRaises(packs.PackError):
            packs.devices("proj", "K-38", [spec])

    def test_symlink_in_prepared_copy_refuses_mount(self):
        source = self.write("a.jsonl")
        spec = self.spec()
        packs.materialize("proj", "K-38", [spec])
        copied = Path(packs.root("proj", "K-38")) / "logs" / str(source).lstrip("/")
        copied.unlink()
        copied.symlink_to(source)
        with self.assertRaises(OSError):
            packs.devices("proj", "K-38", [spec])


class PacksRunnerTest(BoxRunnerCase):
    def runner(self, **kw):
        kw.setdefault("per_card", kw.get("card") == "K-38")
        return super().runner(**kw)

    def prepare_pack(self):
        source = Path(self.tmp) / "session.jsonl"
        source.write_text("dziennik")
        spec = {"name": "logs", "from": str(source)}
        packs.materialize("proj", "K-38", [spec])
        bp.save_card("proj", "K-38", {"tide-box": 1, "packs": [spec], "accepted_red": ["pack:logs"]})
        self.incus.launch("tide-base", bs.card_box_name("proj", "K-38"))
        return spec

    def test_first_step_adds_readonly_device_once_in_correct_box(self):
        self.prepare_pack()
        runner = self.runner(card="K-38")
        runner.ensure_devices(self.step())
        runner.ensure_devices(self.step())
        adds = [argv for argv in self.adds(runner.box) if "tide-pack-k-38-logs" in argv]
        self.assertEqual(len(adds), 1)
        self.assertIn("readonly=true", adds[0])
        self.assertIn("path=/packs/logs", adds[0])
        self.assertIn("source=" + packs.root("proj", "K-38") + "/logs", adds[0])

    def test_changed_device_and_foreign_card_refuse_start(self):
        self.prepare_pack()
        runner = self.runner(card="K-38")
        runner.ensure_devices(self.step())
        foreign = self.runner(card="K-39")
        foreign.box = runner.box
        with self.assertRaisesRegex(Exception, "obcą lub zmienioną"):
            foreign.ensure_devices(self.step())
        self.incus.device_remove(runner.box, "tide-pack-k-38-logs")
        self.incus.device_add(runner.box, "tide-pack-k-38-logs", "disk",
                              {"source": self.tmp, "path": "/packs/logs", "readonly": False})
        with self.assertRaisesRegex(Exception, "obcą lub zmienioną"):
            runner.ensure_devices(self.step())

    def test_pack_cannot_be_hot_added_over_another_running_step(self):
        self.prepare_pack()
        bs.mark_in_use("proj", bs.card_box_name("proj", "K-38"), "other-card-step")
        with self.assertRaisesRegex(Exception, "bez innych biegnących"):
            self.runner(card="K-38").ensure_devices(self.step())
        self.assertNotIn("tide-pack-k-38-logs", self.incus.device_show(bs.card_box_name("proj", "K-38")))

    def test_pack_refused_in_shared_box_even_after_other_step_releases_reservation(self):
        self.prepare_pack()
        bs.mark_in_use("proj", "proj", "other-card-step")
        bs.mark_released("proj", "proj", "other-card-step")
        self.assertEqual(bs.running_steps("proj", "proj"), [])
        with self.assertRaisesRegex(Exception, "osobnego pudełka karty"):
            self.runner(card="K-38", per_card=False).ensure_devices(self.step())
        self.assertNotIn("tide-pack-k-38-logs", self.incus.device_show("proj"))

    def test_approved_packs_are_readable_in_claude_sandbox(self):
        self.prepare_pack()
        bg = self.runner(card="K-38")._background(self.step())
        settings = json.loads(bg.files[os.path.join(self.step_dir, "in", "settings.json")])
        deny = settings["sandbox"]["filesystem"]["denyRead"]
        self.assertNotIn("/packs", deny)
        self.assertIn("~/.claude", deny)

    def test_cleanup_removes_copy_and_device_with_error_step_in_journal(self):
        self.prepare_pack()
        self.runner(card="K-38").ensure_devices(self.step())
        from tide_daemon.cardflow.journal import Journal
        journal = Journal(bp.board_dir())
        journal.append("K-38", "step.finished", step="s1", result="error")
        packs.cleanup("proj", "K-38", self.incus, journal)
        self.assertFalse(os.path.exists(packs.root("proj", "K-38")))
        self.assertNotIn("tide-pack-k-38-logs", self.incus.device_show(bs.card_box_name("proj", "K-38")))
        self.assertEqual(journal.read("K-38")[-1]["type"], "packs.removed")
        packs.cleanup("proj", "K-38", self.incus, journal)
        self.assertEqual(sum(e["type"] == "packs.removed" for e in journal.read("K-38")), 1)
