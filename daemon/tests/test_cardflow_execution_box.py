"""Krok karty w pudełku w silniku (PT-181, #18): wybór runnera według rodzaju kroku i stanu projektu/pudełka/karty,
`boxWait`, start pudełka, kopia per karta, polityka proxy, odmowa proxy → `denied`, `box.risk`, wznowienie
i restart usługi. Wszystko na atrapie `incus` (boxflow.py); prawdziwy krok w pudełku to #28.
"""
import asyncio
import base64
import json
import os
import unittest
from unittest import mock

from tide_daemon.box import profile as bp
from tide_daemon.box import proxy as bproxy
from tide_daemon.box import runner as br
from tide_daemon.box import state as bs
from tide_daemon.cardflow import boxaccess, boxstep, boxwait, engine as flow_engine
from tide_daemon.cardflow.engine import BOX_STEP_KINDS, FlowDeferred, FlowError
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

    def test_environment_wait_blocks_without_an_approved_profile(self):
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.ENVIRONMENT)
        for kind in BOX_STEP_KINDS:
            with self.subTest(kind=kind), self.assertRaises(FlowDeferred):
                self.box_place(kind)

    def test_environment_wait_blocks_when_boxes_are_disabled(self):
        self.build_box()
        self.box_enabled = False
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.ENVIRONMENT)
        with self.assertRaises(FlowDeferred):
            self.box_place()

    def test_environment_wait_blocks_resume_of_a_host_execution_step(self):
        self.engine.journal.append(self.card_id, "step.started", step="host", kind="ticket")
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.ENVIRONMENT)
        with self.assertRaises(boxstep.BoxWaiting) as caught:
            self.place(parent="host")
        self.assertEqual(caught.exception.reason, boxstep.WAIT_ENVIRONMENT)

    def test_environment_wait_does_not_block_planning_or_its_host_resume(self):
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.ENVIRONMENT)
        for kind in PLANNING_KINDS:
            with self.subTest(kind=kind):
                self.assertIsNone(self.place(kind))
                self.engine.journal.append(self.card_id, "step.started", step=kind, kind=kind)
                self.assertIsNone(self.place(kind, parent=kind))

    def change_snapshot(self, box, transform):
        path = os.path.join(os.environ["TIDE_FAKE_INCUS_STATE"], "state.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        transform(data["instances"][box]["snapshots"])
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_missing_clean_snapshot_waits_for_rebuild(self):
        self.build_box()
        self.change_snapshot(SLUG, lambda snapshots: snapshots.clear())
        self.assertEqual(self.incus().snapshot_list(SLUG), [])
        with self.assertRaises(FlowDeferred):
            self.box_place()
        self.assertEqual(fold(self.events_now()).box_wait, boxwait.REBUILD)
        self.assertEqual(self.waits()[-1]["reason"], boxstep.WAIT_REBUILD)

    def test_clean_snapshot_stamp_must_match_even_if_live_stamp_matches(self):
        self.build_box()
        for stamp in (None, "bad-json", json.dumps(self.builder().stamp({"tide-box": 1, "apt": ["jq"]}))):
            with self.subTest(stamp=stamp):
                def change(snapshots):
                    snapshots[0]["config"][bs.RECIPE_KEY] = stamp
                self.change_snapshot(SLUG, change)
                status = self.builder().status(SLUG)
                self.assertEqual(status.recipe_hash, status.profile_hash)
                with self.assertRaises(FlowDeferred):
                    self.box_place()
                self.assertEqual(fold(self.events_now()).box_wait, boxwait.REBUILD)

    def test_existing_card_copy_requires_its_own_clean_snapshot(self):
        self.build_box({"tide-box": 1, "box": "per-card"})
        original = self.place()
        self.change_snapshot(original.box, lambda snapshots: snapshots.clear())
        with self.assertRaises(FlowDeferred):
            self.box_place()
        self.assertEqual(fold(self.events_now()).box_wait, boxwait.REBUILD)
        self.assertEqual(len(self.incus_calls("copy")), 1)

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

    def test_resume_keeps_the_instance_after_profile_mode_changes(self):
        self.build_box()
        self.engine.journal.append(self.card_id, "step.started", step="parent", runner="box", box=SLUG,
                                   boxSlug=SLUG, boxIp="10.0.0.2", kind="ticket")
        bp.save_project(SLUG, {"tide-box": 1, "box": "per-card", "network": {"domains": ["api.example.org"]}})
        placement = self.place(parent="parent")
        self.assertEqual(placement.box, SLUG)
        self.assertEqual(placement.runner.box, SLUG)
        self.assertEqual(self.incus_calls("copy"), [])

    def test_resume_keeps_card_copy_after_switch_to_project_mode(self):
        self.build_box({"tide-box": 1, "box": "per-card"})
        original = self.place()
        self.engine.journal.append(self.card_id, "step.started", step="parent", runner="box", box=original.box,
                                   boxSlug=SLUG, boxIp=original.ip, kind="ticket")
        bp.save_project(SLUG, {"tide-box": 1, "box": "per-project"})
        resumed = self.place(parent="parent")
        self.assertEqual((resumed.box, resumed.runner.box), (original.box, original.box))
        self.assertEqual(len(self.incus_calls("copy")), 1)
        self.incus().stop(original.box)
        self.incus().delete(original.box)
        with self.assertRaises(boxstep.BoxWaiting):
            self.place(parent="parent")
        self.assertEqual(len(self.incus_calls("copy")), 1)  # nie zastępujemy utraconej instancji nową

    def test_reconcile_start_intent_checks_the_box_instead_of_the_host(self):
        self.build_box()
        self.engine.journal.append(self.card_id, "card.started", repo=self.repo)
        self.engine.box_proxy = bproxy.ProxyService(self.proxy.listen, self.proxy.port)
        placement = self.place()
        log = boxstep.before_start(self.engine, placement, self.card_id, "crash")
        access = boxaccess.read(log)
        intent = self.engine.journal.append(self.card_id, "intent", action="step-start", key="crash",
                                           **placement.entry(),
                                           unit="tide-step-crash", kind="ticket", ticket="PT-1")
        self.engine.box_proxy = bproxy.ProxyService(self.proxy.listen, self.proxy.port)
        with mock.patch.object(br.BoxRunner, "step_state", return_value="active") as state, \
                mock.patch.object(flow_engine.stepstop, "unit_state", return_value="inactive") as host:
            self.engine._reconcile(self.card_id, intent)
        state.assert_called_once()
        host.assert_not_called()
        st = fold(self.events_now())
        self.assertTrue(st.results[("step-start", "crash")]["ok"])
        self.assertEqual(st.steps["crash"]["box"], SLUG)
        self.assertEqual(st.steps["crash"]["boxAccess"], 1)
        self.assertIsNotNone(self.engine.box_proxy.policy(placement.ip))
        self.assertIsNotNone(self.engine.box_proxy.grants.check(access["grant"]["key"], placement.ip, "anthropic"))

    def test_missing_access_context_stops_only_the_affected_step(self):
        self.build_box()
        placement = self.place()
        self.engine.journal.append(self.card_id, "step.started", step="missing", **placement.entry(),
                                   unit="tide-step-missing", kind="ticket", ticket="PT-1")
        boxstep.recover_network(self.engine)
        step = fold(self.events_now()).steps["missing"]
        with mock.patch.object(self.engine, "_stop") as stop:
            self.engine._poll(self.card_id, step)
        stop.assert_called_once_with(self.card_id, step, "error", "brak zapisanego kontekstu sieci kroku")

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
        self.assertEqual((ip, box), (placement.ip, SLUG))
        self.assertNotEqual(path, log)  # ruch bez identyfikatora nie obciąża żadnego kroku
        self.assertEqual(self.proxy.steps["K-1-PT-1-1"][2], log)
        for domain in ("api.example.org", "inna.example.org", "moja.example.org", "api.anthropic.com"):
            self.assertIn(domain, domains)
        self.assertTrue(log.endswith(os.path.join("steps", "K-1-PT-1-1", "proxy-denied.log")))
        self.assertFalse(log.startswith(os.path.realpath(self.repo) + "-worktrees"))   # poza montowaniami pudełka
        # `box.use` przed startem runnera (BoxRunner nie zatrzyma pudełka pod tym krokiem); nieudany start go cofa.
        self.assertEqual(bs.running_steps(SLUG, SLUG), ["K-1-PT-1-1"])
        self.assertEqual(self.proxy.grants.active(), 1)
        boxstep.start_failed(self.engine, placement, "K-1-PT-1-1")
        self.assertEqual(bs.running_steps(SLUG, SLUG), [])
        self.assertEqual(self.proxy.grants.active(), 0)
        self.assertEqual(self.proxy.steps, {})

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

    def test_outside_from_box_never_gets_automatic_host_consent(self):
        step = self.step(self.card_id, "box-outside", {"result": "outside", "command": "npm test"})
        with mock.patch.object(self.engine, "_auto_consent", return_value=True):
            self.engine._finish(self.card_id, step, exit_code=0)
        st = fold(self.engine.journal.read(self.card_id))
        self.assertFalse(st.finished["box-outside"]["canAllow"])
        self.assertNotIn("box-outside", st.step_decisions)
        with mock.patch.object(self.engine, "_auto_consent", return_value=True):
            self.engine._consent_recheck(self.card_id, step, {**st.finished["box-outside"], "canAllow": True})
        self.assertNotIn("box-outside", fold(self.engine.journal.read(self.card_id)).step_decisions)
        with mock.patch.object(flow_engine.subprocess, "Popen") as launch:
            with self.assertRaises(flow_engine.FlowError):
                self.engine._start_outside(self.card_id, step, "npm test")
            with self.assertRaises(flow_engine.FlowError):
                self.engine._resume_step(self.card_id, st, step, {"choice": "run", "auto": True})
        launch.assert_not_called()

    def proxy_request(self, key="", ip="10.173.184.10"):
        """Pełny parser żądania rzeczywistego proxy, bez otwierania gniazda ani wyjścia do sieci."""
        class Writer:
            data = b""

            def write(self, data):
                self.data += data

            async def drain(self):
                pass

        async def request():
            reader, writer = asyncio.StreamReader(), Writer()
            auth = ""
            if key:
                value = base64.b64encode(f"tide:{key}".encode()).decode()
                auth = f"Proxy-Authorization: Basic {value}\r\n"
            reader.feed_data(f"CONNECT x.example.org:443 HTTP/1.1\r\n{auth}\r\n".encode())
            reader.feed_eof()
            await self.engine.box_proxy._serve(reader, writer, ip)
            return writer.data

        self.assertIn(b"403 Forbidden", asyncio.run(request()))

    def test_real_proxy_assigns_denial_to_older_step_not_last_policy_writer(self):
        self.engine.box_proxy = bproxy.ProxyService(self.proxy.listen, self.proxy.port)
        other = self.card(title="Inna karta")
        placement = boxstep.place(self.engine, self.card_id, SLUG, "ticket", "", BOX_STEP_KINDS)
        log_a = boxstep.before_start(self.engine, placement, self.card_id, "older")
        a = self.step(self.card_id, "older", {"result": "question", "question": "Potrzebuję x.example.org"})
        log_b = boxstep.before_start(self.engine, placement, other, "newer")
        b = self.step(other, "newer", {"result": "question", "question": "Potrzebuję x.example.org"})
        access_a, access_b = boxaccess.read(log_a), boxaccess.read(log_b)
        self.assertNotEqual(access_a["networkKey"], access_b["networkKey"])
        self.proxy_request(access_a["networkKey"])
        self.assertTrue(boxstep.read_denied(log_a))
        self.assertEqual(boxstep.read_denied(log_b), [])
        self.engine._finish(self.card_id, a, exit_code=0)
        self.engine._finish(other, b, exit_code=0)
        self.assertEqual(self.finished(self.card_id, "older")["result"], "denied")
        self.assertEqual(self.finished(other, "newer")["result"], "question")

    def test_unattributed_or_wrong_ip_proxy_traffic_never_enters_step_log(self):
        self.engine.box_proxy = bproxy.ProxyService(self.proxy.listen, self.proxy.port, log=lambda _s: None)
        placement = boxstep.place(self.engine, self.card_id, SLUG, "ticket", "", BOX_STEP_KINDS)
        log = boxstep.before_start(self.engine, placement, self.card_id, "step-a")
        key = boxaccess.read(log)["networkKey"]
        self.proxy_request()
        self.proxy_request("unknown")
        self.proxy_request(key, ip="10.173.184.99")
        self.assertEqual(boxstep.read_denied(log), [])

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
        self.assertNotEqual(call[4], boxstep.denied_log_path(SLUG, "K-2-PT-2-1"))
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
        self.assertNotEqual(sets[0][4], boxstep.denied_log_path(SLUG, step["step"]))
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

    def test_restart_with_fresh_proxy_restores_network_policy_and_k3(self):
        self.build_box()
        self.proxy = bproxy.ProxyService(self.proxy.listen, self.proxy.port)
        self.engine.box_proxy = self.proxy
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: powoli 5")
        self.start(card_id)
        step = self.started_box(card_id)
        log = boxstep.denied_log_path(SLUG, step["step"])
        original = boxaccess.read(log)
        self.assertIsNotNone(self.proxy.policy(step["boxIp"]))
        self.proxy = bproxy.ProxyService(self.proxy.listen, self.proxy.port)
        self.assertIsNone(self.proxy.policy(step["boxIp"]))
        bp.save_project(SLUG, {"tide-box": 1, "network": {"domains": ["new.example.org"]}})
        self.restart()
        self.assertTrue(wait_until(lambda: self.proxy.policy(step["boxIp"]) is not None, timeout=10))
        policy = self.proxy.policy(step["boxIp"])
        self.assertTrue(policy.allows("api.example.org"))
        self.assertFalse(policy.allows("new.example.org"))  # odtwarzany jest zatwierdzony kontekst kroku
        grant = self.proxy.grants.check(original["grant"]["key"], step["boxIp"], "anthropic")
        self.assertIsNotNone(grant)
        self.assertEqual(grant.step, step["step"])
        encoded = base64.b64encode(f"tide:{original['networkKey']}".encode()).decode()
        assigned = self.proxy._request_policy(step["boxIp"], [f"Proxy-Authorization: Basic {encoded}"])
        self.assertEqual(assigned.denied_log, log)
        self.assertEqual(self.finished(card_id)["result"], "done")
        self.assertTrue(wait_until(lambda: self.proxy.grants.active() == 0, timeout=5))

    def test_both_programs_receive_step_credentials_and_revoke_them_on_finish(self):
        self.build_box()
        for tool, extra in (("claude", {}), ("codex", {"executor": "Codex mocny", "preferredProvider": "openai"})):
            with self.subTest(tool=tool):
                card_id = self.card(description="Dodaj eksport.\n\nATRAPA: powoli 2")
                self.start(card_id, **extra)
                step = self.started_box(card_id)
                self.assertEqual(step["program"], tool)
                log = boxstep.denied_log_path(SLUG, step["step"])
                access = boxaccess.read(log)
                call_path = os.path.join(step["stepDir"], "out", "fake-call.json")
                self.assertTrue(wait_until(lambda: os.path.isfile(call_path), timeout=10))
                with open(call_path, encoding="utf-8") as fh:
                    call = json.load(fh)
                env, argv = call["env"], call["argv"]
                self.assertIn(access["networkKey"], env["HTTPS_PROXY"])
                grant = access["grant"]
                self.assertIsNotNone(self.proxy.grants.check(grant["key"], step["boxIp"], grant["route"]))
                if tool == "claude":
                    self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], grant["key"])
                    self.assertTrue(env["ANTHROPIC_BASE_URL"].endswith("/anthropic"))
                else:
                    with open(os.path.join(env["CODEX_HOME"], "auth.json"), encoding="utf-8") as fh:
                        auth = json.load(fh)
                    self.assertEqual(auth["tokens"]["access_token"], grant["key"])
                    self.assertIn('model_provider="tide"', argv)
                    self.assertTrue(any(a.startswith('chatgpt_base_url="http://') for a in argv))
                    self.assertTrue(any("wire_api" in a and "responses" in a for a in argv))
                self.assertNotIn(grant["key"], " ".join(argv))
                self.assertNotIn(access["networkKey"], " ".join(argv))
                self.assertEqual(self.finished(card_id)["result"], "done")
                self.assertTrue(wait_until(lambda: self.proxy.grants.check(grant["key"], step["boxIp"], grant["route"]) is None,
                                           timeout=5))

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

    def test_resume_after_local_unavailable_still_starts_in_the_same_card_box(self):
        self.build_box({"tide-box": 1, "box": "per-card"})
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: pytanie")
        self.assertTrue(self.start(card_id)["ok"])
        first = self.started(card_id)
        self.assertEqual(first["box"], bs.card_box_name(SLUG, card_id))
        self.assertEqual(self.finished(card_id)["result"], "question")
        self.flow.stop()  # Kolejne próby prowadzimy bez równoległego tick monitora.
        journal = self.engine.journal
        st = fold(journal.read(card_id))
        previous = st.steps[first["step"]]
        for attempt in (1, 2):
            with mock.patch.object(self.engine, "_resume_executor",
                                   side_effect=FlowError("local-unavailable", "model lokalny niedostępny")):
                self.engine._resume_step(card_id, st, previous, {"choice": "retry"}, attempt=attempt)
            st = fold(journal.read(card_id))
            previous = st.steps[st.last_step]
            self.assertEqual(st.finished[previous["step"]]["reason"], "local-unavailable")
        self.engine._resume_step(card_id, st, previous, {"choice": "answer", "answer": "ISO 8601"}, attempt=3)
        resumed = fold(journal.read(card_id)).steps[fold(journal.read(card_id)).last_step]
        self.assertEqual((resumed.get("runner"), resumed.get("box")), ("box", first["box"]))
        for field in ("boxSlug", "boxIp", "boxAccess"):
            self.assertEqual(resumed.get(field), first[field])
        self.assertIn(resumed["step"], self.engine.procs)
        self.flow.start()
        self.assertEqual(self.finished(card_id, 4)["result"], "done")
        self.assertEqual(len(self.incus_calls("copy")), 1)

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
