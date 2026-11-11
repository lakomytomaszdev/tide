"""Widżet tablicy (org.tide.board): pakiet z biblioteką, teksty kafelka i podpowiedzi z liczbą mnogą,
popup (wiersze w kolejności: czeka, w toku, do sprawdzenia, do zrobienia; szybkie dopisanie),
pasek boczny, miejsce w docku i pasku bocznym, instalacja i odinstalowanie.

qml6 offscreen ładuje komponenty bez PlasmoidItem (CompactBoard, BoardPopup, SidebarBoard) — tym
samym kodem, który rysuje podgląd (widgets/preview_board.py). kpackagetool6 jest atrapą.
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
from widgets import preview_board as preview

WIDGET_ID = "org.tide.board"
WIDGET_DIR = os.path.join(WIDGETS_DIR, "board")
QML_PROBLEMS = r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|is not defined|" \
               r"Binding loop|Cannot read"


class PackageTest(unittest.TestCase):
    def test_package_contains_widget_and_library(self):
        with tempfile.TemporaryDirectory(prefix="tide-stage-board-") as tmp:
            pkg = os.path.join(tmp, "pkg")
            subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"), WIDGET_DIR, pkg], check=True)
            files = files_under(pkg)
        for name in ("metadata.json", "contents/config/main.xml", "contents/ui/main.qml", "contents/ui/CompactBoard.qml",
                     "contents/ui/BoardPopup.qml", "contents/ui/SidebarBoard.qml", "contents/ui/boardtexts.js",
                     "contents/ui/tide/Tokens.qml", "contents/ui/tide/DaemonSource.qml"):
            self.assertIn(name, files)
        with open(os.path.join(WIDGET_DIR, "metadata.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["KPlugin"]["Id"], WIDGET_ID)

    def test_main_talks_to_board_area(self):
        with open(os.path.join(WIDGET_DIR, "contents/ui/main.qml"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('path: "/Board"', text)
        self.assertIn('method: "Snapshot"', text)
        self.assertIn('source.call("ShowWindow", [cardId || ""]', text)
        self.assertIn('source.call("CreateCard"', text)
        self.assertIn('column: "backlog"', text, "szybkie dopisanie trafia do Backlogu")
        self.assertIn("Plasmoid.configuration.quickProject = slug", text, "projekt zapamiętany")
        for name in ("MoveCard", "UpdateCard", "UpsertTag"):
            self.assertNotIn(name, text, "kafelek tylko dopisuje — reszta w oknie Tide")

    def test_qml_has_no_hardcoded_colors_and_data_is_plain(self):
        for dirpath, _dirs, names in os.walk(WIDGET_DIR):
            for name in (n for n in names if n.endswith(".qml")):
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    source = fh.read()
                self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', source), [], name)
                self.assertNotIn("StyledText", source, name)
        with open(os.path.join(WIDGET_DIR, "contents/ui/BoardPopup.qml"), encoding="utf-8") as fh:
            source = fh.read()
        for _indent, body in re.findall(r"\n(\s*)Text \{\n(.*?)\n\1\}", source, re.S):
            self.assertIn("textFormat: Text.PlainText", body, body[:80])

    def test_dock_and_sidebar_placement(self):
        # PT-40: kafelek Praca (org.tide.work) zastąpił Tablicę w docku i w pasku bocznym; pakiet zostaje
        # do wycofania i wraca na miejsce Pracy, gdy jej nie ma (scripts/dock.py, scripts/sidebar.py: REPLACES).
        from scripts import dock, sidebar
        self.assertNotIn(WIDGET_ID, dock.DOCK_WIDGETS)
        self.assertNotIn(WIDGET_ID, sidebar.WIDGETS)
        self.assertIn(WIDGET_ID, dock.REPLACES["org.tide.work"])
        self.assertIn(WIDGET_ID, sidebar.REPLACES["org.tide.work"])


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class TileTest(unittest.TestCase):
    def test_tile_texts(self):
        mock = preview.render_tile(preview.TILE_SCENARIOS["mock"])
        self.assertEqual((mock["label"], mock["detail"], mock["summary"]),
                         ("Tablica", "11 kart", "2 w toku · 1 czeka · 1 do sprawdzenia"))
        self.assertTrue(mock["fits"], "dwie linie mieszczą się w 48 px")
        self.assertLess(mock["width"], 300)
        self.assertEqual(mock["toolTip"], "Tablica pracy: 11 kart — 2 w toku, 1 czeka na ciebie, 1 do sprawdzenia, "
                                          "2 do zrobienia, 5 w backlogu\nKliknij, aby zobaczyć, co czeka")
        today = preview.render_tile(preview.TILE_SCENARIOS["today"])
        self.assertEqual(today["summary"], "6 do zrobienia · 5 w backlogu", "bez pracy agentów — co leży na tablicy")
        self.assertTrue(today["fits"], "dwie linie mieszczą się w 48 px")
        self.assertLess(today["width"], 300)
        empty = preview.render_tile(preview.TILE_SCENARIOS["empty"])
        self.assertEqual((empty["detail"], empty["summary"]), ("", "brak kart"))
        self.assertTrue(empty["toolTip"].startswith("Tablica pracy jest pusta"))
        offline = preview.render_tile(preview.TILE_SCENARIOS["offline"])
        self.assertEqual((offline["summary"], offline["toolTip"]), ("usługa nie działa", "Usługa tide-daemon nie działa"))

    def test_plural_forms(self):
        def cards(**counts):
            out = []
            for column, n in counts.items():
                for _ in range(n):
                    manual = column in ("todo", "backlog")
                    out.append(preview.card(len(out) + 1, "x", "p", column if manual else "todo",
                                            derived=None if manual else {"column": column}))
            return {"snapshot": preview.snapshot(out), "available": True}

        cases = [(dict(waiting=1), "Tablica", "1 karta", "1 czeka"), (dict(waiting=3), "Tablica", "3 karty", "3 czekają"),
                 (dict(waiting=5), "Tablica", "5 kart", "5 czeka"),
                 (dict(progress=2, review=1, todo=2), "Tablica", "5 kart", "2 w toku · 1 do sprawdzenia"),
                 (dict(todo=22), "Tablica", "22 karty", "22 do zrobienia"),
                 (dict(backlog=12), "Tablica", "12 kart", "12 w backlogu"),
                 (dict(todo=2, backlog=3), "Tablica", "5 kart", "2 do zrobienia · 3 w backlogu"),
                 (dict(progress=1, backlog=3), "Tablica", "4 karty", "1 w toku")]
        for counts, label, detail, summary in cases:
            with self.subTest(**counts):
                result = preview.render_tile(cards(**counts))
                self.assertEqual((result["label"], result["detail"], result["summary"]), (label, detail, summary))


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class PopupTest(unittest.TestCase):
    def test_rows_order_and_subtitle(self):
        popup = preview.render_popup()
        self.assertEqual(popup["subtitle"], "11 kart w 7 projektach · własne 6 · Firma 5")
        self.assertEqual([r[:2] for r in popup["rows"]],
                         [["K-13", "waiting"], ["K-15", "progress"], ["K-11", "progress"], ["K-12", "review"],
                          ["K-16", "todo"], ["K-20", "todo"]], "najpierw to, co czeka na ciebie")
        self.assertEqual(popup["rows"][1][3], "3/10")
        self.assertEqual(popup["project"], "plasma-tide")
        empty = preview.render_popup(preview.snapshot([], exists=False), quick_project="plasma-tide")
        self.assertEqual((empty["subtitle"], empty["rows"], empty["project"]), ("Nic nie ma na tablicy", [], ""),
                         "projektu, którego nie ma, nie wysyłamy")
        backlog = preview.render_popup(preview.snapshot([preview.card(i, "x", "p", "backlog") for i in range(1, 6)]))
        self.assertEqual([r[1] for r in backlog["rows"]], ["backlog"] * 5, "bez pracy — góra Backlogu")
        offline = preview.render_popup(None, available=False)
        self.assertEqual(offline["subtitle"], "Usługa tide-daemon nie działa")

    def test_quick_add_and_open(self):
        with tempfile.TemporaryDirectory(prefix="tide-board-popup-") as tmp:
            preview.stage_board(tmp)
            harness = os.path.join(tmp, "Popup.qml")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write("""import QtQuick
