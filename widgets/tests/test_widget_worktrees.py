"""Kafelek worktrees: pakiet z biblioteką, ładowanie komponentów, instalacja i odinstalowanie.

kpackagetool6 jest atrapą (daemon/tests/fakebin.py), HOME i XDG_* są tymczasowe.
Wygląd QML nie jest testowany automatycznie — tylko to, że komponenty się ładują,
a pomocnicze funkcje (rozmiar, czas, liczba mnoga, kolor stanu) liczą poprawnie.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from . import REPO, WIDGETS_DIR
from daemon.tests.fakebin import calls, files_under, make_env

WIDGET_ID = "org.tide.worktrees"
WIDGET_DIR = os.path.join(WIDGETS_DIR, "worktrees")


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-stage-wt-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = os.path.join(self.tmp, "pkg")
        subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"), WIDGET_DIR, self.pkg], check=True)

    def test_package_contains_widget_and_library(self):
        files = files_under(self.pkg)
        for name in ("metadata.json", "contents/ui/main.qml", "contents/ui/SummaryView.qml",
                     "contents/ui/tide/qmldir", "contents/ui/tide/Tokens.qml", "contents/ui/tide/DaemonSource.qml"):
            self.assertIn(name, files)
        self.assertNotIn("contents/ui/WorktreesView.qml", files, "lista żyje teraz w oknie Tide Worktrees")

    def test_tile_opens_window_and_counts_groups(self):
        with open(os.path.join(WIDGET_DIR, "contents/ui/main.qml"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('"/Worktrees"', text)
        self.assertIn('source.call("ShowWindow"', text)
        self.assertIn('w.group === "clean"', text)
        self.assertIn('w.group === "active"', text)
        self.assertNotIn('"Remove"', text, "kafelek niczego nie usuwa — tylko okno, z zaznaczeniem")

    def test_qml_has_no_hardcoded_colors(self):
        for dirpath, _dirs, names in os.walk(WIDGET_DIR):
            for name in (n for n in names if n.endswith(".qml")):
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    found = re.findall(r'"#[0-9a-fA-F]{3,8}"', fh.read())
                self.assertEqual(found, [], f"kolor wpisany na sztywno w {name}")

    @unittest.skipUnless(shutil.which("qml6"), "brak qml6")
    def test_components_load_and_helpers_compute(self):
        harness = os.path.join(self.pkg, "contents/ui/Smoke.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write("""import QtQuick
import "tide" as Tide
Item {
    SummaryView { id: a; width: 320; height: 200; cleanCount: 16; cleanBytes: 19327352832; activeCount: 3
                  totalCount: 48; problem: "not-installed" }
    SummaryView { id: b; width: 320; height: 200; available: false }
    Tide.CountPill { tint: Tide.Tokens.positive; count: 4; emphasized: true }
    Component.onCompleted: {
        const T = Tide.Theme
        const out = [T.formatBytes(1288490189), T.formatBytes(-1), T.formatBytes(512),
                     T.plural(1, "a", "b", "c"), T.plural(3, "a", "b", "c"), T.plural(12, "a", "b", "c"),
                     T.plural(22, "a", "b", "c"), T.agoText(1000 - 30, 1000), T.agoText(1000 - 86400 * 3, 1000),
                     T.worktreeColor({ hasAgent: true }) === Tide.Tokens.working,
                     T.worktreeColor({ hasAgent: false, dirty: 0, unmerged: 0, merged: true, mainBranch: "m" })
                         === Tide.Tokens.positive,
                     T.worktreeColor({ hasAgent: false, dirty: 1, unmerged: 0 }) === Tide.Tokens.neutral]
        console.warn("SMOKE " + out.join("|"))
        Qt.quit()
    }
}
""")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        result = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=30)
        log = result.stderr
        self.assertIn("SMOKE 1,2 GB|…|512 B|a|b|c|b|teraz|3 dni temu|true|true|true", log)
        problems = [line for line in log.splitlines()
                    if re.search(r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member", line)]
        self.assertEqual(problems, [])


@unittest.skipUnless(shutil.which("make"), "brak make")
class WidgetInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-widget-wt-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.installed = os.path.join(self.env["XDG_DATA_HOME"], "plasma/plasmoids", WIDGET_ID)

    def make(self, target):
        subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True, capture_output=True, text=True)

    def test_install_then_upgrade_then_uninstall(self):
        self.make("install-widget-worktrees")
        staged = os.path.join(self.env["BUILD_DIR"], "widgets", WIDGET_ID)
        self.assertEqual(calls(self.env), [f"kpackagetool6 -t Plasma/Applet -i {staged}"])
        files = files_under(self.installed)
        self.assertIn("contents/ui/SummaryView.qml", files)
        self.assertIn("contents/ui/tide/Tokens.qml", files)

        self.make("install-widget-worktrees")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -u {staged}")

        self.make("uninstall-widget-worktrees")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -r {WIDGET_ID}")
        self.assertEqual(files_under(self.env["XDG_DATA_HOME"]), [])


if __name__ == "__main__":
    unittest.main()
