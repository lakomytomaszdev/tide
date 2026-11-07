"""Koniec całego procesu po SIGTERM, na prywatnym D-Bus i tymczasowych repozytoriach."""
import os
import json
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import test_cardflow_dbus as flow
from .privatebus import SERVICE, wait_until


class ShutdownTest(flow.FlowCase):
    def spawn(self):
        root = Path(self.base)
        fake_git = root / "fakebin/git"
        shutil.copyfile(Path(__file__).with_name("slow_git.py"), fake_git)
        fake_git.chmod(0o755)
        # Bramka w katalogu wyjściowym kroku jest widoczna także w jego piaskownicy.
        agent = root / "fakebin/claude"
        source = flow.flowfakes.CLAUDE.replace('write("start")', '''
if word == "zycie":
    while not os.path.exists(os.path.join(out, "release")):
        with open(os.path.join(out, "heartbeat"), "w") as fh:
            fh.write(str(time.monotonic_ns()))
        time.sleep(.05)
    word = "gotowe"
write("start")''')
        agent.write_text(source)
        if getattr(self, "unit", None):
            from .lifecycle_systemd import install_controls
            install_controls(root / "fakebin", self.manager_runtime, self.scope_prefix)
            command = f"/usr/bin/python3 {root / 'outside.py'}"
            agent.write_text(source.replace('"command": "echo zewnetrzne-ok"',
                                           '"command": ' + json.dumps(command)))
        self.log_path = root / "lifecycle.log"
        log = self.log_path.open("a")
        self.addCleanup(log.close)
        argv = [sys.executable, str(Path(__file__).with_name("lifecycle_daemon.py")),
             "--claude-dir", str(root / "home/.claude"), "--codex-dir", str(root / "codex"),
             "--workspace", str(root / "ws"), "--board-dir", self.board,
             "--no-processes", "--no-notifications", "--no-tab-marks", "--no-krunner",
             "--no-idle", "--no-limits", "--no-models", "--no-report", "--no-coordination",
             "--no-docker", "--no-activity-look", "--no-monitors", "--no-stream-shortcuts", "--no-youtrack"]
        env = {**self.env, "XDG_STATE_HOME": str(root / "state"), "XDG_DATA_HOME": str(root / "data"),
               "XDG_CACHE_HOME": str(root / "cache"), "TIDE_LIFECYCLE_DIR": self.base}
        if getattr(self, "unit", None):
            from .lifecycle_systemd import start_unit
            self.addCleanup(self.clean_units)
            proc = start_unit(self.unit, argv, {**env, "DBUS_SESSION_BUS_ADDRESS": self.service.address},
                              self.manager_env, self.log_path)
            self.service.children.append(proc)
            self.assertTrue(wait_until(lambda: self.service.bus.interface().isServiceRegistered(SERVICE).value(),
                                       timeout=8), self.log_path.read_text())
            return proc
        return self.service.spawn(argv, SERVICE, extra_env=env, stdout=log, stderr=log)

    def restart_checked(self, columns):
        started = time.monotonic()
        if getattr(self, "unit", None):
            self.daemon.control("restart", check=True)
            self.daemon.pid = int(self.daemon.show("MainPID"))
            self.daemon.returncode = None
        else:
            self.stop_measured()
            self.assertTrue(wait_until(lambda: not self.service.bus.interface().isServiceRegistered(SERVICE).value()))
            self.daemon = self.spawn()
        self.assertTrue(wait_until(lambda: self.service.bus.interface().isServiceRegistered(SERVICE).value(), timeout=5))
        for card, column in columns.items():
            self.wait_column(card, column, timeout=5)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 10)
        print(f"lifecycle: restart -> D-Bus i aktualne karty {elapsed:.3f} s", flush=True)

    def stop_measured(self):
        started = time.monotonic()
        self.daemon.send_signal(signal.SIGTERM)
        try:
            code = self.daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.daemon.send_signal(signal.SIGUSR1)
            time.sleep(.1)
            self.fail("Proces żyje 5 s po SIGTERM:\n" + self.log_path.read_text())
        elapsed = time.monotonic() - started
        self.assertEqual(code, 0, self.log_path.read_text())
        self.assertLessEqual(elapsed, 5)
        print(f"lifecycle: SIGTERM -> koniec procesu {elapsed:.3f} s", flush=True)
        self.assertIn("lifecycle: app.main returned", self.log_path.read_text())

    def test_idle_process_exits_within_five_seconds(self):
        self.stop_measured()

    def test_slow_git_does_not_hold_the_process_after_qt_returns(self):
        self.assertTrue(wait_until(lambda: self.call("List", path="/Worktrees",
                                                     iface="org.tide.Daemon.Worktrees"), timeout=5))
        root = Path(self.base)
        (root / "slow-arm").touch()
        self.service.call(SERVICE, "/Worktrees", "org.tide.Daemon.Worktrees", "Refresh")
        self.assertTrue(wait_until(lambda: (root / "slow-pid").exists(), timeout=5), self.log_path.read_text())
        pids = [int(p) for p in (root / "slow-pid").read_text().split()]
        for pid in pids:
            self.addCleanup(self.kill_owned, pid)
        self.stop_measured()
        self.assertTrue(all(not flow.flowfakes.alive(p) for p in pids), "pozostali potomkowie gita")

    def test_restart_preserves_decision_saved_result_and_review_column(self):
        waiting = self.card(description="ATRAPA: pytanie-opcje")
        waiting_start = self.start(waiting)
        decision = self.wait_column(waiting, "waiting")["derived"]["flow"]["decisions"]
        ready = self.card()
        ready_start = self.start(ready)
        self.wait_column(ready, "review")
        before = {card: self.events(card) for card in (waiting, ready)}
        heads = {r["branch"]: flow.git(self.repo, "rev-parse", r["branch"])
                 for r in (waiting_start, ready_start)}
        result_path = Path(self.of(waiting, "step.started")[0]["stepDir"], "out/result.json")
        saved = result_path.read_bytes()
        self.restart_checked({waiting: "waiting", ready: "review"})
        self.assertEqual(self.shown(waiting)["derived"]["flow"]["decisions"], decision)
        self.assertEqual(result_path.read_bytes(), saved)
        for card in (waiting, ready):
            self.assertEqual(self.events(card), before[card], "restart nie dopisuje ruchu, decyzji ani kroku")
        for branch, sha in heads.items():
            self.assertEqual(flow.git(self.repo, "rev-parse", branch), sha)

    def test_restart_takes_over_an_independent_step_once(self):
        card = self.card(description="ATRAPA: zycie")
        self.assertTrue(self.start(card)["ok"])
        step = self.of(card, "step.started")[0]
        heartbeat = Path(step["stepDir"], "out/heartbeat")
        self.assertTrue(wait_until(lambda: heartbeat.exists() and heartbeat.read_text(), timeout=8))
        pid = self.fake_call(step)["pid"]
        self.addCleanup(self.kill_owned, pid)
        before = heartbeat.read_text()
        self.restart_checked({card: "progress"})
        self.assertTrue(flow.flowfakes.alive(pid))
        self.assertTrue(wait_until(lambda: heartbeat.read_text() != before, timeout=3))
        heartbeat.with_name("release").touch()
        self.wait_column(card, "review")
        self.assertEqual([s["kind"] for s in self.of(card, "step.started")], ["ticket", "review", "close"])
        self.assertEqual(len(self.results(card, "merge")), 1)
        self.assertEqual(len(self.of(card, "step.finished")), 3)
        self.assertEqual(self.of(card, "step.decision"), [])

    @staticmethod
    def kill_owned(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class SystemdRestartTest(ShutdownTest):
    def setUp(self):
        self.manager_env = dict(os.environ)
        self.manager_runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        try:
            probe = subprocess.run(["/usr/bin/systemctl", "--user", "show", "--property=Version", "--value"],
                                   capture_output=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest("brak menedżera systemd użytkownika")
        if probe.returncode:
            self.skipTest("brak menedżera systemd użytkownika")
        suffix = uuid.uuid4().hex
        self.unit = "tide-pt253-test-" + suffix
        self.scope_prefix = "pt253-" + suffix + "-"
        super().setUp()

    def clean_units(self):
        try:
            subprocess.run(["/usr/bin/systemctl", "--user", "stop", self.unit + ".service"],
                           env=self.manager_env, capture_output=True, timeout=6)
        except subprocess.TimeoutExpired:
            subprocess.run(["/usr/bin/systemctl", "--user", "kill", "--kill-whom=all", "--signal=SIGKILL",
                            self.unit + ".service"], env=self.manager_env, capture_output=True, timeout=3)
        found = subprocess.run(["/usr/bin/systemctl", "--user", "list-units", "--all", "--plain", "--no-legend",
                                "--type=scope", self.scope_prefix + "*"], env=self.manager_env,
                               capture_output=True, text=True, timeout=5)
        for line in found.stdout.splitlines():
            unit = line.split()[0]
            if unit.startswith(self.scope_prefix) and unit.endswith(".scope"):
                subprocess.run(["/usr/bin/systemctl", "--user", "stop", unit], env=self.manager_env,
                               capture_output=True, timeout=5)
        subprocess.run(["/usr/bin/systemctl", "--user", "reset-failed", self.unit + ".service"],
                       env=self.manager_env, capture_output=True, timeout=5)

    def test_systemctl_restart_preserves_step_and_outside_command_and_takes_both_results_once(self):
        root = Path(self.base)
        (root / "outside.py").write_text(
            "import os,time\nfrom pathlib import Path\n"
            f"root=Path({self.base!r})\n"
            "(root/'outside-pid').write_text(str(os.getpid()))\n"
            "while not (root/'outside-release').exists():\n"
            "    (root/'outside-heartbeat').write_text(str(time.monotonic_ns()))\n"
            "    time.sleep(.05)\n"
            "print('zewnetrzne-ok', flush=True)\n")
        background = self.card(description="ATRAPA: zycie")
        self.assertTrue(self.start(background)["ok"])
        step = self.of(background, "step.started")[0]
        heartbeat = Path(step["stepDir"], "out/heartbeat")
        self.assertTrue(wait_until(lambda: heartbeat.exists() and heartbeat.read_text(), timeout=8))
        step_pid = self.fake_call(step)["pid"]
        outside = self.card(description="ATRAPA: poza")
        self.assertTrue(self.start(outside)["ok"])
        self.wait_column(outside, "waiting")
        self.assertTrue(self.call("ResolveCardStep", outside, json.dumps({"choice": "run"}))["ok"])
        self.assertTrue(wait_until(lambda: (root / "outside-heartbeat").exists(), timeout=8), self.events(outside))
        outside_pid = int((root / "outside-pid").read_text())
        for pid in (step_pid, outside_pid):
            self.assertIn(self.scope_prefix, Path(f"/proc/{pid}/cgroup").read_text())
            self.assertNotIn(self.unit, Path(f"/proc/{pid}/cgroup").read_text())
        previous = [heartbeat.read_text(), (root / "outside-heartbeat").read_text()]
        self.restart_checked({background: "progress", outside: "progress"})
        for pid in (step_pid, outside_pid):
            self.assertTrue(flow.flowfakes.alive(pid), "niezależny proces przeżywa restart systemd")
        self.assertTrue(wait_until(lambda: heartbeat.read_text() != previous[0] and
                                  (root / "outside-heartbeat").read_text() != previous[1], timeout=3))
        heartbeat.with_name("release").touch()
        (root / "outside-release").touch()
        self.wait_column(background, "review")
        self.wait_column(outside, "review")
        self.assertEqual([s["kind"] for s in self.of(background, "step.started")], ["ticket", "review", "close"])
        self.assertEqual([s["kind"] for s in self.of(outside, "step.started")],
                         ["ticket", "ticket", "review", "close"])
        self.assertEqual(len(self.of(outside, "outside.started")), 1)
        self.assertEqual(len(self.of(outside, "outside.finished")), 1)
        self.assertEqual(len(self.of(outside, "step.decision")), 1)
        for card in (background, outside):
            self.assertEqual(len(self.results(card, "merge")), 1)
        before = {card: self.events(card) for card in (background, outside)}
        self.restart_checked({background: "review", outside: "review"})
        for card in (background, outside):
            self.assertEqual(self.events(card), before[card])
