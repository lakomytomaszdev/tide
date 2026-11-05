"""PT-200: wybór lokalnego celu przez publiczny Board i wspólną transakcję."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git
from .test_cardflow_merge_recovery import DurableMergeFixture
from .test_cardflow_merge_preview import fingerprint
from tide_daemon.cardflow.state import base_of, fold
from tide_daemon.reports import plans


class MergeTargetsTest(DurableMergeFixture, FlowInProcess):
    def call(self, method, request):
        box = []
        getattr(self.board, method)(self.card_id, json.dumps(request), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=30), method)
        return json.loads(box[0])

    def listed(self, **request):
        result = self.call("list_card_branches", {"purpose": "merge", **request})
        self.assertTrue(result["ok"], result)
        return result

    def preview(self, ref):
        result = self.call("preview_card_merge", {"targetRef": ref})
        self.assertTrue(result["ok"], result)
        return result

    @staticmethod
    def consent(preview):
        return {key: preview[key] for key in ("targetRef", "previewToken")}

    def test_full_search_before_limit_and_shared_target_exclusions(self):
        for index in range(110):
            git(self.repo, "branch", f"aaa/{index:03}")
        git(self.repo, "branch", "zzz/full/feature")
        git(self.repo, "update-ref", "refs/remotes/origin/zzz/full/feature", self.before)
        first = self.listed()
        self.assertEqual(len(first["branches"]), 50)
        self.assertTrue(first["hasMore"])
        self.assertEqual(first["recentTargets"], [])
        self.assertEqual(len(self.listed(limit=100)["branches"]), 100)
        found = self.listed(text="ZZZ/FULL")
        self.assertFalse(found["hasMore"])
        [item] = found["branches"]
        self.assertEqual((item["ref"], item["name"], item["commit"]),
                         ("refs/heads/zzz/full/feature", "zzz/full/feature", self.before))
        self.assertFalse(item["selectable"])
        self.assertIn("checkoutu", item["reason"])
        p = self.preview(item["ref"])
        self.assertEqual(p["reason"], "no-target-worktree")
        self.assertFalse(self.call("merge_card", self.consent(p))["ok"])
        [own] = self.listed(text=self.source)["branches"]
        self.assertFalse(own["selectable"])
        self.assertEqual(own["reason"], self.preview(own["ref"])["message"])
        other = self.card()
        journal = self.flow.engine.journal
        # Inny projekt może wskazywać worktree tego samego repozytorium.
        journal.append(other, "card.started", entry="now", repo=self.source_tree)
        journal.append(other, "step.started", step="active", kind="ticket", branch="different-name",
                       repo=self.source_tree, worktree=self.repo)
        [blocked] = self.listed(text=self.target)["branches"]
        self.assertFalse(blocked["selectable"])
        self.assertEqual(blocked["reason"], self.preview(blocked["ref"])["message"])
        self.assertIn(other, blocked["reason"])
        remote = self.call("preview_card_merge", {"targetRef": "refs/remotes/origin/zzz/full/feature"})
        self.assertEqual(remote["error"], "bad-request")
        git(self.repo, "symbolic-ref", "refs/heads/alias", "refs/heads/" + self.target)
        self.assertEqual(self.call("preview_card_merge", {"targetRef": "refs/heads/alias"})["error"], "bad-request")
        self.assertEqual(self.listed(text="refs/heads/alias")["branches"], [])

    def test_unknown_and_remote_base_explicit_target_and_stale_consent(self):
        journal = self.flow.engine.journal
        for base in ({}, {"baseRef": "refs/remotes/origin/feature/base", "baseBranch": "origin/feature/base",
                          "baseCommit": self.before, "baseVersion": 1}):
            journal.append(self.card_id, "card.started", entry="now", repo=self.repo, **base)
            journal.append(self.card_id, "card.closed", summary="Gotowe")
            events, before = journal.read(self.card_id), fingerprint(self.repo)
            self.listed()
            p = self.preview("refs/heads/" + self.target)
            self.assertTrue(p["canMerge"], p)
            self.assertEqual(journal.read(self.card_id), events)
            self.assertEqual(fingerprint(self.repo), before)
            self.assertEqual(base_of(fold(events))["baseRef"], base.get("baseRef", ""))
        git(self.repo, "branch", "another/local", self.before)
        tree = os.path.join(self.base, "ws", "another")
        git(self.repo, "worktree", "add", "-q", tree, "another/local")
        p = self.preview("refs/heads/another/local")
        # Token innego refu nie daje zgody nawet przy tym samym SHA.
        wrong = self.call("merge_card", {**self.consent(p), "targetRef": "refs/heads/" + self.target})
        self.assertEqual(wrong["error"], "stale-preview")
        self.write(tree, "moved.txt", "nowa praca celu\n")
        self.commit(tree, "target moved")
        self.assertEqual(self.call("merge_card", self.consent(p))["error"], "stale-preview")
        current = self.preview("refs/heads/another/local")
        merged = self.call("merge_card", self.consent(current))
        self.assertTrue(merged["ok"], merged)
        self.assertEqual(self.sha(self.target), self.before)
        self.assertEqual(base_of(fold(journal.read(self.card_id)))["baseRef"], base["baseRef"])

    def test_public_feature_start_close_selection_preview_consent_and_projections(self):
        main, main_before = self.target, self.sha(self.target)
        self.target = "feature/full/base-name"
        tree = os.path.join(self.base, "ws", "feature")
        git(self.repo, "worktree", "add", "-qb", self.target, tree)
        self.write(tree, "feature.txt", "tylko feature\n")
        self.before = self.commit(tree, "feature base")
        self.card_id = self.card()
        [base] = self.call("list_card_branches", {"purpose": "base", "text": self.target})["branches"]
        started = self.start(self.card_id, baseRef=base["ref"], baseCommit=base["commit"])
        self.assertTrue(started["ok"], started)

        def closed():
            self.flow.engine.tick()
            return fold(self.flow.engine.journal.read(self.card_id)).closed

        self.assertTrue(wait_until(closed, timeout=60), self.events(self.card_id))
        self.board.publish()
        self.assertEqual(self.shown(self.card_id)["boardColumn"], "review")
        before = self.flow.engine.journal.read(self.card_id)
        targets = self.listed()
        self.assertEqual(targets["branches"][0]["ref"], base["ref"])
        self.assertEqual(len({b["ref"] for b in targets["branches"]}), len(targets["branches"]))
        preview = self.preview(base["ref"])
        self.assertTrue(preview["canMerge"], preview)
        self.assertEqual(self.flow.engine.journal.read(self.card_id), before)
        self.assertEqual(self.sha(self.target), self.before)  # anulowany podgląd
        merged = self.call("merge_card", self.consent(preview))
        self.assertTrue(merged["ok"], merged)
        self.assertEqual(self.sha(main), main_before)
        self.assertEqual(self.sha(self.target), merged["resultCommit"])
        self.assertEqual(git(tree, "show", "HEAD:feature.txt"), "tylko feature\n")
        self.assertIn("ATRAPA.md", git(tree, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
        self.assertNotIn("ATRAPA.md", git(self.repo, "ls-tree", "-r", "--name-only", main).splitlines())
        card = self.shown(self.card_id)
        self.assertEqual(card["boardColumn"], "done")
        self.assertEqual(card["derived"]["flow"]["base"]["baseRef"], base["ref"])
        [history] = [h for h in card["derived"]["flow"]["history"] if h["text"].startswith("Karta scalona do ")]
        self.assertEqual(history["text"], "Karta scalona do " + self.target)
        events = self.events(self.card_id)
        self.assertEqual(len([e for e in events if e["type"] == "card.merged"]), 1)
        self.assertFalse(any(e["type"] == "card.main-merged" for e in events))
        path = Path(__file__).resolve().parents[2] / "apps/sessions/backend.py"
        spec = importlib.util.spec_from_file_location("target_sessions", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = next(c for c in module.flow_cards(json.loads(self.board.snapshot_json())) if c["id"] == self.card_id)
        self.assertIn(self.target, session["derived"]["flow"]["headline"])
        project = SimpleNamespace(path=self.repo, is_repo=True, plans=[])
        plans._attach_flow([project], 0, float("inf"), self.store.root)
        [entry] = [p for p in project.plans if p.get("source") == "card-merge"]
        self.assertIn(self.target, entry["title"])
