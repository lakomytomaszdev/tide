"""PT-191: cała czerwona lista z atrapami; żadnych prób dostępu do usług i plików hosta."""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
FAKE = r'''#!/usr/bin/python3
import json, os, sys
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
with open(os.environ["CHECK_LOG"], "a") as fh:
    fh.write(json.dumps([name, *args]) + "\n")
mode = os.environ.get("CHECK_MODE", "deny")
if name == "id":
    print("agent"); sys.exit(0)
if name == "readlink":
    print("pid:[box]"); sys.exit(0)
if name == "git" and args[:3] == ["remote", "get-url", "origin"]:
    print("https://github.com/test/test.git"); sys.exit(0)
if name == "sh":
    if args[-1:] == ["tide-write"]:
        sys.exit(1)
    os.execv("/bin/sh", ["/bin/sh", *args])
if name == "sudo":
    if args == ["-n", "true"]:
        sys.exit(0 if mode.startswith("spoof") else 1)
    os.execvp(args[1], args[1:])
if name == "ip":
    sys.exit(1 if mode == "spoof-no-root" else 0)
if name == "curl":
    if "--interface" in args:
        if mode == "spoof-tls-failed":
            sys.exit(60)
        sys.exit(22 if mode == "spoof-reaches-proxy" else 7)
    sys.exit(0 if mode == "leak" else 7)
sys.exit(1)
'''


class RedListTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        for name in ("id", "readlink", "ls", "cat", "git", "curl", "docker", "nslookup", "dbus-send", "nc", "sudo", "ip", "sh"):
            path = self.bin / name
            path.write_text(FAKE)
            path.chmod(0o755)
        # PATH celowo nie zawiera gh, także gdy host je ma; timeout jest prawdziwy.
        (self.bin / "timeout").symlink_to("/usr/bin/timeout")
        self.log = self.base / "calls.jsonl"
        self.env = {"PATH": str(self.bin), "HOME": str(self.base), "TIDE_BOX_NAME": "proj",
                    "TIDE_HOST_PID_NS": "pid:[host]", "TIDE_BOX_BRIDGE": "10.173.184.1",
                    "TIDE_HOST_HOME": "/home/user", "TIDE_HOST_REPO": "/home/user/DevWorkspace/plasma-tide",
                    "HTTPS_PROXY": "http://tide:fake-key@10.173.184.1:18080", "CHECK_LOG": str(self.log)}

    def run_check(self, mode="deny", args=()):
        return subprocess.run(["/bin/bash", str(ROOT / "scripts/box/check.sh"), *args], env={**self.env, "CHECK_MODE": mode},
                              capture_output=True, text=True, timeout=20)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_denials_and_not_applicable_do_not_fail_and_every_point_is_present(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        for point in ("SSH hosta", "git-credentials", "główny worktree", "example.com", "github.com", "DNS",
                      "Docker hosta", "D-Bus", "git push origin HEAD", "gh auth status", "/proc/1/environ",
                      "zapis poza", "5432", "podszycie IP", ".claude/.credentials.json"):
            self.assertIn(point, result.stdout)
        calls = self.calls()
        for call in (["ls", "/home/user/.ssh"], ["cat", "/home/user/.git-credentials"],
                     ["ls", "/home/user/DevWorkspace/plasma-tide/apps"],
                     ["docker", "-H", "unix:///host/docker.sock", "ps"], ["git", "push", "origin", "HEAD"],
                     ["cat", "/proc/1/environ"]):
            self.assertIn(call, calls)
        self.assertIn("| nie dotyczy |", result.stdout)
        self.assertNotIn("| przeszło |", result.stdout)

    def test_any_success_returns_one(self):
        result = self.run_check("leak")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("| przeszło |", result.stdout)

    def test_spoof_sends_curl_after_adding_ip_and_removes_ip(self):
        result = self.run_check("spoof-denied", ["--spoof-ip", "10.173.184.250"])
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        add = calls.index(["ip", "addr", "add", "10.173.184.250/24", "dev", "eth0"])
        curl = next(i for i, c in enumerate(calls) if c[:2] == ["curl", "--interface"])
        delete = calls.index(["ip", "addr", "del", "10.173.184.250/24", "dev", "eth0"])
        self.assertLess(add, curl)
        self.assertLess(curl, delete)
        self.assertIn("http://10.173.184.1:18080", calls[curl])
        self.assertIn("wymagane potwierdzenie braku wpisu proxy", result.stdout)

    def test_spoof_http_refusal_is_failure_of_nic_boundary(self):
        result = self.run_check("spoof-reaches-proxy", ["--spoof-ip", "10.173.184.250"])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("pakiet dotarł do proxy", result.stdout)

    def test_no_root_is_not_applicable_and_does_not_claim_packet_was_sent(self):
        result = self.run_check("spoof-no-root", ["--spoof-ip", "10.173.184.250"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nie dotyczy", result.stdout)
        self.assertFalse(any(c[:2] == ["curl", "--interface"] for c in self.calls()))

    def test_spoof_tls_error_also_means_connection_escaped_the_nic(self):
        result = self.run_check("spoof-tls-failed", ["--spoof-ip", "10.173.184.250"])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("pakiet dotarł do proxy", result.stdout)

    def test_docker_without_k3_is_marked_residual_k1(self):
        self.env["TIDE_BOX_DOCKER"] = "1"
        self.assertIn("ryzyko resztkowe K1", self.run_check().stdout)

    def test_make_target_uses_run(self):
        text = (ROOT / "mk/box.mk").read_text()
        self.assertIn("box-check:", text)
        self.assertIn('scripts/box/check-project.py "$(BOX)"', text)


    def test_check_runs_in_foreign_project_without_copying_tide_files(self):
        spec = importlib.util.spec_from_file_location("run_check", ROOT / "scripts/box/run-check.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        project = self.base / "foreign-project"
        project.mkdir()
        captured = []

        def run(argv):
            captured.append(argv)
            self.assertEqual(argv[:5], ["run", "proj", "--worktree", str(project), "--"])
            # Wykonujemy rzeczywisty skrypt poza repozytorium Tide, z atrapami czerwonej listy.
            result = subprocess.run(["/bin/bash", *argv[6:]], cwd=project, env=self.env,
                                    capture_output=True, text=True, timeout=20)
            self.assertIn("podszycie IP + curl --interface", result.stdout)
            self.assertIn("brak roota/narzędzi/obrazu", result.stdout)
            return result.returncode

        with mock.patch.object(module, "project_repo", return_value=str(project)), \
                mock.patch.object(module.cli, "main", side_effect=run):
            self.assertEqual(module.main(["proj", "--spoof-ip", "10.173.184.250"]), 0)
        self.assertEqual(captured[0][-2:], ["--spoof-ip", "10.173.184.250"])
        self.assertEqual(list(project.iterdir()), [])

    def test_check_preserves_failure_from_cli(self):
        spec = importlib.util.spec_from_file_location("run_check", ROOT / "scripts/box/run-check.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.object(module, "project_repo", return_value="/project"), \
                mock.patch.object(module.cli, "main", return_value=37):
            self.assertEqual(module.main(["proj"]), 37)

class ProjectCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("box_check_project", ROOT / "scripts/box/check-project.py")
        cls.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.helper)

    def test_project_repo_selected_and_script_delivered_as_one_argument(self):
        with patch.object(self.helper, "project_repo", return_value="/projects/legalapp") as repo, \
                patch.object(self.helper.cli, "main", return_value=3) as run:
            code = self.helper.main(["legalapp", "--", "--host-ip", "10.0.0.1", "--interface", "eth 0"])
        self.assertEqual(code, 3)
        repo.assert_called_once_with("legalapp")
        run.assert_called_once_with(["run", "legalapp", "--worktree", "/projects/legalapp", "--",
                                     "bash", "-c", (ROOT / "scripts/box/check.sh").read_text(),
                                     "tide-box-check", "--host-ip", "10.0.0.1", "--interface", "eth 0"])

    def test_explicit_worktree_is_forwarded_for_runner_validation(self):
        with patch.object(self.helper, "project_repo") as repo, patch.object(self.helper.cli, "main", return_value=0) as run:
            self.assertEqual(self.helper.main(["legalapp", "--worktree", "/projects/legalapp-worktrees/probe"]), 0)
        repo.assert_not_called()
        self.assertEqual(run.call_args.args[0][:5],
                         ["run", "legalapp", "--worktree", "/projects/legalapp-worktrees/probe", "--"])

    def test_missing_board_project_fails_before_run(self):
        with patch.object(self.helper, "project_repo", side_effect=self.helper.BuildError("brak projektu")), \
                patch.object(self.helper.cli, "main") as run, patch("sys.stderr"):
            self.assertEqual(self.helper.main(["missing"]), 1)
        run.assert_not_called()
