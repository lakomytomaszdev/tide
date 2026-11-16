"""Standard tekstu Tide (K-128, PT-501): widgets/lib/prose.js i Tide.Prose.

- prose.js liczy się w qml6 offscreen tym samym kodem, który rysuje Tide.Prose (bez Plasmy): escapowanie przed
  wyróżnieniami (tabela wstrzyknięć, także w trybie Markdown), wyróżnienia ścieżek, poleceń, adresów, klawiszy, kart
  i ticketów, lista ze średników, tryb Markdown, kontrast kolorów wyróżnień na tłach palety (jak test_diff_highlight),
- Tide.Prose w prawdziwym oknie offscreen (PyQt6): renderer gospodarza albo prose.js, kopiowanie jako czysty tekst,
  renderowanie tylko przy zmianie tekstu, zwinięcie z „więcej”,
- test statyczny: RichText tylko w MarkdownBlock.qml Tablicy i w Tide.Prose.
"""
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from . import REPO, WIDGETS_DIR
from widgets.stage import DEFAULT_PALETTE, copy_lib, load_palette

QML_PROBLEMS = (r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|Cannot assign"
                r"|Required property|is not defined|Invalid property|Binding loop|Cannot read|SyntaxError")
ALLOWED_TAGS = {"p", "br", "ul", "ol", "li", "code", "kbd", "span", "strong", "em", "pre", "blockquote",
                "h1", "h2", "h3", "h4", "h5", "h6", "hr"}
TAG = re.compile(r"<(/?)([a-zA-Z0-9]+)([^>]*)>")

LOGIC = """import QtQuick
import "tide" as Tide
import "tide/prose.js" as Prose

QtObject {
    function r(text, markdown) {
        return Prose.render(text, { markdown: !!markdown, colors: Prose.colors(Tide.Tokens, Tide.Tokens.backgroundCard,
                                                                                 "Geist Mono") })
    }
    Component.onCompleted: {
        const out = [%(calls)s]
        console.warn("RESULT " + JSON.stringify(out))
        Qt.quit()
    }
}
"""


def run_qml(qml: str) -> str:
    with tempfile.TemporaryDirectory(prefix="tide-prose-") as tmp:
        copy_lib(os.path.join(tmp, "tide"), load_palette(DEFAULT_PALETTE))
        path = os.path.join(tmp, "Logic.qml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(qml)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", XDG_CONFIG_DIRS="/etc/xdg")
        result = subprocess.run(["qml6", path], env=env, capture_output=True, text=True, timeout=60)
    return result.stderr


def logic(calls: list[str]) -> list:
    log = run_qml(LOGIC % {"calls": ",\n".join(calls)})
    problems = [line for line in log.splitlines() if re.search(QML_PROBLEMS, line)]
    match = re.search(r"RESULT (\[.*\])", log)
    if problems or not match:
        raise AssertionError("qml6 nie policzył prose.js:\n" + log)
    return json.loads(match.group(1))


def render(*texts, markdown=False) -> list[str]:
    return logic([f"r({json.dumps(t, ensure_ascii=False)}, {str(markdown).lower()})" for t in texts])


def tags(markup: str) -> list[tuple[str, str]]:
    return [(m.group(2).lower(), m.group(3)) for m in TAG.finditer(markup)]


def visible(markup: str) -> str:
    """Tekst, który zobaczy użytkownik: bez znaczników, z encjami zamienionymi na znaki."""
    return html.unescape(TAG.sub("", markup))


def parse_color(value: str):
    digits = value.lstrip("#")
    return tuple(int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4))


