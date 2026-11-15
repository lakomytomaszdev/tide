"""Testy kryteriów i raportu „przed” K-125 (PT-486): progi profilu, czerwony wynik, składanie pliku z wyników kolejki.

Nie mierzą czasu — działają na wynikach syntetycznych."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from . import perf_idle_cpu as perf
from . import perf_idle_scale as scale
from . import perf_k105_queue as queue
from . import perf_k125_report as report

FRAMES = """## Funkcje — czas łączny (próbki)
   1000  threading.py:run
    500  engine.py:tick
    200  engine.py:_consent_recheck
     50  engine.py:_plan_next
     60  trialhold.py:inventory
"""


def profile(tick=500, outside=200, plan=50, inventory=60, busy=1000):
    text = f"Próbek poza ramkami rozpoznanymi jako czekanie: {busy}\n\n## Funkcje — czas łączny (próbki)\n" \
           f"   {tick}  engine.py:tick\n   {outside}  engine.py:_consent_recheck\n   {plan}  engine.py:_plan_next\n" \
           f"   {inventory}  trialhold.py:inventory\n"
    return {"split": perf.parse_profile(text), "report": FRAMES}


def attempt(mean=45.0, box=False, calls=130, **kwargs):
    result = {"sha": "b" * 40, "params": {"box": box, "windows": 10}, "cpuCount": 16, "boxEnabled": box,
              "loadavgBefore": "0.5 0.5 0.5 1/2 3", "loadavgWindowStart": "0.5 0 0 1/2 3",
              "loadavgEnd": "0.6 0 0 1/2 3", "loadavgAfter": "0.6 0 0 1/2 3",
              "cpuWindows": {"windows": [{"index": i, "cpuPercent": mean} for i in range(1, 11)]},
              "profileWindow": profile(**kwargs), "tick": {"share": 0.5, "perSecond": 8.0},
              "fakeSystemctl": perf.systemctl_stats(500, 100, 100 + calls, 600.0),
              "snapshot": {"p95Ms": 800.0}, "scale": {"disk": {"cards": 46}}}
    evaluation = {"windowsPercent": [mean] * 10, "meanPercent": mean, "maxPercent": mean, "limitPercent": 30.0,
                  "windowsOverLimit": [], "verdict": "fail" if mean > 30 else "pass"}
    return {"n": 1, "status": "valid", "startedAt": "t0", "finishedAt": "t1", "result": result,
            "evaluation": evaluation}


def document(box, **kwargs):
    return {"side": "before", "boxEnabled": box, "baseSha": "b" * 40, "decisive": 1,
            "attempts": [attempt(box=box, **kwargs)]}


class ProfileCheckTest(unittest.TestCase):
    def names(self, **kwargs):
        criteria = scale.profile_check(attempt(**kwargs)["result"])
        return {c["name"]: c["ok"] for c in criteria}

    def test_production_order_of_magnitude_passes_every_criterion(self):
        self.assertEqual(self.names(), {"tick": True, "outside": True, "plan": True, "inventory": True,
                                        "systemctlPerTick": True})
        self.assertTrue(scale.profile_ok(scale.profile_check(attempt()["result"])))

    def test_each_threshold_is_checked_on_its_own(self):
        self.assertFalse(self.names(tick=300)["tick"])                          # 0,30 < 0,40
        self.assertTrue(self.names(tick=400)["tick"])                           # 0,40 — próg włącznie
        self.assertFalse(self.names(outside=100)["outside"])                    # 0,20 < 0,30
        self.assertFalse(self.names(outside=300)["outside"])                    # 0,60 > 0,50
        self.assertTrue(self.names(outside=150)["outside"])                     # 0,30 włącznie
        self.assertTrue(self.names(outside=250)["outside"])                     # 0,50 włącznie
        self.assertFalse(self.names(plan=20)["plan"])                           # 0,04 < 0,05
        self.assertTrue(self.names(plan=25)["plan"])
        self.assertFalse(self.names(inventory=24)["inventory"])
        self.assertTrue(self.names(inventory=25)["inventory"])
        self.assertFalse(self.names(calls=100)["systemctlPerTick"])             # 100 wywołań w 120 tickach
        self.assertTrue(self.names(calls=120)["systemctlPerTick"])

    def test_missing_measurement_is_not_a_pass(self):
        criteria = scale.profile_check({})
        self.assertTrue(all(c["ok"] is None for c in criteria))
        self.assertFalse(scale.profile_ok(criteria))

    def test_thresholds_are_the_ones_from_the_card(self):
        self.assertEqual((scale.RED_MEAN_PERCENT, scale.TICK_MIN_SHARE, scale.OUTSIDE_TICK_SHARE,
                          scale.PLAN_MIN_TICK_SHARE, scale.INVENTORY_MIN_TICK_SHARE),
                         (30.0, 0.40, (0.30, 0.50), 0.05, 0.05))


class ReportTest(unittest.TestCase):
    def test_report_has_both_modes_with_sha_loadavg_split_calls_and_snapshot(self):
        data = report.build({"false": document(False), "true": document(True, mean=31.0)})
        self.assertEqual(data["baseSha"], "b" * 40)
        for key in ("false", "true"):
            block = data["byBoxEnabled"][key]["firstValid"]
            self.assertEqual(block["sha"], "b" * 40)
            self.assertEqual(set(block["loadavg"]), {"before", "windowStart", "end", "after"})
            self.assertEqual(len(block["windowsPercent"]), 10)
            self.assertEqual(set(block["profile"]["components"]), {"outside", "plan", "inventory"})
            self.assertEqual(block["profile"]["components"]["outside"]["shareOfTick"], 0.4)
            self.assertEqual(block["fakeSystemctl"]["callsInCpuWindow"], 130)
            self.assertEqual(block["snapshot"]["p95Ms"], 800.0)
            self.assertEqual(block["profile"]["topFrames"][1], {"samples": 500, "frame": "engine.py:tick"})
        self.assertTrue(data["criteria"]["allMet"])
        self.assertEqual(data["criteria"]["red"]["meanPercentByBox"], {"false": 45.0, "true": 31.0})
        json.dumps(data)

    def test_red_needs_a_mean_above_thirty_in_at_least_one_mode(self):
        one = report.build({"false": document(False, mean=12.0), "true": document(True, mean=30.01)})
        self.assertTrue(one["criteria"]["red"]["ok"])
        none = report.build({"false": document(False, mean=12.0), "true": document(True, mean=30.0)})
        self.assertFalse(none["criteria"]["red"]["ok"])
        self.assertFalse(none["criteria"]["allMet"])

    def test_missing_road_in_one_mode_fails_the_profile_and_is_listed(self):
        data = report.build({"false": document(False), "true": document(True, plan=10)})
        self.assertFalse(data["criteria"]["allMet"])
        self.assertEqual(data["criteria"]["profile"]["failedByBox"], {"false": [], "true": ["plan"]})

    def test_mode_without_a_valid_attempt_is_missing_not_passed(self):
        invalid = document(True)
        invalid["attempts"][0]["status"] = "invalid"
        data = report.build({"false": document(False), "true": invalid})
        self.assertIsNone(data["byBoxEnabled"]["true"])
        self.assertFalse(data["criteria"]["bothModesValid"])
        self.assertFalse(data["criteria"]["allMet"])

    def test_main_writes_the_file_and_exit_codes_distinguish_missing_and_unmet(self):
        root = tempfile.mkdtemp(prefix="tide-k125-report-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        out = os.path.join(root, "docs", "pomiar.json")

        def put(box, doc):
            queue.write_json(queue.result_path("before", box, root), doc)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(report.main(["--dir", root, "--out", out]), 1)             # brak wyników
            put(False, document(False))
            put(True, document(True))
            self.assertEqual(report.main(["--dir", root, "--out", out]), 0)
            with open(out, encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertTrue(saved["criteria"]["allMet"])
            put(True, document(True, outside=10))
            self.assertEqual(report.main(["--dir", root, "--out", out]), 2)


if __name__ == "__main__":
    unittest.main()
