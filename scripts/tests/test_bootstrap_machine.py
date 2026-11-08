"""Instalacja na nowej maszynie (scripts/bootstrap-machine.sh): pakiety z kodu, strażnik symulacji apt/dnf
na nagranych wyjściach (Ubuntu 26.04, Fedora 44) i tryb --check, który niczego nie zmienia."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import bootstrap_packages as packages

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bootstrap"
SCRIPT = ROOT / "scripts" / "bootstrap-machine.sh"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class AptSimulationTest(unittest.TestCase):
    def test_clean_ubuntu_install_has_only_new_packages_and_one_unrelated_upgrade(self):
        plan = packages.parse_apt(fixture("apt-ubuntu-26.04.txt"))
        self.assertIn("python3-pyqt6", plan["install"])
        self.assertIn("libqt6core6t64", plan["install"])          # nowa instalacja Qt to nie podniesienie
        self.assertEqual(plan["upgrade"], [("libpcre2-8-0", "10.46-1build1", "10.46-1ubuntu0.1")])
        self.assertEqual(plan["remove"], [])
        self.assertEqual(packages.blocked(plan), [])

    def test_qt_and_frameworks_upgrades_and_removals_are_blocked(self):
        plan = packages.parse_apt(fixture("apt-qt-upgrade.txt"))
        bad = packages.blocked(plan)
        self.assertEqual(bad, ["libqt6core6t64 6.10.2+dfsg-3 → 6.10.2+dfsg-7",
                               "qml6-module-qtquick 6.10.2+dfsg-3 → 6.10.2+dfsg-7",
                               "libkf6coreaddons6 6.20.0-0ubuntu1 → 6.21.0-0ubuntu1",
                               "kf6-kio-legacy (usunięcie)"])
        # biblioteka budowana na Qt to nie Qt — jej aktualizacja nie blokuje
        self.assertIn("libpolkit-qt6-1-1", [name for name, _, _ in plan["upgrade"]])

    def test_guard_exit_codes(self):
        cases = {"apt-ubuntu-26.04.txt": 0, "apt-nothing.txt": 0, "apt-qt-upgrade.txt": 3,
                 "apt-unknown-package.txt": 4}
        for name, code in cases.items():
            with self.subTest(name):
                result = subprocess.run(["/usr/bin/python3", str(ROOT / "scripts" / "bootstrap_packages.py"),
                                         "guard", "apt"], input=fixture(name), capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)


class DnfTransactionTest(unittest.TestCase):
    def test_fedora_transaction_sections(self):
        plan = packages.parse_dnf(fixture("dnf-fedora-44.txt"))
        self.assertEqual([name for name, _, _ in plan["upgrade"]],
                         ["elfutils-libelf", "elfutils-libs", "pcre2", "pcre2-syntax"])
        self.assertIn("python3-pyqt6", plan["install"])
        self.assertIn("bubblewrap", plan["install"])
        self.assertIn("7zip", plan["install"])                    # słabe zależności też są instalacją
        self.assertNotIn("replacing", plan["install"])
        self.assertNotIn("Installing:", plan["install"])          # podsumowanie transakcji to nie pakiety
        self.assertEqual(packages.blocked(plan), [])

    def test_qt_upgrade_in_fedora_is_blocked(self):
        plan = packages.parse_dnf(fixture("dnf-qt-upgrade.txt"))
        self.assertEqual(packages.blocked(plan), ["qt6-qtbase → 0:6.11.2-3.fc44",
                                                  "kf6-kcoreaddons → 0:6.24.0-1.fc44"])

    def test_dnf4_table_header_is_not_a_package(self):
        plan = packages.parse_dnf(fixture("dnf4-qt-upgrade.txt"))
        self.assertEqual(plan["install"], ["python3-pyqt6"])
        self.assertEqual([name for name, _, _ in plan["upgrade"]], ["qt6-qtbase", "qt6-qtbase-gui"])

    def test_guard_exit_codes(self):
        for name, code in {"dnf-fedora-44.txt": 0, "dnf-nothing.txt": 0, "dnf-qt-upgrade.txt": 3,
                           "dnf4-qt-upgrade.txt": 3, "dnf-unknown-package.txt": 4}.items():
            with self.subTest(name):
                result = subprocess.run(["/usr/bin/python3", str(ROOT / "scripts" / "bootstrap_packages.py"),
                                         "guard", "dnf"], input=fixture(name), capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)


class PackagesFromCodeTest(unittest.TestCase):
    """Lista pakietów nadąża za importami okna i usługi."""

    def test_every_qtquick_import_of_the_window_has_a_package(self):
        imports = set()
        for folder in ("apps", "widgets/lib"):
            for path in (ROOT / folder).rglob("*.qml"):
                text = path.read_text(encoding="utf-8")
                if folder == "widgets/lib" and "import org.kde." in text:
                    continue  # tylko widżety Plasmy (DaemonSource, TideWindow…), okno ich nie ładuje
                imports |= set(re.findall(r"^\s*import (Qt[A-Za-z.]+)", text, re.M))
        self.assertTrue(imports)
        provided = {path.removesuffix("/qmldir").replace("/", ".") for path in packages.QMLDIRS.values()}
        self.assertEqual(sorted(imports - provided), [])

    def test_window_never_imports_plasma_only_qml(self):
        for path in (ROOT / "apps").rglob("*.qml"):
            with self.subTest(str(path.relative_to(ROOT))):
                self.assertNotIn("import org.kde.", path.read_text(encoding="utf-8"))

    def test_qml_packages_are_required_apt_packages(self):
        required = packages.names("apt", "required")
        for name in packages.QMLDIRS:
            self.assertIn(name, required)

    def test_both_managers_have_the_sandbox_and_qt_bindings(self):
        for manager in ("apt", "dnf"):
            required = packages.names(manager, "required")
            for name in ("python3-pyqt6", "bubblewrap", "socat", "git", "make", "zstd"):
                self.assertIn(name, required, manager)
            self.assertNotIn("", required)


FAKE_BUSCTL = r"""#!/usr/bin/python3
# Atrapa busctl: usługa Tide na magistrali sesji, gdy FAKE_DBUS=1. Odpowiedzi w formacie `busctl -j`.
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write("busctl " + " ".join(args) + "\n")
if os.environ.get("FAKE_DBUS") != "1":
    sys.exit(1)
