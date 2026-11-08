"""PT-145 i PT-154: akcje przy czekających decyzjach oraz decyzje samego opiekuna."""
import json
import os
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from . import flowfakes
from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_caretaker_retry import EngineOnJournal
from tide_daemon.cardflow import caretaker, decisions
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import attention_step, fold
from apps.board.tests.decisionfix import journal_caretaker_card


class CaretakerDecisionTest(unittest.TestCase):
    def engine(self, trigger="flow-error", limit=False):
        events = journal_caretaker_card(trigger, limit)
        engine = FlowEngine.__new__(FlowEngine)
        engine.clock = lambda: 1_800_000_000.0
        engine._settings_data = mock.Mock(return_value={})
        engine.models_config = engine.catalog = None

        def append(card, kind, /, **fields):
            event = {"seq": len(events) + 1, "ts": "2026-11-03T11:01:00+01:00", "type": kind, **fields}
            events.append(event)
            return event

        engine.journal = SimpleNamespace(read=lambda card: events, state=lambda card: fold(events), append=append)
        engine._notices = []
        engine._caretaker_withheld = mock.Mock(return_value=set())
        engine._limit_view = mock.Mock()
        engine._environment_view = mock.Mock()
        return engine, events

    def test_both_triggers_and_limit_give_a_decision_and_one_notice_even_after_restart(self):
        for trigger in ("flow-error", "stalled"):
            for limit in (False, True):
                with self.subTest(trigger=trigger, limit=limit):
                    engine, events = self.engine(trigger, limit)
                    for _ in range(2):
                        shown = engine.derived({"id": "K-8", "column": "todo"})
                        self.assertEqual(shown["column"], "waiting")
                        [decision] = shown["flow"]["decisions"]
                        self.assertEqual(decision["kind"], "caretaker")
                        self.assertTrue(decision["happened"] and decision["why"])
                        self.assertTrue(decision["caretaker"]["summary"] and decision["caretaker"]["recommendation"])
                        self.assertEqual([o["title"] for o in decision["options"]],
                                         ["Ponów etap", "Zamknij", "Porozmawiaj z opiekunem"])
                        engine._queue_notices("K-8")
                    self.assertEqual(len(engine.take_notices()), 1)
                    engine._queue_notices("K-8")
                    self.assertEqual(engine.take_notices(), [])

    def test_choices_close_the_problem_reset_count_and_only_retry_runs_the_stage(self):
        for trigger in ("flow-error", "stalled"):
            for choice in ("retry-stage", "dismiss"):
                with self.subTest(trigger=trigger, choice=choice):
                    engine, events = self.engine(trigger)
                    engine._advance_card = mock.Mock(return_value=[])
                    reply = engine.resolve_caretaker("K-8", {"choice": choice})
                    self.assertTrue(reply["ok"])
                    self.assertEqual(engine._advance_card.call_count, int(choice == "retry-stage"))
                    st = fold(events)
                    self.assertEqual(caretaker.owned(st, reply["problem"])["count"], 0)
                    self.assertFalse(caretaker.owned(st, reply["problem"])["handed"])
                    self.assertEqual(caretaker.problems(st), [])
                    if trigger == "flow-error":
                        engine.journal.append("K-8", "flow.error", stage="advance", error="Nowy błąd")
                        engine.journal.append("K-8", "flow.error-persisted", stage="advance", error="Nowy błąd")
                    else:
                        engine.journal.append("K-8", "card.stalled")
                    self.assertEqual(caretaker.owned(fold(events), reply["problem"])["count"], 0)
                    self.assertEqual(engine.derived({"id": "K-8", "column": "todo"})["flow"]["decisions"], [])

    def test_each_escalated_stage_describes_its_own_error_in_decision_and_notice(self):
        engine, events = self.engine()
        engine.journal.append("K-8", "flow.error", stage="close", error="Nie udało się domknąć karty.")
        engine.journal.append("K-8", "flow.error-persisted", stage="close", error="Nie udało się domknąć karty.")
        engine.journal.append("K-8", "caretaker.started", problem="flow-error:close", step="K-8-care-2",
                              target="", trigger="flow-error", n=1)
        engine.journal.append("K-8", "caretaker.escalated", problem="flow-error:close",
                              step="K-8-care-2", target="", summary="Nie udało się domknąć karty.")
        shown = engine.derived({"id": "K-8", "column": "todo"})
        expected = {"flow-error:advance": "Nie udało się przygotować etapu karty.",
                    "flow-error:close": "Nie udało się domknąć karty."}
        decisions = shown["flow"]["decisions"]
        self.assertEqual({d["technical"]["problem"]: d["happened"] for d in decisions}, expected)
        engine._queue_notices("K-8")
        self.assertEqual({n["step"]: n["detail"] for n in engine.take_notices()},
                         {d["id"]: expected[d["technical"]["problem"]] for d in decisions})
        engine._queue_notices("K-8")
        self.assertEqual(engine.take_notices(), [])

    def test_caretaker_decision_takes_priority_over_parallel_ticket_work(self):
        for stage in ("close", "advance:PT-1"):
            with self.subTest(stage=stage):
                engine, events = self.engine()
                for event in events:
                    if event.get("stage") == "advance":
                        event["stage"] = stage
                    if event.get("problem") == "flow-error:advance":
                        event["problem"] = "flow-error:" + stage
                engine.journal.append("K-8", "step.started", step="PT-99-ticket", kind="ticket",
                                      ticket="PT-99", executor="Sonnet")
                shown = engine.derived({"id": "K-8", "column": "todo"})
                self.assertEqual(shown["column"], "waiting")
                [decision] = shown["flow"]["decisions"]
                self.assertEqual(decision["kind"], "caretaker")
                self.assertEqual(decision["happened"], "Nie udało się przygotować etapu karty.")
                self.assertEqual(shown["flow"]["flowError"], decision["happened"])
                self.assertIn("flowErrorHint", shown["flow"])
                self.assertEqual([s["step"] for s in fold(events).running()], ["PT-99-ticket"])
                [running] = [s for s in shown["flow"]["steps"] if s["id"] == "PT-99-ticket"]
                self.assertEqual(running["result"], "")
                engine.resolve_caretaker("K-8", {"choice": "dismiss"})
                self.assertEqual(engine.derived({"id": "K-8", "column": "todo"})["column"], "progress")
                self.assertEqual([s["step"] for s in fold(events).running()], ["PT-99-ticket"])

    def test_bad_choice_and_stale_problem_do_not_write(self):
        engine, events = self.engine()
        for request, code in (({"choice": "merge"}, "bad-choice"),
                              ({"choice": "dismiss", "problem": "stalled:card"}, "not-waiting"),
                              ({"choice": "dismiss", "caretaker": True}, "bad-request")):
            before = list(events)
            with self.assertRaises(FlowError) as caught:
                engine.resolve_caretaker("K-8", request)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(events, before)

    def test_parallel_ticket_completion_preserves_unresolved_stage_decision(self):
        for stage in ("close", "advance:PT-1"):
            for limit in (False, True):
                with self.subTest(stage=stage, limit=limit):
                    engine, events = self.engine(limit=limit)
                    problem = "flow-error:" + stage
                    for event in events:
                        if event.get("stage") == "advance":
                            event["stage"] = stage
                        if event.get("problem") == "flow-error:advance":
                            event["problem"] = problem
                    before = engine.derived({"id": "K-8", "column": "todo"})["flow"]["decisions"]
                    engine._queue_notices("K-8")
                    self.assertEqual(len(engine.take_notices()), 1)
                    engine.journal.append("K-8", "step.started", step="PT-99-ticket", kind="ticket",
                                          ticket="PT-99", executor="Sonnet")
                    engine.journal.append("K-8", "step.finished", step="PT-99-ticket", result="done")
                    st = fold(events)
                    self.assertIn(stage, st.flow_errors)
                    self.assertIn(problem, [p["id"] for p in caretaker.problems(st)])
                    own = caretaker.owned(st, problem)
                    self.assertEqual((own["count"], own["handed"]), (1, True))
                    shown = engine.derived({"id": "K-8", "column": "todo"})
                    self.assertEqual(shown["column"], "waiting")
                    self.assertEqual(shown["flow"]["decisions"], before)
                    engine._queue_notices("K-8")
                    self.assertEqual(engine.take_notices(), [])
                    self.assertTrue(engine.resolve_caretaker("K-8", {"choice": "dismiss"})["ok"])
                    self.assertEqual(caretaker.owned(fold(events), problem)["count"], 0)

    def test_stage_recovery_closes_escalation_and_resets_count_before_recurrence(self):
        for stage, kind, ticket in (("close", "close", ""), ("advance:PT-1", "ticket", "PT-1")):
            with self.subTest(stage=stage):
                engine, events = self.engine()
                problem = "flow-error:" + stage
                for event in events:
                    if event.get("stage") == "advance":
                        event["stage"] = stage
                    if event.get("problem") == "flow-error:advance":
                        event["problem"] = problem
                engine.journal.append("K-8", "step.started", step="recovery", kind=kind, ticket=ticket)
                engine.journal.append("K-8", "step.finished", step="recovery", result="done")
                self.assertNotIn(stage, fold(events).flow_errors)
                engine.journal.append("K-8", "flow.error", stage=stage, error="Nowy błąd")
                engine.journal.append("K-8", "flow.error-persisted", stage=stage, error="Nowy błąd")
                own = caretaker.owned(fold(events), problem)
                self.assertEqual((own["count"], own["handed"]), (0, False))
                self.assertEqual(engine.derived({"id": "K-8", "column": "todo"})["flow"]["decisions"], [])

    def test_pause_and_manual_backlog_take_priority_over_caretaker_decision(self):
        for entry in ("now", "plan"):
            for paused, column in ((True, "todo"), (False, "backlog"), (True, "backlog")):
                with self.subTest(entry=entry, paused=paused, column=column):
                    engine, events = self.engine()
                    events[0]["entry"] = entry
                    if column == "backlog":
                        engine.journal.append("K-8", "card.moved", **{"from": "waiting", "to": "backlog"})
                    if paused:
                        engine.journal.append("K-8", "card.paused")
                    shown = engine.derived({"id": "K-8", "column": column})
                    if not paused:
                        self.assertIsNone(shown)
                    else:
                        self.assertEqual(shown["column"], "backlog" if column == "backlog" else "progress")
                        self.assertTrue(shown["flow"]["paused"])
                        self.assertIn("wstrzymana", shown["note"])
                        self.assertEqual([d["kind"] for d in shown["flow"]["decisions"]], ["caretaker"])
                    self.assertTrue(caretaker.owned(fold(events), "flow-error:advance")["handed"])


