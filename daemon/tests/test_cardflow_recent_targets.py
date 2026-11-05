"""PT-214: skróty ukończonych scaleń przez publiczne metody Board, bez osobnego magazynu."""
import json
import os
from pathlib import Path
from unittest import mock

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git
from .test_cardflow_merge_recovery import DurableMergeFixture


class RecentTargetsTest(DurableMergeFixture, FlowInProcess):
    def call(self, method, request, card=None):
        replies = []
        getattr(self.board, method)(card or self.card_id, json.dumps(request), replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=30), method)
        return json.loads(replies[0])

    def listed(self, card=None, **request):
        result = self.call("list_card_branches", {"purpose": "merge", **request}, card)
        self.assertTrue(result["ok"], result)
        return result

    def fixture(self):
        records = json.loads(Path(__file__).with_name("fixtures").joinpath("recent-merge-targets.json").read_text())
        refs = [f"refs/heads/recent/{i:02}" for i in range(11)]
        for ref in refs:
            git(self.repo, "branch", ref.removeprefix("refs/heads/"))
        journal = self.flow.engine.journal
        cards = []
        for i, record in enumerate(records):
            card = self.card(title=f"Historia {i}")
            cards.append(card)
            journal.append(card, "card.started", repo=self.repo if i % 2 else self.source_tree)
            journal.append(card, record["type"], **{k: v for k, v in record.items() if k != "type"})
        return refs, cards

    def test_twelve_merges_order_dedup_restart_worktrees_deleted_ref_and_isolation(self):
        refs, cards = self.fixture()
        expected = [refs[2]] + list(reversed(refs[3:])) + [refs[1]]
        runner = self.flow.engine.git
        before = git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)")
        with mock.patch.object(runner, "run", wraps=runner.run) as calls:
            recent = self.listed(limit=1, text="does-not-match")["recentTargets"]
        self.assertEqual([b["ref"] for b in recent], expected)
        self.assertTrue(all(b["kind"] == "local" and b["commit"] == self.before for b in recent))
        self.assertFalse(any(c.args[1] in ("fetch", "push", "branch", "update-ref") for c in calls.call_args_list))
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)"), before)
        self.restart_stopped()
        self.assertEqual(self.listed()["recentTargets"], recent)
        # Karta bez zlecenia w projekcie wskazującym drugi worktree widzi tę samą historię.
        viewer = self.card(title="Przeglądanie")
        self.store.upsert_project({"slug": "second", "repo": self.source_tree, "name": "Drugi checkout"})
        self.store.update_card(viewer, {"project": "second"})
        self.assertEqual(self.listed(viewer)["recentTargets"], recent)
        other = os.path.join(self.base, "ws", "other")
        os.makedirs(other)
        git(other, "init", "-qb", "recent/02")
        git(other, "commit", "--allow-empty", "-qm", "other repository")
        self.store.upsert_project({"slug": "other", "repo": other, "name": "Inne repozytorium"})
        self.store.update_card(viewer, {"project": "other"})
        self.assertEqual(self.listed(viewer)["recentTargets"], [])
        git(self.repo, "branch", "-D", "recent/02")
        self.assertEqual([b["ref"] for b in self.listed()["recentTargets"]], list(reversed(refs[3:])) + [refs[1], refs[0]])
        self.assertEqual(sum(len([e for e in self.events(c) if e["type"] in ("card.merged", "card.main-merged")])
                             for c in cards), 12)

    def test_previous_order_survives_restart_and_duplicate_event_does_not_refresh_recency(self):
        refs, cards = self.fixture()
        journal = self.flow.engine.journal
        # Duplikat wyniku pierwszej operacji nie staje się najnowszym scaleniem.
        journal.append(cards[0], "card.main-merged", main="recent/00")
        self.assertEqual(self.listed()["recentTargets"][0]["ref"], refs[2])
        # Nowe zlecenie tej samej karty nie usuwa historycznego celu poprzedniego zlecenia.
        journal.append(cards[0], "card.started", repo=self.source_tree)
        git(self.repo, "branch", "-D", "recent/02")
        git(self.repo, "update-ref", refs[0], self.source_commit)
        self.restart_stopped()
        recent = self.listed()["recentTargets"]
        self.assertEqual(recent[-1]["ref"], refs[0])
        self.assertEqual(recent[-1]["commit"], self.source_commit)

    def test_shortcuts_share_exclusions_and_failure_does_not_block_search(self):
        refs, _ = self.fixture()
        journal = self.flow.engine.journal
        other = self.card(title="Inna karta")
        journal.append(other, "card.started", repo=self.repo)
        journal.append(other, "result", action="card-branch", key="card", ok=True,
                       branch="recent/02", worktree="")
        result = self.listed(text="recent/02")
        [branch] = result["branches"]
        self.assertEqual(result["recentTargets"][0], branch)
        self.assertFalse(branch["selectable"])
        self.assertIn(other, branch["reason"])
        with mock.patch.object(self.flow.engine, "_recent_targets", side_effect=OSError("odczyt")):
            result = self.listed(text=refs[2])
        self.assertEqual(result["recentTargets"], [])
        self.assertEqual(result["branches"], [branch])
        self.assertTrue(result["warnings"])

    def test_unknown_target_preview_refusal_and_unfinished_intent_do_not_add_history(self):
        journal = self.flow.engine.journal
        old = self.card(title="Stare scalenie bez celu")
        journal.append(old, "card.started", repo=self.repo)
        journal.append(old, "card.main-merged", commit=self.before)
        pending = self.card(title="Nieukończony zamiar")
        journal.append(pending, "card.started", repo=self.repo)
        journal.append(pending, "intent", action="main-merge", key="pending",
                       targetRef="refs/heads/" + self.target)
        preview = self.call("preview_card_merge", {"targetRef": "refs/heads/" + self.target})
        self.assertTrue(preview["canMerge"], preview)
        self.assertEqual(self.listed()["recentTargets"], [])
        refused = self.call("merge_card", {"targetRef": preview["targetRef"], "previewToken": "invalid"})
        self.assertFalse(refused["ok"])
        self.restart_stopped()
        self.assertEqual(self.listed()["recentTargets"], [])

    def test_real_merge_and_retry_add_one_target_with_current_sha(self):
        ref = "refs/heads/" + self.target
        self.assertEqual(self.listed()["recentTargets"], [])
        preview = self.call("preview_card_merge", {"targetRef": ref})
        self.assertTrue(preview["canMerge"], preview)
        consent = {k: preview[k] for k in ("targetRef", "previewToken")}
        merged = self.call("merge_card", consent)
        self.assertTrue(merged["ok"], merged)
        [recent] = self.listed()["recentTargets"]
        self.assertEqual(recent["ref"], ref)
        self.assertEqual(recent["commit"], self.sha(ref))
        self.assertTrue(recent["selectable"])
        self.assertEqual(self.call("merge_card", consent), merged)
        self.restart_stopped()
        self.assertEqual(self.listed()["recentTargets"], [recent])
        self.assertEqual(len(self.of(self.card_id, "card.merged")), 1)
