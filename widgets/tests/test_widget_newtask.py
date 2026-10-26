"""Widżet „Nowe zadanie” (org.tide.newtask) i przycisk „＋ Nowe zadanie” w widżecie agentów.

Kafelki ładowane przez qml6 offscreen (bez Plasmy), instalacja z atrapą kpackagetool6 na tymczasowym HOME.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from . import REPO, WIDGETS_DIR
from daemon.tests.fakebin import calls, files_under, make_env
from widgets import preview

WIDGET_ID = "org.tide.newtask"
WIDGET_DIR = os.path.join(WIDGETS_DIR, "newtask")
PROBLEMS = r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|is not defined|Cannot read"


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class TilesTest(unittest.TestCase):
    def test_tiles_buttons_and_new_tag(self):
        with tempfile.TemporaryDirectory(prefix="tide-newtask-") as tmp:
            pkg = preview.stage_all(tmp)
            harness = os.path.join(pkg, "Harness.qml")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write("""import QtQuick
import "newtask/contents/ui" as NewTask
import "agents/contents/ui" as Agents
Item {
    width: 420; height: 900
    property int asked: 0
    function find(item, name) {
        if (item.objectName === name) return item
        for (const child of item.children) {
            const hit = find(child, name)
            if (hit) return hit
        }
        return null
    }
    NewTask.CompactNewTask { id: dock; height: 48 }
    NewTask.CompactNewTask { id: off; height: 48; available: false }
    NewTask.SidebarNewTask { id: side; width: 392; onOpenRequested: parent.asked++ }
    NewTask.SidebarNewTask { id: missing; width: 392; problem: "not-installed" }
    Agents.SessionsView {
        id: view
        width: 460; height: 400
        sessions: %(sessions)s
        nowTs: %(now)d
        onNewTaskRequested: parent.asked++
    }
    Agents.SidebarAgents {
        id: sideAgents
        width: 392
        sessions: %(sessions)s
        nowTs: %(now)d
        onNewTaskRequested: parent.asked++
    }
    Component.onCompleted: {
        console.warn("DOCK " + [dock.label, dock.summary, off.summary, dock.implicitHeight <= 48].join("|"))
        console.warn("SIDE " + side.hint + "|" + missing.hint)
        side.openRequested()
        find(view, "newTaskButton").clicked()
        find(sideAgents, "newTaskButton").clicked()
        console.warn("ASKED " + asked)
        console.warn("FRESH " + sideAgents.rows.filter(r => r.fresh).map(r => r.project).join(","))
        Qt.quit()
    }
}
""" % {"sessions": json.dumps(preview.SESSIONS, ensure_ascii=False), "now": preview.NOW_TS})
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", XDG_CONFIG_DIRS="/etc/xdg")
            log = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=60).stderr

        def warned(tag):
            match = re.search(rf"{tag} (.*)$", log, re.M)
            return match.group(1) if match else log[-2000:]

        self.assertEqual([l for l in log.splitlines() if re.search(PROBLEMS, l)], [])
        self.assertEqual(warned("DOCK"), "Zadanie|nowe dla agenta|usługa nie działa|true")
        self.assertEqual(warned("SIDE"), "Repozytorium, Claude Code albo Codex, polecenie i zrzuty · Meta+Alt+A|"
                                         "Okno nie jest zainstalowane (make install-new-task)")
        self.assertEqual(warned("ASKED"), "3")
        self.assertEqual(warned("FRESH"), "alpha")

    def test_qml_has_no_hardcoded_colors(self):
        for dirpath, _dirs, names in os.walk(WIDGET_DIR):
            for name in names:
                if name.endswith(".qml"):
                    with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                        self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', fh.read()), [], name)


@unittest.skipUnless(shutil.which("make"), "brak make")
class InstallTest(unittest.TestCase):
    def test_install_then_uninstall(self):
        with tempfile.TemporaryDirectory(prefix="tide-newtask-install-") as tmp:
            env = make_env(tmp)
            installed = os.path.join(env["XDG_DATA_HOME"], "plasma/plasmoids", WIDGET_ID)
            subprocess.run(["make", "-s", "-C", REPO, "install-widget-newtask"], env=env, check=True,
                           capture_output=True)
            files = files_under(installed)
            for name in ("metadata.json", "contents/ui/main.qml", "contents/ui/CompactNewTask.qml",
                         "contents/ui/SidebarNewTask.qml", "contents/ui/tide/NewTag.qml", "contents/config/main.xml"):
                self.assertIn(name, files)
            subprocess.run(["make", "-s", "-C", REPO, "uninstall-widget-newtask"], env=env, check=True,
                           capture_output=True)
            self.assertFalse(os.path.exists(installed))
            self.assertIn(f"kpackagetool6 -t Plasma/Applet -r {WIDGET_ID}", calls(env))

    def test_dock_lists_the_tile_next_to_agents(self):
        from scripts.dock import DOCK_WIDGETS
        self.assertEqual(DOCK_WIDGETS[:2], ["org.tide.agents", WIDGET_ID])


if __name__ == "__main__":
    unittest.main()
