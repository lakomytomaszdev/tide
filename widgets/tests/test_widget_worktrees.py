"""Widżet worktrees: pakiet z biblioteką, ładowanie komponentów, instalacja i odinstalowanie.

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
        for name in ("metadata.json", "contents/ui/main.qml", "contents/ui/WorktreeRow.qml",
                     "contents/ui/WorktreesView.qml", "contents/ui/tide/qmldir", "contents/ui/tide/Tokens.qml", "contents/ui/tide/DaemonSource.qml"):
            self.assertIn(name, files)

    def test_ui_says_merged_means_local_main_branch(self):
        texts = {}
        for name in ("main.qml", "WorktreesView.qml"):
            with open(os.path.join(WIDGET_DIR, "contents/ui", name), encoding="utf-8") as fh:
                texts[name] = fh.read()
        self.assertIn("scalony z lokalną gałęzią główną", texts["WorktreesView.qml"])
        self.assertIn("Wyczyść scalone (", texts["WorktreesView.qml"])
        self.assertIn('"/Worktrees"', texts["main.qml"])

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
    WorktreeRow { worktree: ({ repo: "alpha", repoPath: "/r/alpha", path: "/r/alpha-wt/x", name: "x",
        branch: "feat/x", detached: false, head: "abcdef1", isMain: false, hasAgent: false, dirty: 2,
        mainBranch: "master", merged: false, unmerged: 3, lastChangeTs: 0, size: 1288490189,
        locked: false, missing: false, removable: false, blocker: "dirty" }); canRider: true }
    Tide.CountPill { tint: Tide.Tokens.positive; count: 4; emphasized: true }
    WorktreesView {
        id: view
        width: 500; height: 700
        worktrees: [
            { repo: "alpha", repoPath: "/r/alpha", path: "/r/alpha", name: "alpha", branch: "master",
              detached: false, head: "1234567", isMain: true, hasAgent: false, dirty: 0, mainBranch: "master",
              merged: true, unmerged: 0, lastChangeTs: 900, size: 5000000, locked: false, missing: false,
              removable: false, blocker: "main" },
            { repo: "alpha", repoPath: "/r/alpha", path: "/r/wt/m", name: "m", branch: "feat/m", detached: false,
              head: "2345678", isMain: false, hasAgent: false, dirty: 0, mainBranch: "master", merged: true,
              unmerged: 0, lastChangeTs: 800, size: 2000000, locked: false, missing: false, removable: true,
              blocker: "" },
            { repo: "beta", repoPath: "/r/beta", path: "/r/beta-wt/t", name: "t", branch: "", detached: true,
              head: "3456789", isMain: false, hasAgent: true, dirty: -1, mainBranch: "", merged: false,
              unmerged: null, lastChangeTs: 0, size: -1, locked: true, missing: true, removable: false,
              blocker: "missing" }]
        launchers: ({ terminal: true, rider: true })
    }
    Component.onCompleted: {
        for (const state of ["confirm", "running", "done", ""]) {
            view.cleanup = state
            view.cleanupResults = [{ path: "/r/wt/m", removed: true, freed: 2000000, branch: "feat/m",
                                     branchDeleted: false, branchMessage: "gałąź zostaje: x" },
                                   { path: "/r/x", removed: false, reason: "dirty", message: "ma zmiany" }]
        }
        console.warn("VIEW " + view.groups.length + "|" + view.cleanable.length + "|" + view.linked.length)
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
        self.assertIn("VIEW 2|1|2", log)
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
        self.assertIn("contents/ui/WorktreeRow.qml", files)
        self.assertIn("contents/ui/WorktreesView.qml", files)
        self.assertIn("contents/ui/tide/Tokens.qml", files)

        self.make("install-widget-worktrees")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -u {staged}")

        self.make("uninstall-widget-worktrees")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -r {WIDGET_ID}")
        self.assertEqual(files_under(self.env["XDG_DATA_HOME"]), [])


if __name__ == "__main__":
    unittest.main()
