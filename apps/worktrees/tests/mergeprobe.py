"""Sonda okna Worktrees z prawdziwym backendem na prywatnej magistrali D-Bus i atrapą usługi (K-99, PT-395).

Użycie (z testów): /usr/bin/python3 mergeprobe.py KATALOG_QML SCENARIUSZ.py BACKEND.py ADRES_MAGISTRALI DANE.json
KATALOG_QML to qml/ widoku Worktrees z pakietu (stage.py) z Harness.qml, który ustawia `backend: realBackend`.
Atrapa usługi (`org.tide.Daemon`: `/Board`, `/Git`, `/Worktrees`) żyje w tym procesie na osobnym połączeniu i odpowiada
od razu: `/Board` z kolejek `replies` (ostatnia odpowiedź się powtarza), `/Git BranchMap` — `projects` na `{}`, mapa
(`map`, zmienna przez `set_map`) na `{project}`. Pomocnicze w scenariuszu jak w apps/board/tests/diffprobe.py
(js, wait, until, key, click, field, out) oraz BOARD() — wywołania `/Board`, MAPS() — liczba odczytów mapy, set_map(tree).
"""
import importlib.util
import json
import sys

from PyQt6.QtCore import QObject, QPoint, Qt, QTimer, QUrl, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBusConnection
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine, QQmlExpression
from PyQt6.QtTest import QTest

qml_dir, scenario_path, backend_path, address, data_path = sys.argv[1:6]
with open(data_path, encoding="utf-8") as fh:
    DATA = json.load(fh)
app = QGuiApplication([])
out = {}
BOARD_CALLS, MAP_CALLS = [], []


@pyqtClassInfo("D-Bus Interface", "org.tide.Daemon.Board")
class FakeBoard(QObject):
    def reply(self, member, *args):
        BOARD_CALLS.append([member, *args])
        queue = DATA["replies"].get(member) or [{"ok": False, "error": "unexpected"}]
        return json.dumps(queue.pop(0) if len(queue) > 1 else queue[0], ensure_ascii=False)

    @pyqtSlot(str, str, result=str)
    def ListCardBranches(self, card_id, request):
        return self.reply("ListCardBranches", card_id, request)

    @pyqtSlot(str, str, result=str)
    def PreviewCardMerge(self, card_id, request):
        return self.reply("PreviewCardMerge", card_id, request)

    @pyqtSlot(str, str, result=str)
    def MergeCard(self, card_id, request):
        return self.reply("MergeCard", card_id, request)

    @pyqtSlot(str, result=str)
    def CreateCard(self, request):
        return self.reply("CreateCard", request)

    @pyqtSlot(str, result=str)
    def MergeCardToMain(self, card_id):
        return self.reply("MergeCardToMain", card_id)


@pyqtClassInfo("D-Bus Interface", "org.tide.Daemon.Git")
class FakeGit(QObject):
    @pyqtSlot(str, result=str)
    def BranchMap(self, request):
        if json.loads(request) == {}:
            return json.dumps(DATA["projects"], ensure_ascii=False)
        MAP_CALLS.append(request)
        return json.dumps(DATA["map"], ensure_ascii=False)

    @pyqtSlot(str, result=str)
    def Diff(self, request):
        return json.dumps({"ok": False, "error": "unavailable", "message": "atrapa"})


@pyqtClassInfo("D-Bus Interface", "org.tide.Daemon.Worktrees")
class FakeWorktrees(QObject):
    @pyqtSlot(result=str)
    def List(self):
        return "[]"

    @pyqtSlot(result=str)
    def Launchers(self):
        return json.dumps({"terminal": False, "rider": False})

    @pyqtSlot()
    def Refresh(self):
        pass


daemon = QDBusConnection.connectToBus(address, "fake-daemon")
fakes = {"/Board": FakeBoard(), "/Git": FakeGit(), "/Worktrees": FakeWorktrees()}
for path, obj in fakes.items():
    if not daemon.registerObject(path, obj, QDBusConnection.RegisterOption.ExportAllSlots):
        raise SystemExit("nie zarejestrowano " + path)
if not daemon.registerService("org.tide.Daemon"):
    raise SystemExit("nie zarejestrowano usługi")

spec = importlib.util.spec_from_file_location("tide_worktrees_probe_backend", backend_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
backend = module.Backend(QDBusConnection.connectToBus(address, "worktrees-window"))
backend.setViewMode("branches")
backend.setBranchProject(DATA["projects"]["projects"][0]["slug"])

engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("realBackend", backend)
engine.load(QUrl.fromLocalFile(qml_dir + "/Harness.qml"))
window = engine.rootObjects()[0]

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


def BOARD():  # noqa: N802 — nazwa jak w opisie scenariusza
    return [list(c) for c in BOARD_CALLS]


def MAPS():  # noqa: N802
    return len(MAP_CALLS)


def set_map(tree):
    DATA["map"] = tree


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
