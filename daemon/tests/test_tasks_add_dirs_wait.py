"""Regresja odczytu testu Tasks po odpowiedzi Start, bez prawdziwej magistrali."""
import os
import tempfile
import unittest

from . import test_tasks_dbus
from .privatebus import QDBusConnection


@unittest.skipUnless(QDBusConnection is not None, "brak PyQt6")
class AddDirsPublicationTest(unittest.TestCase):
    def test_scenario_waits_for_second_agent_publication(self):
        # Wywołujemy prawdziwy scenariusz kontraktu. Start drugiego narzędzia już
        # odpowiedział, lecz jego wpis staje się dostępny dopiero w trzecim odczycie.
        with tempfile.TemporaryDirectory() as root:
            scenario = unittest.TestCase()
            scenario.alpha = os.path.join(root, "alpha")
            scenario.beta = os.path.join(root, "beta")
            scenario.board = os.path.join(root, "board")
            published = []
            pending = []
            observations = []

            def start(**request):
                if request["addDirs"] == [scenario.beta]:
                    return {"ok": False, "error": "bad-add-dir"}
                row = {"name": request["tool"],
                       "argv": ["--add-dir", request["addDirs"][0], "--", request["prompt"]]}
                (published if request["tool"] == "codex" else pending).append(row)
                return {"ok": True, "cwd": scenario.alpha, "worktree": ""}

            def agent_calls():
                if pending:
                    observations.append(len(published))
                    if len(observations) == 3:
                        published.extend(pending)
                        pending.clear()
                return list(published)

            scenario.start = start
            scenario.agent_calls = agent_calls
            test_tasks_dbus.TasksContractTest.test_5_add_dirs_from_board_for_both_tools(scenario)
            self.assertEqual(observations, [1, 1, 1])
            self.assertEqual([row["name"] for row in published], ["codex", "claude"])


if __name__ == "__main__":
    unittest.main()
