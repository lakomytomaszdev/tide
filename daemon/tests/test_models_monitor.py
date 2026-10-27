"""ModelsMonitor w pętli Qt bez D-Bus: odczyt w wątku roboczym, Refresh() w trakcie odczytu (jeszcze jeden
odczyt zaraz po nim), Changed tylko przy zmianie, błąd odczytu i nieczytelne ustawienia przy starcie.

Odczyt stanu jest atrapą (funkcja w teście) — żadnej sieci.
"""
import threading
import unittest
from unittest import mock

from .privatebus import app, wait_until

try:
    from tide_daemon import models_monitor
    from tide_daemon.models import config as models_config
except ImportError:  # pragma: no cover — bez PyQt6
    models_monitor = None


def state(version):
    return {"checked": True, "endpoints": {"local": {"online": True, "version": version, "error": ""}},
            "config": {"error": ""}, "roles": {}}


@unittest.skipIf(models_monitor is None, "brak PyQt6")
class ModelsMonitorTest(unittest.TestCase):
    def setUp(self):
        app()
        self.calls = []
        self.gate = threading.Event()
        self.version = "0.40.11"

    def read(self):
        version = self.version           # stan z chwili rozpoczęcia odczytu
        self.calls.append(version)
        self.gate.wait(5)
        return state(version)

    def monitor(self, read=None):
        monitor = models_monitor.ModelsMonitor(read=read or self.read, poll_ms=600_000)
        self.addCleanup(monitor.stop)
        changes = []
        monitor.changed.connect(lambda: changes.append(monitor.status_json()))
        return monitor, changes

    def test_refresh_during_read_reads_again_right_after(self):
        monitor, changes = self.monitor()
        monitor.poll()
        self.assertTrue(wait_until(lambda: self.calls == ["0.40.11"]))
        self.version = "0.41.0"          # stan zmienił się w trakcie pierwszego odczytu
        monitor.refresh()                # w trakcie odczytu — nie równolegle, tylko zaraz po nim
        monitor.refresh()                # drugi raz nie dokłada trzeciego odczytu
        self.assertEqual(len(self.calls), 1)
        self.gate.set()
        self.assertTrue(wait_until(lambda: len(changes) == 2, timeout=5), changes)
        wait_until(lambda: False, timeout=0.3)
        self.assertEqual(self.calls, ["0.40.11", "0.41.0"])
        self.assertIn('"0.41.0"', monitor.status_json())

    def test_changed_only_when_json_changes_and_read_error_keeps_last_state(self):
        self.gate.set()
        failing = {"on": False}

        def read():
            if failing["on"]:
                raise RuntimeError("nieoczekiwany")
            return self.read()

        monitor, changes = self.monitor(read)
        monitor.poll()
        self.assertTrue(wait_until(lambda: len(changes) == 1))
        monitor.poll()
        self.assertTrue(wait_until(lambda: len(self.calls) == 2))
        failing["on"] = True
        monitor.poll()
        wait_until(lambda: False, timeout=0.3)
        self.assertEqual(len(changes), 1, "ten sam stan i błąd odczytu — bez Changed")
        self.assertIn('"0.40.11"', monitor.status_json())
        failing["on"] = False
        monitor.poll()                   # po błędzie odpytywanie działa dalej
        self.assertTrue(wait_until(lambda: len(self.calls) == 3))

    def test_unreadable_settings_at_start_do_not_stop_the_service(self):
        with mock.patch.object(models_config, "load", side_effect=RuntimeError("boom")):
            monitor, _changes = self.monitor()
        text = monitor.status_json()
        self.assertIn('"checked": false', text)
        self.assertIn("RuntimeError", text)


if __name__ == "__main__":
    unittest.main()
