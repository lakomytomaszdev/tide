"""Widok Raporty okna Tide (apps/reports).

- pakiet okna Tide: moduł widoku w views/reports (backend.py, QML i biblioteka z Tokens.qml), bez kolorów
  wpisanych na sztywno, każdy tekst jako zwykły tekst,
- logika reports.js w qml6: etykiety dni, liczby z odmianą, linia pod tytułem, „Czeka na ciebie”, sekcje,
- ReportsView w qml6 (offscreen) z atrapą backendu na syntetycznych raportach (fixture.py): lista dni
  i kropka nieotwartego raportu, kolejność kart, sekcje panelu, „Kopiuj jako tekst”, przeczytanie po
  obejrzeniu (tylko najnowszy, tylko na wierzchu aktywnego okna), postęp „Wygeneruj teraz”, pusty stan,
  showItem, raport sprzed report.json, HTML w tekstach raportu zostaje tekstem,
- backend z danymi z pliku (bez magistrali): tagi z tablicy, plakietka, przeczytanie w pamięci, schowek,
- backend na prywatnej magistrali z prawdziwym tide-daemon (świat z daemon/tests/reportfix.py): plakietka
  „nowy”, Days()/Day(), MarkRead() gasi kropkę, OpenDay() przez atrapę xdg-open, Generate() z postępem,
- zrzuty okna Tide z --data (bez D-Bus) bez ostrzeżeń QML.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from . import APP_DIR, REPO, fixture
from daemon.tests.fakebin import calls, files_under, make_env
from daemon.tests.privatebus import PrivateBus, requires_dbus, wait_until

QML_PROBLEMS = r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|is not defined|Cannot read|" \
               r"unavailable|Invalid property"
TIDE_DIR = os.path.join(REPO, "apps", "tide")


def setUpModule():
    # Schowek (copyText) wymaga QGuiApplication — przed QCoreApplication z privatebus.app().
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtGui import QGuiApplication
    if QCoreApplication.instance() is None:
        global _APP
        _APP = QGuiApplication([])


def stage(out: str) -> str:
    subprocess.run(["/usr/bin/python3", os.path.join(REPO, "widgets", "stage.py"), "--app", TIDE_DIR, out], check=True,
                   capture_output=True)
    return out


def run_qml(path: str) -> str:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", TZ=fixture.TZ)
    result = subprocess.run(["qml6", path], env=env, capture_output=True, text=True, timeout=60)
    return result.stderr


def warned(log: str, tag: str) -> str:
    match = re.search(rf"{tag} (.*)$", log, re.M)
    return match.group(1).strip() if match else f"<brak {tag}>\n{log[-3000:]}"


def problems(log: str) -> list:
    return [line for line in log.splitlines() if re.search(QML_PROBLEMS, line)]


def load_backend():
    spec = importlib.util.spec_from_file_location("tide_reports_view", os.path.join(APP_DIR, "backend.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def day_payloads(sample: dict) -> dict:
    """Odpowiedzi Day(doba) dla atrapy backendu w QML (pola ok, latest, unread jak z usługi)."""
    days = sample["days"]
    module = load_backend()
    tags = module.board_tags(sample.get("board"))
    out = {}
    for day, report in sample["reports"].items():
        report = module.with_tags(report, tags)
        latest = day == days["latest"]
        out[day] = {"legacy": False, **report, "ok": True, "day": day, "latest": latest,
                    "unread": latest and days["unread"]}
    return out


class PackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-reports-pkg-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_package_contains_view_module_and_library(self):
        files = files_under(os.path.join(self.pkg, "views", "reports"))
        for name in ("backend.py", "qml/ReportsView.qml", "qml/ProjectCard.qml", "qml/DailyPanel.qml",
                     "qml/DailyItem.qml", "qml/DayItem.qml", "qml/OutsideSection.qml", "qml/reports.js",
                     "qml/tide/Tokens.qml", "qml/tide/Theme.qml", "qml/tide/qmldir"):
            self.assertIn(name, files)
        self.assertFalse([f for f in files if f.startswith("tests/") or f.endswith("__init__.py")])

    def test_no_hardcoded_colors(self):
        for name in os.listdir(os.path.join(APP_DIR, "qml")):
            with open(os.path.join(APP_DIR, "qml", name), encoding="utf-8") as fh:
                self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', fh.read()), [], name)

    def test_every_text_is_plain(self):
        """Teksty raportu przychodzą z gita, dzienników i modelu — nigdy jako HTML/StyledText."""
        for name in os.listdir(os.path.join(APP_DIR, "qml")):
            if not name.endswith(".qml"):
                continue
            with open(os.path.join(APP_DIR, "qml", name), encoding="utf-8") as fh:
                source = fh.read()
            blocks = re.findall(r"\n(\s*)Text \{\n(.*?)\n\1\}", source, re.S)
            for _indent, body in blocks:
                self.assertIn("textFormat: Text.PlainText", body, f"{name}: {body[:80]}")
            self.assertNotIn("RichText", source, name)
            self.assertNotIn("StyledText", source, name)
            self.assertNotIn("WebEngine", source, name)


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class QmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-reports-qml-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def harness(self, name: str, body: str) -> str:
        path = os.path.join(self.pkg, "views", "reports", "qml", name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return run_qml(path)

    def stub(self, sample: dict, extra: str = "") -> str:
        """Atrapa backendu z danymi z fixture.py: selectDay podaje Day() natychmiast, akcje zapisują wywołania."""
        return """
    QtObject {
        id: stub
        property var reports: (%(reports)s)
        property string daysJson: %(days)s
        property string dayJson: "{}"
        property string selectedDay: ""
        property bool available: true
        property bool generating: false
        property bool preview: false
        property string homeDir: "/home/ja"
        property var calls: []
        property string copied: ""
        signal generateFinished(string path)
        signal openFinished(bool ok)
        function load() { calls.push("load"); if (JSON.parse(daysJson).latest) selectDay(JSON.parse(daysJson).latest) }
        function reload() { calls.push("reload") }
        function selectDay(day) {
            calls.push("day:" + day); selectedDay = day
            dayJson = JSON.stringify(reports[day] || { ok: false, error: "missing", day: day })
        }
        function generate() { calls.push("generate"); generating = true }
        function openDay(day, project) { calls.push("open:" + day + "/" + project) }
        function markRead(day) {
            calls.push("read:" + day)
            const d = JSON.parse(daysJson); d.unread = false; daysJson = JSON.stringify(d)
            const r = JSON.parse(dayJson); r.unread = false; dayJson = JSON.stringify(r)
        }
        function copyText(text) { copied = text; return true }
        %(extra)s
    }""" % {"reports": json.dumps(day_payloads(sample), ensure_ascii=False),
            "days": json.dumps(json.dumps(sample["days"], ensure_ascii=False), ensure_ascii=False), "extra": extra}

    def late_stub(self, sample: dict) -> str:
        """Atrapa, która zna listę dni dopiero po load() — jak backend przed pierwszym pokazaniem widoku."""
        stub = self.stub(sample).replace("id: stub", "id: earlyStub")
        stub = stub.replace("property string daysJson: ", 'property string daysJson: "{}"\n        property string allDays: ', 1)
        return re.sub(r"function load\(\) \{.*?\n", 'function load() { calls.push("load"); daysJson = allDays; '
                      'if (!selectedDay) selectDay(JSON.parse(allDays).latest) }\n', stub, count=1)

    FIND = """
    // Drzewo elementów: dzieci i contentItem (Flickable, ListView) — każdy element raz.
    function walk(item, visit, seen) {
        if (!item || seen.indexOf(item) >= 0) return
        seen.push(item)
        visit(item)
        for (const child of item.children) walk(child, visit, seen)
        if (item.contentItem) walk(item.contentItem, visit, seen)
    }
    function find(item, name) {
        let hit = null
        walk(item, it => { if (!hit && it.objectName === name) hit = it }, [])
        return hit
    }
    function all(item, name, out) {
        walk(item, it => { if (it.objectName === name && it.visible) out.push(it) }, [])
        return out
    }
    function texts(item, name) { return all(item, name, []).map(t => t.text) }
    function dayLabels(v) {
        const list = find(v, "dayList")
        list.forceLayout()
        const out = []
        for (let i = 0; i < list.count; i++) {
            const it = list.itemAtIndex(i)
            out.push(it ? it.label + (it.fresh ? "•" : "") : "?")
        }
        return out
    }
