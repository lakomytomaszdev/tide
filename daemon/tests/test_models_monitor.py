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


@unittest.skipIf(models_monitor is None, "brak PyQt6")
class ModelsMonitorWritesTest(unittest.TestCase):
    """Zapis ustawień i pobieranie w ModelsMonitor bez D-Bus: models.json w katalogu tymczasowym, atrapa serwera."""

    def setUp(self):
        import json
        import os
        import shutil
        import tempfile
        app()
        self.tmp = tempfile.mkdtemp(prefix="tide-models-monitor-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = os.path.join(self.tmp, "models.json")
        patcher = mock.patch.dict(os.environ, {"TIDE_MODELS_CONFIG": self.path})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.json = json

    def monitor(self, read, **kwargs):
        monitor = models_monitor.ModelsMonitor(read=read, poll_ms=600_000, **kwargs)
        self.addCleanup(monitor.stop)
        changes = []
        monitor.changed.connect(lambda: changes.append(self.json.loads(monitor.status_json())))
        return monitor, changes

    def test_read_started_before_a_write_does_not_bring_back_old_settings(self):
        from tide_daemon.models import status as models_status
        gate = threading.Event()
        reads = []

        def read():
            cfg = models_config.load()  # stan z chwili rozpoczęcia odczytu
            reads.append(cfg.default)
            if len(reads) == 1:
                gate.wait(5)
            return models_status.assemble(cfg, {})

        monitor, changes = self.monitor(read)
        monitor.poll()
        self.assertTrue(wait_until(lambda: reads == ["local"]))
        result = self.json.loads(monitor.set_endpoint(self.json.dumps({"name": "mac", "url": "http://mac:1"})))
        self.assertEqual(result, {"ok": True, "error": ""})
        result = self.json.loads(monitor.set_default("mac"))
        self.assertEqual(result, {"ok": True, "error": ""})
        self.assertEqual(changes[-1]["default"], "mac", "stan od razu z nowych ustawień")
        gate.set()
        self.assertTrue(wait_until(lambda: len(reads) == 2, timeout=5))
        wait_until(lambda: False, timeout=0.3)
        defaults = [c["default"] for c in changes]
        self.assertNotIn("local", defaults[defaults.index("mac"):], "odczyt sprzed zapisu nie cofa widoku")
        self.assertEqual(self.json.loads(monitor.status_json())["default"], "mac")

    def test_refused_write_sends_no_changed(self):
        monitor, changes = self.monitor(lambda: None)
        result = self.json.loads(monitor.set_default("mac"))
        self.assertEqual(result, {"ok": False, "error": "default: nieznany serwer „mac”"})
        self.assertEqual(changes, [])
        self.assertEqual(self.json.loads(monitor.set_role("[]"))["error"], "niepoprawne żądanie: oczekiwano obiektu JSON")
        self.assertIn("raport poranny jest wyłączony", self.json.loads(monitor.set_report_summary("local"))["error"])

    def test_pull_changed_throttled_and_entry_cleared(self):
        import time
        from .test_models_core import start_server

        lines = [{"status": "pulling manifest"}] + [
            {"status": "pulling aaa", "digest": "sha256:aaa", "total": 1000, "completed": n * 20} for n in range(51)
        ] + [{"status": "success"}]

        def stream(handler):
            try:
                handler.send_response(200)
                handler.send_header("Transfer-Encoding", "chunked")
                handler.end_headers()
                for line in lines:  # ok. 2 s: postęp co 40 ms
                    data = (self.json.dumps(line) + "\n").encode()
                    handler.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                    handler.wfile.flush()
                    time.sleep(0.04)
                handler.wfile.write(b"0\r\n\r\n")
            except OSError:
                pass

        server = start_server(self, {("POST", "/api/pull"): (200, stream)})
        with open(self.path, "w", encoding="utf-8") as fh:
            self.json.dump({"endpoints": {"local": {"url": server.url}}}, fh)
        monitor, changes = self.monitor(lambda: None, pull_keep_ms=300)
        started = time.monotonic()
        reply = self.json.loads(monitor.pull_model(self.json.dumps({"endpoint": "local", "model": "qwen3:8b"})))
        self.assertEqual(reply, {"ok": True, "error": "", "key": "local/qwen3:8b"})
        done = wait_until(lambda: changes and changes[-1]["pulls"].get("local/qwen3:8b", {}).get("status") == "success",
                          timeout=10)
        self.assertTrue(done)
        elapsed = time.monotonic() - started
        progress = [c for c in changes if c["pulls"].get("local/qwen3:8b", {}).get("running")]
        self.assertLessEqual(len(progress), int(elapsed) + 2, "Changed z postępem najwyżej raz na sekundę")
        self.assertGreaterEqual(len(progress), 2)
        self.assertEqual(changes[-1]["pulls"]["local/qwen3:8b"]["completed"], 1000)
        self.assertTrue(wait_until(lambda: changes[-1]["pulls"] == {}, timeout=3), "wpis znika po chwili")

    def test_stop_cancels_running_pull(self):
        import time
        from .test_models_core import start_server

        def hang(handler):
            try:
                handler.send_response(200)
                handler.send_header("Transfer-Encoding", "chunked")
                handler.end_headers()
                data = b'{"status": "pulling manifest"}\n'
                handler.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                handler.wfile.flush()
                time.sleep(5)
            except OSError:
                pass

        server = start_server(self, {("POST", "/api/pull"): (200, hang)})
        with open(self.path, "w", encoding="utf-8") as fh:
            self.json.dump({"endpoints": {"local": {"url": server.url}}}, fh)
        monitor, changes = self.monitor(lambda: None)
        monitor.pull_model(self.json.dumps({"endpoint": "local", "model": "qwen3:8b"}))
        self.assertTrue(wait_until(lambda: changes and changes[-1]["pulls"]["local/qwen3:8b"]["status"]
                                   == "pulling manifest", timeout=5))
        monitor.stop()
        entry = wait_until(lambda: not changes[-1]["pulls"]["local/qwen3:8b"]["running"]
                           and changes[-1]["pulls"]["local/qwen3:8b"], timeout=2)
        self.assertTrue(entry, "pobieranie przerwane od razu")
        self.assertIn("usługa się zamyka", entry["error"])
        self.assertIn("usługa się zamyka", self.json.loads(monitor.pull_model(
            self.json.dumps({"endpoint": "local", "model": "qwen3:8b"})))["error"])


    def hanging_pull_server(self):
        import time
        from .test_models_core import start_server

        def hang(handler):
            try:
                handler.send_response(200)
                handler.send_header("Transfer-Encoding", "chunked")
                handler.end_headers()
                data = b'{"status": "pulling manifest"}\n'
                handler.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                handler.wfile.flush()
                time.sleep(5)
            except OSError:
                pass

        return start_server(self, {("POST", "/api/pull"): (200, hang)})

    def test_endpoint_changes_refused_while_pulling(self):
        server = self.hanging_pull_server()
        with open(self.path, "w", encoding="utf-8") as fh:
            self.json.dump({"default": "local", "endpoints": {"local": {"url": "http://127.0.0.1:11434"},
                                                              "box": {"url": server.url}}}, fh)
        monitor, changes = self.monitor(lambda: None)
        monitor.pull_model(self.json.dumps({"endpoint": "box", "model": "qwen3:8b"}))
        self.assertTrue(wait_until(lambda: changes and changes[-1]["pulls"]["box/qwen3:8b"]["status"]
                                   == "pulling manifest", timeout=5))
        refused = self.json.loads(monitor.remove_endpoint("box"))
        self.assertEqual(refused, {"ok": False, "error": "na serwerze „box” trwa pobieranie modelu qwen3:8b — "
                                                         "poczekaj, aż się skończy"})
        moved = self.json.loads(monitor.set_endpoint(self.json.dumps({"name": "box", "url": "http://other:1"})))
        self.assertEqual(moved["error"], refused["error"])
        same = self.json.loads(monitor.set_endpoint(self.json.dumps({"name": "box", "url": server.url + "/",
                                                                     "token": "nowy"})))
        self.assertEqual(same, {"ok": True, "error": ""}, "token przy tym samym adresie — wolno")
        self.assertEqual(self.json.loads(monitor.remove_endpoint("local"))["ok"], False)  # domyślny, nie pobieranie

    def test_set_endpoint_shows_the_endpoint_unchecked_until_next_read(self):
        from tide_daemon.models import status as models_status
        with open(self.path, "w", encoding="utf-8") as fh:
            self.json.dump({"endpoints": {"local": {"url": "http://127.0.0.1:9"}}}, fh)
        gate = threading.Event()

        def read():
            cfg = models_config.load()
            state = models_status.assemble(cfg, {})
            state["endpoints"]["local"].update(error="Model lokalny: serwer odpowiedział 401 (127.0.0.1:9)")
            if gate.is_set():
                threading.Event().wait(5)  # kolejny odczyt „trwa”
            return state

        monitor, changes = self.monitor(read)
        self.assertEqual(self.json.loads(monitor.set_endpoint(self.json.dumps(
            {"name": "local", "url": "http://127.0.0.1:9", "token": "stary"}))), {"ok": True, "error": ""})
        self.assertTrue(wait_until(lambda: changes and "401" in changes[-1]["endpoints"]["local"]["error"]))
        gate.set()
        result = self.json.loads(monitor.set_endpoint(self.json.dumps({"name": "local", "url": "http://127.0.0.1:9",
                                                                       "token": "nowy"})))
        self.assertEqual(result, {"ok": True, "error": ""})
        local = changes[-1]["endpoints"]["local"]
        self.assertEqual((local["online"], local["error"], local["auth"]), (False, "", True),
                         "nowy token — punkt niesprawdzony, nie stary błąd 401")

    def test_bad_requests_and_test_queue_limit(self):
        deep = "[" * 100000
        for call in (monitor_call for monitor_call in ("set_endpoint", "set_role", "pull_model")):
            monitor, _changes = self.monitor(lambda: None)
            reply = self.json.loads(getattr(monitor, call)(deep))
            self.assertEqual((reply["ok"], reply["error"]), (False, "niepoprawne żądanie: oczekiwano obiektu JSON"))
        replies = []
        monitor.test_endpoint(deep, replies.append)
        self.assertEqual(self.json.loads(replies[0])["ok"], False)

        # Próby w toku: powyżej limitu odmowa od razu, bez kolejki rosnącej bez końca.
        import socket
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(64)  # przyjmuje połączenia i nic nie odpowiada — próba trwa do limitu 3 s
        self.addCleanup(listener.close)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        replies = []
        for _ in range(models_monitor.TESTS_MAX + 2):
            monitor.test_endpoint(self.json.dumps({"url": url}), replies.append)
        busy = [self.json.loads(r) for r in replies]
        self.assertEqual(len(busy), 2)
        self.assertEqual(busy[0]["error"], "za dużo prób połączenia naraz — spróbuj za chwilę")
        self.assertTrue(wait_until(lambda: len(replies) == models_monitor.TESTS_MAX + 2, timeout=10))
        monitor.test_endpoint(self.json.dumps({"url": url + "0"}), replies.append)  # po odpowiedziach znowu wolno
        self.assertTrue(wait_until(lambda: len(replies) == models_monitor.TESTS_MAX + 3, timeout=10))
        self.assertNotIn("za dużo", replies[-1])


if __name__ == "__main__":
    unittest.main()
