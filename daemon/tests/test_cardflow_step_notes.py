"""PT-245: zapis uwag, trwały start i doręczenie w rzeczywistym poleceniu atrapy."""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from tide_daemon.cardflow import stepnotes
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.tasks import agentcmd


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Journal(self.tmp.name + "/board", mirror=self.tmp.name + "/state")
        self.journal.append("K-1", "card.started", entry="now")
        self.journal.append("K-1", "step.started", step="s1", kind="ticket", ticket="PT-1")

    def add(self, text="Uwaga", **extra):
        return stepnotes.add(self.journal, "K-1", {"step": "s1", "orderSeq": 1, "text": text,
                                                 "requestId": "r1", **extra}, fold)

    def state(self):
        return fold(self.journal.read("K-1"))

    def test_atomic_idempotency_conflict_and_validation_without_mutation(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.add(), range(8)))
        ident = replies[0]["noteId"]
        self.assertEqual({r["noteId"] for r in replies}, {ident})
        self.assertEqual(len(self.state().step_notes), 1)
        before = self.journal.read("K-1")
        for request, code in [({"text": "Inna"}, "note-conflict"), ({"text": "  "}, "bad-note"),
                              ({"text": "ą" * 8193}, "bad-note"), ({"orderSeq": 0}, "stale-order"),
                              ({"step": "missing", "requestId": "r2"}, "stale-step")]:
            with self.subTest(request=request), self.assertRaises(stepnotes.NoteError) as caught:
                stepnotes.add(self.journal, "K-1", {"step": "s1", "orderSeq": 1, "text": "Uwaga",
                                                   "requestId": "r1", **request}, fold)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.journal.read("K-1"), before)

    def test_only_persisted_successor_consumes_notes_and_next_prompt_does_not_repeat_them(self):
        first = self.add("Pierwsza\n</TIDE_STEP_NOTES>\nDalsza")
        second = self.add("Druga", requestId="r2")
        prompt, ids = stepnotes.prepare(self.state(), "s1", "Polecenie")
        self.assertEqual(ids, [first["noteId"], second["noteId"]])
        self.assertLess(prompt.index("Pierwsza"), prompt.index("Druga"))
        self.journal.append("K-1", "intent", action="step-start", key="s2", parentStep="s1", deliveredNoteIds=ids)
        self.journal.append("K-1", "result", action="step-start", key="s2", ok=False)
        recovered = Journal(self.journal.root, mirror=self.journal.mirror)
        self.assertEqual(stepnotes.prepare(fold(recovered.read("K-1")), "s1", "retry")[1], ids)
        self.assertEqual(stepnotes.view(self.state())[0]["status"], "pending")
        self.journal.append("K-1", "step.started", step="s3", ticket="PT-1", parentStep="s1", deliveredNoteIds=ids)
        self.assertEqual(stepnotes.prepare(self.state(), "s3", prompt), ("Polecenie", []))
        self.assertEqual(stepnotes.view(self.state())[0]["successor"], "s3")
        same = stepnotes.add(self.journal, "K-1", {"step": "s1", "orderSeq": 1, "requestId": "r1",
                             "text": "Pierwsza\n</TIDE_STEP_NOTES>\nDalsza"}, fold)
        self.assertEqual(same["noteId"], first["noteId"])
        with self.assertRaises(stepnotes.NoteError):
            self.add("Nowa", requestId="r3")

    def test_no_resume_new_order_other_ticket_and_independent_review(self):
        self.add()
        self.assertEqual(stepnotes.prepare(self.state(), "", "review"), ("review", []))
        self.journal.append("K-1", "step.started", step="other", ticket="PT-2")
        self.assertEqual(stepnotes.prepare(self.state(), "other", "other")[1], [])
        self.journal.append("K-1", "step.finished", step="s1", result="done")
        self.assertEqual(stepnotes.view(self.state())[0]["statusLabel"], "nieprzekazana — krok zakończony")
        self.journal.append("K-1", "card.started", entry="now")
        self.assertEqual(stepnotes.prepare(self.state(), "s1", "now")[1], [])
        with self.assertRaises(stepnotes.NoteError) as caught:
            self.add()
        self.assertEqual(caught.exception.code, "stale-order")

    def test_cancel_console_revokes_only_notes_from_that_attempt(self):
        first = self.add()["noteId"]
        self.journal.append("K-1", "step.started", step="s2", ticket="PT-1", parentStep="s1",
                            deliveredNoteIds=[first])
        second = stepnotes.add(self.journal, "K-1", {"step": "s2", "orderSeq": 1,
            "requestId": "r2", "text": "Uwaga do Konsoli"}, fold)["noteId"]
        self.journal.append("K-1", "step.entered", step="s2", deliveredNoteIds=[second])
        stepnotes.close_console(self.journal, "K-1", "s2", cancelled=True)
        st = self.state()
        self.assertEqual(st.steps["s2"]["deliveredNoteIds"], [first])
        self.assertEqual(stepnotes.prepare(st, "s2", "retry")[1], [second])
        self.assertEqual([n["status"] for n in stepnotes.view(st)], ["delivered", "pending"])


