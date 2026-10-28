"""Widżet Praca (org.tide.work, PT-40): pakiet z biblioteką, teksty kafelka i podpowiedzi z liczbą mnogą,
popup (grupy: czeka, w toku, do sprawdzenia, do zrobienia, zwinięte Skończone; przyciski i ich cele),
pasek boczny, miejsce w docku i pasku bocznym zamiast Tablicy i Agentów, instalacja i odinstalowanie.

qml6 offscreen ładuje komponenty bez PlasmoidItem (CompactWork, WorkPopup, SidebarWork) — tym samym
kodem, który rysuje podgląd (widgets/preview_work.py). kpackagetool6 jest atrapą.
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from . import REPO, WIDGETS_DIR
from daemon.tests.fakebin import calls, files_under, make_env
from widgets import preview_work as preview

WIDGET_ID = "org.tide.work"
WIDGET_DIR = os.path.join(WIDGETS_DIR, "work")
UI = os.path.join(WIDGET_DIR, "contents/ui")
QML_PROBLEMS = r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|is not defined|" \
               r"Binding loop|Cannot read"


def read(name):
    with open(os.path.join(UI, name), encoding="utf-8") as fh:
        return fh.read()


class PackageTest(unittest.TestCase):
    def test_package_contains_widget_and_library(self):
        with tempfile.TemporaryDirectory(prefix="tide-stage-work-") as tmp:
            pkg = os.path.join(tmp, "pkg")
            subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"), WIDGET_DIR, pkg], check=True)
            files = files_under(pkg)
        for name in ("metadata.json", "contents/config/main.xml", "contents/ui/main.qml", "contents/ui/CompactWork.qml",
                     "contents/ui/WorkPopup.qml", "contents/ui/WorkRow.qml", "contents/ui/SidebarWork.qml",
                     "contents/ui/worktexts.js", "contents/ui/tide/Tokens.qml", "contents/ui/tide/DaemonSource.qml",
                     "contents/ui/tide/ReportLink.qml"):
            self.assertIn(name, files)
        with open(os.path.join(WIDGET_DIR, "metadata.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["KPlugin"]["Id"], WIDGET_ID)

    def test_main_talks_to_one_list_and_keeps_both_old_tiles_actions(self):
        text = read("main.qml")
        self.assertIn('path: "/Board"', text)
        self.assertIn('method: "Work"', text, "jedna lista z usługi, nie Snapshot + Sessions")
        self.assertNotIn('method: "Sessions"', text)
        self.assertIn('changedSignal: "WorkChanged"', text, "kafelek nie odświeża się na Changed całej migawki")
        self.assertIn("pendingSessions[request.session]", text, "drugie „+ karta” nie idzie do usługi")
        with open(os.path.join(WIDGETS_DIR, "lib/DaemonSource.qml"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('function dbusWorkChanged() { if (source.changedSignal === "WorkChanged") source.refresh() }',
                      source)
        # „Przejdź”: agent, a gdy się nie da — karta w oknie Tide albo (sesja bez karty) widok Sesje.
        self.assertIn('"ActivateSession", [sessionId]', text)
        self.assertIn("else if (cardId) root.showBoard(cardId)", text)
        self.assertIn("else root.showSessions(sessionId)", text)
        self.assertIn('source.call("ShowWindow", [cardId || ""]', text)
        self.assertIn('"/Coordination", "org.tide.Daemon.Coordination", "ShowWindow"', text)
        self.assertIn('"/Tasks", "org.tide.Daemon.Tasks", "ShowDialog"', text)
        self.assertIn('source.call("CreateCard", [JSON.stringify(request)]', text, "„+ karta” z żądaniem z usługi")
        self.assertIn('root.showBoard("launch:" + card)', text, "„Uruchom” — okno Tide z „Co ma zrobić agent?”")
        self.assertIn('reports.call("Open"', text)
        for name in ("MoveCard", "UpdateCard", "UpsertTag"):
            self.assertNotIn(name, text, "kafelek tylko zakłada kartę z sesji — reszta w oknie Tide")

    def test_attention_rule_matches_old_agents_tile(self):
        text = read("main.qml")
        self.assertIn("Plasmoid.status: source.available && counts.waiting > 0 && !Plasmoid.configuration.sidebarMode",
                      text)
        self.assertIn("PlasmaCore.Types.NeedsAttentionStatus", text)
        self.assertIn("morePopupOpen ? PlasmaCore.Types.RequiresAttentionStatus", text)
        with open(os.path.join(WIDGETS_DIR, "agents/contents/ui/main.qml"), encoding="utf-8") as fh:
            self.assertIn("Plasmoid.status: waitingCount > 0 && !Plasmoid.configuration.sidebarMode", fh.read())

    def test_qml_has_no_hardcoded_colors_and_data_is_plain(self):
        for dirpath, _dirs, names in os.walk(WIDGET_DIR):
            for name in (n for n in names if n.endswith((".qml", ".js"))):
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    source = fh.read()
                self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', source), [], name)
                self.assertNotIn("StyledText", source, name)
        for name in ("WorkRow.qml", "WorkPopup.qml"):
            for _indent, body in re.findall(r"\n(\s*)Text \{\n(.*?)\n\1\}", read(name), re.S):
                self.assertIn("textFormat: Text.PlainText", body, f"{name}: {body[:80]}")

    def test_dock_and_sidebar_placement_replaces_board_and_agents(self):
        from scripts import dock, sidebar
        self.assertEqual(dock.DOCK_RIGHT[:2], [WIDGET_ID, "org.tide.limits"], "w docku po prawej, przed Limitami AI")
        self.assertEqual(sidebar.WIDGETS[0], WIDGET_ID, "w pasku bocznym pod nagłówkiem")
        for old in ("org.tide.board", "org.tide.agents"):
            self.assertNotIn(old, dock.DOCK_WIDGETS)
            self.assertNotIn(old, sidebar.WIDGETS)
            self.assertIn(old, dock.REPLACES[WIDGET_ID], "bez Pracy w docku stoją stare kafelki")
            self.assertIn(old, sidebar.REPLACES[WIDGET_ID])
            self.assertIn(old, dock.DOCK_RETIRED, "instalacja i odinstalowanie docka zdejmują stare kafelki")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class TileTest(unittest.TestCase):
    def test_tile_texts(self):
        mock = preview.render_tile(preview.TILE_SCENARIOS["mock"])
        self.assertEqual((mock["label"], mock["detail"], mock["summary"]),
                         ("Praca", "10", "3 w toku · 2 czekają · 2 do spr."))
        self.assertEqual(mock["tint"], mock["waitingTint"], "coś czeka — kafelek w brzoskwini")
        self.assertTrue(mock["fits"], "dwie linie mieszczą się w 48 px")
        self.assertLess(mock["width"], 320)
        self.assertEqual(mock["toolTip"], "Praca: 10 (w tym 2 sesje bez karty) — 3 w toku, 2 czekają na ciebie, "
                                          "2 do sprawdzenia, 3 do zrobienia\nKliknij, aby zobaczyć, co czeka")
        todo = preview.render_tile(preview.TILE_SCENARIOS["todo"])
        self.assertEqual((todo["detail"], todo["summary"]), ("3", "3 do zrobienia"))
        self.assertNotEqual(todo["tint"], todo["waitingTint"])
        empty = preview.render_tile(preview.TILE_SCENARIOS["empty"])
        self.assertEqual((empty["detail"], empty["summary"]), ("", "nic w toku"))
        self.assertTrue(empty["toolTip"].startswith("Nic w toku ani do zrobienia"))
        offline = preview.render_tile(preview.TILE_SCENARIOS["offline"])
        self.assertEqual((offline["summary"], offline["toolTip"]), ("usługa nie działa", "Usługa tide-daemon nie działa"))

    def test_plural_forms_and_finished_not_counted(self):
        def sample(**groups):
            items = []
            for group, n in groups.items():
                for k in range(n):
                    items.append(preview.card(len(items) + 1, "x", "p", group) if group != "finished" else
                                 preview.loose(f"claude:{k}", "p", "finished", "done"))
            return {"work": preview.work(items), "available": True}

        cases = [(dict(waiting=1), "1", "1 czeka"), (dict(waiting=3), "3", "3 czekają"),
                 (dict(waiting=5), "5", "5 czeka"), (dict(progress=2, review=1, todo=2), "5", "2 w toku · 1 do spr."),
                 (dict(todo=22), "22", "22 do zrobienia"), (dict(finished=4), "", "nic w toku"),
                 (dict(progress=1, finished=3), "1", "1 w toku")]
        for groups, detail, summary in cases:
            with self.subTest(**groups):
                result = preview.render_tile(sample(**groups))
                self.assertEqual((result["detail"], result["summary"]), (detail, summary))


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class PopupTest(unittest.TestCase):
    def test_groups_rows_and_words(self):
        popup = preview.render_popup()
        self.assertEqual(popup["subtitle"], "3 w toku · 2 czekają · 2 do sprawdzenia · 3 do zrobienia")
        self.assertEqual(popup["groups"], [["waiting", 2, 2], ["progress", 3, 3], ["review", 2, 2], ["todo", 3, 3],
                                           ["finished", 2, 0]], "Skończone zwinięte, z licznikiem")
        rows = {r[0] + "/" + r[1]: r for r in popup["rows"]}
        k20 = rows["K-20/waiting"]
        self.assertEqual((k20[3], k20[4], k20[5]), ("„Czy taki podział na moduły czatu i alertów ma sens?”", True, False))
        loose = rows["bez karty/waiting"]
        self.assertEqual((loose[4], loose[5]), (True, True), "sesja bez karty: Przejdź i + karta")
        self.assertEqual(rows["K-18/progress"][2], "pracuje")
        self.assertEqual(rows["K-17/progress"][2], "bezczynny 1 d")
        self.assertEqual(rows["bez karty/progress"][3], "Biblioteka kazusów: import z PDF · Edit · CaseImporter.cs")
        self.assertEqual((rows["K-26/review"][2], rows["K-24/review"][2]), ("do scalenia", "do testu"))
        self.assertEqual(rows["K-30/todo"][6], True, "do zrobienia: Uruchom")
        opened = preview.render_popup(show_finished=True)
        self.assertEqual(opened["groups"][-1], ["finished", 2, 2])
        finished = [r for r in opened["rows"] if r[1] == "finished"]
        self.assertEqual([r[2] for r in finished], ["skończył 5 min temu", "bezczynny 3 h"])
        empty = preview.render_popup(preview.work([]), report=None)
        self.assertEqual((empty["subtitle"], empty["groups"]), ("Nic w toku ani do zrobienia", []))
        offline = preview.render_popup(None, available=False)
        self.assertEqual((offline["subtitle"], offline["groups"]), ("Usługa tide-daemon nie działa", []))

    def test_go_on_live_cards_and_waiting_session_with_card(self):
        """Review m5: „Przejdź” (link) przy kartach w toku i do sprawdzenia z żywą sesją; review M1: sesja, która
        czeka, a jej karta jest w Gotowych — numer karty i notatka, bez „+ karta”."""
        data = preview.work([
            preview.loose("claude:stara", "dom", "waiting", "waiting", title="Eksport", question="Bash · git push",
                          card="K-9", note="karta K-9 w Gotowych"),
            preview.card(18, "Tablica i Docker", "plasma-tide", "progress", state="working", ago=30, session="claude:k18"),
            preview.card(26, "Raport", "plasma-tide", "review", note="do scalenia: fix/x", state="ended", ago=60,
                         session="claude:k26"),
            preview.card(27, "Bez agenta", "plasma-tide", "progress", note="T-1 w toku"),
        ])
        rows = {r[0]: r for r in preview.render_popup(data, report=None)["rows"]}
        self.assertEqual(rows["K-9"][1:], ["waiting", "", "„Bash · git push” · karta K-9 w Gotowych", True, False, False])
        self.assertEqual((rows["K-18"][4], rows["K-26"][4], rows["K-27"][4]), (True, False, False),
                         "„Przejdź” tylko przy żywej sesji, do której da się przejść")
        side = preview.render_sidebar(data)
        self.assertEqual(side["rows"][0], ["K-9", ""])
        self.assertEqual(side["rows"][1], ["K-18", "pracuje"], "w pasku bocznym słowo stanu zostaje")

    def test_buttons_send_the_right_requests(self):
        qml = """import QtQuick
