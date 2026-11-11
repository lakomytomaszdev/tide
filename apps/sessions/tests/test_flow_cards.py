"""Widok Sesje czyta hierarchię przepływu z jednej migawki tablicy."""
import json
import time
import unittest

from PyQt6.QtCore import QCoreApplication

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


CARD = {"id": "K-28", "title": "Przepływ karty", "derived": {"flow": {"steps": [], "timeline": []}}}
PREPARING = json.dumps({"version": 0, "readState": "preparing", "exists": True, "cards": [], "projects": []})
READY = json.dumps({"version": 3, "readState": "ready", "exists": True, "cards": [CARD], "projects": []})


class PreparingSnapshotTest(unittest.TestCase):
    """PT-385 (N4): Snapshot z `readState: "preparing"` (usługa po restarcie) nie czyści listy „Karty w przepływie”;
    bez poprzedniej migawki widok mówi „wczytywanie” i ponawia odczyt najpóźniej po 2 s."""

    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.backend = Backend(None)
        self.backend.bus = object()          # odczyt przez magistralę — atrapa `_call` zapisuje wywołania
        self.calls = []
        self.backend._call = lambda path, iface, member, args, then, timeout=15_000: self.calls.append(member)
        self.changes = []
        self.backend.flowCardsChanged.connect(lambda: self.changes.append(self.backend.flowCardsLoading))

    def wait_for_retry(self, limit=3.0):
        start = time.monotonic()
        while not self.calls and time.monotonic() - start < limit:
            self.app.processEvents()
            time.sleep(0.01)
        return time.monotonic() - start

    def test_first_start_shows_loading_and_retries_within_two_seconds(self):
        self.backend._got_flow_cards(True, PREPARING)
        self.assertTrue(self.backend.flowCardsLoading)
        self.assertEqual(json.loads(self.backend.flowCardsJson), [])
        self.assertLessEqual(self.backend._flow_retry.interval(), 2000)
        elapsed = self.wait_for_retry()
        self.assertEqual(self.calls, ["Snapshot"], "brak ponownego odczytu po „preparing”")
        self.assertLessEqual(elapsed, 2.5)
        self.backend._got_flow_cards(True, READY)
        self.assertFalse(self.backend.flowCardsLoading)
        self.assertEqual(json.loads(self.backend.flowCardsJson), [CARD])
        self.assertEqual(self.changes, [True, False])

    def test_restart_keeps_the_previous_cards(self):
        self.backend._got_flow_cards(True, READY)
        self.changes.clear()
        self.backend._got_flow_cards(True, PREPARING)
        self.assertEqual(json.loads(self.backend.flowCardsJson), [CARD], "„preparing” to nie pusta Tablica")
        self.assertFalse(self.backend.flowCardsLoading)
        self.assertEqual(self.changes, [], "lista nie mignęła")
        self.wait_for_retry()
        self.assertEqual(self.calls, ["Snapshot"], "ponowny odczyt także przy zachowanej migawce")


if __name__ == "__main__":
    unittest.main()
