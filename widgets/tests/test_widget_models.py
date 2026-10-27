"""Kafelek Modele (org.tide.models): pakiet, miejsce w docku, teksty kafelka we wszystkich stanach (także z
opcjonalnymi polami report, queue i pulls), logika słów i liczby mnogiej, popup (serwery, modele w pamięci,
kolejka, pobieranie, role, tryb raportu, brak usługi), „Otwórz w Tide” z atrapą okna, instalacja
i odinstalowanie z atrapą kpackagetool6.

Kafelek, popup i otwieranie okna nie zależą od PlasmoidItem, więc qml6 offscreen ładuje je z przykładowymi
danymi (widgets/preview_models.py). Bez Plasmy i bez tide-daemon.
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
from widgets import preview_models

WIDGET_ID = "org.tide.models"
WIDGET_DIR = os.path.join(WIDGETS_DIR, "models")


def run_qml(directory: str, name: str, source: str) -> str:
    harness = os.path.join(directory, name)
    with open(harness, "w", encoding="utf-8") as fh:
        fh.write(source)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", TZ=preview_models.TZ)
    return subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=30).stderr


def tags(log: str) -> dict:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^.*?\b([A-Z][A-Z0-9_]+) (.*)$", log, re.M)}


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-stage-models-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = os.path.join(self.tmp, "pkg")
        subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"), WIDGET_DIR, self.pkg], check=True)

    def read(self, name):
        with open(os.path.join(WIDGET_DIR, name), encoding="utf-8") as fh:
            return fh.read()

    def test_package_contains_widget_and_library(self):
        files = files_under(self.pkg)
        for name in ("metadata.json", "contents/ui/main.qml", "contents/ui/CompactModels.qml",
                     "contents/ui/ModelsPopup.qml", "contents/ui/TideWindow.qml", "contents/code/models.js",
                     "contents/ui/tide/Tokens.qml", "contents/ui/tide/DaemonSource.qml"):
            self.assertIn(name, files)
        with open(os.path.join(self.pkg, "metadata.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["KPlugin"]["Id"], WIDGET_ID)

    def test_tile_reads_status_and_only_refreshes(self):
        text = self.read("contents/ui/main.qml")
        self.assertIn('path: "/Models"', text)
        self.assertIn('iface: "org.tide.Daemon.Models"', text)
        self.assertIn('method: "Status"', text)
        self.assertIn('source.call("Refresh"', text)
        calls_made = set(re.findall(r'source\.call\("(\w+)"', text))
        self.assertEqual(calls_made, {"Refresh"}, "kafelek tylko czyta stan i prosi o odczyt — ustawienia są w oknie")

    def test_open_in_tide_uses_window_contract(self):
        text = self.read("contents/ui/TideWindow.qml")
        self.assertIn('property string view: "models"', text)
        self.assertIn('service: "org.tide.Window", path: "/Window", iface: "org.tide.Window"', text)
        self.assertIn('member: "Show", arguments: [view, ""]', text)
        self.assertIn('"kstart --application tide"', text)

    def test_qml_has_no_hardcoded_colors_and_plain_texts(self):
        for dirpath, _dirs, names in os.walk(WIDGET_DIR):
            for name in (n for n in names if n.endswith(".qml")):
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    source = fh.read()
                self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', source), [], f"kolor wpisany na sztywno w {name}")
                for _indent, body in re.findall(r"\n(\s*)Text \{\n(.*?)\n\1\}", "\n" + source, re.S):
                    if "text:" in body:
                        self.assertIn("textFormat: Text.PlainText", body, f"{name}: {body[:80]}")

    def test_dock_placement_right_after_limits(self):
        from scripts import dock, sidebar
        self.assertEqual(dock.DOCK_RIGHT[-2:], ["org.tide.limits", WIDGET_ID], "po prawej, zaraz za Limitami AI")
        self.assertNotIn(WIDGET_ID, dock.DOCK_LEFT)
        self.assertNotIn(WIDGET_ID, sidebar.WIDGETS, "tylko dock")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class TileTest(unittest.TestCase):
    def test_tile_texts_and_dots(self):
        # scenariusz: (etykieta, wartość, ton, pulsuje)
        cases = {
            "loaded": ("Modele", "qwen3:8b · na karcie 100%", "ok", False),
            "ready": ("Modele", "gotowe", "ok", False),
            "partial": ("Modele", "gpt-oss:20b · na karcie 83%", "partial", False),
            "working": ("Modele", "pracuje: opis kart · 2 w kolejce", "working", True),
            "pulling": ("Modele", "pobieram qwen3:14b 41%", "working", True),
            "missing": ("Modele", "brak modelu qwen3:14b", "warning", False),
            "offline": ("Modele", "serwer nie odpowiada", "offline", False),
            "starting": ("Modele", "łączę się…", "muted", False),
            "down": ("Modele", "usługa nie działa", "muted", False),
            "mac": ("Modele · mac", "qwen3-coder:30b · na karcie 100%", "ok", False),
        }
        from widgets.stage import DEFAULT_PALETTE, load_palette
        palette = load_palette(DEFAULT_PALETTE)
        color = {key: palette["pastel"][name] for key, name in palette["semantic"].items()}  # semantic → pastel
        dots = {"ok": color["positive"], "partial": color["neutral"], "warning": color["neutral"],
                "working": color["working"], "offline": color["negative"], "muted": ""}
        for name, (label, value, tone, pulse) in cases.items():
            with self.subTest(name):
                result = preview_models.render_tile(name)
                self.assertEqual((result["label"], result["summary"], result["tone"], result["pulse"]),
                                 (label, value, tone, pulse))
                self.assertEqual(result["dot"].lower(), dots[tone].lower(), "kropka w kolorze stanu z palety")
                self.assertTrue(result["fits"], "dwie linie w 48 px kafelka")
                self.assertLess(result["width"], 260, "kafelek nie spycha reszty docka")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class LogicTest(unittest.TestCase):
    """Słowa z models.js bez wyglądu: liczba mnoga, adresy, czasy, kolejka i pobieranie w obu kształtach,
    tryb raportu, powody niedostępności ról, podpowiedź."""

    def test_words(self):
        tmp = tempfile.mkdtemp(prefix="tide-models-js-")
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copy(os.path.join(WIDGET_DIR, "contents/code/models.js"), tmp)
        snap = json.dumps(preview_models.snapshot_variant("loaded"), ensure_ascii=False)
        log = run_qml(tmp, "Logic.qml", """import QtQuick
