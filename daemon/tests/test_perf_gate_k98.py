"""Ocena bramki K-98 (PT-345, `perf_gate_k98_eval.py`) na syntetycznych próbkach: progi bez obniżania, brak ważnej
próby wstrzymuje odbiór, a skrypt pomiaru pierwszego wywołania liczy p95 nearest-rank. Bez pomiaru czasu."""
import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load("perf_gate_k98_eval")
first_call = _load("perf_dbus_first_call")


def refresh(samples=None, loads=None, methods=None):
    samples = samples if samples is not None else [100.0] * 30
    return {"sha": "x", "coldStart": {"ms": 900.0, "boardLoads": 1},
            "refresh": {"samplesMs": samples, "boardLoads": loads if loads is not None else [1] * len(samples)},
            "methods": methods or {m: {"samplesMs": [5.0] * 100} for m in gate.GATED_METHODS}}


class GateEvalTest(unittest.TestCase):
    def write(self, base, rel, data):
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def build(self, head=None, busy=None, first_after=None):
        base = tempfile.mkdtemp(prefix="tide-gate-eval-")
        self.addCleanup(__import__("shutil").rmtree, base, True)
        d = "attempt-1"
        for scenario, data in (("refresh-idle", head), ("refresh-busy", busy)):
            self.write(base, f"{d}/{scenario}/head.json", data or refresh())
            self.write(base, f"{d}/{scenario}/base.json", refresh(samples=[700.0] * 30))   # baza nie spełnia — jawnie
        for i in range(1, 6):
            for side in ("base", "head"):
                self.write(base, f"{d}/cold-{i}/{side}.json", refresh(samples=[1.0]))
        self.write(base, f"{d}/first-call-base.json", {"firstCallMs": [50.0] * 20, "errors": []})
        self.write(base, f"{d}/first-call-head.json", first_after or {"firstCallMs": [20.0] * 20, "errors": []})
        return base

    def run_eval(self, base, attempt=1):
        args = [base] + (["--attempt", str(attempt)] if attempt else [])
        code = gate.main(args)
        with open(os.path.join(base, "gate.json"), encoding="utf-8") as fh:
            return code, json.load(fh)

    def test_all_thresholds_met_while_the_base_fails_openly(self):
        code, report = self.run_eval(self.build())
        self.assertEqual(code, 0, report)
        self.assertTrue(all(c["ok"] for c in report["candidate"]))
        self.assertFalse(all(c["ok"] for c in report["base"]), "wynik bazy poza progiem jest jawny")
        self.assertEqual(len(report["cold"]), 10)

    def test_one_slow_refresh_fails_the_gate(self):
        code, _ = self.run_eval(self.build(head=refresh(samples=[100.0] * 29 + [500.1])))
        self.assertEqual(code, 1)

    def test_exactly_500_ms_passes_and_29_refreshes_fail(self):
        self.assertEqual(self.run_eval(self.build(head=refresh(samples=[500.0] * 30)))[0], 0)
        self.assertEqual(self.run_eval(self.build(head=refresh(samples=[1.0] * 29)))[0], 1)

    def test_two_board_loads_fail(self):
        self.assertEqual(self.run_eval(self.build(busy=refresh(loads=[1] * 29 + [2])))[0], 1)

    def test_method_p95_is_nearest_rank_and_strictly_below_one_second(self):
        # 100 próbek: p95 = 95. próbka po posortowaniu. 6 wolnych → 95. wolna; 5 wolnych → 95. szybka.
        slow = {m: {"samplesMs": [5.0] * 94 + [1000.0] * 6} for m in gate.GATED_METHODS}
        self.assertEqual(self.run_eval(self.build(busy=refresh(methods=slow)))[0], 1)
        ok = {m: {"samplesMs": [5.0] * 95 + [5000.0] * 5} for m in gate.GATED_METHODS}
        self.assertEqual(self.run_eval(self.build(busy=refresh(methods=ok)))[0], 0)

    def test_first_call_threshold_and_errors(self):
        self.assertEqual(self.run_eval(self.build(first_after={"firstCallMs": [10.0] * 18 + [1000.0] * 2,
                                                               "errors": []}))[0], 1)
        self.assertEqual(self.run_eval(self.build(first_after={"firstCallMs": [10.0] * 19 + [5000.0],
                                                               "errors": []}))[0], 0)
        self.assertEqual(self.run_eval(self.build(first_after={"firstCallMs": [10.0] * 20,
                                                               "errors": [{"call": "Sessions"}]}))[0], 1)

    def test_no_valid_attempt_holds_the_gate(self):
        code, report = self.run_eval(self.build(), attempt=None)
        self.assertEqual(code, 2)
        self.assertIn("wstrzymany", report["verdict"])

    def test_first_call_script_uses_nearest_rank(self):
        self.assertEqual(first_call.nearest_rank([float(i) for i in range(1, 21)]), 19.0)
        self.assertIsNone(first_call.nearest_rank([]))


if __name__ == "__main__":
    unittest.main()
