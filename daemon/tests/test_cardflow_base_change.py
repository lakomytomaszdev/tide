"""PT-201: publiczne podglądy/Snapshot, rzeczywisty graf i dowód zachowania wszystkich danych."""
import json
import unittest
from pathlib import Path
from unittest import mock

from .privatebus import wait_until
from .test_cardflow_base import StartBaseSetup
from .test_cardflow_dbus import git
from .test_cardflow_merge_preview import fingerprint
from tide_daemon.cardflow import basechange, baseproposal
from tide_daemon.cardflow.state import fold


class BaseChangeTest(StartBaseSetup):
    def setUp(self):
        super().setUp()
        self.feature_sha = self.feature()
        self.ident = self.card()
        self.started = self.start(self.ident)
        self.assertTrue(self.started["ok"], self.started)

    def call(self, method, request):
        box = []
        getattr(self.board, method)(self.ident, json.dumps(request), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=30), method)
        return json.loads(box[0])

    def preview(self, **request):
        self.flow.commit()  # domykamy wcześniejsze zdarzenia aranżacji, przed pomiarem odczytu
        before = fingerprint(self.repo)
        board = fingerprint(self.root)
        events = self.events(self.ident)
        response = self.call("preview_card_base_change", request or {
            "baseRef": "refs/heads/feature/start", "baseCommit": self.feature_sha})
        self.assertEqual(fingerprint(self.repo), before, "podgląd zmienił git, indeks lub pliki")
        self.assertEqual(fingerprint(self.root), board, "podgląd zmienił pytanie lub wyniki")
        self.assertEqual(self.events(self.ident), events)
        return response

    def ask(self):
        journal = self.flow.engine.journal
        journal.append(self.ident, "step.started", step="ask", kind="ticket", ticket=self.started["ticket"],
                       branch=self.started["branch"], worktree=self.started["worktree"])
        journal.append(self.ident, "step.finished", step="ask", result="question", question="<b>Czy baza?</b>",
                       kept=["note.md"], baseProposal={"valid": True, "ref": "refs/heads/feature/start",
                                                      "baseCommit": self.feature_sha,
                                                      "evidence": "<b>historia pliku</b>"})

    def commit(self, path, name="code.txt", message="work"):
        file = Path(path, name)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("wkład\n", encoding="utf-8")
        git(path, "add", name)
        git(path, "commit", "-qm", message)
        return git(path, "rev-parse", "HEAD").strip()

    def test_empty_qualified_with_finished_metadata_and_ignored_data_token(self):
        self.ask()
        path = self.started["cardWorktree"]
        ignored = Path(path, "ignored.bin")
        git(path, "config", "core.excludesFile", str(Path(self.base, "excludes")))
        Path(self.base, "excludes").write_text("ignored.bin\n")
        ignored.write_bytes(b"1234")
        initial = self.preview(step="ask")
        self.assertTrue(initial["canChange"], initial)
        self.assertTrue(initial["canExecute"])
        self.assertEqual(initial["oldBase"]["baseCommit"], self.master)
        self.assertEqual(initial["newBase"]["baseCommit"], self.feature_sha)
        self.assertEqual(initial["baseVersion"], 1)
        self.assertEqual(initial["decisionStep"], "ask")
        self.assertEqual(initial["evidence"], "<b>historia pliku</b>")
        self.assertEqual(len(initial["branches"]), 2)
        self.assertIn("ignored.bin", initial["branches"][0]["ignored"])
        self.assertEqual(initial["previewId"], self.preview(step="ask")["previewId"])
        ignored.write_bytes(b"5678")
        self.assertNotEqual(initial["previewId"], self.preview(step="ask")["previewId"])
        self.restart()
        self.board.publish()
        shown = self.shown(self.ident)
        self.assertTrue(shown["derived"]["flow"]["baseDecisionPending"])
        self.assertIn("Czy baza?", shown["derived"]["flow"]["decisions"][0]["happened"])
        # Zapis domknięcia na bazowym SHA jest metadanymi, bez zaakceptowanego commita ticketu.
        self.flow.engine.journal.append(self.ident, "card.closed", commit=self.master, summary="bez wkładu")
        self.assertTrue(self.preview(step="ask")["canChange"])

    def test_user_initiative_persists_equivalent_decision_cancel_and_keep(self):
        before = fingerprint(self.repo)
        response = self.call("request_card_base_change", {"baseRef": "refs/heads/feature/start",
                                                         "baseCommit": self.feature_sha})
        self.assertTrue(response["ok"], response)
        self.assertEqual(fingerprint(self.repo), before)
        self.assertTrue(response["preview"]["canChange"])
        decision = response["preview"]["decisionStep"]
        self.restart()
        self.board.publish()
        shown = self.shown(self.ident)
        self.assertTrue(shown["derived"]["flow"]["baseDecisionPending"])
        options = shown["derived"]["flow"]["decisions"][0]["options"]
        self.assertEqual([o["params"]["baseDecision"] for o in options], ["keep", "change"])
        # Anulowanie/ponowny odczyt nie rozstrzyga decyzji ani nie rusza gita.
        self.assertEqual(self.preview(step=decision)["decisionStep"], decision)
        self.assertEqual(fingerprint(self.repo), before)
        self.assertFalse(self.call("resolve_card_step", {"choice": "answer", "step": decision,
                                                       "answer": "Zachowaj refs/heads/master"})["ok"])
        kept = self.call("resolve_card_step", {"choice": "answer", "step": decision, "baseDecision": "keep"})
        self.assertTrue(kept["ok"], kept)
        self.assertFalse(baseproposal.pending(fold(self.events(self.ident))))
        self.assertEqual(fingerprint(self.repo), before)

    def test_large_ignored_trees_are_compact_and_no_file_contents_are_hashed(self):
        self.ask()
        excludes = Path(self.base, "excludes")
        excludes.write_text("bin/\nobj/\nnode_modules/\ncache-*.bin\n")
        for path in (self.started["cardWorktree"], self.started["worktree"]):
            git(path, "config", "core.excludesFile", str(excludes))
            for name in ("bin", "obj", "node_modules"):
                directory = Path(path, name, "nested")
                directory.mkdir(parents=True)
                for i in range(100):
                    (directory / str(i)).write_bytes(b"artefakt")
                with (directory / "large.bin").open("wb") as stream:
                    stream.truncate(256 * 1024 * 1024)
            for i in range(40):
                Path(path, f"cache-{i:02}.bin").write_bytes(b"cache")
        # Publiczna ścieżka wykonuje również drugi odczyt pod blokadą. Żaden z nich
        # nie może użyć hashowania treści, także dla indeksu i lokalnych zmian.
        with mock.patch.object(basechange, "file_stamp", create=True,
                               side_effect=AssertionError("odczyt treści w podglądzie")), \
                mock.patch("tide_daemon.cardflow.mergepreview.file_stamp",
                           side_effect=AssertionError("odczyt treści checkoutu")):
            response = self.call("preview_card_base_change", {"step": "ask"})
        self.assertTrue(response["canChange"], response)
        for row in response["branches"]:
            self.assertEqual(row["ignoredCount"], 43)
            self.assertLessEqual(len(row["ignored"]), 20)
            self.assertTrue(row["ignoredTruncated"])
            self.assertIn("bin/", row["ignored"])
            self.assertNotIn("nested", str(row["ignored"]))
        self.assertLess(len(json.dumps(response)), 16000)
        self.assertIn("metadane", response["localStatePolicy"])

    def test_local_metadata_budget_exhaustion_refuses_without_partial_qualification(self):
        self.ask()
        excludes = Path(self.base, "excludes")
        excludes.write_text("cache-*.bin\n")
        path = self.started["cardWorktree"]
        git(path, "config", "core.excludesFile", str(excludes))
        for i in range(30):
            Path(path, f"cache-{i}.bin").write_bytes(b"cache")
        with mock.patch.object(basechange, "MAX_LOCAL_ENTRIES", 20):
            response = self.preview(step="ask")
        self.assertFalse(response["canChange"])
        self.assertFalse(response["canExecute"])
        self.assertIn("unknown-local-state", {r["reason"] for r in response["blockedReasons"]})
        self.assertIn("limit odczytu metadanych", str(response["blockedReasons"]))
        self.assertEqual(response["decisionStep"], "ask")

    def test_ignored_directory_metadata_change_during_second_pass_invalidates_preview(self):
        path = self.started["cardWorktree"]
        excludes = Path(self.base, "excludes")
        excludes.write_text("bin/\n")
        git(path, "config", "core.excludesFile", str(excludes))
        directory = Path(path, "bin")
        directory.mkdir()
        (directory / "old").write_bytes(b"artefakt")
        original = basechange.preview
        calls = 0

        def changing(*args):
            nonlocal calls
            result = original(*args)
            calls += 1
            if calls == 1:
                (directory / "new").write_bytes(b"nowy artefakt")
            return result

        with mock.patch.object(basechange, "preview", side_effect=changing):
            # Tutaj zewnętrzny zapis jest celową częścią scenariusza, nie mutacją usługi.
            response = self.call("preview_card_base_change", {"baseRef": "refs/heads/feature/start"})
        self.assertEqual(calls, 2)
        self.assertFalse(response["canChange"])
        self.assertEqual(response["previewId"], "")
        self.assertIn("state-changed", {r["reason"] for r in response["blockedReasons"]})

    def test_commits_documents_code_wip_and_ticket_are_refused_with_sha_and_sketch(self):
        self.ask()
        for path, name, message in ((self.started["cardWorktree"], "docs/agents/plans/plan.md", "docs(plan): plan"),
                                     (self.started["cardWorktree"], "code.txt", "feature"),
                                     (self.started["worktree"], "wip.txt", "WIP: stopped"),
                                     (self.started["worktree"], "ticket.txt", "ticket work")):
            sha = self.commit(path, name, message)
            response = self.preview(step="ask")
            self.assertFalse(response["canChange"], response)
            self.assertIn(sha, str(response["branches"]))
            self.assertIn("contribution", {r["reason"] for r in response["blockedReasons"]})
            self.assertIn("Zachowaj bazę, aby kontynuować", response["instruction"])
            self.assertTrue(response["followup"]["available"])
            self.assertTrue(Path(response["followup"]["path"]).is_file())
            self.assertIn("K-49", response["scope"])
            self.assertIn("bez opublikowanej karty", response["followup"]["message"])
        with mock.patch.object(basechange, "FOLLOWUP", Path(self.base, "missing.md")):
            response = self.preview(step="ask")
            self.assertFalse(response["followup"]["available"])
            self.assertEqual(response["followup"]["path"], "")

    def test_previous_order_evidence_is_explicitly_marked_and_remains_conservative(self):
        past = [{"seq": 1, "type": "card.started", "entry": "now"},
                {"seq": 2, "type": "step.started", "step": "old-step", "kind": "ticket",
                 "branch": "old/order", "worktree": self.started["cardWorktree"]},
                {"seq": 3, "type": "step.finished", "step": "old-step", "result": "done"},
                {"seq": 4, "type": "result", "action": "plan-commit", "ok": True, "commit": "d" * 40}]
        events = past + [{**e, "seq": e["seq"] + len(past)} for e in self.events(self.ident)]
        with mock.patch.object(self.flow.engine.journal, "read", return_value=events):
            response = self.call("preview_card_base_change", {"baseRef": "refs/heads/feature/start"})
        self.assertFalse(response["canChange"])
        recorded = next(r for r in response["blockedReasons"] if r["reason"] == "recorded-contribution")
        self.assertTrue(recorded["previousOrder"])
        self.assertIn("Poprzednie zlecenie", recorded["message"])
        branch = next(b for b in response["branches"] if b["branch"] == "old/order")
        self.assertTrue(branch["previousOrder"])
        self.assertFalse(response["branches"][0]["previousOrder"])
        self.assertTrue(next(p for p in response["processes"] if p["step"] == "old-step")["previousOrder"])

    def test_local_changes_each_kind_and_token_same_size_edit(self):
        path = self.started["worktree"]
        Path(path, "untracked.txt").write_text("1234")
        first = self.preview()
        self.assertFalse(first["canChange"])
        Path(path, "untracked.txt").write_text("5678")
        self.assertNotEqual(first["previewId"], self.preview()["previewId"])
        git(path, "add", "untracked.txt")
        Path(path, "CLAUDE.md").write_text("zmiana poza indeksem")
        changed = self.preview()
        self.assertFalse(changed["canChange"])
        changes = changed["branches"][1]["localChanges"]
        self.assertTrue(changes["staged"])
        self.assertTrue(changes["unstaged"])

    def test_active_and_unknown_process_finished_before_exit_then_wip(self):
        self.ask()
        self.flow.engine.journal.append(self.ident, "step.started", step="parallel", kind="ticket", unit="worker",
                                       branch=self.started["branch"], worktree=self.started["worktree"])
        for state in ("active", "unknown"):
            with mock.patch("tide_daemon.cardflow.engine.stepstop.unit_state", return_value=state), \
                    mock.patch.object(self.flow.engine, "_stop") as stop:
                r = self.preview()
                self.assertFalse(r["canChange"])
                self.assertIn("istniejącej akcji stop", str(r["blockedReasons"]))
                stop.assert_not_called()
        self.flow.engine.journal.append(self.ident, "step.finished", step="parallel", result="done")
        with mock.patch("tide_daemon.cardflow.engine.stepstop.unit_state", return_value="active"):
            self.assertFalse(self.preview()["canChange"])
        with mock.patch("tide_daemon.cardflow.engine.stepstop.unit_state", return_value="inactive"):
            self.assertTrue(self.preview()["canChange"])
            self.commit(self.started["worktree"], "stopped.txt", "WIP: stopped")
            self.assertFalse(self.preview()["canChange"])

    def test_unknown_boundary_foreign_merge_and_changed_ref(self):
        path = self.started["cardWorktree"]
        git(self.repo, "branch", "foreign", self.feature_sha)
        git(path, "merge", "--no-ff", "-qm", "foreign merge", "foreign")
        response = self.preview()
        self.assertFalse(response["canChange"])
        self.assertIn("merge", {r["reason"] for r in response["blockedReasons"]})
        # Punkt startu gałęzi nie jest przodkiem nowego, obcego korzenia.
        git(path, "checkout", "--orphan", "alien")
        self.commit(path, "alien.txt", "unrelated")
        git(self.repo, "update-ref", "refs/heads/" + self.started["cardBranch"], "alien")
        self.assertIn("changed-ref", {r["reason"] for r in self.preview()["blockedReasons"]})
        self.flow.engine.journal.append(self.ident, "card.started", entry="now", repo=self.repo)
        unknown = self.preview()
        self.assertFalse(unknown["canChange"])
        self.assertIn("unknown-base", {r["reason"] for r in unknown["blockedReasons"]})

    def test_remote_validation_no_head_fallback_and_base_ref_movement(self):
        git(self.repo, "update-ref", "refs/remotes/team/topic", self.feature_sha)
        r = self.preview(baseRef="refs/remotes/team/topic", baseCommit=self.feature_sha)
        self.assertTrue(r["canChange"], r)
        for ref in ("HEAD", "master", "refs/tags/x", "refs/remotes/team/HEAD", "refs/heads/missing"):
            self.assertFalse(self.preview(baseRef=ref)["ok"])
        self.assertFalse(self.preview(baseRef="refs/remotes/team/topic")["ok"])
        self.assertFalse(self.preview(baseRef="refs/heads/feature/start", baseCommit=self.master)["ok"])
        git(self.repo, "checkout", "-q", "feature/start")
        git(self.repo, "branch", "-f", "master", self.feature_sha)
        r = self.preview()
        self.assertFalse(r["canChange"])
        self.assertEqual(r["oldBase"]["baseCommit"], self.master)
        self.assertIn("changed-base-ref", {r["reason"] for r in r["blockedReasons"]})

    def test_missing_creation_proof_and_preview_state_change_are_refused(self):
        journal = self.flow.engine.journal
        journal.append(self.ident, "result", action="ticket-branch", key="unproven", ok=True, existing=True,
                       base=self.master, ticket="PT-X", branch=self.started["branch"], worktree=self.started["worktree"])
        self.assertIn("unknown-boundary", {r["reason"] for r in self.preview()["blockedReasons"]})
        original = basechange.preview
        count = 0

        def moving(*args):
            nonlocal count
            count += 1
            result = original(*args)
            if count == 1:
                result["previewId"] = "stary odcisk"
            return result

        with mock.patch.object(basechange, "preview", side_effect=moving):
            changed = self.preview()
        self.assertFalse(changed["canChange"])
        self.assertEqual(changed["previewId"], "")
        self.assertIn("state-changed", {r["reason"] for r in changed["blockedReasons"]})

    def test_active_map_base_version_and_process_changes_invalidate_identifier(self):
        initial = self.preview()
        journal = self.flow.engine.journal
        journal.append(self.ident, "card.base-changed", previousBaseVersion=1, baseVersion=2,
                       base={"baseRef": "refs/heads/master", "baseBranch": "master", "baseCommit": self.master})
        versioned = self.preview()
        self.assertEqual(versioned["baseVersion"], 2)
        self.assertNotEqual(versioned["previewId"], initial["previewId"])
        journal.append(self.ident, "step.started", step="process", kind="ticket", unit="process",
                       branch=self.started["branch"], worktree=self.started["worktree"])
        journal.append(self.ident, "step.finished", step="process", result="done")
        with mock.patch("tide_daemon.cardflow.engine.stepstop.unit_state", return_value="inactive"):
            inactive = self.preview()
        with mock.patch("tide_daemon.cardflow.engine.stepstop.unit_state", return_value="active"):
            active = self.preview()
        self.assertNotEqual(active["previewId"], inactive["previewId"])
        self.assertFalse(active["canChange"])

    def test_nested_untracked_repository_content_is_in_identifier(self):
        nested = Path(self.started["worktree"], "nested")
        nested.mkdir()
        git(str(nested), "init", "-q")
        Path(nested, "local.txt").write_text("1234")
        before = self.preview()
        self.assertFalse(before["canChange"])
        Path(nested, "local.txt").write_text("5678")
        self.assertNotEqual(before["previewId"], self.preview()["previewId"])

    def test_documents_after_light_import_and_full_planning_are_refused_without_replay(self):
        from .test_cardflow_import_plan import PlanImportTest
        engine = self.flow.engine
        engine.terminal = mock.Mock()

        def prepare_branch(card, st, *_args, **_kwargs):
            # Sam proces planujący nie jest przedmiotem testu kwalifikacji.
            return engine._planning_branch(card, st, engine._project(card)[1])

        for entry, mode in (("planning", "light"), ("import", ""), ("planning", "full")):
            with self.subTest(entry=entry, mode=mode):
                self.ident = self.card(title=f"Dokumenty {entry} {mode}")
                request = {"entry": entry, "mode": mode}
                if entry == "import":
                    request["planPath"] = PlanImportTest.source(self, self.ident, old=True,
                                                               name="2026-11-05-" + self.ident.lower() + ".md")
                with mock.patch.object(engine, "_planning_launch", side_effect=prepare_branch):
                    started = self.start(self.ident, **request)
                self.assertTrue(started["ok"], started)
                st = fold(self.events(self.ident))
                branch = next(r for (action, _), r in st.results.items() if action == "card-branch")
                engine.journal.append(self.ident, "step.started", step="planning", kind="planning",
                                      branch=branch["branch"], worktree=branch["worktree"])
                engine.journal.append(self.ident, "step.finished", step="planning", result="done")
                self.assertTrue(self.preview()["canChange"], "sam wynik na bazowym SHA jest metadanymi")
                sha = self.commit(branch["worktree"], "docs/agents/plans/accepted.md", "docs(plan): accepted plan")
                response = self.preview()
                self.assertFalse(response["canChange"])
                self.assertIn(sha, str(response["branches"]))
                self.assertIn("contribution", {r["reason"] for r in response["blockedReasons"]})


