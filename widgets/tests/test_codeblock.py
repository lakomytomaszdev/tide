"""Tide.CodeBlock (K-128, PT-506): kolorowanie powłoki widgets/lib/shell.js i komponent bloku kodu.

- shell.js liczy się w qml6 offscreen tym samym kodem, którego używa Tide.CodeBlock: tabela tokenizera (komentarz,
  słowo kluczowe, napis w '…' i "…", zmienna $X i ${X}, polecenie na początku linii i po separatorze), złączone
  kawałki dają linię znak w znak, HTML linii escapuje każdy kawałek (treść skryptu to niezaufany tekst), kolory
  z palety mają kontrast co najmniej 4,5:1 na tle bloku,
- Tide.CodeBlock w oknie offscreen (PyQt6): domyślnie zwinięty, po rozwinięciu linie z numerami i kolorowaniem,
  `<` i `&` widoczne jako tekst, „Kopiuj” daje surowy tekst, wariant jednowierszowy z „Kopiuj”.
"""
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from widgets.stage import DEFAULT_PALETTE, copy_lib, load_palette
from .test_prose import QML_PROBLEMS, contrast, parse_color

LOGIC = """import QtQuick
import "tide" as Tide
import "tide/shell.js" as Shell

QtObject {
    readonly property var c: Shell.colors(Tide.Tokens)
    function t(line) { return Shell.tokens(line).map(p => [p.kind, p.text]) }
    Component.onCompleted: {
        const out = [%(calls)s]
        console.warn("RESULT " + JSON.stringify(out))
        Qt.quit()
    }
}
"""


