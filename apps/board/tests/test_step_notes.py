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
