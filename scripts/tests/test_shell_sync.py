"""Kopia powłoki z ekranem Tide: tworzenie, odcisk, resynchronizacja, wycofanie i usuwanie.

Wszystko na tymczasowym katalogu domowym. Pakiet systemowy to atrapa (TIDE_SYSTEM_SHELL),
a greeter to skrypt, który zapisuje wywołania i udaje wczytanie albo błąd QML (TIDE_GREETER).
Na końcu test z prawdziwym kscreenlocker_greet i prawdziwym pakietem z /usr/share — poza
ekranem, bez magistrali sesji, jak w theme/lockscreen/tests.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/shell_sync.py"
REAL_SYSTEM_SHELL = Path("/usr/share/plasma/shells/org.kde.plasma.desktop")
REAL_GREETER = next((p for p in ("/usr/lib/x86_64-linux-gnu/libexec/kscreenlocker_greet",
                                 "/usr/libexec/kscreenlocker_greet") if os.access(p, os.X_OK)), None)

FAKE_GREETER = """#!/bin/sh
echo "$*" >> "{log}"
if grep -q ZEPSUTE "$3/contents/lockscreen/LockScreen.qml"; then
    echo "kscreenlocker_greet: Failed to load lockscreen QML, falling back to built-in locker"
else
    echo "qml: Tide: ekran blokady wczytany"
