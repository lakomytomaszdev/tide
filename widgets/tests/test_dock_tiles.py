"""Kafelki widżetów w docku: teksty (liczba mnoga, stany puste), wymiary i podpowiedzi.

Kafelki (CompactAgents, CompactWorktrees, CompactSystem, CompactLimits) nie zależą od
PlasmoidItem, więc qml6 offscreen ładuje je z przykładowymi danymi — tym samym kodem,
który rysuje podgląd (widgets/preview.py). Bez Plasmy, tide-daemon i usługi Limity AI.
"""
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

from . import WIDGETS_DIR
from widgets import preview


def scenario(**changes):
    """Scenariusz „busy” z podmienionymi częściami, np. agents={"working": 1, ...}."""
    data = json.loads(json.dumps(preview.SCENARIOS["busy"]))
    for key, value in changes.items():
        data[key].update(value)
    return data


def png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class TileTextsTest(unittest.TestCase):
    def test_busy_day(self):
        result = preview.render(preview.SCENARIOS["busy"])
        self.assertEqual(result["agents"], {"label": "Agenci", "summary": "2 pracują · 1 czeka"})
        # Ten sam układ co agenci: sam podpis, a w wartości niezerowe części z kropkami.
        self.assertEqual(result["worktrees"], {"label": "Worktrees", "detail": "",
                                               "summary": "5 w pracy · 15 do sprzątnięcia"})
        self.assertEqual(result["system"]["lines"], ["CPU 34%", "RAM 61%", "Dysk 88%"])
        self.assertEqual(result["limits"]["lines"], ["GPT 91%", "Claude 86%"])
        # Dwie linie mieszczą się w 48 px kafelka, a cztery kafelki z odstępami 12 px — w 800 px
        # (dock ma 2560 px, aplikacje są pośrodku).
        self.assertEqual(result["fits"], [True] * 4)
        self.assertLess(result["total"], 800, result["widths"])

    def test_plural_forms(self):
        cases = [((1, 0), "1 pracuje"), ((2, 0), "2 pracują"), ((5, 0), "5 pracują"),
                 ((0, 1), "1 czeka"), ((0, 3), "3 czekają"), ((0, 12), "12 czekają"),
                 ((1, 1), "1 pracuje · 1 czeka"), ((22, 4), "22 pracują · 4 czekają")]
        for (working, waiting), expected in cases:
            with self.subTest(working=working, waiting=waiting):
                result = preview.render(scenario(agents={"working": working, "waiting": waiting}))
                self.assertEqual(result["agents"]["summary"], expected)

    def test_empty_states(self):
        quiet = preview.render(preview.SCENARIOS["quiet"])
        self.assertEqual(quiet["agents"]["summary"], "brak sesji")
        self.assertEqual(quiet["worktrees"]["summary"], "nic do sprzątnięcia")
        self.assertEqual(quiet["worktrees"]["detail"], "")
        self.assertEqual(quiet["system"]["lines"], ["CPU 7%", "RAM 100%", "Dysk —"])
        self.assertEqual(quiet["limits"]["lines"], ["GPT —", "Claude 12%"])

        offline = preview.render(preview.SCENARIOS["offline"])
        self.assertEqual(offline["agents"]["summary"], "usługa nie działa")
        self.assertEqual(offline["worktrees"]["summary"], "usługa nie działa")
        self.assertEqual(offline["system"]["lines"], ["CPU —", "RAM —", "Dysk —"])
        self.assertEqual(offline["limits"]["lines"], ["GPT —", "Claude —"])

    def test_screenshot_has_dock_height(self):
        with tempfile.TemporaryDirectory(prefix="tide-dock-png-") as tmp:
            png = os.path.join(tmp, "tiles.png")
            result = preview.render(preview.SCENARIOS["busy"], png)
            width, height = png_size(png)
        self.assertEqual(height, preview.DOCK_HEIGHT)
        self.assertEqual(width, result["total"] + 2 * preview.MARGIN)


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class ToolTipTest(unittest.TestCase):
    """Podpowiedzi kafelków (Tide.Texts) — jedna linia wyjaśnienia po polsku."""

    def test_tooltips(self):
        with tempfile.TemporaryDirectory(prefix="tide-texts-") as tmp:
            pkg = preview.stage_all(tmp)
            harness = os.path.join(pkg, "agents", "contents", "ui", "Texts.qml")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write("""import QtQuick
import "tide" as Tide
Item {
    Component.onCompleted: {
        const T = Tide.Texts
        console.warn("RESULT " + JSON.stringify({
            agents: [T.agentsToolTip(true, 2, 1), T.agentsToolTip(true, 1, 3), T.agentsToolTip(true, 0, 0),
                     T.agentsToolTip(true, 0, 2), T.agentsToolTip(false, 2, 1)],
            worktrees: [T.worktreesToolTip(true, 15, 17179869184, 5, 48), T.worktreesToolTip(true, 0, 0, 2, 3),
                        T.worktreesToolTip(false, 1, 1, 1, 1)],
            limits: [T.limitsToolTip(true, 91, 86), T.limitsToolTip(true, null, 12.4), T.limitsToolTip(false, 1, 1)]
        }))
        Qt.quit()
    }
}
""")
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1",
                       XDG_CONFIG_DIRS="/etc/xdg")
            run = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=30)
        result = json.loads(re.search(r"RESULT (\{.*\})", run.stderr).group(1))
        self.assertEqual(result["agents"], [
            "Sesje agentów AI: 2 pracują, 1 czeka na ciebie",
            "Sesje agentów AI: 1 pracuje, 3 czekają na ciebie",
            "Sesje agentów AI: żadna nie pracuje ani nie czeka",
            "Sesje agentów AI: 2 czekają na ciebie",
            "Usługa tide-daemon nie działa"])
        self.assertEqual(result["worktrees"], [
            "Dodatkowe worktrees git: 15 do sprzątnięcia (16 GB), 5 w pracy, razem 48"
            "\nKliknij, aby otworzyć okno Worktrees",
            "Dodatkowe worktrees git: nic do sprzątnięcia, 2 w pracy, razem 3\nKliknij, aby otworzyć okno Worktrees",
            "Usługa tide-daemon nie działa"])
        self.assertEqual(result["limits"], ["Pozostały limit AI: GPT 91%, Claude 86%",
                                            "Pozostały limit AI: GPT —, Claude 12%",
                                            "Usługa Limity AI nie działa"])


class SourceTest(unittest.TestCase):
    def test_tiles_use_shared_tile_and_texts(self):
        for widget, name in (("agents", "CompactAgents"), ("worktrees", "CompactWorktrees"),
                             ("system", "CompactSystem"), ("limits", "CompactLimits")):
            with self.subTest(widget=widget):
                with open(os.path.join(WIDGETS_DIR, widget, "contents/ui", name + ".qml"), encoding="utf-8") as fh:
                    self.assertIn("Tide.DockTile", fh.read())
                with open(os.path.join(WIDGETS_DIR, widget, "contents/ui/main.qml"), encoding="utf-8") as fh:
                    main = fh.read()
                self.assertIn(name + " {", main)
                self.assertIn("toolTipSubText", main)


if __name__ == "__main__":
    unittest.main()
