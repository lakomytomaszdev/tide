"""Przygotowanie próby PT-191: brakujące środowisko można zbudować bez zastępowania istniejących danych."""
import importlib.util
import json
import io
import os
from contextlib import redirect_stdout
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
    def test_deployed_plan_writer_without_guard_blocks_the_shared_repository_trial(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(trial, "failures", []), \
                redirect_stdout(io.StringIO()):
            path = Path(tmp) / "gitops.py"
            path.write_text('def commit_plan():\n    work_git.run("commit", "-m", "plan")\n')
            self.assertFalse(trial.deployed_plan_guard_ready(path))
            self.assertEqual(trial.failures, ["host-plan-guard"])

    def test_guard_elsewhere_does_not_cover_the_plan_commit(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(trial, "failures", []), \
                redirect_stdout(io.StringIO()):
            path = Path(tmp) / "gitops.py"
            path.write_text('def commit_plan():\n'
                            '    with refguard.host_operation(repo, ("update-ref", branch)):\n        pass\n'
                            '    work_git.run("commit", "-m", "plan")\n')
            self.assertFalse(trial.deployed_plan_guard_ready(path))

    def test_card_branch_plan_writer_has_the_required_guard(self):
        with mock.patch.object(trial, "failures", []), redirect_stdout(io.StringIO()):
            self.assertTrue(trial.deployed_plan_guard_ready(ROOT / "daemon/tide_daemon/cardflow/gitops.py"))
            self.assertEqual(trial.failures, [])

    def test_main_is_held_before_preparation_logs_or_processes(self):
        replies = [subprocess.CompletedProcess([], 0, "", ""),
                   subprocess.CompletedProcess([], 0, '{"usable":true}', "")]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(trial, "ROOT", Path(tmp)), \
                mock.patch.object(trial.Path, "home", return_value=Path(tmp)), \
                mock.patch.object(trial, "OUT"), mock.patch.object(trial, "failures", []), \
                mock.patch.object(trial, "run", side_effect=replies), \
                mock.patch.object(trial, "cli_proof") as proof, redirect_stdout(io.StringIO()):
            self.assertEqual(trial.main(["--prepare-missing", "--enable-snapshots", "--retry-failed-build"]), 3)
            proof.assert_not_called()
            self.assertEqual(trial.failures, [])
            trial.run.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def prepare(self, *, exists=False, profile=False, status_code=0, approve_code=0, status_error="",
                state=None, retry_failed=False):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "box.yaml"
            if profile:
                path.write_text("existing profile")

            def response(name, argv, **_kwargs):
                if name == "prepare-status":
                    return subprocess.CompletedProcess(argv, status_code,
                                                       json.dumps(state or {"exists": exists, "error": status_error}), "")
                return subprocess.CompletedProcess(argv, approve_code if name == "approve-proof-profile" else 0, "", "")

            with mock.patch.object(trial, "run", side_effect=response) as run, \
                    mock.patch.object(trial.bp, "project_path", return_value=str(path)):
                trial.prepare_missing(retry_failed=retry_failed)
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

    def test_retry_only_rebuilds_failed_box_matching_profile_without_steps(self):
        state = {"exists": True, "state": "błąd budowy", "recipeHash": "hash", "profileHash": "hash", "steps": []}
        calls = self.prepare(state=state, retry_failed=True, status_code=3)
        self.assertEqual([c[0] for c in calls], ["prepare-status", "retry-failed-build"])
        for diff in ({"steps": ["active"]}, {"profileHash": "changed"}, {"diff": ["recipe"]}):
            with self.subTest(diff=diff), self.assertRaisesRegex(ValueError, "odmowa ponowienia"):
                self.prepare(state={**state, **diff}, retry_failed=True)
        calls = self.prepare(state={**state, "state": "clean"}, retry_failed=True)
        self.assertEqual([c[0] for c in calls], ["prepare-status"])

    def test_enabling_snapshots_changes_only_documented_project_property(self):
        replies = [subprocess.CompletedProcess([], 0, "deny\n", ""),
                   subprocess.CompletedProcess([], 0, "", ""),
                   subprocess.CompletedProcess([], 0, "allow\n", "")]
        with mock.patch.object(trial.bsettings, "load", return_value=mock.Mock(project="user-1000")), \
                mock.patch.object(trial, "run", side_effect=replies) as run:
            self.assertTrue(trial.enable_snapshots())
        self.assertEqual(run.call_args_list[1].args[1],
                         ["sudo", "-n", "incus", "project", "set", "user-1000", "restricted.snapshots=allow"])

    def test_unknown_snapshot_policy_is_never_changed(self):
        with mock.patch.object(trial.bsettings, "load", return_value=mock.Mock(project="user-1000")), \
                mock.patch.object(trial, "run", return_value=subprocess.CompletedProcess([], 1, "", "denied")) as run:
            self.assertFalse(trial.enable_snapshots())
        self.assertEqual(run.call_count, 1)

    def test_status_three_is_accepted_only_for_explicit_retry(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(trial, "OUT", Path(tmp)), mock.patch.object(trial, "failures", []), \
                mock.patch.object(trial.subprocess, "run", return_value=subprocess.CompletedProcess([], 3, "{}", "")), \
                redirect_stdout(io.StringIO()):
            trial.run("known-status", ["incus"], ok_codes=(0, 3))
            self.assertEqual(trial.failures, [])
            trial.run("unexpected-status", ["incus"])
            self.assertEqual(trial.failures, ["unexpected-status"])

    def test_snapshot_change_never_waits_for_a_password_even_with_askpass_or_terminal(self):
        for terminal in (False, True):
            with self.subTest(terminal=terminal), \
                    mock.patch.dict(os.environ, {"SUDO_ASKPASS": "/usr/bin/ksshaskpass"}, clear=True), \
                    mock.patch.object(trial.sys, "stdin", mock.Mock(isatty=lambda: terminal)), \
                    mock.patch.object(trial.bsettings, "load", return_value=mock.Mock(project="user-1000")), \
                    mock.patch.object(trial, "run", side_effect=[
                        subprocess.CompletedProcess([], 0, "\n", ""),
                        subprocess.CompletedProcess([], 1, "", "sudo: a password is required"),
                    ]) as run, redirect_stdout(io.StringIO()) as output:
                self.assertFalse(trial.enable_snapshots())
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[1].args[1],
                             ["sudo", "-n", "incus", "project", "set", "user-1000", "restricted.snapshots=allow"])
            self.assertIn("sudo incus project set user-1000 restricted.snapshots=allow", output.getvalue())

    def test_allowed_snapshot_policy_needs_no_privileged_command(self):
        with mock.patch.object(trial.bsettings, "load", return_value=mock.Mock(project="user-1000")), \
                mock.patch.object(trial, "run", return_value=subprocess.CompletedProcess([], 0, "allow\n", "")) as run:
            self.assertTrue(trial.enable_snapshots())
        self.assertEqual(run.call_count, 1)

    def test_proof_profile_is_valid_python_qt_with_ports_and_probe_tools(self):
        text = (ROOT / "docs/agents/pt-191/plasma-tide-proof.box.yaml").read_text()
        profile = bp.parse(text, "project")
        self.assertEqual(profile["stacks"], ["python-qt"])
        self.assertGreaterEqual(profile["ports"]["pool"][1] - profile["ports"]["pool"][0], 9)
        self.assertEqual(set(profile["apt"]), {"dnsutils", "netcat-openbsd", "iproute2", "dbus"})
        self.assertFalse(bp.red_items(profile))


# --- Tide public copy: skipped tests (fix-tests.py) ---
import unittest as _pub_unittest


def _pub_skip(reason, targets):
    g = globals()
    for name in targets:
        cls, _, meth = name.partition('.')
        klass = g.get(cls)
        if klass is None:
            continue
        if not meth:
            g[cls] = _pub_unittest.skip(reason)(klass)
        elif hasattr(klass, meth):
            setattr(klass, meth, _pub_unittest.skip(reason)(getattr(klass, meth)))


_pub_skip('public copy: the internal agent work archive (docs/agents/) is not published', [
    'PrepareTest.test_proof_profile_is_valid_python_qt_with_ports_and_probe_tools',
])
# --- end of public copy block ---
