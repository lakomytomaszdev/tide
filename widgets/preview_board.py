"""Podgląd widżetu Tablica (org.tide.board) bez instalowania i bez Plasmy: kafelek w docku, popup
i kafelek paska bocznego na przykładowych migawkach tablicy (kształt /Board Snapshot()).

Użycie: /usr/bin/python3 widgets/preview_board.py [katalog-wyjściowy]   (domyślnie build/preview)
Pliki: board-tile-<scenariusz>.png (kafelek na tle docka, 64 px), board-popup.png (popup jak na mocku),
board-popup-empty.png, board-sidebar.png. `make preview-widgets` woła to po kafelkach innych widżetów
(widgets/preview.py). Ten sam kod sprawdza teksty w testach (widgets/tests/test_widget_board.py).
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
DOCK_HEIGHT = 64
TILE_HEIGHT = 48
MARGIN = 16
POPUP_WIDTH = 420   # mock: popup 420 px (Kirigami 23 jednostki siatki przy 18 px ≈ 414)
SIDEBAR_WIDTH = 420


def card(num, title, project, column, tags=(), derived=None, note=""):
    shown = (derived or {}).get("column") or column
    return {"id": f"K-{num}", "num": num, "title": title, "project": project.lower(), "projectName": project,
            "column": column, "boardColumn": shown, "resolvedTags": list(tags), "derived": derived, "note": note}


# Tablica z mocka (stan z koordynatora symulowany — PT-5).
MOCK_CARDS = [
    card(13, "Wartość w logu: warunki na kolumnach przebiegu", "LogViewer", "todo", ["Firma"],
         {"column": "waiting", "done": 5, "total": 9, "note": "TOM-146: warunek też na archiwalnych logach?",
          "session": "claude:k13-session"}),
    card(15, "Tablica pracy", "plasma-tide", "todo", ["własne"],
         {"column": "progress", "done": 3, "total": 10, "note": "PT-2 w trakcie"}),
    card(11, "Biblioteka kazusów", "agent-team", "todo", ["własne"],
         {"column": "progress", "done": 8, "total": 13, "note": "LA-036 w trakcie"}),
    card(12, "Hotfix wyszukiwania", "LogViewer", "todo", ["Firma"],
         {"column": "review", "done": 4, "total": 4, "note": "review OK"}),
    card(16, "Własne okno i kafelek Dockera", "plasma-tide", "todo", ["własne"]),
    card(21, "Tryb skupienia", "plasma-tide", "backlog", ["własne"]),
    card(19, "Historia diagnoz pojazdu z filtrem po VIN", "mobileapp", "backlog", ["Firma"]),
    card(18, "Eksport podsumowania przebiegu do PDF", "LogViewer", "backlog", ["Firma"]),
    card(22, "Miesięczne zestawienie godzin do faktury", "TimeTracker", "backlog", ["własne"]),
    card(17, "Import przepisu z linku", "CookApp", "backlog", ["własne"]),
    card(20, "Limit zapytań na klucz API", "webservice", "todo", ["Firma"]),
    card(10, "Unikalne filtry w wyszukiwaniu", "LogViewer", "done", ["Firma"]),
]
PROJECTS = [{"slug": p.lower(), "name": p} for p in
            ("agent-team", "LogViewer", "mobileapp", "webservice", "plasma-tide", "CookApp", "TimeTracker")]


def snapshot(cards, exists=True):
    return {"exists": exists, "cards": cards, "projects": PROJECTS if exists else [], "tags": []}


# Dziś (bez PT-5): karty tylko w kolumnach ręcznych.
TODAY_CARDS = [dict(c, boardColumn=c["column"], derived=None) for c in MOCK_CARDS]

TILE_SCENARIOS = {
    "mock": {"snapshot": snapshot(MOCK_CARDS), "available": True},
    "today": {"snapshot": snapshot(TODAY_CARDS), "available": True},
    "empty": {"snapshot": snapshot([], exists=False), "available": True},
    "offline": {"snapshot": None, "available": False},
}

TILE_HARNESS = """import QtQuick
import QtQuick.Window
import "board/contents/ui" as Board
import "board/contents/ui/tide" as Tide
import "board/contents/ui/boardtexts.js" as Texts

