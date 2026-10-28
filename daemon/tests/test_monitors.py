"""Układ monitorów PC ⇄ Xbox (tide_daemon/monitors.py, PT-38).

`kscreen-doctor` i `ddcutil` to atrapy (fake_screens.py) w katalogu podanym przez $TIDE_MONITORS_BIN —
prawdziwe monitory, KWin i magistrale I²C nie są dotykane. Stan „sprzętu” atrapy to plik JSON,
więc test sprawdza wynik (układ w KWin, wejście monitora), a nie listę argumentów.
"""
import json
import os
import shutil
import tempfile
import unittest

from . import fake_screens
from .privatebus import SERVICE, PrivateBus, app, requires_dbus, wait_until
from tide_daemon import monitors
from tide_daemon.monitors_service import MonitorsService

DP, HDMI = 15, 0x11


def layout(state: dict) -> dict:
    """Układ z atrapy: nazwa → (włączony, pozycja, tryb, priorytet)."""
    out = {}
    for item in state["kscreen"]["outputs"]:
        mode = next(m for m in item["modes"] if m["id"] == item["currentModeId"])
        out[item["name"]] = (item["enabled"], (item["pos"]["x"], item["pos"]["y"]), mode["name"],
                             round(mode["refreshRate"], 2), item["priority"])
    return out


TODAY = {"DP-2": (True, (1920, 0), "2560x1440@165", 164.8, 1),
         "HDMI-A-1": (True, (0, 0), "1920x1080@75", 74.97, 2)}


class MonitorsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-monitors-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = os.path.join(self.tmp, "bin")
        self.hw = os.path.join(self.tmp, "screens.json")
        fake_screens.install(self.bin, self.hw)
        self.sleeps = []
        self.switcher = self.make()

    def make(self, **config):
        cfg = monitors.load_config()
        for key, value in config.items():
            setattr(cfg, key, value)
        tools = monitors.Tools(bin_dir=self.bin, env={"TIDE_FAKE_SCREENS": self.hw}, sleep=self.sleeps.append)
        return monitors.Switcher(cfg, tools, state_dir=os.path.join(self.tmp, "state"))

    def hardware(self) -> dict:
        return fake_screens.load(self.hw)

    def change_hardware(self, change) -> None:
        state = self.hardware()
        change(state)
        fake_screens.save(self.hw, state)

    def saved_state(self) -> dict:
        with open(os.path.join(self.tmp, "state", "monitors.json"), encoding="utf-8") as fh:
            return json.load(fh)


class ConfigTest(unittest.TestCase):
    def test_repository_config_has_the_inputs_read_from_the_monitor(self):
        cfg = monitors.load_config()
        self.assertEqual((cfg.monitor, cfg.primary, cfg.disable_monitor), ("DP-2", "HDMI-A-1", True))
        self.assertEqual((cfg.input_pc, cfg.input_xbox), (0x0F, 0x11))


