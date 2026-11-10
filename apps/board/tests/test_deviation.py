"""„Przyjmij odstępstwo…” przy błędzie domknięcia (K-97, PT-339): apps/common/qml/card/deviation.js i DecisionForm.qml.

- decyzje liczy usługa (`build_decisions` z dziennika karty): błąd kroku domknięcia po automatycznym ponowieniu ma
  możliwość `retry`, pierwszy błąd przed ponowieniem nie ma decyzji, błąd kroku ticketu nie jest domknięciem,
- okno decyzji i blok „Twój ruch”: możliwość po możliwościach usługi, przed „Inną odpowiedzią…”, niezaznaczona
  i niepolecona; bez uzasadnienia „Wybierz” nieaktywne ze zdaniem dlaczego; ponad limit 4000 znaków blokada
  z licznikiem; wybór wysyła jedno `ResolveCardStep` z `choice: retry`, `step` decyzji i `answer` ze stałą
  instrukcją i uzasadnieniem,
- stała instrukcja zgadza się z „Rozliczeniem nieudanych przebiegów” (docs/testing.md) i kartką domknięcia.
"""
import json
import os
import re
import shutil
import unittest

from . import REPO
from .decisionfix import CARD, decisions, full_card, journal_close_error, scenario_f
from .test_card_full_view import STATE_PY as FULL_STATE_PY
from .test_decision_dialog import STATE_PY
from .test_view import ProbeCase

LIBRARY = os.path.join(REPO, "apps", "common", "qml", "card", "deviation.js")
CLOSE_STEP = "K-8-close-1-1"
REASON = "test_cardflow_dbus pada też na bazie i na master przy obciążeniu; poprawka w karcie K-99"

# Stan możliwości odstępstwa w oknie decyzji (`root` — wyrażenie korzenia: okno albo pełna karta).
DEVIATION_PY = r'''
def deviation(root="page.decisionDialog", choose="decisionChoose"):
    return json.loads(js("(function () { const d = " + root + "; const all = " + ALL + "; "
        "const one = n => all(d, n)[0] || null; const y = it => it.mapToItem(d, 0, 0).y; "
        "const dev = one('decisionDeviation'); const other = one('decisionOther'); "
        "const opts = all(d, 'decisionOption').map(y); "
        "return JSON.stringify({ shown: !!dev, "
        "  title: dev ? dev.title : null, effect: dev ? dev.effect : null, "
        "  recommended: dev ? dev.recommended : null, checked: dev ? dev.checked : null, "
        "  badge: dev ? all(dev, 'decisionRecommended').length : 0, "
        "  afterOptions: dev ? opts.every(o => y(dev) > o) : null, "
        "  beforeOther: dev && other ? y(dev) < y(other) : null, "
        "  options: opts.length, field: !!one('decisionDeviationReason'), "
        "  hint: one('decisionDeviationHint') ? one('decisionDeviationHint').text : '', "
        "  counter: one('decisionDeviationCounter') ? one('decisionDeviationCounter').text : '', "
        "  choose: one('" + choose + "') ? one('" + choose + "').enabled : null }) })()"))

def scroll_to_end(root="page.decisionDialog"):
    """Przewija treść tak, żeby możliwość odstępstwa była widoczna (jak użytkownik czytający możliwości)."""
    js("(function () { const a = " + ALL + "; const target = a(" + root + ", 'decisionDeviation')[0]; "
       "let f = target.parent; while (f && f.contentY === undefined) f = f.parent; "
       "if (f) f.contentY = Math.max(0, Math.min(f.contentHeight - f.height, "
       "  target.mapToItem(f.contentItem, 0, 0).y - f.height / 3)); })()")
    wait(80)

def set_reason(text, root="page.decisionDialog"):
    js("(" + ALL + ")(" + root + ", 'decisionDeviationReason')[0].text = " + json.dumps(text))
    wait(60)
'''


def close_decisions(**kwargs) -> list[dict]:
    return decisions(journal_close_error(**kwargs))


