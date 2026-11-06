"""Rzeczywiste CLI i /Issues: ta sama decyzja, przerwanie procesu oraz restart."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest

from . import test_issues_dbus as base
from . import test_issues_attachments_dbus as faults
from .privatebus import requires_dbus


@requires_dbus
class AcceptanceDbusTest(unittest.TestCase):
    setUp = base.IssuesDbusTest.setUp
    start = base.IssuesDbusTest.start
    run_cli = base.IssuesDbusTest.run_cli
    call = base.IssuesDbusTest.call
    board_call = base.IssuesDbusTest.board_call
    stop_daemon = faults.AttachmentDbusTest.stop_daemon
    fault_start = faults.AttachmentDbusTest.fault_start

    def ready(self):
        issue = self.run_cli("issue", "create", "--report", self.report, "--title", "Do przyjęcia")["issue"]
        return self.run_cli("issue", "submit", issue["id"], "--expected-version", "1")["issue"]

    def accept(self, ident="S-1", version=2, key="decyzja", code=0):
        return self.run_cli("backlog", ident, "--expected-version", str(version), "--request-id", key, code=code)

    def test_cli_explicit_version_dbus_parallel_replay_and_terminal_protection(self):
        self.start()
        issue = self.ready()
        self.run_cli("backlog", "S-1", code=2)
        self.assertEqual(self.accept(version=1, code=4)["error"], "version-conflict")
        self.assertEqual(self.board_call("Snapshot")["cards"], [])
        def decision(key):
            return self.accept(key=key)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(decision, ("przycisk", "cli")))
        self.assertEqual([r["card"] for r in results], ["K-1", "K-1"])
        self.assertEqual(sorted(r["alreadyAccepted"] for r in results), [False, True])
        self.assertEqual(self.call("Show", "S-1")["issue"]["card"], "K-1")
        card = self.board_call("Snapshot")["cards"][0]
        self.assertEqual((card["sourceIssueVersion"], card["spec"], card["column"]), (2, "", "backlog"))
        self.assertEqual(card["description"].strip(), issue["report"].strip())
        for field in ("sourceIssue", "sourceIssueVersion", "sourceIssueHash", "sourceIssueReport", "sourceIssueManifest"):
            self.assertFalse(self.board_call("UpdateCard", "K-1", json.dumps({field: "fałsz"}))["ok"])
        self.board_call("UpdateCard", "K-1", '{"description":"Osobny opis"}')
        self.stop_daemon()
        self.start()
        replay = self.accept(key="cli")
        self.assertTrue(replay["alreadyAccepted"])
        self.assertEqual(self.board_call("Snapshot")["cards"][0]["description"].strip(), "Osobny opis")
        self.assertEqual(self.accept(key="cli", version=3, code=4)["error"], "conflict")
        self.assertEqual(self.accept(version=3, key="nowy", code=4)["error"], "version-conflict")
        for action, args in (("update", ("--title", "Nie zmieniaj")), ("submit", ()), ("reject", ()), ("reopen", ())):
            self.run_cli("issue", action, "S-1", "--expected-version", "3", *args, code=2)
        self.assertEqual(self.board_call("CreateCard", '{"title":"Ręczna"}')["id"], "K-2")

    def test_process_death_and_io_failure_at_each_acceptance_boundary(self):
        control = self.fault_start()
        stages = ("request", "intent", "reserve", "snapshot", "manifest", "card", "counter", "accepted", "published", "done", "complete")
        count = 0
        for action in ("error", "crash"):
            for stage in stages:
                for edge in ("before", "after"):
                    with self.subTest(action=action, stage=stage, edge=edge):
                        issue = self.ready()
                        count += 1
                        ident = issue["id"]
                        control.write_text(json.dumps({"stage": f"accept-{edge}-{stage}", "action": action}))
                        self.accept(ident, key=ident, code=7 if action == "error" else 3)
                        self.stop_daemon()
                        control = self.fault_start()
                        shown = self.call("Show", ident)
                        self.assertTrue(shown["ok"], shown)
                        result = self.accept(ident, key=ident)
                        self.assertEqual(result["card"], f"K-{count}")
                        self.assertEqual(len(self.board_call("Snapshot")["cards"]), count)
                        self.assertFalse(control.exists())

    def test_commit_failure_reports_disk_success_and_no_second_reservation(self):
        self.start()
        self.ready()
        lock = Path(self.board, ".git/index.lock")
        lock.write_text("Zajęty indeks")
        accepted = self.accept()
        self.assertTrue(accepted["ok"])
        self.assertTrue(accepted["commitError"])
        self.assertEqual(self.call("Show", "S-1")["issue"]["state"], "accepted")
        self.stop_daemon()
        lock.unlink(missing_ok=True)
        self.start()
        self.assertEqual(self.accept()["card"], "K-1")
        self.assertEqual(len(self.board_call("Snapshot")["cards"]), 1)