class SwitchTest(MonitorsCase):
    def test_xbox_switches_the_input_and_makes_the_left_monitor_primary(self):
        result = self.switcher.switch("xbox")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["changed"])
        hw = self.hardware()
        self.assertEqual(hw["ddc"]["input"]["4"], HDMI, "DP-2 na wejściu HDMI (HD60 X)")
        self.assertEqual(hw["ddc"]["input"]["2"], DP, "lewego monitora DDC nie rusza")
        now = layout(hw)
        self.assertEqual(now["HDMI-A-1"][0::4], (True, 1))
        self.assertFalse(now["DP-2"][0], "DP-2 wyłączony w KWin")
        self.assertEqual(self.switcher.status()["layout"], "xbox")

    def test_pc_restores_exactly_the_layout_saved_before_xbox(self):
        self.switcher.switch("xbox")
        result = self.switcher.switch("pc")
        self.assertTrue(result["ok"], result)
        hw = self.hardware()
        self.assertEqual(layout(hw), TODAY)
        self.assertEqual(hw["ddc"]["input"]["4"], DP)
        self.assertEqual(self.switcher.status()["layout"], "pc")

    def test_the_restored_layout_is_whatever_was_there_before_not_a_fixed_one(self):
        # Inny dzień: lewy monitor po prawej, DP-2 na 60 Hz, HDMI-A-1 na 59,94 Hz (ta sama nazwa trybu co 60 Hz).
        def elsewhere(state):
            dp, hdmi = state["kscreen"]["outputs"]
            dp.update(currentModeId="1", pos={"x": 0, "y": 0})
            hdmi.update(currentModeId="28", pos={"x": 2560, "y": 180})
        self.change_hardware(elsewhere)
        before = layout(self.hardware())
        self.switcher.switch("xbox")
        self.assertTrue(self.switcher.switch("pc")["ok"])
        self.assertEqual(layout(self.hardware()), before)
        self.assertEqual(self.hardware()["kscreen"]["outputs"][1]["currentModeId"], "28", "59,94 Hz, nie 60 Hz")

    def test_scale_and_rotation_come_back_too(self):
        def scaled(state):
            state["kscreen"]["outputs"][0].update(scale=1.25, rotation=2)
        self.change_hardware(scaled)
        self.switcher.switch("xbox")
        self.change_hardware(lambda s: s["kscreen"]["outputs"][0].update(scale=1, rotation=1))  # KWin zapomniał
        self.switcher.switch("pc")
        dp = self.hardware()["kscreen"]["outputs"][0]
        self.assertEqual((dp["scale"], dp["rotation"]), (1.25, 2))


class IdempotenceTest(MonitorsCase):
    def test_xbox_twice_keeps_the_pc_snapshot_and_changes_nothing(self):
        self.switcher.switch("xbox")
        saved = self.saved_state()["saved"]
        calls = len(self.hardware()["calls"])
        again = self.switcher.switch("xbox")
        self.assertTrue(again["ok"])
        self.assertFalse(again["changed"])
        self.assertEqual(self.saved_state()["saved"], saved, "kopia układu PC nie nadpisana układem Xbox")
        writes = [c for c in self.hardware()["calls"][calls:] if c[0] == "ddcutil" or len(c) > 2]
        self.assertEqual(writes, [], "drugie przełączenie niczego nie zmienia")
        self.switcher.switch("pc")
        self.assertEqual(layout(self.hardware()), TODAY)

    def test_pc_when_already_pc_changes_nothing(self):
        result = self.switcher.switch("pc")
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual(layout(self.hardware()), TODAY)
        self.assertFalse(any(c[0] == "kscreen-doctor" and c[1:] != ["-j"] for c in self.hardware()["calls"]))
        self.assertFalse(any("setvcp" in c for c in self.hardware()["calls"]))

    def test_pc_puts_the_input_back_even_when_kwin_already_shows_pc(self):
        self.change_hardware(lambda s: s["ddc"]["input"].update({"4": HDMI}))  # ktoś przełączył wejście na monitorze
        self.assertTrue(self.switcher.switch("pc")["ok"])
        self.assertEqual(self.hardware()["ddc"]["input"]["4"], DP)

    def test_an_interrupted_switch_to_xbox_can_be_undone(self):
        # Przełączenie przerwane po zapisaniu kopii (np. usługa zabita w trakcie): PC wraca do kopii.
        self.change_hardware(lambda s: s["fail"].update({"kscreen-set": "KWin nie odpowiada"}))
        self.assertFalse(self.switcher.switch("xbox")["ok"])
        self.change_hardware(lambda s: s["fail"].clear())
        self.assertTrue(self.switcher.switch("pc")["ok"])
        self.assertEqual(layout(self.hardware()), TODAY)
        self.assertEqual(self.hardware()["ddc"]["input"]["4"], DP)


