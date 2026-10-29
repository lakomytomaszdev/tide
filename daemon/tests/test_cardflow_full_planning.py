"""PT-81: rozmowa planującego w jednej Konsoli i dalszy review propozycji."""
import json
import os
from .test_cardflow_dbus import git

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
        self.assertTrue(wait_until(lambda: self.of(card, "plan.proposed"), timeout=10), self.events(card))
        proposed = self.of(card, "plan.proposed")[0]
        self.assertIn("Eksport pliku z podglądem", proposed["specification"])
        self.assertIn("Plik można pobrać", proposed["rows"][0]["body"])
        self.assertFalse(any(e["type"] == "step.console_closed" for e in self.events(card)))
        with open(os.path.join(step["stepDir"], "out", "plan.md"), "w", encoding="utf-8") as fh:
            fh.write("zmieniony plan")
        with open(os.path.join(step["stepDir"], "out", "spec.md"), "w", encoding="utf-8") as fh:
            fh.write("zmieniona specyfikacja")
        with open(os.path.join(step["stepDir"], "out", "tickets", "1.md"), "w", encoding="utf-8") as fh:
            fh.write("zmieniona kartka")
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
        self.assertEqual(self.of(card, "plan.proposed")[0], proposed)
        self.assertIn("Plik można pobrać", self.of(card, "plan.proposed")[0]["rows"][0]["body"])
        self.assertNotIn("zmieniona", self.of(card, "plan.proposed")[0]["specification"])
        review = next(e for e in self.of(card, "step.started") if e["kind"] == "plan-review")
        with open(os.path.join(review["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            review_prompt = json.load(fh)["prompt"]
        self.assertIn("Eksport pliku z podglądem", review_prompt)
        self.assertNotIn("zmieniona", review_prompt)
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

    def test_explicit_question_result_waits_for_user(self):
        card, step, _terminal = self._start_full()
        with open(os.path.join(step["stepDir"], "out", "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"result": "question", "question": "Który wariant eksportu?"}, fh)
        shown = self.wait_column(card, "waiting")
        self.assertEqual(shown["derived"]["flow"]["question"], "Który wariant eksportu?")
        self.assertEqual(self.of(card, "plan.proposed"), [])
        with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            self.assertIn("Pełne planowanie", fh.read())
        box = []
        self.flow.resolve_step(card, json.dumps({"choice": "answer", "answer": "Wariant CSV"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10), self.events(card))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: len(self.of(card, "step.started")) > 1, timeout=10), self.events(card))
        self.assertFalse(any(e["type"] == "step.console_closed" for e in self.events(card)))
        resumed = self.of(card, "step.started")[1]
        self.assertEqual(resumed["parentStep"], step["step"])
        with open(os.path.join(resumed["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            self.assertIn("Wariant CSV", fh.read())

    def test_denied_command_can_resume_after_decision(self):
        card, step, _terminal = self._start_full()
        with open(os.path.join(step["stepDir"], "out", "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"result": "denied", "command": "git status"}, fh)
        self.assertTrue(wait_until(lambda: self.of(card, "step.finished"), timeout=10), self.events(card))
        box = []
        self.flow.resolve_step(card, json.dumps({"choice": "deny"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10), self.events(card))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: len(self.of(card, "step.started")) > 1, timeout=10), self.events(card))
        self.assertFalse(any(e["type"] == "step.console_closed" for e in self.events(card)))
        resumed = self.of(card, "step.started")[1]
        with open(os.path.join(resumed["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            self.assertIn("Użytkownik odmówił zgody na polecenie: git status", fh.read())

    def test_error_retries_with_original_prompt(self):
        card, step, _terminal = self._start_full()
        with open(os.path.join(step["stepDir"], "out", "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"result": "error", "reason": "przerwano"}, fh)
        self.assertTrue(wait_until(lambda: self.of(card, "step.finished"), timeout=10), self.events(card))
        self.assertTrue(wait_until(lambda: len(self.of(card, "step.started")) > 1, timeout=10), self.events(card))
        self.assertFalse(any(e["type"] == "step.console_closed" for e in self.events(card)))
        resumed = self.of(card, "step.started")[1]
        self.assertEqual(resumed["attempt"], 1)
        self.assertEqual(resumed["parentStep"], step["step"])
        with open(os.path.join(resumed["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            prompt = fh.read()
        self.assertIn("Pełne planowanie", prompt)
        self.assertIn("Krok ponowiony automatycznie", prompt)

    def test_extra_project_is_quoted_context_without_writable_path(self):
        extra = os.path.join(self.base, "ws", "extra")
        os.makedirs(extra)
        git(extra, "init", "-q")
        with open(os.path.join(extra, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("Kontekst projektu")
        with open(os.path.join(extra, "large.txt"), "w", encoding="utf-8") as fh:
            fh.write("x" * 40_000)
        with open(os.path.join(extra, "binary.dat"), "wb") as fh:
            fh.write(b"sekret\0binarny")
        git(extra, "add", "README.md", "large.txt", "binary.dat")
        git(extra, "commit", "-q", "-m", "Initial context")
        self.store.upsert_project({"slug": "extra", "name": "Dodatkowy", "repo": extra})
        card = self.card()
        self.store.update_card(card, {"projects": ["extra"]})
        terminal = _Terminal()
        self.flow.engine.terminal = terminal
        reply = self.start(card, entry="planning", mode="full", executor="Sonnet")
        self.assertTrue(reply["ok"], reply)
        step = self.started(card)
        with open(terminal.opened[0].task_file, encoding="utf-8") as fh:
            task = json.load(fh)
        self.assertEqual([task["argv"][i + 1] for i, arg in enumerate(task["argv"][:-1])
                          if arg == "--add-dir"], [os.path.join(step["stepDir"], "out")])
        self.assertTrue(all(extra not in arg for arg in task["argv"]))
        prompt = task["argv"][-1]
        self.assertIn('"plik": "README.md", "treść": "Kontekst projektu"', prompt)
        self.assertIn("pominięto 2 pozostałych plików", prompt)
        self.assertNotIn("sekret", prompt)
        self.assertNotIn("x" * 100, prompt)
        self.assertLess(len(prompt.encode("utf-8")), 100 * 1024)
        self.assertIn("nie są ścieżkami do otwarcia ani modyfikacji", prompt)
        self.assertFalse(os.path.exists(os.path.join(step["stepDir"], "in", "context")))
        with open(os.path.join(extra, "README.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "Kontekst projektu")

    def test_without_terminal_does_not_start_card(self):
        from tide_daemon.cardflow.engine import FlowError
        card = self.card()
        with self.assertRaises(FlowError) as caught:
            self.flow.engine.start(card, {"entry": "planning", "mode": "full", "executor": "Sonnet"})
        self.assertEqual(caught.exception.code, "no-terminal")
        self.assertEqual(self.events(card), [])
