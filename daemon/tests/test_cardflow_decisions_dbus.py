"""PT-123: `flow.decisions[]` w Snapshot() i odpowiedź przez /Board ResolveCardStep na prywatnej magistrali.

Test widzi tylko to, co z zewnątrz: decyzję w polu `derived` karty, odpowiedź metody i zdarzenia w dzienniku karty
oraz polecenie wznowionego kroku (atrapa `claude -p` zapisuje je w katalogu kroku).
"""
import json
import os

from .privatebus import wait_until
from .test_cardflow_dbus import FlowCase


class DecisionDbusTest(FlowCase):
    def seed_caretaker(self, trigger="flow-error"):
        """Dziennik wejściowy bez uruchamiania procesów, obsługa decyzji przez prawdziwą usługę."""
        from apps.board.tests.decisionfix import journal_caretaker_card
        from tide_daemon.cardflow.journal import Journal, mirror_root
        # `start()` monitora wykonuje tick od razu, niezależnie od interwału timera.
        # Najpierw odczekaj jego koniec przez akcję w tej samej kolejce, dopiero potem zapisz fixture.
        self.env["TIDE_FLOW_POLL_MS"] = "600000"
        self.restart()
        self.assertTrue(self.call("GetCardFlowSettings")["ok"])
        card_id = self.card()
        journal = Journal(self.board, mirror=mirror_root(self.board, {
            "XDG_STATE_HOME": os.path.join(self.base, "state")}))
        for event in journal_caretaker_card(trigger):
            journal.append(card_id, event["type"], **{k: v for k, v in event.items()
                                                     if k not in ("type", "v", "seq", "ts", "card")})
        # Odśwież migawkę po zewnętrznym zapisie dziennika, bez uruchamiania przebiegu silnika.
        self.assertTrue(self.call("UpdateCard", card_id, json.dumps({"title": "Decyzja opiekuna"}))["ok"])
        return card_id

    def test_caretaker_choices_and_errors_on_private_bus(self):
        from tide_daemon.cardflow import caretaker
        from tide_daemon.cardflow.state import fold
        for trigger, choice in (("flow-error", "retry-stage"), ("stalled", "dismiss")):
            with self.subTest(trigger=trigger, choice=choice):
                card_id = self.seed_caretaker(trigger)
                shown = self.shown(card_id)
                self.assertEqual(len(shown["derived"]["flow"]["decisions"]), 1, self.events(card_id))
                [decision] = shown["derived"]["flow"]["decisions"]
                self.assertEqual(decision["kind"], "caretaker")
                bad = self.call("ResolveCardCaretaker", card_id, json.dumps({"choice": "merge"}))
                self.assertEqual(bad["error"], "bad-choice")
                option = next(o for o in decision["options"] if o["choice"] == choice)
                answer = self.call(option["method"], card_id, json.dumps(option["params"]))
                self.assertTrue(answer["ok"], answer)
                self.assertEqual(answer["choice"], choice)
                self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.decision"), timeout=5))
                self.assertEqual(len(self.of(card_id, "stage.retry")), int(choice == "retry-stage"))
                own = caretaker.owned(fold(self.events(card_id)), answer["problem"])
                self.assertEqual((own["count"], own["handed"]), (0, False))
                again = self.call("ResolveCardCaretaker", card_id, json.dumps(option["params"]))
                self.assertEqual(again["error"], "not-waiting")

    def test_resolve_card_step_rejects_internal_caretaker_field(self):
        card_id = self.card()
        reply = self.call("ResolveCardStep", card_id, json.dumps({"choice": "retry", "caretaker": "problem"}))
        self.assertEqual(reply["error"], "bad-request")
        self.assertEqual(self.of(card_id, "step.decision"), [])

    def test_question_decision_is_answered_through_resolve_card_step(self):
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: pytanie")
        self.assertTrue(self.start(card_id)["ok"])
        shown = self.wait_column(card_id, "waiting")
        [decision] = shown["derived"]["flow"]["decisions"]
        self.assertEqual((decision["kind"], decision["options"], decision["freeText"]), ("question", [], True))
        self.assertTrue(decision["happened"] and decision["why"])
        self.assertEqual(shown["derived"]["flow"]["decisions"][0]["kind"], "question")

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
