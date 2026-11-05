"""PT-199: publiczny odczyt /Board na prawdziwych repozytoriach, pełny zakres i brak mutacji."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import FlowCase, git
from .test_cardflow_gitops import LocalStepGit
from tide_daemon.cardflow import gitops
from tide_daemon.tasks.stepstop import StepGit, sandbox_available
from tide_daemon.worktrees.git import Result


def fingerprint(repo):
    """Obiekty próbnego drzewa mogą przybyć; reszta gita i wszystkie pliki muszą pozostać identyczne."""
    paths = [repo] + [line[9:] for line in git(repo, "worktree", "list", "--porcelain").splitlines()
                      if line.startswith("worktree ") and line[9:] != repo]
    files = {}
    for root in paths:
        for folder, dirs, names in os.walk(root):
            if folder == os.path.join(repo, ".git"):
                dirs[:] = [d for d in dirs if d != "objects"]
            for name in names:
                path = os.path.join(folder, name)
                files[path] = (os.lstat(path).st_mode, os.readlink(path) if os.path.islink(path)
                               else Path(path).read_bytes())
    return {"head": git(repo, "rev-parse", "HEAD"), "refs": git(repo, "show-ref"),
            "trees": git(repo, "worktree", "list", "--porcelain"), "files": files}


class PreviewScenarios:
    def make_flow(self):
        # Bez automatycznego ticka, ale monitor nadal przyjmuje publiczne żądania podglądu.
        from tide_daemon.cardflow_monitor import CardFlowMonitor
        with mock.patch.object(CardFlowMonitor, "start"):
            return super().make_flow()

    def setUp(self):
        super().setUp()
        self.card_id = self.card()
        self.source = "card/k-1-preview"
        self.card_tree = os.path.join(self.base, "ws", "card")
        self.write(self.repo, "old\tname.txt", "rename\n")
        self.write(self.repo, "conflict.txt", "base\n")
        self.commit(self.repo, "base")
        git(self.repo, "worktree", "add", "-qb", self.source, self.card_tree)
        journal = self.flow.engine.journal
        journal.append(self.card_id, "card.started", entry="now", executor="Sonnet", repo=self.repo)
        journal.append(self.card_id, "result", action="card-branch", key="card", ok=True,
                       branch=self.source, worktree=self.card_tree)
        journal.append(self.card_id, "card.closed", summary="Gotowe")
        self.assertTrue(self.flow.engine.derived({"id": self.card_id, "column": "review"})["flow"]["closed"])

    @staticmethod
    def write(root, path, text):
        Path(root, path).write_text(text, encoding="utf-8")

    @staticmethod
    def commit(root, subject):
        git(root, "add", "-A")
        git(root, "commit", "-qm", subject)
        return git(root, "rev-parse", "HEAD").strip()

    def preview(self, request="{}"):
        before = fingerprint(self.repo)
        events = self.flow.engine.journal.read(self.card_id)
        column = self.store.load().cards[int(self.card_id[2:])].column
        box = []
        self.board.preview_card_merge(self.card_id, request, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=30), "PreviewCardMerge bez odpowiedzi")
        result = json.loads(box[0])
        self.assertEqual(before, fingerprint(self.repo), "podgląd zmienił repozytorium")
        self.assertEqual(events, self.flow.engine.journal.read(self.card_id), "podgląd dopisał zdarzenie")
        self.assertEqual(column, self.store.load().cards[int(self.card_id[2:])].column)
        return result

    def test_actual_merge_tree_range_rename_and_dirty_files_are_unchanged(self):
        self.write(self.repo, "target-only.txt", "zmiana wyłącznie na celu\n")
        target = self.commit(self.repo, "target")
        git(self.card_tree, "mv", "old\tname.txt", "new\nname.txt")
        self.write(self.card_tree, "source.txt", "wnoszona zmiana\n")
        source = self.commit(self.card_tree, "source")
        self.write(self.repo, "staged.txt", "indeks\n")
        git(self.repo, "add", "staged.txt")
        self.write(self.repo, "conflict.txt", "niezapisana zmiana\n")
        self.write(self.repo, "untracked.txt", "nieśledzony\n")
        self.write(self.card_tree, "untracked.txt", "też nieśledzony\n")
        r = self.preview()
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["canMerge"], r)
        self.assertFalse(r["readyToMerge"])
        self.assertEqual(r["localCollisionCount"], 0)
        self.assertEqual({b["reason"] for b in r["blockedReasons"]}, {"source-dirty"})
        self.assertTrue(r["previewToken"])
        self.assertTrue(r["warnings"])
        self.assertEqual((r["sourceRef"], r["sourceCommit"], r["targetRef"], r["targetCommit"]),
                         ("refs/heads/" + self.source, source, "refs/heads/master", target))
        self.assertEqual(r["commits"], [{"sha": source, "subject": "source"}])
        self.assertEqual(r["commitCount"], 1)
        self.assertEqual(r["fileCount"], 2)
        self.assertIn({"status": "R100", "oldPath": "old\tname.txt", "newPath": "new\nname.txt"}, r["files"])
        self.assertNotIn("target-only.txt", str(r["files"]))
        self.assertEqual(git(self.repo, "show", r["resultTree"] + ":target-only.txt"), "zmiana wyłącznie na celu\n")
        self.assertEqual(r["targetWorktree"], self.repo)
        self.assertTrue(r["targetWorktreeExists"])

    def test_full_lists_beyond_old_summary_limits(self):
        for i in range(45):
            self.write(self.card_tree, f"commit-{i}.txt", f"{i}\n")
            self.commit(self.card_tree, f"commit {i}")
        for i in range(420):
            self.write(self.card_tree, f"file-{i}.txt", f"{i}\n")
        last = self.commit(self.card_tree, "many files")
        r = self.preview()
        self.assertTrue(r["canMerge"], r)
        expected = git(self.repo, "rev-list", f"master..{self.source}").splitlines()
        self.assertEqual(r["commitCount"], 46)
        self.assertEqual([c["sha"] for c in r["commits"]], expected)
        self.assertEqual(r["commits"][0]["sha"], last)
        self.assertEqual(r["fileCount"], 465)
        self.assertEqual(len(r["files"]), 465)

    def test_conflict_is_a_valid_preview_with_all_paths(self):
        self.write(self.repo, "conflict.txt", "cel\n")
        self.commit(self.repo, "target")
        self.write(self.card_tree, "conflict.txt", "źródło\n")
        self.commit(self.card_tree, "source")
        r = self.preview()
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["canMerge"])
        self.assertEqual(r["reason"], "conflict")
        self.assertEqual(r["conflicts"], ["conflict.txt"])
        self.assertEqual(r["conflictCount"], 1)
        self.assertTrue(r["resultTree"])

    def test_already_merged_has_empty_range(self):
        self.write(self.card_tree, "source.txt", "source\n")
        self.commit(self.card_tree, "source")
        git(self.repo, "merge", "--no-edit", self.source)
        r = self.preview()
        self.assertTrue(r["canMerge"], r)
        self.assertEqual((r["commitCount"], r["fileCount"], r["conflictCount"]), (0, 0, 0))
        self.assertEqual(r["resultTree"], git(self.repo, "rev-parse", "master^{tree}").strip())

    def test_unrelated_history_is_distinct_from_conflict(self):
        git(self.card_tree, "checkout", "--orphan", "unrelated")
        self.commit(self.card_tree, "unrelated root")
        git(self.repo, "update-ref", "refs/heads/" + self.source, "unrelated")
        r = self.preview()
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["canMerge"])
        self.assertEqual(r["reason"], "unrelated-history")
        self.assertEqual(r["resultTree"], "")
        self.assertEqual(r["conflicts"], [])
        self.assertEqual(r["commitCount"], 1)

    def test_named_main_without_checkout_reports_proposed_path_without_creating_it(self):
        git(self.repo, "branch", "release/full-target")
        git(self.repo, "config", "tide.mainBranch", "release/full-target")
        r = self.preview()
        self.assertFalse(r["canMerge"], r)
        self.assertEqual(r["reason"], "no-target-worktree")
        self.assertEqual(r["localCollisionCount"], 0)
        self.assertEqual(r["previewToken"], self.preview()["previewToken"])
        self.assertEqual(r["targetBranch"], "release/full-target")
        self.assertFalse(r["targetWorktreeExists"])
        self.assertFalse(os.path.exists(r["targetWorktree"]))
        self.assertEqual(self.flow.engine.derived({"id": self.card_id, "column": "review"})["flow"]["mainBranch"],
                         "release/full-target")

    def test_bad_request_cannot_select_another_target(self):
        git(self.repo, "update-ref", "refs/remotes/origin/master", "master")
        for request in ("[]", "no json", '{"targetRef":"refs/heads/other"}',
                        '{"targetRef":"refs/remotes/origin/master"}', '{"targetRef":null}',
                        '{"targetRef":"master"}', '{"unknown":true}'):
            r = self.preview(request)
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "bad-request")

    def source_change(self, path="conflict.txt", text="karta\n"):
        self.write(self.card_tree, path, text)
        self.commit(self.card_tree, "source")

    def test_local_staged_unstaged_and_untracked_have_separate_collisions(self):
        self.source_change()
        self.source_change("added.txt")
        self.write(self.repo, "conflict.txt", "indeks\n")
        git(self.repo, "add", "conflict.txt")
        self.write(self.repo, "conflict.txt", "plik\n")
        self.write(self.repo, "added.txt", "nieśledzony\n")
        r = self.preview()
        self.assertEqual(r["reason"], "local-collision")
        self.assertEqual(r["localCollisionCount"], 3)
        self.assertEqual(r["localCollisions"]["staged"][0]["paths"], ["conflict.txt"])
        self.assertEqual(r["localCollisions"]["unstaged"][0]["paths"], ["conflict.txt"])
        self.assertEqual(r["localCollisions"]["untracked"][0]["paths"], ["added.txt"])
        self.assertNotIn("staged-not-supported", str(r["blockedReasons"]))
        self.assertEqual(r["conflicts"], [])

    def test_k43_unrelated_unstaged_and_untracked_are_allowed(self):
        self.source_change("added.txt")
        self.write(self.repo, "conflict.txt", "praca\n")
        self.write(self.repo, "note.txt", "notatka\n")
        r = self.preview()
        self.assertTrue(r["canMerge"], r)
        self.assertEqual(r["localCollisionCount"], 0)
        self.assertFalse(r["readyToMerge"])

    def test_unrelated_staged_is_allowed_without_a_collision(self):
        self.source_change("added.txt")
        self.write(self.repo, "conflict.txt", "indeks\n")
        git(self.repo, "add", "conflict.txt")
        r = self.preview()
        self.assertTrue(r["canMerge"], r)
        self.assertEqual(r["reason"], "")
        self.assertEqual(r["localCollisionCount"], 0)

    def test_unrelated_staged_does_not_hide_unstaged_collision(self):
        self.source_change()
        self.write(self.repo, "note.txt", "indeks\n")
        git(self.repo, "add", "note.txt")
        self.write(self.repo, "conflict.txt", "praca\n")
        r = self.preview()
        self.assertEqual({b["reason"] for b in r["blockedReasons"]}, {"local-collision"})
        self.assertEqual(r["localCollisions"]["staged"], [])
        self.assertEqual(r["localCollisionCount"], 1)

    def test_merge_rename_checks_both_paths_with_control_characters(self):
        git(self.card_tree, "mv", "old\tname.txt", "new\nname.txt")
        self.commit(self.card_tree, "rename")
        self.write(self.repo, "old\tname.txt", "praca\n")
        self.write(self.repo, "new\nname.txt", "praca\n")
        r = self.preview()
        self.assertEqual(r["reason"], "local-collision")
        self.assertEqual(r["localCollisionCount"], 2)
        self.assertEqual(r["localCollisions"]["unstaged"][0]["mergePaths"], ["old\tname.txt"])
        self.assertEqual(r["localCollisions"]["untracked"][0]["mergePaths"], ["new\nname.txt"])

    def test_local_rename_checks_old_and_new_paths(self):
        self.source_change("old\tname.txt")
        self.source_change("local-new.txt")
        git(self.repo, "mv", "old\tname.txt", "local-new.txt")
        r = self.preview()
        entry = r["localCollisions"]["staged"][0]
        self.assertEqual(entry["paths"], ["local-new.txt", "old\tname.txt"])
        self.assertEqual(entry["mergePaths"], ["local-new.txt", "old\tname.txt"])
        self.assertEqual(r["reason"], "local-collision")

    def test_deletions_on_either_side_collide(self):
        git(self.card_tree, "rm", "old\tname.txt")
        self.source_change()
        self.write(self.repo, "old\tname.txt", "zmieniony lokalnie\n")
        git(self.repo, "rm", "conflict.txt")
        r = self.preview()
        self.assertEqual(r["localCollisionCount"], 2)
        self.assertEqual(r["localCollisions"]["staged"][0]["paths"], ["conflict.txt"])
        self.assertEqual(r["localCollisions"]["unstaged"][0]["paths"], ["old\tname.txt"])

    def test_unstaged_deletion_collides_with_modified_merge_path(self):
        self.source_change()
        Path(self.repo, "conflict.txt").unlink()
        r = self.preview()
        self.assertEqual(r["reason"], "local-collision")
        self.assertEqual(r["localCollisions"]["unstaged"][0]["status"], " D")

    def test_file_directory_collisions_both_directions_and_component_boundary(self):
        self.source_change("file")
        os.mkdir(os.path.join(self.card_tree, "directory"))
        self.source_change("directory/child")
        os.mkdir(os.path.join(self.repo, "file"))
        self.write(self.repo, "file/child", "praca\n")
        self.write(self.repo, "directory", "praca\n")
        self.write(self.repo, "file-suffix", "bez kolizji\n")
        r = self.preview()
        entries = r["localCollisions"]["untracked"]
        self.assertEqual([e["paths"] for e in entries], [["directory"], ["file/child"]])
        self.assertEqual([e["mergePaths"] for e in entries], [["directory/child"], ["file"]])

    def test_tool_conflict_and_local_collision_are_independent(self):
        self.source_change()
        self.write(self.repo, "conflict.txt", "cel\n")
        self.commit(self.repo, "target")
        self.write(self.repo, "conflict.txt", "praca lokalna\n")
        r = self.preview()
        self.assertEqual(r["reason"], "conflict")
        self.assertEqual({b["reason"] for b in r["blockedReasons"]}, {"conflict", "local-collision"})
        self.assertEqual(r["conflicts"], ["conflict.txt"])

    def test_source_must_be_clean_but_untracked_tooling_is_allowed(self):
        os.makedirs(os.path.join(self.card_tree, ".agents", "skills"))
        self.write(self.card_tree, ".agents/skills/example", "narzędzia\n")
        self.assertTrue(self.preview()["canMerge"])
        self.write(self.card_tree, "note.txt", "praca karty\n")
        self.assertEqual(self.preview()["reason"], "source-dirty")

    def test_flow_gates_closed_running_plan_revision_and_pending_correction(self):
        journal = self.flow.engine.journal
        journal.append(self.card_id, "step.started", step="active", kind="close")
        self.assertEqual(self.preview()["reason"], "active-step")
        journal.append(self.card_id, "step.finished", step="active", result="done")
        journal.append(self.card_id, "intent", action="base-change", key="correction")
        self.assertEqual(self.preview()["reason"], "pending-action")
        journal.append(self.card_id, "result", action="base-change", key="correction", ok=False)
        journal.append(self.card_id, "plan.returned", pending=True)
        r = self.preview()
        self.assertEqual({b["reason"] for b in r["blockedReasons"]}, {"not-closed", "plan-change"})

    def test_local_target_only_and_card_or_foreign_active_target_is_refused(self):
        r = self.preview(json.dumps({"targetRef": "refs/heads/" + self.source}))
        self.assertEqual(r["reason"], "card-target")
        other = self.card()
        journal = self.flow.engine.journal
        journal.append(other, "card.started", entry="now", repo=self.repo)
        journal.append(other, "result", action="card-branch", key="card", ok=True,
                       branch="another-card", worktree="/no-checkout")
        journal.append(other, "step.started", step="foreign", kind="ticket", branch="master", worktree=self.repo)
        r = self.preview()
        self.assertEqual(r["reason"], "protected-target")
        journal.append(other, "step.finished", step="foreign", result="done")
        self.assertTrue(self.preview()["canMerge"])
        # Worktree jest chroniony także po zmianie gałęzi przez czynny krok.
        journal.append(other, "step.started", step="moved", kind="ticket", branch="old-branch", worktree=self.repo)
        self.assertEqual(self.preview()["reason"], "protected-target")

    def test_other_card_branch_is_protected_even_without_a_running_step(self):
        other = self.card()
        journal = self.flow.engine.journal
        journal.append(other, "card.started", entry="now", repo=self.repo)
        journal.append(other, "result", action="card-branch", key="card", ok=True,
                       branch="master", worktree=self.repo)
        self.assertEqual(self.preview()["reason"], "protected-target")

    def test_outside_command_keeps_own_and_foreign_checkout_busy(self):
        journal = self.flow.engine.journal
        journal.append(self.card_id, "outside.started", step="outside")
        self.assertEqual(self.preview()["reason"], "active-step")
        journal.append(self.card_id, "outside.finished", step="outside")
        other = self.card()
        journal.append(other, "card.started", entry="now", repo=self.repo)
        journal.append(other, "step.started", step="foreign", kind="ticket", branch="master", worktree=self.repo)
        journal.append(other, "step.finished", step="foreign", result="denied", outside=True)
        journal.append(other, "outside.started", step="foreign")
        self.assertEqual(self.preview()["reason"], "protected-target")
        journal.append(other, "outside.finished", step="foreign")
        self.assertTrue(self.preview()["canMerge"])

    def test_base_version_change_invalidates_token_and_old_closure(self):
        before = self.preview()
        self.flow.engine.journal.append(self.card_id, "card.base-changed", previousBaseVersion=0, baseVersion=1,
                                        base={"baseRef": "refs/heads/master", "baseBranch": "master",
                                              "baseCommit": before["targetCommit"]})
        after = self.preview()
        self.assertEqual(after["baseVersion"], 1)
        self.assertEqual(after["reason"], "base-change")
        self.assertNotEqual(before["previewToken"], after["previewToken"])

    def test_token_binds_refs_index_dirty_bytes_and_order_with_legacy_zero(self):
        previous = self.preview()
        self.assertEqual(previous["baseVersion"], 0)
        self.assertEqual(previous["previewToken"], self.preview()["previewToken"])

        def changed():
            nonlocal previous
            current = self.preview()
            self.assertNotEqual(previous["previewToken"], current["previewToken"])
            previous = current

        self.source_change("source.txt")
        changed()
        self.write(self.repo, "target.txt", "cel\n")
        self.commit(self.repo, "target")
        changed()
        self.write(self.repo, "conflict.txt", "praca 1\n")
        changed()
        self.write(self.repo, "conflict.txt", "praca 2\n")
        changed()
        git(self.repo, "add", "conflict.txt")
        changed()
        self.write(self.repo, "note.txt", "notatka 1\n")
        changed()
        self.write(self.repo, "note.txt", "notatka 2\n")
        changed()
        # Inny targetRef na tym samym SHA również oznacza inny token.
        git(self.repo, "branch", "alternate")
        alternate = self.preview('{"targetRef":"refs/heads/alternate"}')
        self.assertNotEqual(previous["previewToken"], alternate["previewToken"])
        journal = self.flow.engine.journal
        journal.append(self.card_id, "card.started", entry="now", repo=self.repo, baseVersion=1)
        journal.append(self.card_id, "result", action="card-branch", key="card", ok=True,
                       branch=self.source, worktree=self.card_tree)
        journal.append(self.card_id, "card.closed", summary="Nowe zlecenie")
        changed()
        self.assertEqual(previous["baseVersion"], 1)

    def test_token_binds_checkout_presence_and_location_and_engine_lifetime(self):
        initial = self.preview()["previewToken"]
        git(self.repo, "checkout", "-qb", "parking")
        absent = self.preview()
        self.assertEqual(absent["reason"], "no-target-worktree")
        self.assertNotEqual(absent["previewToken"], initial)
        self.assertEqual(absent["previewToken"], self.preview()["previewToken"])
        checkout = os.path.join(self.base, "target-checkout")
        git(self.repo, "worktree", "add", checkout, "master")
        moved = self.preview()
        self.assertTrue(moved["canMerge"])
        self.assertEqual(moved["targetWorktree"], checkout)
        self.assertNotEqual(moved["previewToken"], initial)
        self.assertNotEqual(moved["previewToken"], absent["previewToken"])
        from tide_daemon.cardflow.engine import FlowEngine
        restarted = FlowEngine(self.store, journal=self.flow.engine.journal)
        r = restarted.preview_card_merge(self.card_id, {})
        self.assertNotEqual(r["previewToken"], moved["previewToken"])

    def test_token_tracks_repeated_edits_inside_a_dirty_submodule(self):
        module = os.path.join(self.repo, "module")
        os.mkdir(module)
        git(module, "init", "-qb", "master")
        self.write(module, "file.txt", "baza\n")
        module_head = self.commit(module, "module")
        git(self.repo, "update-index", "--add", "--cacheinfo", "160000", module_head, "module")
        git(self.repo, "commit", "-qm", "add gitlink")
        git(self.card_tree, "merge", "--no-edit", "master")
        self.write(module, "file.txt", "pierwsza zmiana\n")
        before = self.preview()
        self.assertTrue(before["canMerge"], before)
        self.write(module, "file.txt", "kolejna zmiana\n")
        after = self.preview()
        self.assertTrue(after["canMerge"], after)
        self.assertNotEqual(before["previewToken"], after["previewToken"])

    def custom_driver_conflict(self, driver):
        self.write(self.repo, ".gitattributes", "conflict.txt merge=probe\n")
        self.commit(self.repo, "attributes")
        git(self.card_tree, "merge", "--no-edit", "master")
        self.write(self.repo, "conflict.txt", "cel\n")
        self.commit(self.repo, "target")
        self.write(self.card_tree, "conflict.txt", "źródło\n")
        self.commit(self.card_tree, "source")
        git(self.repo, "config", "merge.probe.driver", driver)


class LocalPreviewTest(PreviewScenarios, FlowInProcess):
    """Prawdziwy git przez publiczny monitor również tam, gdzie host blokuje zagnieżdżony bwrap."""
    def setUp(self):
        super().setUp()

        class ObjectGit(LocalStepGit):
            def __init__(self, *args, objects_only=False):
                super().__init__(*args)
        for patch in (mock.patch.object(gitops, "sandbox_available", return_value=True),
                      mock.patch.object(gitops, "StepGit", ObjectGit)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_concurrent_change_during_preview_cannot_claim_executability_or_issue_token(self):
        original = self.flow.engine.git.prepare_merge_tree

        def changed(*args):
            result = original(*args)
            self.write(self.repo, "note.txt", "ruch użytkownika podczas podglądu\n")
            return result

        with mock.patch.object(self.flow.engine.git, "prepare_merge_tree", changed):
            r = self.flow.engine.preview_card_merge(self.card_id, {})
        self.assertEqual(r["reason"], "state-changed")
        self.assertFalse(r["canMerge"])
        self.assertNotIn("previewToken", r)

    def test_checkout_read_error_is_not_an_empty_clean_checkout(self):
        original = self.flow.engine.git.run

        def failing(cwd, *args, **kwargs):
            if args[:1] == ("status",):
                return Result(128, "", "odmowa odczytu")
            return original(cwd, *args, **kwargs)

        with mock.patch.object(self.flow.engine.git, "run", failing):
            r = self.preview()
        self.assertEqual(r["reason"], "git-error")
        self.assertFalse(r["canMerge"])
        self.assertNotIn("previewToken", r)

    def test_missing_capability_and_tool_failure_have_distinct_reasons(self):
        for code, expected in ((129, "git-capability"), (-1, "git-error")):
            with mock.patch.object(LocalStepGit, "run", return_value=Result(code, "", "brak")):
                r = self.preview()
            self.assertTrue(r["ok"], r)
            self.assertFalse(r["canMerge"])
            self.assertEqual(r["reason"], expected)

    @unittest.skipIf(os.geteuid() == 0, "root omija uprawnienia katalogu w lokalnej reprodukcji")
    def test_custom_driver_tempfile_failure_is_a_tool_error_without_mutation(self):
        self.custom_driver_conflict("exit 1")
        results = []
        original = LocalStepGit.run

        def record(runner, *args, **kwargs):
            result = original(runner, *args, **kwargs)
            if args[:2] == ("merge-tree", "--write-tree"):
                results.append(result)
            return result

        mode = Path(self.repo).stat().st_mode
        Path(self.repo).chmod(0o555)
        try:
            with mock.patch.object(LocalStepGit, "run", record):
                r = self.preview()
        finally:
            Path(self.repo).chmod(mode)
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["canMerge"])
        self.assertEqual(r["reason"], "git-error")
        self.assertEqual((r["resultTree"], r["conflicts"]), ("", []))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].code, 128)
        self.assertIn(".merge_file_", results[0].err)
        self.assertIn("Permission denied", results[0].err)

    def test_no_sandbox_is_explicit_and_never_falls_back_to_local_merge(self):
        with mock.patch.object(gitops, "sandbox_available", return_value=False):
            r = self.preview()
        self.assertEqual(r["reason"], "no-sandbox")
        self.assertFalse(r["canMerge"])

    def test_failure_during_tree_preparation_is_not_reported_as_a_conflict(self):
        original = LocalStepGit.run

        def failing(runner, *args, **kwargs):
            if args[:2] == ("merge-tree", "--write-tree"):
                return Result(128, "", "atrapa błędu narzędzia")
            return original(runner, *args, **kwargs)

        with mock.patch.object(LocalStepGit, "run", failing):
            r = self.preview()
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["canMerge"])
        self.assertEqual(r["reason"], "git-error")
        self.assertEqual(r["conflicts"], [])


@unittest.skipUnless(sandbox_available(), "brak zagnieżdżonej piaskownicy gita")
class SandboxedPreviewTest(PreviewScenarios, FlowInProcess):
    def test_custom_merge_driver_is_refused_without_writing_worktree_index_head_or_refs(self):
        # Złośliwy sterownik próbuje pisać wszędzie, gdzie normalny StepGit ma zapis.
        # Git przygotowuje dla niego .merge_file_* w cwd, więc odczytowy mount odmawia już przed sterownikiem.
        driver = "for f in conflict.txt .git/HEAD .git/index .git/refs/heads/master; do echo BAD > \"$f\"; done; exit 1"
        self.custom_driver_conflict(driver)
        results = []
        original = StepGit.run

        def record(runner, *args, **kwargs):
            result = original(runner, *args, **kwargs)
            if args[:2] == ("merge-tree", "--write-tree"):
                results.append(result)
            return result

        with mock.patch.object(StepGit, "run", record):
            r = self.preview()
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["canMerge"])
        self.assertEqual(r["reason"], "git-error")
        self.assertEqual((r["resultTree"], r["conflicts"]), ("", []))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].code, 128)
        self.assertIn(".merge_file_", results[0].err)
        self.assertIn("Read-only file system", results[0].err)


@unittest.skipUnless(sandbox_available(), "brak zagnieżdżonej piaskownicy gita")
class PreviewDbusTest(FlowCase):
    def test_board_contract_ranges_refusals_adapter_backend_and_legacy_execution(self):
        from apps.board.tests.test_view import stage
        import importlib.util
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "review", timeout=40)
        before = fingerprint(self.repo)
        r = self.call("PreviewCardMerge", card, "{}")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["canMerge"], r)
        self.assertEqual(r["commitCount"], len(git(self.repo, "rev-list", "master.." + r["sourceCommit"]).splitlines()))
        self.assertEqual(before, fingerprint(self.repo))
        # Publiczny backend okna wywołuje dokładnie tę samą metodę adaptera /Board.
        package = stage(os.path.join(self.base, "pkg"))
        spec = importlib.util.spec_from_file_location("preview_backend", package + "/views/board/backend.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        backend = module.Backend(self.service.bus)
        self.addCleanup(backend.deleteLater)
        replies = []
        backend.replied.connect(lambda tag, text: replies.append((tag, json.loads(text))))
        backend.previewCardMerge("preview", card, "{}")
        self.assertTrue(wait_until(lambda: replies, timeout=30))
        self.assertEqual(replies[0], ("preview", r))
        # Nowa kwalifikacja zachowuje ochronę K-43, a stary interfejs nadal scala z niezwiązaną pracą.
        PreviewScenarios.write(self.repo, "CLAUDE.md", "lokalna praca użytkownika\n")
        PreviewScenarios.write(self.repo, "notes.txt", "nieśledzona praca\n")
        dirty = fingerprint(self.repo)
        allowed = self.call("PreviewCardMerge", card, "{}")
        self.assertTrue(allowed["canMerge"], allowed)
        self.assertEqual(allowed["localCollisionCount"], 0)
        self.assertEqual(dirty, fingerprint(self.repo))
        self.assertTrue(self.call("MergeCardToMain", card)["ok"])
        self.assertEqual(Path(self.repo, "CLAUDE.md").read_text(), "lokalna praca użytkownika\n")
        self.assertEqual(Path(self.repo, "notes.txt").read_text(), "nieśledzona praca\n")
        self.assertEqual(self.shown(card)["boardColumn"], "done")
        events = self.events(card)

        def preview():
            before = fingerprint(self.repo)
            result = self.call("PreviewCardMerge", card, "{}")
            self.assertTrue(result["ok"], result)
            self.assertEqual(before, fingerprint(self.repo))
            self.assertEqual(events, self.events(card))
            self.assertEqual(self.shown(card)["boardColumn"], "done")
            return result

        already = preview()
        self.assertEqual((already["commitCount"], already["fileCount"]), (0, 0))
        self.assertTrue(already["canMerge"])
        # Kolejna część sondy zapisuje własne commity celu; praca użytkownika powyżej jest już sprawdzona.
        git(self.repo, "add", "CLAUDE.md", "notes.txt")
        git(self.repo, "commit", "-qm", "save user work")
        branch = self.results(card, "card-branch")[0]
        source_tree = branch["worktree"]
        PreviewScenarios.write(self.repo, "target-only.txt", "cel\n")
        PreviewScenarios.commit(self.repo, "target only")
        git(source_tree, "mv", "CLAUDE.md", "RENAMED.md")
        for i in range(45):
            PreviewScenarios.write(source_tree, f"commit-{i}.txt", f"{i}\n")
            PreviewScenarios.commit(source_tree, f"commit {i}")
        for i in range(420):
            PreviewScenarios.write(source_tree, f"file-{i}.txt", f"{i}\n")
        PreviewScenarios.commit(source_tree, "many files")
        ranged = preview()
        self.assertTrue(ranged["canMerge"], ranged)
        self.assertEqual(ranged["commitCount"], 46)
        self.assertEqual([c["sha"] for c in ranged["commits"]],
                         git(self.repo, "rev-list", f'master..{branch["branch"]}').splitlines())
        self.assertEqual(ranged["fileCount"], 466)
        self.assertEqual(len(ranged["files"]), 466)
        self.assertIn({"status": "R100", "oldPath": "CLAUDE.md", "newPath": "RENAMED.md"}, ranged["files"])
        self.assertNotIn("target-only.txt", str(ranged["files"]))
        self.assertEqual(git(self.repo, "show", ranged["resultTree"] + ":target-only.txt"), "cel\n")
        # Konflikt w jednym wspólnym pliku, niezależnie od rename powyżej.
        PreviewScenarios.write(self.repo, "ATRAPA.md", "cel\n")
        PreviewScenarios.commit(self.repo, "target conflict")
        PreviewScenarios.write(source_tree, "ATRAPA.md", "źródło\n")
        PreviewScenarios.commit(source_tree, "source conflict")
        conflict = preview()
        self.assertFalse(conflict["canMerge"])
        self.assertEqual(conflict["reason"], "conflict")
        self.assertIn("ATRAPA.md", conflict["conflicts"])
        git(source_tree, "checkout", "--orphan", "unrelated")
        PreviewScenarios.commit(source_tree, "unrelated root")
        git(self.repo, "update-ref", "refs/heads/" + branch["branch"], "unrelated")
        unrelated = preview()
        self.assertFalse(unrelated["canMerge"])
        self.assertEqual(unrelated["reason"], "unrelated-history")
        self.assertEqual(unrelated["conflicts"], [])


class ObjectSandboxContractTest(unittest.TestCase):
    def test_only_objects_are_writable_in_preview(self):
        git = StepGit("/repo", "/repo/.git", "/repo", "/repo.step", home="/home/test", objects_only=True)
        argv = git.sandbox()
        writable = [argv[i + 1] for i, flag in enumerate(argv) if flag == "--bind"]
        self.assertEqual(writable, ["/repo/.git/objects"])


if __name__ == "__main__":
    unittest.main()
