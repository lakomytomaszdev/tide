"""PT-128: pola pełnej karty w `derived` — fazy, powód czekania ticketu, ludzkie stany, trwające kroki, jedno zdanie.

Dziennik składany z publicznych zdarzeń, stan przez `fold`, wynik przez publiczne `derived` (tak jak widzi go Tablica).
"""
import unittest

from tide_daemon.cardflow.fullcard import PHASES, STATES, USER
from tide_daemon.cardflow.state import derived

from .test_cardflow_decisions import Events

CARD = {"column": "todo"}
PHASE_KEYS = {"id", "label", "state", "executor", "ts", "note"}


def view(events):
    return derived(events.state(), CARD)


def phase(events, ident):
    return next(p for p in view(events)["phases"] if p["id"] == ident)


def plan(rows=None):
    """Karta z planowaniem: Opus proponuje #1, #2 (bloker #1) i #3 (bloker #1)."""
    events = Events(entry="plan")
    events.step("K-1-planning-1", ticket="", kind="planning", executor="Opus", result="done")
    events.add("plan.proposed", planner="Opus", rows=rows or [
        {"workId": "#1", "title": "A", "blockers": [], "executor": "Sonnet", "reviewer": "Opus"},
        {"workId": "#2", "title": "B", "blockers": ["#1"], "executor": "Opus", "reviewer": "Sonnet"},
        {"workId": "#3", "title": "C", "blockers": ["#1"], "executor": "Sonnet", "reviewer": "Opus"}])
    return events


def approved():
    events = plan()
    events.add("plan.reviewed", reviewer="Sonnet", verdict="APPROVE", findings=[])
    events.add("plan.approved", map={"#1": "PT-1", "#2": "PT-2", "#3": "PT-3"}, rows=[
        {"id": "PT-1", "workId": "#1", "title": "A", "blockers": [], "executor": "Sonnet", "reviewer": "Opus"},
        {"id": "PT-2", "workId": "#2", "title": "B", "blockers": ["PT-1"], "executor": "Opus", "reviewer": "Sonnet"},
        {"id": "PT-3", "workId": "#3", "title": "C", "blockers": ["PT-1"], "executor": "Sonnet", "reviewer": "Opus"}])
    return events


def merge(events, ticket):
    events.add("result", action="merge", key=ticket, ok=True)
    return events


class ShapeTest(unittest.TestCase):
    def test_seven_phases_in_fixed_order_with_contract_fields(self):
        phases = view(Events().step("K-1-PT-1-1"))["phases"]
        self.assertEqual([p["id"] for p in phases], [ident for ident, _label in PHASES])
        self.assertEqual([p["label"] for p in phases], ["Zlecenie", "Planowanie", "Review planu", "Zatwierdzenie planu",
                                                        "Tickety", "Domknięcie", "Scalenie"])
        for item in phases:
            self.assertEqual(set(item), PHASE_KEYS)
            self.assertIn(item["state"], STATES)

    def test_card_without_journal_has_no_full_card_fields(self):
        events = Events()
        events.items = []
        self.assertIsNone(view(events))


