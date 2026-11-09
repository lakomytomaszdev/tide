"""PT-290 (K-38 F2, decyzja D1): oczekiwanie na zgodę środowiska i budowa tylko przy `box.enabled: true`.

Następczo wobec PT-172 (zatwierdzenie planu ustawiało `boxWait: environment` bezwarunkowo) i PT-289 (F1: wyłącznik
pudełka przed zgodą środowiska w `boxstep.place`). Sprawdzamy obie ścieżki zatwierdzenia planu — nowy plan
(`ApproveCardPlan`) i poprawkę zatwierdzonego planu (`ApproveCardEnvironment` z „Tylko plan”) — oraz bezpośrednie
`ApproveCardEnvironment` poza UI: liczniki buildera/launchera/Incusa, a nie tylko pole karty. Projekty: plasma-tide
(ekspozycja v1), projekt z profilem i projekt bez profilu. Wszystko na atrapach `boxflow.py`.
"""
import json
import os
from unittest import mock

from tide_daemon.box import exposure
from tide_daemon.box import packs
from tide_daemon.box import profile as bp
from tide_daemon.box import state as bs
from tide_daemon.cardflow import boxstep, boxwait, envconsent
from tide_daemon.cardflow.engine import BOX_STEP_KINDS
from tide_daemon.cardflow.state import fold

from .boxflow import SLUG, BoxFlow
from .privatebus import wait_until
from . import test_cardflow_plan_integration as fixtures

BOX_YAML = "tide-box: 1\nnetwork:\n  domains:\n    - api.example.org\n"
RED_YAML = "tide-box: 1\nnetwork:\n  domains:\n    - github.com\n    - api.example.org\n"
RED_ACCEPTED = RED_YAML + "accepted_red:\n  - github.com\n"
VARIANTS = ("bare", "profile", exposure.PROJECT)   # projekt bez profilu / z profilem / plasma-tide


class Counters(BoxFlow):
    """Liczniki wszystkiego, co mogłoby zbudować, uruchomić albo wystawić pudełko."""

    def setUp(self):
        self.launched, self.builders = [], []
        super().setUp()
        self.incus_before = self.incus_calls()

    def wire(self, engine):
        super().wire(engine)
        engine.box_launcher = lambda slug, profile, action: self.launched.append((slug, action))
        factory = engine.box_builder

        def counting():
            self.builders.append(1)
            return factory()
        engine.box_builder = counting

    def assert_nothing_built(self):
        self.assertEqual(self.launched, [], "zlecona budowa pudełka")
        self.assertEqual(self.builders, [], "utworzony builder pudełka")
        self.assertEqual(self.incus_calls(), self.incus_before, "wywołanie Incusa")
        self.assertEqual(self.proxy.calls, [], "polityka proxy pudełka")

    def env_waits(self, card_id):
        return [e for e in self.of(card_id, "box.wait") if e.get("value") == boxwait.ENVIRONMENT]

    def state(self, card_id):
        return fold(self.engine.journal.read(card_id))

    def variant(self, name):
        """(slug do `boxstep.place`, łatka `_project_state` albo None). `profile` zapisuje profil projektu `proj`."""
        if name == "profile":
            self.approve()
        if name == exposure.PROJECT:
            return exposure.PROJECT, mock.patch.object(envconsent, "_project_state",
                                                       return_value=(exposure.PROJECT, self.repo))
        return SLUG, None

    def host_allowed(self, card_id, slug):
        """Następny hostowy krok wykonawczy nie czeka (wyłącznik F1 i brak oczekiwania F2)."""
        for kind in BOX_STEP_KINDS:
            self.assertIsNone(boxstep.place(self.engine, card_id, slug, kind, "", BOX_STEP_KINDS))


