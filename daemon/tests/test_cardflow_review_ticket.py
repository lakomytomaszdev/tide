"""PT-316 (K-88): `ResolveCardReview` rozstrzyga pytanie wskazanego ticketu, gdy karta ma kilka otwartych pytań."""
import json
import unittest
from unittest import mock

from tide_daemon.cardflow.engine import FlowError
from tide_daemon.cardflow.state import fold

from .test_cardflow_caretaker_retry import EngineOnJournal


class ResolveReviewTicketTest(EngineOnJournal):
    def setUp(self):
        super().setUp()
        self.engine.which = lambda name: "/fake/" + name
        for method in ("_guard_check", "_guard_rebase"):
            patcher = mock.patch.object(self.engine, method, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.add("K-1", "card.started", entry="now", executor="Sonnet", provider="anthropic",
                 preferredProvider="anthropic", reviewer="Opus", reviewerModel="opus",
                 reviewerProvider="anthropic", column="todo")

    def question(self, ticket, round_no=3):
        step = f"K-1-{ticket}-review-{round_no}"
        self.add("K-1", "step.started", step=step, kind="review", ticket=ticket, round=round_no)
        self.add("K-1", "step.finished", step=step, ticket=ticket, result="done")
        self.add("K-1", "review.verdict", ticket=ticket, round=round_no, verdict="REQUEST_CHANGES")
        self.add("K-1", "review.question", ticket=ticket, round=round_no, question="Pozostały uwagi.")

    def two(self):
        self.question("PT-7")
        self.question("PT-8")

    def state(self):
        return fold(self.journal.read("K-1"))

    def count(self):
        return len(self.journal.read("K-1"))

    def test_ticket_resolves_only_its_own_question(self):
        self.two()
        reply = self.engine.resolve_review("K-1", json.dumps({"choice": "accept", "ticket": "PT-7"}))
        self.assertEqual((reply["ok"], reply["ticket"], reply["round"]), (True, "PT-7", 3))
        st = self.state()
        self.assertEqual(list(st.review_decisions), [("PT-7", 3)])
        self.assertEqual(st.review_decisions[("PT-7", 3)]["choice"], "accept")
        # Drugie pytanie zostaje otwarte i przy jednym otwartym działa goły napis.
        self.assertEqual(self.engine.resolve_review("K-1", "reject")["ticket"], "PT-8")

    def test_older_ticket_can_be_rejected_while_the_newer_one_stays_open(self):
        self.two()
        reply = self.engine.resolve_review("K-1", json.dumps({"choice": "reject", "ticket": "PT-7", "round": 3}))
        self.assertTrue(reply["ok"])
        self.assertIn("PT-7", self.state().rejected)
        self.assertNotIn("PT-8", self.state().rejected)
        self.assertNotIn(("PT-8", 3), self.state().review_decisions)

    def test_without_ticket_two_questions_are_ambiguous_and_nothing_is_written(self):
        self.two()
        before = self.count()
        for choice in ("retry", json.dumps({"choice": "retry"})):
            with self.assertRaises(FlowError) as raised:
                self.engine.resolve_review("K-1", choice)
            self.assertEqual(raised.exception.code, "ambiguous-review")
            self.assertEqual(raised.exception.data["reviews"],
                             [{"ticket": "PT-7", "round": 3}, {"ticket": "PT-8", "round": 3}])
        self.assertEqual(self.count(), before)

    def test_bare_string_with_one_question_works_as_before_including_idempotent_reject(self):
        self.question("PT-7")
        first = self.engine.resolve_review("K-1", "reject")
        self.assertEqual((first["ok"], first["ticket"], first["choice"]), (True, "PT-7", "reject"))
        again = self.engine.resolve_review("K-1", "reject")
        self.assertTrue(again["already"])
        self.assertEqual(len(self.state().review_decisions), 1)

    def test_ticket_without_question_or_stale_round_is_no_review_question(self):
        self.two()
        before = self.count()
        for request in ({"choice": "retry", "ticket": "PT-9"}, {"choice": "retry", "ticket": "PT-7", "round": 2},
                        {"choice": "retry", "ticket": "PT-7", "round": 4}):
            with self.assertRaises(FlowError) as raised:
                self.engine.resolve_review("K-1", json.dumps(request))
            self.assertEqual(raised.exception.code, "no-review-question", request)
        self.assertEqual(self.count(), before)

    def test_answered_ticket_cannot_be_answered_twice(self):
        self.two()
        self.engine.resolve_review("K-1", json.dumps({"choice": "accept", "ticket": "PT-7"}))
        with self.assertRaises(FlowError) as raised:
            self.engine.resolve_review("K-1", json.dumps({"choice": "retry", "ticket": "PT-7"}))
        self.assertEqual(raised.exception.code, "no-review-question")

    def test_bad_json_is_a_readable_error_without_a_write(self):
        self.two()
        before = self.count()
        for request in ("{", '{"ticket": "PT-7"}', '{"choice": "nope", "ticket": "PT-7"}',
                        '{"choice": "retry", "ticket": 7}', '{"choice": "retry", "ticket": "PT-7", "round": "x"}',
                        '["retry"]'):
            with self.assertRaises(FlowError) as raised:
                self.engine.resolve_review("K-1", request)
            self.assertEqual(raised.exception.code, "bad-choice", request)
        self.assertEqual(self.count(), before)

    def test_running_ticket_blocks_only_its_own_question(self):
        self.two()
        self.add("K-1", "step.started", step="K-1-PT-7-fix", kind="fix", ticket="PT-7")
        with self.assertRaises(FlowError) as raised:
            self.engine.resolve_review("K-1", json.dumps({"choice": "retry", "ticket": "PT-7"}))
        self.assertEqual(raised.exception.code, "no-review-question")
        self.assertTrue(self.engine.resolve_review("K-1", json.dumps({"choice": "retry", "ticket": "PT-8"}))["ok"])


if __name__ == "__main__":
    unittest.main()
