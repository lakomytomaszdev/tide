"""Okno decyzji karty (PT-132): wspólny komponent apps/common/qml/card/DecisionDialog.qml w widoku Tablica.

- pakiet: komponent trafia do views/board/qml/card/ obok biblioteki tide, bez kolorów i rozmiarów czcionek
  wpisanych na sztywno, bez logiki wyboru ani polecanej w QML,
- sonda offscreen na scenariuszu F makiety (decisionfix.py, decyzje liczone przez usługę): teksty, kolejność
  możliwości z usługi, oznaczenie polecanej, nic nie zaznaczone, „Wybierz” nieaktywne, Enter bez zaznaczenia
  nic nie robi, Enter po wyborze wysyła ResolveCardStep i przechodzi do następnej decyzji, „1 z N” tylko przy
  N > 1, Esc zamyka bez wysyłania (karta zostaje w „Czeka na mnie”), stary format pytania (samo pole własnej
  odpowiedzi), błąd usługi przywraca decyzję, „Otwórz całą kartę” otwiera pełną kartę (PT-128), a możliwość
  z wyborem wykonawcy — panel karty.
"""
import json
import os
import re
import shutil
import unittest

from . import REPO
from .decisionfix import (A1_STEP, A2_COMMAND, A2_STEP, FINDINGS_C, Q2, Q3, a1_journal, a1_one_journal, a2_journal,
                          decisions, decisions_c, full_card, journal, journal_c, scenario_b, scenario_c, scenario_f,
                          without_options)
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
        "  findings: all(d, 'decisionFinding').sort((a, b) => y(a) - y(b)).map(f => [ "
        "    all(f, 'decisionFindingSeverity')[0].children[0].text, all(f, 'decisionFindingText')[0].text, "
        "    all(f, 'decisionFindingTicket').map(t => t.text), f.checked]), "
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

def commands():
    """Polecenia łańcucha w oknie (PT-124): [tekst, zaznaczone, pole wyboru, powód, plakietki]."""
    return json.loads(js("(function () { const d = page.decisionDialog; const all = " + ALL + "; "
        "const y = it => it.mapToItem(d, 0, 0).y; "
        "return JSON.stringify(all(d, 'decisionCommandRow').sort((a, b) => y(a) - y(b)).map(r => [r.text, r.checked, "
        "  all(r, 'decisionCommandCheck').length === 1, all(r, 'decisionCommandReason').length ? r.reason : '', "
        "  all(r, 'decisionCommandTag').map(t => t.label)])) })()"))

def command(i):
    return at("(" + ALL + ")(page.decisionDialog, 'decisionCommandRow').sort((a, b) => a.y - b.y)[" + str(i) + "]",
              0.3, 0.5)

def permission():
    return json.loads(js("(function () { const d = page.decisionDialog; const all = " + ALL + "; "
        "const one = n => all(d, n)[0] || null; "
        "return JSON.stringify({ otherTitle: one('decisionOtherTitle') ? one('decisionOtherTitle').text : null, "
        "  hint: !!one('decisionAllowHint'), list: !!one('decisionCommands') }) })()"))
