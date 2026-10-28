"""Podgląd widżetu Praca (org.tide.work, PT-40) bez instalowania i bez Plasmy: kafelek w docku, popup
i kafelek paska bocznego na przykładowej liście (kształt /Board Work(), opis w docs/tide-daemon.md).

Użycie: /usr/bin/python3 widgets/preview_work.py [katalog-wyjściowy]   (domyślnie build/preview)
Pliki: work-tile-<scenariusz>.png (kafelek na tle docka, 64 px), work-popup.png (popup jak na mocku),
work-popup-finished.png (rozwinięte Skończone), work-popup-empty.png, work-sidebar.png i work-sidebar-whole.png
(cały pasek boczny z kafelkiem Praca zamiast Agentów i Tablicy). `make preview-widgets` woła to po innych
widżetach (widgets/preview.py). Ten sam kod sprawdza teksty w testach (widgets/tests/test_widget_work.py).
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
POPUP_WIDTH = 450   # Kirigami 25 jednostek siatki przy 18 px minus marginesy popupu
SIDEBAR_WIDTH = 420
NOW_TS = 1_793_192_400   # 28.10.2026, 14:00 czasu warszawskiego
PREVIEW_TZ = "Europe/Warsaw"


def card(num, title, project, group, note="", question="", state="", ago=0, session="", done=0, total=0):
    return {"kind": "card", "id": f"K-{num}", "card": f"K-{num}", "title": title, "project": project.lower(),
            "projectName": project, "group": group, "note": note, "question": question, "nextStep": "",
            "agentState": state, "session": session, "canActivate": bool(session),
            "sinceTs": NOW_TS - ago if ago else 0, "activityTs": NOW_TS - ago if ago else 0,
            "done": done, "total": total, "tool": "claude" if session else "", "branch": ""}


def loose(sid, project, group, state, title="", note="", question="", ago=60, repo=""):
    item = {"kind": "session", "id": sid, "card": "", "title": title or project, "project": "",
            "projectName": project, "group": group, "note": note, "question": question,
            "nextStep": "odpowiedz agentowi" if group == "waiting" else "", "agentState": state, "reason": "",
            "session": sid, "canActivate": True, "sinceTs": NOW_TS - ago, "activityTs": NOW_TS - ago,
            "done": 0, "total": 0, "tool": sid.split(":")[0], "branch": "", "subagents": 0}
    request = {"title": title or project, "column": "todo", "session": sid}
    if repo:
        request["repo"] = repo
    item["cardRequest"] = request
    return item


def work(items, exists=True):
    groups = ("waiting", "progress", "review", "todo", "finished")
    counts = {g: sum(1 for i in items if i["group"] == g) for g in groups}
    counts["total"] = sum(counts[g] for g in groups[:4])
    counts["sessions"] = sum(1 for i in items if i["kind"] == "session" and i["group"] != "finished")
    return {"exists": exists, "items": items, "counts": counts}


# Lista z mocka zatwierdzonego 28.10.2026 („Praca · 10”: 3 w toku, 2 czeka, 2 do sprawdzenia, 3 do zrobienia).
MOCK_ITEMS = [
    card(20, "Twitch: czat i alerty w jednym oknie", "twitch-overlay", "waiting",
         note="czeka na odpowiedź: „Czy taki podział na moduły czatu i alertów ma sens?”",
         question="Czy taki podział na moduły czatu i alertów ma sens?", state="waiting", ago=420,
         session="claude:k20"),
    loose("codex:pricing", "pricingapp", "waiting", "waiting", title="pricingapp · feat/rabaty",
          question="Mogę zrobić push gałęzi feat/rabaty na serwer testowy?", ago=900, repo="/w/pricingapp"),
    card(18, "Tablica i Docker", "plasma-tide", "progress", note="PT-37: kafelek Dockera w docku",
         state="working", ago=30, session="claude:k18", done=3, total=5),
    card(17, "Wartość w logu: warunki na kolumnach", "LogViewer", "progress",
         note="agent bezczynny od 27.10 13:10, w toku: TOM-146", state="idle", ago=90_000, session="claude:k17",
         done=5, total=9),
    loose("claude:kazusy", "agent-team", "progress", "working", title="Biblioteka kazusów: import z PDF",
          note="Edit · CaseImporter.cs", ago=20, repo="/w/agent-team"),
    card(26, "Raport poranny: poprawka nagłówka", "plasma-tide", "review", note="do scalenia: fix/report-header",
         state="ended", ago=3600, session="claude:k26", done=4, total=4),
    card(24, "Tryb skupienia", "plasma-tide", "review", note="do sprawdzenia ręcznie", state="done", ago=1800,
         session="codex:k24"),
    card(30, "Limit zapytań na klucz API", "webservice", "todo"),
    card(31, "Eksport podsumowania przebiegu do PDF", "LogViewer", "todo"),
    card(29, "Import przepisu z linku", "CookApp", "todo"),
    loose("codex:legal", "legal-agents", "finished", "done", title="legal-agents · main", note="shell · pytest -q",
          ago=300, repo="/w/legal-agents"),
    loose("claude:timetracker", "TimeTracker", "finished", "idle", title="Zestawienie godzin do faktury", ago=10_800,
          repo="/w/timetracker"),
]
MOCK = work(MOCK_ITEMS)
TODO_ONLY = work([i for i in MOCK_ITEMS if i["group"] == "todo"])
REPORT = {"path": "/tmp/raport/index.html", "generatedAt": NOW_TS - 7 * 3600, "unread": True, "waiting": 2}

TILE_SCENARIOS = {
    "mock": {"work": MOCK, "available": True},
    "todo": {"work": TODO_ONLY, "available": True},
    "empty": {"work": work([], exists=False), "available": True},
    "offline": {"work": None, "available": False},
}

TILE_HARNESS = """import QtQuick
import QtQuick.Window
import "work/contents/ui" as Work
import "work/contents/ui/tide" as Tide
import "work/contents/ui/worktexts.js" as Texts

