"""Kafelek „AI” (org.tide.limits, PT-41): Limity AI i model lokalny w jednym kafelku.

Kropka modelu lokalnego w kafelku w docku (stany jak w kafelku Modele, ostrzeżenie tylko przy niedziałającym
serwerze, modelu częściowo na procesorze i braku modelu roli, nic bez usługi), wiersz w pasku bocznym, sekcja
„Modele lokalne” w popupie i „Otwórz Modele” przez okno Tide (atrapa org.tide.Window). Komponenty nie zależą
od PlasmoidItem, więc qml6 offscreen ładuje je z przykładowymi danymi (widgets/preview_ai.py,
widgets/preview_models.py). Bez Plasmy, tide-daemon i usługi Limity AI — pakiet bez prawdziwego tokenu.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from . import REPO, WIDGETS_DIR
from daemon.tests.fakebin import files_under
from widgets import preview_ai, preview_models

LIMITS_DIR = os.path.join(WIDGETS_DIR, "limits")
LIB_DIR = os.path.join(WIDGETS_DIR, "lib")


def palette_colors():
    from widgets.stage import DEFAULT_PALETTE, load_palette
    palette = load_palette(DEFAULT_PALETTE)
    semantic = {key: palette["pastel"][name] for key, name in palette["semantic"].items()}
    return palette, semantic


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def stage_limits(target):
    env = {k: v for k, v in os.environ.items() if k != "LIMITY_CONFIG"}
    env["LIMITY_CONFIG"] = os.path.join(target, "brak-konfiguracji.json")
    pkg = os.path.join(target, "pkg")
    subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"), LIMITS_DIR, pkg],
                   env=env, check=True, capture_output=True)
    return pkg


def run_qml(directory, name, source):
    harness = os.path.join(directory, name)
    with open(harness, "w", encoding="utf-8") as fh:
        fh.write(source)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", TZ=preview_models.TZ)
    log = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=30).stderr
    problems = [line for line in log.splitlines() if re.search(preview_models.PROBLEMS, line)]
    return log, problems


def tags(log):
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^.*?\b([A-Z][A-Z0-9_]+) (.*)$", log, re.M)}


class PackageTest(unittest.TestCase):
    def test_package_has_shared_models_code_from_the_library(self):
        with tempfile.TemporaryDirectory(prefix="tide-ai-stage-") as tmp:
            files = files_under(stage_limits(tmp))
        for name in ("contents/ui/main.qml", "contents/ui/CompactLimits.qml", "contents/ui/SidebarLimits.qml",
                     "contents/ui/LocalModelsCard.qml", "contents/ui/LocalDot.qml", "contents/ui/tide/models.js",
                     "contents/ui/tide/modelnames.js", "contents/ui/tide/ModelsState.qml",
                     "contents/ui/tide/TideWindow.qml"):
            self.assertIn(name, files)
        qmldir = read(LIB_DIR, "qmldir")
        self.assertIn("ModelsState 1.0 ModelsState.qml", qmldir)
        self.assertIn("TideWindow 1.0 TideWindow.qml", qmldir)
        # Jedna kopia logiki: kafelek Modele też bierze ją z biblioteki.
        self.assertFalse(os.path.exists(os.path.join(WIDGETS_DIR, "models/contents/code/models.js")))
        self.assertFalse(os.path.exists(os.path.join(WIDGETS_DIR, "models/contents/ui/ModelsState.qml")))
        self.assertFalse(os.path.exists(os.path.join(WIDGETS_DIR, "models/contents/ui/TideWindow.qml")))

    def test_main_reads_models_and_opens_the_models_view(self):
        main = read(LIMITS_DIR, "contents/ui/main.qml")
        self.assertIn('Plasmoid.title: "AI"', main)
        self.assertIn('toolTipMainText: "AI"', main)
        self.assertIn('path: "/Models"', main)
        self.assertIn('iface: "org.tide.Daemon.Models"', main)
        self.assertIn('method: "Status"', main)
        self.assertIn("Tide.ModelsState {", main)
        self.assertIn("Tide.TideWindow {", main)
        self.assertEqual(main.count("models: modelsState"), 2, "kafelek i pasek boczny dostają stan modeli")
        self.assertIn("LocalModelsCard {", main)
        self.assertEqual(main.count("tideWindow.open()"), 2, "„Otwórz Modele” w popupie i „Modele ↗” w pasku")
        self.assertIn("ModelsLogic.toolTipHead(modelsState.snapshot, modelsState.serviceUp, modelsState.nowMs)", main)
        # „Odśwież” w popupie: działa, gdy da się odświeżyć choć jedną usługę, i odświeża tylko działające.
        self.assertIn("enabled: limitsReady || modelsReady", main)
        self.assertIn("if (limitsReady) limitsSource.refresh()", main)
        self.assertIn("if (modelsReady) modelsState.refresh()", main)
        # Pasek boczny wisi cały dzień: rzadkie odpytywanie, zmiany przychodzą sygnałem Changed.
        self.assertIn("pollInterval: Plasmoid.configuration.sidebarMode ? 300000 : 60000", main)
        # Widżet tylko czyta stan modeli i prosi o odczyt — ustawienia są w oknie Tide.
        state = read(LIB_DIR, "ModelsState.qml")
        self.assertEqual(set(re.findall(r'source\.call\("(\w+)"', main + state)), {"Refresh"})

    def test_qml_has_no_hardcoded_colors_and_plain_texts(self):
        for name in ("CompactLimits.qml", "SidebarLimits.qml", "LocalModelsCard.qml", "LocalDot.qml", "main.qml"):
            source = read(LIMITS_DIR, "contents/ui", name)
            self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', source), [], name)
        for name in ("LocalModelsCard.qml",):
            source = read(LIMITS_DIR, "contents/ui", name)
            for _indent, body in re.findall(r"\n(\s*)Text \{\n(.*?)\n\1\}", "\n" + source, re.S):
                if "text:" in body:
                    self.assertIn("textFormat: Text.PlainText", body, f"{name}: {body[:80]}")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class TileTest(unittest.TestCase):
    """Kafelek w docku: „AI · Claude do jutra 14:30”, „GPT 100% Claude 30% ● lokalny”."""

    def test_title_keeps_the_runout_hint(self):
        result = preview_ai.render_tile("normal", "loaded")
        self.assertEqual((result["label"], result["detail"]), ("AI", "Claude do jutra 14:30"))
        self.assertEqual(result["lines"], ["GPT 100%", "Claude 30%"])
        self.assertEqual(result["accessible"], "AI: GPT 100%, Claude 30%, Claude wyczerpie się ok. jutro 14:30, "
                                               "model lokalny: qwen3:8b · na karcie 100%")

    def test_local_indicator_states(self):
        palette, color = palette_colors()
        calm = palette["text"]["secondary"]
        # scenariusz modeli: (słowo, ton, ostrzeżenie, kolor kropki)
        cases = {
            "loaded": ("lokalny", "ok", False, color["positive"]),
            "ready": ("lokalny", "ok", False, color["positive"]),
            "working": ("lokalny", "working", False, color["working"]),
            "pulling": ("lokalny", "working", False, color["working"]),
            "agent-missing": ("lokalny", "ok", False, color["positive"]),
            "mac": ("lokalny", "ok", False, color["positive"]),
            "partial": ("lokalny", "partial", True, color["neutral"]),
            "missing": ("lokalny", "warning", True, color["neutral"]),
            "offline": ("lokalny nie działa", "offline", True, color["negative"]),
        }
        widths = set()
        for scenario, (word, tone, warning, dot) in cases.items():
            with self.subTest(scenario):
                result = preview_ai.render_tile("calm", scenario)
                widths.add(result["width"])
                self.assertEqual((result["word"], result["local"]["tone"], result["local"]["warning"]),
                                 (word, tone, warning))
                self.assertEqual(result["dot"].lower(), dot.lower(), "kropka w kolorze stanu z palety")
                self.assertEqual(result["wordColor"].lower(), calm.lower(),
                                 "słowo zawsze kolorem podpisów; stan niesie kropka (28.10)")
                self.assertEqual(result["wordTop"], result["meterTop"], "słowo w linii procentów")
                self.assertTrue(result["fits"], "dwie linie w 48 px kafelka")
                self.assertLess(result["width"], 300, "kafelek nie spycha reszty docka")
                self.assertIn(", model lokalny: ", result["accessible"])
        self.assertLessEqual(len(widths), 2, f"szerokość zmienia tylko „lokalny nie działa”: {widths}")

    def test_nothing_local_without_service_server_or_first_read(self):
        bare = preview_ai.render_tile("calm", "none")
        self.assertFalse(bare["local"]["visible"])
        for scenario in ("down", "starting"):
            with self.subTest(scenario):
                result = preview_ai.render_tile("calm", scenario)
                self.assertFalse(result["local"]["visible"])
                self.assertEqual((result["word"], result["dot"]), ("", ""))
                self.assertEqual(result["width"], bare["width"], "bez kropki kafelek jak same limity")
                self.assertNotIn("lokaln", result["accessible"])

    def test_limits_service_down_keeps_the_local_dot(self):
        result = preview_ai.render_tile("offline", "loaded")
        self.assertEqual(result["lines"], ["GPT —", "Claude —"])
        self.assertEqual(result["word"], "lokalny")

    def test_indicator_logic_edge_cases(self):
        with tempfile.TemporaryDirectory(prefix="tide-ai-logic-") as tmp:
            shutil.copy(os.path.join(LIB_DIR, "models.js"), tmp)
            shutil.copy(os.path.join(LIB_DIR, "modelnames.js"), tmp)
            snap = json.dumps(preview_models.snapshot_variant("ready"), ensure_ascii=False)
            log, problems = run_qml(tmp, "Logic.qml", """import QtQuick
