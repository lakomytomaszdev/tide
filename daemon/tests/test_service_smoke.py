"""PT-258: mały kontrakt procesu /Board, zawsze przy zmianie usługi."""
from .test_cardflow_dbus import FlowCase


class ServiceSmokeTest(FlowCase):
    def test_background_ticket_reaches_review_through_public_board(self):
        card = self.card()
        reply = self.start(card)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["ticket"], "PT-1")
        shown = self.wait_column(card, "review")
        self.assertTrue(shown["derived"]["flow"])
        self.assertEqual([e["kind"] for e in self.of(card, "step.started")],
                         ["ticket", "review", "close"])
        self.assertEqual([e["result"] for e in self.of(card, "step.finished")],
                         ["done", "done", "done"])
