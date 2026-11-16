"""Tide.Sheet (K-128, PT-504, makieta S-9 `.sheet`): offscreen, w prawdziwym oknie.

- szerokość szuflady to `widthFraction` okna, nie mniej niż `minSheetWidth` i nie więcej niż widok,
- ✕, klik w przyciemnienie i Esc wywołują `closeRequested`; klik w szufladę nie zamyka,
- `contentTakesEscape` oddaje Esc gospodarzowi (Sheet nie zamyka i nie przyjmuje zdarzenia),
- nagłówek, stopka i pasek kroków przyjmują elementy gospodarza; pusta stopka i brak kroków nie zajmują miejsca,
- długa treść przewija się z widocznym `Tide.ScrollBar`, a krótka nie ma paska; `scrolls: false` daje kolumnie
  całą wysokość,
- brak ostrzeżeń QML.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "widgets"))
import stage  # noqa: E402

PROBLEMS = r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|is not defined|" \
           r"Cannot read|unavailable|Invalid property|Binding loop|failed to load"

QML = """
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Item {
    id: root
    width: 1380; height: 700
    property var closes: 0
    property int hostEscapes: 0
    property bool takes: false
    property bool longBody: false
    property bool scrolls: true
    property var steps: []
    property real fraction: 0.64
    Keys.onPressed: event => { if (event.key === Qt.Key_Escape) { root.hostEscapes += 1; event.accepted = true } }
    Tide.Sheet {
        id: sheet
        objectName: "sheet"
        anchors.fill: parent
        widthFraction: root.fraction
        steps: root.steps
        scrolls: root.scrolls
        contentTakesEscape: root.takes
        onCloseRequested: root.closes += 1
        header: [
            Text { objectName: "headerText"; text: "K-1"; color: "white"; Layout.fillWidth: true }
        ]
        Text { objectName: "bodyText"; text: "treść"; color: "white" }
        Repeater {
            model: root.longBody ? 60 : 0
            Text { text: "wiersz " + index; color: "white"; height: 24 }
        }
        Item { objectName: "fillItem"; Layout.fillWidth: true; Layout.fillHeight: true }
        footer: [
            Tide.Button { objectName: "footerButton"; text: "Zapisz" }
        ]
    }
}
"""

PROBE = r"""
import json, sys
from PyQt6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQuick import QQuickView
from PyQt6.QtQml import QQmlExpression
from PyQt6.QtTest import QTest
app = QGuiApplication([])
view = QQuickView()
view.setSource(QUrl.fromLocalFile(sys.argv[1]))
view.resize(1380, 700)
view.show()
QTest.qWait(300)
root = view.rootObject()
engine = view.engine()
out = {}

def js(expr):
    value = QQmlExpression(engine.contextForObject(root), root, expr).evaluate()[0]
    return value.toVariant() if hasattr(value, "toVariant") else value

def wait(ms=60):
    QTest.qWait(ms)

def find(name):
    return root.findChild(QObject, name)

def center(name):
    item = find(name)
    p = item.mapToScene(QPointF(item.property("width") / 2, item.property("height") / 2))
    return QPoint(int(p.x()), int(p.y()))

def click(point):
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    wait()

def closes():
    return js("root.closes")

out["width"] = js("sheet.sheetWidth")
js("root.fraction = 0.3")
wait()
out["minWidth"] = js("sheet.sheetWidth")
js("root.fraction = 0.64")
wait()
out["left"] = js("sheet.sheetItem.x")
out["right"] = js("sheet.sheetItem.x + sheet.sheetItem.width")
out["headerChild"] = find("headerText") is not None
out["footerVisible"] = find("sheetFooter").property("visible")

out["stepsBefore"] = [find("sheetSteps").property("visible"), find("sheetSteps").property("height")]
js("root.steps = ['Wybór', 'Podgląd']")
wait()
out["stepsAfter"] = [find("sheetSteps").property("visible"), find("sheetSteps").property("height")]
js("root.steps = []")

# ✕, przyciemnienie, szuflada.
before = closes()
click(center("sheetClose"))
out["closeButton"] = closes() - before
before = closes()
click(QPoint(100, 300))
out["dim"] = closes() - before
before = closes()
click(center("bodyText"))
out["drawerClick"] = closes() - before

