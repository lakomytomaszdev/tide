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


class ShellSyncBase(unittest.TestCase):
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



class ShellSyncTest(ShellSyncBase):
    def test_copy_is_system_package_with_tide_lockscreen(self):
        out = self.sync()
        self.assertIn("wynik: ok", out.stdout)
        copied, system = self.files(self.copy), self.files(self.system)
        # Wszystko poza lockscreen/ identyczne z systemem.
        for name, text in system.items():
            if not name.startswith("contents/lockscreen/"):
                self.assertEqual(copied[name], text, name)
        lockscreen = {k for k in copied if k.startswith("contents/lockscreen/")}
        # Znacznik wariantu (decyzja 77) jedzie z katalogiem ekranu przy przełączaniu wariantów.
        self.assertEqual(lockscreen, {"contents/lockscreen/LockScreen.qml", "contents/lockscreen/tide/Tokens.qml",
                                      "contents/lockscreen/NOTICE", "contents/lockscreen/.tide-lock-variant"})
        self.assertEqual(copied["contents/lockscreen/.tide-lock-variant"], "mist\n")
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


class LockVariantTest(ShellSyncBase):
    """Warianty ekranu blokady (Tide i Stream) w jednej kopii powłoki i tanie przełączanie między nimi."""

    def setUp(self):
        super().setUp()
        self.stream = self.data / "tide/lockscreens/streamer"
        self.put(self.stream / "contents/lockscreen/LockScreen.qml", "// ekran Stream\n")
        self.put(self.stream / "contents/lockscreen/tide/Tokens.qml", "// tokeny Stream\n")
        self.put(self.stream / "NOTICE", "GPL-2.0-or-later\n")
        self.active = self.copy / "contents/lockscreen"

    def variant(self, *args, check=True):
        return subprocess.run(["/usr/bin/python3", str(SCRIPT), "variant", *args], env=self.env,
                              capture_output=True, text=True, check=check, timeout=30)

    def test_copy_holds_every_variant_and_mist_is_active(self):
        self.sync("--force")
        self.assertEqual((self.active / "LockScreen.qml").read_text(), "// ekran Tide\n")
        inactive = self.copy / "contents/lockscreen-variants/streamer"
        self.assertEqual((inactive / "LockScreen.qml").read_text(), "// ekran Stream\n")
        self.assertEqual((inactive / ".tide-lock-variant").read_text(), "streamer\n")
        self.assertTrue((inactive / "NOTICE").is_file())
        # Greeter sprawdził oba warianty w kopii roboczej.
        self.assertEqual(len(self.greeter_calls()), 2)
        self.assertEqual(self.variant().stdout, "mist\n")

    def test_switch_is_cheap_atomic_and_idempotent(self):
        self.sync("--force")
        inode = self.copy.stat().st_ino
        start = time.monotonic()
        self.variant("streamer")
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertEqual((self.active / "LockScreen.qml").read_text(), "// ekran Stream\n")
        self.assertEqual((self.copy / "contents/lockscreen-variants/mist/LockScreen.qml").read_text(), "// ekran Tide\n")
        self.assertEqual(self.variant().stdout, "streamer\n")
        self.variant("streamer")  # drugi raz: bez zmian
        self.assertEqual(self.variant().stdout, "streamer\n")
        self.assertEqual(sorted(p.name for p in (self.copy / "contents/lockscreen-variants").iterdir()), ["mist"])
        self.variant("mist")
        self.assertEqual((self.active / "LockScreen.qml").read_text(), "// ekran Tide\n")
        # Bez przebudowy: ta sama kopia, żadnego sprawdzania w greeterze.
        self.assertEqual(self.copy.stat().st_ino, inode)
        self.assertEqual(len(self.greeter_calls()), 2)
        self.assertEqual(self.sync().stdout, "")  # odcisk źródeł bez zmian

    def test_selection_survives_a_rebuild(self):
        self.sync("--force")
        self.variant("streamer")
        self.put(self.system / "contents/views/Desktop.qml", "// pulpit 6.9.0\n")  # aktualizacja Plasmy
        self.sync()
        self.assertEqual((self.copy / "contents/views/Desktop.qml").read_text(), "// pulpit 6.9.0\n")
        self.assertEqual(self.variant().stdout, "streamer\n")
        self.sync("--force")  # make install
        self.assertEqual(self.variant().stdout, "streamer\n")

    def test_new_variant_source_resyncs_the_copy(self):
        self.sync("--force")
        self.put(self.stream / "contents/lockscreen/tide/Tokens.qml", "// tokeny Stream v2\n")
        self.sync()
        self.assertEqual((self.copy / "contents/lockscreen-variants/streamer/tide/Tokens.qml").read_text(),
                         "// tokeny Stream v2\n")

    def test_broken_variant_is_left_out_and_mist_stays(self):
        self.put(self.stream / "contents/lockscreen/LockScreen.qml", "// ZEPSUTE\n")
        out = self.sync("--force")
        self.assertIn("wynik: ok", out.stdout)
        self.assertEqual((self.active / "LockScreen.qml").read_text(), "// ekran Tide\n")
        self.assertFalse((self.copy / "contents/lockscreen-variants/streamer").exists())
        failed = self.variant("streamer", check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.variant().stdout, "mist\n")

    def test_active_variant_that_breaks_falls_back_to_mist(self):
        self.sync("--force")
        self.variant("streamer")
        self.put(self.stream / "contents/lockscreen/LockScreen.qml", "// ZEPSUTE\n")
        self.sync()
        self.assertEqual(self.variant().stdout, "mist\n")
        self.assertEqual((self.active / "LockScreen.qml").read_text(), "// ekran Tide\n")

    def test_removed_variant_source_drops_it_from_the_copy(self):
        self.sync("--force")
        self.variant("streamer")
        shutil.rmtree(self.stream)
        self.sync()
        self.assertEqual(self.variant().stdout, "mist\n")
        self.assertFalse((self.copy / "contents/lockscreen-variants").exists())

    def test_without_a_copy_there_is_no_tide_variant(self):
        # Bez kopii Tide blokada to ekran Plasmy z systemu — żaden wariant Tide.
        self.assertEqual(self.variant().stdout, "\n")
        self.assertNotEqual(self.variant("mist", check=False).returncode, 0)
        self.assertNotEqual(self.variant("streamer", check=False).returncode, 0)
        self.assertFalse(self.copy.exists())

    def test_interrupted_switch_is_read_and_repaired(self):
        # Przerwa między atomową zamianą a uporządkowaniem nazw: katalog „streamer” ma w sobie Mist.
        self.sync("--force")
        variants = self.copy / "contents/lockscreen-variants"
        self.active.rename(self.tmp / "swap")
        (variants / "streamer").rename(self.active)
        (self.tmp / "swap").rename(variants / "streamer")
        self.assertEqual(self.variant().stdout, "streamer\n")
        self.variant("mist")
        self.assertEqual((self.active / "LockScreen.qml").read_text(), "// ekran Tide\n")
        self.assertEqual(sorted(p.name for p in variants.iterdir()), ["streamer"])
        self.assertEqual((variants / "streamer/.tide-lock-variant").read_text(), "streamer\n")

    def test_swap_interrupted_after_the_first_rename_is_restored(self):
        # Zamiana przez nazwę tymczasową (system plików bez RENAME_EXCHANGE) przerwana po
        # pierwszym rename(): aktywnego katalogu nie ma, jest lockscreen.tide-swap.
        self.sync("--force")
        self.active.rename(self.active.with_name("lockscreen.tide-swap"))
        self.assertEqual(self.variant().stdout, "\n")  # bez aktywnego ekranu — żaden wariant
        self.variant("streamer")
        self.assertEqual((self.active / "LockScreen.qml").read_text(), "// ekran Stream\n")
        self.assertEqual((self.copy / "contents/lockscreen-variants/mist/LockScreen.qml").read_text(),
                         "// ekran Tide\n")
        self.assertFalse(self.active.with_name("lockscreen.tide-swap").exists())

    def test_swap_interrupted_after_the_second_rename_is_restored_by_sync(self):
        self.sync("--force")
        variants = self.copy / "contents/lockscreen-variants"
        self.active.rename(self.active.with_name("lockscreen.tide-swap"))
        (variants / "streamer").rename(self.active)
        out = self.sync()
        # Bez przebudowy: tylko dokończona zamiana (jedna linia w dzienniku).
        self.assertEqual(out.stdout, "tide-shell-sync: dokończono przerwane przełączenie wariantu ekranu blokady.\n")
        self.assertEqual(len(self.greeter_calls()), 2)
        self.assertEqual(self.variant().stdout, "streamer\n")
        self.assertEqual((variants / "mist/.tide-lock-variant").read_text(), "mist\n")
        self.assertFalse(self.active.with_name("lockscreen.tide-swap").exists())

    def test_missing_active_lockscreen_is_rebuilt(self):
        self.sync("--force")
        shutil.rmtree(self.active)
        self.sync()
        self.assertEqual((self.active / "LockScreen.qml").read_text(), "// ekran Tide\n")
        self.assertEqual(len(self.greeter_calls()), 4)

    def test_switch_waits_for_a_running_sync(self):
        import fcntl
        self.sync("--force")
        lock = self.state / "tide/shell-sync.lock"
        with open(lock, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            proc = subprocess.Popen(["/usr/bin/python3", str(SCRIPT), "variant", "streamer"], env=self.env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(1.0)
            self.assertIsNone(proc.poll())  # czeka na blokadę
            self.assertEqual((self.active / "LockScreen.qml").read_text(), "// ekran Tide\n")
            fcntl.flock(fh, fcntl.LOCK_UN)
        out, _ = proc.communicate(timeout=30)
        self.assertEqual(out, "streamer\n")

    def test_filesystem_error_is_reported_as_a_lock_variant_error(self):
        self.sync("--force")
        contents = self.copy / "contents"
        contents.chmod(0o555)
        self.addCleanup(contents.chmod, 0o755)
        failed = self.variant("streamer", check=False)
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn("Traceback", failed.stderr + failed.stdout)
        contents.chmod(0o755)
        self.assertEqual(self.variant().stdout, "mist\n")

    def test_differing_files_get_distinct_modification_times(self):
        # Pamięć podręczna QML greetera rozpoznaje plik po ścieżce i czasie modyfikacji, a po
        # przełączeniu ta sama ścieżka (tide/Tokens.qml) ma inną treść.
        stamp = 1_700_000_000
        for root in (self.tide, self.stream):
            os.utime(root / "contents/lockscreen/tide/Tokens.qml", (stamp, stamp))
        self.sync("--force")
        mist = (self.active / "tide/Tokens.qml").stat().st_mtime_ns
        stream = (self.copy / "contents/lockscreen-variants/streamer/tide/Tokens.qml").stat().st_mtime_ns
        self.assertNotEqual(mist // 1_000_000, stream // 1_000_000)


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

    def test_real_stream_variant_loads_after_the_switch(self):
        # Ekran Stream z palety Streamer obok Tide: oba sprawdzone przy składaniu kopii, a po
        # przełączeniu prawdziwy greeter wczytuje z kopii ekran Stream (tokeny Streamer).
        subprocess.run(["/usr/bin/python3", str(ROOT / "theme/lockscreen/generate.py"), "streamer",
                        str(self.data / "tide/lockscreens/streamer")], check=True, capture_output=True)
        self.assertIn("wynik: ok", self.sync("--force"))
        self.assertTrue((self.copy / "contents/lockscreen-variants/streamer/TideClock.qml").is_file())
        subprocess.run(["/usr/bin/python3", str(SCRIPT), "variant", "streamer"], env=self.env, check=True,
                       capture_output=True)
        self.assertIn('"Streamer"', (self.copy / "contents/lockscreen/tide/Tokens.qml").read_text())
        code = "import sys; sys.path.insert(0, sys.argv[1]); from scripts import shell_sync as s; " \
               "ok, out = s.check(s.COPY); print('OK' if ok else out)"
        result = subprocess.run(["/usr/bin/python3", "-c", code, str(ROOT)], env=self.env, check=True,
                                capture_output=True, text=True, timeout=60).stdout
        self.assertEqual(result.strip(), "OK")


if __name__ == "__main__":
    unittest.main()
