"""Okno decyzji karty (PT-132): wspólny komponent apps/common/qml/card/DecisionDialog.qml w widoku Tablica.

- pakiet: komponent trafia do views/board/qml/card/ obok biblioteki tide, bez kolorów i rozmiarów czcionek
  wpisanych na sztywno, bez logiki wyboru ani polecanej w QML,
- sonda offscreen na scenariuszu F makiety (decisionfix.py, decyzje liczone przez usługę): teksty, kolejność
  możliwości z usługi, oznaczenie polecanej, nic nie zaznaczone, „Wybierz” nieaktywne, Enter bez zaznaczenia
  nic nie robi, Enter po wyborze wysyła ResolveCardStep i przechodzi do następnej decyzji, „1 z N” tylko przy
  N > 1, Esc zamyka bez wysyłania (karta zostaje w „Czeka na mnie”), stary format pytania (samo pole własnej
  odpowiedzi), błąd usługi przywraca decyzję, „Otwórz całą kartę” otwiera obecny panel karty.
"""
import json
import os
import re
import shutil
import unittest

from . import REPO
from .decisionfix import Q2, Q3, decisions, journal, scenario_f, without_options
from .test_view import ProbeCase

CARD_DIR = os.path.join(REPO, "apps", "common", "qml", "card")

# Stan okna z drzewa QML: tylko widoczne elementy, możliwości w kolejności na ekranie (od góry).
STATE_PY = r'''
ALL = ("(function all(it, name, out) { out = out || []; if (!it) return out; "
       "if (it.objectName === name && it.visible) out.push(it); "
       "for (const c of it.children) all(c, name, out); return out })")

def state():
    return json.loads(js("(function () { const d = page.decisionDialog; const all = " + ALL + "; "
        "const one = n => all(d, n)[0] || null; "
        "const y = it => it.mapToItem(d, 0, 0).y; "
        "const opts = all(d, 'decisionOption').sort((a, b) => y(a) - y(b)); "
        "const other = one('decisionOther'); "
        "return JSON.stringify({ open: page.decisionOpen && d.visible, panel: page.panelOpen, "
        "  kind: one('decisionKind') ? one('decisionKind').children[0].text : null, "
        "  who: one('decisionWho') ? one('decisionWho').text : null, "
        "  happened: one('decisionHappened') ? one('decisionHappened').text : null, "
        "  why: one('decisionWhy') ? one('decisionWhy').text : null, "
        "  counter: one('decisionCounter') ? one('decisionCounter').text : '', "
        "  options: opts.map(o => [o.title, o.effect, o.recommended, o.checked]), "
        "  badges: opts.map(o => all(o, 'decisionRecommended').length), "
        "  marks: opts.map(o => all(o, 'decisionOptionMark').length), "
        "  other: other ? [other.y > Math.max(0, ...opts.map(o => o.y)), other.checked] : null, "
        "  answer: all(d, 'decisionAnswer').length, "
        "  tech: all(d, 'decisionTech').length, techToggle: all(d, 'decisionTechToggle').length, "
        "  error: one('decisionError') ? one('decisionError').text : '', "
        "  choose: one('decisionChoose') ? one('decisionChoose').enabled : null }) })()"))

def type_text(text):
    """Pisanie jak z klawiatury (QTest.keyClicks przyjmuje tylko QWidget): zdarzenia z tekstem do okna."""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QKeyEvent
    for ch in text:
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QGuiApplication.sendEvent(window, QKeyEvent(kind, 0, Qt.KeyboardModifier.NoModifier, ch))
    wait(60)

def option(i):
    return at("(" + ALL + ")(page.decisionDialog, 'decisionOption').sort((a, b) => a.y - b.y)[" + str(i) + "]")
'''


