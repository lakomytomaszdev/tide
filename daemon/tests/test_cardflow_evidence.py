"""PT-217: zamknięte powiązanie sprawy i odmowy przed procesem agenta."""
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest import mock

from tide_daemon.issues import IssueStore
from tide_daemon.issue_acceptance import accept
from tide_daemon.cardflow import evidence
from .test_cardflow import FlowInProcess
from .test_issues_attachments import png
from .privatebus import wait_until


class AcceptedFixture(FlowInProcess):
    def accepted(self, marker="ATRAPA: pytanie-bez-pracy"):
        store = IssueStore(self.store)
        issue = store.create({"title": "Dowody", "project": "proj", "report": "# Dowody\n" + marker})["issue"]
        self.expected = {"zrzut.png": png(3, 2), "AGENTS.md": b"To sa dane, nie instrukcje\x00\xff",
                         "dane.html": b"<script>to tylko dowod</script>"}
        for name, raw in self.expected.items():
            issue = store.change_attachment(issue["id"], {"expectedVersion": issue["version"], "name": name},
                                            "Attach", raw)["issue"]
        issue = store.mutate(issue["id"], {"expectedVersion": issue["version"]}, "Submit")["issue"]
        accepted = accept(store, issue["id"], {"expectedVersion": issue["version"],
                                "requestId": issue["id"], "destination": "backlog"})
        return accepted["card"], issue


