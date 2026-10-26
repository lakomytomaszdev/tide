"""Link do raportu porannego w widżetach: nagłówek paska bocznego („Raport · dziś 07:00”) i dolny
wiersz popupu agentów w docku, z brzoskwiniową kropką, dopóki raport jest nieotwarty.

qml6 offscreen z przykładowym Status() z tide-daemon (widgets/preview.py: report_status) w trzech
stanach — nowy, przeczytany, brak raportu — plus raport z wczoraj i usługa, która nie działa.
Zrzuty trafiają do katalogu tymczasowego; `widgets/preview.py` zapisuje te same do build/preview.
"""
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

from . import WIDGETS_DIR
from widgets import preview

PROBLEMS = r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|is not defined|Cannot read"


def png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def read(*parts):
    with open(os.path.join(WIDGETS_DIR, *parts), encoding="utf-8") as fh:
        return fh.read()


def sidebar(state):
    return dict(preview.SIDEBAR_SCENARIOS["typical"], report=preview.report_status(state))


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class SidebarHeaderTest(unittest.TestCase):
    def test_states_and_screenshots(self):
        with tempfile.TemporaryDirectory(prefix="tide-report-sidebar-") as tmp:
            results = {}
            for state in preview.REPORT_STATES:
                png = os.path.join(tmp, f"sidebar-report-{state}.png")
                results[state] = preview.render_sidebar(sidebar(state), png)
                self.assertEqual(png_size(png), (preview.SIDEBAR_WIDTH, preview.SIDEBAR_HEIGHT))
        unread, read_, none = (results[s]["header"] for s in preview.REPORT_STATES)
        self.assertEqual((unread["text"], unread["unread"]), ("Raport · dziś 07:00", True))
        self.assertEqual(unread["accessible"], "Raport poranny · dziś 07:00 · 1 czeka na ciebie · nowy, nieotwarty")
        self.assertEqual((read_["text"], read_["unread"]), ("Raport · dziś 07:00", False))
        self.assertEqual((none["text"], none["unread"]), ("", False), "bez raportu link znika")
        # Link nie podnosi nagłówka (21 px jak przedtem) i mieści się przed plakietką skrótu.
        heights = {s: r["heights"][0] for s, r in results.items()}
        self.assertEqual(set(heights.values()), {heights["none"]}, heights)
        self.assertLessEqual(heights["none"], 21)
        self.assertLess(unread["linkRight"], preview.SIDEBAR_WIDTH - 2 * preview.SIDEBAR_INSET - 80)

    def test_yesterday_older_and_no_daemon(self):
        result = preview.render_sidebar(sidebar("yesterday"))
        self.assertEqual(result["header"]["text"], "Raport · wczoraj")
        older = dict(preview.report_status("read"), generatedAt=preview.NOW_TS - 3 * 86400, day="2026-10-23")
        result = preview.render_sidebar(dict(preview.SIDEBAR_SCENARIOS["typical"], report=older))
        self.assertEqual(result["header"]["text"], "Raport · 23.10")
        # Raport bez znanego czasu (np. bez pliku stanu) — samo „Raport”.
        unknown = dict(preview.report_status("unread"), generatedAt=None)
        result = preview.render_sidebar(dict(preview.SIDEBAR_SCENARIOS["typical"], report=unknown))
        self.assertEqual((result["header"]["text"], result["header"]["unread"]), ("Raport", True))
        # Usługa nie działa: Status() bez odpowiedzi → null.
        result = preview.render_sidebar(preview.SIDEBAR_SCENARIOS["offline"])
        self.assertEqual(result["header"]["text"], "")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class AgentsPopupTest(unittest.TestCase):
    def test_states_screenshots_and_width(self):
        with tempfile.TemporaryDirectory(prefix="tide-report-popup-") as tmp:
            results = {}
            for state in preview.REPORT_STATES:
                png = os.path.join(tmp, f"agents-popup-report-{state}.png")
                results[state] = preview.render_popup(png=png, report=state)
                self.assertEqual(png_size(png), (preview.POPUP_WIDTH + 2 * preview.MARGIN,
                                                 preview.POPUP_HEIGHT + 2 * preview.MARGIN))
        unread, read_, none = (results[s]["header"] for s in preview.REPORT_STATES)
        self.assertEqual((unread["report"], unread["unread"]), ("Raport · dziś 07:00", True))
        self.assertEqual((read_["report"], read_["unread"]), ("Raport · dziś 07:00", False))
        self.assertEqual((none["report"], none["unread"]), ("", False))
        # Nagłówek popupu (liczniki, „Sesje”, „＋ Nowe zadanie”) się nie zmienia, a dolny wiersz
        # z linkiem mieści się w stałej szerokości popupu (26 jednostek siatki).
        self.assertEqual({r["header"]["implicitWidth"] for r in results.values()}, {none["implicitWidth"]})
        self.assertLessEqual(none["implicitWidth"], preview.POPUP_WIDTH)
        for state, result in results.items():
            self.assertLessEqual(result["header"]["footerImplicitWidth"], preview.POPUP_WIDTH, state)

    def test_popup_width_and_dock_tile_unchanged(self):
        main = read("agents", "contents/ui/main.qml")
        self.assertIn("Layout.preferredWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 26", main)
        compact = main[main.index("compactRepresentation:"):main.index("fullRepresentation:")]
        self.assertNotIn("report", compact.lower(), "kafelek w docku nie dostaje raportu")
        self.assertNotIn("report", read("agents", "contents/ui/CompactAgents.qml").lower())
        # Kafelek agentów w docku: ta sama szerokość co przed linkiem raportu (268 px w „busy”, budżet 275).
        result = preview.render(preview.SCENARIOS["busy"])
        self.assertLessEqual(result["widths"][0], 275, result["widths"])


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class ClickTest(unittest.TestCase):
    def test_click_asks_to_open(self):
        report = json.dumps(preview.report_status("unread"))
        with tempfile.TemporaryDirectory(prefix="tide-report-click-") as tmp:
            pkg = preview.stage_all(tmp)
            harness = os.path.join(pkg, "Harness.qml")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write("""import QtQuick
import "agents/contents/ui" as Agents
import "sidebar/contents/ui" as Sidebar
Item {
    width: 500; height: 600
    property int asked: 0
    function find(item, name) {
        if (item.objectName === name) return item
        for (const child of item.children) {
            const hit = find(child, name)
            if (hit) return hit
        }
        return null
    }
    Sidebar.Header { id: header; width: 400; report: %(report)s; nowTs: %(now)d; onReportRequested: parent.asked++ }
    Agents.SessionsView {
        id: view
        y: 40; width: 468; height: 500
        sessions: []
        nowTs: %(now)d
        report: %(report)s
        onReportRequested: parent.asked++
    }
    Component.onCompleted: {
        find(header, "reportLink").clicked()
        find(view, "reportButton").clicked()
        console.warn("ASKED " + asked)
        Qt.quit()
    }
}
""" % {"report": report, "now": preview.NOW_TS})
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1",
                       XDG_CONFIG_DIRS="/etc/xdg", TZ=preview.PREVIEW_TZ)
            log = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=60).stderr
        self.assertEqual([l for l in log.splitlines() if re.search(PROBLEMS, l)], [])
        match = re.search(r"ASKED (.*)$", log, re.M)
        self.assertIsNotNone(match, log[-2000:])
        self.assertEqual(match.group(1), "2")


class SourceTest(unittest.TestCase):
    def test_widgets_read_status_and_call_open(self):
        for widget in ("sidebar", "agents"):
            with self.subTest(widget=widget):
                text = read(widget, "contents/ui/main.qml")
                self.assertIn('path: "/Reports"', text)
                self.assertIn('method: "Status"', text)
                self.assertIn('call("Open", []', text)

    def test_dot_color_from_palette(self):
        text = read("lib", "ReportLink.qml")
        self.assertIn("Tokens.peach", text)
        self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', text), [])
        self.assertIn("ReportLink 1.0 ReportLink.qml", read("lib", "qmldir"))


if __name__ == "__main__":
    unittest.main()
