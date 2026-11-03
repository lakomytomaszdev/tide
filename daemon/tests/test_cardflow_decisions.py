"""PT-123: `flow.decisions[]` — decyzje karty liczone z dziennika (bez Qt i bez dysku).

Testy składają dziennik z publicznych zdarzeń, liczą stan przez `fold` i patrzą na publiczne `build_decisions`
oraz na pole `derived` karty. Odpowiedź przez StepResolve sprawdzają test_cardflow_decisions_flow.py (w procesie)
i test_cardflow_decisions_dbus.py (prywatna magistrala).
"""
import unittest

from tide_daemon.cardflow.decisions import GENERIC, KINDS, build_decisions
from tide_daemon.cardflow.state import derived, fold

DECISION_KEYS = {"id", "step", "stepKind", "ticket", "executor", "kind", "happened", "why", "options",
                 "freeText", "reply", "fault", "technical"}
OPTION_KEYS = {"id", "title", "effect", "recommended", "choice", "params", "method"}
CARD = {"column": "todo"}


class Events:
    """Dziennik karty zdarzenie po zdarzeniu, z kolejnym `seq` i czasem."""

    def __init__(self, entry="now"):
        self.items = []
        self.add("card.started", entry=entry, executor="Sonnet", program="claude", provider="anthropic")

    def add(self, event_type, /, **data):
        seq = len(self.items) + 1
        self.items.append({"v": 1, "seq": seq, "ts": f"2026-11-03T10:{seq:02d}:00+01:00", "card": "K-1",
                           "type": event_type, **data})
        return self

    def step(self, name, *, ticket="PT-1", kind="ticket", executor="Sonnet", attempt=0, parent="", **finished):
        self.add("step.started", step=name, ticket=ticket, kind=kind, executor=executor, provider="anthropic",
                 model="sonnet", attempt=attempt, parentStep=parent, stepDir=f"/wt/{name}.step",
                 worktree="/wt/pt", branch="ticket/pt")
        if finished:
            self.add("step.finished", step=name, ticket=ticket, **finished)
        return self

    def state(self):
        return fold(self.items)

    def decisions(self):
        return build_decisions(self.state())


def plan_events(**reviewed):
    events = Events(entry="plan")
    events.add("plan.proposed", planner="Opus", rows=[{"workId": "#1", "title": "A", "blockers": []},
                                                     {"workId": "#2", "title": "B", "blockers": ["#1"]}])
    if reviewed:
        events.add("plan.reviewed", **reviewed)
    return events


