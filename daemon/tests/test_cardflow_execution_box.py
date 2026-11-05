"""Krok karty w pudełku w silniku (PT-181, #18): wybór runnera według rodzaju kroku i stanu projektu/pudełka/karty,
`boxWait`, start pudełka, kopia per karta, polityka proxy, odmowa proxy → `denied`, `box.risk`, wznowienie
i restart usługi. Wszystko na atrapie `incus` (boxflow.py); prawdziwy krok w pudełku to #28.
"""
import json
import os
import unittest
from unittest import mock

from tide_daemon.box import profile as bp
from tide_daemon.box import runner as br
from tide_daemon.box import state as bs
from tide_daemon.cardflow import boxstep, boxwait, engine as flow_engine
from tide_daemon.cardflow.engine import BOX_STEP_KINDS, FlowDeferred
from tide_daemon.cardflow.state import fold
from tide_daemon.tasks.runner import HostRunner

from .boxflow import SLUG, BoxFlow
from .privatebus import wait_until

PLANNING_KINDS = ("planning", "plan-fix", "plan-amend", "plan-revise", "plan-review")


class Item:
    def __init__(self, card_id):
        self.id = card_id


class Project:
    slug = SLUG


class Selected:
    provider = "anthropic"


class PlacementTest(BoxFlow):
    """`boxstep.place` / `_box_place` wołane wprost (monitor kroków zatrzymany)."""

    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.card_id = self.card()

    def place(self, kind="ticket", parent=""):
        return boxstep.place(self.engine, self.card_id, SLUG, kind, parent, BOX_STEP_KINDS)

    def box_place(self, kind="ticket"):
        return self.engine._box_place(self.card_id, Project(), Selected(), "PT-1", kind, "")

    def waits(self):
        return [e for e in self.events_now() if e["type"] == "step.waiting"]

    def events_now(self):
        return self.engine.journal.read(self.card_id)

    def box_waits(self):
        return [e.get("value") for e in self.events_now() if e["type"] == "box.wait"]

    def test_box_step_kinds_constant(self):
        self.assertEqual(flow_engine.BOX_STEP_KINDS, ("ticket", "fix", "review", "close"))
        for kind in PLANNING_KINDS + ("conflict",):
            self.assertNotIn(kind, BOX_STEP_KINDS)

    def test_every_box_kind_goes_to_box_runner_when_box_is_clean_and_compatible(self):
        self.build_box()
        for kind in BOX_STEP_KINDS:
            placement = self.place(kind)
            self.assertIsInstance(placement, boxstep.Placement, kind)
            self.assertIsInstance(placement.runner, br.BoxRunner, kind)
            self.assertEqual((placement.box, placement.slug, placement.per_card), (SLUG, SLUG, False))
            self.assertTrue(placement.ip)
            self.assertEqual(placement.entry()["runner"], "box")

    def test_planning_kinds_stay_on_host_even_with_a_ready_box(self):
        self.build_box()
        for kind in PLANNING_KINDS:
            self.assertIsNone(self.place(kind), kind)

    def test_without_profile_box_or_box_enabled_the_step_stays_on_host(self):
        self.assertIsNone(self.place())                     # brak profilu
        self.approve()
        self.assertIsNone(self.place())                     # profil, ale pudełka nie ma
        self.build_box(running=False)
        self.box_enabled = False
        self.assertIsNone(self.place())                     # box.enabled: false
        self.assertEqual(self.incus_calls("start"), [])     # pudełka nikt nie uruchamia

    def test_environment_wait_blocks_the_start(self):
        self.build_box()
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.ENVIRONMENT, source="ui")
        with self.assertRaises(FlowDeferred):
            self.box_place()
        self.assertEqual(self.waits()[-1]["reason"], boxstep.WAIT_ENVIRONMENT)
        self.assertEqual(self.waits()[-1]["cause"], "box")
        self.assertEqual(fold(self.events_now()).box_wait, boxwait.ENVIRONMENT)

    def test_recipe_hash_mismatch_waits_for_rebuild(self):
        self.build_box(stamp_profile={"tide-box": 1, "apt": ["jq"]})
        with self.assertRaises(FlowDeferred):
            self.box_place()
        self.assertEqual(self.waits()[-1]["reason"], "czeka na pudełko (do przebudowy)")
        self.assertEqual(fold(self.events_now()).box_wait, boxwait.REBUILD)
        # Kolejny tick nie dubluje zdarzeń.
        with self.assertRaises(FlowDeferred):
            self.box_place()
        self.assertEqual(len(self.waits()), 1)
        self.assertEqual(self.box_waits(), ["rebuild"])

    def test_missing_nic_filtering_waits_for_rebuild(self):
        self.build_box(nic=False)
        with self.assertRaises(FlowDeferred):
            self.box_place()
        self.assertEqual(fold(self.events_now()).box_wait, boxwait.REBUILD)
        rebuild = [e for e in self.events_now() if e["type"] == "box.wait"][-1]
        self.assertEqual(rebuild["why"], "brak filtrowania NIC")

    def test_rebuilt_box_clears_the_rebuild_wait(self):
        self.build_box(nic=False)
        with self.assertRaises(FlowDeferred):
            self.box_place()
        self.builder().apply_nic_filter(SLUG)
        self.assertIsNotNone(self.place())
        self.assertEqual(fold(self.events_now()).box_wait, "")

    def test_stopped_box_is_started_with_start_wait_cleared_after_ready(self):
        self.build_box(running=False)
        placement = self.place()
        self.assertIsNotNone(placement)
        self.assertEqual(len(self.incus_calls("start")), 1)
        ready = [c for c in self.incus_calls("exec") if c[-1] == "true"]
        self.assertTrue(ready, "gotowość: incus exec … true")
        self.assertEqual(self.box_waits(), ["start", ""])
        self.assertEqual(fold(self.events_now()).box_wait, "")
        self.assertEqual(self.incus().instance(SLUG)["status"], "Running")

    def test_per_card_copies_once_and_runs_in_the_copy(self):
        self.build_box({"tide-box": 1, "box": "per-card"}, running=False)
        first = self.place()
        second = self.place("review")
        self.assertEqual(first.box, bs.card_box_name(SLUG, self.card_id))
        self.assertEqual(second.box, first.box)
        self.assertEqual(first.runner.box, first.box)
        self.assertEqual(len(self.incus_calls("copy")), 1)
        self.assertEqual(self.incus().instance(first.box)["status"], "Running")
        self.assertEqual(self.incus().instance(SLUG)["status"], "Stopped")   # pudełko projektu nietknięte

    def test_resume_inherits_the_runner_of_the_resumed_step(self):
        self.build_box()
        journal = self.engine.journal
        journal.append(self.card_id, "step.started", step="host-1", kind="ticket", ticket="PT-1", unit="tide-step-host-1")
        journal.append(self.card_id, "step.started", step="box-1", kind="planning", ticket="PT-1",
                       unit="tide-step-box-1", runner="box", box=SLUG, boxSlug=SLUG, boxIp="10.0.0.2")
        self.assertIsNone(self.place("ticket", parent="host-1"))       # host zostaje hostem mimo gotowego pudełka
        self.assertIsNotNone(self.place("planning", parent="box-1"))   # pudełko zostaje pudełkiem
        self.box_enabled = False
        with self.assertRaises(boxstep.BoxError):
            self.place("ticket", parent="box-1")                        # nigdy cichy powrót na hosta

    # --- polityka proxy i box.risk ----------------------------------------------------------------

    def test_policy_has_project_and_all_cards_in_the_box_with_the_step_log(self):
        self.build_box()
        other = self.card(title="Inna")
        bp.save_card(SLUG, other, {"tide-box": 1, "network": {"domains": ["inna.example.org"]}})
        bp.save_card(SLUG, self.card_id, {"tide-box": 1, "network": {"domains": ["moja.example.org"]}})
        self.engine.journal.append(other, "step.started", step="K-2-PT-9-1", kind="ticket", ticket="PT-9",
                                   unit="tide-step-K-2-PT-9-1", runner="box", box=SLUG, boxSlug=SLUG, boxIp="x")
        placement = self.place()
        log = boxstep.before_start(self.engine, placement, self.card_id, "K-1-PT-1-1")
        call = self.proxy.sets()[-1]
        _, ip, domains, _ollama, path, _full, box = call
        self.assertEqual((ip, box, path), (placement.ip, SLUG, log))
        for domain in ("api.example.org", "inna.example.org", "moja.example.org", "api.anthropic.com"):
            self.assertIn(domain, domains)
        self.assertTrue(log.endswith(os.path.join("steps", "K-1-PT-1-1", "proxy-denied.log")))
        self.assertFalse(log.startswith(os.path.realpath(self.repo) + "-worktrees"))   # poza montowaniami pudełka
        # `box.use` przed startem runnera (BoxRunner nie zatrzyma pudełka pod tym krokiem); nieudany start go cofa.
        self.assertEqual(bs.running_steps(SLUG, SLUG), ["K-1-PT-1-1"])
        boxstep.start_failed(placement, "K-1-PT-1-1")
        self.assertEqual(bs.running_steps(SLUG, SLUG), [])

    def risk_events(self):
        return [e for e in self.events_now() if e["type"] == "box.risk"]

    def test_k1_risk_once_per_card_with_docker_and_without_k3(self):
        self.build_box({"tide-box": 1, "docker": {"enabled": True}})
        placement = self.place()
        self.assertTrue(placement.docker)
        with mock.patch.dict("tide_daemon.box.k3.K3_PROGRAMS", {"claude": False, "codex": False}):
            boxstep.started(self.engine, placement, self.card_id, "s1", "claude")
            boxstep.started(self.engine, placement, self.card_id, "s2", "claude")
        events = self.risk_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["text"], "poświadczenia agenta czytelne dla kodu w pudełku (grupa docker); "
                                            "pełna ochrona: K3")

    def test_no_k1_risk_with_k3_or_without_docker(self):
        self.build_box({"tide-box": 1, "docker": {"enabled": True}})
        with mock.patch.dict("tide_daemon.box.k3.K3_PROGRAMS", {"claude": True, "codex": True}):
            boxstep.started(self.engine, self.place(), self.card_id, "s1", "claude")
        bp.save_project(SLUG, {"tide-box": 1})
        placement = boxstep.Placement(SLUG, SLUG, "10.0.0.2", False, {}, None, docker=False)
        with mock.patch.dict("tide_daemon.box.k3.K3_PROGRAMS", {"claude": False, "codex": False}):
            boxstep.started(self.engine, placement, self.card_id, "s2", "claude")
        self.assertEqual(self.risk_events(), [])


