"""Tide.Button (K-128, PT-502, makieta S-9 `.btn`): offscreen, każdy wariant w prawdziwym oknie.

- tło, tekst i obrys każdego wariantu (primary, normal, ghost, danger, tonowany) pochodzą z tokenów palety,
- tekst każdego wariantu ma kontrast co najmniej 4,5:1 względem swojego tła (tło wariantu nałożone na tło okna
  i na tło kafelka),
- klik myszą, spacja, Enter i Return wywołują `clicked`; wyłączony przycisk nie reaguje na nic z tego i ma krycie
  0,45; rola Button; fokus z klawiatury pokazuje obwódkę teal,
- rozmiary: 32 px, mały 26 px; `kbd` obok tekstu; brak ostrzeżeń QML.
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

VARIANTS = ("primary", "normal", "ghost", "danger")

QML = """
import QtQuick
import "tide" as Tide

Item {
    id: root
    width: 760; height: 200
    property var counts: ({})
    function hit(name) { const c = Object.assign({}, counts); c[name] = (c[name] || 0) + 1; counts = c }
    function isButton(b) { return b.Accessible.role === Accessible.Button }
    function focusRing(b) {
        const all = (function walk(it, out) { out = out || []; if (it.objectName === "buttonFocus") out.push(it);
            for (const c of it.children) walk(c, out); return out })(b)
        return all.length === 1 && all[0].visible
    }
    Row {
        x: 10; y: 10; spacing: 10
        Tide.Button { objectName: "primary"; variant: "primary"; text: "Główny"; onClicked: root.hit(objectName) }
        Tide.Button { objectName: "normal"; text: "Zwykły"; onClicked: root.hit(objectName) }
        Tide.Button { objectName: "ghost"; variant: "ghost"; text: "Cichy"; onClicked: root.hit(objectName) }
        Tide.Button { objectName: "danger"; variant: "danger"; text: "Usuń"; onClicked: root.hit(objectName) }
        Tide.Button { objectName: "toned"; tone: Tide.Tokens.accent; text: "← Tablica"; onClicked: root.hit(objectName) }
    }
    Row {
        x: 10; y: 80; spacing: 10
        Tide.Button { objectName: "disabled"; text: "Wyłączony"; enabled: false; onClicked: root.hit(objectName) }
        Tide.Button { objectName: "disabledPrimary"; variant: "primary"; text: "Nie teraz"; enabled: false
                      onClicked: root.hit(objectName) }
        Tide.Button { objectName: "small"; small: true; text: "Mały"; onClicked: root.hit(objectName) }
        Tide.Button { objectName: "icon"; iconOnly: true; text: "⋯"; toolTip: "Więcej akcji"; onClicked: root.hit(objectName) }
        Tide.Button { objectName: "kbd"; variant: "primary"; text: "Wybierz"; kbd: "↵" }
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
view.resize(760, 200)
view.show()
QTest.qWait(300)
root = view.rootObject()
engine = view.engine()
out = {"buttons": {}}

def js(expr, target=None):
    value = QQmlExpression(engine.contextForObject(root), target or root, expr).evaluate()[0]
    return value.toVariant() if hasattr(value, "toVariant") else value

def argb(c):
    return [c.alpha(), c.red(), c.green(), c.blue()]

def child(item, name):
    return item.findChild(QObject, name)

def center(item):
    p = item.mapToScene(QPointF(item.property("width") / 2, item.property("height") / 2))
    return QPoint(int(p.x()), int(p.y()))

names = ("primary", "normal", "ghost", "danger", "toned", "disabled", "disabledPrimary", "small", "icon", "kbd")
for name in names:
    b = root.findChild(QObject, name)
    engine.rootContext().setContextProperty("probeButton", b)
    out["buttons"][name] = {
        "fill": argb(b.property("fill")), "ink": argb(b.property("ink")), "edge": argb(b.property("edge")),
        "background": argb(child(b, "buttonBackground").property("color")),
        "label": json.loads(js("JSON.stringify([Math.round(probeButton.labelItem.color.a * 255), "
                               "Math.round(probeButton.labelItem.color.r * 255), "
                               "Math.round(probeButton.labelItem.color.g * 255), "
                               "Math.round(probeButton.labelItem.color.b * 255)])")),
        "height": b.property("height"), "opacity": b.property("opacity"),
        "role": js("root.isButton(probeButton)"),
        "kbd": js("probeButton.kbdItem.visible"), "kbdText": js("probeButton.kbdItem.text"),
    }
for name in ("primary", "normal", "ghost", "danger", "toned", "disabled", "disabledPrimary", "small", "icon"):
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center(root.findChild(QObject, name)))
    QTest.qWait(40)
out["afterMouse"] = js("JSON.stringify(root.counts)")

keys = {"space": Qt.Key.Key_Space, "return": Qt.Key.Key_Return, "enter": Qt.Key.Key_Enter}
out["keys"] = {}
for name in ("normal", "disabled"):
    b = root.findChild(QObject, name)
    engine.rootContext().setContextProperty("probeButton", b)
    js("probeButton.forceActiveFocus(Qt.TabFocusReason)")
    QTest.qWait(40)
    out["keys"][name] = {"focus": js("probeButton.activeFocus"), "ring": js("root.focusRing(probeButton)")}
    for label, code in keys.items():
        before = json.loads(js("JSON.stringify(root.counts)")).get(name, 0)
        QTest.keyClick(view, code)
        QTest.qWait(40)
        out["keys"][name][label] = json.loads(js("JSON.stringify(root.counts)")).get(name, 0) - before
print(json.dumps(out))
"""


def rgb(hex_color: str) -> list[int]:
    h = hex_color.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def over(argb: list[int], base: list[int]) -> list[float]:
    """Kolor z kanałem alfa nałożony na nieprzezroczyste tło."""
    a = argb[0] / 255
    return [argb[i + 1] * a + base[i] * (1 - a) for i in range(3)]


def luminance(color: list[float]) -> float:
    def lin(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: list[float], b: list[float]) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class ButtonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="tide-button-")
        base = Path(cls.tmp.name)
        cls.palette = stage.load_palette(stage.DEFAULT_PALETTE)
        stage.copy_lib(str(base / "tide"), cls.palette)
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

    def tokens(self):
        p = self.palette
        return {"accent": rgb(p["accent"]), "window": rgb(p["background"]["window"]),
                "card": rgb(p["background"]["card"]), "primary": rgb(p["text"]["primary"]),
                "secondary": rgb(p["text"]["secondary"]), "rose": rgb(p["pastel"]["rose"]),
                "outline": p["opacity"]["outline"]}

    def assertColor(self, actual, alpha, color, msg=""):
        self.assertLessEqual(abs(actual[0] - round(alpha * 255)), 1, (msg, actual))
        self.assertEqual(actual[1:], color, msg)

    def test_variant_colors_come_from_the_palette(self):
        t = self.tokens()
        b = self.out["buttons"]
        expected = {
            "primary": ((1, t["accent"]), (1, t["window"]), (0, None)),
            "normal": ((0.06, t["primary"]), (1, t["primary"]), (t["outline"], t["primary"])),
            "ghost": ((0, None), (1, t["secondary"]), (0, None)),
            "danger": ((0.14, t["rose"]), (1, t["rose"]), (0.35, t["rose"])),
            "toned": ((0.12, t["accent"]), (1, t["accent"]), (0.45, t["accent"])),
        }
        for name, (fill, ink, edge) in expected.items():
            with self.subTest(name):
                if fill[1] is None:
                    self.assertEqual(b[name]["fill"][0], 0, b[name])
                else:
                    self.assertColor(b[name]["fill"], *fill, msg="tło")
                self.assertColor(b[name]["ink"], *ink, msg="tekst")
                if edge[1] is None:
                    self.assertEqual(b[name]["edge"][0], 0, b[name])
                else:
                    self.assertColor(b[name]["edge"], *edge, msg="obrys")
                # Narysowane tło i tekst to te same kolory co właściwości wariantu.
                self.assertEqual(b[name]["background"], b[name]["fill"])
                self.assertEqual(b[name]["label"], b[name]["ink"])

    def test_text_contrast_is_at_least_4_5_on_window_and_card(self):
        t = self.tokens()
        for name in ("primary", "normal", "ghost", "danger", "toned", "small", "icon"):
            for surface in ("window", "card"):
                with self.subTest(name=name, surface=surface):
                    b = self.out["buttons"][name]
                    background = over(b["fill"], t[surface])
                    ink = over(b["ink"], background)
                    self.assertGreaterEqual(contrast(ink, background), 4.5, (name, surface))

    def test_mouse_click_works_and_disabled_buttons_do_not_react(self):
        counts = json.loads(self.out["afterMouse"])
        for name in ("primary", "normal", "ghost", "danger", "toned", "small", "icon"):
            self.assertEqual(counts.get(name), 1, name)
        self.assertNotIn("disabled", counts)
        self.assertNotIn("disabledPrimary", counts)
        self.assertEqual(self.out["buttons"]["disabled"]["opacity"], 0.45)
        self.assertEqual(self.out["buttons"]["normal"]["opacity"], 1)

    def test_space_enter_and_return_click_and_focus_shows_teal_ring(self):
        keys = self.out["keys"]["normal"]
        self.assertTrue(keys["focus"])
        self.assertTrue(keys["ring"], "obwódka fokusu z klawiatury")
        self.assertEqual((keys["space"], keys["return"], keys["enter"]), (1, 1, 1))
        disabled = self.out["keys"]["disabled"]
        self.assertFalse(disabled["focus"], "wyłączony przycisk nie bierze fokusu")
        self.assertEqual((disabled["space"], disabled["return"], disabled["enter"]), (0, 0, 0))

    def test_role_sizes_and_shortcut(self):
        b = self.out["buttons"]
        for name in ("primary", "normal", "ghost", "danger", "toned", "disabled", "small", "icon"):
            self.assertTrue(b[name]["role"], name)
        self.assertEqual(b["normal"]["height"], 32)
        self.assertEqual(b["small"]["height"], 26)
        self.assertTrue(b["kbd"]["kbd"])
        self.assertEqual(b["kbd"]["kbdText"], "↵")
        self.assertFalse(b["normal"]["kbd"])

    def test_focus_ring_uses_teal_from_the_palette(self):
        source = (ROOT / "widgets" / "lib" / "Button.qml").read_text(encoding="utf-8")
        self.assertIn("border.color: Tokens.teal", source)
        self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', source), [], "bez kolorów wpisanych na sztywno")

    def test_no_qml_problems(self):
        self.assertEqual(re.findall(PROBLEMS, self.stderr), [], self.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
