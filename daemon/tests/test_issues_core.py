"""Trwałość spraw i reguły granicy zapisu, bez usług pulpitu."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
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
        for raw in ('{"title":"a","title":"b","report":"x"}', '{"report":NaN}', 'bad',
                    '[' * 35000 + ']' * 35000):
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

    def test_deep_json_in_manifest_and_frontmatter_is_isolated(self):
        self.create()
        self.create()
        report = self.root / "issues/S-1/report.md"
        manifest = self.root / "issues/S-1/attachments.json"
        original = report.read_text()
        nested = "[" * 35000 + "]" * 35000
        for source in ("manifest", "frontmatter"):
            with self.subTest(source=source):
                report.write_text(original.replace("card: null", "card: " + nested)
                                  if source == "frontmatter" else original)
                manifest.write_text(nested if source == "manifest" else
                                    '{"version":1,"attachments":[]}')
                result = json.loads(self.service.call("List"))
                self.assertTrue(result["ok"], result)
                self.assertEqual([issue["id"] for issue in result["issues"]], ["S-2"])
                self.assertEqual([(e["id"], e["error"]) for e in result["errors"]],
                                 [("S-1", "broken-file")])
                self.assertEqual(json.loads(self.service.call("Show", "S-1"))["error"], "broken-file")
                self.assertTrue(json.loads(self.service.call("Show", "S-2"))["ok"])

    def test_large_idempotency_history_survives_restart_and_new_creates(self):
        data = {"title": "Duży raport", "report": "x" * 65536, "requestId": "klucz-1"}
        first = self.public(data)
        self.assertTrue(first["ok"], first)
        # Historia w dotychczasowym formacie przekracza dawny limit 128 MiB.
        # Pełne wyniki pozostają potrzebne także po późniejszych zmianach spraw.
        payload = {"title": data["title"], "report": data["report"], "project": None}
        fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")).encode()).hexdigest()
        ledger = {"format": 1, "next": 2101, "requests": {}}
        for number in range(1, 2101):
            ident = f"S-{number}"
            key = hashlib.sha256(f"klucz-{number}".encode()).hexdigest()
            issue = {**first["issue"], "id": ident,
                     "paths": {"report": f"issues/{ident}/report.md",
                               "manifest": f"issues/{ident}/attachments.json"}}
            ledger["requests"][key] = {"fingerprint": fingerprint, "issue": issue, "published": True}
        self.store._save_ledger(ledger)
        self.assertGreater((self.root / ".git/tide-issues.json").stat().st_size, 128 * 1024 * 1024)
        del ledger
        self.service = IssueService(BoardStore(str(self.root)))
        replay = self.public(data)
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["existing"])
        self.assertEqual(replay["issue"], first["issue"])
        created = self.public({**data, "requestId": "nowy"})
        self.assertTrue(created["ok"], created)
        self.assertEqual(created["issue"]["id"], "S-2101")
        self.assertLess((self.root / ".git/tide-issues.json").stat().st_size, 1024)
        receipts = list((self.root / ".git/tide-issue-requests").glob("*.json"))
        self.assertEqual(len(receipts), 2101)
        self.assertGreater(sum(path.stat().st_size for path in receipts), 128 * 1024 * 1024)
        # Kolejny restart sprawdza już docelowy format i pierwszy/ostatni klucz.
        self.service = IssueService(BoardStore(str(self.root)))
        self.assertEqual(self.public(data)["issue"], first["issue"])
        last = self.public({**data, "requestId": "klucz-2100"})
        self.assertTrue(last["ok"], last)
        self.assertEqual(last["issue"]["id"], "S-2100")
        self.assertTrue(last["existing"])
        self.assertEqual(self.public({**data, "report": "inny"})["error"], "conflict")

    def test_interrupted_ledger_migration_preserves_pending_intent(self):
        first = self.create(requestId="gotowe")
        pending = {"title": "Oczekuje", "report": "Raport", "requestId": "oczekuje"}
        with mock.patch.object(self.store, "_publish", side_effect=OSError("awaria publikacji")):
            with self.assertRaises(OSError):
                self.store.create(pending)
        keys = [hashlib.sha256(key.encode()).hexdigest() for key in ("gotowe", "oczekuje")]
        ledger = {"format": 1, "next": 3,
                  "requests": {key: self.store._receipt(key) for key in keys}}
        self.store._save_ledger(ledger)
        shutil.rmtree(self.root / ".git/tide-issue-requests")
        save = self.store._save_receipt
        calls = []
        def crash(key, receipt):
            save(key, receipt)
            calls.append(key)
            if len(calls) == 1:
                raise OSError("awaria migracji")
        with mock.patch.object(self.store, "_save_receipt", side_effect=crash):
            with self.assertRaises(OSError):
                self.store.create(pending)
        self.assertEqual(json.loads((self.root / ".git/tide-issues.json").read_text()), ledger)
        self.service = IssueService(BoardStore(str(self.root)))
        recovered = self.public(pending)
        self.assertTrue(recovered["ok"], recovered)
        self.assertTrue(recovered["existing"])
        self.assertEqual(recovered["issue"]["id"], "S-2")
        self.assertEqual(self.public({"title": "Próba", "report": first["issue"]["report"],
                                     "requestId": "gotowe"})["issue"], first["issue"])
        # Brak samego licznika nie gubi numerów zapisanych w potwierdzeniach.
        (self.root / ".git/tide-issues.json").unlink()
        self.assertEqual(self.public({"title": "Dalej", "report": ""})["issue"]["id"], "S-3")

    def test_idempotency_receipt_fits_maximum_escaped_report(self):
        data = {"title": "😀" * 200, "report": "\x01" * 65536, "requestId": "ucieczka"}
        first = self.public(data)
        self.assertTrue(first["ok"], first)
        self.service = IssueService(BoardStore(str(self.root)))
        replay = self.public(data)
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["existing"])
        self.assertEqual(replay["issue"], first["issue"])

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

    def test_update_contract_is_closed(self):
        for data in ({"expectedVersion": 1, "title": "Nowy"}, {"expectedVersion": 1, "project": None},
                     {"expectedVersion": 2, "report": ""}):
            self.assertEqual(validate(data, update=True), data)
        for data in ({}, {"expectedVersion": 1}, {"expectedVersion": True, "title": "x"},
                     {"expectedVersion": 0, "title": "x"}, {"expectedVersion": 1.0, "title": "x"},
                     {"expectedVersion": 1, "title": None}, {"expectedVersion": 1, "report": None},
                     {"expectedVersion": 1, "title": "x", "version": 8}):
            with self.assertRaises(BoardError):
                validate(data, update=True)

    def mutation(self, method, version, **fields):
        return json.loads(self.service.call(method, "S-1", json.dumps({"expectedVersion": version, **fields})))

    def test_independent_stores_serialize_update_and_submit_at_the_same_version(self):
        self.create()
        barrier = Barrier(2)
        def mutate(method):
            service = IssueService(BoardStore(str(self.root)))
            data = {"expectedVersion": 1}
            if method == "Update":
                data["report"] = "Zwycięska poprawka"
            barrier.wait(timeout=10)
            return json.loads(service.call(method, "S-1", json.dumps(data)))
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(mutate, ("Update", "Submit")))
        self.assertEqual(sum(reply["ok"] for reply in replies), 1)
        winner = next(reply["issue"] for reply in replies if reply["ok"])
        loser = next(reply for reply in replies if not reply["ok"])
        self.assertEqual((loser["error"], loser["currentVersion"]), ("version-conflict", 2))
        self.assertEqual(self.store.show("S-1"), winner)
        self.assertEqual((self.root / "issues/S-1/report.md").read_text(), self.store._report_text(winner))
        self.assertEqual(git_out(str(self.root), "status", "--porcelain"), "")

    def test_mutation_before_rename_failure_leaves_old_version_and_no_partial_file(self):
        original = self.create()["issue"]
        before = {p.name: p.read_bytes() for p in (self.root / "issues/S-1").iterdir()}
        head = git_out(str(self.root), "rev-parse", "HEAD")
        with mock.patch("tide_daemon.issues.os.replace", side_effect=OSError("awaria przed rename")):
            reply = self.mutation("Update", 1, report="Nie publikuj", title="Nie publikuj", project=None)
        self.assertEqual(reply["error"], "io-error")
        self.assertEqual(self.store.show("S-1"), original)
        self.assertEqual({p.name: p.read_bytes() for p in (self.root / "issues/S-1").iterdir()}, before)
        self.assertEqual(git_out(str(self.root), "rev-parse", "HEAD"), head)
        self.assertEqual(self.mutation("Update", 1, title="Ponowienie")["issue"]["version"], 2)

    def test_mutations_refuse_busy_git_and_keep_successful_write_when_commit_is_queued(self):
        original = self.create()["issue"]
        busy = self.root / ".git/MERGE_HEAD"
        busy.write_text("konflikt")
        for method in ("Update", "Submit", "Reject", "Reopen"):
            fields = {"title": "Nie zapisuj"} if method == "Update" else {}
            self.assertEqual(self.mutation(method, 1, **fields)["error"], "git-busy")
            self.assertEqual(self.store.show("S-1"), original)
        busy.unlink()
        lock = self.root / ".git/index.lock"
        lock.write_text("")
        updated = self.mutation("Update", 1, title="Zapis mimo kolejki")
        self.assertTrue(updated["ok"], updated)
        self.assertTrue(updated["commitError"])
        self.assertTrue(self.board.repo.pending)
        lock.unlink()
        restarted = BoardStore(str(self.root))
        self.assertEqual(IssueStore(restarted).show("S-1"), updated["issue"])
        restarted.sync()
        self.assertEqual(git_out(str(self.root), "status", "--porcelain"), "")

    def test_noop_ready_keeps_version_and_state_but_checks_project_and_version(self):
        self.board.upsert_project({"name": "Dom"})
        self.create(project="dom", requestId="pierwotne")
        ready = self.mutation("Submit", 1)["issue"]
        head = git_out(str(self.root), "rev-parse", "HEAD")
        unchanged = self.mutation("Update", 2, title=ready["title"], project="dom", report=ready["report"])
        self.assertEqual(unchanged["issue"], ready)
        self.assertEqual(git_out(str(self.root), "rev-parse", "HEAD"), head)
        self.assertEqual(self.mutation("Update", 1, title=ready["title"])["currentVersion"], 2)
        (self.root / "projects/dom.md").unlink()
        self.assertEqual(self.mutation("Update", 2, project="dom")["error"], "bad-request")
        self.assertEqual(self.store.show("S-1"), ready)
        # Pominięte przypisanie pozostaje bez zmiany; jawne czyszczenie umożliwia submit.
        self.assertEqual(self.mutation("Update", 2, title="Poprawiony")["issue"]["project"], "dom")
        self.assertEqual(self.mutation("Submit", 3)["error"], "bad-request")
        self.assertEqual(self.mutation("Update", 3, project=None)["issue"]["version"], 4)
        self.assertEqual(self.mutation("Submit", 4)["issue"]["state"], "ready")
        # Replay create odtwarza pierwotny wynik, ale nie cofa późniejszej wersji na dysku.
        self.assertEqual(self.create(project="dom", requestId="pierwotne")["issue"]["version"], 1)
        self.assertEqual(self.store.show("S-1")["version"], 5)

    def test_transition_matrix_and_missing_issue_never_publish_invalid_state(self):
        for method in ("Update", "Submit", "Reject", "Reopen"):
            fields = {"title": "x"} if method == "Update" else {}
            self.assertEqual(self.mutation(method, 1, **fields)["error"], "not-found")
        self.assertFalse(self.root.exists())
        original = self.create()["issue"]
        allowed = {"draft": {"Update": "draft", "Submit": "ready", "Reject": "rejected"},
                   "ready": {"Update": "draft", "Reject": "rejected"},
                   "rejected": {"Reopen": "draft"}, "accepted": {}, "archived": {}}
        path = self.root / "issues/S-1/report.md"
        for state, transitions in allowed.items():
            for method in ("Update", "Submit", "Reject", "Reopen"):
                with self.subTest(state=state, method=method):
                    baseline = {**original, "state": state}
                    path.write_text(self.store._report_text(baseline))
                    before = path.read_bytes()
                    fields = {"title": "Zmieniony"} if method == "Update" else {}
                    result = self.mutation(method, 1, **fields)
                    if method in transitions:
                        self.assertTrue(result["ok"], result)
                        self.assertEqual((result["issue"]["state"], result["issue"]["version"]),
                                         (transitions[method], 2))
                    else:
                        self.assertEqual(result["error"], "bad-request")
                        self.assertEqual(path.read_bytes(), before)
