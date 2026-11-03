"""PT-130: limit dostawcy to postój w „W toku”, nie decyzja; zatrzymane kroki, limity innych dostawców i koszt
przeniesienia w `derived.flow`.

Dziennik składany z publicznych zdarzeń, stan przez `fold`, wynik przez publiczne `derived` (tak jak widzi go Tablica).
Zmiana reguły kolumn jest zamierzona: karta z krokiem czekającym na limit przechodzi z „Czeka na mnie” do „W toku”.
"""
import unittest
from types import SimpleNamespace

from tide_daemon.cardflow import fullcard, limits
from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.state import derived
from tide_daemon.models.executors import default_catalog

from .test_cardflow_decisions import Events

CARD = {"column": "todo"}
NOW = 1_000_000.0


def stopped(events=None, name="K-1-PT-1-1", ticket="PT-1", files=3, reset="16:30", **extra):
    """Karta z krokiem zatrzymanym przez limit Claude; WIP zapisany (`files` plików)."""
    events = events or Events()
    events.step(name, ticket=ticket)
    events.add("step.limit", step=name, ticket=ticket, provider="anthropic", reset=reset, window="session")
    events.add("step.finished", step=name, ticket=ticket, result="error", reason="limit",
               wip={"stopped": True, "committed": True, "files": files, **extra})
    return events


def view(events):
    return derived(events.state(), CARD)


