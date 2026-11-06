"""Przygotowanie próby PT-191: brakujące środowisko można zbudować bez zastępowania istniejących danych."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tide_daemon.box import profile as bp

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("boundary_trial", ROOT / "scripts/box/boundary-trial.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class PrepareTest(unittest.TestCase):
    def prepare(self, *, exists=False, profile=False, status_code=0, approve_code=0, status_error=""):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "box.yaml"
            if profile:
                path.write_text("existing profile")

            def response(name, argv):
                if name == "prepare-status":
                    return subprocess.CompletedProcess(argv, status_code,
                                                       json.dumps({"exists": exists, "error": status_error}), "")
                return subprocess.CompletedProcess(argv, approve_code if name == "approve-proof-profile" else 0, "", "")

            with mock.patch.object(trial, "run", side_effect=response) as run, \
                    mock.patch.object(trial.bp, "project_path", return_value=str(path)):
                trial.prepare_missing()
            if profile:
                self.assertEqual(path.read_text(), "existing profile")
            return [c.args for c in run.call_args_list]

    def test_existing_box_is_not_rebuilt_and_profile_is_not_changed(self):
        calls = self.prepare(exists=True, profile=True)
        self.assertEqual([c[0] for c in calls], ["prepare-status"])

    def test_missing_box_uses_existing_profile_without_approving_it_again(self):
        calls = self.prepare(profile=True)
        self.assertEqual([c[0] for c in calls], ["prepare-status", "build-missing-box"])
        self.assertEqual(calls[-1][1][-3:], ["box", "build", "plasma-tide"])

    def test_missing_box_and_profile_use_explicit_proof_profile(self):
        calls = self.prepare()
        self.assertEqual([c[0] for c in calls], ["prepare-status", "approve-proof-profile", "build-missing-box"])
        self.assertEqual(calls[1][1][-1], str(ROOT / "docs/agents/pt-191/plasma-tide-proof.box.yaml"))

    def test_status_or_approval_failure_stops_preparation(self):
        self.assertEqual(len(self.prepare(status_code=1)), 1)
        self.assertEqual(len(self.prepare(approve_code=1)), 2)

    def test_unknown_status_is_not_treated_as_a_missing_box(self):
        with self.assertRaisesRegex(ValueError, "nie potwierdzono"):
            self.prepare(status_error="incus unavailable")

    def test_proof_profile_is_valid_python_qt_with_ports_and_probe_tools(self):
        text = (ROOT / "docs/agents/pt-191/plasma-tide-proof.box.yaml").read_text()
        profile = bp.parse(text, "project")
        self.assertEqual(profile["stacks"], ["python-qt"])
        self.assertGreaterEqual(profile["ports"]["pool"][1] - profile["ports"]["pool"][0], 9)
        self.assertEqual(set(profile["apt"]), {"dnsutils", "netcat-openbsd", "iproute2", "dbus"})
        self.assertFalse(bp.red_items(profile))
