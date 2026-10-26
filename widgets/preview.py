"""Podgląd kafelków widżetów w docku i paska bocznego, bez instalowania i bez Plasmy.

Użycie: /usr/bin/python3 widgets/preview.py [katalog-wyjściowy]   (domyślnie build/preview)

Składa pakiety (agenci, nowe zadanie, worktrees, system, Limity AI i nagłówek paska) do katalogu
tymczasowego, a qml6 offscreen rysuje ich kafelki (CompactAgents, CompactNewTask, CompactWorktrees,
CompactSystem, CompactLimits) obok siebie na tle docka (background.panel, 64 px) z przykładowymi
danymi. Każdy scenariusz daje osobny PNG: dock-tiles-<nazwa>.png. Do tego popup kafelka agentów
(SessionsView — zwarte wiersze, jak w pasku bocznym) z sesjami we wszystkich stanach:
agents-popup.png, i cały pasek boczny lewego monitora
(nagłówek, SidebarAgents, SidebarSystem, SidebarWorktrees, SidebarLimits) w 420×1040 px:
sidebar-<nazwa>.png. Ten sam kod sprawdza teksty i wymiary w testach
(widgets/tests/test_dock_tiles.py, widgets/tests/test_sidebar_tiles.py).
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
WIDGETS = ("agents", "newtask", "worktrees", "system", "limits")
DOCK_HEIGHT = 64
TILE_HEIGHT = 48        # tyle zostaje na kafelek w pływającym docku 64 px
TILE_GAP = 12       # DockTile.dockGap: każdy kafelek ma po 6 px marginesu z obu stron
MARGIN = 16

# Przykładowe dane: zwykły dzień i stany puste (nic nie działa, nic do sprzątnięcia, brak usług).
SCENARIOS = {
    "busy": {
        "agents": {"working": 2, "waiting": 1, "done": 2, "available": True},
        "worktrees": {"clean": 15, "bytes": 16 * 1024 ** 3, "active": 5, "available": True},
        "system": {"cpu": 34, "memory": 61, "disk": 88},
        "limits": {"gpt": 91, "claude": 86, "available": True},
    },
    "finished": {
        "agents": {"working": 0, "waiting": 0, "done": 3, "available": True},
        "worktrees": {"clean": 2, "bytes": 3 * 1024 ** 3, "active": 1, "available": True},
        "system": {"cpu": 12, "memory": 48, "disk": 71},
        "limits": {"gpt": 64, "claude": 40, "available": True},
    },
    "quiet": {
        "agents": {"working": 0, "waiting": 0, "done": 0, "available": True},
        "worktrees": {"clean": 0, "bytes": 0, "active": 0, "available": True},
        "system": {"cpu": 7, "memory": 100, "disk": None},
        "limits": {"gpt": None, "claude": 12, "available": True},
    },
    "offline": {
        "agents": {"working": 0, "waiting": 0, "done": 0, "available": False},
        "worktrees": {"clean": 0, "bytes": 0, "active": 0, "available": False},
        "system": {"cpu": None, "memory": None, "disk": None},
        "limits": {"gpt": None, "claude": None, "available": False},
    },
}

HARNESS = """import QtQuick
import QtQuick.Window
import "agents/contents/ui" as Agents
import "newtask/contents/ui" as NewTask
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
            doneCount: window.sample.agents.done || 0
            available: window.sample.agents.available
        }
        NewTask.CompactNewTask { id: newtask; height: %(tile)d; available: window.sample.agents.available }
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
            const tiles = [agents, newtask, worktrees, system, limits]
            const report = {
                agents: { label: agents.label, summary: agents.summary },
                newtask: { label: newtask.label, summary: newtask.summary },
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


# Karty sesji w popupie: każdy stan raz. Czas „teraz” jest zamrożony (NOW_TS), żeby PNG był powtarzalny.
NOW_TS = 1793016000  # 2026-10-26 12:00 UTC
# Popup kafelka agentów: 26 × 28 jednostek siatki Kirigami (main.qml) przy 18 px.
POPUP_WIDTH = 468
POPUP_HEIGHT = 504
SESSIONS = [
    {"id": "claude:a", "tool": "claude", "project": "plasma-tide", "branch": "feat/agents-done-state",
     "worktree": "", "title": "Stany sesji agentów", "state": "waiting", "reason": "approval",
     "sinceTs": NOW_TS - 120, "lastActivityTs": NOW_TS - 120, "lastAction": "Bash · make test",
     "canActivate": True, "terminal": "Konsole"},
    {"id": "claude:b", "tool": "claude", "project": "alpha", "branch": "feat/login", "worktree": "",
     "newTask": True, "title": "", "state": "working", "reason": "", "sinceTs": NOW_TS - 40, "lastActivityTs": NOW_TS - 5,
     "lastAction": "Edit · main.py", "canActivate": True, "terminal": "Konsole"},
    {"id": "codex:c", "tool": "codex", "project": "legal-agents", "branch": "main", "worktree": "",
     "title": "Kontynuacja", "state": "done", "reason": "scheduled", "sinceTs": NOW_TS - 300,
     "lastActivityTs": NOW_TS - 300, "lastAction": "shell · make test", "canActivate": True, "terminal": "Codex",
     "activation": "deeplink", "subagents": 2},
    {"id": "claude:d", "tool": "claude", "project": "beta", "branch": "fix/report", "worktree": "wt-report",
     "title": "Raport miesięczny", "state": "done", "reason": "", "sinceTs": NOW_TS - 720,
     "lastActivityTs": NOW_TS - 720, "lastAction": "Edit · report.py", "canActivate": True, "terminal": "Konsole"},
    {"id": "codex:e", "tool": "codex", "project": "gamma", "branch": "main", "worktree": "", "title": "",
     "state": "idle", "reason": "", "sinceTs": NOW_TS - 5400, "lastActivityTs": NOW_TS - 5400,
     "lastAction": "apply_patch · app.py", "canActivate": False, "terminal": ""},
    {"id": "codex:f", "tool": "codex", "project": "agent-team", "branch": "feat/reports", "worktree": "",
     "title": "", "state": "waiting", "reason": "question", "sinceTs": NOW_TS - 60, "lastActivityTs": NOW_TS - 60,
     "lastAction": "request_user_input", "canActivate": True, "terminal": "Codex", "activation": "deeplink",
     "subagents": 1},
    {"id": "claude:g", "tool": "claude", "project": "dev-dashboard", "branch": "main", "worktree": "", "title": "",
     "state": "ended", "reason": "", "sinceTs": NOW_TS - 7200, "lastActivityTs": NOW_TS - 7200,
     "lastAction": "", "canActivate": False, "terminal": ""},
]

POPUP_HARNESS = """import QtQuick
import QtQuick.Window
import "agents/contents/ui" as Agents
import "agents/contents/ui/tide" as Tide

Window {
    id: window
    readonly property var sessions: %(sessions)s
    width: %(width)d + %(margin)d * 2
    height: %(height)d + %(margin)d * 2
    visible: true
    color: Tide.Tokens.backgroundWindow

    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundWindow }

    Agents.SessionsView {
        id: view
        x: %(margin)d
        y: %(margin)d
        width: %(width)d
        height: %(height)d
        sessions: window.sessions
        showEnded: %(showEnded)s
        nowTs: %(now)d
    }

    Timer {
        interval: 400; running: true
        onTriggered: {
            const report = { rows: view.rows, ended: view.endedCount, working: view.workingCount,
                             waiting: view.waitingCount, done: view.doneCount }
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


GiB = 1024 ** 3

# Pasek boczny na lewym monitorze: 420 px szerokości (size.sidebar), ok. 1040 px wysokości
# (1080 px ekranu minus smukły pasek 30 px i odstępy pływającego panelu). Ramka panelu „west”
# ma margines treści 10 px (round(radius.dock × 0,35), theme/plasma/generate.py).
SIDEBAR_WIDTH = 420
SIDEBAR_HEIGHT = 1040
SIDEBAR_INSET = 10
# Odstęp, który panel Plasmy 6.8 sam stawia między apletami (zmierzony na zrzucie z zagnieżdżonej
# sesji, make sidebar-preview).
SIDEBAR_APPLET_SPACING = 4


def sidebar_session(sid, tool, project, state, ago, reason="", can=True, terminal="Konsole"):
    return {"id": sid, "tool": tool, "project": project, "branch": "main", "worktree": "", "title": "",
            "state": state, "reason": reason, "sinceTs": NOW_TS - ago, "lastActivityTs": NOW_TS - ago,
            "lastAction": "", "canActivate": can, "terminal": terminal}


# Przykładowe dane paska: zwykły dzień (5 żywych sesji i 1 zakończona), dużo sesji (lista
# z „+N więcej”, worktrees do sprzątnięcia na pełnej partycji) i usługi, które nie działają.
SIDEBAR_SCENARIOS = {
    "typical": {
        "sessions": [
            sidebar_session("claude:a", "claude", "plasma-tide", "working", 20),
            sidebar_session("claude:b", "claude", "legal-agents", "waiting", 180, "approval"),
            {**sidebar_session("codex:c", "codex", "app5-mobile", "working", 45, terminal="Codex"), "newTask": True},
            sidebar_session("claude:d", "claude", "limity-ai", "done", 720),
            sidebar_session("codex:e", "codex", "dev-dashboard", "idle", 5400, can=False, terminal=""),
            sidebar_session("claude:f", "claude", "stary-projekt", "ended", 20000),
        ],
        "agentsAvailable": True,
        "system": {"ready": True, "cpu": 34, "memory": 61, "memoryUsed": 9.8 * GiB, "memoryTotal": 16 * GiB,
                   "gpu": {"available": True, "usage": 31, "usedVram": 0.6 * GiB, "totalVram": 8 * GiB},
                   "disks": [{"target": "/", "usedPercent": 90.4, "free": 9.5 * GiB},
                             {"target": "/home", "usedPercent": 52.0, "free": 412 * GiB}],
                   "worktrees": [{"path": "/home/t/repo-wt", "isMain": False, "size": 2 * GiB, "removable": True}],
                   "targets": ["/", "/home"]},
        "worktrees": {"available": True, "active": 2, "clean": 0, "bytes": 0},
        "limits": {"available": True,
                   "chatgpt": {"session": None, "weekly": 91, "weeklyReset": "poniedziałek 20:00"},
                   "claude": {"session": 86, "weekly": 64, "sessionReset": "18:30", "weeklyReset": "czwartek 11:51"}},
    },
    "busy": {
        "sessions": [
            sidebar_session(f"s:{i}", "claude" if i % 2 else "codex", name, state, ago, reason)
            for i, (name, state, ago, reason) in enumerate([
                ("plasma-tide", "waiting", 60, "question"), ("legal-agents", "waiting", 300, "approval"),
                ("app5-mobile", "working", 10, ""), ("limity-ai", "working", 30, ""), ("dev-dashboard", "working", 90, ""),
                ("alpha", "done", 600, ""), ("beta", "done", 1500, ""), ("gamma", "idle", 7200, ""),
                ("delta", "idle", 9000, ""),
            ])
        ],
        "agentsAvailable": True,
        "system": {"ready": True, "cpu": 78, "memory": 83, "memoryUsed": 13.3 * GiB, "memoryTotal": 16 * GiB,
                   "gpu": {"available": True, "usage": 64, "usedVram": 5.2 * GiB, "totalVram": 8 * GiB},
                   "disks": [{"target": "/", "usedPercent": 88.0, "free": 11.4 * GiB}],
                   "worktrees": [{"path": "/home/t/repo-wt", "isMain": False, "size": 3.2 * GiB, "removable": True}],
                   "targets": ["/"]},
        "worktrees": {"available": True, "active": 5, "clean": 15, "bytes": 16 * GiB},
        "limits": {"available": True,
                   "chatgpt": {"session": 22, "weekly": 40, "sessionReset": "17:05"},
                   "claude": {"session": 9, "weekly": 30, "sessionReset": "16:10"}},
    },
    "offline": {
        "sessions": [],
        "agentsAvailable": False,
        "system": {"ready": False, "problem": "missing", "cpu": None, "memory": None,
                   "gpu": {"available": False},
                   "disks": [{"target": "/", "usedPercent": 71.0, "free": 28 * GiB}],
                   "worktrees": None, "targets": ["/"]},
        "worktrees": {"available": False, "active": 0, "clean": 0, "bytes": 0},
        "limits": {"available": False},
    },
}

SIDEBAR_HARNESS = """import QtQuick
import QtQuick.Window
import "agents/contents/ui" as Agents
import "worktrees/contents/ui" as Worktrees
import "system/contents/ui" as System
import "limits/contents/ui" as Limits
import "sidebar/contents/ui" as Sidebar
import "agents/contents/ui/tide" as Tide

Window {
    id: window
    readonly property var sample: %(data)s
    readonly property var sys: sample.system
    readonly property var lim: sample.limits
    width: %(width)d
    height: %(height)d
    visible: true
    color: Tide.Tokens.backgroundDesktop

    // Pulpit pod pływającym panelem i sam panel (tło panelu z kryciem z palety, promień docka).
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundDesktop }
    Rectangle {
        anchors.fill: parent
        radius: Tide.Tokens.radiusDock
        color: Qt.tint(Tide.Tokens.backgroundDesktop, Qt.rgba(Tide.Tokens.backgroundPanel.r,
            Tide.Tokens.backgroundPanel.g, Tide.Tokens.backgroundPanel.b, Tide.Tokens.opacityPanel))
        border.width: 1
        border.color: Qt.rgba(Tide.Tokens.textPrimary.r, Tide.Tokens.textPrimary.g, Tide.Tokens.textPrimary.b,
                              Tide.Tokens.opacityBorder)
    }

    Item {
        id: sysStub
        readonly property bool sensorsReady: window.sys.ready
        readonly property string problem: window.sys.problem || ""
        readonly property var cpuPercent: window.sys.cpu
        readonly property var memoryPercent: window.sys.memory
        readonly property var memoryUsed: window.sys.memoryUsed === undefined ? null : window.sys.memoryUsed
        readonly property var memoryTotal: window.sys.memoryTotal === undefined ? null : window.sys.memoryTotal
        readonly property var gpu: window.sys.gpu
        readonly property var disks: window.sys.disks
        readonly property var fullerDisk: disks.reduce((a, d) => a === null || d.usedPercent > a.usedPercent ? d : a, null)
        readonly property var worktrees: window.sys.worktrees
        readonly property var targets: window.sys.targets
        readonly property var info: ({ hostname: "tide" })
        readonly property var apps: ({ systemMonitor: "org.kde.plasma-systemmonitor", diskUsage: "org.kde.filelight" })
        readonly property QtObject history: QtObject {
            readonly property int capacity: 30
            readonly property var samples: window.sys.ready
                ? [12, 18, 15, 22, 30, 41, 38, 27, 25, 33, 52, 61, 47, 35, 29, 24, 31, 36, 44, 39,
                   28, 22, 26, 34, 48, 57, 43, 37, 31, window.sys.cpu] : []
        }
        function launch(app, url) {}
    }

    QtObject {
        id: limStub
        readonly property bool available: window.lim.available
        readonly property bool configured: true
        readonly property bool checked: true
        readonly property var snapshot: ({ paused: false })
        function service(key) { return { state: available ? "ok" : "offline" } }
        function remaining(key, metric) {
            if (!available) return null
            const value = window.lim[key][metric]
            return typeof value === "number" ? value : null
        }
        function panelValue(key) {
            const values = [remaining(key, "session"), remaining(key, "weekly")].filter(v => v !== null)
            return values.length ? Math.min.apply(null, values) : null
        }
        function reset(key, metric) { return available ? (window.lim[key][metric + "Reset"] || null) : null }
        function lastRead(key) { return available ? "16:41" : null }
        function open(key) {}
    }

    Column {
        id: column
        x: %(inset)d
        y: %(inset)d
        width: parent.width - %(inset)d * 2
        spacing: %(spacing)d

        Sidebar.Header { id: header; width: parent.width; shortcutText: "Meta+S" }
        Agents.SidebarAgents {
            id: agents
            width: parent.width
            sessions: window.sample.sessions
            available: window.sample.agentsAvailable
            nowTs: %(now)d
        }
        System.SidebarSystem { id: system; width: parent.width; source: sysStub }
        Worktrees.SidebarWorktrees {
            id: worktrees
            width: parent.width
            available: window.sample.worktrees.available
            activeCount: window.sample.worktrees.active
            cleanCount: window.sample.worktrees.clean
            cleanBytes: window.sample.worktrees.bytes
        }
        Limits.SidebarLimits { id: limits; width: parent.width; source: limStub }
    }

    Timer {
        interval: 500; running: true
        onTriggered: {
            const tiles = [header, agents, system, worktrees, limits]
            const report = {
                heights: tiles.map(t => Math.round(t.implicitHeight)),
                total: Math.round(column.implicitHeight + %(inset)d * 2),
                height: window.height,
                agents: { rows: agents.rows, hidden: agents.hiddenCount, ended: agents.endedCount,
                          summary: agents.summary },
                system: { notice: system.noticeText, gpu: system.showGpu ? system.gpuText : "",
                          disks: system.diskLines, note: system.diskNote },
                worktrees: { summary: worktrees.summary, problem: worktrees.showProblem ? worktrees.problemText : "" },
                limits: { rows: limits.rows.map(r => [r.label, r.valueText, r.note]), notice: limits.notice }
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
            for name in (*WIDGETS, "sidebar"):
                stage(os.path.join(WIDGETS_DIR, name), os.path.join(target, name), palette_path)
    finally:
        if previous is None:
            os.environ.pop("LIMITY_CONFIG", None)
        else:
            os.environ["LIMITY_CONFIG"] = previous
    return target


def _run(qml: str, palette_path: str, what: str):
    """Składa pakiety, uruchamia qml6 offscreen z podanym QML i zwraca JSON z linii RESULT."""
    with tempfile.TemporaryDirectory(prefix="tide-dock-tiles-") as tmp:
        stage_all(tmp, palette_path)
        harness = os.path.join(tmp, "Harness.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(qml)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1",
                   XDG_CONFIG_DIRS="/etc/xdg")  # bez ~/.config/kdedefaults z sesji Plasmy
        result = subprocess.run([QML_TOOL, harness], env=env, capture_output=True, text=True, timeout=60)
    problems = [line for line in result.stderr.splitlines()
                if re.search(r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member"
                             r"|Cannot assign|Required property|is not defined|Invalid property", line)]
    match = re.search(r"RESULT ([\[{].*[\]}])", result.stderr)
    if problems or not match:
        raise RuntimeError(f"qml6 nie narysował {what}:\n" + result.stderr)
    return json.loads(match.group(1))


def render(scenario: dict, png: str | None = None, palette_path: str = DEFAULT_PALETTE) -> dict:
    """Rysuje kafelki dla jednego scenariusza; zwraca ich teksty i wymiary (i zapisuje PNG)."""
    qml = HARNESS % {"data": json.dumps(scenario), "margin": MARGIN, "dock": DOCK_HEIGHT,
                     "tile": TILE_HEIGHT, "gap": TILE_GAP, "png": os.path.abspath(png) if png else ""}
    return _run(qml, palette_path, "kafelków")


def render_popup(sessions: list[dict] | None = None, png: str | None = None, show_ended: bool = False,
                 palette_path: str = DEFAULT_PALETTE) -> dict:
    """Rysuje popup kafelka agentów (SessionsView); zwraca wiersze i liczniki (i zapisuje PNG)."""
    qml = POPUP_HARNESS % {"sessions": json.dumps(SESSIONS if sessions is None else sessions, ensure_ascii=False),
                           "width": POPUP_WIDTH, "height": POPUP_HEIGHT, "margin": MARGIN, "now": NOW_TS,
                           "showEnded": "true" if show_ended else "false",
                           "png": os.path.abspath(png) if png else ""}
    return _run(qml, palette_path, "popupu agentów")


def render_sidebar(scenario: dict, png: str | None = None, palette_path: str = DEFAULT_PALETTE,
                   spacing: int = SIDEBAR_APPLET_SPACING) -> dict:
    """Rysuje cały pasek boczny (nagłówek i cztery kafelki) w 420×1040 px; zwraca teksty i wysokości.

    spacing to odstęp, który panel Plasmy dokłada między apletami (resztę przerwy mają kafelki).
    """
    qml = SIDEBAR_HARNESS % {"data": json.dumps(scenario, ensure_ascii=False), "width": SIDEBAR_WIDTH,
                             "height": SIDEBAR_HEIGHT, "inset": SIDEBAR_INSET, "spacing": spacing, "now": NOW_TS,
                             "png": os.path.abspath(png) if png else ""}
    return _run(qml, palette_path, "paska bocznego")


def main(argv: list[str]) -> int:
    out_dir = argv[0] if argv else os.path.join(ROOT, "build", "preview")
    os.makedirs(out_dir, exist_ok=True)
    for name, scenario in SCENARIOS.items():
        png = os.path.join(out_dir, f"dock-tiles-{name}.png")
        report = render(scenario, png)
        print(f"{os.path.abspath(png)}: kafelki {report['widths']} px, razem {report['total']} px")
    png = os.path.join(out_dir, "agents-popup.png")
    popup = render_popup(png=png)
    print(f"{os.path.abspath(png)}: popup agentów ({', '.join(r['state'] for r in popup['rows'])})")
    for name, scenario in SIDEBAR_SCENARIOS.items():
        png = os.path.join(out_dir, f"sidebar-{name}.png")
        report = render_sidebar(scenario, png)
        print(f"{os.path.abspath(png)}: pasek boczny {report['heights']} px, razem {report['total']}"
              f" z {report['height']} px")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
