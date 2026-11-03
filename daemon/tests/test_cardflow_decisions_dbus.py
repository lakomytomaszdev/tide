"""PT-123: `flow.decisions[]` w Snapshot() i odpowiedź przez /Board ResolveCardStep na prywatnej magistrali.

Test widzi tylko to, co z zewnątrz: decyzję w polu `derived` karty, odpowiedź metody i zdarzenia w dzienniku karty
oraz polecenie wznowionego kroku (atrapa `claude -p` zapisuje je w katalogu kroku).
"""
import json
import os

from .privatebus import wait_until
from .test_cardflow_dbus import FlowCase


class DecisionDbusTest(FlowCase):
    def test_question_decision_is_answered_through_resolve_card_step(self):
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: pytanie")
        self.assertTrue(self.start(card_id)["ok"])
        shown = self.wait_column(card_id, "waiting")
        [decision] = shown["derived"]["flow"]["decisions"]
        self.assertEqual((decision["kind"], decision["options"], decision["freeText"]), ("question", [], True))
        self.assertTrue(decision["happened"] and decision["why"])
        self.assertEqual(shown["derived"]["flow"]["action"], "answer")

        reply = decision["reply"]
        self.assertEqual(reply["method"], "ResolveCardStep")
        answer = self.call(reply["method"], card_id, json.dumps({**reply["params"], reply["field"]: "ISO 8601"}))
        self.assertEqual((answer["ok"], answer["choice"], answer["step"]), (True, "answer", decision["step"]))
        resumed = wait_until(lambda: [s for s in self.of(card_id, "step.started")
                                      if s.get("parentStep") == decision["step"]], timeout=20)
        self.assertTrue(resumed, self.events(card_id))
        call = os.path.join(resumed[0]["stepDir"], "out", "fake-call.json")
        self.assertTrue(wait_until(lambda: os.path.isfile(call), timeout=20))
        self.assertIn("Odpowiedź użytkownika: ISO 8601", self.fake_call(resumed[0])["prompt"])
        self.assertEqual([(e["step"], e["choice"]) for e in self.of(card_id, "step.decision")],
                         [(decision["step"], "answer")])