class PhaseTest(unittest.TestCase):
    def test_order_is_done_when_the_card_starts(self):
        item = phase(Events(), "order")
        self.assertEqual((item["state"], item["executor"], item["ts"]), ("done", USER, "2026-11-03T10:01:00+01:00"))

    def test_planning_skipped_for_now_entry_running_then_done_with_planner(self):
        self.assertEqual([phase(Events().step("K-1-PT-1-1"), p)["state"] for p in
                          ("planning", "plan-review", "plan-approval")], ["skipped"] * 3)
        events = Events(entry="planning").step("K-1-planning-1", ticket="", kind="planning", executor="Opus")
        item = phase(events, "planning")
        self.assertEqual((item["state"], item["executor"]), ("now", "Opus"))
        item = phase(plan(), "planning")
        self.assertEqual((item["state"], item["executor"], item["ts"]), ("done", "Opus", "2026-11-03T10:04:00+01:00"))

    def test_plan_review_running_waiting_for_limit_then_done_with_verdict(self):
        events = plan().step("K-1-plan-review-1", ticket="", kind="plan-review", executor="Haiku")
        item = phase(events, "plan-review")
        self.assertEqual((item["state"], item["executor"]), ("now", "Haiku"))
        held = plan().add("step.waiting", ticket="", kind="plan-review", provider="anthropic", cause="quota",
                          until="16:30", reason="czeka na limit Claude do 16:30")
        held.add("plan.reviewer-selected", executor="Sonnet")  # wybór recenzenta zdjął decyzję, limit nadal trwa
        self.assertEqual(phase(held, "plan-review")["state"], "hold")
        reviewed = plan().add("plan.reviewed", reviewer="Sonnet", verdict="REQUEST_CHANGES", findings=[])
        item = phase(reviewed, "plan-review")
        self.assertEqual((item["state"], item["executor"], item["note"]), ("done", "Sonnet", "prosi o zmiany"))

    def test_plan_approval_waits_for_the_user_then_done(self):
        reviewed = plan().add("plan.reviewed", reviewer="Sonnet", verdict="APPROVE", findings=[])
        item = phase(reviewed, "plan-approval")
        self.assertEqual((item["state"], item["executor"]), ("you", USER))
        self.assertEqual(phase(plan(), "plan-approval")["state"], "todo")
        item = phase(approved(), "plan-approval")
        self.assertEqual((item["state"], item["executor"], item["ts"]), ("done", USER, "2026-11-03T10:06:00+01:00"))

    def test_tickets_todo_before_approval_then_now_you_hold_and_done(self):
        self.assertEqual(phase(plan(), "tickets")["state"], "todo")
        running = approved().step("K-1-PT-1-1", ticket="PT-1")
        item = phase(running, "tickets")
        self.assertEqual((item["state"], item["note"]), ("now", "fala 1 z 2"))
        asking = approved().step("K-1-PT-1-1", ticket="PT-1", result="question", question="Który format?")
        self.assertEqual(phase(asking, "tickets")["state"], "you")
        limited = approved().step("K-1-PT-1-1", ticket="PT-1")
        limited.add("step.limit", step="K-1-PT-1-1", reset="16:30", window="session")
        limited.add("step.finished", step="K-1-PT-1-1", ticket="PT-1", result="stopped", reason="limit")
        self.assertEqual(phase(limited, "tickets")["state"], "hold", "limit to nie decyzja użytkownika")
        done = approved()
        for ticket in ("PT-1", "PT-2", "PT-3"):
            events = done.step(f"K-1-{ticket}-1", ticket=ticket, result="done")
            merge(events, ticket)
        self.assertEqual(phase(done, "tickets")["state"], "done")

    def test_now_entry_ticket_phase_has_the_executor(self):
        item = phase(Events().step("K-1-PT-1-1", executor="Sonnet"), "tickets")
        self.assertEqual((item["state"], item["executor"]), ("now", "Sonnet"))

    def test_closing_runs_then_done(self):
        events = merge(approved().step("K-1-PT-1-1", ticket="PT-1", result="done"), "PT-1")
        self.assertEqual(phase(events, "closing")["state"], "todo")
        events.step("K-1-close-1", ticket="", kind="close", executor="Sonnet")
        item = phase(events, "closing")
        self.assertEqual((item["state"], item["executor"]), ("now", "Sonnet"))
        events.add("step.finished", step="K-1-close-1", ticket="", result="done")
        events.add("card.closed", summary="Gotowe")
        self.assertEqual(phase(events, "closing")["state"], "done")

    def test_main_merge_waits_for_the_user_after_closing_then_done(self):
        events = Events().step("K-1-PT-1-1", result="done")
        self.assertEqual(phase(events, "main-merge")["state"], "todo")
        events.add("card.closed", summary="Gotowe")
        item = phase(events, "main-merge")
        self.assertEqual((item["state"], item["executor"]), ("you", USER))
        events.add("card.main-merged")
        self.assertEqual(phase(events, "main-merge")["state"], "done")

    def test_plan_revision_after_approval_belongs_to_tickets(self):
        events = approved().step("K-1-PT-1-1", ticket="PT-1")
        events.step("K-1-plan-amend-1", ticket="", kind="plan-amend", executor="Opus", result="question",
                    question="Który wariant?")
        states = {p["id"]: p["state"] for p in view(events)["phases"]}
        self.assertEqual((states["plan-approval"], states["tickets"]), ("done", "you"))