class DdcTest(MonitorsCase):
    def test_without_ddcutil_only_kwin_changes_and_the_user_is_told(self):
        os.remove(os.path.join(self.bin, "ddcutil"))
        result = self.switcher.switch("xbox")
        self.assertTrue(result["ok"])
        self.assertIn("brak ddcutil — przełącz wejście monitora DP-2 (HDMI) przyciskiem na monitorze",
                      result["notices"])
        self.assertFalse(layout(self.hardware())["DP-2"][0])
        self.assertEqual(self.switcher.status()["ddc"], "missing")
        back = self.switcher.switch("pc")
        self.assertTrue(back["ok"])
        self.assertEqual(layout(self.hardware()), TODAY)
        self.assertIn("brak ddcutil — przełącz wejście monitora DP-2 (DisplayPort) przyciskiem na monitorze",
                      back["notices"])

    def test_monitor_not_answering_ddc_still_gets_the_kwin_layout(self):
        self.change_hardware(lambda s: s["ddc"]["reachable"].update({"4": False}))
        result = self.switcher.switch("xbox")
        self.assertTrue(result["ok"])
        self.assertEqual(result["notices"],
                         ["DP-2 nie odpowiada na DDC — przełącz wejście monitora DP-2 (HDMI) przyciskiem na monitorze"])
        self.assertEqual(len(self.sleeps), 2, "trzy próby co retry_s, bez czekania na ustalenie wejścia")
        self.assertEqual(self.hardware()["ddc"]["input"]["4"], DP)
        self.assertEqual(layout(self.hardware())["HDMI-A-1"][4], 1)

    def test_monitor_missing_from_ddc_detect(self):
        self.change_hardware(lambda s: s["ddc"]["buses"].pop("DP-2"))
        result = self.switcher.switch("xbox")
        self.assertTrue(result["ok"])
        self.assertIn("DP-2 nie odpowiada na DDC (ddcutil detect)", result["notices"][0])

    def test_bus_number_comes_from_detect_not_from_config(self):
        def moved(state):
            state["ddc"]["buses"]["DP-2"] = 7
            state["ddc"]["input"]["7"] = DP
            state["ddc"]["reachable"]["7"] = True
        self.change_hardware(moved)
        self.assertTrue(self.switcher.switch("xbox")["ok"])
        self.assertEqual(self.hardware()["ddc"]["input"]["7"], HDMI)
        self.assertEqual(self.hardware()["ddc"]["input"]["4"], DP)

    def test_monitor_answering_only_on_the_shown_input(self):
        # Częsty przypadek: na wejściu HDMI monitor nie odpowiada na DDC przez DisplayPort — powrót ręczny.
        self.change_hardware(lambda s: s["ddc"].update(only_active={"4": DP}))
        self.assertTrue(self.switcher.switch("xbox")["ok"])
        back = self.switcher.switch("pc")
        self.assertTrue(back["ok"])
        self.assertEqual(layout(self.hardware()), TODAY, "KWin wraca do układu PC mimo braku DDC")
        self.assertIn("DP-2 nie odpowiada na DDC — przełącz wejście monitora DP-2 (DisplayPort) przyciskiem na monitorze",
                      back["notices"])
        self.assertEqual(self.switcher.status()["ddc"], "unreachable")

    def test_monitor_that_disconnects_when_its_input_changes(self):
        # Monitor po zmianie wejścia zgłasza odłączenie (HPD): KWin sam go usuwa, a powrót czeka na ręczne wejście.
        self.change_hardware(lambda s: s["ddc"].update(hpd_drop={"4": "DP-2"}, only_active={"4": DP}))
        self.assertTrue(self.switcher.switch("xbox")["ok"])
        self.assertEqual(self.switcher.status()["layout"], "xbox")
        back = self.switcher.switch("pc")
        self.assertTrue(back["ok"])
        self.assertEqual(back["layout"], "xbox")
        self.assertTrue(any("DP-2 nie jest widoczny w KWin" in n for n in back["notices"]), back["notices"])
        self.assertTrue(self.saved_state()["saved"], "kopia układu PC zostaje na następną próbę")
        # Użytkownik przełącza wejście przyciskiem na monitorze, monitor wraca do KWin wyłączony.
        def by_hand(state):
            state["ddc"]["input"]["4"] = DP
            state["kscreen"]["outputs"][0]["connected"] = True
        self.change_hardware(by_hand)
        again = self.switcher.switch("pc")
        self.assertTrue(again["ok"])
        self.assertEqual(layout(self.hardware()), TODAY)


