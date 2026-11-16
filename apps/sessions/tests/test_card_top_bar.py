"""Stały pasek górny pełnej karty w Sesjach (K-128, PT-502): ten sam CardFullView co na Tablicy.

- powrót jest pierwszym elementem paska od lewej i ma tekst „← Sesje”; klik i Esc wracają do listy Sesji,
- zakładka Opis pokazuje `## Sedno` jako nagłówek bez dosłownego `## ` — Sesje nie mają renderera Markdown
  (`backend.renderMarkdown`), więc opis renderuje prose.js w trybie Markdown (PT-501).

Test offscreen: realne okno z SessionsView, klik myszą (QTest), dane z fixture’ów Tablicy (scenariusz D).
"""
import json
import os
import shutil
import tempfile
import unittest

from . import test_card_open as card_open   # moduł, nie klasa: bez ponownego uruchamiania jej testów
from .test_window import stage
from apps.board.tests.boardfix import preview_data
from apps.board.tests.decisionfix import CARD, full_d

DESCRIPTION = "## Sedno\n\nKarta ma **jeden** pasek górny.\n\n- powrót po lewej\n- akcje po prawej"

HELPERS = r"""
ALL = ("(function all(it, name, out) { out = out || []; if (!it) return out; "
       "if (it.objectName === name && it.visible) out.push(it); "
       "for (const c of it.children) all(c, name, out); return out })")

def items(name):
    return "(" + ALL + ")(cardFullView, '" + name + "')"

def box(name):
    return json.loads(js("(function () { const it = " + items(name) + "[0]; if (!it) return 'null'; "
                         "const p = it.mapToItem(null, 0, 0); "
                         "return JSON.stringify([Math.round(p.x), Math.round(p.y), it.width, it.height]) })()"))

def click_item(expression):
    pos = json.loads(js("(function () { const it = " + expression + "; const p = it.mapToItem(null, it.width / 2, "
                        "it.height / 2); return JSON.stringify([Math.round(p.x), Math.round(p.y)]) })()"))
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(*pos))
    QTest.qWait(200)
"""


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class SessionsTopBarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-sess-topbar-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))
        snapshot = full_d(preview_data(os.path.join(cls.tmp, "board")))
        card = next(c for c in snapshot["cards"] if c["id"] == CARD)
        card["description"] = DESCRIPTION
        cls.working = os.path.join(cls.tmp, "working.json")
        with open(cls.working, "w", encoding="utf-8") as fh:
            json.dump({"boardSnapshot": snapshot, "now": 1793700000}, fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def probe(self, body: str) -> dict:
        return card_open.CardOpenTest.probe(self, self.working, HELPERS + body)

    def test_back_is_first_from_the_left_and_returns_to_sessions(self):
        out = self.probe("""
click_entry(0)
result['open'] = state()['fullOpen']
result['back'] = [js(items('fullBack') + '[0].text'), box('fullBack')]
result['others'] = {n: box(n) for n in ('fullCrumb', 'fullNumber', 'fullHeading', 'fullMenu')}
click_item(items('fullBack') + '[0]')
result['afterClick'] = state()['fullOpen']
click_entry(0)
press_escape()
result['afterEsc'] = state()['fullOpen']
result['calls'] = calls()
""")
        self.assertTrue(out["open"])
        text, back = out["back"]
        self.assertEqual(text, "← Sesje")
        for name, other in out["others"].items():
            self.assertIsNotNone(other, name)
            self.assertGreater(other[0], back[0] + back[2] - 1, f"{name} leży na prawo od powrotu")
        self.assertFalse(out["afterClick"], "klik w powrót wraca do listy Sesji")
        self.assertFalse(out["afterEsc"], "Esc wraca do listy Sesji")
        self.assertEqual(out["calls"], [], "powrót niczego nie wysyła")

    def test_description_tab_renders_markdown_without_a_host_renderer(self):
        out = self.probe(r"""
click_entry(0)
click_item(items('fullTab') + ".find(t => t.tabId === 'description')")
prose = items('fullDescriptionText') + '[0]'
result['renderer'] = js('cardFullView.markdownRenderer === null && ' + prose + '.renderer === null')
SEP = ".replace(new RegExp('[\\u2028\\u2029]', 'g'), '\\n')"
result['plain'] = js(prose + '.edit.getText(0, ' + prose + '.edit.length)' + SEP)
result['html'] = js(prose + '.html' + SEP)
result['tab'] = js('cardFullView.tab')
""")
        self.assertTrue(out["renderer"], "Sesje nie mają renderera — działa prose.js")
        self.assertEqual(out["tab"], "description")
        self.assertIn("Sedno", out["plain"])
        self.assertNotIn("## ", out["plain"], "bez dosłownego „## ”")
        self.assertRegex(out["html"], r"<h2[ >][^<]*(<[^>]*>)*[^<]*Sedno", "„Sedno” jako nagłówek")


if __name__ == "__main__":
    unittest.main()
