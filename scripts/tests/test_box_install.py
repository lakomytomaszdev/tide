"""Instalacja pudełka karty (PT-166): scripts/box/install.sh, uninstall.sh, host-state.sh i files.sh bez sudo.

Host jest udawany: atrapa fake_box_host.py pod nazwami apt-get, incus, incus-user, nft, systemctl, getent,
dpkg-query i ip (stan w state.json), a pliki systemowe leżą pod TIDE_BOX_ROOT. Skrypty uruchamiamy tylko
z --dry-run — atrapy odrzucają każde polecenie zmieniające stan, więc podgląd nie może niczego zmienić.
"""
import json
import os
import pwd
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOX = ROOT / "scripts" / "box"
FAKE = Path(__file__).resolve().parent / "fake_box_host.py"
FAKE_NAMES = ("apt-get", "incus", "incus-user", "nft", "systemctl", "getent", "dpkg-query", "ip")

ADDRESS = "10.120.7.1"
PORT = "18080"
INCUS_PACKAGES = ["incus", "incus-client", "incus-agent", "liblxc1t64", "liblxc-common", "lxcfs",
                  "libcowsql0", "libraft0", "attr", "btrfs-progs"]

# Wzorzec pliku reguł — recenzent czyta go jak kod produkcyjny: jedyne accept to proxy na adresie mostka
# i DHCP, DNS i reszta ruchu z incusbr0 DROP, forward z i do incusbr0 DROP.
EXPECTED_RULES = f"""#!/usr/sbin/nft -f
# Tide — pudełko karty: statyczne reguły dla mostka Incusa (incusbr0). Generuje scripts/box/files.sh,
# zakłada wyłącznie scripts/box/install.sh (sudo); nie edytuj ręcznie — zmiana przez make box-install.
# Z incusbr0 wolno tylko: {ADDRESS}:{PORT}/tcp (proxy Tide) i DHCP; DNS (53), forward i cała reszta DROP.

table inet tide-box
delete table inet tide-box

table inet tide-box {{
	chain input {{
		type filter hook input priority filter + 10; policy accept;
		iifname "incusbr0" ip daddr {ADDRESS} tcp dport {PORT} accept comment "proxy Tide"
		iifname "incusbr0" udp sport 68 udp dport 67 accept comment "DHCP"
		iifname "incusbr0" meta l4proto {{ tcp, udp }} th dport 53 drop comment "DNS tylko przez proxy"
		iifname "incusbr0" drop
	}}

	chain forward {{
		type filter hook forward priority filter + 10; policy accept;
		iifname "incusbr0" drop
		oifname "incusbr0" drop
	}}
}}
"""


def files_sh(*args):
    return subprocess.run(["bash", str(BOX / "files.sh"), *args], capture_output=True, text=True, timeout=30)