"""

    def test_logic(self):
        log = self.harness("LogicSmoke.qml", """import QtQuick
import "reports.js" as Logic
Item {
    Component.onCompleted: {
        console.warn("LABEL " + [Logic.dayLabel("2026-10-27", "2026-10-27"), Logic.dayLabel("2026-10-25", "2026-10-27"),
                                 Logic.dayLabel("zła", "2026-10-27")].join("|"))
        console.warn("SUB " + [Logic.daySub({ generatedAt: %(t0)d, projects: 5 }), Logic.daySub({ generatedAt: %(t0)d, projects: 1 }),
                               Logic.daySub({ generatedAt: %(t0)d, projects: 22 }), Logic.daySub({ generatedAt: %(t0)d, projects: null })].join("|"))
        console.warn("TITLE " + Logic.dayTitle({ title: "Wtorek, 27 października 2026" }, "2026-10-27") + "|"
                     + Logic.dayTitle({ title: "Środa, 30 grudnia 2025" }, "2026-01-02"))
        const sum = r => Logic.summaryParts(r).map(p => p.text + (p.tone ? "!" : "")).join(" · ")
        console.warn("SUMMARY " + [sum({ ok: true, since: "Od poniedziałku 07:00", counts: { projects: 5, waiting: 2 } }),
                                   sum({ ok: true, since: "Od 05:00", counts: { projects: 1, waiting: 0 } }),
                                   sum({ ok: true, counts: { projects: 12, waiting: 5 } }),
                                   sum({ ok: true, legacy: true, counts: { projects: 2 } }),
                                   sum({ ok: false })].join(" ¦ "))
        const p = { waiting: [{ ref: "TOM-1", text: "a?" }, { ref: "", text: "b" }, { ref: "K-2", text: "c" },
                              { text: "i 4 więcej", more: true }] }
        console.warn("WAIT " + Logic.waitText(p, 2) + "|" + Logic.waitText({ waiting: [] }, 2) + "|" + Logic.waitText(null))
        console.warn("SECTIONS " + Logic.sections({ done: [1], next: [1, 2] }).map(s => s.title + ":" + s.items.length + ":" + s.tint).join(","))
        console.warn("FIND " + [Logic.findProject([{ slug: "a", name: "A" }, { slug: "b", name: "B" }], "b").name,
                                Logic.findProject([{ slug: "a", name: "A" }], "A").slug,
                                Logic.findProject([{ slug: "a", name: "A" }], "x").slug,
                                Logic.findProject([], "a")].join("|"))
        console.warn("SPLIT " + JSON.stringify(Logic.splitItem("2026-10-27/plasma-tide")) + JSON.stringify(Logic.splitItem("2026-10-27"))
                     + JSON.stringify(Logic.splitItem("../x")))
        console.warn("UNREAD " + [Logic.unreadDay("d2", { unread: true, latest: "d2" }), Logic.unreadDay("d1", { unread: true, latest: "d2" }),
                                  Logic.unreadDay("d2", { unread: false, latest: "d2" })].join("|"))
        console.warn("OUTSIDE " + Logic.outsideLabel([{ state: "waiting" }, { state: "done" }]) + "|" + Logic.outsideLabel([{ state: "done" }]))
        console.warn("PATH " + Logic.tildePath("/home/ja/x", "/home/ja") + "|" + Logic.tildePath("/home/jan/x", "/home/ja"))
        console.warn("PARSE " + JSON.stringify(Logic.parse("[1]", {})) + JSON.stringify(Logic.parse("nie", [])))
        Qt.quit()
    }
}
""" % {"t0": fixture.T0})
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "LABEL"), "Dziś · wt 27.10|niedz 25.10|zła")
        self.assertEqual(warned(log, "SUB"), "07:00 · 5 projektów|07:00 · 1 projekt|07:00 · 22 projekty|07:00")
        self.assertEqual(warned(log, "TITLE"), "Wtorek, 27 października|Środa, 30 grudnia 2025")
        self.assertEqual(warned(log, "SUMMARY"),
                         "Od poniedziałku 07:00 · 5 projektów z ruchem · 2 rzeczy czekają na ciebie! ¦ "
                         "Od 05:00 · 1 projekt z ruchem · nic na ciebie nie czeka ¦ "
                         "12 projektów z ruchem · 5 rzeczy czeka na ciebie! ¦ "
                         "2 projekty z ruchem · raport sprzed widoku w oknie — szczegóły w przeglądarce ¦")
        self.assertEqual(warned(log, "WAIT"), "TOM-1: a? · b · i 1 więcej||")
        self.assertEqual(warned(log, "SECTIONS"),
                         "Zrobione:1:mint,W toku:0:teal,Czeka na mnie:0:peach,Uwaga:0:butter,Dalej:2:textMuted")
        self.assertEqual(warned(log, "FIND"), "B|a|a|", "pusta lista — null")
        self.assertEqual(warned(log, "SPLIT"),
                         '{"day":"2026-10-27","project":"plasma-tide"}{"day":"2026-10-27","project":""}'
                         '{"day":"","project":""}')
        self.assertEqual(warned(log, "UNREAD"), "true|false|false")
        self.assertEqual(warned(log, "OUTSIDE"), "2 sesje · 1 czeka|1 sesja")
        self.assertEqual(warned(log, "PATH"), "~/x|/home/jan/x")
        self.assertEqual(warned(log, "PARSE"), "{}[]")

    def test_view_days_cards_panel_copy_and_read(self):
        log = self.harness("ViewSmoke.qml", """import QtQuick