class LibraryTest(unittest.TestCase):
    """Treść stałej instrukcji i strażnik „okno niczego nie liczy” dla nowej biblioteki."""

    def setUp(self):
        with open(LIBRARY, encoding="utf-8") as fh:
            self.source = fh.read()

    def instruction(self) -> str:
        body = re.search(r"var INSTRUCTION = (.*?)\nvar CLOSING", self.source, re.S).group(1)
        return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', body))

    def test_instruction_follows_test_exception_rules_and_keeps_failure(self):
        text = self.instruction()
        for phrase in ("przyjmuje odstępstwo od pełnych testów domknięcia", "„Wyjątki od testów”",
                       "pierwszy wynik", "przypiętej bazie", "gałęzi głównej", "obciążenie", "zakres odstępstwa",
                       "właściciela poprawki", "brak danych oznacz jawnie",
                       "z zapisanymi SHA, poleceniami i warunkami porównania",
                       "równoległe przebiegi i limit procesów", "Zachowaj logi przebiegów",
                       "nie uznawaj bez dowodu",
                       "Wynik testu zostaje porażką",
                       "Nie powtarzaj nieudanego pełnego przebiegu na tym samym kodzie",
                       "jeśli pełny przebieg nie został wykonany, wykonaj go raz według kartki",
                       # MAJOR-1 (review PT-339 r1): kartka domknięcia mówi „nie zgłaszaj done” przy nieudanych
                       # testach — instrukcja musi powiedzieć, jak wznowiony krok ma się skończyć.
                       "dokończ domknięcie", "zakończ krok wynikiem done", "nie scalaj do gałęzi głównej",
                       "nie rób push",
                       "Scalenie do gałęzi głównej wymaga osobnej zgody", "Uzasadnienie użytkownika: „"):
            self.assertIn(phrase, text)
        # Nie może sugerować, że testy przeszły, ani pozwalać na scalenie do głównej.
        for wrong in ("przeszły", "zielon", "sukces", "możesz scalić", "scal do", "zgoda na scalenie"):
            self.assertNotIn(wrong, text.lower())

    def test_effect_says_failure_stays_and_merge_needs_separate_consent(self):
        effect = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"',
                                    re.search(r"var EFFECT = (.*?)\nvar ", self.source, re.S).group(1)))
        self.assertIn("niestabilny test z rozliczeniem", effect)
        self.assertIn("Krok domknięcia wznowi się z tą decyzją", effect)
        self.assertIn("wynik testów zostaje porażką", effect)
        self.assertIn("scalenie do gałęzi głównej nadal wymaga osobnej zgody", effect)

    def test_library_never_recommends_or_sorts(self):
        self.assertNotIn("recommended", self.source)
        self.assertNotIn(".sort(", self.source)
        self.assertIn("var LIMIT = 4000", self.source)


