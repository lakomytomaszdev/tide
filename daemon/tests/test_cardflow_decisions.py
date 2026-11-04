"""PT-123: `flow.decisions[]` — decyzje karty liczone z dziennika (bez Qt i bez dysku).

Testy składają dziennik z publicznych zdarzeń, liczą stan przez `fold` i patrzą na publiczne `build_decisions`
oraz na pole `derived` karty. Odpowiedź przez StepResolve sprawdzają test_cardflow_decisions_flow.py (w procesie)
i test_cardflow_decisions_dbus.py (prywatna magistrala).
"""
import unittest

from tide_daemon.cardflow.decisions import GENERIC, KINDS, build_decisions
from tide_daemon.cardflow.state import derived, fold

DECISION_KEYS = {"id", "step", "stepKind", "ticket", "executor", "kind", "happened", "why", "options",
                 "freeText", "reply", "fault", "technical", "findings"}
PERMISSION_KEYS = {"commands", "otherTitle", "otherHint"}       # PT-124: tylko przy zgodzie na polecenie
QUESTION_KEYS = {"copyCommands"}                                # 04.11: polecenia z pytania do skopiowania
OPTION_KEYS = {"id", "title", "effect", "recommended", "choice", "params", "method"}
CARD = {"column": "todo"}
# Pola `derived.flow` z czasów przed oknem decyzji; usunął je PT-135.
OLD_FLOW_FIELDS = {"action", "question", "command", "canAllow", "error", "trackerError", "limit", "fault", "faultCause",
                   "authorProvider", "authorModel", "reviewerProvider", "reviewerModel", "preferredProvider"}


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
        extra = PERMISSION_KEYS if decision["kind"] == "permission" else QUESTION_KEYS if decision["kind"] == "question" \
            else set()
        self.assertEqual(set(decision), DECISION_KEYS | extra)
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
                   {"title": "Nie, z testów", "effect": "Mniejszy pomiar."}]
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

    def test_invalid_step_options_give_only_free_text_without_recommendation(self):
        good = {"title": "A", "effect": "x"}
        invalid = {
            "no title": [good, {"effect": "y"}],
            "empty title": [good, {"title": "  "}],
            "over four": [{"title": t} for t in "ABCDE"],
            "two recommended": [{**good, "recommended": True}, {"title": "B", "recommended": True}],
            "duplicate title": [good, {"title": "A"}],
            "not a list": {"title": "A"},
            "item not object": ["A", "B"],
            "bad recommended": [{"title": "A", "recommended": "tak"}],
            "empty list": [],
        }
        for label, options in invalid.items():
            with self.subTest(label):
                [decision] = Events().step("K-1-PT-1-1", result="question", question="Czy?",
                                           options=options).decisions()
                self.assertEqual(decision["options"], [])
                self.assertTrue(decision["freeText"])
                self.assertEqual(decision["reply"]["field"], "answer")

    def test_four_options_without_recommendation_keep_order(self):
        options = [{"title": t} for t in "ABCD"]
        [decision] = Events().step("K-1-PT-1-1", result="question", question="Czy?", options=options).decisions()
        self.assertEqual([(o["title"], o["recommended"]) for o in decision["options"]],
                         [("A", False), ("B", False), ("C", False), ("D", False)])

    def test_permission_single_command_and_chain(self):
        single = Events().step("K-1-PT-1-1", result="denied", command="git fetch origin", canAllow=True)
        [decision] = single.decisions()
        self.assert_contract(decision)
        self.assertEqual([(o["choice"], o["recommended"]) for o in decision["options"]],
                         [("allow", True), ("deny", False)])
        self.assertIn("git fetch origin", decision["happened"])
        self.assertEqual(decision["reply"]["params"], {"choice": "deny", "step": "K-1-PT-1-1"})
        self.assertEqual((decision["reply"]["field"], decision["otherTitle"]), ("instead", "Zrób zamiast tego…"))

        # Dziennik sprzed PT-124 (bez `commands`): łańcuch jak dotąd, tylko „Kontynuuj bez tego polecenia”.
        chain = Events().step("K-1-PT-1-1", result="denied", command="ls; git log | head", canAllow=False)
        [decision] = chain.decisions()
        self.assert_contract(decision)
        self.assertEqual([o["choice"] for o in decision["options"]], ["deny"])
        self.assertTrue(decision["options"][0]["recommended"])
        self.assertFalse(decision["technical"]["canAllow"])
        self.assertEqual(decision["commands"], [])

    def test_permission_chain_lists_commands_and_allows_selected(self):
        from tide_daemon.board.chain import describe
        command = "ls /wt/K-1-PT-1-1.step/in; git log -3 | head; rm -rf build; make install"
        commands = describe(command, denied=["make install"], step_dir="/wt/K-1-PT-1-1.step")
        [decision] = Events().step("K-1-PT-1-1", result="denied", command=command, canAllow=False,
                                   commands=commands).decisions()
        self.assert_contract(decision)
        self.assertEqual([(c["text"], c["readOnly"], c["forbidden"], c["canAllow"], c["preselected"])
                          for c in decision["commands"]],
                         [("git log -3", True, False, True, True), ("head", True, False, True, True),
                          ("rm -rf build", False, False, True, False), ("make install", False, True, False, False)])
        self.assertEqual(decision["commands"][3]["reason"], "Zabronione w projekcie regułą „make install”.")
        self.assertEqual([(o["title"], o["recommended"]) for o in decision["options"]],
                         [("Kontynuuj bez tych poleceń", True), ("Zezwól na wybrane i wznów", False)])
        self.assertEqual(decision["options"][1]["params"], {"choice": "allow", "step": "K-1-PT-1-1", "allow": []})
        self.assertIn("4 polecenia", decision["happened"])
        self.assertIn("jest powód", decision["why"])
        self.assertEqual(decision["reply"], {"method": "ResolveCardStep", "field": "instead",
                                             "params": {"choice": "deny", "step": "K-1-PT-1-1"}})
        self.assertFalse({"selected", "default", "checked"} & {k for o in decision["options"] for k in o})

    def test_permission_chain_without_allowable_command_has_reason_and_no_allow(self):
        from tide_daemon.board.chain import describe
        command = "make install; bash -c 'make install'"
        commands = describe(command, denied=["make install"], step_dir="/wt/K-1-PT-1-1.step")
        [decision] = Events().step("K-1-PT-1-1", result="denied", command=command, canAllow=False,
                                   commands=commands).decisions()
        self.assert_contract(decision)
        self.assertEqual([o["choice"] for o in decision["options"]], ["deny"])
        self.assertTrue(all(c["reason"] and not c["canAllow"] for c in decision["commands"]))
        self.assertIn("powód jest przy każdym", decision["why"])

    def test_error_after_automatic_retry_names_fault_and_technical_details(self):
        events = (Events().step("K-1-PT-1-1", result="error", reason="start")
                  .add("step.retry", step="K-1-PT-1-1", ticket="PT-1", reason="start")
                  .step("K-1-PT-1-2", attempt=1, parent="K-1-PT-1-1", result="error", reason="start"))
        [decision] = events.decisions()
        self.assert_contract(decision)
        self.assertEqual((decision["kind"], decision["fault"], decision["step"]), ("error", "tide", "K-1-PT-1-2"))
        self.assertEqual([o["choice"] for o in decision["options"]], ["retry", "change-executor", "reject"])
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
        self.assertEqual(derived(stopped.state(), CARD)["flow"]["decisions"], [])

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
        self.assertEqual(decision["reply"]["method"], "ResolveCardStep", "błędu dostawcy nie zgłaszamy jako błędu Tide")
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
        self.assertEqual(decision["reply"]["label"], "Ponów z instrukcją", "nieznana przyczyna: instrukcja dla wykonawcy")

    def test_model_fault_recommends_another_executor(self):
        events = (Events().step("K-1-PT-1-1", result="error", reason="bad-result")
                  .step("K-1-PT-1-2", attempt=1, parent="K-1-PT-1-1", result="error", reason="bad-result"))
        [decision] = events.decisions()
        self.assertEqual(decision["fault"], "model", "stary dziennik bez pola fault — z samego powodu")
        self.assertEqual(decision["options"][0]["choice"], "change-executor")
        self.assertEqual(decision["reply"]["label"], "Ponów z instrukcją")  # 03.11: instrukcja dla wykonawcy

    def test_plan_approval_recommends_from_the_verdict(self):
        changes = plan_events(reviewer="Sonnet", verdict="REQUEST_CHANGES", findings="MAJOR: rozdziel fale.")
        [decision] = changes.decisions()
        self.assert_contract(decision)
        self.assertEqual(decision["kind"], "plan-approval")
        self.assertEqual([(o["method"], o["choice"]) for o in decision["options"]],
                         [("EditCardPlan", "return"), ("ApproveCardPlan", "approve")])
        # Stary napis (karty w toku) to jedna uwaga bez wagi, zaznaczona z góry.
        old = {"severity": "", "text": "MAJOR: rozdziel fale.", "tickets": []}
        self.assertEqual(decision["options"][0]["params"], {"action": "return", "findings": [old]})
        self.assertEqual(decision["findings"], [{"id": "f1", **old, "selected": True}])
        self.assertIn("2 tickety", decision["happened"])
        self.assertEqual(decision["reply"], {"method": "EditCardPlan", "params": {"action": "return"},
                                             "field": "feedback"})
        approve = plan_events(reviewer="Sonnet", verdict="APPROVE", findings="Dobrze.")
        self.assertEqual(approve.decisions()[0]["options"][0]["method"], "ApproveCardPlan")

    SCENARIO_C = [
        {"severity": "major", "text": "#5, #7 i #8 zmieniają to samo okno w jednej fali.", "tickets": ["#5", "#7", "#8"]},
        {"severity": "major", "text": "#3 nie mówi, co zrobić przy odmowie zgody.", "tickets": ["#3"]},
        {"severity": "nit", "text": "Brak testu kolejki.", "tickets": ["PT-42"]},
    ]

    def test_plan_approval_lists_findings_with_default_selection(self):
        [decision] = plan_events(reviewer="Sonnet", verdict="REQUEST_CHANGES", findings=self.SCENARIO_C).decisions()
        self.assert_contract(decision)
        self.assertEqual([(f["severity"], f["selected"], f["tickets"]) for f in decision["findings"]],
                         [("major", True, ["#5", "#7", "#8"]), ("major", True, ["#3"]), ("nit", False, ["PT-42"])])
        [send_back, approve] = decision["options"]
        self.assertEqual((send_back["title"], send_back["recommended"]), ("Odeślij uwagi do poprawy", True))
        # Domyślnie odsyłane są blocker i major, nie nit.
        self.assertEqual(send_back["params"], {"action": "return", "findings": self.SCENARIO_C[:2]})
        self.assertFalse(approve["recommended"])
        self.assertEqual(decision["technical"]["majors"], 2)
        self.assertEqual(decision["technical"]["nits"], 1)

    def test_plan_approval_recommended_action_depends_on_verdict(self):
        nit = [{"severity": "nit", "text": "Drobiazg.", "tickets": []}]
        blocker = [{"severity": "blocker", "text": "Brak kryteriów.", "tickets": ["#1"]}]
        cases = [("APPROVE", [], "approve"), ("APPROVE", nit, "approve"), ("REQUEST_CHANGES", [], "approve"),
                 ("REQUEST_CHANGES", nit, "return"), ("APPROVE", blocker, "return"), ("REQUEST_CHANGES", blocker, "return"),
                 ("MOŻE", nit, None), ("", [], None)]
        for verdict, findings, expected in cases:
            with self.subTest(verdict=verdict, findings=len(findings)):
                [decision] = plan_events(reviewer="Sonnet", verdict=verdict, findings=findings).decisions()
                recommended = [o["id"] for o in decision["options"] if o["recommended"]]
                self.assertEqual(recommended, [expected] if expected else [])
                self.assertEqual(decision["options"][0]["id"], expected or decision["options"][0]["id"])

    def test_no_findings_offer_only_approval(self):
        [decision] = plan_events(reviewer="Sonnet", verdict="APPROVE", findings=[]).decisions()
        self.assertEqual([(o["id"], o["recommended"]) for o in decision["options"]], [("approve", True)])
        self.assertEqual(decision["findings"], [])

    def test_approving_despite_blockers_is_described_by_its_effect(self):
        blockers = [{"severity": "blocker", "text": "A", "tickets": []}, {"severity": "blocker", "text": "B", "tickets": []}]
        [decision] = plan_events(reviewer="Sonnet", verdict="REQUEST_CHANGES", findings=blockers).decisions()
        approve = next(o for o in decision["options"] if o["id"] == "approve")
        self.assertEqual(approve["method"], "ApproveCardPlan")
        self.assertIn("Mimo 2 blokujących uwag", approve["effect"])
        self.assertIn("nie zostaną poprawione", approve["effect"])

    def test_review_question_after_three_rounds(self):
        events = Events().step("K-1-PT-1-3", kind="review", executor="Opus", result="done") \
            .add("review.verdict", ticket="PT-1", round=3, verdict="REQUEST_CHANGES", blockers=0, majors=2, nits=0) \
            .add("review.question", ticket="PT-1", round=3, question="Ponowić?")
        [decision] = events.decisions()
        self.assert_contract(decision)
        self.assertEqual((decision["kind"], decision["ticket"]), ("review", "PT-1"))
        self.assertEqual([(o["method"], o["params"]) for o in decision["options"]],
                         [("ResolveCardReview", {"choice": "retry"}), ("ResolveCardReview", {"choice": "accept"}),
                          ("ResolveCardReview", {"choice": "reject"})])
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

    def test_closed_console_recommends_resuming_in_the_background(self):
        # 03.11, K-33/PT-109: po zamknięciu Konsoli jedyną opcją było „Wróć do rozmowy”.
        from tide_daemon.cardflow.decisions import build_decisions
        from tide_daemon.cardflow.state import fold
        made = build_decisions(fold(self.empty_inputs()["stopped"].items))[0]
        self.assertEqual([(o["choice"], o["recommended"]) for o in made["options"]],
                         [("background", True), ("enter", False)])

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
    def test_derived_carries_decisions_and_no_old_decision_fields(self):
        events = Events().step("K-1-PT-1-1", result="denied", command="git fetch origin", canAllow=True)
        shown = derived(events.state(), CARD)
        flow = shown["flow"]
        # D-Bus: kontrakt bez starych pól decyzji (PT-135); wszystko o decyzji stoi w `decisions[]`.
        self.assertEqual(set(flow) & OLD_FLOW_FIELDS, set())
        technical = flow["decisions"][0]["technical"]
        self.assertEqual((flow["decisions"][0]["kind"], technical["command"], technical["canAllow"]),
                         ("permission", "git fetch origin", True))
        self.assertEqual((shown["column"], shown["note"]), ("waiting", "PT-1: brak zgody na „git fetch origin”"))
        self.assertEqual(flow["decisions"], build_decisions(events.state()))

    def test_question_keeps_card_level_question(self):
        events = Events().step("K-1-PT-1-1", result="question", question="Który format daty?")
        shown = derived(events.state(), CARD)
        self.assertEqual((shown["flow"]["decisions"][0]["technical"]["question"], shown["question"]),
                         ("Który format daty?", "Który format daty?"))
        self.assertEqual(len(shown["flow"]["decisions"]), 1)

    def test_tile_names_tide_fault_in_the_step(self):
        ticket = (Events().step("K-1-PT-1-1", result="error", reason="no-result", fault="tide",
                                faultCause="write-denied")
                  .step("K-1-PT-1-2", attempt=1, parent="K-1-PT-1-1", result="error", reason="no-result",
                        fault="tide", faultCause="write-denied"))
        shown = derived(ticket.state(), CARD)
        self.assertEqual(shown["column"], "waiting")
        self.assertEqual(shown["note"], "błąd Tide w kroku PT-1: Tide nie dał prawa zapisu w katalogu wyniku kroku")
        self.assertEqual((shown["flow"]["decisions"][0]["kind"], shown["flow"]["decisions"][0]["fault"]),
                         ("error", "tide"))

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