class DeniedTest(BoxFlow):
    """Wynik kroku zestawiony z dziennikiem odmów kroku (`_finish` z syntetycznym dziennikiem)."""

    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.build_box()
        self.card_id = self.card()
        self.engine.journal.append(self.card_id, "card.started", entry="now", executor="Sonnet", program="claude",
                                   provider="anthropic", repo=self.repo)

    def step(self, card_id, name, result: dict, log_text=""):
        step_dir = os.path.join(self.base, "steps", name + ".step")
        out = os.path.join(step_dir, "out")
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        with open(os.path.join(out, "step.log"), "w", encoding="utf-8") as fh:
            fh.write(log_text)
        self.engine.journal.append(card_id, "step.started", step=name, kind="ticket", ticket="PT-1",
                                   unit="tide-step-" + name, stepDir=step_dir, program="claude",
                                   runner="box", box=SLUG, boxSlug=SLUG, boxIp="10.173.184.10")
        return fold(self.engine.journal.read(card_id)).steps[name]

    def deny(self, step_name, *hosts):
        path = boxstep.denied_log_path(SLUG, step_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        for host in hosts:
            from tide_daemon.box import proxy
            proxy.append_denied(path, SLUG, "10.173.184.10", host, 443, "CONNECT")

    def finished(self, card_id, name):
        return next(e for e in self.engine.journal.read(card_id) if e["type"] == "step.finished" and e["step"] == name)

    def test_network_question_with_a_denied_domain_becomes_denied_without_env_answer(self):
        step = self.step(self.card_id, "K-1-PT-1-1",
                         {"result": "question", "question": "Proxy odmówiło registry.example.org — czy mogę dostać sieć?"})
        self.deny("K-1-PT-1-1", "registry.example.org")
        self.engine._finish(self.card_id, step, exit_code=0)
        done = self.finished(self.card_id, "K-1-PT-1-1")
        self.assertEqual((done["result"], done["command"]), ("denied", "network: registry.example.org"))
        self.assertEqual(done["network"], "registry.example.org")
        self.assertFalse(done["canAllow"])
        decisions = [e for e in self.engine.journal.read(self.card_id) if e["type"] == "step.decision"]
        self.assertFalse([d for d in decisions if d.get("envAnswer")])

    def test_error_on_a_denied_domain_becomes_denied(self):
        step = self.step(self.card_id, "K-1-PT-1-1", {"result": "error", "reason": "npm"},
                         log_text="npm ERR! 403 Forbidden - CONNECT registry.npmjs.org:443\n")
        self.deny("K-1-PT-1-1", "registry.npmjs.org")
        self.engine._finish(self.card_id, step, exit_code=0)
        done = self.finished(self.card_id, "K-1-PT-1-1")
        self.assertEqual((done["result"], done["command"]), ("denied", "network: registry.npmjs.org"))

    def test_entry_from_another_card_in_the_same_box_does_not_make_denied(self):
        other = self.card(title="Inna karta")
        self.engine.journal.append(other, "card.started", entry="now", executor="Sonnet", program="claude",
                                   provider="anthropic", repo=self.repo)
        self.step(other, "K-2-PT-2-1", {"result": "done", "notes": []})
        self.deny("K-2-PT-2-1", "evil.example.org")          # dziennik kroku innej karty
        step = self.step(self.card_id, "K-1-PT-1-1",
                         {"result": "question", "question": "Czy mogę połączyć się z evil.example.org?"})
        self.engine._finish(self.card_id, step, exit_code=0)
        done = self.finished(self.card_id, "K-1-PT-1-1")
        self.assertEqual(done["result"], "question")
        self.assertEqual(done["command"], "")

    def test_done_with_unrelated_denials_gets_a_note(self):
        step = self.step(self.card_id, "K-1-PT-1-1", {"result": "done", "notes": []})
        self.deny("K-1-PT-1-1", "telemetry.example.org", "telemetry.example.org")
        self.engine._finish(self.card_id, step, exit_code=0)
        done = self.finished(self.card_id, "K-1-PT-1-1")
        self.assertEqual(done["result"], "done")
        self.assertIn(boxstep.NOTE_FILE, done["notes"])
        note = os.path.join(self.root, self.engine.journal.rel_dir(self.card_id), "steps", "K-1-PT-1-1",
                            boxstep.NOTE_FILE)
        with open(note, encoding="utf-8") as fh:
            self.assertIn("telemetry.example.org:443", fh.read())

    def test_forged_entry_in_the_step_dir_is_ignored(self):
        """Dziennik odmów leży poza katalogiem kroku (rw w pudełku) — plik podłożony przez agenta nic nie znaczy."""
        step = self.step(self.card_id, "K-1-PT-1-1",
                         {"result": "question", "question": "Czy mogę dostać sieć do x.example.org?"})
        from tide_daemon.box import proxy
        proxy.append_denied(os.path.join(step["stepDir"], "out", "proxy-denied.log"), SLUG, "1.1.1.1",
                            "x.example.org", 443, "CONNECT")
        self.engine._finish(self.card_id, step, exit_code=0)
        self.assertEqual(self.finished(self.card_id, "K-1-PT-1-1")["result"], "question")

    def test_release_resets_the_policy_for_remaining_steps_or_clears_it(self):
        other = self.card(title="Inna karta")
        self.engine.journal.append(other, "card.started", entry="now", executor="Sonnet", program="claude",
                                   provider="anthropic", repo=self.repo)
        first = self.step(self.card_id, "K-1-PT-1-1", {"result": "done", "notes": []})
        second = self.step(other, "K-2-PT-2-1", {"result": "done", "notes": []})
        self.engine._finish(self.card_id, first, exit_code=0)
        call = self.proxy.calls[-1]
        self.assertEqual(call[0], "set")
        self.assertEqual(call[4], boxstep.denied_log_path(SLUG, "K-2-PT-2-1"))
        self.engine._finish(other, second, exit_code=0)
        self.assertEqual(self.proxy.calls[-1], ("clear", "10.173.184.10"))


class BoxStepInProcessTest(BoxFlow):
    """Prawdziwy przebieg kroku przez atrapę `incus exec` → `tide-step` → atrapa `claude`.

    Testy czekają na `step.finished`, nie na kolumnę „review”: scalenie do gałęzi karty wymaga piaskownicy gita (bwrap),
    której nie ma w piaskownicy kroku — to osobna ścieżka, której ten ticket nie zmienia."""

    def finished(self, card_id, count=1, timeout=60):
        found = wait_until(lambda: len(self.of(card_id, "step.finished")) >= count, timeout=timeout)
        self.assertTrue(found, json.dumps(self.events(card_id), ensure_ascii=False, indent=1))
        return self.of(card_id, "step.finished")[count - 1]

    def started_box(self, card_id):
        step = self.started(card_id)
        self.assertEqual((step.get("runner"), step.get("box"), step.get("boxSlug")), ("box", SLUG, SLUG), step)
        return step

    def test_ticket_step_runs_in_the_box_and_finishes(self):
        self.build_box()
        card_id = self.card()
        self.assertTrue(self.start(card_id)["ok"])
        step = self.started_box(card_id)
        finished = self.finished(card_id)
        self.assertEqual(finished["result"], "done")
        self.assertTrue(wait_until(lambda: self.proxy.calls[-1][0] == "clear", timeout=10), self.proxy.calls)
        execs = [c for c in self.incus_calls("exec") if "/usr/bin/env" in c]
        self.assertTrue(execs)
        self.assertIn(SLUG, execs[0])
        self.assertTrue([line for line in self.box_log() if f"--unit={step['unit']}.service" in line])
        sets = self.proxy.sets()
        self.assertEqual(sets[0][4], boxstep.denied_log_path(SLUG, step["step"]))
        self.assertIn("api.example.org", sets[0][2])
        self.assertEqual(self.proxy.calls[-1][0], "clear")
        kinds = [e["type"] for e in bs.events(SLUG, SLUG)]
        self.assertIn("box.use", kinds)
        self.assertIn("box.release", kinds)
        self.assertEqual(bs.running_steps(SLUG, SLUG), [])

    def test_existing_flow_without_box_stays_on_host(self):
        card_id = self.card()
        self.assertTrue(self.start(card_id)["ok"])
        step = self.started(card_id)
        self.assertNotIn("runner", step)
        self.assertIsInstance(self.engine.runner, HostRunner)
        self.assertEqual(self.finished(card_id)["result"], "done")
        self.assertEqual(self.incus_calls("exec"), [])
        self.assertEqual(self.proxy.calls, [])

    def test_restart_during_a_box_step_finds_it_by_its_entry_and_reads_the_result(self):
        self.build_box()
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: powoli 3")
        self.start(card_id)
        step = self.started_box(card_id)
        self.restart()
        self.assertEqual(self.flow.engine.procs, {})
        finished = self.finished(card_id)
        self.assertEqual(finished["result"], "done", finished)
        self.assertEqual(finished["exit"], 0)              # kod z `in/exit-code` (tide-step), nie z klienta incus
        self.assertTrue(os.path.isfile(os.path.join(step["stepDir"], "out", "result.json")))
        self.assertTrue([line for line in self.box_log() if line.startswith("box-systemctl is-active")])

    def decide(self, card_id, **request):
        reply = []
        self.board.resolve_card_step(card_id, json.dumps(request), reply.append)
        self.assertTrue(wait_until(lambda: reply, timeout=20), "ResolveCardStep bez odpowiedzi")
        return json.loads(reply[0])

    def resumed(self, card_id, parent):
        found = wait_until(lambda: [s for s in self.of(card_id, "step.started") if s.get("parentStep") == parent],
                           timeout=30)
        self.assertTrue(found, self.events(card_id))
        return found[0]

    def test_resume_of_a_box_step_uses_the_box_runner_from_the_entry(self):
        self.build_box()
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: pytanie")
        self.start(card_id)
        first = self.started_box(card_id)
        self.assertEqual(self.finished(card_id)["result"], "question")
        self.assertTrue(self.decide(card_id, choice="answer", answer="ISO 8601", step=first["step"])["ok"])
        resumed = self.resumed(card_id, first["step"])
        self.assertEqual((resumed.get("runner"), resumed.get("box")), ("box", SLUG))
        self.assertEqual(self.finished(card_id, 2)["result"], "done")

    def test_resume_of_a_host_step_stays_on_host_after_the_box_appears(self):
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: pytanie")
        self.start(card_id)
        first = self.started(card_id)
        self.assertNotIn("runner", first)
        self.assertEqual(self.finished(card_id)["result"], "question")
        self.build_box()                                 # pudełko gotowe dopiero teraz
        self.assertTrue(self.decide(card_id, choice="answer", answer="ISO 8601", step=first["step"])["ok"])
        resumed = self.resumed(card_id, first["step"])
        self.assertNotIn("runner", resumed)
        self.assertEqual(self.finished(card_id, 2)["result"], "done")
        self.assertEqual(self.incus_calls("exec"), [])

    def test_outside_limit_does_not_hold_box_steps_and_two_steps_run_in_parallel(self):
        self.build_box()
        a = self.card(title="A", description="Dodaj eksport.\n\nATRAPA: powoli 4")
        b = self.card(title="B", description="Dodaj import.\n\nATRAPA: powoli 4")
        # Polecenie poza piaskownicą trwa w tym projekcie (OUTSIDE_PER_PROJECT wyczerpane).
        self.engine._outside_active = {"zewn": (a, "")}
        self.assertTrue(self.engine._outside_busy(a))
        self.start(a)
        self.start(b)
        sa, sb = self.started_box(a), self.started_box(b)
        self.assertEqual(self.finished(a)["result"], "done")
        self.assertEqual(self.finished(b)["result"], "done")
        uses = [e for e in bs.events(SLUG, SLUG) if e["type"] in ("box.use", "box.release")]
        # Oba kroki weszły do pudełka, zanim którykolwiek wyszedł.
        self.assertEqual([e["type"] for e in uses[:2]], ["box.use", "box.use"], uses)
        self.assertEqual({sa["step"], sb["step"]}, {e["step"] for e in uses[:2]})


if __name__ == "__main__":
    unittest.main()
