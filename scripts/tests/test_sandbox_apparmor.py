"""Profil AppArmor dla bwrap (PT-75): treść profilu i odczyt stanu przez scripts/sandbox/check.py, bez sudo.

Stan systemu jest udawany: TIDE_SANDBOX_ROOT to korzeń z /etc/apparmor.d i /proc/sys, a próby
uruchomienia bwrap i unshare idą przez atrapy (TIDE_SANDBOX_BWRAP, TIDE_SANDBOX_UNSHARE), które
wypisują etykietę AppArmor procesu albo błąd jak prawdziwe programy.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts" / "sandbox" / "check.py"
TARGET = "etc/apparmor.d/bwrap-userns-restrict"
SYSCTL = "proc/sys/kernel/apparmor_restrict_unprivileged_userns"


def run_check(*args, env=None):
    return subprocess.run(["/usr/bin/python3", str(CHECK), *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, timeout=60)


def profile_text() -> str:
    result = run_check("profile")
    assert result.returncode == 0, result.stderr
    return result.stdout


class ProfileTextTest(unittest.TestCase):
    def test_profile_attaches_only_to_usr_bin_bwrap(self):
        # Nagłówki profili: „profile <nazwa> [<ścieżka>] [flags=…] {”. Ścieżkę ma tylko bwrap.
        headers = re.findall(r"^\s*profile\s+(\S+)(?:\s+(/\S*))?", profile_text(), re.M)
        self.assertEqual(headers, [("bwrap", "/usr/bin/bwrap"), ("unpriv_bwrap", "")])

    def test_children_of_bwrap_lose_capabilities(self):
        text = profile_text()
        # Dzieci bwrap dostają stos z unpriv_bwrap, który odbiera uprawnienia w przestrzeni użytkownika —
        # bez tego bwrap byłby furtką do obejścia ograniczenia dla całego systemu.
        self.assertIn("px /** -> bwrap//&unpriv_bwrap,", text)
        unpriv = text[text.index("profile unpriv_bwrap"):]
        self.assertIn("audit deny capability,", unpriv)

    def test_profile_leaves_the_rest_of_the_system_alone(self):
        text = profile_text()
        self.assertNotIn("unprivileged_userns", text)
        self.assertNotIn("flags=(unconfined)", text)
        self.assertNotIn("sysctl", text)

    def test_profile_is_marked_as_tides(self):
        self.assertIn("scripts/sandbox/install.sh", profile_text().splitlines()[0])

    @unittest.skipUnless(shutil.which("apparmor_parser"), "brak apparmor_parser")
    def test_profile_compiles_without_loading(self):
        with tempfile.NamedTemporaryFile("w", suffix=".profile") as fh:
            fh.write(profile_text())
            fh.flush()
            # -Q: bez ładowania do jądra, -K: bez pamięci podręcznej — działa bez roota.
            result = subprocess.run(["apparmor_parser", "-Q", "-K", fh.name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


FAKE = """#!/bin/sh
# Atrapa {name}: wypisuje zadaną etykietę albo błąd i kończy się zadanym kodem.
printf '%s\\n' "$FAKE_{var}_OUT" >&{fd}
exit "${{FAKE_{var}_RC:-0}}"
"""
DENIED = "bwrap: setting up uid map: Permission denied"
TIDE_LABEL = "bwrap//&unpriv_bwrap (enforce)"
RESTRICTED = "unprivileged_userns (enforce)"


class StateTest(unittest.TestCase):
    """Stan na udawanym systemie: pliki profili, sysctl i wynik prób bwrap oraz unshare."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        (self.root / "etc/apparmor.d").mkdir(parents=True)
        (self.root / SYSCTL).parent.mkdir(parents=True)
        (self.root / SYSCTL).write_text("1\n")
        self.env = {"TIDE_SANDBOX_ROOT": str(self.root)}
        for name, var in (("bwrap", "BWRAP"), ("unshare", "UNSHARE")):
            fake = Path(self.tmp.name) / name
            fake.write_text(FAKE.format(name=name, var=var, fd="${FAKE_%s_FD:-1}" % var))
            fake.chmod(0o755)
            self.env[f"TIDE_SANDBOX_{var}"] = str(fake)
        self.unshare(RESTRICTED)

    def tearDown(self):
        self.tmp.cleanup()

    def bwrap(self, out, rc=0):
        self.env.update(FAKE_BWRAP_OUT=out, FAKE_BWRAP_RC=str(rc), FAKE_BWRAP_FD="2" if rc else "1")

    def unshare(self, out, rc=0):
        self.env.update(FAKE_UNSHARE_OUT=out, FAKE_UNSHARE_RC=str(rc))

    def state(self):
        result = run_check("--json", env=self.env)
        import json
        return result.returncode, json.loads(result.stdout)

    def test_before_install_bwrap_is_blocked_and_the_profile_is_missing(self):
        self.bwrap(DENIED, rc=1)
        code, state = self.state()
        self.assertEqual(code, 1)
        self.assertFalse(state["ok"])
        self.assertEqual(state["restrict_userns"], 1)
        self.assertEqual(state["profile"], "brak")
        self.assertFalse(state["bwrap"]["works"])
        self.assertIn("uid map", state["bwrap"]["error"])
        self.assertTrue(state["others_restricted"])

    def test_after_install_bwrap_runs_children_under_unpriv_bwrap_and_the_rest_stays_restricted(self):
        (self.root / TARGET).write_text(profile_text())
        self.bwrap(TIDE_LABEL)
        code, state = self.state()
        self.assertEqual(code, 0)
        self.assertTrue(state["ok"])
        self.assertEqual(state["profile"], "tide")
        self.assertEqual(state["bwrap"]["label"], TIDE_LABEL)
        self.assertTrue(state["others_restricted"])
        self.assertEqual(state["conflicts"], [])

    def test_a_profile_file_without_tides_marker_is_foreign(self):
        # np. kopia z /usr/share/apparmor/extra-profiles albo profil z pakietu w nowszym systemie
        (self.root / TARGET).write_text("abi <abi/4.0>,\nprofile bwrap /usr/bin/bwrap {\n}\n")
        self.bwrap(DENIED, rc=1)
        _, state = self.state()
        self.assertEqual(state["profile"], "obcy")

    def test_another_profile_for_bwrap_is_reported_as_a_conflict(self):
        # profil z dokumentacji Claude Code w /etc/apparmor.d/bwrap — dwa profile o nazwie bwrap się gryzą
        (self.root / "etc/apparmor.d/bwrap").write_text(
            "abi <abi/4.0>,\ninclude <tunables/global>\n\n"
            "profile bwrap /usr/bin/bwrap flags=(unconfined) {\n  userns,\n}\n")
        (self.root / "etc/apparmor.d/chrome").write_text(
            "profile chrome /opt/google/chrome/chrome flags=(unconfined) {\n  userns,\n}\n")
        (self.root / "etc/apparmor.d/usr.bin.bwrap").write_text("/usr/bin/bwrap {\n  userns,\n}\n")
        (self.root / "etc/apparmor.d/local").mkdir()
        self.bwrap("unconfined")
        _, state = self.state()
        self.assertEqual(state["conflicts"], ["/etc/apparmor.d/bwrap", "/etc/apparmor.d/usr.bin.bwrap"])

    def test_restriction_switched_off_for_the_whole_system_is_not_ok(self):
        # bwrap wtedy działa i bez profilu, ale reguła projektu mówi: reszta systemu zostaje pod ograniczeniem
        (self.root / SYSCTL).write_text("0\n")
        self.bwrap("unconfined")
        self.unshare("unconfined")
        code, state = self.state()
        self.assertEqual(code, 1)
        self.assertTrue(state["bwrap"]["works"])
        self.assertFalse(state["others_restricted"])

    def test_report_for_people_names_the_next_step(self):
        self.bwrap(DENIED, rc=1)
        result = run_check(env=self.env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("sudo bash scripts/sandbox/install.sh", result.stdout)


if __name__ == "__main__":
    unittest.main()