class ContractTest(unittest.TestCase):
    def assert_contract(self, decision):
        self.assertEqual(set(decision), DECISION_KEYS)
        self.assertIn(decision["kind"], KINDS)
        self.assertTrue(decision["happened"].strip())
        self.assertTrue(decision["why"].strip())
        self.assertIsInstance(decision["technical"], dict)
        for option in decision["options"]:
            self.assertEqual(set(option), OPTION_KEYS)
            self.assertTrue(option["title"] and option["effect"] and option["method"])
        recommended = [o for o in decision["options"] if o["recommended"]]
        self.assertLessEqual(len(recommended), 1)
        if recommended:
            self.assertIs(decision["options"][0], recommended[0])
        self.assertEqual(decision["freeText"], decision["reply"] is not None)

    def test_old_format_question_has_no_options_and_free_text_through_step_resolve(self):
        events = Events().step("K-1-PT-1-1", result="question", question="Który format daty?")
        [decision] = events.decisions()
        self.assert_contract(decision)
        self.assertEqual((decision["kind"], decision["step"], decision["stepKind"], decision["executor"]),
                         ("question", "K-1-PT-1-1", "ticket", "Sonnet"))
        self.assertEqual(decision["options"], [])
        self.assertTrue(decision["freeText"])
        self.assertEqual(decision["reply"], {"method": "ResolveCardStep", "field": "answer",
                                             "params": {"choice": "answer", "step": "K-1-PT-1-1"}})
        self.assertIn("Który format daty?", decision["happened"])
        self.assertEqual(decision["fault"], "")

    def test_step_options_put_the_single_recommended_first_and_none_is_preselected(self):
        options = [{"title": "Tylko dzienniki Claude", "effect": "Jeden dostawca."},
                   {"title": "Tak, uruchom", "effect": "Pełny pomiar.", "recommended": True},
                   {"title": "Nie, z testów", "effect": "Mniejszy pomiar.", "recommended": True}]
        events = Events().step("K-1-PT-1-1", result="question", question="Czy mogę?", options=options)
        [decision] = events.decisions()
        self.assert_contract(decision)
        self.assertEqual([o["title"] for o in decision["options"]],
                         ["Tak, uruchom", "Tylko dzienniki Claude", "Nie, z testów"])
        self.assertEqual([o["recommended"] for o in decision["options"]], [True, False, False])
        first = decision["options"][0]
        self.assertEqual((first["method"], first["choice"]), ("ResolveCardStep", "answer"))
        self.assertEqual(first["params"], {"choice": "answer", "step": "K-1-PT-1-1", "answer": "Tak, uruchom"})
        for option in decision["options"]:
            self.assertFalse({"selected", "default", "checked"} & set(option))

    def test_permission_single_command_and_chain(self):
        single = Events().step("K-1-PT-1-1", result="denied", command="git fetch origin", canAllow=True)
        [decision] = single.decisions()
        self.assert_contract(decision)
        self.assertEqual([(o["choice"], o["recommended"]) for o in decision["options"]],
                         [("allow", True), ("deny", False)])
        self.assertIn("git fetch origin", decision["happened"])
        self.assertEqual(decision["reply"]["params"], {"choice": "deny", "step": "K-1-PT-1-1"})

        chain = Events().step("K-1-PT-1-1", result="denied", command="ls; git log | head", canAllow=False)
        [decision] = chain.decisions()
        self.assert_contract(decision)
        self.assertEqual([o["choice"] for o in decision["options"]], ["deny"])
        self.assertTrue(decision["options"][0]["recommended"])
        self.assertFalse(decision["technical"]["canAllow"])

    def test_error_after_automatic_retry_names_fault_and_technical_details(self):
        events = (Events().step("K-1-PT-1-1", result="error", reason="start")
                  .add("step.retry", step="K-1-PT-1-1", ticket="PT-1", reason="start")
                  .step("K-1-PT-1-2", attempt=1, parent="K-1-PT-1-1", result="error", reason="start"))
        [decision] = events.decisions()
        self.assert_contract(decision)
        self.assertEqual((decision["kind"], decision["fault"], decision["step"]), ("error", "tide", "K-1-PT-1-2"))
        self.assertEqual([o["choice"] for o in decision["options"]], ["retry", "change-executor"])
        self.assertEqual(decision["options"][1]["params"]["executor"], "")
        technical = decision["technical"]
        self.assertEqual((technical["reason"], technical["reasonLabel"], technical["attempts"]),
                         ("start", "krok nie wystartował", 2))
        self.assertEqual(technical["stepDir"], "/wt/K-1-PT-1-2.step")

    def test_first_error_is_retried_by_tide_and_is_no_decision(self):
        self.assertEqual(Events().step("K-1-PT-1-1", result="error", reason="exit").decisions(), [])

    def test_answered_superseded_and_finished_cards_have_no_decisions(self):
        answered = Events().step("K-1-PT-1-1", result="question", question="?") \
            .add("step.decision", step="K-1-PT-1-1", ticket="PT-1", choice="answer", answer="tak")
        self.assertEqual(answered.decisions(), [])
        restarted = Events().step("K-1-PT-1-1", result="question", question="?").add("card.started", entry="now")
        self.assertEqual(restarted.decisions(), [])
        finished = Events().step("K-1-PT-1-1", result="question", question="?").add("card.finished", column="done")
        self.assertEqual(finished.decisions(), [])

    def test_limit_is_never_a_decision(self):
        # D3 (PT-125): limit dostawcy nie jest decyzją — ani zatrzymany krok, ani błąd z kodem limitu w dzienniku.
        stopped = Events().step("K-1-PT-1-1", result="stopped", reason="limit") \
            .add("step.limit", step="K-1-PT-1-1", reset="16:30")
        self.assertEqual(stopped.decisions(), [])
        failed = (Events().step("K-1-PT-1-1", result="error", reason="exit", fault="provider", faultCause="limit")
                  .step("K-1-PT-1-2", attempt=1, parent="K-1-PT-1-1", result="error", reason="exit",
                        fault="provider", faultCause="limit"))
        self.assertEqual(failed.decisions(), [])
        self.assertEqual(derived(stopped.state(), CARD)["flow"]["action"], "limit", "stare pole panelu bez zmian")

    def test_provider_error_other_than_limit_reaches_the_window(self):
        events = (Events().step("K-1-PT-1-1", result="error", reason="exit", fault="provider", faultCause="auth")
                  .step("K-1-PT-1-2", attempt=1, parent="K-1-PT-1-1", result="error", reason="exit",
                        fault="provider", faultCause="auth"))
        [decision] = events.decisions()
        self.assert_contract(decision)
        self.assertEqual((decision["kind"], decision["fault"]), ("error", "provider"))
        self.assertIn("dostawca odrzucił autoryzację", decision["happened"])
        self.assertIn("błąd dostawcy (Claude)", decision["why"])
        self.assertEqual(decision["options"][0]["choice"], "change-executor", "przy autoryzacji polecany inny wykonawca")
        self.assertIsNone(decision["reply"], "błędu dostawcy nie zgłaszamy jako błędu Tide")
        outage = (Events().step("K-1-PT-1-1", result="error", reason="exit", fault="provider", faultCause="outage")
                  .step("K-1-PT-1-2", attempt=1, parent="K-1-PT-1-1", result="error", reason="exit",
                        fault="provider", faultCause="outage"))
        [decision] = outage.decisions()
        self.assertEqual((decision["fault"], decision["options"][0]["choice"]), ("provider", "retry"))

    def test_tide_fault_says_whose_fault_and_offers_report_and_retry(self):
        events = (Events().step("K-1-plan-1", ticket="", kind="plan-review", executor="Haiku", result="error",
                                reason="no-result", fault="tide", faultCause="write-denied")
                  .step("K-1-plan-2", ticket="", kind="plan-review", executor="Haiku", attempt=1,
                        parent="K-1-plan-1", result="error", reason="no-result", fault="tide",
                        faultCause="write-denied"))
        [decision] = events.decisions()
        self.assert_contract(decision)
        self.assertEqual((decision["kind"], decision["fault"]), ("error", "tide"))
        self.assertEqual(decision["happened"], "Review planu (Haiku) zakończył się bez wyniku (próba 2): "
                                               "Tide nie dał prawa zapisu w katalogu wyniku kroku.")
        self.assertTrue(decision["why"].startswith("To błąd Tide, nie pracy kroku."))
        self.assertEqual([(o["choice"], o["recommended"]) for o in decision["options"]],
                         [("retry", True), ("change-executor", False)])
        self.assertIn("tu nie był", decision["options"][1]["effect"])
        reply = decision["reply"]
        self.assertEqual((reply["method"], reply["preview"], reply["field"], reply["label"]),
                         ("ReportStepFault", "PreviewStepFault", "description", "Opisz problem i ponów"))
        self.assertEqual(reply["params"], {"step": "K-1-plan-2"})
        self.assertEqual(decision["technical"]["signature"], "plan-review/tide/write-denied")

    def test_unknown_fault_says_plainly_that_nobody_knows(self):
        events = (Events().step("K-1-PT-1-1", result="error", reason="exit", fault="unknown", faultCause="exit")
                  .step("K-1-PT-1-2", attempt=1, parent="K-1-PT-1-1", result="error", reason="exit",
                        fault="unknown", faultCause="exit"))
        [decision] = events.decisions()
        self.assertEqual(decision["fault"], "unknown")
        self.assertTrue(decision["why"].startswith("Nie wiadomo, czyja to wina"))
        self.assertEqual(decision["reply"]["method"], "ReportStepFault", "podejrzenie błędu Tide można zgłosić")

    def test_model_fault_recommends_another_executor(self):
        events = (Events().step("K-1-PT-1-1", result="error", reason="bad-result")
                  .step("K-1-PT-1-2", attempt=1, parent="K-1-PT-1-1", result="error", reason="bad-result"))
        [decision] = events.decisions()
        self.assertEqual(decision["fault"], "model", "stary dziennik bez pola fault — z samego powodu")
        self.assertEqual(decision["options"][0]["choice"], "change-executor")
        self.assertIsNone(decision["reply"])

    def test_plan_approval_recommends_from_the_verdict(self):
        changes = plan_events(reviewer="Sonnet", verdict="REQUEST_CHANGES", findings="MAJOR: rozdziel fale.")
        [decision] = changes.decisions()
        self.assert_contract(decision)
        self.assertEqual(decision["kind"], "plan-approval")
        self.assertEqual([(o["method"], o["choice"]) for o in decision["options"]],
                         [("EditCardPlan", "return"), ("ApproveCardPlan", "approve")])
        self.assertEqual(decision["options"][0]["params"], {"action": "return", "feedback": "MAJOR: rozdziel fale."})
        self.assertIn("2 tickety", decision["happened"])
        self.assertEqual(decision["reply"], {"method": "EditCardPlan", "params": {"action": "return"},
                                             "field": "feedback"})
        approve = plan_events(reviewer="Sonnet", verdict="APPROVE", findings="Dobrze.")
        self.assertEqual(approve.decisions()[0]["options"][0]["method"], "ApproveCardPlan")

    def test_review_question_after_three_rounds(self):
        events = Events().step("K-1-PT-1-3", kind="review", executor="Opus", result="done") \
            .add("review.verdict", ticket="PT-1", round=3, verdict="REQUEST_CHANGES", blockers=0, majors=2, nits=0) \
            .add("review.question", ticket="PT-1", round=3, question="Ponowić?")
        [decision] = events.decisions()
        self.assert_contract(decision)
        self.assertEqual((decision["kind"], decision["ticket"]), ("review", "PT-1"))
        self.assertEqual([(o["method"], o["params"]) for o in decision["options"]],
                         [("ResolveCardReview", {"choice": "retry"}), ("ResolveCardReview", {"choice": "reject"})])
        self.assertEqual(decision["technical"]["majors"], 2)


