"""Lista koordynatorów w widoku Sesje pokazuje Tide.ScrollBar od razu po otwarciu (K-128, makieta S-9 wariant A).

Test offscreen na prawdziwym widoku z ponad 20 koordynatorami: pasek jest widoczny bez przewijania,
przy krótkiej liście go nie ma, a log nie ma ostrzeżeń QML (w tym „binding loop”).
"""
import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from daemon.tests import coordfix as F
from .test_window import QML_PROBLEMS, fixture_data, stage

PROBE = r"""
import importlib.util, json, sys
from PyQt6.QtCore import QObject, QTimer, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine
pkg, data, select = sys.argv[1:4]
view = pkg + "/views/sessions"
spec = importlib.util.spec_from_file_location("tide_sessions_backend", view + "/backend.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
app = QGuiApplication([])
with open(data, encoding="utf-8") as fh:
    backend = mod.Backend(None, json.load(fh), select=select)
engine = QQmlApplicationEngine()
engine.setInitialProperties({"source": backend})
engine.loadData(b"import QtQuick\nWindow { width: 1680; height: 980; visible: true\n"
                b"  property var source: null\n  SessionsView { anchors.fill: parent; backend: source } }\n",
                QUrl.fromLocalFile(view + "/qml/Probe.qml"))
root = engine.rootObjects()[0]

def report():
    flick = root.findChild(QObject, "coordinatorFlick")
    bar = root.findChild(QObject, "coordinatorScrollBar")
    print(json.dumps({"contentHeight": flick.property("contentHeight"), "height": flick.property("height"),
                      "contentY": flick.property("contentY"), "visible": bar.property("visible"),
                      "size": bar.property("size")}))
    app.quit()
QTimer.singleShot(800, report)
app.exec()
"""


def probe(pkg, data_path, select):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
    result = subprocess.run(["/usr/bin/python3", "-c", PROBE, pkg, data_path, select], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout.strip().splitlines()[-1]), result.stderr


class CoordinatorScrollBarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-sess-sb-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = stage(os.path.join(self.tmp, "pkg"))
        self.data = fixture_data(self.tmp)
        self.board_id = "claude:" + F.CLAUDE_COORD

    def write(self, count):
        first = self.data["coordinators"][0]
        self.data["coordinators"] = [dict(copy.deepcopy(first), id=f"claude:extra-{i}", title=f"Koordynator {i}")
                                     for i in range(count)] + self.data["coordinators"][1:]
        path = os.path.join(self.tmp, "data.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh)
        return path

    def test_long_coordinator_list_shows_the_bar_without_scrolling(self):
        shown, log = probe(self.pkg, self.write(24), self.board_id)
        self.assertGreater(shown["contentHeight"], shown["height"], shown)
        self.assertEqual(shown["contentY"], 0, "pasek widać bez przewijania")
        self.assertTrue(shown["visible"], shown)
        self.assertLess(shown["size"], 1.0)
        self.assertIsNone(re.search(QML_PROBLEMS, log), log[-3000:])

    def test_short_list_has_no_bar(self):
        shown, log = probe(self.pkg, self.write(1), self.board_id)
        self.assertLessEqual(shown["contentHeight"], shown["height"], shown)
        self.assertFalse(shown["visible"], shown)
        self.assertIsNone(re.search(QML_PROBLEMS, log), log[-3000:])


if __name__ == "__main__":
    unittest.main()