class StepResultContractTest(unittest.TestCase):
    """PT-126: `result.json` z `options[]` — poprawne przechodzą, zły format nie psuje wyniku, stary działa."""

    def read(self, payload):
        import json
        import os
        import tempfile
        from tide_daemon.cardflow.engine import FlowEngine
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "result.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            return FlowEngine._result(path)

    def test_valid_options_are_kept_with_question(self):
        data = self.read({"result": "question", "question": "Czy?",
                          "options": [{"title": "Tak", "effect": "Idziemy.", "recommended": True}, {"title": "Nie"}]})
        self.assertEqual(data["result"], "question")
        self.assertEqual(data["options"], [{"title": "Tak", "effect": "Idziemy.", "recommended": True},
                                           {"title": "Nie", "effect": "", "recommended": False}])

    def test_invalid_options_do_not_turn_the_result_into_an_error(self):
        data = self.read({"result": "question", "question": "Czy?", "options": [{"effect": "brak tytułu"}]})
        self.assertEqual((data["result"], data["options"]), ("question", []))

    def test_old_format_question_has_no_options(self):
        data = self.read({"result": "question", "question": "Czy?"})
        self.assertEqual((data["result"], data["options"]), ("question", []))

    def test_options_on_other_results_are_ignored(self):
        self.assertEqual(self.read({"result": "denied", "command": "ls", "options": [{"title": "A"}]})["options"], [])