import "models.js" as Logic
Item {
    readonly property real now: %(now)d
    Component.onCompleted: {
        const snap = %(snap)s
        const ep = n => ({ online: true, models: new Array(n).fill({ name: "m" }), loaded: [] })
        console.warn("INSTALLED " + [0, 1, 2, 5, 12, 22].map(n => Logic.installedLine(ep(n))).join(" | "))
        console.warn("LOCAL " + ["http://127.0.0.1:11434", "http://localhost:11434/", "http://[::1]:11434",
                                 "http://mac-studio:11434", "http://127.0.0.1.evil:1"].map(Logic.isLocalUrl).join(","))
        console.warn("SHORT " + ["bge-m3:latest", "hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M", "qwen3:8b"]
                                .map(Logic.shortModel).join(" | "))
        console.warn("UNLOAD " + ["2026-10-27T17:00:20+01:00", "2026-10-27T17:04:00+01:00", "2026-10-27T18:30:00+01:00",
                                  "2026-10-27T16:59:00+01:00", "2318-01-01T00:00:00Z", "0001-01-01T00:00:00Z", "", null]
                                 .map(v => Logic.unloadText(v, now)).join(" | "))
        console.warn("QUEUE " + [
            Logic.queueShort(Logic.queueFor({ queue: { local: { running: null, waiting: 3 } } }, "local")),
            Logic.queueShort(Logic.queueFor({ queue: { local: { running: { role: "summary" }, waiting: [] } } }, "local")),
            Logic.queueLine(Logic.queueFor({ queue: { local: { running: { role: "describe", model: "qwen3:8b", since: (now - 120000) / 1000 },
                                                                 waiting: [{ role: "summary" }, { role: "summary" }] } } }, "local"), now),
            String(Logic.queueFor({ queue: { mac: { running: null, waiting: [] } } }, "local")),
            String(Logic.queueBusy(Logic.queueFor({ queue: { local: { running: null, waiting: [] } } }, "local")))
        ].join(" | "))
        const pullsList = { default: "local", endpoints: { local: {} }, pulls: [{ model: "a", percent: 10 },
            { endpoint: "mac", model: "b", completed: 1, total: 4 }, { endpoint: "local", model: "c", status: "success", done: true }] }
        const pullsMap = { pulls: { mac: { model: "b", completed: 1, total: 4 }, local: [{ model: "x:latest", error: "brak miejsca" }] } }
        console.warn("PULLS " + [Logic.pullsFor(pullsList, "local").map(Logic.pullLine).join("+"),
                                 Logic.pullsFor(pullsList, "mac").map(Logic.pullLine).join("+"),
                                 Logic.pullsFor(pullsMap, "mac").map(Logic.pullLine).join("+"),
                                 Logic.pullsFor(pullsMap, "local").map(Logic.pullLine).join("+"),
                                 Logic.pullsFor({}, "local").length].join(" | "))
        const withReport = mode => Object.assign({}, snap, { report: { summary: mode } })
        const down = Object.assign({}, snap, { report: { summary: "local" },
                                              roles: { summary: { endpoint: "local", model: "q", available: false } } })
        console.warn("REPORT " + [Logic.reportLine(withReport("claude")), Logic.reportLine(withReport("local")),
                                  Logic.reportLine(withReport("rules")), Logic.reportLine(down), Logic.reportLine(snap)].join(" | "))
        const eps = { local: { online: true, url: "http://127.0.0.1:11434" }, mac: { online: false, url: "http://mac:1" } }
        console.warn("ROLES " + Logic.roles({ endpoints: eps, roles: {
            embed: { endpoint: "local", model: "bge-m3", installed: true, available: true },
            agent: { endpoint: "mac", model: "qwen3-coder:30b", installed: null, available: false },
            summary: { endpoint: "local", model: "qwen3:14b", installed: false, available: false },
            describe: { endpoint: "gone", model: "qwen3:8b", installed: null, available: false, error: "punkt „gone” pominięty w ustawieniach" },
            zzz: { endpoint: "nowhere", model: "m", available: false } } })
            .map(r => r.key + ":" + r.name + ":" + r.where + ":" + (r.problem || "ok")).join(" | "))
        console.warn("TIP " + Logic.toolTip(snap, true, now).replace(/\\n/g, " // "))
        console.warn("TIP2 " + [Logic.toolTip(null, false, now), Logic.toolTip({ checked: false, endpoints: {} }, true, now)].join(" | "))
        console.warn("META " + [Logic.endpointMeta({ url: "http://127.0.0.1:11434", kind: "ollama", version: "0.40.11" }),
                                Logic.endpointMeta({ url: "https://mac:1234/", kind: "openai", version: null, auth: true })].join(" | "))
        console.warn("CONFIG " + [Logic.configLine({ config: { error: "to nie JSON", warnings: ["x"] } }),
                                  Logic.configLine({ config: { error: "", warnings: ["a", "b"] } }),
                                  Logic.configLine({ config: { error: "", warnings: [] } })].join(" | "))
        console.warn("TITLE " + [Logic.endpointTitle({ name: "local", url: "http://127.0.0.1:11434" }),
                                 Logic.endpointTitle({ name: "mac", url: "http://mac-studio:11434" })].join(" | "))
        Qt.quit()
    }
}
""" % {"now": preview_models.NOW_MS, "snap": snap})
        self.assertEqual([l for l in log.splitlines() if re.search(r"TypeError|ReferenceError|is not", l)], [], log)
        got = tags(log)
        self.assertEqual(got["INSTALLED"], "Brak pobranych modeli | Pobrane: 1 model | Pobrane: 2 modele | "
                         "Pobrane: 5 modeli | Pobrane: 12 modeli | Pobrane: 22 modele")
        self.assertEqual(got["LOCAL"], "true,true,true,false,false")
        self.assertEqual(got["SHORT"], "bge-m3 | Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M | qwen3:8b")
        self.assertEqual(got["UNLOAD"], "zwolni pamięć za chwilę | zwolni pamięć za 4 min | zwolni pamięć o 18:30 | "
                         "zwalnia pamięć | zostaje w pamięci | zostaje w pamięci |  |")
        self.assertEqual(got["QUEUE"], "w kolejce: 3 | pracuje: raport | Pracuje: opis kart (qwen3:8b) od 2 min · "
                         "w kolejce 2: raport | null | false")
        self.assertEqual(got["PULLS"], "Pobieram a · 10% | Pobieram b · 25% | Pobieram b · 25% | "
                         "Nie udało się pobrać x: brak miejsca | 0")
        self.assertEqual(got["REPORT"], "Raport: Claude | Raport: lokalnie | Raport: bez modelu | "
                         "Raport: lokalnie · model niedostępny, opis bez modelu |")
        self.assertEqual(got["ROLES"], "summary:Raport:Ten komputer:model nie jest pobrany na ten serwer | "
                         "describe:Opisy kart:gone:punkt „gone” pominięty w ustawieniach | "
                         "agent:Agent:mac:serwer nie odpowiada | embed:Wyszukiwanie:Ten komputer:ok | "
                         "zzz:zzz:nowhere:serwer „nowhere” nie jest ustawiony")
        self.assertEqual(got["TIP"], "Modele lokalne: Ten komputer odpowiada (Ollama 0.40.11), 5 modeli pobranych; "
                         "w pamięci qwen3:8b (na karcie 100%, zwolni pamięć za 4 min) // Kliknij, aby zobaczyć serwery i role")
        self.assertEqual(got["TIP2"], "Usługa tide-daemon nie działa | Tide sprawdza serwery modeli")
        self.assertEqual(got["META"], "127.0.0.1:11434 · Ollama 0.40.11 | mac:1234 · serwer zgodny z OpenAI · z tokenem")
        self.assertEqual(got["CONFIG"], "Ustawienia: to nie JSON | Ustawienia: a; b |")
        self.assertEqual(got["TITLE"], "Ten komputer | mac")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class PopupTest(unittest.TestCase):
    def test_popup_with_loaded_model(self):
        result = preview_models.render_popup("loaded")
        self.assertEqual((result["titles"], result["states"]), (["Ten komputer"], ["działa"]))
        self.assertEqual(result["metas"], ["127.0.0.1:11434 · Ollama 0.40.11"])
        self.assertEqual((result["loaded"], result["gpu"]), (["qwen3:8b"], ["na karcie 100%"]))
        self.assertEqual(result["details"], ["5,6 GB · kontekst 8192 · zwolni pamięć za 4 min"])
        self.assertEqual((result["idle"], result["installed"]), ([], ["Pobrane: 5 modeli"]))
        self.assertEqual((result["roles"], result["roleModels"], result["roleProblems"]),
                         (["Raport", "Opisy kart"], ["qwen3:8b", "qwen3:8b"], []))
        self.assertEqual((result["report"], result["queue"], result["pulls"], result["config"], result["problem"]),
                         ("", [], [], "", ""), "bez opcjonalnych pól nic z nich nie widać")
        self.assertEqual(result["calls"], ["refresh", "open"])
        self.assertEqual(result["overflow"], [], "nic nie wychodzi poza 400 px")

    def test_popup_with_queue_pulls_report_and_two_servers(self):
        result = preview_models.render_popup("busy")
        self.assertEqual(result["titles"], ["Ten komputer", "mac"])
        self.assertEqual(result["states"], ["działa", "nie odpowiada"])
        self.assertEqual(result["errors"], ["Model lokalny niedostępny: nie ma takiej nazwy (mac-studio)"])
        self.assertEqual(result["metas"][1], "mac-studio:11434 · Ollama · z tokenem")
        self.assertEqual(result["queue"], ["Pracuje: raport (gpt-oss:20b) od 3 min · w kolejce 1"])
        self.assertEqual(result["pulls"], ["Pobieram qwen3:14b · 42%", "Nie udało się pobrać llama3.3:70b: brak miejsca na dysku"])
        self.assertEqual(result["gpu"], ["na karcie 83%"])
        self.assertIn("reszta na procesorze — wolniej", result["details"][0])
        self.assertEqual(result["report"], "Raport: Claude")
        self.assertEqual(result["roles"], ["Raport", "Opisy kart", "Agent", "Wyszukiwanie", "Polszczyzna"])
        self.assertEqual(result["roleModels"][2], "qwen3-coder:30b · mac", "przy kilku serwerach — gdzie")
        self.assertEqual(result["roleProblems"], ["serwer nie odpowiada"])
        self.assertEqual(result["config"], "Ustawienia: rola „vision”: nieznany punkt „lm” — pominięta")
        self.assertEqual(result["overflow"], [])

    def test_popup_default_remote_first(self):
        result = preview_models.render_popup("mac")
        self.assertEqual(result["titles"], ["mac", "Ten komputer"], "domyślny serwer pierwszy")
        self.assertEqual(result["idle"], ["Żaden model nie jest teraz w pamięci"])
        self.assertEqual(result["roleProblems"], ["model nie jest pobrany na ten serwer"])

    def test_popup_offline_and_without_service(self):
        offline = preview_models.render_popup("offline")
        self.assertEqual(offline["states"], ["nie odpowiada"])
        self.assertEqual(offline["errors"], ["Model lokalny niedostępny: serwer nie odpowiada (127.0.0.1:11434)"])
        self.assertEqual(offline["report"], "Raport: lokalnie · model niedostępny, opis bez modelu")
        self.assertEqual(offline["roleProblems"], ["serwer nie odpowiada", "serwer nie odpowiada"])
        self.assertEqual((offline["installed"], offline["idle"]), ([], []))
        down = preview_models.render_popup("down", note="Okno Tide nie ma jeszcze widoku")
        self.assertIn("Usługa tide-daemon nie odpowiada", down["problem"])
        self.assertEqual((down["titles"], down["roles"]), ([], []))
        self.assertEqual(down["calls"], ["open"], "bez usługi nie ma czego odświeżać, okno można otworzyć")
        self.assertEqual(down["note"], "Okno Tide nie ma jeszcze widoku")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class TideWindowTest(unittest.TestCase):
    """„Otwórz w Tide” z atrapą okna: okno zna widok, okno bez widoku (przed PT-26), okna nie ma — uruchomienie
    i ponowienie Show, okno nie wstaje (limit czasu)."""

    def test_open_paths(self):
        tmp = tempfile.mkdtemp(prefix="tide-models-window-")
        self.addCleanup(shutil.rmtree, tmp)
        pkg = os.path.join(tmp, "pkg")
        subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"), WIDGET_DIR, pkg], check=True)
        log = run_qml(os.path.join(pkg, "contents/ui"), "WindowSmoke.qml", """import QtQuick
