"""Dym reproduktora skali produkcji K-98 (`perf_scale_k98.py`, PT-362) i jego oceny (`perf_scale_k98_eval.py`).

Nie mierzy czasu i nie uruchamia usługi: sprawdza budowę syntetycznych danych w małej skali (izolowane środowisko,
liczenie skali na dysku), odczyt raportu próbnika usługi i progi oceny „przed”/„po” na wynikach syntetycznych."""
import json
import os
import shutil
import tempfile
import unittest
from collections import Counter

from . import perf_scale_k98 as perf
from . import perf_scale_k98_eval as evaluation


class HelpersTest(unittest.TestCase):
    def test_nearest_rank_p95(self):
        self.assertEqual(perf.nearest_rank(list(range(1, 121))), 114)
        self.assertIsNone(perf.nearest_rank([]))

    def test_defaults_meet_production_scale_on_paper(self):
        d = perf.defaults()
        self.assertGreaterEqual(d["cards"], perf.PRODUCTION["cards"])
        self.assertGreaterEqual(d["sessions"], perf.PRODUCTION["sessions"])
        self.assertGreaterEqual(d["worktrees"], perf.PRODUCTION["worktrees"])
        # Katalogi: karta + steps + kroki z in/out; pliki: kroki × STEP_FILES (bez docs, kart i dzienników).
        self.assertGreaterEqual(d["cards"] * (2 + 3 * d["steps"]), perf.PRODUCTION["dirs"])
        self.assertGreaterEqual(d["cards"] * d["steps"] * len(perf.STEP_FILES), 12_000)
        self.assertEqual((d["duration"], d["period"]), (60.0, 0.5))
        self.assertGreater(d["journal_every"], 2.0, "seria zmian musi kończyć się ciszą dłuższą niż DEBOUNCE_MS")

    def test_profile_report_of_the_service_sampler_is_parsed(self):
        from tide_daemon import sampler
        tmp = tempfile.mkdtemp(prefix="tide-perf-scale-test-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        stack = "board_monitor.py:sync > board_monitor.py:_rewatch > os.py:walk"
        result = {"samples": 200, "stacks": Counter({("MainThread", stack): 40}),
                  "own": Counter({"os.py:walk": 40}),
                  "total": Counter({"board_monitor.py:sync": 40, "board_monitor.py:_rewatch": 40, "os.py:walk": 40,
                                    "scanner.py:sessions": 10}),
                  "idle": Counter({("MainThread", "app.py:exec:1"): 150})}
        path = sampler.write_report(result, 2.0, tmp)
        with open(path, encoding="utf-8") as fh:
            parsed = perf.parse_profile(fh.read())
        self.assertEqual(parsed["samples"], 200)
        self.assertEqual(parsed["total"]["board_monitor.py:_rewatch"], 40)
        self.assertEqual(parsed["stacks"], [[40, "MainThread", stack]])
        shares = perf.shares(parsed)
        self.assertEqual(shares["board_monitor.py:_rewatch"], 0.2)
        self.assertEqual(shares["scanner.py:sessions"], 0.05)
        self.assertEqual(shares["gitinfo.py:describe"], 0.0)


class BuildSmokeTest(unittest.TestCase):
    def test_small_build_is_isolated_and_counts_scale_on_disk(self):
        before = dict(os.environ)
        params = {**perf.defaults(), "cards": 3, "steps": 2, "sessions": 4, "worktrees": 3, "build_only": True}
        result = perf.measure(params, sha="test")
        self.assertEqual(dict(os.environ), before)
        self.assertFalse(os.path.exists(result["isolatedBase"]), "katalog danych pomiaru został")
        scale = result["scale"]
        self.assertEqual((scale["cards"], scale["sessions"], scale["worktrees"]), (3, 4, 3))
        self.assertEqual(scale["stepDirs"], 3 * (1 + 2 * 3))           # steps/ + kroki z in/ i out/
        self.assertEqual(scale["stepFiles"], 3 * 2 * len(perf.STEP_FILES))
        self.assertGreater(scale["files"], scale["stepFiles"])
        self.assertTrue(result["belowProduction"], "mała skala musi być oznaczona jako poniżej produkcji")


def _result(p95, peak, rewatch, cpu=5.0, scan=0.0, below=None):
    methods = {m: {"n": 120, "p95Ms": p95, "maxMs": peak, "samplesMs": [p95]} for m in perf.METHODS}
    shares = {"board_monitor.py:_rewatch": rewatch, "scanner.py:sessions": scan, "gitinfo.py:describe": scan / 2}
    return {"scale": {}, "belowProduction": below or [],
            "phases": [{"phase": "load", "seconds": 60, "methods": methods, "errors": [],
                        "profile": {"shares": shares}, "cpu": {"percent": 40.0}},
                       {"phase": "idle", "seconds": 60, "profile": {"shares": shares}, "cpu": {"percent": cpu}}]}


class EvaluationTest(unittest.TestCase):
    def test_reproduce_needs_slow_p95_or_rewatch_share(self):
        self.assertTrue(evaluation.reproduce(_result(3400.0, 9700.0, 0.01))[1])
        self.assertTrue(evaluation.reproduce(_result(80.0, 200.0, 0.12))[1])
        self.assertFalse(evaluation.reproduce(_result(80.0, 200.0, 0.05))[1])
        self.assertFalse(evaluation.reproduce(_result(3400.0, 9700.0, 0.5, below=["dirs 10 < 4000"]))[1])

    def _gate(self, after, before=None, cpu_off=5.0):
        before = before or _result(3400.0, 9700.0, 0.4)
        off = {**after, "phases": [after["phases"][0], {**after["phases"][1], "cpu": {"percent": cpu_off}}]}
        refresh = {"refresh": {"samplesMs": [10.0] * 30, "boardLoads": [1] * 30},
                   "methods": {m: {"samplesMs": [1.0] * 100} for m in ("Snapshot", "GetCardFlowSettings", "BoxStatus")}}
        tmp = tempfile.mkdtemp(prefix="tide-perf-scale-eval-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        paths = {}
        for name, data in (("before", before), ("on", after), ("off", off), ("ri", refresh), ("rb", refresh)):
            paths[name] = os.path.join(tmp, f"{name}.json")
            with open(paths[name], "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        code = evaluation.main(["gate", "--before", paths["before"], "--on", paths["on"], "--off", paths["off"],
                                "--refresh-idle", paths["ri"], "--refresh-busy", paths["rb"], "--out", tmp])
        with open(os.path.join(tmp, "gate.json"), encoding="utf-8") as fh:
            return code, json.load(fh)

    def test_gate_passes_fast_answers_without_rewatch(self):
        code, report = self._gate(_result(40.0, 300.0, 0.0))
        self.assertEqual(code, 0, report)
        self.assertFalse(report["conditional"])

    def test_gate_conditional_when_cost_is_in_scanner(self):
        code, report = self._gate(_result(1200.0, 2500.0, 0.01, scan=0.3))
        self.assertEqual(code, 0, report)
        self.assertTrue(report["conditional"])
        self.assertIn("zablokowana", report["verdict"])

    def test_gate_fails_on_rewatch_share_idle_cpu_or_small_drop(self):
        self.assertEqual(self._gate(_result(40.0, 300.0, 0.03))[0], 1)
        self.assertEqual(self._gate(_result(40.0, 300.0, 0.0), cpu_off=31.0)[0], 1)
        self.assertEqual(self._gate(_result(2000.0, 2500.0, 0.01, scan=0.3))[0], 1)   # spadek p95 < 50 %


if __name__ == "__main__":
    unittest.main()
