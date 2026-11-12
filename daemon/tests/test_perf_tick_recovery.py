"""Dym reproduktora K-98 #3 (`perf_tick_recovery.py`): mały przebieg daje komplet pól wyniku i poprawne odzyskiwanie.

Nie mierzy czasu na sztywno — sprawdza kształt wyniku, zachowanie po restarcie (jeden żywy właściciel, rozliczony
martwy proces, blokada stanu unknown, bez nowej jednostki) i to, że skrypt przywraca owinięte funkcje."""
import os
import unittest

from tide_daemon.cardflow import boxstep, processidentity, state, trialhold

from . import perf_tick_recovery as perf


class SmokeTest(unittest.TestCase):
    def test_small_run_recovers_and_reports_counters(self):
        originals = (trialhold.read_process_history, processidentity.process_identities,
                     state.process_identities, boxstep.recover_network)
        before = {key: os.environ.get(key) for key in ("HOME", "XDG_STATE_HOME", "TIDE_BOARD_DIR")}
        result = perf.measure(fillers=2, padding=3, ticks=2, sha="test")
        self.assertEqual({key: os.environ.get(key) for key in before}, before)
        self.assertEqual((trialhold.read_process_history, processidentity.process_identities,
                          state.process_identities, boxstep.recover_network), originals)
        self.assertEqual(result["checks"]["ok"], {"liveOneOwner": True, "deadAccounted": True,
                                                  "unknownBlocked": True, "noNewUnit": True}, result["checks"])
        self.assertEqual(result["history"]["cards"], 5)
        restart = result["restartTick"]
        self.assertEqual(set(restart["functions"]), set(perf.MEASURED))
        self.assertGreater(restart["readComplete"], 0)
        self.assertGreater(restart["recoveryPhase"]["readComplete"], 0)
        self.assertLessEqual(restart["recoveryPhase"]["readComplete"], restart["readComplete"])
        self.assertGreater(restart["functions"]["read_process_history"]["calls"], 0)
        self.assertEqual(restart["functions"]["recover_network"]["calls"], 1)
        self.assertGreaterEqual(restart["boardLoads"], 1)
        self.assertEqual(len(result["warmTicks"]), 1)
        self.assertIn("tick restartu", perf.summary(result))

    def test_script_runs_from_its_file_path(self):
        """Tak uruchamia go skrypt porównania (`perf_tick_recovery_compare.sh`): ścieżka pliku, bez runnera testów."""
        import json
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, "wynik.json")
            done = subprocess.run([sys.executable, perf.__file__, "--src", perf.DEFAULT_SRC, "--out", out,
                                   "--fillers", "1", "--padding", "1", "--ticks", "1", "--sha", "test"],
                                  cwd=folder, capture_output=True, text=True, timeout=300)
            self.assertEqual(done.returncode, 0, done.stderr[-3000:])
            with open(out, encoding="utf-8") as fh:
                result = json.load(fh)
        self.assertTrue(all(result["checks"]["ok"].values()), result["checks"])


if __name__ == "__main__":
    unittest.main()
