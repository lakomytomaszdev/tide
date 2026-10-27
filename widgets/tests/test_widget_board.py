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
        from scripts.dock import DOCK_LEFT, DOCK_RIGHT
        from scripts.sidebar import WIDGETS
        self.assertEqual(DOCK_RIGHT[:2], [WIDGET_ID, "org.tide.agents"], "w docku po prawej, przed Agentami")
        self.assertNotIn(WIDGET_ID, DOCK_LEFT)
        self.assertEqual(WIDGETS.index(WIDGET_ID), WIDGETS.index("org.tide.agents") + 1, "w pasku bocznym pod Agentami")


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
        self.assertEqual(today["summary"], "6 do zrobienia", "bez pracy agentów — ile do zrobienia")
        empty = preview.render_tile(preview.TILE_SCENARIOS["empty"])
        self.assertEqual((empty["detail"], empty["summary"]), ("", "pusto"))
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
                 (dict(backlog=12), "Tablica", "12 kart", "pusto")]
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
        onProjectChosen: slug => { log.push("project:" + slug); quickProject = slug }
    }
    Timer {
        interval: 300; running: true
        onTriggered: {
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
        self.assertEqual(json.loads(match.group(1)), ["add:Eksport do CSV|plasma-tide", "project:",
                                                      "add:Bez projektu|", "open:K-13"],
                         "pusty tytuł i zapis w toku nic nie wysyłają")
        self.assertIsNone(re.search(QML_PROBLEMS, log), log[-3000:])

    def test_sidebar_tile(self):
        side = preview.render_sidebar()
        self.assertEqual((side["title"], side["summary"]), ("Tablica · 11 kart", "2 w toku · 1 czeka · 1 do sprawdzenia"))
        self.assertEqual(side["rows"], ["K-13", "K-15", "K-11"])
        offline = preview.render_sidebar(None, available=False)
        self.assertEqual((offline["title"], offline["rows"]), ("Tablica", []))

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