class DecisionActionsTest(EngineOnJournal):
    def setUp(self):
        super().setUp()
        self.now = 1_800_000_000.0
        self.engine.clock = self.journal.clock = lambda: self.now
        self.engine.which = lambda name: "/fake/" + name
        for method in ("_guard_check", "_guard_rebase"):
            patcher = mock.patch.object(self.engine, method, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.scenario = 0
        self.reset_scenario()

    def reset_scenario(self):
        self.scenario += 1
        self.now = 1_800_000_000.0
        self.journal = self.engine.journal = Journal(os.path.join(self.base, f"scenario-{self.scenario}"),
                                                   mirror=os.path.join(self.base, f"mirror-{self.scenario}"))
        self.journal.clock = lambda: self.now
        self.add("K-1", "card.started", entry="now", executor="Sonnet", provider="anthropic",
                 preferredProvider="anthropic", reviewer="Opus", reviewerModel="opus",
                 reviewerProvider="anthropic", column="todo")

    def target(self, result="question", kind="ticket", **extra):
        self.add("K-1", "step.started", step="target", kind=kind, ticket="PT-7" if kind in caretaker.TICKET_KINDS else "",
                 executor="Sonnet", model="sonnet", provider="anthropic", attempt=1, **extra)
        self.add("K-1", "step.finished", step="target", result=result, question="Gdzie jest konfiguracja?",
                 reason="awaria testowa" if result == "error" else "", command="make test" if result == "denied" else "")

    def state(self):
        return self.engine._caretaker_state(fold(self.journal.read("K-1")))

    def problem(self):
        return caretaker.problems(self.state())[0]

    def caretaker_run(self, problem=None):
        problem = problem or self.problem()
        return self.add("K-1", caretaker.STARTED, problem=problem["id"], key="caretaker:" + problem["id"] + ":1",
                        n=1, step="care", target=problem["step"], root=problem["root"], ticket=problem["ticket"],
                        trigger=problem["trigger"], decision=problem["decision"], watermark=self.state().last_seq)

    def act(self, run, action, **params):
        self.engine._caretaker_act("K-1", run, action, params)
        st = self.state()
        return st, st.caretaker_outcomes[run["step"]]

    def test_timer_uses_saved_minutes_and_starts_only_once(self):
        self.target()
        self.engine.set_flow_settings({"caretakerDecisionMinutes": 2})
        with mock.patch.object(self.engine, "_caretaker_launch") as launch:
            for elapsed in (0, 119, 120):
                self.now = 1_800_000_000 + elapsed
                self.engine._caretake("K-1", self.state())
                launch.assert_not_called()
            self.now += 1
            self.engine._caretake("K-1", self.state())
            launch.assert_called_once()
            run = launch.call_args.args[2]
            self.assertEqual(run["problem"], "waiting:step:target")
            self.add("K-1", "step.started", step=run["step"], kind="caretaker", ticket="PT-7")
            self.engine._caretake("K-1", self.state())
            launch.assert_called_once()

    def test_answer_resolves_technical_questions_and_history(self):
        for kind in caretaker.TICKET_KINDS:
            with self.subTest(kind=kind):
                # Osobny dziennik na każdy rodzaj kroku.
                if kind != "ticket":
                    self.reset_scenario()
                self.target(kind=kind)
                self.now += 601
                problem = self.problem()
                parsed = caretaker.parse_result({"result": "done", "action": "answer", "step": "target",
                                                "category": "technical", "answer": "Plik config.json.",
                                                "reason": "Ścieżka jest w README."}, problem, self.state())
                self.assertTrue(parsed["ok"], parsed)
                st, outcome = self.act(self.caretaker_run(problem), parsed["action"], **parsed["params"])
                self.assertTrue(outcome["ok"], outcome)
                self.assertEqual(st.step_decisions["target"]["answer"], "Plik config.json.")
                self.assertIn("Opiekun: odpowiedział na pytanie", caretaker.history_text(outcome))
                self.assertTrue(caretaker.history_text(outcome).endswith("— Ścieżka jest w README."))

    def test_waiting_ticket_question_can_return_the_plan_for_a_missing_ticket(self):
        from .test_cardflow_caretaker_gap import ROWS, PARAMS

        self.add("K-1", "plan.proposed", planner="Sonnet", rows=ROWS, prefix="PT")
        self.add("K-1", "plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])
        self.add("K-1", "plan.approved", rows=ROWS, map={"#1": "PT-7"})
        self.target()
        self.now += 601
        problem = self.problem()
        self.assertEqual((problem["trigger"], problem["decisionKind"]), ("waiting", "question"))
        parsed = caretaker.parse_result({"result": "done", "action": "return-plan", **PARAMS},
                                        problem, self.state())
        self.assertTrue(parsed["ok"], parsed)
        run = self.caretaker_run(problem)
        st, outcome = self.act(run, parsed["action"], **parsed["params"])
        self.assertTrue(outcome["ok"], outcome)
        [returned] = st.plan_returns
        self.assertEqual((returned["caretaker"], returned["caretakerFrom"], returned["feedback"]),
                         (problem["id"], "target", PARAMS["feedback"]))
        self.assertEqual(st.plan_approved["rows"], ROWS)
        self.assertEqual(st.finished["target"]["result"], "question")
        self.assertIsNone(st.plan_resumes["target"])
        self.assertEqual(decisions.build_decisions(st), [])
        self.assertFalse([e for e in self.journal.read("K-1") if e["type"] == "plan.revision-approved"])
        st, _ = self.act(run, parsed["action"], **parsed["params"])
        self.assertEqual(len(st.plan_returns), 1)

    def test_return_plan_cannot_resolve_protected_waiting_decisions_at_runtime(self):
        for kind in ("permission", "plan-approval", "review"):
            with self.subTest(kind=kind):
                if kind != "permission":
                    self.reset_scenario()
                self.protected(kind)
                st, outcome = self.act(self.caretaker_run(), "return-plan", feedback="Dodaj ticket.", reason="Luka.")
                self.assertEqual(outcome["code"], caretaker.BAD_ACTION)
                self.assertFalse(st.plan_returns)
                self.assertIsNone(st.plan_approved)
                self.assertEqual(st.step_decisions, {})
                self.assertEqual(st.review_decisions, {})

    def test_planning_questions_fail_with_a_visible_recommendation(self):
        for kind in ("planning", "plan-amend", "plan-fix", "plan-review"):
            with self.subTest(kind=kind):
                if kind != "planning":
                    self.reset_scenario()
                self.target(kind=kind)
                self.now += 601
                st, outcome = self.act(self.caretaker_run(), "answer", step="target", answer="Wybierz A.",
                                       category="technical", reason="Tak polecam.")
                self.assertTrue(outcome["failed"], outcome)
                self.assertEqual(outcome["code"], "substantive-question")
                self.assertNotIn("target", st.step_decisions)
                shown = decisions.build_decisions(st)
                self.assertEqual(shown[0]["kind"], "question")
                self.assertTrue(shown[0]["caretaker"]["recommendation"])
                self.assertEqual(caretaker.owned(st, "waiting:step:target")["count"], 1)

    def protected(self, kind):
        if kind == "permission":
            self.target(result="denied")
        elif kind == "plan-approval":
            self.add("K-1", "card.started", entry="planning", executor="Sonnet", provider="anthropic")
            self.add("K-1", "plan.proposed", planner="Sonnet", rows=[{"id": "#1", "title": "Praca"}])
            self.add("K-1", "plan.reviewed", reviewer="Opus", verdict="APPROVE")
        else:
            self.target(result="done", kind="review")
            self.add("K-1", "review.question", ticket="PT-7", round=3, question="Pozostały uwagi.")
        self.now += 601

    def test_user_only_decisions_stay_visible_and_accept_only_escalate(self):
        for kind in ("permission", "plan-approval", "review"):
            with self.subTest(kind=kind):
                if kind != "permission":
                    self.reset_scenario()
                self.protected(kind)
                problem = self.problem()
                self.assertEqual(problem["decisionKind"], kind)
                self.assertEqual(set(caretaker.allowed_actions(problem)), {"escalate"})
                for action in set(caretaker.ACTIONS) - {"escalate"}:
                    self.assertFalse(caretaker.parse_result({"result": "done", "action": action},
                                                           problem, self.state())["ok"], action)
                run = self.caretaker_run(problem)
                self.assertIn(problem["decision"], [d["id"] for d in decisions.build_decisions(self.state())])
                if kind == "permission":
                    self.assertEqual(attention_step(self.state(), user_only=True), "target")
                st, _ = self.act(run, "escalate", summary="Sprawdziłem dziennik.", recommendation="Sprawdź polecenie.")
                shown = next(d for d in decisions.build_decisions(st) if d["id"] == problem["decision"])
                self.assertEqual(shown["caretaker"]["recommendation"], "Sprawdź polecenie.")
                self.assertIsNone(st.plan_approved)
                self.assertEqual(st.step_decisions, {})
                self.assertEqual(st.review_decisions, {})
                with mock.patch.object(self.engine, "_caretaker_launch") as launch:
                    self.engine._caretake("K-1", st)
                    launch.assert_not_called()

    def test_runtime_also_rejects_retry_for_permission(self):
        self.protected("permission")
        st, outcome = self.act(self.caretaker_run(), "retry", step="target", instruction="Uruchom.", reason="Powód.")
        self.assertTrue(outcome["failed"])
        self.assertEqual(st.step_decisions, {})
        self.assertEqual(st.step_retries, {})

    def test_answer_category_is_validated_in_tide(self):
        self.target()
        self.now += 601
        data = {"result": "done", "action": "answer", "step": "target", "answer": "Plik config.json.",
                "reason": "Jest w README."}
        for category in ("technical", "environment", "business", "", None):
            with self.subTest(category=category):
                parsed = caretaker.parse_result({**data, "category": category}, self.problem(), self.state())
                self.assertEqual(parsed["ok"], category in ("technical", "environment"))

    def test_raising_the_threshold_does_not_stale_an_active_problem(self):
        self.target()
        self.now += 601
        run = self.caretaker_run()
        self.engine.set_flow_settings({"caretakerDecisionMinutes": 1440})
        st, outcome = self.act(run, "escalate", summary="Sprawdziłem.", recommendation="Odpowiedz.")
        self.assertFalse(outcome.get("stale"))
        self.assertEqual(decisions.build_decisions(st)[0]["caretaker"]["recommendation"], "Odpowiedz.")

    def test_a_later_error_does_not_inherit_the_resolved_waiting_problem_id(self):
        self.target()
        self.now += 601
        self.act(self.caretaker_run(), "answer", step="target", answer="Plik config.json.",
                 category="environment", reason="Jest w README.")
        self.add("K-1", "step.started", step="child", parentStep="target", kind="ticket", ticket="PT-7", attempt=1)
        self.add("K-1", "step.finished", step="child", result="error", reason="Awaria.")
        self.assertEqual([p["id"] for p in caretaker.problems(self.state())], ["error:target"])

    def test_user_resolution_of_each_protected_decision_makes_the_result_stale(self):
        for kind in ("permission", "plan-approval", "review"):
            with self.subTest(kind=kind):
                if kind != "permission":
                    self.reset_scenario()
                self.protected(kind)
                run = self.caretaker_run()
                if kind == "permission":
                    self.engine.resolve_step("K-1", {"step": "target", "choice": "deny"})
                elif kind == "plan-approval":
                    self.add("K-1", "plan.approved", rows=[])
                else:
                    self.add("K-1", "review.decision", ticket="PT-7", round=3, choice="retry")
                st, outcome = self.act(run, "escalate", summary="Spóźniona analiza.", recommendation="Spóźniona rada.")
                self.assertTrue(outcome["stale"])
                self.assertFalse(st.caretaker_escalations)
                self.assertEqual(caretaker.owned(st, run["problem"])["count"], 0)

    def test_user_resolves_final_review_while_caretaker_runs(self):
        for choice in ("retry", "reject", "accept"):
            with self.subTest(choice=choice):
                self.reset_scenario()
                self.add("K-1", "step.started", step="review", kind="review", ticket="PT-7", round=3)
                self.add("K-1", "step.finished", step="review", result="done")
                self.add("K-1", "review.verdict", ticket="PT-7", round=3, verdict="REQUEST_CHANGES")
                self.add("K-1", "review.question", ticket="PT-7", round=3, question="Pozostały uwagi.")
                self.now += 601
                run = self.caretaker_run()
                self.add("K-1", "step.started", step=run["step"], kind="caretaker", ticket="PT-7")
                self.assertIn("review:PT-7:3", [d["id"] for d in decisions.build_decisions(self.state())])
                result = self.engine.resolve_review("K-1", choice)
                self.assertTrue(result["ok"])
                self.assertIn(run["step"], [s["step"] for s in self.state().running()])
                st, outcome = self.act(run, "escalate", summary="Spóźniona analiza.", recommendation="Spóźniona rada.")
                self.assertEqual(st.review_decisions[("PT-7", 3)]["choice"], choice)
                self.assertTrue(outcome["stale"])
                self.assertFalse(st.caretaker_escalations)
                self.assertEqual(caretaker.owned(st, run["problem"])["count"], 0)

    def test_running_ticket_still_blocks_final_review_resolution(self):
        self.add("K-1", "step.started", step="review", kind="review", ticket="PT-7", round=3)
        self.add("K-1", "step.finished", step="review", result="done")
        self.add("K-1", "review.verdict", ticket="PT-7", round=3, verdict="REQUEST_CHANGES")
        self.add("K-1", "review.question", ticket="PT-7", round=3, question="Pozostały uwagi.")
        self.add("K-1", "step.started", step="fix", kind="fix", ticket="PT-7")
        for choice in ("retry", "reject", "accept"):
            with self.subTest(choice=choice):
                with self.assertRaises(FlowError) as raised:
                    self.engine.resolve_review("K-1", choice)
                self.assertEqual(raised.exception.code, "no-review-question")
        self.assertFalse(self.state().review_decisions)

    def test_another_ticket_does_not_close_the_plan_recommendation_after_restart(self):
        self.protected("plan-approval")
        run = self.caretaker_run()
        self.act(run, "escalate", summary="Sprawdziłem plan.", recommendation="Przeczytaj uwagi.")
        self.add("K-1", "step.started", step="other", ticket="PT-8", kind="ticket")
        self.add("K-1", "step.finished", step="other", result="done")
        # Złożenie od nowa nie korzysta z pamięci poprzedniego przebiegu.
        st = self.state()
        self.assertEqual(decisions.build_decisions(st)[0]["caretaker"]["recommendation"], "Przeczytaj uwagi.")
        self.assertTrue(caretaker.owned(st, run["problem"])["handed"])
        with mock.patch.object(self.engine, "_caretaker_launch") as launch:
            self.engine._caretake("K-1", st)
            launch.assert_not_called()

    def test_user_answer_wins_over_the_running_caretaker(self):
        self.target()
        self.now += 601
        run = self.caretaker_run()
        self.engine.resolve_step("K-1", {"choice": "answer", "step": "target", "answer": "Moja odpowiedź."})
        st, outcome = self.act(run, "answer", step="target", answer="Spóźniona.", category="technical", reason="Powód.")
        self.assertTrue(outcome["stale"])
        self.assertEqual(st.step_decisions["target"]["answer"], "Moja odpowiedź.")
        self.assertEqual(caretaker.owned(st, run["problem"])["count"], 0)

    def test_change_executor_reuses_resolve_validation_without_mutating_the_step_on_failure(self):
        cases = [("Nie istnieje", {}, {}, "katalogu"),
                 ("Opus", {"local": True}, {}, "lokal"),
                 ("Opus", {}, {"reviewerModel": "opus"}, "model"),
                 ("Haiku", {}, {"preferredProvider": "local"}, "lokal"),
                 ("Haiku", {}, {"preferredProvider": "mixed", "reviewerModel": "opus",
                                "reviewerProvider": "anthropic"}, "dostawc")]
        for index, (executor, fields, settings, error) in enumerate(cases):
            with self.subTest(executor=executor, fields=fields, settings=settings):
                if index:
                    self.reset_scenario()
                if settings:
                    self.add("K-1", "card.started", entry="now", executor="Sonnet", provider="anthropic",
                             reviewer="Opus", **settings)
                self.target(result="error", **fields)
                before = self.state().steps["target"]
                st, outcome = self.act(self.caretaker_run(), "change-executor", step="target", executor=executor, reason="Powód.")
                self.assertFalse(outcome["ok"], outcome)
                self.assertIn(error, outcome["error"])
                self.assertEqual(st.steps["target"], before)
                self.assertEqual(st.step_decisions, {})
                self.assertEqual(caretaker.owned(st, outcome["problem"])["count"], 1)
                self.assertFalse(st.open_intents(include_background=True))

    def test_change_executor_and_answer_reconcile_their_own_decision_once(self):
        for action in ("change-executor", "answer"):
            with self.subTest(action=action):
                if action == "answer":
                    self.reset_scenario()
                self.target(result="error" if action == "change-executor" else "question")
                self.now += 601
                run = self.caretaker_run()
                params = {"step": "target", "executor": "Haiku", "instruction": "Sprawdź plik.",
                          "answer": "Plik config.json.", "category": "environment", "reason": "Powód."}
                # Restart po skutku, przed wynikiem pary: ręcznie pozostawiamy otwarty zamiar.
                self.add("K-1", "intent", action="caretaker-action", key=run["step"], caretakerAction=action,
                         params=params, problem=run["problem"])
                self.add("K-1", "step.decision", step="target", choice=action, executor="Haiku", answer=params["answer"],
                         caretaker=run["problem"], caretakerStep=run["step"], auto=True)
                self.engine._caretaker_act("K-1", run, action, params)
                self.engine._caretaker_act("K-1", run, action, params)
                st = self.state()
                self.assertTrue(st.caretaker_outcomes[run["step"]]["ok"])
                self.assertFalse(st.open_intents(include_background=True))
                self.assertEqual(len([e for e in self.journal.read("K-1") if e["type"] == "step.decision"]), 1)

    def reviewer_history(self, review, reassigned, *, newer_override, preferred="anthropic"):
        self.reset_scenario()
        self.add("K-1", "ticket.assigned", ticket="PT-7", executor="Sonnet", model="sonnet",
                 provider="anthropic", preferredProvider=preferred, reviewer="Opus",
                 reviewerModel="opus", reviewerProvider="anthropic")
        catalog = self.engine.catalog()

        def history():
            executor = catalog.get(review)
            self.add("K-1", "step.started", step="past-review", kind="review", ticket="PT-7", round=1,
                     executor=executor.name, model=executor.model, provider=executor.provider,
                     program=executor.program, effort=executor.effort)
            self.add("K-1", "step.finished", step="past-review", result="done")

        def reassign():
            executor = catalog.get(reassigned)
            self.add("K-1", "reviewer.reassigned", ticket="PT-7", reviewer=executor.name,
                     reviewerModel=executor.model, reviewerProvider=executor.provider,
                     reviewerProgram=executor.program, reviewerEffort=executor.effort)

        if newer_override:
            history()
            reassign()
        else:
            reassign()
            history()
        self.target(result="error", kind="fix")

    def test_change_executor_rejects_the_current_reviewers_model_and_mixed_provider(self):
        cases = [("Opus", "Haiku", True, "Haiku", "anthropic", "bad-executor"),
                 ("Opus", "Codex mocny", True, "Codex lekki", "mixed", "bad-executor-provider"),
                 ("Haiku", "Opus", False, "Haiku", "anthropic", "bad-executor"),
                 ("Codex mocny", "Opus", False, "Codex lekki", "mixed", "bad-executor-provider")]
        for review, reassigned, newer, executor, preferred, code in cases:
            with self.subTest(review=review, reassigned=reassigned, newer_override=newer, code=code):
                self.reviewer_history(review, reassigned, newer_override=newer, preferred=preferred)
                before = self.state().steps.copy()
                st, outcome = self.act(self.caretaker_run(), "change-executor", step="target",
                                       executor=executor, reason="Zmiana po błędzie poprawek.")
                self.assertFalse(outcome["ok"], outcome)
                self.assertEqual(outcome["code"], code)
                self.assertEqual(st.steps, before)
                self.assertEqual(st.step_decisions, {})
                self.assertEqual(caretaker.owned(st, outcome["problem"])["count"], 1)
                self.assertFalse(st.open_intents(include_background=True))

    def test_change_executor_can_use_the_obsolete_reviewers_model(self):
        for newer in (True, False):
            with self.subTest(newer_override=newer):
                self.reviewer_history("Haiku" if newer else "Codex mocny",
                                      "Codex mocny" if newer else "Haiku",
                                      newer_override=newer, preferred="mixed")
                st, outcome = self.act(self.caretaker_run(), "change-executor", step="target",
                                       executor="Haiku", reason="Obecny recenzent jest u innego dostawcy.")
                self.assertTrue(outcome["ok"], outcome)
                self.assertEqual(st.step_decisions["target"]["executor"], "Haiku")

    def test_change_executor_checks_the_reviewers_current_catalog_entry(self):
        for history in ("assignment", "review", "reassignment", "later-review"):
            for changed_field, executor, preferred, code in (
                    ("model", "Haiku", "anthropic", "bad-executor"),
                    ("provider", "Codex lekki", "mixed", "bad-executor-provider")):
                with self.subTest(history=history, changed_field=changed_field):
                    if history in ("assignment", "review"):
                        self.reset_scenario()
                        self.add("K-1", "ticket.assigned", ticket="PT-7", executor="Sonnet", model="sonnet",
                                 provider="anthropic", preferredProvider=preferred, reviewer="Opus",
                                 reviewerModel="opus", reviewerProvider="anthropic")
                        if history == "review":
                            self.add("K-1", "step.started", step="past-review", kind="review", ticket="PT-7",
                                     executor="Opus", model="opus", provider="anthropic")
                            self.add("K-1", "step.finished", step="past-review", result="done")
                        self.target(result="error", kind="fix")
                    else:
                        newer = history == "reassignment"
                        self.reviewer_history("Codex mocny" if newer else "Opus",
                                              "Opus" if newer else "Codex mocny",
                                              newer_override=newer, preferred=preferred)
                    catalog = self.engine.catalog()
                    fields = {"model": "haiku"} if changed_field == "model" else {
                        "provider": "openai", "program": "codex"}
                    updated = replace(catalog, executors=tuple(
                        replace(entry, **fields) if entry.name == "Opus" else entry
                        for entry in catalog.executors))
                    before = self.state().steps.copy()
                    with mock.patch.object(self.engine, "catalog", return_value=updated):
                        # Ta sama walidacja obowiązuje przy faktycznym wznowieniu nowej sesji.
                        with self.assertRaises(FlowError) as raised:
                            self.engine._resume_executor("K-1", self.state(), before["target"], executor,
                                                         changed=True, automated=True)
                        self.assertEqual(raised.exception.code, code)
                        st, outcome = self.act(self.caretaker_run(), "change-executor", step="target",
                                               executor=executor, reason="Zmiana po błędzie poprawek.")
                    self.assertFalse(outcome["ok"], outcome)
                    self.assertEqual(outcome["code"], code)
                    self.assertEqual(st.steps, before)
                    self.assertEqual(st.step_decisions, {})
                    self.assertEqual(caretaker.owned(st, outcome["problem"])["count"], 1)
                    self.assertFalse(st.open_intents(include_background=True))

    def test_review_launch_uses_the_same_event_order_as_executor_validation(self):
        for newer, expected in ((True, "Codex mocny"), (False, "Haiku")):
            with self.subTest(newer_override=newer):
                self.reviewer_history("Haiku", "Codex mocny", newer_override=newer, preferred="mixed")
                self.add("K-1", "step.finished", step="target", result="done")
                st = self.state()
                with mock.patch.object(self.engine, "_project", return_value=(mock.Mock(), mock.Mock(repo=self.base))), \
                        mock.patch.object(self.engine.git, "clean_tip", return_value="tip"), \
                        mock.patch.object(self.engine.git, "review_diff", return_value="diff"), \
                        mock.patch.object(self.engine, "_ticket_text", return_value=("Tytuł", "Opis")), \
                        mock.patch.object(self.engine, "_note", return_value="Notka."), \
                        mock.patch.object(self.engine, "_rules", return_value="Zasady."), \
                        mock.patch.object(self.engine, "_launch") as launch:
                    self.engine._review("K-1", st, st.steps["target"], 2)
                launch.assert_called_once()
                reviewer = launch.call_args.args[2]
                entry = self.engine.catalog().get(expected)
                self.assertEqual((reviewer.name, reviewer.model, reviewer.provider),
                                 (entry.name, entry.model, entry.provider))


class DecisionFlowTest(FlowInProcess):
    def setUp(self):
        super().setUp()
        # Własne warianty atrapy PT-145 pozostają w tym pliku, bez rozbudowy wspólnej atrapy.
        source = flowfakes.CLAUDE.replace('if prompt.startswith("Jesteś opiekunem karty"):', '''
if "Opiekun karty wybrał innego wykonawcę." in prompt or "Odpowiedź opiekuna karty:" in prompt:
    word = "gotowe"
if prompt.startswith("Jesteś opiekunem karty"):''')
        source = source.replace("Który format daty?", "Gdzie jest konfiguracja?")
        source = source.replace('    data = None', '''    if "PT145-CHANGE" in prompt or "## Decyzja czeka" in prompt:
        target = re.search(r"Krok problemu: (\\S+)", prompt).group(1)
        data = {"result": "done", "step": target, "reason": "Ścieżka jest w README."}
        if "PT145-CHANGE" in prompt:
            data.update(action="change-executor", executor="Haiku", instruction="Sprawdź config.json.")
        else:
            data.update(action="answer", answer="Plik config.json.", category="technical")
        with open(result, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        sys.exit(0)
    data = None''')
        with open(self.fakes["claude"], "w", encoding="utf-8") as fh:
            fh.write(source)

    def test_change_executor_launches_a_new_session_with_instruction_and_history(self):
        card = self.card(description="ATRAPA: blad-opiekun\nATRAPA-POWOD: PT145-CHANGE")
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, caretaker.ACTION), timeout=45), self.events(card))
        action = self.of(card, caretaker.ACTION)[0]
        self.assertTrue(action["ok"], action)
        self.assertTrue(wait_until(lambda: [s for s in self.of(card, "step.started") if s.get("executor") == "Haiku"],
                                   timeout=30), self.events(card))
        steps = self.of(card, "step.started")
        child = next(s for s in steps if s.get("executor") == "Haiku")
        parent = next(s for s in steps if s["step"] == child["parentStep"])
        self.assertNotEqual(child["session"], parent["session"])
        with open(os.path.join(child["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            self.assertIn("Sprawdź config.json.", fh.read())
        self.assertEqual(caretaker.history_text(action),
                         f"Opiekun: zmienił wykonawcę {action['label']} na Haiku — Ścieżka jest w README.")

    def test_waiting_question_gets_an_answer_and_the_brief_states_the_boundary(self):
        self.flow.engine.set_flow_settings({"caretakerDecisionMinutes": 1})
        card = self.card(description="ATRAPA: pytanie\nPT145-ANSWER")
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "waiting")
        self.assertFalse(self.of(card, caretaker.STARTED))
        original_clock = self.flow.engine.clock
        self.flow.engine.clock = lambda: original_clock() + 61
        self.assertTrue(wait_until(lambda: self.of(card, caretaker.ACTION), timeout=45), self.events(card))
        action = self.of(card, caretaker.ACTION)[0]
        self.assertTrue(action["ok"], action)
        self.assertTrue(wait_until(lambda: [s for s in self.of(card, "step.started") if s.get("parentStep")], timeout=30))
        child = next(s for s in self.of(card, "step.started") if s.get("parentStep"))
        with open(os.path.join(child["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            self.assertIn("Odpowiedź opiekuna karty: Plik config.json.", fh.read())
        step = next(s for s in self.of(card, "step.started") if s["kind"] == "caretaker")
        with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            brief = fh.read()
        for text in ("zakres", "wymagania", "biznesowe", "wariantów produktu", "planning", "plan-*",
                     "nie zatwierdzaj planu", "nie udzielaj zgód"):
            self.assertIn(text, brief)
