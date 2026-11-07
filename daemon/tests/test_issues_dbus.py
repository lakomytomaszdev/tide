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

    def mutate(self, action, version, *args, code=0):
        return self.run_cli("issue", action, "S-1", "--expected-version", str(version), *args, code=code)

    def files(self):
        return {str(p.relative_to(self.board)): p.read_bytes()
                for p in Path(self.board, "issues").rglob("*") if p.is_file()}

    def test_text_lifecycle_metadata_restart_and_independent_counters(self):
        self.start()
        self.board_call("UpsertProject", '{"name":"Dom"}')
        original = self.run_cli("issue", "create", "--report", self.report, "--title", "Szkic")["issue"]
        ledger = Path(self.board, ".git/tide-issues.json").read_bytes()
        updated = self.mutate("update", 1, "--title", "Nowy")["issue"]
        self.assertEqual((updated["title"], updated["report"], updated["project"]),
                         ("Nowy", original["report"], None))
        assigned = self.mutate("update", 2, "--project", "dom")["issue"]
        self.assertEqual((assigned["title"], assigned["project"]), ("Nowy", "dom"))
        cleared = self.mutate("update", 3, "--clear-project")["issue"]
        self.assertIsNone(cleared["project"])
        Path(self.report).write_text("Brak obrazu; świadomie rezygnuję z pliku. Ocena oparta na tekście.\n")
        updated = self.mutate("update", 4, "--title", "Raport tekstowy", "--project", "dom",
                              "--report", self.report)["issue"]
        self.assertEqual(updated["version"], 5)
        self.assertEqual(updated["created"], original["created"])
        self.assertEqual(updated["report"], Path(self.report).read_text())
        self.assertEqual(self.call("Show", "S-1")["issue"], updated)
        self.assertEqual(self.mutate("submit", 5)["issue"]["state"], "ready")
        self.assertEqual(self.mutate("update", 6, "--title", "Poprawiony")["issue"]["state"], "draft")
        ready = self.mutate("submit", 7)["issue"]
        rejected = self.mutate("reject", 8)["issue"]
        self.assertEqual((rejected["state"], rejected["version"]), ("rejected", 9))
        for key in ("report", "project", "title", "attachments", "created"):
            self.assertEqual(rejected[key], ready[key])
        before = self.files()
        for action, args in (("update", ("--title", "Zabroniony")), ("submit", ()), ("reject", ())):
            self.assertEqual(self.mutate(action, 9, *args, code=2)["error"], "bad-request")
            self.assertEqual(self.files(), before)
        self.assertEqual(self.mutate("reopen", 9)["issue"]["state"], "draft")
        final = self.mutate("submit", 10)["issue"]
        self.assertEqual((final["state"], final["version"]), ("ready", 11))
        self.assertEqual(Path(self.board, ".git/tide-issues.json").read_bytes(), ledger)
        self.assertEqual(self.board_call("Snapshot")["nextId"], "K-1")
        self.assertFalse(Path(self.board, ".git/tide-next-card").exists())
        self.assertFalse(Path(self.board, ".git/tide-ticket-ids.json").exists())
        self.assertEqual(git_out(self.board, "status", "--porcelain"), "")
        self.proc.terminate()
        self.proc.wait(10)
        deadline = time.monotonic() + 5
        while self.bus.bus.interface().isServiceRegistered(SERVICE).value() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.start()
        self.assertEqual(self.run_cli("issue", "show", "S-1")["issue"], final)
        self.assertEqual(self.run_cli("issue", "list")["issues"][0]["version"], 11)
        self.assertEqual(self.run_cli("issue", "create", "--title", "Następna", "--report", self.report)
                         ["issue"]["id"], "S-2")

    def test_parallel_updates_have_one_winner_and_conflict_cannot_write(self):
        self.start()
        self.run_cli("issue", "create", "--title", "Start", "--report", self.report)
        def update(title):
            process = subprocess.run([self.cli, "issue", "update", "S-1", "--expected-version", "1",
                                      "--title", title, "--json"], cwd=self.tmp, env=self.env,
                                     capture_output=True, text=True, timeout=40)
            return process.returncode, json.loads(process.stdout)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ("Pierwszy", "Drugi")))
        self.assertEqual(sorted(code for code, _ in results), [0, 4])
        winner = next(reply["issue"] for code, reply in results if code == 0)
        loser = next(reply for code, reply in results if code == 4)
        self.assertEqual((loser["error"], loser["currentVersion"]), ("version-conflict", 2))
        self.assertEqual(self.call("Show", "S-1")["issue"], winner)
        raw = Path(self.board, "issues/S-1/report.md").read_text()
        self.assertIn(winner["title"], raw)
        self.assertNotIn("Drugi" if winner["title"] == "Pierwszy" else "Pierwszy", raw)
        before = self.files()
        for action, args in (("update", ("--title", winner["title"])),
                             ("submit", ()), ("reject", ()), ("reopen", ())):
            reply = self.mutate(action, 1, *args, code=4)
            self.assertEqual(reply["currentVersion"], 2)
            self.assertEqual(self.files(), before)
        # To samo żądanie przy aktualnej wersji nie zwiększa jej ani nie commituje.
        head = git_out(self.board, "rev-parse", "HEAD")
        self.assertEqual(self.mutate("update", 2, "--title", winner["title"])["issue"], winner)
        self.assertEqual(git_out(self.board, "rev-parse", "HEAD"), head)

    def test_mutations_reject_invalid_input_without_partial_changes(self):
        self.start()
        self.board_call("UpsertProject", '{"name":"Usunięty","slug":"usuniety"}')
        self.run_cli("issue", "create", "--title", "Start", "--report", self.report)
        Path(self.board, "projects/usuniety.md").unlink()
        good = {"expectedVersion": 1, "title": "Nie zapisz", "report": "Nie zapisz"}
        invalid = [{**good, field: "obce"} for field in
                   ("id", "state", "version", "created", "updated", "card", "sourceIssue",
                    "sourceIssueVersion", "attachments", "path", "requestId", "missingEvidence")]
        invalid += [{**good, "title": ""}, {**good, "title": None}, {**good, "report": None},
                    {**good, "project": "nieznany"}, {**good, "project": "usuniety"},
                    {**good, "report": "---\nstate: ready\n---\nx"},
                    {**good, "report": "\ufeff---\r\nversion: 9\r\n---\r\nx"},
                    {"expectedVersion": 1}, {"title": "x"}]
        invalid += [{**good, "expectedVersion": value} for value in (None, True, 0, -1, 1.0, "1")]
        before = self.files()
        head = git_out(self.board, "rev-parse", "HEAD")
        for data in invalid:
            with self.subTest(data=data):
                self.assertEqual(self.call("Update", "S-1", json.dumps(data))["error"], "bad-request")
                self.assertEqual(self.files(), before)
        for raw in ('{"expectedVersion":1,"title":"a","title":"b"}',
                    '{"expectedVersion":1,"report":NaN}', '[]', 'null', '{'):
            self.assertEqual(self.call("Update", "S-1", raw)["error"], "bad-request")
        for method in ("Submit", "Reject", "Reopen"):
            for data in ({}, {"expectedVersion": True}, {"expectedVersion": 1, "state": "ready"},
                         {"expectedVersion": 1, "report": "x"}):
                self.assertEqual(self.call(method, "S-1", json.dumps(data))["error"], "bad-request")
        for action in ("update", "submit", "reject", "reopen"):
            self.run_cli("issue", action, "S-1", code=2)
        self.mutate("update", 1, "--project", "x", "--clear-project", code=2)
        self.mutate("update", 1, code=2)
        self.assertEqual(self.files(), before)
        self.assertEqual(git_out(self.board, "rev-parse", "HEAD"), head)

    def test_submit_checks_text_and_manifest_and_terminal_state_is_protected(self):
        self.start()
        Path(self.report).write_text(" \n")
        self.run_cli("issue", "create", "--title", "Start", "--report", self.report)
        before = self.files()
        self.mutate("submit", 1, code=2)
        self.assertEqual(self.files(), before)
        Path(self.report).write_text("Nie mogę odczytać pliku obrazu. Raport opisuje ten brak.")
        self.mutate("update", 1, "--report", self.report)
        manifest = Path(self.board, "issues/S-1/attachments.json")
        for invalid in ('{}', '{"version":true,"attachments":[]}',
                        '{"version":2,"version":1,"attachments":[]}',
                        '{"version":1,"attachments":[{"id":"nieistniejący"}]}'):
            manifest.write_text(invalid)
            before = self.files()
            self.assertEqual(self.mutate("submit", 2, code=7)["error"], "broken-file")
            self.assertEqual(self.files(), before)
        manifest.write_text('{"version":1,"attachments":[]}\n')
        ready = self.mutate("submit", 2)["issue"]
        self.assertEqual(ready["state"], "ready")
        before = self.files()
        self.mutate("submit", 3, code=2)
        self.mutate("reopen", 3, code=2)
        self.assertEqual(self.files(), before)
        # Przyjęcie dostarczy PT-221; tutaj przygotowujemy jego trwały stan wejściowy.
        report = Path(self.board, "issues/S-1/report.md")
        report.write_text(report.read_text().replace('state: "ready"', 'state: "accepted"'))
        before = self.files()
        for action, args in (("update", ("--title", "Zabroniony")),
                             ("submit", ()), ("reject", ()), ("reopen", ())):
            self.mutate(action, 3, *args, code=2)
            self.assertEqual(self.files(), before)

    def test_installed_cli_roundtrip_restart_parallel_and_old_board(self):
        self.start()
        info = self.run_cli("info")
        self.assertEqual(info["apiVersion"], 1)
        self.assertEqual(set(info["capabilities"]), {
            "issue.create", "issue.list", "issue.show", "issue.update", "issue.submit",
            "issue.reject", "issue.reopen", "issue.attach", "issue.detach", "issue.attachment", "issue.accept",
            "issue.go", "issue.retry-start", "issue.preview-launch",
            "card.open",
        })
        self.assertEqual(set(info["resources"]), {"plan-standard", "next-id", "check-plans"})
        self.assertTrue(all(item["available"] for item in info["resources"].values()))
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
        manifest = Path(self.board, "issues/S-1/attachments.json")
        manifest.write_text("[" * 35000 + "]" * 35000)
        listed = self.run_cli("issue", "list")
        self.assertEqual([i["id"] for i in listed["issues"]], ["S-2"])
        self.assertEqual([(e["id"], e["error"]) for e in listed["errors"]], [("S-1", "broken-file")])
        self.assertEqual(self.run_cli("issue", "show", "S-1", code=7)["error"], "broken-file")
        manifest.write_text('{"version":1,"attachments":[]}')
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
        self.assertEqual(self.run_cli("card", "open", "K-1", code=3)["error"], "api-incompatible")
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
