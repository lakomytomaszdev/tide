"""Stały pasek górny pełnej karty na Tablicy (K-128, PT-502, makieta S-9 „Rama i pasek”, wariant B).

Sonda offscreen na danych `test_card_full_view` (scenariusze C i D z decisionfix.py):
- po przewinięciu treści do końca pasek i powrót stoją w tym samym miejscu okna,
- powrót jest pierwszym elementem paska od lewej i ma tekst „← Tablica”; klik i Esc wracają na Tablicę,
  a Esc przy otwartym menu ⋯ najpierw zamyka menu,
- zakładki Przegląd, Plan i Historia przełączają treść; Zmiany otwiera ten sam widok zmian co „Zmiany gałęzi”,
  a bez gałęzi karty jest wyłączona z podpowiedzią,
- zakładka Opis pokazuje `## Sedno` jako nagłówek (renderer Tablicy: backend.renderMarkdown), bez dosłownego `## `,
- każda akcja dawnego nagłówka i menu „Karta ▾” jest w pasku albo w menu ⋯ i wywołuje to samo co przed zmianą,
- w widoku karty jest najwyżej jeden przycisk w wariancie głównym.
"""
import json
import os
import shutil
import unittest

from .decisionfix import full_c, full_d
from .test_card_full_view import STATE_PY as FULL_STATE_PY
from .test_view import ProbeCase

BRANCH = "card/k-8-praca"
DESCRIPTION = "## Sedno\n\nKarta ma **jeden** pasek górny.\n\n- powrót po lewej\n- akcje po prawej"

STATE_PY = FULL_STATE_PY + r'''
V = "page.cardFullView"

def items(name):
    return "(" + ALL + ")(" + V + ", '" + name + "')"

def box(name):
    """[x, y, szerokość, wysokość] pierwszego widocznego elementu we współrzędnych okna."""
    return json.loads(js("(function () { const it = " + items(name) + "[0]; if (!it) return 'null'; "
                         "const p = it.mapToItem(null, 0, 0); "
                         "return JSON.stringify([Math.round(p.x), Math.round(p.y), it.width, it.height]) })()"))

def tab(tab_id):
    return at(items("fullTab") + ".find(t => t.tabId === '" + tab_id + "')")

def shown(*names):
    return {n: json.loads(js("JSON.stringify(" + items(n) + ".length > 0)")) for n in names}

def menu_texts():
    return json.loads(js("JSON.stringify((" + ALL + ")(" + V + ", 'fullMenuList')[0] ? "
                         "(function walk(it, out) { out = out || []; "
                         "if (it.visible && typeof it.iconOnly === 'boolean') out.push(it.text); "
                         "for (const c of it.children) walk(c, out); return out })((" + ALL + ")(" + V + ", 'fullMenuList')[0]) : [])"))

def menu_click(text):
    if not js(V + ".menuOpen"):
        click(named("fullMenu"))
    click(at("(function walk(it) { if (it.visible && typeof it.iconOnly === 'boolean' && it.text === "
             + json.dumps(text) + ") return it; for (const c of it.children) { const r = walk(c); if (r) return r } "
             "return null })((" + ALL + ")(" + V + ", 'fullMenuList')[0])"))

PRIMARY = ("(function walk(it, out) { out = out || []; "
           "if (it.visible && typeof it.iconOnly === 'boolean' && it.variant === 'primary') out.push(it.objectName); "
           "for (const c of it.children) walk(c, out); return out })")
'''


def card_k8(data: dict) -> dict:
    return next(c for c in data["cards"] if c["id"] == "K-8")


