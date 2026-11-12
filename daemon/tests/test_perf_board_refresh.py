"""Dym reproduktora K-98 (`perf_board_refresh.py`): mały przebieg daje komplet pól wyniku i nearest-rank p95.

Nie mierzy czasu na sztywno — sprawdza tylko kształt wyniku i to, że skrypt działa na izolowanych danych."""
import os
import unittest

from . import perf_board_refresh as perf


class NearestRankTest(unittest.TestCase):
    def test_nearest_rank_p95(self):
        self.assertEqual(perf.nearest_rank(list(range(1, 101))), 95)
        self.assertEqual(perf.nearest_rank([5.0, 1.0, 3.0]), 5.0)
        self.assertEqual(perf.nearest_rank(list(range(1, 21))), 19)
        self.assertIsNone(perf.nearest_rank([]))


class SmokeTest(unittest.TestCase):
    def test_small_run_reports_raw_samples_and_restores_environment(self):
        before = {key: os.environ.get(key) for key in ("HOME", "XDG_STATE_HOME", "TIDE_BOARD_DIR")}
        result = perf.measure(cards=3, refreshes=2, requests=2, sha="test", box_file=False)
        self.assertEqual({key: os.environ.get(key) for key in before}, before)
        self.assertFalse(os.path.exists(result["isolatedBase"]), "katalog danych pomiaru został")
        self.assertEqual(result["history"]["cards"], 3)
        self.assertGreater(result["history"]["events"], 0)
        self.assertEqual(len(result["refresh"]["samplesMs"]), 2)
        self.assertEqual(len(result["refresh"]["boardLoads"]), 2)
        self.assertGreaterEqual(result["coldStart"]["boardLoads"], 1)
        self.assertEqual(result["coldStart"]["cleared"], ["engine._env_cache"])
        self.assertEqual(set(result["methods"]), set(perf.METHODS))
        self.assertIn("BoxStatus", perf.METHODS)
        self.assertEqual(result["boxStatus"]["builder"], "fake-incus")
        self.assertTrue(result["boxStatus"]["ok"], result["boxStatus"])
        # Normalny odczyt stanu pudełka (brak instancji w atrapie), nie ścieżka błędu atrapy `incus`.
        self.assertIsNone(result["boxStatus"]["error"], result["boxStatus"])
        from tide_daemon.box import state as bs
        self.assertEqual(result["boxStatus"]["state"], bs.ABSENT, result["boxStatus"])
        for data in result["methods"].values():
            self.assertEqual(len(data["samplesMs"]), 2)
        self.assertIn("p95", perf.summary(result))


if __name__ == "__main__":
    unittest.main()