class OrderTest(unittest.TestCase):
    def test_card_decisions_come_before_tickets_and_the_oldest_comes_first(self):
        events = plan_events(reviewer="Sonnet", verdict="APPROVE", findings="ok")
        events.add("plan.approved", map={"#1": "PT-1", "#2": "PT-2"},
                   rows=[{"id": "PT-1", "title": "A", "blockers": []}, {"id": "PT-2", "title": "B", "blockers": []}])
        events.step("K-1-PT-1-1", ticket="PT-1")
        events.step("K-1-PT-2-1", ticket="PT-2")
        events.add("step.finished", step="K-1-PT-2-1", ticket="PT-2", result="question", question="Druga?")
        events.add("step.finished", step="K-1-PT-1-1", ticket="PT-1", result="question", question="Pierwsza?")
        events.step("K-1-plan-1", ticket="", kind="plan-amend", executor="Opus", result="question",
                    question="Który wariant poprawki?")
        decisions = events.decisions()
        self.assertEqual([d["step"] for d in decisions], ["K-1-plan-1", "K-1-PT-2-1", "K-1-PT-1-1"])

    def test_two_ticket_decisions_oldest_first(self):
        events = Events()
        events.step("K-1-PT-2-1", ticket="PT-2")
        events.step("K-1-PT-1-1", ticket="PT-1")
        events.add("step.finished", step="K-1-PT-1-1", ticket="PT-1", result="denied", command="git fetch")
        events.add("step.finished", step="K-1-PT-2-1", ticket="PT-2", result="question", question="?")
        self.assertEqual([d["ticket"] for d in events.decisions()], ["PT-1", "PT-2"])


