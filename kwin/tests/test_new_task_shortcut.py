"""Skrypt KWin ze skrótem Meta+Alt+A (okno „Tide: nowe zadanie”): co rejestruje, co woła, instalacja.

Meta+Shift+A, o który prosiło zgłoszenie, jest w Plasmie zajęty („Przełączaj między aktywnościami
(odwrotnie)”), więc skrót to Meta+Alt+A. Skrypt jest wykonywany w QJSEngine z atrapami API KWin,
instalacja na tymczasowym HOME z atrapami kpackagetool6, kwriteconfig6 i dbus-send.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from daemon.tests.fakebin import calls, files_under, make_env
from kwin.tests.test_agent_jump import KWIN_STUBS

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT_DIR = os.path.join(REPO, "kwin", "new-task")
SCRIPT_ID = "tide-new-task"

try:
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtQml import QJSEngine
except ImportError:  # pragma: no cover
    QJSEngine = None


class ScriptTest(unittest.TestCase):
    def test_metadata(self):
        with open(os.path.join(SCRIPT_DIR, "metadata.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        self.assertEqual((meta["KPackageStructure"], meta["KPlugin"]["Id"]), ("KWin/Script", SCRIPT_ID))
        self.assertEqual(meta["X-Tide-Shortcut"], {"key": "Meta+Alt+A", "action": "TideNewAgentTask",
                                                   "label": "Tide: nowe zadanie dla agenta"})
        self.assertTrue(os.path.isfile(os.path.join(SCRIPT_DIR, "contents", meta["X-Plasma-MainScript"])))

    @unittest.skipIf(QJSEngine is None, "brak PyQt6.QtQml")
    def test_shortcut_opens_dialog_through_daemon(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        engine = QJSEngine()
        with open(os.path.join(SCRIPT_DIR, "contents/code/main.js"), encoding="utf-8") as fh:
            result = engine.evaluate(KWIN_STUBS + fh.read())
        self.assertFalse(result.isError(), result.toString())
        out = engine.evaluate("""
            registered.forEach(function (s) { s.callback(); });
            JSON.stringify({ shortcuts: registered.map(function (s) { return [s.name, s.text, s.keys]; }),
                             calls: dbusCalls });
        """)
        data = json.loads(out.toString())
        self.assertEqual(data["shortcuts"], [["TideNewAgentTask", "Tide: nowe zadanie dla agenta", "Meta+Alt+A"]])
        self.assertEqual(data["calls"], [["org.tide.Daemon", "/Tasks", "org.tide.Daemon.Tasks", "ShowDialog"]])
        with open(os.path.join(REPO, "daemon/tide_daemon/dbus_service.py"), encoding="utf-8") as fh:
            self.assertIn('<method name="ShowDialog">', fh.read())


@unittest.skipUnless(shutil.which("make"), "brak make")
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-kwin-task-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.installed = os.path.join(self.env["XDG_DATA_HOME"], "kwin/scripts", SCRIPT_ID)

    def make(self, target) -> str:
        return subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True, capture_output=True,
                              text=True).stdout

    def test_install_and_uninstall(self):
        out = self.make("install-new-task-shortcut")
        self.assertIn("skrót Meta+Alt+A", out)
        self.assertEqual(files_under(self.installed), ["contents/code/main.js", "metadata.json"])
        self.assertIn(f"kwriteconfig6 --file kwinrc --group Plugins --key {SCRIPT_ID}Enabled true", calls(self.env))
        self.make("uninstall-new-task-shortcut")
        log = calls(self.env)
        self.assertTrue(any("org.kde.KGlobalAccel.unregister string:kwin string:TideNewAgentTask" in c for c in log))
        self.assertEqual(log[-1], f"kpackagetool6 -t KWin/Script -r {SCRIPT_ID}")

    def test_warns_when_key_is_taken_and_ignores_meta_shift_a(self):
        with open(os.path.join(self.env["XDG_CONFIG_HOME"], "kglobalshortcutsrc"), "w", encoding="utf-8") as fh:
            fh.write("[plasmashell]\nprevious activity=Meta+Shift+A,none,Przełączaj między aktywnościami (odwrotnie)\n"
                     "[kwin]\nTideNewAgentTask=Meta+Alt+A,Meta+Alt+A,Tide: nowe zadanie dla agenta\n")
        self.assertNotIn("Uwaga", self.make("install-new-task-shortcut"), "własna akcja i Meta+Shift+A nie kolidują")
        with open(os.path.join(self.env["XDG_CONFIG_HOME"], "kglobalshortcutsrc"), "a", encoding="utf-8") as fh:
            fh.write("[spectacle]\nRegion=Meta+Alt+A,none,Zrzut obszaru\n")
        out = self.make("install-new-task-shortcut")
        self.assertIn("Uwaga: Meta+Alt+A ma już przypisane", out)
        self.assertIn("„Tide: nowe zadanie dla agenta”", out)


if __name__ == "__main__":
    unittest.main()
