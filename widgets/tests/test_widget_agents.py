"""Widżet agentów: kolory z palety, pakiet z biblioteką, instalacja i odinstalowanie.

kpackagetool6 jest atrapą (daemon/tests/fakebin.py), HOME i XDG_* są tymczasowe.
Wygląd QML nie jest testowany automatycznie — tylko to, że komponenty się ładują.
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

PALETTE = os.path.join(REPO, "theme/palette/mist.json")
WIDGET_ID = "org.tide.agents"


def qml_files():
    for base in (os.path.join(WIDGETS_DIR, "lib"), os.path.join(WIDGETS_DIR, "agents")):
        for dirpath, _dirs, names in os.walk(base):
            yield from (os.path.join(dirpath, n) for n in names if n.endswith(".qml"))


class PaletteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-stage-")
        self.addCleanup(shutil.rmtree, self.tmp)
        out = os.path.join(self.tmp, "pkg")
        subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"),
                        os.path.join(WIDGETS_DIR, "agents"), out], check=True)
        self.pkg = out
        with open(os.path.join(out, "contents/ui/tide/Tokens.qml"), encoding="utf-8") as fh:
            self.tokens = fh.read()
        with open(PALETTE, encoding="utf-8") as fh:
            self.palette = json.load(fh)

    def token(self, name):
        match = re.search(rf"property \w+ {name}: (.+)$", self.tokens, re.M)
        self.assertIsNotNone(match, name)
        return json.loads(match.group(1))

    def test_state_colors_come_from_palette(self):
        pastel = self.palette["pastel"]
        self.assertEqual(self.token("working"), pastel[self.palette["semantic"]["working"]])
        self.assertEqual(self.token("waiting"), pastel[self.palette["semantic"]["waiting"]])
        # „Skończył” ma własny, spokojny kolor — inny niż „pracuje” i „czeka”.
        self.assertEqual(self.token("done"), pastel[self.palette["semantic"]["done"]])
        self.assertNotIn(self.token("done"), (self.token("working"), self.token("waiting")))
        self.assertEqual(self.token("textMuted"), self.palette["text"]["muted"])
        self.assertEqual(self.token("radiusCard"), self.palette["radius"]["card"])
        self.assertEqual(self.token("fontMono"), self.palette["fonts"]["mono"])

    def test_package_contains_library(self):
        files = files_under(self.pkg)
        for name in ("metadata.json", "contents/ui/main.qml", "contents/ui/SessionCard.qml",
                     "contents/ui/CompactAgents.qml", "contents/ui/tide/DockTile.qml", "contents/ui/tide/Texts.qml",
                     "contents/ui/tide/qmldir", "contents/ui/tide/Card.qml", "contents/ui/tide/Ring.qml",
                     "contents/ui/tide/MeterBar.qml", "contents/ui/tide/DaemonSource.qml"):
            self.assertIn(name, files)

    def test_qml_has_no_hardcoded_colors(self):
        for path in qml_files():
            with open(path, encoding="utf-8") as fh:
                found = re.findall(r'"#[0-9a-fA-F]{3,8}"', fh.read())
            self.assertEqual(found, [], f"kolor wpisany na sztywno w {os.path.relpath(path, REPO)}")

    @unittest.skipUnless(shutil.which("qml6"), "brak qml6")
    def test_library_components_load_without_errors(self):
        harness = os.path.join(self.pkg, "contents/ui/Smoke.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write("""import QtQuick
import "tide" as Tide
Item {
    Tide.Card { highlighted: true; accent: Tide.Tokens.waiting; Text { text: Tide.Theme.stateLabel("waiting") } }
    Tide.MeterBar { value: 0.5 }
    Tide.Ring { value: 0.3; width: 30; height: 30 }
    Tide.CountPill { sessionState: "working"; count: 3; emphasized: true }
    Tide.DockTile { label: "Agenci"; detail: "2 w pracy"; Text { text: "x" } }
    CompactAgents { workingCount: 2; waitingCount: 1; doneCount: 2 }
    Tide.CountPill { sessionState: "done"; count: 2 }
    Tide.StateDot { sessionState: "done" }
    SessionCard { session: ({ id: "x", tool: "claude", project: "p", branch: "b", worktree: "", title: "",
        state: "waiting", reason: "approval", sinceTs: 0, lastActivityTs: 0, lastAction: "Bash", canActivate: true,
        terminal: "Konsole" }) }
    SessionCard { session: ({ id: "y", tool: "codex", project: "p", branch: "b", worktree: "", title: "",
        state: "done", reason: "scheduled", sinceTs: 0, lastActivityTs: 0, lastAction: "", canActivate: true,
        terminal: "Codex" }) }
    Component.onCompleted: { console.warn("SMOKE-OK " + Tide.Tokens.name); Qt.quit() }
}
""")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        result = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=30)
        log = result.stderr
        self.assertIn("SMOKE-OK Mist", log)
        problems = [line for line in log.splitlines()
                    if re.search(r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member", line)]
        self.assertEqual(problems, [])


@unittest.skipUnless(shutil.which("make"), "brak make")
class WidgetInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-widget-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.installed = os.path.join(self.env["XDG_DATA_HOME"], "plasma/plasmoids", WIDGET_ID)

    def make(self, target):
        subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True, capture_output=True, text=True)

    def test_install_then_upgrade_then_uninstall(self):
        self.make("install-widget-agents")
        staged = os.path.join(self.env["BUILD_DIR"], "widgets", WIDGET_ID)
        self.assertEqual(calls(self.env), [f"kpackagetool6 -t Plasma/Applet -i {staged}"])
        files = files_under(self.installed)
        self.assertIn("contents/ui/main.qml", files)
        self.assertIn("contents/ui/tide/Tokens.qml", files)

        self.make("install-widget-agents")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -u {staged}")

        self.make("uninstall-widget-agents")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -r {WIDGET_ID}")
        self.assertEqual(files_under(self.env["XDG_DATA_HOME"]), [])


if __name__ == "__main__":
    unittest.main()