class ColumnRuleTest(unittest.TestCase):
    def test_limit_stop_stays_in_progress_with_marker_and_creates_no_decision(self):
        out = view(stopped())
        self.assertEqual(out["column"], "progress", "limit to postój w „W toku”, nie „Czeka na mnie”")
        self.assertIn("czeka na limit do 16:30", out["note"])
        self.assertEqual(out["flow"]["decisions"], [], "limit nigdy nie tworzy decyzji (D3)")
        self.assertEqual(out["question"], "")

    def test_marker_without_known_reset_has_no_hour(self):
        out = view(stopped(reset=""))
        self.assertEqual(out["column"], "progress")
        self.assertTrue(out["note"].endswith("czeka na limit"), out["note"])

    def test_waiting_for_reset_choice_keeps_the_card_in_progress(self):
        events = stopped()
        events.add("step.decision", step="K-1-PT-1-1", ticket="PT-1", choice="wait-limit", executor="")
        self.assertEqual(view(events)["column"], "progress")

    def test_unsafe_wip_still_needs_the_user(self):
        events = Events().step("K-1-PT-1-1")
        events.add("step.limit", step="K-1-PT-1-1", reset="16:30", window="session")
        events.add("step.finished", step="K-1-PT-1-1", ticket="PT-1", result="error", reason="limit",
                   wip={"stopped": True, "committed": False, "files": 2, "error": "git: brak miejsca"})
        out = view(events)
        self.assertEqual(out["column"], "waiting", "zapis zmian do naprawy to praca dla użytkownika, nie limit")
        self.assertEqual(out["flow"]["decisions"], [])

    def test_returns_to_the_usual_rules_after_the_limit_renews(self):
        events = stopped()
        # Odnowienie: Tide wznawia krok (nowa próba z `parentStep`) — kolumnę liczą zwykłe reguły.
        events.step("K-1-PT-1-2", ticket="PT-1", attempt=0, parent="K-1-PT-1-1")
        out = view(events)
        self.assertEqual(out["column"], "progress")
        self.assertEqual(out["flow"]["stoppedByLimit"], [])
        self.assertNotIn("limit", out["note"])
        # Wznowiony krok pyta o decyzję → wraca „Czeka na mnie”.
        events.add("step.finished", step="K-1-PT-1-2", ticket="PT-1", result="question",
                   question="Którą wersję zostawić?")
        out = view(events)
        self.assertEqual(out["column"], "waiting")
        self.assertEqual([d["kind"] for d in out["flow"]["decisions"]], ["question"])

    def _two_tickets(self, limit_first):
        events = Events()
        if limit_first:
            stopped(events, name="K-1-PT-2-1", ticket="PT-2")
        events.step("K-1-PT-1-1", ticket="PT-1", result="question", question="Którą wersję zostawić?")
        if not limit_first:
            stopped(events, name="K-1-PT-2-1", ticket="PT-2")
        return view(events)

    def test_decision_of_another_ticket_wins_over_limit_in_either_order(self):
        for limit_first in (True, False):
            with self.subTest(limit_first=limit_first):
                out = self._two_tickets(limit_first)
                self.assertEqual(out["column"], "waiting", "decyzja innego ticketu → „Czeka na mnie”")
                self.assertEqual([d["kind"] for d in out["flow"]["decisions"]], ["question"])
                self.assertNotIn("limit", out["note"])
                self.assertEqual(out["flow"]["question"], "Którą wersję zostawić?")

    def test_limit_stays_in_progress_when_another_ticket_finished_last(self):
        events = stopped(name="K-1-PT-2-1", ticket="PT-2")
        events.step("K-1-PT-1-1", ticket="PT-1", result="done")
        out = view(events)
        self.assertEqual(out["column"], "progress")
        self.assertIn("czeka na limit do 16:30", out["note"])
        events.step("K-1-PT-1-2", ticket="PT-1", kind="review", result="done", verdict="APPROVE")
        events.add("merge.finished", ticket="PT-1", ok=True)
        out = view(events)
        self.assertEqual(out["column"], "progress", "limit PT-2 nie znika po scaleniu PT-1")
        self.assertIn("czeka na limit do 16:30", out["note"])

    def test_running_step_note_keeps_the_limit_marker(self):
        events = stopped(name="K-1-PT-2-1", ticket="PT-2")
        events.step("K-1-PT-1-1", ticket="PT-1")
        out = view(events)
        self.assertEqual(out["column"], "progress")
        self.assertIn("czeka na limit do 16:30", out["note"])

    def test_plan_step_limit_does_not_hide_ticket_question(self):
        events = Events()
        events.step("K-1-plan-amend-1", ticket="", kind="plan-amend")
        events.add("step.limit", step="K-1-plan-amend-1", provider="anthropic", reset="16:30", window="session")
        events.add("step.finished", step="K-1-plan-amend-1", result="error", reason="limit",
                   wip={"stopped": True, "committed": True, "files": 0})
        events.step("K-1-PT-1-1", ticket="PT-1", result="question", question="Którą wersję zostawić?")
        out = view(events)
        self.assertEqual(out["column"], "waiting")
        self.assertEqual(out["flow"]["question"], "Którą wersję zostawić?")

    def test_review_question_is_not_hidden_by_another_ticket_limit(self):
        events = stopped(name="K-1-PT-2-1", ticket="PT-2")
        events.add("review.question", ticket="PT-1", round=3, question="Ponowić?")
        out = view(events)
        self.assertEqual(out["column"], "waiting")
        self.assertEqual([d["kind"] for d in out["flow"]["decisions"]], ["review"])

    def test_tracker_failure_is_not_hidden_by_limit(self):
        events = stopped(name="K-1-PT-2-1", ticket="PT-2")
        events.add("result", action="youtrack-create", key="k", ok=False, permanent=True, error="boom")
        out = view(events)
        self.assertEqual(out["column"], "waiting")
        self.assertEqual([d["kind"] for d in out["flow"]["decisions"]], ["tracker"])

    def test_fault_cause_alone_is_not_a_limit_stop(self):
        events = Events().step("K-1-PT-1-1", ticket="PT-1")
        events.add("step.finished", step="K-1-PT-1-1", ticket="PT-1", result="error", reason="restart",
                   faultCause="limit")
        self.assertEqual(view(events)["flow"]["stoppedByLimit"], [])

    def test_limit_does_not_leave_a_question_in_the_flow(self):
        self.assertEqual(view(stopped())["flow"]["question"], "")

    def test_returns_to_review_when_the_resumed_step_finishes(self):
        events = stopped()
        events.step("K-1-PT-1-2", ticket="PT-1", parent="K-1-PT-1-1", result="done")
        self.assertEqual(view(events)["column"], "progress")


