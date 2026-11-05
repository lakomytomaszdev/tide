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
    def setUp(self):
        super().setUp()
        self.flow.stop()
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
        self.assertTrue(r["canMerge"], r)
        self.assertFalse(r["readyToMerge"])
        self.assertEqual(r["localCollisions"], "unassessed")
        self.assertNotIn("token", r)
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
        self.assertTrue(r["canMerge"], r)
        self.assertEqual(r["targetBranch"], "release/full-target")
        self.assertFalse(r["targetWorktreeExists"])
        self.assertFalse(os.path.exists(r["targetWorktree"]))
        self.assertEqual(self.flow.engine.derived({"id": self.card_id, "column": "review"})["flow"]["mainBranch"],
                         "release/full-target")

    def test_bad_request_cannot_select_another_target(self):
        for request in ("[]", "no json", '{"targetRef":"refs/heads/other"}'):
            r = self.preview(request)
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "bad-request")


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

    def test_missing_capability_and_tool_failure_have_distinct_reasons(self):
        for code, expected in ((129, "git-capability"), (-1, "git-error")):
            with mock.patch.object(LocalStepGit, "run", return_value=Result(code, "", "brak")):
                r = self.preview()
            self.assertTrue(r["ok"], r)
            self.assertFalse(r["canMerge"])
            self.assertEqual(r["reason"], expected)

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
    def test_merge_driver_cannot_write_worktree_index_head_or_refs(self):
        self.write(self.repo, ".gitattributes", "conflict.txt merge=probe\n")
        self.commit(self.repo, "attributes")
        git(self.card_tree, "merge", "--no-edit", "master")
        self.write(self.repo, "conflict.txt", "cel\n")
        self.commit(self.repo, "target")
        self.write(self.card_tree, "conflict.txt", "źródło\n")
        self.commit(self.card_tree, "source")
        # Złośliwy sterownik próbuje pisać wszędzie, gdzie normalny StepGit ma zapis.
        driver = "for f in conflict.txt .git/HEAD .git/index .git/refs/heads/master; do echo BAD > \"$f\"; done; exit 1"
        git(self.repo, "config", "merge.probe.driver", driver)
        r = self.preview()
        self.assertFalse(r["canMerge"])
        self.assertEqual(r["reason"], "conflict")


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
        self.assertTrue(self.call("MergeCardToMain", card)["ok"])
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