import QtQuick.Window
import "work/contents/ui" as Work
Window {
    width: 500; height: 900; visible: true
    property var log: []
    Work.WorkPopup {
        id: popup
        width: 450
        work: %(work)s
        nowTs: %(now)d
        report: %(report)s
        maxListHeight: 2000
        onGoRequested: (session, card) => log.push("go:" + session + "|" + card)
        onOpenCardRequested: card => log.push("open:" + card)
        onLaunchRequested: card => log.push("launch:" + card)
        onAddCardRequested: request => log.push("add:" + [request.title, request.column, request.session, request.project || ""].join("|"))
        onNewTaskRequested: log.push("new")
        onBoardRequested: log.push("board")
        onSessionsRequested: log.push("sessions")
        onReportRequested: log.push("report")
        onFinishedToggled: { log.push("finished"); showFinished = !showFinished }
    }
    function find(item, name, out) {
        for (const child of item.children) {
            if (child.objectName === name && child.visible) out.push(child)
            find(child, name, out)
        }
        return out
    }
    function rows(item, out) {
        for (const child of item.children) {
            if (child.row !== undefined && child.goRequested !== undefined) out.push(child)
            rows(child, out)
        }
        return out
    }
    Timer {
        interval: 400; running: true
        onTriggered: {
            const go = find(popup, "goButton", [])
            log.push("go buttons:" + go.length)
            go[0].clicked()
            go[1].clicked()
            const add = find(popup, "addCardButton", [])
            log.push("add buttons:" + add.length)
            add[0].clicked()
            popup.busy = true
            add[0].clicked()
            popup.busy = false
            popup.pending = { "codex:pricing": true }   // „+ karta” już poszło — przycisk znika (review m1)
            log.push("add buttons after:" + find(popup, "addCardButton", []).length)
            popup.pending = {}
            find(popup, "launchButton", [])[0].clicked()
            const all = rows(popup, [])
            all[2].openRequested()                     // K-18 — karta w oknie Tide
            all[4].Accessible.pressAction()            // sesja bez karty w toku — do agenta
            find(popup, "newTaskButton", [])[0].clicked()
            find(popup, "boardButton", [])[0].clicked()
            find(popup, "sessionsButton", [])[0].clicked()
            find(popup, "reportButton", [])[0].clicked()
            find(popup, "finishedToggle", [])[0].clicked()
            log.push("rows after toggle:" + rows(popup, []).length)
            console.warn("POPUP " + JSON.stringify(log))
            Qt.quit()
        }
    }
}
""" % {"work": json.dumps(preview.MOCK, ensure_ascii=False), "now": preview.NOW_TS,
       "report": json.dumps(preview.REPORT)}
        log = preview.run_qml(qml, "przycisków popupu", name="Buttons.qml")
        match = re.search(r"POPUP (.*)$", log, re.M)
        self.assertIsNotNone(match, log[-3000:])
        self.assertEqual(json.loads(match.group(1)), [
            "go buttons:6", "go:claude:k20|K-20", "go:codex:pricing|",
            "add buttons:2", "add:pricingapp · feat/rabaty|todo|codex:pricing|", "add buttons after:1",
            "launch:K-30", "open:K-18", "go:claude:kazusy|", "new", "board", "sessions", "report", "finished",
            "rows after toggle:12"], "zapis w toku nie wysyła drugiego „+ karta”")
        self.assertIsNone(re.search(QML_PROBLEMS, log), log[-3000:])

    def test_sidebar_tile(self):
        side = preview.render_sidebar()
        self.assertEqual((side["title"], side["summary"]), ("Praca · 10", "3 w toku · 2 czekają · 2 do sprawdzenia"))
        self.assertEqual(side["rows"], [["K-20", ""], ["bez karty", ""], ["K-18", "pracuje"],
                                        ["K-17", "bezczynny 1 d"], ["bez karty", "pracuje"]])
        self.assertEqual(side["hidden"], 7, "5 pozostałych i 2 skończone w „+N więcej”")
        offline = preview.render_sidebar(None, available=False)
        self.assertEqual((offline["title"], offline["rows"], offline["hidden"]), ("Praca", [], 0))

    def test_whole_sidebar_with_work_fits(self):
        from widgets import preview as sidebar_preview
        for name in ("typical", "busy"):
            whole = preview.render_whole_sidebar(scenario=name)
            self.assertLessEqual(whole["total"], sidebar_preview.SIDEBAR_HEIGHT, (name, whole))

    def test_preview_writes_pngs(self):
        with tempfile.TemporaryDirectory(prefix="tide-work-png-") as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                preview.main([tmp])
            names = sorted(os.listdir(tmp))
            expected = ["work-popup-empty.png", "work-popup-finished.png", "work-popup.png", "work-sidebar.png",
                        "work-tile-empty.png", "work-tile-mock.png", "work-tile-offline.png", "work-tile-todo.png",
                        "work-tile-widest.png"]
            self.assertEqual([n for n in names if n != "work-sidebar-whole.png"], expected)
            for name in names:
                self.assertGreater(os.path.getsize(os.path.join(tmp, name)), 1500, name)


@unittest.skipUnless(shutil.which("make"), "brak make")
class WidgetInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-widget-work-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.installed = os.path.join(self.env["XDG_DATA_HOME"], "plasma/plasmoids", WIDGET_ID)

    def make(self, target):
        subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True, capture_output=True, text=True)

    def test_install_then_upgrade_then_uninstall(self):
        self.make("install-widget-work")
        staged = os.path.join(self.env["BUILD_DIR"], "widgets", WIDGET_ID)
        self.assertEqual(calls(self.env), [f"kpackagetool6 -t Plasma/Applet -i {staged}"])
        files = files_under(self.installed)
        self.assertIn("contents/ui/WorkPopup.qml", files)
        self.assertIn("contents/ui/tide/Tokens.qml", files)
        self.make("install-widget-work")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -u {staged}")
        self.make("uninstall-widget-work")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -r {WIDGET_ID}")
        self.assertEqual(files_under(self.env["XDG_DATA_HOME"]), [])


if __name__ == "__main__":
    unittest.main()
