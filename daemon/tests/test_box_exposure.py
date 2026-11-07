"""PT-263: sztuczne wejścia i prywatny magazyn; bez montowania i wspólnego .git użytkownika."""
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from tide_daemon.box import exposure as ex, k3, packs, profile as bp
from tide_daemon.box.runner import BoxRunner
from tide_daemon.box.settings import BoxSettings
from tide_daemon.cardflow import boxstep, envconsent, projectconsent
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold
from tide_daemon.tasks import agentcmd, inputs
from tide_daemon.tasks.runner import HostRunner


class ExposureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pt263-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        patch = mock.patch.dict(os.environ, {"HOME": str(self.base),
            "XDG_STATE_HOME": str(self.base / "state"), "XDG_DATA_HOME": str(self.base / "data"),
            "TIDE_BOARD_DIR": str(self.base / "board")})
        patch.start()
        self.addCleanup(patch.stop)
        self.repo = self.base / "repos" / "plasma-tide"
        self.repo.mkdir(parents=True)
        self.foreign = self.repo.parent / "other-project"
        self.foreign.mkdir()
        self.registry = {ex.PROJECT: [str(self.repo)], "other": [str(self.foreign)]}
        self.original = self.base / "source.jsonl"
        self.original.write_text("sztuczne wejście")
        self.spec = {"name": "logs", "from": str(self.original)}
        self.copy = packs.create_snapshot(ex.PROJECT, "K-38", self.spec)
        self.profile = {"tide-box": 1, "project": ex.PROJECT, "packs": [self.spec]}

    def service(self):
        return ex.ExposureService(ex.PROJECT, [str(self.repo)], self.registry)

    def approve(self, profile=None, ids=None):
        profile = self.profile if profile is None else profile
        ids = [self.copy.id] if ids is None else ids
        svc = self.service()
        candidate = svc.preview(profile, ids)
        return svc.approve(profile, ids, expected=candidate["expected"], accepted=True)

    def step(self, record, ids=None):
        return agentcmd.Step("claude", "/fake/claude", "", str(self.repo) + "-worktrees/pt-263",
            str(self.repo), str(self.repo) + "-worktrees/pt-263.step", "PT-263",
            exposure_inputs=inputs.Selection(ex.PROJECT, record["version"],
                                            (self.copy.id,) if ids is None else ids))