class StoppedByLimitTest(unittest.TestCase):
    def test_lists_stopped_steps_with_wip_files_through_the_journal(self):
        events = stopped(files=3)
        stopped(events, name="K-1-PT-2-1", ticket="PT-2", files=0, reset="17:00")
        flow = view(events)["flow"]
        self.assertEqual([(s["step"], s["ticket"], s["wipFiles"]) for s in flow["stoppedByLimit"]],
                         [("K-1-PT-1-1", "PT-1", 3), ("K-1-PT-2-1", "PT-2", 0)])
        self.assertEqual({s["provider"] for s in flow["stoppedByLimit"]}, {"anthropic"})

    def test_wip_files_come_from_the_repaired_snapshot_when_present(self):
        events = Events().step("K-1-PT-1-1")
        events.add("step.finished", step="K-1-PT-1-1", ticket="PT-1", result="error", reason="limit",
                   wip={"stopped": True, "committed": False, "files": 5})
        events.add("step.wip", step="K-1-PT-1-1", ticket="PT-1", wip={"stopped": True, "committed": True, "files": 5})
        self.assertEqual(view(events)["flow"]["stoppedByLimit"][0]["wipFiles"], 5)

    def test_empty_without_limit_stops(self):
        flow = view(Events().step("K-1-PT-1-1"))["flow"]
        self.assertEqual((flow["stoppedByLimit"], flow["otherProviders"], flow["limitTransfer"]), ([], [], None))

    def test_transferred_step_is_no_longer_listed(self):
        events = stopped()
        events.step("K-1-PT-1-2", ticket="PT-1", parent="K-1-PT-1-1")
        self.assertEqual(view(events)["flow"]["stoppedByLimit"], [])


class OtherProvidersTest(unittest.TestCase):
    @staticmethod
    def snapshot(**remaining):
        def window(value):
            return {"session": {"remaining": value, "readTs": NOW, "resetTs": NOW + 3600}}
        return {"available": True, "services": {name: window(value) for name, value in remaining.items()}}

    def test_lists_remaining_limit_of_other_providers_only(self):
        found = limits.other_providers(self.snapshot(claude=0, chatgpt=62), {"anthropic"}, NOW)
        self.assertEqual(found, [{"provider": "openai", "remaining": 62}])

    def test_provider_without_fresh_reading_is_not_listed(self):
        self.assertEqual(limits.other_providers({"available": False}, {"anthropic"}, NOW), [])
        stale = self.snapshot(chatgpt=62)
        stale["services"]["chatgpt"]["session"]["readTs"] = NOW - 24 * 3600
        self.assertEqual(limits.other_providers(stale, {"anthropic"}, NOW), [])

    def test_lowest_window_counts_as_remaining(self):
        snap = {"available": True, "services": {"chatgpt": {
            "session": {"remaining": 80, "readTs": NOW}, "weekly": {"remaining": 35, "readTs": NOW}}}}
        self.assertEqual(limits.remaining_of(snap, "openai", NOW), 35)


class TransferCostTest(unittest.TestCase):
    STOPPED = [{"step": "a", "ticket": "PT-1", "wipFiles": 3, "provider": "anthropic", "until": ""},
               {"step": "b", "ticket": "PT-2", "wipFiles": 2, "provider": "anthropic", "until": ""}]

    def test_cost_names_steps_wip_files_and_remaining_limit_without_currency(self):
        offer = fullcard.limit_transfer(self.STOPPED, [{"provider": "openai", "remaining": 62}], 10,
                                        lambda provider: "Codex mocny")
        self.assertEqual(offer["text"], "Przenieś na Codex")
        self.assertEqual(offer["executor"], "Codex mocny")
        self.assertEqual(offer["cost"], "2 zatrzymane kroki i 5 plików WIP do przekazania; "
                                        "po przeniesieniu Codex ma jeszcze 62% limitu.")
        self.assertNotIn("zł", offer["cost"])
        self.assertNotIn("$", offer["cost"])

    def test_cost_for_one_step_and_one_file(self):
        offer = fullcard.limit_transfer(self.STOPPED[:1] and [{**self.STOPPED[0], "wipFiles": 1}],
                                        [{"provider": "openai", "remaining": 40.5}], 10, lambda provider: "Codex lekki")
        self.assertEqual(offer["cost"], "1 zatrzymany krok i 1 plik WIP do przekazania; "
                                        "po przeniesieniu Codex ma jeszcze 40.5% limitu.")

    def test_hidden_with_one_sentence_when_no_other_provider_has_limit(self):
        for others in ([], [{"provider": "openai", "remaining": 4}]):
            offer = fullcard.limit_transfer(self.STOPPED, others, 10, lambda provider: "Codex mocny")
            self.assertFalse(offer["available"])
            self.assertEqual(offer["reason"], fullcard.NO_TRANSFER_TARGET)
            self.assertEqual(offer["cost"], "")
            self.assertEqual(offer["reason"].count("."), 1)

    def test_hidden_when_provider_has_no_executor(self):
        offer = fullcard.limit_transfer(self.STOPPED, [{"provider": "openai", "remaining": 80}], 10, lambda p: "")
        self.assertFalse(offer["available"])

    def test_no_offer_without_stopped_steps(self):
        self.assertIsNone(fullcard.limit_transfer([], [{"provider": "openai", "remaining": 80}], 10, lambda p: "x"))


