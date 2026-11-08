"""PT-80/63: bramka review i zdarzenia zmian planu."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock
from . import flowfakes
from . import test_cardflow_plan_integration as fixtures
from .test_cardflow import FlowInProcess
from .privatebus import wait_until


class PlanAmendFixtureTest(unittest.TestCase):
    def test_amending_plan_preserves_started_ticket_body_for_any_tracker_prefix(self):
        body = "# Rozpoczęty ticket\n- [ ] Wynik.\n"
        for ticket in ("PT-1", "LOG-1"):
            with self.subTest(ticket=ticket), tempfile.TemporaryDirectory(prefix="tide-amend-fixture-") as folder:
                out = os.path.join(folder, "out")
                row = dict(workId=ticket, title="Ticket", body=body, blockers=[], executor="Sonnet",
                           reviewer="Opus", note="", **{"class": "standard"})
                prompt = ("Popraw propozycję planu karty K-1\n"
                          f"Plik wyniku: {out}/result.json\n"
                          "Poprzednia propozycja: " + json.dumps({"rows": [row]}))
                subprocess.run([sys.executable, "-c", flowfakes.CLAUDE, "--session-id", "fixture"],
                               input=prompt, text=True, capture_output=True, check=True, cwd=folder,
                               env=dict(os.environ, HOME=folder))
                with open(os.path.join(out, "tickets", ticket + ".md"), encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), body)


class PlanReviewEditingTest(FlowInProcess):
    proposal = fixtures.PlanFlowTest.proposal
    submit = fixtures.PlanFlowTest.submit
    approve = fixtures.PlanFlowTest.approve

    @contextmanager
    def running_ticket(self, card):
        """Pierwszy ticket kończy pracę dopiero po asercjach edycji planu."""
        path = self.proposal(card)
        ticket_path = Path(path).parent / "tickets" / "1.md"
        ticket_path.write_text(ticket_path.read_text(encoding="utf-8").replace(
            "ATRAPA: gotowe", "ATRAPA: na-sygnal"), encoding="utf-8")
        try:
            self.assertTrue(self.submit(card, path)["ok"])
            approved = self.approve(card)
            self.assertTrue(approved["ok"], approved)
            self.assertTrue(wait_until(lambda: any(s["kind"] == "ticket" for s in self.of(card, "step.started")),
                                       timeout=30), self.events(card))
            working = next(s for s in self.of(card, "step.started") if s["kind"] == "ticket")
            yield working
        finally:
            # Także przy nieudanej asercji lub restarcie: sterowanie poza checkoutem.
            step = next((s for s in self.of(card, "step.started") if s["kind"] == "ticket"), None)
            if step is not None:
                Path(step["stepDir"], "out", "release-agent").touch()

    def test_review_precedes_approval_and_survives_restart(self):
        card = self.card()
        path = self.proposal(card)
        with open(os.path.join(os.path.dirname(path), "spec.md"), "w", encoding="utf-8") as fh:
            fh.write("Pełna specyfikacja: eksport uwzględnia polskie znaki.")
        self.assertTrue(self.submit(card, path)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
        review = next(s for s in self.of(card, "step.started") if s["kind"] == "plan-review")
        self.assertNotEqual(review["executor"], "Sonnet")
        with open(os.path.join(review["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            prompt = json.load(fh)["prompt"]
        for expected in ("eksport uwzględnia polskie znaki", "mierzalność", "pionowość", "Dla recenzenta", "jednym świeżym kontekście"):
            self.assertIn(expected, prompt)
        shown = self.wait_column(card, "waiting")["derived"]["flow"]["plan"]
        self.assertEqual(shown["review"]["verdict"], "APPROVE")
        self.restart()
        wait_until(lambda: False, timeout=.3)
        self.assertEqual(len(self.of(card, "plan.reviewed")), 1)

    def test_edit_is_journalled_and_replayed(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        self.flow.stop()
        result = self.flow.engine.edit_plan(card, {"action": "edit", "ticket": "#2", "class": "mocny",
                                                  "executor": "Opus", "reviewer": "Sonnet",
                                                  "body": "# Poprawiona kartka\n- [ ] Nowe kryterium.\n"})
        self.assertTrue(result["ok"])
        self.flow.engine.edit_plan(card, {"action": "edit", "ticket": "#2", "executor": "Opus"})
        self.restart()
        rows = self.shown(card)["derived"]["flow"]["plan"]["rows"]
        self.assertEqual(rows[1]["executor"], "Opus")
        self.assertIn("Nowe kryterium", rows[1]["body"])
        self.assertEqual(len(self.of(card, "plan.ticket-edited")), 1)

    def test_same_model_with_different_effort_is_an_independent_reviewer(self):
        # 05.11 (decyzja użytkownika): Codex mocny recenzuje Codexa standard; ten sam model i siła — odmowa.
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        ok = self.flow.engine.edit_plan(card, {"action": "edit", "ticket": "#2", "executor": "Codex standard",
                                               "reviewer": "Codex mocny"})
        self.assertTrue(ok["ok"], ok)
        from tide_daemon.cardflow.engine import FlowError
        with self.assertRaises(FlowError):
            self.flow.engine.edit_plan(card, {"action": "edit", "ticket": "#2", "executor": "Codex mocny",
                                              "reviewer": "Codex mocny"})

    def test_started_ticket_cannot_be_edited(self):
        from tide_daemon.cardflow.engine import FlowError
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        self.flow.stop()
        self.flow.engine.approve_plan(card)
        self.flow.engine.journal.append(card, "step.started", step="working", ticket="PT-1", kind="ticket")
        with self.assertRaises(FlowError) as error:
            self.flow.engine.edit_plan(card, {"action": "edit", "ticket": "PT-1", "executor": "Opus"})
        self.assertEqual(error.exception.code, "ticket-started")

    def test_one_correction_after_review_then_plan_is_visible(self):
        card = self.card(description="ATRAPA: plan-review-changes")
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
        self.assertEqual([s["kind"] for s in self.of(card, "step.started")], ["plan-review", "plan-revise"])
        self.assertTrue(self.of(card, "plan.reviewed")[0]["corrected"])
        review, revise = self.of(card, "step.started")
        # K-35: domyślny recenzent planu nie jest wykonawcą klasy wyłącznie „lekki”, a werdykt podpisuje
        # krok, który go wydał.
        self.assertNotEqual(review["executor"], "Haiku")
        self.assertEqual(self.of(card, "plan.reviewed")[0]["reviewer"], review["executor"])
        # K-35: krok poprawki nie widzi Tablicy — dostaje oryginalny plan.md i dokładny wzór nagłówka i tabeli.
        with open(os.path.join(revise["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            prompt = json.load(fh)["prompt"]
        with open(self.of(card, "plan.proposed")[0]["planPath"], encoding="utf-8") as fh:
            original = fh.read()
        self.assertIn(original.strip(), prompt)
        self.assertIn("`tide-plan: 1`", prompt)
        self.assertIn("dokładnie jedna tabela", prompt)
        self.assertNotIn("standardu 1.2 i tickets", prompt)
        self.restart()
        wait_until(lambda: False, timeout=.3)
        self.assertEqual(len(self.of(card, "plan.reviewed")), 1)

    def test_returned_plan_has_service_difference(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "Doprecyzuj ryzyka."}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.revision-ready"), timeout=20), self.events(card))
        difference = self.of(card, "plan.revision-ready")[-1]["difference"]
        self.assertEqual(difference["added"], [])
        self.assertEqual(difference["changed"][0], {"ticket": "#1", "fields": ["body"]})

    def test_pending_review_cannot_be_approved(self):
        from tide_daemon.cardflow.engine import FlowError
        self.flow.stop()
        card = self.card()
        self.flow.engine.propose_plan(card, self.proposal(card))
        with self.assertRaises(FlowError) as error:
            self.flow.engine.approve_plan(card)
        self.assertEqual(error.exception.code, "review-pending")
        self.assertEqual(self.results(card, "reserve"), [])

    def test_editing_unstarted_approved_ticket_does_not_stop_work(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.flow.stop()
        self.assertTrue(wait_until(lambda: not self.flow._ticking, timeout=5),
                        "koniec tyknięcia przed bezpośrednią edycją zatwierdzonego planu")
        approved = self.flow.engine.approve_plan(card)
        # Monitor jest zatrzymany: także zgodę zapisujemy bezpośrednio w silniku.
        with mock.patch.object(self.flow.engine, "box_launcher", lambda *_args: None):
            environment = self.flow.engine.approve_environment(card, "tide-box: 1\n", False)
        self.assertTrue(environment["ok"], environment)
        ident = approved["map"]["#2"]
        self.flow.engine.edit_plan(card, {"action": "edit", "ticket": ident, "executor": "Opus", "reviewer": "Sonnet"})
        self.assertEqual(self.of(card, "plan.ticket-edited")[-1]["ticket"], ident)
        self.assertFalse(any(e.get("result") == "stopped" for e in self.of(card, "step.finished")))
        self.restart()
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=30), self.events(card))
        second = next(s for s in self.of(card, "step.started") if s.get("ticket") == ident and s["kind"] == "ticket")
        self.assertEqual(second["executor"], "Opus")

    def test_approving_difference_does_not_stop_running_ticket(self):
        card = self.card()
        with self.running_ticket(card) as working:
            box = []
            self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "Doprecyzuj drugi ticket."}), box.append)
            self.assertTrue(wait_until(lambda: box, timeout=10))
            self.assertTrue(json.loads(box[0])["ok"], box)
            self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=6), self.events(card))
            # 03.11, K-16: poprawka zatwierdzonego planu dostaje aktualną tabelę z numerami ticketów i listą tych, które
            # ruszyły — nie pierwotny plan.md z `#1…`.
            amend = next(s for s in self.of(card, "step.started") if s["kind"] == "plan-amend")
            with open(os.path.join(amend["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
                prompt = fh.read()
            self.assertIn("ZATWIERDZONY plan", prompt)
            self.assertIn(f"| {working['ticket']} |", prompt)
            self.assertIn(f"Tickety, które już ruszyły ({working['ticket']}", prompt)
            self.assertTrue(self.approve(card)["ok"])
            self.assertFalse(any(e["step"] == working["step"] for e in self.of(card, "step.finished")), self.events(card))
            self.assertEqual(len(self.of(card, "plan.revision-approved")), 1)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=25), self.events(card))

    def test_enter_opens_planner_in_console(self):
        from .test_cardflow_enter_unit import _Terminal
        order = []
        terminal = _Terminal(order)
        self.flow.engine.terminal = terminal
        card = self.card(description="ATRAPA: planowanie")
        self.assertTrue(self.start(card, entry="planning", mode="light")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "enter"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        entered = self.of(card, "step.entered")[-1]
        source = next(s for s in self.of(card, "step.started") if s["step"] == entered["step"])
        self.assertEqual(source["executor"], "Sonnet")
        self.assertEqual(order, ["open"])
        self.assertIn("--resume", terminal.plan.argv)

    def test_added_ticket_gets_number_only_when_difference_is_approved(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "Dodaj ticket"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=20), self.events(card))
        self.assertEqual(self.of(card, "plan.revision-ready")[-1]["difference"]["added"], ["#3"])
        self.assertEqual(self.results(card, "reserve"), [])
        approved = self.approve(card)
        self.assertTrue(approved["ok"], approved)
        self.assertEqual(len(approved["map"]), 3)

    def test_ready_revision_can_be_returned_again_from_the_decision_window(self):
        # 04.11, K-46: okno decyzji proponowało „Odeślij z uwagami” przy gotowej poprawce, a usługa odpowiadała
        # „poprawa planu już trwa”.
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "Dodaj ticket"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=20), self.events(card))
        first = self.of(card, "plan.revision-ready")[-1]
        again = self.flow.engine.edit_plan(card, {"action": "return", "feedback": "Doprecyzuj ryzyka."})
        self.assertTrue(again["ok"], again)
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.revision-ready")) >= 2 and
                                   len(self.of(card, "plan.reviewed")) == 3, timeout=20), self.events(card))
        second = self.of(card, "plan.revision-ready")[-1]
        self.assertEqual(second["baseline"], first["baseline"])   # różnica dalej od planu sprzed pierwszej poprawki
        self.assertEqual(second["difference"]["added"], ["#3"])   # zmiany z pierwszej poprawki zostają
        self.assertIn("Dodaj ticket", second["feedback"])
        self.assertIn("Doprecyzuj ryzyka.", second["feedback"])
        self.assertTrue(self.approve(card)["ok"])

    def test_plan_question_can_be_answered_while_ticket_is_running_after_restart(self):
        from tide_daemon.cardflow.state import fold, derived
        from tide_daemon.cardflow.engine import FlowEngine
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.flow.stop()
        approved = self.flow.engine.approve_plan(card)
        journal = self.flow.engine.journal
        journal.append(card, "step.started", step="working", ticket=approved["map"]["#1"], kind="ticket", worktree="/tmp/ticket")
        for kind in ("plan-amend", "plan-review", "plan-revise"):
            for result, choice, action in (("question", "answer", "question"), ("denied", "allow", "permission"), ("error", "retry", "error")):
                with self.subTest(kind=kind, result=result):
                    name = kind + result
                    journal.append(card, "step.started", step=name, ticket="", kind=kind, worktree="/tmp/plan", executor="Sonnet", attempt=1)
                    journal.append(card, "step.finished", step=name, result=result, question="Który wariant?", command="git status")
                    state = fold(journal.read(card))
                    shown = derived(state, {"column": "todo"})
                    self.assertEqual(shown["flow"]["decisions"][0]["kind"], action)
                    expected = {"plan-review": "review planu", "plan-amend": "poprawa planu", "plan-revise": "poprawa planu"}[kind]
                    self.assertEqual(shown["note"], expected + " czeka na decyzję (" + {"question": "pytanie", "denied": "brak zgody", "error": "błąd"}[result] + ")")
                    restarted = FlowEngine(self.store, journal=journal)
                    reply = restarted.resolve_step(card, {"choice": choice, "answer": "Pierwszy"})
                    self.assertEqual(reply["step"], name)
                    self.assertEqual(state.last_step, "working")
                    self.assertNotIn("working", fold(journal.read(card)).finished)


    def test_pending_difference_uses_new_waves_and_waits_after_old_plan_finishes(self):
        from tide_daemon.cardflow.state import fold, derived
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.flow.stop()
        self.flow.engine.approve_plan(card)
        j = self.flow.engine.journal
        proposal = fold(j.read(card)).plan_proposed
        proposal = {**proposal, "rows": [*proposal["rows"], {**proposal["rows"][-1], "workId": "#3", "blockers": ["#2"]}], "waves": [["#1"], ["#2"], ["#3"]]}
        j.append(card, "plan.proposed", **{k: v for k, v in proposal.items() if k not in ("seq", "ts", "card", "type", "v")}, extra="revision")
        j.append(card, "plan.revision-ready", pending=True, baseline=[], difference={}, feedback="Dodaj")
        j.append(card, "plan.reviewed", verdict="APPROVE")
        j.append(card, "plan.finished")
        shown = derived(fold(j.read(card)), {"column": "todo"})
        self.assertEqual(shown["flow"]["plan"]["waves"], [["#1"], ["#2"], ["#3"]])
        self.assertEqual(shown["flow"]["plan"]["rows"][-1]["blockers"], ["#2"])
        self.assertEqual(shown["column"], "waiting")

    def test_invalid_amendment_can_be_discarded_and_returned_again(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "ATRAPA: invalid-amend"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        # Błędny format wraca do planującego sam, z listą błędów, trzy razy; dopiero potem decyzja użytkownika (K-41).
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.revision-failed")) == 4, timeout=30), self.events(card))
        automatic = [e for e in self.of(card, "plan.returned") if e.get("automatic")]
        self.assertEqual(len(automatic), 3)
        self.assertIn("nie przeszła sprawdzenia formatu", automatic[0]["feedback"])
        self.assertIn("ATRAPA: invalid-amend", automatic[-1]["feedback"])
        self.flow.stop()
        self.assertTrue(self.flow.engine.edit_plan(card, {"action": "discard-revision"})["ok"])
        self.assertTrue(self.flow.engine.edit_plan(card, {"action": "return", "feedback": "Popraw ponownie"})["ok"])
        self.assertEqual(len([s for s in self.of(card, "step.started") if s["kind"] == "plan-amend"]), 5)

    def test_plan_amendment_runs_on_the_reassigned_planner(self):
        # 06.11, K-38: poprawka planu czekała na limit Claude do soboty, bo zawsze szła do pierwotnego planisty.
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        planner = self.of(card, "plan.proposed")[-1].get("planner")
        done = self.flow.engine.resolve_step(card, {"choice": "reassign-author", "ticket": "plan",
                                                    "executor": "Codex standard"})
        self.assertTrue(done["ok"], done)
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "Popraw tytuł #1"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(wait_until(lambda: any(s["kind"] == "plan-amend" for s in self.of(card, "step.started")),
                                   timeout=20), self.events(card))
        amend = next(s for s in self.of(card, "step.started") if s["kind"] == "plan-amend")
        self.assertEqual(amend["executor"], "Codex standard")
        self.assertNotEqual(planner, "Codex standard")

    def test_answer_after_restart_resumes_amendment_without_stopping_ticket(self):
        card = self.card()
        with self.running_ticket(card) as working:
            box = []
            self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "ATRAPA: amend-question"}), box.append)
            self.assertTrue(wait_until(lambda: box, timeout=10))
            self.assertTrue(wait_until(lambda: self.shown(card)["derived"]["flow"]["decisions"][:1] and self.shown(card)["derived"]["flow"]["decisions"][0]["kind"] == "question", timeout=5), self.events(card))
            self.restart()
            self.assertEqual(self.shown(card)["derived"]["flow"]["decisions"][0]["kind"], "question")
            box = []
            self.flow.resolve_step(card, json.dumps({"choice": "answer", "answer": "Pierwszy"}), box.append)
            self.assertTrue(wait_until(lambda: box, timeout=5))
            self.assertTrue(json.loads(box[0])["ok"], box)
            self.assertTrue(wait_until(lambda: self.of(card, "plan.revision-ready"), timeout=5), self.events(card))
            self.assertFalse(any(e["step"] == working["step"] for e in self.of(card, "step.finished")))

    def test_restart_after_checked_recovers_failed_revision_and_panel_decision(self):
        from unittest import mock
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        journal = self.flow.engine.journal
        append = journal.append

        def crash_between_events(ident, event_type, **data):
            if event_type == "plan.revision-failed":
                raise OSError("restart po plan.checked")
            return append(ident, event_type, **data)

        with mock.patch.object(journal, "append", side_effect=crash_between_events):
            box = []
            self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "ATRAPA: invalid-amend"}), box.append)
            self.assertTrue(wait_until(lambda: box, timeout=10))
            self.assertTrue(wait_until(lambda: any(not e["valid"] for e in self.of(card, "plan.checked")), timeout=10))
            self.flow.stop()
        self.assertEqual(journal.read(card)[-1]["type"], "plan.checked")
        self.assertEqual(self.of(card, "plan.revision-failed"), [])
        self.restart()
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.revision-failed")) == 4, timeout=30), self.events(card))
        shown = self.wait_column(card, "waiting")
        self.assertTrue(shown["derived"]["flow"]["plan"]["revision"]["failed"])
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "discard-revision"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=5))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertEqual(len(self.of(card, "plan.revision-failed")), 4)
        self.assertEqual(len([e for e in self.of(card, "plan.checked") if not e["valid"]]), 4)