class ResumeContextOptionTest(unittest.TestCase):
    PRIOR = {"result": "question", "question": "Czy?", "options": [{"title": "Tak", "effect": "Idziemy."}]}

    def test_chosen_option_brings_title_and_effect(self):
        from tide_daemon.cardflow.handoff import resume_context
        text = resume_context(choice="answer", prior=self.PRIOR, answer="Tak", command="", out_dir="/o")
        self.assertIn("Odpowiedź użytkownika: Tak", text)
        self.assertIn("Skutek opcji: Idziemy.", text)

    def test_free_answer_has_no_option_lines(self):
        from tide_daemon.cardflow.handoff import resume_context
        text = resume_context(choice="answer", prior=self.PRIOR, answer="Własna", command="", out_dir="/o")
        self.assertNotIn("Skutek opcji", text)

    def test_step_card_describes_options(self):
        from tide_daemon.cardflow.handoff import ticket_card
        text = ticket_card(card_id="K-1", title="t", description="d", project="p", ticket="PT-1", branch="b",
                           card_branch="c", worktree="/w", out_dir="/o")
        self.assertIn('"options"', text)
        from tide_daemon.cardflow.handoff import conflict_card, close_card, resume_step
        for cmd in (conflict_card(card_id="K-1", title="t", description="d", ticket="PT-1", card_branch="c",
                                  ticket_branch="b", worktree="/w", out_dir="/o", error="e"),
                    close_card(card_id="K-1", title="t", description="d", worktree="/w", out_dir="/o",
                               tickets=[], manual=False),
                    resume_step("K-1", "PT-1", "/o")):
            self.assertIn('"options"', cmd)


