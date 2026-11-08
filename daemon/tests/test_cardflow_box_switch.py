"""PT-289 (K-38 F1): wyłącznik pudełka (`box.enabled: false`) przed zgodą środowiska i ekspozycją projektu.

Następczo wobec PT-181: przy wyłączonych pudełkach i braku wymuszenia (`forced`, wznowienie kroku z pudełka)
`boxstep.place` zwraca `None` (HostRunner) zanim dotknie `boxWait`, ekspozycji PT-264, profili, rezerwacji
repozytorium, admission i Incusa. Wznowienie kroku z pudełka nadal daje `BoxError` bez uruchomienia procesu.
Przy `box.enabled: true` dotychczasowe bariery obowiązują (kontrole dodatnie i ujemne niżej).
Wszystko na atrapach `boxflow.py` (fake incus, atrapa proxy, prywatna magistrala testu).
"""
import json
import os
from unittest import mock

import tide_daemon.box as box_pkg
from tide_daemon.box import admission
from tide_daemon.box import exposure
from tide_daemon.box import profile as bp
from tide_daemon.box import refguard
from tide_daemon.box import runner as br
from tide_daemon.cardflow import boxstep, boxwait, trialhold
from tide_daemon.cardflow.engine import BOX_STEP_KINDS, FlowDeferred, FlowError
from tide_daemon.cardflow.state import fold
from tide_daemon.tasks.runner import HostRunner

from .boxflow import SLUG, BoxFlow
from .privatebus import wait_until

BARE = "bez-profilu"
PROJECTS = (exposure.PROJECT, SLUG, BARE)   # plasma-tide (ekspozycja v1) / projekt z profilem / bez profilu


class Selected:
    provider = "anthropic"


class _Project:
    def __init__(self, slug):
        self.slug = slug


class _Forbidden:
    """Atrapa modułu `exposure`: każdy odczyt (także `PROJECT`) to błąd testu."""

    def __init__(self, calls):
        self._calls = calls

    def __getattr__(self, name):
        self._calls.append(("exposure", name))
        raise AssertionError(f"exposure.{name} przy wyłączonym pudełku")


