"""PT-171: porty w silniku — oczekiwanie, zwolnienie i odzyskanie po restarcie."""
import json
import os
from unittest import mock

from tide_daemon.box.ports import PortPool, PoolExhausted
from tide_daemon.cardflow import boxstep
from tide_daemon.cardflow.engine import FlowDeferred

from .boxflow import BoxFlow, SLUG
from .privatebus import wait_until

PROFILE = {"tide-box": 1, "ports": {"pool": [49123, 49132], "env": ["APP_PORT"]}}


class PortPlacementTest(BoxFlow):
    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.card_id = self.card()
        self.engine.journal.append(self.card_id, "card.started", project=SLUG)
        self.build_box(PROFILE)

    def place(self, step):
        return self.engine._box_place(self.card_id, mock.Mock(slug=SLUG), mock.Mock(provider="openai"),
                                      "PT-1", "ticket", "", step)

    def test_exhaustion_waits_without_start_and_release_allows_retry(self):
        placement = self.place("first")
        with self.assertRaises(FlowDeferred):
            self.place("second")
        waits = [e for e in self.engine.journal.read(self.card_id) if e["type"] == "step.waiting"]
        self.assertIn("porty", waits[-1]["reason"])
        self.assertEqual(waits[-1]["cause"], "box")
        boxstep.start_failed(self.engine, placement, "first")
        self.assertEqual(self.place("second").port_env["PORT"], "49123")

    def test_release_after_error_works_without_proxy_and_is_idempotent(self):
        placement = self.place("first")
        step = {"step": "first", **placement.entry()}
        self.engine.journal.append(self.card_id, "step.started", **step, kind="ticket", ticket="PT-1")
        self.engine.journal.append(self.card_id, "step.finished", step="first", result="error")
        with mock.patch.object(boxstep, "proxy_of", return_value=None):
            boxstep.recover_network(self.engine)
            boxstep.release(self.engine, self.card_id, step)
        self.assertEqual(self.place("second").port_env["PORT"], "49123")

    def test_recovery_releases_reservation_before_step_start_intent(self):
        self.place("first")
        boxstep.recover_network(self.engine)
        self.assertEqual(self.place("second").port_env["PORT"], "49123")

    def test_recovery_keeps_reservation_of_open_start_intent(self):
        placement = self.place("first")
        self.engine.journal.append(self.card_id, "intent", action="step-start", key="first",
                                   step="first", ticket="PT-1", **placement.entry())
        # Bez odtwarzania sieci: otwarty zamiar ma testować wyłącznie trwałość portów.
        with mock.patch.object(boxstep, "proxy_of", return_value=None):
            boxstep.recover_network(self.engine)
        with self.assertRaises(PoolExhausted):
            PortPool(SLUG, SLUG).acquire("other", PROFILE)

    def test_recovered_start_preserves_owner_and_releases_ports_when_finished(self):
        from tide_daemon.cardflow.state import fold
        placement = self.place("first")
        journal = self.engine.journal
        journal.append(self.card_id, "intent", action="step-start", key="first", ticket="PT-1", **placement.entry())
        journal.append(self.card_id, "result", action="step-start", key="first", ok=True)
        self.engine._recover_started_steps(self.card_id, fold(journal.read(self.card_id)))
        step = fold(journal.read(self.card_id)).steps["first"]
        self.assertEqual(step.get("boxPortOwner"), placement.port_owner)
        journal.append(self.card_id, "step.finished", step="first", result="error")
        with mock.patch.object(boxstep, "proxy_of", return_value=None):
            boxstep.recover_network(self.engine)
        self.assertEqual(self.place("second").port_env["PORT"], "49123")


class PortExecutionTest(BoxFlow):
    def test_two_parallel_steps_get_disjoint_ranges_in_their_env_files(self):
        from .test_cardflow_plan_integration import PlanFlowTest
        from .test_cardflow_gitops import LocalStepGit
        for patch in (mock.patch("tide_daemon.cardflow.gitops.sandbox_available", return_value=True),
                      mock.patch("tide_daemon.cardflow.gitops.StepGit", LocalStepGit)):
            patch.start()
            self.addCleanup(patch.stop)
        self.build_box({**PROFILE, "ports": {"pool": [49123, 49142], "env": ["APP_PORT"]}})
        # D7 serializuje karty tego samego repozytorium; równoległe tickety należą do jednej karty.
        card = self.card()
        path = PlanFlowTest.proposal(self, card)
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source.replace("| Opus | #1 |", "| Opus | |"))
        for number in (1, 2):
            with open(os.path.join(os.path.dirname(path), "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\nATRAPA: czekaj\n- [ ] Wynik.\n")
        self.assertTrue(PlanFlowTest.submit(self, card, path)["ok"])
        self.assertTrue(PlanFlowTest.approve(self, card)["ok"])
        starts = lambda: [e for e in self.of(card, "step.started") if e.get("runner") == "box"]
        # Kroki żyją do odczytu obu zakresów, bez zależności od szybkości przygotowania pudełka.
        self.assertTrue(wait_until(lambda: len(starts()) == 2, timeout=30), self.events(card))
        steps = starts()
        for step in steps:
            self.addCleanup(self.engine._runner_for(step).kill, step,
                            proc=self.engine.procs.get(step["step"]))
        values = []
        for step in steps:
            path = os.path.join(step["stepDir"], "in", "env")
            with open(path, encoding="utf-8") as fh:
                values.append(dict(line.split("=", 1) for line in fh.read().splitlines()))
        self.assertEqual({e["TIDE_PORT_BASE"] for e in values}, {"49123", "49133"})
        for step in steps:
            self.assertTrue(self.engine._runner_for(step).kill(step, proc=self.engine.procs.get(step["step"])))
        names = {step["step"] for step in steps}
        self.assertTrue(wait_until(lambda: len([e for e in self.of(card, "step.finished")
                                               if e["step"] in names]) == 2, timeout=30))

    def test_step_env_receives_ports_and_finish_returns_them(self):
        self.build_box(PROFILE)
        card = self.card(description="Dodaj eksport.\n\nATRAPA: powoli 2")
        self.assertTrue(self.start(card)["ok"])
        step = self.started(card)
        path = os.path.join(step["stepDir"], "out", "fake-call.json")
        self.assertTrue(wait_until(lambda: os.path.isfile(path), timeout=15))
        with open(path, encoding="utf-8") as fh:
            call = json.load(fh)
        for key in ("PORT", "E2E_PORT", "TIDE_PORT_BASE", "APP_PORT"):
            self.assertEqual(call["env"][key], "49123")
        self.assertFalse(any("49123" in str(a) for a in self.incus_calls("exec")))
        self.assertNotIn("DOCKER_HOST", call["env"])
        self.assertTrue(wait_until(lambda: self.of(card, "step.finished"), timeout=30))
        # step.finished jest zapisywane przed release; oczekiwanie na box.release zamyka to okno.
        from tide_daemon.box import state as bs
        self.assertTrue(wait_until(lambda: not bs.running_steps(SLUG, SLUG), timeout=10))
        self.assertEqual(PortPool(SLUG, SLUG).acquire("next", PROFILE)["PORT"], "49123")
