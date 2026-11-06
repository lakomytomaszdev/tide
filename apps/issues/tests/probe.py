"""Scenariusz QML/backendu w osobnym procesie Qt z pakietem instalacyjnym."""
import importlib.util
import json
import sys
import time

from PyQt6.QtCore import QObject, QTimer, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine, QQmlExpression
from PyQt6.QtTest import QTest

pkg, data_path, scenario = sys.argv[1:]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load("issue_backend", pkg + "/views/issues/backend.py")
tide = load("tide_window", pkg + "/tide.py")
app = QGuiApplication([])
with open(data_path) as fh:
    data = json.load(fh)
backend = module.Backend(None, data)
engine = QQmlApplicationEngine()
factory = tide.guard_network(engine)
backend.install_image_provider(engine)
engine.setInitialProperties({"source": backend})
engine.loadData(b'import QtQuick\nimport "tide" as Tide\nWindow { width: 1440; height: 920; visible: true; color: Tide.Tokens.backgroundWindow\n'
                b"property var source\nIssuesView { objectName: 'issues'; anchors.fill: parent; backend: source } }",
                QUrl.fromLocalFile(pkg + "/views/issues/qml/Probe.qml"))
assert engine.rootObjects(), "Nie udało się załadować QML"
window = engine.rootObjects()[0]
view = window.findChild(QObject, "issues")
engine.rootContext().setContextProperty("view", view)
out = {}
pending_callbacks = set()


def js(expression):
    exp = QQmlExpression(engine.contextForObject(view), view, expression)
    value, undefined = exp.evaluate()
    if exp.hasError():
        raise AssertionError(exp.error().toString())
    return value.toVariant() if hasattr(value, "toVariant") else (None if undefined else value)


def obj(name):
    return window.findChild(QObject, name)


def wait(ms=30):
    QTest.qWait(ms)


def settle():
    """Czeka na wszystkie odpowiedzi atrapy, także kolejne odczyty dowodów po Show."""
    deadline = time.monotonic() + 5
    while pending_callbacks:
        if time.monotonic() >= deadline:
            raise AssertionError("Nie zakończono odpowiedzi transportu scenariusza w 5 s")
        QTest.qWait(10)


def state():
    return json.loads(backend.stateJson)


def live_script(callback_delay=1):
    """Transport scenariusza z odpowiedziami; produkcyjna logika zapisu pozostaje ta sama."""
    backend._preview = False
    backend._available = True
    backend._capabilities = {"issue." + a for a in ("update", "submit", "reject", "reopen", "attach", "detach", "attachment")}
    calls, replies = [], []
    def call(method, args, callback, *unused):
        calls.append([method, *args])
        result = replies.pop(0) if replies else {"ok": True, "apiVersion": 1}
        token = object()
        pending_callbacks.add(token)
        def done():
            try:
                callback(result, b"")
            finally:
                pending_callbacks.discard(token)
        QTimer.singleShot(callback_delay, done)
    backend._call = call
    backend.reload = lambda: None
    # preview jest stałą Qt. Po zmianie transportu podepnij backend ponownie,
    # aby QML odczytał tę stałą tak jak przy starcie z usługą.
    engine.rootContext().setContextProperty("liveSource", backend)
    js("view.backend = null; view.backend = liveSource")
    return calls, replies


def run():
    try:
        with open(scenario) as fh:
            exec(fh.read(), globals())
    except Exception as exc:
        out["error"] = repr(exc)
    print(json.dumps(out, ensure_ascii=False, default=str))
    app.quit()


QTimer.singleShot(100, run)
app.exec()