class FailureTest(MonitorsCase):
    def test_without_kscreen_doctor_nothing_changes(self):
        os.remove(os.path.join(self.bin, "kscreen-doctor"))
        result = self.switcher.switch("xbox")
        self.assertFalse(result["ok"])
        self.assertIn("brak kscreen-doctor", result["error"])
        self.assertEqual(self.hardware()["ddc"]["input"]["4"], DP, "bez KWin nie przełączamy też wejścia")
        self.assertFalse(self.switcher.status()["available"])

    def test_kwin_refusing_the_layout_puts_the_input_back(self):
        self.change_hardware(lambda s: s["fail"].update({"kscreen-set": "nie da się"}))
        result = self.switcher.switch("xbox")
        self.assertFalse(result["ok"])
        self.assertIn("kscreen-doctor nie zmienił układu: nie da się", result["error"])
        self.assertEqual(self.hardware()["ddc"]["input"]["4"], DP)
        self.assertEqual(layout(self.hardware()), TODAY)
        self.assertEqual(self.switcher.status()["layout"], "pc")

    def test_pc_without_a_snapshot_uses_kwin_data(self):
        def xbox_by_hand(state):
            dp, hdmi = state["kscreen"]["outputs"]
            dp.update(enabled=False, priority=0, currentModeId="4")
            hdmi.update(priority=1)
        self.change_hardware(xbox_by_hand)
        result = self.switcher.switch("pc")
        self.assertTrue(result["ok"])
        self.assertIn("brak zapisanego układu PC — DP-2 włączony w preferowanym trybie jako główny", result["notices"])
        now = layout(self.hardware())
        self.assertEqual(now["DP-2"], (True, (1920, 0), "2560x1440@60", 59.94, 1))

    def test_unknown_layout_is_refused(self):
        with self.assertRaises(ValueError):
            self.switcher.switch("tv")


class CommandLineTest(MonitorsCase):
    def run_cli(self, *args):
        import subprocess, sys
        env = dict(os.environ, TIDE_MONITORS_BIN=self.bin, TIDE_FAKE_SCREENS=self.hw,
                   XDG_STATE_HOME=os.path.join(self.tmp, "xdg-state"))
        path = os.path.join(os.path.dirname(monitors.__file__), "monitors.py")
        return subprocess.run([sys.executable, path, *args], env=env, capture_output=True, text=True)

    def test_status_switch_and_exit_codes(self):
        status = self.run_cli("status", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["layout"], "pc")
        self.assertEqual(self.run_cli("xbox").returncode, 0)
        self.assertEqual(json.loads(self.run_cli("status", "--json").stdout)["layout"], "xbox")
        self.assertEqual(self.run_cli("pc").returncode, 0)
        self.assertEqual(layout(self.hardware()), TODAY)
        self.assertEqual(self.run_cli("tv").returncode, 2)
        os.remove(os.path.join(self.bin, "kscreen-doctor"))
        failed = self.run_cli("xbox")
        self.assertEqual(failed.returncode, 1)
        self.assertIn("brak kscreen-doctor", failed.stderr)


