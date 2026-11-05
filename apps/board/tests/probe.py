"""Sonda widoku Tablica w prawdziwym oknie (offscreen): BoardView z backendem w trybie podglądu,
którego zapisy odpowiadają według scenariusza, a test steruje myszą (QTest) i wyrażeniami QML.

Użycie (z testów): /usr/bin/python3 probe.py PAKIET DANE.json SCENARIUSZ.py
Scenariusz to kod Pythona wykonywany po załadowaniu widoku, z pomocnikami:
  js(wyrażenie)            — wartość wyrażenia QML w kontekście widoku (np. "page.cards.length"),
  obj(nazwa)               — obiekt QML po objectName,
  at(wyrażenie, fx, fy)    — punkt elementu QML we współrzędnych okna (QPoint),
  card(id), column(id)     — punkt karty albo kolumny (domyślnie środek),
  named(objectName)        — punkt elementu po objectName,
  drag(od, do)             — przeciągnięcie myszą (QPoint → QPoint), z krokami jak u człowieka,
  click(punkt), key(klawisz, modyfikatory), wait(ms),
  until(funkcja, ms)       — czeka, aż funkcja() da prawdę (najwyżej ms; pod obciążeniem zamiast stałych pauz),
  calls()                  — zapisy wysłane do usługi: [[metoda, argumenty…], …],
  replies                  — lista odpowiedzi usługi na kolejne zapisy (domyślnie {ok: true}),
  set_snapshot(dane)       — nowa migawka (jak po sygnale Changed),
  out                      — słownik wyniku (drukowany jako JSON w ostatniej linii).
"""
import importlib.util
import json
import sys

from PyQt6.QtCore import QObject, QPoint, Qt, QTimer, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine, QQmlExpression
from PyQt6.QtTest import QTest

pkg, data_path, scenario_path = sys.argv[1:4]
view_dir = pkg + "/views/board"
spec = importlib.util.spec_from_file_location("tide_board_backend", view_dir + "/backend.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

app = QGuiApplication([])
with open(data_path, encoding="utf-8") as fh:
    data = json.load(fh)


class Scripted(module.Backend):
    """Backend z migawką z pliku, którego zapisy odpowiadają z listy `replies` (domyślnie ok)."""

    def __init__(self, snapshot):
        super().__init__(None, snapshot)
        self.replies = []
        self.branch_replies = []
        self.branch_calls = []

    def _write(self, tag, member, args):
        if member == "ListCardBranches":
            # Odczyty nie zużywają scenariuszy zapisów istniejących testów formularza.
            self.branch_calls.append([member, *args])
            reply = self.branch_replies.pop(0) if self.branch_replies else {
                "ok": True, "branches": [{"ref": "refs/heads/master", "name": "master", "kind": "local",
                                           "remote": "", "commit": "a" * 40, "selectable": True, "reason": ""}],
                "mainRef": "refs/heads/master", "recentBases": [], "recentTargets": [], "warnings": [], "hasMore": False}
            QTimer.singleShot(10, lambda: self.replied.emit(tag, json.dumps(reply, ensure_ascii=False)))
            return
        self._calls.append([member, *args])
        self.callsChanged.emit()
        reply = self.replies.pop(0) if self.replies else {"ok": True, "id": args[0] if member != "CreateCard" else "K-99",
                                                           "commitError": ""}
        QTimer.singleShot(10, lambda: self.replied.emit(tag, json.dumps(reply, ensure_ascii=False)))

    def set_snapshot(self, snapshot):
        self._snapshot = json.dumps(snapshot, ensure_ascii=False)
        self.snapshotChanged.emit()
        self._update_badge()


backend = Scripted(data)
engine = QQmlApplicationEngine()
engine.setInitialProperties({"source": backend})
engine.loadData(b"import QtQuick\nWindow { width: 1440; height: 900; visible: true\n"
                b"  property var source: null\n  BoardView { objectName: 'boardView'; anchors.fill: parent; backend: source } }\n",
                QUrl.fromLocalFile(view_dir + "/qml/Probe.qml"))
window = engine.rootObjects()[0]
view = window.findChild(QObject, "boardView")
out = {}


def wait(ms=50):
    QTest.qWait(ms)


def until(predicate, ms=5000):
    for _ in range(max(1, ms // 20)):
        value = predicate()
        if value:
            return value
        wait(20)
    return predicate()


def js(expression):
    value, undefined = QQmlExpression(engine.contextForObject(view), view, expression).evaluate()
    if hasattr(value, "toVariant"):
        value = value.toVariant()
    return None if undefined else value


def obj(name):
    return window.findChild(QObject, name)


def at(item_expression, fx=0.5, fy=0.5, dx=0, dy=0) -> QPoint:
    """Punkt elementu wskazanego wyrażeniem QML (np. "page.findCard('K-15')") we współrzędnych okna:
    ułamek szerokości i wysokości plus przesunięcie w pikselach."""
    point = js(f"(function () {{ const it = {item_expression}; "
               f"return it ? JSON.stringify(it.mapToItem(null, it.width * {fx} + {dx}, it.height * {fy} + {dy})) : '' }})()")
    if not point:
        raise AssertionError(f"nie ma elementu: {item_expression}")
    point = json.loads(point)
    return QPoint(int(point["x"]), int(point["y"]))


def named(name, fx=0.5, fy=0.5) -> QPoint:
    """Punkt elementu po objectName (np. przycisku „cardSave”)."""
    item = obj(name)
    if item is None:
        raise AssertionError(f"nie ma elementu {name}")
    engine.rootContext().setContextProperty("probeTarget", item)
    return at("probeTarget", fx, fy)


def card(card_id, fx=0.5, fy=0.5, dx=0, dy=0) -> QPoint:
    """Punkt karty na tablicy (domyślnie środek)."""
    return at(f"page.findCard('{card_id}')", fx, fy, dx, dy)


def column(column_id, fx=0.5, fy=0.5, dx=0, dy=0) -> QPoint:
    """Punkt kolumny układu Razem."""
    return at(f"page.columnItems().find(c => c.spec.id === '{column_id}')", fx, fy, dx, dy)


def click(point):
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    wait(60)


def key(code, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(window, code, modifiers)
    wait(60)


def drag(start: QPoint, end: QPoint, steps=12):
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    wait(30)
    for i in range(1, steps + 1):
        point = QPoint(start.x() + (end.x() - start.x()) * i // steps, start.y() + (end.y() - start.y()) * i // steps)
        QTest.mouseMove(window, point)
        wait(15)
    wait(80)  # jak człowiek: chwila nad celem przed puszczeniem (Drag dostarcza wejście w DropArea z opóźnieniem)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    wait(120)


def calls():
    return json.loads(backend.callsJson)


replies = backend.replies
def set_snapshot(snapshot):
    backend.set_snapshot(snapshot)
    wait(200)  # kolumny układają się na nowo


def run():
    try:
        with open(scenario_path, encoding="utf-8") as fh:
            exec(fh.read(), globals())
    except Exception as exc:  # wynik z błędem — test pokaże, co poszło nie tak
        out["error"] = repr(exc)
    print(json.dumps(out, ensure_ascii=False, default=str))
    app.quit()


QTimer.singleShot(500, run)
app.exec()
