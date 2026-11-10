"""Karta „Od razu” pokazuje swój ticket jako kafelek planu (PT-333, K-97).

Scenariusze pochodzą z dziennika usługi (`fold` + `derived`) dla wejścia „Od razu”; okno offscreen jak w
test_card_full_view.py. Kafelek jest tym samym TicketTile.qml co w kartach z planem, bez nagłówka „Fala 1”.
"""
import unittest

from .decisionfix import CARD, Q2, Q2_OPTIONS, derived, fold, full_card, full_d
from .test_card_full_view import STATE_PY
from .test_view import ProbeCase

TILES_PY = STATE_PY + r'''
def plan():
    return json.loads(js("(function () { const v = page.cardFullView; const all = " + ALL + "; "
        "const tiles = all(v, 'fullTicket').map(t => ({ id: t.ticketId, status: t.status, label: t.statusLabel, "
        "  title: t.item ? t.item.title : null, border: t.border.color.toString(), "
        "  people: t.item ? [t.item.executor || '', t.item.reviewer || ''] : null })); "
        "const empty = all(v, 'fullPlanEmpty')[0]; "
        "return JSON.stringify({ tiles: tiles, waves: all(v, 'fullWave').length, "
        "  empty: !!empty && empty.visible, cardTitle: v.card.title, "
        "  phases: all(v, 'fullPhase').map(p => [p.phaseId, p.phaseState]) }) })()"))
'''
# Karta z decyzją otwiera najpierw okno decyzji; pełną kartę otwiera z niego „Otwórz całą kartę”.
OPEN_PLAN = TILES_PY + ('click(card("K-8"))\n'
                        'if js("page.decisionOpen"):\n'
                        '    click(named("decisionFullCard"))\n'
                        'out["plan"] = plan()\n')


def now_journal(stage: str, numbered: bool = True) -> list[dict]:
    """Dziennik karty „Od razu” (K-8, ticket PT-7): etapy `queued`, `ticket`, `question`, `review`, `closed`."""
    items = []

    def add(event_type, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-03T10:{len(items):02d}:00+01:00",
                      "card": CARD, "type": event_type, **data})

    def step(name, kind, executor):
        add("step.started", step=name, ticket="PT-7", kind=kind, executor=executor, provider="anthropic",
            model=executor.lower(), attempt=0, parentStep="", stepDir=f"/wt/{name}.step", worktree="/wt/pt-7",
            branch="ticket/pt-7")

    add("card.started", entry="now", executor="Sonnet", program="claude", provider="anthropic")
    if numbered:
        add("result", action="reserve", key="PT-7", ok=True, ticket="PT-7", title="Tytuł ticketu")
    if stage == "queued":
        return items
    step("K-8-ticket-0", "ticket", "Sonnet")
    if stage == "ticket":
        return items
    if stage == "question":
        add("step.finished", step="K-8-ticket-0", ticket="PT-7", result="question", question=Q2,
            options=Q2_OPTIONS)
        return items
    add("step.finished", step="K-8-ticket-0", ticket="PT-7", result="done")
    step("K-8-review-1", "review", "Opus")
    if stage == "review":
        return items
    add("step.finished", step="K-8-review-1", ticket="PT-7", result="done")
    add("review.verdict", ticket="PT-7", round=1, step="K-8-review-1", verdict="APPROVE", blockers=0, majors=0,
        nits=0)
    add("result", action="merge", key="PT-7", ok=True)
    add("card.closed", step="K-8-close-2", summary="Gotowe")
    return items


class NowSnapshotTest(unittest.TestCase):
    """Dane z usługi: karta „Od razu” nie ma `derived.tickets`, a kafelek składa się z kroków przepływu."""

    def test_snapshot_has_no_tickets_and_steps_carry_the_executors(self):
        out = derived(fold(now_journal("review")), {"column": "todo"})
        self.assertEqual(out["tickets"], [])
        self.assertEqual(out["flow"]["ticket"], "PT-7")
        self.assertEqual([(s["kind"], s["executor"], s["result"]) for s in out["flow"]["steps"]],
                         [("ticket", "Sonnet", "done"), ("review", "Opus", "")])


class NowTileTest(ProbeCase):
    def plan(self, stage, numbered=True):
        out = self.probe(OPEN_PLAN, full_card(self.data, now_journal(stage, numbered)))
        return out["plan"]

    def test_running_ticket_step_shows_working_with_executor_only(self):
        plan = self.plan("ticket")
        self.assertEqual(len(plan["tiles"]), 1)
        tile = plan["tiles"][0]
        self.assertEqual((tile["id"], tile["status"], tile["label"]), ("PT-7", "running", "pracuje"))
        self.assertEqual(tile["title"], plan["cardTitle"], "tytuł karty")
        self.assertTrue(plan["cardTitle"])
        self.assertEqual(tile["people"], ["Sonnet", ""], "bez recenzenta, dopóki review nie ruszył")
        self.assertEqual(plan["waves"], 0, "karta „Od razu” nie ma fal ani nagłówka „Fala 1”")
        self.assertFalse(plan["empty"])

    def test_review_started_shows_review_and_executor_to_reviewer(self):
        tile = self.plan("review")["tiles"][0]
        self.assertEqual((tile["status"], tile["label"]), ("review", "review"))
        self.assertEqual(tile["people"], ["Sonnet", "Opus"], "recenzent z kroku review")

    def test_before_the_first_step_the_ticket_is_queued(self):
        tile = self.plan("queued")["tiles"][0]
        self.assertEqual((tile["id"], tile["status"], tile["label"]), ("PT-7", "queued", "w kolejce"))

    def test_decision_for_this_ticket_shows_waiting_for_you_with_peach_border(self):
        tile = self.plan("question")["tiles"][0]
        self.assertEqual((tile["status"], tile["label"]), ("you", "czeka na Ciebie"))
        other = self.plan("ticket")["tiles"][0]
        self.assertNotEqual(tile["border"], other["border"], "obwódka jak dziś przy „czeka na Ciebie”")

    def test_closed_card_shows_merged_to_the_card_branch_and_phases_agree(self):
        plan = self.plan("closed")
        tile = plan["tiles"][0]
        self.assertEqual((tile["status"], tile["label"]), ("merged", "scalony do gałęzi karty"))
        self.assertEqual(dict(plan["phases"])["tickets"], "done", "pasek faz zgodny z kafelkiem")

    def test_merged_never_shows_while_the_tickets_phase_is_running(self):
        for stage in ("queued", "ticket", "review", "question"):
            plan = self.plan(stage)
            self.assertNotEqual(plan["tiles"][0]["status"], "merged", stage)
            self.assertNotEqual(dict(plan["phases"])["tickets"], "done", stage)

    def test_card_with_a_plan_keeps_its_waves_and_has_no_extra_tile(self):
        plan = self.probe(OPEN_PLAN, full_d(self.data))["plan"]
        self.assertEqual(plan["waves"], 4)
        self.assertEqual(len(plan["tiles"]), 9, "tyle co w planie scenariusza D, bez kafelka „Od razu”")
        self.assertFalse(plan["empty"])

    def test_card_without_a_ticket_number_keeps_the_no_plan_sentence(self):
        plan = self.plan("queued", numbered=False)
        self.assertEqual(plan["tiles"], [])
        self.assertTrue(plan["empty"], "karta bez numeru ticketu: „Karta bez planu…”")


if __name__ == "__main__":
    unittest.main()
