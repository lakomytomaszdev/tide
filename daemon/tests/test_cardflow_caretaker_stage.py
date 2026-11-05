"""Opiekun przy błędzie Tide i karcie, która stoi (K-37, PT-147): wyzwalacze `flow-error` i `stalled` bez
kroku-sprawcy i bez decyzji, akcja `retry-stage`, nieaktualność bez decyzji, znacznik „opiekun sprawdza kartę”
i powrót do dzisiejszego znacznika po eskalacji.

Najpierw czyste funkcje (`caretaker.py`, `state.fold`/`derived`) na złożonych dziennikach, potem `FlowEngine`
z atrapą `claude` (tests/flowfakes.py): błąd przygotowania etapu wymusza podmienione `clean_tip` (sprawdzenie
worktree ticketu przed review), a co odpowiada atrapa opiekuna, mówi „ATRAPA-OPIEKUN: …” w tekście błędu (kartka go
cytuje) albo w sekcji kartki dopisanej w teście. Karta, która stoi, to luka w przepływie: `_advance` bez skutku po
pierwszym kroku i zegar silnika przesunięty o 3 minuty.
"""
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from tide_daemon.cardflow import caretaker, execution
from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.gitops import FlowGitError
from tide_daemon.cardflow.state import derived, fold, guard_reason

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_caretaker import Journal

FE = "FlowGitError: brak gałęzi ticket/pt-7"
FLOW_PROBLEM = "flow-error:advance:PT-7"
STALLED = "stalled:card"


def flow_error(j, error=FE, stage="advance:PT-7", ticket="PT-7"):
    return j.add("flow.error", error=error, stage=stage, step="K-1-PT-7-1", ticket=ticket, at=0)


def persisted(j, error=FE):
    return j.add("flow.error-persisted", error=error, stage="advance:PT-7", step="K-1-PT-7-1", ticket="PT-7")


def done_step(j, name="K-1-PT-7-1", ticket="PT-7"):
    j.step(name, ticket=ticket)
    j.add("step.finished", step=name, ticket=ticket, result="done", reason="")


def care(j, problem, trigger, step="K-1-care-1", n=1, ticket=""):
    return j.add(caretaker.STARTED, problem=problem, key=f"caretaker:{problem}:{n}", n=n, step=step, target="",
                 root="", ticket=ticket, trigger=trigger, triggers=[trigger], decision="",
                 watermark=j.events[-1]["seq"])


def ids(j):
    return [p["id"] for p in caretaker.problems(j.st)]


class FlowErrorTriggerTest(unittest.TestCase):
    def setUp(self):
        self.j = Journal()
        done_step(self.j)

    def test_one_off_error_is_not_a_problem(self):
        flow_error(self.j)
        self.assertEqual(ids(self.j), [])

    def test_error_persisting_into_the_next_pass_is_one_problem_without_a_step(self):
        flow_error(self.j)
        persisted(self.j)
        [problem] = caretaker.problems(self.j.st)
        self.assertEqual((problem["id"], problem["trigger"], problem["step"], problem["ticket"], problem["decision"]),
                         (FLOW_PROBLEM, "flow-error", "", "PT-7", ""))

    def test_same_text_repeated_after_it_disappeared_is_a_problem(self):
        flow_error(self.j)
        self.j.step("K-1-PT-7-2", ticket="PT-7")          # krok tego etapu rusza: błąd znika
        self.assertEqual(ids(self.j), [])
        flow_error(self.j)
        self.assertEqual(ids(self.j), [FLOW_PROBLEM])

    def test_marker_of_another_text_does_not_count(self):
        flow_error(self.j)
        persisted(self.j, error="FlowGitError: inny błąd")
        self.assertEqual(ids(self.j), [])

    def test_started_step_or_cleared_error_ends_the_problem(self):
        for end in ("step", "cleared"):
            with self.subTest(end=end):
                j = Journal()
                done_step(j)
                flow_error(j)
                persisted(j)
                if end == "step":
                    j.step("K-1-PT-7-2", ticket="PT-7")
                else:
                    j.add("flow.error-cleared", error=FE, caretaker=FLOW_PROBLEM, caretakerStep="K-1-care-1")
                self.assertEqual(ids(j), [])
                self.assertEqual(j.st.flow_error, "")

    def test_persisted_marker_does_not_count_as_card_activity(self):
        flow_error(self.j)
        before = self.j.st.last_activity_ts
        persisted(self.j)                               # późniejszy `ts` (atrapa dziennika liczy go z `seq`)
        self.assertEqual(self.j.st.last_activity_ts, before)


