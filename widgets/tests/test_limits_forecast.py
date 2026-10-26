"""Prognoza Limitów AI w widżecie: kafelek w docku, popup (karty usług) i kafelek paska bocznego
na przykładowych prognozach z tide-daemon (widgets/preview.py), offscreen w qml6.

Stany: wyczerpie się przed resetem (brzoskwinia), starczy do resetu (wyciszone), brak prognozy
(za mało danych albo usługa tide-daemon nie działa) — wtedy nic nie dochodzi. Kafelek w docku
ma zostać zwarty: dopisek tylko przy wyczerpaniu przed resetem i najwyżej kilka pikseli szerzej.
"""
import copy
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import unittest

from widgets import preview
from widgets.tests import test_widget_limits as limits_tests  # moduł, żeby unittest nie dublował testów
from widgets.tests.test_dock_tiles import png_size

with open(os.path.join(preview.ROOT, "theme/palette/mist.json"), encoding="utf-8") as _fh:
    PALETTE = json.load(_fh)
PEACH = PALETTE["pastel"][PALETTE["semantic"]["runout"]]
MUTED = PALETTE["text"]["muted"]


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class DockTileForecastTest(unittest.TestCase):
    def test_runout_adds_short_peach_detail_and_stays_compact(self):
        busy = preview.render(preview.SCENARIOS["busy"])
        runout = preview.render(preview.SCENARIOS["runout"])
        self.assertEqual(busy["limits"]["detail"], "")
        # Najwcześniejsze wyczerpanie: sesja Claude (17:40), nie tydzień (czwartek).
        self.assertEqual(runout["limits"]["detail"], "Claude do 17:40")
        self.assertEqual(runout["limits"]["lines"], ["GPT 91%", "Claude 24%"])
        self.assertEqual(runout["fits"], [True] * 5)
        self.assertLess(runout["total"], 1000, runout["widths"])
        self.assertLessEqual(runout["widths"][-1] - busy["widths"][-1], 12, (busy["widths"], runout["widths"]))

    def test_lasting_forecast_adds_nothing_to_the_tile(self):
        scenario = copy.deepcopy(preview.SCENARIOS["runout"])
        scenario["limits"]["forecast"] = {"claude": {"session": preview.forecast_entry("lasts")}}
        result = preview.render(scenario)
        self.assertEqual(result["limits"]["detail"], "")

    def test_screenshot(self):
        with tempfile.TemporaryDirectory(prefix="tide-dock-runout-") as tmp:
            png = os.path.join(tmp, "tiles.png")
            result = preview.render(preview.SCENARIOS["runout"], png)
            self.assertEqual(png_size(png), (result["total"] + 2 * preview.MARGIN, preview.DOCK_HEIGHT))


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class PopupForecastTest(unittest.TestCase):
    def test_forecast_lines_under_each_limit(self):
        result = preview.render_limits_popup(preview.LIMITS_POPUP_SCENARIOS["forecast"])
        self.assertEqual(result["chatgpt"], [{"label": "Limit tygodniowy", "visible": True,
                                              "forecast": "starczy do resetu", "color": MUTED}])
        session, weekly = result["claude"]
        self.assertEqual((session["label"], session["forecast"], session["color"]),
                         ("Sesja (5 h)", "wyczerpie się ok. 17:40", PEACH))
        self.assertEqual((weekly["forecast"], weekly["color"]), ("starczy do resetu", MUTED))

    def test_without_daemon_there_is_no_forecast_and_no_extra_height(self):
        with_forecast = preview.render_limits_popup(preview.LIMITS_POPUP_SCENARIOS["forecast"])
        without = preview.render_limits_popup(preview.LIMITS_POPUP_SCENARIOS["nodata"])
        self.assertEqual([r["forecast"] for r in without["chatgpt"] + without["claude"]], ["", "", ""])
        # Prognoza siedzi w linii resetu — karta nie rośnie.
        self.assertEqual(with_forecast["height"], without["height"])

    def test_screenshot(self):
        with tempfile.TemporaryDirectory(prefix="tide-limits-popup-") as tmp:
            png = os.path.join(tmp, "popup.png")
            preview.render_limits_popup(preview.LIMITS_POPUP_SCENARIOS["forecast"], png)
            width, height = png_size(png)
        self.assertEqual(width, preview.LIMITS_POPUP_WIDTH + 2 * preview.MARGIN)
        self.assertGreater(height, 200)


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class SidebarForecastTest(unittest.TestCase):
    def test_rows_end_with_the_forecast(self):
        typical = preview.render_sidebar(preview.SIDEBAR_SCENARIOS["typical"])["limits"]
        self.assertEqual(typical["tails"], [[" · starczy do resetu", False], [" · starczy do resetu", False]])
        busy = preview.render_sidebar(preview.SIDEBAR_SCENARIOS["busy"])["limits"]
        # GPT: niższa jest sesja bez prognozy (za mało danych) — nic nie dochodzi; Claude kończy się przed resetem.
        self.assertEqual(busy["tails"], [["", False], [" · koniec ok. 15:40", True]])
        self.assertEqual(busy["rows"][1], ["Claude", "9%", "sesja · reset 16:10"])

    def test_forecast_does_not_make_the_sidebar_taller(self):
        data = copy.deepcopy(preview.SIDEBAR_SCENARIOS["typical"])
        plain = copy.deepcopy(data)
        plain["limits"].pop("forecast")
        self.assertEqual(preview.render_sidebar(data)["heights"], preview.render_sidebar(plain)["heights"])

    def test_service_down_has_no_forecast(self):
        result = preview.render_sidebar(preview.SIDEBAR_SCENARIOS["offline"])["limits"]
        self.assertEqual(result["tails"], [["", False], ["", False]])
        self.assertEqual(result["notice"], "Usługa Limity AI nie działa")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class SourceForecastTest(unittest.TestCase):
    """LimitsSource.forecastFor() i teksty z Tide.Texts na prawdziwym LimitsSource z atrapą usługi."""

    def test_forecast_for_needs_known_limit(self):
        token = "test-" + secrets.token_hex(8)
        server = limits_tests.FakeService(token)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        forecast = {"available": True, "error": "", "services": {
            "chatgpt": {"session": preview.forecast_entry("lasts"), "weekly": preview.forecast_entry("lasts")},
            "claude": {"session": preview.forecast_entry("runs-out", "17:40", 60)}}}
        with tempfile.TemporaryDirectory(prefix="tide-limits-source-") as tmp:
            pkg = preview.stage_all(tmp)
            harness = os.path.join(pkg, "limits/contents/ui/Harness.qml")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write("""import QtQuick
import "tide" as Tide
LimitsSource {
    id: src
    port: %d
    token: "%s"
    pollInterval: 0
    forecast: %s
    onUpdated: {
        const T = Tide.Texts
        console.warn("RESULT " + JSON.stringify({
            claudeSession: forecastFor("claude", "session").state,
            claudeWeekly: forecastFor("claude", "weekly"),
            gptSession: forecastFor("chatgpt", "session"),
            gptWeekly: forecastFor("chatgpt", "weekly").state,
            texts: [T.forecastText(forecastFor("claude", "session")), T.forecastShort(forecastFor("claude", "session")),
                    T.forecastText(null), T.forecastText({ state: "unknown", text: "x" }),
                    T.limitsRunoutDetail("Claude", forecastFor("claude", "session")),
                    T.limitsRunoutDetail("GPT", forecastFor("chatgpt", "weekly"))],
            color: String(Tide.Theme.forecastColor(forecastFor("claude", "session")))
        }))
        Qt.quit()
    }
    Timer { interval: 10000; running: true; onTriggered: Qt.quit() }
}
""" % (server.server_address[1], token, json.dumps(forecast)))
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1",
                       XDG_CONFIG_DIRS="/etc/xdg")
            run = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=30)
        result = json.loads(re.search(r"RESULT (\{.*\})", run.stderr).group(1))
        self.assertEqual(result["claudeSession"], "runs-out")
        self.assertIsNone(result["claudeWeekly"])   # usługa nie zna prognozy tygodnia
        self.assertIsNone(result["gptSession"])     # ChatGPT nie ma limitu sesji (remaining null)
        self.assertEqual(result["gptWeekly"], "lasts")
        self.assertEqual(result["texts"], ["wyczerpie się ok. 17:40", "koniec ok. 17:40", "", "",
                                           "Claude do 17:40", ""])
        self.assertEqual(result["color"], PEACH)


MAIN_CHECK = """import json, sys
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine
app = QGuiApplication(sys.argv[:1])
comp = QQmlComponent(QQmlEngine(), QUrl.fromLocalFile(sys.argv[1]))
print("ERRORS " + json.dumps([e.toString() for e in comp.errors()]))
"""


class MainCompilesTest(unittest.TestCase):
    """main.qml (PlasmoidItem, źródło prognozy z D-Bus) kompiluje się z modułami Plasmy z systemu."""

    def test_main_qml_compiles(self):
        with tempfile.TemporaryDirectory(prefix="tide-limits-main-") as tmp:
            pkg = preview.stage_all(tmp)
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_DIRS="/etc/xdg")
            run = subprocess.run(["/usr/bin/python3", "-c", MAIN_CHECK, os.path.join(pkg, "limits/contents/ui/main.qml")],
                                 env=env, capture_output=True, text=True, timeout=60)
        match = re.search(r"ERRORS (\[.*\])", run.stdout)
        self.assertIsNotNone(match, run.stderr)
        errors = json.loads(match.group(1))
        if any("is not installed" in e for e in errors):
            self.skipTest("brak modułów QML Plasmy: " + errors[0])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