if "status" in args:
    sys.exit(0)
method = args[args.index("call") + 4] if "call" in args else ""
replies = {
    "Info": {"ok": True, "apiVersion": 1, "resources": {}},
    "Snapshot": {"exists": False, "cards": [], "projects": []},
    "Status": {"search": {"enabled": os.environ.get("FAKE_SEARCH", "1") == "1"}},
    "SetSemanticSearch": {"ok": True, "error": ""},
    "UpsertTag": {"ok": True, "name": "własne", "changed": False},
}
if method not in replies:
    sys.exit(1)
print(json.dumps({"type": "s", "data": [json.dumps(replies[method])]}))
"""

FAKE_WINDOW = r"""#!/usr/bin/python3
# Atrapa okna Tide: zapisuje argumenty i pusty zrzut z --grab.
import os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write("tide.py " + " ".join(args) + "\n")
open(args[args.index("--grab") + 1], "wb").write(b"png")
"""


class ScriptTest(unittest.TestCase):
    """Skrypt na udawanym systemie: atrapy menedżera pakietów, sudo, make, systemctl i busctl."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        self.bin = base / "bin"
        self.bin.mkdir()
        self.calls = base / "calls.log"
        self.calls.write_text("")
        self.os_release = base / "os-release"
        self.meminfo = base / "meminfo"
        self.system("ubuntu", "26.04", "Kubuntu 26.04 LTS")
        self.memory(32)
        self.simulation = FIXTURES / "apt-qt-upgrade.txt"
        self.plasma = "6.6.4"
        self.dbus = False
        self.search = True
        fakes = {
            # Każde wywołanie zapisujemy; sudo i make nie mogą paść w trybie --check.
            "sudo": "exit 1",
            "make": "exit 0",
            "apt-get": f'[ "$1" = -s ] && cat "{self.simulation}" && exit 0; exit 1',
            "dpkg-query": "exit 1",
            "rpm": "exit 1",
            "plasmashell": 'echo "plasmashell $FAKE_PLASMA"',
            "systemctl": '[ "$FAKE_DBUS" = 1 ] || exit 1; case "$*" in *is-enabled*) echo enabled ;; esac; exit 0',
        }
        for name, body in fakes.items():
            path = self.bin / name
            path.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.calls}"\n{body}\n')
            path.chmod(0o755)
        busctl = self.bin / "busctl"
        busctl.write_text(FAKE_BUSCTL)
        busctl.chmod(0o755)

    def system(self, os_id, version, name):
        like = "debian" if os_id == "ubuntu" else ""
        self.os_release.write_text(f'ID={os_id}\nID_LIKE={like}\nVERSION_ID="{version}"\nPRETTY_NAME="{name}"\n')

    def memory(self, gb):
        self.meminfo.write_text(f"MemTotal:       {gb * 1024 * 1024 - 300000} kB\nMemFree:        1000 kB\n")

    def install_window(self):
        window = self.home / ".local/share/tide/tide-window/tide.py"
        window.parent.mkdir(parents=True)
        window.write_text(FAKE_WINDOW)
        window.chmod(0o755)

    def run_script(self, *args):
        env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "LANG": "C.UTF-8",
               "TIDE_BOOTSTRAP_OS_RELEASE": str(self.os_release), "TIDE_BOOTSTRAP_MEMINFO": str(self.meminfo),
               "FAKE_CALLS": str(self.calls), "FAKE_PLASMA": self.plasma, "FAKE_DBUS": "1" if self.dbus else "0",
               "FAKE_SEARCH": "1" if self.search else "0"}
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env,
                              stdin=subprocess.DEVNULL, timeout=300)

    def calls_of(self, *names):
        return [c for c in self.calls.read_text().splitlines() if c.split(" ", 1)[0] in names]

    def test_check_diagnoses_without_sudo_make_or_writes(self):
        result = self.run_script("--check")
        calls = self.calls.read_text().splitlines()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.calls_of("sudo", "make"), [])
        self.assertTrue([c for c in calls if c.startswith("apt-get -s install ")], calls)
        self.assertIn("Kubuntu 26.04 LTS, menedżer pakietów: apt", result.stdout)
        self.assertIn("KDE Plasma 6.6.4", result.stdout)
        # Symulacja z podniesieniem Qt: strażnik mówi, co by zmieniła, a --check tylko to zgłasza.
        self.assertIn("libqt6core6t64 6.10.2+dfsg-3 → 6.10.2+dfsg-7", result.stderr)
        self.assertIn("symulacja apt nie przeszła", result.stdout)
        self.assertIn("Instalacja: bash scripts/bootstrap-machine.sh", result.stdout)
        # --check nie zakłada katalogu projektów ani niczego w katalogu domowym.
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [])

    def test_plasma_5_is_missing_not_a_warning(self):
        self.plasma = "5.27.11"
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 2)
        self.assertIn("BRAK: KDE Plasma 5.27.11 — Tide wymaga Plasmy 6", result.stdout)

    def test_install_on_plasma_5_changes_nothing(self):
        self.plasma = "5.27.11"
        result = self.run_script("--yes")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("nic nie zainstalowano", result.stdout)
        self.assertEqual(self.calls_of("sudo", "make", "apt-get"), [])
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [])

    def test_kubuntu_older_than_26_04_is_refused(self):
        self.system("ubuntu", "24.04", "Ubuntu 24.04.3 LTS")
        result = self.run_script("--yes")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("Tide wymaga Kubuntu 26.04 lub nowszego", result.stdout)
        self.assertEqual(self.calls_of("sudo", "make", "apt-get"), [])

    def test_low_memory_turns_low_power_on_and_check_only_reports_search(self):
        self.memory(16)
        self.dbus = True
        self.install_window()
        result = self.run_script("--check")
        self.assertIn("pamięć: 16 GB — tryb słabszej maszyny", result.stdout)
        self.assertIn("SetSemanticSearch s off", result.stdout)        # podpowiedź, nie wywołanie
        self.assertEqual([c for c in self.calls_of("busctl") if "SetSemanticSearch" in c], [])
        windows = self.calls_of("tide.py")
        self.assertEqual(len(windows), 1, windows)
        self.assertIn("--view board", windows[0])
        self.assertIn("QML się ładuje (widoki: board)", result.stdout)

    def test_enough_memory_checks_every_view(self):
        self.dbus = True
        self.install_window()
        result = self.run_script("--check")
        self.assertIn("pamięć: 32 GB", result.stdout)
        self.assertNotIn("tryb słabszej maszyny", result.stdout)
        self.assertEqual(len(self.calls_of("tide.py")), 7)

    def test_low_power_install_turns_semantic_search_off(self):
        self.dbus = True
        self.install_window()
        result = self.run_script("--low-power", "--yes", "--no-packages", "--no-board")
        calls = [c for c in self.calls_of("busctl") if "SetSemanticSearch" in c]
        self.assertEqual(len(calls), 1, result.stdout + result.stderr)
        self.assertTrue(calls[0].endswith("/Models org.tide.Daemon.Models SetSemanticSearch s off"), calls)
        self.assertIn("wyłączono wyszukiwanie po znaczeniu", result.stdout)
        self.assertEqual(len(self.calls_of("tide.py")), 1)

    def test_low_power_with_search_already_off_changes_nothing(self):
        self.dbus = True
        self.install_window()
        self.search = False
        result = self.run_script("--low-power", "--yes", "--no-packages", "--no-board")
        self.assertEqual([c for c in self.calls_of("busctl") if "SetSemanticSearch" in c], [])
        self.assertIn("wyszukiwanie po znaczeniu wyłączone", result.stdout)

    def test_unknown_argument_is_refused(self):
        result = subprocess.run(["bash", str(SCRIPT), "--force"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Nieznany argument", result.stderr)


if __name__ == "__main__":
    unittest.main()