class FollowupArtifactTest(unittest.TestCase):
    def test_approved_sketch_scope_links_and_installation_copy(self):
        repo = Path(__file__).resolve().parents[2]
        sketch = repo / "docs/agents/plans/followups/replay-code.md"
        content = sketch.read_text()
        for text in ("K-50-replay-code", "K-49", "dokumentów", "kod", "WIP", "graf", "poza v1"):
            self.assertIn(text, content)
        sources = (("docs/agents/plans/2026-11-05-k-50-flow.md", "followups/replay-code.md"),
                   ("docs/agents/tickets/k-50-flow/pt-201.md", "../../plans/followups/replay-code.md"),
                   ("docs/agents/tickets/k-50-flow/pt-208.md", "../../plans/followups/replay-code.md"))
        for source, target in sources:
            document = repo / source
            self.assertIn("](" + target + ")", document.read_text())
            self.assertEqual((document.parent / target).resolve(), sketch)
        # Szkic działa samodzielnie także w kopii usługi, bez martwego odnośnika do planu.
        self.assertNotIn("](../", content)
        self.assertNotIn("2026-11-05-k-50-flow.md", (repo / "daemon/install.sh").read_text())
        self.assertIn('cp "$src/../docs/agents/plans/followups/replay-code.md"',
                      (repo / "daemon/install.sh").read_text())
