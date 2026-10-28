"""Podgląd kafelka „AI” (org.tide.limits, PT-41: Limity AI i model lokalny w jednym kafelku) bez instalowania
i bez Plasmy: kafelek w docku, kafelek w pasku bocznym i popup (karty usług i sekcja „Modele lokalne”) —
qml6 offscreen na przykładowych limitach i Status() tide-daemon (/Models) z widgets/preview_models.py.

Użycie: /usr/bin/python3 widgets/preview_ai.py [katalog-wyjściowy]   (domyślnie build/preview)
Pliki: ai-tile-<scenariusz>.png, ai-sidebar-<scenariusz>.png, ai-popup-<scenariusz>.png. Ten sam kod
sprawdza teksty i wymiary w widgets/tests/test_widget_ai.py. Wywołuje go też widgets/preview.py.
Scenariusz modeli to nazwa z preview_models.snapshot_variant ("loaded", "offline", "down"…), a "none" —
bez obiektu modeli (jak kafelek bez ModelsState).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile

WIDGETS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(WIDGETS_DIR)
sys.path.insert(0, ROOT)
from widgets import preview_models  # noqa: E402
from widgets.stage import DEFAULT_PALETTE, stage  # noqa: E402

QML_TOOL = "qml6"
DOCK_HEIGHT = 64
TILE_HEIGHT = 48
MARGIN = 16
SIDEBAR_WIDTH = 420 - 2 * 10      # pasek boczny minus margines treści panelu (widgets/preview.py)
POPUP_WIDTH = 380                 # ok. 21 gridUnit popupu w Plasmie minus marginesy (jak Limity AI)
PROBLEMS = preview_models.PROBLEMS

# Limity w kształcie odpowiedzi LimitsSource (atrapa niżej): zwykły dzień i prognoza końca sesji Claude.
LIMITS = {
    "normal": {"available": True,
               "chatgpt": {"state": "ready", "session": None, "weekly": 100, "weeklyReset": "poniedziałek 20:00"},
               "claude": {"state": "ready", "session": 30, "weekly": 64, "sessionReset": "jutro 14:30",
                          "weeklyReset": "czwartek 11:51"},
               "forecast": {"claude": {"session": {"state": "runs-out", "text": "wyczerpie się ok. jutro 14:30",
                                                   "short": "koniec ok. jutro 14:30", "whenText": "jutro 14:30",
                                                   "exhaustTs": 1793116800 + 3600}}}},
    "calm": {"available": True,
             "chatgpt": {"state": "ready", "session": None, "weekly": 91, "weeklyReset": "poniedziałek 20:00"},
             "claude": {"state": "ready", "session": 86, "weekly": 64, "sessionReset": "18:30",
                        "weeklyReset": "czwartek 11:51"},
             "forecast": None},
    "offline": {"available": False},
}

# Scenariusze podglądu: (limity, model lokalny).
TILE_SCENARIOS = {
    "normal": ("normal", "loaded"),
    "working": ("calm", "working"),
    "partial": ("calm", "partial"),
    "missing": ("calm", "missing"),
    "offline": ("calm", "offline"),
    "down": ("calm", "down"),
    "starting": ("calm", "starting"),
    "none": ("calm", "none"),
    "limits-offline": ("offline", "loaded"),
}
SIDEBAR_SCENARIOS = {"normal": ("normal", "loaded"), "warning": ("calm", "offline"), "down": ("calm", "down")}
POPUP_SCENARIOS = {"normal": ("normal", "loaded"), "busy": ("calm", "busy"), "offline": ("calm", "offline"),
                   "down": ("calm", "down")}

STUBS = """
    QtObject {
        id: lim
        readonly property var sample: %(limits)s
        readonly property bool available: sample.available
        readonly property bool configured: true
        readonly property bool checked: true
        readonly property var snapshot: ({ paused: false })
        function service(key) { return { state: available ? sample[key].state : "offline" } }
        function remaining(key, metric) {
            if (!available) return null
            const value = sample[key][metric]
            return typeof value === "number" ? value : null
        }
        function panelValue(key) {
            const values = [remaining(key, "session"), remaining(key, "weekly")].filter(v => v !== null)
            return values.length ? Math.min.apply(null, values) : null
        }
        function reset(key, metric) { return available ? (sample[key][metric + "Reset"] || null) : null }
        function lastRead(key) { return available ? "16:41" : null }
        function forecastFor(key, metric) {
            const all = sample.forecast || {}
            return remaining(key, metric) !== null && all[key] ? (all[key][metric] || null) : null
        }
        function open(key) {}
    }
    // Jak ModelsState z biblioteki: migawka Status() i czy usługa odpowiada.
    QtObject {
        id: modelsStub
        readonly property var snapshot: %(snapshot)s
        readonly property bool serviceUp: %(up)s
        readonly property real nowMs: %(now)d
    }