Window {
    id: window
    readonly property var sample: %(data)s
    width: tile.implicitWidth + %(margin)d * 2
    height: %(dock)d
    visible: true
    color: Tide.Tokens.backgroundPanel
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundPanel }
    Board.CompactBoard {
        id: tile
        x: %(margin)d
        anchors.verticalCenter: parent.verticalCenter
        height: %(tile)d
        counts: Texts.counts(window.sample.snapshot)
        available: window.sample.available
    }
    Timer {
        interval: 400; running: true
        onTriggered: {
            const n = Texts.counts(window.sample.snapshot)
            const report = { label: tile.label, detail: tile.detail, summary: tile.summary,
                             toolTip: Texts.toolTip(window.sample.available, !!window.sample.snapshot
                                                    && window.sample.snapshot.exists, n),
                             width: Math.round(tile.width), fits: tile.implicitHeight <= %(tile)d }
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
import "board/contents/ui" as Board
import "board/contents/ui/tide" as Tide

Window {
    id: window
    width: %(width)d + 32
    height: popup.implicitHeight + 32
    visible: true
    color: Tide.Tokens.backgroundPanel
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundPanel }
    property var requests: []
    Board.%(component)s {
        id: popup
        x: 16; y: 16
        width: %(width)d
        snapshot: %(snapshot)s
        available: %(available)s
        %(extra)s
        onOpenRequested: id => window.requests.push("open:" + id)
    }
    Timer {
        interval: 400; running: true
        onTriggered: {
            const report = %(report)s
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


def stage_board(target: str, palette_path: str = DEFAULT_PALETTE) -> str:
    with contextlib.redirect_stderr(io.StringIO()):
        stage(os.path.join(WIDGETS_DIR, "board"), os.path.join(target, "board"), palette_path)
    return target


def _run(qml: str, what: str, palette_path: str = DEFAULT_PALETTE) -> dict:
    with tempfile.TemporaryDirectory(prefix="tide-board-preview-") as tmp:
        stage_board(tmp, palette_path)
        harness = os.path.join(tmp, "Harness.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(qml)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", XDG_CONFIG_DIRS="/etc/xdg")
        result = subprocess.run([QML_TOOL, harness], env=env, capture_output=True, text=True, timeout=60)
    problems = [line for line in result.stderr.splitlines()
                if re.search(r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member"
                             r"|Cannot assign|Required property|is not defined|Invalid property|Binding loop", line)]
    match = re.search(r"RESULT ([\[{].*[\]}])", result.stderr)
    if problems or not match:
        raise RuntimeError(f"qml6 nie narysował {what}:\n" + result.stderr)
    return json.loads(match.group(1))


def render_tile(scenario: dict, png: str | None = None) -> dict:
    qml = TILE_HARNESS % {"data": json.dumps(scenario, ensure_ascii=False), "margin": MARGIN, "dock": DOCK_HEIGHT,
                          "tile": TILE_HEIGHT, "png": png or ""}
    return _run(qml, "kafelka tablicy")


def render_popup(snap: dict | None = None, png: str | None = None, available: bool = True,
                 quick_project: str = "plasma-tide", status: str = "", status_error: bool = False) -> dict:
    extra = (f"quickProject: {json.dumps(quick_project)}\n        status: {json.dumps(status, ensure_ascii=False)}\n"
             f"        statusError: {'true' if status_error else 'false'}")
    report = ("{ subtitle: popup.children[1].text, rows: popup.rows.map(r => [r.id, r.kind, r.word, r.progress]),"
              " more: popup.listing.more, project: popup.projectSlug, height: Math.round(popup.implicitHeight) }")
    qml = POPUP_HARNESS % {"width": POPUP_WIDTH, "component": "BoardPopup",
                           "snapshot": json.dumps(snapshot(MOCK_CARDS) if snap is None else snap, ensure_ascii=False),
                           "available": "true" if available else "false", "extra": extra, "report": report,
                           "png": png or ""}
    return _run(qml, "popupu tablicy")


def render_sidebar(snap: dict | None = None, png: str | None = None, available: bool = True) -> dict:
    report = "{ title: popup.title, summary: popup.summary, rows: popup.rows.map(r => r.id), height: Math.round(popup.implicitHeight) }"
    qml = POPUP_HARNESS % {"width": SIDEBAR_WIDTH - 32, "component": "SidebarBoard",
                           "snapshot": json.dumps(snapshot(MOCK_CARDS) if snap is None else snap, ensure_ascii=False),
                           "available": "true" if available else "false", "extra": "", "report": report,
                           "png": png or ""}
    return _run(qml, "kafelka tablicy w pasku bocznym")


def main(argv: list[str]) -> int:
    out_dir = argv[0] if argv else os.path.join(ROOT, "build", "preview")
    os.makedirs(out_dir, exist_ok=True)
    for name, scenario in TILE_SCENARIOS.items():
        png = os.path.join(out_dir, f"board-tile-{name}.png")
        report = render_tile(scenario, png)
        label = report["label"] + (" · " + report["detail"] if report["detail"] else "")
        print(f"{os.path.abspath(png)}: kafelek tablicy „{label}” — {report['summary']}")
    png = os.path.join(out_dir, "board-popup.png")
    popup = render_popup(png=png)
    print(f"{os.path.abspath(png)}: popup tablicy ({', '.join(r[1] for r in popup['rows'])})")
    png = os.path.join(out_dir, "board-popup-today.png")
    render_popup(snapshot(TODAY_CARDS), png=png, status="Dopisano K-23 do backlogu")
    print(f"{os.path.abspath(png)}: popup tablicy bez stanu z koordynatora")
    png = os.path.join(out_dir, "board-sidebar.png")
    render_sidebar(png=png)
    print(f"{os.path.abspath(png)}: tablica w pasku bocznym")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
