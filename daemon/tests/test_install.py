"""Instalacja i odinstalowanie tide-daemon na tymczasowym katalogu domowym (podmienione XDG).

systemctl jest atrapą (fakebin.py) — test sprawdza rozmieszczenie plików, wywołania usługi
i to, że po odinstalowaniu nic nie zostaje.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from . import DAEMON_DIR, FIXTURES
from .fakebin import calls, files_under, make_env

REPO = os.path.dirname(DAEMON_DIR)


def make(target: str, env: dict) -> None:
    subprocess.run(["make", "-s", "-C", REPO, target], env=env, check=True, capture_output=True, text=True)


@unittest.skipUnless(shutil.which("make"), "brak make")
class DaemonInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-install-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.data = self.env["XDG_DATA_HOME"]
        self.config = self.env["XDG_CONFIG_HOME"]

    def test_install_places_files_and_enables_service(self):
        make("install-daemon", self.env)

        installed = os.path.join(self.data, "tide/daemon")
        files = files_under(installed)
        self.assertIn("tide-daemon.py", files)
        self.assertIn("tide_daemon/agents/scanner.py", files)
        self.assertFalse([f for f in files if "__pycache__" in f])

        with open(os.path.join(self.config, "systemd/user/tide-daemon.service"), encoding="utf-8") as fh:
            unit = fh.read()
        self.assertIn(f"ExecStart=/usr/bin/python3 -s {installed}/tide-daemon.py", unit)
        self.assertIn("BusName=org.tide.Daemon", unit)
        self.assertTrue(os.path.exists(os.path.join(self.data, "dbus-1/services/org.tide.Daemon.service")))
        self.assertEqual(calls(self.env), [
            "systemctl --user daemon-reload",
            "systemctl --user enable tide-daemon.service",
            "systemctl --user restart tide-daemon.service",
        ])

        # Zainstalowana kopia działa samodzielnie, bez repozytorium.
        out = subprocess.run(
            ["/usr/bin/python3", os.path.join(installed, "tide-daemon.py"), "--print", "--no-processes",
             "--claude-dir", os.path.join(FIXTURES, "claude"), "--codex-dir", os.path.join(FIXTURES, "codex"),
             "--now", "2026-10-26T12:00:00Z"],
            env=self.env, check=True, capture_output=True, text=True).stdout
        self.assertGreater(len(json.loads(out)), 0)

    def test_uninstall_leaves_nothing_behind(self):
        make("install-daemon", self.env)
        make("uninstall-daemon", self.env)
        self.assertEqual(files_under(self.data), [])
        self.assertEqual(files_under(self.config), [])
        self.assertIn("systemctl --user disable --now tide-daemon.service", calls(self.env))


if __name__ == "__main__":
    unittest.main()
