"""PT-226: cienki adapter CLI i zainstalowany proces na prywatnym D-Bus.

Rejestracja: zestaw issues (test_issues*.py) oraz pełny zestaw daemon.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from . import DAEMON_DIR
from . import test_cardflow_dbus as flow
from .privatebus import wait_until

REPO = Path(DAEMON_DIR).parent
spec = importlib.util.spec_from_file_location("tide_card_cli", REPO / "cli/tide.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
XML = ('<node><interface name="org.tide.Daemon.Board"><method name="TalkToCaretaker">'
       '<arg type="s" direction="in"/><arg type="s" direction="in"/>'
       '<arg type="s" direction="out"/></method></interface></node>')


class CardCliTest(unittest.TestCase):
    def invoke(self, api, *args):
        output = io.StringIO()
        with patch.object(cli.BoardApi, "__init__", return_value=None), \
                patch.object(cli.BoardApi, "request", side_effect=api) as request, \
                patch.object(cli.PublicApi, "call", side_effect=AssertionError("API spraw")), \
                patch.object(cli.subprocess, "run", side_effect=AssertionError("program poza usługą")), \
                contextlib.redirect_stdout(output):
            code = cli.main(["card", "open", *args, "--json"])
        return code, json.loads(output.getvalue()), request

    def test_exact_contract_and_optional_problem(self):
        for problem in (None, "problem-ą"):
            with self.subTest(problem=problem):
                expected = {"ok": True, "step": "chat-1"}
                args = ["K-55"] + (["--problem", problem] if problem else [])
                code, reply, request = self.invoke([[XML], [json.dumps(expected)]], *args)
                self.assertEqual((code, reply), (0, expected))
                self.assertEqual(request.call_count, 2)
                path, iface, method, card, raw = request.call_args.args
                self.assertEqual((path, iface, method, card),
                                 ("/Board", "org.tide.Daemon.Board", "TalkToCaretaker", "K-55"))
                self.assertEqual(json.loads(raw), {} if problem is None else {"problem": problem})

    def test_refusals_preserve_entire_reply(self):
        for error, code in (("console-open", 4), ("no-step", 5), ("no-terminal", 3),
                            ("bad-request", 2), ("not-found", 5), ("future-error", 1)):
            with self.subTest(error=error):
                expected = {"ok": False, "error": error, "message": "Odmowa", "detail": "dowód"}
                actual, reply, _ = self.invoke([[XML], [json.dumps(expected)]], "K-1")
                self.assertEqual((actual, reply), (code, expected))

    def test_missing_or_wrong_public_signature_has_no_fallback(self):
        for xml in ("<node/>", XML.replace('type="s"', 'type="i"', 1),
                    XML.replace("TalkToCaretaker", "OpenCardGuardian")):
            code, reply, request = self.invoke([[xml]], "K-1")
            self.assertEqual((code, reply["error"]), (3, "api-incompatible"))
            self.assertEqual(request.call_count, 1)

    def test_malformed_responses_are_not_successes(self):
        for raw in ('{}', '[]', 'null', '{"ok":true}', '{"ok":false}',
                    '{"ok":1,"step":"x"}', 'invalid'):
            code, reply, _ = self.invoke([[XML], [raw]], "K-1")
            self.assertEqual((code, reply["error"]), (3, "api-incompatible"))

    def test_invalid_arguments_do_not_call_service(self):
        for args in (("S-1",), ("K-0",), ("K-1", "--problem", "")):
            code, reply, request = self.invoke([], *args)
            self.assertEqual((code, reply["error"]), (2, "bad-request"))
            request.assert_not_called()

    def test_info_does_not_claim_behavioral_verification_or_open_chat(self):
        for available in (True, False):
            output = io.StringIO()
            with patch.object(cli.PublicApi, "__init__", return_value=None), \
                    patch.object(cli.PublicApi, "call", return_value={"ok": True, "apiVersion": 1,
                                                                      "capabilities": ["issue.list"]}), \
                    patch.object(cli.BoardApi, "available", return_value=available), \
                    patch.object(cli.BoardApi, "open", side_effect=AssertionError("otwarcie")), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(["info", "--json"]), 0)
            info = json.loads(output.getvalue())
            self.assertEqual("card.open" in info["capabilities"], available)
            self.assertEqual(info["operations"]["card.open"]["available"], available)
            self.assertFalse(info["operations"]["card.open"]["contractVerified"])


class CardCliDbusTest(flow.CaretakerChatDbusTest):
    # Dowód kontraktu PT-149 jest osobnym testem; tutaj tylko scenariusze adaptera.
    test_talk_opens_console_with_chat_context_and_only_one_session = None

    def setUp(self):
        super().setUp()
        self.cli_env = {**self.env, "DBUS_SESSION_BUS_ADDRESS": self.service.address, "PYTHONPATH": ""}
        subprocess.run(["make", "-s", "install-cli"], cwd=REPO, env=self.cli_env,
                       check=True, capture_output=True, text=True)
        self.cli_path = os.path.join(self.env["HOME"], ".local/bin/tide")

    def run_cli(self, card, problem=None, code=0):
        args = [self.cli_path, "card", "open", card, "--json"]
        if problem is not None:
            args += ["--problem", problem]
        proc = subprocess.run(args, cwd=self.base, env=self.cli_env,
                              capture_output=True, text=True, timeout=40)
        self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def test_button_and_installed_cli_share_one_chat_and_preserve_card_state(self):
        for cli_first in (True, False):
            card = self.card(description="Eksport.\n\nATRAPA: blad-opiekun\n"
                             "ATRAPA-POWOD: test pada ATRAPA-OPIEKUN: escalate")
            self.assertTrue(self.start(card)["ok"])
            self.assertTrue(wait_until(lambda: self.of(card, "caretaker.escalated"), timeout=30))
            problem = self.of(card, "caretaker.started")[0]["problem"]
            before = self.shown(card)
            tasks_before = len(self.console_tasks())
            # Przycisk PT-149 wywołuje tę samą publiczną metodę przez backend Tablicy.
            if cli_first:
                reply = self.run_cli(card, problem)
                duplicate = self.call("TalkToCaretaker", card, "{}")
            else:
                reply = self.call("TalkToCaretaker", card, json.dumps({"problem": problem}))
                duplicate = self.run_cli(card, code=4)
            self.assertTrue(reply["ok"], reply)
            self.assertEqual(duplicate["error"], "console-open")
            self.assertEqual(self.run_cli(card, code=4)["error"], "console-open")
            self.assertTrue(wait_until(lambda: len(self.console_tasks()) == tasks_before + 1, timeout=5))
            chats = [e for e in self.of(card, "step.started") if e.get("kind") == "caretaker-chat"]
            self.assertEqual([e["step"] for e in chats], [reply["step"]])
            after = self.shown(card)
            self.assertEqual(after["boardColumn"], before["boardColumn"])
            self.assertEqual(after["derived"]["flow"]["paused"], before["derived"]["flow"]["paused"])
            self.assertTrue(Path(chats[0]["stepDir"], "in/context").is_dir())

    def test_unsupported_and_completed_card_return_no_step(self):
        for column in ("todo", "done"):
            card = self.card(column=column)
            before = self.shown(card)
            self.assertEqual(self.run_cli(card, code=5)["error"], "no-step")
            self.assertEqual(self.shown(card), before)
        self.assertEqual(self.console_tasks(), [])

    def test_missing_console_returns_no_terminal_without_starting_chat(self):
        card = self.card(description="Eksport.\n\nATRAPA: blad-opiekun\n"
                         "ATRAPA-POWOD: test pada ATRAPA-OPIEKUN: escalate")
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "caretaker.escalated"), timeout=30))
        self.fake_console.terminate()
        self.fake_console.wait(5)
        self.assertTrue(wait_until(lambda: not self.service.bus.interface().isServiceRegistered(
            "org.kde.konsole.tide_caretaker_test").value(), timeout=5))
        before = self.shown(card)
        self.assertEqual(self.run_cli(card, code=3)["error"], "no-terminal")
        self.assertEqual(self.shown(card), before)
        self.assertFalse([e for e in self.of(card, "step.started") if e.get("kind") == "caretaker-chat"])
