"""PT-245: publiczna metoda D-Bus i automatyczne doręczenie do polecenia atrapy."""
import json
import os

from .privatebus import wait_until
from .test_cardflow_dbus import FlowCase


class StepNotesDbusTest(FlowCase):
    def test_note_is_saved_without_interrupting_and_delivered_after_automatic_outside(self):
        settings = os.path.join(self.base, "config", "tide", "cardflow.json")
        with open(settings, encoding="utf-8") as fh:
            data = json.load(fh)
        with open(settings, "w", encoding="utf-8") as fh:
            json.dump({**data, "autoConsent": True}, fh)
        card = self.card(description="ATRAPA: poza-uwagi")
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "step.started"), timeout=15))
        first = self.of(card, "step.started")[0]
        order = self.shown(card)["derived"]["flow"]["orderSeq"]
        request = {"step": first["step"], "orderSeq": order, "requestId": "dbus-r1", "text": "Uwagi z D-Bus"}
        saved = self.call("AddCardStepNote", card, json.dumps(request))
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(self.call("AddCardStepNote", card, json.dumps(request))["noteId"], saved["noteId"])
        self.assertEqual(len(self.of(card, "step.started")), 1)
        self.assertEqual(len(self.of(card, "step.finished")), 0)
        for change, error in [({"text": "zmieniona"}, "note-conflict"), ({"text": ""}, "bad-note"),
                              ({"orderSeq": 0}, "stale-order")]:
            reply = self.call("AddCardStepNote", card, json.dumps({**request, **change}))
            self.assertEqual(reply["error"], error)
        self.assertEqual(self.call("AddCardStepNote", card, "[]")["error"], "bad-request")
        with open(os.path.join(first["worktree"], "release-notes"), "w"):
            pass
        self.wait_column(card, "review", timeout=40)
        successor = next(s for s in self.of(card, "step.started") if s.get("parentStep") == first["step"])
        self.assertIn("Uwagi z D-Bus", self.fake_call(successor)["prompt"])
        self.assertEqual(successor["deliveredNoteIds"], [saved["noteId"]])
        notes = self.shown(card)["derived"]["flow"]["stepNotes"]
        self.assertEqual((notes[0]["status"], notes[0]["successor"]), ("delivered", successor["step"]))
