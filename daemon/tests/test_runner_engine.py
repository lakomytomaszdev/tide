"""PT-180 — silnik kart dostaje Runner w konstruktorze i woła wyłącznie `start` / `kill` / `env`.

Atrapa Runnera ma tylko te trzy metody (każda inna próba dostępu kończy się AttributeError), zapisuje wywołania
i deleguje do HostRunnera z atrapą systemd-run — kroki są prawdziwymi procesami atrap, jak w test_cardflow.py.
"""
import json
import os
import unittest

from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.tasks.runner import HostRunner
from . import flowfakes
from .privatebus import wait_until
from .test_cardflow import FlowInProcess


class RecordingRunner:
    """Tylko umowa Runnera; nic ponad `start`, `kill`, `env`."""
    __slots__ = ("inner", "calls")

    def __init__(self, inner):
        self.inner = inner
        self.calls = []

    def start(self, step, **kw):
        self.calls.append(("start", step.step_id, step.tool))
        return self.inner.start(step, **kw)

    def kill(self, step, **kw):
        self.calls.append(("kill", step.get("step"), step.get("unit")))
        return self.inner.kill(step, **kw)

    def env(self, step):
        self.calls.append(("env", step.step_id))
        return self.inner.env(step)


class EngineRunnerTest(FlowInProcess):
    guard_minutes = 0.05   # 3 s: strażnik zatrzymuje krok bez postępu
    def inject(self):
        runner = RecordingRunner(HostRunner(self.fakes["systemd-run"]))
        self.flow.engine.runner = runner
        return runner

    def test_engine_defaults_to_host_runner(self):
        engine = FlowEngine(self.store, systemd_run="/x/systemd-run")
        self.assertIsInstance(engine.runner, HostRunner)
        self.assertEqual(engine.runner.systemd_run, "/x/systemd-run")
        fake = object()
        self.assertIs(FlowEngine(self.store, runner=fake).runner, fake)

    def test_launch_goes_through_the_runner_start(self):
        runner = self.inject()
        card_id = self.card()
        self.assertTrue(self.start(card_id)["ok"])
        step = self.started(card_id)
        self.assertTrue(self.unit_pid(step))
        starts = [call for call in runner.calls if call[0] == "start"]
        self.assertEqual(starts[0], ("start", step["step"], "claude"))
        self.assertEqual(step["unit"], "tide-step-" + step["step"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "step.finished"), timeout=40), self.events(card_id))
        self.assertEqual(self.of(card_id, "step.finished")[0]["result"], "done")

    def test_guard_stop_goes_through_the_runner_kill_and_keeps_changes(self):
        runner = self.inject()
        card_id = self.card(description="ATRAPA: czekaj")
        reply = self.start(card_id)
        step = self.started(card_id)
        pid = self.unit_pid(step)
        self.assertTrue(flowfakes.alive(pid))
        self.wait_column(card_id, "waiting", timeout=30)
        kills = [call for call in runner.calls if call[0] == "kill"]
        self.assertTrue(kills, runner.calls)
        self.assertEqual(kills[0][1:], (step["step"], step["unit"]))
        self.assertTrue(wait_until(lambda: not flowfakes.alive(pid), timeout=5), "krok zatrzymany")
        finished = self.of(card_id, "step.finished")[0]
        self.assertTrue(finished["wip"]["stopped"])
        self.assert_changes_kept(finished, step, reply["branch"])
        self.assertTrue({call[0] for call in runner.calls} <= {"start", "kill", "env"})

    def test_restarted_engine_stops_a_step_known_only_by_its_unit(self):
        """Format wpisu kroku bez zmian: po restarcie usługi (brak Popen) kill dostaje samą jednostkę."""
        card_id = self.card(description="ATRAPA: czekaj")
        self.start(card_id)
        step = self.started(card_id)
        pid = self.unit_pid(step)
        self.flow.stop()
        self.flow = self.make_flow()
        runner = self.inject()
        self.assertEqual(self.flow.engine.procs, {})
        self.wait_column(card_id, "waiting", timeout=40)
        self.assertTrue(wait_until(lambda: not flowfakes.alive(pid), timeout=5), "krok zatrzymany")
        self.assertIn("kill", {call[0] for call in runner.calls})


if __name__ == "__main__":
    unittest.main()