if __name__ == "__main__":
    unittest.main()


class RepeatedErrorTest(unittest.TestCase):
    def test_same_reason_twice_does_not_recommend_plain_retry(self):
        # 03.11, K-16/LEGAL-67: ten sam powód w kolejnych próbach, a okno dalej polecało „Ponów krok”.
        from tide_daemon.cardflow.decisions import build_decisions
        from tide_daemon.cardflow.state import fold
        events = (Events().step("s0", ticket="PT-1", kind="fix", result="error", reason="B1 nadal otwarte: brak wyniku osobnego ticketu T0/T1/R/V do zamknięcia AC3")
                  .step("s", ticket="PT-1", kind="fix", attempt=1, parent="s0", result="error", reason="B1 nadal otwarte: brak wyniku osobnego ticketu T0/T1/R/V do zamknięcia AC3"))
        made = build_decisions(fold(events.items))[0]
        self.assertFalse(any(o["recommended"] for o in made["options"] if o["choice"] == "retry"))
        self.assertIn("Ten sam powód", made["why"])
        self.assertEqual(made["reply"]["label"], "Ponów z instrukcją")


class RejectedTicketDecisionTest(unittest.TestCase):
    def test_rejected_ticket_shows_no_decision(self):
        # 03.11, K-16/LEGAL-75: przebieg odrzuconego ticketu skończył się pytaniem i karta wołała użytkownika.
        from tide_daemon.cardflow.decisions import build_decisions
        from tide_daemon.cardflow.state import fold
        events = Events().step("s", ticket="PT-1", kind="ticket", result="question", question="Co dalej?")
        events.add("ticket.rejected", ticket="PT-1")
        self.assertEqual(build_decisions(fold(events.items)), [])


