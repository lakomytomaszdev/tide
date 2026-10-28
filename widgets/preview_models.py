"""Podgląd kafelka Modele (org.tide.models) bez instalowania i bez Plasmy: kafelek w docku zaraz za Limitami AI
i popup — qml6 offscreen na przykładowych danych w kształcie Status() tide-daemon (/Models,
docs/tide-daemon.md#modele-lokalne), także z opcjonalnymi polami report, queue i pulls.

Użycie: /usr/bin/python3 widgets/preview_models.py [katalog-wyjściowy]   (domyślnie build/preview)
Pliki: models-tile-<scenariusz>.png, models-popup-<scenariusz>.png. Ten sam kod sprawdza teksty i wymiary
w widgets/tests/test_widget_models.py. Wywołuje go też `make preview-widgets` (widgets/preview.py).
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
from widgets.stage import DEFAULT_PALETTE, stage  # noqa: E402

QML_TOOL = "qml6"
# 2026-10-27 17:00 czasu lokalnego (Europe/Warsaw, CET).
NOW_MS = 1_793_116_800_000
TZ = "Europe/Warsaw"
DOCK_HEIGHT = 64
TILE_HEIGHT = 48
TILE_GAP = 12
MARGIN = 16
POPUP_WIDTH = 400
PROBLEMS = (r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|Cannot assign"
            r"|Required property|is not defined|Invalid property|unavailable|Binding loop")

GB = 1_000_000_000
MODELS = [
    {"name": "bge-m3:latest", "size": 1157672605, "family": "bert", "parameterSize": "566.70M", "quantization": "F16"},
    {"name": "gpt-oss:20b", "size": 13793441566, "family": "gpt-oss", "parameterSize": "20.9B", "quantization": "MXFP4"},
    {"name": "hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M", "size": 6724052276, "family": "llama",
     "parameterSize": "11.2B", "quantization": "Q4_K_M"},
    {"name": "qwen3-coder:30b", "size": 18556700761, "family": "qwen3moe", "parameterSize": "30.5B", "quantization": "Q4_K_M"},
    {"name": "qwen3:8b", "size": 5225388164, "family": "qwen3", "parameterSize": "8.2B", "quantization": "Q4_K_M"},
]
QWEN_LOADED = {"name": "qwen3:8b", "size": 6 * GB, "sizeVram": 6 * GB, "gpuPercent": 100, "contextLength": 8192,
               "expiresAt": "2026-10-27T17:04:00+01:00"}
GPT_OSS_LOADED = {"name": "gpt-oss:20b", "size": 15 * GB, "sizeVram": 12_450_000_000, "gpuPercent": 83,
                  "contextLength": 8192, "expiresAt": "2026-10-27T17:25:00+01:00"}


def local(online=True, loaded=None, error=""):
    return {"name": "local", "url": "http://127.0.0.1:11434", "kind": "ollama", "auth": False, "online": online,
            "error": "" if online else (error or "Model lokalny niedostępny: serwer nie odpowiada (127.0.0.1:11434)"),
            "version": "0.40.11" if online else None, "models": MODELS if online else [],
            "loaded": list(loaded or []) if online else []}


def mac(online=True, loaded=None):
    return {"name": "mac", "url": "http://mac-studio:11434", "kind": "ollama", "auth": True, "online": online,
            "error": "" if online else "Model lokalny niedostępny: nie ma takiej nazwy (mac-studio)",
            "version": "0.41.2" if online else None,
            "models": [{"name": "qwen3-coder:30b"}, {"name": "qwen3:32b"}, {"name": "gpt-oss:120b"}] if online else [],
            "loaded": list(loaded or []) if online else []}


def role(model, endpoint="local", installed=True, online=True, ctx=8192, error=""):
    return {"endpoint": endpoint, "model": model, "ctx": ctx, "installed": installed if online else None,
            "available": bool(online and installed and not error), "error": error}


def pull(model, state="pulling", completed=0, total=0, error="", endpoint="local"):
    """Wpis Status().pulls z PT-25 (klucz "<punkt>/<model>"): running, success albo error."""
    return {"endpoint": endpoint, "model": model, "status": "pulling 3a4f9c…" if state == "pulling" else state,
            "completed": completed, "total": total, "running": state == "pulling", "error": error}


def status(endpoints, roles=None, default="local", **extra):
    online = {name: ep["online"] for name, ep in endpoints.items()}
    roles = roles if roles is not None else {
        "summary": role("qwen3:8b", online=online.get("local", False)),
        "describe": role("qwen3:8b", online=online.get("local", False)),
    }
    out = {"checked": True, "default": default,
           "config": {"path": "/home/user/.config/tide/models.json", "exists": False, "error": "", "warnings": []},
           "endpoints": endpoints, "roles": roles}
    out.update(extra)
    return out


def snapshot_variant(name: str) -> dict | None:
    """Status() dla scenariusza; None — usługa tide-daemon nie odpowiada."""
    if name == "loaded":
        return status({"local": local(loaded=[QWEN_LOADED])})
    if name == "ready":
        return status({"local": local()})
    if name == "partial":
        return status({"local": local(loaded=[GPT_OSS_LOADED])})
    if name == "working":
        return status({"local": local(loaded=[QWEN_LOADED])}, report={"summary": "local"},
                      queue={"local": {"running": {"role": "describe", "model": "qwen3:8b", "since": "2026-10-27T16:58:00+01:00"},
                                       "waiting": [{"role": "summary", "model": "qwen3:8b"}, {"role": "describe", "model": "qwen3:8b"}]}})
    if name == "pulling":
        return status({"local": local()}, pulls={"local/qwen3:14b": pull("qwen3:14b", completed=3_900_000_000,
                                                                          total=9_300_000_000)})
    if name == "pulled":
        return status({"local": local()}, pulls={"local/qwen3:14b": pull("qwen3:14b", "success")})
    if name == "missing":
        snap = status({"local": local()})
        snap["roles"]["summary"] = role("qwen3:14b", installed=False)
        return snap
    if name == "agent-missing":
        # Opcjonalne role (agent, wyszukiwanie) bez pobranego modelu nie ostrzegają na kafelku.
        snap = status({"local": local()})
        snap["roles"]["agent"] = role("qwen3-coder:32b", installed=False, ctx=None)
        snap["roles"]["embed"] = role("nomic-embed-text", installed=False, ctx=None)
        return snap
    if name == "offline":
        return status({"local": local(online=False)}, report={"summary": "local"})
    if name == "starting":
        snap = status({"local": local(online=False)})
        snap["checked"] = False
        snap["endpoints"]["local"]["error"] = ""
        return snap
    if name == "down":
        return None
    if name == "mac":
        return status({"local": local(), "mac": mac(loaded=[{**QWEN_LOADED, "name": "qwen3-coder:30b", "size": 19 * GB,
                                                             "sizeVram": 19 * GB, "contextLength": 16384,
                                                             "expiresAt": "2026-10-27T18:30:00+01:00"}])},
                      default="mac", roles={"summary": role("qwen3:8b", endpoint="mac", installed=False),
                                            "agent": role("qwen3-coder:30b", endpoint="mac", ctx=None)})
    if name == "moved":
        # Adres punktu zmieniony w trakcie raportu: na nowym serwerze opis kart, na starym jeszcze raport (PT-16).
        return status({"local": local(loaded=[QWEN_LOADED])}, report={"summary": "local"},
                      queue={"local": {"running": {"endpoint": "local", "role": "describe", "model": "qwen3:8b",
                                                   "since": "2026-10-27T16:59:00+01:00"},
                                       "waiting": [],
                                       "other": [{"lane": "http://10.0.0.5:11434", "waiting": [],
                                                  "running": {"endpoint": "local", "role": "summary", "model": "qwen3:8b",
                                                              "since": "2026-10-27T16:57:00+01:00"}}]}})
    if name == "busy":
        snap = status({"local": local(loaded=[GPT_OSS_LOADED]), "mac": mac(online=False)},
                      roles={"summary": role("qwen3:8b"), "describe": role("qwen3:8b"),
                             "agent": role("qwen3-coder:30b", endpoint="mac", online=False, ctx=None),
                             "embed": role("bge-m3", ctx=None),
                             "polish": role("hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M")},
                      report={"summary": "claude"},
                      queue={"local": {"running": {"role": "summary", "model": "gpt-oss:20b", "since": "2026-10-27T16:57:00+01:00"},
                                       "waiting": 1}},
                      pulls={"local/qwen3:14b": pull("qwen3:14b", completed=424, total=1000),
                             "local/bge-m3": pull("bge-m3", "success"),
                             "local/llama3.3:70b": pull("llama3.3:70b", "error", error="brak miejsca na dysku")})
        snap["config"]["exists"] = True
        snap["config"]["warnings"] = ["rola „vision”: nieznany punkt „lm” — pominięta"]
        return snap
    raise KeyError(name)


TILE_SCENARIOS = ("loaded", "ready", "partial", "working", "pulling", "pulled", "missing", "agent-missing", "offline", "starting", "down", "mac")
POPUP_SCENARIOS = ("loaded", "working", "offline", "busy", "mac", "moved", "down")

TILE_HARNESS = """import QtQuick
import QtQuick.Window
import "models/contents/ui" as Models
import "limits/contents/ui" as Limits
import "models/contents/ui/tide" as Tide