class GenericSentenceTest(unittest.TestCase):
    """US 2: rodzaj decyzji bez danych do zdania dostaje zdanie ogólne, nigdy pusty napis."""

    def empty_inputs(self):
        tracker = Events().add("result", action="youtrack-create", key="k", ok=False, permanent=True, error="")
        return {
            "question": Events().step("s", ticket="", result="question", question=""),
            "permission": Events().step("s", ticket="", kind="", result="denied", command=""),
            "error": Events().step("s0", ticket="", kind="", result="error", reason="")
                             .step("s", ticket="", kind="", attempt=1, parent="s0", result="error", reason=""),
            "transfer": Events().step("s", ticket="", kind="", result="stopped", reason="transfer"),
            "stopped": Events().step("s", ticket="", kind="", result="stopped", reason="interactive-closed"),
            "review": Events().add("review.question", ticket="", round=0, question=""),
            "plan-approval": Events(entry="plan").add("plan.proposed", planner="", rows=[]).add("plan.reviewed"),
            "plan-revision": Events(entry="plan").add("plan.proposed", planner="", rows=[]).add("plan.reviewed")
                                                 .add("plan.revision-ready", pending=True),
            "plan-revision-failed": Events(entry="plan").add("plan.proposed", planner="", rows=[])
                                                        .add("plan.revision-failed", failed=True),
            "plan-reviewer": Events(entry="plan").add("plan.proposed", planner="", rows=[])
                                                 .add("step.waiting", ticket="", kind="plan-review", reason=""),
            "tracker": tracker,
        }

    def test_every_kind_with_empty_input_gets_its_generic_sentences(self):
        inputs = self.empty_inputs()
        self.assertEqual(set(inputs), set(KINDS), "tabela obejmuje każdy rodzaj decyzji")
        for kind, events in inputs.items():
            with self.subTest(kind=kind):
                [decision] = events.decisions()
                self.assertEqual(decision["kind"], kind)
                self.assertEqual((decision["happened"], decision["why"]), GENERIC[kind])
                self.assertTrue(all(GENERIC[kind]))


