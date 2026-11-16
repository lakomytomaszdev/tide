"""Tide.ScrollBar (K-128, makieta S-9 wariant A): offscreen, na Flickable z krótką i długą treścią.

- widoczny od razu (bez przewijania), gdy treść jest dłuższa od widoku, ukryty, gdy nie jest — pion i poziom,
- kolory i szerokość z tokenów palety (tor ~4 %, uchwyt ~30 %, akcent ~75 % po najechaniu i przy przeciąganiu),
- przeciągnięcie uchwytu przewija treść,
- brak ostrzeżeń „binding loop” i innych problemów QML.
"""
import json
import os
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
import QtQuick.Controls as QQC2
import "tide" as Tide

Item {
    width: 400; height: 300
    property alias vShort: vShort
    property alias vLong: vLong
    property alias hShort: hShort
    property alias hLong: hLong

    Flickable { id: vShort; objectName: "vShort"; x: 0; y: 0; width: 180; height: 130
        contentWidth: width; contentHeight: 60; clip: true
        QQC2.ScrollBar.vertical: Tide.ScrollBar { objectName: "bar" } }
    Flickable { id: vLong; objectName: "vLong"; x: 200; y: 0; width: 180; height: 130
        contentWidth: width; contentHeight: 900; clip: true
        QQC2.ScrollBar.vertical: Tide.ScrollBar { objectName: "bar" } }
    Flickable { id: hShort; objectName: "hShort"; x: 0; y: 150; width: 180; height: 130
        contentWidth: 100; contentHeight: height; clip: true
        QQC2.ScrollBar.horizontal: Tide.ScrollBar { objectName: "bar" } }
    Flickable { id: hLong; objectName: "hLong"; x: 200; y: 150; width: 180; height: 130
        contentWidth: 900; contentHeight: height; clip: true
        QQC2.ScrollBar.horizontal: Tide.ScrollBar { objectName: "bar" } }
}
"""

PROBE = r"""
import json, sys
from PyQt6.QtCore import QEvent, QObject, QPointF, Qt, QTimer, QUrl
from PyQt6.QtGui import QGuiApplication, QMouseEvent, QPointingDevice
from PyQt6.QtQuick import QQuickView
app = QGuiApplication([])
view = QQuickView()
view.setSource(QUrl.fromLocalFile(sys.argv[1]))
view.resize(400, 300)
view.show()
root = view.rootObject()
out = {}

def bar_of(name):
    return root.findChild(QObject, name).findChild(QObject, "bar")

def pump(ms):
    from PyQt6.QtCore import QEventLoop
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()

def color(item):
    c = item.property("color")
    return [c.alpha(), c.red(), c.green(), c.blue()]

def mouse(kind, x, y, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton):
    ev = QMouseEvent(kind, QPointF(x, y), QPointF(view.mapToGlobal(QPointF(x, y).toPoint())), button, buttons,
                     Qt.KeyboardModifier.NoModifier)
    app.sendEvent(view, ev)

pump(300)
for name in ("vShort", "vLong", "hShort", "hLong"):
    bar = bar_of(name)
    out[name] = {"visible": bar.property("visible"), "size": bar.property("size"),
                 "width": bar.property("width"), "height": bar.property("height")}
long_bar = bar_of("vLong")
out["rest"] = color(long_bar.property("contentItem"))
out["track"] = color(long_bar.property("background"))
out["flickY0"] = root.findChild(QObject, "vLong").property("contentY")
# najechanie na uchwyt
pos = long_bar.mapToScene(QPointF(long_bar.property("width") / 2, 10)).toPoint()
mouse(QEvent.Type.MouseMove, pos.x(), pos.y(), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton)
pump(400)
out["hover"] = color(long_bar.property("contentItem"))
# przeciągnięcie uchwytu w dół
mouse(QEvent.Type.MouseButtonPress, pos.x(), pos.y())
pump(100)
for step in range(1, 7):
    mouse(QEvent.Type.MouseMove, pos.x(), pos.y() + step * 12, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    pump(30)
out["pressed"] = long_bar.property("pressed")
out["dragging"] = color(long_bar.property("contentItem"))
mouse(QEvent.Type.MouseButtonRelease, pos.x(), pos.y() + 72, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
pump(100)
out["flickY1"] = root.findChild(QObject, "vLong").property("contentY")
print(json.dumps(out))
"""


class ScrollBarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="tide-scrollbar-")
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
        cls.palette = palette

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_visible_without_scrolling_only_when_content_is_longer(self):
        for name, visible in (("vLong", True), ("hLong", True), ("vShort", False), ("hShort", False)):
            with self.subTest(name):
                self.assertEqual(self.out[name]["visible"], visible, self.out[name])
        self.assertLess(self.out["vLong"]["size"], 1.0)
        self.assertEqual(self.out["vShort"]["size"], 1.0)

    def test_width_comes_from_palette(self):
        width = self.palette["scrollbar"]["width"]
        self.assertEqual(self.out["vLong"]["width"], width)
        self.assertEqual(self.out["hLong"]["height"], width)

    def test_colors_come_from_palette_tokens(self):
        sb = self.palette["scrollbar"]
        text = self.palette["text"]["primary"].lstrip("#")
        accent = self.palette["accent"].lstrip("#")
        rgb = lambda h: [int(h[i:i + 2], 16) for i in (0, 2, 4)]  # noqa: E731
        self.assertEqual(self.out["track"], [round(sb["track"] * 255)] + rgb(text))
        self.assertEqual(self.out["rest"], [round(sb["thumb"] * 255)] + rgb(text))
        self.assertEqual(self.out["hover"], [round(sb["thumbActive"] * 255)] + rgb(accent))

    def test_dragging_keeps_accent_and_scrolls_content(self):
        self.assertTrue(self.out["pressed"])
        self.assertEqual(self.out["dragging"], self.out["hover"])
        self.assertEqual(self.out["flickY0"], 0)
        self.assertGreater(self.out["flickY1"], 20)

    def test_no_qml_problems(self):
        import re
        self.assertEqual(re.findall(PROBLEMS, self.stderr), [], self.stderr[-2000:])


class PaletteTokensTest(unittest.TestCase):
    def test_tokens_expose_scrollbar_block_at_the_end(self):
        text = stage.palette_qml(stage.load_palette(stage.DEFAULT_PALETTE))
        for name in ("scrollbarTrack", "scrollbarThumb", "scrollbarThumbActive", "scrollbarWidth"):
            self.assertIn(f"property", [l for l in text.splitlines() if name + ":" in l][0])
        self.assertIn("readonly property int scrollbarWidth: 10", text)
        self.assertIn('readonly property color scrollbarTrack: "#0aeceff1"', text)
        self.assertIn('readonly property color scrollbarThumb: "#4ceceff1"', text)
        self.assertIn('readonly property color scrollbarThumbActive: "#bf6fd6c6"', text)


if __name__ == "__main__":
    unittest.main()