def luminance(color):
    def channel(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = color[:3]
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def styled(markup: str, cls: str, text: str) -> bool:
    return re.search(rf'class="{cls}"[^>]*>{re.escape(text)}</', markup) is not None


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class EscapeTest(unittest.TestCase):
    """Tekst agenta nigdy nie wstrzykuje HTML ani linków: wszystko z wejścia wychodzi jako widoczny tekst."""

    CASES = [
        ("<script>alert(1)</script>", False),
        ('<a href="javascript:alert(1)">kliknij</a>', False),
        ("<img src=http://127.0.0.1:9/x.png onerror=alert(1)>", False),
        ('Cudzysłów " i apostrof \' oraz &amp; i &lt;b&gt;', False),
        ("<b>pogrubione</b> <style>p{}</style> <iframe src=x>", False),
        ("[x](javascript:alert(1))", True),
        ("[x](http://example.com/a)", True),
        ('<a href="http://example.com">**tak**</a>', True),
        ("```\n<script>alert(1)</script>\n```", True),
        ("> <img src=x>\n- <a href=y>z</a>", True),
    ]

    def test_markup_from_input_comes_out_as_visible_text_without_href(self):
        outputs = logic([f"r({json.dumps(t)}, {str(md).lower()})" for t, md in self.CASES])
        for (text, markdown), out in zip(self.CASES, outputs):
            with self.subTest(text=text, markdown=markdown):
                for name, attrs in tags(out):
                    self.assertIn(name, ALLOWED_TAGS, out)
                    self.assertNotIn("href", attrs, out)
                    self.assertNotIn("src", attrs, out)
                    self.assertIsNone(re.search(r"\bon\w+=", attrs), out)
                    self.assertEqual(re.sub(r'\s*(class|style)="[^"]*"', "", attrs).strip(), "", out)
                self.assertNotIn("javascript:", "".join(a for _n, a in tags(out)))
                self.assertNotIn("tide-card:", out)

        script, link, image, quotes, other = outputs[:5]
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", script)
        self.assertEqual(visible(script), "<script>alert(1)</script>")
        self.assertEqual(visible(link), '<a href="javascript:alert(1)">kliknij</a>')
        self.assertEqual(visible(image), "<img src=http://127.0.0.1:9/x.png onerror=alert(1)>")
        self.assertIn("&quot;", quotes)
        self.assertIn("&#39;", quotes)
        self.assertIn("&amp;amp;", quotes, "encja z tekstu zostaje widocznym tekstem „&amp;”")
        self.assertEqual(visible(quotes), 'Cudzysłów " i apostrof \' oraz &amp; i &lt;b&gt;')
        self.assertEqual(visible(other), "<b>pogrubione</b> <style>p{}</style> <iframe src=x>")

    def test_markdown_links_are_text_with_address_and_never_links(self):
        bad, good, html_link = render("[x](javascript:alert(1))", "[x](http://example.com/a)",
                                      '<a href="http://example.com">**tak**</a>', markdown=True)
        self.assertNotIn("<a", bad + good + html_link)
        self.assertEqual(visible(bad).strip(), "x", "link o innym schemacie zostaje samym tekstem")
        self.assertEqual(visible(good), "x (http://example.com/a)")
        self.assertTrue(styled(good, "url", "http://example.com/a"), good)
        self.assertIn("&lt;a href=&quot;", html_link)


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class HighlightTest(unittest.TestCase):
    def test_each_kind_gets_its_own_style(self):
        (out,) = render("Zmień apps/board/qml/CardPanel.qml i ~/.docker/config.json, uruchom `make test-changed`, "
                        "plik `docs/design-system.md`, adres cdn.jsdelivr.net i https://example.com/a?b=1&c=2. "
                        "Naciśnij Ctrl+Enter albo Esc. Karta K-104 i ticket PT-431.")
        self.assertTrue(styled(out, "p", "apps/board/qml/CardPanel.qml"), out)
        self.assertTrue(styled(out, "p", "~/.docker/config.json"), out)
        self.assertTrue(styled(out, "cmd", "make test-changed"), out)
        self.assertTrue(styled(out, "p", "docs/design-system.md"), "ścieżka w backtickach — sky, nie masło")
        self.assertTrue(styled(out, "url", "cdn.jsdelivr.net"), out)
        self.assertTrue(styled(out, "url", "https://example.com/a?b=1&amp;c=2"), "kropka zdania poza adresem")
        self.assertEqual(re.findall(r"<kbd[^>]*>([^<]+)</kbd>", out), ["Ctrl", "Enter", "Esc"])
        self.assertIn("</kbd>+<kbd", out)
        self.assertTrue(styled(out, "cref", "K-104"), out)
        self.assertTrue(styled(out, "tref", "PT-431"), out)
        self.assertEqual(out.count("<p "), 1)
        # Każdy rodzaj ma inny wygląd, kolory z palety (sky, masło, lawenda).
        palette = load_palette(DEFAULT_PALETTE)["pastel"]
        style = {cls: re.search(rf'class="{cls}" style="([^"]*)"', out).group(1) for cls in ("p", "cmd", "url", "cref", "tref")}
        style["kbd"] = re.search(r'<kbd style="([^"]*)"', out).group(1)
        self.assertEqual(len(set(style.values())), 6, style)
        self.assertIn(f"color:{palette['sky']}", style["p"])
        self.assertIn(f"color:{palette['butter']}", style["cmd"])
        self.assertIn(f"color:{palette['lavender']}", style["url"])
        self.assertIn(f"color:{palette['sky']}", style["cref"])
        self.assertIn("font-weight:600", style["cref"])
        self.assertIn(f"color:{palette['sky']}", style["tref"])
        self.assertIn("Geist Mono", style["tref"])

    def test_plain_text_is_one_paragraph(self):
        (out,) = render("Zwykłe zdanie bez żadnych wyróżnień, tylko przecinki i kropka.")
        self.assertEqual([t for t, _a in tags(out)], ["p", "p"])
        self.assertEqual(visible(out), "Zwykłe zdanie bez żadnych wyróżnień, tylko przecinki i kropka.")
        self.assertIn("line-height:120%", out)   # ok. 1,6 em w Qt (prose.js, LINE_HEIGHT)

    def test_semicolons_after_colon_become_a_list_only_from_three_parts(self):
        three, two, tail = render("Krok robi trzy rzeczy: pobiera silnik; buduje portal; sprawdza stronę.",
                                  "Dwie rzeczy: pobiera; buduje.",
                                  "Uwaga: sieć; Docker; zapis do /tmp. Tide uruchomi je raz.")
        self.assertEqual(re.findall(r"<li[^>]*>(.*?)</li>", three), ["pobiera silnik", "buduje portal", "sprawdza stronę"])
        self.assertEqual(visible(re.search(r"<p[^>]*>.*?</p>", three).group(0)), "Krok robi trzy rzeczy:")
        self.assertNotIn("<ul", two)
        self.assertEqual(visible(two), "Dwie rzeczy: pobiera; buduje.")
        self.assertEqual(len(re.findall("<li", tail)), 3)
        self.assertTrue(visible(tail).endswith("Tide uruchomi je raz."), "zdanie po liście zostaje akapitem")

    def test_lines_stay_lines_and_lookalikes_stay_text(self):
        lines, lookalikes = render("Uwaga: ścieżka spoza worktree.\nPowód kroku: testy.",
                                   "tak/nie, UTF-8, &lt;/b&gt;, 3/4 i Escape")
        self.assertEqual(lines.count("<p "), 1)
        self.assertIn("worktree.<br>Powód kroku", lines)
        self.assertEqual([t for t, _a in tags(lookalikes)], ["p", "p"], lookalikes)

    def test_blocks_render_in_both_modes(self):
        for markdown in (False, True):
            (out,) = render("Lista:\n\n- jeden\n- dwa\n\n1. pierwszy\n2. drugi\n\n> cytat agenta\n\n```\nmake <b>\n```",
                            markdown=markdown)
            with self.subTest(markdown=markdown):
                self.assertEqual(re.findall(r"<li[^>]*>(.*?)</li>", out), ["jeden", "dwa", "pierwszy", "drugi"])
                self.assertIn("<ul", out)
                self.assertIn("<ol", out)
                self.assertRegex(out, r"<blockquote[^>]*><p[^>]*>cytat agenta</p></blockquote>")
                self.assertRegex(out, r"<pre[^>]*>make &lt;b&gt;</pre>")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class MarkdownTest(unittest.TestCase):
    def test_headings_emphasis_and_rule(self):
        md, plain = logic([
            'r("# Tytuł\\n\\n## Sedno\\n\\n###### Mały\\n\\nTo **ważne** i *pochylone*.\\n\\n---", true)',
            'r("## Sedno", false)'])
        self.assertRegex(md, r"<h1[^>]*>Tytuł</h1>")
        self.assertRegex(md, r"<h2[^>]*>Sedno</h2>")
        self.assertRegex(md, r"<h6[^>]*>Mały</h6>")
        self.assertNotIn("## ", visible(md))
        self.assertNotIn("#", visible(md))
        self.assertRegex(md, r"<strong[^>]*>ważne</strong>")
        self.assertIn("<em>pochylone</em>", md)
        self.assertIn("<hr>", md)
        self.assertEqual(visible(plain), "## Sedno", "bez trybu Markdown krzyżyki zostają tekstem")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class ContrastTest(unittest.TestCase):
    BACKGROUNDS = ("backgroundView", "backgroundWindow", "backgroundCard", "backgroundTitlebar", "backgroundPanel")

    def test_highlight_colors_have_a_contrast_of_4_5_on_their_backgrounds(self):
        pairs = logic([f"Prose.pairs(Prose.colors(Tide.Tokens, Tide.Tokens.{bg}, 'm'))" for bg in self.BACKGROUNDS])
        for bg, found in zip(self.BACKGROUNDS, pairs):
            self.assertEqual({p["name"] for p in found},
                             {"path", "command", "url", "key", "card", "ticket", "quote", "pre", "text", "strong"})
            for pair in found:
                with self.subTest(background=bg, kind=pair["name"]):
                    self.assertGreaterEqual(contrast(parse_color(pair["fg"]), parse_color(pair["bg"])), 4.5, pair)

    def test_contrast_helper_matches_wcag_values(self):
        self.assertAlmostEqual(contrast((0, 0, 0), (1, 1, 1)), 21.0, places=3)
        self.assertAlmostEqual(contrast((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)), 1.0, places=3)


# --- Tide.Prose w oknie offscreen -----------------------------------------------------------------------------

HARNESS_QML = """import QtQuick
import QtQuick.Window
import "tide" as Tide

Window {
    id: root
    width: 900; height: 700; visible: true
    property var hostCalls: []
    QtObject {
        id: host
        function renderMarkdown(markdown, color) {
            root.hostCalls = root.hostCalls.concat([markdown])
            return "<p>HOST:" + markdown.length + "</p>"
        }
    }
    Column {
        width: 700
        Tide.Prose { id: plainProse; objectName: "plain"; width: parent.width
            text: "Zmień apps/board/qml/CardPanel.qml i uruchom `make test-changed`; potem Ctrl+Enter.\\n\\n- K-104\\n- PT-431" }
        Tide.Prose { objectName: "host"; width: parent.width; markdown: true; renderer: host.renderMarkdown
            text: "## Sedno" }
        Tide.Prose { objectName: "fallback"; width: parent.width; markdown: true; text: "## Sedno\\n\\n- jeden" }
        Tide.Prose { objectName: "noMarkdown"; width: parent.width; renderer: host.renderMarkdown; text: "## Sedno" }
        Tide.Prose { objectName: "collapsed"; width: 300; collapsedLines: 2
            text: "Pierwsza linia tekstu.\\nDruga linia tekstu.\\nTrzecia linia tekstu.\\nCzwarta linia tekstu." }
        Tide.Prose { objectName: "wide"; width: parent.width; text: "x" }
    }
}
"""

HARNESS_PY = r"""
import json, sys
from PyQt6.QtCore import QObject, Qt, QTimer, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine, QQmlExpression
from PyQt6.QtTest import QTest

app = QGuiApplication([])
engine = QQmlApplicationEngine()
engine.load(QUrl.fromLocalFile(sys.argv[1]))
window = engine.rootObjects()[0]
out = {}


def item(name):
    return window.findChild(QObject, name)


def js(expression):
    value, undefined = QQmlExpression(engine.contextForObject(window), window, expression).evaluate()
    return None if undefined else (value.toVariant() if hasattr(value, "toVariant") else value)


def run():
    try:
        plain, host, fallback, collapsed = item("plain"), item("host"), item("fallback"), item("collapsed")
        out["plainHtml"] = plain.property("html")
        out["hostHtml"] = host.property("html")
        out["hostCalls"] = window.property("hostCalls").toVariant()
        out["fallbackHtml"] = fallback.property("html")
        out["noMarkdownHtml"] = item("noMarkdown").property("html")
        out["renders"] = plain.property("renders")
        plain.setProperty("width", 650)
        plain.setProperty("text", plain.property("text"))
        QTest.qWait(50)
        out["rendersSame"] = plain.property("renders")
        plain.setProperty("text", "Nowy tekst K-1.")
        QTest.qWait(50)
        out["rendersChanged"] = plain.property("renders")
        plain.setProperty("text", "Zmień apps/board/qml/CardPanel.qml i uruchom `make test-changed`.\n\n- K-104\n- PT-431")
        QTest.qWait(50)
        # Zaznaczenie wszystkiego i Ctrl+C z klawiatury.
        out["selected"] = js("(function () { plainProse.edit.forceActiveFocus(); plainProse.edit.selectAll(); "
                             "return plainProse.plainSelection() })()")
        QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(50)
        mime = QGuiApplication.clipboard().mimeData()
        out["clipText"] = mime.text()
        out["clipHtml"] = mime.html() if mime.hasHtml() else ""
        out["collapsible"] = collapsed.property("collapsible")
        out["collapsedHeight"] = collapsed.property("height")
        more = collapsed.findChild(QObject, "proseMore")
        out["more"] = [more.property("visible"), more.property("text")]
        collapsed.setProperty("expanded", True)
        QTest.qWait(50)
        out["expandedHeight"] = collapsed.property("height")
        wide = item("wide")
        out["maxWidth"] = wide.property("maxWidth")
    except Exception as exc:
        out["error"] = repr(exc)
    print(json.dumps(out, ensure_ascii=False, default=str))
    app.quit()


QTimer.singleShot(300, run)
app.exec()
"""


@unittest.skipUnless(os.path.exists("/usr/bin/python3"), "brak /usr/bin/python3")
class ProseComponentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="tide-prose-item-") as tmp:
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

    def test_uses_host_renderer_in_markdown_mode_and_prose_js_without_it(self):
        self.assertEqual(self.out["hostHtml"].count("HOST:8"), 1, self.out["hostHtml"])
        self.assertEqual(self.out["hostCalls"], ["## Sedno"])
        self.assertRegex(self.out["fallbackHtml"], r"<h2[^>]*>.*Sedno")
        self.assertNotIn("HOST", self.out["fallbackHtml"])
        self.assertNotIn("HOST", self.out["noMarkdownHtml"], "renderer gospodarza tylko w trybie Markdown")
        self.assertIn("## Sedno", self.out["noMarkdownHtml"])

    def test_highlights_reach_the_text_document(self):
        doc = self.out["plainHtml"]   # HTML dokumentu Qt po wczytaniu wyniku prose.js
        sky = load_palette(DEFAULT_PALETTE)["pastel"]["sky"]
        self.assertRegex(doc, rf'<span style="[^"]*color:{sky};[^"]*">apps/board/qml/CardPanel.qml</span>')
        self.assertNotIn("href", doc)

    def test_copy_gives_plain_text_without_markup(self):
        expected = "Zmień apps/board/qml/CardPanel.qml i uruchom make test-changed.\nK-104\nPT-431"
        self.assertEqual(self.out["selected"], expected)
        self.assertEqual(self.out["clipText"], expected)
        self.assertNotIn("<", self.out["clipText"])
        self.assertNotIn("<code", self.out["clipHtml"])
        self.assertNotIn(load_palette(DEFAULT_PALETTE)["pastel"]["sky"], self.out["clipHtml"])

    def test_renders_only_when_the_text_changes(self):
        self.assertEqual(self.out["renders"], 1)
        self.assertEqual(self.out["rendersSame"], 1, "ta sama treść i inna szerokość bez ponownego renderowania")
        self.assertEqual(self.out["rendersChanged"], 2)

    def test_collapsed_lines_with_more(self):
        self.assertTrue(self.out["collapsible"])
        self.assertEqual(self.out["more"], [True, "więcej"])
        self.assertLess(self.out["collapsedHeight"], self.out["expandedHeight"])

    def test_paragraph_width_is_about_80_characters(self):
        self.assertGreater(self.out["maxWidth"], 300)
        self.assertLess(self.out["maxWidth"], 900)


