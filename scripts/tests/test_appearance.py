"""Instalacja i odinstalowanie wyglądu na tymczasowym katalogu domowym.

Narzędzia Plasmy, które zmieniają działający pulpit, są podmienione atrapami
zapisującymi wywołania. kreadconfig6/kwriteconfig6 działają naprawdę, ale na
tymczasowym XDG_CONFIG_HOME.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.tests.broken_palettes import BROKEN, broken_palette_dir  # noqa: E402

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
            "XDG_CONFIG_DIRS": "/etc/xdg",  # bez ~/.config/kdedefaults z sesji Plasmy
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

    def test_install_applies_scheme_and_fonts_but_leaves_the_plasma_style(self):
        self.appearance("install")
        self.assertTrue((self.data / "color-schemes/TideMist.colors").is_file())
        self.assertTrue((self.data / "fonts/tide/Geist-Regular.ttf").is_file())
        self.assertTrue((self.data / "fonts/tide/GeistMono-Regular.ttf").is_file())
        self.assertTrue(self.read_config("kdeglobals", "General", "font").startswith("Geist,10,"))
        self.assertIn("plasma-apply-colorscheme TideMist", self.calls())
        # Styl Plasmy nakłada install-plasma-style według wyglądu aktywności (decyzja 78).
        self.assertFalse([c for c in self.calls() if c.startswith("plasma-apply-desktoptheme")])

    def test_install_writes_the_streamer_scheme_beside_mist_but_applies_only_mist(self):
        self.appearance("install")
        scheme = self.data / "color-schemes/TideStreamer.colors"
        self.assertIn("ColorScheme=TideStreamer", scheme.read_text())
        self.assertNotIn("TideStreamer", "\n".join(self.calls()))
        self.appearance("uninstall")
        self.assertFalse(scheme.exists())

    def test_broken_variant_does_not_stop_the_mist_install(self):
        for kind in BROKEN:
            with self.subTest(broken=kind):
                self.env["TIDE_PALETTE_DIR"] = str(broken_palette_dir(Path(self.tmp.name), kind))
                self.appearance("install")
                self.assertTrue((self.data / "color-schemes/TideMist.colors").is_file())
                self.assertIn("plasma-apply-colorscheme TideMist", self.calls())
                self.assertTrue(self.read_config("kdeglobals", "General", "font").startswith("Geist,10,"))

    def test_uninstall_removes_every_scheme_tide_wrote_and_only_those(self):
        self.appearance("install")
        schemes = self.data / "color-schemes"
        old = schemes / "TideStary.colors"  # wariant usunięty z repozytorium po instalacji
        old.write_text((schemes / "TideMist.colors").read_text())
        foreign = schemes / "TideCudzy.colors"  # nie od Tide (brak nagłówka generatora)
        foreign.write_text("[General]\nName=Cudzy\n")
        self.appearance("uninstall")
        self.assertEqual(sorted(p.name for p in schemes.iterdir()), ["TideCudzy.colors"])

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

    def enter_stream(self):
        """Bieżąca aktywność to Stream (atrapa ActivityManager w qdbus6), przypisana do wyglądu stream."""
        stream = "5e7a0c1d-0000-4000-8000-00000000beef"
        qdbus = Path(self.env["PATH"].split(":")[0]) / "qdbus6"
        qdbus.write_text(f'#!/bin/sh\necho "qdbus6 $*" >> "{self.log}"\necho {stream}\n')
        qdbus.chmod(0o755)
        bindings = self.home / ".config/tide/activities.json"
        bindings.parent.mkdir(parents=True, exist_ok=True)
        bindings.write_text(json.dumps({"activities": {stream: "stream"}}))

    def test_install_in_the_stream_activity_does_not_apply_mist(self):
        self.enter_stream()
        (self.data / "color-schemes").mkdir(parents=True)
        (self.data / "color-schemes/TideStreamer.colors").write_text("[General]\n")  # z PT-34
        self.appearance("install")
        self.assertIn("plasma-apply-colorscheme TideStreamer", self.calls())
        self.assertNotIn("plasma-apply-colorscheme TideMist", self.calls())
        self.assertTrue((self.data / "color-schemes/TideMist.colors").is_file())  # pliki pracy są

    def test_install_refreshes_a_stale_engine_copy_for_the_service(self):
        engine = self.data / "tide/activity-look"
        (engine / "scripts").mkdir(parents=True)
        (engine / "scripts/activity_look.py").write_text("# stara kopia\n")
        (engine / ".tide-engine.json").write_text(json.dumps({"hash": "stara"}))
        out = subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/appearance.py"), "install"],
                             env=self.env, check=True, capture_output=True, text=True).stdout
        self.assertIn("odświeżono kopię silnika", out)
        self.assertEqual((engine / "scripts/activity_look.py").read_text(),
                         (ROOT / "scripts/activity_look.py").read_text())

    def test_wrong_font_checksum_stops_install(self):
        self.env["TIDE_GEIST_SHA256"] = "0" * 64
        with self.assertRaises(subprocess.CalledProcessError):
            self.appearance("install")
        self.assertFalse((self.data / "fonts/tide").exists())


if __name__ == "__main__":
    unittest.main()