fi
exec sleep 30
"""


class ShellSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tide-shell-sync-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.data = self.tmp / "home/.local/share"
        self.state = self.tmp / "home/.local/state"
        self.system = self.tmp / "usr/share/plasma/shells/org.kde.plasma.desktop"
        self.copy = self.data / "plasma/shells/org.kde.plasma.desktop"
        self.tide = self.data / "plasma/shells/org.tide.desktop"
        self.stamp = self.state / "tide/shell-sync.json"
        self.log = self.tmp / "greeter.log"

        self.put(self.system / "metadata.json", json.dumps({"KPackageStructure": "Plasma/Shell",
                                                            "KPlugin": {"Id": "org.kde.plasma.desktop"},
                                                            "X-Plasma-APIVersion": "2"}))
        self.put(self.system / "contents/views/Desktop.qml", "// pulpit 6.8.1\n")
        self.put(self.system / "contents/views/Panel.qml", "// panel 6.8.1\n")
        self.put(self.system / "contents/defaults", "[Desktop]\nContainment=org.kde.plasma.folder\n")
        self.put(self.system / "contents/lockscreen/LockScreen.qml", "// ekran Plasmy\n")
        self.put(self.system / "contents/lockscreen/LockScreenUi.qml", "// ekran Plasmy UI\n")
        self.put(self.tide / "contents/lockscreen/LockScreen.qml", "// ekran Tide\n")
        self.put(self.tide / "contents/lockscreen/tide/Tokens.qml", "// tokeny\n")
        self.put(self.tide / "NOTICE", "GPL-2.0-or-later\n")

        greeter = self.tmp / "bin/kscreenlocker_greet"
        self.put(greeter, FAKE_GREETER.format(log=self.log))
        greeter.chmod(0o755)
        self.env = {**os.environ, "HOME": str(self.tmp / "home"), "XDG_DATA_HOME": str(self.data),
                    "XDG_CONFIG_DIRS": "/etc/xdg",  # bez ~/.config/kdedefaults z sesji Plasmy
                    "XDG_STATE_HOME": str(self.state), "TIDE_SYSTEM_SHELL": str(self.system),
                    "TIDE_GREETER": str(greeter)}

    @staticmethod
    def put(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def sync(self, *args):
        return subprocess.run(["/usr/bin/python3", str(SCRIPT), "sync", *args], env=self.env,
                              capture_output=True, text=True, check=True, timeout=60)

    def greeter_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def files(self, root: Path) -> dict[str, str]:
        return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}

    def stamp_result(self):
        return json.loads(self.stamp.read_text())["result"]

    def test_copy_is_system_package_with_tide_lockscreen(self):
        out = self.sync()
        self.assertIn("wynik: ok", out.stdout)
        copied, system = self.files(self.copy), self.files(self.system)
        # Wszystko poza lockscreen/ identyczne z systemem.
        for name, text in system.items():
            if not name.startswith("contents/lockscreen/"):
                self.assertEqual(copied[name], text, name)
        lockscreen = {k for k in copied if k.startswith("contents/lockscreen/")}
        self.assertEqual(lockscreen, {"contents/lockscreen/LockScreen.qml", "contents/lockscreen/tide/Tokens.qml",
                                      "contents/lockscreen/NOTICE"})
        self.assertEqual(copied["contents/lockscreen/LockScreen.qml"], "// ekran Tide\n")
        marker = json.loads(copied["tide-shell-copy.json"])
        self.assertEqual(set(copied) - set(system) - lockscreen, {"tide-shell-copy.json"})
        self.assertEqual(json.loads(self.stamp.read_text())["fingerprint"], marker["fingerprint"])
        self.assertEqual(self.stamp_result(), "ok")
        # Greeter sprawdzał kopię roboczą (poza plasma/shells), nie gotowy katalog.
        [call] = self.greeter_calls()
        self.assertTrue(call.startswith("--testing --shell "), call)
        self.assertIn(".tide-shell-staging", call)
        self.assertFalse((self.data / "plasma/.tide-shell-staging").exists())

    def test_unchanged_system_is_a_cheap_no_op(self):
        self.sync()
        before = self.copy.stat().st_ino
        start = time.monotonic()
        out = self.sync()
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertEqual(len(self.greeter_calls()), 1)
        self.assertEqual(self.copy.stat().st_ino, before)
        self.assertEqual(out.stdout, "")

    def test_plasma_upgrade_resyncs_the_copy(self):
        self.sync()
        self.put(self.system / "contents/views/Desktop.qml", "// pulpit 6.9.0\n")
        self.put(self.system / "contents/views/NewThing.qml", "// nowy plik\n")
        (self.system / "contents/views/Panel.qml").unlink()
        self.sync()
        self.assertEqual((self.copy / "contents/views/Desktop.qml").read_text(), "// pulpit 6.9.0\n")
        self.assertTrue((self.copy / "contents/views/NewThing.qml").is_file())
        self.assertFalse((self.copy / "contents/views/Panel.qml").exists())
        self.assertEqual((self.copy / "contents/lockscreen/LockScreen.qml").read_text(), "// ekran Tide\n")
        self.assertEqual(len(self.greeter_calls()), 2)

    def test_new_tide_lockscreen_resyncs_the_copy(self):
        self.sync()
        self.put(self.tide / "contents/lockscreen/LockScreen.qml", "// ekran Tide v2\n")
        self.sync()
        self.assertEqual((self.copy / "contents/lockscreen/LockScreen.qml").read_text(), "// ekran Tide v2\n")

    def test_stale_copy_restored_from_backup_is_rebuilt(self):
        self.sync()
        marker = self.copy / "tide-shell-copy.json"
        marker.write_text(json.dumps({"fingerprint": "stary"}))
        self.put(self.copy / "contents/views/Desktop.qml", "// pulpit 6.7\n")
        self.sync()
        self.assertEqual((self.copy / "contents/views/Desktop.qml").read_text(), "// pulpit 6.8.1\n")

    def test_failed_check_removes_the_copy_and_waits_for_a_change(self):
        self.sync()
        self.put(self.tide / "contents/lockscreen/LockScreen.qml", "// ZEPSUTE\n")
        out = self.sync()
        self.assertIn("wynik: fallback", out.stdout)
        self.assertFalse(self.copy.exists())  # wygrywa pakiet systemowy
        self.assertEqual(self.stamp_result(), "fallback")
        self.sync()  # to samo przy kolejnym logowaniu: bez ponownego sprawdzania
        self.assertEqual(len(self.greeter_calls()), 2)
        self.assertFalse(self.copy.exists())
        self.put(self.tide / "contents/lockscreen/LockScreen.qml", "// ekran Tide naprawiony\n")
        self.sync()
        self.assertTrue((self.copy / "contents/lockscreen/LockScreen.qml").is_file())
        self.assertEqual(self.stamp_result(), "ok")

    def test_failed_first_check_never_creates_the_copy(self):
        self.put(self.tide / "contents/lockscreen/LockScreen.qml", "// ZEPSUTE\n")
        self.sync("--force")
        self.assertFalse(self.copy.exists())
        self.assertFalse((self.data / "plasma/.tide-shell-staging").exists())

    def test_missing_greeter_means_no_copy(self):
        self.env["TIDE_GREETER"] = str(self.tmp / "nie-ma")
        self.sync("--force")
        self.assertFalse(self.copy.exists())
        self.assertEqual(self.stamp_result(), "fallback")

    def test_manual_removal_is_respected_until_reinstall(self):
        self.sync()
        shutil.rmtree(self.copy)  # sposób ratunkowy z README
        self.sync()
        self.assertFalse(self.copy.exists())
        self.assertEqual(self.stamp_result(), "removed")
        self.put(self.system / "contents/views/Desktop.qml", "// pulpit 6.9.0\n")  # aktualizacja Plasmy
        self.sync()
        self.assertFalse(self.copy.exists())
        self.sync("--force")  # make install
        self.assertTrue(self.copy.is_dir())
        self.assertEqual(self.stamp_result(), "ok")

    def test_foreign_copy_is_left_alone(self):
        self.put(self.copy / "metadata.json", "{}")
        self.sync("--force")
        subprocess.run(["/usr/bin/python3", str(SCRIPT), "remove"], env=self.env, check=True)
        self.assertEqual(self.files(self.copy), {"metadata.json": "{}"})
        self.assertEqual(self.greeter_calls(), [])

    def test_missing_system_package_removes_the_copy(self):
        self.sync()
        shutil.rmtree(self.system)
        self.sync()
        self.assertFalse(self.copy.exists())

    def test_remove_deletes_copy_and_stamp(self):
        self.sync()
        subprocess.run(["/usr/bin/python3", str(SCRIPT), "remove"], env=self.env, check=True)
        self.assertFalse(self.copy.exists())
        self.assertFalse(self.stamp.exists())
        self.assertTrue(self.tide.is_dir())  # pakiet org.tide.desktop usuwa odinstalowanie motywu

    def test_status(self):
        self.sync()
        out = subprocess.run(["/usr/bin/python3", str(SCRIPT), "status"], env=self.env, check=True,
                             capture_output=True, text=True).stdout
        self.assertIn("(Tide)", out)
        self.assertIn("ok", out)


@unittest.skipUnless(REAL_GREETER and REAL_SYSTEM_SHELL.is_dir(), "brak kscreenlocker_greet albo pakietu powłoki")
class RealGreeterSyncTest(unittest.TestCase):
    """Prawdziwy pakiet z /usr/share i prawdziwy greeter (poza ekranem): kopia działa, zepsuta znika."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tide-shell-sync-real-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.data = self.tmp / "data"
        self.copy = self.data / "plasma/shells/org.kde.plasma.desktop"
        subprocess.run(["/usr/bin/python3", str(ROOT / "theme/lockscreen/generate.py"),
                        str(ROOT / "theme/palette/mist.json"), str(self.data / "plasma/shells/org.tide.desktop")],
                       check=True, capture_output=True)
        self.env = {**os.environ, "XDG_DATA_HOME": str(self.data), "XDG_STATE_HOME": str(self.tmp / "state"),
                    "XDG_CONFIG_DIRS": "/etc/xdg"}
        for name in ("TIDE_SYSTEM_SHELL", "TIDE_GREETER"):
            self.env.pop(name, None)

    def sync(self, *args):
        return subprocess.run(["/usr/bin/python3", str(SCRIPT), "sync", *args], env=self.env,
                              capture_output=True, text=True, check=True, timeout=60).stdout

    def test_real_copy_passes_and_broken_one_falls_back(self):
        self.assertIn("wynik: ok", self.sync("--force"))
        self.assertTrue((self.copy / "contents/views/Desktop.qml").is_file())
        self.assertTrue((self.copy / "contents/lockscreen/TideClock.qml").is_file())
        lock = self.data / "plasma/shells/org.tide.desktop/contents/lockscreen/LockScreen.qml"
        with open(lock, "a") as fh:
            fh.write("\nItem { to nie jest QML\n")
        self.assertIn("wynik: fallback", self.sync())
        self.assertFalse(self.copy.exists())


if __name__ == "__main__":
    unittest.main()
