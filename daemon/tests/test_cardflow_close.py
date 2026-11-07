"""PT-70: konflikt, podsumowanie oraz jawne scalenie karty do gałęzi głównej."""
import os
import json
from unittest import mock

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_plan_integration import PlanFlowTest
from .test_cardflow_waves import WaveFlowTest
from .test_cardflow_dbus import git


class CardClosingTest(FlowInProcess):
    proposal = PlanFlowTest.proposal
    submit = PlanFlowTest.submit
    approve = PlanFlowTest.approve
    wave_plan = WaveFlowTest.wave_plan

    def test_manual_summary_then_explicit_main_merge(self):
        card = self.card()
        self.assertTrue(self.start(card, check="manual")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        summary = self.of(card, "card.closed")[-1]["summary"]
        self.assertIn("Co przetestować ręcznie i jak", summary)
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        self.assertEqual(self.shown(card)["boardColumn"], "review")
        difference = self.flow.engine.card_diff(card)
        self.assertTrue(difference["ok"])
        self.assertIn("ATRAPA.md", difference["diff"])
        box = []
        self.flow.merge_card_to_main(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        merged = json.loads(box[0])
        self.assertTrue(merged["ok"], merged)
        self.assertNotEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        self.assertTrue(wait_until(lambda: self.shown(card)["boardColumn"] == "done", timeout=5))
        self.assertEqual(len(self.of(card, "card.merged")), 1)

    def test_manual_close_collects_desk_items_from_step_notes(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: gotowe\nATRAPA: pulpit")
        self.assertTrue(self.start(card, check="manual")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        summary = self.of(card, "card.closed")[-1]["summary"]
        self.assertIn("Co przetestować ręcznie i jak", summary)
        self.assertIn("Do sprawdzenia na pulpicie", summary)
        self.assertRegex(summary, r"- Otwórz widżet na drugim monitorze \(krok [^,]+, ticket PT-\d+; krok [^,]+, ticket PT-\d+\)")
        self.assertRegex(summary, r"- Przeciągnij okno i sprawdź ramkę \(krok [^,]+, ticket PT-\d+\)")
        # Notka kroku review: pozycja wspólna z ticketem ma oba kroki, własna tylko krok review.
        self.assertEqual(summary.count("Otwórz widżet na drugim monitorze"), 1)
        shared = next(line for line in summary.splitlines() if "Otwórz widżet" in line)
        self.assertEqual(shared.count("krok "), 2, shared)
        ticket_step, review_step = (part.split("krok ")[1].split(",")[0] for part in shared.split(";"))
        self.assertNotEqual(ticket_step, review_step)
        own = next(line for line in summary.splitlines() if "Sprawdź skróty klawiszowe" in line)
        self.assertEqual(own.count("krok "), 1, own)
        self.assertIn(f"krok {review_step},", own)
        self.assertEqual(self.of(card, "step.started")[1]["kind"], "review")
        self.assertEqual(self.of(card, "step.started")[1]["step"], review_step)

    def test_manual_close_without_desk_section_is_unchanged(self):
        card = self.card()
        self.assertTrue(self.start(card, check="manual")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        self.assertNotIn("pulpicie", self.of(card, "card.closed")[-1]["summary"])

    def test_auto_check_closes_without_manual_list_and_leaves_main_untouched(self):
        card = self.card()
        self.assertTrue(self.start(card, check="auto")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        self.assertNotIn("Co przetestować ręcznie i jak", self.of(card, "card.closed")[-1]["summary"])
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        self.assertEqual(self.shown(card)["boardColumn"], "review")

    def test_return_to_todo_revokes_main_merge_until_new_close(self):
        from tide_daemon.cardflow.engine import FlowError
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        self.flow.stop()
        self.flow.engine.moved(card, "todo", shown="review", move_id="return-to-todo")
        with self.assertRaises(FlowError) as failure:
            self.flow.engine.merge_card_to_main(card)
        self.assertEqual(failure.exception.code, "not-closed")

    def test_main_merge_opens_its_own_worktree_when_main_is_not_checked_out(self):
        # 03.11, K-15: główny katalog stał na innej gałęzi, a „Scal do gałęzi głównej” odmawiało.
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        git(self.repo, "checkout", "-q", "-b", "feature/other")
        box = []
        self.flow.merge_card_to_main(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box[0])
        self.assertNotEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        self.assertEqual(git(self.repo, "branch", "--show-current").strip(), "feature/other")
        self.assertTrue(any(line.endswith("[master]") for line in git(self.repo, "worktree", "list").splitlines()))

    def test_main_merge_refuses_dirty_checkout_without_losing_changes(self):
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        branch = self.of(card, "card.closed")[-1].get("branch") or \
            next(e["branch"] for e in self.events(card) if e.get("type") == "result" and e.get("branch"))
        touched = git(self.repo, "diff", "--name-only", f"master...{branch}").split()
        self.assertTrue(touched)
        # Zmiana w pliku, który scalenie zmienia, blokuje i niczego nie traci.
        source = os.path.join(self.repo, touched[0])
        if os.path.exists(source):
            with open(source, "a", encoding="utf-8") as fh:
                fh.write("\nMoja niezapisana zmiana.\n")
        else:
            with open(source, "w", encoding="utf-8") as fh:
                fh.write("Moja niezapisana zmiana.\n")
            git(self.repo, "add", "-N", touched[0])
        box = []
        self.flow.merge_card_to_main(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertFalse(json.loads(box[0])["ok"])
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        with open(source, encoding="utf-8") as fh:
            self.assertIn("Moja niezapisana zmiana", fh.read())

    def test_main_merge_keeps_unrelated_uncommitted_work(self):
        # 04.11, K-43: niezacommitowana praca użytkownika w innych plikach gałęzi głównej nie blokuje scalenia karty.
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        source = os.path.join(self.repo, "CLAUDE.md")
        with open(source, "a", encoding="utf-8") as fh:
            fh.write("\nMoja niezapisana zmiana.\n")
        box = []
        self.flow.merge_card_to_main(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box[0])
        self.assertNotEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        with open(source, encoding="utf-8") as fh:
            self.assertIn("Moja niezapisana zmiana", fh.read())

    def test_conflict_uses_the_reassigned_author(self):
        # 05.11: konflikt przy scalaniu ticketu przepiętego na Codexa czekał na limit Claude (wykonawca z planu).
        card = self.card(description="Plan z konfliktem")
        path = self.wave_plan(card)
        for number, mode in ((1, "gotowe"), (2, "powoli 2")):
            with open(os.path.join(os.path.dirname(path), "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\nATRAPA: {mode}\nATRAPA-PLIK: same\n"
                         "\n- [ ] Wynik.\n")
        self.assertTrue(self.submit(card, path)["ok"])
        approved = self.approve(card)
        self.assertTrue(approved["ok"], approved)
        for ticket in approved["map"].values():
            self.flow.engine.journal.append(card, "author.reassigned", ticket=ticket, executor="Codex standard")
        self.assertTrue(wait_until(lambda: any(s.get("kind") == "conflict" for s in self.of(card, "step.started")),
                                   timeout=20), self.events(card))
        conflict = next(s for s in self.of(card, "step.started") if s.get("kind") == "conflict")
        self.assertEqual(conflict["executor"], "Codex standard")

    def test_conflict_is_resolved_in_card_worktree_before_next_ticket(self):
        card = self.card(description="Plan z konfliktem")
        path = self.wave_plan(card)
        for number, mode in ((1, "gotowe"), (2, "powoli 2")):
            with open(os.path.join(os.path.dirname(path), "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\nATRAPA: {mode}\nATRAPA-PLIK: same\n"
                         "\n- [ ] Wynik.\n")
        self.assertTrue(self.submit(card, path)["ok"])
        approved = self.approve(card)
        self.assertTrue(approved["ok"], approved)
        self.assertTrue(wait_until(lambda: any(s.get("kind") == "conflict" for s in self.of(card, "step.started")), timeout=20))
        conflicts = [s for s in self.of(card, "step.started") if s.get("kind") == "conflict"]
        self.assertEqual(len(conflicts), 1, self.events(card))
        self.assertTrue(wait_until(lambda: any(e.get("step") == conflicts[0]["step"] for e in self.of(card, "step.finished")), timeout=15))
        finished = next(e for e in self.of(card, "step.finished") if e["step"] == conflicts[0]["step"])
        if finished["result"] != "done":
            with open(os.path.join(conflicts[0]["stepDir"], "out", "step.log"), encoding="utf-8") as fh:
                self.fail(f"krok konfliktu: {finished['reason']}\n{fh.read()[-4000:]}")
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=35), self.events(card))
        self.assertEqual(conflicts[0]["worktree"], next(e["worktree"] for e in self.results(card, "card-branch")))
        resolved = [r for r in self.results(card, "merge") if r.get("resolved")]
        self.assertEqual(len(resolved), 1, self.events(card))
        self.assertTrue(resolved[0]["ok"])
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)

    def test_failed_conflict_waits_for_user_without_retry_loop(self):
        card = self.card(description="ATRAPA: conflict-error")
        path = self.wave_plan(card)
        for number, mode in ((1, "gotowe"), (2, "powoli 2")):
            with open(os.path.join(os.path.dirname(path), "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\nATRAPA: {mode}\nATRAPA-PLIK: same\n"
                         "\n- [ ] Wynik.\n")
        self.assertTrue(self.submit(card, path)["ok"])
        self.assertTrue(self.approve(card)["ok"])
        self.assertTrue(wait_until(lambda: any(s.get("kind") == "conflict" for s in self.of(card, "step.started")),
                                   timeout=25), self.events(card))
        self.assertTrue(wait_until(lambda: any(d["kind"] == "error" for d in self.shown(card)["derived"]["flow"]["decisions"]),
                                   timeout=15), self.events(card))
        self.assertEqual(len([s for s in self.of(card, "step.started") if s.get("kind") == "conflict"]), 1)
        self.assertFalse(self.of(card, "card.closed"))
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)

    def test_return_from_direct_card_adds_new_ticket_on_same_card_branch(self):
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        branch = self.results(card, "card-branch")[0]["branch"]
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "Dodaj ticket z poprawką"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.revision-ready"), timeout=20), self.events(card))
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) >= 2, timeout=20), self.events(card))
        approved = self.approve(card)
        self.assertTrue(approved["ok"], approved)
        self.assertTrue(wait_until(lambda: len(self.of(card, "card.closed")) == 2, timeout=30), self.events(card))
        closings = self.of(card, "card.closed")
        self.assertNotEqual(closings[0]["step"], closings[1]["step"])
        self.assertEqual(len([s for s in self.of(card, "step.started") if s["kind"] == "close"]), 2)
        self.assertEqual(self.results(card, "card-branch")[0]["branch"], branch)
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)

    def test_second_conflict_waits_while_first_uses_card_worktree(self):
        from tide_daemon.cardflow.state import fold
        card = self.card()
        self.flow.stop()
        journal = self.flow.engine.journal
        journal.append(card, "card.started", entry="plan", repo=self.repo)
        journal.append(card, "plan.approved", rows=[{"id": "PT-2"}, {"id": "PT-3"}])
        journal.append(card, "step.started", step="conflict-2", ticket="PT-2", kind="conflict")
        journal.append(card, "step.started", step="review-3", ticket="PT-3", kind="review", round=1)
        journal.append(card, "step.finished", step="review-3", ticket="PT-3", result="done")
        journal.append(card, "review.verdict", step="review-3", ticket="PT-3", round=1,
                       verdict="APPROVE", blockers=0, majors=0, nits=0)
        journal.append(card, "result", action="merge", key="PT-3", ok=False,
                       error="konflikt przy scalaniu")
        state = fold(journal.read(card))
        with mock.patch.object(self.flow.engine, "_start_conflict") as start:
            self.flow.engine._advance(card, state)
            start.assert_not_called()
