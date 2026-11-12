"""Podświetlanie składni w widoku diffa (K-36, PT-351): apps/common/qml/git/DiffView.qml, SyntaxText.qml,
ModuleHighlighter.qml i diff.js.

- główna ścieżka na atrapie podświetlacza wstrzykniętej jednym punktem (`highlighter`), niezależnie od modułu:
  podświetlenie tylko dla widocznych linii plików znanych typów (`.py`, `.qml`), nie dla `.txt`, pliku `large` ani linii
  poza widokiem 20000-liniowego diffu; ten sam wynik w trybie obok siebie,
- brak modułu (wymuszony plikiem importującym nieistniejący moduł): widok ładuje się bez ostrzeżeń, linie są zwykłym
  tekstem, a ściągawka mówi, że podświetlania nie ma,
- prawdziwy moduł KSyntaxHighlighting (test pomijany z powodem, gdy go brak w środowisku),
- kolory diffu pod podświetleniem: tło delegatu linii dodanej, usuniętej i kontekstowej jest takie samo z podświetleniem
  i bez niego i równe tokenom palety; kolory formatów mają kontrast >= 4,5:1 na każdym z trzech teł (WCAG 2.x),
- brak kolorów wpisanych w QML podświetlania.
"""
import json
import os
import re
import subprocess
import unittest

from . import test_diff_view as base
from .test_diff_view import DIFF, GIT_DIR, PROBE, file_entry, hunk, line
from .test_view import QML_PROBLEMS, offscreen

MOCK_HARNESS = base.HARNESS.replace(
    "    readonly property var tokens: Tide.Tokens\n",
    '''    readonly property var tokens: Tide.Tokens
    // Atrapa podświetlacza (PT-351): zlicza utworzone i żyjące instancje, a kolory formatów przepuszcza przez `readable`.
    property string hlSource: "ModuleHighlighter.qml"
    property bool useMock: false
    property var mockColors: []
    property var created: []
    property var appliedLast: []
    property int live: 0
    Component {
        id: mockHighlighter
        QtObject {
            id: mock
            property var textEdit: null
            property string language: ""
            property var readable: null
            Component.onCompleted: {
                root.created = root.created.concat([{language: language, text: textEdit.text}])
                root.live += 1
                root.appliedLast = root.mockColors.map(c => readable(Qt.color(c)))
            }
            Component.onDestruction: root.live -= 1
        }
    }
''').replace("                backend: fake\n", '''                backend: fake
                highlighterSource: root.hlSource
                highlighter: root.useMock ? mockHighlighter : null
''')

MISSING = '''import QtQuick
import org.kde.nonexistentsyntaxhighlighting

QtObject {
    property var textEdit: null
    property string language: ""
    property var readable: null
}
'''

PY_FILE = DIFF["files"][0]  # apps/a.py
QML_FILE = file_entry("ui/panel.qml", "M", 1, 1, [hunk(1, [
    line(" ", 1, 1, "import QtQuick"), line("-", 2, None, "width: 10"), line("+", None, 2, "width: 20")])])
LARGE_PY = dict(file_entry("big_module.py", "M", 3500, 0, []), large=True, lines=3500)
BIG_PY = dict(base.BIG, path="big.py")
EXTRA = [QML_FILE, LARGE_PY, BIG_PY]

HEAD = '''
def select(path):
    js("loader.item.selectFile(loader.item.files.map(f => f.path).indexOf(%s))" % json.dumps(path))
    wait(150)

def mock_state():
    return {"live": js("live"), "created": json.loads(js("JSON.stringify(created)"))}

def start(path, mock=True, source="ModuleHighlighter.qml"):
    js("useMock = %s" % ("true" if mock else "false"))
    js("hlSource = %s" % json.dumps(source))
    open_view(path=path)
    reply(last_tag(), DIFF)
    wait(150)
'''


