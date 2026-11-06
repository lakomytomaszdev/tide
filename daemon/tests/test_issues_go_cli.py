"""Go i retry są różnymi operacjami; historyczna odpowiedź ma własny kod wyjścia."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from .test_issues_backlog_cli import cli


class GoCliTest(unittest.TestCase):
    def invoke(self, args, result):
        output = io.StringIO()
        current = {"ok": True, "apiVersion": 1, "start": {"state": "started", "retryable": False}}
        with patch.object(cli, "PublicApi") as api, contextlib.redirect_stdout(output):
            api.return_value.call.side_effect = [{"ok": True, "apiVersion": 1,
                "capabilities": ["issue.go", "issue.retry-start"]}, result, current]
            code = cli.main([*args, "--json"])
        return code, json.loads(output.getvalue()), api.return_value.call.call_args_list

    def test_go_requires_read_version_omits_missing_launch_and_returns_historical_error_code(self):
        result = {"ok": True, "apiVersion": 1, "accepted": True, "card": "K-3",
                  "startError": {"code": "failed"}, "start": {"state": "start-error"}}
        code, reply, calls = self.invoke(["go", "S-1", "--expected-version", "2", "--request-id", "decision"], result)
        self.assertEqual(code, 5)
        method, ident, raw = calls[1].args
        self.assertEqual((method, ident), ("AcceptIssue", "S-1"))
        self.assertEqual(json.loads(raw), {"expectedVersion": 2, "destination": "go", "requestId": "decision"})
        self.assertEqual(reply["start"]["state"], "start-error")
        self.assertEqual(reply["currentStart"]["state"], "started")
        self.assertEqual(calls[2].args, ("Show", "S-1"))

    def test_retry_uses_only_intent_start_version_and_new_request_key(self):
        code, reply, calls = self.invoke(["issue", "retry-start", "S-1", "--intent", "intent",
            "--expected-start-version", "3", "--request-id", "retry-decision"],
            {"ok": True, "apiVersion": 1, "accepted": True, "start": {"state": "started"}})
        self.assertEqual(code, 0)
        method, ident, raw = calls[1].args
        self.assertEqual((method, ident), ("RetryIssueStart", "S-1"))
        self.assertEqual(json.loads(raw), {"intentId": "intent", "expectedStartVersion": 3,
                                         "requestId": "retry-decision"})
        self.assertEqual(reply["currentStart"]["state"], "started")
