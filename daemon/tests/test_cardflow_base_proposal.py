"""PT-208: wynik kroku, publiczne decyzje/Snapshot i równoległe wyniki bez integracji."""
import json
import os
from unittest import mock

from .privatebus import wait_until
from .test_cardflow_base import StartBaseSetup
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git
from tide_daemon.cardflow.engine import FlowDeferred
from tide_daemon.cardflow.state import fold
from tide_daemon.cardflow import baseproposal


class BaseProposalTest(StartBaseSetup):
    def prepare(self, proposal=None):
        self.feature()
        card = self.card()
        reply = self.start(card)
        self.assertTrue(reply["ok"], reply)
        engine = self.flow.engine
        step_dir = os.path.join(self.base, card + ".question.step")
        os.makedirs(os.path.join(step_dir, "out"))
        engine.journal.append(card, "step.started", step="ask", ticket="PT-1", kind="ticket",
                              worktree=reply["worktree"], branch=reply["branch"], stepDir=step_dir)
        result = {"result": "question", "question": "Czy baza jest właściwa?", "options": [{"title": "Stara opcja"}]}
        if proposal is not None:
            result["baseProposal"] = proposal
        with open(os.path.join(step_dir, "out", "result.json"), "w") as fh:
            json.dump(result, fh)
        engine._finish(card, fold(engine.journal.read(card)).steps["ask"], exit_code=0)
        self.board.publish()
        return card, reply

    def decide(self, card, **request):
        box = []
        self.board.resolve_card_step(card, json.dumps({"step": "ask", **request}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        return json.loads(box[0])

    def decision(self, card):
        self.board.publish()
        return self.shown(card)["derived"]["flow"]["decisions"][0]

    def test_old_question_and_invalid_proposals_keep_options_and_free_text(self):
        for proposal in (None, {"ref": "feature/start", "evidence": "brak pliku"},
                         {"ref": "refs/heads/missing", "evidence": "sprawdzono"},
                         {"ref": "refs/heads/../master", "evidence": "sprawdzono"},
                         {"ref": "refs/heads/feature/start", "evidence": ""}, "zły typ"):
            with self.subTest(proposal=proposal):
                # Każda propozycja jest sprawdzana w repozytorium tej karty.
                if not git(self.repo, "branch", "--list", "feature/start").strip():
                    self.feature()
                with mock.patch.object(self, "feature"):
                    card, _ = self.prepare(proposal)
                d = self.decision(card)
                self.assertEqual(d["options"][0]["title"], "Stara opcja")
                self.assertTrue(d["freeText"])
                self.assertFalse(baseproposal.pending(fold(self.flow.engine.journal.read(card))))
                if proposal is not None:
                    self.assertIn("Nieprawidłowa propozycja", d["why"])
                self.assertTrue(self.decide(card, choice="answer", answer="Zwykła odpowiedź")["ok"])

    def test_valid_remote_candidate_is_resolved_locally_without_fetch(self):
        git(self.repo, "update-ref", "refs/remotes/team/topic", self.master)
        card, _ = self.prepare({"ref": "refs/remotes/team/topic", "evidence": "Lokalny ref remote"})
        proposal = self.of(card, "step.finished")[-1]["baseProposal"]
        self.assertTrue(proposal["valid"])
        self.assertEqual(proposal["baseCommit"], self.master)

    def test_candidate_from_another_project_is_only_information(self):
        other_repo = os.path.join(self.base, "other")
        os.makedirs(other_repo)
        git(other_repo, "init", "-q")
        git(other_repo, "commit", "--allow-empty", "-qm", "Initial context")
        git(other_repo, "branch", "only-elsewhere")
        card, _ = self.prepare({"ref": "refs/heads/only-elsewhere", "evidence": "Istnieje w drugim projekcie"})
        self.assertFalse(self.of(card, "step.finished")[-1]["baseProposal"]["valid"])
        self.assertTrue(self.decision(card)["freeText"])

    def test_parallel_result_restart_change_wait_and_keep(self):
        evidence = "<b>Sprawdzono historię feature.txt</b>"
        card, reply = self.prepare({"ref": "refs/heads/feature/start", "evidence": evidence})
        engine = self.flow.engine
        d = self.decision(card)
        self.assertEqual([o["title"] for o in d["options"]],
                         ["Zachowaj refs/heads/master", "Zmień na refs/heads/feature/start"])
        self.assertIn(evidence, d["happened"])
        self.assertIn("K-50-replay-code", d["why"])
        self.assertIn("WIP", d["why"])
        self.assertFalse(d["freeText"])
        self.assertEqual(self.start(card)["error"], "base-decision")
        # Nazwa opcji ani tekst odpowiedzi nie zastępują strukturalnej decyzji.
        self.assertFalse(self.decide(card, choice="answer", answer=d["options"][0]["title"])["ok"])
        parallel_dir = os.path.join(self.base, "parallel.step")
        os.makedirs(os.path.join(parallel_dir, "out"))
        engine.journal.append(card, "step.started", step="parallel", ticket="PT-2", kind="ticket",
                              stepDir=parallel_dir, unit="parallel", worktree="/test/parallel")
        self.assertEqual(self.decide(card, choice="transfer", executor="Opus")["error"], "base-decision")
        with open(os.path.join(parallel_dir, "out", "note.md"), "w") as fh:
            fh.write("Zachowany wynik równoległy")
        with open(os.path.join(parallel_dir, "out", "result.json"), "w") as fh:
            json.dump({"result": "done", "notes": ["note.md"]}, fh)
        with mock.patch("tide_daemon.cardflow.engine.stepstop.unit_state", return_value="active"), \
                mock.patch.object(engine, "_stop") as stop, mock.patch.object(engine, "_advance") as advance:
            engine._tick_card(card, fold(engine.journal.read(card)))
            stop.assert_not_called()
            advance.assert_not_called()
        with mock.patch("tide_daemon.cardflow.engine.stepstop.unit_state", return_value="inactive"), \
                mock.patch("tide_daemon.cardflow.engine.stepstop.read_exit_code", return_value=0), \
                mock.patch.object(engine, "_advance") as advance:
            engine._tick_card(card, fold(engine.journal.read(card)))
            advance.assert_not_called()
        st = fold(engine.journal.read(card))
        self.assertEqual(st.finished["parallel"]["result"], "done")
        self.assertIn("note.md", st.finished["parallel"]["kept"])
        self.assertFalse(self.results(card, "merge"))
        work = mock.Mock()
        with self.assertRaises(FlowDeferred):
            engine._act(card, "merge", "PT-2", work)
        work.assert_not_called()
        from tide_daemon.cardflow.gitops import lock_for
        lock = lock_for(self.repo)
        self.assertTrue(lock.acquire(blocking=False), "oczekiwanie nie trzyma blokady repozytorium")
        lock.release()
        original = git(self.repo, "rev-parse", reply["cardBranch"]).strip()
        self.assertTrue(self.decide(card, **d["options"][1]["params"])["waiting"])
        self.assertEqual(self.of(card, "step.decision"), [])
        self.restart()
        engine = self.flow.engine
        d = self.decision(card)
        self.assertIn("baza nie została zmieniona", d["why"])
        self.assertTrue(self.shown(card)["derived"]["flow"]["baseDecisionPending"])
        # Pytanie nie trzyma blokady repozytorium: inne karty z tego samego repo mogą wystartować.
        other = self.card(title="Inna karta")
        self.assertTrue(self.start(other)["ok"])
        self.assertEqual(git(self.repo, "rev-parse", reply["cardBranch"]).strip(), original)
        kept = self.decide(card, **d["options"][0]["params"])
        self.assertTrue(kept["ok"], kept)
        st = fold(engine.journal.read(card))
        self.assertFalse(baseproposal.pending(st))
        self.assertEqual(st.step_decisions["ask"]["baseDecision"], "keep")
        with mock.patch.object(engine, "_resume_step") as resume, mock.patch.object(engine, "_review") as review:
            st.last_step = "ask"
            engine._advance(card, st)
            resume.assert_called_once()
            st.last_step = "parallel"
            engine._advance(card, st)
            review.assert_called_once()
        self.assertEqual(st.finished["parallel"]["result"], "done")

    def test_context_contains_full_base_or_unknown_and_safety_contract(self):
        for base, expected in (({}, "unknown"), ({"baseRef": "refs/heads/topic", "baseCommit": self.master}, self.master)):
            text = baseproposal.context(base)
            self.assertIn(expected, text)
            self.assertIn("baseProposal", text)
            self.assertIn("Sam brak pliku", text)
            self.assertIn("checkout/rebase/reset", text)

    def test_conflict_finished_during_base_decision_is_cleaned_up_after_keep(self):
        self.feature()
        card = self.card()
        reply = self.start(card)
        self.assertTrue(reply["ok"], reply)
        engine = self.flow.engine
        ticket = reply["ticket"]
        conflict_dir = os.path.join(self.base, "conflict.step")
        os.makedirs(os.path.join(conflict_dir, "out"))
        engine.journal.append(card, "intent", action="merge", key=ticket, cardTip=self.master)
        engine.journal.append(card, "result", action="merge", key=ticket, ok=False, error="konflikt")
        engine.journal.append(card, "step.started", step="conflict", ticket=ticket, kind="conflict",
                              stepDir=conflict_dir, worktree=reply["cardWorktree"], reviewTip=self.master)
        # Pytanie równoległego ticketu pojawia się, gdy rozwiązywanie konfliktu już trwa.
        engine.journal.append(card, "step.started", step="ask", ticket="PT-2", kind="ticket")
        engine._finish(card, fold(engine.journal.read(card)).steps["ask"], forced={
            "result": "question", "question": "Czy zachować bazę?",
            "baseProposal": {"ref": "refs/heads/feature/start", "evidence": "Sprawdzono historię"}})
        with open(os.path.join(conflict_dir, "out", "result.json"), "w") as fh:
            json.dump({"result": "done"}, fh)
        # Sprawdzenie istniejącego scalenia jest odczytem; samej piaskownicy gita nie testujemy tutaj.
        with mock.patch.object(engine.git, "merged_tip", return_value=self.master) as merged_tip, \
                mock.patch.object(engine.git, "remove_worktree") as cleanup, \
                mock.patch.object(engine.git, "merge") as merge:
            engine._finish(card, fold(engine.journal.read(card)).steps["conflict"], exit_code=0)
            engine._tick_card(card, fold(engine.journal.read(card)))
            merged_tip.assert_called_once_with(self.repo, reply["cardBranch"], reply["branch"],
                                               reply["cardWorktree"], self.master, self.master)
            cleanup.assert_not_called()
            merge.assert_not_called()
        st = fold(engine.journal.read(card))
        self.assertTrue(baseproposal.pending(st))
        self.assertEqual(st.finished["conflict"]["result"], "done")
        self.assertTrue(st.results[("merge", ticket)]["ok"])
        self.assertTrue(st.results[("merge", ticket)]["resolved"])
        self.assertEqual(st.results[("merge", ticket)]["commit"], self.master)
        self.assertFalse(self.results(card, "cleanup"))
        self.restart()
        engine = self.flow.engine
        self.assertTrue(self.decide(card, choice="answer", baseDecision="keep")["ok"])
        with mock.patch.object(engine.git, "remove_worktree", return_value={"removed": True}) as cleanup, \
                mock.patch.object(engine.git, "merge") as merge, \
                mock.patch.object(engine, "_resume_step"):
            engine._tick_card(card, fold(engine.journal.read(card)))
            engine._tick_card(card, fold(engine.journal.read(card)))
            cleanup.assert_called_once_with(self.repo, reply["branch"], reply["worktree"])
            merge.assert_not_called()
        self.assertTrue(self.results(card, "cleanup")[-1]["ok"])
        self.assertEqual(len([r for r in self.results(card, "merge") if r.get("ok")]), 1)


class PromptBaseTest(FlowInProcess):
    def test_ticket_and_light_planning_receive_pinned_context(self):
        for entry, word in (("now", "pytanie"), ("planning", "plan-question")):
            card = self.card(description="ATRAPA: " + word)
            self.assertTrue(self.start(card, entry=entry, **({"mode": "light"} if entry == "planning" else {}))["ok"])
            self.wait_column(card, "waiting")
            step = self.of(card, "step.started")[0]
            with open(os.path.join(step["stepDir"], "out", "fake-call.json")) as fh:
                prompt = json.load(fh)["prompt"]
            self.assertIn("Bieżąca baza karty: refs/heads/master", prompt)
            self.assertIn("Przypięty SHA bazy: " + self.master, prompt)
            self.assertIn('"baseProposal"', prompt)
            self.assertIn("K-50-replay-code", prompt)
