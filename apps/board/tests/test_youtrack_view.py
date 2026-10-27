"""Widok Tablica i YouTrack (PT-7): logika (board.js) w QJSEngine i sonda w oknie offscreen — znacznik
synchronizacji na karcie, stan zgłoszenia i konflikt w panelu karty, notki przy nagłówku, import zgłoszeń
na stronie „Projekty i tagi”, klucz YouTracka i przełącznik w formularzu projektu.

Sonda działa na backendzie w trybie podglądu (probe.py) — żadnych wywołań usługi ani YouTracka.
"""
import copy
import json
import os
import shutil
import tempfile
import unittest

from . import APP_DIR
from .test_view import GrabTest, ProbeCase


class YouTrackLogicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtQml import QJSEngine
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.engine = QJSEngine()
        with open(os.path.join(APP_DIR, "qml", "board.js"), encoding="utf-8") as fh:
            result = cls.engine.evaluate(fh.read().replace(".pragma library", ""))
        assert not result.isError(), result.toString()

    def js(self, expression):
        value = self.engine.evaluate(f"JSON.stringify({expression})")
        self.assertFalse(value.isError(), value.toString())
        return json.loads(value.toString())

    def test_glyphs_tones_hints_and_links(self):
        self.assertEqual([self.js(f"syncGlyph({{status: '{s}'}})") for s in ("ok", "pending", "offline", "conflict", "error", "off")],
                         ["✓", "⟳", "⟳", "⚠", "⚠", ""])
        self.assertEqual(self.js("syncGlyph(null)"), "")
        self.assertEqual(self.js("syncTone({status: 'conflict'})"), "rose")
        card = ("{youtrack: 'LOG-1', youtrackSync: {status: 'ok', issue: 'LOG-1', issueState: 'Fixed', text: '', "
                "via: 'ticket', label: 'ticket LOG-1', url: 'http://yt:8111/issue/LOG-1'}}")
        self.assertEqual(self.js(f"syncHint({card})"), "YouTrack LOG-1 · w YouTracku „Fixed” · stan według: ticket LOG-1")
        self.assertEqual(self.js(f"youtrackUrl({card})"), "http://yt:8111/issue/LOG-1")
        self.assertEqual(self.js("youtrackUrl({youtrackSync: {url: 'javascript:alert(1)'}})"), "")
        self.assertEqual(self.js("syncHint({youtrack: 'LOG-2', youtrackSync: null})"), "YouTrack LOG-2")

    def test_issue_keys_and_requests(self):
        self.assertEqual(self.js("issueKey(' legal-35 ')"), "LEGAL-35")
        self.assertEqual(self.js("issueKey('http://yt:8111/issue/LOG-232?x=1')"), "LOG-232")
        self.assertEqual(self.js("issueKey('')"), "")
        self.assertIsNone(self.js("issueKey('nie klucz')"))
        card = "{title: 't', description: '', note: '', tags: [], project: '', youtrack: 'LOG-1'}"
        self.assertEqual(self.js(f"updateRequest({card}, {{title: 't', tags: [], project: '', youtrack: 'LOG-1'}})"), {})
        self.assertEqual(self.js(f"updateRequest({card}, {{title: 't', tags: [], project: '', youtrack: ''}})"),
                         {"youtrack": ""})
        self.assertEqual(self.js("createRequest({title: 'x', youtrack: 'LOG-5'})")["youtrack"], "LOG-5")

    def test_notes_import_rows_and_summary(self):
        yt = "{enabled: true, sync: true, available: false, error: 'YouTrack nie odpowiada', pending: 2, conflicts: 1, newIssues: 5, states: {}}"
        notes = self.js(f"youtrackNotes({{youtrack: {yt}}})")
        self.assertEqual([n["kind"] for n in notes], ["conflict", "offline", "new"])
        self.assertEqual(notes[0]["text"], "1 konflikt z YouTrackiem")
        self.assertEqual(notes[1]["text"], "YouTrack niedostępny · 2 czeka")
        self.assertEqual(notes[2]["text"], "5 nowych zgłoszeń w YouTracku")
        self.assertEqual(self.js("youtrackNotes({youtrack: {enabled: false, available: false, conflicts: 0, newIssues: 0}})"), [],
                         "bez projektów z kluczem niedostępność nie ma znaczenia")
        self.assertEqual(self.js("youtrackNotes(null)"), [])
        rows = self.js("importRows([{id: 'LOG-1', summary: 'Eksport', card: 'K-1'}, {id: 'LOG-2', summary: 'Łódź', card: ''}], '')")
        self.assertEqual([r["id"] for r in rows], ["LOG-2", "LOG-1"], "zgłoszenia z kartą na końcu")
        self.assertEqual([r["id"] for r in self.js("importRows([{id: 'LOG-2', summary: 'Łódź'}, {id: 'LOG-3', summary: 'Inne'}], 'ŁÓDŹ')")], ["LOG-2"])
        mixed = "[{id: 'LOG-2', project: 'LOG'}, {id: 'APP5-1', project: 'APP5'}, {id: 'LOG-3', project: 'LOG'}]"
        self.assertEqual([r["id"] for r in self.js(f"importRows({mixed}, '', 'LOG')")], ["LOG-2", "LOG-3"])
        self.assertEqual(self.js(f"importProjects({mixed})"),
                         [{"value": "", "label": "Wszystkie"}, {"value": "APP5", "label": "APP5 · 1"},
                          {"value": "LOG", "label": "LOG · 2"}])
        projects = "[{youtrack: 'LOG', youtrackSync: true, name: 'logtool'}]"
        summary = self.js(f"youtrackSummary({yt}, {projects})")
        self.assertIn("LOG → logtool", summary)
        self.assertIn("YouTrack niedostępny", summary)
        self.assertIn("Do sprawdzenia → Fixed", summary)
        self.assertIn("nie ma klucza", self.js("youtrackSummary({sync: true, available: true}, [])"))