class RulesTest(unittest.TestCase):
    """Plik reguł nftables, jednostka i drop-in z files.sh."""

    def rules(self):
        result = files_sh("rules", ADDRESS, PORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_rules_match_the_pattern_and_nothing_else(self):
        self.assertEqual(self.rules(), EXPECTED_RULES)

    def test_only_proxy_and_dhcp_are_accepted_and_both_chains_end_with_drop(self):
        text = self.rules()
        accepts = [line.strip() for line in text.splitlines() if re.search(r"\baccept\b", line)
                   and not line.strip().startswith(("type ", "#"))]
        self.assertEqual(accepts, [
            f'iifname "incusbr0" ip daddr {ADDRESS} tcp dport {PORT} accept comment "proxy Tide"',
            'iifname "incusbr0" udp sport 68 udp dport 67 accept comment "DHCP"',
        ])
        for chain in ("input", "forward"):
            body = re.search(r"chain %s \{(.*?)\n\t\}" % chain, text, re.S).group(1).strip().splitlines()
            self.assertIn(f"hook {chain} priority filter + 10", body[0])
            self.assertTrue(body[-1].strip().endswith("drop"), body[-1])
        self.assertIn("th dport 53 drop", text)

    def test_rules_refuse_a_bad_address_or_port(self):
        for args in (("10.0.0.256", PORT), ("incusbr0", PORT), ("", PORT), (ADDRESS, "0"), (ADDRESS, "70000"),
                     (ADDRESS, "80; accept")):
            with self.subTest(args=args):
                self.assertEqual(files_sh("rules", *args).returncode, 2)

    @unittest.skipUnless(shutil.which("nft"), "brak nft w środowisku testu — sprawdzenie składni pominięte")
    def test_rules_pass_nft_syntax_check_without_loading(self):
        with tempfile.NamedTemporaryFile("w", suffix=".nft") as fh:
            fh.write(self.rules())
            fh.flush()
            result = subprocess.run(["nft", "-c", "-f", fh.name], capture_output=True, text=True, timeout=30)
        if result.returncode != 0 and "Operation not permitted" in result.stderr:
            self.skipTest("nft -c wymaga CAP_NET_ADMIN, którego środowisko testu nie ma — składnię sprawdza "
                          "install.sh z sudo")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unit_loads_the_rules_before_the_network_and_incus(self):
        unit = files_sh("unit").stdout
        self.assertIn("ExecStart=/usr/sbin/nft -f /etc/tide-box/tide-box.nft", unit)
        self.assertIn("ExecStop=/usr/sbin/nft delete table inet tide-box", unit)
        self.assertRegex(unit, r"Before=network-pre\.target incus\.service")
        self.assertNotIn("flush ruleset", unit)
        dropin = files_sh("dropin").stdout
        self.assertIn("Requires=tide-box-nft.service", dropin)
        self.assertIn("scripts/box/install.sh", unit.splitlines()[0])
        self.assertIn("scripts/box/install.sh", dropin.splitlines()[0])


def project_config():
    entry = pwd.getpwuid(os.getuid())
    return {
        "features.images": "true", "features.profiles": "true", "features.storage.volumes": "true",
        "features.networks": "false", "restricted": "true", "restricted.containers.nesting": "allow",
        "restricted.devices.disk": "allow", "restricted.devices.disk.paths": entry.pw_dir,
        "restricted.idmap.uid": str(entry.pw_uid), "restricted.idmap.gid": str(entry.pw_gid),
        "restricted.networks.access": "incusbr0",
    }


class FakeHost(unittest.TestCase):
    """Udawany host: atrapy w PATH, stan w state.json, pliki systemowe pod TIDE_BOX_ROOT."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.fake_dir = base / "fake"
        self.bin = base / "bin"
        self.root = base / "root"
        for d in (self.fake_dir, self.bin, self.root):
            d.mkdir()
        for name in FAKE_NAMES:
            wrapper = self.bin / name
            wrapper.write_text(f'#!/bin/sh\nexec /usr/bin/python3 "{FAKE}" {name} "$@"\n')
            wrapper.chmod(0o755)
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.project = f"user-{os.getuid()}"
        self.state = {"installed": False, "apt_new": list(INCUS_PACKAGES), "groups": {}, "units": {}}
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "TIDE_BOX_FAKE_DIR": str(self.fake_dir),
                    "TIDE_BOX_ROOT": str(self.root), "LC_ALL": "C.UTF-8"}
        self.env.pop("BASH_ENV", None)  # plik startowy powłoki mógłby nadpisać PATH z atrapami
        self.env.pop("SUDO_USER", None)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def installed(self, port=PORT):
        """Stan po udanej instalacji: Incus, pula, mostek, grupa, projekt, tabela i jednostka."""
        self.state.update({
            "installed": True, "pool": True, "address": f"{ADDRESS}/24", "profile_pool": "tide",
            "projects": {self.project: {"config": project_config(), "root.pool": "tide",
                                        "eth0.network": "incusbr0"}},
            "groups": {"incus": [self.user], "incus-admin": []},
            "units": {"tide-box-nft.service": "enabled", "incus-user.socket": "enabled"},
            "nft_table": True,
        })
        self.write("var/lib/tide-box/install.state",
                   f"# scripts/box/install.sh — stan instalacji pudełka\nuser={self.user}\n"
                   f"groups_before=\npackages={' '.join(INCUS_PACKAGES)}\nmember_of=incus\n")
        self.write("var/lib/tide-box/subuid.before", "")
        self.write("var/lib/tide-box/subgid.before", "")
        self.write("etc/tide-box/tide-box.nft", files_sh("rules", ADDRESS, port).stdout)
        self.write("etc/systemd/system/tide-box-nft.service", files_sh("unit").stdout)
        self.write("etc/systemd/system/incus.service.d/tide-box.conf", files_sh("dropin").stdout)
        self.write("home/tide-incus-pool.img", "")

    def run_script(self, name, *args):
        (self.fake_dir / "state.json").write_text(json.dumps(self.state))
        return subprocess.run(["bash", str(BOX / name), "--dry-run", *args], capture_output=True, text=True,
                              env=self.env, timeout=60)

    def calls(self):
        log = self.fake_dir / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def files(self):
        return sorted((str(p.relative_to(self.root)), p.read_bytes()) for p in self.root.rglob("*") if p.is_file())

    def steps(self, out):
        """[(numer, nazwa, zmiana?)] z linii planu „[n/N] nazwa: zmiana|bez zmian — …”."""
        return [(int(m[1]), m[2], m[3] == "zmiana")
                for m in re.finditer(r"^\[(\d+)/\d+\] (.+?): (zmiana|bez zmian)\b", out, re.M)]


class InstallDryRunTest(FakeHost):
    def test_full_dry_run_plans_every_step_in_order_and_changes_nothing(self):
        before = self.files()
        result = self.run_script("install.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"^\[1/7\] symulacja apt: apt-get -s install incus btrfs-progs", )
        self.assertEqual(self.steps(result.stdout), [
            (2, "instalacja", True), (3, "preseed", True), (4, "grupa", True), (5, "projekt", True),
            (6, "nftables", True), (7, "trwałość", True)])
        self.assertIn("nic nie zmieniono", result.stdout)
        self.assertIn("/home/tide-incus-pool.img", result.stdout)
        self.assertIn(f"incus project create {self.project}", result.stdout)
        self.assertIn("restricted.containers.nesting=allow", result.stdout)
        self.assertEqual(self.files(), before)
        apt = [c for c in self.calls() if c.startswith("apt-get")]
        self.assertEqual(apt, ["apt-get -s install incus btrfs-progs"])

    def test_second_run_on_an_installed_host_plans_no_change(self):
        self.installed()
        before = self.files()
        result = self.run_script("install.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([s for s in self.steps(result.stdout) if s[2]], [])
        self.assertEqual(len(self.steps(result.stdout)), 6)
        self.assertIn("Nic do zmiany", result.stdout)
        self.assertEqual(self.files(), before)

    def test_another_proxy_port_changes_only_the_rules(self):
        self.installed()
        result = self.run_script("install.sh", "--proxy-port", "18081")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([s[1] for s in self.steps(result.stdout) if s[2]], ["nftables"])
        self.assertIn(f"+\t\tiifname \"incusbr0\" ip daddr {ADDRESS} tcp dport 18081 accept", result.stdout)

    def test_missing_project_setting_plans_only_the_project(self):
        self.installed()
        del self.state["projects"][self.project]["config"]["restricted.containers.nesting"]
        result = self.run_script("install.sh")
        self.assertEqual([s[1] for s in self.steps(result.stdout) if s[2]], ["projekt"])
        self.assertIn("restricted.containers.nesting=allow", result.stdout)

    def test_refuses_when_apt_simulation_pulls_qt_or_kde_and_lists_them(self):
        self.state["apt_new"] += ["libqt6core6t64", "libkf6coreaddons6", "plasma-workspace"]
        result = self.run_script("install.sh")
        self.assertEqual(result.returncode, 1)
        for pkg in ("libqt6core6t64", "libkf6coreaddons6", "plasma-workspace"):
            self.assertIn(pkg, result.stderr)
        self.assertNotIn("incus\n", result.stderr)
        self.assertIn("Nic nie zmieniono", result.stderr)
        self.assertEqual(self.steps(result.stdout), [])

    def test_refuses_when_apt_simulation_upgrades_an_installed_package(self):
        self.state["apt_upgrade"] = ["Inst libc6 [2.39-0ubuntu8.5] (2.39-0ubuntu8.6 Ubuntu:24.04/noble-updates [amd64])"]
        result = self.run_script("install.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("libc6", result.stderr)

    def test_refuses_to_take_over_incus_installed_by_someone_else(self):
        self.state["installed"] = True
        result = self.run_script("install.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nie przez ten skrypt", result.stderr)

    def test_refuses_to_format_an_existing_pool_file(self):
        self.write("home/tide-incus-pool.img", "cudze dane")
        result = self.run_script("install.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nie formatuję", result.stderr)

    def test_admin_mode_needs_a_decision_entry(self):
        result = self.run_script("install.sh", "--project-mode", "admin")
        self.assertEqual(result.returncode, 1)
        self.assertIn("tryb-admin", result.stderr)

    def test_rejects_unknown_mode_and_bad_port(self):
        self.assertEqual(self.run_script("install.sh", "--project-mode", "root").returncode, 2)
        self.assertEqual(self.run_script("install.sh", "--proxy-port", "x").returncode, 2)


class UninstallDryRunTest(FakeHost):
    def test_nothing_installed_means_nothing_to_remove(self):
        result = self.run_script("uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nic do usunięcia", result.stdout)

    def test_dry_run_plans_the_reverse_order_and_keeps_everything(self):
        self.installed()
        before = self.files()
        result = self.run_script("uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.steps(result.stdout), [
            (1, "trwałość", True), (2, "nftables", True), (3, "incus", True), (4, "grupa", False),
            (5, "pakiety", True), (6, "pozostałości", True)])
        self.assertIn("apt-get purge -y " + " ".join(INCUS_PACKAGES), result.stdout)
        self.assertIn("nic nie zmieniono", result.stdout)
        self.assertEqual(self.files(), before)

    def test_refuses_when_purge_would_remove_packages_it_did_not_install(self):
        self.installed()
        self.state["apt_extra_purge"] = ["incus-ui-canonical"]
        result = self.run_script("uninstall.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("incus-ui-canonical", result.stderr)

    def test_refuses_a_unit_without_tides_marker(self):
        self.installed()
        self.write("etc/systemd/system/tide-box-nft.service", "[Unit]\nDescription=cudza\n")
        result = self.run_script("uninstall.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nie nasz plik", result.stderr)


class HostStateTest(FakeHost):
    def snapshot(self):
        (self.fake_dir / "state.json").write_text(json.dumps(self.state))
        result = subprocess.run(["bash", str(BOX / "host-state.sh")], capture_output=True, text=True,
                                env=self.env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_snapshot_lists_packages_links_rules_groups_pool_and_units_without_counters(self):
        self.state.update({
            "dpkg": ["zlib1g:amd64\t1:1.3", "attr\t1:2.5.2-1"],
            "links": ["2: enp5s0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP mode DEFAULT"
                      " group default qlen 1000\\    link/ether 00:11:22:33:44:55 brd ff:ff:ff:ff:ff:ff",
                      "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT group"
                      " default qlen 1000\\    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00"],
            "ruleset": "table ip filter {\n\tchain FORWARD {\n\t\tcounter packets 12 bytes 3456 jump DOCKER\n\t}\n}\n",
            "groups": {"docker": [self.user]},
            "unit_files": ["incus.service enabled enabled"],
        })
        self.write("etc/subuid", f"{self.user}:100000:65536\n")
        out = self.snapshot()
        self.assertIn("attr\t1:2.5.2-1\nzlib1g:amd64\t1:1.3", out)
        self.assertIn("enp5s0 link/ether\nlo link/loopback", out)
        self.assertIn("\t\tcounter jump DOCKER", out)
        self.assertNotIn("3456", out)
        self.assertIn(f"docker:x:999:{self.user}", out)
        self.assertIn(f"  {self.user}:100000:65536", out)
        self.assertIn("/home/tide-incus-pool.img: brak", out)
        self.assertIn("incus.service enabled", out)
        self.state["ruleset"] = self.state["ruleset"].replace("12 bytes 3456", "99 bytes 100000")
        self.assertEqual(self.snapshot(), out)

    def test_pool_file_and_units_show_up_after_install(self):
        before = self.snapshot()
        self.installed()
        self.state["unit_files"] = ["tide-box-nft.service enabled enabled"]
        after = self.snapshot()
        self.assertIn("/home/tide-incus-pool.img: jest", after)
        self.assertIn("tide-box-nft.service enabled", after)
        self.assertNotEqual(before, after)


class MakeTargetsTest(unittest.TestCase):
    def test_box_targets_live_in_mk_box(self):
        text = (ROOT / "mk" / "box.mk").read_text()
        for target in ("box-install", "box-uninstall", "box-state"):
            self.assertRegex(text, rf"(?m)^{target}:")
        self.assertIn("scripts/box/install.sh --yes", text)

    def test_make_install_and_uninstall_never_reach_box_targets(self):
        sources = [ROOT / "Makefile", *sorted((ROOT / "mk").glob("*.mk"))]
        for path in sources:
            for line in path.read_text().splitlines():
                if re.match(r"\s*(LATE_)?(UN)?INSTALL_TARGETS\s*[+:]?=", line) or re.match(r"(un)?install:", line):
                    self.assertNotRegex(line, r"box", f"{path.name}: {line}")
            if path.name != "box.mk":
                self.assertNotRegex(path.read_text(), r"scripts/box/|\$\(MAKE\)\s+box-", path.name)


class DocsTest(unittest.TestCase):
    def test_box_decisions_has_the_table_and_the_static_nftables_entry(self):
        text = (ROOT / "docs" / "box-decisions.md").read_text()
        self.assertIn("| Etykieta | Data | Ticket | Treść | Uzasadnienie |", text)
        self.assertIn("nftables statyczne, polityka w proxy", text)

    def test_tide_daemon_doc_describes_the_box_installation(self):
        text = (ROOT / "docs" / "tide-daemon.md").read_text()
        section = re.search(r"^## Pudełko\n(.*?)(?=^## )", text, re.S | re.M)
        self.assertIsNotNone(section, "brak sekcji „## Pudełko”")
        body = section.group(1)
        self.assertIn("### Instalacja", body)
        for phrase in ("make box-install", "make box-uninstall", "host-state.sh", "/home/tide-incus-pool.img",
                       "restricted.containers.nesting=allow", "inet tide-box", "`input`", "`forward`",
                       "tide-proto", "nftables statyczne, polityka w proxy"):
            self.assertIn(phrase, body)


if __name__ == "__main__":
    unittest.main()