class ManifestTests(ExposureCase):
    def test_reading_and_preview_do_not_create_consent_store(self):
        self.assertIsNone(ex.current(ex.PROJECT))
        self.service().preview(self.profile, [self.copy.id])
        self.assertFalse(Path(ex.root(ex.PROJECT)).exists())

    def test_exact_repository_mounts_and_copy_report_without_scanning_or_copying(self):
        self.original.unlink()  # Po wykonaniu kopii zgoda nie potrzebuje oryginału.
        with mock.patch.object(packs, "inspect", side_effect=AssertionError("nie skanuj")), \
                mock.patch.object(packs, "create_snapshot", side_effect=AssertionError("nie kopiuj")):
            record = self.approve()
        manifest = record["manifest"]
        self.assertEqual(manifest["repositories"], [str(self.repo)])
        mounts = manifest["mounts"]
        self.assertNotIn(str(self.repo.parent), [m["source"] for m in mounts])
        self.assertNotIn(str(self.foreign), [m["source"] for m in mounts])
        copy = manifest["inputs"][0]["snapshot"]
        self.assertEqual(copy, json.loads(json.dumps(self.copy.to_dict())))
        self.assertIn({"source": self.copy.directory, "target": "/packs/logs", "mode": "ro", "kind": "pack"}, mounts)
        self.assertNotIn(str(self.original), [m["source"] for m in mounts])
        self.assertEqual(ex.current(ex.PROJECT), record)
        self.assertEqual(Path(ex.root(ex.PROJECT), "state.json").stat().st_mode & 0o777, 0o400)
        with ex.approved_access(ex.PROJECT, record["version"]) as approved:
            self.assertEqual(approved, manifest)

    def test_parent_repository_cannot_implicitly_include_another_project(self):
        self.registry[ex.PROJECT] = [str(self.repo.parent)]
        with self.assertRaisesRegex(ex.ExposureError, "inny projekt"):
            ex.ExposureService(ex.PROJECT, self.registry[ex.PROJECT], self.registry)
        self.registry[ex.PROJECT] = [str(self.repo)]
        # Repozytorium innego projektu zagnieżdżone w obszarze współdzielonej pracy.
        self.registry["other"] = [str(self.repo) + "-worktrees/foreign"]
        with self.assertRaisesRegex(ex.ExposureError, "inny projekt"):
            self.service().preview(self.profile, [self.copy.id])

    def test_legacy_card_consents_do_not_become_project_rights(self):
        bp.save_card(ex.PROJECT, "K-38", {"tide-box": 1, "packs": [self.spec], "accepted_red": ["pack:logs"]})
        bp.save_card(ex.PROJECT, "K-39", {"tide-box": 1, "network": {"domains": ["github.com"]}, "accepted_red": ["github.com"]})
        self.assertIsNone(ex.current(ex.PROJECT))
        svc = self.service()
        proposal = svc.preview(self.profile, [self.copy.id])
        with self.assertRaisesRegex(ex.ExposureError, "świadoma"):
            svc.approve(self.profile, [self.copy.id], expected=proposal["expected"], accepted=False)
        record = self.approve()
        self.assertNotIn("github.com", json.dumps(record))
        # Inna karta tego samego projektu nie wymaga innej zgody na identyczny zakres.
        proposal = svc.preview(self.profile, [self.copy.id])
        self.assertEqual(svc.approve(self.profile, [self.copy.id], expected=proposal["expected"], accepted=False), record)

    def test_new_snapshot_or_profile_or_revocation_invalidates_preview(self):
        svc = self.service()
        candidate = svc.preview(self.profile, [self.copy.id])
        for profile, ids in (({**self.profile, "verify": ["make test"]}, [self.copy.id]),
                             (self.profile, [packs.create_snapshot(ex.PROJECT, "K-39", self.spec).id])):
            with self.assertRaisesRegex(ex.ExposureError, "nowy podgląd"):
                svc.approve(profile, ids, expected=candidate["expected"], accepted=True)
        record = self.approve()
        pending = svc.preview(self.profile, [self.copy.id])
        ex.revoke(ex.PROJECT, record["version"])
        with self.assertRaises(ex.ExposureError):
            svc.approve(self.profile, [self.copy.id], expected=pending["expected"], accepted=True)

    def test_revocation_survives_restart_and_does_not_erase_other_cards_or_sources(self):
        record = self.approve()
        second = packs.create_snapshot(ex.PROJECT, "K-39", {"name": "other", "from": str(self.original)})
        message = ex.revoke(ex.PROJECT, record["version"])
        self.assertIn("kopie już poznane", message)
        importlib.reload(ex)
        self.assertIsNone(ex.current(ex.PROJECT))
        with self.assertRaises(ex.ExposureError):
            with ex.approved_access(ex.PROJECT, record["version"]):
                self.fail("wycofana zgoda")
        self.assertEqual(packs.load_snapshot(ex.PROJECT, second.id), second)
        self.assertEqual(self.original.read_text(), "sztuczne wejście")
        newer = self.approve()
        self.assertNotEqual(newer["version"], record["version"])

    def test_yaml_edits_never_change_approved_manifest(self):
        record = self.approve()
        bp.save_project(ex.PROJECT, {"tide-box": 1, "setup": ["inne polecenie"]})
        bp.save_card(ex.PROJECT, "K-38", {"tide-box": 1})
        (self.repo / ".tide").mkdir()
        (self.repo / ".tide" / "box.yaml").write_text("tide-box: 1\nsetup: [obce]\n")
        self.assertEqual(ex.current(ex.PROJECT), record)

    def test_missing_revoked_foreign_or_wrong_contract_copy_fails_closed(self):
        for ident in ("a" * 32, packs.create_snapshot("foreign", "K-38", self.spec).id):
            with self.assertRaises(OSError):
                self.service().preview(self.profile, [ident])
        packs.revoke_snapshot(ex.PROJECT, self.copy.id)
        with self.assertRaises(packs.PackError):
            self.service().preview(self.profile, [self.copy.id])
        with self.assertRaisesRegex(ex.ExposureError, "PackSnapshot"):
            self.service().preview(self.profile, [])

    def test_unknown_snapshot_version_is_not_a_source_mount(self):
        metadata = Path(self.copy.directory).parent / "snapshot.json"
        raw = json.loads(metadata.read_text())
        raw["version"] = 999
        metadata.chmod(0o600)
        metadata.write_text(json.dumps(raw))
        with self.assertRaises(packs.PackError):
            self.service().preview(self.profile, [self.copy.id])

    def test_shared_work_cannot_include_originals_or_manifest_store(self):
        original = Path(str(self.repo) + "-worktrees") / "source"
        original.parent.mkdir()
        original.write_text("oryginał")
        spec = {"name": "unsafe", "from": str(original)}
        copy = packs.create_snapshot(ex.PROJECT, "K-38", spec)
        with self.assertRaisesRegex(ex.ExposureError, "oryginały"):
            self.service().preview({"tide-box": 1, "packs": [spec]}, [copy.id])
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(original.parent)}):
            with self.assertRaises((ex.ExposureError, OSError)):
                self.service().preview({"tide-box": 1}, [])

    def test_tampered_state_audit_symlink_or_hardlink_is_refused(self):
        record = self.approve()
        audit = Path(ex.root(ex.PROJECT), f"manifest-{record['generation']}.json")
        previous = audit.read_bytes()
        audit.chmod(0o600)
        audit.write_bytes(previous.replace(b'"mode":"ro"', b'"mode":"rw"'))
        audit.chmod(0o400)
        with self.assertRaises(ex.ExposureError):
            ex.current(ex.PROJECT)
        audit.unlink()
        audit.symlink_to(self.original)
        with self.assertRaises(OSError):
            ex.current(ex.PROJECT)
        audit.unlink()
        audit.write_bytes(previous)
        audit.chmod(0o400)
        os.link(audit, audit.with_suffix(".alias"))
        with self.assertRaises(ex.ExposureError):
            ex.current(ex.PROJECT)

    def test_mount_transaction_serializes_with_revoke(self):
        record = self.approve()
        started, finished = threading.Event(), threading.Event()
        errors = []
        def revoke():
            started.set()
            try:
                ex.revoke(ex.PROJECT, record["version"])
            except Exception as exc:
                errors.append(exc)
            finished.set()
        with ex.approved_access(ex.PROJECT, record["version"]):
            worker = threading.Thread(target=revoke)
            worker.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(finished.wait(.05))
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])