import QtQuick.Window
import "board/contents/ui" as Board
Window {
    width: 460; height: 520; visible: true
    property var log: []
    Board.BoardPopup {
        id: popup
        width: 420
        snapshot: %s
        quickProject: "plasma-tide"
        onAddRequested: (title, project) => log.push("add:" + title + "|" + project)
        onOpenRequested: id => log.push("open:" + id)
        onLaunchRequested: id => log.push("launch:" + id)
        onAgentRequested: (session, id) => log.push("agent:" + session + "|" + id)
        onProjectChosen: slug => { log.push("project:" + slug); quickProject = slug }
    }
    function goButtons(item, out) {
        for (const child of item.children) {
            if (child.text === "Przejdź" && child.visible) out.push(child)
            goButtons(child, out)
        }
        return out
    }
    function launchRows(item, out) {
        for (const child of item.children) {
            if (child.objectName === "launchRow" && child.visible) out.push(child)
            launchRows(child, out)
        }
        return out
    }
    Timer {
        interval: 300; running: true
        onTriggered: {
            const rows = launchRows(popup, [])
            log.push("launchRows:" + rows.length)
            rows[0].Accessible.pressAction()
            goButtons(popup, [])[0].clicked()
            popup.input.text = "   "
            popup.submit()
            popup.input.text = "Eksport do CSV"
            popup.input.accepted()
            popup.projectChosen("")
            popup.input.text = "Bez projektu"
            popup.submit()
            popup.busy = true
            popup.submit()
            popup.openRequested("K-13")
            console.warn("POPUP " + JSON.stringify(log))
            Qt.quit()
        }
    }
}
""" % json.dumps(preview.snapshot(preview.MOCK_CARDS), ensure_ascii=False))
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
            log = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=60).stderr
        match = re.search(r"POPUP (.*)$", log, re.M)
        self.assertIsNotNone(match, log[-3000:])
        self.assertEqual(json.loads(match.group(1)), ["launchRows:2", "launch:K-16", "agent:claude:k13-session|K-13",
                                                      "add:Eksport do CSV|plasma-tide", "project:",
                                                      "add:Bez projektu|", "open:K-13"],
                         "pusty tytuł i zapis w toku nic nie wysyłają")
        self.assertIsNone(re.search(QML_PROBLEMS, log), log[-3000:])

    def test_go_without_agent_session_opens_the_card(self):
        # „Przejdź” bez sesji agenta (karta bez koordynatora) — karta w oknie Tide, jak dotąd.
        with open(os.path.join(WIDGET_DIR, "contents/ui/BoardPopup.qml"), encoding="utf-8") as fh:
            self.assertIn(": popup.openRequested(row.modelData.id)", fh.read())
        with open(os.path.join(WIDGET_DIR, "contents/ui/main.qml"), encoding="utf-8") as fh:
            main = fh.read()
        self.assertIn('"ActivateSession", [sessionId]', main)
        self.assertIn("root.showWindow(cardId)", main, "sesji nie da się aktywować — karta w oknie Tide")

    def test_launch_from_popup_opens_tide_with_launch_intent(self):
        with open(os.path.join(WIDGET_DIR, "contents/ui/main.qml"), encoding="utf-8") as fh:
            self.assertIn('onLaunchRequested: id => root.showWindow("launch:" + id)', fh.read())

    def test_quick_add_offers_workspace_repos_on_an_empty_board(self):
        # Regresja: na pustej tablicy menu projektu miało tylko „bez projektu”. Jak w oknie — repozytoria
        # z katalogu projektów zakładają projekt razem z kartą (CreateCard z `repo`).
        empty = dict(preview.snapshot([], exists=False),
                     repos=[{"name": "plasma-tide", "path": "/w/plasma-tide", "aiDocs": "repo"},
                            {"name": "mobileapp", "path": "/w/mobileapp", "aiDocs": "tide"}])
        made = dict(empty, exists=True, repos=[{"name": "mobileapp", "path": "/w/mobileapp", "aiDocs": "tide"}],
                    projects=[{"slug": "plasma-tide", "name": "plasma-tide", "repo": "/w/plasma-tide", "tags": []}])
        with tempfile.TemporaryDirectory(prefix="tide-board-repos-") as tmp:
            preview.stage_board(tmp)
            harness = os.path.join(tmp, "Repos.qml")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write("""import QtQuick
