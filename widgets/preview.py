"""Podgląd kafelków widżetów w docku, bez instalowania i bez Plasmy.

Użycie: /usr/bin/python3 widgets/preview.py [katalog-wyjściowy]   (domyślnie build/preview)

Składa cztery pakiety (agenci, worktrees, system, Limity AI) do katalogu tymczasowego, a qml6
offscreen rysuje ich kafelki (CompactAgents, CompactWorktrees, CompactSystem, CompactLimits)
obok siebie na tle docka (background.panel, 64 px) z przykładowymi danymi. Każdy scenariusz
daje osobny PNG: dock-tiles-<nazwa>.png. Ten sam kod sprawdza teksty kafelków w testach
(widgets/tests/test_dock_tiles.py).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

WIDGETS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(WIDGETS_DIR)
sys.path.insert(0, ROOT)
from widgets.stage import DEFAULT_PALETTE, stage  # noqa: E402

QML_TOOL = shutil.which("qml6") or "/usr/lib/qt6/bin/qml"
WIDGETS = ("agents", "worktrees", "system", "limits")
DOCK_HEIGHT = 64
TILE_HEIGHT = 48        # tyle zostaje na kafelek w pływającym docku 64 px
TILE_GAP = 12       # DockTile.dockGap: każdy kafelek ma po 6 px marginesu z obu stron
MARGIN = 16

# Przykładowe dane: zwykły dzień i stany puste (nic nie działa, nic do sprzątnięcia, brak usług).
SCENARIOS = {
    "busy": {
        "agents": {"working": 2, "waiting": 1, "available": True},
        "worktrees": {"clean": 15, "bytes": 16 * 1024 ** 3, "active": 5, "available": True},
        "system": {"cpu": 34, "memory": 61, "disk": 88},
        "limits": {"gpt": 91, "claude": 86, "available": True},
    },
    "quiet": {
        "agents": {"working": 0, "waiting": 0, "available": True},
        "worktrees": {"clean": 0, "bytes": 0, "active": 0, "available": True},
        "system": {"cpu": 7, "memory": 100, "disk": None},
        "limits": {"gpt": None, "claude": 12, "available": True},
    },
    "offline": {
        "agents": {"working": 0, "waiting": 0, "available": False},
        "worktrees": {"clean": 0, "bytes": 0, "active": 0, "available": False},
        "system": {"cpu": None, "memory": None, "disk": None},
        "limits": {"gpt": None, "claude": None, "available": False},
    },
}

HARNESS = """import QtQuick
import QtQuick.Window
import "agents/contents/ui" as Agents
import "worktrees/contents/ui" as Worktrees
import "system/contents/ui" as System
import "limits/contents/ui" as Limits
import "agents/contents/ui/tide" as Tide

Window {
    id: window
    readonly property var sample: %(data)s
    width: row.implicitWidth + %(margin)d * 2
    height: %(dock)d
    visible: true
    color: Tide.Tokens.backgroundPanel

    // grabToImage nie łapie koloru okna, więc tło docka jest osobnym prostokątem.
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundPanel }

    QtObject {
        id: sys
        property var cpuPercent: window.sample.system.cpu
        property var memoryPercent: window.sample.system.memory
        property var fullerDisk: window.sample.system.disk === null ? null
            : ({ target: "/home", usedPercent: window.sample.system.disk })
    }
    QtObject {
        id: lim
        property bool available: window.sample.limits.available
        function panelValue(key) {
            return available ? window.sample.limits[key === "chatgpt" ? "gpt" : "claude"] : null
        }
    }

    Row {
        id: row
        x: %(margin)d
        anchors.verticalCenter: parent.verticalCenter
        spacing: %(gap)d

        Agents.CompactAgents {
            id: agents
            height: %(tile)d
            workingCount: window.sample.agents.working
            waitingCount: window.sample.agents.waiting
            available: window.sample.agents.available
        }
        Worktrees.CompactWorktrees {
            id: worktrees
            height: %(tile)d
            cleanCount: window.sample.worktrees.clean
            cleanBytes: window.sample.worktrees.bytes
            activeCount: window.sample.worktrees.active
            available: window.sample.worktrees.available
        }
        System.CompactSystem { id: system; height: %(tile)d; source: sys }
        Limits.CompactLimits { id: limits; height: %(tile)d; source: lim; opacity: lim.available ? 1 : 0.45 }
    }

    Timer {
        interval: 400; running: true
        onTriggered: {
            const tiles = [agents, worktrees, system, limits]
            const report = {
                agents: { label: agents.label, summary: agents.summary },
                worktrees: { label: worktrees.label, detail: worktrees.detail, summary: worktrees.summary },
                system: { lines: system.lines },
                limits: { lines: limits.lines },
                widths: tiles.map(t => Math.round(t.width)),
                fits: tiles.map(t => t.implicitHeight <= %(tile)d),
                total: Math.round(row.width)
            }
            const png = "%(png)s"
            if (png === "") {
                console.warn("RESULT " + JSON.stringify(report))
                Qt.quit()
                return
            }
            window.contentItem.grabToImage(function (result) {
                result.saveToFile(png)
                console.warn("RESULT " + JSON.stringify(report))
                Qt.quit()
            })
        }
    }
}
"""


def stage_all(target: str, palette_path: str = DEFAULT_PALETTE) -> str:
    """Pakiety do katalogu target. Limity AI bez prawdziwego tokenu: kafelek go nie potrzebuje."""
    previous = os.environ.get("LIMITY_CONFIG")
    os.environ["LIMITY_CONFIG"] = os.path.join(target, "brak-konfiguracji.json")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            for name in WIDGETS:
                stage(os.path.join(WIDGETS_DIR, name), os.path.join(target, name), palette_path)
    finally:
        if previous is None:
            os.environ.pop("LIMITY_CONFIG", None)
        else:
            os.environ["LIMITY_CONFIG"] = previous
    return target


def render(scenario: dict, png: str | None = None, palette_path: str = DEFAULT_PALETTE) -> dict:
    """Rysuje kafelki dla jednego scenariusza; zwraca ich teksty i wymiary (i zapisuje PNG)."""
    with tempfile.TemporaryDirectory(prefix="tide-dock-tiles-") as tmp:
        stage_all(tmp, palette_path)
        harness = os.path.join(tmp, "Harness.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(HARNESS % {"data": json.dumps(scenario), "margin": MARGIN, "dock": DOCK_HEIGHT,
                                "tile": TILE_HEIGHT, "gap": TILE_GAP,
                                "png": os.path.abspath(png) if png else ""})
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1",
                   XDG_CONFIG_DIRS="/etc/xdg")  # bez ~/.config/kdedefaults z sesji Plasmy
        result = subprocess.run([QML_TOOL, harness], env=env, capture_output=True, text=True, timeout=60)
    problems = [line for line in result.stderr.splitlines()
                if re.search(r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member"
                             r"|Cannot assign|Required property|is not defined|Invalid property", line)]
    match = re.search(r"RESULT (\{.*\})", result.stderr)
    if problems or not match:
        raise RuntimeError("qml6 nie narysował kafelków:\n" + result.stderr)
    return json.loads(match.group(1))


def main(argv: list[str]) -> int:
    out_dir = argv[0] if argv else os.path.join(ROOT, "build", "preview")
    os.makedirs(out_dir, exist_ok=True)
    for name, scenario in SCENARIOS.items():
        png = os.path.join(out_dir, f"dock-tiles-{name}.png")
        report = render(scenario, png)
        print(f"{os.path.abspath(png)}: kafelki {report['widths']} px, razem {report['total']} px")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
