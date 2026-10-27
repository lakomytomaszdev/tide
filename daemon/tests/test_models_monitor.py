"""ModelsMonitor w pętli Qt bez D-Bus: odczyt w wątku roboczym, Refresh() w trakcie odczytu (jeszcze jeden
odczyt zaraz po nim), Changed tylko przy zmianie, błąd odczytu i nieczytelne ustawienia przy starcie,
pole `queue` (kolejka modeli z wątków roboczych) i Changed z jej powodu najwyżej raz na odstęp.

Odczyt stanu jest atrapą (funkcja w teście) — żadnej sieci.
"""
import threading
import unittest
from unittest import mock

from .privatebus import app, wait_until

try:
    from tide_daemon import models_monitor
    from tide_daemon.models import config as models_config
    from tide_daemon.models import queue as models_queue
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

    def monitor(self, read=None, **kw):
        monitor = models_monitor.ModelsMonitor(read=read or self.read, poll_ms=600_000, **kw)
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

    def test_queue_in_status_and_changed_throttled(self):
        import json
        import time
        self.gate.set()
        queue = models_queue.Queue()
        monitor, changes = self.monitor(queue=queue, queue_gap_ms=400)
        monitor.poll()
        self.assertTrue(wait_until(lambda: len(changes) == 1))
        self.assertEqual(json.loads(monitor.status_json())["queue"], {"local": {"running": None, "waiting": []}},
                         "każdy punkt z konfiguracji, bezczynny")

        gate = threading.Event()
        workers = [threading.Thread(target=lambda: queue.run(lambda: gate.wait(5), endpoint="local", role="summary",
                                                             model="qwen3:8b")),
                   threading.Thread(target=lambda: queue.run(lambda: gate.wait(5), endpoint="local", role="describe",
                                                             model="qwen3:8b"))]
        workers[0].start()
        self.assertTrue(wait_until(lambda: queue.snapshot().get("local", {}).get("running")))
        workers[1].start()
        self.assertTrue(wait_until(lambda: queue.snapshot().get("local", {}).get("waiting")))
        started = time.monotonic()
        def busy():
            local = json.loads(monitor.status_json())["queue"]["local"]
            return local if local["running"] and local["waiting"] else None

        state = wait_until(busy, timeout=3)
        self.assertEqual(state["running"]["role"], "summary")
        self.assertEqual([w["role"] for w in state["waiting"]], ["describe"])
        self.assertEqual(set(state["waiting"][0]), {"endpoint", "role", "model", "since"}, "bez treści poleceń")
        gate.set()
        for worker in workers:
            worker.join(5)
        self.assertTrue(wait_until(lambda: json.loads(monitor.status_json())["queue"]["local"] ==
                                   {"running": None, "waiting": []}, timeout=3))
        elapsed = time.monotonic() - started
        # Sześć zmian kolejki (dwa dołączenia, dwa starty, dwa końce) w krótkim czasie — Changed najwyżej
        # raz na 400 ms (plus pierwsza od razu), a końcowy stan zawsze dochodzi.
        queue_changes = len(changes) - 1
        self.assertGreaterEqual(queue_changes, 2)
        self.assertLessEqual(queue_changes, 2 + int(elapsed / 0.4))
        self.assertLess(queue_changes, 6)

    def test_queue_by_server_shown_under_every_endpoint_name(self):
        import json
        url = "http://127.0.0.1:11434"

        def read():
            return {"checked": True, "config": {"error": ""}, "roles": {},
                    "endpoints": {"local": {"url": url, "online": True, "error": ""},
                                  "same": {"url": url + "/", "online": True, "error": ""},
                                  "mac": {"url": "http://mac-studio:11434", "online": False, "error": ""}}}

        queue = models_queue.Queue()
        monitor, changes = self.monitor(read, queue=queue, queue_gap_ms=0)
        monitor.poll()
        self.assertTrue(wait_until(lambda: changes))
        gate = threading.Event()
        worker = threading.Thread(target=lambda: queue.run(lambda: gate.wait(5), endpoint="same",
                                                           lane=models_queue.lane_of(url), role="describe",
                                                           model="qwen3:8b"))
        worker.start()
        try:
            state = wait_until(lambda: (json.loads(monitor.status_json())["queue"]["local"]["running"] and
                                        json.loads(monitor.status_json())["queue"]), timeout=3)
            self.assertEqual(state["local"], state["same"], "dwa punkty na jednym serwerze — ten sam stan toru")
            self.assertEqual(state["local"]["running"]["endpoint"], "same")
            self.assertEqual(state["mac"], {"running": None, "waiting": []})
        finally:
            gate.set()
            worker.join(5)

    def test_stop_detaches_from_the_queue(self):
        queue = models_queue.Queue()
        monitor, _changes = self.monitor(queue=queue)
        monitor.stop()
        self.assertEqual(queue._listeners, [])


if __name__ == "__main__":
    unittest.main()
