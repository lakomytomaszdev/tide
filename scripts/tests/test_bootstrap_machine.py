"""Instalacja na nowej maszynie (scripts/bootstrap-machine.sh): pakiety z kodu, strażnik symulacji apt/dnf
na nagranych wyjściach (Ubuntu 26.04, Fedora 44) i tryb --check, który niczego nie zmienia."""
import os
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

    def test_guard_exit_codes(self):
        for name, code in {"dnf-fedora-44.txt": 0, "dnf-nothing.txt": 0, "dnf-qt-upgrade.txt": 3}.items():
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


class CheckModeTest(unittest.TestCase):
    """--check na udawanym Kubuntu: diagnoza, symulacja apt bez sudo, żadnego make ani sudo."""

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
        (base / "os-release").write_text('ID=ubuntu\nID_LIKE=debian\nPRETTY_NAME="Kubuntu 26.04 LTS"\n')
        self.os_release = base / "os-release"
        self.simulation = FIXTURES / "apt-qt-upgrade.txt"
        fakes = {
            # Każde wywołanie zapisujemy; sudo i make nie mogą paść w trybie --check.
            "sudo": "exit 1",
            "make": "exit 1",
            "apt-get": f'[ "$1" = -s ] && cat "{self.simulation}" && exit 0; exit 1',
            "dpkg-query": "exit 1",
            "rpm": "exit 1",
            "plasmashell": 'echo "plasmashell 6.6.4"',
            "systemctl": "exit 1",
            "busctl": "exit 1",
        }
        for name, body in fakes.items():
            path = self.bin / name
            path.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.calls}"\n{body}\n')
            path.chmod(0o755)

    def run_check(self):
        env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "LANG": "C.UTF-8",
               "TIDE_BOOTSTRAP_OS_RELEASE": str(self.os_release)}
        return subprocess.run(["bash", str(SCRIPT), "--check"], capture_output=True, text=True, env=env,
                              stdin=subprocess.DEVNULL, timeout=300)

    def test_check_diagnoses_without_sudo_make_or_writes(self):
        result = self.run_check()
        calls = self.calls.read_text().splitlines()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual([c for c in calls if c.startswith(("sudo", "make"))], [])
        self.assertTrue([c for c in calls if c.startswith("apt-get -s install ")], calls)
        self.assertIn("Kubuntu 26.04 LTS, menedżer pakietów: apt", result.stdout)
        self.assertIn("KDE Plasma 6.6.4", result.stdout)
        # Symulacja z podniesieniem Qt: strażnik mówi, co by zmieniła, a --check tylko to zgłasza.
        self.assertIn("libqt6core6t64 6.10.2+dfsg-3 → 6.10.2+dfsg-7", result.stderr)
        self.assertIn("symulacja apt nie przeszła", result.stdout)
        self.assertIn("Instalacja: bash scripts/bootstrap-machine.sh", result.stdout)
        # --check nie zakłada katalogu projektów ani niczego w katalogu domowym.
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [])

    def test_unknown_argument_is_refused(self):
        result = subprocess.run(["bash", str(SCRIPT), "--force"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Nieznany argument", result.stderr)


if __name__ == "__main__":
    unittest.main()
