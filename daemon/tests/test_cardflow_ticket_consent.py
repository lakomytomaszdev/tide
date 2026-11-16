"""PT-244: publiczne cofnięcie zgody i obserwowalne starty poleceń poza piaskownicą."""
import json
import os
from pathlib import Path
from unittest import mock

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import FlowCase


class ConsentScenario:
    def test_public_revoke_survives_restart_and_requires_each_next_decision(self):
        card = self.card(description="ATRAPA: poza-kolejne")
        started = self.start(card)
        self.assertTrue(started["ok"], started)
        self.wait_column(card, "waiting")
        first = self.of(card, "step.finished")[0]["step"]
        self.assertTrue(self.call("ResolveCardStep", card, json.dumps({
            "step": first, "choice": "run-ticket"}))["ok"])
        # Cofnięcie może wygrać wyścig ze startem pierwszego polecenia; oba porządki są poprawne.
        self.assertTrue(wait_until(lambda: self.shown(card)["derived"]["flow"]["ticketConsents"], timeout=10))
        consent = self.shown(card)["derived"]["flow"]["ticketConsents"][0]
        request = {key: consent[key] for key in ("ticket", "orderSeq", "baseVersion")}
        response = self.call("RevokeTicketConsent", card, json.dumps(request))
        self.assertTrue(response["ok"], response)
        self.assertFalse(response["active"])
        self.assertEqual(self.call("RevokeTicketConsent", card, json.dumps(request)), response)
        shown = self.wait_column(card, "waiting")
        self.assertTrue(shown["derived"]["flow"]["ticketConsents"][0]["revoked"])
        for change, error in (({"ticket": "PT-99999"}, "bad-ticket"),
                              ({"orderSeq": request["orderSeq"] + 1}, "stale-context"),
                              ({"baseVersion": request["baseVersion"] + 1}, "stale-context")):
            self.assertEqual(self.call("RevokeTicketConsent", card, json.dumps({**request, **change}))["error"], error)
        settings = Path(self.base, "config", "tide", "cardflow.json")
        saved = json.loads(settings.read_text())
        settings.write_text(json.dumps({**saved, "autoConsent": True,
            "outsideCommands": {"proj": ["echo zewnetrzne-ok *"]}}))
        self.restart()
        shown = self.wait_column(card, "waiting")
        self.assertTrue(shown["derived"]["flow"]["ticketConsents"][0]["revoked"])
        before = len(self.of(card, "command.started"))
        step = shown["derived"]["flow"]["decisions"][0]["step"]
        self.assertTrue(self.call("ResolveCardStep", card, json.dumps({"step": step, "choice": "run"}))["ok"])
        self.assertTrue(wait_until(lambda: len(self.of(card, "command.finished")) == before + 1, timeout=20))
        self.assertTrue(wait_until(lambda: len(self.of(card, "step.finished")) >= 2
                                  and self.shown(card)["boardColumn"] == "waiting"
                                  and self.shown(card)["derived"]["flow"]["decisions"], timeout=20))
        self.assertEqual(len(self.of(card, "command.started")), before + 1)
        # Świadome ponowne nadanie przywraca automatyczne starty kolejnych próśb.
        self.assertTrue(self.call("ResolveCardStep", card, json.dumps({"choice": "run-ticket"}))["ok"])
        self.assertTrue(wait_until(lambda: len(self.of(card, "command.started")) >= before + 3, timeout=25))
        self.assertTrue(self.call("RevokeTicketConsent", card, json.dumps(request))["ok"])


class TicketConsentDbusTest(ConsentScenario, FlowCase):
    pass


