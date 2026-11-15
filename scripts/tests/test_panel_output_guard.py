"""Strażnik układu wyjść (PT-493): przy podłączonym, ale wyłączonym wyjściu z układu Tide (DP-2, HDMI-A-1)
silnik i instalatory paneli nie uruchamiają żadnego skryptu plasmashell. Wyłączenie czytamy z `kscreen-doctor -j`.

Funkcja strażnika jest czysta (odczyt w argumencie). Instalatory biegną jako procesy z atrapami kscreen-doctor
i qdbus6 w PATH — nic nie dotyka działającego pulpitu ani prawdziwego narzędzia.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.tests import fake_plasmashell as fake

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import activity_panels as ap  # noqa: E402

BOTH_ENABLED = fake.kscreen_json()
DP2_DISABLED = fake.kscreen_json(disabled=("DP-2",))
HDMI_DISCONNECTED = fake.kscreen_json(disconnected=("HDMI-A-1",))
NOTE = "Panele pominięte: wyłączone wyjście DP-2 (włącz monitor i uruchom ponownie)"


def reader(value):
    def read(argv):
        assert argv == ["kscreen-doctor", "-j"], argv
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value)
    return read


class DisabledOutputsTest(unittest.TestCase):
    def test_both_outputs_enabled(self):
        self.assertEqual(ap.disabled_outputs(reader(BOTH_ENABLED)), [])

    def test_a_connected_but_disabled_output_is_reported(self):
        self.assertEqual(ap.disabled_outputs(reader(DP2_DISABLED)), ["DP-2"])

    def test_a_disconnected_output_is_not_reported(self):
        self.assertEqual(ap.disabled_outputs(reader(HDMI_DISCONNECTED)), [])

    def test_both_disabled_in_the_order_main_then_side(self):
        both = fake.kscreen_json(disabled=("DP-2", "HDMI-A-1"))
        self.assertEqual(ap.disabled_outputs(reader(both)), ["DP-2", "HDMI-A-1"])

    def test_outputs_outside_the_layout_are_ignored(self):
        data = {"outputs": [*BOTH_ENABLED["outputs"], {"name": "HDMI-A-2", "connected": True, "enabled": False}]}
        self.assertEqual(ap.disabled_outputs(reader(data)), [])

    def test_unreadable_output_raises_value_error_and_guard_turns_it_into_a_note(self):
        for failure in (subprocess.CalledProcessError(1, "kscreen-doctor"), FileNotFoundError("kscreen-doctor"),
                        "nie json", "[]", "{}"):
            with self.subTest(failure=failure):
                with self.assertRaises(ValueError):
                    ap.disabled_outputs(reader(failure))
                disabled, note = ap.guard(reader(failure))
                self.assertEqual(disabled, [])
                self.assertIn("nie udało się odczytać układu wyjść", note)

    def test_skipped_note_names_the_output(self):
        self.assertEqual(ap.skipped_note(["DP-2"]), "wyłączone wyjście z układu Tide: DP-2 — panele pominięte")


@unittest.skipUnless(fake.available(), "Brak qml6 do uruchomienia skryptów")
class InstallersWithDisabledOutputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.data = self.home / ".local/share"
        self.state = self.home / ".local/state"
        self.fake_dir = base / "fake"
        self.fake_dir.mkdir()
        (self.home / ".config").mkdir(parents=True)
        (self.fake_dir / "plasma.json").write_text(json.dumps(fake.desktop_like_this_one()))
        (self.fake_dir / "kded.json").write_text(json.dumps({"appmenu": True}))
        bin_dir = base / "bin"
        bin_dir.mkdir()
        (bin_dir / "qdbus6").symlink_to(ROOT / "scripts/tests/fake_qdbus6.py")
        fake.link_kscreen_doctor(bin_dir)
        (bin_dir / "dbus-send").write_text(f'#!/bin/sh\necho "dbus-send $*" >> "{self.fake_dir}/dbus.log"\n')
        (bin_dir / "dbus-send").chmod(0o755)
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_CONFIG_DIRS": "/etc/xdg",
            "XDG_DATA_HOME": str(self.data),
            "XDG_STATE_HOME": str(self.state),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TIDE_FAKE_DIR": str(self.fake_dir),
            "TIDE_PANEL_SETTLE": "0",
        }
        for widget in ("org.tide.sidebar", "org.tide.work", "org.tide.system"):
            (self.data / "plasma/plasmoids" / widget).mkdir(parents=True)

    def kscreen(self, value):
        (self.fake_dir / "kscreen.json").write_text(json.dumps(value))

    def installer(self, script, action):
        return subprocess.run(["/usr/bin/python3", str(ROOT / "scripts" / script), action],
                              env=self.env, capture_output=True, text=True)

    def plasmashell_scripts(self):
        calls = self.fake_dir / "calls"
        return sorted(p.name for p in calls.glob("*.js")) if calls.exists() else []

    def state_files(self):
        directory = self.state / "tide"
        return sorted(p.name for p in directory.iterdir()) if directory.exists() else []

    def test_every_installer_path_skips_with_a_note_and_exit_code_0(self):
        self.kscreen(DP2_DISABLED)
        for script in ("panels.py", "sidebar.py", "dock.py"):
            for action in ("install", "uninstall"):
                with self.subTest(script=script, action=action):
                    done = self.installer(script, action)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertIn(NOTE, done.stdout)
        self.assertEqual(self.plasmashell_scripts(), [])
        self.assertEqual(self.state_files(), [])  # ani panels-previous.json, ani *-installed.json
        self.assertFalse((self.data / "icons").exists())
        self.assertFalse((self.fake_dir / "calls/kded.log").exists())

    def test_with_both_outputs_enabled_install_still_runs(self):
        for script, tag, state_file in (("panels.py", "install", "panels-installed.json"),
                                        ("sidebar.py", "sidebar", "sidebar-installed.json"),
                                        ("dock.py", "dock", "dock-installed.json")):
            with self.subTest(script=script):
                done = self.installer(script, "install")
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertNotIn("Panele pominięte", done.stdout)
                self.assertTrue(any(name.endswith(f"-{tag}.js") for name in self.plasmashell_scripts()))
                self.assertIn(state_file, self.state_files())

    def test_unreadable_kscreen_gives_a_note_and_the_usual_work(self):
        (self.fake_dir / "kscreen.fail").write_text("")
        done = self.installer("panels.py", "install")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Uwaga: nie udało się odczytać układu wyjść", done.stdout)
        self.assertNotIn("Panele pominięte", done.stdout)
        self.assertIn("panels-installed.json", self.state_files())

    def test_a_disconnected_output_does_not_block_the_installer(self):
        self.kscreen(HDMI_DISCONNECTED)
        done = self.installer("panels.py", "install")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("Panele pominięte", done.stdout)
        self.assertIn("panels-installed.json", self.state_files())


if __name__ == "__main__":
    unittest.main()