class WaitingOnTest(unittest.TestCase):
    def tickets(self, events):
        return {t["id"]: t for t in view(events)["tickets"]}

    def test_several_reasons_at_once_user_beats_blocker_beats_limit(self):
        events = approved()
        # PT-2: bloker PT-1 niescalony, pytanie i limit naraz → użytkownik.
        events.step("K-1-PT-2-1", ticket="PT-2", executor="Opus", result="question", question="Naprawić?")
        events.add("step.waiting", ticket="PT-2", kind="review", provider="anthropic", cause="quota", until="16:30",
                   reason="czeka na limit Claude do 16:30")
        # PT-3: bloker PT-1 niescalony i limit → bloker.
        events.add("step.waiting", ticket="PT-3", kind="ticket", provider="anthropic", cause="slots", until="",
                   reason="limit kroków dostawcy anthropic: 3/3")
        tickets = self.tickets(events)
        self.assertEqual(tickets["PT-2"]["waitingOn"], {"kind": "user"})
        self.assertEqual(tickets["PT-3"]["waitingOn"], {"kind": "blocker", "ticket": "PT-1"})
        self.assertIsNone(tickets["PT-1"]["waitingOn"])
        merge(events.step("K-1-PT-1-1", ticket="PT-1", result="done"), "PT-1")
        tickets = self.tickets(events)
        self.assertEqual(tickets["PT-3"]["waitingOn"], {"kind": "limit", "until": ""}, "bez blokera zostaje limit")
        self.assertEqual(tickets["PT-2"]["waitingOn"], {"kind": "user"})
        self.assertIsNone(tickets["PT-1"]["waitingOn"], "scalony ticket na nic nie czeka")

    def test_limit_stop_gives_reset_time(self):
        events = approved().step("K-1-PT-1-1", ticket="PT-1")
        events.add("step.limit", step="K-1-PT-1-1", reset="16:30", window="session")
        events.add("step.finished", step="K-1-PT-1-1", ticket="PT-1", result="stopped", reason="limit")
        ticket = self.tickets(events)["PT-1"]
        self.assertEqual(ticket["waitingOn"], {"kind": "limit", "until": "16:30"})
        self.assertEqual((ticket["status"], ticket["statusLabel"]), ("limit", "czeka na limit do 16:30"))

    def test_old_waiting_event_without_cause_is_read_from_reason_and_pause_is_not_a_limit(self):
        events = merge(approved().step("K-1-PT-1-1", ticket="PT-1", result="done"), "PT-1")
        events.add("step.waiting", ticket="PT-2", kind="ticket", provider="anthropic",
                   reason="limit kroków dostawcy anthropic: 3/3")
        events.add("step.waiting", ticket="PT-3", kind="ticket", provider="anthropic", reason="karta wstrzymana",
                   cause="paused")
        tickets = self.tickets(events)
        self.assertEqual(tickets["PT-2"]["waitingOn"], {"kind": "limit", "until": ""})
        self.assertIsNone(tickets["PT-3"]["waitingOn"])

    def test_human_states_executor_and_reviewer(self):
        events = approved().step("K-1-PT-1-1", ticket="PT-1", result="done")
        events.step("K-1-PT-1-r1", ticket="PT-1", kind="review", executor="Opus")
        tickets = self.tickets(events)
        self.assertEqual([tickets[t]["statusLabel"] for t in ("PT-1", "PT-2", "PT-3")],
                         ["review", "czeka na PT-1", "czeka na PT-1"])
        self.assertEqual((tickets["PT-2"]["executor"], tickets["PT-2"]["reviewer"]), ("Opus", "Sonnet"))
        merge(events, "PT-1")
        events.step("K-1-PT-2-1", ticket="PT-2", executor="Opus")
        tickets = self.tickets(events)
        self.assertEqual([tickets[t]["statusLabel"] for t in ("PT-1", "PT-2", "PT-3")],
                         ["scalony", "pracuje", "w kolejce"])
        before = self.tickets(plan())
        self.assertEqual({(t["statusLabel"], t["waitingOn"]) for t in before.values()}, {("w planie", None)},
                         "przed zatwierdzeniem planu ticket jest „w planie” i na nic jeszcze nie czeka")


