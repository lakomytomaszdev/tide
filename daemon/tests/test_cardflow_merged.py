"""PT-205: ogólne zakończenie przez publiczny Board, odczyt historii i odczytowy cel diff."""
import importlib.util
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tide_daemon.cardflow.state import derived, fold
from tide_daemon.reports import plans
from tide_daemon.reports.cards import BoardInfo

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import FlowCase, git
from . import test_cardflow_fault_report as fault_reporting
from .test_cardflow_merge_recovery import DurableMergeFixture
from tide_daemon.cardflow import gitops


class MergedCardTest(DurableMergeFixture, FlowInProcess):
    def call(self, method, *args):
        box = []
        getattr(self.board, method)(self.card_id, *args, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20), method)
        return json.loads(box[0])

    def report_entries(self):
        project = SimpleNamespace(path=self.repo, is_repo=True, plans=[])
        plans._attach_flow([project], 0, float("inf"), self.store.root)
        return project.plans

    def assert_public_completion(self):
        self.assert_success()
        events = self.flow.engine.journal.read(self.card_id)
        [merged] = [e for e in events if e["type"] == "card.merged"]
        self.assertFalse(any(e["type"] == "card.main-merged" for e in events))
        intent = self.intent()
        for field in ("targetRef", "targetBranch", "sourceCommit", "targetCommitBefore", "resultCommit",
                      "baseVersion", "operationId"):
            self.assertEqual(merged[field], intent[field])
        snapshot = json.loads(self.board.snapshot_json())
        card = next(c for c in snapshot["cards"] if c["id"] == self.card_id)
        self.assertEqual(card["boardColumn"], "done")
        flow = card["derived"]["flow"]
        self.assertTrue(flow["merged"])
        self.assertEqual(flow["merge"]["targetRef"], "refs/heads/" + self.target)
        self.assertEqual(flow["base"]["baseCommit"], self.before)
        self.assertEqual(flow["decisions"], [])
        self.assertIn(self.target, flow["headline"])
        [phase] = [p for p in card["derived"]["phases"] if p["id"] == "main-merge"]
        self.assertEqual((phase["state"], phase["label"]), ("done", "Scalenie do " + self.target))
        [line] = [h for h in flow["history"] if h["kind"] == "merge"]
        self.assertEqual((line["text"], line["actor"]), ("Karta scalona do " + self.target, "user"))
        self.assertNotIn(card, BoardInfo(snapshot).open_cards())
        # Sesje konsumują dokładnie tę migawkę, bez prywatnego stanu silnika.
        path = Path(__file__).resolve().parents[2] / "apps/sessions/backend.py"
        spec = importlib.util.spec_from_file_location("merged_sessions", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        [session_card] = module.flow_cards(snapshot)
        self.assertEqual(session_card["derived"]["column"], "done")
        [entry] = self.report_entries()
        self.assertEqual(entry["source"], "card-merge")
        self.assertIn(self.target, entry["title"])
        self.assertEqual(entry["merge"]["resultCommit"], merged["resultCommit"])
        self.assertEqual((entry["active"], entry["done"]), (0, 1))
        self.restart_stopped()
        self.flow.engine.tick()
        self.assertTrue(self.merge()["already"])
        self.assertEqual(len(self.report_entries()), 1)
        self.assertEqual(len([e for e in self.flow.engine.journal.read(self.card_id)
                              if e["type"] == "card.finished"]), 1)

    def test_legacy_api_finishes_with_general_event_and_all_public_effects(self):
        self.assertEqual(self.merge(), {"ok": True, "id": self.card_id, "main": self.target, "already": False})
        self.assert_public_completion()

    def test_legacy_merge_serializes_board_write_with_journal_sync(self):
        original = self.store.move_card

        def move(*args, **kwargs):
            # BoardMonitor.sync() bierze tę samą blokadę przed opróżnieniem BoardRepo.pending.
            def can_sync():
                acquired = self.flow.lock.acquire(blocking=False)
                if acquired:
                    self.flow.lock.release()
                return acquired

            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertFalse(pool.submit(can_sync).result(timeout=5),
                                 "zapis kolumny konkuruje z opróżnianiem kolejki commitów Tablicy")
            return original(*args, **kwargs)

        with mock.patch.object(self.store, "move_card", side_effect=move):
            self.assertTrue(self.merge()["ok"])
        self.assert_public_completion()

    def test_token_api_finishes_with_general_event_and_all_public_effects(self):
        preview = self.call("preview_card_merge", "{}")
        result = self.call("merge_card", json.dumps({k: preview[k] for k in ("targetRef", "previewToken")}))
        self.assertTrue(result["ok"], result)
        self.assert_public_completion()

    def test_old_completion_between_git_and_projection_is_not_completed_twice(self):
        self.crash_after("main-merge.checkpoint", stage="ref-moved")
        intent = self.intent()
        self.flow.engine.journal.append(self.card_id, "card.main-merged", main=self.target,
                                       operationId=intent["operationId"], commit=intent["resultCommit"])
        self.restart_stopped()
        for _ in range(2):
            self.flow.engine.tick()
        self.assertTrue(self.merge()["already"])
        events = self.flow.engine.journal.read(self.card_id)
        self.assertEqual(len([e for e in events if e["type"] == "card.finished"]), 1)
        self.assertEqual(len([e for e in events if e["type"] in ("card.merged", "card.main-merged")]), 1)
        self.assertEqual(len(self.report_entries()), 1)

    def test_fault_report_during_projection_gap_does_not_reopen_merged_card(self):
        journal = self.flow.engine.journal
        journal.append(self.card_id, "step.started", step="failed", kind="ticket", ticket="PT-1", attempt=1)
        journal.append(self.card_id, "step.finished", step="failed", result="error", reason="no-result")
        journal.append(self.card_id, "card.closed", summary="Błąd naprawiony")
        self.crash_after("card.merged")
        events = journal.read(self.card_id)
        self.assertFalse(fold(events).card_finished)
        result = self.call("report_step_fault", json.dumps({"step": "failed", "description": "Ponów"}))
        self.assertEqual(result["error"], "not-waiting")
        self.assertEqual(journal.read(self.card_id), events)

    def test_historical_main_without_intent_returns_saved_target_after_configuration_change(self):
        self.flow.engine.journal.append(self.card_id, "card.main-merged", main="release/old-main", commit="abc")
        with mock.patch.object(self.flow.engine.git, "main_branch", side_effect=AssertionError("dzisiejsza główna")):
            self.assertEqual(self.merge(), {"ok": True, "id": self.card_id,
                                           "main": "release/old-main", "already": True})
        self.assertEqual(self.shown(self.card_id)["derived"]["flow"]["merge"]["targetRef"],
                         "refs/heads/release/old-main")


class CardDiffTest(DurableMergeFixture, FlowInProcess):
    def diff(self, ref=""):
        box = []
        self.board.card_branch_diff(self.card_id, box.append, ref)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        return json.loads(box[0])

    def test_defaults_to_current_base_ref_and_accepts_another_local_or_remote_ref_without_writing(self):
        git(self.repo, "branch", "feature/base", self.source_commit)
        git(self.repo, "branch", "feature/other", self.before)
        git(self.repo, "update-ref", "refs/remotes/origin/feature/base", self.before)
        journal = self.flow.engine.journal
        journal.append(self.card_id, "card.base-changed", baseRef="refs/heads/feature/base",
                       baseBranch="feature/base", baseCommit=self.before, baseVersion=2, previousBaseVersion=1)
        events = journal.read(self.card_id)
        before = self.sha(self.target)
        default = self.diff()
        self.assertTrue(default["ok"], default)
        self.assertEqual((default["targetRef"], default["diff"]), ("refs/heads/feature/base", ""))
        for ref in ("refs/heads/feature/other", "refs/remotes/origin/feature/base"):
            result = self.diff(ref)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["targetRef"], ref)
            self.assertIn("result.txt", result["diff"])
        for ref in ("master", "HEAD", "refs/heads/absent", "refs/remotes/absent/x", "refs/tags/tag"):
            self.assertFalse(self.diff(ref)["ok"], ref)
        self.assertEqual(journal.read(self.card_id), events)
        self.assertEqual(self.sha(self.target), before)
        self.assertEqual(fold(events).base_version, 2)

    def test_unknown_base_requires_explicit_ref_and_never_uses_current_main_as_history(self):
        journal = self.flow.engine.journal
        journal.append(self.card_id, "card.started", entry="now", repo=self.repo)
        journal.append(self.card_id, "card.closed", summary="Stara karta")
        events = journal.read(self.card_id)
        with mock.patch.object(self.flow.engine.git, "main_branch", side_effect=AssertionError("domniemana główna")):
            self.assertEqual(self.diff()["error"], "no-base")
            result = self.diff("refs/heads/" + self.target)
            self.assertTrue(result["ok"], result)
            self.assertIn("result.txt", result["diff"])
        self.assertEqual(journal.read(self.card_id), events)
        self.assertEqual(self.shown(self.card_id)["derived"]["flow"]["base"]["status"], "unknown")