class OutsideRunGuardTest(unittest.TestCase):
    def test_running_outside_command_is_work_not_a_stall(self):
        # 03.11, K-31: „karta stoi: brak kroku i powodu czekania” w trakcie testów poza piaskownicą.
        from tide_daemon.cardflow.state import fold, guard_reason
        events = Events().step("s", ticket="PT-1", kind="ticket", result="denied", command="npm run e2e")
        events.add("step.decision", step="s", ticket="PT-1", choice="run", command="npm run e2e")
        events.add("outside.started", step="s", ticket="PT-1", command="npm run e2e")
        self.assertIsNone(guard_reason(fold(events.items)))
        events.add("outside.finished", step="s", ticket="PT-1", exit=0, tail="")
        self.assertEqual(fold(events.items).outside_started, {})


class PlanRevisionInProgressTest(unittest.TestCase):
    def test_returned_plan_is_not_a_decision_while_the_planner_revises_it(self):
        # 03.11, K-40: po „Odeślij z uwagami” okno dalej pokazywało plan do zatwierdzenia.
        from tide_daemon.cardflow.decisions import build_decisions
        from tide_daemon.cardflow.state import fold
        events = (Events(entry="plan").add("plan.proposed", planner="Opus", rows=[])
                  .add("plan.reviewed", verdict="REQUEST_CHANGES", findings="x"))
        self.assertEqual([d["kind"] for d in build_decisions(fold(events.items))], ["plan-approval"])
        events.add("plan.returned", feedback="popraw", baseline=[])
        self.assertEqual(build_decisions(fold(events.items)), [])


