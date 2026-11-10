"""Sesje na wspólnym komponencie karty (PT-131, K-35).

- klik w kartę z listy „Karty w przepływie” otwiera okno decyzji (DecisionDialog) albo pełną kartę (CardFullView)
  według ustawienia okna Tide i reguły „Czeka na mnie”: karta spoza „Czeka na mnie” zawsze otwiera pełną kartę,
- odpowiedź z okna idzie do /Board tą samą metodą i z tymi samymi parametrami co z Tablicy; ustawienie
  z okna trafia do backendu; Esc zamyka okno bez wysyłania,
- Sesje nie importują FlowCardPanel.qml, a komponent jest ten sam co w Tablicy (pakiet ma jedną kopię źródła
  w apps/common/qml/card, w obu widokach bajt w bajt taki sam).

Test offscreen: realne okno z SessionsView, klik myszą w wiersz listy (QTest), dane z fixture’ów Tablicy.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from . import APP_DIR, REPO
from .test_window import QML_PROBLEMS, stage
from apps.board.tests.boardfix import preview_data
from apps.board.tests.decisionfix import CARD, TICKETS_D, full_c, full_card, full_d, journal_move

import re

PROBE = r"""
import importlib.util, json, sys
from PyQt6.QtCore import QPoint, Qt, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine, QQmlExpression
from PyQt6.QtTest import QTest
pkg, data, body = sys.argv[1:4]
view = pkg + "/views/sessions"
spec = importlib.util.spec_from_file_location("tide_sessions_backend", view + "/backend.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
app = QGuiApplication([])
with open(data, encoding="utf-8") as fh:
    backend = mod.Backend(None, json.load(fh))
engine = QQmlApplicationEngine()
engine.setInitialProperties({"source": backend})
engine.loadData(b"import QtQuick\nWindow { width: 1680; height: 980; visible: true\n"
                b"  property var source: null\n  property alias sessions: v\n"
                b"  SessionsView { id: v; anchors.fill: parent; backend: source } }\n",
                QUrl.fromLocalFile(view + "/qml/Probe.qml"))
window = engine.rootObjects()[0]
page = window.property("sessions").property("page")
QTest.qWait(500)

def js(expression):
    value = QQmlExpression(engine.contextForObject(page), page, expression).evaluate()[0]
    return value

def state():
    return json.loads(js("JSON.stringify({ decisionOpen: decisionOpen, fullOpen: fullOpen, "
                         "dialog: decisionDialog.visible, full: cardFullView.visible, opened: openedCardId, "
                         "entries: " + ENTRIES + ".length })"))

ENTRIES = ("(function walk(it) { let out = []; for (const c of it.children) { "
           "if (c.objectName === 'flowCardEntry') out.push(c); out = out.concat(walk(c)) } return out })(page)")

def click_entry(i):
    pos = json.loads(js("(function () { const e = " + ENTRIES + "[" + str(i) + "]; "
                        "const p = e.mapToItem(null, e.width / 2, e.height / 2); "
                        "return JSON.stringify([Math.round(p.x), Math.round(p.y)]) })()"))
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(*pos))
    QTest.qWait(300)

def press_escape():
    QTest.keyClick(window, Qt.Key.Key_Escape)
    QTest.qWait(200)

def calls():
    return json.loads(backend.callsJson)