class HistoricalMergeTest(unittest.TestCase):
    def test_archive_after_projection_failure_keeps_saved_result_without_reopening(self):
        events = [
            {"seq": 1, "type": "card.started", "entry": "now"},
            {"seq": 2, "type": "card.merged", "targetBranch": "feature/full/path",
             "targetRef": "refs/heads/feature/full/path", "operationId": "op"},
            {"seq": 3, "type": "main-merge.waiting", "key": "op", "reason": "błąd zapisu kolumny"},
            {"seq": 4, "type": "card.moved", "to": "archived", "from": "waiting"},
            {"seq": 5, "type": "card.finished", "column": "archived", "operationId": "op"},
        ]
        out = derived(fold(events), {"column": "archived"})
        self.assertEqual(out["column"], "archived")
        self.assertTrue(out["flow"]["merged"])
        self.assertEqual(out["flow"]["decisions"], [])

    def test_legacy_and_feature_fixtures_and_mixed_journals_keep_one_completion(self):
        fixtures = json.loads(Path(__file__).with_name("fixtures").joinpath("card-merges.json").read_text())
        for name, events in fixtures.items():
            for duplicate in (False, True):
                with self.subTest(fixture=name, mixed=duplicate):
                    log = list(events)
                    if duplicate:
                        source = next(e for e in log if e["type"] in ("card.merged", "card.main-merged"))
                        log.append({**source, "type": "card.merged" if source["type"] == "card.main-merged"
                                    else "card.main-merged", "seq": 10})
                    st = fold(log)
                    out = derived(st, {"column": "done"})
                    self.assertEqual(out["column"], "done")
                    target = "release/old-main" if name == "legacy" else "feature/full/path"
                    self.assertEqual(out["flow"]["merge"]["targetRef"], "refs/heads/" + target)
                    self.assertIn(target, out["flow"]["headline"])
                    self.assertEqual(len([h for h in out["flow"]["history"] if h["kind"] == "merge"]), 1)
                    self.assertEqual(len([e for e in st.timeline if e["type"] in
                                          ("card.merged", "card.main-merged")]), 1)