class TicketConsentInProcessTest(ConsentScenario, FlowInProcess):
    def call(self, member, *args):
        methods = {"ResolveCardStep": self.board.resolve_card_step,
                   "RevokeTicketConsent": self.board.revoke_ticket_consent}
        replies = []
        methods[member](*args, replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=20), member)
        return json.loads(replies[0])

    def seeded(self):
        """Publiczne akcje monitora na kontrolowanym stanie kolejki; start procesu pozostaje rzeczywisty."""
        card = self.card()
        self.flow.stop()
        self.flow = self.make_flow()
        self.flow._timer.stop()
        self.assertTrue(wait_until(lambda: not self.flow._ticking, timeout=5))
        eng = self.flow.engine
        order = eng.journal.append(card, "card.started", project="proj", entry="plan", repo=self.repo, baseVersion=1)
        eng.journal.append(card, "plan.approved", rows=[{"id": "PT-1"}, {"id": "PT-2"}])
        steps = []
        for index in range(1, 4):
            name = f"s{index}"
            folder = str(Path(self.base, name))
            Path(folder, "out").mkdir(parents=True)
            step = eng.journal.append(card, "step.started", step=name, ticket="PT-2" if index == 3 else "PT-1",
                                      kind="ticket", stepDir=folder, worktree=self.repo, baseVersion=1)
            eng.journal.append(card, "step.finished", step=name, ticket=step["ticket"], result="denied",
                               outside=True, command="echo zewnetrzne-ok", canAllow=True)
            steps.append(step)
        return card, eng, steps, {"ticket": "PT-1", "orderSeq": order["seq"], "baseVersion": 1}

    def test_queued_auto_consent_is_revoked_but_other_ticket_and_project_rules_stay(self):
        from tide_daemon.cardflow.state import fold
        card, eng, steps, request = self.seeded()
        Path(self.settings).write_text(json.dumps({"autoConsent": True,
            "outsideCommands": {"proj": ["echo zewnetrzne-ok *"]}}))
        for step in steps:
            eng._outside_recheck(card, step, fold(eng.journal.read(card)).finished[step["step"]])
        old = fold(eng.journal.read(card)).step_decisions.copy()
        with mock.patch.object(eng, "_outside_busy", return_value=True):
            eng._outside_ready(card, steps[0], fold(eng.journal.read(card)).finished["s1"], old["s1"])
        self.assertIn("s1", eng._outside_waiting)
        self.assertTrue(self.call("RevokeTicketConsent", card, json.dumps(request))["ok"])
        self.assertNotIn("s1", eng._outside_waiting)
        st = fold(eng.journal.read(card))
        self.assertNotIn("s1", st.step_decisions)
        self.assertIn("s3", st.step_decisions)
        # Nawet stara migawka decyzji nie uprawnia do startu po cofnięciu.
        eng._outside_ready(card, steps[0], st.finished["s1"], old["s1"])
        eng._outside_recheck(card, steps[1], st.finished["s2"])
        self.assertFalse(self.of(card, "command.started"))
        self.assertNotIn("s2", fold(eng.journal.read(card)).step_decisions)
        eng._outside_ready(card, steps[2], st.finished["s3"], st.step_decisions["s3"])
        self.assertEqual([e["step"] for e in self.of(card, "command.started")], ["s3"])
        log = Path(self.of(card, "command.started")[0]["log"])     # <krok>/outside/tide-cmd-<attempt>/ (PT-479)
        self.assertEqual(log.parent.parent, Path(steps[2]["stepDir"], "outside"))
        self.assertTrue(wait_until(lambda: log.exists() and "zewnetrzne-ok" in log.read_text(), timeout=5))
        self.assertEqual(json.loads(Path(self.settings).read_text())["outsideCommands"],
                         {"proj": ["echo zewnetrzne-ok *"]})

    def test_paused_card_does_not_start_approved_command_until_resumed(self):
        """07.11 (K-38/PT-264): automatyczna zgoda 3 min po wstrzymaniu karty puściła pełne testy w trakcie gry."""
        from tide_daemon.cardflow.state import fold
        card, eng, steps, _ = self.seeded()
        self.assertTrue(self.call("ResolveCardStep", card, json.dumps({"choice": "run", "step": "s1"}))["ok"])
        st = fold(eng.journal.read(card))
        with mock.patch.object(eng, "_outside_busy", return_value=True):
            eng._outside_ready(card, steps[0], st.finished["s1"], st.step_decisions["s1"])
        self.assertIn("s1", eng._outside_waiting)
        eng.journal.append(card, "card.paused")
        self.assertIsNone(eng._outside_ready(card, steps[0], st.finished["s1"], st.step_decisions["s1"]))
        self.assertFalse(self.of(card, "command.started"))
        self.assertNotIn("s1", eng._outside_waiting)     # wstrzymana karta nie blokuje kolejki innym
        eng.journal.append(card, "card.resumed")
        eng._outside_ready(card, steps[0], st.finished["s1"], st.step_decisions["s1"])
        self.assertEqual([e["step"] for e in self.of(card, "command.started")], ["s1"])

    def test_outside_command_runs_without_qml_disk_cache(self):
        """08.11: testy qml6 poza piaskownicą zapełniały ~/.cache/QtProject (100 GB) — polecenie outside dostaje
        QML_DISABLE_DISK_CACHE=1 niezależnie od uruchamiacza testów na gałęzi."""
        from tide_daemon.cardflow.state import fold
        card, eng, steps, _ = self.seeded()
        self.assertTrue(self.call("ResolveCardStep", card, json.dumps({"choice": "run", "step": "s1"}))["ok"])
        st = fold(eng.journal.read(card))
        # Uruchamiacz testów sam ustawia tę zmienną — bez jej usunięcia test nie sprawdzałby usługi.
        clean = {k: v for k, v in os.environ.items() if k != "QML_DISABLE_DISK_CACHE"}
        with mock.patch.dict(os.environ, clean, clear=True), \
                mock.patch("tide_daemon.cardflow.engine.subprocess.Popen") as popen:
            eng._outside_ready(card, steps[0], st.finished["s1"], st.step_decisions["s1"])
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(popen.call_args.kwargs["env"].get("QML_DISABLE_DISK_CACHE"), "1")
        # Jawna wartość użytkownika zostaje (setdefault).
        st = fold(eng.journal.read(card))
        self.assertTrue(self.call("ResolveCardStep", card, json.dumps({"choice": "run", "step": "s2"}))["ok"])
        st = fold(eng.journal.read(card))
        with mock.patch.dict(os.environ, {**clean, "QML_DISABLE_DISK_CACHE": "0"}, clear=True), \
                mock.patch.object(eng, "_outside_busy", return_value=False), \
                mock.patch("tide_daemon.cardflow.engine.subprocess.Popen") as popen:
            eng._outside_active.clear()
            eng._outside_ready(card, steps[1], st.finished["s2"], st.step_decisions["s2"])
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(popen.call_args.kwargs["env"].get("QML_DISABLE_DISK_CACHE"), "0")

    def test_running_command_finishes_and_separate_manual_once_still_starts(self):
        from tide_daemon.cardflow.state import fold
        card, eng, steps, request = self.seeded()
        self.assertTrue(self.call("ResolveCardStep", card, json.dumps({"choice": "run-ticket", "step": "s1"}))["ok"])
        self.assertTrue(self.call("ResolveCardStep", card, json.dumps({"choice": "run", "step": "s2"}))["ok"])
        st = fold(eng.journal.read(card))
        eng._outside_ready(card, steps[0], st.finished["s1"], st.step_decisions["s1"])
        self.assertTrue(self.call("RevokeTicketConsent", card, json.dumps(request))["ok"])
        exit_file = Path(self.of(card, "command.started")[0]["exitFile"])     # plik opakowania tide-step (PT-479)
        self.assertEqual(exit_file.parent.parent, Path(steps[0]["stepDir"], "outside"))
        self.assertTrue(wait_until(exit_file.exists, timeout=5))
        self.assertTrue(wait_until(lambda: eng._outside_ready(card, steps[0], st.finished["s1"],
                                                            st.step_decisions["s1"]) is not None, timeout=10))
        self.assertEqual(self.of(card, "command.finished")[0]["exit"], 0)
        eng._outside_ready(card, steps[1], st.finished["s2"], st.step_decisions["s2"])
        self.assertTrue(wait_until(lambda: eng._outside_ready(card, steps[1], st.finished["s2"],
                                                            st.step_decisions["s2"]) is not None, timeout=10))
        self.assertEqual([e["step"] for e in self.of(card, "command.started")], ["s1", "s2"])

    def test_new_order_and_base_boundary_do_not_inherit_old_consent(self):
        from tide_daemon.cardflow.state import fold
        card, eng, steps, request = self.seeded()
        self.assertTrue(self.call("ResolveCardStep", card, json.dumps({"choice": "run-ticket", "step": "s1"}))["ok"])
        eng.journal.append(card, "card.base-changed", previousBaseVersion=1, baseVersion=2,
                           baseRef="refs/heads/master", baseBranch="master", baseCommit=self.master)
        self.assertEqual(fold(eng.journal.read(card)).ticket_consents, {})
        self.assertEqual(self.call("RevokeTicketConsent", card, json.dumps(request))["error"], "stale-context")
        eng.journal.append(card, "card.started", entry="plan", repo=self.repo, baseVersion=2)
        self.assertEqual(fold(eng.journal.read(card)).ticket_consents, {})
        self.assertEqual(self.call("RevokeTicketConsent", card, json.dumps(request))["error"], "stale-context")
        eng.journal.append(card, "outside.ticket-consent", **request)
        self.assertEqual(fold(eng.journal.read(card)).ticket_consents, {})

    def test_forbidden_command_cannot_be_granted_or_started_after_regrant(self):
        from tide_daemon.cardflow.state import fold
        card, eng, steps, request = self.seeded()
        # Zakaz projektu obowiązuje także aktywną zgodę ticketu i historyczną zgodę jednorazową.
        self.assertTrue(self.call("ResolveCardStep", card, json.dumps({"choice": "run-ticket", "step": "s1"}))["ok"])
        self.assertTrue(json.loads(self.board.upsert_project(json.dumps({"repo": self.repo,
                                                                        "deniedCommands": ["make install"]})))["ok"])
        forbidden = eng.journal.append(card, "step.finished", step="s2", ticket="PT-1", result="denied",
                                       outside=True, command="make install", canAllow=False)
        self.assertEqual(self.call("ResolveCardStep", card, json.dumps({"choice": "run-ticket", "step": "s2"}))["error"],
                         "forbidden-command")
        eng._outside_ready(card, steps[1], forbidden, {"choice": "run", "auto": True})
        self.assertFalse(self.of(card, "command.started"))
        self.assertEqual(fold(eng.journal.read(card)).finished["s2"]["command"], "make install")

    def test_finished_command_keeps_result_when_revoked_before_agent_resume(self):
        from tide_daemon.cardflow.state import fold
        card, eng, steps, request = self.seeded()
        self.assertTrue(self.call("ResolveCardStep", card, json.dumps({"choice": "run-ticket", "step": "s1"}))["ok"])
        st = fold(eng.journal.read(card))
        eng._outside_ready(card, steps[0], st.finished["s1"], st.step_decisions["s1"])
        self.assertTrue(wait_until(lambda: eng._outside_ready(card, steps[0], st.finished["s1"],
                                                            st.step_decisions["s1"]) is not None, timeout=10))
        self.assertTrue(self.call("RevokeTicketConsent", card, json.dumps(request))["ok"])
        current = fold(eng.journal.read(card))
        self.assertIn("s1", current.step_decisions)
        result = eng._outside_ready(card, steps[0], current.finished["s1"], current.step_decisions["s1"])
        self.assertIn("Kod wyjścia: 0", result["answer"])
        self.assertEqual(len(self.of(card, "command.started")), 1)