"""

COMMON = """
    function find(item, name, out) {
        if (item.objectName === name && item.visible) out.push(item)
        for (const child of item.children) find(child, name, out)
        return out
    }
    function texts(item, out, name) {
        if (!item.visible) return out
        if (typeof item.text === "string" && item.text !== "" && (!name || item.objectName === name)) out.push(item.text)
        for (const child of item.children) texts(child, out, name)
        return out
    }
    function overflow(item, root, right, out) {
        if (!item.visible) return out
        const p = item.mapToItem(root, 0, 0)
        if (item.width > 0 && p.x + item.width > right + 0.5 && item.objectName !== "") out.push(item.objectName)
        for (const child of item.children) overflow(child, root, right, out)
        return out
    }
    function finish(report) {
        const png = "%(png)s"
        if (png === "") { console.warn("RESULT " + JSON.stringify(report)); Qt.quit(); return }
        window.contentItem.grabToImage(function (result) {
            result.saveToFile(png)
            console.warn("RESULT " + JSON.stringify(report))
            Qt.quit()
        })
    }
"""

TILE_HARNESS = """import QtQuick
import QtQuick.Window
import "limits/contents/ui" as Limits
import "limits/contents/ui/tide" as Tide

Window {
    id: window
    width: tile.width + %(margin)d * 2
    height: %(dock)d
    visible: true
    color: Tide.Tokens.backgroundPanel
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundPanel }
%(stubs)s
    Limits.CompactLimits {
        id: tile
        x: %(margin)d
        anchors.verticalCenter: parent.verticalCenter
        height: %(tile)d
        source: lim
        models: %(models)s
        opacity: lim.available || tile.local.visible ? 1 : 0.45
    }
%(common)s
    Timer {
        interval: 400; running: true
        onTriggered: {
            const dot = find(tile, "localDot", [])[0]
            const word = find(tile, "localWord", [])[0]
            const meters = find(tile, "meter", [])
            const bar = meters.length ? meters[meters.length - 1] : null
            window.finish({
                label: tile.label, detail: tile.detail, lines: tile.lines, accessible: tile.Accessible.name,
                local: tile.local, dot: dot ? String(dot.color) : "", word: word ? word.text : "",
                wordColor: word ? String(word.color) : "",
                // Słowo w linii procentów: górna krawędź słowa jak u podpisu miernika („Claude”).
                wordTop: word ? Math.round(word.mapToItem(tile, 0, 0).y) : null,
                meterTop: bar ? Math.round(bar.mapToItem(tile, 0, 0).y) : null,
                width: Math.round(tile.width), height: Math.round(tile.implicitHeight),
                fits: tile.implicitHeight <= %(tile)d
            })
        }
    }
}
"""

SIDEBAR_HARNESS = """import QtQuick
import QtQuick.Window
import "limits/contents/ui" as Limits
import "limits/contents/ui/tide" as Tide

Window {
    id: window
    width: %(width)d + %(margin)d * 2
    height: side.implicitHeight + %(margin)d * 2
    visible: true
    color: Tide.Tokens.backgroundDesktop
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundDesktop }
    property var calls: []
%(stubs)s
    Limits.SidebarLimits {
        id: side
        x: %(margin)d
        y: %(margin)d
        width: %(width)d
        source: lim
        models: %(models)s
        windowNote: %(note)s
        onOpenModelsRequested: window.calls.push("open")
    }
%(common)s
    Timer {
        interval: 400; running: true
        onTriggered: {
            const open = find(side, "openModels", [])
            if (open.length) open[0].clicked()
            const text = find(side, "localText", [])[0]
            window.finish({
                title: side.title, rows: side.rows.map(r => [r.label, r.valueText, r.note]), local: side.local,
                localText: text ? text.text : "", localColor: text ? String(text.color) : "",
                note: texts(side, [], "windowNote").join(""), accessible: side.Accessible.name,
                calls: window.calls, height: Math.round(side.implicitHeight),
                overflow: overflow(side, side, side.width, [])
            })
        }
    }
}
"""

POPUP_HARNESS = """import QtQuick
import QtQuick.Window
import "limits/contents/ui" as Limits
import "limits/contents/ui/tide" as Tide

Window {
    id: window
    width: %(width)d + %(margin)d * 2
    height: column.implicitHeight + %(margin)d * 2
    visible: true
    color: Tide.Tokens.backgroundWindow
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundWindow }
    property var calls: []
%(stubs)s
    // Treść popupu jak w main.qml (bez nagłówka z kontrolkami Plasmy): karty usług i „Modele lokalne”.
    Column {
        id: column
        x: %(margin)d
        y: %(margin)d
        width: %(width)d
        spacing: 12
        Text {
            text: "AI"
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt + 3
            font.weight: Font.DemiBold
        }
        Limits.ServiceCard {
            width: parent.width; source: lim; serviceKey: "chatgpt"; title: "ChatGPT"; hideEmptySession: true
        }
        Limits.ServiceCard {
            width: parent.width; source: lim; serviceKey: "claude"; title: "Claude"; sessionLabel: "Sesja (5 h)"
        }
        Limits.LocalModelsCard {
            id: local
            width: parent.width
            snapshot: modelsStub.snapshot
            serviceUp: modelsStub.serviceUp
            nowMs: modelsStub.nowMs
            windowNote: %(note)s
            onOpenRequested: window.calls.push("open")
        }
    }
