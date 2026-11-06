"""Odczyt scenariusza review dopiero po domknięciu, niezależnie od kolejności callbacków Qt."""
import json
import os
import tempfile
import unittest
from unittest import mock

from PyQt6.QtCore import QTimer

from .privatebus import app
from . import test_cardflow_review


class ReviewCompletionSyncTest(unittest.TestCase):
    def test_rounds_scenario_waits_when_review_column_precedes_close(self):
        app()
        case = test_cardflow_review.ReviewFlowTest("test_two_request_changes_rounds_then_approve")
        case.master = "a" * 40
        kinds = ["ticket", "review", "fix", "review", "fix", "review"]
        with tempfile.TemporaryDirectory(prefix="tide-review-sync-") as step_dir:
            os.mkdir(os.path.join(step_dir, "out"))
            with open(os.path.join(step_dir, "out", "fake-call.json"), "w", encoding="utf-8") as fh:
                json.dump({"prompt": f"MAJOR: popraw ATRAPA.md\nmake test-changed BASE={case.master}"}, fh)
            steps = [{"kind": kind, "stepDir": step_dir} for kind in kinds]
            card = {"boardColumn": "review", "derived": {"flow": {"closed": False}}}
            verdicts = [{"round": n, "verdict": "REQUEST_CHANGES" if n < 3 else "APPROVE",
                         "majors": 1 if n < 3 else 0} for n in range(1, 4)]
            timer = QTimer()
            timer.setSingleShot(True)

            def close():
                steps.append({"kind": "close"})
                card["derived"]["flow"]["closed"] = True

            timer.timeout.connect(close)

            def review_column(*args, **kwargs):
                # Ten sam stan przejściowy co w bramce: merge jest gotowy, close jeszcze nie ruszył.
                timer.start(30)
                return card

            def events(_card, kind):
                return verdicts if kind == "review.verdict" else steps

            try:
                with mock.patch.object(case, "card", return_value="K-1"), \
                        mock.patch.object(case, "start", return_value={"ok": True}), \
                        mock.patch.object(case, "wait_column", side_effect=review_column), \
                        mock.patch.object(case, "shown", return_value=card), \
                        mock.patch.object(case, "of", side_effect=events), \
                        mock.patch.object(case, "results", return_value=[{"ok": True}]):
                    case.test_two_request_changes_rounds_then_approve()
                self.assertTrue(card["derived"]["flow"]["closed"])
            finally:
                timer.stop()