class CredentialTests(ExposureCase):
    def test_unknown_or_truthy_without_p3_is_false_before_consent_and_after_restart(self):
        self.assertFalse(k3.k3_active("new-program"))
        with mock.patch.dict(k3.K3_PROGRAMS, {"new-program": True, "codex": "true"}):
            self.assertFalse(k3.k3_active("new-program"))
            self.assertFalse(k3.k3_active("codex"))
            self.assertEqual(k3.credential_mode("codex")["mode"], "K1")
            self.assertIn("denyRead nie chroni", envconsent.docker_note())
        importlib.reload(k3)
        self.assertFalse(k3.k3_active("new-program"))
        self.assertTrue(k3.k3_active("codex"))  # Konkretny odebrany wariant z P-3.

    def test_losing_confirmation_cannot_downgrade_an_approved_k3(self):
        record = self.approve()
        with mock.patch.dict(k3.K3_CONFIRMATIONS, {"codex": ""}):
            self.assertFalse(k3.k3_active("codex"))
            with self.assertRaisesRegex(ex.ExposureError, "K3/K1"):
                with ex.approved_access(ex.PROJECT, record["version"]):
                    self.fail("cichy K1")
            with self.assertRaises(ValueError):
                k3.GrantRegistry().restore("step", "10.1.1.1", "chatgpt", "dummy", 60)


