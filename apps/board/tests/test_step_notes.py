"""PT-245: formularz pełnej karty zapisuje konkretny krok i przechowuje klucz ponowienia."""
import json
import os
import shutil
import unittest

from .decisionfix import full_d
from .test_card_full_view import STATE_PY
from .test_view import ProbeCase


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class StepNotesFormTest(ProbeCase):
    def test_delayed_reply_after_navigation_releases_the_original_draft(self):
        data = full_d(self.data)
        first = next(c for c in data["cards"] if c["id"] == "K-8")
        data["cards"].append(dict(first, id="K-98", title="Druga karta"))
        for success in (True, False):
            with self.subTest(success=success):
                out = self.probe(STATE_PY + "success = " + repr(success) + r'''
pending_notes = []
original_write = backend._write
def delayed_write(tag, member, args):
    if member != 'AddCardStepNote':
        return original_write(tag, member, args)
    backend._calls.append([member, *args])
    pending_notes.append(tag)
backend._write = delayed_write
js("page.openFull('K-8')")
step = js("page.cardFullView.noteSteps[0].id")
draft = "page.cardFullView.noteDraft(" + json.dumps(step) + ")"
js("page.cardFullView.editNote(" + json.dumps(step) + ", 'Uwaga A')")
js("page.cardFullView.sendNote(" + json.dumps(step) + ")")
out['first'] = calls()[-1]
js("page.openFull('K-98')")
js("page.cardFullView.editNote(" + json.dumps(step) + ", 'Szkic B')")
backend.replied.emit(pending_notes.pop(0), json.dumps(
    {'ok': True, 'noteId': 'note-A'} if success else {'ok': False, 'message': 'Błąd A'}))
out['other'] = js(draft)
js("page.openFull('K-8')")
out['settled'] = js(draft)
assert not out['settled']['busy'], 'odpowiedź A nie zwolniła szkicu po nawigacji'
if success:
    js("page.cardFullView.editNote(" + json.dumps(step) + ", 'Następna uwaga A')")
js("page.cardFullView.sendNote(" + json.dumps(step) + ")")
out['next'] = calls()[-1]
out['calls'] = len(pending_notes)
assert out['calls'] == 1, 'formularz nie wysłał następnej uwagi albo ponowienia'
backend.replied.emit(pending_notes.pop(0), json.dumps({'ok': True, 'noteId': 'note-next'}))
out['saved'] = js(draft)
''', data)
                self.assertEqual(out["other"]["text"], "Szkic B")
                self.assertFalse(out["other"]["busy"])
                self.assertFalse(out["settled"]["busy"])
                self.assertEqual(out["calls"], 1)
                self.assertEqual(out["saved"]["saved"], "note-next")
                if success:
                    self.assertEqual(out["settled"]["saved"], "note-A")
                    self.assertNotEqual(json.loads(out["first"][2])["requestId"],
                                        json.loads(out["next"][2])["requestId"])
                else:
                    self.assertEqual(out["settled"]["error"], "Błąd A")
                    self.assertEqual(out["first"], out["next"])

    def test_form_error_retry_success_and_persisted_status(self):
        data = full_d(self.data)
        flow = next(c for c in data["cards"] if c["id"] == "K-8")["derived"]["flow"]
        target = next(s for s in flow["steps"] if s.get("canAddNote"))
        flow["stepNotes"] = [{"step": target["id"], "text": "Poprzednia uwaga",
                              "statusLabel": "przekazana — kolejny-krok", "successor": "kolejny-krok"}]
        out = self.probe(STATE_PY + r'''
click(card("K-8"))
replies.extend([{"ok": False, "error": "test", "message": "Nie zapisano testowo"},
                {"ok": True, "noteId": "stable-id"}])
forms = "(" + ALL + ")(page.cardFullView, 'stepNoteForm')"
field = "(" + ALL + ")(" + forms + "[0], 'stepNoteText')[0]"
js(field + ".text = 'Uwagi formularza'")
out["text"] = js(field + ".text")
js(forms + "[0].submitted()")
until(lambda: not js("page.cardFullView.noteDraft(" + forms + "[0].stepId).busy"))
out["first"] = calls()[-1]
out["draft"] = js("page.cardFullView.noteDraft(" + forms + "[0].stepId)")
js(forms + "[0].submitted()")
until(lambda: not js("page.cardFullView.noteDraft(" + forms + "[0].stepId).busy"))
out["retry"] = calls()[-1]
out["saved"] = js("page.cardFullView.noteDraft(" + forms + "[0].stepId)")
out["status"] = js("(" + ALL + ")(" + forms + "[0], 'stepNoteStatus')[0].text")
''', data)
        self.assertEqual(out["text"], "Uwagi formularza")
        self.assertEqual(out["first"][:2], ["AddCardStepNote", "K-8"])
        request = json.loads(out["first"][2])
        self.assertEqual((request["step"], request["orderSeq"], request["text"]),
                         (target["id"], flow["orderSeq"], "Uwagi formularza"))
        self.assertTrue(request["requestId"])
        self.assertEqual(out["retry"], out["first"])
        self.assertEqual(out["draft"]["text"], "Uwagi formularza")
        self.assertFalse(out["draft"]["busy"])
        self.assertTrue(out["draft"]["error"])
        self.assertEqual((out["saved"]["text"], out["saved"]["saved"]), ("", "stable-id"))
        self.assertIn("przekazana — kolejny-krok", out["status"])