Window {
    id: window
    width: row.implicitWidth + %(margin)d * 2
    height: %(dock)d
    visible: true
    color: Tide.Tokens.backgroundPanel
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundPanel }
    QtObject {
        id: lim
        property bool available: true
        function panelValue(key) { return key === "chatgpt" ? 91 : 64 }
        function forecastFor(key, metric) { return null }
    }
    Row {
        id: row
        x: %(margin)d
        anchors.verticalCenter: parent.verticalCenter
        spacing: %(gap)d
        Limits.CompactLimits { height: %(tile)d; source: lim }
        Models.CompactModels { id: tile; height: %(tile)d; snapshot: %(snapshot)s; serviceUp: %(up)s }
    }
    function find(item, name) {
        if (item.objectName === name) return item
        for (const child of item.children) { const hit = find(child, name); if (hit) return hit }
        return null
    }
    Timer {
        interval: 400; running: true
        onTriggered: {
            const dot = find(tile, "tileDot")
            const report = { label: tile.label, summary: tile.summary, tone: tile.info.tone, pulse: tile.info.pulse,
                             dot: dot && dot.visible ? String(dot.color) : "", width: Math.round(tile.width),
                             fits: tile.implicitHeight <= %(tile)d }
            const png = "%(png)s"
            if (png === "") { console.warn("RESULT " + JSON.stringify(report)); Qt.quit(); return }
            window.contentItem.grabToImage(function (result) {
                result.saveToFile(png)
                console.warn("RESULT " + JSON.stringify(report))
                Qt.quit()
            })
        }
    }
}
"""

POPUP_HARNESS = """import QtQuick
import QtQuick.Window
import "models/contents/ui" as Models
import "models/contents/ui/tide" as Tide