Item {
    width: 1440; height: 900
    %(stub)s
    ReportsView { id: view; anchors.fill: parent; backend: stub; windowActive: true; readAfterMs: 50 }
    %(find)s
    Component.onCompleted: {
        console.warn("CALLS " + stub.calls.join(","))
        console.warn("DAYS " + dayLabels(view).join("|"))
        console.warn("FRESH " + all(view, "freshDot", []).length)
        console.warn("TITLE " + find(view, "dayTitle").text)
        console.warn("CARDS " + texts(view, "projectName").join("|"))
        console.warn("TAGS " + texts(view, "tagChip").join("|"))
        console.warn("WAITS " + texts(view, "waitText").join(" ¦ "))
        console.warn("SELECTED " + view.selectedProject.name + "|" + find(view, "panelTitle").text)
        console.warn("SECTIONTITLES " + texts(view.panel, "sectionTitle").join("|"))
        console.warn("ITEMS " + texts(view.panel, "itemText").join(" ¦ "))
        console.warn("NEXT " + texts(view.panel, "itemNext").join("|"))
        console.warn("THEME " + find(view.panel, "panelTheme").text)
        console.warn("OUTSIDE " + find(view, "outsideLabel").text + "|" + all(view, "outsideTitle", []).length)
        view.outsideSection.expanded = true
        console.warn("OUTSIDE2 " + texts(view, "outsideTitle").join("|"))
        // Wybór innego projektu i kopiowanie jego „Na daily”.
        view.selectedKey = "plasma-tide"
        find(view.panel, "copyButton").clicked()
        console.warn("COPY " + JSON.stringify(stub.copied.split("\\n").slice(0, 4)) + "|" + find(view.panel, "copyButton").text)
        console.warn("CHORES " + find(view.panel, "choresToggle").text + "|" + texts(view.panel, "itemText").length)
        view.panel.pageRequested("plasma-tide")
        view.backend.openDay(view.selectedDay, "")
        console.warn("OPEN " + stub.calls.filter(c => c.startsWith("open:")).join(","))
        Qt.callLater(function () {
            // Przeczytanie po READ_AFTER_MS (tu 50 ms), tylko najnowszy raport.
            readCheck.start()
        })
    }
    Timer {
        id: readCheck
        interval: 300
        onTriggered: {
            console.warn("READ " + stub.calls.filter(c => c.startsWith("read:")).join(",") + "|" + dayLabels(view)[0])
            // Starsza doba: bez przeczytania, panel tego samego projektu, jeśli jest w raporcie.
            view.selectDay("2026-10-26")
            console.warn("OLDER " + find(view, "dayTitle").text + "|" + texts(view, "projectName").join(",") + "|"
                         + view.selectedProject.name + "|" + texts(view, "waitText").length)
            view.step(1)
            console.warn("STEP " + view.selectedDay)
            view.step(-5)
            console.warn("STEPUP " + view.selectedDay)
            Qt.quit()
        }
    }
}
""" % {"stub": self.stub(fixture.sample()), "find": self.FIND})
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "CALLS"), "load,day:2026-10-27")
        self.assertEqual(warned(log, "DAYS"), "Dziś · wt 27.10•|pon 26.10|niedz 25.10|sob 24.10|pt 23.10|czw 22.10")
        self.assertEqual(warned(log, "FRESH"), "1", "kropka tylko przy najnowszym nieotwartym raporcie")
        self.assertEqual(warned(log, "TITLE"), "Wtorek, 27 października")
        self.assertEqual(warned(log, "CARDS"), "log-explorer|mobile-app|plasma-tide|team-v2|cookapp")
        self.assertEqual(warned(log, "WAITS"),
                         "Czeka na ciebie: TOM-146: czy warunek ma działać też na archiwalnych logach? · "
                         "K-12: poprawka gotowa do scalenia z master. ¦ "
                         "Czeka na ciebie: K-19: filtr po dacie w pierwszej wersji czy później?")
        self.assertEqual(warned(log, "SELECTED"), "log-explorer|log-explorer", "pierwszy projekt (czeka) wybrany")
        self.assertEqual(warned(log, "SECTIONTITLES"), "Zrobione|W toku|Czeka na mnie|Dalej")
        self.assertEqual(warned(log, "ITEMS"),
                         "K-14: Wyniki wyszukiwania da się pobrać jako plik CSV. Zespół otworzy je w arkuszu bez "
                         "kopiowania. ¦ "
                         "Wyszukiwanie fraz nie zwraca już duplikatów; poprawka przeszła review bez uwag. ¦ "
                         "Test liczby rekordów nie zależy już od czasu wykonania. ¦ "
                         "K-13: Wartość w logu: 5 z 9 ticketów, teraz warunki na kolumnach przebiegu. ¦ "
                         "TOM-146: czy warunek ma działać też na archiwalnych logach? ¦ "
                         "K-12: poprawka gotowa do scalenia z master. ¦ TOM-147: warunki w zapisanych zapytaniach.")
        self.assertEqual(warned(log, "NEXT"), "dalej: warunki w zapisanych zapytaniach")
        self.assertEqual(warned(log, "THEME"), "Filtrowanie logów po wartościach: warunki na kolumnach i zapisane zapytania.")
        self.assertEqual(warned(log, "OUTSIDE"), "Poza projektami: 2 sesje · 1 czeka|0", "zwinięte")
        self.assertEqual(warned(log, "OUTSIDE2"), "Konfiguracja KDE|Próba czcionek", "czekająca pierwsza")
        self.assertEqual(warned(log, "COPY"),
                         '["Na daily · wtorek, 27 października 2026","","plasma-tide","Zrobione:"]|Skopiowano ✓')
        self.assertEqual(warned(log, "CHORES"), "▸ Porządki · 1|6", "porządki zwinięte")
        self.assertEqual(warned(log, "OPEN"), "open:2026-10-27/plasma-tide,open:2026-10-27/")
        self.assertEqual(warned(log, "READ"), "read:2026-10-27|Dziś · wt 27.10", "obejrzenie najnowszego raportu gasi kropkę")
        self.assertEqual(warned(log, "OLDER"),
                         "Poniedziałek, 26 października|plasma-tide,cookapp|plasma-tide|0")
        self.assertEqual(warned(log, "STEP"), "2026-10-25")
        self.assertEqual(warned(log, "STEPUP"), "2026-10-27")
        self.assertNotIn("read:2026-10-26", log)
        self.assertEqual(warned(log, "TAGS"), "praca|praca|własne|własne|własne", "tagi projektów z tablicy")

    def test_brief_now_finished_stale_and_chores(self):
        """„Dziś w skrócie” z „Czeka teraz” (na żywo), „Skończone dziś” na karcie i w panelu z ręcznym
        sprawdzeniem, nieaktualne „Czeka na mnie” przekreślone, projekty z samymi porządkami w jednej linii."""
        log = self.harness("LivingSmoke.qml", """import QtQuick
