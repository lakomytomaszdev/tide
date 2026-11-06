"""Regresja kolejności: odpowiedź Start może wyprzedzić zapis atrapy procesu agenta."""
import json
from pathlib import Path
import tempfile
import unittest

from . import privatebus
from . import test_tasks_dbus as contract


@privatebus.requires_dbus
class AgentLogSynchronizationTest(unittest.TestCase):
    def test_start_waits_for_agent_record_after_claim_reply(self):
        from PyQt6.QtCore import QTimer
        app = privatebus.app()
        with tempfile.TemporaryDirectory(prefix="tide-agent-log-sync-") as tmp:
            case = contract.TasksContractTest("test_1_repos_defaults_and_suggestion")
            case.agent_log = str(Path(tmp) / "agent.log")
            entry = {"name": "claude", "argv": ["--", "Raport z rozmowy"], "cwd": tmp}
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: Path(case.agent_log).write_text(json.dumps(entry) + "\n"))

            def claim_reply(*args, **kwargs):
                # Jak prawdziwe Tasks.Start: potwierdzenie przejęcia wraca przed logiem procesu.
                timer.start(30)
                return '{"ok":true}'

            case.call = claim_reply
            try:
                result = case.start(prompt="Raport z rozmowy")
                self.assertTrue(result["ok"])
                self.assertEqual(case.agent_calls(), [entry])
            finally:
                timer.stop()
