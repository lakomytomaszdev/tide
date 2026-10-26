"""Instalacja i odinstalowanie wyglądu na tymczasowym katalogu domowym.

Narzędzia Plasmy, które zmieniają działający pulpit, są podmienione atrapami
zapisującymi wywołania. kreadconfig6/kwriteconfig6 działają naprawdę, ale na
tymczasowym XDG_CONFIG_HOME.
"""
import hashlib
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUBS = ("plasma-apply-colorscheme", "plasma-apply-desktoptheme", "fc-cache", "dbus-send")


class AppearanceInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.config = self.home / ".config"
        self.data = self.home / ".local/share"
        self.state = self.home / ".local/state"
        self.log = base / "calls.log"
        bin_dir = base / "bin"
        bin_dir.mkdir()
        for name in STUBS:
            stub = bin_dir / name
            stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\n')
            stub.chmod(0o755)

        archive = base / "geist.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("geist-font/Geist/ttf/Geist-Regular.ttf", b"font")
            zf.writestr("geist-font/GeistMono/ttf/GeistMono-Regular.ttf", b"mono")
            zf.writestr("geist-font/OFL.txt", "license")

        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_DATA_HOME": str(self.data),
            "XDG_STATE_HOME": str(self.state),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TIDE_GEIST_ZIP": str(archive),
            "TIDE_GEIST_SHA256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        self.write_config("kdeglobals", "General", "ColorScheme", "Nordic")
        self.write_config("plasmarc", "Theme", "name", "Nordic")

    def tearDown(self):
        self.tmp.cleanup()

    def write_config(self, file, group, key, value):
        subprocess.run(["kwriteconfig6", "--file", file, "--group", group, "--key", key, value],
                       env=self.env, check=True)

    def read_config(self, file, group, key):
        return subprocess.run(["kreadconfig6", "--file", file, "--group", group, "--key", key],
                              env=self.env, check=True, capture_output=True, text=True).stdout.strip()

    def appearance(self, action):
        subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/appearance.py"), action],
                       env=self.env, check=True, capture_output=True, text=True)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_install_applies_scheme_fonts_and_breeze_style(self):
        self.appearance("install")
        self.assertTrue((self.data / "color-schemes/TideMist.colors").is_file())
        self.assertTrue((self.data / "fonts/tide/Geist-Regular.ttf").is_file())
        self.assertTrue((self.data / "fonts/tide/GeistMono-Regular.ttf").is_file())
        self.assertTrue(self.read_config("kdeglobals", "General", "font").startswith("Geist,10,"))
        self.assertIn("plasma-apply-colorscheme TideMist", self.calls())
        self.assertIn("plasma-apply-desktoptheme default", self.calls())

    def test_uninstall_restores_previous_appearance(self):
        self.appearance("install")
        self.appearance("uninstall")
        self.assertFalse((self.data / "color-schemes/TideMist.colors").exists())
        self.assertFalse((self.data / "fonts/tide").exists())
        self.assertEqual(self.read_config("kdeglobals", "General", "font"), "")
        self.assertIn("plasma-apply-colorscheme Nordic", self.calls())
        self.assertIn("plasma-apply-desktoptheme Nordic", self.calls())

    def test_reinstall_keeps_the_original_previous_state(self):
        self.appearance("install")
        # Narzędzia Plasmy są atrapami, więc zapis Tide w konfiguracji symulujemy ręcznie.
        self.write_config("kdeglobals", "General", "ColorScheme", "TideMist")
        self.appearance("install")
        self.appearance("uninstall")
        self.assertIn("plasma-apply-colorscheme Nordic", self.calls())

    def test_wrong_font_checksum_stops_install(self):
        self.env["TIDE_GEIST_SHA256"] = "0" * 64
        with self.assertRaises(subprocess.CalledProcessError):
            self.appearance("install")
        self.assertFalse((self.data / "fonts/tide").exists())


if __name__ == "__main__":
    unittest.main()
