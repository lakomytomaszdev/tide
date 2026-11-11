"""PT-385 (N4): okno Sprawy czyta projekty z /Board Snapshot(). Odpowiedź z `readState: "preparing"` (usługa po
restarcie jeszcze nie opublikowała Tablicy, `projects: []`) nie czyści listy projektów i nie daje ostrzeżenia
„Nieznany lub usunięty projekt”; bez poprzedniej migawki stan mówi „wczytywanie” do najbliższego /Board Changed
(usługa wysyła go przy pierwszej publikacji). Test na sygnał, nie na czas."""
import inspect
import json
import unittest

from PyQt6.QtCore import QCoreApplication

from apps.issues import backend as module

PROJECTS = [{"slug": "plasma-tide", "name": "Tide"}]
PREPARING = {"version": 0, "readState": "preparing", "exists": True, "cards": [], "projects": []}
READY = {"version": 4, "readState": "ready", "exists": True, "cards": [], "projects": PROJECTS}


class PreparingProjectsTest(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.backend = module.Backend(None, {})
        self.served = PREPARING              # co usługa teraz podaje w Snapshot()
        self.calls = []

        def call(method, args, callback, path=None, interface=None):
            self.calls.append((method, path))
            callback(self.served, b"")
        self.backend._call = call

    def state(self):
        return json.loads(self.backend.stateJson)

    def test_backend_listens_to_board_changed(self):
        with open(inspect.getsourcefile(module), encoding="utf-8") as fh:
            self.assertIn('bus.connect(SERVICE, BOARD_PATH, BOARD_IFACE, "Changed", self._board_changed)', fh.read())

    def test_first_start_shows_loading_until_board_changed(self):
        self.backend._load_projects()
        self.assertTrue(self.state()["projectsLoading"])
        self.assertEqual(self.state()["projects"], [])
        self.served = READY                  # usługa opublikowała Tablicę…
        self.assertTrue(self.state()["projectsLoading"], "bez sygnału — dalej wczytywanie")
        self.backend._board_changed()        # …i wysłała /Board Changed
        self.assertEqual((self.state()["projectsLoading"], self.state()["projects"]), (False, PROJECTS))
        self.assertEqual(self.calls, [("Snapshot", "/Board")] * 2)
        self.backend._board_changed()
        self.assertEqual(len(self.calls), 2, "po gotowej migawce Changed nie dokłada odczytów (reload co 5 s)")

    def test_restart_keeps_the_previous_projects(self):
        self.served = READY
        self.backend._load_projects()
        self.served = PREPARING
        self.backend._load_projects()
        self.assertEqual(self.state()["projects"], PROJECTS, "„preparing” to nie brak projektów")
        self.assertFalse(self.state()["projectsLoading"])


if __name__ == "__main__":
    unittest.main()
