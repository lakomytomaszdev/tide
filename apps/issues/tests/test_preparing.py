"""PT-385 (N4): okno Sprawy czyta projekty z /Board Snapshot(). Odpowiedź z `readState: "preparing"` (usługa po
restarcie jeszcze nie opublikowała Tablicy, `projects: []`) nie czyści listy projektów i nie daje ostrzeżenia
„Nieznany lub usunięty projekt”; bez poprzedniej migawki stan mówi „wczytywanie”, a odczyt jest ponawiany
najpóźniej po 2 s (reload co 5 s bywa wstrzymany)."""
import json
import time
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
        self.calls = []
        self.backend._call = lambda method, args, callback, path=None, interface=None: self.calls.append(method)

    def state(self):
        return json.loads(self.backend.stateJson)

    def wait_for_retry(self, limit=3.0):
        start = time.monotonic()
        while not self.calls and time.monotonic() - start < limit:
            self.app.processEvents()
            time.sleep(0.01)
        return time.monotonic() - start

    def test_first_start_shows_loading_and_retries_within_two_seconds(self):
        self.backend._got_projects(PREPARING, b"")
        self.assertTrue(self.state()["projectsLoading"])
        self.assertEqual(self.state()["projects"], [])
        self.assertLessEqual(self.backend._projects_retry.interval(), 2000)
        elapsed = self.wait_for_retry()
        self.assertEqual(self.calls, ["Snapshot"], "brak ponownego odczytu po „preparing”")
        self.assertLessEqual(elapsed, 2.5)
        self.backend._got_projects(READY, b"")
        self.assertEqual((self.state()["projectsLoading"], self.state()["projects"]), (False, PROJECTS))

    def test_restart_keeps_the_previous_projects(self):
        self.backend._got_projects(READY, b"")
        self.backend._got_projects(PREPARING, b"")
        self.assertEqual(self.state()["projects"], PROJECTS, "„preparing” to nie brak projektów")
        self.assertFalse(self.state()["projectsLoading"])
        self.wait_for_retry()
        self.assertEqual(self.calls, ["Snapshot"])


if __name__ == "__main__":
    unittest.main()
