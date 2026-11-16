"""Testy raportu „po” K-125 (PT-489): werdykt progu po oknach, udział `outside`, reguła rekomendacji dla #5, porównanie.

Nie mierzą czasu — działają na wynikach syntetycznych."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from . import perf_k105_queue as queue
from . import perf_k125_after as after
from . import perf_k125_report as base
from .test_perf_k125_report import attempt, document


def doc(box, windows=None, **kwargs):
    d = document(box, **kwargs)
    d["side"] = "after"
    if windows is not None:
        item = d["attempts"][0]
        item["evaluation"]["windowsPercent"] = windows
        item["result"]["cpuWindows"] = {"windows": [{"index": i + 1, "cpuPercent": w} for i, w in enumerate(windows)]}
    return d


def windows(top=12.0, last=None):
    values = [top] * 10
    if last is not None:
        values[9] = last
    return values


def before_report():
    return base.build({"false": document(False, mean=17.0), "true": document(True, mean=17.0)})


class ThresholdTest(unittest.TestCase):
    def build(self, off, on, **kwargs):
        return after.build({"false": doc(False, windows=off, **kwargs), "true": doc(True, windows=on, **kwargs)},
                           before_report())

    def test_pass_only_when_every_window_in_both_modes_is_within_the_limit(self):
        self.assertEqual(self.build(windows(30.0), windows(12.0))["threshold"], "pass")

    def test_one_window_over_the_limit_fails_even_with_a_low_mean(self):
        data = self.build(windows(5.0, last=30.01), windows(5.0))
        self.assertEqual(data["threshold"], "fail")
        data = self.build(windows(5.0), windows(5.0, last=45.0))
        self.assertEqual(data["threshold"], "fail")

    def test_mode_without_a_valid_attempt_is_invalid_not_a_pass(self):
        invalid = doc(True, windows=windows(5.0))
        invalid["attempts"][0]["status"] = "invalid"
        data = after.build({"false": doc(False, windows=windows(5.0)), "true": invalid}, before_report())
        self.assertEqual(data["threshold"], "invalid")
        self.assertFalse(data["criteria"]["bothModesValid"])

    def test_incomplete_window_series_is_invalid(self):
        data = self.build(windows(5.0)[:9], windows(5.0))
        self.assertEqual(data["threshold"], "invalid")


class RecommendationTest(unittest.TestCase):
    def rec(self, threshold, off, on):
        return after.recommendation_of(threshold, {"false": off, "true": on})

    def test_pass_rejects_5_and_6_whatever_the_outside_share(self):
        self.assertEqual(self.rec("pass", 0.5, 0.5), "#5 i #6 odrzucić")

    def test_fail_with_outside_at_ten_percent_in_either_mode_needs_5(self):
        self.assertEqual(self.rec("fail", 0.10, 0.0), "#5 potrzebny")
        self.assertEqual(self.rec("fail", 0.0, 0.1), "#5 potrzebny")

    def test_fail_with_cost_elsewhere_is_a_question(self):
        self.assertIn("question", self.rec("fail", 0.0999, 0.02))
        self.assertIn("question", self.rec("fail", None, 0.02))

    def test_invalid_is_not_a_recommendation_for_5(self):
        self.assertIn("brak ważnego pomiaru", self.rec("invalid", 0.9, 0.9))


class ReportTest(unittest.TestCase):
    def test_outside_share_per_mode_comes_from_the_recorded_samples(self):
        data = after.build({"false": doc(False, windows=windows(), outside=50),
                            "true": doc(True, windows=windows(), outside=100)}, before_report())
        self.assertEqual(data["outsideShare"], {"false": 0.1, "true": 0.2})
        self.assertEqual(data["recommendation"], "#5 i #6 odrzucić")        # pass: nic nie wymaga #5

    def test_comparison_has_before_and_after_for_each_metric(self):
        data = after.build({"false": doc(False, windows=windows(), calls=60), "true": doc(True, windows=windows())},
                           before_report())
        cmp = data["comparison"]["false"]
        self.assertEqual(cmp["systemctlCallsInCpuWindow"], {"before": 130, "after": 60})
        self.assertEqual(cmp["componentShareOfTick"]["outside"], {"before": 0.4, "after": 0.4})
        self.assertEqual(cmp["snapshotP95Ms"], {"before": 800.0, "after": 800.0})
        self.assertTrue(cmp["sameRoles"])
        self.assertEqual(data["beforeSha"], "b" * 40)
        json.dumps(data)

    def test_snapshot_limit_is_checked_per_mode(self):
        slow = doc(True, windows=windows())
        slow["attempts"][0]["result"]["snapshot"] = {"p95Ms": 1000.0}
        data = after.build({"false": doc(False, windows=windows()), "true": slow}, before_report())
        self.assertEqual(data["criteria"]["snapshotP95UnderOneSecond"], {"false": True, "true": False})

    def test_frame_samples_match_the_function_name_in_any_module(self):
        text = "## Funkcje — czas łączny (próbki)\n   40  engine.py:_resume_step\n   25  store.py:_inside\n" \
               "   9  other.py:_inside\n   7  engine.py:tick\n## Funkcje — czas własny (próbki)\n   99  x.py:_inside\n"
        self.assertEqual(after.frame_samples(text),
                         {"_resume_step": {"engine.py:_resume_step": 40},
                          "_inside": {"store.py:_inside": 25, "other.py:_inside": 9}})

    def test_main_writes_the_file_and_exit_code_signals_only_missing_results(self):
        root = tempfile.mkdtemp(prefix="tide-k125-after-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        out, before = os.path.join(root, "docs", "po.json"), os.path.join(root, "przed.json")
        queue.write_json(before, before_report())

        def put(box, value):
            queue.write_json(queue.result_path("after", box, root), value)
        args = ["--dir", root, "--before", before, "--out", out]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(after.main(args), 1)
            put(False, doc(False, windows=windows()))
            put(True, doc(True, windows=windows(40.0)))
            self.assertEqual(after.main(args), 0)                       # fail to wynik pomiaru, nie błąd
        with open(out, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["threshold"], "fail")
        self.assertIn("outsideShare", saved)


if __name__ == "__main__":
    unittest.main()