def youtrack_data(base: dict) -> dict:
    data = copy.deepcopy(base)
    for project in data["projects"]:
        project["youtrack"] = "LOG" if project["name"] == "LogViewer" else ""
        project["youtrackSync"] = project["name"] == "LogViewer"
    for card in data["cards"]:
        card["youtrackSync"] = None
        if card["id"] == "K-7":
            card["youtrack"] = "LOG-142"
            card["youtrackSync"] = {"status": "conflict", "issue": "LOG-142", "issueState": "In Progress",
                                    "text": "YouTrack: stan zmieniony ręcznie — w YouTracku „In Progress”, Tide: „Fixed”",
                                    "via": "card", "label": "karta K-7", "url": "http://yt:8111/issue/LOG-142",
                                    "conflict": {"youtrack": "In Progress", "tide": "Fixed"}}
        if card["id"] == "K-13":
            card["youtrackSync"] = {"status": "ok", "issue": "LOG-131", "issueState": "Open", "via": "card",
                                    "text": "zsynchronizowane — w YouTracku „Open”", "label": "karta K-13",
                                    "url": "http://yt:8111/issue/LOG-131", "conflict": None}
    data["youtrack"] = {"enabled": True, "sync": True, "url": "http://yt:8111", "available": True, "error": "",
                        "pending": 0, "conflicts": 1, "newIssues": 2, "importError": "", "states": {},
                        "conflictItems": [{"issue": "LOG-142", "label": "karta K-7", "cards": ["K-7"],
                                           "youtrack": "In Progress", "tide": "Fixed", "url": ""}]}
    return data


