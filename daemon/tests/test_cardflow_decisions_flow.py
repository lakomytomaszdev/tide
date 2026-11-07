"""PT-123: odpowiedź z `flow.decisions[]` idzie istniejącym StepResolve (ResolveCardStep) i trafia do wznowionego
kroku — w procesie, na prawdziwych atrapach kroku (ten sam przebieg co test_cardflow_decisions_dbus.py)."""
import io
import json
import os
import unittest
from unittest.mock import patch

from .privatebus import wait_until
from .test_cardflow import FlowInProcess


class DecisionAnswerTest(FlowInProcess):
    def decide(self, card_id, request):
        box = []
        self.board.resolve_card_step(card_id, json.dumps(request), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20), "ResolveCardStep bez odpowiedzi")
        return json.loads(box[0])

    def resumed_prompt(self, card_id, parent):
        found = wait_until(lambda: [s for s in self.of(card_id, "step.started") if s.get("parentStep") == parent],
                           timeout=20)
        self.assertTrue(found, self.events(card_id))
        call = os.path.join(found[0]["stepDir"], "out", "fake-call.json")

        def read_call():
            # Istnienie pliku nie oznacza końca json.dump w procesie atrapy.
            try:
                with open(call, encoding="utf-8") as fh:
                    return json.load(fh)
            except (FileNotFoundError, json.JSONDecodeError):
                return None

        data = wait_until(read_call, timeout=20)
        self.assertTrue(data, "wznowiony krok nie zapisał pełnego wywołania")
        return data["prompt"]

    def test_free_text_answer_from_decision_reaches_the_resumed_step(self):
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: pytanie")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        [decision] = card["derived"]["flow"]["decisions"]
        self.assertEqual((decision["kind"], decision["options"], decision["freeText"]), ("question", [], True))
        self.assertIn("Który format daty?", decision["happened"])

        reply = decision["reply"]
        self.assertEqual(reply["method"], "ResolveCardStep")
        answer = self.decide(card_id, {**reply["params"], reply["field"]: "ISO 8601"})
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["step"], decision["step"])
        self.assertIn("Odpowiedź użytkownika: ISO 8601", self.resumed_prompt(card_id, decision["step"]))
        [event] = self.of(card_id, "step.decision")
        self.assertEqual((event["step"], event["choice"], event["answer"]), (decision["step"], "answer", "ISO 8601"))
        self.assertFalse([d for d in self.shown(card_id)["derived"]["flow"]["decisions"]
                          if d["step"] == decision["step"]])

    def test_permission_option_and_own_words_reach_the_resumed_step(self):
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: zgoda")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        [decision] = card["derived"]["flow"]["decisions"]
        self.assertEqual(decision["kind"], "permission")
        self.assertEqual([o["choice"] for o in decision["options"]], ["allow", "deny"])
        reply = decision["reply"]
        answer = self.decide(card_id, {**reply["params"], reply["field"]: "Sprawdź gałęzie przez git branch -a."})
        self.assertTrue(answer["ok"], answer)
        prompt = self.resumed_prompt(card_id, decision["step"])
        self.assertIn("Polecenie nie zostało uruchomione: git fetch origin", prompt)
        self.assertIn("Sprawdź gałęzie przez git branch -a.", prompt)
        self.assertNotIn("git fetch origin", self.store.load().projects["proj"].allowed_commands)


class ResumedPromptReadinessTest(unittest.TestCase):
    def test_waits_for_complete_json_in_the_resumed_call(self):
        case = DecisionAnswerTest("test_free_text_answer_from_decision_reaches_the_resumed_step")
        started = [{"parentStep": "original", "stepDir": "/synthetic/step"}]
        reads = [io.StringIO(""), io.StringIO('{"prompt":'),
                 io.StringIO('{"prompt": "Odpowiedź użytkownika: ISO 8601"}')]
        with patch.object(case, "of", return_value=started), \
                patch.object(case, "events", return_value=[]), \
                patch("os.path.isfile", return_value=True), \
                patch(f"{__name__}.open", side_effect=reads, create=True) as opened:
            self.assertEqual(case.resumed_prompt("K-1", "original"), "Odpowiedź użytkownika: ISO 8601")
        self.assertEqual(opened.call_count, 3)


class DecisionStepTargetTest(FlowInProcess):
    """Dwie decyzje jednej karty: `step` w żądaniu wskazuje, na którą odpowiada okno („1 z 2”, „2 z 2”)."""

    def setUp(self):
        super().setUp()
        self.flow.stop()   # dziennik składany ręcznie — bez ticków, które próbowałyby wznawiać kroki
        self.engine = self.flow.engine
        self.card_id = self.card()
        journal = self.engine.journal
        journal.append(self.card_id, "card.started", entry="now", executor="Sonnet", program="claude",
                       provider="anthropic")
        for ticket in ("PT-1", "PT-2"):
            journal.append(self.card_id, "step.started", step=f"{self.card_id}-{ticket}-1", ticket=ticket,
                           kind="ticket", executor="Sonnet", provider="anthropic", attempt=0)
        for ticket in ("PT-2", "PT-1"):
            journal.append(self.card_id, "step.finished", step=f"{self.card_id}-{ticket}-1", ticket=ticket,
                           result="question", question=f"Pytanie {ticket}?")

    def decisions(self):
        from tide_daemon.cardflow.decisions import build_decisions
        from tide_daemon.cardflow.state import fold
        return build_decisions(fold(self.engine.journal.read(self.card_id)))

    def test_answer_goes_to_the_step_named_by_the_decision(self):
        first, second = self.decisions()
        self.assertEqual((first["ticket"], second["ticket"]), ("PT-2", "PT-1"))
        reply = self.engine.resolve_step(self.card_id, {**second["reply"]["params"], "answer": "B"})
        self.assertEqual(reply["step"], second["step"])
        reply = self.engine.resolve_step(self.card_id, {**first["reply"]["params"], "answer": "A"})
        self.assertEqual(reply["step"], first["step"])
        decided = {e["step"]: e["answer"] for e in self.engine.journal.read(self.card_id)
                   if e["type"] == "step.decision"}
        self.assertEqual(decided, {second["step"]: "B", first["step"]: "A"})
        self.assertEqual(self.decisions(), [])

    def test_step_without_pending_decision_is_refused(self):
        from tide_daemon.cardflow.engine import FlowError
        with self.assertRaises(FlowError) as caught:
            self.engine.resolve_step(self.card_id, {"choice": "answer", "answer": "x", "step": "K-9-PT-9-1"})
        self.assertEqual(caught.exception.code, "not-waiting")

    def test_request_without_step_keeps_the_old_focus(self):
        reply = self.engine.resolve_step(self.card_id, {"choice": "answer", "answer": "x"})
        self.assertEqual(reply["step"], f"{self.card_id}-PT-1-1")
