"""Zainstalowany proces CLI → prywatny D-Bus → rzeczywisty daemon → git Tablicy."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from . import DAEMON_DIR
from .fakebin import make_env
from .privatebus import PrivateBus, SERVICE, requires_dbus
from .test_board_dbus import git_config, git_out

REPO = Path(DAEMON_DIR).parent
IFACE = "org.tide.Daemon.Issues"


@requires_dbus
class IssuesDbusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-issues-cli-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = {**make_env(self.tmp), **git_config(self.tmp), "PYTHONPATH": ""}
        self.bus = PrivateBus("issues-" + str(id(self)))
        self.addCleanup(self.bus.stop)
        self.env["DBUS_SESSION_BUS_ADDRESS"] = self.bus.address
        subprocess.run(["make", "-s", "install-cli"], cwd=REPO, env=self.env,
                       check=True, capture_output=True, text=True)
        self.cli = str(Path(self.env["HOME"]) / ".local/bin/tide")
        self.board = str(Path(self.tmp) / "board")
        self.report = str(Path(self.tmp) / "report.md")
        Path(self.report).write_text("# Rozmowa\n\nWnioski.\n---\nDalsze dowody.\n", encoding="utf-8")

    def start(self, *extra):
        self.proc = self.bus.spawn_daemon("--board-dir", self.board, "--workspace", self.tmp,
                    "--claude-dir", str(Path(self.tmp) / "claude"),
                    "--codex-dir", str(Path(self.tmp) / "codex"),
                    "--no-processes", "--no-notifications", "--no-tab-marks", "--no-idle",
                    "--no-report", "--no-limits", "--no-tasks", "--no-coordination", "--no-youtrack",
                    "--no-models", "--no-docker", "--no-krunner", "--no-monitors",
                    "--no-activity-look", "--no-stream-shortcuts", *extra, extra_env=self.env)

    def run_cli(self, *args, code=0):
        process = subprocess.run([self.cli, *args, "--json"], cwd=self.tmp, env=self.env,
                                 capture_output=True, text=True, timeout=40)
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        return json.loads(process.stdout)

    def call(self, method, *args):
        reply = self.bus.call(SERVICE, "/Issues", IFACE, method, *args)
        self.assertEqual(len(reply.arguments()), 1, reply.errorMessage())
        return json.loads(reply.arguments()[0])

    def board_call(self, method, *args):
        reply = self.bus.call(SERVICE, "/Board", "org.tide.Daemon.Board", method, *args)
        return json.loads(reply.arguments()[0])

    def test_installed_cli_roundtrip_restart_parallel_and_old_board(self):
        self.start()
        info = self.run_cli("info")
        self.assertEqual(info["apiVersion"], 1)
        self.assertIn("issue.create", info["capabilities"])
        self.assertEqual(self.run_cli("issue", "list")["issues"], [])
        self.assertFalse(Path(self.board).exists())
        # Istniejąca karta nadal jest kartą, a sprawa nie zużywa jej licznika.
        self.assertEqual(self.board_call("CreateCard", '{"title":"Stara karta"}')["id"], "K-1")
        created = self.run_cli("issue", "create", "--report", self.report, "--title", "Szkic",
                               "--request-id", "trwały-klucz")
        issue = created["issue"]
        self.assertEqual(issue["id"], "S-1")
        self.assertEqual(issue["version"], 1)
        self.assertEqual(self.run_cli("issue", "show", "S-1")["issue"], issue)
        self.assertEqual(issue["report"], Path(self.report).read_text())
        self.assertNotIn("report", self.run_cli("issue", "list")["issues"][0])
        self.assertEqual(self.board_call("Snapshot")["nextId"], "K-2")
        self.assertEqual(git_out(self.board, "branch", "--format=%(refname:short)").strip(), "master")
        self.assertEqual(git_out(self.board, "status", "--porcelain"), "")
        self.proc.terminate()
        self.proc.wait(10)
        deadline = time.monotonic() + 5
        while self.bus.bus.interface().isServiceRegistered(SERVICE).value() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.start()
        replay = self.run_cli("issue", "create", "--report", self.report, "--title", "Szkic",
                              "--request-id", "trwały-klucz")
        self.assertEqual(replay["issue"], issue)
        self.assertTrue(replay["existing"])
        self.assertEqual(self.run_cli("issue", "create", "--report", self.report, "--title", "Inny",
                                     "--request-id", "trwały-klucz", code=4)["error"], "conflict")
        def create(index):
            return self.run_cli("issue", "create", "--report", self.report, "--title", f"Szkic {index}")
        with ThreadPoolExecutor(max_workers=5) as pool:
            ids = [result["issue"]["id"] for result in pool.map(create, range(10))]
        self.assertEqual(len(set(ids)), 10)
        self.assertNotIn("S-1", ids)
        self.assertEqual(len(self.run_cli("issue", "list")["issues"]), 11)
        self.assertEqual(self.board_call("Snapshot")["nextId"], "K-2")

    def test_public_boundary_rejects_fields_frontmatter_utf8_and_project(self):
        self.start()
        good = {"title": "Test", "report": "Raport"}
        invalid = [{**good, field: "obce"} for field in
                   ("id", "state", "version", "created", "updated", "card", "expectedVersion", "attachments", "path")]
        invalid += [{**good, "title": ""}, {**good, "title": None}, {**good, "report": "\udcff"},
                    {**good, "report": "ą" * 32769}, {**good, "project": "nieznany"},
                    {**good, "report": "---\nstate: ready\n---\n"}]
        for data in invalid:
            result = self.call("Create", json.dumps(data))
            self.assertEqual(result["error"], "bad-request", result)
        self.assertFalse(Path(self.board).exists())
        self.board_call("UpsertProject", '{"name":"Dom"}')
        assigned = self.call("Create", json.dumps({**good, "project": "dom"}))
        self.assertEqual(assigned["issue"]["project"], "dom")
        Path(self.board, "projects/dom.md").unlink()
        self.assertEqual(self.call("Create", json.dumps({**good, "project": "dom"}))["error"], "bad-request")
        self.assertEqual(self.call("Create", json.dumps({**good, "project": None}))["issue"]["id"], "S-2")
        self.assertEqual(self.run_cli("issue", "show", "S-99", code=5)["error"], "not-found")
        Path(self.board, "issues/S-1/report.md").write_bytes(b"\xff")
        listed = self.run_cli("issue", "list")
        self.assertEqual([i["id"] for i in listed["issues"]], ["S-2"])
        self.assertEqual(listed["errors"][0]["error"], "broken-file")
        self.assertEqual(self.run_cli("issue", "show", "S-1", code=7)["error"], "broken-file")
        secret = Path(self.tmp) / "secret"
        secret.write_text("sekret")
        Path(self.board, "issues/S-2/report.md").unlink()
        Path(self.board, "issues/S-2/report.md").symlink_to(secret)
        self.assertNotIn("sekret", json.dumps(self.run_cli("issue", "show", "S-2", code=7)))

    def test_cli_bad_input_busy_and_missing_service_do_not_write(self):
        self.assertEqual(self.run_cli("issue", "create", "--report", self.report, "--title", "x", code=3)["error"],
                         "unavailable")
        self.assertFalse(Path(self.board).exists())
        self.start()
        for raw in (b"\xff", b"---\nid: S-9\n---\nx", b"x" * 65537):
            Path(self.report).write_bytes(raw)
            self.assertEqual(self.run_cli("issue", "create", "--report", self.report, "--title", "x", code=2)["error"],
                             "bad-request")
        self.assertFalse(Path(self.board).exists())
        self.run_cli("issue", "create", "--report", self.report, code=2)
        Path(self.report).write_text("Treść")
        self.run_cli("issue", "create", "--report", self.report, "--title", "x")
        Path(self.board, ".git/REVERT_HEAD").write_text("konflikt")
        self.assertEqual(self.run_cli("issue", "create", "--report", self.report, "--title", "x", code=6)["error"],
                         "git-busy")
        self.assertEqual(len(self.run_cli("issue", "list")["issues"]), 1)

    def test_incompatible_api_is_checked_before_report_or_create(self):
        proof = str(Path(self.tmp) / "called-create")
        self.bus.spawn(["/usr/bin/python3", str(REPO / "daemon/tests/fake_issues_api.py"), proof], SERVICE,
                       extra_env=self.env)
        reply = self.run_cli("issue", "create", "--report", "/nie-ma-pliku", "--title", "x", code=3)
        self.assertEqual(reply["error"], "api-incompatible")
        self.assertFalse(Path(proof).exists())
        self.assertFalse(Path(self.board).exists())

    def test_disabled_board_and_isolated_uninstall(self):
        self.start("--no-board")
        self.assertEqual(self.run_cli("info")["capabilities"], [])
        self.assertEqual(self.call("Create", '{"title":"x","report":"x"}')["error"], "unavailable")
        self.run_cli("issue", "create", "--report", self.report, "--title", "x", code=3)
        self.assertFalse(Path(self.board).exists())
        self.assertTrue(os.access(self.cli, os.X_OK))
        subprocess.run(["make", "-s", "uninstall-cli"], cwd=REPO, env=self.env, check=True, capture_output=True)
        self.assertFalse(Path(self.cli).exists())
