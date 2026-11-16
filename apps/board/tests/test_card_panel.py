"""Duży panel karty na Tablicy (K-128, PT-504): Tide.Sheet z trybami Czytaj | Edytuj.

Sonda działa na atrapie backendu (probe.py): zapisy odpowiadają według scenariusza, nic nie idzie do usługi.
Konflikt YouTracka w trybie czytania sprawdza też `test_youtrack_view`.
"""
import copy
import json
import os
import shutil
import unittest

from . import test_view as base


# Przełącznik trybu to Segmented z Repeaterem: opcje (`cardMode-edit`) leżą w drzewie elementów, nie wśród dzieci QObject.
MODE_PY = base.FIND_PY + '''
def mode(value):
    click(at(FIND + "(page.cardPanel, 'cardMode-" + value + "')"))
'''


def with_description(data, card_id="K-12", text="## Sedno\n\nTreść akapitu opisu.\n\n- punkt pierwszy\n- punkt drugi"):
    data = copy.deepcopy(data)
    next(c for c in data["cards"] if c["id"] == card_id)["description"] = text
    return data


def with_history(data, count, card_id="K-12"):
    data = copy.deepcopy(data)
    history = [{"seq": i, "ts": "2026-11-0%dT10:%02d:00Z" % (1 + i % 8, i % 60), "kindLabel": "krok",
                "text": "Zdarzenie numer %d karty z dłuższym opisem, który może się zawinąć" % i, "actor": "agent",
                "step": "", "old": i < count // 4, "canResume": False} for i in range(count)]
    next(c for c in data["cards"] if c["id"] == card_id).update(
        derived={"column": "waiting", "flow": {"steps": [], "history": history}})
    return data


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class CardPanelModesTest(base.ProbeCase):
    def test_click_opens_reading_with_rendered_description(self):
        out = self.probe(MODE_PY + """
click(card("K-12"))
until(lambda: js("page.panelOpen"))
out["mode"] = js("page.cardPanel.mode")
out["reading"] = js("page.cardPanel.reading")
out["readTitle"] = obj("cardReadTitle").property("visible")
out["titleField"] = obj("cardTitle").property("visible")
out["html"] = obj("cardDescriptionText").property("html")
out["markdown"] = obj("cardDescriptionText").property("markdown")
out["plain"] = js("(function () { const t = page.cardPanel.card.description; return t })()")
""", with_description(self.data))
        self.assertEqual(out["mode"], "read")
        self.assertTrue(out["reading"])
        self.assertTrue(out["readTitle"])
        self.assertFalse(out["titleField"], "formularz jest w trybie Edytuj")
        self.assertTrue(out["markdown"])
        self.assertIn("## Sedno", out["plain"], "źródło opisu zostaje w karcie")
        self.assertIn("Sedno", out["html"])
        self.assertNotIn("## ", out["html"], "nagłówek, a nie dosłowne ## ")
        self.assertNotIn("##", out["html"])

    def test_sheet_is_at_least_sixty_percent_of_the_window(self):
        out = self.probe(MODE_PY + """
window.setWidth(1380)
window.setHeight(700)
wait(150)
click(card("K-12"))
until(lambda: js("page.panelOpen"))
wait(100)
out["window"] = window.width()
out["sheet"] = js("page.cardPanel.sheet.sheetWidth")
out["x"] = js("page.cardPanel.sheet.sheetItem.x")
out["right"] = js("page.cardPanel.sheet.sheetItem.x + page.cardPanel.sheet.sheetItem.width")
out["page"] = js("page.width")
""")
        self.assertEqual(out["window"], 1380)
        self.assertGreaterEqual(out["sheet"], 0.6 * out["window"])
        self.assertGreaterEqual(out["sheet"], 640)
        self.assertEqual(out["right"], out["page"], "szuflada przy prawej krawędzi")

    def test_switch_changes_mode_and_new_card_opens_in_edit(self):
        out = self.probe(MODE_PY + """
click(card("K-12"))
until(lambda: js("page.panelOpen"))
out["start"] = js("page.cardPanel.mode")
mode("edit")
until(lambda: js("page.cardPanel.mode") == "edit")
out["edit"] = [js("page.cardPanel.mode"), obj("cardTitle").property("visible"), obj("cardReadTitle").property("visible"),
               obj("cardSave").property("visible")]
mode("read")
until(lambda: js("page.cardPanel.mode") == "read")
out["back"] = [js("page.cardPanel.mode"), obj("cardTitle").property("visible"), obj("cardReadTitle").property("visible"),
               obj("cardSave").property("visible")]
js("page.closePanel()")
js("page.columnItems()[1].addRequested('todo')")
until(lambda: js("page.panelOpen"))
out["new"] = [js("page.cardPanel.editing"), js("page.cardPanel.formMode"), obj("cardTitle").property("visible"),
              obj("cardMode").property("visible")]
""")
        self.assertEqual(out["start"], "read")
        self.assertEqual(out["edit"], ["edit", True, False, True])
        self.assertEqual(out["back"], ["read", False, True, False])
        self.assertEqual(out["new"], [False, True, True, False], "＋ Karta od razu w edycji, bez przełącznika")

    def test_escape_in_edit_returns_to_reading_without_saving_then_closes(self):
        out = self.probe(MODE_PY + """
click(card("K-12"))
until(lambda: js("page.panelOpen"))
mode("edit")
until(lambda: js("page.cardPanel.mode") == "edit")
obj("cardTitle").setProperty("text", "Porzucony tytuł")
before = len(calls())
key(Qt.Key.Key_Escape)
wait(100)
out["afterFirst"] = [js("page.cardPanel.mode"), js("page.panelOpen"), len(calls()) - before]
mode("edit")
until(lambda: js("page.cardPanel.mode") == "edit")
out["titleAgain"] = obj("cardTitle").property("text")
key(Qt.Key.Key_Escape)
wait(100)
key(Qt.Key.Key_Escape)
wait(100)
out["afterSecond"] = [js("page.panelOpen"), [c[0] for c in calls() if c[0] == "UpdateCard"]]
""")
        self.assertEqual(out["afterFirst"], ["read", True, 0], "Esc w edycji: czytanie, panel otwarty, żadnego zapisu")
        self.assertNotIn("Porzucony", out["titleAgain"], "zmiany odrzucone bez pytania")
        self.assertEqual(out["afterSecond"], [False, []], "Esc w czytaniu zamyka panel, UpdateCard nie wywołano")

    def test_ctrl_enter_saves_without_youtrack_key_and_returns_to_reading(self):
        data = copy.deepcopy(self.data)
        next(c for c in data["cards"] if c["id"] == "K-12")["youtrack"] = "LOG-77"
        out = self.probe(MODE_PY + """
click(card("K-12"))
until(lambda: js("page.panelOpen"))
mode("edit")
until(lambda: js("page.cardPanel.mode") == "edit")
obj("cardTitle").setProperty("text", "Nowy tytuł karty")
key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
until(lambda: calls() and calls()[-1][0] == "UpdateCard")
until(lambda: js("page.cardPanel.mode") == "read")
out["call"] = calls()[-1]
out["mode"] = js("page.cardPanel.mode")
out["open"] = js("page.panelOpen")
# Usługa scala tylko przysłane pola: klucz YouTracka karty zostaje w migawce.
import copy, json as _json
snap = copy.deepcopy(data)
card_data = next(c for c in snap["cards"] if c["id"] == "K-12")
card_data.update(_json.loads(out["call"][2]))
set_snapshot(snap)
wait(100)
out["snapshotKey"] = js("page.snapshot.cards.find(c => c.id === 'K-12').youtrack")
out["snapshotTitle"] = js("page.snapshot.cards.find(c => c.id === 'K-12').title")
""", data)
        member, card_id, payload = out["call"][:3]
        self.assertEqual((member, card_id), ("UpdateCard", "K-12"))
        self.assertEqual(json.loads(payload), {"title": "Nowy tytuł karty"}, "bez klucza youtrack, także pustego")
        self.assertEqual((out["mode"], out["open"]), ("read", True), "po zapisie czytanie, panel otwarty")
        self.assertEqual(out["snapshotKey"], "LOG-77", "klucz zostaje w migawce po zapisie z panelu")
        self.assertEqual(out["snapshotTitle"], "Nowy tytuł karty")

    def test_form_has_no_youtrack_field(self):
        out = self.probe(MODE_PY + """
click(card("K-12"))
until(lambda: js("page.panelOpen"))
mode("edit")
until(lambda: js("page.cardPanel.mode") == "edit")
find = "(function f(it, name) { if (it.objectName === name) return true; for (const c of it.children) if (f(c, name)) return true; return false })"
out["field"] = js(find + "(page.cardPanel, 'cardYouTrackField')")
out["fields"] = [obj(n).property("visible") for n in ("cardTitle", "cardProject", "cardTags", "cardDescription", "cardNote", "cardColumn")]
out["archive"] = obj("cardArchive").property("visible")
""")
        self.assertFalse(out["field"], "obiektu pola „Zgłoszenie w YouTracku” nie ma")
        self.assertEqual(out["fields"], [True] * 6, "pozostałe pola formularza zostają")
        self.assertTrue(out["archive"])


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class CardPanelHistoryTest(base.ProbeCase):
    SCENARIO = """
click(card("K-12"))
until(lambda: js("page.panelOpen"))
wait(200)
out["sheet"] = js("page.cardPanel.sheet.sheetItem.height")
out["frame"] = js("(function f(it) { if (it.objectName === 'fullHistoryFrame') return it.height; "
                  "for (const c of it.children) { const r = f(c); if (r) return r } return 0 })(page.cardPanel)")
out["count"] = js("(function f(it) { if (it.objectName === 'fullHistoryCount') return it.text; "
                  "for (const c of it.children) { const r = f(c); if (r) return r } return '' })(page.cardPanel)")
"""

    def test_history_block_keeps_panel_height_with_many_events(self):
        few = self.probe(self.SCENARIO, with_history(self.data, 3))
        many = self.probe(self.SCENARIO, with_history(self.data, 200))
        self.assertEqual(few["sheet"], many["sheet"], "wysokość panelu nie zależy od liczby zdarzeń")
        self.assertEqual((few["frame"], many["frame"]), (340, 340), "blok historii ma stałą wysokość")
        self.assertIn("3 zdarzenia", few["count"])
        self.assertIn("200 zdarzeń", many["count"])


if __name__ == "__main__":
    unittest.main()
