"""Scalony dispatcher CLI zachowuje przyjęcie sprawy obok opiekuna karty."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "tide_backlog_cli", Path(__file__).resolve().parents[2] / "cli/tide.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class BacklogCliTest(unittest.TestCase):
    def invoke(self, reply, capabilities=("issue.accept",)):
        output = io.StringIO()
        with patch.object(cli, "PublicApi") as api, \
                patch.object(cli.BoardApi, "open", side_effect=AssertionError("opiekun karty")), \
                contextlib.redirect_stdout(output):
            api.return_value.call.side_effect = [
                {"ok": True, "apiVersion": 1, "capabilities": list(capabilities)}, reply]
            code = cli.main(["backlog", "S-7", "--expected-version", "4",
                             "--request-id", "przeczytana-wersja", "--json"])
        return code, json.loads(output.getvalue()), api.return_value.call

    def test_backlog_dispatches_acceptance_with_the_decided_version(self):
        expected = {"ok": True, "apiVersion": 1, "card": "K-9",
                    "acceptedVersion": 4, "alreadyAccepted": True}
        code, reply, call = self.invoke(expected)
        self.assertEqual((code, reply), (0, expected))
        self.assertEqual(call.call_count, 2)
        method, ident, raw = call.call_args.args
        self.assertEqual((method, ident), ("AcceptIssue", "S-7"))
        self.assertEqual(json.loads(raw), {"expectedVersion": 4, "destination": "backlog",
                                         "requestId": "przeczytana-wersja"})

    def test_backlog_preserves_service_refusal_and_exit_code(self):
        expected = {"ok": False, "apiVersion": 1, "error": "version-conflict", "currentVersion": 5}
        code, reply, call = self.invoke(expected)
        self.assertEqual((code, reply), (4, expected))
        self.assertEqual(call.call_count, 2)

    def test_backlog_requires_acceptance_capability_before_mutation(self):
        code, reply, call = self.invoke({}, capabilities=())
        self.assertEqual((code, reply["error"]), (3, "api-incompatible"))
        call.assert_called_once_with("Info")
