"""Standard tekstu w decyzji karty (K-128, PT-501): „Uwaga” i „Powód kroku” przez Tide.Prose.

Prośba o zgodę na polecenie poza piaskownicą z dziennika karty (decisionfix.journal_outside, decyzje liczone przez
usługę) z ryzykiem (ścieżka spoza worktree → linia „Uwaga:”) i powodem kroku z poleceniem w backtickach, adresem,
klawiszem i numerami karty i ticketu. Sonda offscreen sprawdza wyróżnienia w oknie decyzji i w „Twój ruch” pełnej
karty. Z `TIDE_PROSE_SHOTS=<katalog>` zapisuje też zrzuty PNG (treść 1180×1000 i najwęższe okno 1180×620 —
okno Tide 1380×620 bez panelu bocznego 200 px); tylko offscreen, bez okien na pulpicie.
"""
import os
import re
import shutil
import unittest

from .decisionfix import full_card, journal_outside
from .test_view import ProbeCase

COMMAND = "cat ~/.docker/config.json"
REASON = ("Testy ticketu PT-3 z karty K-126: `docker compose build docs` pobiera silnik z cdn.jsdelivr.net "
          "do docs/site/templates/assets/vendor/ (opis: https://www.jsdelivr.com/), Ctrl+C przerywa.")

FIND = ("(function find(it, name, out) { out = out || []; if (!it) return out; "
        "if (it.objectName === name && it.visible) out.push(it); "
        "for (const c of it.children) find(c, name, out); return out })")

SCENARIO = """
import os
from PyQt6 import sip
from PyQt6.QtQuick import QQuickWindow
shots = os.environ.get("TIDE_PROSE_SHOTS", "")

def why(root):
    return json.loads(js("(function () { const w = (" + FIND + ")(" + root + ", 'decisionWhy')[0]; "
                         "return JSON.stringify(w ? { text: w.text, html: w.html, renders: w.renders, "
                         "width: w.width, maxWidth: w.maxWidth } : null) })()"))

def grab(name):
    if not shots:
        return
    os.makedirs(shots, exist_ok=True)
    for size, (w, h) in (("wide", (1180, 1000)), ("narrow", (1180, 620))):
        window.setWidth(w)
        window.setHeight(h)
        wait(400)
        quick = sip.cast(window, QQuickWindow)
        quick.grabWindow().save(os.path.join(shots, name + "-" + size + ".png"))
    window.setWidth(1440)
    window.setHeight(900)
    wait(200)

click(card("K-8"))
until(lambda: js("page.decisionOpen || page.fullOpen"))
wait(200)
out["dialog"] = [js("page.decisionOpen"), js("page.fullOpen")]
out["why"] = why("ROOT")
grab(NAME)
""".replace("FIND", repr(FIND))


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class ProseInDecisionTest(ProbeCase):
    def data_for(self, full: bool) -> dict:
        data = full_card(self.data, journal_outside([("PT-3", COMMAND, {"reason": REASON, "pattern": ""})]))
        if full:
            data["cardOpen"] = "full"
        return data

    def check(self, why: dict):
        from widgets.stage import DEFAULT_PALETTE, load_palette
        pastel = load_palette(DEFAULT_PALETTE)["pastel"]
        self.assertIsNotNone(why)
        lines = why["text"].split("\n")
        self.assertTrue(lines[0].startswith("Uwaga: ścieżka spoza worktree: ~/.docker/config.json"), lines)
        self.assertEqual(lines[-1], "Powód kroku: " + REASON)
        doc = why["html"]

        def span(color, text):
            return re.search(rf'<span style="[^"]*color:{color};[^"]*">{re.escape(text)}</span>', doc) is not None

        self.assertTrue(span(pastel["sky"], "~/.docker/config.json"), "ścieżka w „Uwaga” — sky")
        self.assertTrue(span(pastel["butter"], "docker compose build docs"), "polecenie — masło")
        self.assertTrue(span(pastel["sky"], "docs/site/templates/assets/vendor/"), "ścieżka w „Powód kroku”")
        self.assertTrue(span(pastel["lavender"], "cdn.jsdelivr.net"), "domena — lawenda")
        self.assertTrue(span(pastel["lavender"], "https://www.jsdelivr.com/"), "adres — lawenda")
        self.assertTrue(span(pastel["sky"], "K-126"), "numer karty")
        self.assertTrue(span(pastel["sky"], "PT-3"), "numer ticketu")
        key = re.findall(r'<span style="([^"]*)">Ctrl</span><span[^>]*>\+</span><span style="([^"]*)">C</span>', doc)
        self.assertEqual(len(key), 1, "klawisz Ctrl+C jako dwa klawisze")
        self.assertEqual(key[0][0], key[0][1])
        self.assertIn("background-color", key[0][0])
        self.assertNotIn("href", doc)
        self.assertNotIn("<img", doc)
        self.assertEqual(why["renders"], 1, "jedno renderowanie na decyzję")
        self.assertLessEqual(why["width"], why["maxWidth"] + 1, "akapit do ok. 80 znaków")

    def test_decision_dialog_highlights_note_and_step_reason(self):
        out = self.probe(SCENARIO.replace("ROOT", "page.decisionDialog").replace("NAME", '"dialog"'),
                         self.data_for(False))
        self.assertEqual(out["dialog"], [True, False])
        self.check(out["why"])

    def test_full_card_your_move_highlights_note_and_step_reason(self):
        out = self.probe(SCENARIO.replace("ROOT", "page.cardFullView").replace("NAME", '"twoj-ruch"'),
                         self.data_for(True))
        self.assertEqual(out["dialog"], [False, True])
        self.check(out["why"])


if __name__ == "__main__":
    unittest.main()