class PlanApprovalSwitchTest(Counters):
    """Obie ścieżki zatwierdzenia planu przez monitor (jak D-Bus), z prawdziwym `approve_plan`."""

    proposal = fixtures.PlanFlowTest.proposal
    submit = fixtures.PlanFlowTest.submit

    def setUp(self):
        super().setUp()
        # Każdy wariant trzyma ticket na hoście do końca testu; trzy warianty i krok poprawki planu nie mogą
        # wyczerpać domyślnych 3 slotów dostawcy (EditCardPlan → FlowDeferred „limit kroków dostawcy”).
        with open(self.settings, encoding="utf-8") as fh:
            settings = json.load(fh)
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump({**settings, "providerSlots": {"anthropic": 12}}, fh)

    def call(self, method, *args):
        box = []
        getattr(self.flow, method)(*args, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20), f"{method} bez odpowiedzi")
        return json.loads(box[0])

    def approved_with_held_ticket(self, card_id):
        """Zatwierdzony plan (ApproveCardPlan) z pierwszym ticketem trzymanym na hoście do końca testu."""
        path = self.proposal(card_id)
        ticket = os.path.join(os.path.dirname(path), "tickets", "1.md")
        with open(ticket, encoding="utf-8") as fh:
            body = fh.read()
        with open(ticket, "w", encoding="utf-8") as fh:
            fh.write(body.replace("ATRAPA: gotowe", "ATRAPA: na-sygnal"))
        self.assertTrue(self.submit(card_id, path)["ok"])
        reply = self.call("approve_plan", card_id)
        self.assertTrue(reply["ok"], reply)
        self.addCleanup(self.release, card_id)
        return reply

    def release(self, card_id):
        for step in self.of(card_id, "step.started"):
            if step.get("kind") == "ticket":
                open(os.path.join(step["stepDir"], "out", "release-agent"), "w").close()

    def ticket_started(self, card_id):
        found = wait_until(lambda: any(s.get("kind") == "ticket" for s in self.of(card_id, "step.started")),
                           timeout=30)
        problems = [e for e in self.events(card_id) if e["type"] == "flow.error" or e.get("ok") is False]
        self.assertTrue(found, json.dumps(problems, ensure_ascii=False, indent=1))
        return next(s for s in self.of(card_id, "step.started") if s.get("kind") == "ticket")

    # --- nowy plan (ApproveCardPlan) ------------------------------------------------------------------

    def test_disabled_new_plan_approval_creates_no_environment_wait_and_the_host_ticket_starts(self):
        self.box_enabled = False
        self.approve()                                     # projekt z profilem: dawniej zawsze `environment`
        card = self.card()
        self.approved_with_held_ticket(card)
        st = self.state(card)
        self.assertEqual(self.env_waits(card), [], "zatwierdzenie planu utworzyło oczekiwanie environment")
        self.assertEqual(boxwait.card_box_wait(st), "")
        self.assertTrue(boxwait.environment_deferred(st), "zgoda środowiska odroczona, nie udzielona")
        step = self.ticket_started(card)
        self.assertNotIn("runner", step)                   # HostRunner
        self.assertEqual(self.of(card, "step.waiting"), [])
        for slug in (SLUG, exposure.PROJECT, "bez-profilu"):
            with self.subTest(slug=slug):
                self.host_allowed(card, slug)
        self.assert_nothing_built()

    def test_enabled_new_plan_approval_still_waits_for_the_environment(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.assertTrue(self.call("approve_plan", card)["ok"])
        self.assertEqual(boxwait.card_box_wait(self.state(card)), boxwait.ENVIRONMENT)
        self.assertEqual([e["source"] for e in self.env_waits(card)], ["plan-approval"])
        self.assertFalse(wait_until(lambda: any(s.get("kind") == "ticket" for s in self.of(card, "step.started")),
                                    timeout=2), "ticket ruszył bez zgody środowiska")
        self.assert_nothing_built()

    # --- poprawka zatwierdzonego planu (ApproveCardEnvironment „Tylko plan” z okna planu) ------------------

    def revision_pending(self, card_id):
        self.approved_with_held_ticket(card_id)
        reply = self.call("edit_plan", card_id, json.dumps({"action": "return", "feedback": "Dodaj ticket"}))
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(wait_until(lambda: len(self.of(card_id, "plan.reviewed")) == 2, timeout=30),
                        self.events(card_id))
        self.assertTrue(self.state(card_id).plan_revision.get("pending"))
        plan = self.engine.derived({"id": card_id, "column": "waiting"})["flow"]["plan"]
        self.assertEqual(plan["status"], "pending")        # okno pokazuje sekcję środowiska z „Tylko plan”

    def test_disabled_revision_of_an_approved_plan_is_approved_without_environment_wait(self):
        self.box_enabled = False
        for name in VARIANTS:
            with self.subTest(project=name):
                slug, patch = self.variant(name)
                card = self.card(title=f"Poprawka {name}")
                self.revision_pending(card)
                before = len(self.env_waits(card))
                if patch:
                    patch.start()
                try:
                    reply = self.call("approve_environment", card, BOX_YAML, True)
                finally:
                    if patch:
                        patch.stop()
                self.assertTrue(reply["ok"], reply)
                self.assertEqual(len(self.env_waits(card)), before, "poprawka planu utworzyła oczekiwanie")
                self.assertEqual(boxwait.card_box_wait(self.state(card)), "")
                self.assertEqual(len(self.of(card, "plan.revision-approved")), 1, "różnica niezatwierdzona")
                self.host_allowed(card, slug)
                self.assertEqual((reply["onlyPlan"], reply["boxWait"], reply["boxEnabled"], reply["build"]),
                                 (True, "", False, ""))
                self.assertIn("box.enabled=false", reply["message"])
                if slug != exposure.PROJECT:
                    self.assertEqual(bp.load_card(SLUG, card), None, "„Tylko plan” nie zapisuje profilu karty")
        self.assert_nothing_built()

    def test_enabled_revision_needs_the_environment_and_direct_plan_approval_keeps_prior_consent(self):
        card = self.card()
        self.approved_with_held_ticket(card)
        self.assertEqual(boxwait.card_box_wait(self.state(card)), boxwait.ENVIRONMENT)
        reply = self.call("approve_environment", card, "tide-box: 1\n", False)   # świadoma zgoda, potem budowa
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["build"], "build")
        self.assertEqual(self.launched, [(SLUG, "build")])
        reply = self.call("edit_plan", card, json.dumps({"action": "return", "feedback": "Dodaj ticket"}))
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=30), self.events(card))
        reply = self.call("approve_environment", card, BOX_YAML, True)            # „Tylko plan” dla poprawki
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["boxWait"], boxwait.ENVIRONMENT)
        self.assertEqual(len(self.of(card, "plan.revision-approved")), 1)
        self.assertEqual(boxwait.card_box_wait(self.state(card)), boxwait.ENVIRONMENT)
        self.assertEqual(self.launched, [(SLUG, "build")], "„Tylko plan” nie buduje")