result = {}
exec(body)
print(json.dumps(result))
app.quit()
"""


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class CardOpenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-sess-cardopen-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))
        base = preview_data(os.path.join(cls.tmp, "board"))
        cls.waiting = cls.data_file("waiting", full_c(base))
        cls.working = cls.data_file("working", full_d(base))
        cls.waiting_full = cls.data_file("waiting-full", full_c(base), cardOpen="full")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def data_file(cls, name, snapshot, **extra):
        path = os.path.join(cls.tmp, name + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(dict({"boardSnapshot": snapshot, "now": 1793700000}, **extra), fh)
        return path

    def probe(self, data: str, body: str) -> dict:
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        run = subprocess.run(["/usr/bin/python3", "-c", PROBE, self.pkg, data, body], env=env,
                             capture_output=True, text=True, timeout=90)
        self.assertEqual(run.returncode, 0, run.stderr[-3000:])
        self.assertIsNone(re.search(QML_PROBLEMS, run.stderr), run.stderr[-3000:])
        return json.loads(run.stdout.strip().splitlines()[-1])

    def test_waiting_card_opens_decision_dialog_and_escape_keeps_it_waiting(self):
        out = self.probe(self.waiting, "result['before'] = state()\nclick_entry(0)\nresult['open'] = state()\n"
                                       "press_escape()\nresult['after'] = state()\nresult['calls'] = calls()")
        self.assertEqual(out["before"]["entries"], 1)
        self.assertEqual((out["before"]["decisionOpen"], out["before"]["fullOpen"]), (False, False))
        self.assertEqual((out["open"]["decisionOpen"], out["open"]["dialog"], out["open"]["full"], out["open"]["opened"]),
                         (True, True, False, CARD))
        self.assertEqual((out["after"]["decisionOpen"], out["after"]["fullOpen"]), (False, False))
        self.assertEqual(out["calls"], [], "Esc niczego nie wysyła")

    def test_waiting_card_opens_full_view_when_setting_is_full(self):
        out = self.probe(self.waiting_full, "click_entry(0)\nresult['open'] = state()\npress_escape()\n"
                                            "result['after'] = state()")
        self.assertEqual((out["open"]["fullOpen"], out["open"]["full"], out["open"]["dialog"], out["open"]["opened"]),
                         (True, True, False, CARD))
        self.assertEqual(out["after"]["fullOpen"], False, "Esc wraca do listy")

    def test_card_outside_waiting_always_opens_full_view(self):
        out = self.probe(self.working, "click_entry(0)\nresult['open'] = state()")
        self.assertEqual((out["open"]["fullOpen"], out["open"]["decisionOpen"], out["open"]["dialog"]),
                         (True, False, False))

    def test_ticket_settings_button_is_hidden_in_sessions(self):
        """PT-334: przycisk „Ustawienia” ticketu jest tylko w Tablicy (Sesje nie mają katalogu wykonawców)."""
        out = self.probe(self.working, "click_entry(0)\n"
                         "result['tiles'] = json.loads(js(\"JSON.stringify((function walk(it) { let out = []; "
                         "for (const c of it.children) { if (c.objectName === 'fullTicket') out.push(c); "
                         "out = out.concat(walk(c)) } return out })(cardFullView).map(t => "
                         "[t.ticketId, t.ticketSettingsEnabled, (function find(it) { for (const c of it.children) { "
                         "if (c.objectName === 'fullTicketSettings') return c.visible; const r = find(c); "
                         "if (r !== undefined) return r } return undefined })(t)]))\"))")
        self.assertTrue(out["tiles"], "pełna karta ma kafelki ticketów")
        for ticket, enabled, visible in out["tiles"]:
            self.assertFalse(enabled, ticket)
            self.assertFalse(visible, ticket)

    def test_running_close_sentence_is_the_same_in_the_sessions_full_card(self):
        # PT-337: to samo zdanie „Twój ruch” co na Tablicy (wspólny CardFullView i move.js).
        events = journal_move([("K-8-close-1", "", "close", "Opus", [])], tuple(row[0] for row in TICKETS_D))
        base = preview_data(os.path.join(self.tmp, "board-move"))
        data = self.data_file("closing", full_card(base, events))
        out = self.probe(data, "click_entry(0)\nresult['open'] = state()\n"
                               "result['headline'] = js('cardFullView.headline')")
        self.assertTrue(out["open"]["full"])
        self.assertTrue(out["headline"].startswith("Domknięcie pracuje w tle (Opus);"), out["headline"])

    def test_answer_goes_to_board_with_method_and_params_from_the_decision(self):
        out = self.probe(self.waiting, "click_entry(0)\n"
                         "js(\"decisionDialog.answered('d1', 'ApproveCardPlan', {})\")\n"
                         "js(\"decisionDialog.answered('d2', 'ResolveCardStep', {step: 's1', option: 1})\")\n"
                         "QTest.qWait(200)\nresult['calls'] = calls()\nresult['state'] = state()")
        self.assertEqual(out["calls"][0], ["ApproveCardPlan", CARD])
        self.assertEqual(out["calls"][1][:2], ["ResolveCardStep", CARD])
        self.assertEqual(json.loads(out["calls"][1][2]), {"step": "s1", "option": 1})
        self.assertTrue(out["state"]["decisionOpen"], "podgląd z pliku odrzuca zapis — decyzja wraca do okna")

    def test_dialog_setting_reaches_the_backend(self):
        out = self.probe(self.waiting, "click_entry(0)\njs(\"decisionDialog.cardOpenRequested('full')\")\n"
                                       "QTest.qWait(100)\nresult['cardOpen'] = backend.cardOpen\n"
                                       "result['page'] = js('cardOpen')")
        self.assertEqual((out["cardOpen"], out["page"]), ("full", "full"))

    def test_ticket_consent_button_reaches_board_from_sessions(self):
        with open(self.working, encoding="utf-8") as fh:
            snapshot = json.load(fh)["boardSnapshot"]
        flow = next(c for c in snapshot["cards"] if c["id"] == CARD)["derived"]["flow"]
        flow["ticketConsents"] = [{"ticket": "PT-2", "active": True, "canRevoke": True,
                                   "orderSeq": 7, "baseVersion": 1}]
        data = self.data_file("ticket-consent", snapshot)
        out = self.probe(data, """