import "models.js" as Logic
Item {
    Component.onCompleted: {
        const snap = %s
        const cloud = JSON.parse(JSON.stringify(snap))
        cloud.endpoints.local.loaded = [{ name: "gpt-oss:120b-cloud", size: 384, gpuPercent: 0 }]
        const empty = { checked: true, default: "local", endpoints: {}, roles: {} }
        const show = i => [i.visible, i.word, i.tone, i.warning, i.text].join("/")
        console.warn("CLOUD " + show(Logic.indicator(cloud, true)))
        // Model chmurowy w pamięci i brak modelu roli raportu: ostrzeżenie o roli, jak bez modelu w pamięci.
        const cloudMissing = JSON.parse(JSON.stringify(cloud))
        cloudMissing.roles.summary = { endpoint: "local", model: "qwen3:14b", installed: false, available: false }
        const t = Logic.tile(cloudMissing, true)
        console.warn("CLOUDMISSING " + show(Logic.indicator(cloudMissing, true)) + " | " + t.value + "/" + t.tone)
        console.warn("ONCARD " + [Logic.onCard(cloud.endpoints.local.loaded[0]), Logic.partlyOnCpu(cloud.endpoints.local.loaded[0]),
                                  Logic.partlyOnCpu({ name: "gpt-oss:20b", gpuPercent: 55 }),
                                  Logic.partlyOnCpu({ name: "qwen3:8b", gpuPercent: 92 })].join(","))
        console.warn("EMPTY " + show(Logic.indicator(empty, true)))
        console.warn("NULL " + show(Logic.indicator(null, true)) + " | " + show(Logic.indicator(snap, false)))
        Qt.quit()
    }
}
""" % snap)
        self.assertEqual(problems, [], log)
        got = tags(log)
        self.assertEqual(got["CLOUD"], "true/lokalny/ok/false/gotowe",
                         "model chmurowy w pamięci nie jest stanem modelu lokalnego (Tide go nie używa)")
        self.assertEqual(got["CLOUDMISSING"], "true/lokalny/warning/true/brak modelu qwen3:14b | "
                                              "brak modelu qwen3:14b/warning",
                         "kafelek AI i kafelek Modele mówią to samo, co widok Modele (apps/models: slow tylko lokalny)")
        self.assertEqual(got["ONCARD"], "false,false,true,false", "92% na karcie to nie ostrzeżenie (28.10)")
        self.assertEqual(got["EMPTY"], "false//muted/false/", "bez serwera w ustawieniach — nic")
        self.assertEqual(got["NULL"], "false//muted/false/ | false//muted/false/")

    def test_runout_detail_uses_the_genitive(self):
        with tempfile.TemporaryDirectory(prefix="tide-ai-texts-") as tmp:
            pkg = stage_limits(tmp)
            log, problems = run_qml(os.path.join(pkg, "contents/ui"), "Texts.qml", """import QtQuick
