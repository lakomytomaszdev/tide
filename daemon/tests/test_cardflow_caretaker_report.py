"""PT-152: zgłoszenie obok akcji opiekuna, bez ponawiania kroku."""
import os
from unittest import mock

from tide_daemon.cardflow import caretaker, history
from tide_daemon.cardflow.state import fold

from . import test_cardflow_fault_report as fault_tests
from . import test_cardflow_caretaker_decision as decision_tests

TOKEN, ENV_SECRET = fault_tests.TOKEN, fault_tests.ENV_SECRET


class CaretakerReportTest(fault_tests.FaultReportCase):
    failed_card = fault_tests.DeduplicationTest.failed_card

    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.engine = self.flow.engine

    def apply_report(self, card, target, description="Tide nie zapisał wyniku", *, apply=True, suffix="-care"):
        st = fold(self.engine.journal.read(card))
        problem = next(p for p in caretaker.problems(st) if p["step"] == target)
        own = target + suffix
        run = self.engine.journal.append(card, caretaker.STARTED, step=own, problem=problem["id"],
                                         target=target, root=problem["root"], trigger=problem["trigger"],
                                         ticket="PT-1", watermark=max(e["seq"] for e in self.engine.journal.read(card)))
        outcome = caretaker.parse_result({"result": "done", "action": "escalate", "summary": "Potrzebna naprawa",
                                          "recommendation": "Napraw Tide",
                                          "report": {"description": description}}, problem, st)
        self.assertTrue(outcome["ok"], outcome)
        if apply:
            self.engine._caretaker_apply(card, fold(self.engine.journal.read(card)), run, {"outcome": outcome})
        return run, outcome

    def test_report_recovery_after_card_write_before_journal_marker(self):
        for scenario in ("create", "append", "archived", "merged"):
            with self.subTest(scenario=scenario):
                # Każdy scenariusz zaczyna z pustą listą otwartych zgłoszeń.
                for saved in self.tide_cards():
                    self.store.move_card(saved.id, "archived", -1)
                existing = None
                if scenario == "append":
                    card, target = self.failed_card("Wcześniejsze wystąpienie")
                    self.apply_report(card, target)
                    existing = self.tide_cards()[-1].id
                card, target = self.failed_card("Przerwany zapis")
                run, outcome = self.apply_report(card, target, apply=False)
                append = self.engine.journal.append

                def interrupt(card, kind, **fields):
                    if kind == "caretaker.reported":
                        raise RuntimeError("przerwanie przed znacznikiem dziennika")
                    return append(card, kind, **fields)

                with mock.patch.object(self.engine.journal, "append", side_effect=interrupt):
                    with self.assertRaisesRegex(RuntimeError, "przerwanie"):
                        self.engine._caretaker_apply(card, fold(self.engine.journal.read(card)), run,
                                                     {"outcome": outcome})
                saved = self.tide_cards()[-1]
                if existing:
                    self.assertEqual(saved.id, existing)
                self.assertEqual(saved.description.count("### "), 2 if existing else 1)
                self.assertFalse(any(e["type"] == "caretaker.reported" for e in self.engine.journal.read(card)))
                if scenario == "archived":
                    self.store.move_card(saved.id, "archived", -1)
                elif scenario == "merged":
                    append(saved.id, "card.main-merged", commit="abc")
                before = {c.id: c.description for c in self.tide_cards()}
                # Ponowne zastosowanie odczytuje kartę i dziennik z dysku.
                for _ in range(2):
                    self.engine._caretaker_apply(card, fold(self.engine.journal.read(card)), run,
                                                 {"outcome": outcome})
                self.assertEqual({c.id: c.description for c in self.tide_cards()}, before)
                [reported] = [e for e in self.engine.journal.read(card) if e["type"] == "caretaker.reported"]
                self.assertTrue(reported["ok"])
                self.assertEqual(reported["reported"], saved.id)
                self.assertEqual(len([e for e in self.engine.journal.read(card)
                                      if e["type"] == caretaker.ESCALATED]), 1)

    def test_stalled_signature_matches_preview_and_saved_report(self):
        card = self.card(title="Zastój karty")
        _, events = decision_tests.CaretakerDecisionTest().engine("stalled")
        for event in events:
            self.engine.journal.append(card, event["type"], **{k: v for k, v in event.items()
                                       if k not in ("type", "seq", "ts", "v", "card")})
        request = {"problem": "stalled:card", "choice": "report", "description": "Karta stoi"}
        preview = self.engine.preview_step_fault(card, request)
        self.assertEqual(preview["signature"], "flow/tide/stalled")
        self.assertEqual(self.tide_cards(), [])
        reply = self.engine.resolve_caretaker(card, request)
        self.assertEqual(reply["signature"], preview["signature"])
        [saved] = self.tide_cards()
        self.assertIn("Podpis błędu: `flow/tide/stalled`", saved.description)

    def test_new_caretaker_step_adds_distinct_occurrence_even_without_excerpt(self):
        card, target = self.failed_card("Ten sam problem, dwa działania opiekuna")
        self.apply_report(card, target)
        # Przekroczenie limitu pełnego opisu uruchamia zapis wystąpienia bez wycinka.
        with mock.patch("tide_daemon.cardflow.engine.DESCRIPTION_LIMIT_BYTES", 1):
            run, outcome = self.apply_report(card, target, suffix="-care-2")
        [saved] = self.tide_cards()
        self.assertEqual(saved.description.count("### "), 2)
        self.assertEqual(saved.description.count("Wycinek dziennika kroku"), 1)
        markers = [line for line in saved.description.splitlines() if line.startswith("<!-- tide-fault-occurrence:")]
        self.assertEqual(len(set(markers)), 2)
        self.engine._caretaker_apply(card, fold(self.engine.journal.read(card)), run, {"outcome": outcome})
        self.assertEqual(self.tide_cards()[0].description, saved.description)

    def test_report_masks_secrets_deduplicates_and_never_retries(self):
        reports = []
        for title in ("Pierwszy", "Drugi"):
            card, target = self.failed_card(title)
            step = fold(self.engine.journal.read(card)).steps[target]
            with open(os.path.join(step["stepDir"], "out", "step.log"), "a") as fh:
                fh.write(f"GITHUB_TOKEN={TOKEN}\nTIDE_FAKE_SECRET_TOKEN={ENV_SECRET}\n")
            run, outcome = self.apply_report(card, target, f"Błąd {TOKEN}")
            self.engine._caretaker_apply(card, fold(self.engine.journal.read(card)), run, {"outcome": outcome})
            events = self.engine.journal.read(card)
            [reported] = [e for e in events if e["type"] == "caretaker.reported"]
            reports.append(reported)
            self.assertEqual([e for e in events if e["type"] in ("step.decision", "stage.retry")], [])
            self.assertEqual(len([e for e in events if e["type"] == caretaker.ESCALATED]), 1)
            self.assertIn(f"Opiekun: zgłosił błąd Tide ({reported['reported']}) —", caretaker.history_text(reported))
            self.assertIsNotNone(history._event_entry(reported))
        self.assertEqual(reports[0]["reported"], reports[1]["reported"])
        [saved] = self.tide_cards()
        for secret in (TOKEN, ENV_SECRET):
            self.assertNotIn(secret, saved.description)
        self.assertEqual(saved.description.count("### "), 2)
        self.store.move_card(saved.id, "archived", -1)
        card, target = self.failed_card("Trzeci")
        self.apply_report(card, target)
        self.assertEqual(len(self.tide_cards()), 2)

    def test_missing_report_project_does_not_block_main_action(self):
        card, target = self.failed_card("Brak projektu")
        board = self.store.load()
        board.projects.pop("plasma-tide")
        with mock.patch.object(self.store, "load", return_value=board):
            self.apply_report(card, target)
        events = self.engine.journal.read(card)
        [reported] = [e for e in events if e["type"] == "caretaker.reported"]
        self.assertFalse(reported["ok"])
        self.assertIn("no-report-project", caretaker.history_text(reported))
        self.assertEqual(len([e for e in events if e["type"] == caretaker.ESCALATED]), 1)

    def test_flow_error_signature_and_preview_without_writes(self):
        card = self.card(title="Awaria etapu")
        _, events = decision_tests.CaretakerDecisionTest().engine()
        for event in events:
            self.engine.journal.append(card, event["type"], **{k: v for k, v in event.items()
                                       if k not in ("type", "seq", "ts", "v", "card")})
        request = {"problem": "flow-error:advance", "choice": "report", "description": "Nie działa etap"}
        preview = self.engine.preview_step_fault(card, request)
        self.assertEqual(preview["signature"], "flow/tide/advance")
        self.assertEqual(self.tide_cards(), [])
        reply = self.engine.resolve_caretaker(card, request)
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["signature"], preview["signature"])
        self.assertEqual(len(self.tide_cards()), 1)
        self.assertEqual([e for e in self.engine.journal.read(card) if e["type"] == "stage.retry"], [])

    def test_invalid_report_is_rejected(self):
        card, target = self.failed_card("Błędny opis")
        st = fold(self.engine.journal.read(card))
        problem = caretaker.problems(st)[0]
        for report in ("tekst", {}, {"description": " "}, {"description": "x" * 4001}):
            outcome = caretaker.parse_result({"result": "done", "action": "escalate", "summary": "Naprawić",
                                              "recommendation": "Napraw Tide",
                                              "report": report}, problem, st)
            self.assertFalse(outcome["ok"], report)

    def test_report_is_orthogonal_to_every_registered_action_and_trigger(self):
        card, _ = self.failed_card("Wyniki opiekuna")
        st = fold(self.engine.journal.read(card))
        params = {"summary": "Problem Tide", "recommendation": "Napraw Tide", "reason": "Błąd Tide",
                  "instruction": "Sprawdź wynik", "step": "step-1", "feedback": "Popraw wiersz planu"}
        for trigger in ("error", "repeat", "no-progress", "flow-error", "stalled", "waiting", "plan-fix"):
            problem = {"trigger": trigger}
            for action in caretaker.allowed_actions(problem):
                with self.subTest(trigger=trigger, action=action):
                    result = caretaker.parse_result({"result": "done", "action": action, **params,
                                                     "report": {"description": "Zgłoszenie"}}, problem, st)
                    self.assertTrue(result["ok"], result)
                    self.assertEqual(result["report"], {"description": "Zgłoszenie"})
