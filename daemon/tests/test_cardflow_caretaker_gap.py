"""PT-155: luka w działającym planie wykryta po błędzie ticketu — istniejąca ścieżka poprawy planu."""
import json
import os
from unittest import mock

from tide_daemon.cardflow import caretaker, decisions
from tide_daemon.cardflow.engine import FlowError, _CaretakerPort
from tide_daemon.cardflow.state import derived, fold

from . import test_cardflow_plan_integration as fixtures
from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_caretaker import Journal
from .test_cardflow_caretaker_retry import EngineOnJournal

TARGET, ROOT, PROBLEM = "K-1-PT-7-2", "K-1-PT-7-1", "error:K-1-PT-7-1"
FEEDBACK = "Dodaj ticket na migrację formatu, wynika z decyzji A; tickety, które ruszyły, przepisz bez zmian."
REASON = "W planie brakuje migracji z decyzji A."
PARAMS = {"feedback": FEEDBACK, "reason": REASON}
ROWS = [{"id": "PT-7", "workId": "#1", "title": "Eksport", "executor": "Sonnet", "reviewer": "Opus",
         "class": "standard", "note": "", "blockers": [], "body": "# Eksport\n- [ ] Wynik.\n"}]


class GapActionTest(EngineOnJournal):
    def setUp(self):
        super().setUp()
        j = Journal()
        j.add("plan.proposed", planner="Sonnet", rows=ROWS, prefix="PT", specification="Decyzja A: migracja.")
        j.add("plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])
        j.add("plan.approved", rows=ROWS, map={"#1": "PT-7"})
        j.failed_twice(ROOT, TARGET)
        for event in j.events:
            self.add("K-1", event["type"], **{k: v for k, v in event.items() if k not in ("seq", "ts", "type")})
        self.run = self.care()

    def care(self, n=1):
        return self.add("K-1", caretaker.STARTED, problem=PROBLEM, key=f"caretaker:{PROBLEM}:{n}", n=n,
                        step=f"K-1-care-{n}", target=TARGET, root=ROOT, ticket="PT-7", trigger="error",
                        triggers=["error", "repeat"], decision=f"step:{TARGET}",
                        watermark=len(self.journal.read("K-1")))

    def act(self, run=None):
        with mock.patch.object(self.engine, "_guard_check"), mock.patch.object(self.engine, "_guard_rebase"):
            self.engine._caretaker_act("K-1", run or self.run, "return-plan", PARAMS)
        return fold(self.journal.read("K-1"))

    def test_return_closes_ticket_problem_durably_and_leaves_plan_approval_to_the_user(self):
        st = self.act()
        returned = st.plan_returns[0]
        self.assertEqual((returned["baseline"][0]["workId"], returned["caretaker"], returned["feedback"]),
                         ("PT-7", PROBLEM, FEEDBACK))
        self.assertEqual(returned["caretakerFrom"], TARGET)
        self.assertEqual(caretaker.problems(st), [])
        self.assertEqual(decisions.build_decisions(st), [])
        self.assertEqual(caretaker.owned(st, PROBLEM)["count"], 0)
        self.assertEqual(st.finished[TARGET]["result"], "error", "wynik kroku pozostaje bez zmian")
        self.assertEqual(st.plan_approved["rows"], ROWS)
        # Po restarcie port rozpoznaje istniejące odesłanie, nawet przed zapisem wyniku pary akcji.
        port = _CaretakerPort(self.engine, "K-1", self.run)
        before = self.journal.read("K-1")
        self.assertEqual(port.return_plan({"id": PROBLEM, "step": TARGET}, PARAMS)["label"], "plan")
        self.assertEqual(self.journal.read("K-1"), before)
        self.assertFalse([e for e in self.journal.read("K-1") if e["type"] == "plan.revision-approved"])
        entries = derived(st, {"column": "todo"})["flow"]["history"]
        self.assertEqual([h["text"] for h in entries if h["kind"] == "caretaker"],
                         [f"Opiekun: odesłał plan z uwagą — {REASON}"])
        # Stary błąd nie uruchamia automatycznej próby na mocniejszym modelu ani odpowiedzi na pytanie.
        st.last_step = TARGET
        with mock.patch.object(self.engine, "_advance_plan_review", return_value=False), \
                mock.patch.object(self.engine, "_advance_planning", return_value=False), \
                mock.patch.object(self.engine, "_resume_step") as resume, \
                mock.patch.object(self.engine, "_stronger_executor") as stronger:
            self.engine._advance("K-1", st)
        resume.assert_not_called()
        stronger.assert_not_called()
        # Błąd poprawy planu to osobny problem, nie dalszy ciąg błędu ticketu.
        self.add("K-1", "step.started", step="amend", ticket="", kind="plan-amend")
        self.add("K-1", "step.finished", step="amend", result="done")
        self.add("K-1", "plan.revision-failed", failed=True, baseline=returned["baseline"], errors=["zły plan"])
        st = fold(self.journal.read("K-1"))
        self.assertEqual(caretaker.chain_root(st, "amend"), "amend")
        self.assertEqual([p["id"] for p in caretaker.problems(st)], ["plan-fix:amend"])

    def test_revision_active_is_a_failed_action_counted_until_the_limit(self):
        # Poprawa zaczęła się przed uruchomieniem opiekuna drugiego problemu — to revision-active, nie stale.
        self.add("K-1", "plan.returned", baseline=ROWS, feedback="Popraw inny fragment.")
        # Pierwszy krok z setUp wystartował przed odesłaniem, więc jego wynik jest już nieaktualny.
        self.act()
        limit = self.engine._caretaker_limit()
        for n in range(1, limit + 1):
            run = self.care(n + 1)
            st = self.act(run)
            self.assertEqual(st.caretaker_outcomes[run["step"]]["code"], "revision-active")
            self.assertEqual(caretaker.owned(st, PROBLEM)["count"], n)
        st = fold(self.journal.read("K-1"))
        self.assertEqual(caretaker.owned(st, PROBLEM)["count"], limit)
        with mock.patch.object(self.engine, "_caretaker_launch") as launch:
            self.engine._caretake("K-1", st)
        launch.assert_not_called()
        st = fold(self.journal.read("K-1"))
        self.assertTrue(st.caretaker_limits[PROBLEM]["limit"])
        self.assertEqual(len(st.plan_returns), 1)

    def test_restart_after_plan_return_finishes_the_action_without_a_second_return(self):
        self.add("K-1", "intent", action="caretaker-action", key=self.run["step"], problem=PROBLEM,
                 caretakerAction="return-plan", params=PARAMS)
        port = _CaretakerPort(self.engine, "K-1", self.run)
        port.return_plan({"id": PROBLEM, "step": TARGET}, PARAMS)
        st = self.act()
        self.assertEqual(len(st.plan_returns), 1)
        self.assertTrue(st.caretaker_outcomes[self.run["step"]]["ok"])
        self.assertEqual(st.open_intents(include_background=True), [])
        self.assertEqual(caretaker.problems(st), [])

    def test_user_can_explicitly_retry_the_returned_ticket_using_the_existing_path(self):
        self.act()
        self.engine.resolve_step("K-1", {"step": TARGET, "choice": "retry", "answer": "Migracja gotowa."})
        st = fold(self.journal.read("K-1"))
        st.last_step = TARGET
        with mock.patch.object(self.engine, "_advance_plan_review", return_value=False), \
                mock.patch.object(self.engine, "_advance_planning", return_value=False), \
                mock.patch.object(self.engine, "_resume_step") as resume:
            self.engine._advance("K-1", st)
        self.assertEqual(resume.call_args.args[2]["step"], TARGET)
        self.assertEqual(resume.call_args.args[3]["answer"], "Migracja gotowa.")

    def test_user_return_during_caretaking_makes_the_action_stale(self):
        with mock.patch.object(self.engine, "_advance_user_revision"):
            self.engine.edit_plan("K-1", {"action": "return", "feedback": "Uwagi użytkownika."})
        st = self.act()
        self.assertEqual(len(st.plan_returns), 1)
        self.assertNotIn("caretaker", st.plan_returns[0])
        self.assertTrue(st.caretaker_outcomes[self.run["step"]]["stale"])
        self.assertEqual(caretaker.owned(st, PROBLEM)["count"], 0)

    def test_user_retry_during_caretaking_makes_the_action_stale(self):
        self.engine.resolve_step("K-1", {"step": TARGET, "choice": "retry", "answer": "Spróbuj inaczej."})
        st = self.act()
        self.assertTrue(st.caretaker_outcomes[self.run["step"]]["stale"])
        self.assertEqual(st.plan_returns, [])
        self.assertEqual(caretaker.owned(st, PROBLEM)["count"], 0)

    def test_approval_still_rejects_changing_or_removing_a_started_ticket(self):
        self.act()
        for rows in ([], [{**ROWS[0], "workId": "PT-7", "title": "Zmieniony"}]):
            with self.subTest(rows=rows):
                self.add("K-1", "plan.proposed", rows=rows, prefix="PT", planner="Sonnet")
                self.add("K-1", "plan.revision-ready", pending=True, baseline=ROWS)
                self.add("K-1", "plan.reviewed", verdict="APPROVE", findings=[])
                with self.assertRaises(FlowError) as error:
                    self.engine.approve_plan("K-1")
                self.assertEqual(error.exception.code, "ticket-started")
        self.assertFalse([e for e in self.journal.read("K-1") if e["type"] == "plan.revision-approved"])


class GapFlowTest(FlowInProcess):
    proposal = fixtures.PlanFlowTest.proposal
    submit = fixtures.PlanFlowTest.submit
    approve = fixtures.PlanFlowTest.approve

    def test_ticket_error_returns_approved_plan_adds_a_ticket_and_waits_for_the_user(self):
        card = self.card()
        path = self.proposal(card)
        body = ("# Eksport\nATRAPA: blad-opiekun\n"
                "ATRAPA-POWOD: Brak migracji z decyzji A. ATRAPA-OPIEKUN: return-plan-gap\n"
                "- [ ] Eksport działa po migracji.\n## Dla recenzenta\nSprawdź eksport.\n")
        with open(os.path.join(os.path.dirname(path), "tickets", "1.md"), "w", encoding="utf-8") as fh:
            fh.write(body)
        with open(os.path.join(os.path.dirname(path), "spec.md"), "w", encoding="utf-8") as fh:
            fh.write("Decyzja A: migracja formatu jest osobnym zadaniem przed eksportem.")
        self.assertTrue(self.submit(card, path)["ok"])
        approved = self.approve(card)
        self.assertTrue(approved["ok"], approved)
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=45), self.events(card))
        self.assertTrue(wait_until(lambda: self.of(card, caretaker.ACTION), timeout=10), self.events(card))
        self.flow.stop()
        events = self.flow.engine.journal.read(card)
        st = fold(events)
        returned = st.plan_returns[0]
        target = returned["caretakerFrom"]
        care = next(s for s in st.caretakers.values())
        run = st.caretaker_runs[care["step"]]
        self.assertEqual((run["trigger"], returned["caretaker"], returned["feedback"]),
                         ("error", run["problem"], FEEDBACK))
        self.assertEqual([r["workId"] for r in returned["baseline"]], list(approved["map"].values()))
        self.assertEqual(st.finished[target]["result"], "error")
        self.assertEqual(st.caretaker_outcomes[care["step"]]["ok"], True)
        self.assertEqual(caretaker.problems(st), [])
        self.assertEqual([d["kind"] for d in decisions.build_decisions(st)], ["plan-revision"])
        self.assertEqual(st.plan_revision["difference"]["added"], ["#3"])
        self.assertEqual(st.plan_revision["difference"]["changed"], [])
        self.assertEqual(st.plan_revision["difference"]["removed"], [])
        self.assertFalse([e for e in events if e["type"] == "plan.revision-approved"])
        self.assertEqual(len(st.plan_approved["rows"]), 2)
        amend = next(s for s in st.steps.values() if s["kind"] == "plan-amend")
        with open(os.path.join(amend["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            prompt = fh.read()
        self.assertIn(FEEDBACK, prompt)
        self.assertIn("ZATWIERDZONY plan", prompt)
        self.assertIn(f"| {approved['map']['#1']} |", prompt)
        context = os.path.join(care["stepDir"], "in", "context")
        expected = {"plan/approved.md": approved["map"]["#1"],
                    "plan/spec.md": "Decyzja A: migracja",
                    f"plan/tickets/{approved['map']['#1']}.md": "Eksport działa po migracji",
                    f"steps/{target}/note.md": "Atrapa zrobiła ticket.",
                    f"steps/{target}/step-log.md": "Brak migracji z decyzji A.",
                    f"steps/{target}/prompt.txt": "Eksport działa po migracji"}
        for rel, text in expected.items():
            with self.subTest(rel=rel), open(os.path.join(context, rel), encoding="utf-8") as fh:
                self.assertIn(text, fh.read())
        mine = [h["text"] for h in derived(st, {"column": "todo"})["flow"]["history"] if h["kind"] == "caretaker"]
        self.assertEqual(mine, [f"Opiekun: odesłał plan z uwagą — {REASON}"])
        self.restart()
        self.assertTrue(wait_until(lambda: self.shown(card)["derived"]["flow"]["decisions"], timeout=10))
        self.assertEqual(len(self.of(card, caretaker.STARTED)), 1)
        self.assertEqual(len(self.of(card, "plan.returned")), 1)
        self.assertEqual([d["kind"] for d in self.shown(card)["derived"]["flow"]["decisions"]], ["plan-revision"])
