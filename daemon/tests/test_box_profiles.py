"""PT-176: dostarczony profil, propozycja paczki i rzeczywiste argumenty prób."""
import importlib.util
import io
import shutil
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tide_daemon.box import profile as bp
from tide_daemon.cardflow import plans
from tide_daemon.models.executors import default_catalog

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "docs/box-profiles"
spec = importlib.util.spec_from_file_location("plasma_trial", ROOT / "scripts/box/plasma-tide-host-trial.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class PlasmaTideProfileTest(unittest.TestCase):
    def test_project_profile_valid_without_red_permissions(self):
        profile = bp.parse((PROFILES / "plasma-tide.yaml").read_text())
        self.assertEqual(profile["project"], "plasma-tide")
        self.assertEqual(profile["stacks"], ["python-qt"])
        self.assertTrue(profile["network"]["host-ollama"])
        self.assertEqual(profile["verify"], ["make test"])
        self.assertEqual(bp.red_items(profile), [])
        self.assertFalse(profile.get("docker", {}).get("enabled"))

    def test_card_pack_requires_explicit_consent_before_merge(self):
        project = bp.parse((PROFILES / "plasma-tide.yaml").read_text())
        card = bp.parse((PROFILES / "plasma-tide-acceptance/box.yaml").read_text(), "card")
        self.assertEqual(card["box"], "per-card")
        self.assertEqual(bp.red_items(card), ["pack:session-logs"])
        self.assertEqual(bp.merge(project, card).profile.get("packs", []), [])
        card["accepted_red"] = ["pack:session-logs"]
        merged = bp.merge(project, card)
        self.assertEqual(merged.dropped_red, [])
        self.assertEqual(merged.profile["packs"][0]["name"], "session-logs")

    def test_acceptance_proposal_import_requires_consent_and_preserves_pack(self):
        # Kopia symuluje przygotowanie przez użytkownika; wzorzec pozostaje bez zgody.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "proposal"
            shutil.copytree(PROFILES / "plasma-tide-acceptance", folder)

            def read():
                return plans.read(str(folder / "plan.md"), "K-999", default_catalog(),
                                  allowed_roots=(tmp,), imported=True)

            pending = read()
            self.assertEqual(len(pending.errors), 1, pending.errors)
            self.assertIn("pack:session-logs", pending.errors[0])
            self.assertIn("accepted_red", pending.errors[0])
            with (folder / "box.yaml").open("a") as profile:
                profile.write("\naccepted_red: [pack:session-logs]\n")
            approved = read()
            self.assertEqual(approved.errors, [])
            self.assertEqual(approved.rows[0]["executor"], "Sonnet")
            self.assertEqual(approved.rows[0]["reviewer"], "Opus")
            self.assertEqual(approved.box["packs"][0]["name"], "session-logs")
            self.assertEqual(approved.box["box"], "per-card")

    def test_long_argument_crosses_box_cli_without_shell_rejoining(self):
        result = subprocess.CompletedProcess([], 0, "PT-176-find-ok\n", "")
        with mock.patch.object(trial.subprocess, "run", return_value=result) as run, \
                mock.patch.object(trial.sys, "argv", ["trial", "long-command"]), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(trial.main(), 0)
        argv = run.call_args.args[0]
        self.assertIn("box", argv)
        self.assertEqual(argv[-3:-1], ["sh", "-c"])
        self.assertGreater(len(argv[-1]), 300)
        self.assertIn('-exec cat {} \\;', argv[-1])

    def test_long_find_command_runs_and_preserves_output(self):
        # Sama składnia i find są sprawdzane lokalnie; to nie dowód granicy Incusa.
        with tempfile.TemporaryDirectory() as tmp:
            command = trial.long_command().replace("/home/agent/pt176-XXXXXX", tmp + "/pt176-XXXXXX")
            result = subprocess.run(["sh", "-c", command], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, "PT-176-find-ok\n"))

    def test_install_only_runs_through_box_and_checks_even_failed_install(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as output:
            path = Path(tmp, "theme")
            path.write_text("host theme")
            with mock.patch.object(trial.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)) as run:
                self.assertEqual(trial.install_trial(path), 7)
            self.assertEqual(run.call_args.args[0], trial.box_command(["make", "install"]))
            self.assertIn('"unchanged": true', output.getvalue())

    def test_host_theme_change_fails_trial(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            path = Path(tmp, "theme")
            path.write_text("before")

            def changed(*args, **kwargs):
                path.write_text("after")
                return subprocess.CompletedProcess([], 0)

            with mock.patch.object(trial.subprocess, "run", side_effect=changed):
                self.assertEqual(trial.install_trial(path), 1)