class ServiceDecisionTest(unittest.TestCase):
    """Kiedy usługa daje decyzję z `retry` — możliwość okna stoi tylko tam (warunek w deviation.offered)."""

    def test_close_error_after_automatic_retry_has_retry(self):
        items = close_decisions()
        self.assertEqual(len(items), 1)
        decision = items[0]
        self.assertEqual((decision["kind"], decision["stepKind"], decision["step"]), ("error", "close", CLOSE_STEP))
        retry = [o for o in decision["options"] if o["params"].get("choice") == "retry"]
        self.assertEqual([(o["method"], o["params"]) for o in retry],
                         [("ResolveCardStep", {"choice": "retry", "step": CLOSE_STEP})])

    def test_first_close_error_before_retry_has_no_decision(self):
        self.assertEqual(close_decisions(attempts=1), [])

    def test_ticket_error_is_not_close(self):
        items = close_decisions(kind="ticket", ticket="#3")
        self.assertEqual([(d["kind"], d["stepKind"]) for d in items], [("error", "ticket")])


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class DeviationDialogTest(ProbeCase):
    def test_close_error_offers_deviation_after_service_options_unchecked_and_not_recommended(self):
        out = self.probe(STATE_PY + DEVIATION_PY + """
click(card("K-8"))
out["state"] = state()
out["dev"] = deviation()
""", scenario_f(self.data, close_decisions()))
        state, dev = out["state"], out["dev"]
        self.assertTrue(state["open"])
        self.assertEqual([o[0] for o in state["options"]], ["Ponów krok", "Ponów innym wykonawcą…"],
                         "możliwości usługi bez zmian, bez sortowania")
        self.assertTrue(dev["shown"])
        self.assertEqual(dev["title"], "Przyjmij odstępstwo…")
        self.assertFalse(dev["recommended"])
        self.assertEqual(dev["badge"], 0, "nigdy nie polecona")
        self.assertFalse(dev["checked"], "niezaznaczona")
        self.assertTrue(dev["afterOptions"], "po możliwościach usługi")
        self.assertTrue(dev["beforeOther"], "„Inna odpowiedź…” zostaje ostatnia")
        self.assertIn("wynik testów zostaje porażką", dev["effect"])
        self.assertIn("scalenie do gałęzi głównej nadal wymaga osobnej zgody", dev["effect"])
        self.assertFalse(dev["field"], "pole uzasadnienia dopiero po wyborze")
        self.assertFalse(dev["choose"])

    def test_without_reason_choose_is_disabled_with_a_sentence_then_sends_one_retry(self):
        out = self.probe(STATE_PY + DEVIATION_PY + """
click(card("K-8"))
scroll_to_end()
click(at("(" + ALL + ")(page.decisionDialog, 'decisionDeviation')[0]", 0.5, 0.2))
out["picked"] = deviation()
click(named("decisionChoose"))
out["empty"] = len(calls())
set_reason("   ")
out["blank"] = deviation()
set_reason(""" + repr(REASON) + """)
out["ready"] = deviation()
click(at("(" + ALL + ")(page.decisionDialog, 'decisionDeviationReason')[0]", 0.9, 0.5))
key(Qt.Key.Key_Return)
out["enter"] = len(calls())
click(named("decisionChoose"))
out["sent"] = calls()
""", scenario_f(self.data, close_decisions()))
        picked = out["picked"]
        self.assertTrue(picked["checked"])
        self.assertTrue(picked["field"])
        self.assertFalse(picked["choose"])
        self.assertIn("Wpisz uzasadnienie", picked["hint"])
        self.assertEqual(out["empty"], 0)
        self.assertFalse(out["blank"]["choose"], "same spacje to brak uzasadnienia")
        self.assertTrue(out["ready"]["choose"])
        self.assertEqual(out["ready"]["hint"], "")
        self.assertRegex(out["ready"]["counter"], r"^\d+ / 4000 znaków")
        self.assertEqual(out["enter"], 0, "Enter w polu uzasadnienia dodaje linię, nie wysyła")
        self.assertEqual(len(out["sent"]), 1, "jedno ResolveCardStep")
        self.assertEqual(out["sent"][0][:2], ["ResolveCardStep", CARD])
        params = json.loads(out["sent"][0][2])
        self.assertEqual(set(params), {"choice", "step", "answer"})
        self.assertEqual((params["choice"], params["step"]), ("retry", CLOSE_STEP))
        self.assertTrue(params["answer"].startswith("Użytkownik przyjmuje odstępstwo od pełnych testów domknięcia."))
        self.assertTrue(params["answer"].endswith("Uzasadnienie użytkownika: „" + REASON + "”"))
        self.assertIn("Wynik testu zostaje porażką", params["answer"])
        self.assertIn("Scalenie do gałęzi głównej wymaga osobnej zgody", params["answer"])
        self.assertLessEqual(len(params["answer"]), 4000)
        self.assertIn(str(len(params["answer"])), out["ready"]["counter"])

    def test_answer_over_the_service_limit_is_blocked_with_counter(self):
        out = self.probe(STATE_PY + DEVIATION_PY + """
click(card("K-8"))
scroll_to_end()
click(at("(" + ALL + ")(page.decisionDialog, 'decisionDeviation')[0]", 0.5, 0.2))
set_reason("x" * 4000)
out["long"] = deviation()
click(named("decisionChoose"))
out["sent"] = len(calls())
# Długość stałej części odpowiedzi z licznika przy jednym znaku uzasadnienia.
set_reason("y")
base = int(deviation()["counter"].split(" ")[0]) - 1
set_reason("z" * (4000 - base))
out["edge"] = deviation()
set_reason("z" * (4001 - base))
out["over"] = deviation()
""", scenario_f(self.data, close_decisions()))
        self.assertFalse(out["long"]["choose"])
        self.assertIn("za długie", out["long"]["hint"])
        self.assertRegex(out["long"]["counter"], r"^\d{4} / 4000")
        self.assertEqual(out["sent"], 0)
        self.assertTrue(out["edge"]["counter"].startswith("4000 / 4000"), out["edge"]["counter"])
        self.assertTrue(out["edge"]["choose"], "dokładnie 4000 znaków mieści się w limicie usługi")
        self.assertTrue(out["over"]["counter"].startswith("4001 / 4000"))
        self.assertFalse(out["over"]["choose"])

    def test_no_deviation_for_ticket_error_or_close_error_without_retry(self):
        """Decyzja „bez `retry`” jest syntetyczna (usługa zawsze dokłada `retry` do decyzji błędu) — sprawdza
        sam warunek `offered()`, nie stan, który usługa dziś zwraca."""
        without_retry = close_decisions()
        without_retry[0]["options"] = [o for o in without_retry[0]["options"] if o["params"].get("choice") != "retry"]
        for name, items in (("ticket", close_decisions(kind="ticket", ticket="#3")), ("no-retry", without_retry)):
            with self.subTest(name):
                out = self.probe(STATE_PY + DEVIATION_PY + """
click(card("K-8"))
out["state"] = state()
out["dev"] = deviation()
""", scenario_f(self.data, items))
                self.assertTrue(out["state"]["open"])
                self.assertFalse(out["dev"]["shown"])


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class DeviationFullCardTest(ProbeCase):
    def test_your_move_offers_the_same_deviation_and_sends_one_retry(self):
        data = full_card(self.data, journal_close_error())
        data["cardOpen"] = "full"
        root = "page.cardFullView"
        out = self.probe(FULL_STATE_PY + DEVIATION_PY + """
click(card("K-8"))
out["opened"] = full()
out["dev"] = deviation(""" + repr(root) + """, "fullChoose")
scroll_to_end(""" + repr(root) + """)
click(at("(" + ALL + ")(""" + root + """, 'decisionDeviation')[0]", 0.5, 0.2))
out["picked"] = deviation(""" + repr(root) + """, "fullChoose")
set_reason(""" + repr(REASON) + """, """ + repr(root) + """)
out["ready"] = deviation(""" + repr(root) + """, "fullChoose")
click(named("fullChoose"))
out["sent"] = calls()
""", data)
        self.assertTrue(out["opened"]["open"])
        self.assertEqual([o[:3] for o in out["opened"]["options"]][0][0], "Ponów krok")
        dev = out["dev"]
        self.assertTrue(dev["shown"])
        self.assertEqual((dev["recommended"], dev["checked"], dev["badge"]), (False, False, 0))
        self.assertTrue(dev["afterOptions"])
        self.assertFalse(out["picked"]["choose"])
        self.assertIn("Wpisz uzasadnienie", out["picked"]["hint"], out["picked"])
        self.assertTrue(out["ready"]["choose"])
        self.assertEqual(len(out["sent"]), 1)
        self.assertEqual(out["sent"][0][:2], ["ResolveCardStep", CARD])
        params = json.loads(out["sent"][0][2])
        self.assertEqual((params["choice"], params["step"]), ("retry", CLOSE_STEP))
        self.assertTrue(params["answer"].endswith("„" + REASON + "”"))
