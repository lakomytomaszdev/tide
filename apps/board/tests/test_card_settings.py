"""Rzadkie ustawienia pełnej karty bez „Zapisz” i szuflada opisu (PT-134, K-35, US 14).

- ustawienia (kolumna, wstrzymanie, YouTrack, gałąź, archiwizacja) zapisują się od razu po kliknięciu przycisku,
  który nazywa skutek; w pełnej karcie nie ma globalnego „Zapisz / Anuluj”; po zapisie widać zdanie o skutku,
  a błąd usługi pokazuje powód (nic nie przechodzi po cichu),
- archiwizacja i zmiana gałęzi pytają o potwierdzenie opisujące skutek; odmowa (przycisk albo Esc) niczego nie
  wysyła i nie zmienia karty,
- menu „Karta ▾” ma te same akcje co sekcja ustawień,
- szuflada panelu karty z pełnej karty to tylko opis: „Zapisz opis” wysyła sam opis, Esc anuluje bez zapisu.
"""
import json
import os
import re
import shutil
import unittest

from . import REPO
from .decisionfix import full_d
from .test_card_full_view import STATE_PY as FULL_STATE_PY
from .test_view import ProbeCase

CARD_DIR = os.path.join(REPO, "apps", "common", "qml", "card")

STATE_PY = FULL_STATE_PY + r'''
V = "page.cardFullView"

def items(name):
    return "(" + ALL + ")(" + V + ", '" + name + "')"

def texts(name):
    return json.loads(js("JSON.stringify(" + items(name) + ".map(b => b.text))"))

def setting(text):
    return at(items("settingButton") + ".find(b => b.text === " + json.dumps(text) + ")")

def scroll():
    js(V + ".showSettings()")
    wait(80)

def field(name, text):
    js("(function () { " + items(name) + "[0].text = " + json.dumps(text) + " })()")
    wait(40)

def confirm():
    return json.loads(js("JSON.stringify({ open: " + V + ".confirming !== null, "
        "title: (" + items("settingConfirmTitle") + "[0] || {}).text || null, "
        "effect: (" + items("settingConfirmEffect") + "[0] || {}).text || null, "
        "yes: (" + items("settingConfirmYes") + "[0] || {}).text || null })"))

def open_full():
    click(card("K-8"))
    scroll()
'''


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class SettingsTest(ProbeCase):
    def test_buttons_name_the_effect_and_there_is_no_save_or_cancel(self):
        out = self.probe(STATE_PY + """
open_full()
out["buttons"] = texts("settingButton")
out["yt"] = texts("settingYoutrackButton")
out["branch"] = texts("settingBranchButton")
out["labels"] = json.loads(js("JSON.stringify((function all(it, out) { out = out || []; "
    "if (it.visible && typeof it.text === 'string') out.push(it.text); "
    "for (const c of it.children) all(c, out); return out })(" + V + "))"))
""", full_d(self.data))
        self.assertEqual(out["buttons"], ["Przenieś do Backlogu", "Przenieś do Gotowych",
                                          "Wstrzymaj kartę", "Archiwizuj kartę"],
                         "pracująca karta dostaje tylko przeniesienia, na które pozwala Tablica (bez Do zrobienia)")
        self.assertEqual(out["yt"], ["Powiąż ze zgłoszeniem w YouTracku"])
        self.assertEqual(out["branch"], ["Zmień gałąź karty"])
        self.assertFalse([t for t in out["labels"] if re.fullmatch(r"(Zapisz|Anuluj)( .*)?", t)],
                         "w pełnej karcie nie ma globalnego Zapisz / Anuluj")

    def test_move_and_pause_save_at_once_and_show_the_effect(self):
        out = self.probe(STATE_PY + """
open_full()
click(setting("Przenieś do Gotowych"))
click(named("settingConfirmYes"))
wait(80)
out["move"] = calls()
out["moveToast"] = js("page.toast")
click(setting("Wstrzymaj kartę"))
wait(80)
out["pause"] = calls()[-1]
out["pauseToast"] = js("page.toast")
""", full_d(self.data))
        self.assertEqual(out["move"][0][:3], ["MoveCard", "K-8", "done"])
        self.assertEqual(out["moveToast"], "Karta K-8 przeniesiona do „Gotowe”.")
        self.assertEqual(out["pause"], ["SetCardPaused", "K-8", True])
        self.assertEqual(out["pauseToast"], "Karta K-8 wstrzymana. Trwające kroki kończą się.")

    def test_finishing_a_working_card_names_the_real_effect_and_refusal_sends_nothing(self):
        out = self.probe(STATE_PY + """
open_full()
out["finish"] = json.loads(js("JSON.stringify(" + V + ".finish)"))
click(setting("Przenieś do Gotowych"))
out["asked"] = confirm()
out["callsWhileAsking"] = len(calls())
click(named("settingConfirmNo"))
out["afterNo"] = [confirm()["open"], len(calls())]
click(setting("Archiwizuj kartę"))
out["archive"] = confirm()
""", full_d(self.data))
        self.assertGreater(out["finish"]["steps"] + out["finish"]["tickets"], 0)
        self.assertEqual(out["asked"]["title"], "Zakończyć kartę K-8?")
        self.assertIn("przepływ się kończy: zatrzyma", out["asked"]["effect"])
        self.assertEqual(out["asked"]["yes"], "Zakończ kartę")
        self.assertEqual(out["callsWhileAsking"], 0)
        self.assertEqual(out["afterNo"], [False, 0], "odmowa: nic nie wysłano")
        self.assertNotIn("nie są przerywane", out["archive"]["effect"])
        self.assertIn("przepływ się kończy", out["archive"]["effect"])

    def test_youtrack_saves_at_once_names_the_key_and_rejects_a_bad_one(self):
        out = self.probe(STATE_PY + """
open_full()
field("settingYoutrackField", "to zły klucz")
click(at(items("settingYoutrackButton") + "[0]"))
out["badCalls"] = len(calls())
out["badError"] = js("page.cardFullView.settingError")
field("settingYoutrackField", "tom-142")
out["label"] = texts("settingYoutrackButton")
click(at(items("settingYoutrackButton") + "[0]"))
wait(80)
out["calls"] = calls()
out["toast"] = js("page.toast")
""", full_d(self.data))
        self.assertEqual(out["badCalls"], 0, "błędny klucz nic nie wysyła")
        self.assertIn("klucz", out["badError"])
        self.assertEqual(out["label"], ["Powiąż ze zgłoszeniem TOM-142"], "przycisk nazywa skutek")
        self.assertEqual(out["calls"], [["UpdateCard", "K-8", '{"youtrack":"TOM-142"}']])
        self.assertEqual(out["toast"], "Karta K-8 powiązana ze zgłoszeniem TOM-142.")

    def test_tickets_at_once_saves_a_number_and_an_empty_field_restores_the_default(self):
        """PT-375: „Tickety naraz” — liczba 1..6 zapisuje `maxTickets`, spoza zakresu nic nie wysyła, puste pole
        (przy zapisanym nadpisaniu) usuwa je; podpowiedź pokazuje limit obowiązujący z usługi."""
        data = full_d(self.data)
        for card in data["cards"]:
            if card["id"] == "K-8":
                card["maxTickets"] = 3
                card["derived"].setdefault("flow", {})["ticketLimit"] = {"limit": 3, "running": 1, "override": 3}
        out = self.probe(STATE_PY + """
open_full()
out["initial"] = [js(items("settingMaxTicketsField") + "[0].text"), js(items("settingMaxTicketsField") + "[0].placeholderText")]
field("settingMaxTicketsField", "7")
click(at(items("settingMaxTicketsButton") + "[0]"))
out["badCalls"] = len(calls())
out["badError"] = js("page.cardFullView.settingError")
field("settingMaxTicketsField", "x")
click(at(items("settingMaxTicketsButton") + "[0]"))
out["wordCalls"] = len(calls())
field("settingMaxTicketsField", "5")
click(at(items("settingMaxTicketsButton") + "[0]"))
wait(80)
out["saved"] = calls()
out["toast"] = js("page.toast")
field("settingMaxTicketsField", "")
click(at(items("settingMaxTicketsButton") + "[0]"))
wait(80)
out["cleared"] = calls()[1:]
""", data)
        self.assertEqual(out["initial"], ["3", "domyślnie 3"])
        self.assertEqual((out["badCalls"], out["wordCalls"]), (0, 0), "wartość spoza 1..6 i napis nic nie wysyłają")
        self.assertIn("od 1 do 6", out["badError"])
        self.assertEqual(out["saved"], [["UpdateCard", "K-8", '{"maxTickets":5}']])
        self.assertEqual(out["toast"], "Karta K-8: najwyżej 5 tickety naraz. Trwające nie są zatrzymywane.")
        self.assertEqual(out["cleared"], [["UpdateCard", "K-8", '{"maxTickets":""}']])

    def test_linked_card_empty_field_only_focuses_and_unlink_is_explicit(self):
        data = full_d(self.data)
        for card in data["cards"]:
            if card["id"] == "K-8":
                card["youtrack"] = "LEGAL-35"
        out = self.probe(STATE_PY + """
open_full()
out["yt"] = texts("settingYoutrackButton")
click(at(items("settingYoutrackButton") + "[0]"))
wait(60)
out["afterEmptyClick"] = len(calls())
js(V + ".showTop()")
wait(60)
click(named("fullMenu"))
out["menu"] = texts("fullMenuItem")
out["section"] = texts("settingButton") + texts("settingYoutrackButton") + texts("settingBranchButton")
key(Qt.Key.Key_Escape)
scroll()
click(setting("Odłącz od zgłoszenia LEGAL-35"))
wait(80)
out["calls"] = calls()
out["toast"] = js("page.toast")
""", data)
        self.assertEqual(out["yt"], ["Zmień zgłoszenie w YouTracku"], "puste pole nie nazywa odłączenia")
        self.assertEqual(out["afterEmptyClick"], 0, "puste pole niczego nie odłącza")
        self.assertEqual(sorted(t.rstrip("…") for t in out["menu"]), sorted(out["section"]),
                         "menu i sekcja mają te same akcje także przy powiązanej karcie")
        self.assertEqual(out["calls"], [["UpdateCard", "K-8", '{"youtrack":""}']])
        self.assertEqual(out["toast"], "Karta K-8 odłączona od zgłoszenia LEGAL-35.")

    def test_flow_branch_is_not_offered_for_change(self):
        data = full_d(self.data)
        for card in data["cards"]:
            if card["id"] == "K-8":
                card["derived"]["flow"] = dict(card["derived"].get("flow") or {}, cardBranch="card/k-8-x")
        out = self.probe(STATE_PY + """
open_full()
out["locked"] = js(V + ".branchLocked")
out["menu"] = js("JSON.stringify(" + V + ".actions.map(a => a.id))")
""", data)
        self.assertTrue(out["locked"], "gałąź karty z przepływu ustala Tide")
        self.assertNotIn("branch", json.loads(out["menu"]), "zmiany gałęzi nie ma ani w sekcji, ani w menu")

    def test_card_to_review_offers_only_actions_the_board_allows(self):
        data = full_d(self.data)
        for card in data["cards"]:
            if card["id"] == "K-8":
                card["boardColumn"] = "review"
                card["derived"] = dict(card["derived"], column="review")
        out = self.probe(STATE_PY + """
open_full()
out["buttons"] = texts("settingButton")
""", data)
        self.assertEqual(out["buttons"], ["Odeślij do poprawy", "Oznacz jako sprawdzoną"],
                         "bez Backlogu i archiwizacji, których Tablica odmawia")

    def test_service_refusal_is_visible(self):
        out = self.probe(STATE_PY + """
replies.append({"ok": False, "error": "busy", "message": "tablica się zapisuje"})
open_full()
field("settingYoutrackField", "TOM-142")
click(at(items("settingYoutrackButton") + "[0]"))
wait(120)
out["toast"] = js("page.toast")
out["tone"] = js("page.toastTone")
""", full_d(self.data))
        self.assertIn("Nie zapisano", out["toast"])
        self.assertIn("tablica się zapisuje", out["toast"])
        self.assertEqual(out["tone"], "rose")

    def test_branch_change_asks_and_refusal_changes_nothing(self):
        out = self.probe(STATE_PY + """
open_full()
field("settingBranchField", "feat/inna-galaz")
out["label"] = texts("settingBranchButton")
click(at(items("settingBranchButton") + "[0]"))
out["asked"] = confirm()
out["callsWhileAsking"] = len(calls())
click(named("settingConfirmNo"))
out["afterNo"] = [confirm()["open"], len(calls()), js("page.fullOpen")]
click(at(items("settingBranchButton") + "[0]"))
key(Qt.Key.Key_Return)
out["afterEnter"] = [confirm()["open"], len(calls())]
key(Qt.Key.Key_Escape)
out["afterEsc"] = [confirm()["open"], len(calls()), js("page.fullOpen")]
click(at(items("settingBranchButton") + "[0]"))
click(named("settingConfirmYes"))
wait(80)
out["calls"] = calls()
out["toast"] = js("page.toast")
""", full_d(self.data))
        self.assertEqual(out["label"], ["Zmień gałąź na feat/inna-galaz"])
        self.assertTrue(out["asked"]["open"])
        self.assertEqual(out["asked"]["title"], "Zmienić gałąź karty K-8?")
        self.assertIn("feat/inna-galaz", out["asked"]["effect"])
        self.assertIn("bez zmian", out["asked"]["effect"])
        self.assertEqual(out["asked"]["yes"], "Zmień gałąź na feat/inna-galaz")
        self.assertEqual(out["callsWhileAsking"], 0, "przed potwierdzeniem nic nie jest wysłane")
        self.assertEqual(out["afterNo"], [False, 0, True], "odmowa: nic się nie zmienia, pełna karta zostaje")
        self.assertEqual(out["afterEnter"], [True, 0], "Enter nie potwierdza po cichu")
        self.assertEqual(out["afterEsc"], [False, 0, True], "Esc odmawia, a nie wraca do Tablicy")
        self.assertEqual(out["calls"], [["UpdateCard", "K-8", '{"branch":"feat/inna-galaz"}']])
        self.assertEqual(out["toast"], "Gałąź karty K-8: feat/inna-galaz.")

    def test_archive_asks_and_refusal_changes_nothing(self):
        out = self.probe(STATE_PY + """
open_full()
click(setting("Archiwizuj kartę"))
out["asked"] = confirm()
click(named("settingConfirmNo"))
out["afterNo"] = [confirm()["open"], len(calls()), js("page.fullOpen"),
                  js("page.cards.some(c => c.id === 'K-8')")]
click(setting("Archiwizuj kartę"))
key(Qt.Key.Key_Escape)
out["afterEsc"] = [confirm()["open"], len(calls()), js("page.fullOpen")]
click(setting("Archiwizuj kartę"))
click(named("settingConfirmYes"))
wait(80)
out["calls"] = calls()
out["after"] = [js("page.fullOpen"), js("page.toast")]
""", full_d(self.data))
        self.assertEqual(out["asked"]["title"], "Zarchiwizować kartę K-8?")
        self.assertIn("Plik karty i historia zostają", out["asked"]["effect"])
        self.assertEqual(out["asked"]["yes"], "Archiwizuj kartę")
        self.assertEqual(out["afterNo"], [False, 0, True, True], "odmowa: karta zostaje na tablicy, nic nie wysłano")
        self.assertEqual(out["afterEsc"], [False, 0, True])
        self.assertEqual(out["calls"][0][:3], ["MoveCard", "K-8", "archived"])
        self.assertEqual(len(out["calls"]), 1)
        self.assertEqual(out["after"], [False, "Karta K-8 zdjęta z tablicy."])

    def test_card_menu_has_the_same_actions_as_the_settings_section(self):
        out = self.probe(STATE_PY + """
open_full()
section = texts("settingButton") + texts("settingYoutrackButton") + texts("settingBranchButton")
js(V + ".showTop()")
wait(60)
click(named("fullMenu"))
out["open"] = js(V + ".menuOpen")
out["menu"] = texts("fullMenuItem")
out["section"] = section
click(at(items("fullMenuItem") + ".find(b => b.text.indexOf('Backlogu') >= 0)"))
wait(80)
out["calls"] = calls()
out["closed"] = js(V + ".menuOpen")
click(named("fullMenu"))
click(at(items("fullMenuItem") + ".find(b => b.text.indexOf('Archiwizuj') >= 0)"))
out["archiveAsks"] = [confirm()["open"], len(calls())]
key(Qt.Key.Key_Escape)
click(named("fullMenu"))
key(Qt.Key.Key_Escape)
out["escMenu"] = [js(V + ".menuOpen"), js("page.fullOpen")]
""", full_d(self.data))
        self.assertTrue(out["open"])
        strip = lambda t: t.rstrip("…")
        self.assertEqual(sorted(strip(t) for t in out["menu"]), sorted(out["section"]),
                         "menu i sekcja ustawień mają te same akcje")
        self.assertEqual(out["calls"][0][:3], ["MoveCard", "K-8", "backlog"], "pozycja menu zapisuje od razu")
        self.assertFalse(out["closed"])
        self.assertEqual(out["archiveAsks"], [True, 1], "archiwizacja z menu też pyta")
        self.assertEqual(out["escMenu"], [False, True], "Esc zamyka menu, a nie pełną kartę")


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class DescriptionDrawerTest(ProbeCase):
    SCENARIO = STATE_PY + r'''
def drawer():
    return json.loads(js("JSON.stringify((function () { "
        "const all = " + ALL + "; const root = (function r(i) { return i.parent ? r(i.parent) : i })(page); "
        "const panel = all(root, 'cardPanel')[0]; "
        "return { open: page.panelOpen, drawer: page.descriptionDrawer, full: page.fullOpen, "
        "  title: all(panel, 'cardTitle').length, project: all(panel, 'cardProject').length, "
        "  note: all(panel, 'cardNote').length, youtrack: all(panel, 'cardYouTrackField').length, "
        "  archive: all(panel, 'cardArchive').length, column: all(panel, 'cardColumn').length, "
        "  save: all(panel, 'cardSave').map(b => b.text), description: all(panel, 'cardDescription').length } })())"))
def set_description(text):
    js("(function () { const all = " + ALL + "; const root = (function r(i) { return i.parent ? r(i.parent) : i })(page); "
       "all(root, 'cardDescription')[0].text = " + json.dumps(text) + " })()")
    wait(40)
'''

    def test_drawer_edits_only_the_description_and_saves_it(self):
        out = self.probe(self.SCENARIO + """
click(card("K-8"))
click(named("fullDescription"))
out["drawer"] = drawer()
set_description("Nowy opis karty")
out["save"] = drawer()["save"]
click(named("cardSave"))
wait(80)
out["calls"] = calls()
out["after"] = drawer()
""", full_d(self.data))
        d = out["drawer"]
        self.assertEqual((d["open"], d["drawer"], d["full"]), (True, True, True), "szuflada nad pełną kartą")
        self.assertEqual((d["title"], d["project"], d["note"], d["youtrack"], d["archive"], d["column"]),
                         (0, 0, 0, 0, 0, 0), "żadnych ustawień w szufladzie")
        self.assertEqual(d["description"], 1)
        self.assertEqual(out["save"], ["Zapisz opis"])
        self.assertEqual(out["calls"], [["UpdateCard", "K-8", '{"description":"Nowy opis karty"}']])
        self.assertEqual((out["after"]["open"], out["after"]["full"]), (False, True), "po zapisie wraca pełna karta")

    def test_escape_cancels_without_saving(self):
        out = self.probe(self.SCENARIO + """
click(card("K-8"))
click(named("fullDescription"))
set_description("Porzucony opis")
key(Qt.Key.Key_Escape)
out["calls"] = len(calls())
out["after"] = drawer()
click(named("fullDescription"))
out["reopened"] = js("(function () { const all = " + ALL + "; const root = (function r(i) { return i.parent ? r(i.parent) : i })(page); "
                     "return all(root, 'cardDescription')[0].text })()")
""", full_d(self.data))
        self.assertEqual(out["calls"], 0, "Esc nic nie zapisuje")
        self.assertEqual((out["after"]["open"], out["after"]["full"]), (False, True))
        self.assertNotIn("Porzucony", out["reopened"], "porzucony opis nie wraca")

    def test_new_card_drawer_still_adds_a_card(self):
        out = self.probe(self.SCENARIO + """
js("page.newCard('backlog')")
wait(80)
out["drawer"] = drawer()
""", full_d(self.data))
        self.assertEqual(out["drawer"]["save"], ["Dodaj kartę"])
        self.assertEqual((out["drawer"]["drawer"], out["drawer"]["title"]), (False, 1))


class PackageTest(unittest.TestCase):
    def test_settings_logic_is_not_in_the_view_file(self):
        with open(os.path.join(CARD_DIR, "CardFullView.qml"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', source), [])
        self.assertIn('import "settings.js" as Settings', source)


if __name__ == "__main__":
    unittest.main()