class ServiceTest(MonitorsCase):
    """Obiekt usługi: przełączenie w wątku roboczym, odpowiedź od razu, wynik w Status() po Changed."""

    def setUp(self):
        super().setUp()
        app()
        self.service = MonitorsService(self.switcher)
        self.addCleanup(self.service.stop)
        self.changes = []
        self.service.changed.connect(lambda: self.changes.append(json.loads(self.service.status_json())))

    def status(self):
        return json.loads(self.service.status_json())

    def test_status_reads_kwin_and_offers_the_other_layout(self):
        self.service.start()
        self.assertTrue(wait_until(lambda: self.status()["layout"] == "pc", timeout=5))
        status = self.status()
        self.assertEqual((status["target"], status["available"], status["switching"]), ("xbox", True, ""))

    def test_set_layout_answers_at_once_and_reports_the_result(self):
        self.service.start()
        wait_until(lambda: self.status()["layout"], timeout=5)
        self.assertEqual(json.loads(self.service.set_layout("xbox")), {"accepted": True, "error": ""})
        self.assertEqual(self.status()["switching"], "xbox")
        busy = json.loads(self.service.set_layout("pc"))
        self.assertEqual(busy, {"accepted": False, "error": "przełączenie już trwa"})
        self.assertTrue(wait_until(lambda: self.status()["layout"] == "xbox" and not self.status()["switching"],
                                   timeout=10))
        result = self.status()["result"]
        self.assertEqual((result["ok"], result["layout"], result["changed"]), (True, "xbox", True))
        self.assertTrue(result["at"])
        self.assertEqual(self.status()["target"], "pc")
        self.assertTrue(self.status()["saved"])

    def test_unknown_layout_and_errors_do_not_stop_the_service(self):
        self.assertFalse(json.loads(self.service.set_layout("tv"))["accepted"])
        os.remove(os.path.join(self.bin, "kscreen-doctor"))
        self.service.set_layout("xbox")
        self.assertTrue(wait_until(lambda: self.status()["result"] is not None, timeout=10))
        self.assertIn("brak kscreen-doctor", self.status()["result"]["error"])
        self.assertTrue(wait_until(lambda: self.status()["available"] is False, timeout=5))


@requires_dbus
class DBusTest(MonitorsCase):
    COMMON = ("--no-processes", "--no-notifications", "--no-krunner", "--no-idle", "--no-limits", "--no-coordination",
              "--no-tab-marks", "--no-report", "--no-docker", "--no-board", "--no-models", "--no-tasks",
              "--no-stream-shortcuts")

    def test_status_and_switch_over_dbus(self):
        bus = PrivateBus("tide-monitors-dbus")
        self.addCleanup(bus.stop)
        bus.spawn_daemon("--claude-dir", self.tmp, "--codex-dir", self.tmp, "--workspace", self.tmp, *self.COMMON,
                         extra_env={"TIDE_MONITORS_BIN": self.bin, "TIDE_FAKE_SCREENS": self.hw,
                                    "XDG_STATE_HOME": os.path.join(self.tmp, "daemon-state")})

        def call(member, *args):
            reply = bus.call(SERVICE, "/Monitors", "org.tide.Daemon.Monitors", member, *args)
            return json.loads(reply.arguments()[0])

        self.assertTrue(wait_until(lambda: call("Status")["layout"] == "pc", timeout=10))
        self.assertTrue(call("SetLayout", "xbox")["accepted"])
        self.assertTrue(wait_until(lambda: call("Status")["layout"] == "xbox" and not call("Status")["switching"],
                                   timeout=15))
        self.assertEqual(self.hardware()["ddc"]["input"]["4"], HDMI)
        self.assertTrue(call("SetLayout", "pc")["accepted"])
        self.assertTrue(wait_until(lambda: call("Status")["layout"] == "pc" and not call("Status")["switching"],
                                   timeout=15))
        self.assertEqual(layout(self.hardware()), TODAY)
        xml = bus.call(SERVICE, "/Monitors", "org.freedesktop.DBus.Introspectable", "Introspect").arguments()[0]
        self.assertIn('<signal name="Changed"', xml)


if __name__ == "__main__":
    unittest.main()