class StalledTriggerTest(unittest.TestCase):
    def stalled(self, *before):
        j = Journal()
        done_step(j)
        for kind, fields in before:
            j.add(kind, **fields)
        j.add("card.stalled", reason="brak kroku i powodu czekania")
        return j

    def test_stalled_card_is_one_problem_without_a_step(self):
        [problem] = caretaker.problems(self.stalled().st)
        self.assertEqual((problem["id"], problem["step"], problem["ticket"], problem["decision"]),
                         (STALLED, "", "", ""))

    def test_named_wait_reasons_are_not_a_problem(self):
        cases = {
            "slot": ("step.waiting", {"ticket": "PT-8", "kind": "ticket", "reason": "limit kroków dostawcy anthropic: 3/3"}),
            "limit": ("step.waiting", {"ticket": "PT-8", "kind": "ticket", "reason": "limit Claude do 14:00"}),
            "pause": ("card.paused", {}),
            "running": ("step.started", {"step": "K-1-PT-8-1", "kind": "ticket", "ticket": "PT-8"}),
            "intent": ("intent", {"action": "merge", "key": "PT-7"}),
        }
        for name, event in cases.items():
            with self.subTest(name=name):
                j = self.stalled(event)
                self.assertTrue(j.st.stalled_since)
                self.assertNotEqual(guard_reason(j.st), "")
                self.assertEqual(ids(j), [])

    def test_caretaker_work_keeps_the_card_stalled_and_the_problem_open(self):
        j = self.stalled()
        care(j, STALLED, "stalled")
        j.add("step.waiting", ticket="", kind="caretaker", reason="limit kroków dostawcy anthropic: 3/3")
        j.add("intent", action="step-start", key="K-1-care-1")
        j.add("result", action="step-start", key="K-1-care-1", ok=True)
        j.add("step.started", step="K-1-care-1", kind="caretaker", ticket="", executor="Opus")
        j.add("step.activity", step="K-1-care-1", ticket="")
        st = j.st
        self.assertTrue(st.stalled_since, "praca opiekuna nie zdejmuje „stoi”")
        self.assertIsNone(guard_reason(st), "trwa krok opiekuna — strażnik nie zapisze drugiego card.stalled")
        self.assertEqual(ids(j), [STALLED])
        j.add("step.finished", step="K-1-care-1", ticket="", result="done", outcome={"ok": True})
        j.add(caretaker.ACTION, problem=STALLED, step="K-1-care-1", target="", ticket="", action="retry-stage",
              params={}, ok=False, error="stage-no-change: …", code="stage-no-change", label="„card”",
              retried="card", stale=False)
        self.assertTrue(j.st.stalled_since)
        self.assertEqual(ids(j), [STALLED])

    def test_a_flow_error_problem_is_the_only_problem_of_a_card_that_stalls_on_it(self):
        j = Journal()
        done_step(j)
        flow_error(j)
        persisted(j)
        j.add("card.stalled", reason=FE)
        self.assertEqual(ids(j), [FLOW_PROBLEM])

    def test_one_off_flow_error_card_that_stalls_is_stalled(self):
        j = Journal()
        done_step(j)
        flow_error(j)
        j.add("card.stalled", reason=FE)
        self.assertEqual(ids(j), [STALLED])

    def test_real_work_ends_the_stall(self):
        j = self.stalled()
        j.step("K-1-PT-8-1", ticket="PT-8")
        self.assertEqual((j.st.stalled_since, ids(j)), ("", []))


class StaleWithoutDecisionTest(unittest.TestCase):
    def test_stalled_action_is_stale_once_a_step_started(self):
        j = Journal()
        done_step(j)
        j.add("card.stalled", reason="brak kroku i powodu czekania")
        run = care(j, STALLED, "stalled")
        j.add(caretaker.STAGE_RETRY, problem=STALLED, stage="card", ticket="", caretaker=STALLED,
              caretakerStep="K-1-care-1")
        self.assertEqual(caretaker.stale(j.st, run, j.events), "", "własne zdarzenia opiekuna")
        j.step("K-1-PT-8-1", ticket="PT-8")
        self.assertEqual(caretaker.stale(j.st, run, j.events), "po starcie opiekuna: step.started")

    def test_flow_error_action_is_stale_once_the_error_is_gone(self):
        j = Journal()
        done_step(j)
        flow_error(j)
        persisted(j)
        run = care(j, FLOW_PROBLEM, "flow-error", ticket="PT-7")
        self.assertEqual(caretaker.stale(j.st, run, j.events), "")
        j.add("flow.error-cleared", error=FE, caretaker="kto inny")   # ślad z polem `caretaker` — nie liczy się
        self.assertEqual(caretaker.stale(j.st, run, j.events), "problem już nie występuje")


class ViewAndBriefTest(unittest.TestCase):
    def stalled_with_caretaker(self):
        j = Journal()
        done_step(j)
        j.add("card.stalled", reason="brak kroku i powodu czekania")
        care(j, STALLED, "stalled")
        j.add("step.started", step="K-1-care-1", kind="caretaker", ticket="", executor="Opus")
        return j

    def test_caretaker_marker_replaces_stalled_while_it_works(self):
        out = derived(self.stalled_with_caretaker().st, {"column": "todo"})
        self.assertEqual((out["column"], out["note"]), ("progress", "opiekun sprawdza kartę"))
        self.assertEqual(out["flow"]["decisions"], [])

    def test_after_escalation_the_card_is_stalled_again_with_the_diagnosis(self):
        j = self.stalled_with_caretaker()
        j.add("step.finished", step="K-1-care-1", ticket="", result="done", outcome={"ok": True})
        j.add(caretaker.ESCALATED, problem=STALLED, step="K-1-care-1", target="", ticket="", action="escalate",
              summary="Karta stoi po scaleniu PT-7.", recommendation="Uruchom domknięcie ręcznie.", optionId="",
              failed=False, error="", code="")
        out = derived(j.st, {"column": "todo"})
        self.assertEqual(out["column"], "waiting")
        self.assertTrue(out["note"].startswith("stoi"), out["note"])
        self.assertEqual([d["kind"] for d in out["flow"]["decisions"]], ["caretaker"])
        self.assertEqual([e["problem"] for e in out["flow"]["caretaker"]["escalated"]], [STALLED])
        texts = [h["text"] for h in out["flow"]["history"] if h["kind"] == "caretaker"]
        self.assertEqual(texts, ["Opiekun: oddaje decyzję — Karta stoi po scaleniu PT-7."])

    def test_after_flow_error_escalation_the_card_shows_todays_error(self):
        j = Journal()
        done_step(j)
        flow_error(j)
        persisted(j)
        care(j, FLOW_PROBLEM, "flow-error", ticket="PT-7")
        j.add("step.started", step="K-1-care-1", kind="caretaker", ticket="PT-7", executor="Opus")
        out = derived(j.st, {"column": "todo"})
        self.assertEqual((out["column"], out["note"]), ("progress", "opiekun sprawdza PT-7"))
        j.add("step.finished", step="K-1-care-1", ticket="PT-7", result="done", outcome={"ok": True})
        j.add(caretaker.ESCALATED, problem=FLOW_PROBLEM, step="K-1-care-1", target="", ticket="PT-7",
              action="escalate", summary="Gałąź ticketu usunięto ręcznie.", recommendation="Odrzuć ticket.",
              optionId="", failed=False, error="", code="")
        out = derived(j.st, {"column": "todo"})
        self.assertEqual(out["column"], "waiting")
        self.assertIn("brak gałęzi ticket/pt-7", out["note"])
        self.assertEqual(out["flow"]["flowError"], FE)
        self.assertEqual([d["kind"] for d in out["flow"]["decisions"]], ["caretaker"])
        self.assertEqual([e["problem"] for e in out["flow"]["caretaker"]["escalated"]], [FLOW_PROBLEM])

    def test_brief_has_the_error_recent_events_and_open_pairs(self):
        j = Journal()
        done_step(j)
        j.add("intent", action="merge", key="PT-7")
        flow_error(j, error="FlowGitError: brak gałęzi ticket/pt-7 GITHUB_TOKEN=ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")
        persisted(j, error=j.st.flow_error)
        [problem] = caretaker.problems(j.st)
        text = caretaker.brief(problem, j.st, card="K-1", out_dir="/o", worktree="/w")
        for part in ("## Problem: błąd Tide (`flow.error`)", "Tekst błędu: FlowGitError: brak gałęzi ticket/pt-7",
                     "Etap: advance:PT-7, krok K-1-PT-7-1, ticket PT-7; błąd utrzymał się w kolejnym przebiegu.",
                     "Ostatnie zdarzenia karty", "step.finished K-1-PT-7-1 PT-7 done",
                     "Otwarte pary „zamiar → wynik”", "- merge PT-7", "`retry-stage`", "GITHUB_TOKEN=•••"):
            self.assertIn(part, text)
        self.assertNotIn("ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", text)

    def test_stalled_brief(self):
        j = Journal()
        done_step(j)
        j.add("card.stalled", reason="brak kroku i powodu czekania")
        [problem] = caretaker.problems(j.st)
        text = caretaker.brief(problem, j.st, card="K-1", out_dir="/o", worktree="/w")
        for part in ("## Problem: karta stoi", "Ostatni krok: K-1-PT-7-1", "Otwarte pary „zamiar → wynik”",
                     "- (brak)", "`retry-stage`"):
            self.assertIn(part, text)

    def test_retry_stage_is_allowed_only_for_tide_errors_and_stalls_and_has_no_fields(self):
        data = {"result": "done", "action": "retry-stage", "summary": "ignorowane"}
        for trigger in ("flow-error", "stalled"):
            made = caretaker.parse_result(data, {"id": f"{trigger}:x", "trigger": trigger, "options": []}, Journal().st)
            self.assertEqual(made, {"ok": True, "action": "retry-stage", "params": {}})
        made = caretaker.parse_result(data, {"id": "error:x", "trigger": "error", "options": []}, Journal().st)
        self.assertEqual((made["ok"], made["code"]), (False, caretaker.BAD_ACTION))
        self.assertNotIn("`retry-stage`", caretaker.brief({"id": "error:x", "trigger": "error", "step": ""},
                                                          Journal().st, card="K-1", out_dir="/o", worktree="/w"))

    def test_history_of_retry_stage(self):
        base = {"type": caretaker.ACTION, "action": "retry-stage", "label": "„advance:PT-7”"}
        self.assertEqual(caretaker.history_text({**base, "ok": True}), "Opiekun: ponowił etap „advance:PT-7”")
        self.assertEqual(caretaker.history_text({**base, "ok": False, "error": "stage-failed: FlowGitError: x"}),
                         "Opiekun: nie udało się (ponowienie etapu) — stage-failed: FlowGitError: x")