Item {
    width: 1440; height: 900
    %(stub)s
    ReportsView { id: view; anchors.fill: parent; backend: stub; windowActive: false }
    %(find)s
    Component.onCompleted: {
        console.warn("BRIEF " + texts(view, "briefLine").join(" ¦ "))
        console.warn("NOW " + find(view, "nowTitle").text + "|" + texts(view, "nowLine").join(" ¦ "))
        console.warn("CARDFINISHED " + texts(view, "finishedText").join(" ¦ "))
        console.warn("PANELFINISHED " + find(view.panel, "finishedTitle").text + "|"
                     + all(view.panel, "finishedItem", []).length + "|" + texts(view.panel, "itemCheck").join(" ¦ "))
        console.warn("CHORELINE " + find(view, "choreLine").text + "|" + all(view, "choreProject", []).length)
        find(view, "choreLine").forceActiveFocus()
        view.showChoreProjects = true
        console.warn("CHOREOPEN " + texts(view, "choreProject").join(" ¦ "))
        view.selectedKey = "mobile-app"
        console.warn("STALE " + texts(view.panel, "itemStale").join("|") + "|"
                     + all(view.panel, "itemText", []).filter(t => t.font.strikeout).map(t => t.text).join("|"))
        console.warn("MOBILEWAIT " + texts(view, "waitText").join(" ¦ "))
        view.selectedKey = "cookapp"
        console.warn("ATTENTION " + find(view.panel, "section-attention").visible + "|"
                     + texts(view.panel, "sectionTitle").join(",") + "|" + texts(view, "attentionCount").join(","))
        view.selectedKey = "plasma-tide"
        console.warn("NOATTENTION " + find(view.panel, "section-attention").visible)
        view.selectDay("2026-10-26")
        console.warn("OLDNOW " + find(view, "nowBox").visible + "|" + texts(view, "briefLine").length + "|"
                     + find(view, "choreProjects").visible)
        Qt.quit()
    }
}
""" % {"stub": self.stub(fixture.sample()), "find": self.FIND})
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "BRIEF"), " ¦ ".join(fixture.today_report()["brief"]))
        self.assertEqual(warned(log, "NOW"),
                         "Czeka teraz · 1|log-explorer — pyta: „czy warunek ma działać też na archiwalnych logach?”")
        self.assertEqual(warned(log, "CARDFINISHED"), "Skończone dziś: Wyniki wyszukiwania da się pobrać jako CSV")
        self.assertEqual(warned(log, "PANELFINISHED"),
                         "Skończone dziś|1|Sprawdź ręcznie: pobierz CSV i otwórz w arkuszu; polskie znaki w nagłówkach")
        self.assertEqual(warned(log, "CHORELINE"), "▸ Porządki: QuotaApp, dev-dashboard|0")
        self.assertEqual(warned(log, "CHOREOPEN"), "QuotaApp: Instrukcje dla agentów uporządkowane. ¦ "
                                                   "dev-dashboard: Zadania przeniesione do nowego trackera.")
        self.assertEqual(warned(log, "STALE"), "już nieaktualne — agent na to nie czeka|Wydanie: agent pyta: „Pushować?”")
        self.assertEqual(warned(log, "MOBILEWAIT").count("Pushować"), 0, "nieaktualne nie na karcie")
        self.assertEqual(warned(log, "OLDNOW"), "false|2|false", "starsza doba — bez „Czeka teraz”")
        self.assertEqual(warned(log, "ATTENTION"), "true|Zrobione,W toku,Czeka na mnie,Uwaga,Dalej| · 1 uwaga",
                         "konflikt numerów w „Uwaga”, nie w „Czeka na mnie”")
        self.assertEqual(warned(log, "NOATTENTION"), "false", "pusta „Uwaga” ukryta")

    def test_read_needs_active_view_and_window(self):
        """Widok w tle albo okno bez fokusu — raport nie jest „przeczytany”; po wejściu na wierzch tak."""
        log = self.harness("ReadSmoke.qml", """import QtQuick
