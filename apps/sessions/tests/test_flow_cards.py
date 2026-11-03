"""Widok Sesje czyta hierarchię przepływu z jednej migawki tablicy."""
import json
import unittest

from apps.sessions.backend import Backend, flow_cards


class FlowCardsTest(unittest.TestCase):
    def test_only_flow_cards_are_shown_with_tickets_steps_and_timeline(self):
        card = {"id": "K-28", "title": "Przepływ karty", "derived": {
            "tickets": [{"id": "PT-1", "state": "w-toku"}],
            "flow": {"steps": [{"id": "K-28-PT-1-1", "result": ""}],
                     "timeline": [{"type": "step.started", "ticket": "PT-1"}]}}}
        snapshot = {"cards": [card, {"id": "K-29", "derived": {"link": "session"}},
                              {"id": "K-30", "derived": None}]}
        self.assertEqual(flow_cards(snapshot), [card])
        self.assertEqual(flow_cards(None), [])
        backend = Backend(None, {"boardSnapshot": snapshot})
        self.assertEqual(json.loads(backend.flowCardsJson), [card])


if __name__ == "__main__":
    unittest.main()
