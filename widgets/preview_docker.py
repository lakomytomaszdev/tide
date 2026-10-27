"""Podgląd kafelka Docker (org.tide.docker) bez instalowania i bez Plasmy: kafelek w docku obok System
(jak na mocku „Kafelki Tablica i Docker”), popup i kafelek paska bocznego — qml6 offscreen na przykładowych
danych z apps/docker/tests/fixture.py (migawka, Stats(), Disk() w kształtach tide-daemon).

Użycie: /usr/bin/python3 widgets/preview_docker.py [katalog-wyjściowy]   (domyślnie build/preview)
Pliki: docker-tile-<scenariusz>.png, docker-popup.png, docker-sidebar.png. Ten sam kod sprawdza teksty
i wymiary w widgets/tests/test_widget_docker.py. Wywołuje go też `make preview-widgets` (widgets/preview.py).
"""
from __future__ import annotations

import contextlib
import copy
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
from apps.docker.tests.fixture import data as fixture  # noqa: E402
from widgets.stage import DEFAULT_PALETTE, stage  # noqa: E402

QML_TOOL = "qml6"
NOW = 1_793_090_000
DOCK_HEIGHT = 64
TILE_HEIGHT = 48
TILE_GAP = 12
MARGIN = 16
POPUP_WIDTH = 400
SIDEBAR_WIDTH = 420
SIDEBAR_INSET = 10
PROBLEMS = (r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|Cannot assign"
            r"|Required property|is not defined|Invalid property|unavailable|Binding loop")


def snapshot_variant(name: str) -> dict | None:
    """Migawka dla scenariusza kafelka; None — usługa tide-daemon nie odpowiada."""
    snap = fixture(NOW)["snapshot"]
    if name == "busy":
        return snap
    if name == "healthy":
        snap = copy.deepcopy(snap)
        snap["summary"]["failed"] = 0
        for g in snap["groups"]:
            for c in g["containers"]:
                c["failed"] = False
        return snap
    if name == "quiet":
        return {**snap, "summary": {**snap["summary"], "running": 0, "failed": 0, "projects": 0}, "groups": []}
    if name == "docker-down":
        return {"available": False, "state": "unavailable", "error": "Docker nie działa — brak socketu /var/run/docker.sock",
                "groups": [], "summary": {}}
    if name == "offline":
        return None
    raise KeyError(name)


TILE_SCENARIOS = ("busy", "healthy", "quiet", "docker-down", "offline")

TILE_HARNESS = """import QtQuick
import QtQuick.Window
import "docker/contents/ui" as Docker
import "system/contents/ui" as System
import "docker/contents/ui/tide" as Tide

Window {
    id: window
    width: row.implicitWidth + %(margin)d * 2
    height: %(dock)d
    visible: true
    color: Tide.Tokens.backgroundPanel
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundPanel }
    QtObject {
        id: sys
        property var cpuPercent: 14
        property var memoryPercent: 32
        property var fullerDisk: ({ target: "/", usedPercent: 62 })
    }
    Row {
        id: row
        x: %(margin)d
        anchors.verticalCenter: parent.verticalCenter
        spacing: %(gap)d
        System.CompactSystem { id: system; height: %(tile)d; source: sys }
        Docker.CompactDocker { id: tile; height: %(tile)d; snapshot: %(snapshot)s; serviceUp: %(up)s }
    }
    Timer {
        interval: 400; running: true
        onTriggered: {
            const report = { label: tile.label, summary: tile.summary, width: Math.round(tile.width),
                             fits: tile.implicitHeight <= %(tile)d, toolTip: "" }
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
import "docker/contents/ui" as Docker
import "docker/contents/ui/tide" as Tide

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
        Docker.DockerPopup {
            id: popup
            x: 16; y: 16
            width: parent.width - 32
            snapshot: %(snapshot)s
            serviceUp: %(up)s
            stats: %(stats)s
            disk: %(disk)s
            nowTs: %(now)d
            busy: %(busy)s
            notes: %(notes)s
            onOpenRequested: item => window.calls.push("open:" + item)
            onGroupActionRequested: (project, action) => window.calls.push(action + ":" + project)
            onStartRequested: id => window.calls.push("start:" + id)
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
    Timer {
        interval: 400; running: true
        onTriggered: {
            const stops = find(popup, "stopButton", [])
            if (stops.length) stops[0].clicked()
            const starts = find(popup, "startButton", [])
            if (starts.length) starts[0].clicked()
            const opens = find(popup, "openButton", [])
            if (opens.length) opens[0].clicked()
            const report = {
                projects: texts(popup, [], "projectName"), ports: texts(popup, [], "projectPorts"),
                counts: texts(popup, [], "projectCount"), failed: texts(popup, [], "failedLine"),
                minis: texts(popup, [], "miniValue"), footer: texts(popup, [], "footer").join(""),
                problem: texts(popup, [], "problem").join(""), calls: window.calls,
                height: Math.round(card.height)
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

SIDEBAR_HARNESS = """import QtQuick
import QtQuick.Window
import "docker/contents/ui" as Docker
import "docker/contents/ui/tide" as Tide

