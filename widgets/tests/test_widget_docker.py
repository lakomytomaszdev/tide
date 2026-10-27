"""Kafelek Docker (org.tide.docker): pakiet, teksty kafelka i podpowiedzi z liczbą mnogą, popup (projekty,
porty, padnięte, akcje, stan akcji, brak Dockera), pasek boczny (i to, że cały pasek dalej mieści się
w 1040 px), instalacja i odinstalowanie z atrapą kpackagetool6.

Kafelek, popup i kafelek paska nie zależą od PlasmoidItem, więc qml6 offscreen ładuje je z przykładowymi
danymi (widgets/preview_docker.py, apps/docker/tests/fixture.py). Bez Plasmy i bez tide-daemon.
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
from widgets import preview, preview_docker

WIDGET_ID = "org.tide.docker"
WIDGET_DIR = os.path.join(WIDGETS_DIR, "docker")


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-stage-docker-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = os.path.join(self.tmp, "pkg")
        subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"), WIDGET_DIR, self.pkg], check=True)

    def test_package_contains_widget_and_library(self):
        files = files_under(self.pkg)
        for name in ("metadata.json", "contents/ui/main.qml", "contents/ui/CompactDocker.qml",
                     "contents/ui/DockerPopup.qml", "contents/ui/SidebarDocker.qml", "contents/code/docker.js",
                     "contents/config/main.xml", "contents/ui/tide/Tokens.qml", "contents/ui/tide/DaemonSource.qml"):
            self.assertIn(name, files)
        with open(os.path.join(self.pkg, "metadata.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["KPlugin"]["Id"], WIDGET_ID)

    def test_tile_talks_to_docker_object_and_never_removes(self):
        with open(os.path.join(WIDGET_DIR, "contents/ui/main.qml"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('path: "/Docker"', text)
        self.assertIn('source.call("ShowWindow"', text)
        self.assertIn('source.call("GroupAction"', text)
        self.assertIn("onActionFinished", text)
        # Statystyki kosztują Dockera: tylko przy otwartym popupie.
        self.assertRegex(text, r'running: root\.popupShown && root\.dockerUp\s+onTriggered: source\.call\("Stats"')
        for forbidden in ('"Remove"', '"Prune"', '"Stop"'):
            self.assertNotIn(forbidden, text, "kafelek niczego nie usuwa ani nie porządkuje — to robi okno")

    def test_qml_has_no_hardcoded_colors_and_plain_texts(self):
        for dirpath, _dirs, names in os.walk(WIDGET_DIR):
            for name in (n for n in names if n.endswith(".qml")):
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    source = fh.read()
                self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', source), [], f"kolor wpisany na sztywno w {name}")
                for _indent, body in re.findall(r"\n(\s*)Text \{\n(.*?)\n\1\}", "\n" + source, re.S):
                    if "text:" in body:
                        self.assertIn("textFormat: Text.PlainText", body, f"{name}: {body[:80]}")

    def test_dock_and_sidebar_placement(self):
        from scripts import dock, sidebar
        self.assertEqual(dock.DOCK_LEFT, ["org.tide.system", WIDGET_ID], "z lewej, zaraz po System")
        self.assertEqual(sidebar.WIDGETS.index(WIDGET_ID), sidebar.WIDGETS.index("org.tide.system") + 1)


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class TileTest(unittest.TestCase):
    def test_tile_texts(self):
        cases = {
            "busy": ("Docker · 6 projektów", "9 działa · 1 padł"),
            "healthy": ("Docker · 6 projektów", "9 działa"),
            "quiet": ("Docker", "nic nie działa"),
            "docker-down": ("Docker", "Docker nie działa"),
            "offline": ("Docker", "usługa nie działa"),
        }
        for name, (label, summary) in cases.items():
            with self.subTest(name):
                result = preview_docker.render_tile(name)
                self.assertEqual((result["label"], result["summary"]), (label, summary))
                self.assertTrue(result["fits"], "dwie linie w 48 px kafelka")
                self.assertLess(result["width"], 200, "kafelek z lewej nie spycha aplikacji")

    def test_plurals_and_tooltips(self):
        tmp = tempfile.mkdtemp(prefix="tide-docker-js-")
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copy(os.path.join(WIDGET_DIR, "contents/code/docker.js"), tmp)
        harness = os.path.join(tmp, "Plural.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write("""import QtQuick
