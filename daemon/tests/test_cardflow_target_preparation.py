"""PT-206: publiczny wybór, anulowanie, zgoda i awarie przygotowania zasobów celu."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tide_daemon.cardflow import gitops

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git
from .test_cardflow_merge_preview import fingerprint
from .test_cardflow_merge_recovery import DurableMergeFixture, PowerLoss


class TargetPreparationTest(DurableMergeFixture, FlowInProcess):
    def call(self, method, request):
        box = []
        getattr(self.board, method)(self.card_id, json.dumps(request), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=30), method)
        return json.loads(box[0])

    def preview(self, ref=None):
        result = self.call("preview_card_merge", {"targetRef": ref} if ref else {})
        self.assertTrue(result["ok"], result)
        return result

    def listed(self):
        result = self.call("list_card_branches", {"purpose": "merge"})
        self.assertTrue(result["ok"], result)
        return result

    @staticmethod
    def consent(preview):
        return {k: preview[k] for k in ("targetRef", "previewToken")}

    def remote_base(self):
        git(self.repo, "remote", "add", "origin", "/nonexistent/origin")
        git(self.repo, "remote", "add", "other", "/nonexistent/other")
        ref = "refs/remotes/origin/feature/full/base"
        # Inny commit remote niż lokalna główna: nie wolno startować od dzisiejszej głównej.
        start = git(self.repo, "commit-tree", self.before + "^{tree}", "-p", self.before,
                    "-m", "remote base").strip()
        git(self.repo, "update-ref", ref, start)
        git(self.repo, "update-ref", "refs/remotes/other/feature/full/base", start)
        journal = self.flow.engine.journal
        journal.append(self.card_id, "card.started", entry="now", repo=self.repo, baseRef=ref,
                       baseBranch="origin/feature/full/base", baseCommit=start, baseVersion=1)
        journal.append(self.card_id, "result", action="card-branch", key="card", ok=True,
                       branch=self.source, worktree=self.source_tree)
        journal.append(self.card_id, "card.closed", summary="Gotowe")
        return ref, start

    def test_existing_local_without_checkout_cancel_and_confirm_preserves_user_checkout(self):
        git(self.repo, "branch", "feature/deep/name", self.before)
        self.write(self.repo, "local.txt", "praca użytkownika\n")
        before = fingerprint(self.repo)
        events = self.events(self.card_id)
        p = self.preview("refs/heads/feature/deep/name")
        self.listed()
        self.assertTrue(p["canMerge"], p)
        path = Path(p["targetWorktree"])
        self.assertTrue(path.name.startswith("merge-target-"))
        self.assertEqual(path.parent, Path(self.repo + "-worktrees"))
        self.assertFalse(path.exists())
        self.assertEqual(fingerprint(self.repo), before)
        self.assertEqual(self.events(self.card_id), events)
        result = self.call("merge_card", self.consent(p))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.sha(self.target), self.before)
        self.assertEqual(git(self.repo, "branch", "--show-current").strip(), self.target)
        self.assertEqual(Path(self.repo, "local.txt").read_text(), "praca użytkownika\n")
        self.assertEqual(Path(path, "result.txt").read_text(), "wynik karty\n")
        self.assertEqual(git(path, "status", "--porcelain"), "")

    def test_already_merged_without_checkout_still_fulfils_resource_plan_without_new_merge(self):
        git(self.repo, "branch", "local/contains-card", self.source_commit)
        p = self.preview("refs/heads/local/contains-card")
        self.assertEqual(p["commitCount"], 0)
        result = self.call("merge_card", self.consent(p))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["already"])
        self.assertEqual(result["resultCommit"], self.source_commit)
        self.assertTrue(Path(p["targetWorktree"], "result.txt").exists())
        self.assertEqual(git(p["targetWorktree"], "status", "--porcelain"), "")

    def check_already_merged_recovery_after_release(self, boundary):
        git(self.repo, "branch", "local/contains-card", self.source_commit)
        p = self.preview("refs/heads/local/contains-card")
        request = self.consent(p)
        if boundary == "worktree-unlocked":
            run = self.flow.engine.git.run

            def crash(cwd, *args, **kwargs):
                result = run(cwd, *args, **kwargs)
                if result.ok and args[:2] == ("worktree", "unlock"):
                    raise PowerLoss()
                return result

            with mock.patch.object(self.flow.engine.git, "run", crash), self.assertRaises(PowerLoss):
                self.flow.engine.merge_card(self.card_id, request)
        else:
            self.crash_at(request, boundary)
        intent = self.intent()
        events = self.events(self.card_id)
        self.assertTrue(any(e["type"] == "main-merge.checkpoint" and e.get("key") == intent["key"] and
                            e.get("stage") == "ref-moved" for e in events))
        self.assertFalse(any(e["type"] in ("card.merged", "card.finished") for e in events))
        self.assertNotIn("locked", git(self.repo, "worktree", "list", "--porcelain"))
        # Po zwolnieniu zasobów użytkownik może już pracować w celu; recovery ma tylko dokończyć projekcję.
        self.write(p["targetWorktree"], "local.txt", "praca po odblokowaniu\n")
        before_recovery = fingerprint(self.repo)
        self.restart_stopped()
        with mock.patch("tide_daemon.cardflow.targetprep.provision", side_effect=AssertionError("ponowne zasoby")), \
                mock.patch("tide_daemon.cardflow.mergeop.MergeWorktree.execute", side_effect=AssertionError("drugi merge")):
            result = self.call("merge_card", request)
            self.assertTrue(result["ok"], result)
            self.assertEqual(self.call("merge_card", {**request, "operationId": intent["key"]}), result)
        self.assertTrue(result["already"])
        self.assertEqual(result["operationId"], intent["key"])
        self.assertEqual(result["targetRef"], p["targetRef"])
        self.assertEqual(result["resultCommit"], self.source_commit)
        self.assertEqual(self.sha(p["targetRef"]), self.source_commit)
        self.assertEqual(self.sha(self.target), self.before)
        self.assertEqual(fingerprint(self.repo), before_recovery)
        events = self.events(self.card_id)
        for kind, action in (("intent", "main-merge"), ("result", "main-merge"),
                             ("card.merged", None), ("card.finished", None)):
            matching = [e for e in events if e["type"] == kind and (action is None or e.get("action") == action)]
            self.assertEqual(len(matching), 1, (kind, matching))
            self.assertEqual(matching[0]["operationId"], intent["key"])
        self.assertEqual(self.store.load().cards[int(self.card_id[2:])].column, "done")

    def test_already_merged_recovery_immediately_after_worktree_unlock(self):
        self.check_already_merged_recovery_after_release("worktree-unlocked")

    def test_already_merged_recovery_after_result_saved_before_projection(self):
        self.check_already_merged_recovery_after_release("result-saved")

    def test_already_merged_unlocked_worktree_before_git_completion_is_not_adopted(self):
        git(self.repo, "branch", "local/contains-card", self.source_commit)
        p = self.preview("refs/heads/local/contains-card")
        request = self.consent(p)
        self.crash_at(request, "target-worktree-created")
        git(self.repo, "worktree", "unlock", p["targetWorktree"])
        before = fingerprint(p["targetWorktree"])
        self.restart_stopped()
        result = self.call("merge_card", request)
        self.assertFalse(result["ok"], result)
        self.assertIn("nie należy", result["message"])
        self.assertEqual(fingerprint(p["targetWorktree"]), before)
        self.assertEqual(self.sha(p["targetRef"]), self.source_commit)
        self.assertFalse(any(e["type"] in ("card.merged", "card.finished") for e in self.events(self.card_id)))

    def test_remote_only_menu_preview_cancel_and_merge_use_remote_start(self):
        remote, start = self.remote_base()
        before = fingerprint(self.repo)
        config = Path(self.repo, ".git", "config").read_bytes()
        listing = self.listed()
        self.assertEqual(listing["trackingTargets"], [])
        self.assertEqual(listing["defaultTargetRef"], "")
        proposal = listing["createTarget"]
        self.assertEqual(proposal["label"], "Utwórz lokalną feature/full/base od origin/feature/full/base")
        p = self.preview(proposal["ref"])
        self.assertTrue(p["canMerge"], p)
        self.assertTrue(p["targetPreparation"]["createRef"])
        self.assertEqual(p["targetPreparation"]["remoteRef"], remote)
        self.assertEqual(p["targetPreparation"]["startCommit"], start)
        self.assertEqual(p["targetCommit"], start)
        self.assertIn("result.txt", [f["newPath"] for f in p["files"]])
        self.assertEqual(fingerprint(self.repo), before)
        self.assertFalse(Path(p["targetWorktree"]).exists())
        result = self.call("merge_card", self.consent(p))
        self.assertTrue(result["ok"], result)
        self.assertEqual(git(self.repo, "rev-list", "--parents", "-n", "1", result["resultCommit"]).split(),
                         [result["resultCommit"], start, self.source_commit])
        self.assertEqual(self.sha(remote), start)
        self.assertEqual(self.sha("refs/remotes/other/feature/full/base"), start)
        self.assertEqual(self.sha(self.target), self.before)
        self.assertEqual(Path(self.repo, ".git", "config").read_bytes(), config)
        self.assertEqual(self.call("merge_card", self.consent(p)), result)
        [merged] = [e for e in self.events(self.card_id) if e["type"] == "card.merged"]
        self.assertEqual(merged["targetRef"], proposal["ref"])

    def test_remote_base_needs_no_local_main_and_uses_current_remote_not_historical_sha(self):
        remote, start = self.remote_base()
        git(self.repo, "checkout", "-qb", "user/parking")
        git(self.repo, "branch", "-D", self.target)
        self.assertEqual(self.listed()["mainRef"], "")
        # Baza historyczna zostaje przypięta, lecz nowy cel startuje od SHA remote widocznego w zgodzie.
        moved = git(self.repo, "commit-tree", start + "^{tree}", "-p", start, "-m", "remote moved").strip()
        git(self.repo, "update-ref", remote, moved, start)
        p = self.preview(self.listed()["createTarget"]["ref"])
        self.assertEqual(p["targetPreparation"]["startCommit"], moved)
        result = self.call("merge_card", self.consent(p))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["targetCommitBefore"], moved)
        self.assertEqual(git(self.repo, "branch", "--show-current").strip(), "user/parking")
        self.assertEqual(self.sha(remote), moved)

    def test_tracking_identity_ignores_name_sha_and_other_remote_and_requires_unique_candidate(self):
        remote, start = self.remote_base()
        git(self.repo, "branch", "feature/full/base", start)  # zgodna nazwa i SHA nie są upstreamem
        git(self.repo, "branch", "other-tracker", "--track", "other/feature/full/base")
        listing = self.listed()
        self.assertEqual(listing["trackingTargets"], [])
        self.assertFalse(listing["createTarget"]["selectable"])
        self.assertEqual(self.call("preview_card_merge", {})["error"], "target-required")
        git(self.repo, "branch", "local/one", "--track", "origin/feature/full/base")
        self.assertEqual(self.listed()["defaultTargetRef"], "refs/heads/local/one")
        self.assertEqual(self.preview()["targetRef"], "refs/heads/local/one")
        git(self.repo, "branch", "local/two", "--track", "origin/feature/full/base")
        self.assertEqual(self.listed()["defaultTargetRef"], "")
        self.assertEqual(set(self.listed()["trackingTargets"]), {"refs/heads/local/one", "refs/heads/local/two"})
        self.assertEqual(self.call("preview_card_merge", {})["error"], "target-required")
        self.assertTrue(self.preview("refs/heads/local/two")["canMerge"])

    def test_remote_movement_name_occupation_and_new_checkout_invalidate_before_creation(self):
        remote, start = self.remote_base()
        ref = "refs/heads/new/local"
        p = self.preview(ref)
        git(self.repo, "update-ref", remote, self.before, start)
        events = self.events(self.card_id)
        self.assertEqual(self.call("merge_card", self.consent(p))["error"], "stale-preview")
        self.assertEqual(self.events(self.card_id), events)
        self.assertFalse(Path(p["targetWorktree"]).exists())
        p = self.preview(ref)
        git(self.repo, "branch", "new/local", self.before)
        self.assertEqual(self.call("merge_card", self.consent(p))["error"], "stale-preview")
        p = self.preview(ref)
        elsewhere = Path(self.base, "ws", "user-checkout")
        git(self.repo, "worktree", "add", str(elsewhere), "new/local")
        self.assertEqual(self.call("merge_card", self.consent(p))["error"], "stale-preview")
        self.assertFalse(Path(p["targetWorktree"]).exists())
        self.assertEqual(self.preview(ref)["targetWorktree"], str(elsewhere))

    def test_occupied_directory_and_custom_local_name(self):
        self.remote_base()
        proposal = self.listed()["createTarget"]
        git(self.repo, "branch", proposal["name"], self.before)
        listing = self.listed()
        self.assertFalse(listing["createTarget"]["selectable"])
        self.assertTrue(self.preview(proposal["ref"])["canMerge"])  # jawny istniejący cel
        p = self.preview("refs/heads/alternative/full/name")
        path = Path(p["targetWorktree"])
        path.mkdir(parents=True)
        (path / "notes.txt").write_text("obcy katalog\n")
        self.assertEqual(self.call("merge_card", self.consent(p))["error"], "stale-preview")
        blocked = self.preview(p["targetRef"])
        self.assertFalse(blocked["canMerge"])
        self.assertEqual(blocked["reason"], "target-path-occupied")
        self.assertEqual((path / "notes.txt").read_text(), "obcy katalog\n")
        self.assertEqual(self.flow.engine.git.sha(self.repo, p["targetRef"]), "")
        good = self.preview("refs/heads/alternative/different/name")
        self.assertTrue(self.call("merge_card", self.consent(good))["ok"])

    def crash_at(self, request, stage):
        journal = self.flow.engine.journal
        append = journal.append

        def crash(card, kind, **data):
            result = append(card, kind, **data)
            if (kind == "main-merge.checkpoint" and data.get("stage") == stage or
                    kind == "intent" and stage == "intent"):
                raise PowerLoss()
            return result

        with mock.patch.object(journal, "append", crash), self.assertRaises(PowerLoss):
            self.flow.engine.merge_card(self.card_id, request)

    def check_recovery(self, stage):
        remote, start = self.remote_base()
        self.target = "remote/local/result"
        p = self.preview("refs/heads/" + self.target)
        request = self.consent(p)
        self.crash_at(request, stage)
        intent = self.intent()
        self.assertEqual(intent["targetCommitBefore"], start)
        self.restart_stopped()
        result = self.call("merge_card", request)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operationId"], intent["key"])
        self.assertEqual(self.call("merge_card", {**request, "operationId": intent["key"]}), result)
        self.assertEqual(self.sha(remote), start)
        self.assertEqual(self.sha(self.target), intent["resultCommit"])
        self.assertEqual(Path(p["targetWorktree"], "result.txt").read_text(), "wynik karty\n")
        events = self.events(self.card_id)
        self.assertEqual(len([e for e in events if e["type"] == "intent" and e.get("action") == "main-merge"]), 1)
        self.assertEqual(len([e for e in events if e["type"] == "card.merged"]), 1)

    def test_recovery_after_ref_created(self):
        self.check_recovery("target-ref-created")

    def test_recovery_after_directory_reserved(self):
        self.check_recovery("target-directory")

    def test_recovery_after_worktree_created(self):
        self.check_recovery("target-worktree-created")

    def test_foreign_same_sha_ref_after_intent_is_never_adopted(self):
        _, start = self.remote_base()
        p = self.preview("refs/heads/new/local")
        self.crash_at(self.consent(p), "intent")
        git(self.repo, "branch", "new/local", start)
        self.restart_stopped()
        result = self.call("merge_card", self.consent(p))
        self.assertFalse(result["ok"], result)
        self.assertIn("własności", result["message"])
        self.assertEqual(self.sha(p["targetRef"]), start)
        self.assertFalse(Path(p["targetWorktree"]).exists())

    def test_foreign_directory_after_owned_ref_waits_without_overwrite(self):
        self.remote_base()
        p = self.preview("refs/heads/new/local")
        self.crash_at(self.consent(p), "target-ref-created")
        path = Path(p["targetWorktree"])
        path.mkdir(parents=True)
        (path / "user.txt").write_text("cudza praca\n")
        self.restart_stopped()
        result = self.call("merge_card", self.consent(p))
        self.assertFalse(result["ok"], result)
        self.assertIn("zajęty", result["message"])
        self.assertEqual((path / "user.txt").read_text(), "cudza praca\n")

    def test_crash_between_git_creation_and_checkpoint_recovers_owned_ref_and_worktree(self):
        self.remote_base()
        p = self.preview("refs/heads/new/local")
        run = self.flow.engine.git.run
        crashed = set()

        def crash(cwd, *args, **kwargs):
            result = run(cwd, *args, **kwargs)
            boundary = "ref" if args[:2] == ("update-ref", "--stdin") else "worktree" if args[:2] == ("worktree", "add") else ""
            if result.ok and boundary and boundary not in crashed:
                crashed.add(boundary)
                raise PowerLoss()
            return result

        for boundary in ("ref", "worktree"):
            with mock.patch.object(self.flow.engine.git, "run", crash), self.assertRaises(PowerLoss):
                self.flow.engine.merge_card(self.card_id, self.consent(p))
            self.assertIn(boundary, crashed)
            self.restart_stopped()
            run = self.flow.engine.git.run
        self.assertTrue(self.call("merge_card", self.consent(p))["ok"])

    def test_ref_namespace_collision_and_unsafe_names_never_create_resources(self):
        self.remote_base()
        git(self.repo, "branch", "prefix", self.before)
        p = self.preview("refs/heads/prefix/child")
        self.assertFalse(p["canMerge"])
        self.assertEqual(p["reason"], "target-name-occupied")
        self.assertFalse(Path(p["targetWorktree"]).exists())
        for ref in ("refs/heads/../../outside", "refs/heads/name\ncommand", "refs/heads/", "short-name"):
            result = self.call("preview_card_merge", {"targetRef": ref})
            self.assertEqual(result["error"], "bad-request", result)

    def test_remote_symbolic_alias_after_preview_is_stale(self):
        remote, start = self.remote_base()
        p = self.preview("refs/heads/new/local")
        git(self.repo, "update-ref", "refs/remotes/origin/alias", start)
        git(self.repo, "symbolic-ref", remote, "refs/remotes/origin/alias")
        self.assertEqual(self.call("merge_card", self.consent(p))["error"], "stale-preview")
        self.assertFalse(Path(p["targetWorktree"]).exists())

    def test_changed_owned_ref_after_creation_waits(self):
        self.remote_base()
        p = self.preview("refs/heads/new/local")
        self.crash_at(self.consent(p), "target-ref-created")
        git(self.repo, "update-ref", p["targetRef"], self.before)
        self.restart_stopped()
        result = self.call("merge_card", self.consent(p))
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.sha(p["targetRef"]), self.before)
        self.assertFalse(Path(p["targetWorktree"]).exists())

    def test_remote_movement_during_atomic_ref_creation_leaves_no_partial_resources(self):
        remote, start = self.remote_base()
        p = self.preview("refs/heads/new/local")
        run = self.flow.engine.git.run

        def move(cwd, *args, **kwargs):
            if args[:2] == ("update-ref", "--stdin"):
                git(self.repo, "update-ref", remote, self.before, start)
            return run(cwd, *args, **kwargs)

        with mock.patch.object(self.flow.engine.git, "run", move):
            result = self.call("merge_card", self.consent(p))
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.flow.engine.git.sha(self.repo, p["targetRef"]), "")
        self.assertEqual(self.flow.engine.git.sha(self.repo, "refs/tide/merge-targets/" + self.intent()["key"]), "")
        self.assertFalse(Path(p["targetWorktree"]).exists())

    def test_changed_owned_directory_after_reservation_waits(self):
        self.remote_base()
        p = self.preview("refs/heads/new/local")
        self.crash_at(self.consent(p), "target-directory")
        path = Path(p["targetWorktree"])
        path.rename(str(path) + "-previous")
        path.mkdir()
        (path / "notes.txt").write_text("obcy katalog\n")
        self.restart_stopped()
        result = self.call("merge_card", self.consent(p))
        self.assertFalse(result["ok"], result)
        self.assertIn("właściciela", result["message"])
        self.assertEqual((path / "notes.txt").read_text(), "obcy katalog\n")

    def test_symbolic_target_after_resource_creation_never_moves_the_other_branch(self):
        _, start = self.remote_base()
        p = self.preview("refs/heads/new/local")
        self.crash_at(self.consent(p), "target-worktree-created")
        git(self.repo, "branch", "foreign/target", start)
        git(self.repo, "symbolic-ref", p["targetRef"], "refs/heads/foreign/target")
        self.restart_stopped()
        result = self.call("merge_card", self.consent(p))
        self.assertFalse(result["ok"], result)
        self.assertIn("symboliczny", result["message"])
        self.assertEqual(self.sha("foreign/target"), start)
        self.assertFalse(Path(p["targetWorktree"], "result.txt").exists())


@unittest.skipUnless(gitops.sandbox_available(), "brak zagnieżdżonej piaskownicy gita")
class SandboxedTargetPreparationTest(TargetPreparationTest):
    local_git = False
