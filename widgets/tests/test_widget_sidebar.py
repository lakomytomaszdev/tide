"""Pasek boczny Tide (#18): nagłówek org.tide.sidebar i tryb sidebarMode widżetów.

kpackagetool6 jest atrapą (daemon/tests/fakebin.py), HOME i XDG_* są tymczasowe.
Panel składa scripts/sidebar.py — jego testy są w scripts/tests (test_sidebar, test_panels_js).
"""
import os
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

    def test_sidebar_mode_uses_the_sidebar_tiles(self):
        # Pasek ma własne, zwarte kafelki; popupy kafelków w docku zostają bez zmian.
        for widget, tile, popup in (("agents", "SidebarAgents", "SessionsView"), ("system", "SidebarSystem", "FullSystem"),
                                    ("worktrees", "SidebarWorktrees", "SummaryView"),
                                    ("limits", "SidebarLimits", "ServiceCard")):
            with self.subTest(widget=widget):
                text = read(widget, "contents/ui/main.qml")
                self.assertIn("sourceComponent: full.sidebar ? sidebarView : popupView", text)
                self.assertIn(tile + " {", text)
                self.assertIn(popup + " {", text)
                self.assertIn("Tide.SidebarTile", read(widget, "contents/ui", tile + ".qml"))
                # Kafelek nie rozciąga się na resztę paska — wolne miejsce bierze odstęp na końcu.
                self.assertNotIn("Layout.fillHeight: Plasmoid.configuration.sidebarMode", text)

    def test_more_sessions_open_the_full_list_next_to_the_sidebar(self):
        text = read("agents", "contents/ui/main.qml")
        self.assertIn("onMoreRequested: morePopup.visible = !morePopup.visible", text)
        self.assertIn("PlasmaCore.Dialog {", text)
        self.assertIn("visualParent: side.moreButton", text)

    def test_popups_have_no_dense_variant_left(self):
        for path in (("system", "contents/ui/FullSystem.qml"), ("limits", "contents/ui/ServiceCard.qml"),
                     ("limits", "contents/ui/MetricRow.qml"), ("worktrees", "contents/ui/SummaryView.qml")):
            with self.subTest(path=path):
                self.assertNotIn("dense", read(*path))


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
