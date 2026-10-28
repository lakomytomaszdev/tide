"""Wejście prawego monitora PC ⇄ Xbox (tide_daemon/monitors.py, monitors_service.py, PT-38).

`ddcutil` (i `kscreen-doctor`, żeby sprawdzić, że nikt go nie woła) to atrapy (fake_screens.py) w katalogu podanym
przez $TIDE_MONITORS_BIN — prawdziwe monitory, KWin i magistrale I²C nie są dotykane. Stan „sprzętu” atrapy
to plik JSON, więc test sprawdza wynik (wejście monitora), a nie listę argumentów.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from . import fake_screens
from .privatebus import SERVICE, PrivateBus, app, requires_dbus, wait_until
from tide_daemon import monitors
from tide_daemon.monitors_service import MonitorsService

DP, HDMI = 15, 0x11


class MonitorsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-monitors-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = os.path.join(self.tmp, "bin")
        self.hw = os.path.join(self.tmp, "screens.json")
        fake_screens.install(self.bin, self.hw)
        self.kwin_before = self.hardware()["kscreen"]
        self.sleeps = []
        self.switcher = self.make()

    def make(self):
        tools = monitors.Tools(bin_dir=self.bin, env={"TIDE_FAKE_SCREENS": self.hw}, sleep=self.sleeps.append)
        return monitors.Switcher(monitors.load_config(), tools, state_dir=os.path.join(self.tmp, "state"))

    def hardware(self) -> dict:
        return fake_screens.load(self.hw)

    def change_hardware(self, change) -> None:
        state = self.hardware()
        change(state)
        fake_screens.save(self.hw, state)

    def dp_input(self) -> int:
        return self.hardware()["ddc"]["input"]["4"]

    def writes(self) -> list:
        return [c for c in self.hardware()["calls"] if "setvcp" in c]

    def assertKwinUntouched(self):
        hw = self.hardware()
        self.assertEqual([c for c in hw["calls"] if c[0] == "kscreen-doctor"], [], "KWin nikt nie pyta ani nie zmienia")
        self.assertEqual(hw["kscreen"], self.kwin_before)


class ConfigTest(unittest.TestCase):
    def test_repository_config_has_the_inputs_read_from_the_monitor(self):
        cfg = monitors.load_config()
        self.assertEqual((cfg.monitor, cfg.input_pc, cfg.input_xbox), ("DP-2", 0x0F, 0x11))


class SwitchTest(MonitorsCase):
    def test_xbox_changes_only_the_right_monitor_input(self):
        result = self.switcher.switch("xbox")
        self.assertEqual(result, {"ok": True, "layout": "xbox", "changed": True, "error": ""})
        self.assertEqual(self.dp_input(), HDMI)
        self.assertEqual(self.hardware()["ddc"]["input"]["2"], DP, "lewego monitora DDC nie rusza")
        self.assertKwinUntouched()
        self.assertEqual((self.switcher.status()["layout"], self.switcher.status()["target"]), ("xbox", "pc"))

    def test_pc_puts_displayport_back(self):
        self.switcher.switch("xbox")
        result = self.switcher.switch("pc")
        self.assertEqual((result["ok"], result["layout"], result["changed"]), (True, "pc", True))
        self.assertEqual(self.dp_input(), DP)
        self.assertKwinUntouched()

    def test_bus_number_comes_from_detect_not_from_config(self):
        def moved(state):
            state["ddc"]["buses"]["DP-2"] = 7
            state["ddc"]["input"]["7"] = DP
            state["ddc"]["reachable"]["7"] = True
        self.change_hardware(moved)
        self.assertTrue(self.switcher.switch("xbox")["ok"])
        self.assertEqual(self.hardware()["ddc"]["input"]["7"], HDMI)
        self.assertEqual(self.dp_input(), DP)

    def test_invalid_display_blocks_are_skipped(self):
        # Blok „Invalid display” ze złączem DP-2 nie może dać magistrali.
        self.change_hardware(lambda s: s["ddc"].update(invalid={"DP-2": 9}))
        self.assertTrue(self.switcher.switch("xbox")["ok"])
        self.assertEqual(self.dp_input(), HDMI)
        self.assertFalse(any(c[:3] == ["ddcutil", "--bus", "9"] for c in self.hardware()["calls"]))
        self.assertIsNone(monitors.parse_detect("Invalid display\n   I2C bus:  /dev/i2c-9\n"
                                                "   DRM connector:  card1-DP-2\n", "DP-2"))


class IdempotenceTest(MonitorsCase):
    def test_xbox_twice_writes_once(self):
        self.switcher.switch("xbox")
        again = self.switcher.switch("xbox")
        self.assertEqual((again["ok"], again["changed"]), (True, False))
        self.assertEqual(len(self.writes()), 1)

    def test_pc_when_already_pc_writes_nothing(self):
        result = self.switcher.switch("pc")
        self.assertEqual((result["ok"], result["changed"]), (True, False))
        self.assertEqual(self.writes(), [])

    def test_xbox_again_when_the_monitor_does_not_answer_on_hdmi(self):
        # Na wejściu HDMI monitor nie odpowiada przez DisplayPort: potwierdzony Xbox nie jest zapisywany ponownie.
        self.change_hardware(lambda s: s["ddc"].update(only_active={"4": DP}))
        self.switcher.switch("xbox")
        again = self.switcher.switch("xbox")
        self.assertEqual((again["ok"], again["changed"]), (True, False))
        self.assertEqual(len(self.writes()), 1)

    def test_pc_puts_the_input_back_even_when_the_state_says_pc(self):
        self.change_hardware(lambda s: s["ddc"]["input"].update({"4": HDMI}))  # przełączone przyciskiem
        self.assertTrue(self.switcher.switch("pc")["changed"])
        self.assertEqual(self.dp_input(), DP)


class DdcFailureTest(MonitorsCase):
    def test_without_ddcutil_it_is_an_error_with_a_hint(self):
        os.remove(os.path.join(self.bin, "ddcutil"))
        result = self.switcher.switch("xbox")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"],
                         "brak ddcutil — przełącz wejście monitora DP-2 na HDMI z Xboxem przyciskiem na monitorze")
        status = self.switcher.status()
        self.assertEqual((status["layout"], status["available"]), ("pc", False))
        self.assertKwinUntouched()

    def test_monitor_not_answering_is_retried_and_reported(self):
        self.change_hardware(lambda s: s["ddc"]["reachable"].update({"4": False}))
        result = self.switcher.switch("xbox")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "DP-2 nie odpowiada na DDC — przełącz wejście monitora DP-2 na HDMI "
                                          "z Xboxem przyciskiem na monitorze")
        self.assertEqual(len(self.sleeps), 2, "trzy próby co retry_s")
        self.assertEqual(self.switcher.status()["layout"], "pc", "stan przy poprzednim wejściu")
        self.assertFalse(self.switcher.status()["interrupted"])

    def test_failed_return_to_pc_can_be_retried(self):
        # Monitor na HDMI nie odpowiada przez DisplayPort: powrót to błąd, stan zostaje „xbox”, widżet proponuje PC.
        self.change_hardware(lambda s: s["ddc"].update(only_active={"4": DP}))
        self.switcher.switch("xbox")
        back = self.switcher.switch("pc")
        self.assertFalse(back["ok"])
        self.assertIn("przełącz wejście monitora DP-2 na DisplayPort przyciskiem na monitorze", back["error"])
        self.assertEqual((back["layout"], self.switcher.status()["target"]), ("xbox", "pc"))
        self.change_hardware(lambda s: s["ddc"].update(only_active={}))  # np. KWin skończył używać DDC
        self.assertTrue(self.switcher.switch("pc")["ok"])
        self.assertEqual(self.dp_input(), DP)

    def test_monitor_missing_from_detect(self):
        self.change_hardware(lambda s: s["ddc"]["buses"].pop("DP-2"))
        result = self.switcher.switch("xbox")
        self.assertFalse(result["ok"])
        self.assertIn("DP-2 nie odpowiada na DDC (ddcutil detect)", result["error"])

    def test_interrupted_switch_offers_pc(self):
        # Usługa zabita między zapisem „pending” a końcem: stan liczy się jak Xbox, powrót do PC jest bezpieczny.
        os.makedirs(os.path.join(self.tmp, "state"))
        with open(os.path.join(self.tmp, "state", "monitors.json"), "w", encoding="utf-8") as fh:
            json.dump({"layout": "pc", "pending": "xbox"}, fh)
        status = self.switcher.status()
        self.assertEqual((status["layout"], status["target"], status["interrupted"]), ("xbox", "pc", True))
        self.assertTrue(self.switcher.switch("pc")["ok"])
        self.assertFalse(self.switcher.status()["interrupted"])

    def test_unknown_layout_is_refused(self):
        with self.assertRaises(ValueError):
            self.switcher.switch("tv")


class CommandLineTest(MonitorsCase):
    def run_cli(self, *args):
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
        self.assertEqual(self.dp_input(), DP)
        self.assertEqual(self.run_cli("tv").returncode, 2)
        os.remove(os.path.join(self.bin, "ddcutil"))
        failed = self.run_cli("xbox")
        self.assertEqual(failed.returncode, 1)
        self.assertIn("brak ddcutil", failed.stderr)


class ServiceTest(MonitorsCase):
    """Obiekt usługi: przełączenie w wątku roboczym, odpowiedź od razu, wynik w Status() po Changed."""

    def setUp(self):
        super().setUp()
        app()
        self.service = MonitorsService(self.switcher)
        self.addCleanup(self.service.stop)

    def status(self, service=None):
        return json.loads((service or self.service).status_json())

    def settled(self, layout, service=None):
        return wait_until(lambda: self.status(service)["layout"] == layout and not self.status(service)["switching"]
                          and self.status(service)["result"] is not None, timeout=10)

    def test_set_layout_answers_at_once_and_reports_the_result(self):
        self.assertEqual(self.status()["layout"], "pc")
        self.assertEqual(json.loads(self.service.set_layout("xbox")), {"accepted": True, "error": ""})
        self.assertEqual(self.status()["switching"], "xbox")
        self.assertEqual(json.loads(self.service.set_layout("pc")),
                         {"accepted": False, "error": "przełączenie już trwa"})
        self.assertTrue(self.settled("xbox"))
        result = self.status()["result"]
        self.assertEqual((result["ok"], result["layout"], result["changed"], result["auto"]),
                         (True, "xbox", True, False))
        self.assertEqual(self.status()["target"], "pc")

    def test_errors_do_not_stop_the_service(self):
        self.assertFalse(json.loads(self.service.set_layout("tv"))["accepted"])
        os.remove(os.path.join(self.bin, "ddcutil"))
        self.service.set_layout("xbox")
        self.assertTrue(self.settled("pc"))
        self.assertIn("brak ddcutil", self.status()["result"]["error"])
        self.assertFalse(self.status()["available"])

    def test_leaving_stream_puts_the_monitor_back_on_pc(self):
        self.service.set_layout("xbox")
        self.assertTrue(self.settled("xbox"))
        self.service.stream_changed(True)     # w Stream nic się nie dzieje
        self.assertEqual(self.status()["switching"], "")
        self.service.stream_changed(False)    # powrót do pracy
        self.assertEqual(self.status()["switching"], "pc")
        self.assertTrue(self.settled("pc"))
        self.assertEqual(self.dp_input(), DP)
        self.assertTrue(self.status()["result"]["auto"])

    def test_service_start_in_work_with_xbox_state_goes_back_to_pc(self):
        self.switcher.switch("xbox")  # stan z poprzedniej sesji usługi
        fresh = MonitorsService(self.make())
        self.addCleanup(fresh.stop)
        fresh.stream_changed(False)   # pierwsza odpowiedź ActivityManagera po starcie: praca
        self.assertTrue(self.settled("pc", fresh))
        self.assertEqual(self.dp_input(), DP)

    def test_work_with_pc_does_nothing(self):
        self.service.stream_changed(False)
        self.assertEqual((self.status()["switching"], self.status()["result"]), ("", None))
        self.assertEqual(self.writes(), [])


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

        self.assertEqual(call("Status")["layout"], "pc")
        self.assertTrue(call("SetLayout", "xbox")["accepted"])
        self.assertTrue(wait_until(lambda: call("Status")["layout"] == "xbox" and not call("Status")["switching"],
                                   timeout=15))
        self.assertEqual(self.dp_input(), HDMI)
        self.assertTrue(call("SetLayout", "pc")["accepted"])
        self.assertTrue(wait_until(lambda: call("Status")["layout"] == "pc" and not call("Status")["switching"],
                                   timeout=15))
        self.assertEqual(self.dp_input(), DP)
        self.assertKwinUntouched()
        xml = bus.call(SERVICE, "/Monitors", "org.freedesktop.DBus.Introspectable", "Introspect").arguments()[0]
        self.assertIn('<signal name="Changed"', xml)


if __name__ == "__main__":
    unittest.main()
