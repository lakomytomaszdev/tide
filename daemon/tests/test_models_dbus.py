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
                                                     "installed": True, "available": True, "error": ""})
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
