"""PT-81: rozmowa planującego w jednej Konsoli i dalszy review propozycji."""
import json
import os

from .privatebus import wait_until
from .test_cardflow import FlowInProcess


class _Terminal:
    def __init__(self):
        self.opened = []

    def available(self):
        return True

    def open(self, plan):
        self.opened.append(plan)
        return "tab"


class FullPlanningTest(FlowInProcess):
    def _start_full(self, executor="Sonnet", provider="anthropic"):
        terminal = _Terminal()
        self.flow.engine.terminal = terminal
        card = self.card(description="Dodaj eksport pliku z podglądem wyniku.")
        reply = self.start(card, entry="planning", mode="full", executor=executor,
                           preferredProvider=provider)
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(reply["interactive"])
        self.assertEqual(len(terminal.opened), 1)
        step = self.started(card)
        self.assertEqual(step["kind"], "planning")
        return card, step, terminal

    def _write_proposal(self, card, step):
        out = os.path.join(step["stepDir"], "out")
        os.makedirs(os.path.join(out, "tickets"), exist_ok=True)
        with open(os.path.join(out, "spec.md"), "w", encoding="utf-8") as fh:
            fh.write("# Specyfikacja\nEksport pliku z podglądem.\n")
        with open(os.path.join(out, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Eksport\ncard: " + card +
                     "\ntracker: local\nticket-prefix: PT\ncoordinator: claude\n---\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     "| #1 | Eksport pliku | todo | | Sonnet | standardowa praca | standard | Opus | |\n")
        with open(os.path.join(out, "tickets", "1.md"), "w", encoding="utf-8") as fh:
            fh.write("# Eksport pliku\n**Co zbudować:** Eksport pliku z podglądem.\n"
                     "- [ ] Plik można pobrać.\n## Dla recenzenta\nSprawdź pobieranie i podgląd.\n")
        with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"result": "done", "notes": ["spec.md", "plan.md"]}, fh)

    def test_one_console_result_then_existing_review_and_panel(self):
        card, step, terminal = self._start_full()
        with open(terminal.opened[0].task_file, encoding="utf-8") as fh:
            task = json.load(fh)
        prompt = task["argv"][-1]
        self.assertEqual(task["workdir"], step["worktree"])
        self.assertEqual(task["closedFile"], os.path.join(step["stepDir"], "in", "console-closed.json"))
        self.assertIn("--session-id", task["argv"])
        for phrase in ("grill-with-docs", "domain-modeling", "to-spec", "to-tickets", "pionowe plasterki",
                       "W rozmowie pokaż", "Dla recenzenta", "standardzie 1.2", "Nie rezerwuj numerów",
                       "nie publikuj", "GitHub Issues", "Plik wyniku:"):
            self.assertIn(phrase, prompt)
        self.assertEqual(len(self.of(card, "step.started")), 1)
        self.assertEqual(self.shown(card)["boardColumn"], "progress")
        self.assertIn("planowanie w Konsoli", self.shown(card)["derived"]["note"])
        self._write_proposal(card, step)
        self.assertTrue(wait_until(lambda: self.of(card, "step.finished"), timeout=10), self.events(card))
        self.assertEqual(self.of(card, "plan.proposed"), [], "otwarta Konsola chroni wspólne worktree")
        self.flow.engine.journal.append(card, "step.console_closed", step=step["step"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
        shown = self.wait_column(card, "waiting")
        self.assertEqual(shown["derived"]["flow"]["plan"]["status"], "pending")
        self.assertEqual([e["kind"] for e in self.of(card, "step.started")], ["planning", "plan-review"])
        self.assertEqual(len(terminal.opened), 1)

    def test_codex_receives_written_grilling_process_and_selected_model(self):
        card, step, terminal = self._start_full("Codex mocny", "openai")
        with open(terminal.opened[0].task_file, encoding="utf-8") as fh:
            task = json.load(fh)
        prompt = task["argv"][-1]
        self.assertEqual(step["program"], "codex")
        self.assertIn("gpt-6.1-sol", task["argv"])
        self.assertIn("dociekliwy grilling z dokumentacją", prompt)
        self.assertIn("słownik domenowy", prompt)
        self.assertIn("to-spec", prompt)
        self.assertIn("to-tickets", prompt)
        self.assertNotIn("grill-with-docs", prompt)
        self.assertNotIn("domain-modeling", prompt)

    def test_restart_does_not_open_second_console(self):
        card, step, terminal = self._start_full()
        self.restart()
        self.flow.engine.terminal = terminal
        wait_until(lambda: False, timeout=0.4)
        self.assertEqual([e["step"] for e in self.of(card, "step.started")], [step["step"]])
        self.assertEqual(len(terminal.opened), 1)

    def test_without_terminal_does_not_start_card(self):
        from tide_daemon.cardflow.engine import FlowError
        card = self.card()
        with self.assertRaises(FlowError) as caught:
            self.flow.engine.start(card, {"entry": "planning", "mode": "full", "executor": "Sonnet"})
        self.assertEqual(caught.exception.code, "no-terminal")
        self.assertEqual(self.events(card), [])