Item {
    width: 1440; height: 900
    %(stub)s
    ReportsView { id: view; anchors.fill: parent; backend: stub; windowActive: false; active: true; readAfterMs: 30 }
    Timer {
        running: true; interval: 200
        onTriggered: {
            console.warn("UNFOCUSED " + stub.calls.filter(c => c.startsWith("read:")).length)
            view.windowActive = true
            view.active = false
            later.start()
        }
    }
    Timer {
        id: later; interval: 200
        onTriggered: {
            console.warn("HIDDEN " + stub.calls.filter(c => c.startsWith("read:")).length)
            view.active = true
            last.start()
        }
    }
    Timer {
        id: last; interval: 200
        onTriggered: { console.warn("SHOWN " + stub.calls.filter(c => c.startsWith("read:")).join(",")); Qt.quit() }
    }
}
""" % {"stub": self.stub(fixture.sample())})
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "UNFOCUSED"), "0")
        self.assertEqual(warned(log, "HIDDEN"), "0")
        self.assertEqual(warned(log, "SHOWN"), "read:2026-10-27")

    def test_legacy_latest_report_is_not_marked_read(self):
        """Najnowszy raport sprzed report.json (bez „Na daily” w oknie) — kropka zostaje do otwarcia w przeglądarce."""
        sample = fixture.sample()
        sample["reports"]["2026-10-27"] = dict(sample["reports"]["2026-10-24"])
        log = self.harness("LegacyReadSmoke.qml", """import QtQuick
