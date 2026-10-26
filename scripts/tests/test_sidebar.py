"""Instalacja i odinstalowanie paska bocznego Tide na tymczasowym katalogu domowym.

qdbus6 i dbus-send są atrapami (fake_qdbus6.py): skrypty plasmashell wykonują się na atrapie
API w qml6, a wywołania KGlobalAccel trafiają do dziennika. Żadne narzędzie nie dotyka
działającego pulpitu ani prawdziwych skrótów.
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
ALL = ["org.tide.sidebar", "org.tide.agents", "org.tide.system", "org.tide.worktrees", "org.tide.limits"]


@unittest.skipUnless(fake.available(), "Brak qml6 do uruchomienia skryptów")
class SidebarInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.config = self.home / ".config"
        self.data = self.home / ".local/share"
        self.state = self.home / ".local/state"
        self.fake_dir = base / "fake"
        self.fake_dir.mkdir()
        self.config.mkdir(parents=True)
        (self.fake_dir / "plasma.json").write_text(json.dumps(fake.desktop_like_this_one()))
        self.plasmoids(ALL)

        bin_dir = base / "bin"
        bin_dir.mkdir()
        (bin_dir / "qdbus6").symlink_to(ROOT / "scripts/tests/fake_qdbus6.py")
        (bin_dir / "dbus-send").write_text(f'#!/bin/sh\necho "dbus-send $*" >> "{self.fake_dir}/dbus.log"\n')
        (bin_dir / "dbus-send").chmod(0o755)
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_CONFIG_DIRS": "/etc/xdg",  # bez ~/.config/kdedefaults z sesji Plasmy
            "XDG_DATA_HOME": str(self.data),
            "XDG_STATE_HOME": str(self.state),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TIDE_FAKE_DIR": str(self.fake_dir),
            "TIDE_PANEL_SETTLE": "0",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def plasmoids(self, ids):
        for widget in ids:
            (self.data / "plasma/plasmoids" / widget).mkdir(parents=True, exist_ok=True)

    def sidebar(self, action):
        return subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/sidebar.py"), action],
                              env=self.env, check=True, capture_output=True, text=True).stdout

    def plasma(self):
        return json.loads((self.fake_dir / "plasma.json").read_text())

    def sidebars(self):
        return [p for p in self.plasma()["panels"]
                if not p["removed"] and p["config"].get("/Tide", {}).get("role") == "sidebar"]

    def layout(self, panel):
        by_id = {w["id"]: w for w in panel["widgets"]}
        return [by_id[i]["type"] for i in panel["layout"]]

    @property
    def installed_file(self):
        return self.state / "tide/sidebar-installed.json"

    def thickness(self, panel_id):
        return subprocess.run(["kreadconfig6", "--file", "plasmashellrc", "--group", "PlasmaViews",
                               "--group", f"Panel {panel_id}", "--group", "Defaults", "--key", "thickness"],
                              env=self.env, capture_output=True, text=True, check=True).stdout.strip()

    def test_install_builds_the_sidebar_and_persists_its_width(self):
        out = self.sidebar("install")
        [bar] = self.sidebars()
        self.assertEqual((bar["screen"], bar["location"], bar["hiding"]), (1, "left", "dodgewindows"))
        self.assertEqual(self.layout(bar), ALL)
        # Plasma nie zapisuje grubości ustawionej skryptem — szerokość trafia do plasmashellrc.
        self.assertEqual(self.thickness(bar["id"]), str(PALETTE["size"]["sidebar"]))
        installed = json.loads(self.installed_file.read_text())
        self.assertEqual(installed["sidebar"], bar["id"])
        self.assertEqual(installed["shortcut"], "Meta+S")
        self.assertIn("Meta+S", out)

    def test_only_installed_widgets_are_added(self):
        for widget in ("org.tide.system", "org.tide.limits"):
            (self.data / "plasma/plasmoids" / widget).rmdir()
        self.sidebar("install")
        [bar] = self.sidebars()
        self.assertEqual(self.layout(bar), ["org.tide.sidebar", "org.tide.agents", "org.tide.worktrees"])

    def test_reinstall_is_idempotent(self):
        self.sidebar("install")
        first = json.loads(self.installed_file.read_text())
        self.sidebar("install")
        [bar] = self.sidebars()
        self.assertEqual(bar["id"], first["sidebar"])
        self.assertEqual(self.layout(bar), ALL)
        self.assertEqual(json.loads(self.installed_file.read_text())["header"], first["header"])
        self.assertEqual(len([p for p in self.plasma()["panels"] if not p["removed"]]), 3)

    def test_taken_shortcut_is_detected_and_not_forced(self):
        (self.config / "kglobalshortcutsrc").write_text(
            "[kwin]\nSomething=Meta+S,none,Coś innego\n")
        out = self.sidebar("install")
        self.assertIn("Meta+S ma już przypisane", out)
        self.assertIn("kwin/Something (Coś innego)", out)
        self.assertEqual(self.plasma().get("shortcuts", {}), {})
        self.assertEqual(json.loads(self.installed_file.read_text())["shortcut"], "")

    def test_own_shortcut_from_a_previous_install_is_not_a_conflict(self):
        self.sidebar("install")
        header = json.loads(self.installed_file.read_text())["header"]
        (self.config / "kglobalshortcutsrc").write_text(
            f"[plasmashell]\nactivate widget {header}=Meta+S,none,Aktywuj widżet\n")
        out = self.sidebar("install")
        self.assertNotIn("ma już przypisane", out)

    def test_uninstall_removes_the_sidebar_and_unregisters_the_shortcut(self):
        self.sidebar("install")
        header = json.loads(self.installed_file.read_text())["header"]
        self.sidebar("uninstall")
        self.assertEqual(self.sidebars(), [])
        self.assertEqual(self.plasma().get("shortcuts"), {})
        self.assertIn(f"string:activate widget {header}", (self.fake_dir / "dbus.log").read_text())
        self.assertFalse(self.installed_file.exists())
        self.assertEqual([p["id"] for p in self.plasma()["panels"] if not p["removed"]], [73, 90])

    def test_single_monitor_gets_no_sidebar(self):
        state = fake.desktop_like_this_one(screens=("DP-2",))
        (self.fake_dir / "plasma.json").write_text(json.dumps(state))
        out = self.sidebar("install")
        self.assertIn("nie ma drugiego monitora", out)
        self.assertEqual(self.sidebars(), [])
        self.assertFalse(self.installed_file.exists())

    def test_show_prints_the_script_without_running_it(self):
        out = self.sidebar("show")
        self.assertIn("// tide:sidebar", out)
        self.assertIn('"shortcut": "Meta+S"', out)
        self.assertIn(f'"width": {PALETTE["size"]["sidebar"]}', out)
        self.assertFalse((self.fake_dir / "calls").exists())


class MakefileTest(unittest.TestCase):
    def test_sidebar_runs_after_the_widgets_and_is_removed_before_them(self):
        out = subprocess.run(["make", "-pn", "-C", str(ROOT), "help"], capture_output=True, text=True).stdout
        late = next(line for line in out.splitlines() if line.startswith("LATE_INSTALL_TARGETS :="))
        installs = next(line for line in out.splitlines() if line.startswith("INSTALL_TARGETS :="))
        uninstalls = next(line for line in out.splitlines() if line.startswith("UNINSTALL_TARGETS :="))
        self.assertIn("install-sidebar", late.split())
        self.assertIn("install-widget-sidebar", installs.split())
        order = uninstalls.split()
        self.assertLess(order.index("uninstall-sidebar"), order.index("uninstall-widget-sidebar"))
        self.assertLess(order.index("uninstall-sidebar"), order.index("uninstall-widget-agents"))


if __name__ == "__main__":
    unittest.main()
