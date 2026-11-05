"""Opiekun odsyła plan z precyzyjną uwagą (K-37, PT-146): wyzwalacz `plan-fix` i akcja `return-plan`.

Najpierw czyste funkcje (`caretaker.py`, `caretakerctx.py`, `decisions.py`, historia) na złożonych dziennikach,
potem `FlowEngine` na dziennikach w katalogu tymczasowym (akcja, `stale`, nieudane odesłanie) i przebiegi z atrapą
`claude` (`ATRAPA-OPIEKUN: return-plan`, `retry`) dla nieudanej poprawki planu i drugiej nieudanej poprawki formatu.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from tide_daemon.cardflow import caretaker, caretakerctx, decisions, faults, history
from tide_daemon.cardflow.state import derived, fold

from . import test_cardflow_plan_integration as fixtures
from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_caretaker import Journal, ev
from .test_cardflow_caretaker_retry import EngineOnJournal

FINDINGS = [{"severity": "blocker", "text": "Pierwszy plasterek nie ma mierzalnego kryterium.", "tickets": ["#1"]},
            {"severity": "major", "text": "Rozdziel pierwszy plasterek.", "tickets": ["#1"]},
            {"severity": "nit", "text": "Drobiazg w nazwie drugiego.", "tickets": ["#2"]}]
ROWS = [{"id": "#1", "workId": "#1", "title": "Eksport", "executor": "Sonnet", "reviewer": "Opus", "class": "standard",
         "note": "", "blockers": [], "body": "# Eksport\n- [ ] Działa.\n"}]
ERRORS = ["wiersz #1: brak kryterium", "brak sekcji Dla recenzenta"]
PLAN_PROBLEM = "plan-fix:K-1-plan-amend-4"


def planning_journal(*, failed=True, reviewed=True) -> Journal:
    """Karta z planem po review i nieudaną poprawką po odesłaniu użytkownika (`plan.revision-failed`)."""
    j = Journal()
    j.events[0]["entry"] = "planning"
    j.add("plan.proposed", planner="Sonnet", rows=ROWS, specification="Eksport.", prefix="PT", planPath="")
    if reviewed:
        j.add("plan.reviewed", reviewer="Opus", verdict="REQUEST_CHANGES", findings=FINDINGS, corrected=True)
    if failed:
        j.add("plan.returned", feedback="Uwagi użytkownika: popraw.", baseline=ROWS)
        j.step("K-1-plan-amend-4", kind="plan-amend", ticket="")
        j.add("step.finished", step="K-1-plan-amend-4", ticket="", result="done")
        j.add("plan.checked", step="K-1-plan-amend-4", valid=False, errors=ERRORS)
        j.add("plan.revision-failed", failed=True, pending=False, errors=ERRORS, baseline=ROWS,
              feedback="Uwagi użytkownika: popraw.")
    return j


def care(j, n=1, step="K-1-care-1", problem=PLAN_PROBLEM, target="K-1-plan-amend-4", decision=None):
    revision = j.st.plan_revision or {}
    return j.add(caretaker.STARTED, problem=problem, key=f"caretaker:{problem}:{n}", n=n, step=step, target=target,
                 root=caretaker.chain_root(j.st, target), ticket="", trigger="plan-fix", triggers=["plan-fix"],
                 decision=f"plan-revision-failed:{revision.get('seq') or 0}" if decision is None else decision,
                 watermark=j.events[-1]["seq"])


def second_failure_journal() -> Journal:
    """Karta Lekkiego planowania po drugiej nieudanej poprawce formatu (bez propozycji planu)."""
    j = Journal()
    j.events[0]["entry"] = "planning"
    for index, kind in enumerate(("planning", "plan-fix", "plan-fix")):
        name = f"K-1-plan-1-{index}"
        j.add("step.started", step=name, kind=kind, ticket="", round=index, executor="Sonnet", worktree="/w",
              stepDir=f"/w.{name}.step")
        j.add("step.finished", step=name, ticket="", result="done")
        j.add("plan.checked", step=name, valid=False, errors=ERRORS)
    return j


class TriggerTest(unittest.TestCase):
    def test_failed_revision_is_a_plan_fix_problem_with_the_decision(self):
        j = planning_journal()
        found = caretaker.problems(j.st)
        self.assertEqual([(p["id"], p["trigger"], p["step"]) for p in found],
                         [(PLAN_PROBLEM, "plan-fix", "K-1-plan-amend-4")])
        self.assertTrue(found[0]["decision"].startswith("plan-revision-failed:"))
        self.assertEqual([d["kind"] for d in decisions.build_decisions(j.st)], ["plan-revision-failed"])

    def test_second_failed_format_fix_is_a_problem_without_a_decision(self):
        j = second_failure_journal()
        found = caretaker.problems(j.st)
        self.assertEqual([(p["id"], p["decision"], p["step"]) for p in found],
                         [("plan-fix:K-1-plan-1-2", "", "K-1-plan-1-2")])
        j2 = second_failure_journal()
        j2.events[-3:] = []              # po pierwszej poprawce formatu karta jeszcze próbuje sama
        self.assertEqual(caretaker.problems(j2.st), [])

    def test_a_plan_waiting_for_approval_is_not_a_trigger(self):
        j = planning_journal(failed=False)
        self.assertEqual(caretaker.problems(j.st), [])
        j.add("plan.approval_requested")
        self.assertEqual(caretaker.problems(j.st), [])
        self.assertEqual([d["kind"] for d in decisions.build_decisions(j.st)], ["plan-approval"])

    def test_decision_is_hidden_while_the_caretaker_works_and_returns_with_its_result(self):
        j = planning_journal()
        care(j)
        self.assertEqual([d for d in decisions.build_decisions(j.st) if d["kind"] == "plan-revision-failed"], [])
        j.add(caretaker.ESCALATED, problem=PLAN_PROBLEM, step="K-1-care-1", target="K-1-plan-amend-4", ticket="",
              action="escalate", summary="Poprawka łamie format.", recommendation="Odrzuć poprawkę.", optionId="",
              failed=False, error="", code="")
        shown = decisions.build_decisions(j.st)
        self.assertEqual([d["kind"] for d in shown], ["plan-revision-failed"])
        self.assertEqual(shown[0]["caretaker"]["summary"], "Poprawka łamie format.")


class PlanFixDecisionTest(unittest.TestCase):
    """Druga nieudana poprawa formatu po oddaniu problemu przez opiekuna wraca do użytkownika jako decyzja."""

    def escalate(self, j, **extra):
        care(j, step="K-1-care-1", problem="plan-fix:K-1-plan-1-2", target="K-1-plan-1-2", decision="")
        return j.add(caretaker.ESCALATED, problem="plan-fix:K-1-plan-1-2", step="K-1-care-1", target="K-1-plan-1-2",
                     ticket="", action="escalate", summary="Format nadal błędny.", recommendation="Ponów.",
                     optionId="retry", failed=False, error="", **{"code": "", **extra})

    def test_no_decision_before_the_caretaker_hands_the_problem_back(self):
        j = second_failure_journal()
        self.assertEqual(decisions.build_decisions(j.st), [])
        care(j, step="K-1-care-1", problem="plan-fix:K-1-plan-1-2", target="K-1-plan-1-2", decision="")
        self.assertEqual(decisions.build_decisions(j.st), [])

    def test_escalation_gives_a_decision_with_summary_and_a_working_retry(self):
        j = second_failure_journal()
        self.escalate(j)
        shown = decisions.build_decisions(j.st)
        self.assertEqual([d["kind"] for d in shown], ["plan-fix-failed"])
        self.assertEqual(shown[0]["caretaker"]["summary"], "Format nadal błędny.")
        self.assertEqual((shown[0]["happened"], shown[0]["why"]), decisions.GENERIC["plan-fix-failed"])
        self.assertEqual(shown[0]["technical"]["errors"], ERRORS)
        option = shown[0]["options"][0]
        self.assertEqual((option["method"], option["params"]),
                         ("ResolveCardStep", {"choice": "retry", "step": "K-1-plan-1-2"}))
        self.assertEqual(shown[0]["reply"]["field"], "answer")
        self.assertEqual(caretaker.plan_fix_waiting(j.st), ["K-1-plan-1-2"])
        j.add("step.retry", step="K-1-plan-1-2", ticket="", reason="", user=True, instruction="Popraw wiersz #1.")
        self.assertEqual(decisions.build_decisions(j.st), [])

    def test_user_retry_closes_the_previous_cycle_after_escalation_or_limit(self):
        for limited in (False, True):
            with self.subTest(limited=limited):
                j = second_failure_journal()
                self.escalate(j, limit=limited)
                problem = "plan-fix:K-1-plan-1-2"
                j.add("step.retry", step="K-1-plan-1-2", ticket="", user=True,
                      instruction="Popraw format.")
                j.add("step.started", step="next-fix", kind="plan-fix", ticket="", round=2,
                      parentStep="K-1-plan-1-2", executor="Sonnet", worktree="/w", stepDir="/w.retry.step")
                j.add("step.finished", step="next-fix", ticket="", result="done")
                j.add("plan.checked", step="next-fix", valid=False, errors=ERRORS)
                own = caretaker.owned(j.st, problem)
                self.assertFalse(own["handed"])
                self.assertEqual((own["count"], own["next"]), (0, 2))
                self.assertEqual([(p["id"], p["step"]) for p in caretaker.problems(j.st)],
                                 [(problem, "next-fix")])


class ResultTest(unittest.TestCase):
    problem = {"id": PLAN_PROBLEM, "trigger": "plan-fix", "step": "K-1-plan-amend-4", "decision": "x", "options": []}

    def test_return_plan_params(self):
        base = {"result": "done", "action": "return-plan", "feedback": "Wiersz #1: dodaj kryterium.",
                "reason": "Brak kryterium."}
        made = caretaker.parse_result(base, self.problem, Journal().st)
        self.assertEqual(made, {"ok": True, "action": "return-plan",
                                "params": {"feedback": base["feedback"], "reason": base["reason"]}})
        self.assertEqual(caretaker.parse_result({**base, "findings": "1,2"}, self.problem, Journal().st)["params"]
                         ["findings"], "1,2")
        for field in ("feedback", "reason"):
            self.assertEqual(caretaker.parse_result({**base, field: ""}, self.problem, Journal().st)["code"],
                             "bad-caretaker-result")

    def test_return_plan_supports_plan_fix_and_ticket_problem_triggers(self):
        base = {"result": "done", "action": "return-plan", "feedback": "x", "reason": "y"}
        self.assertEqual(caretaker.ACTIONS["return-plan"].kinds, ("plan-fix", "error", "repeat", "waiting"))
        for trigger in ("plan-fix", "error", "repeat", "waiting"):
            other = {"id": "problem", "trigger": trigger, "step": "K-1-PT-7-2", "options": []}
            self.assertTrue(caretaker.parse_result(base, other, Journal().st)["ok"])
        for trigger in ("flow-error", "stalled", "no-progress"):
            other = {"id": "problem", "trigger": trigger, "options": []}
            self.assertEqual(caretaker.parse_result(base, other, Journal().st)["code"], "bad-caretaker-action")

    def test_the_caretaker_has_no_approval_action(self):
        for action in ("approve-plan", "approve-revision", "ApproveCardPlan", "approve"):
            with self.subTest(action=action):
                made = caretaker.parse_result({"result": "done", "action": action, "feedback": "x", "reason": "y"},
                                              self.problem, Journal().st)
                self.assertEqual((made["ok"], made["code"]), (False, "bad-caretaker-action"))
        self.assertEqual(sorted(caretaker.ACTIONS), ["escalate", "retry", "retry-stage", "return-plan"])


class BriefAndContextTest(unittest.TestCase):
    def files(self, j, problem):
        with tempfile.TemporaryDirectory() as card_dir, tempfile.TemporaryDirectory() as tree:
            return {f.path: f.render() for f in caretakerctx.collect(
                problem, j.st, j.events, card="K-1", card_dir=card_dir, worktree=tree, step="K-1-care-1")}

    def test_brief_names_errors_findings_and_asks_for_row_blocker_and_standard_error(self):
        j = planning_journal()
        problem = caretaker.problems(j.st)[0]
        text = caretaker.brief(problem, j.st, card="K-1", out_dir="/o", worktree="/w")
        for part in ("poprawa planu nie przeszła sprawdzenia formatu", "wiersz #1: brak kryterium",
                     "1. [blocker] Pierwszy plasterek nie ma mierzalnego kryterium.", "3. [nit] Drobiazg",
                     "Uwagi użytkownika: popraw.", "wiersz planu", "bloker", "błąd standardu planu 1.2",
                     "dosłownie", "`return-plan`", "`findings`"):
            self.assertIn(part, text)
        self.assertNotIn("Kroki do ponowienia (pole", text, "nieudana poprawka różnicy wraca planem, nie `retry`")

    def test_context_has_errors_proposal_findings_and_the_plan_standard(self):
        j = planning_journal()
        files = self.files(j, caretaker.problems(j.st)[0])
        self.assertIn("wiersz #1: brak kryterium", files["plan-fix/errors.md"])
        self.assertIn("Eksport", files["plan-fix/previous-proposal.md"])
        self.assertIn("2. [major] Rozdziel pierwszy plasterek. (tickety: #1)", files["plan-fix/review-findings.md"])
        self.assertIn("Uwagi użytkownika: popraw.", files["plan-fix/returned-feedback.md"])
        self.assertIn("docs/agent-plan-standard.md", files)
        self.assertIn("1.2", files["docs/agent-plan-standard.md"])

    def test_brief_masks_multiline_secrets_before_normalizing_errors_and_findings(self):
        secret = "abcdefgh\nijklmnop"
        for source in ("errors", "review", "feedback"):
            with self.subTest(source=source), mock.patch.dict(os.environ, {"PRIVATE_SECRET": secret}):
                j = planning_journal()
                if source == "review":
                    reviewed = next(e for e in j.events if e["type"] == "plan.reviewed")
                    reviewed["findings"] = [{**FINDINGS[0], "text": f"Jawna uwaga: {secret}"}]
                else:
                    j.events[-1][source] = [secret] if source == "errors" else secret
                text = caretaker.brief(caretaker.problems(j.st)[0], j.st, card="K-1", out_dir="/o", worktree="/w")
                self.assertIn(faults.MASK, text)
                for part in secret.splitlines():
                    self.assertNotIn(part, text)

    def test_brief_masks_secrets_before_shortening_errors_findings_and_feedback(self):
        secret = "abcdefghijklmnopqrstuvwx"
        for source, limit in (("errors", 300), ("review", 300), ("feedback", 1500)):
            with self.subTest(source=source), mock.patch.dict(os.environ, {"PRIVATE_SECRET": secret}):
                j = planning_journal()
                # Granica skrótu przecina sekret; przed maskowaniem zostawiłaby jawne pierwsze 10 znaków.
                value = "x" * (limit - 11) + " " + secret
                if source == "review":
                    reviewed = next(e for e in j.events if e["type"] == "plan.reviewed")
                    reviewed["findings"] = [{**FINDINGS[0], "text": value}]
                else:
                    j.events[-1][source] = [value] if source == "errors" else value
                text = caretaker.brief(caretaker.problems(j.st)[0], j.st, card="K-1", out_dir="/o", worktree="/w")
                self.assertIn(faults.MASK, text)
                self.assertNotIn(secret[:10], text)

    def test_previous_proposal_is_masked_after_decoding_quoted_and_multiline_secrets(self):
        j = planning_journal()
        secrets = ['correct horse battery staple', 'line one\nline two secret']
        j.st.plan_proposed = {**j.st.plan_proposed,
                              "specification": f'Opis password: "{secrets[0]}"',
                              "prefix": f'token: "{secrets[1]}"'}
        files = self.files(j, caretaker.problems(j.st)[0])
        text = files["plan-fix/previous-proposal.md"]
        for secret in (*secrets, "horse battery staple", "line two secret"):
            self.assertNotIn(secret, text)

    def test_second_failure_brief_points_to_retry(self):
        j = second_failure_journal()
        problem = caretaker.problems(j.st)[0]
        text = caretaker.brief(problem, j.st, card="K-1", out_dir="/o", worktree="/w")
        self.assertIn("Kroki do ponowienia (pole `step` akcji `retry`): K-1-plan-1-2", text)
        self.assertIn("`return-plan` się nie powiedzie", text)
        self.assertIn("Plan nie ma jeszcze żadnej propozycji", self.files(j, problem)["plan-fix/previous-proposal.md"])


class LimitAndHistoryTest(unittest.TestCase):
    def test_a_returned_plan_keeps_the_chain_and_the_count_until_the_user_acts(self):
        j = planning_journal()
        care(j)
        j.add("plan.returned", feedback="Wiersz #1.", baseline=ROWS, caretaker=PLAN_PROBLEM,
              caretakerStep="K-1-care-1", caretakerFrom="K-1-plan-amend-4")
        j.add(caretaker.ACTION, problem=PLAN_PROBLEM, step="K-1-care-1", target="K-1-plan-amend-4", ticket="",
              action="return-plan", params={"feedback": "Wiersz #1.", "reason": "Brak kryterium."}, ok=True,
              error="", code="", label="plan", retried="K-1-plan-amend-4", stale=False)
        self.assertEqual(caretaker.problems(j.st), [], "odesłany plan jest w poprawie")
        j.step("K-1-plan-amend-9", kind="plan-amend", ticket="")
        self.assertEqual(caretaker.chain_root(j.st, "K-1-plan-amend-9"), "K-1-plan-amend-4")
        j.add("step.finished", step="K-1-plan-amend-9", ticket="", result="done")
        j.add("plan.checked", step="K-1-plan-amend-9", valid=False, errors=ERRORS)
        j.add("plan.revision-failed", failed=True, pending=False, errors=ERRORS, baseline=ROWS, feedback="Wiersz #1.")
        found = caretaker.problems(j.st)
        self.assertEqual([(p["id"], p["step"]) for p in found], [(PLAN_PROBLEM, "K-1-plan-amend-9")])
        own = caretaker.owned(j.st, PLAN_PROBLEM)
        self.assertEqual((own["count"], own["next"]), (1, 2), "zakończona `done` poprawka nie zeruje limitu")
        # Dopiero decyzja użytkownika zamyka problem i zeruje licznik.
        j.add("plan.revision-discarded")
        self.assertEqual(caretaker.owned(j.st, PLAN_PROBLEM)["count"], 0)

    def test_user_return_resets_the_count(self):
        j = planning_journal()
        care(j)
        self.assertEqual(caretaker.owned(j.st, PLAN_PROBLEM)["count"], 1)
        j.add("plan.returned", feedback="Moje uwagi.", baseline=ROWS)
        self.assertEqual(caretaker.owned(j.st, PLAN_PROBLEM)["count"], 0)

    def test_history_has_one_caretaker_entry_and_no_plan_returned_entry(self):
        j = planning_journal()
        care(j)
        j.add("plan.returned", feedback="Wiersz #1.", baseline=ROWS, caretaker=PLAN_PROBLEM,
              caretakerStep="K-1-care-1", caretakerFrom="K-1-plan-amend-4")
        j.add(caretaker.ACTION, problem=PLAN_PROBLEM, step="K-1-care-1", target="K-1-plan-amend-4", ticket="",
              action="return-plan", params={"feedback": "Wiersz #1.", "reason": "Brak kryterium w wierszu #1."},
              ok=True, error="", code="", label="plan", retried="K-1-plan-amend-4", stale=False)
        entries = derived(j.st, {"column": "todo"})["flow"]["history"]
        mine = [(h["text"], h["actor"]) for h in entries if h["kind"] == "caretaker"]
        self.assertEqual(mine, [("Opiekun: odesłał plan z uwagą — Brak kryterium w wierszu #1.", "caretaker")])
        self.assertFalse([h for h in entries if "plan.returned" in h["id"]])
        failed = caretaker.history_text({"type": caretaker.ACTION, "action": "return-plan", "ok": False,
                                         "error": "bad-findings: uwaga 9 spoza listy"})
        self.assertEqual(failed, "Opiekun: nie udało się (odesłanie planu) — bad-findings: uwaga 9 spoza listy")


class ActionOnJournalTest(EngineOnJournal):
    """`_caretaker_act` na prawdziwym `FlowEngine`: ta sama ścieżka `edit_plan`, nieaktualność i nieudane odesłanie."""

    def setUp(self):
        super().setUp()
        self.j = planning_journal()
        for event in self.j.events:
            fields = {k: v for k, v in event.items() if k not in ("seq", "ts", "type")}
            self.add("K-1", event["type"], **fields)
        self.run_event = self.add("K-1", caretaker.STARTED, problem=PLAN_PROBLEM, key=f"caretaker:{PLAN_PROBLEM}:1",
                                  n=1, step="K-1-care-1", target="K-1-plan-amend-4", root="K-1-plan-amend-4",
                                  ticket="", trigger="plan-fix", triggers=["plan-fix"],
                                  decision=f"plan-revision-failed:{fold(self.journal.read('K-1')).plan_revision['seq']}",
                                  watermark=len(self.journal.read("K-1")))

    def act(self, params, run=None):
        with mock.patch.object(self.engine, "_guard_check", return_value=None), \
                mock.patch.object(self.engine, "_guard_rebase", return_value=None), \
                mock.patch.object(self.engine, "_advance_user_revision") as advance:
            self.engine._caretaker_act("K-1", run or self.run_event, "return-plan", params)
        self.assertFalse(advance.called, "plan-amend rusza w pętli, nie w akcji opiekuna")
        return self.journal.read("K-1")

    def test_return_plan_goes_through_edit_plan_once_even_when_repeated(self):
        params = {"feedback": "Wiersz #1: kryterium; błąd standardu 1.2.", "findings": "1, 3",
                  "reason": "Brak kryterium."}
        events = self.act(params)
        returned = [e for e in events if e["type"] == "plan.returned" and e.get("caretaker")]
        self.assertEqual(len(returned), 1)
        self.assertEqual((returned[0]["caretaker"], returned[0]["caretakerStep"], returned[0]["caretakerFrom"]),
                         (PLAN_PROBLEM, "K-1-care-1", "K-1-plan-amend-4"))
        feedback = returned[0]["feedback"]
        for part in ("Pierwszy plasterek nie ma mierzalnego kryterium.", "Drobiazg w nazwie drugiego.",
                     "Wiersz #1: kryterium; błąd standardu 1.2."):
            self.assertIn(part, feedback)
        self.assertNotIn("Rozdziel pierwszy plasterek.", feedback, "niezaznaczona uwaga nie jest odsyłana")
        actions = [e for e in events if e["type"] == caretaker.ACTION]
        self.assertEqual([(a["ok"], a["action"], a["code"]) for a in actions], [(True, "return-plan", "")])
        # Powtórka po restarcie (zamiar bez wyniku) nie zapisuje drugiego odesłania.
        from tide_daemon.cardflow.engine import _CaretakerPort
        port = _CaretakerPort(self.engine, "K-1", self.run_event)
        problem = {"id": PLAN_PROBLEM, "step": "K-1-plan-amend-4", "decision": "x"}
        self.assertEqual(port.return_plan(problem, params), {"target": "K-1-plan-amend-4", "label": "plan"})
        self.assertEqual(len([e for e in self.journal.read("K-1") if e["type"] == "plan.returned"
                              and e.get("caretaker")]), 1)
        self.assertFalse([e for e in events if e["type"] in ("plan.approved", "plan.revision-approved")])

    def test_findings_outside_the_review_list_fail_with_a_reason(self):
        events = []
        invalid = ("9", "0", "x", "1,4", "²", "①", "1,²")
        for n, findings in enumerate(invalid, 1):
            with self.subTest(findings=findings):
                run = self.run_event if n == 1 else self.add(
                    "K-1", caretaker.STARTED, problem=PLAN_PROBLEM, key=f"caretaker:{PLAN_PROBLEM}:{n}", n=n,
                    step=f"K-1-care-{n}", target="K-1-plan-amend-4", root="K-1-plan-amend-4", ticket="",
                    trigger="plan-fix", triggers=["plan-fix"], decision=self.run_event["decision"],
                    watermark=len(self.journal.read("K-1")))
                st = fold(self.journal.read("K-1"))
                parsed = caretaker.parse_result(
                    {"result": "done", "action": "return-plan", "feedback": "Wiersz #1.",
                     "findings": findings, "reason": "Powód."}, caretaker.problems(st)[0], st)
                self.assertTrue(parsed["ok"])
                events = self.act(parsed["params"], run=run)
                self.assertFalse([e for e in events if e["type"] == "plan.returned" and e.get("caretaker")])
                last = [e for e in events if e["type"] == caretaker.ACTION][-1]
                self.assertEqual((last["ok"], last["code"]), (False, "bad-findings"))
                self.assertIn("spoza listy uwag review (3)", last["error"])
                st = fold(events)
                self.assertEqual(st.results[("caretaker-action", run["step"])]["failed"], "bad-findings")
                self.assertEqual(st.open_intents(include_background=True), [])
        self.assertEqual(caretaker.owned(fold(events), PLAN_PROBLEM)["count"], len(invalid),
                         "nieudane działania liczą się do limitu")
        self.assertFalse([e for e in events if e["type"] in ("plan.approved", "plan.revision-approved")])

    def test_unicode_findings_reconcile_closes_intent_and_reaches_the_action_limit(self):
        run = self.run_event
        limit = self.engine._caretaker_setup(fold(self.journal.read("K-1")))["limit"]
        for n in range(1, limit + 1):
            st = fold(self.journal.read("K-1"))
            parsed = caretaker.parse_result(
                {"result": "done", "action": "return-plan", "feedback": "Wiersz #1.",
                 "reason": "Powód.", "findings": "²"}, caretaker.problems(st)[0], st)
            self.assertTrue(parsed["ok"])
            self.add("K-1", "step.finished", step=run["step"], result="done", outcome=parsed)
            # Restart między zamiarem a wynikiem: odtworzenie musi zamknąć także niepoprawną akcję.
            intent = self.add("K-1", "intent", action="caretaker-action", key=run["step"],
                              problem=PLAN_PROBLEM, caretakerAction="return-plan", params=parsed["params"])
            with mock.patch.object(self.engine, "_guard_check", return_value=None), \
                    mock.patch.object(self.engine, "_guard_rebase", return_value=None):
                self.engine._reconcile("K-1", intent)
                self.engine._reconcile("K-1", intent)
            st = fold(self.journal.read("K-1"))
            self.assertEqual(st.open_intents(include_background=True), [])
            self.assertEqual(st.results[("caretaker-action", run["step"])]["failed"], "bad-findings")
            self.assertEqual((st.caretaker_outcomes[run["step"]]["ok"],
                              st.caretaker_outcomes[run["step"]]["code"]), (False, "bad-findings"))
            self.assertEqual(len(st.caretaker_outcomes), n, "odtwarzanie nie dubluje wyniku")
            self.assertEqual(caretaker.owned(st, PLAN_PROBLEM)["count"], n)
            with mock.patch.object(self.engine, "_caretaker_launch") as launch:
                self.engine._caretake("K-1", st)
            if n < limit:
                launch.assert_called_once()
                run = launch.call_args.args[2]
                self.assertEqual(run["n"], n + 1)
            else:
                launch.assert_not_called()
        events = self.journal.read("K-1")
        escalated = [e for e in events if e["type"] == caretaker.ESCALATED]
        self.assertEqual([(e["code"], e["count"]) for e in escalated], [(caretaker.LIMIT, limit)])
        shown = decisions.build_decisions(fold(events))
        self.assertEqual([d["kind"] for d in shown], ["plan-revision-failed"])
        self.assertTrue(shown[0]["caretaker"]["summary"].startswith(f"Opiekun wykonał {limit} z {limit}"))
        self.assertEqual(len(shown[0]["caretaker"]["tried"]), limit)
        self.assertFalse([e for e in events if e["type"] in ("plan.approved", "plan.revision-approved")
                          or e["type"] == "plan.returned" and e.get("caretaker")])

    def test_no_plan_to_return_fails_with_a_reason_and_approves_nothing(self):
        # Druga nieudana poprawa formatu: planowanie bez propozycji planu.
        self.add("K-2", "card.started", entry="planning", executor="Sonnet", provider="anthropic",
                 preferredProvider="anthropic", column="todo")
        run = self.add("K-2", caretaker.STARTED, problem="plan-fix:K-2-plan-1-2", key="caretaker:plan-fix:K-2-plan-1-2:1",
                       n=1, step="K-2-care-1", target="K-2-plan-1-2", root="K-2-plan-1-2", ticket="",
                       trigger="plan-fix", triggers=["plan-fix"], decision="", watermark=1)
        with mock.patch.object(self.engine, "_guard_check", return_value=None), \
                mock.patch.object(self.engine, "_guard_rebase", return_value=None), \
                mock.patch.object(caretaker, "stale", return_value=""):
            self.engine._caretaker_act("K-2", run, "return-plan", {"feedback": "Wiersz #1.", "reason": "Powód."})
        events = self.journal.read("K-2")
        last = [e for e in events if e["type"] == caretaker.ACTION][-1]
        self.assertEqual((last["ok"], last["code"]), (False, "no-plan"))
        self.assertIn("brak planu", last["error"])
        self.assertFalse([e for e in events if e["type"] in ("plan.returned", "plan.approved",
                                                              "plan.revision-approved")])

    def test_user_return_while_the_caretaker_works_makes_the_action_stale(self):
        self.add("K-1", "plan.returned", feedback="Moje uwagi.", baseline=ROWS)
        events = self.act({"feedback": "Wiersz #1.", "reason": "Powód."})
        self.assertEqual(len([e for e in events if e["type"] == "plan.returned"]), 2)
        self.assertFalse([e for e in events if e["type"] == "plan.returned" and e.get("caretaker")])
        last = [e for e in events if e["type"] == caretaker.ACTION][-1]
        self.assertEqual((last["ok"], last["stale"], last["error"]), (False, True, "stale"))
        self.assertEqual(caretaker.owned(fold(events), PLAN_PROBLEM)["count"], 0)

    def test_discarding_or_editing_the_plan_makes_the_action_stale(self):
        for event in ("plan.revision-discarded", "plan.ticket-edited"):
            with self.subTest(event=event):
                self.add("K-1", event, ticket="#1", changes={"class": "mocny"})
                events = self.act({"feedback": "Wiersz #1.", "reason": "Powód."})
                last = [e for e in events if e["type"] == caretaker.ACTION][-1]
                self.assertTrue(last["stale"], last)
                self.assertFalse([e for e in events if e["type"] == "plan.returned" and e.get("caretaker")])
                break


class PlanFlowTest(FlowInProcess):
    """Przebiegi przez `FlowEngine` z atrapą `claude` (plan z nieudaną poprawką, druga nieudana poprawka formatu)."""
    proposal = fixtures.PlanFlowTest.proposal
    submit = fixtures.PlanFlowTest.submit

    def caretaker_steps(self, card):
        return [s for s in self.of(card, "step.started") if s["kind"] == "caretaker"]

    def prompt(self, step):
        with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            return fh.read()

    def test_failed_revision_makes_the_caretaker_return_the_plan_with_its_note(self):
        card = self.card(description="ATRAPA: plan-review-structured")
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "return",
                                               "feedback": "ATRAPA: invalid-amend ATRAPA-OPIEKUN: return-plan"}),
                            box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(wait_until(lambda: self.of(card, "plan.revision-failed"), timeout=20), self.events(card))
        # Opiekun odsyła plan: drugi `plan.returned` z jego uwagą, potem druga poprawa planu.
        self.assertTrue(wait_until(lambda: [r for r in self.of(card, "plan.returned") if r.get("caretaker")],
                                   timeout=60), self.events(card))
        returned = [r for r in self.of(card, "plan.returned") if r.get("caretaker")][0]
        started = self.of(card, "caretaker.started")[0]
        self.assertEqual((started["trigger"], returned["caretaker"]), ("plan-fix", started["problem"]))
        self.assertTrue(started["problem"].startswith("plan-fix:"))
        self.assertIn("Wiersz #1: kryterium nie jest mierzalne", returned["feedback"])
        self.assertTrue(wait_until(lambda: len([s for s in self.of(card, "step.started")
                                                 if s["kind"] == "plan-amend"]) == 2, timeout=30), self.events(card))
        second = [s for s in self.of(card, "step.started") if s["kind"] == "plan-amend"][1]
        self.assertIn(returned["feedback"], self.prompt(second), "uwaga opiekuna trafia do kartki dosłownie")
        self.assertTrue(wait_until(lambda: self.of(card, "plan.revision-ready"), timeout=40), self.events(card))
        # Opiekun niczego nie zatwierdził; wpis w historii jest jeden i nie dubluje `plan.returned`.
        self.assertEqual(self.of(card, "plan.approved"), [])
        self.assertEqual(self.of(card, "plan.revision-approved"), [])
        action = self.of(card, "caretaker.action")[0]
        self.assertEqual((action["ok"], action["action"], action["stale"]), (True, "return-plan", False))
        shown = self.shown(card)["derived"]["flow"]
        mine = [h["text"] for h in shown["history"] if h["kind"] == "caretaker"]
        self.assertEqual(mine, ["Opiekun: odesłał plan z uwagą — Poprzednia poprawka nie przeszła sprawdzenia formatu."])
        self.assertFalse([d for d in shown["decisions"] if d["kind"] == "plan-revision-failed"])
        # Kontekst opiekuna: błędy formatu, poprzednia propozycja, uwagi review i standard planu.
        context = os.path.join(self.caretaker_steps(card)[0]["stepDir"], "in", "context")
        for rel in ("plan-fix/errors.md", "plan-fix/previous-proposal.md", "plan-fix/review-findings.md",
                    "plan-fix/failed-plan.md", "docs/agent-plan-standard.md"):
            self.assertTrue(os.path.isfile(os.path.join(context, rel)), rel)
        with open(os.path.join(context, "plan-fix", "review-findings.md"), encoding="utf-8") as fh:
            self.assertIn("1. [blocker] Pierwszy plasterek nie ma mierzalnego kryterium.", fh.read())

    def test_second_failed_format_fix_starts_the_caretaker_whose_retry_is_bounded_by_the_limit(self):
        card = self.card(description="ATRAPA: plan-bad ATRAPA-OPIEKUN: retry")
        self.assertTrue(self.start(card, entry="planning", mode="light")["ok"])
        self.assertTrue(wait_until(lambda: [e for e in self.of(card, "caretaker.escalated") if e.get("limit")],
                                   timeout=150), self.events(card))
        problem = self.of(card, "caretaker.started")[0]["problem"]
        self.assertTrue(problem.startswith("plan-fix:"))
        runs = self.of(card, "caretaker.started")
        self.assertEqual(([r["problem"] for r in runs], [r["n"] for r in runs]), ([problem] * 3, [1, 2, 3]))
        actions = self.of(card, "caretaker.action")
        self.assertEqual([(a["ok"], a["action"]) for a in actions], [(True, "retry")] * 3)
        fixes = [s for s in self.of(card, "step.started") if s["kind"] == "plan-fix"]
        retried = [s for s in fixes if s.get("parentStep")]
        self.assertEqual(len(retried), 3)
        self.assertIn("Instrukcja od opiekuna karty", self.prompt(retried[0]))
        self.assertIn("popraw format daty na ISO 8601", self.prompt(retried[0]))
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(len(self.caretaker_steps(card)), 3, "czwarty krok opiekuna nie startuje")
        escalated = [e for e in self.of(card, "caretaker.escalated") if e.get("limit")]
        self.assertEqual((escalated[0]["code"], escalated[0]["count"]), ("caretaker-limit", 3))
        shown = self.wait_column(card, "waiting")["derived"]["flow"]
        self.assertEqual([e["problem"] for e in shown["caretaker"]["escalated"]], [problem])
        self.assertTrue(shown["caretaker"]["escalated"][0]["summary"].startswith("Opiekun wykonał 3 z 3"))
        self.assertEqual(self.of(card, "plan.proposed"), [])
        decision = [d for d in shown["decisions"] if d["kind"] == "plan-fix-failed"]
        self.assertEqual(len(decision), 1, shown["decisions"])
        self.assertTrue(decision[0]["caretaker"]["summary"].startswith("Opiekun wykonał 3 z 3"))
        option = decision[0]["options"][0]
        before = len([s for s in self.of(card, "step.started") if s["kind"] == "plan-fix"])
        reply = self.flow.engine.resolve_step(card, {**option["params"], "answer": "Popraw datę na ISO 8601."})
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(wait_until(lambda: len([s for s in self.of(card, "step.started") if s["kind"] == "plan-fix"])
                                   > before, timeout=60), self.events(card))
        child = [s for s in self.of(card, "step.started") if s.get("parentStep") == decision[0]["step"]][0]
        self.assertTrue(wait_until(lambda: [e for e in self.of(card, "plan.checked")
                                           if e.get("step") == child["step"] and e.get("valid") is False], timeout=30))
        self.assertTrue(wait_until(lambda: [d for d in self.shown(card)["derived"]["flow"]["decisions"]
                                           if d["kind"] == "plan-fix-failed" and d["step"] != decision[0]["step"]],
                                   timeout=90), self.events(card))
        runs = self.of(card, "caretaker.started")
        self.assertEqual(([r["problem"] for r in runs], [r["n"] for r in runs]),
                         ([problem] * 6, [1, 2, 3, 4, 5, 6]))
        self.assertEqual(len([e for e in self.of(card, "caretaker.escalated") if e.get("limit")]), 2)

    def test_unknown_approval_action_fails_without_any_approval(self):
        card = self.card(description="ATRAPA: plan-bad ATRAPA-OPIEKUN: approve-plan")
        self.assertTrue(self.start(card, entry="planning", mode="light")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "caretaker.escalated"), timeout=60), self.events(card))
        escalated = self.of(card, "caretaker.escalated")[0]
        self.assertEqual((escalated["failed"], escalated["code"]), (True, "bad-caretaker-action"))
        self.assertEqual(self.of(card, "plan.approved"), [])
        self.assertEqual(self.of(card, "plan.revision-approved"), [])
        self.assertEqual(self.of(card, "plan.proposed"), [])

        reply = self.flow.engine.resolve_step(card, {"choice": "retry", "step": escalated["target"],
                                                   "answer": "Popraw format tabeli."})
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(wait_until(lambda: [e for e in self.of(card, "plan.checked")
                                           if e.get("step") != escalated["target"] and e.get("valid") is False
                                           and e["seq"] > reply["seq"]], timeout=30))
        self.assertTrue(wait_until(lambda: [d for d in self.shown(card)["derived"]["flow"]["decisions"]
                                           if d["kind"] == "plan-fix-failed" and d["step"] != escalated["target"]],
                                   timeout=30), self.events(card))
        self.assertEqual(len(self.of(card, "caretaker.started")), 2)


if __name__ == "__main__":
    unittest.main()
