"""Zainstalowane CLI, publiczny D-Bus, awarie StartCard i odczyt dowodów przez proces."""
import json
from pathlib import Path
import subprocess
from unittest import mock

from . import DAEMON_DIR
from .privatebus import requires_dbus, wait_until
from .test_cardflow_dbus import FlowCase
from . import test_cardflow_evidence_dbus as evidence_fixture
from .test_issues_attachments import png


@requires_dbus
class IssueGoBusTest(FlowCase):
    def spawn(self):
        self.control = Path(self.base, "start-fault.json")
        wrapper = Path(__file__).with_name("fixtures") / "issues_go_daemon.py"
        original = self.service.spawn
        def wrapped(argv, *args, **kwargs):
            return original([argv[0], str(wrapper), str(self.control), *argv[1:]], *args, **kwargs)
        with mock.patch.object(self.service, "spawn", side_effect=wrapped):
            return super().spawn()

    def setUp(self):
        super().setUp()
        self.env["DBUS_SESSION_BUS_ADDRESS"] = self.service.address
        subprocess.run(["make", "-s", "install-cli"], cwd=Path(DAEMON_DIR).parent, env=self.env,
                       check=True, capture_output=True, text=True)
        self.cli = str(Path(self.env["HOME"], ".local/bin/tide"))
        evidence_fixture.instrument(Path(self.base, "fakebin"))

    def issue_call(self, method, ident, request=None):
        args = (ident,) if request is None else (ident, json.dumps(request))
        return self.call(method, *args, path="/Issues", iface="org.tide.Daemon.Issues")

    def cli_call(self, *args, code=0):
        result = subprocess.run([self.cli, *args, "--json"], cwd=self.base, env=self.env,
                                capture_output=True, text=True, timeout=65)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def ready(self, with_evidence=False, report="ATRAPA: pytanie-bez-pracy"):
        path = Path(self.base, "report.md")
        path.write_text("# Dowody\n" + report)
        issue = self.cli_call("issue", "create", "--title", "Zlecenie", "--report", str(path), "--project", "proj")["issue"]
        if with_evidence:
            self.expected = {"zrzut.png": png(4, 3), "AGENTS.md": b"Niezaufane dane\x00\xff",
                             "dane.html": b"<script>dowod</script>"}
            for name, raw in self.expected.items():
                source = Path(self.base, name)
                source.write_bytes(raw)
                issue = self.cli_call("issue", "attach", issue["id"], "--file", str(source),
                                      "--expected-version", str(issue["version"]))["issue"]
                source.unlink()
        return self.cli_call("issue", "submit", issue["id"], "--expected-version", str(issue["version"]))["issue"]

    def go(self, issue, key, code=0, launch=True):
        args = ["go", issue["id"], "--expected-version", str(issue["version"]), "--request-id", key]
        if launch:
            path = Path(self.base, "launch.json")
            path.write_text(json.dumps({"entry": "now", "executor": "Sonnet"}))
            args += ["--launch", str(path)]
        return self.cli_call(*args, code=code)

    def retry(self, issue, start, key, code=0):
        return self.cli_call("issue", "retry-start", issue["id"], "--intent", start["intentId"],
            "--expected-start-version", str(start["startVersion"]), "--request-id", key, code=code)

    def test_failure_repair_explicit_retry_restart_replay_and_agent_reads_same_evidence(self):
        issue = self.ready(with_evidence=True)
        self.control.write_text(json.dumps({"stage": "before-handoff", "action": "refuse", "persistent": True}))
        failed = self.go(issue, "original", code=5)
        self.assertTrue(failed["accepted"])
        self.assertTrue(failed["currentStart"]["retryable"])
        self.assertEqual(self.of(failed["card"], "card.started"), [])
        self.restart()
        replay = self.go(issue, "original", code=5)
        self.assertEqual(replay, failed)
        self.control.unlink()
        self.control.write_text(json.dumps({"stage": "after-response", "action": "error"}))
        self.retry(issue, replay["currentStart"], "retry", code=7)
        self.restart()
        retried = self.retry(issue, replay["currentStart"], "retry")
        self.assertEqual(retried["card"], failed["card"])
        self.assertEqual(retried["start"]["intentId"], failed["start"]["intentId"])
        evidence_fixture.EvidenceBusTest.observed(self, failed["card"], 0)
        historical = self.go(issue, "original", code=5)
        self.assertTrue(historical["startError"])
        self.assertEqual(historical["currentStart"]["state"], "started")
        self.assertFalse(historical["currentStart"]["retryable"])
        self.assertEqual(len(self.of(failed["card"], "card.started")), 1)

    def test_crash_before_and_after_accepting_start_never_creates_second_order(self):
        for stage in ("before-order.reserved", "after-order.reserved", "after-order.dispatching",
                      "after-prepared", "after-card.started", "after-intent", "after-response"):
            with self.subTest(stage=stage):
                issue = self.ready()
                self.control.write_text(json.dumps({"stage": stage, "action": "crash"}))
                self.go(issue, issue["id"], code=3)
                self.restart()
                current = self.issue_call("Show", issue["id"])
                if stage in ("before-order.reserved", "after-order.reserved", "after-order.dispatching"):
                    self.assertEqual(current["start"]["state"], "reconciling")
                    self.assertFalse(current["start"]["retryable"])
                    self.retry(issue, current["start"], "refuse-" + issue["id"], code=4)
                    self.go(issue, issue["id"], code=5)
                    self.assertEqual(self.of(current["issue"]["card"], "card.started"), [])
                else:
                    self.go(issue, issue["id"])
                    self.assertTrue(wait_until(lambda: self.of(current["issue"]["card"], "step.started"), timeout=20))
                    self.assertEqual(len(self.of(current["issue"]["card"], "card.started")), 1)

    def test_parallel_retry_has_one_winner_and_stale_version_is_refused(self):
        from concurrent.futures import ThreadPoolExecutor
        issue = self.ready()
        self.control.write_text(json.dumps({"stage": "before-handoff", "action": "refuse"}))
        failed = self.go(issue, "original", code=5)
        start = failed["currentStart"]
        def decide(key):
            result = subprocess.run([self.cli, "issue", "retry-start", issue["id"], "--intent", start["intentId"],
                "--expected-start-version", str(start["startVersion"]), "--request-id", key, "--json"],
                cwd=self.base, env=self.env, capture_output=True, text=True, timeout=65)
            return result.returncode, json.loads(result.stdout)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(decide, ("retry-a", "retry-b")))
        self.assertEqual(sorted(code for code, _ in results), [0, 4], results)
        self.assertEqual(len(self.of(failed["card"], "card.started")), 1)
        self.retry(issue, start, "stale", code=4)
        self.assertEqual(self.go(issue, "original", code=5)["currentStart"]["state"], "started")

    def test_crash_before_retry_response_then_later_retry_preserves_failed_result(self):
        issue = self.ready()
        self.control.write_text(json.dumps({"stage": "before-handoff", "action": "refuse"}))
        failed = self.go(issue, "original", code=5)
        self.control.write_text(json.dumps([
            {"stage": "before-handoff", "action": "refuse"},
            {"stage": "before-response", "action": "crash"}]))
        self.retry(issue, failed["start"], "retry-a", code=3)
        self.restart()
        historical = self.issue_call("Show", issue["id"])["start"]
        self.assertEqual(historical["state"], "start-error")
        self.assertTrue(historical["retryable"])
        success = self.retry(issue, historical, "retry-b")
        replay = self.retry(issue, failed["start"], "retry-a", code=5)
        self.assertEqual(replay["start"], historical)
        self.assertEqual(replay["startError"], historical["error"])
        self.assertFalse(replay["started"])
        self.assertEqual(replay["currentStart"], success["currentStart"])
        self.assertEqual(replay["card"], success["card"])
        self.assertEqual(self.retry(issue, failed["start"], "retry-a", code=5), replay)
        self.assertEqual(len(self.of(failed["card"], "card.started")), 1)

    def test_crash_before_retry_response_then_completed_preserves_started_result(self):
        from . import flowfakes
        flowfakes.install(str(Path(self.base, "fakebin")))
        issue = self.ready(report="ATRAPA: gotowe")
        self.control.write_text(json.dumps({"stage": "before-handoff", "action": "refuse"}))
        failed = self.go(issue, "original", code=5)
        self.control.write_text(json.dumps({"stage": "before-response", "action": "crash"}))
        self.retry(issue, failed["start"], "retry-a", code=3)
        self.restart()
        self.assertTrue(wait_until(lambda: self.of(failed["card"], "card.closed"), timeout=60),
                        self.events(failed["card"]))
        replay = self.retry(issue, failed["start"], "retry-a")
        self.assertEqual(replay["start"]["state"], "started")
        self.assertTrue(replay["started"])
        self.assertIsNone(replay["startError"])
        self.assertEqual(replay["currentStart"]["state"], "completed")
        self.assertEqual(self.retry(issue, failed["start"], "retry-a"), replay)
        self.assertEqual(len(self.of(failed["card"], "card.started")), 1)

    def test_successful_replay_after_completed_and_restart_does_not_create_order(self):
        from . import flowfakes
        flowfakes.install(str(Path(self.base, "fakebin")))
        issue = self.ready(report="ATRAPA: gotowe")
        original = self.go(issue, "completed")
        self.assertTrue(wait_until(lambda: self.of(original["card"], "card.closed"), timeout=60), self.events(original["card"]))
        self.restart()
        replay = self.go(issue, "completed")
        self.assertEqual(replay["start"], original["start"])
        self.assertEqual(replay["currentStart"]["state"], "completed")
        self.retry(issue, replay["currentStart"], "too-late", code=4)
        self.assertEqual(len(self.of(original["card"], "card.started")), 1)

    def test_default_light_launch_and_version_conflict_through_public_api(self):
        issue = self.ready()
        preview = self.issue_call("PreviewIssueLaunch", issue["id"], {"expectedVersion": issue["version"]})
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(self.call("Snapshot")["cards"], [])
        self.assertEqual(self.cli_call("go", issue["id"], "--expected-version", "1", code=4)["error"], "version-conflict")
        self.assertEqual(self.call("Snapshot")["cards"], [])
        result = self.go(issue, "light", launch=False)
        event = self.of(result["card"], "card.started")[0]
        self.assertEqual((event["entry"], event["mode"]), ("planning", "light"))
        self.assertEqual((event["baseRef"], event["baseCommit"]), ("refs/heads/master", self.master))