class RichTextBoundaryTest(unittest.TestCase):
    """RichText tylko w Tide.Prose i w MarkdownBlock.qml Tablicy (HTML z backend.renderMarkdown)."""

    ALLOWED = {"widgets/lib/Prose.qml", "apps/board/qml/MarkdownBlock.qml"}
    # Wyjątek sprzed K-128: raport sprawy w oknie Sprawy rysuje HTML z backend.renderReport (z obrazami sprawy).
    # Przegląd pozostałych miejsc z tekstem na Tide.Prose to etap A2 karty K-128 (plan, „Odłożone jawnie”).
    LEGACY = {"apps/issues/qml/IssuesView.qml"}

    def test_rich_text_only_in_prose_and_markdown_block(self):
        found = set()
        for base in ("apps", "widgets", "theme", "tray", "krunner", "kwin"):
            for dirpath, dirs, names in os.walk(os.path.join(REPO, base)):
                dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", "build")]
                for name in names:
                    if name.endswith((".qml", ".js")):
                        path = os.path.join(dirpath, name)
                        with open(path, encoding="utf-8") as fh:
                            if "RichText" in fh.read():
                                found.add(os.path.relpath(path, REPO))
        self.assertEqual(found - self.LEGACY, self.ALLOWED)
        self.assertTrue(self.LEGACY <= found, "wyjątek bez RichText — usuń go z listy")

    def test_decision_form_shows_why_through_prose(self):
        with open(os.path.join(REPO, "apps/common/qml/card/DecisionForm.qml"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertRegex(source, r'Tide\.Prose \{\n\s*objectName: "decisionWhy"')
        with open(os.path.join(WIDGETS_DIR, "lib", "qmldir"), encoding="utf-8") as fh:
            self.assertIn("Prose 1.0 Prose.qml", fh.read())


if __name__ == "__main__":
    unittest.main()