%(common)s
    Timer {
        interval: 400; running: true
        onTriggered: {
            const open = find(local, "openModels", [])
            if (open.length) open[0].clicked()
            window.finish({
                visible: local.visible, highlighted: local.highlighted,
                endpoint: texts(local, [], "localEndpoint").join(""), dot: String(local.info.visible ? find(local, "localDot", [])[0].color : ""),
                error: texts(local, [], "localError").join(""),
                models: texts(local, [], "localModel"), gpu: texts(local, [], "localGpu"),
                details: texts(local, [], "localDetail"), idle: texts(local, [], "localIdle").join(""),
                queue: texts(local, [], "localQueue").join(""), queueOther: texts(local, [], "localQueueOther"),
                pulls: texts(local, [], "localPull"), roleProblems: texts(local, [], "localRoleProblem"),
                config: texts(local, [], "localConfig").join(""), note: texts(local, [], "windowNote").join(""),
                calls: window.calls, overflow: overflow(local, local, local.width, []),
                height: Math.round(column.implicitHeight)
            })
        }
    }
}
"""


def _run(qml: str, what: str, palette_path: str = DEFAULT_PALETTE) -> dict:
    previous = os.environ.get("LIMITY_CONFIG")
    with tempfile.TemporaryDirectory(prefix="tide-ai-tile-") as tmp:
        # Kafelek nie potrzebuje tokenu usługi Limity AI — pakiet bez prawdziwej konfiguracji.
        os.environ["LIMITY_CONFIG"] = os.path.join(tmp, "brak-konfiguracji.json")
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                stage(os.path.join(WIDGETS_DIR, "limits"), os.path.join(tmp, "limits"), palette_path)
        finally:
            if previous is None:
                os.environ.pop("LIMITY_CONFIG", None)
            else:
                os.environ["LIMITY_CONFIG"] = previous
        harness = os.path.join(tmp, "Harness.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(qml)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", XDG_CONFIG_DIRS="/etc/xdg",
                   TZ=preview_models.TZ)
        result = subprocess.run([QML_TOOL, harness], env=env, capture_output=True, text=True, timeout=60)
    problems = [line for line in result.stderr.splitlines() if re.search(PROBLEMS, line)]
    match = re.search(r"RESULT ([\[{].*[\]}])", result.stderr)
    if problems or not match:
        raise RuntimeError(f"qml6 nie narysował {what}:\n" + result.stderr)
    return json.loads(match.group(1))


def _params(limits: str, models: str, png: str | None, note: str = "") -> dict:
    snap = None if models in ("down", "none") else preview_models.snapshot_variant(models)
    stubs = STUBS % {"limits": json.dumps(LIMITS[limits], ensure_ascii=False),
                     "snapshot": "(" + json.dumps(snap, ensure_ascii=False) + ")",
                     "up": "false" if snap is None else "true", "now": preview_models.NOW_MS}
    common = COMMON % {"png": os.path.abspath(png) if png else ""}
    return {"stubs": stubs, "common": common, "models": "null" if models == "none" else "modelsStub",
            "margin": MARGIN, "note": json.dumps(note, ensure_ascii=False)}


def render_tile(limits: str, models: str, png: str | None = None) -> dict:
    qml = TILE_HARNESS % {**_params(limits, models, png), "dock": DOCK_HEIGHT, "tile": TILE_HEIGHT}
    return _run(qml, "kafelka AI")


def render_sidebar(limits: str, models: str, png: str | None = None, note: str = "") -> dict:
    qml = SIDEBAR_HARNESS % {**_params(limits, models, png, note), "width": SIDEBAR_WIDTH}
    return _run(qml, "kafelka AI w pasku bocznym")


def render_popup(limits: str, models: str, png: str | None = None, note: str = "") -> dict:
    qml = POPUP_HARNESS % {**_params(limits, models, png, note), "width": POPUP_WIDTH}
    return _run(qml, "popupu kafelka AI")


def main(argv: list[str]) -> int:
    out_dir = argv[0] if argv else os.path.join(ROOT, "build", "preview")
    os.makedirs(out_dir, exist_ok=True)
    for name, (limits, models) in TILE_SCENARIOS.items():
        png = os.path.join(out_dir, f"ai-tile-{name}.png")
        report = render_tile(limits, models, png)
        print(f"{os.path.abspath(png)}: „{report['label']}{' · ' + report['detail'] if report['detail'] else ''}” "
              f"{' '.join(report['lines'])}{' ● ' + report['word'] if report['word'] else ''}, {report['width']} px")
    for name, (limits, models) in SIDEBAR_SCENARIOS.items():
        png = os.path.join(out_dir, f"ai-sidebar-{name}.png")
        report = render_sidebar(limits, models, png)
        print(f"{os.path.abspath(png)}: pasek boczny, {report['height']} px")
    for name, (limits, models) in POPUP_SCENARIOS.items():
        png = os.path.join(out_dir, f"ai-popup-{name}.png")
        report = render_popup(limits, models, png)
        print(f"{os.path.abspath(png)}: popup, {report['height']} px")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
