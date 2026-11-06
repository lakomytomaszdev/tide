"""Powtórzenie kolejności z pełnych testów: ręczny powrót do todo wyprzedza wynik domknięcia."""
import unittest

from tide_daemon.cardflow.state import derived, fold


class CloseReworkTest(unittest.TestCase):
    def events(self, kind="close", *, late_closed=True):
        events = [
            {"type": "card.started", "entry": "now", "executor": "Sonnet", "check": "auto"},
            {"type": "step.started", "step": "K-1-close", "kind": kind, "ticket": "" if kind == "close" else "PT-1"},
            {"type": "card.moved", "from": "review", "to": "todo", "moveId": "rework"},
            {"type": "step.finished", "step": "K-1-close", "result": "done"},
        ]
        if late_closed:
            events.append({"type": "card.closed", "step": "K-1-close", "summary": "Gotowe"})
        return [{"v": 1, "seq": i + 1, "card": "K-1", "ts": "2026-11-06T06:14:58+01:00", **event}
                for i, event in enumerate(events)]

    def test_finishing_an_older_closing_step_cannot_undo_manual_rework(self):
        st = fold(self.events(late_closed=False))
        self.assertEqual(st.moved_to, "todo")
        self.assertFalse(st.active())
        self.assertIsNone(derived(st, {"id": "K-1", "column": "todo"}))

    def test_late_card_closed_from_captured_history_cannot_restore_review(self):
        st = fold(self.events())
        self.assertEqual(st.moved_to, "todo")
        self.assertIsNone(st.closed)
        self.assertIsNone(derived(st, {"id": "K-1", "column": "todo"}))

    def test_new_ticket_result_still_clears_the_previous_move(self):
        self.assertEqual(fold(self.events(kind="ticket", late_closed=False)).moved_to, "")

    def test_closing_without_a_manual_move_still_publishes_summary(self):
        events = [e for e in self.events() if e["type"] != "card.moved"]
        self.assertEqual(fold(events).closed["summary"], "Gotowe")