class BackgroundTest(unittest.TestCase):
    def test_now_in_background_lists_only_running_steps(self):
        events = approved().step("K-1-PT-1-1", ticket="PT-1", result="done")
        events.step("K-1-PT-1-r1", ticket="PT-1", kind="review", executor="Opus")
        running = view(events)["flow"]["running"]
        self.assertEqual([(s["id"], s["kindLabel"], s["executor"]) for s in running],
                         [("K-1-PT-1-r1", "review", "Opus")], "zakończone kroki (planowanie, ticket) nie trafiają")
        self.assertIn("canEnter", running[0])

    def test_outside_command_counts_as_work_not_waiting(self):
        # 04.11, K-37/PT-153: przy trwającym `make test` poza piaskownicą kafelek pokazywał „czeka”,
        # a „Teraz w tle” — „Nic nie pracuje teraz w tle”.
        events = approved().step("K-1-PT-1-1", ticket="PT-1", result="denied", outside=True, command="make test")
        events.add("step.decision", step="K-1-PT-1-1", ticket="PT-1", choice="run", command="make test")
        tickets = {t["id"]: t for t in view(events)["tickets"]}
        self.assertEqual(tickets["PT-1"]["statusLabel"], "polecenie w kolejce")
        events.add("outside.started", step="K-1-PT-1-1", command="make test")
        out = view(events)
        tickets = {t["id"]: t for t in out["tickets"]}
        self.assertEqual((tickets["PT-1"]["status"], tickets["PT-1"]["statusLabel"]),
                         ("running", "polecenie poza piaskownicą"))
        self.assertEqual([(s["id"], s["kindLabel"]) for s in out["flow"]["running"]],
                         [("K-1-PT-1-1", "polecenie poza piaskownicą")])
        events.add("outside.finished", step="K-1-PT-1-1", ok=True)
        events.step("K-1-PT-1-2", ticket="PT-1", parent="K-1-PT-1-1")
        self.assertEqual([s["id"] for s in view(events)["flow"]["running"]], ["K-1-PT-1-2"])

    def test_card_with_outside_command_of_another_ticket_stays_in_progress(self):
        # 04.11, K-46: ostatni krok to review APPROVE innego ticketu → kafelek trafiał do „Do sprawdzenia”,
        # choć inny ticket puszczał testy poza piaskownicą.
        events = approved().step("K-1-PT-2-1", ticket="PT-2", result="denied", outside=True, command="make test")
        events.add("step.decision", step="K-1-PT-2-1", ticket="PT-2", choice="run", command="make test")
        events.add("outside.started", step="K-1-PT-2-1", command="make test")
        events.step("K-1-PT-1-1", ticket="PT-1", result="done")
        events.step("K-1-PT-1-r1", ticket="PT-1", kind="review", executor="Opus", result="done")
        events.add("review.verdict", ticket="PT-1", round=1, verdict="APPROVE", blockers=0, majors=0, nits=0)
        merge(events, "PT-1")
        out = view(events)
        self.assertEqual(out["column"], "progress")
        self.assertIn("PT-2: polecenie poza piaskownicą", out["note"])

    def test_author_step_start_clears_stale_author_wait_of_the_ticket(self):
        # 05.11, K-37/PT-147: wpis „czeka na limit” kroku ticket został, gdy poprawki ruszyły na innym wykonawcy.
        events = approved()
        events.add("step.waiting", ticket="PT-1", kind="ticket", provider="anthropic",
                   reason="czeka na limit Claude do sobota 18:00", cause="quota", until="sobota 18:00")
        events.step("K-1-PT-1-2", ticket="PT-1", kind="fix", executor="Codex mocny")
        tickets = {t["id"]: t for t in view(events)["tickets"]}
        self.assertEqual(tickets["PT-1"]["status"], "fix")
        self.assertNotIn(("PT-1", "ticket"), events.state().waiting)

    def test_finish_counts_running_steps_and_unmerged_tickets(self):
        events = approved().step("K-1-PT-1-1", ticket="PT-1", result="done")
        events.step("K-1-PT-1-r1", ticket="PT-1", kind="review", executor="Opus")
        finish = view(events)["flow"]["finish"]
        self.assertEqual(finish["steps"], 1)
        self.assertGreaterEqual(finish["tickets"], 1, "ticket bez scalenia zostanie odrzucony przy zakończeniu karty")

    def test_headline_only_when_nothing_waits(self):
        running = Events().step("K-1-PT-1-1").step("K-1-PT-2-1", ticket="PT-2")
        self.assertEqual(view(running)["flow"]["headline"],
                         "2 kroki pracują w tle; następna decyzja będzie Twoja przy domknięciu karty, chyba że "
                         "któryś krok o coś zapyta.")
        asking = Events().step("K-1-PT-1-1", result="question", question="?")
        self.assertEqual(view(asking)["flow"]["headline"], "", "przy decyzjach „Twój ruch” pokazuje decyzje")
        blocked = approved()
        self.assertIn("blokery", view(blocked)["flow"]["headline"])


if __name__ == "__main__":
    unittest.main()
