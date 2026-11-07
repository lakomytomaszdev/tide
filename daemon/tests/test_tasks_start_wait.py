"""Regresja PT-249: odpowiedź Start może wyprzedzić pełny zapis atrapy."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from . import test_tasks_dbus as tasks
from .privatebus import requires_dbus, wait_until
from .test_tasks_core import FAKE_AGENT, write_exec


class AgentCallWaitTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="tide-agent-wait-")
        self.addCleanup(tmp.cleanup)
        self.case = tasks.TasksContractTest("test_4_refusals_and_main_checkout")
        self.case.agent_log = os.path.join(tmp.name, "agent.log")
        self.log = Path(self.case.agent_log)
        self.prompt = "Przejrzyj README w głównym katalogu"
        self.record = {"name": "codex", "argv": ["--", self.prompt], "cwd": tmp.name}

    def test_start_response_returns_before_agent_finishes_writing(self):
        # Bramka agenta może zostać zwolniona dopiero przez odbiorcę odpowiedzi Start.
        self.log.write_bytes(b'{"argv":"\xc3')
        with mock.patch.object(self.case, "call", return_value='{"ok": true}') as call, \
                mock.patch.object(tasks, "wait_until", side_effect=AssertionError("Start czeka na agenta")):
            self.assertEqual(self.case.start_response(prompt=self.prompt), {"ok": True})
        call.assert_called_once_with("Start", json.dumps({"prompt": self.prompt}, ensure_ascii=False))

    def test_agent_calls_decodes_only_complete_lines(self):
        complete = (json.dumps(self.record, ensure_ascii=False) + "\n").encode()
        for partial in (b'{"argv":', b'{"argv":"\xc3'):
            with self.subTest(partial=partial):
                self.log.write_bytes(complete + partial)
                self.assertEqual(self.case.agent_calls(), [self.record])

    def test_agent_calls_rejects_malformed_complete_lines(self):
        for content in (b'{"argv":\n', b'{"argv":"\xc3"}\n'):
            with self.subTest(content=content):
                self.log.write_bytes(content)
                with self.assertRaises((ValueError, UnicodeDecodeError)):
                    self.case.agent_calls()

    def test_waits_for_new_complete_line_even_if_old_record_matches(self):
        old = (json.dumps(self.record) + "\n").encode()
        other = b'{"name":"claude","argv":["--","inne zadanie"]}\n'
        current = json.dumps(self.record).encode()
        # Poprawny JSON bez końca linii nadal jest zapisem w toku.
        self.log.write_bytes(old + other + current)
        finished = threading.Event()

        def finish():
            finished.set()
            with self.log.open("ab") as fh:
                fh.write(b"\n")

        timer = threading.Timer(0.03, finish)
        timer.start()
        self.addCleanup(timer.join)
        got = self.case.wait_for_agent_call(len(old), self.prompt)
        self.assertTrue(finished.is_set())
        self.assertEqual(got, self.record)

    def test_missing_call_fails_with_start_and_log_diagnostics(self):
        for content in (None, b'{"argv":["--","inne zadanie"]}\n', b'{"argv":"\xc5'):
            with self.subTest(content=content):
                if content is not None:
                    self.log.write_bytes(content)
                with self.assertRaises(AssertionError) as caught:
                    self.case.wait_for_agent_call(0, self.prompt, timeout=0.02)
                message = str(caught.exception)
                self.assertIn(self.prompt, message)
                self.assertIn(self.case.agent_log, message)
                self.assertIn("offset=0", message)
                self.assertIn(f"nowe bajty={content or b''!r}", message)


@requires_dbus
@unittest.skipUnless(shutil.which("git"), "brak gita")
class DelayedMainCheckoutTest(unittest.TestCase):
    def test_scenario_4_with_agent_blocked_until_after_start_reply(self):
        fixture = tasks.TasksContractTest
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        gate = Path(fixture.tmp) / "release-agent"
        agent = Path(fixture.bin) / "codex"
        # Atrapa zapisuje fragment UTF-8 i czeka na bramkę. Dopiero po odpowiedzi
        # Start włączamy kontrolowane opóźnienie zwolnienia bramki.
        delayed = '''#!/usr/bin/python3
import json, os, sys, time
payload = json.dumps({"name": os.path.basename(sys.argv[0]), "argv": sys.argv[1:],
                      "cwd": os.getcwd()}, ensure_ascii=False).encode("utf-8") + b"\\n"
cut = payload.index("ó".encode("utf-8")) + 1
with open(os.environ["TIDE_FAKE_AGENT_LOG"], "ab", buffering=0) as fh:
    fh.write(payload[:cut])
    deadline = time.monotonic() + 10
    while not os.path.exists(GATE):
        if time.monotonic() >= deadline:
            sys.exit(97)
        time.sleep(0.01)
    fh.write(payload[cut:])
'''.replace("GATE", repr(str(gate)))
        write_exec(str(agent), delayed)
        self.addCleanup(write_exec, str(agent), FAKE_AGENT)
        case = fixture("test_4_refusals_and_main_checkout")
        original_start = case.start_response
        timers = []
        replies = []

        def start(**request):
            result = original_start(**request)
            if result.get("ok"):
                replies.append(result)
                self.assertFalse(gate.exists())
                log = Path(fixture.agent_log)
                self.assertTrue(wait_until(lambda: log.exists() and log.stat().st_size > 0))
                self.assertFalse(log.read_bytes().endswith(b"\n"))
                timer = threading.Timer(0.15, gate.touch)
                timers.append(timer)
                timer.start()
            return result

        case.start = start
        try:
            case.test_4_refusals_and_main_checkout()
            self.assertEqual(len(replies), 1)
            self.assertEqual(len(case.agent_calls()), 1)
        finally:
            gate.touch()
            for timer in timers:
                timer.join()