import "docker.js" as Logic
Item {
    function snap(running, failed, projects) {
        const failedList = []
        for (let i = 0; i < failed; i++) failedList.push({ name: "bad-" + i, failed: true })
        return { available: true, state: "ok", summary: { running: running, failed: failed, projects: projects, stopped: failed },
                 groups: [{ kind: "compose", running: running, total: running, containers: failedList }] }
    }
    Component.onCompleted: {
        const out = []
        for (const [r, f, p] of [[1, 0, 1], [2, 0, 2], [4, 1, 5], [5, 2, 12], [22, 5, 22], [0, 1, 0]])
            out.push(Logic.tileLabel(snap(r, f, p), true) + " / " + Logic.tileValue(snap(r, f, p), true))
        console.warn("TILES " + out.join(" | "))
        console.warn("TIP " + Logic.toolTip(snap(9, 1, 6), true).replace("\\n", " // "))
        console.warn("TIP2 " + Logic.toolTip(snap(2, 2, 1), true).replace("\\n", " // "))
        console.warn("TIP3 " + [Logic.toolTip(null, false), Logic.toolTip({ available: false, state: "denied",
                                error: "brak uprawnień do /var/run/docker.sock" }, true)].join(" | "))
        console.warn("FAILED " + [Logic.failedLine({ exitCode: 137, finishedTs: 1000 - 21 * 3600 }, 1000),
                                  Logic.failedLine({ oomKilled: true, exitCode: 137, finishedTs: 1000 - 120 }, 1000),
                                  Logic.failedLine({ state: "created", reason: "nie wystartował: port is already allocated" }, 1000)].join(" | "))
        console.warn("NOTES " + [Logic.groupNote("stop", { ok: true }).text, Logic.groupNote("stop", { ok: false, failed: [{ name: "a", error: "b" }] }).text,
                                 Logic.startNote({ ok: true, note: "już działał" }).text, Logic.startNote(null).text].join(" | "))
        Qt.quit()
    }
}
""")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        log = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=30).stderr

        def tag(name):
            match = re.search(rf"{name} (.*)$", log, re.M)
            return match.group(1).strip() if match else log

        self.assertEqual(tag("TILES"), "Docker · 1 projekt / 1 działa | Docker · 2 projekty / 2 działają | "
                         "Docker · 5 projektów / 4 działają · 1 padł | Docker · 12 projektów / 5 działa · 2 padły | "
                         "Docker · 22 projekty / 22 działają · 5 padło | Docker / 1 padł")
        self.assertEqual(tag("TIP"), "Kontenery Dockera: 9 kontenerów działa w 6 projektach, bad-0 padł "
                         "// Kliknij, aby zobaczyć projekty")
        self.assertEqual(tag("TIP2"), "Kontenery Dockera: 2 kontenery działają w 1 projekcie, 2 padły: bad-0, bad-1 "
                         "// Kliknij, aby zobaczyć projekty")
        self.assertEqual(tag("TIP3"), "Usługa tide-daemon nie działa | brak uprawnień do /var/run/docker.sock")
        self.assertEqual(tag("FAILED"), "padł 21 h temu · kod 137 | brak pamięci (OOM) 2 min temu · kod 137 | "
                         "nie wystartował · port is already allocated")
        self.assertEqual(tag("NOTES"), "projekt zatrzymany | a: b | uruchomiony — już działał | brak odpowiedzi usługi")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class PopupTest(unittest.TestCase):
    def test_popup_rows_and_actions(self):
        result = preview_docker.render_popup("busy")
        self.assertEqual(result["projects"], ["bo-catalog", "cockpit", "agent-team", "app4-release-localhost",
                                              "cookapp", "youtrack", "legal-agents-library-work"])
        self.assertEqual(result["ports"], [":15075", ":8080 :8000", ":8111"])
        self.assertEqual(result["counts"], ["1/1", "1/1", "1/1", "1/1", "3/3", "1/1", "1/1"])
        self.assertEqual(result["failed"], ["padł 21 h temu · kod 137"])
        self.assertEqual(result["minis"], ["3%", "2,2 GB", "7,3 GB"])
        self.assertEqual(result["footer"], "Zatrzymane: 3 · testcontainers ukryte: 1")
        bot = next(c["id"] for g in preview_docker.snapshot_variant("busy")["groups"] for c in g["containers"]
                   if c["name"] == "translations-slack-bot")
        self.assertEqual(result["calls"], ["stop:bo-catalog", "start:" + bot, "open:"])

    def test_popup_shows_running_actions_and_results(self):
        snap = preview_docker.snapshot_variant("busy")
        bot = next(c["id"] for g in snap["groups"] for c in g["containers"] if c["name"] == "translations-slack-bot")
        result = preview_docker.render_popup("busy", busy={"compose:cookapp": "stop", bot: "start"})
        self.assertEqual(result["counts"][4], "…")
        self.assertEqual(result["failed"], ["uruchamiam…"])
        self.assertEqual(result["calls"][0], "stop:bo-catalog", "projekt w toku ma wyłączony przycisk, inne działają")
        self.assertNotIn("start:" + bot, result["calls"], "Uruchom wyłączony w trakcie")

    def test_popup_without_docker(self):
        down = preview_docker.render_popup("docker-down")
        self.assertEqual(down["problem"], "Docker nie działa — brak socketu /var/run/docker.sock")
        self.assertEqual((down["projects"], down["footer"], down["minis"]), ([], "", []))
        offline = preview_docker.render_popup("offline")
        self.assertIn("Usługa tide-daemon nie odpowiada", offline["problem"])


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class SidebarTest(unittest.TestCase):
    def test_sidebar_tile_and_whole_sidebar_still_fits(self):
        result = preview_docker.render_sidebar("busy")
        self.assertEqual(result["summary"], "9 działa · 1 padł · 6 projektów")
        sidebar = preview.render_sidebar(preview.SIDEBAR_SCENARIOS["typical"])
        total = sidebar["total"] + result["height"] + preview.SIDEBAR_APPLET_SPACING
        self.assertLessEqual(total, preview.SIDEBAR_HEIGHT, (sidebar["heights"], result["height"]))
        busy = preview.render_sidebar(preview.SIDEBAR_SCENARIOS["busy"])
        self.assertLessEqual(busy["total"] + result["height"] + preview.SIDEBAR_APPLET_SPACING, preview.SIDEBAR_HEIGHT)
        self.assertEqual(preview_docker.render_sidebar("offline")["summary"], "usługa nie działa")


@unittest.skipUnless(shutil.which("make"), "brak make")
class WidgetInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-widget-docker-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.installed = os.path.join(self.env["XDG_DATA_HOME"], "plasma/plasmoids", WIDGET_ID)

    def make(self, target):
        subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True, capture_output=True, text=True)

    def test_install_then_upgrade_then_uninstall(self):
        self.make("install-widget-docker")
        staged = os.path.join(self.env["BUILD_DIR"], "widgets", WIDGET_ID)
        self.assertEqual(calls(self.env), [f"kpackagetool6 -t Plasma/Applet -i {staged}"])
        files = files_under(self.installed)
        self.assertIn("contents/ui/DockerPopup.qml", files)
        self.assertIn("contents/ui/tide/Tokens.qml", files)
        self.make("install-widget-docker")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -u {staged}")
        self.make("uninstall-widget-docker")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -r {WIDGET_ID}")
        self.assertEqual(files_under(self.env["XDG_DATA_HOME"]), [])


if __name__ == "__main__":
    unittest.main()