class DerivedTest(unittest.TestCase):
    def test_decisions_sit_next_to_unchanged_old_fields(self):
        events = Events().step("K-1-PT-1-1", result="denied", command="git fetch origin", canAllow=True)
        shown = derived(events.state(), CARD)
        flow = shown["flow"]
        self.assertEqual((flow["action"], flow["command"], flow["canAllow"]),
                         ("permission", "git fetch origin", True))
        self.assertEqual((shown["column"], shown["note"]), ("waiting", "PT-1: brak zgody na „git fetch origin”"))
        self.assertEqual(flow["decisions"], build_decisions(events.state()))
        self.assertEqual(flow["decisions"][0]["kind"], "permission")

    def test_question_keeps_old_question_field(self):
        events = Events().step("K-1-PT-1-1", result="question", question="Który format daty?")
        shown = derived(events.state(), CARD)
        self.assertEqual((shown["flow"]["action"], shown["flow"]["question"], shown["question"]),
                         ("answer", "Który format daty?", "Który format daty?"))
        self.assertEqual(len(shown["flow"]["decisions"]), 1)

    def test_tile_names_tide_fault_in_the_step(self):
        ticket = (Events().step("K-1-PT-1-1", result="error", reason="no-result", fault="tide",
                                faultCause="write-denied")
                  .step("K-1-PT-1-2", attempt=1, parent="K-1-PT-1-1", result="error", reason="no-result",
                        fault="tide", faultCause="write-denied"))
        shown = derived(ticket.state(), CARD)
        self.assertEqual(shown["column"], "waiting")
        self.assertEqual(shown["note"], "błąd Tide w kroku PT-1: Tide nie dał prawa zapisu w katalogu wyniku kroku")
        self.assertEqual((shown["flow"]["action"], shown["flow"]["fault"]), ("error", "tide"))

        plan = plan_events()
        plan.step("K-1-plan-1", ticket="", kind="plan-review", executor="Haiku", result="error", reason="no-result",
                  fault="tide", faultCause="write-denied")
        plan.step("K-1-plan-2", ticket="", kind="plan-review", executor="Haiku", attempt=1, parent="K-1-plan-1",
                  result="error", reason="no-result", fault="tide", faultCause="write-denied")
        shown = derived(plan.state(), CARD)
        self.assertEqual((shown["column"], shown["note"]), ("waiting", "błąd Tide w kroku review planu: Tide nie dał prawa zapisu w katalogu wyniku kroku"))

        unknown = (Events().step("K-1-PT-1-1", result="error", reason="exit")
                   .step("K-1-PT-1-2", attempt=1, parent="K-1-PT-1-1", result="error", reason="exit"))
        self.assertEqual(derived(unknown.state(), CARD)["note"], "błąd kroku PT-1: program zakończył się błędem",
                         "bez winy Tide kafelek bez zmian")

    def test_working_card_has_empty_decisions(self):
        events = Events().step("K-1-PT-1-1")
        self.assertEqual(derived(events.state(), CARD)["flow"]["decisions"], [])


if __name__ == "__main__":
    unittest.main()
