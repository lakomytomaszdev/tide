"""09.11, po instalacji K-38: koszt odświeżenia kart i odczytu ustawień przy wyłączonym pudełku.

Pomiar przed poprawką (Tablica właściciela, 30 kart z dziennikiem): jedno `board_monitor._derived` wczytywało całą
Tablicę 107 razy (widok zgody środowiska PT-172/PT-263, także przy `box.enabled: false`), a `GetCardFlowSettings`
czekało w jednowątkowej kolejce za `engine.tick` — odpowiedzi D-Bus 14–46 s. PT-290: zgoda środowiska obowiązuje
tylko przy włączonym pudełku, więc przy wyłączonym widok zgody liczymy wyłącznie dla karty, która już na nią czeka.
"""
import json
import threading
from unittest import mock

from tide_daemon.box import exposure
from tide_daemon.cardflow import boxwait, envconsent

from .boxflow import BoxFlow
from .privatebus import wait_until


class RefreshCostTest(BoxFlow):
    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.card_id = self.card(description="Dodaj eksport.")
        journal = self.engine.journal
        journal.append(self.card_id, "card.started", entry="plan", executor="Sonnet", program="claude",
                       provider="anthropic")
        rows = [{"workId": "#1", "title": "A", "blockers": [], "executor": "Sonnet", "reviewer": "Opus",
                 "class": "standard", "note": ""}]
        journal.append(self.card_id, "plan.proposed", title="Plan", prefix="PT", rows=rows, planner="Opus", box=None)
        journal.append(self.card_id, "plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])

    def refresh(self):
        """(migawka karty, liczba pełnych wczytań Tablicy podczas jej wyliczenia)."""
        store, calls = self.engine.store, []
        real = store.load

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)
        with mock.patch.object(store, "load", side_effect=counting):
            out = self.engine.derived({"id": self.card_id, "column": "todo"})
        return out, len(calls)

    def test_disabled_box_refresh_reads_no_board_and_has_no_environment_section(self):
        self.box_enabled = False
        out, loads = self.refresh()
        self.assertEqual(loads, 0, "odświeżenie karty wczytało Tablicę przy wyłączonym pudełku")
        self.assertEqual(out["flow"]["boxWait"], "")
        self.assertNotIn("environment", out["flow"]["plan"])
        self.assertNotIn("environment", out["flow"])

    def test_enabled_box_refresh_still_builds_the_environment_section(self):
        out, loads = self.refresh()
        self.assertIn("environment", out["flow"]["plan"])
        self.assertGreater(loads, 0)

    def test_project_consent_model_is_computed_only_with_the_box_enabled(self):
        models = []
        real = envconsent.model

        def counting(engine, card, st, *rest):
            models.append(card)
            return real(engine, card, st, *rest)
        with mock.patch.object(envconsent, "_project_state", return_value=(exposure.PROJECT, "")), \
                mock.patch.object(envconsent, "model", side_effect=counting):
            self.box_enabled = False
            self.engine.derived({"id": self.card_id, "column": "todo"})
            self.assertEqual(models, [], "model zgody plasma-tide liczony przy wyłączonym pudełku")
            self.box_enabled = True
            self.engine.derived({"id": self.card_id, "column": "todo"})
            self.assertEqual(models, [self.card_id])

    def test_card_already_waiting_for_the_environment_keeps_the_section_when_disabled(self):
        """Dawne `boxWait: environment` (test_cardflow_env_consent_switch) nadal da się rozstrzygnąć z panelu."""
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.ENVIRONMENT, source="ui")
        self.box_enabled = False
        out, _ = self.refresh()
        self.assertEqual(out["flow"]["boxWait"], boxwait.ENVIRONMENT)
        self.assertIn("environment", out["flow"]["plan"])


class SettingsReadTest(BoxFlow):
    def test_flow_settings_answer_while_the_flow_worker_is_busy(self):
        """Odczyt ustawień nie czeka za `engine.tick` w jednowątkowej kolejce przepływu."""
        release, started = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def busy():
            started.set()
            release.wait(20)
        self.assertTrue(self.flow._submit(busy, lambda future: None))
        self.assertTrue(started.wait(5), "zadanie blokujące nie wystartowało")
        replies = []
        self.flow.get_settings(replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=1), "GetCardFlowSettings czekało za zajętą kolejką")
        reply = json.loads(replies[0])
        self.assertTrue(reply["ok"], reply)
        self.assertIn("reviewRoundsBeforeQuestion", reply)