class SwitchPlacementTest(BoxFlow):
    """`boxstep.place` i `_box_place` wołane wprost (monitor kroków zatrzymany)."""

    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.card_id = self.card()
        self.engine.journal.append(self.card_id, "card.started", project=SLUG)
        self.runner_calls = []
        factory = self.engine.box_runner_factory

        def counting(*args):
            self.runner_calls.append(args)
            return factory(*args)
        self.engine.box_runner_factory = counting

    def place(self, kind="ticket", parent="", slug=SLUG):
        return boxstep.place(self.engine, self.card_id, slug, kind, parent, BOX_STEP_KINDS)

    def box_place(self, kind="ticket", slug=SLUG, parent=""):
        return self.engine._box_place(self.card_id, _Project(slug), Selected(), "PT-1", kind, parent)

    def waits(self):
        return [e for e in self.engine.journal.read(self.card_id) if e["type"] == "step.waiting"]

    def forbidden(self):
        """Atrapy zgody, ekspozycji, profili, rezerwacji, admission, buildera i runnera pudełka rzucające wyjątek."""
        calls = []

        def boom(name):
            def raise_(*args, **kwargs):
                calls.append(name)
                raise AssertionError(f"{name} przy wyłączonym pudełku")
            return raise_
        patches = [
            mock.patch.object(boxwait, "card_box_wait", boom("boxwait.card_box_wait")),
            mock.patch.object(box_pkg, "exposure", _Forbidden(calls)),
            mock.patch.object(bp, "load_project", boom("profile.load_project")),
            mock.patch.object(bp, "load_card", boom("profile.load_card")),
            mock.patch.object(refguard, "other_cards", boom("refguard.other_cards")),
            mock.patch.object(admission, "request", boom("admission.request")),
            mock.patch.object(self.engine, "box_builder", boom("builder")),
            mock.patch.object(self.engine, "box_runner_factory", boom("box_runner_factory")),
        ]
        return calls, patches

    def with_forbidden(self, body):
        calls, patches = self.forbidden()
        for p in patches:
            p.start()
        try:
            body()
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertEqual(calls, [])

    # --- enabled=false, bez forced: host przed każdą barierą --------------------------------------------

    def test_disabled_matrix_runs_on_host_with_environment_wait_and_unapproved_exposure(self):
        self.build_box()                                   # projekt `proj` ma profil i gotowe pudełko
        self.assertIsNone(bp.load_project(BARE))          # ekspozycji plasma-tide nikt tu nie zatwierdza
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.ENVIRONMENT, source="ui")
        self.box_enabled = False
        before = len(self.engine.journal.read(self.card_id))
        incus_before = self.incus_calls()

        def body():
            for slug in PROJECTS:
                for kind in BOX_STEP_KINDS:
                    with self.subTest(slug=slug, kind=kind):
                        self.assertIsNone(self.place(kind, slug=slug))
                        self.assertIsNone(self.box_place(kind, slug=slug))
        self.with_forbidden(body)
        self.assertEqual(len(self.engine.journal.read(self.card_id)), before)   # bez step.waiting/box.wait
        self.assertEqual(self.waits(), [])
        self.assertEqual(self.runner_calls, [])
        self.assertEqual(self.incus_calls(), incus_before)          # żadnego wywołania Incusa

    def test_disabled_rebuild_wait_does_not_hold_host_step(self):
        self.approve()
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.REBUILD)
        self.box_enabled = False
        self.with_forbidden(lambda: [self.assertIsNone(self.place(kind)) for kind in BOX_STEP_KINDS])

    def test_disabled_resume_of_host_step_keeps_host_runner(self):
        self.build_box()
        self.engine.journal.append(self.card_id, "step.started", step="host-1", kind="ticket", ticket="PT-1")
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.ENVIRONMENT)
        self.box_enabled = False

        def body():
            for slug in PROJECTS:
                with self.subTest(slug=slug):
                    self.assertIsNone(self.place("ticket", parent="host-1", slug=slug))
                    self.assertIsNone(self.box_place("ticket", slug=slug, parent="host-1"))
        self.with_forbidden(body)
        self.assertEqual(self.runner_calls, [])

    # --- forced: wznowienie kroku z pudełka nie przechodzi na hosta --------------------------------------

    def test_disabled_forced_resume_is_refused_before_anything_runs(self):
        self.build_box()
        for slug in PROJECTS:
            self.engine.journal.append(self.card_id, "step.started", step=f"box-{slug}", kind="ticket",
                                       ticket="PT-1", runner="box", box=SLUG, boxSlug=slug, boxIp="10.0.0.2")
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.ENVIRONMENT)
        self.box_enabled = False
        incus_before = self.incus_calls()

        def body():
            for slug in PROJECTS:
                with self.subTest(slug=slug):
                    with self.assertRaisesRegex(boxstep.BoxError, "pudełko jest wyłączone"):
                        self.place("ticket", parent=f"box-{slug}", slug=slug)
                    with self.assertRaises(FlowError) as caught:
                        self.box_place("ticket", slug=slug, parent=f"box-{slug}")
                    self.assertEqual(caught.exception.code, "box")
        with mock.patch.object(br.BoxRunner, "start", side_effect=AssertionError("BoxRunner.start")), \
                mock.patch.object(HostRunner, "start", side_effect=AssertionError("HostRunner.start")):
            self.with_forbidden(body)
        self.assertEqual(self.runner_calls, [])
        self.assertEqual(self.incus_calls(), incus_before)
        self.assertEqual(self.waits(), [])

    def test_missing_parent_entry_still_refused_when_disabled(self):
        self.box_enabled = False
        with self.assertRaisesRegex(boxstep.BoxError, "brak wpisu"):
            self.place(parent="nieznany")

    # --- PT-261: bramka prób przed wyborem runnera, niezależnie od wyłącznika ----------------------------

    def test_trial_hold_still_refuses_launch_when_boxes_are_disabled(self):
        self.box_enabled = False
        card = type("Card", (), {"id": self.card_id})()
        with mock.patch.object(trialhold, "held", return_value=True), \
                mock.patch.object(boxstep, "place", side_effect=AssertionError("place po bramce PT-261")):
            with self.assertRaises(FlowError) as caught:
                self.engine._launch(card, _Project(SLUG), Selected(), "PT-1", None, None, "step-x", [])
        self.assertEqual(caught.exception.code, "k38-trial-held")

    # --- enabled=true: bariery obowiązują (dodatnie i ujemne) --------------------------------------------

    def test_enabled_environment_wait_blocks_and_clear_wait_lets_the_box_run(self):
        self.build_box()
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.ENVIRONMENT, source="ui")
        for kind in BOX_STEP_KINDS:
            with self.subTest(kind=kind):
                with self.assertRaises(FlowDeferred):
                    self.box_place(kind)
                with self.assertRaises(FlowDeferred):
                    self.box_place(kind, slug=BARE)       # także bez profilu
        self.assertEqual(self.waits()[-1]["reason"], boxstep.WAIT_ENVIRONMENT)
        boxwait.set_box_wait(self.engine.journal, self.card_id, "")
        self.assertIsInstance(self.place().runner, br.BoxRunner)
        self.assertEqual(len(self.runner_calls), 1)

    def test_enabled_environment_wait_blocks_host_resume(self):
        self.engine.journal.append(self.card_id, "step.started", step="host-1", kind="ticket", ticket="PT-1")
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.ENVIRONMENT)
        with self.assertRaises(boxstep.BoxWaiting):
            self.place(parent="host-1")
        boxwait.set_box_wait(self.engine.journal, self.card_id, "")
        self.assertIsNone(self.place(parent="host-1"))

    def test_enabled_project_exposure_blocks_every_execution_kind(self):
        for kind in BOX_STEP_KINDS:
            with self.subTest(kind=kind), self.assertRaisesRegex(boxstep.BoxWaiting, "manifest projektu"):
                self.place(kind, slug=exposure.PROJECT)
        self.assertIsNone(self.place("planning", slug=exposure.PROJECT))
        self.assertEqual(self.runner_calls, [])

    def test_enabled_profile_hash_and_nic_checks_still_apply(self):
        self.assertIsNone(self.place(slug=BARE))                         # bez profilu: host
        self.build_box(stamp_profile={"tide-box": 1, "apt": ["jq"]})     # hash niezgodny
        with self.assertRaisesRegex(boxstep.BoxWaiting, "przebudowy"):
            self.place()
        self.assertEqual(fold(self.engine.journal.read(self.card_id)).box_wait, boxwait.REBUILD)
        self.assertEqual(self.runner_calls, [])

    def test_enabled_missing_nic_filtering_still_waits(self):
        self.build_box(nic=False)
        with self.assertRaisesRegex(boxstep.BoxWaiting, "przebudowy"):
            self.place()
        self.assertEqual(self.runner_calls, [])

    def test_enabled_invalid_profile_is_a_card_error(self):
        with mock.patch.object(bp, "load_project", side_effect=bp.ProfileError("zły")):
            with self.assertRaisesRegex(boxstep.BoxError, "niepoprawny"):
                self.place()

    def test_enabled_repository_reservation_still_serializes_cards(self):
        self.build_box()
        with mock.patch.object(refguard, "other_cards", return_value=["K-55"]):
            with self.assertRaisesRegex(boxstep.BoxWaiting, "jedna karta naraz"):
                self.place()
        self.assertEqual(self.runner_calls, [])

    def test_enabled_admission_guards_a_running_box(self):
        self.build_box()
        with mock.patch.object(admission, "request", side_effect=OSError("slot")) as request:
            with self.assertRaisesRegex(boxstep.BoxWaiting, "slot"):
                self.place()
        request.assert_called_once()


