import unittest

from tide_daemon.cardflow import labels

ALL_KINDS = ("planning", "plan-import", "plan-fix", "plan-review", "plan-revise", "plan-amend",
             "ticket", "review", "fix", "conflict", "close", "interactive")
ALL_RESULTS = ("done", "question", "denied", "error", "stopped")


class LabelsTest(unittest.TestCase):
    def test_every_kind_and_result_has_polish_label(self):
        for kind in ALL_KINDS:
            self.assertIn(kind, labels.KIND_LABELS)
            self.assertNotEqual(labels.kind_label(kind), labels.GENERIC_KIND, kind)
        for result in ALL_RESULTS:
            self.assertIn(result, labels.RESULT_LABELS)
            self.assertNotEqual(labels.result_label(result), labels.GENERIC_RESULT, result)
        self.assertEqual(set(labels.KIND_LABELS), set(ALL_KINDS))
        self.assertEqual(set(labels.RESULT_LABELS), set(ALL_RESULTS))

    def test_named_values(self):
        self.assertEqual(labels.kind_label("planning"), "planowanie")
        self.assertEqual(labels.kind_label("plan-review"), "review planu")
        self.assertEqual(labels.kind_label("plan-revise"), "poprawa planu")
        self.assertEqual(labels.kind_label("fix"), "poprawki")
        self.assertEqual(labels.kind_label("close"), "domknięcie")
        self.assertEqual(labels.result_label("done"), "gotowe")
        self.assertEqual(labels.result_label("stopped"), "zatrzymany")

    def test_unknown_or_empty_gives_generic_label(self):
        for value in ("tajny-krok", "", None, 5, ["x"]):
            self.assertEqual(labels.kind_label(value), labels.GENERIC_KIND)
            self.assertEqual(labels.result_label(value), labels.GENERIC_RESULT)

    def test_waiting_text_follows_kind(self):
        self.assertEqual(labels.waiting_text("plan-review"), "review planu czeka na decyzję")
        self.assertEqual(labels.waiting_text("plan-amend"), "poprawa planu czeka na decyzję")
        self.assertEqual(labels.waiting_text("nieznany"), "krok czeka na decyzję")


if __name__ == "__main__":
    unittest.main()