click_entry(0)
pos = json.loads(js("(function walk(it) { for (const c of it.children) { "
    "if (c.objectName === 'revokeTicketConsent') { const p = c.mapToItem(null, c.width/2, c.height/2); "
    "return JSON.stringify([Math.round(p.x), Math.round(p.y)]) } "
    "const found = walk(c); if (found) return found } return null })(cardFullView)"))
QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(*pos))
QTest.qWait(200)
result['calls'] = calls()
""")
        sent = [c for c in out["calls"] if c[0] == "RevokeTicketConsent"]
        self.assertEqual(len(sent), 1, out)
        self.assertEqual(sent[0][1], CARD)
        self.assertEqual(json.loads(sent[0][2]), {"ticket": "PT-2", "orderSeq": 7, "baseVersion": 1})

    def test_step_note_uses_board_api_and_keeps_draft_after_rejected_save(self):
        out = self.probe(self.working, r"""
click_entry(0)
step = js('cardFullView.noteSteps[0].id')
js("cardFullView.editNote(" + json.dumps(step) + ", 'Uwagi z Sesji')")
js('cardFullView.sendNote(' + json.dumps(step) + ')')
QTest.qWait(200)
result['calls'] = calls()
result['step'] = step
result['draft'] = json.loads(js('JSON.stringify(cardFullView.noteDraft(' + json.dumps(step) + '))'))
""")
        self.assertEqual(out["calls"][0][:2], ["AddCardStepNote", CARD])
        request = json.loads(out["calls"][0][2])
        self.assertEqual((request["step"], request["text"]), (out["step"], "Uwagi z Sesji"))
        self.assertTrue(request["requestId"])
        self.assertGreater(request["orderSeq"], 0)
        self.assertEqual(out["draft"]["text"], "Uwagi z Sesji")
        self.assertFalse(out["draft"]["busy"])
        self.assertTrue(out["draft"]["error"])

    def test_delayed_note_reply_after_opening_another_card_releases_original_draft(self):
        with open(self.working, encoding="utf-8") as fh:
            data = json.load(fh)["boardSnapshot"]
        first = next(c for c in data["cards"] if c["id"] == CARD)
        data["cards"].append(dict(first, id="K-98", title="Druga karta"))
        path = self.data_file("notes-navigation", data)
        for success in (True, False):
            with self.subTest(success=success):
                out = self.probe(path, "success = " + repr(success) + r'''
pending_notes = []
original_write = backend._write
def delayed_write(tag, member, args):
    if member != 'AddCardStepNote':
        return original_write(tag, member, args)
    backend._calls.append([member, *args])
    pending_notes.append(tag)
backend._write = delayed_write
js("openFull('K-8')")
step = js('cardFullView.noteSteps[0].id')
draft = 'cardFullView.noteDraft(' + json.dumps(step) + ')'
js("cardFullView.editNote(" + json.dumps(step) + ", 'Uwaga A')")
js('cardFullView.sendNote(' + json.dumps(step) + ')')
result['first'] = calls()[-1]
js("openFull('K-98')")
js("cardFullView.editNote(" + json.dumps(step) + ", 'Szkic B')")
backend.replied.emit(pending_notes.pop(0), json.dumps(
    {'ok': True, 'noteId': 'note-A'} if success else {'ok': False, 'message': 'Błąd A'}))
result['other'] = json.loads(js('JSON.stringify(' + draft + ')'))
js("openFull('K-8')")
result['settled'] = json.loads(js('JSON.stringify(' + draft + ')'))
assert not result['settled']['busy'], 'odpowiedź A nie zwolniła szkicu po nawigacji'
if success:
    js("cardFullView.editNote(" + json.dumps(step) + ", 'Następna uwaga A')")
js('cardFullView.sendNote(' + json.dumps(step) + ')')
result['next'] = calls()[-1]
result['calls'] = len(pending_notes)
assert result['calls'] == 1, 'formularz nie wysłał następnej uwagi albo ponowienia'
backend.replied.emit(pending_notes.pop(0), json.dumps({'ok': True, 'noteId': 'note-next'}))
result['saved'] = json.loads(js('JSON.stringify(' + draft + ')'))
''')
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

    def test_base_refusal_opens_shared_preview_and_escape_keeps_question(self):
        from apps.board.tests.test_base_change import PREVIEW
        response = json.dumps({"ok": True, "waiting": True, "changed": False, "preview": PREVIEW})
        out = self.probe(self.waiting, "click_entry(0)\n"
                         "js(\"cardTags = {'decision:test': ['K-8', 'decision-id']}\")\n"
                         "js(" + repr("handleReply('decision:test', " + response + ")") + ")\n"
                         "QTest.qWait(100)\nresult['open'] = js('baseChangePreview.visible')\n"
                         "result['text'] = js('baseChangePreview.describe(baseChangePreview.result)')\n"
                         "press_escape()\nresult['after'] = js('baseChangePreview.visible')\n"
                         "result['question'] = state()['decisionOpen']\nresult['calls'] = calls()")
        self.assertTrue(out["open"], out)
        self.assertFalse(out["after"])
        self.assertTrue(out["question"])
        self.assertIn("K-50-replay-code", out["text"])
        self.assertIn("Odmowa korekty", out["text"])
        self.assertEqual(out["calls"], [])

    def test_base_confirmation_uses_the_same_structured_resolve_in_sessions(self):
        from apps.board.tests.test_base_change import PREVIEW
        preview = {**PREVIEW, "canExecute": True, "canChange": True}
        request = {"choice": "change-base", "step": "base-request:5", "baseRef": "refs/heads/topic",
                   "baseCommit": "b" * 40, "previewId": "token"}
        out = self.probe(self.waiting, "click_entry(0)\n"
                         "js(" + repr("baseChangePreview.show('K-8', " + json.dumps(preview) + ")") + ")\n"
                         "js(" + repr("baseChangePreview.confirmed(" + json.dumps(request) + ")") + ")\n"
                         "QTest.qWait(200)\nresult['calls'] = calls()\n"
                         "result['visible'] = js('baseChangePreview.visible')\n"
                         "result['text'] = js('baseChangePreview.describe(baseChangePreview.result)')")
        self.assertEqual(out["calls"][0][:2], ["ResolveCardStep", CARD])
        self.assertEqual(json.loads(out["calls"][0][2]), request)
        self.assertTrue(out["visible"], "odmowa backendu podglądu zachowuje dane")
        self.assertIn("K-50-replay-code", out["text"])


class SharedSettingTest(unittest.TestCase):
    def test_setting_saved_by_one_view_reaches_the_other(self):
        from PyQt6.QtCore import QCoreApplication, QElapsedTimer
        from apps.common import windowconfig
        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tide", "window.json")
            board, sessions = [], []
            keep = [windowconfig.watch(board.append, None, path), windowconfig.watch(sessions.append, None, path)]
            windowconfig.set_card_open("full", path)
            timer = QElapsedTimer()
            timer.start()
            while not (board and sessions) and timer.elapsed() < 3000:
                app.processEvents()
            self.assertEqual(board[-1:], ["full"])
            self.assertEqual(sessions[-1:], ["full"])
            self.assertTrue(all(keep))


class SharedComponentTest(unittest.TestCase):
    def test_sessions_do_not_import_flow_card_panel(self):
        folder = os.path.join(APP_DIR, "qml")
        self.assertFalse(os.path.exists(os.path.join(folder, "FlowCardPanel.qml")), "stary panel usunął PT-135")
        for name in os.listdir(folder):
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                self.assertNotIn("FlowCardPanel", fh.read(), name)

    def test_board_and_sessions_share_one_copy_of_the_card_component(self):
        with tempfile.TemporaryDirectory(prefix="tide-sess-shared-") as tmp:
            pkg = stage(os.path.join(tmp, "pkg"))
            source = os.path.join(REPO, "apps", "common", "qml", "card")
            for name in ("DecisionDialog.qml", "CardFullView.qml", "DecisionForm.qml", "decision.js",
                         "TicketTile.qml", "ticket.js"):
                with open(os.path.join(source, name), "rb") as fh:
                    expected = fh.read()
                for view in ("board", "sessions"):
                    with open(os.path.join(pkg, "views", view, "qml", "card", name), "rb") as fh:
                        self.assertEqual(fh.read(), expected, f"{view}/{name}")
        for name in ("DecisionDialog.qml", "CardFullView.qml"):
            self.assertFalse(os.path.exists(os.path.join(APP_DIR, "qml", name)), "bez kopii w apps/sessions")


if __name__ == "__main__":
    unittest.main()
