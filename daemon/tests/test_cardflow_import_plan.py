"""PT-71: przejęcie istniejącego planu przez publiczne zlecenie karty."""
import json
import os
import shutil
from unittest import mock

from .privatebus import wait_until
from .test_cardflow import FlowInProcess


class PlanImportTest(FlowInProcess):
    def source(self, card, *, old=False, bound=True, name="2026-10-29-import.md"):
        folder = os.path.join(self.repo, "docs", "agents", "plans")
        os.makedirs(os.path.join(folder, "tickets"), exist_ok=True)
        path = os.path.join(folder, name)
        rows = ("| PT-7 | Gotowy plasterek | gotowe | | Sonnet | zachować wynik | standard | Opus | |\n"
                "| PT-8 | Dalszy plasterek | todo | | Sonnet | po gotowym | standard | Opus | PT-7 |\n")
        if old:
            rows = ("| PT-7 | Gotowy plasterek | gotowe | | | |\n"
                    "| PT-8 | Dalszy plasterek | w-toku | | | |\n")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Przekazanie\n" + (f"card: {card}\n" if bound else "") +
                     "tracker: local\nticket-prefix: PT\ncoordinator: claude\nstatus: active\n"
                     "updated: 2026-10-29T12:00:00+01:00\n---\n\n"
                     + ("| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |\n"
                        "| --- | --- | --- | --- | --- | --- |\n" if old else
                        "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n") + rows +
                     ("\n## Przekazanie\nWolny tekst: zachowaj wynik poprzedniej sesji.\n" if old else ""))
        if not old:
            for ticket in ("PT-7", "PT-8"):
                with open(os.path.join(folder, "tickets", ticket + ".md"), "w", encoding="utf-8") as fh:
                    fh.write(f"# {ticket}\n\nATRAPA: gotowe\n\n- [ ] Wynik.\n\n## Dla recenzenta\nSprawdź wynik.\n")
        return path

    def import_plan(self, card, path):
        box = []
        self.board.start_card(card, json.dumps({"entry": "import", "planPath": path, "executor": "Sonnet"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=15))
        return json.loads(box[0])

    def test_candidates_prioritize_plan_of_card_and_cardless_is_only_hint(self):
        card = self.card()
        other = self.card()
        own = self.source(card, name="2026-10-28-own.md")
        hint = self.source(card, bound=False, name="2026-10-29-hint.md")
        self.source(other, name="2026-10-29-other.md")
        shown = self.shown(card)
        self.assertEqual([item["path"] for item in shown["planCandidates"]], [own, hint])
        self.assertEqual(shown["planPath"], "")

    def test_candidates_also_find_plans_kept_in_board_repository(self):
        card = self.card()
        self.store.upsert_project({"slug": "proj", "aiDocs": "tide"})
        source = self.source(card)
        target = os.path.join(self.root, "docs", "proj", "plans", os.path.basename(source))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        os.unlink(source)
        self.assertEqual(self.shown(card)["planCandidates"][0]["path"], target)

    def test_import_preserves_number_and_finished_ticket_without_editing_source(self):
        card = self.card()
        path = self.source(card)
        with open(path, "rb") as fh:
            before = fh.read()
        reply = self.import_plan(card, path)
        self.assertTrue(reply["ok"], reply)
        self.assertFalse(reply["needsCompletion"])
        self.assertEqual(self.shown(card)["planPath"], path)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
        self.restart()
        box = []
        self.flow.approve_plan(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        approved = json.loads(box[0])
        self.assertEqual(approved["map"], {"PT-7": "PT-7", "PT-8": "PT-8"})
        self.approve_environment(card)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=40), self.events(card))
        self.assertEqual([(e["ticket"], e["state"]) for e in self.of(card, "ticket.imported")], [("PT-7", "gotowe")])
        self.assertEqual([e["ticket"] for e in self.of(card, "step.started") if e["kind"] == "ticket"], ["PT-8"])
        from .test_cardflow_dbus import git
        from tide_daemon.cardflow.state import fold, ticket_of
        branch = ticket_of(fold(self.flow.engine.journal.read(card)))["cardBranch"]
        tree = git(self.repo, "ls-tree", "-r", "--name-only", branch)
        self.assertIn(f"docs/agents/tickets/{card.lower()}-flow/pt-7.md", tree)
        self.assertIn(f"docs/agents/tickets/{card.lower()}-flow/pt-8.md", tree)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_restart_after_approval_keeps_imported_terminal_state(self):
        card = self.card()
        self.assertTrue(self.import_plan(card, self.source(card))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        self.flow.stop()
        journal = self.flow.engine.journal
        original = journal.append

        def interrupted(ident, kind, **data):
            if kind == "ticket.imported":
                raise OSError("restart przed zapisaniem pojedynczego ticketu")
            return original(ident, kind, **data)

        with mock.patch.object(journal, "append", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.flow.engine.approve_plan(card)
        from tide_daemon.cardflow.state import fold
        state = fold(journal.read(card))
        self.assertIn("PT-7", state.imported_done)
        self.assertTrue(self.flow.engine.approve_plan(card)["already"])
        self.restart()
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=40), self.events(card))
        self.assertEqual([e["ticket"] for e in self.of(card, "step.started") if e["kind"] == "ticket"], ["PT-8"])

    def test_completion_cannot_remove_or_rename_imported_ticket(self):
        card = self.card()
        path = self.source(card)
        self.assertTrue(self.import_plan(card, path)["ok"])
        self.flow.stop()
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        changed = body.replace("| PT-7 | Gotowy plasterek | gotowe | | Sonnet | zachować wynik | standard | Opus | |\n", "") \
                      .replace("| PT-8 | Dalszy plasterek |", "| #1 | Dalszy plasterek |")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(changed)
        with open(os.path.join(os.path.dirname(path), "tickets", "1.md"), "w", encoding="utf-8") as fh:
            fh.write("# #1\n\n- [ ] Wynik.\n")
        result = self.flow.engine.propose_plan(card, path, import_source=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("PT-7" in error for error in result["errors"]))
        self.assertTrue(any("PT-8" in error for error in result["errors"]))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body.replace("Gotowy plasterek", "Nowy tytuł"))
        result = self.flow.engine.propose_plan(card, path, import_source=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("tytuł" in error for error in result["errors"]))

    def test_old_six_column_plan_is_completed_in_background_then_reviewed(self):
        card = self.card()
        path = self.source(card, old=True, bound=False)
        with open(path, "rb") as fh:
            before = fh.read()
        reply = self.import_plan(card, path)
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(reply["needsCompletion"])
        self.assertEqual([r["id"] for r in self.of(card, "plan.imported")[0]["rows"]], ["PT-7", "PT-8"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=25), self.events(card))
        self.assertTrue(any(s["kind"] == "plan-import" for s in self.of(card, "step.started")))
        prompts = []
        for root, _dirs, files in os.walk(self.base):
            if "fake-call.json" in files:
                with open(os.path.join(root, "fake-call.json"), encoding="utf-8") as fh:
                    prompts.append(json.load(fh)["prompt"])
        self.assertTrue(any("Wolny tekst: zachowaj wynik" in prompt for prompt in prompts))
        self.assertEqual([r["workId"] for r in self.shown(card)["derived"]["flow"]["plan"]["rows"]], ["PT-7", "PT-8"])
        approved = self.flow.engine.approve_plan(card)
        self.assertEqual(approved["map"], {"PT-7": "PT-7", "PT-8": "PT-8"})
        self.approve_environment(card)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=40), self.events(card))
        self.assertEqual([e["ticket"] for e in self.of(card, "step.started") if e["kind"] == "ticket"], ["PT-8"])
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_rejected_imported_ticket_does_not_stop_independent_ticket(self):
        card = self.card()
        path = self.source(card)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body.replace("| PT-7 | Gotowy plasterek | gotowe |", "| PT-7 | Gotowy plasterek | odrzucone |")
                     .replace("| Opus | PT-7 |", "| Opus | |"))
        self.assertTrue(self.import_plan(card, path)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        box = []
        self.flow.approve_plan(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"])
        self.approve_environment(card)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=40), self.events(card))
        self.assertEqual([(e["ticket"], e["state"]) for e in self.of(card, "ticket.imported")],
                         [("PT-7", "odrzucone")])
        self.assertEqual([e["ticket"] for e in self.of(card, "step.started") if e["kind"] == "ticket"], ["PT-8"])

    def test_new_ticket_number_skips_existing_id_in_board_only_plan(self):
        card = self.card()
        self.store.upsert_project({"slug": "proj", "aiDocs": "tide"})
        source = self.source(card)
        target = os.path.join(self.root, "docs", "proj", "plans", os.path.basename(source))
        os.makedirs(os.path.join(os.path.dirname(target), "tickets"), exist_ok=True)
        with open(source, encoding="utf-8") as fh:
            body = fh.read().replace("PT-7", "PT-1").replace("PT-8", "#1")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(body)
        for old, new in (("PT-7", "PT-1"), ("PT-8", "1")):
            shutil.copyfile(os.path.join(os.path.dirname(source), "tickets", old + ".md"),
                            os.path.join(os.path.dirname(target), "tickets", new + ".md"))
        os.unlink(source)
        self.assertTrue(self.import_plan(card, target)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        self.flow.stop()
        journal = self.flow.engine.journal
        original = journal.append
        interrupted = False
        def crash_after_reservation(ident, kind, **data):
            nonlocal interrupted
            if kind == "result" and data.get("action") == "reserve" and not interrupted:
                interrupted = True
                raise OSError("restart po rezerwacji")
            return original(ident, kind, **data)
        with mock.patch.object(journal, "append", side_effect=crash_after_reservation):
            with self.assertRaises(OSError):
                self.flow.engine.approve_plan(card)
        self.restart()
        self.assertTrue(wait_until(lambda: self.of(card, "plan.approved"), timeout=15), self.events(card))
        mapping = self.of(card, "plan.approved")[-1]["map"]
        self.assertEqual(mapping, {"PT-1": "PT-1", "#1": "PT-2"})
        self.assertEqual([r["ticket"] for r in self.results(card, "reserve")], ["PT-2"])