import "tide" as Tide
Item {
    Component.onCompleted: {
        const f = when => ({ state: "runs-out", whenText: when })
        console.warn("UNTIL " + ["17:40", "jutro 14:30", "czwartek 11:00", "niedziela 09:05", "środa 8:00"]
            .map(w => Tide.Texts.limitsRunoutDetail("Claude", f(w))).join(" | "))
        Qt.quit()
    }
}
""")
        self.assertEqual(problems, [], log)
        self.assertEqual(tags(log)["UNTIL"], "Claude do 17:40 | Claude do jutra 14:30 | Claude do czwartku 11:00 | "
                                             "Claude do niedzieli 09:05 | Claude do środy 8:00")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class DockBudgetTest(unittest.TestCase):
    """Wszystkie kafelki docka (DOCK_LEFT i DOCK_RIGHT) w najszerszych wariantach mieszczą się na głównym
    monitorze obok aplikacji. Kafelek AI w najszerszej postaci: najdłuższy dopisek prognozy i najdłuższe
    słowo modelu lokalnego (miejsce na nie i tak jest zarezerwowane)."""

    SCREEN = 2560         # główny monitor (DP-2), dock na pełną szerokość
    APPS = 900            # miejsce na aplikacje pośrodku (ok. 18 ikon) i dwa odstępy
    PADDING = 2 * 16      # margines pływającego panelu

    def test_widest_tiles_fit_next_to_the_apps(self):
        from scripts import dock
        from widgets import preview, preview_board, preview_docker, preview_work
        busy = preview.render(preview.SCENARIOS["busy"])["widths"]   # agenci, worktrees, system, Limity AI
        ai = max(preview_ai.render_tile("widest", models)["width"] for models in ("missing", "offline"))
        widths = {
            "org.tide.system": busy[2],
            "org.tide.docker": max(preview_docker.render_tile(s)["width"] for s in preview_docker.TILE_SCENARIOS),
            "org.tide.worktrees": busy[1],
            "org.tide.board": max(preview_board.render_tile(v)["width"] for v in preview_board.TILE_SCENARIOS.values()),
            "org.tide.agents": busy[0],
            "org.tide.limits": ai,
            # Praca (PT-40) zamiast Tablicy i Agentów: najszerszy wariant — dwucyfrowe liczby w każdej części.
            "org.tide.work": max(preview_work.render_tile(v)["width"] for v in preview_work.TILE_SCENARIOS.values()),
        }
        tiles = dock.DOCK_LEFT + dock.DOCK_RIGHT
        unknown = [t for t in tiles if t not in widths]
        self.assertEqual(unknown, [], "nowy kafelek w docku — dopisz jego najszerszy wariant do tego testu")
        gap = 12                                                  # DockTile.dockGap
        total = sum(widths[t] + gap for t in tiles)
        self.assertLessEqual(total, self.SCREEN - self.APPS - self.PADDING,
                             f"kafelki docka zajmują {total} px: {widths}")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class SidebarTest(unittest.TestCase):
    def test_local_row_under_the_limits(self):
        result = preview_ai.render_sidebar("normal", "loaded")
        self.assertEqual(result["title"], "AI")
        self.assertEqual([r[0] for r in result["rows"]], ["GPT", "Claude"])
        self.assertEqual(result["localText"], "qwen3:8b · na karcie 100%")
        self.assertEqual(result["calls"], ["open"], "„Modele ↗” prosi o widok Modele")
        self.assertEqual(result["overflow"], [])
        self.assertTrue(result["accessible"].endswith(", model lokalny: qwen3:8b · na karcie 100%"))

    def test_warning_and_hidden_without_service(self):
        _, color = palette_colors()
        warning = preview_ai.render_sidebar("calm", "offline", note="Okno Tide się nie otworzyło.")
        self.assertEqual(warning["localText"], "serwer nie odpowiada")
        self.assertEqual(warning["localColor"].lower(), color["negative"].lower())
        self.assertEqual(warning["note"], "Okno Tide się nie otworzyło.")
        down = preview_ai.render_sidebar("calm", "down", note="x")
        self.assertFalse(down["local"]["visible"])
        self.assertEqual((down["localText"], down["note"], down["calls"]), ("", "", []))
        self.assertLess(down["height"], warning["height"], "bez usługi wiersza modelu nie ma")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class PopupTest(unittest.TestCase):
    def test_section_with_model_on_the_card(self):
        result = preview_ai.render_popup("normal", "loaded")
        self.assertTrue(result["visible"])
        self.assertFalse(result["highlighted"], "spokojna karta, gdy model działa")
        self.assertEqual(result["endpoint"], "Ten komputer")
        self.assertEqual((result["models"], result["gpu"]), (["qwen3:8b"], ["na karcie 100%"]))
        self.assertEqual(result["details"], ["5,6 GB · kontekst 8192 · zwolni pamięć za 4 min"])
        self.assertEqual((result["error"], result["queue"], result["pulls"], result["roleProblems"], result["config"]),
                         ("", "", [], [], ""))
        self.assertEqual(result["calls"], ["open"])
        self.assertEqual(result["overflow"], [])

    def test_section_with_queue_pulls_and_errors(self):
        result = preview_ai.render_popup("calm", "busy")
        self.assertEqual(result["queue"], "Pracuje: raport (gpt-oss:20b) od 3 min · 1 w kolejce")
        self.assertEqual(result["pulls"], ["Pobieram qwen3:14b · 42%", "Pobrano bge-m3",
                                           "Nie udało się pobrać llama3.3:70b: brak miejsca na dysku"])
        self.assertEqual(result["gpu"], ["na karcie 55%"])
        self.assertEqual(result["roleProblems"], ["Agent (qwen3-coder:30b): serwer nie odpowiada"])
        self.assertEqual(result["config"], "Ustawienia: rola „vision”: nieznany punkt „lm” — pominięta")
        self.assertEqual(result["overflow"], [])

    def test_section_when_the_server_is_down(self):
        result = preview_ai.render_popup("calm", "offline", note="Okno Tide nie ma jeszcze widoku")
        self.assertTrue(result["highlighted"])
        self.assertEqual(result["error"], "Model lokalny niedostępny: serwer nie odpowiada (127.0.0.1:11434)")
        self.assertEqual(result["roleProblems"], [], "„serwer nie odpowiada” przy rolach nie powtarza błędu serwera")
        self.assertEqual((result["models"], result["idle"]), ([], ""))
        self.assertEqual(result["note"], "Okno Tide nie ma jeszcze widoku")

    def test_no_section_without_service(self):
        result = preview_ai.render_popup("calm", "down")
        self.assertFalse(result["visible"])
        self.assertEqual(result["calls"], [])

    def test_screenshots(self):
        with tempfile.TemporaryDirectory(prefix="tide-ai-png-") as tmp:
            for name, render in (("tile", lambda p: preview_ai.render_tile("normal", "loaded", p)),
                                 ("sidebar", lambda p: preview_ai.render_sidebar("normal", "loaded", p)),
                                 ("popup", lambda p: preview_ai.render_popup("normal", "loaded", p))):
                png = os.path.join(tmp, name + ".png")
                render(png)
                with open(png, "rb") as fh:
                    self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n", name)


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class OpenModelsTest(unittest.TestCase):
    """„Otwórz Modele ↗” w popupie kafelka AI otwiera widok Modele przez TideWindow z biblioteki (atrapa okna)."""

    def test_open_button_shows_the_models_view(self):
        with tempfile.TemporaryDirectory(prefix="tide-ai-open-") as tmp:
            pkg = stage_limits(tmp)
            snap = json.dumps(preview_models.snapshot_variant("loaded"), ensure_ascii=False)
            log, problems = run_qml(os.path.join(pkg, "contents/ui"), "OpenSmoke.qml", """import QtQuick
