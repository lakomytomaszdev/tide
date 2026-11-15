"""Sonda wspólnego widoku diffa (apps/common/qml/git/DiffView.qml, PT-354) w prawdziwym oknie offscreen.

Użycie (z testów): /usr/bin/python3 diffprobe.py KATALOG_QML SCENARIUSZ.py
KATALOG_QML ma Harness.qml, tide/ (biblioteka z paletą) i git/ (komponent); atrapa backendu `fake` żyje w Harness.qml
i tylko zapisuje wywołania (`fake.calls`), odpowiedzi wysyła scenariusz przez reply().
Pomocnicze w scenariuszu: js(wyrażenie), wait(ms), until(funkcja), key(klawisz, modyfikatory), click(wyrażenie QML
elementu po objectName), field(nazwa, wyrażenie `it`) — wyniki dla widocznych elementów od góry, calls() — wywołania atrapy,
reply(tag, obiekt) — odpowiedź usługi, out — słownik wyniku (JSON w ostatniej linii).
"""
import json
import sys

from PyQt6.QtCore import QPoint, Qt, QTimer, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine, QQmlExpression
from PyQt6.QtTest import QTest

qml_dir, scenario_path = sys.argv[1:3]
app = QGuiApplication([])
engine = QQmlApplicationEngine()
engine.load(QUrl.fromLocalFile(qml_dir + "/Harness.qml"))
window = engine.rootObjects()[0]
out = {}

ALL = ("(function all(it, name, out) { out = out || []; if (!it) return out; "
       "if (it.objectName === name && it.visible) out.push(it); "
       "for (const c of it.children) all(c, name, out); return out })")


def wait(ms=50):
    QTest.qWait(ms)


def until(predicate, ms=3000):
    for _ in range(max(1, ms // 20)):
        value = predicate()
        if value:
            return value
        wait(20)
    return predicate()


def js(expression):
    value, undefined = QQmlExpression(engine.contextForObject(window), window, expression).evaluate()
    if hasattr(value, "toVariant"):
        value = value.toVariant()
    return None if undefined else value


def field(name, expression):
    """Lista wyników wyrażenia (zmienna `it`) dla widocznych elementów o objectName."""
    text = js("JSON.stringify(" + ALL + "(loader.item, " + json.dumps(name) + ").sort((x, y) => "
              "x.mapToItem(loader.item, 0, 0).y - y.mapToItem(loader.item, 0, 0).y).map(it => " + expression + "))")
    return json.loads(text) if text else []


def click(name, index=0, modifiers=Qt.KeyboardModifier.NoModifier, button=Qt.MouseButton.LeftButton):
    point = js("(function () { const v = loader.item; const it = " + ALL + "(v, " + json.dumps(name) + ")[" + str(index)
               + "]; if (!it) return ''; return JSON.stringify(it.mapToItem(null, it.width / 2, it.height / 2)) })()")
    if not point:
        raise AssertionError(f"nie ma elementu {name}[{index}]")
    point = json.loads(point)
    QTest.mouseClick(window, button, modifiers, QPoint(int(point["x"]), int(point["y"])))
    wait(60)


def key(code, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(window, code, modifiers)
    wait(60)


def calls():
    return json.loads(js("JSON.stringify(fake.calls)"))


def reply(tag, payload):
    js("fake.replied(" + json.dumps(tag) + ", " + json.dumps(json.dumps(payload)) + ")")
    wait(60)


def run():
    try:
        with open(scenario_path, encoding="utf-8") as fh:
            exec(fh.read(), globals())
    except Exception as exc:  # wynik z błędem — test pokaże, co poszło nie tak
        out["error"] = repr(exc)
    print(json.dumps(out, ensure_ascii=False, default=str))
    app.quit()


QTimer.singleShot(400, run)
app.exec()