'''


class PackageTest(unittest.TestCase):
    def test_component_is_staged_next_to_the_library(self):
        from .test_view import stage
        import tempfile
        from daemon.tests.fakebin import files_under
        with tempfile.TemporaryDirectory(prefix="tide-decision-pkg-") as tmp:
            files = files_under(os.path.join(stage(os.path.join(tmp, "pkg")), "views", "board"))
        for name in ("qml/card/DecisionDialog.qml", "qml/card/DecisionOption.qml", "qml/card/DecisionFinding.qml",
                     "qml/card/DecisionForm.qml", "qml/card/DecisionKind.qml", "qml/card/CardFullView.qml",
                     "qml/card/decision.js",
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
            self.assertIsNone(re.search(r"selected\s*=(?!=)(?!\s*(?:id\b|(?:dialog|form)\.otherId|\"\"))", source),
                              f"{name}: zaznaczenie tylko z wyboru użytkownika")
        # Treść decyzji (możliwości) jest w DecisionForm.qml — wspólnej dla okna decyzji i pełnej karty (PT-128).
        for name in ("DecisionDialog.qml", "CardFullView.qml"):
            with open(os.path.join(CARD_DIR, name), encoding="utf-8") as fh:
                self.assertNotIn("recommended", fh.read(), f"{name}: możliwości tylko przez DecisionForm")
        with open(os.path.join(CARD_DIR, "DecisionForm.qml"), encoding="utf-8") as fh:
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

    def test_scenario_c_lists_findings_with_severity_tickets_and_default_selection(self):
        out = self.probe(STATE_PY + """
def finding(i):
    return at("(" + ALL + ")(page.decisionDialog, 'decisionFinding').sort((a, b) => a.y - b.y)[" + str(i) + "]")
click(card("K-8"))
out["opened"] = state()
click(finding(2))
out["nitOn"] = [f[3] for f in state()["findings"]]
click(finding(2))
click(finding(0))
out["firstOff"] = [f[3] for f in state()["findings"]]
click(finding(0))
click(option(0))
key(Qt.Key.Key_Return)
out["sent"] = calls()
""", scenario_c(self.data))
        opened = out["opened"]
        self.assertTrue(opened["open"])
        self.assertEqual(opened["kind"], "Plan do zatwierdzenia")
        self.assertEqual(opened["who"], "K-8 · Opus")
        self.assertIn("Plan ma 9 ticketów", opened["happened"])
        self.assertIn("prosi o zmiany", opened["happened"])
        self.assertEqual([f[0] for f in opened["findings"]], ["ważna", "ważna", "drobna"],
                         "każda uwaga ma wagę, nie same liczniki")
        self.assertEqual([f[2] for f in opened["findings"]], [["#5", "#7", "#8"], ["#3"], ["PT-42"]],
                         "powiązane tickety przy każdej uwadze")
        self.assertIn("kartka nie mówi", opened["findings"][1][1])
        self.assertEqual([f[3] for f in opened["findings"]], [True, True, False], "blocker i major zaznaczone, nit nie")
        self.assertEqual([(o[0], o[2]) for o in opened["options"]],
                         [("Odeślij uwagi do poprawy", True), ("Zatwierdź plan i rozpocznij implementację", False)],
                         "z prośbą o zmiany polecone jest odesłanie")
        self.assertEqual(out["nitOn"], [True, True, True])
        self.assertEqual(out["firstOff"], [False, True, False])
        self.assertEqual(out["sent"][0][:2], ["EditCardPlan", "K-8"])
        params = json.loads(out["sent"][0][2])
        self.assertEqual(params["action"], "return")
        self.assertEqual([f["text"] for f in params["findings"]], [f["text"] for f in FINDINGS_C[:2]],
                         "odesłane tylko zaznaczone uwagi")
        self.assertNotIn("feedback", params)

    def test_scenario_c_sends_selected_findings_with_own_text_and_needs_something_to_send(self):
        out = self.probe(STATE_PY + """
def finding(i):
    return at("(" + ALL + ")(page.decisionDialog, 'decisionFinding').sort((a, b) => a.y - b.y)[" + str(i) + "]")
click(card("K-8"))
click(finding(0))
click(finding(1))
click(option(0))
key(Qt.Key.Key_Return)
out["none"] = [len(calls()), state()["error"]]
click(named("decisionOther", 0.5, 0.2))
type_text("#7 i #8 zostaw w fali 3.")
key(Qt.Key.Key_Return)
out["sent"] = calls()
""", scenario_c(self.data))
        self.assertEqual(out["none"][0], 0, "bez zaznaczonych uwag i bez własnego tekstu nic nie wychodzi")
        self.assertIn("Zaznacz uwagi", out["none"][1])
        params = json.loads(out["sent"][0][2])
        self.assertEqual(params["feedback"], "#7 i #8 zostaw w fali 3.")
        self.assertEqual(params["findings"], [], "niezaznaczone uwagi nie wychodzą, własny tekst tak")

    def test_scenario_c_approve_despite_blockers_states_its_effect(self):
        blockers = [{"severity": "blocker", "text": "Pierwszy ticket nie ma kryteriów.", "tickets": ["#1"]}]
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["state"] = state()
click(option(1))
key(Qt.Key.Key_Return)
out["sent"] = calls()
""", scenario_c(self.data, decisions_c(journal_c(blockers))))
        self.assertEqual([f[0] for f in out["state"]["findings"]], ["blokująca"])
        self.assertEqual(out["state"]["options"][0][0], "Odeślij uwagi do poprawy")
        approve = out["state"]["options"][1]
        self.assertIn("Mimo 1 blokującej uwagi", approve[1])
        self.assertEqual(out["sent"][0][:2], ["ApproveCardPlan", "K-8"])

    def test_scenario_c_without_verdict_recommends_nothing_and_old_text_has_no_severity(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["state"] = state()
""", scenario_c(self.data, decisions_c(journal_c("Stary tekst uwag.", verdict=""))))
        state = out["state"]
        self.assertEqual([o[2] for o in state["options"]], [False, False], "nieznany werdykt — brak polecanej")
        self.assertEqual([(f[0], f[1], f[2], f[3]) for f in state["findings"]],
                         [("bez wagi", "Stary tekst uwag.", [], True)], "stary tekst: bez wagi, bez ticketów")

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

    def test_open_full_card_opens_the_full_card_view(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
click(named("decisionFullCard"))
out["state"] = [js("page.decisionOpen"), js("page.fullOpen"), js("page.fullCardId"), js("page.panelOpen")]
out["calls"] = len(calls())
""", full_card(self.data, journal()))
        self.assertEqual(out["state"], [False, True, "K-8", False])
        self.assertEqual(out["calls"], 0)

    def test_scenario_b_tide_error_previews_the_card_then_reports_and_retries(self):
        preview = {"ok": True, "id": "K-8", "step": "K-8-plan-2", "preview": True, "action": "create", "card": "",
                   "project": "plasma-tide", "title": "Błąd Tide w kroku review planu: Tide nie dał prawa zapisu",
                   "description": "Podpis błędu: `plan-review/tide/write-denied`\n\n> Po aktualizacji to samo.\n"
                                  "```text\n[result] odmowa Write: /wt/K-8-plan-2.step/out/result.json\n```"}
        out = self.probe(STATE_PY + """
PREVIEW = ("(function () { const all = " + ALL + "; const one = n => all(page.decisionDialog, n)[0] || null; "
           "return JSON.stringify({ shown: !!one('decisionPreview'), "
           "  where: one('decisionPreviewWhere') ? one('decisionPreviewWhere').text : '', "
           "  title: one('decisionPreviewTitle') ? one('decisionPreviewTitle').text : '', "
           "  body: one('decisionPreviewBody') ? one('decisionPreviewBody').text : '', "
           "  other: one('decisionOtherTitle') ? one('decisionOtherTitle').text : '', "
           "  effect: one('decisionOtherEffect') ? one('decisionOtherEffect').text : '', "
           "  button: one('decisionChoose') ? one('decisionChoose').text : '' }) })()")
replies.append(json.loads(%r))
click(card("K-8"))
out["opened"] = state()
out["openedExtra"] = json.loads(js(PREVIEW))
click(named("decisionOther", 0.5, 0.2))
type_text("Po aktualizacji to samo.")
out["typed"] = json.loads(js(PREVIEW))
key(Qt.Key.Key_Return)
wait(150)
out["previewCall"] = calls()
out["preview"] = json.loads(js(PREVIEW))
out["previewState"] = state()
key(Qt.Key.Key_Return)
wait(150)
out["sent"] = calls()
out["after"] = [js("page.decisionOpen"), js("page.toast")]
""" % json.dumps(preview, ensure_ascii=False), scenario_b(self.data))
        opened = out["opened"]
        self.assertTrue(opened["open"])
        self.assertEqual(opened["kind"], "Błąd Tide", "plakietka oddziela usterkę narzędzia od decyzji o pracy")
        self.assertEqual(opened["who"], "K-8 · Haiku")
        self.assertEqual(opened["happened"], "Review planu (Haiku) zakończył się bez wyniku (próba 2): "
                                             "Tide nie dał prawa zapisu w katalogu wyniku kroku.")
        self.assertTrue(opened["why"].startswith("To błąd Tide, nie pracy kroku."))
        self.assertEqual([(o[0], o[2], o[3]) for o in opened["options"]],
                         [("Ponów krok", True, False), ("Ponów innym wykonawcą…", False, False)])
        self.assertIn("tu nie był", opened["options"][1][1])
        self.assertEqual(opened["other"], [True, False])
        self.assertEqual(out["openedExtra"]["other"], "Opisz problem i ponów")
        self.assertIn("Backlogu plasma-tide", out["openedExtra"]["effect"])
        self.assertFalse(out["openedExtra"]["shown"], "podglądu nie ma przed opisem")

        self.assertEqual(out["typed"]["button"], "Pokaż podgląd  ↵")
        [call] = out["previewCall"]
        self.assertEqual(call[:2], ["PreviewStepFault", "K-8"])
        self.assertEqual(json.loads(call[2]), {"step": "K-8-plan-2", "description": "Po aktualizacji to samo."})
        shown = out["preview"]
        self.assertTrue(shown["shown"], "podgląd karty przed zapisem")
        self.assertEqual(shown["where"], "Nowa karta w Backlogu plasma-tide:")
        self.assertEqual(shown["title"], preview["title"])
        self.assertIn("> Po aktualizacji to samo.", shown["body"], "opis użytkownika w podglądzie")
        self.assertIn("odmowa Write", shown["body"], "wycinek dziennika w podglądzie")
        self.assertEqual(shown["button"], "Zapisz i ponów  ↵")
        self.assertTrue(out["previewState"]["open"], "podgląd niczego nie wysyła ani nie zamyka")

        self.assertEqual(len(out["sent"]), 2)
        self.assertEqual(out["sent"][1][:2], ["ReportStepFault", "K-8"])
        self.assertEqual(json.loads(out["sent"][1][2]), {"step": "K-8-plan-2", "description": "Po aktualizacji to samo."})
        self.assertFalse(out["after"][0], "jedyna decyzja odpowiedziana — okno się zamyka")

    def test_option_that_needs_an_executor_opens_the_card_panel(self):
        events = journal()[:3] + [
            {"v": 1, "seq": 4, "ts": "2026-11-03T11:00:00+01:00", "card": "K-8", "type": "step.started",
             "step": "K-8-ticket-2-1", "ticket": "#2", "kind": "ticket", "executor": "Opus", "provider": "anthropic",
             "attempt": 1, "parentStep": "K-8-ticket-2-0"},
            {"v": 1, "seq": 5, "ts": "2026-11-03T11:10:00+01:00", "card": "K-8", "type": "step.finished",
             "step": "K-8-ticket-2-1", "ticket": "#2", "result": "error", "reason": "exit"}]
        out = self.probe(STATE_PY + """
click(card("K-8"))
click(option(1))
key(Qt.Key.Key_Return)
out["state"] = [js("page.decisionOpen"), js("page.fullOpen"), js("page.panelOpen"), js("page.panelCardId"),
                js("page.cardPanel.notice")]
""", full_card(self.data, events))
        self.assertEqual(out["state"][:4], [False, False, True, "K-8"], "wybór wykonawcy jest w panelu karty")
        self.assertIn("wybierz wykonawcę", out["state"][4])

    def test_waiting_card_without_decisions_still_opens_panel(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["state"] = [js("page.decisionOpen"), js("page.panelOpen"), js("page.panelCardId")]
""")
        self.assertEqual(out["state"], [False, True, "K-8"])


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class PermissionDialogTest(ProbeCase):
    """PT-124: zgoda na łańcuch (scenariusz A1) i na jedno polecenie (A2) — decyzje z usługi, nie wpisane ręcznie."""

    def test_a1_chain_lists_commands_preselected_and_allows_exactly_the_checked(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["opened"] = state()
out["perm"] = permission()
out["commands"] = commands()
click(option(1))
out["picked"] = state()["choose"]
click(command(1))
out["unchecked"] = [c[1] for c in commands()]
key(Qt.Key.Key_Return)
out["sent"] = calls()
""", scenario_f(self.data, decisions(a1_journal())))
        opened = out["opened"]
        self.assertEqual(opened["kind"], "Prośba o zgodę")
        self.assertEqual(opened["who"], "K-8 · Haiku")
        self.assertIn("naraz 4 polecenia spoza listy dozwolonych", opened["happened"])
        self.assertIn("Tide zatwierdza polecenia pojedynczo", opened["why"])
        self.assertEqual([o[0] for o in opened["options"]], ["Kontynuuj bez tych poleceń", "Zezwól na wybrane i wznów"])
        self.assertEqual([o[3] for o in opened["options"]], [False, False], "żadna możliwość nie jest zaznaczona (D10)")
        self.assertFalse(opened["choose"])
        self.assertEqual(out["perm"], {"otherTitle": "Zrób zamiast tego…", "hint": False, "list": True})
        self.assertEqual(out["commands"], [
            ["ls docs", True, True, "", ["tylko czyta"]],
            ["head -50", True, True, "", ["tylko czyta"]],
            ["git log --oneline -3 -- docs/agent-plan-standard.md", True, True, "", ["tylko czyta"]],
            ["git branch --list 'codex/k28*' 'claude/k-29*' 'fix/plan-ticket*'", True, True, "", ["tylko czyta"]],
        ], "polecenia „tylko czyta” zaznaczone z góry; odczyt własnego katalogu kroku nie jest na liście")
        self.assertTrue(out["picked"])
        self.assertEqual(out["unchecked"], [True, False, True, True])
        self.assertEqual(len(out["sent"]), 1)
        self.assertEqual(out["sent"][0][:2], ["ResolveCardStep", "K-8"])
        self.assertEqual(json.loads(out["sent"][0][2]),
                         {"choice": "allow", "step": A1_STEP, "allow": [0, 2, 3]})

    def test_a1_allow_without_checked_command_says_why(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
for i in range(4):
    click(command(i))
click(option(1))
out["state"] = [state()["choose"], permission()["hint"], js("page.decisionDialog.form.checks.length")]
key(Qt.Key.Key_Return)
out["calls"] = len(calls())
click(command(0))
out["after"] = [state()["choose"], permission()["hint"]]
""", scenario_f(self.data, decisions(a1_journal())))
        self.assertEqual(out["state"], [False, True, 0], "„Wybierz” nieaktywne, ale z powodem")
        self.assertEqual(out["calls"], 0)
        self.assertEqual(out["after"], [True, False])

    def test_a1_forbidden_command_has_reason_and_no_checkbox(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["commands"] = commands()
click(command(4))
out["after"] = commands()[4][1]
""", scenario_f(self.data, decisions(a1_journal("; echo $(make install)"))))
        rows = {c[0]: c for c in out["commands"]}
        self.assertEqual(rows["make install"][1:], [False, False, "Zabronione w projekcie regułą „make install”.",
                                                    ["zabronione"]])
        self.assertFalse(rows["echo $(make install)"][2])
        self.assertTrue(rows["echo $(make install)"][3], "zablokowane polecenie zawsze ma powód")
        self.assertFalse(out["after"], "polecenia zabronionego nie da się zaznaczyć")

    def test_a1_continue_without_and_do_instead(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
click(option(0))
key(Qt.Key.Key_Return)
out["deny"] = calls()
""", scenario_f(self.data, decisions(a1_journal())))
        self.assertEqual(json.loads(out["deny"][0][2]), {"choice": "deny", "step": A1_STEP})
        out = self.probe(STATE_PY + """
click(card("K-8"))
click(named("decisionOther", 0.5, 0.2))
type_text("przeczytaj tylko docs/agent-plan-standard.md")
key(Qt.Key.Key_Return)
out["instead"] = calls()
""", scenario_f(self.data, decisions(a1_journal())))
        self.assertEqual(json.loads(out["instead"][0][2]),
                         {"choice": "deny", "step": A1_STEP, "instead": "przeczytaj tylko docs/agent-plan-standard.md"})

    def test_chain_with_own_read_and_one_command_shows_it_and_allows_it(self):
        """Review PT-124 r2 MAJOR-1: po odjęciu własnego odczytu zostaje jedno polecenie — lista jest widoczna,
        polecenie do zaznaczenia, a „Zezwól na wybrane” wysyła dokładnie je."""
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["opened"] = state()
out["perm"] = permission()
out["commands"] = commands()
click(option(1))
out["blocked"] = [state()["choose"], permission()["hint"]]
click(command(0))
out["checked"] = [state()["choose"], permission()["hint"], commands()[0][1]]
key(Qt.Key.Key_Return)
out["sent"] = calls()
""", scenario_f(self.data, decisions(a1_one_journal())))
        opened = out["opened"]
        self.assertIn("git fetch origin", opened["happened"])
        self.assertEqual([o[0] for o in opened["options"]], ["Kontynuuj bez tych poleceń", "Zezwól na wybrane i wznów"])
        self.assertTrue(out["perm"]["list"], "lista poleceń widoczna także przy jednym poleceniu z łańcucha")
        self.assertEqual(out["commands"], [["git fetch origin", False, True, "", []]])
        self.assertEqual(out["blocked"], [False, True], "bez zaznaczenia „Wybierz” nieaktywne, podpowiedź wskazuje listę")
        self.assertEqual(out["checked"], [True, False, True])
        self.assertEqual(json.loads(out["sent"][0][2]), {"choice": "allow", "step": A1_STEP, "allow": [0]})

    def test_chain_with_own_read_and_one_read_only_command_is_visible_and_preselected(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["perm"] = permission()
out["commands"] = commands()
click(option(1))
key(Qt.Key.Key_Return)
out["sent"] = calls()
""", scenario_f(self.data, decisions(a1_one_journal("ls docs"))))
        self.assertTrue(out["perm"]["list"], "zgoda tylko na tekst widoczny w oknie")
        self.assertEqual(out["commands"], [["ls docs", True, True, "", ["tylko czyta"]]])
        self.assertEqual(json.loads(out["sent"][0][2]), {"choice": "allow", "step": A1_STEP, "allow": [0]})

    def test_a2_single_command_allow_and_resume(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["opened"] = state()
out["perm"] = permission()
click(option(0))
key(Qt.Key.Key_Return)
out["sent"] = calls()
""", scenario_f(self.data, decisions(a2_journal())))
        opened = out["opened"]
        self.assertEqual(opened["who"], "K-8 · #3 · Sonnet")
        self.assertIn(A2_COMMAND, opened["happened"])
        self.assertEqual([(o[0], o[2]) for o in opened["options"]],
                         [("Zezwól i wznów", True), ("Kontynuuj bez tego polecenia", False)])
        self.assertEqual(out["perm"], {"otherTitle": "Zrób zamiast tego…", "hint": False, "list": False},
                         "jedno polecenie — bez listy, polecenie jest na wierzchu")
        self.assertEqual(json.loads(out["sent"][0][2]), {"choice": "allow", "step": A2_STEP})


if __name__ == "__main__":
    unittest.main()
