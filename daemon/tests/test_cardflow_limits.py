"""PT-69: odczyty limitów i sygnały wyczerpania bez prawdziwego dostawcy."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tide_daemon.cardflow import limits
from tide_daemon.cardflow.engine import FlowDeferred, FlowError
from tide_daemon.cardflow.state import derived, fold
from tide_daemon.tasks import launch

from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import FlowCase
from .test_cardflow_full_planning import FullPlanningTest, _Terminal
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


class ConsoleClosureTest(unittest.TestCase):
    def test_launcher_signal_requires_recorded_process_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "process.json")
            launch.write_process(path, os.getpid())
            with mock.patch("tide_daemon.tasks.launch.signal.pidfd_send_signal") as send:
                self.assertTrue(launch.signal_flow_launcher(path, 15))
                send.assert_called_once()
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data["launcher"]["start"] = "wrong-start"
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            with mock.patch("tide_daemon.tasks.launch.signal.pidfd_send_signal") as send:
                self.assertFalse(launch.signal_flow_launcher(path, 15))
                send.assert_not_called()

    def test_cancelled_transfer_closes_old_tab_without_shell(self):
        with tempfile.TemporaryDirectory() as claim_dir:
            with open(os.path.join(claim_dir, launch.FLOW_TRANSFER_CLOSE), "w", encoding="utf-8"):
                pass
            with mock.patch("tide_daemon.tasks.launch.claim", return_value=None), \
                 mock.patch("tide_daemon.tasks.launch.os.getcwd", return_value=claim_dir), \
                 mock.patch("tide_daemon.tasks.launch.shell", side_effect=AssertionError("powłoka")):
                self.assertEqual(launch.main(["--task", os.path.join(claim_dir, "cancelled.task")]), 0)


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

    def test_waiting_step_records_cause_and_reset_for_the_full_card(self):
        engine = self.flow.engine
        now = engine.clock()
        engine.limits_source = lambda: {
            "available": True, "services": {"chatgpt": {
                "session": {"remaining": None, "readTs": now},
                "weekly": {"remaining": 7, "readTs": now, "resetTs": now + 3600}}}}
        selected = SimpleNamespace(provider="openai", local=False)
        with self.assertRaises(FlowDeferred):
            engine._admit("K-77", selected, "PT-1", "ticket", "K-77-PT-1-1")
        wait = fold(engine.journal.read("K-77")).waiting[("PT-1", "ticket")]
        self.assertEqual((wait["cause"], wait["until"]), ("quota", limits.reset_label(now + 3600, now)))
        engine.limits_source = lambda: {"available": False, "services": {}}
        engine._wait_reason = lambda _selected: "limit kroków dostawcy openai: 3/3"
        with self.assertRaises(FlowDeferred):
            engine._admit("K-77", selected, "PT-2", "ticket", "K-77-PT-2-1")
        wait = fold(engine.journal.read("K-77")).waiting[("PT-2", "ticket")]
        self.assertEqual((wait["cause"], wait["until"]), ("slots", ""))

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

    def test_provider_slots_are_set_through_flow_settings(self):
        # 03.11: użytkownik chciał więcej kroków naraz bez ręcznej edycji ~/.config.
        engine = self.flow.engine
        self.assertEqual(engine.get_flow_settings()["providerSlots"], {})
        reply = engine.set_flow_settings({"providerSlots": {"anthropic": 6, "openai": 4}})
        self.assertEqual(reply["providerSlots"], {"anthropic": 6, "openai": 4})
        with open(self.settings, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["providerSlots"], {"anthropic": 6, "openai": 4})
        for bad in ({"anthropic": 0}, {"anthropic": 13}, {"anthropic": True}, {"anthropic": "6"}, ["anthropic"]):
            with self.assertRaises(FlowError, msg=repr(bad)):
                engine.set_flow_settings({"providerSlots": bad})

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
        # PT-130: zamierzona zmiana reguły kolumn — limit to postój w „W toku”, nie „Czeka na mnie”.
        self.assertEqual(card["column"], "progress")
        self.assertIn("czeka na limit do piątek 18:00", card["note"])
        self.assertEqual(card["flow"]["decisions"], [])
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

    def test_mixed_transfer_reassigns_reviewer_to_other_provider(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: czekaj-pierwszy")
        reply = self.start(card, preferredProvider="mixed", reviewer="Codex Astra")
        self.assertTrue(reply["ok"], reply)
        first = self.of(card, "step.started")[0]
        self.assertTrue(wait_until(lambda: os.path.isfile(os.path.join(first["worktree"], "ATRAPA.md")), timeout=15))
        changed = self.resolve(card, {"choice": "transfer", "step": first["step"],
                                      "executor": "Codex mocny"})
        self.assertTrue(changed["ok"], changed)
        event = self.of(card, "reviewer.reassigned")[0]
        self.assertEqual((event["ticket"], event["reviewerProvider"], event["reason"]),
                         (first["ticket"], "anthropic", "transfer-provider"))
        self.wait_column(card, "review", timeout=50)
        reviewers = [step for step in self.of(card, "step.started") if step["kind"] == "review"]
        self.assertEqual(reviewers[0]["provider"], "anthropic")

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

    def test_limit_without_any_choice_resumes_by_itself_after_the_reset(self):
        # StartCard może oddać odpowiedź dopiero po wykryciu szybkiego sygnału atrapy.
        # Włączamy odpytywanie po zmianie metryki, aby step.limit miało właściwe okno i reset.
        self.flow.stop()
        self.assertTrue(wait_until(lambda: not self.flow._ticking, timeout=5))
        now = self.flow.engine.clock()
        metric = {"remaining": 25, "readTs": now, "resetTs": now + 1000}
        self.flow.engine.limits_source = lambda: {"available": True,
                                                  "services": {"claude": {"session": metric}}}
        card = self.card(description="Dodaj eksport.\n\nATRAPA: limit")
        self.assertTrue(self.start(card)["ok"])
        metric["remaining"] = 0
        self.flow.start()
        self.assertTrue(wait_until(lambda: any(e.get("reason") == "limit" for e in
                                        self.of(card, "step.finished")), timeout=30), self.events(card))
        first = self.of(card, "step.started")[0]
        self.assertEqual(self.of(card, "step.decision"), [], "limit nie tworzy decyzji")
        metric.update(remaining=40, readTs=now + 1001)
        self.flow.engine.clock = lambda: now + 1001
        self.assertTrue(wait_until(lambda: len(self.of(card, "step.started")) >= 2, timeout=30), self.events(card))
        second = self.of(card, "step.started")[1]
        self.assertEqual((second["provider"], second["ticket"], second["worktree"], second["parentStep"]),
                         ("anthropic", first["ticket"], first["worktree"], first["step"]))
        self.wait_column(card, "review", timeout=50)

    def test_failed_wip_never_starts_other_provider_and_can_be_repaired(self):
        from tide_daemon.tasks.stepstop import Snapshot
        card = self.card(description="Dodaj eksport.\n\nATRAPA: czekaj-pierwszy")
        self.assertTrue(self.start(card)["ok"])
        first = self.of(card, "step.started")[0]
        self.assertTrue(wait_until(lambda: os.path.isfile(os.path.join(first["worktree"], "ATRAPA.md")), timeout=15))
        with mock.patch("tide_daemon.tasks.stepstop.git_snapshot", return_value=Snapshot(error="no-sandbox")):
            queued = self.resolve(card, {"choice": "transfer", "step": first["step"], "executor": "Codex mocny"})
        self.assertTrue(queued["ok"] and queued["queued"], queued)
        self.assertEqual(len(self.of(card, "step.started")), 1)
        self.assertEqual(self.of(card, "step.finished")[0]["wip"]["error"], "no-sandbox")
        self.assertEqual(self.results(card, "merge"), [])
        self.assertIn("zapis zmian wymaga naprawy", self.shown(card)["derived"]["note"])
        now = self.flow.engine.clock()
        self.flow.engine.clock = lambda: now + 61
        self.assertTrue(wait_until(lambda: len(self.of(card, "step.started")) >= 2, timeout=30), self.events(card))
        self.assertTrue(self.of(card, "step.wip")[-1]["wip"]["committed"])
        self.wait_column(card, "review", timeout=50)


class InteractiveTransferTest(FlowInProcess):
    resolve = TransferFlowTest.resolve

    def full(self):
        terminal = _Terminal()
        self.flow.engine.terminal = terminal
        card = self.card(description="Dodaj eksport pliku z podglądem wyniku.")
        reply = self.start(card, entry="planning", mode="full", executor="Sonnet",
                           preferredProvider="anthropic")
        self.assertTrue(reply["ok"] and reply["interactive"], reply)
        step_id = self.of(card, "step.started")[0]["step"]
        step = fold(self.events(card)).steps[step_id]
        return card, step, terminal

    def entered(self, card, step_id):
        terminal = _Terminal()
        terminal.launchers = SimpleNamespace(agents={"claude": "/bin/claude", "codex": "/bin/codex"},
                                             codex_for=lambda _: "/bin/codex")
        self.flow.engine.terminal = terminal
        answers = []
        with mock.patch.object(self.flow.engine, "_session", return_value=("00000000-0000-4000-8000-000000000069", "")):
            self.board.enter_step(card, step_id, answers.append)
            self.assertTrue(wait_until(lambda: answers, timeout=30), self.events(card))
        reply = json.loads(answers[0])
        self.assertTrue(reply["ok"], reply)
        return fold(self.events(card)).steps[reply["step"]], terminal

    def test_entered_ticket_transfer_keeps_ticket_kind_and_worktree(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: czekaj-pierwszy")
        self.assertTrue(self.start(card)["ok"])
        original = self.of(card, "step.started")[0]
        self.assertTrue(wait_until(lambda: os.path.isfile(os.path.join(original["worktree"], "ATRAPA.md")), timeout=15))
        entered, terminal = self.entered(card, original["step"])
        self.assertEqual(entered["mode"], "interactive")
        answer = self.resolve(card, {"choice": "transfer", "step": entered["step"],
                                     "executor": "Codex mocny"})
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(wait_until(lambda: len(terminal.opened) == 2, timeout=30), self.events(card))
        resumed = fold(self.events(card)).steps[self.of(card, "step.started")[-1]["step"]]
        self.assertEqual((resumed["kind"], resumed["ticket"], resumed["worktree"], resumed["parentStep"]),
                         ("ticket", original["ticket"], original["worktree"], original["step"]))

    def test_entered_review_transfer_preserves_review_verdict(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: czekaj-review")
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: any(s["kind"] == "review" for s in self.of(card, "step.started")),
                                   timeout=30), self.events(card))
        original = next(s for s in self.of(card, "step.started") if s["kind"] == "review")
        self.assertTrue(wait_until(lambda: os.path.isfile(os.path.join(original["stepDir"], "out", "fake-call.json")),
                                   timeout=15))
        entered, terminal = self.entered(card, original["step"])
        answer = self.resolve(card, {"choice": "transfer", "step": entered["step"],
                                     "executor": "Codex mocny"})
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(wait_until(lambda: len(terminal.opened) == 2, timeout=30), self.events(card))
        resumed = fold(self.events(card)).steps[self.of(card, "step.started")[-1]["step"]]
        self.assertEqual((resumed["kind"], resumed["ticket"], resumed["worktree"], resumed["reviewTip"]),
                         ("review", original["ticket"], original["worktree"], original["reviewTip"]))
        out = os.path.join(resumed["stepDir"], "out")
        with open(os.path.join(out, "review.md"), "w", encoding="utf-8") as fh:
            fh.write("# Review\nAPPROVE\n")
        with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"result": "done", "verdict": "APPROVE", "blockers": 0, "majors": 0,
                       "nits": 0, "notes": ["review.md"]}, fh)
        self.assertTrue(wait_until(lambda: any(v["step"] == resumed["step"] for v in
                                        self.of(card, "review.verdict")), timeout=20), self.events(card))

    def test_pending_console_transfer_preserves_wip_and_continues_in_new_console(self):
        card, first, terminal = self.full()
        self.assertTrue(self.shown(card)["derived"]["flow"]["steps"][0]["canTransfer"])
        with open(os.path.join(first["worktree"], "PLANNING-WIP.md"), "w", encoding="utf-8") as fh:
            fh.write("Niedokończony plan\n")
        reply = self.resolve(card, {"choice": "transfer", "step": first["step"], "executor": "Codex mocny"})
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(wait_until(lambda: len(terminal.opened) == 2, timeout=30), self.events(card))
        second = fold(self.events(card)).steps[self.of(card, "step.started")[1]["step"]]
        self.assertEqual((second["parentStep"], second["worktree"], second["provider"]),
                         (first["step"], first["worktree"], "openai"))
        self.assertTrue(os.path.exists(first["taskFile"] + ".cancelled"))
        done = self.of(card, "step.finished")[0]
        self.assertTrue(done["wip"]["committed"])
        with open(os.path.join(second["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            self.assertIn("Notka techniczna przekazania", fh.read())
        self.assertEqual(second["mode"], "interactive")
        FullPlanningTest._write_proposal(self, card, second)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.proposed"), timeout=20), self.events(card))

    def test_claimed_console_must_confirm_stop_before_new_executor(self):
        card, first, terminal = self.full()
        self.flow._timer.stop()
        os.rename(first["taskFile"], first["taskFile"] + ".claimed")
        live = {"alive": True}
        def signal(*_args):
            live["alive"] = False
            return True
        with mock.patch("tide_daemon.cardflow.engine.launch.process_alive", side_effect=lambda _: live["alive"]), \
             mock.patch("tide_daemon.cardflow.engine.launch.signal_flow_launcher", side_effect=signal), \
             mock.patch("tide_daemon.cardflow.engine.stepstop.unit_state", return_value="inactive"), \
             mock.patch("tide_daemon.cardflow.engine.stepstop.terminate", side_effect=[False, True, True, True]):
            refused = self.resolve(card, {"choice": "transfer", "step": first["step"],
                                          "executor": "Codex mocny"})
            self.assertEqual((refused["ok"], refused["error"]), (False, "stop-failed"), refused)
            self.assertEqual(len(terminal.opened), 1)
            self.assertEqual(self.of(card, "step.finished"), [])
            accepted = self.resolve(card, {"choice": "transfer", "step": first["step"],
                                           "executor": "Codex mocny"})
        self.assertTrue(accepted["ok"], accepted)
        self.flow._timer.start()
        self.assertTrue(wait_until(lambda: len(terminal.opened) == 2, timeout=30), self.events(card))

    def test_restart_after_interactive_transfer_decision_opens_one_console(self):
        card, first, terminal = self.full()
        self.flow._timer.stop()
        reply = self.resolve(card, {"choice": "transfer", "step": first["step"], "executor": "Codex mocny"})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(len(terminal.opened), 1)
        self.restart()
        self.flow.engine.terminal = terminal
        self.assertTrue(wait_until(lambda: len(terminal.opened) == 2, timeout=30), self.events(card))
        self.assertEqual(len(self.of(card, "step.started")), 2)

    def test_restart_after_first_transfer_event_finishes_stop_and_wip(self):
        card, first, terminal = self.full()
        self.flow._timer.stop()
        self.flow.engine.journal.append(card, "step.transfer_requested", step=first["step"], ticket="",
                                        executor="Codex mocny")
        self.assertEqual(self.of(card, "step.finished"), [])
        self.assertTrue(fold(self.events(card)).active())
        self.restart()
        self.flow.engine.terminal = terminal
        self.assertTrue(wait_until(lambda: len(terminal.opened) == 2, timeout=30), self.events(card))
        self.assertEqual(self.of(card, "step.finished")[0]["reason"], "transfer")
        self.assertTrue(self.of(card, "step.finished")[0]["wip"]["stopped"])
        self.assertEqual(len(self.of(card, "step.decision")), 1)
        self.assertEqual(len(self.of(card, "step.started")), 2)

    def test_restart_after_console_closed_before_wip_repairs_transfer(self):
        card, first, terminal = self.full()
        self.flow._timer.stop()
        self.flow.engine.journal.append(card, "step.transfer_requested", step=first["step"], ticket="",
                                        executor="Codex mocny")
        self.assertTrue(self.flow.engine.task_files.cancel(first["taskFile"]))
        self.flow.engine.journal.append(card, "step.console_closed", step=first["step"])
        self.assertTrue(fold(self.events(card)).active())
        self.restart()
        self.flow.engine.terminal = terminal
        self.assertTrue(wait_until(lambda: len(terminal.opened) == 2, timeout=30), self.events(card))
        self.assertEqual(self.of(card, "step.finished")[0]["reason"], "transfer")
        self.assertTrue(self.of(card, "step.finished")[0]["wip"]["stopped"])

    def test_restart_opens_persisted_console_task_once(self):
        terminal = _Terminal()
        terminal.open = mock.Mock(side_effect=KeyboardInterrupt("crash before terminal.open"))
        self.flow.engine.terminal = terminal
        card = self.card(description="Plan po awarii.")
        self.flow._timer.stop()
        with self.assertRaises(KeyboardInterrupt):
            self.flow.engine.start(card, {"entry": "planning", "mode": "full",
                                          "executor": "Sonnet", "preferredProvider": "anthropic"})
        step_id = self.of(card, "step.started")[0]["step"]
        pending = fold(self.events(card)).steps[step_id]
        self.assertFalse(pending["consoleOpened"])
        self.assertTrue(os.path.isfile(pending["taskFile"]))
        self.restart()
        recovered = _Terminal()
        self.flow.engine.terminal = recovered
        self.assertTrue(wait_until(lambda: len(recovered.opened) == 1, timeout=20), self.events(card))
        self.assertEqual(len(self.of(card, "step.console_opened")), 1)
        self.assertTrue(self.of(card, "step.console_opened")[0]["recovered"])
        wait_until(lambda: False, timeout=0.3)
        self.assertEqual(len(recovered.opened), 1)

    def test_late_second_console_tab_closes_after_claim(self):
        terminal = _Terminal()
        self.flow.engine.terminal = terminal
        card = self.card(description="Plan po awarii otwierania karty.")
        self.flow._timer.stop()
        journal = self.flow.engine.journal
        append = journal.append
        def crash_after_open(card_id, event_type, **data):
            if event_type == "step.console_opened":
                raise KeyboardInterrupt("crash after terminal.open")
            return append(card_id, event_type, **data)
        with mock.patch.object(journal, "append", side_effect=crash_after_open):
            with self.assertRaises(KeyboardInterrupt):
                self.flow.engine.start(card, {"entry": "planning", "mode": "full",
                                              "executor": "Sonnet", "preferredProvider": "anthropic"})
        self.assertEqual(len(terminal.opened), 1)
        first = fold(self.events(card)).steps[self.of(card, "step.started")[0]["step"]]
        self.assertFalse(first["consoleOpened"])
        self.restart()
        recovered = _Terminal()
        self.flow.engine.terminal = recovered
        self.assertTrue(wait_until(lambda: len(recovered.opened) == 1, timeout=20), self.events(card))
        task = first["taskFile"]
        os.rename(task, task + ".claimed")
        claim_dir = os.path.join(first["stepDir"], "in", "console-claim")
        self.assertTrue(os.path.isfile(os.path.join(claim_dir, launch.FLOW_TRANSFER_CLOSE)))
        with mock.patch("tide_daemon.tasks.launch.os.getcwd", return_value=claim_dir), \
             mock.patch("tide_daemon.tasks.launch.shell", side_effect=AssertionError("spóźniona powłoka")):
            self.assertEqual(launch.main(["--task", task]), 0)
            with mock.patch("tide_daemon.tasks.launch.claim_for_cwd", return_value=None):
                self.assertEqual(launch.main([]), 0)
        self.assertEqual(len(self.of(card, "step.console_opened")), 1)

    def test_restart_after_wip_commit_recovers_sha_diff_without_second_commit(self):
        card, first, terminal = self.full()
        self.flow._timer.stop()
        with open(os.path.join(first["worktree"], "PLANNING-WIP.md"), "w", encoding="utf-8") as fh:
            fh.write("Niedokończony plan\n")
        with mock.patch.object(self.flow.engine, "_handoff_note", side_effect=RuntimeError("crash after WIP")):
            with self.assertRaises(RuntimeError):
                self.flow.engine._resolve_transfer(card, fold(self.events(card)),
                                                   {"choice": "transfer", "step": first["step"],
                                                    "executor": "Codex mocny"})
        tip = self.flow.engine.git.sha(first["repo"], f"refs/heads/{first['branch']}")
        self.assertEqual(self.flow.engine.git.run(first["repo"], "show", "-s", "--format=%s", tip).out.strip(),
                         "WIP: step stopped before it finished")
        self.assertEqual(self.of(card, "step.finished"), [])
        self.restart()
        self.flow.engine.terminal = terminal
        self.assertTrue(wait_until(lambda: len(terminal.opened) == 2, timeout=30), self.events(card))
        finished = self.of(card, "step.finished")[0]
        self.assertEqual(finished["wip"]["commit"], tip)
        self.assertEqual(self.flow.engine.git.sha(first["repo"], f"refs/heads/{first['branch']}"), tip)
        with open(finished["wip"]["handoff"], encoding="utf-8") as fh:
            note = fh.read()
        self.assertIn(tip, note)
        self.assertIn("+Niedokończony plan", note)

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