Window {
    id: window
    width: %(width)d
    height: card.height + %(margin)d * 2
    visible: true
    color: Tide.Tokens.backgroundDesktop
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundDesktop }
    property var calls: []
    Rectangle {
        id: card
        x: %(margin)d
        y: %(margin)d
        width: parent.width - %(margin)d * 2
        height: popup.implicitHeight + 32
        radius: Tide.Tokens.radiusCard
        color: Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.97)
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
        Models.ModelsPopup {
            id: popup
            x: 16; y: 16
            width: parent.width - 32
            snapshot: %(snapshot)s
            serviceUp: %(up)s
            nowMs: %(now)d
            windowNote: %(note)s
            onRefreshRequested: window.calls.push("refresh")
            onOpenRequested: window.calls.push("open")
        }
    }
    function texts(item, out, name) {
        if (!item.visible) return out
        if (typeof item.text === "string" && item.text !== "" && (!name || item.objectName === name)) out.push(item.text)
        for (const child of item.children) texts(child, out, name)
        return out
    }
    function find(item, name, out) {
        if (item.objectName === name && item.visible) out.push(item)
        for (const child of item.children) find(child, name, out)
        return out
    }
    function overflow(item, right, out) {
        if (!item.visible) return out
        const p = item.mapToItem(popup, 0, 0)
        if (item.width > 0 && p.x + item.width > right + 0.5 && item.objectName !== "") out.push(item.objectName)
        for (const child of item.children) overflow(child, right, out)
        return out
    }
    Timer {
        interval: 400; running: true
        onTriggered: {
            for (const name of ["refreshButton", "openButton"]) {
                const buttons = find(popup, name, [])
                if (buttons.length) buttons[0].clicked()
            }
            const report = {
                titles: texts(popup, [], "endpointTitle"), states: texts(popup, [], "endpointState"),
                metas: texts(popup, [], "endpointMeta"), errors: texts(popup, [], "endpointError"),
                queue: texts(popup, [], "queueLine"), queueOther: texts(popup, [], "queueOtherLine"),
                pulls: texts(popup, [], "pullLine"),
                loaded: texts(popup, [], "loadedName"), gpu: texts(popup, [], "loadedGpu"),
                details: texts(popup, [], "loadedDetail"), idle: texts(popup, [], "idleLine"),
                installed: texts(popup, [], "installedLine"), roles: texts(popup, [], "roleName"),
                roleModels: texts(popup, [], "roleModel"), roleProblems: texts(popup, [], "roleProblem"),
                report: texts(popup, [], "report").join(""), config: texts(popup, [], "config").join(""),
                problem: texts(popup, [], "problem").join(""), command: texts(popup, [], "problemCommand").join(""), note: texts(popup, [], "windowNote").join(""),
                overflow: overflow(popup, popup.width, []), calls: window.calls, height: Math.round(card.height)
            }
            const png = "%(png)s"
            if (png === "") { console.warn("RESULT " + JSON.stringify(report)); Qt.quit(); return }
            window.contentItem.grabToImage(function (result) {
                result.saveToFile(png)
                console.warn("RESULT " + JSON.stringify(report))
                Qt.quit()
            })
        }
    }
}
"""


def _run(qml: str, what: str, palette_path: str = DEFAULT_PALETTE) -> dict:
    with tempfile.TemporaryDirectory(prefix="tide-models-tile-") as tmp:
        with contextlib.redirect_stderr(io.StringIO()):
            for name in ("models", "limits"):
                stage(os.path.join(WIDGETS_DIR, name), os.path.join(tmp, name), palette_path)
        harness = os.path.join(tmp, "Harness.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(qml)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", XDG_CONFIG_DIRS="/etc/xdg",
                   TZ=TZ)
        result = subprocess.run([QML_TOOL, harness], env=env, capture_output=True, text=True, timeout=60)
    problems = [line for line in result.stderr.splitlines() if re.search(PROBLEMS, line)]
    match = re.search(r"RESULT ([\[{].*[\]}])", result.stderr)
    if problems or not match:
        raise RuntimeError(f"qml6 nie narysował {what}:\n" + result.stderr)
    return json.loads(match.group(1))


def _qml_value(value) -> str:
    return "(" + json.dumps(value, ensure_ascii=False) + ")"


def render_tile(scenario: str, png: str | None = None) -> dict:
    snap = snapshot_variant(scenario)
    qml = TILE_HARNESS % {"margin": MARGIN, "dock": DOCK_HEIGHT, "gap": TILE_GAP, "tile": TILE_HEIGHT,
                          "snapshot": _qml_value(snap), "up": "false" if snap is None else "true",
                          "png": os.path.abspath(png) if png else ""}
    return _run(qml, "kafelka Modele")


def render_popup(scenario: str = "loaded", png: str | None = None, note: str = "") -> dict:
    snap = snapshot_variant(scenario)
    qml = POPUP_HARNESS % {"width": POPUP_WIDTH + MARGIN * 2, "margin": MARGIN, "snapshot": _qml_value(snap),
                           "up": "false" if snap is None else "true", "now": NOW_MS, "note": json.dumps(note),
                           "png": os.path.abspath(png) if png else ""}
    return _run(qml, "popupu Modele")


def main(argv: list[str]) -> int:
    out_dir = argv[0] if argv else os.path.join(ROOT, "build", "preview")
    os.makedirs(out_dir, exist_ok=True)
    for name in TILE_SCENARIOS:
        png = os.path.join(out_dir, f"models-tile-{name}.png")
        report = render_tile(name, png)
        print(f"{os.path.abspath(png)}: „{report['label']}” · „{report['summary']}”, {report['width']} px")
    for name in POPUP_SCENARIOS:
        png = os.path.join(out_dir, f"models-popup-{name}.png")
        report = render_popup(name, png)
        print(f"{os.path.abspath(png)}: popup Modele ({name}), {report['height']} px")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