import QtQuick.Window
import "board/contents/ui" as Board
import "board/contents/ui/boardtexts.js" as Texts
Window {
    width: 460; height: 520; visible: true
    property var log: []
    Board.BoardPopup {
        id: popup
        width: 420
        snapshot: %s
        onAddRequested: (title, project) => log.push("add:" + title + "|" + project)
        onProjectChosen: slug => { log.push("project:" + slug); quickProject = slug }
    }
    Timer {
        interval: 300; running: true
        onTriggered: {
            log.push("menu:" + popup.projectList.map(p => p.kind + "=" + p.name).join(","))
            popup.projectChosen(popup.projectList[1].slug)
            log.push("chip:" + Texts.projectName(popup.snapshot, popup.projectSlug))
            popup.input.text = "Pierwsza karta"
            popup.submit()
            log.push("request:" + JSON.stringify(Texts.projectRequest(popup.projectSlug)))
            popup.snapshot = %s
            log.push("after:" + popup.projectSlug)
            console.warn("REPOS " + JSON.stringify(log))
            Qt.quit()
        }
    }
}
""" % (json.dumps(empty, ensure_ascii=False), json.dumps(made, ensure_ascii=False)))
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
            log = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=60).stderr
        match = re.search(r"REPOS (.*)$", log, re.M)
        self.assertIsNotNone(match, log[-3000:])
        self.assertEqual(json.loads(match.group(1)), [
            "menu:none=bez projektu,repo=plasma-tide,repo=mobileapp",
            "project:repo:/w/plasma-tide",
            "chip:plasma-tide",
            "add:Pierwsza karta|repo:/w/plasma-tide",
            'request:{"repo":"/w/plasma-tide"}',
            "after:plasma-tide",
        ], "po założeniu projektu zapamiętane repozytorium wskazuje ten projekt")
        self.assertIsNone(re.search(QML_PROBLEMS, log), log[-3000:])

    def test_preparing_keeps_the_previous_snapshot_or_shows_loading_and_retries(self):
        """PT-385 (N4): Snapshot z `readState: "preparing"` (usługa po restarcie) nie daje „0 kart”: bez poprzedniej
        migawki — „wczytywanie” i ponowny odczyt najpóźniej po 2 s; z poprzednią — zostaje poprzednia."""
        with open(os.path.join(WIDGET_DIR, "contents/ui/main.qml"), encoding="utf-8") as fh:
            main = fh.read()
        self.assertIn("readonly property var snapshot: board.snapshot", main)
        self.assertIn("daemon: source", main)
        preparing = {"version": 0, "readState": "preparing", "exists": True, "cards": [], "projects": [],
                     "tags": [], "repos": [], "errors": []}
        ready = dict(preview.snapshot(preview.MOCK_CARDS), version=7, readState="ready")
        with tempfile.TemporaryDirectory(prefix="tide-board-preparing-") as tmp:
            preview.stage_board(tmp)
            harness = os.path.join(tmp, "Preparing.qml")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write("""import QtQuick
