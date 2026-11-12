"""K-39 #4 (PT-374): `cardflow/queueview` — pole `queue` migawki Tablicy z samych obiektów `FlowState`.

Testy nie mają katalogu dzienników: stany powstają z `fold` listy zdarzeń, a `queueview.build` dostaje tylko je,
ustawienia i dwa wywołania silnika (klasa polecenia, projekt karty). Moduł nie ma jak czytać dziennika ani plików
kroków, więc zero odczytów w ścieżce migawki wynika z jego wejścia.
"""
import unittest

from tide_daemon.cardflow import heavyqueue, mergecheck, queueview
from tide_daemon.cardflow.state import fold

TS_RUN = "2026-11-12T10:00:00+01:00"
TS_MERGE = "2026-11-12T10:02:00+01:00"
TS_ASK = "2026-11-12T10:05:00+01:00"
TS_LATER = "2026-11-12T10:06:00+01:00"
HEAVY = "make test-changed BASE=1"
LIGHT = "make lint"
SHA = "a" * 40


PROJECTS = {"K-1": "tide", "K-2": "other"}


def journal(card, events):
    out = []
    for event in [{"type": "card.started", "entry": "now", "executor": "Sonnet", "program": "claude",
                   "provider": "anthropic", "project": PROJECTS.get(card, "")}, *events]:
        event = dict(event)
        out.append({"v": 1, "seq": len(out) + 1, "ts": event.pop("ts", TS_RUN), "card": card, **event})
    return out


def started(step, ticket):
    return {"type": "step.started", "step": step, "ticket": ticket, "kind": "ticket", "executor": "Sonnet",
            "provider": "anthropic", "model": "sonnet", "attempt": 0, "parentStep": "", "stepDir": f"/wt/{ticket}.step",
            "worktree": f"/wt/{ticket}", "branch": f"ticket/{ticket.lower()}"}


def asks(step, ticket, command):
    return {"type": "step.finished", "step": step, "ticket": ticket, "result": "denied", "outside": True,
            "command": command, "canAllow": True}


def approved(step, ticket, ts=TS_ASK):
    return {"type": "step.decision", "step": step, "ticket": ticket, "choice": "run", "ts": ts}


def launched(step, ticket, command, ts=TS_RUN):
    return {"type": "outside.started", "step": step, "ticket": ticket, "command": command, "ts": ts}


def outside_events(step, ticket, command, *, run=False, ask_ts=TS_ASK, since=TS_RUN):
    events = [started(step, ticket), asks(step, ticket, command), approved(step, ticket, ask_ts)]
    return events + ([launched(step, ticket, command, since)] if run else [])


def merge_check_events(ticket, *, run=False, since=TS_RUN, ask_ts=TS_ASK):
    check = f"{ticket}:1"
    step = mergecheck.step_name(check)
    events = [{"type": mergecheck.ARMED},
              {"type": mergecheck.REQUESTED, "ticket": ticket, "check": check, "command": HEAVY, "after": SHA,
               "start": SHA, "before": SHA},
              {"type": mergecheck.STEP, "step": step, "ticket": ticket, "kind": mergecheck.KIND, "check": check,
               "command": HEAVY, "after": SHA, "worktree": "/wt/card", "stepDir": f"/wt/card.{step}.step"},
              approved(step, ticket, ask_ts)]
    return step, events + ([launched(step, ticket, HEAVY, since)] if run else [])


def build(states, settings=None, limits=(6, 7)):
    return queueview.build(states, settings or {},
                           heavy_of=lambda card, name, step, command: command.startswith("make test"),
                           outside_limits=limits)


def two_cards():
    """K-1: trwające ciężkie polecenie (PT-1), trwająca próba po scaleniu (PT-3) i czekająca zgoda (PT-2);
    K-2: lekkie trwające (PT-5) i lekkie czekające (PT-4)."""
    merge_step, merge_events = merge_check_events("PT-3", run=True, since=TS_MERGE)
    first = journal("K-1", [*outside_events("K-1-PT-1", "PT-1", HEAVY, run=True),
                            *merge_events,
                            *outside_events("K-1-PT-2", "PT-2", "make test-changed BASE=2")])
    second = journal("K-2", [*outside_events("K-2-PT-5", "PT-5", LIGHT, run=True),
                             *outside_events("K-2-PT-4", "PT-4", LIGHT)])
    return merge_step, {"K-1": fold(first), "K-2": fold(second)}


