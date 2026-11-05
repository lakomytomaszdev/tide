"""Trwałość spraw i reguły granicy zapisu, bez usług pulpitu."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from . import DAEMON_DIR
from .test_board_dbus import git_config, git_out
from tide_daemon.board.model import BoardError
from tide_daemon.board.store import BoardStore
from tide_daemon.issues import IssueService, IssueStore, validate


class IssuesCoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-issues-")
        self.addCleanup(shutil.rmtree, self.tmp)
        patch = mock.patch.dict(os.environ, git_config(self.tmp))
        patch.start()
        self.addCleanup(patch.stop)
        self.root = Path(self.tmp) / "board"
        self.board = BoardStore(str(self.root))
        self.store = IssueStore(self.board)
        self.service = IssueService(self.board)

    def create(self, **fields):
        return self.store.create({"title": "Próba", "report": "# Raport\n\nTreść\n---\nKoniec\n", **fields})

    def public(self, data):
        return json.loads(self.service.call("Create", json.dumps(data)))

    def test_public_validation_has_no_side_effects(self):
        good = {"title": "Tytuł", "report": "Treść"}
        invalid = [{}, [], None, {**good, "title": " \t"}, {**good, "title": None},
                   {**good, "project": "brak"}, {**good, "project": "../x"},
                   {**good, "report": "ą" * 32769}, {**good, "report": "\udcff"},
                   {**good, "report": "\ufeff---\r\nid: S-7\r\n---\r\nTreść"},
                   {**good, "report": "---\nversion: 5\n---\nx"},
                   {**good, "requestId": ""}, {**good, "requestId": None}]
        for field in ("id", "version", "state", "created", "updated", "card", "attachments", "expectedVersion", "repo"):
            invalid.append({**good, field: "obce"})
        for data in invalid:
            with self.subTest(data=repr(data)[:150]):
                result = self.public(data)
                self.assertEqual(result["error"], "bad-request", result)
                self.assertFalse(self.root.exists())
        for raw in ('{"title":"a","title":"b","report":"x"}', '{"report":NaN}', 'bad'):
            self.assertEqual(json.loads(self.service.call("Create", raw))["error"], "bad-request")
        self.assertFalse(self.root.exists())

    def test_roundtrip_separates_body_metadata_and_numbering(self):
        body = "# Raport\r\n\r\n---\r\n\n" + "ą" * 100
        issue = self.create(report=body, title='Cudzysłów " i : #', project=None)["issue"]
        self.assertEqual(issue, self.store.show("S-1"))
        self.assertEqual(issue["report"], body)
        self.assertEqual((issue["state"], issue["version"], issue["card"]), ("draft", 1, None))
        self.assertEqual(issue["attachments"], [])
        self.assertEqual(json.loads((self.root / "issues/S-1/attachments.json").read_text()),
                         {"version": 1, "attachments": []})
        raw = (self.root / "issues/S-1/report.md").read_bytes()
        self.assertTrue(raw.startswith(b"---\n"))
        self.assertTrue(raw.endswith(body.encode("utf-8")))
        self.assertEqual(self.board.snapshot()["nextId"], "K-1")
        self.assertEqual(self.board.snapshot()["cards"], [])
        self.assertEqual(git_out(str(self.root), "branch", "--format=%(refname:short)").strip(), "master")
        self.assertFalse((self.root / ".git/tide-next-card").exists())
        self.assertEqual(self.board.create_card({"title": "Karta"})["id"], "K-1")
        self.assertEqual(self.create()["issue"]["id"], "S-2")
        self.assertEqual(git_out(str(self.root), "status", "--porcelain"), "")

    def test_frontmatter_and_size_boundaries(self):
        self.assertEqual(self.create(report="ą" * 32768)["issue"]["version"], 1)
        self.create(report="# Nagłówek\n---\nstate: fragment treści\n---\n")
        for body in ("---\ntitle: obcy\n", "---\nid: S-999\n---\n", "x" * 65537):
            with self.assertRaises(BoardError):
                self.create(report=body)
        self.assertEqual(self.create()["issue"]["id"], "S-3")

    def test_project_is_explicit_and_deleted_project_is_rejected(self):
        self.board.upsert_project({"name": "Dom"})
        result = self.create(project="dom", requestId="pierwszy")
        self.assertEqual(result["issue"]["project"], "dom")
        (self.root / "projects/dom.md").unlink()
        with self.assertRaises(BoardError):
            self.create(project="dom")
        restarted = IssueStore(BoardStore(str(self.root)))
        self.assertEqual(restarted.create({"title": "Próba", "report": result["issue"]["report"],
                                           "project": "dom", "requestId": "pierwszy"})["issue"], result["issue"])
        self.assertEqual(self.create(report="# dom\nProjekt: dom")["issue"]["project"], None)

    def test_restart_replay_and_payload_conflicts(self):
        original = self.create(requestId="powtórka")
        store = IssueStore(BoardStore(str(self.root)))
        data = {"report": original["issue"]["report"], "title": "Próba", "requestId": "powtórka", "project": None}
        count = git_out(str(self.root), "rev-list", "--count", "HEAD")
        replay = store.create(data)
        self.assertEqual(replay["issue"], original["issue"])
        self.assertTrue(replay["existing"])
        self.assertEqual(git_out(str(self.root), "rev-list", "--count", "HEAD"), count)
        for field, value in (("title", "Inny"), ("report", "Inny raport"), ("project", "brak")):
            with self.assertRaises(BoardError) as error:
                store.create({**data, field: value})
            self.assertEqual(error.exception.code, "conflict")

    def test_reserved_intent_recovers_before_and_after_publication(self):
        for stage in ("before", "after"):
            with self.subTest(stage=stage):
                key = "awaria-" + stage
                data = {"title": "Awaria", "report": stage, "requestId": key}
                original_publish = self.store._publish
                def crash(issue):
                    if stage == "after":
                        original_publish(issue)
                    raise OSError("symulowana awaria")
                with mock.patch.object(self.store, "_publish", side_effect=crash):
                    with self.assertRaises(OSError):
                        self.store.create(data)
                restarted = IssueStore(BoardStore(str(self.root)))
                result = restarted.create(data)
                self.assertTrue(result["existing"])
                self.assertEqual(restarted.show(result["issue"]["id"]), result["issue"])
        self.assertEqual(self.create()["issue"]["id"], "S-3")

    def test_parallel_creates_and_high_water_survive_deletion(self):
        self.board.ensure()
        def create(number):
            return IssueStore(BoardStore(str(self.root))).create({"title": str(number), "report": str(number)})
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = [result["issue"]["id"] for result in pool.map(create, range(12))]
        self.assertEqual(len(set(ids)), 12)
        shutil.rmtree(self.root / "issues/S-12")
        self.assertEqual(IssueStore(BoardStore(str(self.root))).create({"title": "a", "report": ""})["issue"]["id"], "S-13")
        (self.root / "issues/S-30").mkdir()
        self.assertEqual(self.create()["issue"]["id"], "S-31")
        # Kopia bez lokalnego licznika nadal uwzględnia usunięte sprawy w historii.
        (self.root / ".git/tide-issues.json").unlink()
        shutil.rmtree(self.root / "issues/S-31")
        self.assertEqual(self.create()["issue"]["id"], "S-32")

    def test_broken_files_and_symlinks_do_not_block_other_reads(self):
        self.create()
        self.create()
        (self.root / "issues/S-2/report.md").write_bytes(b"\xff")
        (self.root / "issues/S-3").mkdir()
        secret = Path(self.tmp) / "secret"
        secret.write_text("TAJNE DANE")
        (self.root / "issues/S-4").symlink_to(secret.parent, target_is_directory=True)
        (self.root / "issues/S-1/attachments.json").unlink()
        (self.root / "issues/S-1/attachments.json").symlink_to(secret)
        self.create()
        result = self.store.list()
        self.assertEqual([issue["id"] for issue in result["issues"]], ["S-5"])
        self.assertEqual(len(result["errors"]), 4)
        self.assertNotIn("TAJNE DANE", json.dumps(result))
        for ident in ("../secret", "S-1/../../secret", "K-1", "S-0"):
            with self.assertRaises(BoardError) as error:
                self.store.show(ident)
            self.assertEqual(error.exception.code, "bad-request")

    def test_busy_repository_refuses_write_and_queue_keeps_written_issue(self):
        self.create()
        (self.root / ".git/MERGE_HEAD").write_text("konflikt")
        with self.assertRaises(BoardError) as error:
            self.create()
        self.assertEqual(error.exception.code, "git-busy")
        (self.root / ".git/MERGE_HEAD").unlink()
        lock = self.root / ".git/index.lock"
        lock.write_text("")
        result = self.create(requestId="kolejka")
        self.assertTrue(result["commitError"])
        self.assertTrue(self.board.repo.pending)
        self.assertEqual(self.store.show("S-2"), result["issue"])
        lock.unlink()
        self.board.sync()
        self.assertEqual(self.board.repo.pending, [])
        self.assertIn("S-2: create issue", git_out(str(self.root), "log", "--format=%s"))

    def test_update_contract_is_closed_without_enabling_mutation(self):
        for data in ({"expectedVersion": 1, "title": "Nowy"}, {"expectedVersion": 1, "project": None},
                     {"expectedVersion": 2, "report": ""}):
            self.assertEqual(validate(data, update=True), data)
        for data in ({}, {"expectedVersion": 1}, {"expectedVersion": True, "title": "x"},
                     {"expectedVersion": 0, "title": "x"}, {"expectedVersion": 1.0, "title": "x"},
                     {"expectedVersion": 1, "title": None}, {"expectedVersion": 1, "report": None},
                     {"expectedVersion": 1, "title": "x", "version": 8}):
            with self.assertRaises(BoardError):
                validate(data, update=True)