class StageObservationTest(unittest.TestCase):
    """Rzeczywiste przejścia silnika z dziennikiem w pamięci, bez procesów i piaskownicy gita."""

    def setUp(self):
        self.j = Journal()
        self.engine = FlowEngine.__new__(FlowEngine)
        self.engine._settings_data = mock.Mock(return_value={})
        self.engine.models_config = self.engine.catalog = None
        self.engine.journal = SimpleNamespace(read=lambda card: self.j.events,
                                             append=lambda card, kind, **fields: self.j.add(kind, **fields))
        self.engine.clock = mock.Mock(return_value=100.0)
        self.engine.git = mock.Mock()
        self.engine.git.commit_plan.return_value = {"commit": "abc"}
        for name in ("_advance", "_plan_next", "_advance_close", "_guard_watched", "_guard_freeze",
                     "_guard_check", "_guard_rebase", "_guard_thaw", "_caretaker_launch"):
            setattr(self.engine, name, mock.Mock())
        self.engine._repo = mock.Mock(return_value="/r")
        self.engine._plan_files = mock.Mock(return_value={})
        self.engine._card_column = mock.Mock(return_value="todo")

    def observe(self, ticket, error=None):
        self.engine._flow_error("K-1", {"error": error or f"FlowGitError: brak {ticket}",
                                       "stage": f"advance:{ticket}", "ticket": ticket})

    def failing_pair(self, same_text=False):
        for now in (100.0, 111.0):
            self.engine.clock.return_value = now
            for ticket in ("PT-7", "PT-8"):
                self.observe(ticket, "OSError: Permission denied" if same_text else None)

    def test_parallel_errors_are_independent_even_with_the_same_text(self):
        for same_text in (False, True):
            with self.subTest(same_text=same_text):
                self.j = Journal()
                self.failing_pair(same_text)
                self.assertEqual(set(ids(self.j)), {FLOW_PROBLEM, "flow-error:advance:PT-8"})
                errors = [e for e in self.j.events if e["type"] == "flow.error"]
                markers = [e for e in self.j.events if e["type"] == "flow.error-persisted"]
                self.assertEqual(len(errors), 2, "deduplikacja w obrębie etapu")
                self.assertEqual({e["stage"] for e in markers}, {"advance:PT-7", "advance:PT-8"})

    def test_identical_text_at_another_stage_does_not_borrow_persistence_or_repetition(self):
        self.observe("PT-7", "OSError: Permission denied")
        self.engine.clock.return_value = 111.0
        self.observe("PT-8", "OSError: Permission denied")
        self.assertEqual(ids(self.j), [], "każdy etap potrzebuje własnej drugiej obserwacji")
        self.engine.clock.return_value = 122.0
        self.observe("PT-8", "OSError: Permission denied")
        self.assertEqual(ids(self.j), ["flow-error:advance:PT-8"])

    def test_two_actual_stage_failures_persist_across_engine_passes(self):
        for ticket in ("PT-7", "PT-8"):
            done_step(self.j, name=f"K-1-{ticket}-1", ticket=ticket)
        self.engine._advance.side_effect = FlowGitError("ten sam błąd przygotowania review")
        for now in (100.0, 111.0):
            self.engine.clock.return_value = now
            found = self.engine._advance_card("K-1")
            self.assertEqual({e["stage"] for e in found}, {"advance:PT-7", "advance:PT-8"})
            for item in found:
                self.engine._flow_error("K-1", item)
        self.assertEqual(set(ids(self.j)), {FLOW_PROBLEM, "flow-error:advance:PT-8"})

    def test_resolved_stage_without_a_new_step_invalidates_action_even_if_the_error_recurs(self):
        done_step(self.j)
        self.observe("PT-7")
        self.engine.clock.return_value = 111.0
        self.observe("PT-7")
        run = care(self.j, FLOW_PROBLEM, "flow-error", ticket="PT-7")
        self.assertEqual(self.engine._advance_card("K-1"), [])
        self.assertEqual(ids(self.j), [])
        self.observe("PT-7")
        self.assertEqual(ids(self.j), [FLOW_PROBLEM], "nawrót w obrębie tego samego etapu")
        self.assertEqual(caretaker.stale(self.j.st, run, self.j.events),
                         "po starcie opiekuna: flow.error-cleared")

    def test_retry_of_a_does_not_take_the_failure_of_b_or_clear_it(self):
        for ticket in ("PT-7", "PT-8"):
            done_step(self.j, name=f"K-1-{ticket}-1", ticket=ticket)
        self.failing_pair()
        run = care(self.j, FLOW_PROBLEM, "flow-error", ticket="PT-7")

        def advance(card, st):
            if st.steps[st.last_step]["ticket"] == "PT-8":
                raise FlowGitError("brak PT-8")

        self.engine._advance.side_effect = advance
        self.engine._caretaker_act("K-1", run, "retry-stage", {})
        [action] = [e for e in self.j.events if e["type"] == caretaker.ACTION]
        self.assertTrue(action["ok"], action)
        self.assertEqual(ids(self.j), ["flow-error:advance:PT-8"])
        self.assertEqual(caretaker.owned(self.j.st, FLOW_PROBLEM)["count"], 1)

    def test_escalating_b_does_not_hide_a(self):
        self.failing_pair()
        care(self.j, "flow-error:advance:PT-8", "flow-error", ticket="PT-8")
        self.j.add(caretaker.ESCALATED, problem="flow-error:advance:PT-8", step="K-1-care-1", ticket="PT-8")
        self.engine._caretake("K-1", self.j.st)
        [call] = self.engine._caretaker_launch.call_args_list
        self.assertEqual(call.args[2]["problem"], FLOW_PROBLEM)

    def test_brief_uses_its_stage_error_instead_of_the_last_error(self):
        self.failing_pair()
        problem = next(p for p in caretaker.problems(self.j.st) if p["id"] == FLOW_PROBLEM)
        text = caretaker.brief(problem, self.j.st, card="K-1", out_dir="/o", worktree="/w")
        self.assertIn("Tekst błędu: FlowGitError: brak PT-7", text)
        self.assertIn("ticket PT-7; błąd utrzymał się", text)

    def test_successful_normal_plan_commit_makes_late_retry_stale_without_spending_limit(self):
        self.j.add("result", action="card-branch", key="K-1", ok=True, branch="card/k-1", worktree="/w")
        self.j.add("plan.approved", rows=[])
        self.real_plan_next()
        self.engine.git.commit_plan.side_effect = FlowGitError("chwilowy błąd commita")
        for now in (100.0, 111.0):
            self.engine.clock.return_value = now
            for found in self.engine._advance_card("K-1"):
                self.engine._flow_error("K-1", found)
        [problem] = caretaker.problems(self.j.st)
        self.assertEqual(problem["id"], "flow-error:plan-commit")
        run = care(self.j, problem["id"], "flow-error")
        self.engine.git.commit_plan.side_effect = None
        self.assertEqual(self.engine._advance_card("K-1"), [])
        self.assertFalse(any(e["type"] == "step.started" for e in self.j.events))
        done = self.j.add("step.finished", step=run["step"], result="done",
                          outcome={"ok": True, "action": "retry-stage", "params": {}})
        self.engine._caretaker_apply("K-1", self.j.st, run, done)
        [action] = [e for e in self.j.events if e["type"] == caretaker.ACTION]
        self.assertEqual((action["ok"], action["error"], action["stale"]), (False, "stale", True))
        self.assertFalse(any(e["type"] == caretaker.STAGE_RETRY for e in self.j.events))
        self.assertEqual(caretaker.owned(self.j.st, problem["id"])["count"], 0)
        self.assertEqual(ids(self.j), [])

    def test_resolving_one_ticket_does_not_clear_another_ticket_error(self):
        self.failing_pair(same_text=True)
        self.j.step("K-1-PT-8-2", ticket="PT-8")
        self.assertEqual(ids(self.j), [FLOW_PROBLEM])

    def test_clearing_one_stage_with_identical_text_leaves_the_other(self):
        self.failing_pair(same_text=True)
        self.j.add("flow.error-cleared", stage="advance:PT-8", error="OSError: Permission denied")
        self.assertEqual(ids(self.j), [FLOW_PROBLEM])

    def observe_pass(self, now):
        self.engine.clock.return_value = now
        found = self.engine._advance_card("K-1")
        for item in found:
            self.engine._flow_error("K-1", item)
        return found

    def real_plan_next(self):
        self.engine._plan_next = FlowEngine._plan_next.__get__(self.engine)
        self.engine._project = mock.Mock(return_value=(SimpleNamespace(title="Plan"), SimpleNamespace(repo="/r")))
        self.engine.git.main_branch.return_value = "master"
        self.engine.git.branch_worktree.return_value = {"branch": "card/k-1-plan", "worktree": "/w"}

    def planned_stall(self):
        self.j.events[0]["entry"] = "plan"
        self.j.add("plan.proposed", planner="Sonnet")
        self.j.add("plan.reviewed", verdict="APPROVE")
        self.j.add("plan.approved", rows=[{"id": "PT-7", "title": "Ticket", "blockers": []}])
        done_step(self.j)
        self.real_plan_next()
        # Jak `idle_card`: przejście ticket -> review nic nie wykonuje, ale plan jest naprawdę zapisywany.
        self.assertEqual(self.observe_pass(100.0), [])
        self.engine.git.commit_plan.assert_called_once()
        self.j.add("card.stalled", reason="brak kroku i powodu czekania")
        self.assertEqual(ids(self.j), [STALLED])

    def test_planned_stall_survives_caretaker_lifecycle_and_retries_reach_limit(self):
        self.planned_stall()
        stalled_since = self.j.st.stalled_since
        last_activity = self.j.st.last_activity_ts
        for n in range(1, 4):
            self.engine._caretake("K-1", self.j.st)
            run = self.j.st.caretaker_runs[f"K-1-care-{n}"]
            events = [
                ("step.waiting", {"ticket": "", "kind": "caretaker", "reason": "limit kroków dostawcy"}),
                ("step.started", {"step": run["step"], "ticket": "", "kind": "caretaker", "executor": "Opus"}),
                ("step.finished", {"step": run["step"], "ticket": "", "result": "done",
                                   "outcome": {"ok": True, "action": "retry-stage", "params": {}}}),
            ]
            for kind, fields in events:
                with self.subTest(n=n, event=kind):
                    done = self.j.add(kind, **fields)
                    self.assertEqual(self.observe_pass(111.0 + n), [])
                    self.assertEqual(ids(self.j), [STALLED])
                    self.assertEqual(self.j.st.stalled_since, stalled_since)
                    self.assertEqual(self.j.st.last_activity_ts, last_activity)
                    self.engine.git.commit_plan.assert_called_once()
            self.engine._advance.reset_mock()
            self.engine._caretaker_apply("K-1", self.j.st, run, done)
            self.engine._advance.assert_called_once()  # dokładnie jeden przebieg ponowienia
            action = self.j.st.caretaker_outcomes[run["step"]]
            self.assertEqual((action["ok"], action["code"], action["stale"]),
                             (False, caretaker.STAGE_NO_CHANGE, False))
            self.assertEqual(caretaker.owned(self.j.st, STALLED)["count"], n)
            self.assertEqual(ids(self.j), [STALLED])
        self.assertEqual(len([e for e in self.j.events if e["type"] == caretaker.STAGE_RETRY]), 3)
        self.engine._caretaker_launch.reset_mock()
        for _ in range(2):
            self.engine._caretake("K-1", self.j.st)
            self.assertEqual(self.observe_pass(130.0), [])
        self.engine._caretaker_launch.assert_not_called()
        self.engine.git.commit_plan.assert_called_once()
        own = caretaker.owned(self.j.st, STALLED)
        self.assertEqual((own["count"], own["handed"]), (3, True))
        [escalation] = [e for e in self.j.events if e["type"] == caretaker.ESCALATED]
        self.assertEqual((escalation["problem"], escalation["limit"]), (STALLED, True))
        out = self.engine.derived({"id": "K-1", "column": "todo"})
        self.assertTrue(out["note"].startswith("stoi"), out["note"])
        self.assertEqual([d["kind"] for d in out["flow"]["decisions"]], ["caretaker"])
        self.assertEqual([e["problem"] for e in out["flow"]["caretaker"]["escalated"]], [STALLED])

    def test_planned_stall_accepts_caretaker_escalation_after_normal_pass(self):
        self.planned_stall()
        self.engine._caretake("K-1", self.j.st)
        run = self.j.st.caretaker_runs["K-1-care-1"]
        self.j.add("step.started", step=run["step"], kind="caretaker", ticket="", executor="Opus")
        self.assertEqual(self.observe_pass(111.0), [])
        done = self.j.add("step.finished", step=run["step"], ticket="", result="done",
                          outcome={"ok": True, "action": "escalate", "params": {
                              "summary": "Brakuje przejścia do review.", "recommendation": "Sprawdź przepływ."}})
        self.assertEqual(self.observe_pass(122.0), [])
        self.engine._caretaker_apply("K-1", self.j.st, run, done)
        escalation = self.j.st.caretaker_outcomes[run["step"]]
        self.assertEqual(escalation["type"], caretaker.ESCALATED)
        self.assertEqual(escalation["summary"], "Brakuje przejścia do review.")
        self.engine.git.commit_plan.assert_called_once()

    def test_finished_plan_is_still_committed_without_starting_another_ticket(self):
        self.j.add("plan.approved", rows=[{"id": "PT-7", "title": "Ticket", "blockers": []}])
        self.j.add("result", action="merge", key="PT-7", ok=True)
        self.j.add("plan.finished", merged=1, rejected=0)
        self.real_plan_next()
        self.assertEqual(self.observe_pass(100.0), [])
        self.engine.git.commit_plan.assert_called_once()
        self.assertFalse(any(e["type"] == "step.started" for e in self.j.events))
        self.assertEqual(self.observe_pass(111.0), [])
        self.engine.git.commit_plan.assert_called_once()

    def test_real_close_error_survives_caretaker_work_and_retries_reach_limit(self):
        done_step(self.j)
        self.j.add("result", action="merge", key="PT-7", ok=True)
        self.j.add("result", action="cleanup", key="PT-7", ok=True)
        self.j.add("result", action="card-branch", key="K-1", ok=True, branch="card/k-1", worktree="/w")
        self.engine._advance_close = FlowEngine._advance_close.__get__(self.engine)
        self.engine._project = mock.Mock(return_value=(SimpleNamespace(), SimpleNamespace()))
        self.engine.models_config = None
        self.engine.catalog = mock.Mock(return_value={"Opus": {}})
        self.engine.model_probe = self.engine.which = None
        with mock.patch.object(execution, "resolve", side_effect=execution.ExecutionError(
                "unknown-executor", "wykonawca domknięcia został usunięty")) as resolve:
            for now in (100.0, 111.0):
                self.assertEqual([f["stage"] for f in self.observe_pass(now)], ["close"])
            self.assertEqual(ids(self.j), ["flow-error:close"])
            for n in range(1, 4):
                run = care(self.j, "flow-error:close", "flow-error", step=f"K-1-care-{n}", n=n)
                self.j.add("step.started", step=run["step"], kind="caretaker", ticket="", executor="Opus")
                before = resolve.call_count
                self.assertEqual(self.observe_pass(111.0 + n), [])
                self.assertEqual(resolve.call_count, before, "domknięcie pominięte, a nie naprawione")
                self.assertEqual(ids(self.j), ["flow-error:close"])
                self.assertFalse(any(e["type"] == "flow.error-cleared" for e in self.j.events))
                self.j.add("step.finished", step=run["step"], ticket="", result="done")
                self.engine._caretaker_act("K-1", run, "retry-stage", {})
                self.assertEqual(resolve.call_count, before + 1, "jedna próba domknięcia na akcję")
                action = self.j.st.caretaker_outcomes[run["step"]]
                self.assertEqual((action["ok"], action["code"]), (False, caretaker.STAGE_FAILED))
                self.assertEqual(caretaker.owned(self.j.st, "flow-error:close")["count"], n)
            self.engine._caretake("K-1", self.j.st)
            self.engine._caretake("K-1", self.j.st)
            self.engine._caretaker_launch.assert_not_called()
            self.assertTrue(caretaker.owned(self.j.st, "flow-error:close")["handed"])

    def test_real_plan_next_has_one_commit_attempt_and_one_problem_until_limit(self):
        # Zakończone planowanie też przechodzi przez rzeczywiste `_advance`; dawniej wołało `_plan_next`
        # przed jego wywołaniem w `_advance_card` i przed końcowym, trzecim miejscem commita planu.
        done_step(self.j, name="K-1-plan", ticket="")
        self.j.events[-2]["kind"] = "planning"
        self.j.add("plan.approved", rows=[{"id": "PT-7", "title": "Ticket", "blockers": []}])
        self.real_plan_next()
        self.engine._advance = FlowEngine._advance.__get__(self.engine)
        for name in ("_advance_limit", "_advance_plan_review", "_advance_planning", "_caretaker_resume"):
            setattr(self.engine, name, mock.Mock(return_value=False))
        self.engine.git.commit_plan.side_effect = FlowGitError("chwilowy błąd commita")
        for now in (100.0, 111.0):
            self.engine.git.commit_plan.reset_mock()
            found = self.observe_pass(now)
            with self.subTest(now=now):
                self.assertEqual([f["stage"] for f in found], ["plan-commit"])
                self.assertEqual(self.engine.git.commit_plan.call_count, 1)
        self.assertEqual(ids(self.j), ["flow-error:plan-commit"])
        for n in range(1, 4):
            run = care(self.j, "flow-error:plan-commit", "flow-error", step=f"K-1-care-{n}", n=n)
            self.engine.git.commit_plan.reset_mock()
            self.engine._caretaker_act("K-1", run, "retry-stage", {})
            self.assertEqual(self.engine.git.commit_plan.call_count, 1)
            action = self.j.st.caretaker_outcomes[run["step"]]
            self.assertEqual((action["ok"], action["code"]), (False, caretaker.STAGE_FAILED))
            self.assertEqual(caretaker.owned(self.j.st, "flow-error:plan-commit")["count"], n)
            self.assertEqual(ids(self.j), ["flow-error:plan-commit"])
        self.engine._caretake("K-1", self.j.st)
        self.engine._caretake("K-1", self.j.st)
        self.engine._caretaker_launch.assert_not_called()
        self.assertTrue(caretaker.owned(self.j.st, "flow-error:plan-commit")["handed"])


