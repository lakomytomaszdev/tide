"""Instalacja i odinstalowanie górnych pasków na tymczasowym katalogu domowym.

qdbus6 jest atrapą (fake_qdbus6.py): skrypty plasmashell trafiają do katalogu wywołań,
żeby dało się sprawdzić ich treść, i wykonują się na atrapie API w qml6. dbus-send też
jest atrapą. Żadne narzędzie nie dotyka działającego pulpitu.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.tests import fake_plasmashell as fake

ROOT = Path(__file__).resolve().parents[2]
PALETTE = json.loads((ROOT / "theme/palette/mist.json").read_text())


@unittest.skipUnless(fake.available(), "Brak qml6 do uruchomienia skryptów")
class PanelsInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.data = self.home / ".local/share"
        self.state = self.home / ".local/state"
        self.fake_dir = base / "fake"
        self.fake_dir.mkdir()
        self.icon_file = self.data / "plasma_icons/index.html.desktop"
        self.icon_file.parent.mkdir(parents=True)
        self.icon_file.write_text("[Desktop Entry]\nType=Link\nURL=file:///tmp/index.html\n")
        (self.fake_dir / "plasma.json").write_text(json.dumps(fake.desktop_like_this_one(str(self.icon_file))))
        self.set_appmenu(True)

        bin_dir = base / "bin"
        bin_dir.mkdir()
        (bin_dir / "qdbus6").symlink_to(ROOT / "scripts/tests/fake_qdbus6.py")
        (bin_dir / "dbus-send").write_text(f'#!/bin/sh\necho "dbus-send $*" >> "{self.fake_dir}/dbus.log"\n')
        (bin_dir / "dbus-send").chmod(0o755)
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.data),
            "XDG_STATE_HOME": str(self.state),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TIDE_FAKE_DIR": str(self.fake_dir),
            "TIDE_PANEL_SETTLE": "0",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def set_appmenu(self, autoloaded):
        (self.fake_dir / "kded.json").write_text(json.dumps({"appmenu": autoloaded}))

    def panels(self, action):
        return subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/panels.py"), action],
                              env=self.env, check=True, capture_output=True, text=True).stdout

    def scripts(self, tag):
        return [p.read_text() for p in sorted((self.fake_dir / "calls").glob(f"*-{tag}.js"))]

    def plasma(self):
        return json.loads((self.fake_dir / "plasma.json").read_text())

    def kded_calls(self):
        log = self.fake_dir / "calls/kded.log"
        return log.read_text().splitlines() if log.exists() else []

    @property
    def previous_file(self):
        return self.state / "tide/panels-previous.json"

    def test_install_script_sets_up_both_bars(self):
        self.panels("install")
        js = self.scripts("install")[0]
        self.assertIn('"org.kde.plasma.appmenu"', js)
        self.assertIn('"icon": "tide-logo"', js)
        self.assertIn("showDate: true", js)
        self.assertIn("allScreens: false", js)
        self.assertIn('"mainOutput": "DP-2"', js)
        self.assertIn('"sideOutput": "HDMI-A-1"', js)
        self.assertIn("tideScreensNow.main", js)
        self.assertIn("tideScreensNow.side", js)
        self.assertIn(f'"barHeight": {PALETTE["size"]["bar"]}', js)
        # Kolejność widżetów ustawia osobny skrypt, po dodaniu widżetów.
        self.assertIn("AppletOrder", self.scripts("order")[0])

        live = [p for p in self.plasma()["panels"] if not p["removed"]]
        self.assertEqual(sorted(p["screen"] for p in live if p["location"] == "top"), [0, 1])

    def test_install_saves_the_previous_layout_with_launcher_files(self):
        self.panels("install")
        previous = json.loads(self.previous_file.read_text())
        top = next(p for p in previous["panels"] if p["id"] == 73)
        self.assertEqual([w["type"].rsplit(".", 1)[-1] for w in top["widgets"]],
                         ["kickoff", "icon", "panelspacer", "digitalclock", "panelspacer", "systemtray"])
        icon = top["widgets"][1]
        self.assertEqual(icon["localFile"]["path"], str(self.icon_file))
        self.assertIn("URL=file:///tmp/index.html", icon["localFile"]["content"])
        installed = json.loads((self.state / "tide/panels-installed.json").read_text())
        self.assertEqual(installed["mainPanel"], 73)
        self.assertIsNotNone(installed["sidePanel"])

    def test_install_puts_the_logo_into_hicolor(self):
        self.panels("install")
        apps = self.data / "icons/hicolor/scalable/apps"
        self.assertIn(PALETTE["pastel"]["teal"], (apps / "tide-logo.svg").read_text())
        self.assertIn("ColorScheme-Text", (apps / "tide-logo-symbolic.svg").read_text())
        self.assertIn("org.kde.KIconLoader.iconChanged", (self.fake_dir / "dbus.log").read_text())

    def test_reinstall_keeps_the_first_dump(self):
        self.panels("install")
        first = self.previous_file.read_text()
        self.panels("install")
        self.assertEqual(self.previous_file.read_text(), first)
        self.assertEqual(len(self.scripts("dump")), 1)
        live = [p for p in self.plasma()["panels"] if not p["removed"]]
        self.assertEqual(len(live), 3)

    def test_uninstall_removes_the_left_bar_and_restores_the_main_bar(self):
        self.panels("install")
        # Plasma kasuje plik .desktop razem z widżetem „Ikona”.
        self.icon_file.unlink()
        self.panels("uninstall")

        js = self.scripts("uninstall")[0]
        self.assertIn("org.kde.plasma.icon", js)
        self.assertIn("tideIsSideBar", js)
        live = [p for p in self.plasma()["panels"] if not p["removed"]]
        self.assertEqual([p["id"] for p in live], [73, 90])
        bar = live[0]
        by_id = {w["id"]: w for w in bar["widgets"]}
        self.assertEqual([by_id[i]["type"].rsplit(".", 1)[-1] for i in bar["layout"]],
                         ["kickoff", "icon", "panelspacer", "digitalclock", "panelspacer", "systemtray"])
        self.assertEqual(bar["height"], 36)
        self.assertTrue(self.icon_file.is_file())
        self.assertFalse((self.data / "icons/hicolor/scalable/apps/tide-logo.svg").exists())
        self.assertFalse(self.previous_file.exists())
        self.assertFalse((self.state / "tide/panels-installed.json").exists())

    def test_appmenu_module_is_enabled_only_when_needed_and_reverted(self):
        self.panels("install")
        self.panels("uninstall")
        self.assertEqual(self.kded_calls(), ["org.kde.kded6.isModuleAutoloaded appmenu"])

        self.set_appmenu(False)
        (self.fake_dir / "calls/kded.log").unlink()
        self.panels("install")
        self.assertIn("org.kde.kded6.setModuleAutoloading appmenu true", self.kded_calls())
        self.assertIn("org.kde.kded6.loadModule appmenu", self.kded_calls())
        self.panels("uninstall")
        self.assertIn("org.kde.kded6.setModuleAutoloading appmenu false", self.kded_calls())

    def test_show_prints_scripts_without_running_them(self):
        out = self.panels("show")
        self.assertIn("// tide:install", out)
        self.assertIn("// tide:order", out)
        self.assertFalse((self.fake_dir / "calls").exists())


if __name__ == "__main__":
    unittest.main()
