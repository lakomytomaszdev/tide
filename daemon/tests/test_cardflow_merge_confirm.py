"""PT-204: zgoda na aktualny podgląd, publiczny Board i wspólna operacja PT-212."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import FlowCase, git
from .test_cardflow_merge_preview import fingerprint
from .test_cardflow_merge_recovery import DurableMergeFixture, PowerLoss
from tide_daemon.cardflow import gitops
from tide_daemon.cardflow.state import fold


class ConfirmMergeTest(DurableMergeFixture, FlowInProcess):
    def call(self, method, request):
        box = []
        getattr(self.board, method)(self.card_id, json.dumps(request), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=30), method)
        return json.loads(box[0])

    def preview(self):
        r = self.call("preview_card_merge", {"targetRef": "refs/heads/" + self.target})
        self.assertTrue(r["ok"], r)
        return r

    def request(self, preview=None):
        p = preview or self.preview()
        return {k: p[k] for k in ("targetRef", "previewToken")}

    def assert_refused(self, request, reason="stale-preview"):
        before, events = fingerprint(self.repo), self.flow.engine.journal.read(self.card_id)
        r = self.call("merge_card", request)
        self.assertEqual(r.get("error"), reason, r)
        self.assertEqual(fingerprint(self.repo), before)
        self.assertEqual(self.flow.engine.journal.read(self.card_id), events)

    def test_confirm_merges_real_checkout_preserving_unrelated_work_and_retries_by_id(self):
        self.write(self.repo, "local.txt", "lokalna praca\n")
        self.write(self.repo, "notes.txt", "notatki\n")
        p = self.preview()
        self.assertTrue(p["canMerge"], p)
        req = self.request(p)
        r = self.call("merge_card", req)
        self.assertTrue(r["ok"], r)
        self.assert_success()
        self.assertEqual(r["targetRef"], p["targetRef"])
        self.assertEqual(r["targetCommitBefore"], p["targetCommit"])
        self.assertEqual(Path(self.repo, "local.txt").read_text(), "lokalna praca\n")
        self.assertEqual(Path(self.repo, "notes.txt").read_text(), "notatki\n")
        self.restart_stopped()
        req["operationId"] = r["operationId"]
        events = self.flow.engine.journal.read(self.card_id)
        self.assertEqual(self.call("merge_card", req), r)
        self.assertEqual(self.flow.engine.journal.read(self.card_id), events)
        self.assert_success()

    def test_started_operation_survives_restart_and_original_request_finds_same_id(self):
        req = self.request()
        journal = self.flow.engine.journal
        original = journal.append

        def append(card, event, **data):
            r = original(card, event, **data)
            if event == "intent" and data.get("action") == "main-merge":
                raise PowerLoss()
            return r

        with mock.patch.object(journal, "append", append), self.assertRaises(PowerLoss):
            self.flow.engine.merge_card(self.card_id, req)
        ident = self.intent()["operationId"]
        self.assertEqual(self.sha(self.target), self.before)
        self.restart_stopped()
        journal = self.flow.engine.journal
        journal.append(self.card_id, "step.started", step="active", kind="ticket")
        before = fingerprint(self.repo)
        blocked = self.call("merge_card", req)
        self.assertEqual(blocked.get("error"), "main-merge", blocked)
        self.assertEqual(before, fingerprint(self.repo))
        journal.append(self.card_id, "step.finished", step="active", result="done")
        r = self.call("merge_card", req)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["operationId"], ident)
        self.assert_success()

    def test_restart_without_intent_revokes_old_token(self):
        req = self.request()
        self.restart_stopped()
        self.assert_refused(req)
        self.assertTrue(self.call("merge_card", self.request())["ok"])

    def test_stale_source_target_checkout_index_file_and_base_version(self):
        mutations = [
            lambda: self.commit_source(),
            lambda: git(self.repo, "update-ref", "refs/heads/" + self.target, self.source_commit),
            lambda: git(self.repo, "checkout", "--detach", "-q"),
            lambda: git(self.repo, "update-index", "--assume-unchanged", "local.txt"),
            lambda: self.write(self.repo, "notes.txt", "kolejna treść\n"),
            lambda: self.flow.engine.journal.append(self.card_id, "card.base-changed", baseVersion=2,
                                                   previousBaseVersion=1,
                                                   baseRef="refs/heads/" + self.target,
                                                   baseBranch=self.target, baseCommit=self.before),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                # Każdy kolejny podgląd potwierdza bieżący stan; przywracamy tylko checkout/ref testu.
                git(self.repo, "checkout", "-q", self.target)
                git(self.repo, "update-index", "--no-assume-unchanged", "local.txt")
                req = self.request()
                mutate()
                self.assert_refused(req)
        self.assertEqual(fold(self.flow.engine.journal.read(self.card_id)).base_version, 2)

    def commit_source(self):
        self.write(self.source_tree, "result.txt", "zmienione źródło\n")
        self.commit(self.source_tree, "source moved")

    def test_missing_or_wrong_token_card_order_target_and_unknown_operation_are_not_consent(self):
        req = self.request()
        self.assert_refused({"targetRef": req["targetRef"]}, "bad-request")
        self.assert_refused({**req, "previewToken": "wrong"})
        self.assert_refused({**req, "previewToken": "nieaktualny-żółw"})
        self.assert_refused({**req, "operationId": "unknown"})
        other = self.card()
        p = self.call("preview_card_merge", {})
        self.flow.engine.journal.append(other, "card.started", repo=self.repo, entry="now")
        # Ten sam token nie upoważnia innej karty.
        original_card = self.card_id
        self.card_id = other
        self.assert_refused(req)
        self.card_id = original_card
        self.flow.engine.journal.append(self.card_id, "card.started", repo=self.repo, entry="now")
        self.assert_refused(self.request(p))

    def test_current_blocked_previews_cannot_execute(self):
        for event, data, reason in [
            ("step.started", {"step": "running", "kind": "ticket", "ticket": "PT-1"}, "active-step"),
            ("card.moved", {"to": "todo"}, "not-closed"),
        ]:
            with self.subTest(reason=reason):
                self.flow.engine.journal.append(self.card_id, event, **data)
                p = self.preview()
                self.assertIn(reason, [r["reason"] for r in p["blockedReasons"]])
                self.assert_refused(self.request(p), p["reason"])

    def test_pending_base_decision_and_plan_change_block_api(self):
        self.flow.engine.journal.append(self.card_id, "plan.proposed", rows=[])
        p = self.preview()
        self.assertIn("plan-change", [r["reason"] for r in p["blockedReasons"]])
        self.assert_refused(self.request(p), p["reason"])
        # Pytanie o bazę ma tę samą reprezentację, której używa PT-208.
        self.flow.engine.journal.append(self.card_id, "step.started", step="question", kind="ticket")
        self.flow.engine.journal.append(self.card_id, "step.finished", step="question", result="question",
                                       question="Baza?", baseProposal={"valid": True,
                                           "ref": "refs/heads/" + self.target, "baseCommit": self.before})
        p = self.preview()
        self.assertIn("base-decision", [r["reason"] for r in p["blockedReasons"]])
        self.assert_refused(self.request(p), p["reason"])

    def test_default_preview_token_can_confirm_explicit_named_target(self):
        p = self.call("preview_card_merge", {})
        self.assertTrue(self.call("merge_card", self.request(p))["ok"])

    def test_user_base_change_decision_blocks_merge_until_keep_and_fresh_confirmation(self):
        original = self.request()
        before = fingerprint(self.repo)
        asked = self.call("request_card_base_change", {"baseRef": "refs/heads/" + self.target,
                                                      "baseCommit": self.before})
        self.assertTrue(asked["ok"], asked)
        self.assertFalse(asked["preview"]["canChange"], "karta ma już commit wkładu")
        self.assertEqual(fingerprint(self.repo), before)
        self.assert_refused(original)
        blocked = self.preview()
        self.assertIn("base-decision", [r["reason"] for r in blocked["blockedReasons"]])
        self.assert_refused(self.request(blocked), "base-decision")
        kept = self.call("resolve_card_step", {"choice": "answer", "baseDecision": "keep",
                                               "step": asked["preview"]["decisionStep"]})
        self.assertTrue(kept["ok"], kept)
        self.assertEqual(fingerprint(self.repo), before)
        self.assert_refused(original)
        self.assertTrue(self.call("merge_card", self.request())["ok"])
        self.assert_success()

    def test_target_worktree_path_keeps_control_characters_from_preview(self):
        target_tree = os.path.join(self.base, "ws", "target\twith\nnewline")
        git(self.repo, "checkout", "--detach", "-q")
        git(self.repo, "worktree", "add", target_tree, self.target)
        p = self.preview()
        self.assertEqual(p["targetWorktree"], target_tree)
        r = self.call("merge_card", self.request(p))
        self.assertTrue(r["ok"], r)
        self.assert_success()

    def test_unrelated_staged_is_allowed_but_local_collision_is_refused_without_intent(self):
        self.write(self.repo, "notes.txt", "staged\n")
        git(self.repo, "add", "notes.txt")
        p = self.preview()
        self.assertTrue(p["canMerge"], p)
        self.write(self.repo, "result.txt", "kolizja\n")
        p = self.preview()
        self.assertIn("local-collision", [r["reason"] for r in p["blockedReasons"]])
        self.assert_refused(self.request(p), p["reason"])

    def test_no_checkout_is_provisioned_by_shared_transaction(self):
        git(self.repo, "checkout", "-qb", "feature/elsewhere")
        p = self.preview()
        self.assertTrue(p["canMerge"], p)
        self.assertFalse(os.path.exists(p["targetWorktree"]))
        self.assertTrue(self.call("merge_card", self.request(p))["ok"])
        self.assertTrue(self.merge()["already"])

    def test_feature_target_uses_existing_transaction_without_moving_main(self):
        git(self.repo, "branch", "feature/target")
        git(self.repo, "checkout", "-q", "feature/target")
        p = self.call("preview_card_merge", {"targetRef": "refs/heads/feature/target"})
        self.assertTrue(p["canMerge"], p)
        result = self.call("merge_card", self.request(p))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["targetRef"], "refs/heads/feature/target")
        self.assertEqual(self.sha(self.target), self.before)
        self.assertEqual(self.sha("feature/target"), result["resultCommit"])

    def test_already_merged_records_one_result_without_new_commit(self):
        git(self.repo, "merge", "--ff-only", self.source)
        p = self.preview()
        self.assertEqual(p["commitCount"], 0)
        r = self.call("merge_card", self.request(p))
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["already"])
        self.assertEqual(self.sha(self.target), self.source_commit)
        self.assertEqual(r["resultCommit"], self.source_commit)

    def test_preview_and_auto_check_alone_never_create_merge_intent(self):
        before = fingerprint(self.repo)
        self.assertTrue(self.preview()["canMerge"])
        self.flow.engine.tick()
        self.assertEqual(self.sha(self.target), self.before)
        self.assertEqual(fingerprint(self.repo), before)
        self.assertFalse(any(e.get("action") == "main-merge" for e in self.flow.engine.journal.read(self.card_id)))


@unittest.skipUnless(gitops.sandbox_available(), "brak zagnieżdżonej piaskownicy gita")
class SandboxedConfirmMergeTest(ConfirmMergeTest):
    local_git = False


@unittest.skipUnless(gitops.sandbox_available(), "brak zagnieżdżonej piaskownicy gita")
class ConfirmMergeDbusTest(FlowCase):
    def test_public_board_and_window_backend_confirm_named_main_once(self):
        import importlib.util
        from apps.board.tests.test_view import stage

        git(self.repo, "branch", "-m", "develop")
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "review", timeout=40)
        package = stage(os.path.join(self.base, "pkg"))
        spec = importlib.util.spec_from_file_location("confirm_backend", package + "/views/board/backend.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        backend = module.Backend(self.service.bus)
        self.addCleanup(backend.deleteLater)
        replies = []
        backend.replied.connect(lambda tag, text: replies.append((tag, json.loads(text))))
        before = fingerprint(self.repo)
        backend.previewCardMerge("preview", card, "{}")
        self.assertTrue(wait_until(lambda: replies, timeout=30))
        p = replies[-1][1]
        self.assertTrue(p["canMerge"], p)
        self.assertEqual(p["targetRef"], "refs/heads/develop")
        self.assertEqual(before, fingerprint(self.repo))
        req = {k: p[k] for k in ("targetRef", "previewToken")}
        backend.mergeCard("confirm", card, json.dumps(req))
        self.assertTrue(wait_until(lambda: len(replies) == 2, timeout=30))
        r = replies[-1][1]
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["sourceCommit"], p["sourceCommit"])
        self.assertEqual(r["targetCommitBefore"], p["targetCommit"])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").strip(), r["resultCommit"])
        self.assertEqual(git(self.repo, "write-tree").strip(), p["resultTree"])
        self.assertEqual(self.shown(card)["boardColumn"], "done")
        req["operationId"] = r["operationId"]
        self.assertEqual(self.call("MergeCard", card, json.dumps(req)), r)
        self.assertEqual(len(self.results(card, "main-merge")), 1)
