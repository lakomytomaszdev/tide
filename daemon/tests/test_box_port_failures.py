"""PT-171: rezerwacja należy do jednostki, także po utracie klienta lub zapisu dziennika."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tide_daemon.box import runner as br
from tide_daemon.box.ports import PortPool, PoolExhausted
from tide_daemon.cardflow import boxstep
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.journal import Journal


PROFILE = {"ports": {"pool": [4300, 4309]}}
OWNER = "demo-20261106120000-12345678"


class PortFailureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.pool = PortPool("proj", "proj")

    def assert_reserved(self):
        try:
            with self.assertRaises(PoolExhausted):
                self.pool.acquire("other", PROFILE)
        finally:
            self.pool.release("other")

    def test_launch_preserves_ports_after_successful_spawn_and_journal_failure(self):
        for failure in ("result", "box.risk"):
            with self.subTest(failure=failure):
                owner = "K-38/first"
                env = self.pool.acquire(owner, PROFILE)
                runner = mock.Mock()
                runner.start.return_value = (SimpleNamespace(unit="tide-step-first", git_dir="/git"), mock.Mock())
                placement = boxstep.Placement("proj", "proj", "10.0.0.2", False, PROFILE, runner,
                                              docker=True, port_owner=owner, port_env=env)
                engine = FlowEngine.__new__(FlowEngine)
                engine._admit = mock.Mock()
                engine._release_local = mock.Mock()
                engine._box_place = mock.Mock(return_value=placement)
                engine._settings_data = lambda: {}
                engine._hidden = (set(), ())
                engine.procs, engine._box_runners = {}, {}
                engine.tmp_root = self.tmp.name
                engine.journal = Journal(os.path.join(self.tmp.name, failure),
                                         mirror=os.path.join(self.tmp.name, failure + "-mirror"))
                append = engine.journal.append

                def fail(card, event, **fields):
                    if event == failure and (event != "result" or fields.get("ok")):
                        raise OSError("disk full")
                    return append(card, event, **fields)

                selected = SimpleNamespace(tool="codex", binary="codex", route=None, name="test",
                                           model="test", provider="openai", effort="", local=False)
                project = SimpleNamespace(repo=self.tmp.name, allowed_commands=[], denied_commands=[])
                with mock.patch.object(engine.journal, "append", side_effect=fail), \
                        mock.patch.object(boxstep, "before_start"), \
                        mock.patch.object(boxstep.k3, "k3_active", return_value=False), \
                        mock.patch("tide_daemon.tasks.dotnet.is_dotnet", return_value=False), \
                        mock.patch("tide_daemon.tasks.node.is_node", return_value=False):
                    with self.assertRaises((FlowError, OSError)):
                        engine._launch(SimpleNamespace(id="K-38"), project, selected, "PT-171", {},
                                       {"worktree": self.tmp.name, "branch": "ticket/pt-171"}, "first", [],
                                       prompt_text="test")
                runner.start.assert_called_once()
                self.assert_reserved()
                with mock.patch.object(boxstep, "proxy_of", return_value=None):
                    boxstep.recover_network(engine)
                self.assert_reserved()
                self.pool.release(owner)

    def demo(self, state, wait_error=None):
        runner = mock.Mock(box="proj")
        runner.unit_state.return_value = state
        runner.unit_for.return_value = "tide-step-" + OWNER + ".service"
        bg = SimpleNamespace(log_file="/nonexistent/log", exit_file="/nonexistent/exit")
        proc = mock.Mock()
        proc.wait.side_effect = wait_error
        proc.wait.return_value = 0
        runner.start.return_value = (bg, proc)
        step = SimpleNamespace(step_id=OWNER, step_dir=self.tmp.name)
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            if wait_error:
                with self.assertRaises(type(wait_error)):
                    br._demo_run(runner, self.pool, step, PROFILE, ["true"], "marker")
            else:
                br._demo_run(runner, self.pool, step, PROFILE, ["true"], "marker")
        return runner

    def test_demo_client_exit_keeps_active_and_unknown_units_reserved(self):
        for state in ("active", "unknown"):
            with self.subTest(state=state):
                self.demo(state)
                self.assert_reserved()
                self.pool.release(OWNER)

    def test_demo_interrupt_preserves_lease_until_recovery_confirms_inactive(self):
        runner = self.demo("unknown", KeyboardInterrupt())
        runner.kill.assert_called_once()
        runner.unit_state.assert_called_with("proj", "tide-step-" + OWNER + ".service")
        self.assert_reserved()
        for state in ("active", "unknown"):
            self.pool.recover_demos(lambda unit: state)
            self.assert_reserved()
        self.pool.recover_demos(lambda unit: "inactive")
        self.assertEqual(self.pool.acquire("other", PROFILE)["PORT"], "4300")

    def test_demo_inactive_unit_releases_even_when_wait_raises(self):
        self.demo("inactive", RuntimeError("lost client"))
        self.assertEqual(self.pool.acquire("other", PROFILE)["PORT"], "4300")

    def test_demo_normal_finish_releases_ports_and_recovers_older_orphan(self):
        self.pool.acquire("demo-20261105120000-87654321", PROFILE)
        self.demo("inactive")
        self.assertEqual(self.pool.acquire("other", PROFILE)["PORT"], "4300")

    def test_demo_failed_start_releases_ports(self):
        runner = mock.Mock(box="proj")
        runner.start.side_effect = OSError("spawn failed")
        step = SimpleNamespace(step_id=OWNER, step_dir=self.tmp.name)
        with self.assertRaises(OSError):
            br._demo_run(runner, self.pool, step, PROFILE, ["true"], "marker")
        self.assertEqual(self.pool.acquire("other", PROFILE)["PORT"], "4300")

    def test_demo_recovery_never_probes_or_releases_card_reservations(self):
        self.pool.acquire("K-38/first", PROFILE)
        probe = mock.Mock(return_value="inactive")
        self.pool.recover_demos(probe)
        probe.assert_not_called()
        self.assert_reserved()

    def test_demo_live_launcher_is_not_reaped_before_unit_creation(self):
        with self.pool.demo_guard(OWNER):
            self.pool.acquire(OWNER, PROFILE)
            probe = mock.Mock(return_value="inactive")
            self.pool.recover_demos(probe)
            probe.assert_not_called()
            self.assert_reserved()
        units = []
        self.pool.recover_demos(lambda unit: units.append(unit) or "inactive")
        self.assertEqual(units, ["tide-step-" + OWNER + ".service"])
        self.assertEqual(self.pool.acquire("other", PROFILE)["PORT"], "4300")