class DirectApproveSwitchTest(Counters):
    """`ApproveCardEnvironment` wołane wprost (poza UI), plan zatwierdza atrapa — liczniki buildera i Incusa."""

    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.planned = []
        self.engine.approve_plan = lambda card: (self.planned.append(card), {"ok": True, "id": card})[1]
        self.card_id = self.card(description="Dodaj eksport.")
        journal = self.engine.journal
        journal.append(self.card_id, "card.started", entry="plan", executor="Sonnet", program="claude",
                       provider="anthropic")
        rows = [{"workId": "#1", "title": "A", "blockers": [], "executor": "Sonnet", "reviewer": "Opus",
                 "class": "standard", "note": ""}]
        journal.append(self.card_id, "plan.proposed", title="Plan", prefix="PT", rows=rows, planner="Opus")
        journal.append(self.card_id, "plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])

    def approve_env(self, text, only_plan=False):
        return self.engine.approve_environment(self.card_id, text, only_plan)

    def test_disabled_direct_approval_records_the_decision_without_build_start_incus_or_packs(self):
        self.box_enabled = False
        # Wcześniejsza zgoda i historia pudełka, które nie mogą zniknąć.
        bs.record(SLUG, "box.approved", bs.box_name(SLUG), source="ui", card="K-99", accepted_red=[], items=[],
                  recipe="stary", path="x")
        history = self.engine.journal.read(self.card_id)
        with mock.patch.object(packs, "materialize", side_effect=AssertionError("kopia paczek")), \
                mock.patch.object(boxstep, "builder_of", side_effect=AssertionError("builder")):
            reply = self.approve_env(RED_ACCEPTED)
        self.assertTrue(reply["ok"], reply)
        self.assert_nothing_built()
        self.assertEqual(reply["build"], "")
        self.assertEqual((reply["onlyPlan"], reply["boxEnabled"], reply["decision"]), (False, False, "recorded"))
        self.assertIn("budowy nie zlecono", reply["message"])
        self.assertIn("środowisko nie działa", reply["message"])
        self.assertEqual(self.planned, [self.card_id])
        # Zapis decyzji: profile i `box.approved`; stare wpisy zostają.
        self.assertEqual(bp.load_card(SLUG, self.card_id)["accepted_red"], ["github.com"])
        approved = [e for e in bs.events(SLUG) if e["type"] == "box.approved"]
        self.assertEqual([e["card"] for e in approved], ["K-99", self.card_id])
        self.assertEqual(self.engine.journal.read(self.card_id)[:len(history)], history)
        # Bez oczekiwania, host działa; zgoda na budowę wymaga potwierdzenia po włączeniu.
        st = self.state(self.card_id)
        self.assertEqual(self.env_waits(self.card_id), [])
        self.assertTrue(boxwait.environment_deferred(st))
        self.host_allowed(self.card_id, SLUG)

    def test_disabled_only_plan_for_every_project_kind_creates_no_wait_and_builds_nothing(self):
        self.box_enabled = False
        for name in VARIANTS:
            with self.subTest(project=name):
                slug, patch = self.variant(name)
                if patch:
                    patch.start()
                try:
                    reply = self.approve_env(BOX_YAML, only_plan=True)
                finally:
                    if patch:
                        patch.stop()
                self.assertTrue(reply["ok"], reply)
                self.assertEqual(self.env_waits(self.card_id), [])
                self.host_allowed(self.card_id, slug)
                self.assertEqual((reply["boxWait"], reply["boxEnabled"]), ("", False))
        self.assert_nothing_built()

    def test_disabled_invalid_environment_is_still_refused_without_any_write(self):
        self.box_enabled = False
        reply = self.approve_env(RED_YAML)
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"], "invalid-environment")
        self.assertEqual((self.planned, bs.events(SLUG), self.of(self.card_id, "box.wait")), ([], [], []))
        self.assert_nothing_built()

    def test_disabled_existing_environment_wait_is_kept_and_nothing_is_built_after_restart(self):
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.ENVIRONMENT, source="ui")  # dawna historia
        self.box_enabled = False
        self.restart()
        self.engine = self.flow.engine
        self.assertFalse(wait_until(lambda: len(self.of(self.card_id, "box.wait")) > 1, timeout=1.5))
        self.flow.stop()
        self.engine.approve_plan = lambda card: (self.planned.append(card), {"ok": True, "id": card})[1]
        reply = self.approve_env(BOX_YAML)
        self.assertTrue(reply["ok"], reply)
        self.assert_nothing_built()
        self.assertEqual([e["value"] for e in self.of(self.card_id, "box.wait")], [boxwait.ENVIRONMENT],
                         "bez migracji historii oczekiwania")
        self.assertEqual((reply["boxWait"], reply["build"]), (boxwait.ENVIRONMENT, ""))
        self.host_allowed(self.card_id, SLUG)             # F1: host mimo zapisanego `environment`

    # --- ponowne włączenie: samo zatwierdzenie planu nie jest zgodą -------------------------------------

    def test_reenabling_after_plan_only_approval_requires_a_fresh_environment_consent(self):
        self.box_enabled = False
        self.assertTrue(self.approve_env(BOX_YAML, only_plan=True)["ok"])
        self.assertEqual(self.env_waits(self.card_id), [])
        self.box_enabled = True
        for kind in BOX_STEP_KINDS:
            with self.subTest(kind=kind), self.assertRaises(boxstep.BoxWaiting) as caught:
                boxstep.place(self.engine, self.card_id, SLUG, kind, "", BOX_STEP_KINDS)
            self.assertEqual(str(caught.exception), boxstep.WAIT_ENVIRONMENT)
        self.assertEqual([e["source"] for e in self.env_waits(self.card_id)], ["box-enabled"])
        flow = self.engine.derived({"id": self.card_id, "column": "todo"})["flow"]
        self.assertEqual(flow["boxWait"], boxwait.ENVIRONMENT)   # panel pokazuje sekcję środowiska
        self.assertEqual(self.launched, [])
        # Świadoma zgoda przy włączonym pudełku: walidacja, potem budowa (atrapa).
        self.assertFalse(self.approve_env(RED_YAML)["ok"])
        self.assertEqual(self.launched, [])
        reply = self.approve_env(BOX_YAML)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(boxwait.card_box_wait(self.state(self.card_id)), "")
        self.assertFalse(boxwait.environment_deferred(self.state(self.card_id)))
        self.assertEqual(len(self.launched), 1)

    def test_reenabling_after_a_disabled_decision_still_requires_consent_before_build(self):
        self.box_enabled = False
        self.assertTrue(self.approve_env(BOX_YAML)["ok"])
        self.box_enabled = True
        with self.assertRaises(boxstep.BoxWaiting):
            boxstep.place(self.engine, self.card_id, SLUG, "ticket", "", BOX_STEP_KINDS)
        self.assertEqual(self.launched, [])
        self.assertTrue(self.approve_env(BOX_YAML)["ok"])
        self.assertEqual([a for _s, a in self.launched], ["build"])

    def test_direct_clear_resolves_a_deferred_consent_even_without_a_wait(self):
        self.box_enabled = False
        self.approve_env(BOX_YAML, only_plan=True)
        self.assertTrue(boxwait.environment_deferred(self.state(self.card_id)))
        self.assertIsNone(boxwait.set_box_wait(self.engine.journal, self.card_id, "", source="ui",
                                               deferred=boxwait.ENVIRONMENT))
        boxwait.set_box_wait(self.engine.journal, self.card_id, "", source="ui")
        self.assertFalse(boxwait.environment_deferred(self.state(self.card_id)))
        with self.assertRaises(ValueError):
            boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.START, deferred=boxwait.ENVIRONMENT)

    # --- włączone: kontrola dodatnia i odmowy -----------------------------------------------------------

    def test_enabled_bad_or_unapproved_profile_never_reaches_the_builder(self):
        for text, expected in ((RED_YAML, "wymaga jawnego zaznaczenia"), ("tide-box: 1\nmagic: 1\n", "magic"),
                               ("to nie jest: [yaml", "YAML")):
            with self.subTest(text=text):
                reply = self.approve_env(text)
                self.assertFalse(reply["ok"])
                self.assertIn(expected, reply["message"])
        self.assertEqual(self.planned, [])
        self.assertEqual(self.launched, [])
        reply = self.approve_env(RED_ACCEPTED)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual((reply["build"], reply["boxWait"]), ("build", ""))
        self.assertEqual(self.launched, [(SLUG, "build")])
