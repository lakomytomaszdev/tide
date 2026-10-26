"""Instalacja i odinstalowanie wtyczki KRunnera na tymczasowym HOME (atrapa qdbus6 z fakebin.py)."""
import configparser
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

from . import DAEMON_DIR
from .fakebin import calls, files_under, make_env
from .test_install import make
from tide_daemon.krunner import service

REPO = os.path.dirname(DAEMON_DIR)


@unittest.skipUnless(shutil.which("make"), "brak make")
class KRunnerInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-krunner-install-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.data = self.env["XDG_DATA_HOME"]
        self.plugin = os.path.join(self.data, "krunner/dbusplugins/tide.desktop")

    def test_install_places_plugin_and_restarts_krunner(self):
        make("install-krunner", self.env)
        self.assertEqual(files_under(self.data), ["krunner/dbusplugins/tide.desktop"])
        self.assertEqual(stat.S_IMODE(os.stat(self.plugin).st_mode), 0o644)
        self.assertEqual(calls(self.env), ["qdbus6 org.kde.krunner /MainApplication quit"])

    def test_plugin_points_at_the_daemon(self):
        make("install-krunner", self.env)
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(self.plugin, encoding="utf-8")
        entry = parser["Desktop Entry"]
        self.assertEqual(entry["X-Plasma-API"], "DBus")
        self.assertEqual(entry["X-Plasma-DBusRunner-Service"], service.SERVICE)
        self.assertEqual(entry["X-Plasma-DBusRunner-Path"], service.PATH)
        self.assertEqual(entry["X-KDE-PluginInfo-Name"], "tide")
        self.assertEqual(entry["X-KDE-PluginInfo-EnabledByDefault"], "true")
        self.assertEqual(entry["X-Plasma-Runner-Min-Letter-Count"], "3")
        syntaxes = entry["X-Plasma-Runner-Syntaxes"].split(",")
        self.assertEqual(syntaxes, [":q:", "ai :q:", "raport"])
        self.assertEqual(len(entry["X-Plasma-Runner-Syntax-Descriptions"].split(",")), len(syntaxes))

    def test_uninstall_removes_plugin(self):
        make("install-krunner", self.env)
        make("uninstall-krunner", self.env)
        self.assertEqual(files_under(self.data), [])
        self.assertFalse(os.path.exists(os.path.join(self.data, "krunner")))
        self.assertEqual(calls(self.env)[-1], "qdbus6 org.kde.krunner /MainApplication quit")

    def test_daemon_install_adds_activation_for_krunner_name(self):
        make("install-daemon", self.env)
        path = os.path.join(self.data, f"dbus-1/services/{service.SERVICE}.service")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(f"Name={service.SERVICE}\n", text)
        self.assertIn("SystemdService=tide-daemon.service", text)
        make("uninstall-daemon", self.env)
        self.assertFalse(os.path.exists(path))

    def test_plugin_is_part_of_full_install_and_uninstall(self):
        # -n: tylko wypisanie poleceń, nic nie jest wykonywane.
        for target, action in (("install", "install"), ("uninstall", "uninstall")):
            out = subprocess.run(["make", "-s", "-n", "-C", REPO, target], env=self.env, capture_output=True,
                                 text=True).stdout
            self.assertIn(f"bash krunner/install.sh {action}", out)


if __name__ == "__main__":
    unittest.main()
