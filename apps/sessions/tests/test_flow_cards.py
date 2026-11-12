"""Widok Sesje czyta hierarchię przepływu z jednej migawki tablicy."""
import inspect
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


CARD = {"id": "K-28", "title": "Przepływ karty", "derived": {"flow": {"steps": [], "timeline": []}}}
PREPARING = json.dumps({"version": 0, "readState": "preparing", "exists": True, "cards": [], "projects": []})
READY = json.dumps({"version": 3, "readState": "ready", "exists": True, "cards": [CARD], "projects": []})


class PreparingSnapshotTest(unittest.TestCase):
    """PT-385 (N4): Snapshot z `readState: "preparing"` (usługa po restarcie) nie czyści listy „Karty w przepływie”;
    bez poprzedniej migawki widok mówi „wczytywanie” do najbliższego /Board Changed (usługa wysyła go przy pierwszej
    publikacji). Test na sygnał, nie na czas."""

    def setUp(self):
        self.backend = Backend(None)
        self.backend.bus = object()          # odczyt przez magistralę — atrapa `_call` odpowiada od razu
        self.served = PREPARING              # co usługa teraz podaje w Snapshot()
        self.calls = []

        def call(path, iface, member, args, then, timeout=15_000):
            self.calls.append(member)
            then(True, self.served)
        self.backend._call = call
        self.changes = []
        self.backend.flowCardsChanged.connect(lambda: self.changes.append(self.backend.flowCardsLoading))

    def test_backend_listens_to_board_changed(self):
        with open(inspect.getsourcefile(Backend), encoding="utf-8") as fh:
            self.assertIn('bus.connect(SERVICE, BOARD_PATH, BOARD_IFACE, "Changed", self._board_changed)', fh.read())

    def test_first_start_shows_loading_until_board_changed(self):
        self.backend._load_flow_cards()
        self.assertTrue(self.backend.flowCardsLoading)
        self.assertEqual(json.loads(self.backend.flowCardsJson), [])
        self.served = READY                  # usługa opublikowała Tablicę…
        self.assertTrue(self.backend.flowCardsLoading, "bez sygnału — dalej wczytywanie")
        self.backend._board_changed()        # …i wysłała /Board Changed
        self.assertFalse(self.backend.flowCardsLoading)
        self.assertEqual(json.loads(self.backend.flowCardsJson), [CARD])
        self.assertEqual(self.changes, [True, False])
        self.assertEqual(self.calls, ["Snapshot", "Snapshot"], "bez dodatkowych odczytów")

    def test_restart_keeps_the_previous_cards(self):
        self.served = READY
        self.backend._load_flow_cards()
        self.changes.clear()
        self.served = PREPARING
        self.backend._board_changed()
        self.assertEqual(json.loads(self.backend.flowCardsJson), [CARD], "„preparing” to nie pusta Tablica")
        self.assertFalse(self.backend.flowCardsLoading)
        self.assertEqual(self.changes, [], "lista nie mignęła")


if __name__ == "__main__":
    unittest.main()
