"""PT-248: dopisanie przez użytkownika, review i osobne zatwierdzenie różnicy."""
import copy
import json
from pathlib import Path
from unittest import mock

from tide_daemon.cardflow import planappend
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.cardflow.state import fold
from . import test_cardflow_plan_integration as fixture
from .privatebus import wait_until
from .test_cardflow import FlowInProcess


class AppendPlanTest(FlowInProcess):
    proposal = fixture.PlanFlowTest.proposal
    submit = fixture.PlanFlowTest.submit
    approve = fixture.PlanFlowTest.approve

    def prepared(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.flow.stop()
        self.assertTrue(self.flow.engine.approve_plan(card)["ok"])
        return card

    def state(self, card):
        return fold(self.flow.engine.journal.read(card))

    def request(self, card, **changes):
        return {"action": "append", "title": "Nowy eksport", "body": "ATRAPA: gotowe\n- [ ] Plik ma 3 kolumny.\n"
                "## Dla recenzenta\nSprawdź liczbę kolumn.", "class": "mocny", "executor": "Codex mocny",
                "reviewer": "Codex standard", "blockers": [], "requestKey": "append-test",
                "planFingerprint": planappend.fingerprint(self.state(card)), **changes}

    def test_append_preserves_approved_sketches_and_manual_plan_text(self):
        card = self.card()
        path = Path(self.proposal(card))
        draft = path.parent / "followups/nested/scope.md"
        draft.parent.mkdir(parents=True)
        draft.write_text("# Zakres\n[plan](../../2026-10-29-plan.md)\n[kartka](../../tickets/1.md)\n")
        path.write_text(path.read_text() + "\n[szkic](followups/nested/scope.md)\n")
        body_path = path.parent / "tickets/1.md"
        body_path.write_text(body_path.read_text() + "\n[szkic](../followups/nested/scope.md)\n")
        self.assertTrue(self.submit(card, str(path))["ok"])
        self.flow.stop()
        engine = self.flow.engine
        self.assertTrue(engine.approve_plan(card)["ok"])
        before = self.state(card)
        # Pliki docelowe bez uruchamiania piaskownicy gita: sprawdzamy treść kolejnego commita.
        worktree = Path(self.repo) / "committed-plan"
        for name, content in engine._plan_files(card, before, set()).items():
            target = worktree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        plan_file = before.plan_approved["planFile"]
        committed = worktree / plan_file
        committed.write_text(committed.read_text() + "\nRęczny dopisek użytkownika.\n")
        reply = engine.edit_plan(card, self.request(card))
        self.assertTrue(reply["ok"], reply)
        pending = self.state(card)
        with mock.patch.object(engine, "_launch") as launch:
            engine._plan_step(card, pending, engine.catalog().get("Codex standard"), "plan-review", "Review")
        prompt = launch.call_args.kwargs["prompt_text"]
        self.assertIn("Zamrożony komplet", prompt)
        engine._record_plan_review(card, verdict="APPROVE", reviewer="Codex standard", findings=[], corrected=False)
        self.assertTrue(engine.approve_plan(card)["ok"])
        files = engine._plan_files(card, self.state(card), set(), worktree=str(worktree))
        self.assertIn("Ręczny dopisek użytkownika.", files[plan_file].decode())
        scope_file = str(Path(plan_file).parent / "followups/nested/scope.md")
        self.assertEqual(files[scope_file], engine._plan_files(card, before, set())[scope_file])
        self.assertEqual(len(self.state(card).plan_approved["rows"]), 3)

    def test_append_reviews_without_planner_and_approval_alone_allocates_one_id(self):
        card = self.prepared()
        before = copy.deepcopy(self.state(card).plan_approved)
        engine = self.flow.engine
        # Rozpoczęty ticket z gotowym blokerem: jego przydział i stan zostają nietknięte.
        engine.journal.append(card, "step.started", step="existing", ticket="PT-2", kind="ticket")
        engine.journal.append(card, "step.finished", step="existing", result="question", question="Który format?")
        engine.journal.append(card, "ticket.assigned", ticket="PT-2", executor="Sonnet", reviewer="Opus")
        engine.journal.append(card, "result", action="merge", key="PT-1", ok=True)
        req = self.request(card, blockers=["PT-1"])
        reply = engine.edit_plan(card, req)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["workId"], "#3")
        self.assertTrue(engine.edit_plan(card, req)["already"])
        self.assertEqual(len(self.results(card, "reserve")), 2)
        self.assertEqual(self.results(card, "ticket-branch"), [])
        self.assertEqual(self.state(card).plan_approved, before)
        with self.assertRaises(FlowError) as error:
            engine.approve_plan(card)
        self.assertEqual(error.exception.code, "review-pending")
        self.restart()
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=20), self.events(card))
        st = self.state(card)
        self.assertEqual(st.plan_revision["kind"], "append")
        from tide_daemon.cardflow.decisions import build_decisions
        decision = next(d for d in build_decisions(st) if d["kind"] == "plan-revision")
        self.assertEqual([o["id"] for o in decision["options"]], ["approve", "discard-revision"])
        self.assertFalse(decision["freeText"])
        self.assertEqual(st.plan_revision["difference"], {"added": ["#3"], "changed": [], "removed": []})
        self.assertEqual([s["executor"] for s in self.of(card, "step.started") if s["seq"] > st.plan_proposed["seq"]],
                         ["Codex standard"])
        self.assertEqual(len(self.results(card, "reserve")), 2)
        self.assertFalse(any(s.get("ticket") == "PT-3" for s in self.of(card, "step.started")))
        self.assertTrue(self.approve(card)["ok"])
        self.assertTrue(wait_until(lambda: any(s.get("ticket") == "PT-3" for s in self.of(card, "step.started")), timeout=20), self.events(card))
        after = self.state(card)
        self.assertEqual(after.plan_approved["rows"][:2], before["rows"])
        self.assertEqual(after.plan_approved["rows"][-1]["blockers"], ["PT-1"])
        self.assertEqual(after.assignments["PT-2"]["executor"], "Sonnet")
        self.assertEqual(after.finished["existing"]["result"], "question")
        self.flow.stop()
        self.assertTrue(self.flow.engine.edit_plan(card, req)["already"])
        self.assertTrue(self.flow.engine.approve_plan(card)["already"])
        self.assertEqual(len(self.results(card, "reserve")), 3)
        self.assertEqual(len([r for r in self.results(card, "ticket-branch") if r["key"] == "PT-3"]), 1)
        self.assertEqual(fold(self.events(card)).plan_approved, after.plan_approved)

    def test_shared_format_validation_rejects_bad_fields_unknown_blocker_and_cycle_without_events(self):
        card = self.prepared()
        engine = self.flow.engine
        count = len(self.events(card))
        for changes in ({"body": "Brak kryteriów"}, {"executor": "Nieznany"}, {"reviewer": "Codex mocny"},
                        {"blockers": ["PT-999"]}, {"blockers": ["#3"]}, {"class": "nieznana"}):
            with self.subTest(changes=changes):
                reply = engine.edit_plan(card, self.request(card, **changes))
                self.assertEqual(reply["error"], "invalid-plan", reply)
                self.assertEqual(len(self.events(card)), count)
        for changes, code in (({"title": ""}, "bad-request"), ({"project": "other"}, "foreign-project"),
                              ({"planFingerprint": "old"}, "stale-plan")):
            with self.subTest(changes=changes), self.assertRaises(FlowError) as error:
                engine.edit_plan(card, self.request(card, **changes))
            self.assertEqual(error.exception.code, code)
        self.assertEqual(len(self.events(card)), count)

    def test_guards_and_request_key_conflict(self):
        card = self.prepared()
        engine = self.flow.engine
        req = self.request(card)
        engine.edit_plan(card, req)
        with self.assertRaises(FlowError) as error:
            engine.edit_plan(card, {**req, "title": "Inny tytuł"})
        self.assertEqual(error.exception.code, "request-conflict")
        with self.assertRaises(FlowError) as error:
            engine.edit_plan(card, {**req, "requestKey": "another"})
        self.assertEqual(error.exception.code, "revision-active")
        for patch, code in (({"plan_approved": None}, "no-plan"), ({"card_finished": True}, "no-plan"),
                            ({"plan_revision": {"pending": True}}, "revision-active"),
                            ({"base_request": {"decision": "base"}, "plan_revision": None}, "base-decision-pending")):
            st = self.state(card)
            for key, value in patch.items():
                setattr(st, key, value)
            with self.subTest(patch=patch), self.assertRaises(FlowError) as error:
                planappend.append(engine, card, st, {**req, "requestKey": "new"})
            self.assertEqual(error.exception.code, code)

    def test_rejection_restores_old_plan_and_request_changes_never_launches_planner(self):
        card = self.prepared()
        engine = self.flow.engine
        original = self.state(card)
        req = self.request(card, body="ATRAPA: plan-review-changes\n- [ ] Działa.\n")
        engine.edit_plan(card, req)
        self.restart()
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=20))
        self.flow.stop()
        engine = self.flow.engine
        self.assertEqual(self.state(card).plan_reviewed["verdict"], "REQUEST_CHANGES")
        self.assertFalse(any(s["kind"] in ("plan-amend", "plan-revise", "planning") for s in self.of(card, "step.started")))
        self.assertTrue(engine.edit_plan(card, {"action": "discard-revision"})["ok"])
        restored = self.state(card)
        self.assertEqual(restored.plan_approved, original.plan_approved)
        self.assertEqual(restored.plan_proposed, original.plan_proposed)
        self.assertEqual(restored.plan_reviewed, original.plan_reviewed)
        self.assertIsNone(restored.plan_revision)
        self.assertTrue(engine.edit_plan(card, req)["already"])
        self.assertEqual(len(self.results(card, "reserve")), 2)

    def test_reviewer_availability_and_planner_limit(self):
        card = self.prepared()
        engine = self.flow.engine
        engine.journal.append(card, "step.started", step="old-planner", kind="planning", executor="Sonnet")
        engine.journal.append(card, "step.finished", step="old-planner", result="limit")
        req = self.request(card)
        before = len(self.events(card))
        with mock.patch.object(engine, "which", return_value=""):
            with self.assertRaises(FlowError) as error:
                engine.edit_plan(card, req)
            self.assertEqual(error.exception.code, "no-program")
        self.assertEqual(len(self.events(card)), before)
        self.assertTrue(engine.edit_plan(card, req)["ok"])
        self.restart()
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=20), self.events(card))
        self.assertEqual([s["kind"] for s in self.of(card, "step.started")].count("planning"), 1)

    def test_retry_after_reservation_and_stale_plan_never_duplicates_id(self):
        card = self.prepared()
        engine = self.flow.engine
        stale = self.request(card)
        engine.edit_plan(card, {"action": "edit", "ticket": "PT-2", "body": "- [ ] Nowe kryterium."})
        with self.assertRaises(FlowError) as error:
            engine.edit_plan(card, stale)
        self.assertEqual(error.exception.code, "stale-plan")
        req = self.request(card)
        engine.edit_plan(card, req)
        engine._record_plan_review(card, verdict="APPROVE", reviewer="Codex standard", findings=[], corrected=False)
        append = engine.journal.append

        def crash(ident, kind, **data):
            if kind == "plan.revision-approved":
                raise OSError("przerwanie po rezerwacji")
            return append(ident, kind, **data)

        with mock.patch.object(engine.journal, "append", side_effect=crash):
            with self.assertRaises(OSError):
                engine.approve_plan(card)
        self.assertEqual(len(self.results(card, "reserve")), 3)
        self.assertTrue(engine.approve_plan(card)["ok"])
        self.assertTrue(engine.edit_plan(card, req)["already"])
        self.assertEqual(len(self.results(card, "reserve")), 3)
        self.assertEqual(len(self.state(card).plan_approved["rows"]), 3)