Window {
    id: window
    readonly property var sample: %(data)s
    width: tile.implicitWidth + %(margin)d * 2
    height: %(dock)d
    visible: true
    color: Tide.Tokens.backgroundPanel
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundPanel }
    Work.CompactWork {
        id: tile
        x: %(margin)d
        anchors.verticalCenter: parent.verticalCenter
        height: %(tile)d
        counts: Texts.counts(window.sample.work)
        available: window.sample.available
    }
    Timer {
        interval: 400; running: true
        onTriggered: {
            const n = Texts.counts(window.sample.work)
            const report = { label: tile.label, detail: tile.detail, summary: tile.summary,
                             toolTip: Texts.toolTip(window.sample.available, n),
                             tint: String(tile.tint), waitingTint: String(Tide.Tokens.waiting),
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
import "work/contents/ui" as Work
import "work/contents/ui/tide" as Tide

Window {
    id: window
    width: %(width)d + 32
    height: popup.implicitHeight + 32
    visible: true
    color: Tide.Tokens.backgroundPanel
    Rectangle { anchors.fill: parent; color: Tide.Tokens.backgroundPanel }
    Work.%(component)s {
        id: popup
        x: 16; y: 16
        width: %(width)d
        work: %(work)s
        available: %(available)s
        nowTs: %(now)d
        %(extra)s
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


def stage_work(target: str, palette_path: str = DEFAULT_PALETTE) -> str:
    with contextlib.redirect_stderr(io.StringIO()):
        stage(os.path.join(WIDGETS_DIR, "work"), os.path.join(target, "work"), palette_path)
    return target


def run_qml(qml: str, what: str, palette_path: str = DEFAULT_PALETTE, name: str = "Harness.qml") -> str:
    """Składa pakiet, uruchamia qml6 offscreen z podanym QML i zwraca stderr (z liniami console.warn)."""
    with tempfile.TemporaryDirectory(prefix="tide-work-preview-") as tmp:
        stage_work(tmp, palette_path)
        harness = os.path.join(tmp, name)
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(qml)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", XDG_CONFIG_DIRS="/etc/xdg",
                   TZ=PREVIEW_TZ)
        result = subprocess.run([QML_TOOL, harness], env=env, capture_output=True, text=True, timeout=60)
    return result.stderr


def _run(qml: str, what: str, palette_path: str = DEFAULT_PALETTE) -> dict:
    log = run_qml(qml, what, palette_path)
    problems = [line for line in log.splitlines()
                if re.search(r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member"
                             r"|Cannot assign|Required property|is not defined|Invalid property|Binding loop", line)]
    match = re.search(r"RESULT ([\[{].*[\]}])", log)
    if problems or not match:
        raise RuntimeError(f"qml6 nie narysował {what}:\n" + log)
    return json.loads(match.group(1))


def render_tile(scenario: dict, png: str | None = None) -> dict:
    qml = TILE_HARNESS % {"data": json.dumps(scenario, ensure_ascii=False), "margin": MARGIN, "dock": DOCK_HEIGHT,
                          "tile": TILE_HEIGHT, "png": png or ""}
    return _run(qml, "kafelka Praca")


POPUP_REPORT = ("{ subtitle: popup.children[1].text, groups: popup.groups.map(g => [g.key, g.count, g.rows.length]),"
                " rows: popup.groups.reduce((all, g) => all.concat(g.rows.map(r => [r.label, r.group, r.word, r.line,"
                " r.go, r.addCard, r.launch])), []), height: Math.round(popup.implicitHeight) }")


def render_popup(data: dict | None = None, png: str | None = None, available: bool = True,
                 show_finished: bool = False, report: dict | None = REPORT, status: str = "") -> dict:
    extra = (f"showFinished: {'true' if show_finished else 'false'}\n        report: {json.dumps(report)}\n"
             f"        status: {json.dumps(status, ensure_ascii=False)}")
    qml = POPUP_HARNESS % {"width": POPUP_WIDTH, "component": "WorkPopup",
                           "work": json.dumps(MOCK if data is None else data, ensure_ascii=False),
                           "available": "true" if available else "false", "now": NOW_TS, "extra": extra,
                           "report": POPUP_REPORT, "png": png or ""}
    return _run(qml, "popupu Praca")


def render_sidebar(data: dict | None = None, png: str | None = None, available: bool = True) -> dict:
    report = ("{ title: popup.title, summary: popup.summary, rows: popup.rows.map(r => [r.label, r.word]),"
              " hidden: popup.hiddenCount, height: Math.round(popup.implicitHeight) }")
    qml = POPUP_HARNESS % {"width": SIDEBAR_WIDTH - 32, "component": "SidebarWork",
                           "work": json.dumps(MOCK if data is None else data, ensure_ascii=False),
                           "available": "true" if available else "false", "now": NOW_TS, "extra": "",
                           "report": report, "png": png or ""}
    return _run(qml, "kafelka Praca w pasku bocznym")


def render_whole_sidebar(png: str | None = None, scenario: str = "typical") -> dict:
    """Cały pasek boczny (widgets/preview.py) z kafelkiem Praca w miejscu Agentów — wysokości i obraz złożony
    z dwóch zrzutów: nagłówek, Praca, potem System, Worktrees i Limity AI."""
    from widgets import preview as sidebar_preview
    base = sidebar_preview.render_sidebar(sidebar_preview.SIDEBAR_SCENARIOS[scenario])
    tile = render_sidebar()["height"]
    heights = base["heights"]
    total = base["total"] - heights[1] + tile
    out = {"heights": [heights[0], tile, *heights[2:]], "total": total, "height": base["height"]}
    if png:
        with tempfile.TemporaryDirectory(prefix="tide-work-sidebar-") as tmp:
            whole, ours = os.path.join(tmp, "whole.png"), os.path.join(tmp, "work.png")
            sidebar_preview.render_sidebar(sidebar_preview.SIDEBAR_SCENARIOS[scenario], whole)
            render_sidebar(png=ours)
            _compose_sidebar(whole, ours, heights, sidebar_preview, png)
    return out


def _compose_sidebar(whole: str, ours: str, heights: list, sidebar_preview, png: str) -> None:
    """Wycina kafelek Agentów ze zrzutu paska i wstawia w jego miejsce kafelek Praca (Pillow)."""
    from PIL import Image
    inset, spacing = sidebar_preview.SIDEBAR_INSET, sidebar_preview.SIDEBAR_APPLET_SPACING
    bar = Image.open(whole).convert("RGBA")
    tile = Image.open(ours).convert("RGBA")
    tile = tile.crop((16, 16, tile.width - 16, tile.height - 16))
    top = inset + heights[0] + spacing          # początek kafelka Agentów
    rest = top + heights[1] + spacing           # początek kafelka System
    out = bar.copy()
    out.paste(bar.crop((0, 0, bar.width, top)), (0, 0))
    out.paste(Image.new("RGBA", (bar.width - 2 * inset, bar.height - top - inset), bar.getpixel((inset + 2, top - 1))),
              (inset, top))
    out.paste(tile, (inset, top))
    lower = bar.crop((0, rest, bar.width, bar.height - inset))
    out.paste(lower.crop((inset, 0, bar.width - inset, lower.height)), (inset, top + tile.height + spacing))
    out.save(png)


def main(argv: list[str]) -> int:
    out_dir = argv[0] if argv else os.path.join(ROOT, "build", "preview")
    os.makedirs(out_dir, exist_ok=True)
    for name, scenario in TILE_SCENARIOS.items():
        png = os.path.join(out_dir, f"work-tile-{name}.png")
        report = render_tile(scenario, png)
        label = report["label"] + (" · " + report["detail"] if report["detail"] else "")
        print(f"{os.path.abspath(png)}: kafelek „{label}” — {report['summary']}")
    png = os.path.join(out_dir, "work-popup.png")
    popup = render_popup(png=png)
    print(f"{os.path.abspath(png)}: popup Praca ({', '.join(f'{g[0]} {g[1]}' for g in popup['groups'])})")
    png = os.path.join(out_dir, "work-popup-finished.png")
    render_popup(png=png, show_finished=True, status="Założono kartę K-32")
    print(f"{os.path.abspath(png)}: popup Praca z rozwiniętymi Skończonymi")
    png = os.path.join(out_dir, "work-popup-empty.png")
    render_popup(work([]), png=png, report=None)
    print(f"{os.path.abspath(png)}: popup Praca bez pracy")
    png = os.path.join(out_dir, "work-sidebar.png")
    side = render_sidebar(png=png)
    print(f"{os.path.abspath(png)}: Praca w pasku bocznym ({side['height']} px)")
    png = os.path.join(out_dir, "work-sidebar-whole.png")
    try:
        whole = render_whole_sidebar(png)
        print(f"{os.path.abspath(png)}: cały pasek boczny {whole['heights']} px, razem {whole['total']}"
              f" z {whole['height']} px")
    except ImportError:  # bez Pillow — tylko wysokości
        whole = render_whole_sidebar()
        print(f"cały pasek boczny: {whole['heights']} px, razem {whole['total']} z {whole['height']} px (bez Pillow)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