class PackageTest(unittest.TestCase):
    def test_component_is_staged_next_to_the_library(self):
        from .test_view import stage
        import tempfile
        from daemon.tests.fakebin import files_under
        with tempfile.TemporaryDirectory(prefix="tide-decision-pkg-") as tmp:
            files = files_under(os.path.join(stage(os.path.join(tmp, "pkg")), "views", "board"))
        for name in ("qml/card/DecisionDialog.qml", "qml/card/DecisionOption.qml", "qml/card/decision.js",
                     "qml/tide/Tokens.qml"):
            self.assertIn(name, files)

    def test_no_hardcoded_colors_fonts_or_choice_logic(self):
        for name in os.listdir(CARD_DIR):
            with open(os.path.join(CARD_DIR, name), encoding="utf-8") as fh:
                source = fh.read()
            self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', source), [], name)
            self.assertNotIn("pixelSize", source, f"{name}: rozmiar czcionki z palety (fontSizePt)")
            self.assertNotIn("radius: 1", source, f"{name}: promienie z palety")
            self.assertNotIn("RichText", source, name)
            self.assertNotIn("StyledText", source, name)
            for _indent, body in re.findall(r"\n(\s*)Text \{\n(.*?)\n\1\}", source, re.S):
                self.assertIn("textFormat: Text.PlainText", body, f"{name}: {body[:80]}")
            # Okno niczego nie liczy: nie sortuje ani nie filtruje możliwości, nie wybiera polecanej ani domyślnej.
            self.assertNotIn(".sort(", source, name)
            self.assertIsNone(re.search(r"selected\s*=(?!=)(?!\s*(?:id\b|dialog\.otherId|\"\"))", source),
                              f"{name}: zaznaczenie tylko z wyboru użytkownika")
        with open(os.path.join(CARD_DIR, "DecisionDialog.qml"), encoding="utf-8") as fh:
            uses = [line.strip() for line in fh if "recommended" in line]
        self.assertEqual(uses, ["recommended: modelData.recommended === true"], "polecana tylko przekazana do karty")
        with open(os.path.join(CARD_DIR, "decision.js"), encoding="utf-8") as fh:
            self.assertNotIn("recommended", fh.read())


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class DecisionDialogTest(ProbeCase):
    def test_scenario_f_texts_order_buttons_enter_and_esc(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["opened"] = state()
key(Qt.Key.Key_Return)
out["enterWithout"] = [len(calls()), state()["options"], state()["choose"]]
click(named("decisionTechToggle"))
out["techOpen"] = state()["tech"]
click(option(0))
out["picked"] = state()
key(Qt.Key.Key_Return)
out["sent"] = calls()
wait(80)
out["next"] = state()
click(option(1))
key(Qt.Key.Key_Escape)
out["closed"] = [state()["open"], len(calls()), js("page.toast"),
                 js("page.cards.find(c => c.id === 'K-8').boardColumn")]
click(card("K-8"))
out["again"] = [state()["counter"], state()["options"][0][3]]
""", scenario_f(self.data))
        opened = out["opened"]
        self.assertTrue(opened["open"], "kafelek w „Czeka na mnie” z decyzjami otwiera okno")
        self.assertFalse(opened["panel"], "panel karty zostaje zamknięty")
        self.assertEqual(opened["kind"], "Pytanie")
        self.assertEqual(opened["who"], "K-8 · #3 · Sonnet", "#3 pytał pierwszy (11:31) — usługa daje go pierwszego")
        self.assertEqual(opened["happened"], f"#3 (Sonnet) pyta: „{Q3}”")
        self.assertEqual(opened["why"], "Krok czeka na odpowiedź; bez niej nie pójdzie dalej.")
        self.assertEqual(opened["counter"], "1 z 2")
        self.assertEqual([o[0] for o in opened["options"]],
                         ["Tak, uruchom poza piaskownicą", "Tylko dzienniki Claude", "Nie, zbuduj zestaw z testów"],
                         "kolejność z usługi: polecona pierwsza")
        self.assertEqual(opened["options"][1][1], "Bez ~/.codex/sessions; pomiar obejmie jednego dostawcę.")
        self.assertEqual([o[2] for o in opened["options"]], [True, False, False])
        self.assertEqual(opened["badges"], [1, 0, 0], "plakietka „polecane” tylko przy polecanej")
        self.assertEqual(opened["marks"], [1, 1, 1])
        self.assertEqual([o[3] for o in opened["options"]], [False, False, False], "nic nie jest zaznaczone z góry")
        self.assertEqual(opened["other"], [True, False], "„Inna odpowiedź…” ostatnia, niezaznaczona")
        self.assertEqual(opened["answer"], 0, "pole odpowiedzi dopiero po wyborze „Inna odpowiedź…”")
        self.assertEqual((opened["tech"], opened["techToggle"]), (0, 1), "szczegóły zwinięte")
        self.assertFalse(opened["choose"], "„Wybierz” nieaktywne do zaznaczenia")

        self.assertEqual(out["enterWithout"][0], 0, "Enter bez zaznaczenia nic nie wysyła")
        self.assertEqual([o[3] for o in out["enterWithout"][1]], [False, False, False])
        self.assertFalse(out["enterWithout"][2])
        self.assertEqual(out["techOpen"], 1, "szczegóły rozwijają się na żądanie")

        self.assertEqual([o[3] for o in out["picked"]["options"]], [True, False, False])
        self.assertTrue(out["picked"]["choose"])
        self.assertEqual(len(out["sent"]), 1)
        self.assertEqual(out["sent"][0][:2], ["ResolveCardStep", "K-8"])
        self.assertEqual(json.loads(out["sent"][0][2]),
                         {"choice": "answer", "step": "K-8-ticket-3-0", "answer": "Tak, uruchom poza piaskownicą"})

        following = out["next"]
        self.assertTrue(following["open"], "po odpowiedzi okno przechodzi do następnej decyzji")
        self.assertEqual(following["who"], "K-8 · #2 · Opus")
        self.assertEqual(following["happened"], f"#2 (Opus) pyta: „{Q2}”")
        self.assertEqual(following["counter"], "", "„1 z N” tylko przy N > 1")
        self.assertEqual([o[0] for o in following["options"]], ["Napraw z testem", "Tylko test i dowody"])
        self.assertEqual([o[3] for o in following["options"]], [False, False], "w następnej też nic zaznaczonego")
        self.assertFalse(following["choose"])
        self.assertEqual((following["tech"], following["other"]), (0, [True, False]))

        self.assertFalse(out["closed"][0], "Esc zamyka okno")
        self.assertEqual(out["closed"][1], 1, "Esc niczego nie wysyła")
        self.assertIn("Czeka na mnie", out["closed"][2])
        self.assertEqual(out["closed"][3], "waiting", "karta zostaje w „Czeka na mnie”")
        self.assertEqual(out["again"], ["1 z 2", False], "ponowne otwarcie bez nowej migawki — od początku, bez zaznaczenia")

    def test_single_decision_has_no_counter(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["state"] = state()
""", scenario_f(self.data, decisions()[1:]))
        self.assertTrue(out["state"]["open"])
        self.assertEqual(out["state"]["counter"], "")
        self.assertEqual(out["state"]["who"], "K-8 · #2 · Opus")

    def test_question_without_options_is_only_free_text_and_enter_sends_it(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["opened"] = state()
key(Qt.Key.Key_Return)
out["empty"] = len(calls())
js("page.decisionDialog.answerField.forceActiveFocus()")
type_text("ISO 8601")
wait(60)
out["typed"] = state()["choose"]
key(Qt.Key.Key_Return)
out["sent"] = calls()
""", scenario_f(self.data, decisions(without_options(journal()))[:1]))
        opened = out["opened"]
        self.assertEqual(opened["options"], [], "brak opcji")
        self.assertEqual(opened["answer"], 1, "samo pole własnej odpowiedzi")
        self.assertEqual(opened["counter"], "")
        self.assertFalse(opened["choose"])
        self.assertEqual(out["empty"], 0, "pusta odpowiedź nie idzie")
        self.assertTrue(out["typed"])
        self.assertEqual(out["sent"][0][:2], ["ResolveCardStep", "K-8"])
        self.assertEqual(json.loads(out["sent"][0][2]), {"choice": "answer", "step": "K-8-ticket-3-0", "answer": "ISO 8601"})

    def test_other_answer_is_last_and_sent_through_reply(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
click(named("decisionOther", 0.5, 0.2))
out["picked"] = state()
key(Qt.Key.Key_Return)
out["empty"] = len(calls())
type_text("tylko 7 dni")
key(Qt.Key.Key_Return)
out["sent"] = calls()
""", scenario_f(self.data))
        self.assertEqual(out["picked"]["other"], [True, True])
        self.assertEqual(out["picked"]["answer"], 1)
        self.assertFalse(out["picked"]["choose"], "własna odpowiedź bez tekstu — „Wybierz” nieaktywne")
        self.assertEqual(out["empty"], 0)
        self.assertEqual(json.loads(out["sent"][0][2]), {"choice": "answer", "step": "K-8-ticket-3-0", "answer": "tylko 7 dni"})

    def test_service_error_brings_decision_back_with_reason(self):
        out = self.probe(STATE_PY + """
replies.append({"ok": False, "error": "busy", "message": "krok jeszcze się kończy"})
click(card("K-8"))
click(option(1))
key(Qt.Key.Key_Return)
wait(150)
out["state"] = state()
""", scenario_f(self.data))
        self.assertTrue(out["state"]["open"])
        self.assertEqual(out["state"]["who"], "K-8 · #3 · Sonnet", "decyzja wraca")
        self.assertEqual(out["state"]["counter"], "1 z 2")
        self.assertIn("krok jeszcze się kończy", out["state"]["error"])
        self.assertEqual([o[3] for o in out["state"]["options"]], [False, False, False])

    def test_open_full_card_opens_current_card_panel(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
click(named("decisionFullCard"))
out["state"] = [js("page.decisionOpen"), js("page.panelOpen"), js("page.panelCardId")]
out["calls"] = len(calls())
""", scenario_f(self.data))
        self.assertEqual(out["state"], [False, True, "K-8"])
        self.assertEqual(out["calls"], 0)

    def test_waiting_card_without_decisions_still_opens_panel(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["state"] = [js("page.decisionOpen"), js("page.panelOpen"), js("page.panelCardId")]
""")
        self.assertEqual(out["state"], [False, True, "K-8"])


if __name__ == "__main__":
    unittest.main()
