"""Skrypt KWin ze skrótem Meta+A: co rejestruje, co woła, instalacja i wykrywanie kolizji skrótu.

Skrypt jest wykonywany w QJSEngine z atrapami registerShortcut i callDBus (API skryptów KWin),
więc sprawdzamy zachowanie, a nie tekst. Instalacja idzie na tymczasowym HOME z atrapami
kpackagetool6, kwriteconfig6 i dbus-send — prawdziwy KWin ani skróty nie są dotykane.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from daemon.tests.fakebin import calls, files_under, make_env
from kwin.shortcuts import conflicts

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT_DIR = os.path.join(REPO, "kwin", "agent-jump")
SCRIPT_ID = "tide-agent-jump"

try:
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtQml import QJSEngine
except ImportError:  # pragma: no cover
    QJSEngine = None

KWIN_STUBS = """
var registered = [];
var dbusCalls = [];
function registerShortcut(name, text, keys, callback) {
    registered.push({ name: name, text: text, keys: keys, callback: callback });
}
function callDBus() { dbusCalls.push(Array.prototype.slice.call(arguments)); }
"""


class ScriptTest(unittest.TestCase):
    def test_metadata_points_to_main_script(self):
        with open(os.path.join(SCRIPT_DIR, "metadata.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        self.assertEqual(meta["KPackageStructure"], "KWin/Script")
        self.assertEqual(meta["KPlugin"]["Id"], SCRIPT_ID)
        self.assertEqual(meta["X-Plasma-API"], "javascript")
        self.assertTrue(os.path.isfile(os.path.join(SCRIPT_DIR, "contents", meta["X-Plasma-MainScript"])))

    @unittest.skipIf(QJSEngine is None, "brak PyQt6.QtQml")
    def test_shortcut_calls_daemon(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        engine = QJSEngine()
        with open(os.path.join(SCRIPT_DIR, "contents/code/main.js"), encoding="utf-8") as fh:
            source = fh.read()
        result = engine.evaluate(KWIN_STUBS + source)
        self.assertFalse(result.isError(), result.toString())
        out = engine.evaluate("""
            registered.forEach(function (s) { s.callback(); });
            JSON.stringify({ shortcuts: registered.map(function (s) { return [s.name, s.keys]; }), calls: dbusCalls });
        """)
        self.assertFalse(out.isError(), out.toString())
        data = json.loads(out.toString())
        self.assertEqual(data["shortcuts"], [["TideJumpToWaitingAgent", "Meta+A"]])
        self.assertEqual(data["calls"], [["org.tide.Daemon", "/Agents", "org.tide.Daemon.Agents",
                                          "ActivateLongestWaiting"]])

    def test_called_method_is_part_of_daemon_contract(self):
        with open(os.path.join(REPO, "daemon/tide_daemon/dbus_service.py"), encoding="utf-8") as fh:
            self.assertIn('<method name="ActivateLongestWaiting">', fh.read())


class ConflictTest(unittest.TestCase):
    def test_reports_other_actions_with_the_same_key(self):
        tmp = tempfile.mkdtemp(prefix="tide-shortcuts-")
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "kglobalshortcutsrc")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[plasmashell]\n_k_friendly_name=plasmashell\n"
                     "next activity=Meta+A,none,Przełączaj między aktywnościami\n"
                     "show dashboard=Ctrl+F12,Ctrl+F12,Pokaż pulpit\n"
                     "[kwin]\nTideJumpToWaitingAgent=Meta+A,Meta+A,Tide: przejdź\n"
                     "Other=Meta+Shift+A\tMeta+A,none,Dwa klawisze\n")
        self.assertEqual(conflicts(path, "Meta+A", "kwin/TideJumpToWaitingAgent"),
                         ["plasmashell/next activity (Przełączaj między aktywnościami)", "kwin/Other (Dwa klawisze)"])
        self.assertEqual(conflicts(os.path.join(tmp, "brak"), "Meta+A"), [])


@unittest.skipUnless(shutil.which("make"), "brak make")
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-kwin-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.installed = os.path.join(self.env["XDG_DATA_HOME"], "kwin/scripts", SCRIPT_ID)

    def make(self, target) -> str:
        return subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True, capture_output=True,
                              text=True).stdout

    def test_install_enables_script_then_uninstall_cleans_up(self):
        self.make("install-agent-jump")
        self.assertEqual(files_under(self.installed), ["contents/code/main.js", "metadata.json"])
        log = calls(self.env)
        self.assertEqual(log[0], f"kpackagetool6 -t KWin/Script -i {SCRIPT_DIR}")
        self.assertIn(f"kwriteconfig6 --file kwinrc --group Plugins --key {SCRIPT_ID}Enabled true", log)
        self.assertTrue(any("org.kde.kwin.Scripting.start" in c for c in log))

        self.make("install-agent-jump")
        self.assertIn(f"kpackagetool6 -t KWin/Script -u {SCRIPT_DIR}", calls(self.env))

        self.make("uninstall-agent-jump")
        log = calls(self.env)
        self.assertIn(f"kwriteconfig6 --file kwinrc --group Plugins --key {SCRIPT_ID}Enabled --delete", log)
        self.assertEqual(log[-1], f"kpackagetool6 -t KWin/Script -r {SCRIPT_ID}")
        self.assertEqual(files_under(self.env["XDG_DATA_HOME"]), [])

    def test_install_warns_when_key_is_taken(self):
        with open(os.path.join(self.env["XDG_CONFIG_HOME"], "kglobalshortcutsrc"), "w", encoding="utf-8") as fh:
            fh.write("[plasmashell]\nnext activity=Meta+A,none,Przełączaj między aktywnościami\n")
        out = self.make("install-agent-jump")
        self.assertIn("Uwaga: Meta+A ma już przypisane", out)
        self.assertIn("plasmashell/next activity", out)


if __name__ == "__main__":
    unittest.main()