class RunnerInputTests(ExposureCase):
    def test_both_runners_consume_the_same_copies_host_gets_only_this_step_selection(self):
        other_spec = {"name": "other", "from": str(self.original)}
        other = packs.create_snapshot(ex.PROJECT, "K-39", other_spec)
        record = self.approve({**self.profile, "packs": [self.spec, other_spec]}, [self.copy.id, other.id])
        step = self.step(record)
        with inputs.selected(step, "host") as (host, host_inputs):
            self.assertEqual(host.read_dirs, (self.copy.directory,))
            self.assertNotIn(other.directory, host.read_dirs)
            self.assertNotIn(str(self.repo) + "-worktrees", host.read_dirs)
        with inputs.selected(step, "box") as (box, box_inputs):
            self.assertEqual(box.read_dirs, ("/packs/logs",))
        self.assertEqual(host_inputs, box_inputs)
        bg = mock.Mock(env={"test": "value"})
        with mock.patch.object(agentcmd, "background", return_value=bg) as background:
            HostRunner().start(step)
            self.assertEqual(background.call_args.args[0].read_dirs, (self.copy.directory,))
            HostRunner().env(step)
        runner = BoxRunner(BoxSettings(), ex.PROJECT, proxy_base="http://10.1.1.1:18080")
        # Weryfikacja wspólnego opisu przed budową argv, bez jakichkolwiek urządzeń.
        with mock.patch.object(runner, "box_step", side_effect=lambda value: value) as convert, \
                mock.patch.object(runner, "_claude_settings"), \
                mock.patch.object(agentcmd, "background", return_value=SimpleNamespace(
                    env={"PATH": "/usr/bin:/bin"}, cwd=step.worktree, step_dir=step.step_dir)):
            runner._background(step)
            self.assertEqual(convert.call_args.args[0].read_dirs, ("/packs/logs",))

    def test_foreign_missing_revoked_input_never_falls_back_to_original(self):
        record = self.approve()
        for ids in (("a" * 32,),):
            with self.assertRaises(agentcmd.StepError):
                HostRunner().env(self.step(record, ids))
        packs.revoke_snapshot(ex.PROJECT, self.copy.id)
        with self.assertRaises(agentcmd.StepError):
            HostRunner().start(self.step(record))

    def test_box_direct_entry_waits_before_writes_or_devices(self):
        record = self.approve()
        runner = BoxRunner(BoxSettings(), ex.PROJECT)
        with mock.patch.object(runner, "_background") as bg, mock.patch.object(runner.incus, "device_add") as device:
            with self.assertRaisesRegex(agentcmd.StepError, "PT-264"):
                runner.start(self.step(record))
            bg.assert_not_called()
            device.assert_not_called()


