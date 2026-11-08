"""Publiczna operacja akceptacji: numeracja, izolacja i granice trwałych zapisów."""
import hashlib
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from . import test_issues_core as fixture
from tide_daemon.board.model import BoardError
from tide_daemon.board.store import BoardStore
from tide_daemon.issues import IssueService, IssueStore
from tide_daemon import issue_acceptance as acceptance


class AcceptanceTest(unittest.TestCase):
    setUp = fixture.IssuesCoreTest.setUp
    create = fixture.IssuesCoreTest.create

    def call(self, ident="S-1", version=2, key="decyzja", **extra):
        return json.loads(self.service.call("AcceptIssue", ident, json.dumps({
            "expectedVersion": version, "destination": "backlog", "requestId": key, **extra})))

    def ready(self, **fields):
        issue = self.create(**fields)["issue"]
        return self.store.mutate(issue["id"], {"expectedVersion": 1}, "Submit")["issue"]

    def restart(self):
        self.board = BoardStore(str(self.root))
        self.store = IssueStore(self.board)
        self.service = IssueService(self.board)

    def test_roundtrip_immutable_report_source_fields_replay_and_history_failure(self):
        self.board.upsert_project({"name": "Dom", "slug": "dom"})
        ready = self.ready(project="dom")
        with mock.patch.object(self.board.repo, "commit", return_value="Historia niedostępna"):
            result = self.call()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["commitError"], "Historia niedostępna")
        self.assertEqual((result["card"], result["acceptedVersion"], result["alreadyAccepted"]), ("K-1", 2, False))
        self.assertEqual(self.store.show("S-1")["state"], "accepted")
        self.assertEqual(self.store.show("S-1")["version"], 3)
        card = self.board.snapshot()["cards"][0]
        self.assertEqual((card["column"], card["project"], card["description"]), ("backlog", "dom", ready["report"]))
        self.assertEqual((card["sourceIssue"], card["sourceIssueVersion"], card["spec"]), ("S-1", 2, ""))
        manifest = (self.root / card["sourceIssueManifest"]).read_bytes()
        self.assertEqual(card["sourceIssueHash"], hashlib.sha256(ready["report"].encode() + b"\0" + manifest).hexdigest())
        snapshot = (self.root / card["sourceIssueReport"]).read_bytes()
        self.board.update_card("K-1", {"description": "Nowy opis karty"})
        self.restart()
        replay = self.call()
        self.assertTrue(replay["alreadyAccepted"])
        self.assertEqual(replay["card"], "K-1")
        self.assertEqual(self.board.snapshot()["cards"][0]["description"].strip(), "Nowy opis karty")
        self.assertEqual((self.root / card["sourceIssueReport"]).read_bytes(), snapshot)
        self.assertEqual(self.store.show("S-1")["report"], ready["report"])
        for field in acceptance.SOURCE_KEYS:
            with self.assertRaises(BoardError):
                self.board.update_card("K-1", {field: "sfałszowane"})
            with self.assertRaises(BoardError):
                self.board.create_card({"title": "Obca", field: "sfałszowane"})
        for method in ("Update", "Submit", "Reject", "Reopen"):
            payload = {"expectedVersion": 3, **({"title": "Zmień"} if method == "Update" else {})}
            self.assertFalse(json.loads(self.service.call(method, "S-1", json.dumps(payload)))["ok"])
        self.assertEqual(self.board.create_card({"title": "Ręczna"})["id"], "K-2")
        self.assertEqual(self.call(key="druga-decyzja")["card"], "K-1")
        self.assertEqual(self.call(version=3)["error"], "conflict")
        self.assertEqual(self.call(version=3, key="nowy")["error"], "version-conflict")

    def test_refusals_do_not_reserve_cards(self):
        self.create()
        self.assertEqual(self.call(version=1)["error"], "bad-request")
        self.store.mutate("S-1", {"expectedVersion": 1}, "Submit")
        self.assertEqual(self.call(version=1)["error"], "version-conflict")
        self.assertEqual(self.call(destination="go")["error"], "bad-request")
        for value in (None, True, 0, "2", 2.0):
            self.assertEqual(self.call(version=value)["error"], "bad-request")
        self.store.mutate("S-1", {"expectedVersion": 2}, "Reject")
        self.assertEqual(self.call(version=3)["error"], "bad-request")
        self.board.upsert_project({"name": "Dom", "slug": "dom"})
        issue = self.ready(project="dom")
        (self.root / "projects/dom.md").unlink()
        self.assertEqual(self.call(ident=issue["id"])["error"], "bad-request")
        self.assertEqual(self.board.snapshot()["nextId"], "K-1")
        self.assertEqual(self.board.snapshot()["cards"], [])

    def test_permanent_and_exceptional_commit_failures_are_warnings_after_publication(self):
        self.ready()
        with mock.patch.object(self.board.repo, "_commit_now", return_value=("Trwały błąd historii", False)):
            result = self.call()
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["commitError"])
        self.assertEqual(self.store.show("S-1")["state"], "accepted")
        self.ready()
        with mock.patch.object(self.board.repo, "commit", side_effect=OSError("Błąd historii")):
            result = self.call(ident="S-2", key="druga")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["commitError"])
        self.restart()
        self.assertEqual(self.call(ident="S-2", key="druga")["card"], "K-2")

    def test_accepted_evidence_survives_restart_and_cannot_be_attached_or_detached(self):
        self.create()
        attached = self.store.change_attachment("S-1", {"expectedVersion": 1, "name": "dowod.txt"},
                                                "Attach", b"trwaly dowod")["issue"]
        self.store.mutate("S-1", {"expectedVersion": 2}, "Submit")
        self.assertTrue(self.call(version=3)["ok"])
        self.restart()
        entry = attached["attachments"][0]
        result, raw = self.store.read_attachment("S-1", {"expectedVersion": 4, "attachmentId": entry["id"]})
        self.assertEqual(raw, b"trwaly dowod")
        self.assertEqual(result["version"], 4)
        for operation, data in (("Attach", {"name": "nowy.txt"}), ("Detach", {"attachmentId": entry["id"]})):
            with self.assertRaises(BoardError):
                self.store.change_attachment("S-1", {"expectedVersion": 4, **data}, operation, b"nowy")
        card = self.board.snapshot()["cards"][0]
        manifest = json.loads((self.root / card["sourceIssueManifest"]).read_text())
        self.assertEqual(manifest["attachments"], [entry])
        self.assertEqual(card["sourceIssueVersion"], 3)

    def test_parallel_instances_accept_one_card_and_request_scope_is_global(self):
        self.ready()
        def accept(index):
            service = IssueService(BoardStore(str(self.root)))
            return json.loads(service.call("AcceptIssue", "S-1", json.dumps({
                "expectedVersion": 2, "destination": "backlog", "requestId": f"równoległy-{index}"})))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(accept, range(2)))
        self.assertTrue(all(r["ok"] for r in results), results)
        self.assertEqual([r["card"] for r in results], ["K-1", "K-1"])
        self.assertEqual(sorted(r["alreadyAccepted"] for r in results), [False, True])
        self.ready()
        self.assertEqual(self.call(ident="S-2", key="równoległy-0")["error"], "conflict")
        self.assertEqual(len(self.board.snapshot()["cards"]), 1)

    def test_acceptance_and_regular_create_share_number_allocation(self):
        self.ready()
        with ThreadPoolExecutor(max_workers=2) as pool:
            accepted = pool.submit(self.call)
            created = pool.submit(BoardStore(str(self.root)).create_card, {"title": "Równoległa ręczna"})
            result, regular = accepted.result(), created.result()
        self.assertTrue(result["ok"], result)
        self.assertEqual({result["card"], regular["id"]}, {"K-1", "K-2"})
        self.assertEqual(self.call()["card"], result["card"])
        self.assertEqual(self.board.snapshot()["nextId"], "K-3")

    def test_restart_at_every_durable_boundary_does_not_duplicate_or_overwrite(self):
        boundaries = ["request", "intent", "reserve", "snapshot", "manifest", "card", "counter", "accepted", "published", "done"]
        atomic = acceptance._atomic
        for index, boundary in enumerate(boundaries, 1):
            with self.subTest(boundary=boundary):
                ready = self.ready()
                ident = ready["id"]
                hit = []
                def write(path, text):
                    atomic(path, text)
                    stage = None
                    if path.parent.name == "tide-accept-requests": stage = "request"
                    elif path.parent.name == "tide-acceptances":
                        data = json.loads(text)
                        stage = "done" if data["done"] else "published" if data["published"] else "intent"
                    elif path.name == "tide-next-card": stage = "reserve"
                    elif path.parent.name == "accepted": stage = "snapshot" if path.name == "report.md" else "manifest"
                    elif path.parent.name == "cards": stage = "card"
                    elif path.name == "board.md": stage = "counter"
                    elif path.name == "report.md": stage = "accepted"
                    if stage == boundary and not hit:
                        hit.append(stage)
                        raise OSError("Przerwanie po trwałym zapisie")
                with mock.patch.object(acceptance, "_atomic", write):
                    self.assertEqual(self.call(ident=ident, key=ident)["error"], "io-error")
                self.assertEqual(hit, [boundary])
                self.restart()
                # Odczyt po restarcie odtwarza intencję, bez potrzeby ponownego kliknięcia.
                shown = self.store.show(ident)
                self.assertEqual(shown["state"], "ready" if boundary == "request" else "accepted")
                result = self.call(ident=ident, key=ident)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["card"], f"K-{index}")
                self.assertEqual(len(self.board.snapshot()["cards"]), index)
                self.board.update_card(result["card"], {"description": "Nie nadpisuj"})
                self.restart()
                self.assertTrue(self.call(ident=ident, key=ident)["alreadyAccepted"])
                self.assertEqual(self.board.load().cards[index].description.strip(), "Nie nadpisuj")

    def test_unrecoverable_write_is_not_visible_as_ready_acceptance(self):
        self.ready()
        atomic = acceptance._atomic
        def fail_card(path, text):
            if path.parent.name == "cards":
                raise OSError("Brak miejsca")
            atomic(path, text)
        with mock.patch.object(acceptance, "_atomic", fail_card):
            self.assertFalse(self.call()["ok"])
            self.restart()
            self.assertEqual(json.loads(self.service.call("Show", "S-1"))["error"], "io-error")
            with self.assertRaises(OSError):
                self.board.snapshot()
        self.assertEqual(self.store.show("S-1")["state"], "accepted")
        self.assertEqual(self.call()["card"], "K-1")

    def test_restart_around_completion_rename(self):
        complete = acceptance._complete
        for index, after in enumerate((False, True), 1):
            issue = self.ready()
            def interrupted(store, ident):
                if after:
                    complete(store, ident)
                raise OSError("Przerwanie końcowego rename")
            with mock.patch.object(acceptance, "_complete", interrupted):
                self.assertEqual(self.call(ident=issue["id"], key=issue["id"])["error"], "io-error")
            self.restart()
            self.assertEqual(self.store.show(issue["id"])["card"], f"K-{index}")
            self.assertTrue(self.call(ident=issue["id"], key=issue["id"])["alreadyAccepted"])