class CorrectedPlanReviewTest(unittest.TestCase):
    def test_plan_revised_after_review_is_offered_for_approval_not_sent_back(self):
        # 03.11, K-38: po automatycznej poprawce okno pisało „recenzent prosi o zmiany” i polecało odesłanie.
        from tide_daemon.cardflow.decisions import build_decisions
        from tide_daemon.cardflow.state import fold
        findings = [{"severity": "blocker", "text": "graf blokerów"}]
        events = (Events(entry="plan").add("plan.proposed", planner="Fable", rows=[{"workId": "#1"}])
                  .add("plan.reviewed", verdict="REQUEST_CHANGES", reviewer="Sonnet", findings=findings, corrected=True))
        decision = build_decisions(fold(events.items))[0]
        self.assertIn("poprawił go po uwagach", decision["happened"])
        recommended = [o["id"] for o in decision["options"] if o.get("recommended")]
        self.assertEqual(recommended, ["approve"])
        returned = next(o for o in decision["options"] if o["id"] == "return")
        self.assertEqual(returned["params"]["findings"], [])


class PlanWorkInProgressTest(unittest.TestCase):
    def test_review_of_a_revised_plan_is_progress_not_a_decision(self):
        # 03.11, K-38: po odesłaniu kafelek i okno dalej pokazywały „plan do zatwierdzenia”.
        from tide_daemon.cardflow.decisions import build_decisions
        events = (Events(entry="plan").add("plan.proposed", planner="Fable", rows=[{"workId": "#1"}])
                  .add("plan.reviewed", verdict="REQUEST_CHANGES", reviewer="Sonnet", findings="x")
                  .add("plan.returned", feedback="popraw", baseline=[])
                  .add("plan.revision-ready", pending=True, baseline=[], difference=[])
                  .add("step.started", step="K-1-plan-review-9", kind="plan-review"))
        st = fold(events.items)
        self.assertEqual(build_decisions(st), [])
        shown = derived(st, CARD)
        self.assertEqual((shown["column"], shown["note"]), ("progress", "review planu w tle"))


class FlowErrorRecoveryTest(unittest.TestCase):
    def test_flow_error_clears_when_the_failed_operation_succeeds(self):
        # 04.11, K-38: zapis planu przeszedł po naprawie gita, a kafelek dalej pokazywał błąd przepływu.
        events = (Events(entry="plan").add("result", action="plan-commit", key="1", ok=False,
                                           error="worktree karty nie należy do gałęzi planu")
                  .add("flow.error", error="FlowError: worktree karty nie należy do gałęzi planu"))
        self.assertTrue(fold(events.items).flow_error)
        events.add("result", action="merge", key="2", ok=True)
        self.assertTrue(fold(events.items).flow_error)
        events.add("result", action="plan-commit", key="1", ok=True)
        self.assertEqual(fold(events.items).flow_error, "")