class MergedFaultReportTest(DurableMergeFixture, FlowInProcess):
    failed_card = fault_reporting.DeduplicationTest.failed_card
    report = fault_reporting.DeduplicationTest.report

    def setUp(self):
        super().setUp()
        self.engine = self.flow.engine
        tide_repo = Path(self.base, "ws", "plasma-tide")
        tide_repo.mkdir()
        git(str(tide_repo), "init", "-q")
        git(str(tide_repo), "commit", "-q", "--allow-empty", "-m", "init")
        result = json.loads(self.board.upsert_project(json.dumps({"repo": str(tide_repo)})))
        self.assertTrue(result["ok"], result)

    def test_actual_merge_does_not_reopen_reported_card_on_next_fault(self):
        first = self.report("Pierwszy błąd")
        # Zgłoszona karta staje się źródłem rzeczywistej naprawy w repozytorium fixture.
        self.card_id = first["reported"]
        self.seed()
        self.assertTrue(self.merge()["ok"])
        second_card, step = self.failed_card("Kolejny błąd")
        box = []
        self.board.report_step_fault(second_card, json.dumps({"step": step, "description": "Nawrót"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        second = json.loads(box[0])
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["action"], "create")
        self.assertNotEqual(second["reported"], first["reported"])
        self.assertEqual(self.shown(first["reported"])["boardColumn"], "done")


@unittest.skipUnless(gitops.sandbox_available(), "brak zagnieżdżonej piaskownicy gita")
class CardDiffDbusTest(FlowCase):
    def test_both_signatures_reach_board_and_explicit_target_does_not_change_base(self):
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "review", timeout=40)
        git(self.repo, "branch", "feature/compare")
        before = self.events(card)
        self.assertEqual(self.call("CardBranchDiff", card)["targetRef"], "refs/heads/master")
        explicit = self.call("CardBranchDiff", card, "refs/heads/feature/compare")
        self.assertTrue(explicit["ok"], explicit)
        self.assertEqual(explicit["targetRef"], "refs/heads/feature/compare")
        self.assertEqual(self.shown(card)["baseRef"], "refs/heads/master")
        self.assertEqual(self.events(card), before)
