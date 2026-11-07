"""PT-267: zachowany klient, prywatny D-Bus, staging i odmowy bez lokalnej mutacji."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock
import unittest
import uuid

from tide_daemon.box import cli, builder as bb, lifecycle, profile as bp, state as bs
from tide_daemon.box.incus import Incus
from tide_daemon.box.settings import BoxSettings
from tide_daemon.cardflow import boxcontrol
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.cardflow.journal import Journal

from . import DAEMON_DIR
from . import privatebus
from .fakebin import make_env, calls
from .test_box_builder import BoxCase
from .test_cardflow_dbus import FlowCase


class ClientTest(unittest.TestCase):
    def test_only_status_and_stop_use_the_service(self):
        for argv, method, params in ((["status", "p", "--json"], "BoxStatus", ("p",)),
                                     (["status", "p", "--card", "K-7"], "BoxCardStatus", ("p", "K-7")),
                                     (["stop", "p"], "StopBox", ("p", "")),
                                     (["stop", "p", "--card", "K-7"], "StopBox", ("p", "K-7"))):
            request = mock.Mock(return_value={"ok": True, "slug": "p", "box": "p", "state": "brak", "duration": 0.1})
            self.assertEqual(cli.main(argv, out=io.StringIO(), request=request), 0)
            request.assert_called_once_with(method, *params)

    def test_disabled_commands_never_open_files_or_call_the_service(self):
        for command in cli.DISABLED:
            request, err = mock.Mock(), io.StringIO()
            with mock.patch("builtins.open", side_effect=AssertionError("lokalny plik")):
                self.assertEqual(cli.main([command, "p", "--file", "/secret", "--", "true"],
                                          err=err, request=request), 3)
            request.assert_not_called()
            self.assertIn("wyłączone", err.getvalue())

    def test_missing_bus_fails_before_importing_local_state_or_spawning(self):
        for command in ("status", "stop"):
            with mock.patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": ""}), \
                    mock.patch("subprocess.Popen", side_effect=AssertionError("lokalny proces")):
                err = io.StringIO()
                self.assertEqual(cli.main([command, "p"], err=err), 1)
                self.assertIn("usługa Tide niedostępna", err.getvalue())


class StagingClientTest(unittest.TestCase):
    def test_installed_client_and_disabled_module_entries_without_any_service(self):
        with tempfile.TemporaryDirectory(prefix="pt267-stage-") as folder:
            env = {**os.environ, **make_env(folder), "DBUS_SESSION_BUS_ADDRESS": "",
                   "XDG_STATE_HOME": os.path.join(folder, "state"), "TIDE_BOARD_DIR": os.path.join(folder, "board")}
            install = subprocess.run(["bash", os.path.join(DAEMON_DIR, "install.sh")], env=env,
                                     capture_output=True, text=True, timeout=30)
            self.assertEqual(install.returncode, 0, install.stderr)
            path = Path(env["XDG_DATA_HOME"], "tide/daemon/tide.py")
            env["PYTHONPATH"] = str(path.parent)
            before = calls(env)
            entries = ([str(path), "box"], ["-m", "tide_daemon.box.cli"])
            for entry in entries:
                for command in ("status", "stop", *sorted(cli.DISABLED)):
                    reply = subprocess.run([sys.executable, *entry, command, "proj"], env=env,
                                           cwd=folder, capture_output=True, text=True, timeout=10)
                    self.assertEqual(reply.returncode, 1 if command in ("status", "stop") else 3, reply.stderr)
                    self.assertIn("niedostępna" if command in ("status", "stop") else "wyłączone", reply.stderr)
            reply = subprocess.run([sys.executable, "-m", "tide_daemon.box.runner", "--demo", "proj"], env=env,
                                   cwd=folder, capture_output=True, text=True, timeout=10)
            self.assertEqual(reply.returncode, 3, reply.stderr)
            self.assertIn("wyłączone", reply.stderr)
            self.assertEqual(calls(env), before)
            self.assertFalse(Path(env["TIDE_BOARD_DIR"]).exists())
            self.assertFalse(Path(env["XDG_STATE_HOME"]).exists())


class StopPolicyTest(BoxCase):
    def setUp(self):
        super().setUp()
        self.approve()
        self.b = self.builder()
        self.b.build("plasma-tide")
        self.card = SimpleNamespace(project="plasma-tide")
        board = SimpleNamespace(projects={"plasma-tide": object()}, cards={38: self.card})
        self.engine = SimpleNamespace(store=SimpleNamespace(load=lambda: board),
                                      journal=Journal(bp.board_dir()), procs={}, box_builder=lambda: self.b,
                                      box_settings_source=lambda: self.settings, _unit_state=mock.Mock(return_value="inactive"))

    def stop(self, card=""):
        return boxcontrol.stop(self.engine, "plasma-tide", card)

    def unchanged(self, expected, card=""):
        before, events = len(self.incus_calls()), bs.events("plasma-tide")
        with self.assertRaises(FlowError) as raised:
            self.stop(card)
        self.assertEqual(raised.exception.code, expected)
        self.assertFalse([a for a in self.incus_calls()[before:] if a[0] == "stop"])
        self.assertEqual(bs.events("plasma-tide"), events)

    def test_live_client_and_unknown_or_active_unit_refuse_even_without_box_use(self):
        journal = self.engine.journal
        journal.append("K-38", "card.started", project="plasma-tide")
        journal.append("K-38", "step.started", step="live", boxSlug="plasma-tide", box="plasma-tide",
                       runner="box", unit="live.service")
        for poll, unit in ((None, "inactive"), (0, "active"), (0, "unknown")):
            self.engine.procs["live"] = SimpleNamespace(poll=lambda: poll)
            self.engine._unit_state.return_value = unit
            self.unchanged("box-busy")
        self.engine.procs.clear()  # po restarcie nadal rozstrzyga jednostka, nie proces klienta
        self.engine._unit_state.return_value = "unknown"
        self.unchanged("box-busy")

    def test_unknown_instance_or_corrupted_history_is_not_owned(self):
        state = self.fake_state()
        state["instances"]["plasma-tide"]["config"].pop(bs.RECIPE_KEY)
        self.write_state(state)
        self.unchanged("unknown-box-owner")
        self.b.incus.config_set("plasma-tide", {bs.RECIPE_KEY: "foreign"})
        self.unchanged("unknown-box-owner")
        with open(bs.journal_path("plasma-tide"), "a") as stream:
            stream.write("broken\n")
        self.unchanged("unknown-box-owner")

    def test_card_copy_requires_matching_labels_and_project_membership(self):
        name = self.b.copy_for_card("plasma-tide", "K-38")
        self.b.incus.start(name)
        self.b.incus.config_set(name, {bs.CARD_KEY: "K-39"})
        self.unchanged("unknown-box-owner", "K-38")
        self.b.incus.config_set(name, {bs.CARD_KEY: "K-38"})
        self.card.project = "foreign"
        self.unchanged("box-scope", "K-38")
        self.card.project = "plasma-tide"
        self.assertTrue(self.stop("k-38")["ok"])
        self.assertEqual(self.b.incus.instance("plasma-tide")["status"], "Running")

    def test_stale_recipe_can_be_stopped_but_foreign_copy_labels_are_refused(self):
        self.b.incus.config_set("plasma-tide", {bs.CARD_KEY: "K-39"})
        self.unchanged("unknown-box-owner")
        self.b.incus.config_set("plasma-tide", {bs.CARD_KEY: ""})
        self.approve({"tide-box": 1, "apt": ["jq"]})
        self.assertTrue(self.stop()["ok"])


class ServiceDemoTest(FlowCase):
    def setUp(self):
        # Długie TMPDIR kroku przekracza limit ścieżki AF_UNIX. Prywatna,
        # losowa nazwa abstrakcyjna nie zależy od długości katalogu worktree.
        config = privatebus.BUS_CONFIG.replace("unix:tmpdir={tmp}", "unix:abstract=pt267-" + uuid.uuid4().hex)
        patcher = mock.patch.object(privatebus, "BUS_CONFIG", config)
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def spawn(self):
        fake = make_env(os.path.join(self.base, "boxfake"))
        self.env.update({k: fake[k] for k in ("TIDE_FAKE_INCUS_STATE",)})
        self.env["TIDE_INCUS_BIN"] = os.path.join(self.base, "boxfake", "fakebin", "incus")
        return super().spawn()

    def seed(self):
        env = {**self.env, "XDG_STATE_HOME": os.path.join(self.base, "state")}
        with mock.patch.dict(os.environ, env):
            profile = {"tide-box": 1}
            bp.save_project("proj", profile)
            b = bb.Builder(BoxSettings(), incus=Incus(BoxSettings().project))
            stamp = b.stamp(profile)
            b.incus.init("base", "proj", {bs.RECIPE_KEY: json.dumps(stamp)})
            b.apply_nic_filter("proj")
            b.incus.start("proj")
            bs.record("proj", "box.ready", "proj", hash=stamp["hash"], duration=0.1)

    def client(self, *args, path=None, module=None, env=None):
        invocation = ["-m", module] if module else [str(path or Path(DAEMON_DIR) / "tide.py"), "box"]
        return subprocess.run([sys.executable, *invocation, *args], capture_output=True, text=True, timeout=70,
                              cwd=self.base, env={**os.environ, **self.service.env, **self.env,
                                                 "XDG_STATE_HOME": os.path.join(self.base, "state"),
                                                 "PYTHONPATH": str(Path(path).parent if path else DAEMON_DIR), **(env or {})})

    def test_demo_status_stop_allowed_then_busy_or_foreign_stop_denied(self):
        self.seed()
        status = self.client("status", "proj", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertTrue(json.loads(status.stdout)["running"])
        stopped = self.client("stop", "proj")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertFalse(self.call("BoxStatus", "proj")["running"])
        with mock.patch.dict(os.environ, self.env):
            bs.mark_in_use("proj", "proj", "live-step")
        before = Path(self.env["TIDE_BOARD_DIR"], "projects/proj/box/journal.jsonl").read_bytes()
        denied = self.client("stop", "proj")
        self.assertEqual(denied.returncode, 3, denied.stderr)
        self.assertIn("live-step", denied.stderr)
        self.assertEqual(Path(self.env["TIDE_BOARD_DIR"], "projects/proj/box/journal.jsonl").read_bytes(), before)
        with mock.patch.dict(os.environ, self.env):
            bs.mark_released("proj", "proj", "live-step")
            Incus(BoxSettings().project).config_set("proj", {bs.RECIPE_KEY: "foreign"})
        foreign = self.client("stop", "proj")
        self.assertEqual(foreign.returncode, 3, foreign.stderr)
        self.assertIn("nieznana własność", foreign.stderr)
        self.assertEqual(self.call("StopBox", "../foreign", "")["error"], "bad-project")
        self.assertEqual(self.call("StopBox", "proj", "K-999")["error"], "box-scope")
        self.assertEqual(self.call("BoxCardStatus", "proj", "bad")["error"], "bad-card")

    def test_staging_client_and_module_entries_and_missing_service(self):
        self.seed()
        staging = make_env(os.path.join(self.base, "staging"))
        installed = subprocess.run(["bash", os.path.join(DAEMON_DIR, "install.sh")],
                                   env={**os.environ, **staging}, capture_output=True, text=True, timeout=30)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        path = Path(staging["XDG_DATA_HOME"], "tide/daemon/tide.py")
        self.assertTrue(path.is_file())
        before = Path(self.env["TIDE_BOARD_DIR"], "projects/proj/box/journal.jsonl").read_bytes()
        for command in cli.DISABLED:
            for module in (None, "tide_daemon.box.cli"):
                denied = self.client(command, "proj", path=path, module=module)
                self.assertEqual(denied.returncode, 3, denied.stderr)
                self.assertIn("wyłączone", denied.stderr)
        demo = self.client("--demo", "proj", path=path, module="tide_daemon.box.runner")
        self.assertEqual(demo.returncode, 3, demo.stderr)
        self.assertIn("wyłączone", demo.stderr)
        self.assertEqual(self.client("status", "proj", path=path).returncode, 0)
        self.daemon.terminate()
        self.daemon.wait(10)
        for command in ("status", "stop"):
            absent = self.client(command, "proj", path=path)
            self.assertEqual(absent.returncode, 1, absent.stderr)
            self.assertIn("usługa Tide niedostępna", absent.stderr)
        self.assertEqual(Path(self.env["TIDE_BOARD_DIR"], "projects/proj/box/journal.jsonl").read_bytes(), before)