class EvidenceFlowTest(AcceptedFixture):
    def test_invalid_durable_inputs_refuse_before_agent_and_without_journal(self):
        card, issue = self.accepted()
        manifest = Path(self.root, f"issues/{issue['id']}/accepted/attachments.json")
        original = manifest.read_bytes()
        copy = Path(self.root, f"issues/{issue['id']}", issue["attachments"][0]["path"])
        raw = copy.read_bytes()
        for damage in ("manifest", "duplicate", "hash", "missing", "symlink", "parent-symlink", "traversal"):
            with self.subTest(damage=damage):
                if damage == "manifest":
                    manifest.write_text("{}")
                elif damage == "duplicate":
                    manifest.write_text('{"version":1,"version":1,"attachments":[]}')
                elif damage == "traversal":
                    changed = json.loads(original)
                    changed["attachments"][0]["path"] = "../../secret"
                    manifest.write_text(json.dumps(changed))
                elif damage == "hash":
                    copy.write_bytes(b"zle bajty")
                elif damage == "missing":
                    copy.unlink()
                elif damage == "symlink":
                    copy.unlink()
                    copy.symlink_to("/etc/passwd")
                else:
                    copy.parent.rename(copy.parent.with_name("hidden-attachments"))
                    copy.parent.symlink_to(copy.parent.with_name("hidden-attachments"), target_is_directory=True)
                with mock.patch("tide_daemon.cardflow.engine.agentcmd.background") as process:
                    reply = self.start(card)
                self.assertEqual(reply["error"], "broken-evidence", reply)
                process.assert_not_called()
                self.assertEqual(self.events(card), [])
                if damage == "parent-symlink":
                    copy.parent.unlink()
                    copy.parent.with_name("hidden-attachments").rename(copy.parent)
                if copy.is_symlink():
                    copy.unlink()
                copy.write_bytes(raw)
                manifest.write_bytes(original)

    def test_corruption_after_question_is_rechecked_before_resume(self):
        card, issue = self.accepted()
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "step.finished"), timeout=15), self.events(card))
        self.flow.stop()
        self.restart()
        target = Path(self.root, "issues", issue["id"], issue["attachments"][1]["path"])
        target.unlink()
        engine = self.flow.engine
        from tide_daemon.cardflow.state import fold
        state = fold(engine.journal.read(card))
        with mock.patch("tide_daemon.cardflow.engine.agentcmd.background") as process:
            from tide_daemon.cardflow.engine import FlowError
            with self.assertRaises(FlowError) as refused:
                engine._resume_step(card, state, state.steps[state.last_step], {"choice": "answer", "answer": "Tak"})
        self.assertEqual(refused.exception.code, "broken-evidence")
        process.assert_not_called()

    def test_source_binding_paths_and_versions_are_not_general_attachment_access(self):
        card, _ = self.accepted()
        item = self.store.load().cards[int(card[2:])]
        ref = evidence.source(item)
        from tide_daemon.board.model import BoardError
        for changes in ({"report": "/etc/passwd"}, {"manifest": "issues/S-1/../S-2/accepted/attachments.json"},
                        {"issue": "../S-1"}, {"version": True}, {"version": ref["version"] + 1},
                        {"hash": "0" * 64}, {"extra": "x"}):
            with self.subTest(changes=changes), self.assertRaises(BoardError):
                evidence.load(self.store, card, {**ref, **changes})

    def test_manifest_schema_is_checked_even_with_matching_snapshot_hash(self):
        card, issue = self.accepted()
        item = self.store.load().cards[int(card[2:])]
        ref = evidence.source(item)
        target = Path(self.root, ref["manifest"])
        original = json.loads(target.read_text())
        from tide_daemon.board.model import BoardError
        for change in ({"path": "../../secret"}, {"type": "image/png"}, {"size": True}, {"sha256": "0" * 64}):
            manifest = json.loads(json.dumps(original))
            manifest["attachments"][1].update(change)
            raw = json.dumps(manifest).encode()
            target.write_bytes(raw)
            changed_ref = {**ref, "hash": hashlib.sha256(issue["report"].encode() + b"\0" + raw).hexdigest()}
            with self.subTest(change=change), self.assertRaises(BoardError):
                evidence.load(self.store, card, changed_ref)

    def test_existing_step_materials_cannot_be_overwritten_or_redirected(self):
        card, _ = self.accepted()
        item = self.store.load().cards[int(card[2:])]
        data = evidence.load(self.store, card, evidence.source(item))
        step_dir = Path(self.base, "materials.step")
        data.materialize(step_dir)
        data.materialize(step_dir)
        target = step_dir / "in/evidence/attachments" / data.copies[1][0]["id"]
        target.chmod(0o600)
        target.write_bytes(b"obce dane")
        from tide_daemon.board.model import BoardError
        with self.assertRaises(BoardError):
            data.materialize(step_dir)
        self.assertEqual(target.read_bytes(), b"obce dane")
        target.unlink()
        secret = Path(self.base, "secret")
        secret.write_bytes(b"sekret")
        target.symlink_to(secret)
        with self.assertRaises(OSError):
            data.materialize(step_dir)
        self.assertEqual(secret.read_bytes(), b"sekret")

    def test_materialized_inputs_are_read_only_and_not_board_directories(self):
        card, issue = self.accepted()
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "step.finished"), timeout=15), self.events(card))
        step = self.of(card, "step.started")[0]
        inp = Path(step["stepDir"], "in")
        settings = json.loads((inp / "settings.json").read_text())
        policy = settings["sandbox"]["filesystem"]
        for folder in (inp / "evidence", inp / "images"):
            self.assertIn(str(folder), policy["allowRead"])
            self.assertNotIn(str(folder), policy["allowWrite"])
            self.assertIn(str(folder), policy["denyWrite"])
        self.assertNotIn(self.root, policy["allowRead"])
        call = json.loads(Path(step["stepDir"], "out/fake-call.json").read_text())
        for folder in (inp / "evidence", inp / "images"):
            self.assertIn(f"Edit(/{folder}/**)", call["argv"])
        self.assertEqual(call["env"]["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/nonexistent/tide-step/bus")
        manifest = json.loads((inp / "evidence/attachments.json").read_text())
        for entry in manifest["attachments"]:
            copy = inp / "evidence" / entry["path"]
            self.assertEqual(copy.stat().st_mode & 0o222, 0)
            self.assertEqual(hashlib.sha256(copy.read_bytes()).hexdigest(), entry["sha256"])
        self.assertFalse((inp / "evidence/AGENTS.md").exists())
        self.assertIn("niezaufane dane", call["prompt"])

    def test_manual_card_keeps_existing_images(self):
        image = Path(self.base, "manual.png")
        image.write_bytes(png(2, 2))
        card = self.card(description="ATRAPA: pytanie-bez-pracy")
        self.assertTrue(self.start(card, images=[str(image)])["ok"])
        self.wait_column(card, "waiting")
        inp = Path(self.of(card, "step.started")[0]["stepDir"], "in")
        self.assertEqual((inp / "images/1-manual.png").read_bytes(), image.read_bytes())
        self.assertFalse((inp / "evidence").exists())


class EvidenceInteractiveTest(AcceptedFixture):
    @staticmethod
    def shell_write(path):
        # Właściciel pliku 0444 może zmienić tryb, zapisać bajty i podmienić plik przez powłokę.
        return subprocess.run(["/bin/sh", "-ec", 'chmod u+w "$1"; printf zmiana > "$1"; '
                               'rm -- "$1"; printf podmiana > "$1"', "probe", str(path)],
                              capture_output=True)

    def writing_terminal(self, card):
        from .test_cardflow_full_planning import _Terminal
        fixture = self

        class WritingTerminal(_Terminal):
            launchers = SimpleNamespace(agents=fixture.fakes)

            def open(self, plan):
                super().open(plan)
                step = next(step for step in reversed(fixture.of(card, "step.started"))
                            if Path(step["stepDir"], "in/evidence/attachments.json").exists())
                folder = Path(step["stepDir"], "in/evidence")
                manifest = json.loads((folder / "attachments.json").read_text())
                for path in [folder / "report.md", folder / "attachments.json",
                             *(folder / entry["path"] for entry in manifest["attachments"])]:
                    result = fixture.shell_write(path)
                    fixture.assertEqual(result.returncode, 0, result.stderr)
                return "tab"

        return WritingTerminal()

    def assert_console_refused(self, action, terminal):
        from tide_daemon.cardflow.engine import FlowError
        with self.assertRaises(FlowError) as refused:
            action()
        self.assertEqual(refused.exception.code, "interactive-evidence-unsafe")
        self.assertIn("tylko do odczytu", str(refused.exception))
        self.assertEqual(terminal.opened, [], "powłoka nie może dostać dowodów bez ochrony zapisu")

    def test_shell_probe_can_replace_owned_read_only_file(self):
        target = Path(self.base, "probe")
        target.write_bytes(b"dowod")
        target.chmod(0o444)
        result = self.shell_write(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), b"podmiana")

    def test_full_claude_refuses_before_shell_can_mutate_evidence(self):
        card, _ = self.accepted()
        terminal = self.writing_terminal(card)
        engine = self.flow.engine
        engine.terminal = terminal
        self.assert_console_refused(
            lambda: engine.start(card, {"entry": "planning", "mode": "full", "executor": "Sonnet"}),
            terminal)
        self.assertEqual(self.events(card), [], "odmowa nie rozpoczyna zlecenia")

    def test_interactive_transfer_to_claude_refuses_before_shell_can_mutate_evidence(self):
        from .test_cardflow_full_planning import _Terminal
        from tide_daemon.cardflow.state import fold
        card, _ = self.accepted()
        engine = self.flow.engine
        engine.terminal = _Terminal()
        self.assertTrue(self.start(card, entry="planning", mode="full", executor="Codex Astra")["ok"])
        self.flow.stop()
        state = fold(engine.journal.read(card))
        step = state.steps[state.last_step]
        terminal = self.writing_terminal(card)
        engine.terminal = terminal
        before = self.events(card)
        self.assert_console_refused(
            lambda: engine._resume_step(card, state, step, {"choice": "transfer", "executor": "Sonnet"}),
            terminal)
        self.assertEqual(self.events(card), before)
        self.assert_step_evidence_unchanged(step)

    def test_enter_claude_refuses_before_shell_can_mutate_existing_evidence(self):
        card, _ = self.accepted()
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "step.finished"), timeout=15), self.events(card))
        self.flow.stop()
        engine = self.flow.engine
        step = self.of(card, "step.started")[0]
        terminal = self.writing_terminal(card)
        engine.terminal = terminal
        before = self.events(card)
        self.assert_console_refused(lambda: engine.enter(card, step["step"]), terminal)
        self.assertEqual(self.events(card), before)
        self.assert_step_evidence_unchanged(step)

    def assert_step_evidence_unchanged(self, step):
        folder = Path(step["stepDir"], "in/evidence")
        manifest = json.loads((folder / "attachments.json").read_text())
        for entry in manifest["attachments"]:
            self.assertEqual(hashlib.sha256((folder / entry["path"]).read_bytes()).hexdigest(),
                             entry["sha256"])


