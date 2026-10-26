"""Pasek boczny Tide (#18): nagłówek org.tide.sidebar i tryb sidebarMode widżetów.

kpackagetool6 jest atrapą (daemon/tests/fakebin.py), HOME i XDG_* są tymczasowe.
Panel składa scripts/sidebar.py — jego testy są w scripts/tests (test_sidebar, test_panels_js).
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from . import REPO, WIDGETS_DIR
from daemon.tests.fakebin import calls, files_under, make_env

WIDGET_ID = "org.tide.sidebar"
WIDGET_DIR = os.path.join(WIDGETS_DIR, "sidebar")
FULL_VIEW_WIDGETS = ("agents", "system", "worktrees", "limits")
KCFG = "{http://www.kde.org/standards/kcfg/1.0}"


def read(*parts):
    with open(os.path.join(WIDGETS_DIR, *parts), encoding="utf-8") as fh:
        return fh.read()


class HeaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-stage-sidebar-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = os.path.join(self.tmp, "pkg")
        subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"), WIDGET_DIR, self.pkg], check=True)

    def test_package_contains_header_and_library(self):
        files = files_under(self.pkg)
        for name in ("metadata.json", "contents/ui/main.qml", "contents/ui/tide/Tokens.qml"):
            self.assertIn(name, files)

    def test_activation_keeps_the_autohiding_panel_out(self):
        text = read("sidebar", "contents/ui/main.qml")
        # Skrót aktywuje widżet; status RequiresAttention trzyma chowany panel na ekranie.
        self.assertIn("function onActivated()", text)
        self.assertIn("RequiresAttentionStatus", text)
        self.assertIn("Plasmoid.globalShortcut", text)
        self.assertNotRegex(text, r'"#[0-9a-fA-F]{3,8}"')


class SidebarModeTest(unittest.TestCase):
    def test_every_widget_has_the_sidebar_mode_setting_off_by_default(self):
        for widget in FULL_VIEW_WIDGETS:
            with self.subTest(widget=widget):
                root = ET.fromstring(read(widget, "contents/config/main.xml"))
                entry = root.find(f"{KCFG}group[@name='General']/{KCFG}entry[@name='sidebarMode']")
                self.assertIsNotNone(entry)
                self.assertEqual(entry.get("type"), "Bool")
                self.assertEqual(entry.find(f"{KCFG}default").text, "false")

    def test_sidebar_mode_shows_the_full_view_and_keeps_the_tile_elsewhere(self):
        for widget in FULL_VIEW_WIDGETS:
            with self.subTest(widget=widget):
                text = read(widget, "contents/ui/main.qml")
                self.assertIn("preferredRepresentation: Plasmoid.configuration.sidebarMode ? fullRepresentation"
                              " : compactRepresentation", text)

    def test_waiting_agent_does_not_pin_the_sidebar(self):
        text = read("agents", "contents/ui/main.qml")
        self.assertRegex(text, r"Plasmoid\.status: waitingCount > 0 && !Plasmoid\.configuration\.sidebarMode")

    @unittest.skipUnless(shutil.which("qml6"), "brak qml6")
    def test_worktrees_summary_in_the_sidebar_offers_the_window(self):
        tmp = tempfile.mkdtemp(prefix="tide-stage-wt-side-")
        self.addCleanup(shutil.rmtree, tmp)
        pkg = os.path.join(tmp, "pkg")
        subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"),
                        os.path.join(WIDGETS_DIR, "worktrees"), pkg], check=True)
        harness = os.path.join(pkg, "contents/ui/Smoke.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write("""import QtQuick
Item {
    SummaryView { id: ok; sidebar: true; width: 280; height: 160; cleanCount: 2; totalCount: 6 }
    SummaryView { id: broken; sidebar: true; width: 280; height: 160; problem: "no-service" }
    SummaryView { id: popup; width: 280; height: 160 }
    Component.onCompleted: {
        console.warn("SMOKE " + [ok.showProblem, broken.showProblem, popup.showProblem].join("|"))
        Qt.quit()
    }
}
""")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        log = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=30).stderr
        self.assertIn("SMOKE false|true|true", log)
        problems = [line for line in log.splitlines()
                    if re.search(r"Unable to assign|TypeError|ReferenceError|is not a type", line)]
        self.assertEqual(problems, [])
        self.assertIn("Otwórz okno Worktrees", read("worktrees", "contents/ui/SummaryView.qml"))

    @unittest.skipUnless(shutil.which("qml6"), "brak qml6")
    def test_dense_limit_rows_are_shorter(self):
        # Cztery widżety muszą się zmieścić w ok. 1000 px lewego monitora.
        tmp = tempfile.mkdtemp(prefix="tide-stage-limits-side-")
        self.addCleanup(shutil.rmtree, tmp)
        pkg = os.path.join(tmp, "pkg")
        subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"),
                        os.path.join(WIDGETS_DIR, "limits"), pkg], check=True, capture_output=True)
        harness = os.path.join(pkg, "contents/ui/Smoke.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write("""import QtQuick
Item {
    width: 280
    LimitsSource { id: src; port: 1; token: "" }
    MetricRow { id: tall; width: 280; label: "Limit tygodniowy"; value: 86; reset: "poniedziałek 11:51" }
    MetricRow { id: dense; width: 280; dense: true; label: "Limit tygodniowy"; value: 86; reset: "poniedziałek 11:51" }
    ServiceCard { id: card; width: 280; dense: true; source: src; serviceKey: "claude"; title: "Claude" }
    Component.onCompleted: {
        console.warn("SMOKE " + (dense.implicitHeight < tall.implicitHeight) + "|" + card.padding)
        Qt.quit()
    }
}
""")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        log = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=30).stderr
        self.assertIn("SMOKE true|10", log)
        problems = [line for line in log.splitlines()
                    if re.search(r"Unable to assign|TypeError|ReferenceError|is not a type", line)]
        self.assertEqual(problems, [])

    def test_system_view_has_a_dense_layout_for_the_sidebar(self):
        self.assertIn("dense: Plasmoid.configuration.sidebarMode", read("system", "contents/ui/main.qml"))
        self.assertEqual(read("system", "contents/ui/FullSystem.qml").count("visible: !full.dense"), 3)


@unittest.skipUnless(shutil.which("make"), "brak make")
class WidgetInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-widget-sidebar-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.installed = os.path.join(self.env["XDG_DATA_HOME"], "plasma/plasmoids", WIDGET_ID)

    def make(self, target):
        subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True, capture_output=True, text=True)

    def test_install_then_upgrade_then_uninstall(self):
        self.make("install-widget-sidebar")
        staged = os.path.join(self.env["BUILD_DIR"], "widgets", WIDGET_ID)
        self.assertEqual(calls(self.env), [f"kpackagetool6 -t Plasma/Applet -i {staged}"])
        self.assertIn("contents/ui/main.qml", files_under(self.installed))

        self.make("install-widget-sidebar")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -u {staged}")

        self.make("uninstall-widget-sidebar")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -r {WIDGET_ID}")
        self.assertEqual(files_under(self.env["XDG_DATA_HOME"]), [])


if __name__ == "__main__":
    unittest.main()
