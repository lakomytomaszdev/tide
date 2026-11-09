"""Klucz `box` w Set/GetCardFlowSettings (K-38, PT-292).

Kolejno: katalog kluczy `box/settings.py` i ścisła walidacja, kontrakt portu z regułami nftables (fixture reguł
z `scripts/box/files.sh`, tego samego generatora co `scripts/box/install.sh`), `FlowEngine.set/get_flow_settings`
z odmową przed zapisem i zachowaniem innych pól, a na końcu `SetCardFlowSettings` na prywatnej magistrali
z restartem usługi. Plik reguł podstawia `TIDE_BOX_NFT_FILE`; testy nie czytają `/etc/tide-box` hosta.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tide_daemon.box import settings as bsettings
from tide_daemon.cardflow import caretaker
from tide_daemon.cardflow.engine import FlowError

from .test_cardflow_dbus import FlowCase

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FILES_SH = os.path.join(ROOT, "scripts", "box", "files.sh")
ADDRESS = "10.120.7.1"
nftcontract = None   # tide_daemon.box.nftcontract, importowany w NftContractTest.setUp


def rules(address=ADDRESS, port=18080) -> str:
    """Reguły z generatora instalatora (`files.sh rules`) — jedno źródło z `make box-install`."""
    result = subprocess.run(["bash", FILES_SH, "rules", address, str(port)], capture_output=True, text=True,
                            timeout=30)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


class CatalogTest(unittest.TestCase):
    def test_catalog_is_exactly_the_keys_read_by_box_settings(self):
        self.assertEqual(tuple(bsettings.KEYS), (
            "enabled", "proxy-port", "bridge", "project", "base-image", "pool", "nic", "agents-version",
            "ipv6-filtering", "ip-wait-seconds", "idle-hours", "agent-uid", "agent-gid"))
        # Każdy klucz katalogu jest czytany przez `from_dict`: zła wartość daje ostrzeżenie z jego nazwą.
        for key in bsettings.KEYS:
            with self.subTest(key=key):
                got = bsettings.from_dict({"box": {key: object()}})
                self.assertEqual([w for w in got.warnings if w.startswith(f"box.{key}:")].__len__(), 1, got.warnings)

    def test_view_shows_effective_values_in_file_keys(self):
        view = bsettings.view(bsettings.from_dict({"box": {"proxy-port": 18555, "idle-hours": 2}}))
        self.assertEqual(set(view), set(bsettings.KEYS))
        self.assertEqual((view["enabled"], view["proxy-port"], view["idle-hours"], view["bridge"]),
                         (False, 18555, 2.0, "incusbr0"))

    def test_strict_problem_rejects_without_default(self):
        bad = [None, [], "box", 5,
               {"enabled": "true"}, {"enabled": 1}, {"enabled": 0}, {"enabled": None},
               {"proxy-port": True}, {"proxy-port": False}, {"proxy-port": 80}, {"proxy-port": 1023},
               {"proxy-port": 65536}, {"proxy-port": "18080"}, {"proxy-port": 18080.0},
               {"bridge": "a b"}, {"bridge": "x" * 16}, {"project": ""}, {"pool": 7},
               {"ipv6-filtering": "false"}, {"ip-wait-seconds": True}, {"ip-wait-seconds": 601},
               {"idle-hours": 0}, {"idle-hours": float("nan")}, {"agent-uid": 0}, {"agent-gid": True},
               {"enabled": False, "nieznany": 1}, {"proxyPort": 18080}]
        for patch in bad:
            with self.subTest(patch=patch):
                self.assertTrue(bsettings.problem(patch))
        self.assertIn("nieznany", bsettings.problem({"nieznany": 1}))
        good = [{}, {"enabled": False}, {"enabled": True}, {"proxy-port": 1024}, {"proxy-port": 65535},
                {"bridge": "tidebr0", "agents-version": "", "ip-wait-seconds": 0, "idle-hours": 0.5},
                {"agent-uid": 1001, "agent-gid": 1001, "ipv6-filtering": True, "project": "user-7"}]
        for patch in good:
            with self.subTest(patch=patch):
                self.assertIsNone(bsettings.problem(patch))


class NftContractTest(unittest.TestCase):
    """Fixture reguł z generatora instalatora: adres i port proxy muszą być dopuszczone w `inet tide-box`."""

    def setUp(self):
        # Import w teście: na bazie ticketu moduł nie istnieje, a czerwony wynik silnika ma pokazać samą odmowę `box`.
        global nftcontract
        from tide_daemon.box import nftcontract

    def test_installed_rules_allow_exactly_the_proxy_address_and_port(self):
        text = rules(ADDRESS, 18080)
        self.assertEqual(nftcontract.proxy_rules(text), [("incusbr0", ADDRESS, 18080)])
        self.assertIsNone(nftcontract.problem(text, port=18080, bridge="incusbr0", address=ADDRESS))
        self.assertIsNone(nftcontract.problem(text, port=18080, bridge="incusbr0"))

    def test_mismatch_of_port_address_or_bridge_is_a_problem_with_a_procedure(self):
        text = rules(ADDRESS, 18080)
        port = nftcontract.problem(text, port=18081, bridge="incusbr0")
        self.assertIn("18080", port)
        self.assertIn("make box-install BOX_PROXY_PORT=18081", port)
        self.assertIn("10.120.7.9", nftcontract.problem(text, port=18080, bridge="incusbr0", address="10.120.7.9"))
        self.assertIn("tidebr0", nftcontract.problem(text, port=18080, bridge="tidebr0"))

    def test_rules_without_the_static_table_or_outside_input_chain_do_not_confirm(self):
        line = f'iifname "incusbr0" ip daddr {ADDRESS} tcp dport 18080 accept comment "proxy Tide"'
        self.assertEqual(nftcontract.proxy_rules(line + "\n"), [])
        moved = rules().replace("\t\t" + line + "\n", "").replace("\t\toifname", f"\t\t{line}\n\t\toifname")
        self.assertEqual(nftcontract.proxy_rules(moved), [])
        self.assertIn("brak", nftcontract.problem(moved, port=18080, bridge="incusbr0"))
        twice = rules().replace(line, line + "\n\t\t" + line.replace("18080", "18081"))
        self.assertEqual(len(nftcontract.proxy_rules(twice)), 2)
        self.assertTrue(nftcontract.problem(twice, port=18080, bridge="incusbr0"))
        self.assertTrue(nftcontract.problem("", port=18080, bridge="incusbr0"))

    def test_missing_or_unreadable_file_is_unconfirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tide-box.nft")
            missing = nftcontract.check_file(path, port=18080, bridge="incusbr0")
            self.assertIn("niepotwierdzon", missing)
            self.assertIn(path, missing)
            write(path, rules())
            self.assertIsNone(nftcontract.check_file(path, port=18080, bridge="incusbr0"))
        self.assertEqual(nftcontract.rules_file({}), "/etc/tide-box/tide-box.nft")
        self.assertEqual(nftcontract.rules_file({"TIDE_BOX_NFT_FILE": "/x"}), "/x")


class EngineBoxSettingsTest(unittest.TestCase):
    def setUp(self):
        from tide_daemon.board.store import BoardStore
        from tide_daemon.cardflow.journal import Journal as CardJournal
        from tide_daemon.models import executors
        self.base = tempfile.mkdtemp(prefix="tide-pt292-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.settings = os.path.join(self.base, "cardflow.json")
        self.stored = {"stepGuardMinutes": 7, "providerSlots": {"anthropic": 2}, "limitThresholdPercent": 25,
                       "limitTransfer": {"mode": "auto", "executor": "Sonnet"}, "caretakerActionLimit": 5,
                       "caretakerExecutor": "Sonnet", "networkDomains": {"proj": ["example.com"]},
                       "box": {"proxy-port": 18080, "project": "user-7", "idle-hours": 2, "przyszly": "zostaje"}}
        write(self.settings, json.dumps(self.stored))
        self.rules = os.path.join(self.base, "tide-box.nft")
        write(self.rules, rules(ADDRESS, 18080))
        self.store = BoardStore(os.path.join(self.base, "board"))
        self.journal = CardJournal(self.store.root, mirror=os.path.join(self.base, "mirror"))
        self.catalog = executors.default_catalog
        self.engine = self.make()

    def make(self):
        from tide_daemon.cardflow.engine import FlowEngine
        engine = FlowEngine(self.store, self.journal, catalog=self.catalog, settings=self.settings,
                            tmp_root=self.base)
        engine.box_rules = self.rules
        return engine

    def file(self):
        with open(self.settings, encoding="utf-8") as fh:
            return json.load(fh)

    def test_partial_box_write_keeps_every_other_setting_and_survives_restart(self):
        before = self.engine.get_flow_settings()
        reply = self.engine.set_flow_settings({"box": {"enabled": False}})
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["box"]["enabled"], False)
        self.assertEqual((reply["box"]["proxy-port"], reply["box"]["project"], reply["box"]["idle-hours"]),
                         (18080, "user-7", 2.0))
        for key in ("limitTransfer", "limitThresholdPercent", "providerSlots", *caretaker.SETTINGS_DEFAULTS):
            self.assertEqual(reply[key], before[key], key)
        stored = self.file()
        self.assertEqual(stored["box"], {**self.stored["box"], "enabled": False})
        for key in ("stepGuardMinutes", "networkDomains", "providerSlots", "limitThresholdPercent",
                    "limitTransfer", "caretakerActionLimit", "caretakerExecutor"):
            self.assertEqual(stored[key], self.stored[key], key)
        # Odczyt po restarcie: nowy silnik i moduł ustawień pudełka (proxy, runner) widzą ten sam zapis.
        self.assertEqual(self.make().get_flow_settings(), reply)
        loaded = bsettings.load(self.settings)
        self.assertEqual((loaded.enabled, loaded.proxy_port, loaded.project, loaded.idle_hours),
                         (False, 18080, "user-7", 2.0))

    def test_set_without_box_keeps_the_stored_box_section(self):
        self.engine.set_flow_settings({"limitThresholdPercent": 40})
        self.assertEqual(self.file()["box"], self.stored["box"])

    def test_bad_requests_are_refused_before_any_write(self):
        bad = [{"box": None}, {"box": []}, {"box": "enabled"},
               {"box": {"enabled": "true"}}, {"box": {"enabled": 1}}, {"box": {"enabled": 0}},
               {"box": {"proxy-port": True}}, {"box": {"proxy-port": 80}}, {"box": {"proxy-port": 70000}},
               {"box": {"proxy-port": "18081"}}, {"box": {"nieznany": 1}},
               {"limitThresholdPercent": 30, "box": {"enabled": "nie"}},
               {"caretakerActionLimit": 4, "box": {"proxy-port": 0}},
               {"box": {"enabled": False}, "limitThresholdPercent": 0},
               {"box": {"enabled": False}, "caretakerActionLimit": 11}]
        original = read_bytes(self.settings)
        for request in bad:
            with self.subTest(request=request), self.assertRaises(FlowError) as raised:
                self.engine.set_flow_settings(request)
            self.assertIn(raised.exception.code, ("bad-settings", "bad-request"))
            self.assertEqual(read_bytes(self.settings), original, request)
        self.assertEqual([n for n in os.listdir(self.base) if n.startswith(".cardflow-")], [])

    def test_port_change_against_installed_rules(self):
        original = read_bytes(self.settings)
        with self.assertRaises(FlowError) as raised:
            self.engine.set_flow_settings({"box": {"proxy-port": 18081}, "limitThresholdPercent": 30})
        self.assertEqual(raised.exception.code, "box-install-mismatch")
        self.assertIn("make box-install BOX_PROXY_PORT=18081", str(raised.exception))
        self.assertEqual(read_bytes(self.settings), original)
        # Zatwierdzona ponowna instalacja z nowym portem — dopiero wtedy zapis przechodzi.
        write(self.rules, rules(ADDRESS, 18081))
        reply = self.engine.set_flow_settings({"box": {"proxy-port": 18081}})
        self.assertEqual(reply["box"]["proxy-port"], 18081)
        self.assertEqual(self.file()["box"]["proxy-port"], 18081)

    def test_enabling_needs_a_confirmed_install(self):
        original = read_bytes(self.settings)
        os.unlink(self.rules)
        for request in ({"box": {"enabled": True}}, {"box": {"proxy-port": 18082}}):
            with self.subTest(request=request), self.assertRaises(FlowError) as raised:
                self.engine.set_flow_settings(request)
            self.assertEqual(raised.exception.code, "box-install-mismatch")
            self.assertIn("niepotwierdzon", str(raised.exception))
            self.assertEqual(read_bytes(self.settings), original)
        write(self.rules, rules(ADDRESS, 18080))
        with self.assertRaises(FlowError):   # mostek spoza reguł inet tide-box
            self.engine.set_flow_settings({"box": {"enabled": True, "bridge": "tidebr0"}})
        self.assertEqual(read_bytes(self.settings), original)
        self.assertTrue(self.engine.set_flow_settings({"box": {"enabled": True}})["box"]["enabled"])
        # Wyłączenie nie zależy od instalacji: zawsze można wrócić do pracy na hoście.
        os.unlink(self.rules)
        self.assertFalse(self.engine.set_flow_settings({"box": {"enabled": False}})["box"]["enabled"])

    def test_stored_invalid_value_is_reported_not_rewritten(self):
        write(self.settings, json.dumps({"box": {"proxy-port": "zly", "pool": "fast"}}))
        got = self.engine.get_flow_settings()
        self.assertEqual(got["box"]["proxy-port"], bsettings.DEFAULT_PORT)
        self.assertTrue(any("proxy-port" in w for w in got["boxWarnings"]))
        self.engine.set_flow_settings({"box": {"pool": "tide"}})
        self.assertEqual(self.file()["box"], {"proxy-port": "zly", "pool": "tide"})


class BoxSettingsDbusTest(FlowCase):
    def test_box_settings_on_the_bus_with_restart(self):
        rules_file = os.path.join(self.base, "tide-box.nft")
        write(rules_file, rules(ADDRESS, 18080))
        self.env["TIDE_BOX_NFT_FILE"] = rules_file
        self.restart()
        settings = os.path.join(self.base, "config", "tide", "cardflow.json")
        got = self.call("GetCardFlowSettings")
        self.assertEqual((got["box"]["enabled"], got["box"]["proxy-port"]), (False, 18080))
        original = read_bytes(settings)
        for request, code in (({"box": None}, "bad-settings"), ({"box": {"enabled": "true"}}, "bad-settings"),
                              ({"box": {"proxy-port": True}}, "bad-settings"),
                              ({"box": {"nieznany": 1}}, "bad-settings"),
                              ({"box": {"proxy-port": 18081}}, "box-install-mismatch"),
                              ({"providerSlots": {"anthropic": 3}, "box": {"enabled": 1}}, "bad-settings")):
            with self.subTest(request=request):
                rejected = self.call("SetCardFlowSettings", json.dumps(request))
                self.assertEqual((rejected["ok"], rejected["error"]), (False, code), rejected)
                self.assertEqual(read_bytes(settings), original)
        changed = self.call("SetCardFlowSettings", json.dumps({"box": {"enabled": False, "idle-hours": 6}}))
        self.assertTrue(changed["ok"], changed)
        self.assertEqual((changed["box"]["enabled"], changed["box"]["idle-hours"]), (False, 6.0))
        self.restart()
        got = self.call("GetCardFlowSettings")
        self.assertEqual(got["box"], changed["box"])
        with open(settings, encoding="utf-8") as fh:
            stored = json.load(fh)
        self.assertEqual((stored["box"], stored["stepGuardMinutes"]), ({"enabled": False, "idle-hours": 6},
                                                                      self.guard_minutes))


if __name__ == "__main__":
    unittest.main()
