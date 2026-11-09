"""PT-59: review ticketu, poprawki, limit rund i sprzątanie po zatwierdzeniu."""
import json
import os
from dataclasses import replace
from unittest import mock

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git


class ReviewFlowTest(FlowInProcess):
    def wait_closed(self, card, timeout=30):
        # „Do sprawdzenia” pojawia się już po merge; domknięcie uruchamia się dalej w ticku.
        self.wait_column(card, "review", timeout=timeout)
        self.assertTrue(wait_until(lambda: self.shown(card)["derived"]["flow"]["closed"],
                                   timeout=timeout), f"{card}: domknięcie nie zakończyło się")
        return self.wait_column(card, "review", timeout=timeout)

    def test_default_reviewer_stays_with_custom_authors_provider(self):
        from tide_daemon.models import executors

        catalog = executors.Catalog(
            (executors.Executor("Inny", "codex", "model-inny", "other"),),
            {"other": executors.Provider("other", "Inny", "https://example.test/v1", "openai")})
        self.flow.engine.catalog = lambda: catalog
        card = self.card()
        reply = self.start(card, executor="Inny")
        self.assertEqual((reply["ok"], reply["error"]), (False, "bad-reviewer"), reply)
        self.assertEqual(self.events(card), [])

    def test_mixed_provider_review_uses_other_provider_and_codex_route(self):
        card = self.card()
        reply = self.start(card, executor="Sonnet", preferredProvider="mixed", reviewer="Codex Astra")
        self.assertTrue(reply["ok"], reply)
        self.wait_column(card, "review")
        review = next(s for s in self.of(card, "step.started") if s["kind"] == "review")
        self.assertEqual((review["executor"], review["program"], review["provider"], review["model"]),
                         ("Codex Astra", "codex", "openai", "gpt-6-astra"))
        with open(os.path.join(review["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            self.assertIn('model_reasoning_effort="high"', json.load(fh)["argv"])

    def test_local_author_without_second_local_model_is_refused_before_journal(self):
        from tide_daemon.models import config as models_config
        path = os.path.join(self.base, "config", "tide", "models.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"roles": {"agent": "local:qwen3-coder:30b"}, "agentProgram": "codex"}, fh)
        self.flow.engine.models_config = lambda: models_config.load(path)
        self.flow.engine.model_probe = lambda endpoint: {"online": True, "kind": "ollama",
                                                         "models": [{"name": "qwen3-coder:30b"}]}
        card = self.card()
        reply = self.start(card, executor="Lokalny", preferredProvider="local")
        self.assertEqual((reply["ok"], reply["error"]), (False, "bad-reviewer"), reply)
        self.assertEqual(self.events(card), [])

    def test_catalog_change_before_review_is_noted_and_does_not_stop_the_card(self):
        from tide_daemon.models.executors import Catalog, default_catalog

        original = default_catalog()
        changed = Catalog(tuple(replace(e, model="sonnet") if e.name == "Opus" else e
                                for e in original.executors), original.providers)
        calls = 0

        def catalog():
            nonlocal calls
            calls += 1
            return original if calls == 1 else changed

        self.flow.engine.catalog = catalog
        card = self.card()
        self.assertTrue(self.start(card, reviewer="Opus")["ok"])
        # 03.11 (decyzja użytkownika): niezależny recenzent to rekomendacja, nie blokada — review rusza z notką.
        self.wait_column(card, "review", timeout=30)
        self.assertEqual(len(self.of(card, "review.reviewer-note")), 1)
        self.assertIn("review", [s["kind"] for s in self.of(card, "step.started")])

    def test_ticket_ref_change_after_approval_cannot_merge_unreviewed_commit(self):
        card = self.card()
        original = self.flow.engine.git.merge
        changed = False

        def move_ref(repo, card_branch, ticket_branch, card_worktree, message, *, expected_tip):
            nonlocal changed
            if not changed:
                changed = True
                ticket_path = next(s["worktree"] for s in self.flow.engine.journal.read(card)
                                   if s.get("type") == "step.started" and s.get("kind") == "ticket")
                with open(os.path.join(ticket_path, "after-review.txt"), "w", encoding="utf-8") as fh:
                    fh.write("nieprzejrzana zmiana\n")
                git(ticket_path, "add", "after-review.txt")
                git(ticket_path, "commit", "-qm", "later change")
            return original(repo, card_branch, ticket_branch, card_worktree, message,
                            expected_tip=expected_tip)

        with mock.patch.object(self.flow.engine.git, "merge", side_effect=move_ref):
            self.assertTrue(self.start(card)["ok"])
            shown = self.wait_column(card, "waiting")
        self.assertIn("nie scalono", shown["derived"]["note"])
        self.assertEqual([v["verdict"] for v in self.of(card, "review.verdict")], ["APPROVE"])
        self.assertEqual(len(self.results(card, "merge")), 1)
        self.assertFalse(self.results(card, "merge")[0]["ok"])
        card_branch = next(r["branch"] for r in self.results(card, "card-branch"))
        self.assertEqual(git(self.repo, "rev-parse", card_branch).strip(), self.master)
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)

    def test_reviewer_files_are_moved_out_of_the_ticket_and_the_review_counts(self):
        # Nowy nieśledzony plik recenzenta nie trafia do ticketu (autor by go zacommitował), ale review nie przepada:
        # Tide przenosi go do katalogu kroku review (04.11, K-43/BO-9: 9 odrzuconych review przez wynik pomiaru).
        card = self.card(description="Dodaj eksport.\n\nATRAPA: review-modifies")
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "review")
        self.assertEqual(len(self.of(card, "review.verdict")), 1)
        review = next(s for s in self.of(card, "step.started") if s["kind"] == "review")
        self.assertFalse(os.path.exists(os.path.join(review["worktree"], "review-modified.txt")))
        self.assertTrue(os.path.isfile(os.path.join(review["stepDir"], "out", "pliki-recenzenta", "review-modified.txt")))

    def test_approved_review_receives_full_context_and_cleans_worktrees(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: gotowe")
        reply = self.start(card, reviewer="Opus")
        self.assertTrue(reply["ok"], reply)
        shown = self.wait_closed(card)
        steps = self.of(card, "step.started")
        self.assertEqual([s["kind"] for s in steps], ["ticket", "review", "close"])
        self.assertEqual((steps[0]["executor"], steps[1]["executor"]), ("Sonnet", "Opus"))
        self.assertNotEqual(steps[0]["model"], steps[1]["model"])
        with open(os.path.join(steps[1]["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            call = json.load(fh)
        prompt = call["prompt"]
        for phrase in ("Dodaj eksport.", "Dla recenzenta:", "Diff ticketu", "diff --git",
                       "Notka wyniku wykonawcy:", "Atrapa zrobiła ticket.", "Zasady projektu:",
                       "CLAUDE.md", "Plik wyniku:"):
            self.assertIn(phrase, prompt)
        self.assertIn(f"make test-changed BASE={self.master}", prompt)
        with open(os.path.join(steps[0]["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            self.assertIn(f"make test-changed BASE={self.master}", json.load(fh)["prompt"])
        with open(os.path.join(steps[2]["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            self.assertIn("Przed napisaniem podsumowania uruchom raz pełny `make test`", json.load(fh)["prompt"])
        verdict = self.of(card, "review.verdict")
        self.assertEqual([(v["verdict"], v["blockers"], v["majors"], v["nits"]) for v in verdict],
                         [("APPROVE", 0, 0, 0)])
        self.assertTrue(shown["derived"]["flow"]["closed"])
        self.assertIn("Podsumowanie karty", shown["derived"]["flow"]["summary"])
        self.assertEqual(len(self.results(card, "merge")), 1)
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        self.assertFalse(os.path.exists(reply["worktree"]), "worktree ticketu po zatwierdzeniu")
        self.assertFalse(os.path.exists(steps[1]["worktree"]), "worktree review po zatwierdzeniu")

    def test_two_request_changes_rounds_then_approve(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: review-requests 2")
        self.assertTrue(self.start(card)["ok"])
        self.wait_closed(card, timeout=50)
        verdicts = self.of(card, "review.verdict")
        self.assertEqual([(v["round"], v["verdict"], v["majors"]) for v in verdicts],
                         [(1, "REQUEST_CHANGES", 1), (2, "REQUEST_CHANGES", 1), (3, "APPROVE", 0)])
        self.assertEqual([s["kind"] for s in self.of(card, "step.started")],
                         ["ticket", "review", "fix", "review", "fix", "review", "close"])
        fixes = [s for s in self.of(card, "step.started") if s["kind"] == "fix"]
        with open(os.path.join(fixes[0]["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            prompt = json.load(fh)["prompt"]
        self.assertIn("MAJOR: popraw ATRAPA.md", prompt)
        self.assertIn(f"make test-changed BASE={self.master}", prompt)
        self.assertEqual(len(self.results(card, "merge")), 1)

    def test_three_failed_reviews_wait_for_user_without_merging(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: review-requests 3")
        self.assertTrue(self.start(card)["ok"])
        shown = self.wait_column(card, "waiting", timeout=50)
        self.assertIn("po 3 rundach review", shown["derived"]["note"])
        self.assertTrue(shown["derived"]["flow"]["reviewQuestion"])
        self.assertIn("Ponów poprawki", shown["derived"]["question"])
        self.assertIn("po 3 rundach review zostały uwagi", shown["derived"]["question"])
        self.assertEqual(len(self.of(card, "review.question")), 1)
        verdicts = self.of(card, "review.verdict")
        self.assertEqual([v["verdict"] for v in verdicts], ["REQUEST_CHANGES"] * 3)
        self.assertEqual(self.results(card, "merge"), [])
        count = len(self.events(card))
        wait_until(lambda: False, timeout=0.5)
        self.assertEqual(len(self.events(card)), count)

        replies = []
        self.board.resolve_card_review(card, "retry", replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=10))
        self.assertTrue(json.loads(replies[0])["ok"])
        self.wait_column(card, "review", timeout=30)
        self.assertEqual([v["round"] for v in self.of(card, "review.verdict")], [1, 2, 3, 4])
        self.assertEqual([d["choice"] for d in self.of(card, "review.decision")], ["retry"])
        self.assertEqual(len(self.results(card, "merge")), 1)
        # K-88/PT-312: poprawki rundy 3 nie pisze recenzent, który potem ocenia je w rundzie 4.
        started = self.of(card, "step.started")
        fix = next(s for s in started if s["kind"] == "fix" and s["round"] == 3)
        review = next(s for s in started if s["kind"] == "review" and s["round"] == 4)
        self.assertNotEqual(fix["executor"], review["executor"])
        self.assertIn(fix.get("executorReason"), ("author", "escalated"))

    def test_accept_after_three_rounds_merges_the_last_reviewed_commit(self):
        # 03.11, K-33/PT-108: uwagi dotyczyły testów niewykonalnych w piaskownicy — użytkownik scala mimo uwag.
        card = self.card(description="Dodaj eksport.\n\nATRAPA: review-requests 4")
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "waiting", timeout=50)
        reviews = len([s for s in self.of(card, "step.started") if s["kind"] in ("fix", "review")])
        replies = []
        self.board.resolve_card_review(card, "accept", replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=10))
        self.assertTrue(json.loads(replies[0])["ok"])
        self.assertTrue(wait_until(lambda: self.results(card, "merge"), timeout=30), self.events(card))
        self.assertTrue(self.results(card, "merge")[0]["ok"])
        self.assertEqual([d["choice"] for d in self.of(card, "review.decision")], ["accept"])
        self.assertEqual(len([s for s in self.of(card, "step.started") if s["kind"] in ("fix", "review")]), reviews)
        self.assertEqual(self.of(card, "ticket.rejected"), [])

    def test_reject_after_three_rounds_records_decision_without_merging(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: review-requests 4")
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "waiting", timeout=50)
        # Odrzucenie kończy wykonanie ticketu; osobne domknięcie karty nadal może wystartować.
        execution_steps = lambda: [s for s in self.of(card, "step.started") if s["kind"] != "close"]
        before = len(execution_steps())
        replies = []
        self.board.resolve_card_review(card, "reject", replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=10))
        self.assertTrue(json.loads(replies[0])["ok"])
        self.assertEqual([d["choice"] for d in self.of(card, "review.decision")], ["reject"])
        self.assertEqual(len(self.of(card, "ticket.rejected")), 1)
        self.assertEqual(len(execution_steps()), before)
        self.assertEqual(self.results(card, "merge"), [])
        replies = []
        self.board.resolve_card_review(card, "retry", replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=10))
        self.assertEqual(json.loads(replies[0])["error"], "no-review-question")

    def test_restart_finishes_rejection_interrupted_after_decision(self):
        from tide_daemon.cardflow.state import fold

        card = self.card(description="Dodaj eksport.\n\nATRAPA: review-requests 3")
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "waiting", timeout=50)
        self.flow.stop()
        journal = self.flow.engine.journal
        append = journal.append

        def interrupted(card_id, kind, **data):
            if kind == "ticket.rejected":
                raise OSError("przerwano po zapisaniu decyzji")
            return append(card_id, kind, **data)

        with mock.patch.object(journal, "append", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "przerwano po zapisaniu decyzji"):
                self.flow.engine.resolve_review(card, "reject")
        before = fold(journal.read(card))
        self.assertEqual(len(before.review_decisions), 1)
        self.assertTrue(before.active())
        self.assertEqual(before.rejected, set())
        self.assertEqual(self.of(card, "ticket.rejected"), [])

        self.flow = self.make_flow()
        self.assertTrue(wait_until(lambda: self.of(card, "ticket.rejected"), timeout=10))
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=10), self.events(card))
        after = fold(self.flow.engine.journal.read(card))
        self.assertFalse(after.active())
        self.assertEqual(len(self.of(card, "ticket.rejected")), 1)
        self.assertEqual(self.results(card, "merge"), [])
        wait_until(lambda: False, timeout=0.5)
        self.assertEqual(len(self.of(card, "ticket.rejected")), 1)
        self.assertTrue(self.flow.engine.resolve_review(card, "reject")["already"])

    def test_explicit_reviewer_and_same_model_validation(self):
        card = self.card()
        bad = self.start(card, reviewer="Sonnet")
        self.assertEqual((bad["ok"], bad["error"]), (False, "bad-reviewer"), bad)
        self.assertEqual(self.events(card), [])
        good = self.start(card, reviewer="Haiku")
        self.assertTrue(good["ok"], good)
        self.wait_column(card, "review")
        review = next(s for s in self.of(card, "step.started") if s["kind"] == "review")
        self.assertEqual(review["executor"], "Haiku")
        with open(os.path.join(review["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            self.assertIn("--model=haiku", json.load(fh)["argv"])

    def test_mixed_provider_rejects_reviewer_from_authors_provider(self):
        card = self.card()
        reply = self.start(card, preferredProvider="mixed", reviewer="Opus")
        self.assertEqual((reply["ok"], reply["error"]), (False, "bad-reviewer-provider"), reply)
        self.assertEqual(self.events(card), [])