class ReviewRoundsOptionsTest(unittest.TestCase):
    def test_review_rounds_offer_merge_despite_remarks(self):
        # 04.11: po K-35 okno decyzji po rundach review miało tylko „Ponów poprawki” i „Odrzuć ticket”.
        from tide_daemon.cardflow.decisions import _review
        decision = _review(fold(Events().items), "PT-1", 6, {"question": "Co dalej?"})
        options = {o["id"]: o for o in decision["options"]}
        self.assertEqual(list(options), ["retry", "accept", "reject"])
        self.assertEqual(options["accept"]["params"], {"choice": "accept"})
        self.assertTrue(options["retry"]["recommended"])


class QuestionLayoutTest(unittest.TestCase):
    def test_inline_options_get_their_own_lines_and_commands_are_copyable(self):
        # 04.11: pytania z opcjami a, b, c w jednym akapicie i poleceniami do wpisania w terminalu w środku tekstu.
        from tide_daemon.cardflow.decisions import layout, copy_commands
        text = layout("Jak dalej? **(a)** uruchom `bash scripts/p5.sh`, (b) poczekaj na `make box-install`, (c) zamknij.")
        self.assertEqual(text.split("\n"), ["Jak dalej?", "(a) uruchom `bash scripts/p5.sh`,",
                                            "(b) poczekaj na `make box-install`,", "(c) zamknij."])
        self.assertEqual(layout("Pytanie bez listy (a) tylko raz."), "Pytanie bez listy (a) tylko raz.")
        self.assertEqual(copy_commands("Uruchom `bash scripts/p5.sh`, plik `docs/x.md`, `make box-install`."),
                         ["bash scripts/p5.sh", "make box-install"])

    def test_question_decision_keeps_lines_and_lists_commands(self):
        events = Events().step("K-1-PT-1-1", result="question",
                               question="Co robimy? (a) uruchom `bash x.sh --all`, (b) zostaw.")
        decision = build_decisions(events.state())[0]
        self.assertIn("\n(b) zostaw.", decision["happened"])
        self.assertEqual(decision["copyCommands"], ["bash x.sh --all"])


class OutsideHeadlineTest(unittest.TestCase):
    def test_long_command_stays_out_of_the_headline_and_why_keeps_lines(self):
        # 04.11, KAMP-1: nagłówek był kilkulinijkowym zlepkiem z całym `bash -c '…'`.
        long_command = "bash -c 'for f in \"$HOME\"/*.mp4; do ffmpeg -i \"$f\" out/%05d.jpg; done; du -sh out'"
        events = Events().step("K-1-PT-1-1", result="denied", outside=True, command=long_command,
                               reason="Nagrania leżą w katalogu domowym.", canAllow=False)
        decision = build_decisions(events.state())[0]
        self.assertNotIn("ffmpeg", decision["happened"])
        self.assertTrue(decision["happened"].endswith("prosi o uruchomienie polecenia poza piaskownicą"))
        self.assertEqual(decision["technical"]["command"], long_command)
        self.assertIn("\nPowód kroku: Nagrania leżą w katalogu domowym.", decision["why"])


class OutsideQueueTest(unittest.TestCase):
    def test_consented_outside_command_waiting_for_a_slot_is_not_a_stall(self):
        # 04.11, K-37: zgoda na `make test` dana, polecenie czeka na wolne miejsce w projekcie → „stoi” po 3 min.
        from tide_daemon.cardflow.state import guard_reason, outside_queued
        events = Events().step("K-1-PT-1-1", result="denied", outside=True, command="make test")
        events.add("step.decision", step="K-1-PT-1-1", ticket="PT-1", choice="run")
        st = events.state()
        self.assertEqual(outside_queued(st), ["K-1-PT-1-1"])
        self.assertEqual(guard_reason(st), "polecenie poza piaskownicą w kolejce")
        events.add("outside.started", step="K-1-PT-1-1")
        self.assertEqual(outside_queued(events.state()), [])
