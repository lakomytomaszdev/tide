"""PT-222: ta sama akceptacja i zlecenie, osobno replay oraz decyzja retry."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from .test_cardflow import FlowInProcess
from .privatebus import wait_until
from tide_daemon.issues import IssueService, IssueStore
from tide_daemon.cardflow.engine import FlowError
from tide_daemon import issue_start


class IssueGoTest(FlowInProcess):
    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.issues = IssueStore(self.store)
        self.service = IssueService(self.store, self.flow.engine)

    def ready(self, report="ATRAPA: pytanie-bez-pracy"):
        issue = self.issues.create({"title": "Sprawa do zlecenia", "project": "proj", "report": report})["issue"]
        return self.issues.mutate(issue["id"], {"expectedVersion": 1}, "Submit")["issue"]

    def call(self, method, ident="S-1", request=None):
        args = (ident,) if request is None else (ident, json.dumps(request))
        return json.loads(self.service.call(method, *args))

    def go(self, ident="S-1", key="go", version=2, **extra):
        return self.call("AcceptIssue", ident, {"destination": "go", "expectedVersion": version,
            "requestId": key, "launch": {"entry": "now", "executor": "Sonnet"}, **extra})

    def retry(self, start, key="retry", **extra):
        return self.call("RetryIssueStart", request={"intentId": start["intentId"],
            "expectedStartVersion": start["startVersion"], "requestId": key, **extra})

    def fail_before_handoff(self):
        original = self.flow.engine._start
        def start(*args, **kwargs):
            if kwargs.get("_order"):
                raise FlowError("test-failure", "Przyczyna wymagająca naprawy")
            return original(*args, **kwargs)
        return mock.patch.object(self.flow.engine, "_start", side_effect=start)

    def test_failed_go_explicit_retry_one_card_one_success_and_historical_replay(self):
        self.ready()
        with self.fail_before_handoff():
            failed = self.go()
        self.assertTrue(failed["ok"], failed)
        self.assertTrue(failed["accepted"])
        self.assertEqual(failed["start"]["state"], "start-error", failed)
        self.assertTrue(failed["start"]["retryable"])
        self.assertEqual(self.issues.show("S-1")["state"], "accepted")
        self.assertEqual(self.go(), failed, "replay nie uruchamia StartCard")
        self.assertEqual(self.of("K-1", "card.started"), [])
        retried = self.retry(failed["start"])
        self.assertTrue(retried["ok"], retried)
        self.assertTrue(retried["started"], retried)
        self.assertEqual(retried["card"], failed["card"])
        self.assertEqual(retried["start"]["intentId"], failed["start"]["intentId"])
        self.assertEqual(retried["start"]["orderId"], failed["start"]["orderId"])
        self.assertNotEqual(retried["start"]["attemptId"], failed["start"]["attemptId"])
        self.assertEqual(self.retry(failed["start"]), retried)
        self.assertEqual(self.go(), failed)
        self.assertEqual(len(self.of("K-1", "card.started")), 1)
        self.assertEqual(len(self.store.snapshot()["cards"]), 1)
        shown = self.call("Show")
        self.assertEqual(shown["start"]["state"], "started")
        self.assertGreater(shown["start"]["startVersion"], failed["start"]["startVersion"])
        self.flow.engine.journal.append("K-1", "card.closed")
        self.assertEqual(self.call("Show")["start"]["state"], "completed")
        self.assertEqual(self.go(), failed)
        self.assertEqual(self.retry(failed["start"]), retried)
        self.assertEqual(self.retry(shown["start"], key="completed")["error"], "start-conflict")
        self.assertEqual(len(self.of("K-1", "card.started")), 1)

    def test_validation_and_read_version_precede_acceptance_and_reservation(self):
        self.ready()
        for launch in ({"entry": "now", "executor": "brak"}, {"entry": "now", "executor": "Sonnet", "check": "bad"},
                       {"entry": "now", "executor": "Sonnet", "baseRef": "HEAD"},
                       {"entry": "import", "planPath": "/missing"}, {"entry": "spec"}, [],
                       {"entry": "now", "executor": "Sonnet", "foreign": True}):
            with self.subTest(launch=launch):
                self.assertFalse(self.go(launch=launch)["ok"])
                self.assertEqual(self.issues.show("S-1")["state"], "ready")
                self.assertEqual(self.store.snapshot()["cards"], [])
        self.assertEqual(self.go(version=1)["error"], "version-conflict")
        self.assertEqual(self.store.snapshot()["nextId"], "K-1")
        preview = self.call("PreviewIssueLaunch", request={"expectedVersion": 2})
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(self.store.snapshot()["cards"], [])
        self.issues.mutate("S-1", {"expectedVersion": 2, "report": "Nowszy raport"}, "Update")
        self.assertEqual(self.go()["error"], "version-conflict")
        self.assertEqual(self.store.snapshot()["cards"], [])

    def test_many_failed_retries_with_max_report_preserve_show_success_and_replays(self):
        self.ready("R" * (64 * 1024))
        history = []
        with self.fail_before_handoff():
            original = self.go()
            current = original
            for number in range(40):
                start = current["start"]
                key = f"many-retries-{number}"
                if number == 12:
                    with self.interrupt_retry_response():
                        self.assertEqual(self.retry(start, key=key)["error"], "io-error")
                    current = {"ok": True, "apiVersion": 1,
                               **issue_start._response(self.issues, issue_start._load(self.issues, "S-1"))}
                else:
                    current = self.retry(start, key=key)
                self.assertTrue(current["ok"], (number, current))
                self.assertEqual(current["start"]["state"], "start-error")
                self.assertTrue(current["start"]["retryable"])
                self.assertEqual(self.call("Show")["start"], current["start"])
                history.append((start, key, current))
        self.restart()
        self.flow.stop()
        self.service = IssueService(self.store, self.flow.engine)
        success = self.retry(self.call("Show")["start"], key="many-retries-success")
        self.assertTrue(success["started"], success)
        self.assertEqual(success["card"], original["card"])
        self.assertEqual(success["start"]["intentId"], original["start"]["intentId"])
        self.assertEqual(success["start"]["orderId"], original["start"]["orderId"])
        self.flow.engine.journal.append("K-1", "card.closed")
        self.assertEqual(self.call("Show")["start"]["state"], "completed")
        self.assertEqual(self.go(), original)
        for start, key, response in history:
            self.assertEqual(self.retry(start, key=key), response)
        self.assertEqual(len(self.store.snapshot()["cards"]), 1)
        self.assertEqual(len(self.of("K-1", "card.started")), 1)

    def test_parallel_retries_stale_versions_changed_payload_and_changed_card(self):
        self.ready()
        with self.fail_before_handoff():
            failed = self.go()
        start = failed["start"]
        self.assertEqual(self.go(version=3)["error"], "conflict")
        self.assertEqual(self.go(version=2.0)["error"], "conflict")
        self.assertEqual(self.retry(start, launch={})["error"], "bad-request")
        self.assertEqual(self.retry(start, expectedStartVersion=start["startVersion"] - 1)["error"], "start-conflict")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda key: self.retry(start, key=key), ("parallel-1", "parallel-2")))
        self.assertEqual(sum(r["ok"] for r in results), 1, results)
        self.assertEqual(len(self.of("K-1", "card.started")), 1)
        winner = next(r for r in results if r["ok"])
        self.assertEqual(self.retry(start, key="go")["error"], "conflict")
        self.assertEqual(self.retry(start, key="parallel-1", intentId="other")["error"], "conflict")
        self.assertEqual(winner["card"], "K-1")

    def test_changed_generation_blocks_retry_without_second_order(self):
        self.ready()
        with self.fail_before_handoff():
            failed = self.go()
        self.store.update_card("K-1", {"description": "Zmieniona karta"})
        current = self.call("Show")["start"]
        self.assertEqual(current["state"], "reconciling")
        self.assertFalse(current["retryable"])
        self.assertEqual(self.retry(failed["start"])["error"], "start-conflict")
        self.assertEqual(self.of("K-1", "card.started"), [])

    def test_queue_is_existing_startcard_queue_and_release_does_not_start_second_order(self):
        self.ready()
        engine = self.flow.engine
        with mock.patch.object(engine, "_wait_reason", return_value="czeka na wolne miejsce u dostawcy"):
            queued = self.go()
        self.assertTrue(queued["ok"], queued)
        self.assertTrue(queued["queued"], queued)
        self.assertFalse(queued["started"])
        self.assertFalse(queued["startError"])
        self.assertFalse(queued["start"]["retryable"])
        engine.tick()
        self.assertEqual(self.call("Show")["start"]["state"], "started")
        self.assertEqual(self.go(), queued)
        self.assertEqual(len(self.of("K-1", "card.started")), 1)
        self.assertEqual(len(self.of("K-1", "step.started")), 1)

    def test_truncated_journal_blocks_recovery_and_retry(self):
        self.ready()
        with self.fail_before_handoff():
            failed = self.go()
        journal = self.flow.engine.journal
        path = Path(journal.mirror_path("K-1"))
        path.write_text(path.read_text() + '{"type":"card.started"')
        before = path.read_bytes()
        self.assertEqual(self.call("Show")["start"]["state"], "reconciling")
        with mock.patch.object(self.flow.engine, "_start", side_effect=AssertionError("Nie wolno przekazać drugi raz")):
            self.flow.engine.tick()
            self.assertEqual(self.retry(failed["start"])["error"], "start-conflict")
        self.assertEqual(path.read_bytes(), before)

    def test_changed_pinned_base_refuses_retry_before_creating_an_attempt(self):
        from .test_cardflow_dbus import git
        self.ready()
        with self.fail_before_handoff():
            failed = self.go()
        git(self.repo, "commit", "--allow-empty", "-m", "Advance base")
        current = self.call("Show")["start"]
        self.assertFalse(current["retryable"])
        self.assertEqual(self.retry(failed["start"])["error"], "start-conflict")
        self.assertEqual(len(self.of("K-1", "order.dispatching")), 1)

    def test_planning_preflight_uses_existing_project_validation_without_publishing_extras(self):
        from .test_cardflow_dbus import git, CLAUDE_MD
        other = Path(self.base, "extra-project")
        other.mkdir()
        git(str(other), "init", "-q")
        (other / "CLAUDE.md").write_text(CLAUDE_MD)
        git(str(other), "add", "-A")
        git(str(other), "commit", "-m", "Initialize extra project")
        self.ready()
        launch = {"entry": "planning", "mode": "light", "executor": "Sonnet", "projects": [str(other)]}
        with self.fail_before_handoff():
            result = self.go(launch=launch)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["start"]["state"], "start-error")
        self.assertEqual(set(self.store.load().projects), {"proj"})

    def test_process_reads_original_durable_evidence_after_explicit_retry(self):
        from . import test_cardflow_evidence_dbus as evidence_fixture
        from .test_issues_attachments import png
        evidence_fixture.instrument(Path(self.base, "fakebin"))
        issue = self.issues.create({"title": "Dowody", "project": "proj",
                                   "report": "# Dowody\nATRAPA: pytanie-bez-pracy"})["issue"]
        self.expected = {"zrzut.png": png(4, 3), "AGENTS.md": b"Niezaufane dane\x00\xff",
                         "dane.html": b"<script>dowod</script>"}
        for name, raw in self.expected.items():
            issue = self.issues.change_attachment(issue["id"], {"expectedVersion": issue["version"], "name": name},
                                                  "Attach", raw)["issue"]
        issue = self.issues.mutate(issue["id"], {"expectedVersion": issue["version"]}, "Submit")["issue"]
        with self.fail_before_handoff():
            failed = self.go(version=issue["version"])
        retried = self.retry(failed["start"])
        self.assertTrue(retried["started"], retried)
        evidence_fixture.EvidenceBusTest.observed(self, retried["card"], 0)
        self.assertEqual(self.go(version=issue["version"]), failed)
        self.assertEqual(len(self.of(retried["card"], "card.started")), 1)

    def test_partial_journal_before_or_after_acceptance_never_duplicates(self):
        self.ready()
        journal = self.flow.engine.journal
        append = journal.append
        def broken(card, kind, **fields):
            result = append(card, kind, **fields)
            if kind == "order.dispatching":
                raise OSError("po trwałym zapisie")
            return result
        with mock.patch.object(journal, "append", side_effect=broken):
            reply = self.go()
        self.assertEqual(reply["start"]["state"], "reconciling", reply)
        self.assertFalse(reply["start"]["retryable"])
        self.assertEqual(self.retry(reply["start"])["error"], "start-conflict")
        self.assertEqual(self.of("K-1", "card.started"), [])
        # Po card.started przekazanie jest przyjęte mimo utraty odpowiedzi zapisu.
        self.ready()
        def after_acceptance(card, kind, **fields):
            result = append(card, kind, **fields)
            if kind == "card.started":
                raise OSError("odpowiedź po fsync zginęła")
            return result
        with mock.patch.object(journal, "append", side_effect=after_acceptance):
            reply = self.go("S-2", key="go-2")
        self.assertEqual(reply["start"]["state"], "start-error", reply)
        self.assertTrue(reply["startError"])
        self.assertFalse(reply["start"]["retryable"])
        self.assertEqual(self.go("S-2", key="go-2"), reply)
        self.assertEqual(len(self.of("K-2", "card.started")), 1)
        self.flow.engine.tick()
        self.assertEqual(self.call("Show", "S-2")["start"]["state"], "started")
        self.assertEqual(self.go("S-2", key="go-2"), reply)
        self.assertEqual(len(self.of("K-2", "card.started")), 1)

    def test_prepared_acceptance_is_reserved_before_journal_and_recovery_continues_it(self):
        self.ready()
        with mock.patch.object(issue_start, "_dispatch", side_effect=OSError("Przerwanie przed przekazaniem")):
            self.assertEqual(self.go()["error"], "io-error")
        self.assertEqual(self.issues.show("S-1")["state"], "accepted")
        with self.assertRaises(FlowError) as refused:
            self.flow.engine.start("K-1", {"entry": "now", "executor": "Sonnet"})
        self.assertEqual(refused.exception.code, "start-reserved")
        issue_start.recover(self.issues, self.flow.engine)
        self.assertEqual(self.call("Show")["start"]["state"], "started")
        self.assertTrue(self.go()["started"])
        self.assertEqual(len(self.of("K-1", "card.started")), 1)

    def test_restart_lost_original_response_and_lost_retry_response(self):
        self.ready()
        save = issue_start.acceptance._save
        def lose_response(store, path, value):
            save(store, path, value)
            if path.startswith(issue_start.RESULTS) and "response" in value:
                raise OSError("utracona odpowiedź")
        with self.fail_before_handoff(), mock.patch.object(issue_start.acceptance, "_save", side_effect=lose_response):
            self.assertEqual(self.go()["error"], "io-error")
        original = self.go()
        self.assertEqual(original["start"]["state"], "start-error")
        self.restart()
        self.flow.stop()
        self.service = IssueService(self.store, self.flow.engine)
        self.assertEqual(self.go(), original)
        with mock.patch.object(issue_start.acceptance, "_save", side_effect=lose_response):
            self.assertEqual(self.retry(original["start"])["error"], "io-error")
        retried = self.retry(original["start"])
        self.assertTrue(retried["started"], retried)
        self.assertEqual(self.go(), original)
        self.assertEqual(len(self.of("K-1", "card.started")), 1)

    def interrupt_retry_response(self):
        save = issue_start.acceptance._save

        def interrupted(store, path, value):
            if (path.startswith(issue_start.RESULTS) and "response" in value and
                    value["payload"].get("operation") == "retry-start"):
                raise OSError("Przerwanie przed zapisem odpowiedzi retry")
            return save(store, path, value)
        return mock.patch.object(issue_start.acceptance, "_save", side_effect=interrupted)

    def test_retry_result_survives_missing_receipt_response_restart_and_later_retry(self):
        self.ready()
        with self.fail_before_handoff():
            failed = self.go()
            with self.interrupt_retry_response():
                self.assertEqual(self.retry(failed["start"], key="retry-a")["error"], "io-error")
        # Projekcja A jest trwała, ale receipt nie ma jeszcze odpowiedzi.
        historical = issue_start._response(self.issues, issue_start._load(self.issues, "S-1"))
        self.assertEqual(historical["start"]["state"], "start-error")
        path = f"{issue_start.RESULTS}/{issue_start._key('retry-a')}.json"
        self.assertNotIn("response", issue_start.acceptance._read(self.issues, path))
        self.restart()
        self.flow.stop()
        self.service = IssueService(self.store, self.flow.engine)
        current = self.call("Show")["start"]
        success = self.retry(current, key="retry-b")
        self.assertTrue(success["started"], success)
        replay = self.retry(failed["start"], key="retry-a")
        self.assertEqual(replay, {"ok": True, "apiVersion": 1, **historical})
        self.assertEqual(self.retry(failed["start"], key="retry-a"), replay)
        self.assertEqual(self.call("Show")["start"]["state"], "started")
        self.assertEqual(len(self.of("K-1", "card.started")), 1)

    def test_retry_result_survives_missing_receipt_response_and_completed(self):
        self.ready()
        with self.fail_before_handoff():
            failed = self.go()
        with self.interrupt_retry_response():
            self.assertEqual(self.retry(failed["start"])["error"], "io-error")
        historical = issue_start._response(self.issues, issue_start._load(self.issues, "S-1"))
        self.assertEqual(historical["start"]["state"], "started")
        self.flow.engine.journal.append("K-1", "card.closed")
        self.restart()
        self.flow.stop()
        self.service = IssueService(self.store, self.flow.engine)
        self.assertEqual(self.call("Show")["start"]["state"], "completed")
        self.assertEqual(self.retry(failed["start"]), {"ok": True, "apiVersion": 1, **historical})
        self.assertEqual(len(self.of("K-1", "card.started")), 1)

    def test_retry_snapshot_survives_interruption_before_projection_save_and_completed(self):
        self.ready()
        with self.fail_before_handoff():
            failed = self.go()
        save = issue_start.acceptance._save

        def interrupted(store, path, value):
            if path.startswith(issue_start.INTENTS) and value["state"] == "started":
                raise OSError("Przerwanie po migawce, przed projekcją")
            return save(store, path, value)

        with mock.patch.object(issue_start.acceptance, "_save", side_effect=interrupted):
            self.assertEqual(self.retry(failed["start"])["error"], "io-error")
        intent = issue_start._load(self.issues, "S-1")
        self.assertEqual(intent["state"], "dispatching")
        historical = issue_start._retry_response(self.issues, intent, intent["attemptId"])
        self.assertEqual(historical["start"]["state"], "started")
        self.flow.engine.journal.append("K-1", "card.closed")
        self.restart()
        self.flow.stop()
        self.service = IssueService(self.store, self.flow.engine)
        shown = self.call("Show")["start"]
        self.assertEqual(shown["state"], "completed")
        self.assertGreater(shown["startVersion"], historical["start"]["startVersion"])
        self.assertEqual(self.retry(failed["start"]), {"ok": True, "apiVersion": 1, **historical})
        self.assertEqual(len(self.of("K-1", "card.started")), 1)

    def test_legacy_embedded_retry_response_migration_survives_interrupted_intent_save(self):
        self.ready()
        with self.fail_before_handoff():
            failed = self.go()
            with self.interrupt_retry_response():
                self.assertEqual(self.retry(failed["start"])["error"], "io-error")
        intent = issue_start._load(self.issues, "S-1")
        attempt = intent["attemptId"]
        historical = issue_start._retry_response(self.issues, intent, attempt)
        # Odtworzenie formatu rundy 3 z nieukończonym receipt.
        intent["retryResponses"] = {attempt: historical}
        path = f"{issue_start.INTENTS}/S-1.json"
        save = issue_start.acceptance._save
        save(self.issues, path, intent)
        self.issues._path(issue_start._attempt_path(intent, attempt)).unlink()

        def interrupted(store, rel, value):
            if rel == path:
                raise OSError("Przerwanie migracji po migawkach")
            return save(store, rel, value)

        with mock.patch.object(issue_start.acceptance, "_save", side_effect=interrupted):
            self.assertEqual(self.call("Show")["error"], "io-error")
        self.assertIn("retryResponses", issue_start.acceptance._read(self.issues, path))
        self.assertEqual(issue_start._retry_response(self.issues, intent, attempt), historical)
        self.restart()
        self.flow.stop()
        self.service = IssueService(self.store, self.flow.engine)
        self.assertNotIn("retryResponses", issue_start._load(self.issues, "S-1"))
        self.assertTrue(self.retry(self.call("Show")["start"], key="after-migration")["started"])
        self.assertEqual(self.retry(failed["start"]), {"ok": True, "apiVersion": 1, **historical})
        self.assertEqual(len(self.of("K-1", "card.started")), 1)

    def assert_import_recovery(self, boundary, *, restart=False, complete=False, hard=False,
                               partial=False, remove_source=False, change_source=False, lost_copies=None):
        from . import test_cardflow_import_plan as imports
        from tide_daemon.cardflow.state import fold

        self.ready()
        path = imports.PlanImportTest.source(self, "K-1", old=not complete)
        source = Path(path).read_bytes()
        source_cards = {p: p.read_bytes() for p in Path(path).parent.glob("tickets/*.md")}
        spec_path = Path(path).with_name("spec.md")
        if complete:
            spec_path.write_text("# Przyjęta specyfikacja\nPierwotne wymagania.\n")
        source_spec = spec_path.read_bytes() if complete else b""
        launch = {"entry": "import", "planPath": path, "executor": "Sonnet"}
        journal = self.flow.engine.journal
        append, write_file = journal.append, journal.write_file

        class Interrupted(BaseException):
            pass

        def interrupt():
            raise Interrupted() if hard else OSError("Przerwanie przekazania planu")

        def interrupted_append(card, kind, **data):
            result = append(card, kind, **data)
            if kind == boundary:
                interrupt()
            return result

        def interrupted_write(card, rel, data):
            target = {"source.md": "plan/source.md", "ticket.md": "plan/import/tickets/PT-8.md"}.get(boundary)
            result = write_file(card, rel, b"urwana kopia" if partial and rel == target else data)
            if target and rel == target:
                interrupt()
            return result

        with mock.patch.object(journal, "append", side_effect=interrupted_append), \
                mock.patch.object(journal, "write_file", side_effect=interrupted_write):
            if hard:
                with self.assertRaises(Interrupted):
                    self.go(launch=launch)
            else:
                failed = self.go(launch=launch)
                self.assertEqual(failed["start"]["state"], "start-error", failed)
                self.assertFalse(failed["start"]["retryable"])

        started = self.of("K-1", "card.started")[0]
        intent = issue_start._load(self.issues, "S-1")
        self.assertEqual(len(self.of("K-1", "plan.imported")), int(boundary == "plan.imported"))
        self.assertEqual(self.of("K-1", "step.started"), [])
        if hard:
            self.assertEqual(self.of("K-1", "order.delivery-error"), [])
        if remove_source:
            Path(path).unlink()
            for card_path in source_cards:
                card_path.unlink()
            if complete:
                spec_path.unlink()
        elif change_source:
            Path(path).write_bytes(source.replace(b"Przekazanie", b"Zmienione").replace(b"po gotowym", b"zmienione"))
            for card_path in source_cards:
                card_path.write_text("# Zmieniona kartka\n\n- [ ] Inne wymaganie.\n")
            spec_path.write_text("# Zmieniona specyfikacja\n")
        if lost_copies:
            # Awaria systemu zachowała zsynchronizowany dziennik, ale utraciła
            # niezależne zapisy wejść lub ich końcówki, już po plan.imported.
            copies = list(Path(self.root, "flow/K-1/plan").rglob("*.md"))
            self.assertEqual(len(copies), 3 + len(source_cards) if complete else 1)
            for copy in copies:
                if lost_copies == "missing":
                    copy.unlink()
                else:
                    copy.write_bytes(copy.read_bytes()[:7])
        history = self.events("K-1")
        if restart:
            self.restart()
            self.flow.stop()
            self.service = IssueService(self.store, self.flow.engine)
        self.flow.engine.tick()
        state = fold(self.flow.engine.journal.read("K-1"))
        self.assertEqual(self.events("K-1")[:len(history)], history)
        self.assertIsNotNone(state.imported, self.events("K-1"))
        self.assertEqual(Path(self.root, "flow/K-1/plan/source.md").read_bytes(), source)
        if complete:
            snapshot = Path(state.imported["snapshotPath"])
            self.assertEqual(snapshot.read_bytes(), source)
            self.assertEqual(snapshot.with_name("spec.md").read_bytes(), source_spec)
            for card_path, body in source_cards.items():
                self.assertEqual((snapshot.parent / "tickets" / card_path.name).read_bytes(), body)
            self.assertIsNotNone(state.plan_proposed)
            self.assertEqual(state.plan_proposed["title"], "Przekazanie")
            self.assertEqual(state.plan_proposed["specification"].encode(), source_spec)
            self.assertEqual(Path(self.root, "flow/K-1/plan/plan.md").read_bytes(), source)
            for row in state.plan_proposed["rows"]:
                self.assertEqual(row["body"].encode(), source_cards[Path(path).parent / "tickets" / (row["workId"] + ".md")])
                self.assertEqual(Path(row["cardPath"]).read_bytes(), row["body"].encode())
            self.assertEqual(state.plan_proposed["rows"][1]["note"], "po gotowym")
            self.assertEqual(state.imported["path"], path)
        else:
            self.assertEqual([s["kind"] for s in state.steps.values()], ["plan-import"])
        def reviewed():
            self.flow.engine.tick()
            return self.of("K-1", "plan.reviewed")
        self.assertTrue(wait_until(reviewed, timeout=25), self.events("K-1"))
        current = self.call("Show")["start"]
        self.assertEqual(current["state"], "started")
        self.assertFalse(current["retryable"])
        self.assertEqual(current["intentId"], intent["intentId"])
        self.assertEqual(current["orderId"], started["orderId"])
        replay = self.go(launch=launch)
        if not hard:
            self.assertEqual(replay, failed)
        self.assertEqual(self.go(launch=launch), replay)
        self.assertEqual(self.retry(current, key="refused")["error"], "start-conflict")
        self.assertEqual(self.of("K-1", "card.started"), [started])
        self.assertEqual(len(self.of("K-1", "plan.imported")), 1)
        self.assertEqual(len(self.store.snapshot()["cards"]), 1)
        if remove_source:
            self.assertFalse(Path(path).exists())
            self.assertTrue(all(not p.exists() for p in source_cards))
        elif change_source:
            self.assertIn(b"Zmienione", Path(path).read_bytes())
            self.assertTrue(all(b"Inne wymaganie" in p.read_bytes() for p in source_cards))
            self.assertEqual(spec_path.read_text(), "# Zmieniona specyfikacja\n")
        else:
            self.assertEqual(Path(path).read_bytes(), source)
            self.assertTrue(all(p.read_bytes() == body for p, body in source_cards.items()))

    def test_import_recovers_after_started(self):
        self.assert_import_recovery("card.started")

    def test_import_recovers_after_source_copy(self):
        self.assert_import_recovery("source.md")

    def test_import_restart_after_started_without_error_handler(self):
        self.assert_import_recovery("card.started", restart=True, hard=True)

    def test_import_restart_after_source_copy_without_error_handler(self):
        self.assert_import_recovery("source.md", restart=True, hard=True)

    def test_complete_import_restart_after_started(self):
        self.assert_import_recovery("card.started", restart=True, complete=True, hard=True)

    def test_complete_import_recovers_without_original(self):
        self.assert_import_recovery("card.started", complete=True, hard=True, remove_source=True)

    def test_complete_import_restart_without_original(self):
        self.assert_import_recovery("card.started", restart=True, complete=True, hard=True, remove_source=True)

    def test_complete_import_restart_after_imported_without_original(self):
        self.assert_import_recovery("plan.imported", restart=True, complete=True, hard=True, remove_source=True)

    def test_complete_import_recovers_without_reading_changed_original(self):
        self.assert_import_recovery("card.started", complete=True, hard=True, change_source=True)

    def test_complete_import_restart_after_imported_ignores_changed_original(self):
        self.assert_import_recovery("plan.imported", restart=True, complete=True, hard=True, change_source=True)

    def test_complete_import_restart_repairs_partial_ticket_without_original(self):
        self.assert_import_recovery("ticket.md", restart=True, complete=True, hard=True, partial=True, remove_source=True)

    def test_complete_import_restart_after_imported(self):
        self.assert_import_recovery("plan.imported", restart=True, complete=True, hard=True)

    def test_complete_import_restart_repairs_missing_copies_after_imported(self):
        self.assert_import_recovery("plan.imported", restart=True, complete=True, hard=True,
                                    remove_source=True, lost_copies="missing")

    def test_complete_import_restart_repairs_partial_copies_after_imported(self):
        self.assert_import_recovery("plan.imported", restart=True, complete=True, hard=True,
                                    remove_source=True, lost_copies="partial")

    def test_import_restart_repairs_missing_copies_after_imported(self):
        self.assert_import_recovery("plan.imported", restart=True, hard=True,
                                    remove_source=True, lost_copies="missing")

    def test_import_restart_repairs_partial_copy_from_journal_without_original(self):
        self.assert_import_recovery("source.md", restart=True, hard=True, partial=True, remove_source=True)

    def test_omitted_launch_pins_light_and_saved_preferences(self):
        self.ready()
        # Ostatnie zlecenie zapisuje dostawcę i wykonawcę; nowy go nie przejmuje
        # poprzedniej gałęzi, bazy ani pełnego trybu.
        self.flow.engine.journal.append("K-90", "card.started", project="proj", executor="Codex mocny",
            preferredProvider="openai", check="manual", entry="planning", mode="full")
        with self.fail_before_handoff():
            reply = self.call("AcceptIssue", request={"destination": "go", "expectedVersion": 2, "requestId": "default"})
        intent = issue_start._load(self.issues, "S-1")
        launch = intent["launch"]
        self.assertEqual((launch["entry"], launch["mode"], launch["executor"], launch["check"]),
                         ("planning", "light", "Codex mocny", "manual"))
        self.assertEqual(launch["baseRef"], "refs/heads/master")
        self.assertEqual(launch["baseCommit"], self.master)
        self.assertEqual(reply["start"]["state"], "start-error")
