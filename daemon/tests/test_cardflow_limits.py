"""PT-69: odczyty limitów i sygnały wyczerpania bez prawdziwego dostawcy."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tide_daemon.cardflow import limits
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.cardflow.state import derived, fold

from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import FlowCase
from .privatebus import wait_until


class QuotaReadingTest(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000

    def snapshot(self, session=None, weekly=None, available=True):
        return {"available": available, "services": {"chatgpt": {
            "session": session or {}, "weekly": weekly or {}}}}

    def test_null_session_does_not_hide_low_weekly_limit(self):
        data = self.snapshot(session={"remaining": None, "readTs": self.now},
                             weekly={"remaining": 8, "readTs": self.now - 10, "resetTs": self.now + 3600})
        reading = limits.low_limit(data, "openai", 10, self.now)
        self.assertEqual((reading["window"], reading["remaining"]), ("weekly", 8))
        self.assertIn("Codex", limits.wait_reason(reading))
        self.assertIn("do ", limits.wait_reason(reading))

    def test_stale_unavailable_and_reset_windows_do_not_block(self):
        stale = self.snapshot(session={"remaining": 2, "readTs": self.now - 3600})
        self.assertIsNone(limits.low_limit(stale, "openai", 10, self.now))
        current = self.snapshot(weekly={"remaining": 0, "readTs": self.now,
                                        "resetTs": self.now - 1, "reason": "resetPassed"})
        self.assertIsNone(limits.low_limit(current, "openai", 10, self.now))
        current["available"] = False
        self.assertIsNone(limits.low_limit(current, "openai", 10, self.now))

    def test_unknown_provider_or_remaining_above_threshold_does_not_block(self):
        data = self.snapshot(weekly={"remaining": 11, "readTs": self.now})
        self.assertIsNone(limits.low_limit(data, "openai", 10, self.now))
        self.assertIsNone(limits.low_limit(data, "other", 10, self.now))

    def test_planning_warning_does_not_say_the_conversation_is_waiting(self):
        info = limits.low_limit(self.snapshot(session={"remaining": 8, "readTs": self.now}),
                                "openai", 10, self.now)
        self.assertIn("Mało limitu Codex", limits.planning_warning(info))
        self.assertNotIn("czeka", limits.planning_warning(info))

    def test_recovery_requires_exhausted_window_not_an_unrelated_healthy_window(self):
        data = self.snapshot(session={"remaining": None, "readTs": self.now},
                             weekly={"remaining": 80, "readTs": self.now})
        self.assertFalse(limits.recovered(data, "openai", 10, self.now, "session"))
        data["services"]["chatgpt"]["session"] = {"remaining": 75, "readTs": self.now}
        self.assertTrue(limits.recovered(data, "openai", 10, self.now, "session"))


class ExhaustionClassifierTest(unittest.TestCase):
    def check(self, rows, tool):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "step.log")
            with open(path, "w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")
            return limits.exhausted_log(path, tool)

    def test_explicit_provider_errors_are_detected(self):
        self.assertTrue(self.check([{"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}],
                                   "claude"))
        self.assertTrue(self.check([{"type": "result", "is_error": True,
                                    "result": "You've hit your limit"}], "claude"))
        self.assertTrue(self.check([{"type": "error", "message": "Rate limit reached"}], "codex"))
        self.assertTrue(self.check([{"type": "error", "api_error_status": 429}], "codex"))

    def test_warnings_and_agent_text_are_not_exhaustion(self):
        self.assertFalse(self.check([{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}},
                                     {"type": "assistant", "message": "Rate limit reached in a test fixture"}],
                                    "claude"))
        self.assertFalse(self.check([{"type": "result", "is_error": False,
                                     "result": "You've hit your limit"}], "claude"))


class FlowQuotaContractTest(FlowInProcess):
    def test_low_limit_holds_new_openai_step_and_uses_fresh_weekly_reading(self):
        now = self.flow.engine.clock()
        self.flow.engine.limits_source = lambda: {
            "available": True, "services": {"chatgpt": {
                "session": {"remaining": None, "readTs": now},
                "weekly": {"remaining": 7, "readTs": now, "resetTs": now + 3600}}}}
        selected = SimpleNamespace(provider="openai", local=False)
        reason = self.flow.engine._wait_reason(selected)
        self.assertIn("czeka na limit Codex do", reason)
        self.assertIn("7%", reason)
        self.flow.engine.limits_source = lambda: {"available": False, "services": {}}
        self.assertEqual(self.flow.engine._wait_reason(selected), "")

    def test_flow_settings_persist_without_erasing_other_fields(self):
        engine = self.flow.engine
        self.assertEqual(engine.get_flow_settings()["limitTransfer"], {"mode": "ask", "executor": ""})
        reply = engine.set_flow_settings({"limitTransfer": {"mode": "auto", "executor": "Codex mocny"},
                                          "limitThresholdPercent": 12})
        self.assertEqual(reply["limitTransfer"], {"mode": "auto", "executor": "Codex mocny"})
        with open(self.settings, encoding="utf-8") as fh:
            stored = json.load(fh)
        self.assertEqual(stored["stepGuardMinutes"], self.guard_minutes)
        self.assertEqual(stored["limitThresholdPercent"], 12)
        with self.assertRaises(FlowError):
            engine.set_flow_settings({"limitTransfer": {"mode": "auto", "executor": ""}})
        self.assertEqual(engine.get_flow_settings(), reply)

    def test_low_limit_queues_card_then_starts_when_reading_recovers(self):
        now = self.flow.engine.clock()
        metric = {"remaining": 7, "readTs": now, "resetTs": now + 1000}
        self.flow.engine.limits_source = lambda: {"available": True,
                                                  "services": {"claude": {"session": metric}}}
        card = self.card()
        reply = self.start(card)
        self.assertTrue(reply["queued"], reply)
        self.assertEqual(self.of(card, "step.started"), [])
        self.assertIn("czeka na limit Claude", self.of(card, "step.waiting")[-1]["reason"])
        self.assertIn("czeka na limit Claude", self.shown(card)["derived"]["note"])
        metric["remaining"] = 50
        self.assertTrue(wait_until(lambda: self.of(card, "step.started"), timeout=20), self.events(card))
        self.wait_column(card, "review", timeout=40)


class LimitProjectionTest(unittest.TestCase):
    def test_finished_limit_step_is_actionable_before_automatic_retry(self):
        events = [
            {"v": 1, "seq": 1, "ts": "2026-10-29T14:00:00+01:00", "type": "card.started",
             "entry": "now", "executor": "Sonnet", "provider": "anthropic"},
            {"v": 1, "seq": 2, "ts": "2026-10-29T14:00:01+01:00", "type": "step.started",
             "step": "K-1-PT-1-1", "kind": "ticket", "ticket": "PT-1", "executor": "Sonnet",
             "provider": "anthropic"},
            {"v": 1, "seq": 3, "ts": "2026-10-29T14:00:02+01:00", "type": "step.limit",
             "step": "K-1-PT-1-1", "ticket": "PT-1", "provider": "anthropic", "reset": "piątek 18:00"},
            {"v": 1, "seq": 4, "ts": "2026-10-29T14:00:03+01:00", "type": "step.finished",
             "step": "K-1-PT-1-1", "ticket": "PT-1", "result": "error", "reason": "limit",
             "wip": {"stopped": True, "committed": True, "commit": "a" * 40, "files": 1}},
        ]
        card = derived(fold(events), {"id": "K-1", "column": "todo", "check": "auto"})
        self.assertEqual((card["column"], card["flow"]["action"]), ("waiting", "limit"))
        self.assertEqual(card["flow"]["limit"]["reset"], "piątek 18:00")
        self.assertEqual(card["flow"]["steps"][0]["provider"], "anthropic")


class TransferFlowTest(FlowInProcess):
    def resolve(self, card, request):
        replies = []
        self.board.resolve_card_step(card, json.dumps(request), replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=30), "brak odpowiedzi ResolveCardStep")
        return json.loads(replies[0])

    def test_manual_transfer_keeps_ticket_worktree_and_wip(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: czekaj-pierwszy")
        started = self.start(card)
        self.assertTrue(started["ok"], started)
        first = self.of(card, "step.started")[0]
        self.assertTrue(wait_until(lambda: os.path.isfile(os.path.join(first["worktree"], "ATRAPA.md")), timeout=15))
        refused = self.resolve(card, {"choice": "transfer", "step": first["step"], "executor": "Sonnet"})
        self.assertEqual((refused["ok"], refused["error"]), (False, "same-provider"), refused)
        self.assertEqual(self.of(card, "step.finished"), [])
        answer = self.resolve(card, {"choice": "transfer", "step": first["step"], "executor": "Codex mocny"})
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(wait_until(lambda: len(self.of(card, "step.started")) >= 2, timeout=30), self.events(card))
        second = self.of(card, "step.started")[1]
        self.assertEqual((second["ticket"], second["worktree"], second["provider"], second["parentStep"]),
                         (first["ticket"], first["worktree"], "openai", first["step"]))
        finished = self.of(card, "step.finished")[0]
        self.assertEqual((finished["result"], finished["reason"]), ("stopped", "transfer"))
        self.assertTrue(finished["wip"]["committed"], finished["wip"])
        self.assertTrue(os.path.isfile(finished["wip"]["handoff"]))
        with open(second["stepDir"] + "/in/prompt.txt", encoding="utf-8") as fh:
            self.assertIn("Notka techniczna przekazania", fh.read())
        self.wait_column(card, "review", timeout=50)

    def test_exhausted_limit_auto_transfers_only_after_wip(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: limit")
        self.flow.engine.set_flow_settings({"limitTransfer": {"mode": "auto", "executor": "Codex mocny"}})
        started = self.start(card)
        self.assertTrue(started["ok"], started)
        self.assertTrue(wait_until(lambda: len(self.of(card, "step.started")) >= 2, timeout=50), self.events(card))
        first, second = self.of(card, "step.started")[:2]
        done = self.of(card, "step.finished")[0]
        self.assertEqual((done["result"], done["reason"]), ("error", "limit"))
        self.assertTrue(done["wip"]["committed"], done["wip"])
        self.assertEqual((second["ticket"], second["worktree"], second["provider"]),
                         (first["ticket"], first["worktree"], "openai"))
        decisions = self.of(card, "step.decision")
        self.assertEqual((decisions[0]["choice"], decisions[0]["automatic"]), ("transfer", True))
        self.wait_column(card, "review", timeout=50)

    def test_wait_for_reset_resumes_same_provider_after_known_reset(self):
        now = self.flow.engine.clock()
        metric = {"remaining": 25, "readTs": now, "resetTs": now + 1000}
        self.flow.engine.limits_source = lambda: {"available": True,
                                                  "services": {"claude": {"session": metric}}}
        card = self.card(description="Dodaj eksport.\n\nATRAPA: limit")
        self.assertTrue(self.start(card)["ok"])
        metric["remaining"] = 0
        self.assertTrue(wait_until(lambda: any(e.get("reason") == "limit" for e in
                                        self.of(card, "step.finished")), timeout=30), self.events(card))
        first = self.of(card, "step.started")[0]
        self.assertEqual(len(self.of(card, "step.started")), 1)
        answer = self.resolve(card, {"choice": "wait-limit"})
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(len(self.of(card, "step.started")), 1)
        metric.update(remaining=40, readTs=now + 1001)
        self.flow.engine.clock = lambda: now + 1001
        self.assertTrue(wait_until(lambda: len(self.of(card, "step.started")) >= 2, timeout=30), self.events(card))
        second = self.of(card, "step.started")[1]
        self.assertEqual((second["provider"], second["ticket"], second["worktree"]),
                         ("anthropic", first["ticket"], first["worktree"]))
        self.wait_column(card, "review", timeout=50)

    def test_failed_wip_never_starts_other_provider_and_can_be_repaired(self):
        from tide_daemon.tasks.stepstop import Snapshot
        card = self.card(description="Dodaj eksport.\n\nATRAPA: czekaj-pierwszy")
        self.assertTrue(self.start(card)["ok"])
        first = self.of(card, "step.started")[0]
        self.assertTrue(wait_until(lambda: os.path.isfile(os.path.join(first["worktree"], "ATRAPA.md")), timeout=15))
        with mock.patch("tide_daemon.tasks.stepstop.git_snapshot", return_value=Snapshot(error="no-sandbox")):
            refused = self.resolve(card, {"choice": "transfer", "step": first["step"], "executor": "Codex mocny"})
        self.assertEqual((refused["ok"], refused["error"]), (False, "wip-failed"), refused)
        self.assertEqual(len(self.of(card, "step.started")), 1)
        self.assertEqual(self.of(card, "step.finished")[0]["wip"]["error"], "no-sandbox")
        self.assertEqual(self.results(card, "merge"), [])
        repaired = self.resolve(card, {"choice": "transfer", "step": first["step"], "executor": "Codex mocny"})
        self.assertTrue(repaired["ok"], repaired)
        self.assertTrue(wait_until(lambda: len(self.of(card, "step.started")) >= 2, timeout=30), self.events(card))
        self.assertTrue(self.of(card, "step.wip")[-1]["wip"]["committed"])
        self.wait_column(card, "review", timeout=50)

    def test_restart_after_transfer_decision_starts_one_new_attempt(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: czekaj-pierwszy")
        self.assertTrue(self.start(card)["ok"])
        first = self.of(card, "step.started")[0]
        self.assertTrue(wait_until(lambda: os.path.isfile(os.path.join(first["worktree"], "ATRAPA.md")), timeout=15))
        self.flow._timer.stop()
        reply = self.resolve(card, {"choice": "transfer", "step": first["step"], "executor": "Codex mocny"})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(len(self.of(card, "step.started")), 1)
        self.restart()
        self.assertTrue(wait_until(lambda: len(self.of(card, "step.started")) >= 2, timeout=30), self.events(card))
        second = self.of(card, "step.started")[1]
        self.assertEqual((second["ticket"], second["worktree"], second["parentStep"]),
                         (first["ticket"], first["worktree"], first["step"]))
        self.wait_column(card, "review", timeout=50)
        self.assertEqual(len([s for s in self.of(card, "step.started") if s.get("parentStep") == first["step"]]), 1)


class FlowSettingsDbusTest(FlowCase):
    def test_get_set_settings_survive_service_restart(self):
        original = self.call("GetCardFlowSettings")
        self.assertEqual(original["limitTransfer"], {"mode": "ask", "executor": ""})
        changed = self.call("SetCardFlowSettings", json.dumps({
            "limitTransfer": {"mode": "auto", "executor": "Codex mocny"},
            "limitThresholdPercent": 15}))
        self.assertTrue(changed["ok"], changed)
        self.assertEqual(changed["limitThresholdPercent"], 15)
        self.restart()
        self.assertEqual(self.call("GetCardFlowSettings")["limitTransfer"],
                         {"mode": "auto", "executor": "Codex mocny"})
        rejected = self.call("SetCardFlowSettings", json.dumps({"limitTransfer": {"mode": "auto", "executor": ""}}))
        self.assertEqual((rejected["ok"], rejected["error"]), (False, "bad-settings"))
