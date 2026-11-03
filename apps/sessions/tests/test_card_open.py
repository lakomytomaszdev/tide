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
from apps.board.tests.decisionfix import CARD, full_c, full_d

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
            for name in ("DecisionDialog.qml", "CardFullView.qml", "DecisionForm.qml", "decision.js"):
                with open(os.path.join(source, name), "rb") as fh:
                    expected = fh.read()
                for view in ("board", "sessions"):
                    with open(os.path.join(pkg, "views", view, "qml", "card", name), "rb") as fh:
                        self.assertEqual(fh.read(), expected, f"{view}/{name}")
        for name in ("DecisionDialog.qml", "CardFullView.qml"):
            self.assertFalse(os.path.exists(os.path.join(APP_DIR, "qml", name)), "bez kopii w apps/sessions")


if __name__ == "__main__":
    unittest.main()
