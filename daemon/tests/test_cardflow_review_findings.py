"""PT-127: uwagi review planu w formacie `findings[{severity, text, tickets}]` i zgodność ze starym napisem."""
import json
import os
import unittest

from tide_daemon.cardflow import findings

from . import test_cardflow_plan_integration as fixtures
from .privatebus import wait_until
from .test_cardflow import FlowInProcess


class NormalizeTest(unittest.TestCase):
    def test_new_format_is_accepted(self):
        raw = [{"severity": "major", "text": " Rozdziel fale. ", "tickets": [" #5", "PT-42"]},
               {"severity": "nit", "text": "Drobiazg."}]
        self.assertEqual(findings.normalize(raw), [
            {"severity": "major", "text": "Rozdziel fale.", "tickets": ["#5", "PT-42"]},
            {"severity": "nit", "text": "Drobiazg.", "tickets": []}])

    def test_old_string_becomes_one_finding_without_severity(self):
        self.assertEqual(findings.normalize("Uzupełnij Dla recenzenta."),
                         [{"severity": "", "text": "Uzupełnij Dla recenzenta.", "tickets": []}])

    def test_empty_means_no_findings(self):
        for empty in ("", "  ", None, []):
            self.assertEqual(findings.normalize(empty), [])

    def test_malformed_findings_are_rejected_with_a_readable_reason(self):
        cases = [
            (42, "listą uwag albo napisem"),
            (["tekst"], "uwaga 1: oczekiwano obiektu"),
            ([{"severity": "krytyczna", "text": "x"}], "uwaga 1: severity musi być jedną z blocker, major, nit"),
            ([{"text": "x"}], "uwaga 1: severity"),
            ([{"severity": "nit", "text": "ok"}, {"severity": "major", "text": "  "}], "uwaga 2: text musi być niepustym"),
            ([{"severity": "major", "text": 5}], "uwaga 1: text musi być niepustym"),
            ([{"severity": "major", "text": "x", "tickets": "#1"}], "uwaga 1: tickets musi być listą"),
            ([{"severity": "major", "text": "x", "tickets": [1]}], "uwaga 1: tickets musi być listą"),
        ]
        for raw, reason in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError) as error:
                    findings.normalize(raw)
                self.assertIn(reason, str(error.exception))

    def test_stored_events_never_break_the_card_state(self):
        self.assertEqual(findings.lenient([{"severity": "krytyczna", "text": "x"}]), [])
        self.assertEqual(findings.lenient(42), [])
        self.assertEqual(findings.lenient([{"severity": "", "text": "x", "tickets": []}]),
                         [{"severity": "", "text": "x", "tickets": []}])

    def test_selection_defaults_exclude_nits_but_never_leave_nothing(self):
        mixed = findings.normalize([{"severity": "nit", "text": "a"}, {"severity": "major", "text": "b"},
                                    {"severity": "blocker", "text": "c"}])
        self.assertEqual([f["text"] for f in findings.selection(mixed)], ["b", "c"])
        only_nits = findings.normalize([{"severity": "nit", "text": "a"}])
        self.assertEqual([f["text"] for f in findings.selection(only_nits)], ["a"])
        self.assertTrue(findings.default_selected(findings.normalize("stary napis")[0]))


class ReviewViewTest(unittest.TestCase):
    def test_approve(self):
        view = findings.review_view({"verdict": "APPROVE", "reviewer": "Sonnet", "findings": []})
        self.assertEqual((view["verdict"], view["reviewer"], view["findings"]), ("APPROVE", "Sonnet", []))

    def test_request_changes_with_new_and_old_formats(self):
        new = findings.review_view({"verdict": "REQUEST_CHANGES",
                                    "findings": [{"severity": "major", "text": "x", "tickets": ["#1"]}]})
        self.assertEqual((new["verdict"], new["findings"]),
                         ("REQUEST_CHANGES", [{"severity": "major", "text": "x", "tickets": ["#1"]}]))
        old = findings.review_view({"verdict": "REQUEST_CHANGES", "findings": "stare uwagi"})
        self.assertEqual(old["findings"], [{"severity": "", "text": "stare uwagi", "tickets": []}])

    def test_missing_or_unknown_verdict_is_empty(self):
        for event in ({"findings": [{"severity": "nit", "text": "x"}]}, {"verdict": "MOŻE", "findings": []}):
            self.assertEqual(findings.review_view(event)["verdict"], "")
        self.assertEqual(findings.review_view(None), {})