Window {
    id: window
    width: %(width)d
    height: column.implicitHeight + %(inset)d * 2
    visible: true
    color: Tide.Tokens.backgroundPanel
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundPanel }
    Column {
        id: column
        x: %(inset)d; y: %(inset)d
        width: parent.width - %(inset)d * 2
        Docker.SidebarDocker { id: tile; width: parent.width; snapshot: %(snapshot)s; serviceUp: %(up)s; nowTs: %(now)d }
    }
    Timer {
        interval: 400; running: true
        onTriggered: {
            const report = { summary: tile.summary, height: Math.round(tile.implicitHeight) }
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
    with tempfile.TemporaryDirectory(prefix="tide-docker-tile-") as tmp:
        with contextlib.redirect_stderr(io.StringIO()):
            for name in ("docker", "system"):
                stage(os.path.join(WIDGETS_DIR, name), os.path.join(tmp, name), palette_path)
        harness = os.path.join(tmp, "Harness.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(qml)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", XDG_CONFIG_DIRS="/etc/xdg",
                   TZ="Europe/Warsaw")
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
    return _run(qml, "kafelka Docker")


def render_popup(scenario: str = "busy", png: str | None = None, busy: dict | None = None,
                 notes: dict | None = None) -> dict:
    snap = snapshot_variant(scenario)
    data = fixture(NOW)
    qml = POPUP_HARNESS % {"width": POPUP_WIDTH + MARGIN * 2, "margin": MARGIN, "snapshot": _qml_value(snap),
                           "up": "false" if snap is None else "true", "stats": _qml_value(data["stats"]),
                           "disk": _qml_value(data["disk"]), "now": NOW, "busy": _qml_value(busy or {}),
                           "notes": _qml_value(notes or {}), "png": os.path.abspath(png) if png else ""}
    return _run(qml, "popupu Docker")


def render_sidebar(scenario: str = "busy", png: str | None = None) -> dict:
    snap = snapshot_variant(scenario)
    qml = SIDEBAR_HARNESS % {"width": SIDEBAR_WIDTH, "inset": SIDEBAR_INSET, "snapshot": _qml_value(snap),
                             "up": "false" if snap is None else "true", "now": NOW,
                             "png": os.path.abspath(png) if png else ""}
    return _run(qml, "kafelka Docker w pasku bocznym")


def main(argv: list[str]) -> int:
    out_dir = argv[0] if argv else os.path.join(ROOT, "build", "preview")
    os.makedirs(out_dir, exist_ok=True)
    for name in TILE_SCENARIOS:
        png = os.path.join(out_dir, f"docker-tile-{name}.png")
        report = render_tile(name, png)
        print(f"{os.path.abspath(png)}: „{report['label']}” · „{report['summary']}”, {report['width']} px")
    png = os.path.join(out_dir, "docker-popup.png")
    report = render_popup("busy", png)
    print(f"{os.path.abspath(png)}: popup Docker, {len(report['projects'])} projektów, {report['height']} px")
    png = os.path.join(out_dir, "docker-sidebar.png")
    report = render_sidebar("busy", png)
    print(f"{os.path.abspath(png)}: pasek boczny Docker, {report['height']} px")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