class QueueViewTest(unittest.TestCase):
    def test_heavy_part_lists_running_outside_and_merge_check_and_waiting_with_reason(self):
        merge_step, states = two_cards()
        heavy = build(states, {"heavyLimit": 2})["heavy"]
        self.assertEqual(heavy["limit"], 2)
        self.assertEqual([(e["kind"], e["step"], e["card"], e["ticket"]) for e in heavy["running"]],
                         [("outside", "K-1-PT-1", "K-1", "PT-1"), ("merge-check", merge_step, "K-1", "PT-3")])
        [waiting] = heavy["waiting"]
        self.assertEqual((waiting["step"], waiting["card"], waiting["ticket"], waiting["kind"]),
                         ("K-1-PT-2", "K-1", "PT-2", "outside"))
        self.assertEqual(waiting["reason"], "czeka na wolne miejsce: ciężkie polecenia 2 z 2 zajęte")

    def test_running_and_waiting_entries_come_from_flow_state_only(self):
        merge_step, states = two_cards()
        heavy = build(states, {"heavyLimit": 2})["heavy"]
        by_step = {e["step"]: e for e in heavy["running"]}
        self.assertEqual((by_step["K-1-PT-1"]["command"], by_step["K-1-PT-1"]["since"]), (HEAVY, TS_RUN))
        self.assertEqual((by_step[merge_step]["command"], by_step[merge_step]["since"]), (HEAVY, TS_MERGE))
        [waiting] = heavy["waiting"]
        self.assertEqual((waiting["command"], waiting["since"]), ("make test-changed BASE=2", TS_ASK))

    def test_light_commands_go_to_outside_with_limits_and_occupied_counts(self):
        _, states = two_cards()
        outside = build(states, {"heavyLimit": 2}, limits=(6, 7))["outside"]
        self.assertEqual((outside["perProject"], outside["total"]), (6, 7))
        self.assertEqual([(e["step"], e["project"]) for e in outside["running"]], [("K-2-PT-5", "other")])
        self.assertEqual([(e["step"], e["project"]) for e in outside["waiting"]], [("K-2-PT-4", "other")])
        # Do limitów liczą się wszystkie trwające, także ciężkie: dwa w projekcie tide i jedno w other.
        self.assertEqual(outside["occupied"], {"total": 3, "projects": {"tide": 2, "other": 1}})

    def test_limits_part_has_heavy_limit_and_provider_slots(self):
        queue = build({}, {"heavyLimit": 4, "providerSlots": {"anthropic": 2, "bad": 0, "x": True}})
        self.assertEqual(queue["limits"], {"heavyLimit": 4, "providerSlots": {"anthropic": 2},
                                           "providerSlotsDefault": 3})
        self.assertEqual(build({}, {"heavyLimit": 99})["limits"]["heavyLimit"], heavyqueue.DEFAULT_LIMIT)

    def test_free_slot_gives_the_waiting_command_the_next_check_sentence(self):
        _, states = two_cards()
        [waiting] = build(states, {"heavyLimit": 3})["heavy"]["waiting"]
        self.assertEqual(waiting["reason"], queueview.WAITING_NEXT)

    def test_waiting_follow_the_start_order_of_heavyqueue(self):
        late = journal("K-1", outside_events("K-1-PT-9", "PT-9", HEAVY, ask_ts=TS_LATER))
        early = journal("K-2", outside_events("K-2-PT-8", "PT-8", HEAVY, ask_ts=TS_ASK))
        queue = build({"K-1": fold(late), "K-2": fold(early)}, {"heavyLimit": 1})
        self.assertEqual([e["step"] for e in queue["heavy"]["waiting"]], ["K-2-PT-8", "K-1-PT-9"])

    def test_merge_check_waiting_for_a_place_is_a_heavy_waiting_entry(self):
        step, events = merge_check_events("PT-6")
        queue = build({"K-1": fold(journal("K-1", events))}, {"heavyLimit": 1})
        [waiting] = queue["heavy"]["waiting"]
        self.assertEqual((waiting["step"], waiting["kind"], waiting["ticket"], waiting["command"]),
                         (step, "merge-check", "PT-6", HEAVY))

    def test_paused_card_holds_no_place_in_the_queue(self):
        state = fold(journal("K-1", [*outside_events("K-1-PT-2", "PT-2", HEAVY), {"type": "card.paused"}]))
        self.assertTrue(state.paused)
        self.assertEqual(build({"K-1": state})["heavy"]["waiting"], [])

    def test_empty_states_give_an_empty_queue(self):
        queue = build({})
        self.assertEqual(queue["heavy"], {"limit": heavyqueue.DEFAULT_LIMIT, "running": [], "waiting": []})
        self.assertEqual(queue["outside"]["occupied"], {"total": 0, "projects": {}})
        self.assertNotIn("load", queue["heavy"], "obciążenie maszyny dochodzi w #6")
        self.assertNotIn("steps", queue, "część steps dochodzi w #10")


if __name__ == "__main__":
    unittest.main()
