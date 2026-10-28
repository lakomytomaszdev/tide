"""Obiekt /Models usługi na prywatnej magistrali: atrapa serwera Ollamy (HTTP w wątku testu) i własny models.json.

Sprawdzamy kontrakt (Status() → JSON, Refresh(), sygnał Changed tylko przy zmianie), punkt, który nie
odpowiada, ustawienia czytane przy każdym odczycie, token tylko w nagłówku i nigdy w odpowiedzi,
--no-models i --print-models. Prawdziwa Ollama z tego komputera nie jest pytana (privatebus.py
i każdy test podają własny models.json).
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest

from . import DAEMON_DIR
from .privatebus import SERVICE, PrivateBus, requires_dbus, wait_until
from .test_models_core import OLLAMA_PS, closed_port, ollama_routes, start_server, write_models

MODELS_PATH = "/Models"
MODELS_IFACE = "org.tide.Daemon.Models"


@requires_dbus
class ModelsDBusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-models-dbus-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.empty = os.path.join(self.tmp, "empty")
        os.makedirs(self.empty)
        self.token = "test-" + secrets.token_hex(12)
        self.token_file = os.path.join(self.tmp, "local.token")
        with open(self.token_file, "w", encoding="utf-8") as fh:
            fh.write(self.token)
        self.server = start_server(self, ollama_routes())
        self.server.routes[("GET", "/api/ps")] = (200, {"models": []})
        self.down = f"http://127.0.0.1:{closed_port()}"
        self.bus = PrivateBus("tide-models-test")
        self.addCleanup(self.bus.stop)

    def config(self, endpoints, **extra):
        return write_models(self.tmp, {"endpoints": endpoints, **extra})

    def spawn(self, config_path, *args):
        return self.bus.spawn_daemon(
            "--claude-dir", self.empty, "--codex-dir", self.empty, "--workspace", self.empty, "--no-processes",
            "--no-krunner", "--no-idle", "--no-limits", *args, extra_env={"TIDE_MODELS_CONFIG": config_path})

    def status(self):
        reply = self.bus.call(SERVICE, MODELS_PATH, MODELS_IFACE, "Status")
        return json.loads(reply.arguments()[0]) if reply.arguments() else None

    def test_status_and_changed_only_when_state_changes(self):
        from PyQt6.QtCore import QObject, pyqtSlot

        path = self.config({"local": {"url": self.server.url, "kind": "ollama", "tokenFile": self.token_file},
                            "mac": {"url": self.down, "kind": "ollama"}},
                           roles={"summary": "local:qwen3:8b", "agent": "mac:qwen3-coder:30b"})
        self.spawn(path, "--models-poll", "0.2")
        state = wait_until(lambda: (self.status() or {}).get("checked") and self.status(), timeout=10)
        self.assertTrue(state, "pierwszy odczyt stanu")
        local, mac = state["endpoints"]["local"], state["endpoints"]["mac"]
        self.assertEqual((local["online"], local["version"], local["auth"], local["loaded"]),
                         (True, "0.40.11", True, []))
        self.assertIn("qwen3:8b", [m["name"] for m in local["models"]])
        self.assertFalse(mac["online"])
        self.assertEqual(mac["error"], f"Model lokalny niedostępny: serwer nie odpowiada "
                                       f"({self.down.removeprefix('http://')})")
        self.assertEqual(state["roles"]["summary"], {"endpoint": "local", "model": "qwen3:8b", "ctx": 8192,
                                                     "installed": True, "available": True, "error": "",
                                                     "pinned": True})
        self.assertEqual(state["roles"]["agent"]["available"], False)
        self.assertEqual(state["config"], {"path": path, "exists": True, "error": "", "warnings": []})
        self.assertEqual(state["queue"], {"local": {"running": None, "waiting": []},
                                          "mac": {"running": None, "waiting": []}}, "kolejka modeli — nic nie trwa")

        changes = []

        class Receiver(QObject):
            @pyqtSlot()
            def changed(self):
                changes.append(True)

        receiver = Receiver()
        self.assertTrue(self.bus.bus.connect(SERVICE, MODELS_PATH, MODELS_IFACE, "Changed", receiver.changed))
        polled = len(self.server.requests)
        wait_until(lambda: len(self.server.requests) >= polled + 9, timeout=5)  # co najmniej trzy odczyty
        self.assertEqual(changes, [], "ten sam stan — bez Changed")

        self.server.routes[("GET", "/api/ps")] = (200, OLLAMA_PS)  # ktoś załadował model
        self.assertTrue(wait_until(lambda: changes, timeout=5), "sygnał Changed po załadowaniu modelu")
        loaded = self.status()["endpoints"]["local"]["loaded"]
        self.assertEqual([(m["name"], m["gpuPercent"], m["contextLength"]) for m in loaded], [("qwen3:8b", 75, 8192)])

        # Tylko odczyt stanu, token tylko w nagłówku — nigdy w odpowiedzi D-Bus.
        self.assertEqual({(r["method"], r["path"], r["auth"]) for r in self.server.requests},
                         {("GET", p, f"Bearer {self.token}") for p in ("/api/version", "/api/tags", "/api/ps")})
        self.assertNotIn(self.token, json.dumps(self.status()))

        xml = self.bus.call(SERVICE, MODELS_PATH, "org.freedesktop.DBus.Introspectable", "Introspect").arguments()[0]
        self.assertIn(f'<interface name="{MODELS_IFACE}">', xml)
        for member in ('<method name="Status">', '<method name="Refresh"', '<signal name="Changed"'):
            self.assertIn(member, xml)

    def test_refresh_reads_settings_now(self):
        path = self.config({"local": {"url": self.down, "kind": "ollama"}})
        self.spawn(path, "--models-poll", "600")
        state = wait_until(lambda: (self.status() or {}).get("checked") and self.status(), timeout=10)
        self.assertFalse(state["endpoints"]["local"]["online"])
        self.assertIn("serwer nie odpowiada", state["endpoints"]["local"]["error"])
        self.assertEqual(state["roles"]["summary"]["installed"], None)

        # Serwer wstał pod innym adresem: zmiana models.json i Refresh() — bez restartu usługi.
        self.config({"local": {"url": self.server.url, "kind": "ollama"}})
        from PyQt6.QtDBus import QDBusMessage
        reply = self.bus.call(SERVICE, MODELS_PATH, MODELS_IFACE, "Refresh")
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        state = wait_until(lambda: self.status()["endpoints"]["local"]["online"] and self.status(), timeout=5)
        self.assertTrue(state, "Refresh() czyta stan od razu")
        self.assertTrue(state["roles"]["summary"]["available"])

    def test_no_models_flag_drops_the_object(self):
        self.spawn(self.config({"local": {"url": self.server.url}}), "--no-models")
        reply = self.bus.call(SERVICE, MODELS_PATH, MODELS_IFACE, "Status")
        from PyQt6.QtDBus import QDBusMessage
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ErrorMessage)
        wait_until(lambda: False, timeout=1.5)
        self.assertEqual(self.server.requests, [], "bez obiektu nikt nie pyta serwera")


@requires_dbus
class ModelsSettingsDBusTest(unittest.TestCase):
    """Zapis ustawień z D-Bus (PT-25): models.json i tokeny w katalogu testu, report.json we własnym
    XDG_CONFIG_HOME — nigdy prawdziwe ~/.config. Serwery to atrapy; prawdziwa Ollama nie jest pytana."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-models-settings-dbus-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.empty = os.path.join(self.tmp, "empty")
        os.makedirs(self.empty)
        self.config_home = os.path.join(self.tmp, "config")
        self.models = os.path.join(self.config_home, "tide", "models.json")
        self.report = os.path.join(self.config_home, "tide", "report.json")
        self.server = start_server(self, ollama_routes())
        self.server.routes[("GET", "/api/ps")] = (200, {"models": []})
        self.bus = PrivateBus("tide-models-settings-test")
        self.addCleanup(self.bus.stop)

    def spawn(self, *args):
        return self.bus.spawn_daemon(
            "--claude-dir", self.empty, "--codex-dir", self.empty, "--workspace", self.empty, "--no-processes",
            "--no-krunner", "--no-idle", "--no-limits", "--models-poll", "600", *args,
            extra_env={"TIDE_MODELS_CONFIG": self.models, "XDG_CONFIG_HOME": self.config_home})

    def call(self, member, *args):
        reply = self.bus.call(SERVICE, MODELS_PATH, MODELS_IFACE, member, *args)
        from PyQt6.QtDBus import QDBusMessage
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        return json.loads(reply.arguments()[0])

    def status(self):
        return self.call("Status")

    def changes(self):
        from PyQt6.QtCore import QObject, pyqtSlot
        seen = []

        class Receiver(QObject):
            @pyqtSlot()
            def changed(self):
                seen.append(True)

        self.receiver = Receiver()
        self.assertTrue(self.bus.bus.connect(SERVICE, MODELS_PATH, MODELS_IFACE, "Changed", self.receiver.changed))
        return seen

    def test_settings_written_from_dbus(self):
        os.makedirs(os.path.dirname(self.report))
        with open(self.report, "w", encoding="utf-8") as fh:
            json.dump({"at": "06:45", "summaryModel": "opus"}, fh)
        self.spawn()
        state = self.status()
        self.assertEqual(state["report"], {"summary": "claude", "configured": "claude", "summaryModel": "opus",
                                           "raw": None, "pinned": False, "path": self.report, "warnings": []})
        self.assertEqual(state["pulls"], {})
        self.assertFalse(os.path.exists(self.models), "start usługi niczego nie zapisuje")
        seen = self.changes()

        token = "tok-" + secrets.token_hex(12)
        result = self.call("SetEndpoint", json.dumps({"name": "mac", "url": self.server.url, "kind": "ollama",
                                                      "token": token}))
        self.assertEqual(result, {"ok": True, "error": ""})
        self.assertTrue(wait_until(lambda: seen, timeout=5), "Changed po zapisie")
        state = self.status()
        self.assertEqual(state["endpoints"]["mac"]["auth"], True)
        self.assertNotIn(token, json.dumps(state))
        state = wait_until(lambda: self.status()["endpoints"]["mac"]["online"] and self.status(), timeout=10)
        self.assertTrue(state, "nowy punkt sprawdzony zaraz po zapisie")
        self.assertIn(("/api/version", f"Bearer {token}"), {(r["path"], r["auth"]) for r in self.server.requests})
        token_file = os.path.join(self.config_home, "tide", "tokens", "mac")
        self.assertEqual(oct(os.stat(token_file).st_mode & 0o777), oct(0o600))
        self.assertEqual(oct(os.stat(self.models).st_mode & 0o777), oct(0o600))

        self.assertEqual(self.call("SetRole", json.dumps({"role": "agent", "model": "qwen3-coder:30b"})),
                         {"ok": True, "error": ""})
        self.assertEqual(self.call("SetDefault", "mac"), {"ok": True, "error": ""})
        state = self.status()  # stan po zapisie od razu z nowych ustawień, bez czekania na odczyt
        self.assertEqual(state["default"], "mac")
        self.assertEqual({r: state["roles"][r]["endpoint"] for r in ("summary", "describe", "agent")},
                         {"summary": "mac", "describe": "mac", "agent": "mac"})
        state = wait_until(lambda: self.status()["roles"]["summary"]["available"] and self.status(), timeout=10)
        self.assertTrue(state, "rola summary dostępna na nowym domyślnym punkcie")

        refused = self.call("RemoveEndpoint", "mac")
        self.assertEqual(refused["ok"], False)
        self.assertIn("domyślny", refused["error"])
        self.assertEqual(self.call("RemoveEndpoint", "local"), {"ok": True, "error": ""})
        self.assertEqual(list(self.status()["endpoints"]), ["mac"])
        self.assertEqual(self.call("SetEndpoint", "nie json")["ok"], False)
        bad = self.call("SetEndpoint", json.dumps({"name": "lm", "url": "http://lm:1234", "kind": "llamacpp"}))
        self.assertEqual(bad, {"ok": False, "error": "serwer „lm”: nieznany rodzaj „llamacpp” (ollama albo openai)"})

        self.assertEqual(state["agentProgram"], "codex", "program agenta lokalnego: domyślnie Codex")
        self.assertEqual(self.call("SetAgentProgram", "claude"), {"ok": True, "error": ""})
        self.assertEqual(self.status()["agentProgram"], "claude", "stan od razu po zapisie")
        with open(self.models, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["agentProgram"], "claude")
        self.assertEqual(self.call("SetAgentProgram", "opencode"),
                         {"ok": False, "error": "agentProgram: nieznany program „opencode” (codex albo claude)"})
        self.assertEqual(self.status()["agentProgram"], "claude")

        self.assertEqual(self.call("SetReportSummary", "local"), {"ok": True, "error": ""})
        with open(self.report, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"at": "06:45", "summaryModel": "opus", "summary": "local"})
        self.assertEqual((self.status()["report"]["summary"], self.status()["report"]["configured"]),
                         ("local", "local"), "tryb działa od razu w usłudze")
        wrong = self.call("SetReportSummary", "Local")
        self.assertEqual(wrong["ok"], False)
        self.assertIn("claude, local, rules", wrong["error"])

        xml = self.bus.call(SERVICE, MODELS_PATH, "org.freedesktop.DBus.Introspectable", "Introspect").arguments()[0]
        for member in ("SetEndpoint", "RemoveEndpoint", "SetDefault", "SetRole", "SetAgentProgram",
                       "SetReportSummary", "TestEndpoint", "PullModel"):
            self.assertIn(f'<method name="{member}">', xml)

    def test_pinned_report_summary_changes_only_the_file(self):
        self.spawn("--report-summary", "rules")
        self.assertEqual(self.call("SetReportSummary", "local"), {"ok": True, "error": ""})
        report = self.status()["report"]
        self.assertEqual((report["summary"], report["configured"], report["pinned"]), ("rules", "local", True))

    def test_test_endpoint_is_async_and_saves_nothing(self):
        import threading
        import time
        self.spawn()
        result = self.call("TestEndpoint", json.dumps({"url": self.server.url, "kind": "ollama", "token": "abc"}))
        self.assertEqual(result, {"ok": True, "error": "", "version": "0.40.11", "models": 3})
        self.assertEqual({r["auth"] for r in self.server.requests if r["path"] == "/api/tags"}, {"Bearer abc"})
        down = self.call("TestEndpoint", json.dumps({"url": f"http://127.0.0.1:{closed_port()}"}))
        self.assertEqual(down["ok"], False)
        self.assertIn("serwer nie odpowiada", down["error"])
        self.assertEqual(self.call("TestEndpoint", json.dumps({"name": "mac"}))["error"], "nieznany serwer „mac”")
        self.assertFalse(os.path.exists(self.models), "próba niczego nie zapisuje")

        # Wolny serwer: odpowiedź po odczycie, a w tym czasie usługa odpowiada na inne wywołania.
        slow = start_server(self, ollama_routes())
        slow.delay = 1.0
        box = {}

        def call_slow():  # osobne połączenie testu w innym wątku — wywołanie blokujące
            from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage
            conn = QDBusConnection.connectToBus(self.bus.address, "tide-models-slow-thread")
            msg = QDBusMessage.createMethodCall(SERVICE, MODELS_PATH, MODELS_IFACE, "TestEndpoint")
            msg.setArguments([json.dumps({"url": slow.url})])
            box["reply"] = conn.call(msg, QDBus.CallMode.Block, 10000).arguments()
            QDBusConnection.disconnectFromBus("tide-models-slow-thread")

        worker = threading.Thread(target=call_slow)
        worker.start()
        time.sleep(0.3)
        started = time.monotonic()
        self.status()
        self.assertLess(time.monotonic() - started, 0.5, "Status() nie czeka na próbę połączenia")
        worker.join(15)
        self.assertEqual(json.loads(box["reply"][0])["version"], "0.40.11")

    def test_pull_model_progress(self):
        from .test_models_settings import PULL_LINES

        def slow_stream(handler):
            import time
            try:
                handler.send_response(200)
                handler.send_header("Transfer-Encoding", "chunked")
                handler.end_headers()
                for line in PULL_LINES:
                    data = (json.dumps(line) + "\n").encode()
                    handler.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                    handler.wfile.flush()
                    time.sleep(0.4)
                handler.wfile.write(b"0\r\n\r\n")
            except OSError:
                pass

        self.server.routes[("POST", "/api/pull")] = (200, slow_stream)
        os.makedirs(os.path.dirname(self.models))
        with open(self.models, "w", encoding="utf-8") as fh:
            json.dump({"endpoints": {"local": {"url": self.server.url},
                                     "lm": {"url": self.server.url, "kind": "openai"}}}, fh)
        self.spawn()
        seen = self.changes()
        self.assertEqual(self.call("PullModel", json.dumps({"endpoint": "local", "model": "qwen3:8b"})),
                         {"ok": True, "error": "", "key": "local/qwen3:8b"})
        busy = self.call("PullModel", json.dumps({"endpoint": "local", "model": "bge-m3"}))
        self.assertEqual((busy["ok"], busy["key"]), (False, ""))
        self.assertIn("trwa już pobieranie qwen3:8b", busy["error"])
        self.assertIn("tylko z Ollamą", self.call("PullModel", json.dumps({"endpoint": "lm", "model": "x"}))["error"])

        progress = wait_until(lambda: (self.status()["pulls"].get("local/qwen3:8b") or {}).get("total")
                              and self.status()["pulls"]["local/qwen3:8b"], timeout=10)
        self.assertTrue(progress, "postęp w Status().pulls")
        self.assertEqual((progress["endpoint"], progress["model"], progress["running"]), ("local", "qwen3:8b", True))
        done = wait_until(lambda: self.status()["pulls"]["local/qwen3:8b"]["status"] == "success"
                          and self.status()["pulls"]["local/qwen3:8b"], timeout=10)
        self.assertEqual(done, {"endpoint": "local", "model": "qwen3:8b", "status": "success", "completed": 1100,
                                "total": 1100, "running": False, "error": ""})
        self.assertTrue(seen)
        self.assertEqual([r["body"] for r in self.server.requests if r["path"] == "/api/pull"],
                         [{"model": "qwen3:8b", "stream": True}])


class PrintModelsTest(unittest.TestCase):
    def test_print_models_one_read_without_dbus(self):
        tmp = tempfile.mkdtemp(prefix="tide-models-print-")
        self.addCleanup(shutil.rmtree, tmp)
        server = start_server(self, ollama_routes())
        path = write_models(tmp, {"endpoints": {"local": {"url": server.url}}})
        env = dict(os.environ, TIDE_MODELS_CONFIG=path, DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent/bus")
        result = subprocess.run([sys.executable, os.path.join(DAEMON_DIR, "tide-daemon.py"), "--print-models",
                                 "--claude-dir", tmp, "--codex-dir", tmp, "--workspace", tmp],
                                capture_output=True, text=True, timeout=30, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertTrue(state["checked"])
        self.assertTrue(state["endpoints"]["local"]["online"])
        self.assertEqual(state["endpoints"]["local"]["loaded"][0]["gpuPercent"], 75)
        self.assertEqual({r["method"] for r in server.requests}, {"GET"})
        self.assertEqual(os.listdir(tmp), ["models.json"], "niczego nie zapisuje")


if __name__ == "__main__":
    unittest.main()