ISSUES = {"ok": True, "error": "", "issues": [
    {"id": "LOG-200", "summary": "Nowy eksport", "project": "LOG", "state": "Open", "url": "http://yt:8111/issue/LOG-200",
     "card": "", "boardProject": "logviewer", "boardProjectName": "LogViewer", "new": True},
    {"id": "APP5-3", "summary": "Bez projektu", "project": "APP5", "state": "Submitted", "url": "", "card": "",
     "boardProject": "", "boardProjectName": "", "new": True},
    {"id": "LOG-131", "summary": "Już na tablicy", "project": "LOG", "state": "Open", "url": "", "card": "K-13",
     "boardProject": "logviewer", "boardProjectName": "LogViewer", "new": False}]}


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class YouTrackViewTest(ProbeCase):
    def test_card_chip_panel_conflict_and_link(self):
        out = self.probe("""
out["glyphs"] = [js("page.findCard('K-7').syncGlyph"), js("page.findCard('K-13').syncGlyph"),
                 js("page.findCard('K-12').syncGlyph")]
click(card("K-7"))
until(lambda: obj("cardYouTrackBox").property("visible"))
out["box"] = obj("cardYouTrackBox").property("visible")
out["state"] = obj("cardYouTrackState").property("text")
out["text"] = obj("cardYouTrackText").property("text")
out["field"] = obj("cardYouTrackField").property("text")
click(named("cardYouTrackTake"))
until(lambda: calls() and calls()[-1][0] == "ResolveYouTrack" and js("page.cardPanel.notice"))
out["resolve"] = calls()[-1]
out["notice"] = js("page.cardPanel.notice")
click(named("cardYouTrackOpen"))
until(lambda: calls()[-1][0] == "openLink")
out["link"] = calls()[-1]
obj("cardYouTrackField").setProperty("text", "nie klucz")
click(named("cardSave"))
until(lambda: js("page.cardPanel.error"))
out["badKey"] = js("page.cardPanel.error")
obj("cardYouTrackField").setProperty("text", "log-150")
click(named("cardSave"))
until(lambda: calls()[-1][0] == "UpdateCard")
out["update"] = calls()[-1]
""", youtrack_data(self.data))
        self.assertEqual(out["glyphs"], ["⚠", "✓", ""], "konflikt, zsynchronizowana, karta bez zgłoszenia")
        self.assertTrue(out["box"])
        self.assertEqual(out["state"], "LOG-142 · In Progress")
        self.assertIn("stan zmieniony ręcznie", out["text"])
        self.assertEqual(out["field"], "LOG-142")
        self.assertEqual(out["resolve"], ["ResolveYouTrack", "K-7", "youtrack"])
        self.assertEqual(out["notice"], "Zostaje stan z YouTracka.")
        self.assertEqual(out["link"], ["openLink", "http://yt:8111/issue/LOG-142"])
        self.assertIn("klucz, np. LEGAL-35", out["badKey"])
        self.assertEqual(out["update"][0:2], ["UpdateCard", "K-7"])
        self.assertEqual(json.loads(out["update"][2]), {"youtrack": "LOG-150"})

    def test_header_notes_open_import_and_import_selected(self):
        issues = json.dumps(ISSUES)
        out = self.probe(f"""
out["notes"] = js("page.youtrackNotes.map(n => n.text)")
replies.append(json.loads({issues!r}))
js("page.openImport()")
until(lambda: obj("importList").property("count") == 3)
out["managing"] = js("page.managing")
out["asked"] = calls()[-1]
out["rows"] = obj("importList").property("count")
js("page.managePage.importPanel.project = 'APP5'")
until(lambda: obj("importList").property("count") == 1)
out["dp5Rows"] = obj("importList").property("count")
js("page.managePage.importPanel.project = ''")
until(lambda: obj("importList").property("count") == 3)
js("page.managePage.importPanel.toggle('LOG-200')")
until(lambda: js("page.managePage.importPanel.pickedCount") == 1)
out["picked"] = js("page.managePage.importPanel.pickedCount")
replies.append({{"ok": True, "created": [{{"issue": "LOG-200", "card": "K-16", "project": "logviewer"}}],
                "skipped": [], "commitError": ""}})
replies.append(json.loads({issues!r}))
click(named("importSubmit"))
until(lambda: obj("importResult").property("text") and calls()[-1][0] == "YouTrackIssues" and calls()[-1][1] is False)
out["import"] = [c for c in calls() if c[0] == "ImportYouTrack"][-1]
out["result"] = obj("importResult").property("text")
out["refetch"] = calls()[-1]
out["conflicts"] = js("page.snapshot.youtrack.conflictItems.length")
""", youtrack_data(self.data))
        self.assertEqual(out["notes"], ["1 konflikt z YouTrackiem", "2 nowe zgłoszenia w YouTracku"])
        self.assertTrue(out["managing"])
        self.assertEqual(out["asked"], ["YouTrackIssues", True])
        self.assertEqual(out["rows"], 3)
        self.assertEqual(out["dp5Rows"], 1, "filtr projektu YouTracka")
        self.assertEqual(out["picked"], 1)
        self.assertEqual(json.loads(out["import"][1]), {"issues": ["LOG-200"]})
        self.assertIn("LOG-200 → K-16", out["result"])
        self.assertEqual(out["refetch"], ["YouTrackIssues", False], "po imporcie lista odświeża się z pamięci usługi")
        self.assertEqual(out["conflicts"], 1)

    def test_project_form_sends_youtrack_key_and_toggle(self):
        out = self.probe("""
js("page.managing = true")
slug = js("page.snapshot.projects.find(p => p.name === 'CookApp').slug")
log = js("page.snapshot.projects.find(p => p.name === 'LogViewer').slug")
js(f"page.managePage.openProject = '{slug}'")
until(lambda: js(f"page.managePage.projectRow('{slug}') !== null"))
out["before"] = js(f"page.managePage.projectRow('{slug}').youtrackToggleEnabled")
js(f"page.managePage.projectRow('{slug}').youtrackKey = 'chef'")
until(lambda: js(f"page.managePage.projectRow('{slug}').youtrackToggleEnabled"))
out["toggleEnabled"] = js(f"page.managePage.projectRow('{slug}').youtrackToggleEnabled")
js(f"page.managePage.projectRow('{slug}').save()")
until(lambda: calls() and calls()[-1][0] == "UpsertProject")
out["save"] = calls()[-1]
out["summaries"] = [js(f"page.managePage.projectRow('{log}').youtrackSummary"),
                    js(f"page.managePage.projectRow('{slug}').youtrackSummary")]
out["status"] = obj("youtrackStatus").property("text")
""", youtrack_data(self.data))
        self.assertFalse(out["before"], "bez klucza przełącznik nieaktywny")
        self.assertTrue(out["toggleEnabled"])
        self.assertEqual(out["save"][0], "UpsertProject")
        self.assertEqual(json.loads(out["save"][1])["youtrack"], "CHEF")
        self.assertNotIn("youtrackSync", json.loads(out["save"][1]), "domyślnie włączona — nie wysyłamy")
        self.assertEqual(out["summaries"], ["YouTrack LOG ⟲", ""])
        self.assertIn("LOG → LogViewer", out["status"])


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class YouTrackGrabTest(GrabTest):
    """Zrzuty z YouTrackiem bez ostrzeżeń QML: tablica z notkami, panel karty z konfliktem, strona projektów
    z otwartym importem (lista przychodzi z klucza youtrackIssues danych)."""

    def test_views_render_without_warnings(self):
        data = dict(youtrack_data(self.data), youtrackIssues=ISSUES)
        self.grab("yt-board", data=data)
        self.grab("yt-card", "--preview", "card=K-7", data=data)
        self.grab("yt-manage", "--preview", "manage=logviewer", data=data)
        self.grab("yt-import", "--preview", "import=1", data=data)


if __name__ == "__main__":
    unittest.main()