class ServiceTests(ExposureCase):
    def setUp(self):
        super().setUp()
        self.project = SimpleNamespace(slug=ex.PROJECT, repo=str(self.repo))
        self.journal = Journal(str(self.base / "board"))
        self.journal.append("K-38", "card.started", entry="now")
        self.journal.append("K-39", "card.started", entry="now")
        self.engine = SimpleNamespace(
            _project=lambda card: (SimpleNamespace(id=card), self.project),
            store=SimpleNamespace(load=lambda: SimpleNamespace(projects={
                ex.PROJECT: self.project, "other": SimpleNamespace(repo=str(self.foreign))})),
            journal=self.journal, approve_plan=mock.Mock(), box_launcher=mock.Mock())
        bp.save_project(ex.PROJECT, {"tide-box": 1, "project": ex.PROJECT})

    def request(self, action="preview", **extras):
        return bp.dump({**self.profile, projectconsent.REQUEST: {"version": 1, "snapshotIds": [self.copy.id]},
                        projectconsent.ACTION: action, **extras})

    def call(self, text, card="K-38"):
        return envconsent.approve(self.engine, card, text, False)

    def test_direct_service_ignores_claimed_manifest_and_requires_exact_new_consent(self):
        legacy = self.call(bp.dump({**self.profile, "accepted_red": ["pack:logs"]}))
        self.assertFalse(legacy["ok"])
        self.assertIn("dawna zgoda", legacy["message"])
        self.assertIsNone(ex.current(ex.PROJECT))
        preview = self.call(self.request())
        self.assertTrue(preview["ok"], preview)
        self.assertFalse(self.call(self.request("approve"))["ok"])
        ack = {projectconsent.EXPECTED: preview["exposure"]["expected"], projectconsent.ACCEPTED: True}
        forged = self.call(self.request("approve", manifest={"mounts": ["/"]}, **ack))
        self.assertFalse(forged["ok"])
        before = Path(bp.project_path(ex.PROJECT)).read_bytes()
        with mock.patch.object(packs, "inspect", side_effect=AssertionError("skan")), \
                mock.patch.object(packs, "materialize", side_effect=AssertionError("kopia")):
            approved = self.call(self.request("approve", **ack))
        self.assertTrue(approved["ok"], approved)
        for card in ("K-38", "K-39"):
            model = envconsent.model(self.engine, card, fold(self.journal.read(card)))
            self.assertEqual(model["approvedVersion"], approved["consentVersion"])
            self.assertTrue(model["exposure"]["approved"])
            self.assertIn(self.copy.directory, model["exposure"]["details"])
        with self.assertRaisesRegex(boxstep.BoxWaiting, "PT-264"):
            boxstep.place(self.engine, "K-39", ex.PROJECT, "ticket", "", ("ticket",))
        # Ta sama zgoda projektu działa dla kolejnej karty bez kolejnego zaznaczenia.
        preview = self.call(self.request(), "K-39")
        reused = self.call(self.request("approve", **{projectconsent.EXPECTED: preview["exposure"]["expected"]}), "K-39")
        self.assertTrue(reused["ok"], reused)
        self.assertEqual(reused["consentVersion"], approved["consentVersion"])
        self.assertEqual(before, Path(bp.project_path(ex.PROJECT)).read_bytes())
        self.engine.box_launcher.assert_not_called()
        # Zakończenie jednej karty nie resetuje zgody ani kopii dla pozostałych.
        self.journal.append("K-38", "card.finished")
        self.assertEqual(ex.current(ex.PROJECT)["version"], approved["consentVersion"])
        self.assertFalse(self.call(self.request("approve", **ack))["ok"])
        revoked = self.call(self.request("revoke", **{projectconsent.EXPECTED: approved["consentVersion"]}))
        self.assertTrue(revoked["ok"], revoked)
        self.assertIsNone(ex.current(ex.PROJECT))

    def test_full_profile_update_roundtrip_with_stage2_data_is_refused_without_loss(self):
        for scope in ("project", "card"):
            for extra in ({"tests": {"changed": "make test-changed", "full": "make test"}},
                          {"outside": {"commands": ["make test"], "history": ["stara próba"]},
                           "readDirs": ["/foreign"]}):
                path = Path(bp.project_path(ex.PROJECT) if scope == "project" else bp.card_path(ex.PROJECT, "K-38"))
                path.parent.mkdir(parents=True, exist_ok=True)
                text = bp.dump({"tide-box": 1, **extra})
                path.write_text(text)
                preview = self.call(self.request())
                self.assertFalse(preview["ok"], preview)
                self.assertIn("PT-275", preview["message"])
                self.assertEqual(path.read_text(), text)
                self.assertIsNone(ex.current(ex.PROJECT))
                path.unlink()
                bp.save_project(ex.PROJECT, {"tide-box": 1})

    def test_consent_model_explains_project_scope_and_k1_and_does_not_scan_packs(self):
        bp.save_card(ex.PROJECT, "K-38", {"tide-box": 1})
        with mock.patch.dict(k3.K3_CONFIRMATIONS, {"codex": ""}), \
                mock.patch.object(packs, "describe", side_effect=AssertionError("skan")):
            model = envconsent.model(self.engine, "K-38", fold(self.journal.read("K-38")))
        self.assertTrue(model["projectExposure"])
        self.assertIn("Wszystkie karty i kroki", model["exposure"]["scope"])
        self.assertIn("root przez wspólny Docker", model["exposure"]["scope"])
        self.assertIn("denyRead nie chroni", model["exposure"]["details"])
        self.assertIn("per-card", model["exposure"]["scope"])
        self.assertFalse(model["approved"])