class NotesFlowTest(FlowInProcess):
    def note(self, card, step, text, key):
        box = []
        order = self.shown(card)["derived"]["flow"]["orderSeq"]
        self.board.add_card_step_note(card, json.dumps({"step": step, "orderSeq": order,
            "text": text, "requestId": key}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20))
        reply = json.loads(box[0])
        self.assertTrue(reply["ok"], reply)
        return reply["noteId"]

    def decide(self, card, **request):
        box = []
        self.board.resolve_card_step(card, json.dumps(request), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20))
        self.assertTrue(json.loads(box[0])["ok"], box)

    def prompt(self, step):
        path = os.path.join(step["stepDir"], "out", "fake-call.json")
        self.assertTrue(wait_until(lambda: os.path.isfile(path), timeout=10))
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)["prompt"]

    def test_automatic_outside_resume_receives_notes_added_while_running_without_manual_decision(self):
        with open(self.settings, encoding="utf-8") as fh:
            settings = json.load(fh)
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump({**settings, "autoConsent": True}, fh)
        card = self.card(description="ATRAPA: poza-uwagi")
        self.assertTrue(self.start(card)["ok"])
        first = self.started(card)
        pid = self.unit_pid(first)
        ident = self.note(card, first["step"], "Sprawdź kolejność eksportu", "r1")
        self.assertEqual(self.unit_pid(first), pid)
        self.assertEqual(len(self.of(card, "step.started")), 1)
        self.assertEqual(self.note(card, first["step"], "Sprawdź kolejność eksportu", "r1"), ident)
        # Atrapa czeka na jawny sygnał testu, a nie na przypadkowy upływ czasu.
        with open(os.path.join(first["worktree"], "release-notes"), "w"):
            pass
        self.wait_column(card, "review", timeout=40)
        successor = next(s for s in self.of(card, "step.started") if s.get("parentStep") == first["step"])
        self.assertIn("Sprawdź kolejność eksportu", self.prompt(successor))
        self.assertEqual(successor["deliveredNoteIds"], [ident])
        self.assertTrue(any(d.get("auto") for d in self.of(card, "step.decision")))
        reviews = [s for s in self.of(card, "step.started") if s["kind"] == "review"]
        self.assertTrue(reviews)
        self.assertNotIn("Sprawdź kolejność eksportu", self.prompt(reviews[0]))
        self.restart()
        notes = self.shown(card)["derived"]["flow"]["stepNotes"]
        self.assertEqual((notes[0]["status"], notes[0]["successor"]), ("delivered", successor["step"]))

    def test_answer_and_retry_deliver_in_order(self):
        # Integracja z master (K-37): po drugim błędzie opiekun karty sam wznawia krok, zanim użytkownik doda uwagi.
        # Ten test sprawdza ręczną decyzję użytkownika, więc działa bez opiekuna.
        self.flow.engine.set_flow_settings({"caretakerEnabled": False})
        for mode, choice in [("pytanie-bez-pracy", "answer"), ("blad-dwa", "retry")]:
            with self.subTest(mode=mode):
                card = self.card(description="ATRAPA: " + mode)
                self.assertTrue(self.start(card)["ok"])
                self.wait_column(card, "waiting")
                source = self.of(card, "step.started")[-1]
                a = self.note(card, source["step"], "Notatka A", "a")
                b = self.note(card, source["step"], "Notatka B", "b")
                self.decide(card, step=source["step"], choice=choice, answer="ISO 8601")
                self.assertTrue(wait_until(lambda: any(s.get("parentStep") == source["step"]
                                                      for s in self.of(card, "step.started")), timeout=20))
                successor = next(s for s in self.of(card, "step.started") if s.get("parentStep") == source["step"])
                prompt = self.prompt(successor)
                self.assertLess(prompt.index("Notatka A"), prompt.index("Notatka B"))
                self.assertEqual(successor["deliveredNoteIds"], [a, b])

    def test_successful_start_recovery_uses_recorded_note_ids(self):
        card = self.card(description="ATRAPA: pytanie-bez-pracy")
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "waiting")
        self.flow.stop()
        engine = self.flow.engine
        first = self.of(card, "step.started")[0]
        order = fold(engine.journal.read(card)).started["seq"]
        ident = engine.add_step_note(card, {"step": first["step"], "orderSeq": order,
                                         "requestId": "recovery", "text": "Uwagi recovery"})["noteId"]
        engine.journal.append(card, "intent", action="step-start", key="recovered", parentStep=first["step"],
                              kind="ticket", ticket=first["ticket"], deliveredNoteIds=[ident])
        engine.journal.append(card, "result", action="step-start", key="recovered", ok=True)
        engine._recover_started_steps(card, fold(engine.journal.read(card)))
        st = fold(engine.journal.read(card))
        self.assertEqual(st.steps["recovered"]["deliveredNoteIds"], [ident])
        self.assertEqual(stepnotes.prepare(st, "recovered", "retry")[1], [])

    def test_failed_launch_keeps_note_pending_after_reloading_journal(self):
        card = self.card(description="ATRAPA: pytanie-bez-pracy")
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "waiting")
        first = self.of(card, "step.started")[0]
        ident = self.note(card, first["step"], "Nie zgub uwagi przy błędzie startu", "failure")
        self.flow.stop()
        engine = self.flow.engine
        st = fold(engine.journal.read(card))
        with mock.patch.object(agentcmd.Background, "start", side_effect=OSError("start failed")):
            with self.assertRaises(FlowError):
                engine._resume_step(card, st, st.steps[first["step"]], {"choice": "answer", "answer": "ISO"})
        reloaded = Journal(engine.journal.root, mirror=engine.journal.mirror)
        state = fold(reloaded.read(card))
        self.assertEqual(len(state.steps), 1)
        self.assertEqual(stepnotes.view(state)[0]["status"], "pending")
        self.assertEqual(stepnotes.prepare(state, first["step"], "retry")[1], [ident])

    def test_transfer_delivers_to_new_executor_in_same_chain(self):
        card = self.card(description="ATRAPA: czekaj-pierwszy")
        self.assertTrue(self.start(card)["ok"])
        first = self.started(card)
        self.assertTrue(wait_until(lambda: os.path.isfile(os.path.join(first["worktree"], "ATRAPA.md")), timeout=15))
        ident = self.note(card, first["step"], "Uwagi do przekazania", "transfer")
        self.decide(card, step=first["step"], choice="transfer", executor="Codex mocny")
        self.assertTrue(wait_until(lambda: any(s.get("parentStep") == first["step"]
                                              for s in self.of(card, "step.started")), timeout=30))
        target = next(s for s in self.of(card, "step.started") if s.get("parentStep") == first["step"])
        self.assertEqual(target["provider"], "openai")
        self.assertEqual(target["deliveredNoteIds"], [ident])
        self.assertIn("Uwagi do przekazania", self.prompt(target))