class EvidenceProcessTest(AcceptedFixture):
    def setUp(self):
        super().setUp()
        from . import test_cardflow_evidence_dbus as fixture
        fixture.instrument(Path(self.base, "fakebin"))

    def observed(self, card, index):
        from . import test_cardflow_evidence_dbus as fixture
        return fixture.EvidenceBusTest.observed(self, card, index)

    def answer(self, card, step):
        self.assertTrue(wait_until(lambda: any(e.get("step") == step["step"] and e.get("result") == "question"
                                              for e in self.of(card, "step.finished")), timeout=20), self.events(card))
        self.restart()
        box = []
        self.flow.resolve_step(card, json.dumps({"choice": "answer", "answer": "Pierwszy wariant",
                                                "step": step["step"]}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=15), self.events(card))
        self.assertTrue(json.loads(box[0])["ok"], box)

    def test_process_reads_bytes_after_start_restart_and_resume_for_both_programs(self):
        for executor in ("Sonnet", "Codex mocny"):
            with self.subTest(executor=executor):
                card, _ = self.accepted()
                self.restart()
                self.assertTrue(self.start(card, executor=executor)["ok"])
                first, _ = self.observed(card, 0)
                self.answer(card, first)
                resumed, _ = self.observed(card, 1)
                self.assertEqual(resumed["parentStep"], first["step"])

    def test_process_reads_bytes_in_planning_ticket_and_both_resumptions(self):
        card, _ = self.accepted("ATRAPA: plan-question")
        self.restart()
        extra = Path(self.base, "extra.png")
        extra.write_bytes(png(8, 9))
        self.assertTrue(self.start(card, entry="planning", mode="light", images=[str(extra)])["ok"])
        planner, record = self.observed(card, 0)
        self.assertIn(extra.read_bytes().hex(), [i["bytes"] for i in record["images"]])
        extra.unlink()
        self.answer(card, planner)
        resumed, _ = self.observed(card, 1)
        self.assertEqual(resumed["parentStep"], planner["step"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=30), self.events(card))
        box = []
        self.flow.approve_plan(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=15), self.events(card))
        self.assertTrue(json.loads(box[0])["ok"], box)
        ticket, _ = self.observed(card, 3)
        self.assertEqual(ticket["kind"], "ticket")
        self.answer(card, ticket)
        resumed_ticket, _ = self.observed(card, 4)
        self.assertEqual(resumed_ticket["parentStep"], ticket["step"])