def with_bar_data(data: dict, branch: str | None = BRANCH, talk: bool = True) -> dict:
    """Karta K-8 z gałęzią przepływu, sprawą źródłową, opisem i (opcjonalnie) rozmową z opiekunem."""
    data = json.loads(json.dumps(data))
    card = card_k8(data)
    card.update(sourceIssue="S-7", sourceIssueVersion=4, description=DESCRIPTION)
    flow = card["derived"].setdefault("flow", {})
    if branch:
        flow["cardBranch"] = branch
    if talk:
        flow["canTalkToCaretaker"] = True
    return data


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class TopBarTest(ProbeCase):
    def test_bar_and_back_keep_their_place_after_scrolling_to_the_end(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
wait(200)
out["before"] = [box("fullTopBar"), box("fullBack")]
js(V + ".showEnd()")
wait(150)
out["contentY"] = js("(" + ALL + ")(" + V + ", 'fullScroll')[0].contentY")
out["after"] = [box("fullTopBar"), box("fullBack")]
out["scroll"] = box("fullScroll")
""", with_bar_data(full_d(self.data)))
        self.assertGreater(out["contentY"], 200, "treść naprawdę się przewinęła")
        self.assertEqual(out["after"], out["before"], "pasek i powrót w tym samym miejscu okna")
        bar = out["before"][0]
        self.assertEqual(bar[3], 52 + 38, "52 px paska i 38 px zakładek")
        self.assertGreaterEqual(out["scroll"][1], bar[1] + bar[3], "przewijana treść zaczyna się pod paskiem")

    def test_back_is_first_from_the_left_and_returns_to_the_board(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
wait(200)
out["back"] = [obj("fullBack").property("text"), box("fullBack")]
out["others"] = {n: box(n) for n in ("fullCrumb", "fullNumber", "fullHeading", "talkToCaretaker", "fullMenu")}
click(named("fullBack"))
out["afterClick"] = js("page.fullOpen")
click(card("K-8"))
key(Qt.Key.Key_Escape)
out["afterEsc"] = js("page.fullOpen")
""", with_bar_data(full_d(self.data)))
        text, back = out["back"]
        self.assertEqual(text, "← Tablica")
        for name, other in out["others"].items():
            self.assertIsNotNone(other, name)
            self.assertGreater(other[0], back[0] + back[2] - 1, f"{name} leży na prawo od powrotu")
        self.assertEqual(back[3], 36, "powrót ma 36 px")
        self.assertFalse(out["afterClick"], "klik w powrót wraca na Tablicę")
        self.assertFalse(out["afterEsc"], "Esc wraca na Tablicę")

    def test_escape_with_open_menu_closes_the_menu_first(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
click(named("fullMenu"))
out["open"] = [js(V + ".menuOpen"), len(menu_texts())]
key(Qt.Key.Key_Escape)
out["first"] = [js(V + ".menuOpen"), js("page.fullOpen")]
key(Qt.Key.Key_Escape)
out["second"] = js("page.fullOpen")
click(card("K-8"))
click(named("fullMenu"))
click(at(V, 0.5, 0.75))   # klik obok menu
out["outside"] = [js(V + ".menuOpen"), js("page.fullOpen"), len(calls())]
""", with_bar_data(full_d(self.data)))
        self.assertTrue(out["open"][0])
        self.assertGreater(out["open"][1], 0)
        self.assertEqual(out["first"], [False, True], "pierwszy Esc zamyka menu, karta zostaje")
        self.assertFalse(out["second"], "drugi Esc wraca na Tablicę")
        self.assertEqual(out["outside"], [False, True, 0], "klik obok zamyka menu bez akcji")

    def test_tabs_switch_content_and_changes_opens_the_branch_diff(self):
        names = ("fullMove", "fullPhases", "fullPlan", "fullNowSection", "fullHistory", "fullSettings",
                 "fullDescriptionTab")
        out = self.probe(STATE_PY + "\nnames = " + repr(names) + """
click(card("K-8"))
wait(150)
out["tabs"] = json.loads(js("JSON.stringify(" + items("fullTab") + ".map(t => [t.text, t.tabId, t.enabled, t.current]))"))
out["overview"] = shown(*names)
click(tab("plan"))
out["plan"] = shown(*names)
click(tab("history"))
out["history"] = shown(*names)
click(tab("overview"))
out["back"] = shown(*names)
click(tab("changes"))
out["changes"] = [js("page.diffOpen"), calls()[-1] if calls() else None, js(V + ".tab")]
key(Qt.Key.Key_Escape)
out["afterDiff"] = [js("page.diffOpen"), js("page.fullOpen")]
""", with_bar_data(full_d(self.data)))
        self.assertEqual([t[0] for t in out["tabs"]], ["Przegląd", "Plan", "Historia", "Opis", "Zmiany"])
        self.assertEqual([t[3] for t in out["tabs"]], [True, False, False, False, False])
        everything = {n: n != "fullDescriptionTab" for n in names}
        self.assertEqual(out["overview"], everything, "Przegląd to dzisiejsza strona")
        self.assertEqual(out["back"], everything)
        self.assertEqual({n for n, v in out["plan"].items() if v}, {"fullPlan"}, "Plan: sam plan")
        self.assertEqual({n for n, v in out["history"].items() if v}, {"fullHistory"}, "Historia: sama historia")
        opened, call, current = out["changes"]
        self.assertTrue(opened, "Zmiany otwierają widok zmian gałęzi")
        self.assertEqual(call[0], "Diff")
        self.assertEqual(json.loads(call[1]), {"source": "card", "card": "K-8"}, "to samo co „Zmiany gałęzi”")
        self.assertEqual(current, "overview", "Zmiany nie są zakładką treści")
        self.assertEqual(out["afterDiff"], [False, True], "Esc zamyka widok zmian i zostawia kartę")

    def test_changes_tab_is_disabled_with_a_hint_without_a_card_branch(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["tab"] = json.loads(js("JSON.stringify(" + items("fullTab") + ".filter(t => t.tabId === 'changes')"
                           ".map(t => [t.enabled, t.hint, t.Accessible.description]))"))
click(tab("changes"))
out["calls"] = calls()
out["diff"] = js("page.diffOpen")
out["menu"] = (click(named("fullMenu")), menu_texts())[1]
""", with_bar_data(full_d(self.data), branch=None))
        enabled, hint, described = out["tab"][0]
        self.assertFalse(enabled)
        self.assertIn("nie ma jeszcze gałęzi", hint)
        self.assertEqual(described, hint, "podpowiedź także dla czytnika ekranu")
        self.assertEqual(out["calls"], [])
        self.assertFalse(out["diff"])
        self.assertNotIn("Zmiany gałęzi", out["menu"], "jak dziś: bez gałęzi nie ma „Zmiany gałęzi”")

    def test_description_tab_renders_the_heading_with_the_board_renderer(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
click(tab("description"))
wait(150)
prose = items("fullDescriptionText") + "[0]"
out["renderer"] = js("typeof " + V + ".markdownRenderer === 'function' && " + prose + ".renderer === " + V + ".markdownRenderer")
# Separatory akapitów QTextDocument (U+2028/U+2029) psułyby ostatnią linię wyniku sondy — zamieniamy je na \\n.
SEP = ".replace(new RegExp('[\\\\u2028\\\\u2029]', 'g'), '\\\\n')"
out["plain"] = js(prose + ".edit.getText(0, " + prose + ".edit.length)" + SEP)
out["html"] = js(prose + ".html" + SEP)
out["shown"] = shown("fullDescriptionTab", "fullMove", "fullPlan", "fullHistory")
""", with_bar_data(full_d(self.data)))
        self.assertTrue(out["renderer"], "Tablica przekazuje backend.renderMarkdown")
        self.assertIn("Sedno", out["plain"])
        self.assertNotIn("## ", out["plain"], "bez dosłownego „## ”")
        self.assertRegex(out["html"], r"<h2[ >][^<]*(<[^>]*>)*[^<]*Sedno", "„Sedno” jako nagłówek")
        self.assertEqual(out["shown"], {"fullDescriptionTab": True, "fullMove": False, "fullPlan": False,
                                        "fullHistory": False})

    def test_every_header_and_card_menu_action_is_in_the_bar_or_menu_and_does_the_same(self):
        out = self.probe(STATE_PY + """
navigation = []
view.navigateRequested.connect(lambda view_id, item: navigation.append([view_id, item]))
click(card("K-8"))
wait(150)
out["actions"] = json.loads(js("JSON.stringify(" + V + ".actions.map(a => [a.id, a.label, !!a.input, !!a.danger]))"))
out["bar"] = obj("talkToCaretaker").property("text")
click(named("fullMenu"))
out["menu"] = menu_texts()
key(Qt.Key.Key_Escape)
effects = {}
click(named("talkToCaretaker"))
effects["talk"] = calls()[-1]
menu_click("Zmiany gałęzi")
effects["diff"] = [calls()[-1], js("page.diffOpen")]
key(Qt.Key.Key_Escape)
menu_click("Edytuj opis")
effects["description"] = [js("page.panelOpen"), js("page.descriptionDrawer"), js("page.fullOpen")]
js("page.closePanel()")
wait(80)
menu_click("Raport i dowody S-7 · wersja 4")
effects["source"] = list(navigation)
before = len(calls())
menu_click("Przenieś do Backlogu")
effects["backlog"] = calls()[before:]
before = len(calls())
menu_click("Przenieś do Gotowych")
effects["done"] = [js(V + ".confirming ? " + V + ".confirming.title : ''"), len(calls()) - before]
click(named("settingConfirmNo"))
menu_click("Powiąż ze zgłoszeniem w YouTracku…")
wait(80)
effects["youtrack"] = [js(items("settingYoutrackField") + "[0].activeFocus"), len(calls()) - before]
before = len(calls())
menu_click("Wstrzymaj kartę")
effects["pause"] = calls()[before:]
before = len(calls())
menu_click("Archiwizuj kartę…")
effects["archive"] = [js(V + ".confirming ? " + V + ".confirming.title : ''"), len(calls()) - before]
key(Qt.Key.Key_Escape)
effects["archiveEsc"] = [js(V + ".confirming === null"), js("page.fullOpen"), len(calls()) - before]
out["effects"] = effects
""", with_bar_data(full_d(self.data)))
        # Dzisiejszy nagłówek: Zmiany gałęzi, Edytuj opis, Raport i dowody, Porozmawiaj z opiekunem; „Karta ▾” —
        # wszystkie `actions` (pozycje z polem albo niszczące z wielokropkiem).
        header = ["Zmiany gałęzi", "Edytuj opis", "Raport i dowody S-7 · wersja 4"]
        card_menu = [label + ("…" if inp or danger else "") for _id, label, inp, danger in out["actions"]]
        self.assertEqual(out["bar"], "Porozmawiaj z opiekunem", "akcja główna paska")
        self.assertEqual(out["menu"], header + card_menu, "menu ⋯: akcje nagłówka i całe „Karta ▾”")
        self.assertEqual([a[0] for a in out["actions"]],
                         ["move:backlog", "move:done", "youtrack", "pause", "archive"])
        e = out["effects"]
        self.assertEqual(e["talk"], ["TalkToCaretaker", "K-8", "{}"])
        self.assertEqual(e["diff"][0][0], "Diff")
        self.assertEqual(json.loads(e["diff"][0][1]), {"source": "card", "card": "K-8"})
        self.assertTrue(e["diff"][1])
        self.assertEqual(e["description"], [True, True, True], "szuflada opisu nad pełną kartą")
        self.assertEqual(e["source"], [["issues", "S-7"]])
        self.assertEqual([c[:3] for c in e["backlog"]], [["MoveCard", "K-8", "backlog"]])
        self.assertEqual(e["done"], ["Zakończyć kartę K-8?", 0], "zakończenie pyta o potwierdzenie ze skutkiem")
        self.assertEqual(e["youtrack"], [True, 0], "pozycja z polem tylko daje fokus polu")
        self.assertEqual(e["pause"], [["SetCardPaused", "K-8", True]])
        self.assertEqual(e["archive"], ["Zarchiwizować kartę K-8?", 0], "archiwizacja pyta o potwierdzenie")
        self.assertEqual(e["archiveEsc"], [True, True, 0], "Esc odmawia, niczego nie wysyła")

    def test_at_most_one_primary_button_in_the_card_view(self):
        data = with_bar_data(full_c(self.data))
        data["cardOpen"] = "full"
        out = self.probe(STATE_PY + """
click(card("K-8"))
wait(150)
out["decision"] = js("JSON.stringify(" + PRIMARY + "(" + V + "))")
js(V + ".askArchive()")
wait(80)
out["confirm"] = js("JSON.stringify(" + PRIMARY + "(" + V + "))")
key(Qt.Key.Key_Escape)
js("(function () { " + V + ".confirming = {kind: 'update', params: {}, title: 'T', effect: 'E', yes: 'Tak'} })()")
wait(80)
out["confirmUpdate"] = js("JSON.stringify(" + PRIMARY + "(" + V + "))")
""", data)
        self.assertEqual(json.loads(out["decision"]), ["fullChoose"], "decyzja: jedyny główny to „Wybierz”")
        self.assertEqual(json.loads(out["confirm"]), [], "archiwizacja jest niszcząca, nie główna")
        self.assertEqual(json.loads(out["confirmUpdate"]), ["settingConfirmYes"],
                         "pod pytaniem główny jest tylko przycisk pytania")

    def test_at_most_one_primary_button_without_a_decision(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
wait(150)
out["primary"] = js("JSON.stringify(" + PRIMARY + "(" + V + "))")
""", with_bar_data(full_d(self.data)))
        self.assertLessEqual(len(json.loads(out["primary"])), 1)


if __name__ == "__main__":
    unittest.main()