# Esc: zamyka; z contentTakesEscape trafia do gospodarza.
js("sheet.forceActiveFocus()")
wait()
before = closes()
QTest.keyClick(view, Qt.Key.Key_Escape)
wait()
out["escCloses"] = closes() - before
out["escHost"] = js("root.hostEscapes")
js("root.takes = true")
wait()
before = closes()
QTest.keyClick(view, Qt.Key.Key_Escape)
wait()
out["escTaken"] = closes() - before
out["escTakenHost"] = js("root.hostEscapes")
js("root.takes = false")

# Przewijanie: krótka treść bez paska, długa z paskiem; scrolls: false daje kolumnie całą wysokość.
out["shortHeight"] = [js("sheet.flick.contentHeight"), js("sheet.flick.height")]
js("root.longBody = true")
wait(200)
out["longHeight"] = [js("sheet.flick.contentHeight"), js("sheet.flick.height")]
out["longInteractive"] = js("sheet.flick.interactive")
js("sheet.flick.contentY = 300")
wait()
out["scrolled"] = js("sheet.flick.contentY")
js("root.longBody = false; root.scrolls = false")
wait(200)
out["fixed"] = [js("sheet.contentColumn.height"), js("sheet.flick.height"), find("fillItem").property("height")]
out["fixedInteractive"] = js("sheet.flick.interactive")
print(json.dumps(out))
"""


class SheetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="tide-sheet-")
        base = Path(cls.tmp.name)
        palette = stage.load_palette(stage.DEFAULT_PALETTE)
        stage.copy_lib(str(base / "tide"), palette)
        (base / "Probe.qml").write_text(QML, encoding="utf-8")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        result = subprocess.run([sys.executable, "-c", PROBE, str(base / "Probe.qml")], env=env,
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr[-3000:]
        cls.stderr = result.stderr
        cls.out = json.loads(result.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_width_is_a_fraction_of_the_window_with_a_minimum(self):
        self.assertEqual(self.out["width"], round(0.64 * 1380))
        self.assertEqual(self.out["minWidth"], 640, "najmniej 640 px")
        self.assertEqual(self.out["right"], 1380, "szuflada przy prawej krawędzi")
        self.assertEqual(self.out["left"], 1380 - round(0.64 * 1380))

    def test_slots_header_footer_and_steps(self):
        self.assertTrue(self.out["headerChild"])
        self.assertTrue(self.out["footerVisible"])
        self.assertEqual(self.out["stepsBefore"], [False, 0], "bez kroków nie ma paska")
        self.assertEqual(self.out["stepsAfter"], [True, 40])

    def test_close_by_button_dim_and_escape(self):
        self.assertEqual(self.out["closeButton"], 1)
        self.assertEqual(self.out["dim"], 1, "klik w przyciemnienie zamyka")
        self.assertEqual(self.out["drawerClick"], 0, "klik w szufladę nie zamyka")
        self.assertEqual(self.out["escCloses"], 1)
        self.assertEqual(self.out["escHost"], 0, "Esc zamykający Sheet nie idzie dalej")

    def test_content_can_take_escape(self):
        self.assertEqual(self.out["escTaken"], 0, "treść przejęła Esc: Sheet nie zamyka")
        self.assertEqual(self.out["escTakenHost"], 1, "zdarzenie idzie do gospodarza")

    def test_long_content_scrolls_and_short_does_not(self):
        content, height = self.out["shortHeight"]
        self.assertLessEqual(content - 48, height, "krótka treść mieści się w ramie")
        content, height = self.out["longHeight"]
        self.assertGreater(content, height)
        self.assertTrue(self.out["longInteractive"])
        self.assertGreater(self.out["scrolled"], 0)

    def test_fixed_body_gives_the_column_the_whole_height(self):
        column, flick, fill = self.out["fixed"]
        self.assertEqual(column, flick - 18 - 12)
        self.assertGreater(fill, 200, "element z Layout.fillHeight rośnie do reszty")
        self.assertFalse(self.out["fixedInteractive"])

    def test_no_qml_problems(self):
        self.assertIsNone(re.search(PROBLEMS, self.stderr), self.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