class StructuredReviewFlowTest(FlowInProcess):
    proposal = fixtures.PlanFlowTest.proposal
    submit = fixtures.PlanFlowTest.submit
    approve = fixtures.PlanFlowTest.approve

    def prompt_of(self, step):
        with open(os.path.join(step["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            return json.load(fh)["prompt"]

    def test_review_prompt_describes_the_new_format(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        review = next(s for s in self.of(card, "step.started") if s["kind"] == "plan-review")
        prompt = self.prompt_of(review)
        for expected in ('"findings":[{"severity"', "blocker", "major", "nit", '"tickets"'):
            self.assertIn(expected, prompt)

    def test_old_string_result_still_works_and_shows_one_finding(self):
        card = self.card(description="ATRAPA: plan-review-changes")
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        review = self.wait_column(card, "waiting")["derived"]["flow"]["plan"]["review"]
        self.assertEqual(review["verdict"], "REQUEST_CHANGES", (self.of(card, "plan.reviewed"), review))
        self.assertEqual(review["findings"], [{"severity": "", "text": "Uzupełnij Dla recenzenta.", "tickets": []}],
                         self.of(card, "plan.reviewed"))

    def test_structured_result_reaches_the_card_and_the_automatic_revision_skips_nits(self):
        card = self.card(description="ATRAPA: plan-review-structured")
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        review = self.wait_column(card, "waiting")["derived"]["flow"]["plan"]["review"]
        self.assertEqual([f["severity"] for f in review["findings"]], ["blocker", "major", "nit"])
        self.assertEqual(review["findings"][0]["tickets"], ["#1"])
        revise = next(s for s in self.of(card, "step.started") if s["kind"] == "plan-revise")
        prompt = self.prompt_of(revise)
        self.assertIn("Pierwszy plasterek nie ma mierzalnego kryterium", prompt)
        self.assertIn("[major] Rozdziel pierwszy plasterek. (tickety: #1)", prompt)
        self.assertNotIn("Drobiazg w nazwie drugiego", prompt)

    def test_malformed_result_is_not_recorded_as_a_review(self):
        card = self.card(description="ATRAPA: plan-review-malformed")
        self.flow.stop()
        self.flow.engine.propose_plan(card, self.proposal(card))
        self.flow.start()
        self.assertTrue(wait_until(lambda: [s for s in self.of(card, "step.finished") if s.get("ticket") == ""],
                                   timeout=20), self.events(card))
        wait_until(lambda: False, timeout=.5)
        self.assertEqual(self.of(card, "plan.reviewed"), [])
        self.assertEqual([s["kind"] for s in self.of(card, "step.started")], ["plan-review"])

    def test_approving_despite_blockers_works(self):
        card = self.card(description="ATRAPA: plan-review-structured")
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.wait_column(card, "waiting")
        self.assertTrue(self.approve(card)["ok"])
        self.assertTrue(self.of(card, "plan.approved"))

    def test_revision_prompt_contains_only_selected_findings_and_own_notes(self):
        card = self.card()
        engine = self.flow.engine
        selected = findings.normalize([{"severity": "major", "text": "Zaznaczona uwaga.", "tickets": ["#5", "#7"]}])
        prompt = engine._revision_prompt(card, {"rows": []}, selected, "Własna uwaga użytkownika.")
        self.assertIn("- [major] Zaznaczona uwaga. (tickety: #5, #7)", prompt)
        self.assertIn("Uwagi użytkownika:\nWłasna uwaga użytkownika.", prompt)
        self.assertNotIn("Niezaznaczony drobiazg", prompt)
        only_own = engine._revision_prompt(card, {"rows": []}, [], "Tylko moje słowa.")
        self.assertIn("Uwagi: Tylko moje słowa.", only_own)

    def test_returned_selected_findings_and_own_text_are_the_only_feedback(self):
        card = self.card(description="ATRAPA: plan-review-structured")
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.wait_column(card, "waiting")
        wait_until(lambda: self.of(card, "plan.revision-ready") or True, timeout=.2)
        box = []
        self.flow.edit_plan(card, json.dumps({
            "action": "return", "feedback": "#2 zostaw w fali 2.",
            "findings": [{"severity": "major", "text": "Rozdziel pierwszy plasterek.", "tickets": ["#1"]}]}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        returned = self.of(card, "plan.returned")[-1]["feedback"]
        self.assertIn("- [major] Rozdziel pierwszy plasterek. (tickety: #1)", returned)
        self.assertIn("#2 zostaw w fali 2.", returned)
        self.assertNotIn("Drobiazg", returned)
        self.assertNotIn("mierzalnego", returned)

    def test_return_without_findings_or_text_is_rejected(self):
        from tide_daemon.cardflow.engine import FlowError
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        for request in ({"action": "return"}, {"action": "return", "findings": [], "feedback": " "},
                        {"action": "return", "findings": [{"severity": "zła", "text": "x"}]}):
            with self.subTest(request=request):
                with self.assertRaises(FlowError) as error:
                    self.flow.engine.edit_plan(card, request)
                self.assertEqual(error.exception.code, "bad-feedback")


if __name__ == "__main__":
    unittest.main()
