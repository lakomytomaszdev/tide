"""PT-67: publiczne /Board EnterStep zatrzymuje krok przed otwarciem Konsoli."""
import os

from .privatebus import requires_dbus, wait_until
from .test_cardflow_dbus import FlowCase


@requires_dbus
class EnterStepBusTest(FlowCase):
    def test_enter_running_step_stops_background_before_console(self):
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: czekaj")
        self.assertTrue(self.start(card_id)["ok"])
        started = self.of(card_id, "step.started")[0]
        self.assertTrue(wait_until(lambda: os.path.isfile(os.path.join(started["stepDir"], "out", "fake-call.json")),
                                   timeout=5))
        reply = self.call("EnterStep", card_id, started["step"])
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["mode"], "window")  # atrapa Konsoli bez profilu istniejącego okna
        self.assertTrue(wait_until(lambda: self.of(card_id, "step.entered"), timeout=5))
        entered = self.of(card_id, "step.entered")[-1]
        self.assertEqual(entered["step"], started["step"])
        self.assertEqual(self.call("EnterStep", card_id, started["step"])["error"], "already-entered")
        self.assertTrue(wait_until(lambda: os.path.isfile(self.calls), timeout=5))
        with open(self.calls, encoding="utf-8") as fh:
            self.assertIn("konsole", fh.read())