Item {
    width: 1440; height: 900
    %(stub)s
    ReportsView { id: view; anchors.fill: parent; backend: stub; windowActive: true; readAfterMs: 30 }
    Timer {
        running: true; interval: 300
        onTriggered: {
            console.warn("LEGACYREAD " + view.legacy + "|" + stub.calls.filter(c => c.startsWith("read:")).length)
            Qt.quit()
        }
    }
}
""" % {"stub": self.stub(sample)})
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "LEGACYREAD"), "true|0")

    def test_generate_progress_empty_state_show_item_and_legacy(self):
        empty = fixture.sample()
        empty["days"]["days"], empty["days"]["latest"], empty["reports"] = [], "", {}
        log = self.harness("StatesSmoke.qml", """import QtQuick
Item {
    width: 1440; height: 900
    %(stub)s
    Item {
        %(empty)s
    }
    ReportsView { id: view; anchors.fill: parent; backend: stub; readAfterMs: 100000 }
    ReportsView { id: none; anchors.fill: parent; backend: emptyStub }
    Item {
        %(early)s
    }
    ReportsView { id: early; anchors.fill: parent; backend: earlyStub; active: false; visible: false }
    %(find)s
    Component.onCompleted: {
        find(view, "generateButton").clicked()
        console.warn("GEN " + [stub.calls.filter(c => c === "generate").length, find(view, "progress").visible,
                               find(view, "generateButton").text, find(view, "generateButton").enabled].join("|"))
        stub.generating = false
        stub.generateFinished("")
        console.warn("GENFAIL " + find(view, "notice").text + "|" + find(view, "progress").visible)
        stub.generateFinished("/r/2026-10-27/index.html")
        console.warn("GENOK " + find(view, "notice").visible)
        console.warn("EMPTY " + [find(none, "emptyState").visible, find(none, "emptyTitle").text].join("|"))
        emptyStub.available = false
        console.warn("NODAEMON " + find(none, "emptyTitle").text)
        // showItem: projekt w bieżącej dobie, potem doba i projekt (slug).
        view.showItem("2026-10-27/plasma-tide")
        console.warn("SAMEDAY " + view.selectedDay + "|" + view.selectedProject.name)
        view.showItem("2026-10-26/cookapp")
        console.warn("SHOW " + view.selectedDay + "|" + view.selectedProject.name + "|" + find(view.panel, "panelTitle").text)
        // Widok jeszcze niepokazany (bez listy dni): element czeka na listę.
        early.showItem("2026-10-25")
        const pending = early.pendingDay
        early.active = true
        console.warn("PENDING " + pending + "|" + early.pendingDay + "|" + early.selectedDay)
        // Raport sprzed report.json: karty bez liczników, panel z informacją, bez „Kopiuj”.
        view.showItem("2026-10-24")
        console.warn("LEGACY " + [view.legacy, texts(view, "projectName").join(","), find(view.panel, "panelLegacy").visible,
                                  find(view.panel, "copyButton").visible, find(view, "summaryLine").children.length > 0].join("|"))
        view.showItem("2026-10-21")   // doby nie ma na liście — czeka, nic się nie zmienia
        console.warn("UNKNOWN " + view.pendingDay + "|" + view.selectedDay)
        view.selectDay("2026-10-21")  // doba zniknęła (retencja) między Days() a Day()
        console.warn("MISSING " + find(view, "dayError").visible + "|" + find(view, "dayError").text)
        Qt.quit()
    }
}
""" % {"stub": self.stub(fixture.sample()), "find": self.FIND,
       "empty": self.stub(empty).replace("id: stub", "id: emptyStub"),
       "early": self.late_stub(fixture.sample())})
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "GEN"), "1|true|Składam raport…|false")
        self.assertEqual(warned(log, "GENFAIL"),
                         "Nie udało się złożyć raportu — szczegóły w dzienniku tide-daemon|false")
        self.assertEqual(warned(log, "GENOK"), "false")
        self.assertEqual(warned(log, "EMPTY"), "true|Nie ma jeszcze raportów")
        self.assertEqual(warned(log, "NODAEMON"), "Usługa tide-daemon nie odpowiada")
        self.assertEqual(warned(log, "SAMEDAY"), "2026-10-27|plasma-tide")
        self.assertEqual(warned(log, "SHOW"), "2026-10-26|cookapp|cookapp")
        self.assertEqual(warned(log, "PENDING"), "2026-10-25||2026-10-25")
        self.assertEqual(warned(log, "LEGACY"), "true|log-explorer,cookapp|true|false|true")
        self.assertEqual(warned(log, "UNKNOWN"), "2026-10-21|2026-10-24")
        self.assertTrue(warned(log, "MISSING").startswith("true|Raportu z tej doby już nie ma"), log[-2000:])

    def test_report_text_with_markup_stays_plain(self):
        """HTML i znaczniki w tekstach raportu (tytuły sesji, tematy commitów, odpowiedź modelu) — dosłownie."""
        sample = fixture.sample()
        report = sample["reports"]["2026-10-27"]
        report["projects"][0]["name"] = "<i>log</i>"
        report["projects"][0]["headline"] = fixture.INJECTION
        report["projects"][0]["done"][0]["text"] = fixture.INJECTION
        report["projects"][0]["waiting"][0]["text"] = "<script>alert(1)</script>"
        report["outside"][0]["title"] = "<u>sesja</u>"
        log = self.harness("MarkupSmoke.qml", """import QtQuick