class ImplicitWaitTest(unittest.TestCase):
    """`_advance_limit`: bez wyboru użytkownika Tide czeka na odnowienie sam (limit nie jest decyzją, D3)."""

    def engine(self, *, recovered, mode="ask", executor=""):
        calls = []
        stub = SimpleNamespace(
            calls=calls,
            get_flow_settings=lambda: {"limitTransfer": {"mode": mode, "executor": executor}},
            _limit_recovered=lambda *args: recovered,
            _repair_wip_due=lambda *args: {"stopped": True, "committed": True, "files": 3},
            _wip_safe=lambda wip: True,
            _resume_step=lambda card, st, step, decision, **kw: calls.append(("resume", decision)))
        return stub

    def advance(self, stub):
        st = stopped().state()
        return FlowEngine._advance_limit(stub, "K-1", st)

    def test_resumes_the_same_step_after_the_reset_without_any_decision(self):
        stub = self.engine(recovered=True)
        self.assertTrue(self.advance(stub))
        self.assertEqual(stub.calls, [("resume", {"choice": "retry"})])

    def test_keeps_waiting_before_the_reset(self):
        stub = self.engine(recovered=False)
        self.assertTrue(self.advance(stub))
        self.assertEqual(stub.calls, [])

    def test_auto_mode_without_executor_also_waits_for_the_reset(self):
        stub = self.engine(recovered=True, mode="auto", executor="")
        self.assertTrue(self.advance(stub))
        self.assertEqual(stub.calls, [("resume", {"choice": "retry"})])


class EngineViewTest(unittest.TestCase):
    """`FlowEngine._limit_view` składa kontrakt z odczytu Limitów AI i katalogu wykonawców."""

    def engine(self, snapshot, preferred=""):
        return SimpleNamespace(
            limits_source=lambda: snapshot, clock=lambda: NOW, models_config=None, catalog=default_catalog,
            get_flow_settings=lambda: {"limitTransfer": {"mode": "ask", "executor": preferred},
                                       "limitThresholdPercent": 10})

    def run_view(self, snapshot, preferred="", events=None):
        events = events or stopped()
        out = derived(events.state(), CARD)
        FlowEngine._limit_view(self.engine(snapshot, preferred), events.state(), out)
        return out["flow"]

    def test_offers_codex_with_cost_from_limits_and_stopped_steps(self):
        flow = self.run_view(OtherProvidersTest.snapshot(claude=0, chatgpt=62))
        self.assertEqual(flow["otherProviders"], [{"provider": "openai", "remaining": 62}])
        offer = flow["limitTransfer"]
        self.assertTrue(offer["available"])
        self.assertEqual(offer["label"], "Codex")
        self.assertEqual(offer["executor"], "Codex standard", "ta sama klasa co zatrzymany Sonnet")
        self.assertEqual(offer["cost"], "1 zatrzymany krok i 3 pliki WIP do przekazania; "
                                        "po przeniesieniu Codex ma jeszcze 62% limitu.")

    def test_configured_transfer_executor_is_preferred(self):
        offer = self.run_view(OtherProvidersTest.snapshot(chatgpt=62), preferred="Codex mocny")["limitTransfer"]
        self.assertEqual(offer["executor"], "Codex mocny")

    def test_exhausted_other_provider_hides_the_action(self):
        flow = self.run_view(OtherProvidersTest.snapshot(claude=0, chatgpt=3))
        self.assertEqual(flow["otherProviders"], [{"provider": "openai", "remaining": 3}])
        self.assertFalse(flow["limitTransfer"]["available"])
        self.assertEqual(flow["limitTransfer"]["reason"], fullcard.NO_TRANSFER_TARGET)

    def test_missing_limit_reading_hides_the_action(self):
        flow = self.run_view({"available": False})
        self.assertEqual(flow["otherProviders"], [])
        self.assertFalse(flow["limitTransfer"]["available"])


if __name__ == "__main__":
    unittest.main()