class SwitchInProcessTest(BoxFlow):
    """Pełny krok `Od razu` z pudełkiem wyłączonym, `boxWait: environment` i gotowym pudełkiem: biegnie na hoście."""

    def finished(self, card_id, count=1, timeout=60):
        found = wait_until(lambda: len(self.of(card_id, "step.finished")) >= count, timeout=timeout)
        self.assertTrue(found, json.dumps(self.events(card_id), ensure_ascii=False, indent=1))
        return self.of(card_id, "step.finished")[count - 1]

    def test_ticket_runs_on_host_with_environment_wait_and_counted_runners(self):
        self.build_box()
        self.box_enabled = False
        incus_before = self.incus_calls()
        card_id = self.card()
        host_starts, box_factories, seen = [], [], []
        original_start = HostRunner.start
        factory = self.engine.box_runner_factory
        admit, place = self.engine._admit, boxstep.place

        def admit_with_wait(card, *args, **kwargs):
            # `card.started` zeruje `boxWait`; zgodę zapisujemy tuż przed wyborem runnera (jak „Tylko plan”/UI).
            boxwait.set_box_wait(self.engine.journal, card, boxwait.ENVIRONMENT, source="ui")
            return admit(card, *args, **kwargs)

        def observed_place(engine, card, *args):
            seen.append(fold(engine.journal.read(card)).box_wait)
            return place(engine, card, *args)
        self.engine._admit = admit_with_wait

        def host_start(runner, *args, **kwargs):
            host_starts.append(args)
            return original_start(runner, *args, **kwargs)

        def box_factory(*args):
            box_factories.append(args)
            return factory(*args)
        self.engine.box_runner_factory = box_factory
        with mock.patch.object(HostRunner, "start", host_start), \
                mock.patch.object(boxstep, "place", observed_place), \
                mock.patch.object(br.BoxRunner, "start", side_effect=AssertionError("BoxRunner.start")):
            self.assertTrue(self.start(card_id)["ok"])
            step = self.started(card_id)
            finished = self.finished(card_id)
        self.assertNotIn("runner", step)
        self.assertEqual(finished["result"], "done")
        self.assertTrue(seen)
        self.assertEqual(set(seen), {boxwait.ENVIRONMENT})       # place widział zapisaną zgodę środowiska
        self.assertEqual(len(host_starts), 1)
        self.assertEqual(box_factories, [])
        self.assertEqual(self.of(card_id, "step.waiting"), [])
        self.assertEqual(self.incus_calls(), incus_before)
        self.assertEqual(self.proxy.calls, [])
        self.assertEqual(fold(self.engine.journal.read(card_id)).box_wait, boxwait.ENVIRONMENT)