def parse_color(text):
    """`#rrggbb` albo `#aarrggbb` → (r, g, b, alfa) w 0..1."""
    digits = text.lstrip("#")
    alpha = 1.0
    if len(digits) == 8:
        alpha = int(digits[:2], 16) / 255
        digits = digits[2:]
    r, g, b = (int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return r, g, b, alpha


def luminance(color):
    def channel(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = color[:3]
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def over(top, bottom):
    r, g, b, a = top
    return tuple(c * a + d * (1 - a) for c, d in zip((r, g, b), bottom[:3]))


class HighlightCase(base.QmlCase):
    stderr = ""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(os.path.join(cls.tmp, "Harness.qml"), "w", encoding="utf-8") as fh:
            fh.write(MOCK_HARNESS)
        with open(os.path.join(cls.tmp, "git", "MissingHighlighter.qml"), "w", encoding="utf-8") as fh:
            fh.write(MISSING)

    def probe(self, scenario: str, files=EXTRA) -> dict:
        script = os.path.join(self.tmp, "scenario.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(base.SCENARIO_HEAD + HEAD + "\nDIFF = json.loads(" + repr(json.dumps(DIFF)) + ")\n"
                     + "DIFF['files'] = DIFF['files'] + json.loads(" + repr(json.dumps(list(files))) + ")\n"
                     + scenario)
        env = offscreen()
        env["TZ"] = "UTC"
        result = subprocess.run(["/usr/bin/python3", PROBE, self.tmp, script], env=env, capture_output=True,
                                text=True, timeout=90)
        self.stderr = result.stderr
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIsNone(re.search(QML_PROBLEMS, result.stderr), result.stderr[-3000:])
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertNotIn("error", out, out)
        return out


class MockHighlighterTest(HighlightCase):
    def test_visible_lines_of_known_types_are_highlighted_and_others_are_not(self):
        out = self.probe('''
start("apps/a.py")
out["py"] = mock_state()
select("new.txt")
out["txt"] = mock_state()
select("big_module.py")
out["large"] = mock_state()
out["largeNote"] = texts("diffFileNote")
select("ui/panel.qml")
out["qml"] = mock_state()
key(Qt.Key.Key_U)
wait(150)
out["split"] = mock_state()
''')
        py = out["py"]
        self.assertGreater(py["live"], 0)
        self.assertLess(py["live"], 120, "tylko widoczne linie, nie wszystkie linie pliku")
        self.assertEqual({c["language"] for c in py["created"]}, {"Python"})
        self.assertNotIn("def pierwsza():", [c["text"] for c in py["created"]], "nagłówek hunka nie jest kodem")
        self.assertEqual(out["txt"]["live"], 0, "nieznane rozszerzenie: zwykły tekst")
        self.assertEqual(len(out["txt"]["created"]), len(py["created"]), "plik .txt nie tworzy podświetlenia")
        self.assertEqual(out["large"]["live"], 0)
        self.assertEqual(len(out["large"]["created"]), len(py["created"]), "plik large nie tworzy podświetlenia")
        self.assertIn("duży", out["largeNote"][0])
        qml = out["qml"]["created"][len(py["created"]):]
        self.assertEqual(sorted(c["text"] for c in qml), ["import QtQuick", "width: 10", "width: 20"])
        self.assertEqual({c["language"] for c in qml}, {"QML"})
        split = out["split"]["created"][len(py["created"]) + len(qml):]
        self.assertEqual(sorted(c["text"] for c in split),
                         ["import QtQuick", "import QtQuick", "width: 10", "width: 20"],
                         "obok siebie: kontekst po obu stronach, usunięte po lewej, dodane po prawej")

    def test_lines_outside_a_scrolled_20000_line_diff_get_no_highlighter(self):
        out = self.probe('''
start("big.py")
out["top"] = mock_state()
counts = []
for y in (30000, 200000, 380000):
    js("loader.item.activeList.contentY = " + str(y))
    wait(120)
    counts.append(js("live"))
out["live"] = counts
out["all"] = mock_state()
''')
        top = out["top"]["created"]
        self.assertTrue(0 < len(top) < 120, len(top))
        self.assertTrue(all(c["text"].startswith("linia 0/") for c in top), "linie poza widokiem nie mają podświetlacza")
        self.assertTrue(all(0 < n < 120 for n in out["live"]), out["live"])
        self.assertLess(len(out["all"]["created"]), 600, "przewijanie nie tworzy podświetlaczy dla całego pliku")

    def test_language_comes_from_the_extension(self):
        out = self.probe('''
names = ["a.py", "x/b.qml", "c.js", "d.mjs", "e.cs", "README.md", "f.yml", "g.yaml", "h.json", "i.sh", "j.bash",
         "noext", ".gitignore", "x.PY", "a.b/c", "z.txt", "Makefile", "constructor", "x.hasOwnProperty"]
out["langs"] = [js("Diff.languageFor(%s)" % json.dumps(n)) for n in names]
out["binary"] = js("Diff.highlightLanguage({path: 'a.py', binary: true})")
out["large"] = js("Diff.highlightLanguage({path: 'a.py', large: true, loaded: true})")
out["none"] = js("Diff.highlightLanguage(null)")
''', files=[])
        self.assertEqual(out["langs"], ["Python", "QML", "JavaScript", "JavaScript", "C#", "Markdown", "YAML", "YAML",
                                        "JSON", "Bash", "Bash", "", "", "Python", "", "", "", "", ""])
        self.assertEqual([out["binary"], out["large"], out["none"]], ["", "", ""])


class MissingModuleTest(HighlightCase):
    def test_view_loads_without_warnings_and_says_the_module_is_missing(self):
        out = self.probe('''
start("apps/a.py", mock=False, source="MissingHighlighter.qml")
out["available"] = view("highlightAvailable")
out["lines"] = texts("diffLineText")[:3]
out["syntax"] = field("diffSyntaxText", "it.item !== null")
out["noteBefore"] = len(field("diffHighlightNote", "1"))
key(Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier)
out["note"] = texts("diffHighlightNote")
key(Qt.Key.Key_Escape)
start("apps/a.py", mock=False, source="MissingHighlighter.qml")
key(Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier)
out["noteAgain"] = texts("diffHighlightNote")
''')
        self.assertFalse(out["available"])
        self.assertIn("kontekst 1", out["lines"], "bez modułu widok działa jak dotąd")
        self.assertTrue(all(not shown for shown in out["syntax"]))
        self.assertEqual(out["noteBefore"], 0, "informacja jest w ściągawce, nie w widoku")
        expected = ["Podświetlanie składni niedostępne (brak modułu KSyntaxHighlighting)"]
        self.assertEqual(out["note"], expected)
        self.assertEqual(out["noteAgain"], expected)
        problems = [l for l in self.stderr.splitlines() if re.search(r"qml|QML|module|highlight", l, re.I)]
        self.assertEqual(problems, [], "brak ostrzeżeń w logu przy otwarciu")

    def test_cheat_sheet_has_no_missing_note_when_a_highlighter_is_available(self):
        out = self.probe('''
start("apps/a.py")
key(Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier)
out["available"] = view("highlightAvailable")
out["note"] = texts("diffHighlightNote")
''')
        self.assertTrue(out["available"])
        self.assertEqual(out["note"], [])


class RealModuleTest(HighlightCase):
    def test_a_python_line_gets_the_real_highlighter(self):
        out = self.probe('''
start("apps/a.py", mock=False)
out["available"] = view("highlightAvailable")
if out["available"]:
    out["names"] = field("diffSyntaxText", "it.instance ? it.instance.engine.definition.name : ''")
''')
        if not out["available"]:
            self.skipTest("brak modułu QML org.kde.syntaxhighlighting (KSyntaxHighlighting) w środowisku testów")
        names = [n for n in out["names"] if n]   # wiersze bez linii kodu (nagłówek hunka) nie mają podświetlacza
        self.assertGreater(len(names), 20, "widoczne linie kodu mają prawdziwy podświetlacz")
        self.assertEqual(set(names), {"Python"})


class ColorsUnderHighlightTest(HighlightCase):
    COLORS = '''
def delegate_colors():
    rows = field("diffLine", "[it.kind, String(it.color)]")
    seen = {}
    for kind, color in rows:
        seen.setdefault(kind, color)
    return seen

def tokens():
    alpha = view("tintAlpha")
    def tint(name):
        return js("String(Qt.rgba(tokens.%s.r, tokens.%s.g, tokens.%s.b, %r))" % (name, name, name, alpha))
    return {"added": tint("added"), "removed": tint("removed"),
            "window": js("String(tokens.backgroundWindow)"), "primary": js("String(tokens.textPrimary)"),
            "view": js("String(loader.item.color)")}
'''

    def test_delegate_backgrounds_are_the_palette_tokens_with_and_without_highlighting(self):
        out = self.probe(self.COLORS + '''
js("mockColors = []")
start("apps/a.py", mock=True)
out["on"] = delegate_colors()
out["liveOn"] = js("live")
start("apps/a.py", mock=False, source="MissingHighlighter.qml")
out["off"] = delegate_colors()
out["tokens"] = tokens()
''')
        self.assertGreater(out["liveOn"], 0)
        self.assertEqual(out["on"], out["off"], "podświetlenie nie zmienia tła delegatów")
        for kind in (" ", "+", "-"):
            self.assertIn(kind, out["on"])
        self.assertEqual(out["on"]["+"], out["tokens"]["added"])
        self.assertEqual(out["on"]["-"], out["tokens"]["removed"])
        self.assertEqual(parse_color(out["on"][" "])[3], 0.0, "kontekst: przezroczysty, widać tło okna")
        self.assertEqual(out["tokens"]["view"], out["tokens"]["window"])

    def test_format_colors_have_a_contrast_of_4_5_on_every_diff_background(self):
        dark_blue, pale_yellow, grey = "#101080", "#f0e68c", "#555555"
        out = self.probe(self.COLORS + '''
js("mockColors = " + %r)
start("apps/a.py", mock=True)
out["applied"] = json.loads(js("JSON.stringify(appliedLast.map(c => String(c)))"))
out["delegates"] = delegate_colors()
out["tokens"] = tokens()
''' % json.dumps([dark_blue, pale_yellow, grey]))
        window = parse_color(out["tokens"]["window"])
        backgrounds = [window, over(parse_color(out["delegates"]["+"]), window),
                       over(parse_color(out["delegates"]["-"]), window)]
        self.assertEqual(len(out["applied"]), 3)
        for color in out["applied"]:
            for bg in backgrounds:
                self.assertGreaterEqual(contrast(parse_color(color), bg), 4.5, (color, bg))
        self.assertEqual(out["applied"][0], out["tokens"]["primary"], "ciemny niebieski: zastąpiony tekstem z palety")
        self.assertEqual(out["applied"][1], pale_yellow, "czytelny kolor zostaje")
        self.assertEqual(out["applied"][2], out["tokens"]["primary"], "szary poniżej progu: zastąpiony")

    def test_contrast_helper_matches_wcag_values(self):
        out = self.probe('''
out["bw"] = js("Diff.contrast({r: 0, g: 0, b: 0}, {r: 1, g: 1, b: 1})")
out["same"] = js("Diff.contrast({r: 0.5, g: 0.5, b: 0.5}, {r: 0.5, g: 0.5, b: 0.5})")
out["blend"] = js("Diff.blend({r: 1, g: 0, b: 0}, 0.25, {r: 0, g: 0, b: 1})")
''', files=[])
        self.assertAlmostEqual(out["bw"], 21.0, places=6)
        self.assertAlmostEqual(out["same"], 1.0, places=6)
        self.assertEqual(out["blend"], {"r": 0.25, "g": 0, "b": 0.75})


class SourceTest(unittest.TestCase):
    def test_highlighting_code_has_no_hard_coded_colors_and_no_static_module_import(self):
        for name in ("SyntaxText.qml", "ModuleHighlighter.qml", "DiffView.qml"):
            with open(os.path.join(GIT_DIR, name), encoding="utf-8") as fh:
                code = re.sub(r"//.*", "", fh.read())
            self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", code), [], name)
            self.assertEqual(re.findall(r'color:\s*"(?!transparent")[^"]*"', code), [], name)
            self.assertNotIn("Qt.rgba(", code.replace("Tide.Theme.alpha", ""), name)
            if name != "ModuleHighlighter.qml":
                self.assertNotIn("org.kde.syntaxhighlighting", code, "moduł tylko dynamicznie, przez ModuleHighlighter.qml")
        with open(os.path.join(GIT_DIR, "diff.js"), encoding="utf-8") as fh:
            js = re.sub(r"//.*", "", fh.read())
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", js), [])

    def test_text_edit_exists_only_in_the_line_loader(self):
        with open(os.path.join(GIT_DIR, "DiffView.qml"), encoding="utf-8") as fh:
            view = re.sub(r"//.*", "", fh.read())
        self.assertEqual(view.count("TextEdit {"), 1, "w DiffView tylko niewidoczny TextEdit schowka")
        self.assertEqual(view.count("SyntaxText {"), 3)
        with open(os.path.join(GIT_DIR, "SyntaxText.qml"), encoding="utf-8") as fh:
            syntax = re.sub(r"//.*", "", fh.read())
        self.assertIn("Loader {", syntax)
        self.assertIn("active: language !== \"\" && highlighter !== null", syntax)


if __name__ == "__main__":
    unittest.main()