Item {
    width: 1440; height: 900
    %(stub)s
    ReportsView { id: view; anchors.fill: parent; backend: stub; readAfterMs: 100000 }
    %(find)s
    function formats(item, out) {
        if (item.visible && typeof item.text === "string" && item.textFormat !== undefined && item.text.indexOf("<") >= 0)
            out.push(item.textFormat + ":" + item.text)
        for (const child of item.children) formats(child, out)
        if (item.contentItem && item.contentItem !== item) formats(item.contentItem, out)
        return out
    }
    Component.onCompleted: {
        view.outsideSection.expanded = true
        for (const line of formats(view, [])) console.warn("FMT " + line)
        console.warn("NAME " + texts(view, "projectName")[0])
        Qt.quit()
    }
}
""" % {"stub": self.stub(sample), "find": self.FIND})
        self.assertEqual(problems(log), [])
        lines = re.findall(r"FMT (\d+):(.*)$", log, re.M)
        self.assertTrue(lines, log[-2000:])
        self.assertEqual({fmt for fmt, _ in lines}, {"0"}, "Text.PlainText (0) dla każdego tekstu ze znacznikami")
        shown = "\n".join(text for _fmt, text in lines)
        for raw in ("<i>log</i>", fixture.INJECTION, "<script>alert(1)</script>", "<u>sesja</u>"):
            self.assertIn(raw, shown)
        self.assertEqual(warned(log, "NAME"), "<i>log</i>")


class BackendDataTest(unittest.TestCase):
    """Backend z danymi z pliku (--data): bez magistrali, akcje wyłączone, przeczytanie w pamięci."""

    @classmethod
    def setUpClass(cls):
        cls.module = load_backend()

    def test_days_day_tags_badge_and_read(self):
        backend = self.module.create(None, fixture.sample())
        self.assertEqual((backend.available, backend.preview, backend.badgeText, backend.badgeTone),
                         (True, True, "nowy", "waiting"))
        self.assertEqual(json.loads(backend.dayJson), {}, "raport doby dopiero po load()")
        backend.load()
        self.assertEqual(backend.selectedDay, "2026-10-27")
        day = json.loads(backend.dayJson)
        self.assertEqual((day["ok"], day["latest"], day["unread"], day["legacy"]), (True, True, True, False))
        self.assertEqual({p["name"]: [t["name"] for t in p.get("tags", [])] for p in day["projects"]},
                         {"log-explorer": ["praca"], "mobile-app": ["praca"], "plasma-tide": ["własne"],
                          "team-v2": ["własne"], "cookapp": ["własne"]}, "po ścieżce repozytorium albo nazwie")
        self.assertEqual(day["projects"][0]["tags"][0]["color"], "sky")
        backend.selectDay("2026-10-24")
        self.assertTrue(json.loads(backend.dayJson)["legacy"])
        backend.selectDay("2026-10-21")
        self.assertEqual(json.loads(backend.dayJson), {"ok": False, "error": "missing", "day": "2026-10-21"})
        backend.markRead("2026-10-26")
        self.assertEqual(backend.badgeText, "nowy", "starsza doba niczego nie gasi")
        backend.selectDay("2026-10-27")
        backend.markRead("2026-10-27")
        self.assertEqual(backend.badgeText, "")
        self.assertFalse(json.loads(backend.daysJson)["unread"])
        self.assertFalse(json.loads(backend.dayJson)["unread"])

    def test_preview_actions_are_disabled(self):
        backend = self.module.create(None, fixture.sample(unread=False))
        self.assertEqual(backend.badgeText, "")
        finished, opened = [], []
        backend.generateFinished.connect(finished.append)
        backend.openFinished.connect(opened.append)
        backend.generate()
        backend.openDay("2026-10-27", "")
        self.assertEqual((finished, opened, backend.generating), ([""], [False], False))

    def test_without_bus_and_data_everything_is_hidden(self):
        backend = self.module.create(None, None)
        self.assertEqual((backend.available, backend.badgeText, json.loads(backend.daysJson)["days"]), (False, "", []))

    def test_copy_text_goes_to_clipboard(self):
        from PyQt6.QtGui import QGuiApplication
        backend = self.module.create(None, fixture.sample())
        if not isinstance(QGuiApplication.instance(), QGuiApplication):
            self.skipTest("bez QGuiApplication nie ma schowka")
        self.assertTrue(backend.copyText("Na daily · <b>tekst</b>\n- pozycja\n"))
        self.assertEqual(QGuiApplication.clipboard().text(), "Na daily · <b>tekst</b>\n- pozycja\n")
        self.assertFalse(backend.copyText(""))

    def test_board_tags_degrade_silently(self):
        tags = self.module.board_tags
        self.assertEqual(tags(None), {})
        self.assertEqual(tags({"projects": "nie lista"}), {})
        self.assertEqual(tags({"projects": [{"name": "x", "tags": ["a"]}], "tags": []}),
                         {"name:x": [{"name": "a", "color": ""}]})
        self.assertEqual(self.module.with_tags({"projects": [{"name": "y"}]}, {}), {"projects": [{"name": "y"}]})


@requires_dbus
class BackendOverDbusTest(unittest.TestCase):
    """Backend na prywatnej magistrali z prawdziwym tide-daemon i raportem ze świata reportfix.py."""

    def setUp(self):
        from daemon.tests import reportfix
        self.reportfix = reportfix
        self.tmp = tempfile.mkdtemp(prefix="tide-reports-dbus-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.p = reportfix.build(os.path.join(self.tmp, "world"))
        self.env = make_env(self.tmp)
        self.env.update({"TZ": reportfix.TZ, "LIMITY_CONFIG": os.path.join(self.tmp, "no-limity.json")})
        self.reports = os.path.join(self.tmp, "reports")
        self.bus = PrivateBus("tide-reports-view-test")
        self.addCleanup(self.bus.stop)
        self.bus.spawn_daemon("--claude-dir", self.p["claude"], "--codex-dir", self.p["codex"],
                              "--workspace", self.p["work"], "--no-processes", "--no-idle", "--no-notifications",
                              "--now", "2026-10-26T06:00:00Z", "--report-delay", "0", "--report-dir", self.reports,
                              extra_env=self.env)
        self.module = load_backend()

    def test_badge_days_read_open_and_generate(self):
        backend = self.module.Backend(self.bus.bus)
        self.assertTrue(wait_until(lambda: backend.badgeText == "nowy", timeout=20), "raport przy starcie → „nowy”")
        self.assertEqual(json.loads(backend.daysJson)["days"], [], "lista dni dopiero po load()")
        backend.load()
        self.assertTrue(wait_until(lambda: json.loads(backend.dayJson).get("ok"), timeout=10))
        days = json.loads(backend.daysJson)
        self.assertEqual((days["latest"], days["unread"], [d["day"] for d in days["days"]]),
                         (self.reportfix.DAY, True, [self.reportfix.DAY]))
        day = json.loads(backend.dayJson)
        self.assertEqual(backend.selectedDay, self.reportfix.DAY)
        self.assertEqual([p["name"] for p in day["projects"]], ["alpha", "beta"])
        self.assertIn(self.reportfix.INJECTION, day["copyAll"], "surowy tekst do schowka")

        # Obejrzenie w oknie → MarkRead: kropka gaśnie (plakietka, Days(), Day()); raport w oknie nie miga
        # (Changed odświeża tę samą dobę bez czyszczenia danych).
        seen = []
        backend.dayChanged.connect(lambda: seen.append(json.loads(backend.dayJson)))
        backend.markRead(self.reportfix.DAY)
        self.assertTrue(wait_until(lambda: backend.badgeText == "", timeout=10))
        self.assertTrue(wait_until(lambda: json.loads(backend.dayJson).get("unread") is False, timeout=10))
        self.assertFalse(json.loads(backend.daysJson)["unread"])
        self.assertTrue(seen and all(d.get("ok") for d in seen), [d.get("ok") for d in seen])

        opened = []
        backend.openFinished.connect(opened.append)
        backend.openDay(self.reportfix.DAY, "alpha")
        self.assertTrue(wait_until(lambda: opened, timeout=10))
        self.assertEqual(opened, [True])
        self.assertTrue(wait_until(lambda: any(c.endswith(os.path.join(self.reportfix.DAY, "alpha.html"))
                                               for c in calls(self.env) if "xdg-open" in c), timeout=10))
        backend.openDay(self.reportfix.DAY, "../x")
        self.assertTrue(wait_until(lambda: len(opened) == 2, timeout=10))
        self.assertEqual(opened[1], False)

        finished = []
        backend.generateFinished.connect(finished.append)
        backend.generate()
        self.assertTrue(backend.generating)
        backend.generate()  # drugi raz w trakcie — bez drugiego wywołania
        self.assertTrue(wait_until(lambda: finished, timeout=60))
        self.assertEqual(finished, [os.path.join(self.reports, self.reportfix.DAY, "index.html")])
        self.assertFalse(backend.generating)
        self.assertEqual(backend.badgeText, "", "Generate() tej samej doby nie zapala kropki")


class GrabTest(unittest.TestCase):
    """Zrzut okna Tide na widoku Raporty z danymi z pliku (bez magistrali)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-reports-grab-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))
        cls.data = os.path.join(cls.tmp, "reports.json")
        with open(cls.data, "w", encoding="utf-8") as fh:
            json.dump(fixture.sample(), fh, ensure_ascii=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def grab(self, name, *argv, width=1440, height=900) -> str:
        png = os.path.join(os.environ.get("TIDE_GRAB_DIR") or self.tmp, name + ".png")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", TZ=fixture.TZ)
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)  # --data nie potrzebuje magistrali
        result = subprocess.run([sys.executable, os.path.join(self.pkg, "tide.py"), "--view", "reports",
                                 "--data", "reports=" + self.data, *argv, "--grab", png, "--width", str(width),
                                 "--height", str(height)], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertEqual(problems(result.stderr), [], result.stderr[-3000:])
        with open(png, "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        return png

    def test_grabs(self):
        shots = {
            "reports": self.grab("reports"),
            "reports-wide": self.grab("reports-wide", width=1800, height=1000),
            "reports-outside": self.grab("reports-outside", "--select", "2026-10-27/plasma-tide",
                                         "--preview", "expand=1"),
            "reports-legacy": self.grab("reports-legacy", "--select", "2026-10-24"),
            # Projekt z nieaktualną pozycją „Czeka na mnie” (agent już nie czeka — przekreślona).
            "reports-stale": self.grab("reports-stale", "--select", "2026-10-27/mobile-app"),
        }
        for name, png in shots.items():
            self.assertGreater(os.path.getsize(png), 40_000, name)
        with open(shots["reports"], "rb") as a, open(shots["reports-outside"], "rb") as b:
            self.assertNotEqual(a.read(), b.read(), "--select i --preview zmieniają widok")


if __name__ == "__main__":
    unittest.main()
