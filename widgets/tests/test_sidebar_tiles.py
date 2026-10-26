"""Kafelki paska bocznego (sidebarMode): wiersze agentów, System, Worktrees, Limity AI i to,
że cały pasek mieści się w 1040 px bez przewijania.

Kafelki (SidebarAgents, SidebarSystem, SidebarWorktrees, SidebarLimits) i nagłówek nie zależą
od PlasmoidItem, więc qml6 offscreen ładuje je z przykładowymi danymi — tym samym kodem, który
rysuje podgląd (widgets/preview.py, sidebar-<scenariusz>.png). Bez Plasmy i bez usług.
"""
import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from widgets import preview
from widgets.tests.test_dock_tiles import png_size


def scenario(name="typical", **changes):
    data = copy.deepcopy(preview.SIDEBAR_SCENARIOS[name])
    data.update(changes)
    return data


def session(sid, state, ago=60, project=None, can=True):
    return preview.sidebar_session(sid, "claude", project or sid, state, ago, can=can)


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class SidebarTilesTest(unittest.TestCase):
    def test_typical_day_fits_without_scrolling(self):
        result = preview.render_sidebar(scenario())
        # Nagłówek i cztery kafelki jeden pod drugim, z marginesami panelu, w 1040 px.
        self.assertEqual(len(result["heights"]), 5)
        self.assertLessEqual(result["total"], preview.SIDEBAR_HEIGHT, result["heights"])
        # Zapas na dłuższe nazwy, ostrzeżenia i stany usług — pasek ma być spokojny, nie wypchany.
        self.assertLess(result["total"], 900, result["heights"])

    def test_busy_day_fits_too(self):
        result = preview.render_sidebar(scenario("busy"))
        self.assertLessEqual(result["total"], preview.SIDEBAR_HEIGHT, result["heights"])

    def test_agent_rows_show_state_and_only_waiting_gets_the_button(self):
        agents = preview.render_sidebar(scenario())["agents"]
        rows = agents["rows"]
        # Czekający pierwszy, potem pracujący, skończony i bezczynny; zakończona sesja ukryta.
        self.assertEqual([r["state"] for r in rows], ["waiting", "working", "working", "done", "idle"])
        self.assertEqual([r["project"] for r in rows],
                         ["legal-agents", "plasma-tide", "app5-mobile", "limity-ai", "dev-dashboard"])
        self.assertEqual([r["word"] for r in rows], ["czeka", "pracuje", "pracuje", "skończył", "bezczynna"])
        self.assertEqual([r["ago"] for r in rows], ["3 min", "teraz", "teraz", "12 min", "1 h"])
        self.assertEqual([r["button"] for r in rows], [True, False, False, False, False])
        self.assertEqual((agents["hidden"], agents["ended"]), (0, 1))
        self.assertEqual(agents["summary"], "2 pracują · 1 czeka · 1 gotowa")

    def test_waiting_without_a_known_window_has_no_button(self):
        data = scenario(sessions=[session("a", "waiting", can=False), session("b", "done")])
        rows = preview.render_sidebar(data)["agents"]["rows"]
        self.assertEqual([(r["state"], r["button"]) for r in rows], [("waiting", False), ("done", False)])

    def test_more_than_six_sessions_collapse_into_a_link(self):
        agents = preview.render_sidebar(scenario("busy"))["agents"]
        self.assertEqual(len(agents["rows"]), 6)
        self.assertEqual(agents["hidden"], 3)
        self.assertEqual([r["state"] for r in agents["rows"]][:2], ["waiting", "waiting"])
        self.assertEqual(sum(r["button"] for r in agents["rows"]), 2)

    def test_older_daemon_reply_is_a_calm_done_row(self):
        old = dict(session("a", "waiting"), reason="reply")
        rows = preview.render_sidebar(scenario(sessions=[old]))["agents"]["rows"]
        self.assertEqual((rows[0]["state"], rows[0]["word"], rows[0]["button"]), ("done", "skończył", False))

    def test_system_tile_texts(self):
        system = preview.render_sidebar(scenario())["system"]
        self.assertEqual(system["gpu"], "GPU 31% · VRAM 0,6/8 GB")
        self.assertEqual(system["disks"], ["/ 9,5 GB wolne", "/home 412 GB wolne"])
        # Worktrees leżą na /home, a pełny jest / — sama krótka uwaga, bez podpowiedzi o sprzątaniu.
        self.assertEqual(system["note"], "Dysk prawie pełny")
        busy = preview.render_sidebar(scenario("busy"))["system"]
        self.assertEqual(busy["note"], "Dysk prawie pełny · worktrees zwolnią 3,2 GB")

    def test_worktrees_and_limits_tiles(self):
        result = preview.render_sidebar(scenario())
        self.assertEqual(result["worktrees"], {"summary": "2 w pracy · 0 do sprzątnięcia", "problem": ""})
        self.assertEqual(result["limits"]["rows"], [["GPT", "91%", "tydzień · reset poniedziałek 20:00"],
                                                    ["Claude", "64%", "tydzień · reset czwartek 11:51"]])
        busy = preview.render_sidebar(scenario("busy"))
        self.assertEqual(busy["worktrees"]["summary"], "5 w pracy · 15 do sprzątnięcia (16 GB)")
        self.assertEqual(busy["limits"]["rows"][1], ["Claude", "9%", "sesja · reset 16:10"])

    def test_services_down_are_single_muted_lines(self):
        result = preview.render_sidebar(scenario("offline"))
        self.assertEqual(result["agents"]["rows"], [])
        self.assertEqual(result["system"]["notice"], "Zainstaluj pakiet ksystemstats")
        self.assertEqual(result["worktrees"]["problem"], "Usługa tide-daemon nie odpowiada")
        self.assertEqual(result["limits"]["notice"], "Usługa Limity AI nie działa")
        self.assertLess(result["total"], 600)

    def test_screenshot_has_the_sidebar_size(self):
        with tempfile.TemporaryDirectory(prefix="tide-sidebar-png-") as tmp:
            png = os.path.join(tmp, "sidebar.png")
            preview.render_sidebar(scenario(), png)
            self.assertEqual(png_size(png), (preview.SIDEBAR_WIDTH, preview.SIDEBAR_HEIGHT))


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class ButtonsAndPopupTest(unittest.TestCase):
    """Przyciski paska są prawdziwymi przyciskami, a lista sesji z popupu ładuje się osobno."""

    def run_qml(self, body):
        with tempfile.TemporaryDirectory(prefix="tide-sidebar-qml-") as tmp:
            preview.stage_all(tmp)
            harness = os.path.join(tmp, "agents/contents/ui/Harness.qml")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write(body)
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1",
                       XDG_CONFIG_DIRS="/etc/xdg")
            result = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=60)
        problems = [line for line in result.stderr.splitlines()
                    if re.search(r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member"
                                 r"|Cannot assign|Required property|is not defined|Invalid property", line)]
        self.assertEqual(problems, [], result.stderr)
        match = re.search(r"RESULT (\{.*\})", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        return json.loads(match.group(1))

    def test_text_button_is_an_accessible_button_and_clicks(self):
        result = self.run_qml("""import QtQuick
import "tide" as Tide
Item {
    property int clicks: 0
    Tide.TextButton { id: link; text: "+3 więcej"; onClicked: parent.clicks++ }
    Tide.TextButton { id: go; variant: "filled"; text: "Przejdź"; onClicked: parent.clicks++ }
    Component.onCompleted: {
        link.clicked()
        go.Keys.returnPressed(null)
        console.warn("RESULT " + JSON.stringify({
            role: link.Accessible.role === Accessible.Button, name: go.Accessible.name,
            focus: link.focusPolicy === Qt.StrongFocus, clicks: clicks, height: go.implicitHeight }))
        Qt.quit()
    }
}
""")
        self.assertTrue(result["role"])
        self.assertEqual(result["name"], "Przejdź")
        self.assertTrue(result["focus"])
        self.assertEqual(result["clicks"], 2)
        self.assertLessEqual(result["height"], 26)

    def test_sessions_view_of_the_popup_loads(self):
        sessions = json.dumps(preview.SESSIONS, ensure_ascii=False)
        result = self.run_qml("""import QtQuick
SessionsView {
    width: 440; height: 500
    sessions: %s
    nowTs: %d
    Component.onCompleted: {
        console.warn("RESULT " + JSON.stringify({ working: workingCount, waiting: waitingCount,
                                                   done: doneCount, shown: shownSessions.length }))
        Qt.quit()
    }
}
""" % (sessions, preview.NOW_TS))
        self.assertEqual(result, {"working": 1, "waiting": 1, "done": 2, "shown": 5})


if __name__ == "__main__":
    unittest.main()