def logic(calls: list[str]) -> list:
    with tempfile.TemporaryDirectory(prefix="tide-shell-") as tmp:
        copy_lib(os.path.join(tmp, "tide"), load_palette(DEFAULT_PALETTE))
        path = os.path.join(tmp, "Logic.qml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(LOGIC % {"calls": ",\n".join(calls)})
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", XDG_CONFIG_DIRS="/etc/xdg")
        result = subprocess.run(["qml6", path], env=env, capture_output=True, text=True, timeout=60)
    log = result.stderr
    problems = [line for line in log.splitlines() if re.search(QML_PROBLEMS, line)]
    match = re.search(r"RESULT (\[.*\])", log)
    if problems or not match:
        raise AssertionError("qml6 nie policzył shell.js:\n" + log)
    return json.loads(match.group(1))


def tokens(*lines) -> list:
    return logic([f"t({json.dumps(line)})" for line in lines])


TAG = re.compile(r"<(/?)([a-zA-Z0-9]+)([^>]*)>")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class TokenizerTableTest(unittest.TestCase):
    """Tabela tokenizera: wejście → kawałki (rodzaj, tekst)."""

    CASES = [
        ("komentarz", "# 1. Pobierz silnik", [["comment", "# 1. Pobierz silnik"]]),
        ("komentarz po poleceniu", "make test # pełny", [["command", "make"], ["text", " test "],
                                                         ["comment", "# pełny"]]),
        ("hash w słowie to nie komentarz", "echo a#b", [["keyword", "echo"], ["text", " a#b"]]),
        ("słowo kluczowe", "if [ -f x ]; then", [["keyword", "if"], ["text", " "], ["command", "["],
                                                  ["text", " -f x ]; "], ["keyword", "then"]]),
        ("export i przypisanie", 'export DOCKER_CONFIG="$(mktemp -d)"',
         [["keyword", "export"], ["text", " DOCKER_CONFIG="], ["string", '"$(mktemp -d)"']]),
        ("napis w apostrofach", "echo '{}' > cfg", [["keyword", "echo"], ["text", " "], ["string", "'{}'"],
                                                    ["text", " > cfg"]]),
        ("napis w apostrofach bez zmiennych", "echo '$HOME'", [["keyword", "echo"], ["text", " "],
                                                              ["string", "'$HOME'"]]),
        ("napis w cudzysłowie ze zmienną", 'echo "wynik: $OUT"',
         [["keyword", "echo"], ["text", " "], ["string", '"wynik: '], ["variable", "$OUT"], ["string", '"']]),
        ("zmienna $X", "cd $WORK", [["keyword", "cd"], ["text", " "], ["variable", "$WORK"]]),
        ("zmienna ${X}", "ls ${DIR}/a", [["command", "ls"], ["text", " "], ["variable", "${DIR}"], ["text", "/a"]]),
        ("zmienne specjalne", "shift $1 $@", [["keyword", "shift"], ["text", " "], ["variable", "$1"], ["text", " "],
                                              ["variable", "$@"]]),
        ("polecenie na początku linii", "docker compose build docs",
         [["command", "docker"], ["text", " compose build docs"]]),
        ("polecenie z wcięciem", "    curl -fsSL x", [["text", "    "], ["command", "curl"], ["text", " -fsSL x"]]),
        ("polecenie po potoku i średniku", "cat a | grep b; sha256sum c",
         [["command", "cat"], ["text", " a | "], ["command", "grep"], ["text", " b; "], ["command", "sha256sum"],
          ["text", " c"]]),
        ("polecenie w $(…)", "cid=$(docker create x)",
         [["text", "cid=$("], ["command", "docker"], ["text", " create x)"]]),
        ("przypisanie przed poleceniem", "LC_ALL=C make test", [["text", "LC_ALL=C "], ["command", "make"],
                                                               ["text", " test"]]),
        ("przekierowanie to nie separator", "make test 2>&1 | tee log",
         [["command", "make"], ["text", " test 2>&1 | "], ["command", "tee"], ["text", " log"]]),
        ("pusta linia", "", []),
    ]

    def test_table(self):
        found = tokens(*[line for _name, line, _expected in self.CASES])
        for (name, line, expected), got in zip(self.CASES, found):
            with self.subTest(name=name, line=line):
                self.assertEqual(got, expected)
                self.assertEqual("".join(text for _kind, text in got), line, "kawałki składają się w linię")

    def test_unclosed_string_runs_to_the_end_of_line(self):
        (got,) = tokens("echo \"niedomknięty $X i reszta")
        self.assertEqual(got, [["keyword", "echo"], ["text", " "], ["string", '"niedomknięty '], ["variable", "$X"],
                               ["string", " i reszta"]])


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class EscapeTest(unittest.TestCase):
    LINES = ['echo "<script>alert(1)</script>" && rm -rf "$X"', "cat <<'EOF' > a&b.txt",
             '<img src=x onerror=alert(1)> # <b>', "printf '%s' \"&amp;\""]

    def test_every_piece_is_escaped_and_only_spans_with_fixed_style(self):
        out = logic([f"Shell.html({json.dumps(line)}, c)" for line in self.LINES])
        for line, markup in zip(self.LINES, out):
            with self.subTest(line=line):
                for match in TAG.finditer(markup):
                    self.assertEqual(match.group(2), "span", markup)
                    attrs = match.group(3).strip()
                    if match.group(1) != "/":
                        self.assertRegex(attrs, r'^style="color:#[0-9a-f]{6}(;font-style:italic)?"$', markup)
                self.assertEqual(html.unescape(TAG.sub("", markup)), line, "widoczny tekst = linia skryptu")
                self.assertNotIn("<script", markup)
                self.assertNotIn("<img", markup)
        self.assertIn("&amp;amp;", out[3], "encja w skrypcie zostaje widocznym tekstem „&amp;”")

    def test_lines_and_labels(self):
        lines, crlf, trailing, empty, labels = logic([
            'Shell.lines("a\\nb\\n\\nc")', 'Shell.lines("a\\r\\nb")', 'Shell.lines("a\\n")', 'Shell.lines("")',
            "[1, 2, 5, 12, 22, 34].map(Shell.lineLabel)"])
        self.assertEqual(lines, ["a", "b", "", "c"])
        self.assertEqual(crlf, ["a", "b"])
        self.assertEqual(trailing, ["a"], "końcowy znak nowej linii nie daje pustej linii")
        self.assertEqual(empty, [])
        self.assertEqual(labels, ["1 linia", "2 linie", "5 linii", "12 linii", "22 linie", "34 linie"])


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class ContrastTest(unittest.TestCase):
    def test_script_colors_have_a_contrast_of_4_5_on_the_block(self):
        (pairs,) = logic(["Shell.pairs(c)"])
        self.assertEqual({p["name"] for p in pairs},
                         {"text", "comment", "keyword", "string", "variable", "command", "number", "head", "headMuted"})
        for pair in pairs:
            with self.subTest(kind=pair["name"]):
                self.assertGreaterEqual(contrast(parse_color(pair["fg"]), parse_color(pair["bg"])), 4.5, pair)

    def test_colors_come_from_the_palette(self):
        (colors,) = logic(["c"])
        palette = load_palette(DEFAULT_PALETTE)
        pastel = palette["pastel"]
        self.assertEqual(colors["keyword"], pastel["lavender"])
        self.assertEqual(colors["string"], pastel["butter"])
        self.assertEqual(colors["variable"], pastel["sky"])
        self.assertEqual(colors["command"], pastel["teal"])
        self.assertEqual(colors["comment"], palette["text"]["muted"])
        self.assertEqual(colors["background"], palette["background"]["view"])
        self.assertEqual(len({colors[k] for k in ("keyword", "string", "variable", "command", "comment", "text")}), 6)


# --- Tide.CodeBlock w oknie offscreen -------------------------------------------------------------------------

SCRIPT = "#!/usr/bin/env bash\n# pobierz <silnik> & sprawdź\nset -euo pipefail\ncurl -fsSL \"$URL\" > a&b.txt\n"

HARNESS_QML = """import QtQuick
import QtQuick.Window
import "tide" as Tide

Window {
    width: 900; height: 700; visible: true
    color: Tide.Tokens.backgroundWindow
    Column {
        width: parent.width - 40
        x: 20; y: 20
        spacing: 12
        Tide.CodeBlock {
            id: script
            objectName: "script"
            width: parent.width
            path: "scripts/tmp/reci38-run.sh"
            language: "bash"
            text: %(script)s
        }
        Tide.CodeBlock {
            objectName: "command"
            width: parent.width
            oneLine: true
            text: "bash scripts/tmp/reci38-run.sh"
        }
    }
}
""" % {"script": json.dumps(SCRIPT)}

HARNESS_PY = r"""
import json, sys
from PyQt6.QtCore import QObject, QTimer, QUrl, Qt, QPoint, QPointF
from PyQt6.QtGui import QGuiApplication, QTextDocument
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtQuick import QQuickItem  # noqa: F401 — rzutowanie elementów QML na QQuickItem (childItems, mapToScene)
from PyQt6.QtTest import QTest

app = QGuiApplication(sys.argv)
engine = QQmlApplicationEngine()
engine.load(QUrl.fromLocalFile(sys.argv[1]))
window = engine.rootObjects()[0]
out = {}


def item(name):
    return window.findChild(QObject, name)


def shown(root, name):
    found = []

    def walk(it):
        if it.objectName() == name and it.property("visible"):
            found.append(it)
        for child in it.childItems() if hasattr(it, "childItems") else []:
            walk(child)
    walk(root)
    return found


def plain(markup):
    doc = QTextDocument()
    doc.setHtml(markup)
    return doc.toPlainText()


def click(it):
    point = it.mapToScene(QPointF(it.width() / 2, it.height() / 2))
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(int(point.x()), int(point.y())))
    QTest.qWait(80)


def state(block):
    lines = shown(block, "codeBlockLine")
    return {"lines": [plain(line.findChild(QObject, "codeBlockCode").property("text")) for line in lines],
            "html": [line.findChild(QObject, "codeBlockCode").property("text") for line in lines],
            "numbers": [line.findChild(QObject, "codeBlockNumber").property("text") for line in lines],
            "toggle": [b.property("text") for b in shown(block, "codeBlockToggle")],
            "copy": [b.property("text") for b in shown(block, "codeBlockCopy")],
            "summary": [t.property("text") for t in shown(block, "codeBlockSummary")],
            "path": [t.property("text") for t in shown(block, "codeBlockPath")],
            "height": block.property("height")}


def run():
    try:
        script, command = item("script"), item("command")
        out["closed"] = state(script)
        click(shown(script, "codeBlockToggle")[0])
        out["open"] = state(script)
        click(shown(script, "codeBlockCopy")[0])
        out["clip"] = QGuiApplication.clipboard().text()
        out["copied"] = state(script)["copy"]
        click(shown(script, "codeBlockToggle")[0])
        out["again"] = state(script)
        out["command"] = {"text": [t.property("text") for t in shown(command, "codeBlockText")],
                          "copy": [b.property("text") for b in shown(command, "codeBlockCopy")],
                          "toggle": len(shown(command, "codeBlockToggle")),
                          "lines": len(shown(command, "codeBlockLine"))}
        click(shown(command, "codeBlockCopy")[0])
        out["commandClip"] = QGuiApplication.clipboard().text()
    except Exception as exc:
        out["error"] = repr(exc)
    print(json.dumps(out, ensure_ascii=False, default=str))
    app.quit()


QTimer.singleShot(300, run)
app.exec()
"""


@unittest.skipUnless(os.path.exists("/usr/bin/python3"), "brak /usr/bin/python3")
class CodeBlockComponentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="tide-codeblock-") as tmp:
            copy_lib(os.path.join(tmp, "tide"), load_palette(DEFAULT_PALETTE))
            qml, script = os.path.join(tmp, "Harness.qml"), os.path.join(tmp, "harness.py")
            with open(qml, "w", encoding="utf-8") as fh:
                fh.write(HARNESS_QML)
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(HARNESS_PY)
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
            result = subprocess.run(["/usr/bin/python3", script, qml], env=env, capture_output=True, text=True,
                                    timeout=90)
        if result.returncode != 0 or not result.stdout.strip():
            raise AssertionError(result.stderr[-3000:])
        cls.log = result.stderr
        cls.out = json.loads(result.stdout.strip().splitlines()[-1])

    def test_loads_without_qml_problems(self):
        self.assertNotIn("error", self.out, self.out)
        self.assertIsNone(re.search(QML_PROBLEMS, self.log), self.log[-3000:])

    def test_collapsed_by_default_with_path_line_count_and_language(self):
        closed = self.out["closed"]
        self.assertEqual(closed["lines"], [], "domyślnie zwinięty — bez linii")
        self.assertEqual(closed["path"], ["scripts/tmp/reci38-run.sh"])
        self.assertEqual(closed["summary"], ["· 4 linie · bash"])
        self.assertEqual(closed["toggle"], ["pokaż treść skryptu"])
        self.assertEqual(closed["copy"], ["Kopiuj"])

    def test_expanded_lines_have_numbers_colors_and_visible_markup_characters(self):
        shown = self.out["open"]
        self.assertEqual(shown["toggle"], ["zwiń"])
        self.assertEqual(shown["numbers"], ["1", "2", "3", "4"])
        self.assertEqual(shown["lines"], SCRIPT.rstrip("\n").split("\n"), "„<” i „&” widoczne jako tekst")
        self.assertIn("&lt;silnik&gt; &amp;", shown["html"][1])
        palette = load_palette(DEFAULT_PALETTE)
        self.assertIn(f"color:{palette['text']['muted']};font-style:italic", shown["html"][1], "komentarz")
        self.assertIn(f"color:{palette['pastel']['lavender']}", shown["html"][2], "słowo kluczowe `set`")
        self.assertIn(f"color:{palette['pastel']['teal']}", shown["html"][3], "polecenie `curl`")
        self.assertIn(f"color:{palette['pastel']['butter']}", shown["html"][3], "napis")
        self.assertIn(f"color:{palette['pastel']['sky']}", shown["html"][3], "zmienna $URL")
        self.assertGreater(shown["height"], self.out["closed"]["height"])
        self.assertEqual(self.out["again"]["lines"], [], "ponowne kliknięcie zwija")

    def test_copy_gives_the_raw_script(self):
        self.assertEqual(self.out["clip"], SCRIPT)
        self.assertEqual(self.out["copied"], ["Skopiowano"])

    def test_one_line_variant_is_a_command_field_with_copy(self):
        command = self.out["command"]
        self.assertEqual(command["text"], ["bash scripts/tmp/reci38-run.sh"])
        self.assertEqual(command["copy"], ["Kopiuj"])
        self.assertEqual((command["toggle"], command["lines"]), (0, 0))
        self.assertEqual(self.out["commandClip"], "bash scripts/tmp/reci38-run.sh")


if __name__ == "__main__":
    unittest.main()