import "tide" as Tide
Item {
    id: test
    width: 380; height: 400
    property var log: []
    property string note: ""
    Tide.TideWindow {
        id: win
        retryMs: 20
        waitMs: 300
        showCall: function (view, item, ok, error) { test.log.push("Show(" + view + "," + item + ")"); ok(test.answer) }
        launchCall: cmd => test.log.push("launch:" + cmd)
        onOpened: test.log.push("opened")
        onFailed: kind => test.note = kind
    }
    property var answer: true
    LocalModelsCard {
        id: card
        width: parent.width
        snapshot: %s
        serviceUp: true
        windowNote: test.note
        onOpenRequested: win.open()
    }
    function find(item, name) {
        if (item.objectName === name) return item
        for (const child of item.children) { const hit = find(child, name); if (hit) return hit }
        return null
    }
    Component.onCompleted: {
        find(card, "openModels").clicked()
        console.warn("OPENED " + test.log.join(";"))
        test.log = []; test.answer = false
        find(card, "openModels").clicked()
        console.warn("NOVIEW " + test.log.join(";") + "|" + test.note)
        Qt.quit()
    }
}
""" % snap)
        self.assertEqual(problems, [], log)
        got = tags(log)
        self.assertEqual(got["OPENED"], "Show(models,);opened")
        self.assertEqual(got["NOVIEW"], "Show(models,);Show(,)|noview", "okno bez widoku wychodzi na wierzch")


if __name__ == "__main__":
    unittest.main()
