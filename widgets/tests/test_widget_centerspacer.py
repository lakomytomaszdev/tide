"""Widżet „Środek”: rachunek odstępu, który trzyma zegar na środku górnego paska.

03.11.2026: lewy odstęp Plasmy liczył środek z deklarowanej szerokości globalnego menu i nie przeliczał się,
gdy menu chowało się przy oknie bez menu — zegar uciekał o ~500 px w lewo. Odstęp Tide mierzy faktyczne
położenie zegara. Logikę sprawdzamy w qml6 bez Plasmy, a pakiet przez stage.py.
"""
import json
import os
import re
import subprocess
import tempfile
import unittest

from . import REPO, WIDGETS_DIR

CODE = os.path.join(WIDGETS_DIR, "centerspacer", "contents", "code")


class CenterLogicTest(unittest.TestCase):
    def run_qml(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            ui = os.path.join(tmp, "ui")
            os.makedirs(ui)
            os.symlink(CODE, os.path.join(tmp, "code"))
            harness = os.path.join(ui, "harness.qml")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write(body)
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
            result = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=60)
        match = re.search(r"RESULT (\{.*\})", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        return json.loads(match.group(1))

    def test_clock_is_moved_to_the_panel_centre_whatever_the_menu_did(self):
        result = self.run_qml(r"""import QtQuick
import "../code/center.js" as Center
Item {
    Component.onCompleted: {
        const own = { visible: true, width: 666 }
        const hiddenMenu = { visible: false, width: 496 }
        const clock = { visible: true, width: 120 }
        console.warn("RESULT " + JSON.stringify({
            // Stan z 03.11: zegar (712..832) na pasku 2560 px — odstęp ma urosnąć o 508 px.
            grow: Center.nextSize(666, 772, 2560),
            // Menu wróciło i zegar przesunął się za środek — odstęp maleje.
            shrink: Center.nextSize(1174, 1780, 2560),
            centred: Center.nextSize(1174, 1280.3, 2560),
            neverZero: Center.nextSize(10, 2000, 2560),
            next: Center.nextShown([hiddenMenu, own, { visible: true, width: 0 }, clock], own) === clock,
            none: Center.nextShown([own], own)
        }))
        Qt.quit()
    }
}
""")
        self.assertEqual(result["grow"], 1174)
        self.assertEqual(result["shrink"], 674)
        self.assertEqual(result["centred"], 1174)
        self.assertEqual(result["neverZero"], 1)
        self.assertTrue(result["next"])
        self.assertIsNone(result["none"])

    def test_package_stages_with_its_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "pkg")
            subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"),
                            os.path.join(WIDGETS_DIR, "centerspacer"), out], check=True, capture_output=True)
            self.assertTrue(os.path.isfile(os.path.join(out, "contents", "code", "center.js")))
            meta = json.load(open(os.path.join(out, "metadata.json"), encoding="utf-8"))
            self.assertEqual(meta["KPlugin"]["Id"], "org.tide.centerspacer")