Item {
    id: test
    // Atrapa okna: answers — kolejne odpowiedzi Show (true, false albo "error" — nazwa niezajęta).
    property var answers: []
    property var log: []
    property var events: []
    function show(view, item, ok, error) {
        log.push("Show(" + view + "," + item + ")")
        const a = answers.length ? answers.shift() : "error"
        if (a === "error") error(); else ok(a)
    }
    TideWindow {
        id: win
        retryMs: 20
        waitMs: 300
        showCall: test.show
        launchCall: cmd => test.log.push("launch:" + cmd)
        onOpened: test.events.push("opened")
        onFailed: kind => test.events.push("failed:" + kind)
    }
    function report(tag) {
        console.warn(tag + " " + log.join(";") + "|" + events.join(";") + "|" + win.busy)
        log = []; events = []
    }
    Component.onCompleted: {
        answers = [true]; win.open(); report("RUNNING")
        answers = [false]; win.open(); report("NOVIEW")
        answers = ["error", "error", true]; win.open()
        later.start()
    }
    Timer {
        id: later
        interval: 250
        onTriggered: {
            test.report("LAUNCHED")
            test.answers = ["error", false]; win.open()
            last.start()
        }
    }
    Timer {
        id: last
        interval: 250
        onTriggered: {
            test.report("LAUNCHED_NOVIEW")
            test.answers = []; win.open(); win.open()
            end.start()
        }
    }
    Timer {
        id: end
        interval: 600
        onTriggered: {
            const shows = test.log.filter(l => l.startsWith("Show")).length
            test.log = test.log.filter(l => !l.startsWith("Show"))
            console.warn("TIMEOUT " + test.log.join(";") + "|" + test.events.join(";") + "|" + win.busy + "|" + (shows > 3))
            Qt.quit()
        }
    }
}
""")
        self.assertEqual([l for l in log.splitlines() if re.search(r"TypeError|ReferenceError|is not|unavailable", l)], [], log)
        got = tags(log)
        self.assertEqual(got["RUNNING"], "Show(models,)|opened|false")
        self.assertEqual(got["NOVIEW"], "Show(models,)|failed:noview|false")
        self.assertEqual(got["LAUNCHED"], "Show(models,);launch:kstart --application tide;Show(models,);Show(models,)|opened|false")
        self.assertEqual(got["LAUNCHED_NOVIEW"], "Show(models,);launch:kstart --application tide;Show(models,)|failed:noview|false")
        self.assertEqual(got["TIMEOUT"], "launch:kstart --application tide|failed:timeout|false|true",
                         "drugie kliknięcie w trakcie niczego nie uruchamia")


@unittest.skipUnless(shutil.which("make"), "brak make")
class WidgetInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-widget-models-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.installed = os.path.join(self.env["XDG_DATA_HOME"], "plasma/plasmoids", WIDGET_ID)

    def make(self, target):
        subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True, capture_output=True, text=True)

    def test_install_then_upgrade_then_uninstall(self):
        self.make("install-widget-models")
        staged = os.path.join(self.env["BUILD_DIR"], "widgets", WIDGET_ID)
        self.assertEqual(calls(self.env), [f"kpackagetool6 -t Plasma/Applet -i {staged}"])
        files = files_under(self.installed)
        self.assertIn("contents/ui/ModelsPopup.qml", files)
        self.assertIn("contents/ui/tide/Tokens.qml", files)
        self.make("install-widget-models")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -u {staged}")
        self.make("uninstall-widget-models")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -r {WIDGET_ID}")
        self.assertEqual(files_under(self.env["XDG_DATA_HOME"]), [])


if __name__ == "__main__":
    unittest.main()