class StageEngineTest(FlowInProcess):
    """Przebiegi przez `FlowEngine`: krok ticketu kończy się `done`, a przygotowanie review (`clean_tip` — pierwsze
    sprawdzenie worktree ticketu przed review) pada błędem Tide, póki test go nie zwolni. Zwolnione zwraca czubek
    gałęzi bez piaskownicy gita (`bwrap` nie zawsze działa tam, gdzie biegną testy)."""

    def setUp(self):
        super().setUp()
        self.failing = {"on": True, "error": "", "only_retry": False}
        engine = self.flow.engine
        retrying = {"on": False}
        advance_card = engine._advance_card

        def clean_tip(repo, branch, path):
            if self.failing["on"] and not (self.failing["only_retry"] and retrying["on"]):
                raise FlowGitError(self.failing["error"])
            if self.failing["only_retry"] and retrying["on"]:
                self.failing["on"] = False      # przyczyna minęła w chwili ponowienia opiekuna
            return engine.git.sha(repo, f"refs/heads/{branch}")

        def advance_card_marked(card, *, own=None):
            retrying["on"] = own is not None
            try:
                return advance_card(card, own=own)
            finally:
                retrying["on"] = False

        # Monitor zabiera powiadomienia z silnika (`take_notices`); test zachowuje ich kopię.
        self.taken = []
        take = engine.take_notices

        def take_and_keep():
            got = take()
            self.taken.extend(got)
            return got

        patches = [mock.patch.object(engine.git, "clean_tip", side_effect=clean_tip),
                   mock.patch.object(engine, "_advance_card", side_effect=advance_card_marked),
                   mock.patch.object(engine, "take_notices", side_effect=take_and_keep)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def caretaker_steps(self, card_id):
        return [s for s in self.of(card_id, "step.started") if s["kind"] == "caretaker"]

    def trace(self, card_id) -> str:
        """Skrót dziennika do komunikatu nieudanej asercji."""
        return "\n".join(f"{e['seq']} {e['type']} {e.get('step') or e.get('action') or ''} {e.get('key') or ''} "
                         f"{e.get('kind') or ''} {e.get('result') or ''} {e.get('reason') or e.get('error') or ''}"[:300]
                         for e in self.events(card_id))

    def reviews(self, card_id):
        return [s for s in self.of(card_id, "step.started") if s["kind"] == "review"]

    def later(self, seconds):
        """Zegar silnika przesunięty o `seconds` (próg 10 s utrzymania błędu, 3 minuty zastoju)."""
        self.flow.engine.clock = lambda: time.time() + seconds

    def failing_card(self, care):
        self.failing["error"] = f"przygotowanie review nie powiodło się ATRAPA-OPIEKUN: {care}"
        card_id = self.card(description="Eksport.\n\nATRAPA: gotowe")
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "flow.error"), timeout=30), self.events(card_id))
        return card_id

    # --- błąd Tide ---------------------------------------------------------------------------------

    def test_one_off_error_does_not_start_the_caretaker_and_a_persisting_one_starts_it_once(self):
        # Jednorazowy: znika w następnym przebiegu (review rusza), opiekuna nie ma.
        once = self.failing_card("akcja-escalate")
        self.failing["on"] = False
        self.assertTrue(wait_until(lambda: self.reviews(once), timeout=30), self.events(once))
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(len(self.of(once, "flow.error")), 1)
        self.assertEqual(self.of(once, "flow.error-persisted"), [])
        self.assertEqual(self.of(once, "caretaker.started"), [])
        self.assertEqual(fold(self.events(once)).flow_error, "")

        # Utrzymujący się: ten sam błąd w przebiegach po 10 s — jeden znacznik i jeden opiekun.
        self.failing["on"] = True
        card_id = self.failing_card("akcja-escalate")
        first = self.of(card_id, "flow.error")[0]
        self.assertTrue(first["stage"].startswith("advance:"), first)
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(self.of(card_id, "flow.error-persisted"), [], "przed upływem 10 s")
        self.later(11)
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.escalated"), timeout=40), self.trace(card_id))
        self.assertEqual(len(self.of(card_id, "flow.error")), 1, "ten sam tekst nie daje nowego flow.error")
        self.assertEqual([m["since"] for m in self.of(card_id, "flow.error-persisted")], [first["seq"]])
        [run] = self.of(card_id, "caretaker.started")
        self.assertEqual((run["problem"], run["target"], run["decision"], run["ticket"]),
                         (f"flow-error:{first['stage']}", "", "", first["ticket"]))
        wait_until(lambda: False, timeout=1.5)
        self.assertEqual(len(self.caretaker_steps(card_id)), 1, "oddany problem nie uruchamia opiekuna znowu")

        # Po eskalacji: dzisiejszy znacznik błędu, diagnoza w historii i w `escalated[]`, bez nowej decyzji.
        card = self.wait_column(card_id, "waiting", timeout=30)
        flow = card["derived"]["flow"]
        self.assertIn("przygotowanie review nie powiodło się", card["derived"]["note"])
        self.assertEqual([d["kind"] for d in flow["decisions"]], ["caretaker"])
        self.assertEqual([e["problem"] for e in flow["caretaker"]["escalated"]], [run["problem"]])
        texts = [h["text"] for h in flow["history"] if h["kind"] == "caretaker"]
        self.assertEqual(texts, ["Opiekun: oddaje decyzję — Sprawdziłem."])

    def test_retry_stage_runs_the_stage_once_and_three_failures_escalate_after_the_limit(self):
        card_id = self.failing_card("akcja-retry-stage")
        self.later(11)
        self.assertTrue(wait_until(lambda: [e for e in self.of(card_id, "caretaker.escalated") if e.get("limit")],
                                   timeout=120), self.trace(card_id))
        actions = self.of(card_id, "caretaker.action")
        self.assertEqual([(a["action"], a["ok"], a["code"], a["stale"]) for a in actions],
                         [("retry-stage", False, caretaker.STAGE_FAILED, False)] * 3)
        self.assertIn("przygotowanie review nie powiodło się", actions[0]["error"])
        retries = self.of(card_id, caretaker.STAGE_RETRY)
        self.assertEqual([r["caretakerStep"] for r in retries], [a["step"] for a in actions], "jeden przebieg na akcję")
        self.assertEqual([len([r for r in self.results(card_id, "caretaker-action") if r["key"] == a["step"]])
                          for a in actions], [1, 1, 1])
        runs = self.of(card_id, "caretaker.started")
        self.assertEqual([r["n"] for r in runs], [1, 2, 3])
        self.assertEqual({r["problem"] for r in runs}, {runs[0]["problem"]})
        [limit] = self.of(card_id, "caretaker.escalated")
        self.assertEqual((limit["code"], limit["count"], limit["problem"]), (caretaker.LIMIT, 3, runs[0]["problem"]))
        wait_until(lambda: False, timeout=1.5)
        self.assertEqual(len(self.caretaker_steps(card_id)), 3, "czwarty krok opiekuna nie startuje")
        card = self.wait_column(card_id, "waiting", timeout=30)
        flow = card["derived"]["flow"]
        self.assertEqual([d["kind"] for d in flow["decisions"]], ["caretaker"])
        self.assertEqual([e["problem"] for e in flow["caretaker"]["escalated"]], [runs[0]["problem"]])
        texts = [h["text"] for h in flow["history"] if h["kind"] == "caretaker"]
        self.assertEqual(len(texts), 4)
        self.assertTrue(all(t.startswith("Opiekun: nie udało się (ponowienie etapu) — stage-failed") for t in texts[:3]))
        self.assertTrue(texts[3].startswith("Opiekun: oddaje decyzję po 3 działaniach (limit)"), texts[3])

    def test_retry_stage_that_succeeds_starts_the_stage_and_clears_the_error(self):
        self.failing["only_retry"] = True           # zwykły przebieg pada, ponowienie opiekuna już nie
        card_id = self.failing_card("akcja-retry-stage")
        self.later(11)
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.action"), timeout=40), self.trace(card_id))
        self.assertFalse(self.failing["on"], "przygotowanie etapu przeszło w przebiegu opiekuna")
        [action] = self.of(card_id, "caretaker.action")
        self.assertEqual((action["ok"], action["action"], action["code"]), (True, "retry-stage", ""), action)
        self.assertTrue(wait_until(lambda: self.reviews(card_id), timeout=10))
        events = self.events(card_id)
        retry = next(e for e in events if e["type"] == caretaker.STAGE_RETRY)
        review = self.reviews(card_id)[0]
        self.assertLess(retry["seq"], review["seq"])
        self.assertEqual(fold(events).flow_error, "")
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(len(self.caretaker_steps(card_id)), 1)
        texts = [h["text"] for h in self.shown(card_id)["derived"]["flow"]["history"] if h["kind"] == "caretaker"]
        self.assertEqual(texts, [f"Opiekun: ponowił etap „{action['retried']}”"])

    def test_error_gone_while_the_caretaker_works_makes_retry_stage_stale(self):
        card_id = self.failing_card("akcja-retry-stage-powoli")
        self.later(11)
        self.assertTrue(wait_until(lambda: self.caretaker_steps(card_id), timeout=30), self.events(card_id))
        label = f"opiekun sprawdza {self.of(card_id, 'caretaker.started')[0]['ticket']}"
        self.assertTrue(wait_until(lambda: self.shown(card_id)["derived"]["note"] == label, timeout=5),
                        self.shown(card_id)["derived"])
        self.failing["on"] = False                  # przyczyna mija, zwykły przebieg uruchamia review
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.action"), timeout=40), self.trace(card_id))
        [late] = self.of(card_id, "caretaker.action")
        self.assertEqual((late["ok"], late["error"], late["stale"]), (False, "stale", True), late)
        self.assertEqual(self.of(card_id, caretaker.STAGE_RETRY), [], "nic nie wykonano")
        self.assertEqual(self.results(card_id, "caretaker-action"), [])
        st = fold(self.events(card_id))
        self.assertEqual(caretaker.owned(st, late["problem"])["count"], 0, "nie zwiększa licznika")
        self.assertEqual(len(self.reviews(card_id)), 1)

    # --- karta stoi --------------------------------------------------------------------------------

    def idle_card(self, care="akcja-escalate"):
        """Krok ticketu kończy się `done`, a dalszy etap nic nie robi (`_advance` bez skutku po pierwszym kroku) —
        luka w przepływie, przy której karta stoi. Sekcja kartki opiekuna dostaje polecenie dla atrapy."""
        engine = self.flow.engine
        self.advancing = {"on": False}
        real = engine._advance

        def advance(card, st):
            if self.advancing["on"] or not st.steps:
                return real(card, st)
            return None

        def section(problem, st):
            return caretaker._stalled_section(problem, st) + f"\nATRAPA-OPIEKUN: {care}"

        for patch in (mock.patch.object(engine, "_advance", side_effect=advance),
                      mock.patch.dict(caretaker.BRIEF_SECTIONS, {"stalled": section})):
            patch.start()
            self.addCleanup(patch.stop)
        self.failing["on"] = False
        card_id = self.card(description="Eksport.\n\nATRAPA: gotowe")
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "step.finished"), timeout=30), self.events(card_id))
        wait_until(lambda: False, timeout=0.5)
        self.assertEqual(self.of(card_id, "card.stalled"), [])
        return card_id

    def stalled_card(self, care):
        card_id = self.idle_card(care)
        self.later(200)                             # 3 minuty bez pracy według zegara silnika
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.stalled"), timeout=10), self.events(card_id))
        return card_id

    def notices(self, card_id):
        return [n for n in [*self.taken, *self.flow.engine._notices]
                if n["card"] == card_id and n["result"] in ("stalled", "caretaker")]

    def test_stalled_card_starts_the_caretaker_and_escalation_brings_back_the_stalled_marker(self):
        card_id = self.stalled_card("akcja-escalate-powoli")
        self.assertTrue(wait_until(lambda: self.caretaker_steps(card_id), timeout=30), self.events(card_id))
        card = self.wait_column(card_id, "progress")
        self.assertEqual(card["derived"]["note"], "opiekun sprawdza kartę")
        [run] = self.of(card_id, "caretaker.started")
        self.assertEqual((run["problem"], run["target"], run["ticket"], run["decision"]), (STALLED, "", "", ""))
        self.assertEqual(len(self.of(card_id, "card.stalled")), 1)
        self.assertTrue(fold(self.events(card_id)).stalled_since, "praca opiekuna nie zdejmuje zastoju")

        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.escalated"), timeout=40), self.trace(card_id))
        card = self.wait_column(card_id, "waiting", timeout=30)
        self.assertTrue(card["derived"]["note"].startswith("stoi"), card["derived"]["note"])
        flow = card["derived"]["flow"]
        self.assertEqual([d["kind"] for d in flow["decisions"]], ["caretaker"])
        self.assertEqual([e["problem"] for e in flow["caretaker"]["escalated"]], [STALLED])
        self.assertEqual([h["text"] for h in flow["history"] if h["kind"] == "caretaker"],
                         ["Opiekun: oddaje decyzję — Sprawdziłem."])
        wait_until(lambda: False, timeout=1.5)
        # PT-154: jedno powiadomienie dopiero przy decyzji opiekuna.
        self.assertEqual(len(self.notices(card_id)), 1)
        self.assertEqual(len(self.caretaker_steps(card_id)), 1, "oddany problem nie uruchamia opiekuna znowu")
        self.assertEqual(len(self.of(card_id, "card.stalled")), 1)

    def test_step_started_while_the_caretaker_works_makes_retry_stage_stale(self):
        card_id = self.stalled_card("akcja-retry-stage-powoli")
        self.assertTrue(wait_until(lambda: self.caretaker_steps(card_id), timeout=30), self.events(card_id))
        self.advancing["on"] = True                 # karta rusza sama: review startuje
        self.assertTrue(wait_until(lambda: self.reviews(card_id), timeout=20), self.events(card_id))
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.action"), timeout=40), self.trace(card_id))
        [late] = self.of(card_id, "caretaker.action")
        self.assertEqual((late["ok"], late["error"], late["stale"]), (False, "stale", True), late)
        self.assertEqual(self.of(card_id, caretaker.STAGE_RETRY), [], "nic nie wykonano")
        self.assertEqual(self.results(card_id, "caretaker-action"), [])
        self.assertEqual(caretaker.owned(fold(self.events(card_id)), STALLED)["count"], 0)

    def test_named_wait_reasons_do_not_stall_the_card_or_start_the_caretaker(self):
        cases = {"slot": ("step.waiting", {"ticket": "PT-9", "kind": "ticket",
                                           "reason": "limit kroków dostawcy anthropic: 3/3"}),
                 "limit": ("step.waiting", {"ticket": "PT-9", "kind": "ticket", "reason": "limit Claude do 14:00"}),
                 "pause": ("card.paused", {})}
        for name, (kind, fields) in cases.items():
            with self.subTest(name=name):
                card_id = self.idle_card()
                self.flow.engine.journal.append(card_id, kind, **fields)
                self.later(200)
                wait_until(lambda: False, timeout=1.5)
                self.assertEqual(self.of(card_id, "card.stalled"), [])
                self.assertEqual(self.of(card_id, "caretaker.started"), [])
                self.flow.engine.clock = time.time


if __name__ == "__main__":
    unittest.main()