import QtQuick.Window
import "board/contents/ui" as Board
import "board/contents/ui/boardtexts.js" as Texts
Window {
    id: win
    width: 460; height: 520; visible: true
    property var log: []
    property double t0: 0
    QtObject {
        id: fake
        property var result: null
        property var refreshes: []
        signal refreshed()
        function refresh() { refreshes.push(Date.now() - win.t0) }
        function reply(value) { result = value; refreshed() }
    }
    Board.BoardSnapshot { id: board; daemon: fake }
    Board.CompactBoard { id: tile; counts: Texts.counts(board.snapshot); loading: board.loading }
    Board.SidebarBoard { id: side; y: 100; width: 300; snapshot: board.snapshot; loading: board.loading }
    Board.BoardPopup { id: popup; y: 260; width: 420; snapshot: board.snapshot; loading: board.loading }
    function state(tag) {
        const n = Texts.counts(board.snapshot)
        log.push([tag, board.loading, n.total, tile.summary, side.summary, popup.children[1].text,
                  Texts.toolTip(true, !!board.snapshot && board.snapshot.exists === true, n, board.loading)])
    }
    Timer {
        id: later
        interval: 2300
        onTriggered: {
            log.push(["retries", fake.refreshes.length > 0, fake.refreshes.length ? fake.refreshes[0] : -1])
            fake.reply(%s)
            state("ready")
            fake.reply(%s)
            state("preparing-again")
            console.warn("PREPARING " + JSON.stringify(log))
            Qt.quit()
        }
    }
    Component.onCompleted: {
        t0 = Date.now()
        fake.reply(%s)
        state("first")
        later.start()
    }
}
""" % (json.dumps(ready, ensure_ascii=False), json.dumps(preparing), json.dumps(preparing)))
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
            log = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=60).stderr
        match = re.search(r"PREPARING (.*)$", log, re.M)
        self.assertIsNotNone(match, log[-3000:])
        first, retries, after, again = json.loads(match.group(1))
        self.assertEqual(first, ["first", True, 0, "wczytywanie…", "wczytywanie…", "Tablica się wczytuje…",
                                 "Tablica pracy się wczytuje…"], "bez poprzedniej migawki — wczytywanie, nie „brak kart”")
        self.assertTrue(retries[1], "brak ponownego odczytu po „preparing”")
        self.assertLessEqual(retries[2], 2100, "ponowny odczyt najpóźniej po 2 s, nie po 60 s odpytywania")
        self.assertEqual(after[:4], ["ready", False, 11, "2 w toku · 1 czeka · 1 do sprawdzenia"])
        self.assertEqual(again[1:], after[1:], "„preparing” po restarcie zachowuje poprzednią migawkę")
        self.assertIsNone(re.search(QML_PROBLEMS, log), log[-3000:])

    def test_sidebar_tile(self):
        side = preview.render_sidebar()
        self.assertEqual((side["title"], side["summary"]), ("Tablica · 11 kart", "2 w toku · 1 czeka · 1 do sprawdzenia"))
        self.assertEqual(side["rows"], ["K-13", "K-15", "K-11"])
        offline = preview.render_sidebar(None, available=False)
        self.assertEqual((offline["title"], offline["rows"]), ("Tablica", []))

    def test_whole_sidebar_with_board_fits(self):
        # Cały pasek boczny (typowy i pracowity) z kafelkiem tablicy mieści się w wysokości lewego monitora.
        from widgets import preview as sidebar_preview
        board = preview.render_sidebar()["height"]
        for name in ("typical", "busy"):
            base = sidebar_preview.render_sidebar(sidebar_preview.SIDEBAR_SCENARIOS[name])["total"]
            total = base + board + sidebar_preview.SIDEBAR_APPLET_SPACING
            self.assertLessEqual(total, sidebar_preview.SIDEBAR_HEIGHT, (name, base, board))

    def test_preview_writes_pngs(self):
        with tempfile.TemporaryDirectory(prefix="tide-board-png-") as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                preview.main([tmp])
            names = sorted(os.listdir(tmp))
            self.assertEqual(names, ["board-popup-today.png", "board-popup.png", "board-sidebar.png",
                                     "board-tile-empty.png", "board-tile-mock.png", "board-tile-offline.png",
                                     "board-tile-today.png"])
            for name in names:
                self.assertGreater(os.path.getsize(os.path.join(tmp, name)), 1500, name)


@unittest.skipUnless(shutil.which("make"), "brak make")
class WidgetInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-widget-board-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.installed = os.path.join(self.env["XDG_DATA_HOME"], "plasma/plasmoids", WIDGET_ID)

    def make(self, target):
        subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True, capture_output=True, text=True)

    def test_install_then_upgrade_then_uninstall(self):
        self.make("install-widget-board")
        staged = os.path.join(self.env["BUILD_DIR"], "widgets", WIDGET_ID)
        self.assertEqual(calls(self.env), [f"kpackagetool6 -t Plasma/Applet -i {staged}"])
        files = files_under(self.installed)
        self.assertIn("contents/ui/BoardPopup.qml", files)
        self.assertIn("contents/ui/tide/Tokens.qml", files)
        self.make("install-widget-board")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -u {staged}")
        self.make("uninstall-widget-board")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -r {WIDGET_ID}")
        self.assertEqual(files_under(self.env["XDG_DATA_HOME"]), [])


if __name__ == "__main__":
    unittest.main()
