"""Uruchamia skrypt plasmashell na atrapie API (fake_plasmashell.js) w qml6.

Używane przez testy skryptów paneli i przez atrapę qdbus6 w testach instalacji.
Stan atrapy (monitory, panele, widżety, konfiguracja) to zwykły JSON.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
QML = shutil.which("qml6") or "/usr/lib/qt6/bin/qml"
RESULT = "TIDE_FAKE_RESULT:"


def available() -> bool:
    return Path(QML).exists()


def widget(wid: int, type_: str, config: dict | None = None) -> dict:
    return {"id": wid, "type": type_, "config": config or {}, "removed": False}


def panel(pid: int, screen: int, location: str, height: int, widgets: list[dict], floating: bool = True) -> dict:
    ids = [w["id"] for w in widgets]
    return {"id": pid, "screen": screen, "location": location, "height": height, "floating": floating,
            "alignment": "center", "lengthMode": "fill", "hiding": "none", "opacity": "adaptive",
            "offset": 0, "minimumLength": 2560, "maximumLength": 2560,
            "config": {"/": {"plugin": "org.kde.panel"}, "/General": {"AppletOrder": ";".join(map(str, ids))}},
            "widgets": widgets, "layout": ids, "log": [], "removed": False}


def desktop_like_this_one(icon_path: str = "/tmp/plasma_icons/index.html.desktop",
                          screens: tuple[str, ...] = ("DP-2", "HDMI-A-1")) -> dict:
    """Układ jak na tym komputerze (26.10.2026): panel 73 u góry i dock 90 na DP-2."""
    top = panel(73, 0, "top", 36, [
        widget(74, "org.kde.plasma.kickoff", {"/General": {"icon": "go-home-large-symbolic",
                                                          "favoritesPortedToKAstats": "true"}}),
        widget(96, "org.kde.plasma.icon", {"/": {"localPath": icon_path, "url": "file:///tmp/index.html"}}),
        widget(75, "org.kde.plasma.panelspacer", {"/General": {"expanding": "true"}}),
        widget(76, "org.kde.plasma.digitalclock", {"/Appearance": {"showDate": "true"},
                                                   "/": {"popupHeight": "451"}}),
        widget(77, "org.kde.plasma.panelspacer", {"/General": {"expanding": "true"}}),
        widget(78, "org.kde.plasma.systemtray", {"/": {"plugin": "org.kde.plasma.systemtray", "immutability": "1"},
                                                 "/General": {"extraItems": "org.kde.plasma.volume"},
                                                 "/Applets/89": {"plugin": "org.kde.plasma.volume"}}),
    ])
    dock = panel(90, 0, "bottom", 64, [widget(91, "org.kde.plasma.icontasks"), widget(97, "org.limityai.widget")])
    return {"screens": list(screens), "panels": [top, dock], "nextId": 200}


def desktop_with_tide_panels(work: str = "work") -> dict:
    """Panele Tide jak na tym komputerze 28.10.2026 (panels.py dump): górny pasek i dock DP-2, smukły pasek
    i pasek boczny HDMI-A-1 — z ustawieniami apletów pracy, które powrót ze Stream musi zachować."""
    top = panel(73, 0, "top", 34, [
        widget(74, "org.kde.plasma.kickoff", {"/General": {"icon": "tide-logo"}}),
        widget(75, "org.kde.plasma.appmenu"),
        widget(76, "org.kde.plasma.panelspacer", {"/General": {"expanding": "true"}}),
        widget(77, "org.kde.plasma.digitalclock", {"/Appearance": {"showDate": "false"}}),
        widget(78, "org.kde.plasma.panelspacer", {"/General": {"expanding": "true"}}),
        widget(79, "org.kde.plasma.systemtray"),
    ])
    dock = panel(90, 0, "bottom", 64, [
        widget(91, "org.tide.system"), widget(92, "org.tide.docker"), widget(93, "org.tide.worktrees"),
        widget(94, "org.kde.plasma.panelspacer", {"/General": {"expanding": "true"}, "/Tide": {"role": "dock-spacer"}}),
        widget(95, "org.kde.plasma.icontasks", {"/General": {"fill": "false", "launchers": "applications:org.kde.konsole.desktop"}}),
        widget(96, "org.kde.plasma.panelspacer", {"/General": {"expanding": "true"}, "/Tide": {"role": "dock-spacer"}}),
        widget(97, "org.tide.work", {"/Configuration/General": {"showDone": "true"}}),
        widget(98, "org.tide.limits", {"/Configuration/General": {"provider": "claude"}}),
    ])
    slim = panel(103, 1, "top", 30, [
        widget(104, "org.kde.plasma.appmenu"),
        widget(105, "org.kde.plasma.panelspacer", {"/General": {"expanding": "true"}}),
        widget(106, "org.kde.plasma.digitalclock"),
        widget(107, "org.kde.plasma.panelspacer", {"/General": {"expanding": "true"}}),
    ])
    slim["config"]["/Tide"] = {"role": "side-bar"}
    side = panel(118, 1, "left", 420, [
        widget(119, "org.tide.sidebar"),
        widget(120, "org.tide.work", {"/Configuration/General": {"sidebarMode": "true"}}),
        widget(121, "org.tide.system", {"/Configuration/General": {"sidebarMode": "true"}}),
        widget(122, "org.tide.worktrees", {"/Configuration/General": {"sidebarMode": "true"}}),
        widget(123, "org.tide.limits", {"/Configuration/General": {"sidebarMode": "true"}}),
        widget(124, "org.kde.plasma.panelspacer", {"/General": {"expanding": "true"}, "/Tide": {"role": "sidebar-spacer"}}),
    ])
    side.update(hiding="dodgewindows", lengthMode="custom", alignment="left", offset=38, minimumLength=1042,
                maximumLength=1042)
    side["config"]["/Tide"] = {"role": "sidebar"}
    for p_ in (slim,):
        p_.update(minimumLength=1920, maximumLength=1920)
    return {"screens": ["DP-2", "HDMI-A-1"], "panels": [top, dock, slim, side], "nextId": 200,
            "currentActivity": work, "geometries": [{"x": 1920, "y": 0, "width": 2560, "height": 1440},
                                                     {"x": 0, "y": 0, "width": 1920, "height": 1080}],
            "desktops": [{"id": 25, "screen": 0, "activity": work, "widgets": []},
                         {"id": 26, "screen": 1, "activity": work, "widgets": []}]}


def desktop_like_15_11(work: str = "work") -> dict:
    """Stan z 15.11.2026: DP-2 wyłączony, jeden ekran HDMI-A-1 (Plasma numeruje go 0), a panele 103 i 118
    mają zapamiętane lastScreen=1, czyli `screen=1 >= screenCount` — to panele bez widoku. Reszta jak
    w desktop_with_tide_panels (górny pasek i dock na ekranie 0; pulpit 26 też ze screen=1)."""
    state = desktop_with_tide_panels(work)
    state["screens"] = ["HDMI-A-1"]
    state["geometries"] = [{"x": 0, "y": 0, "width": 1920, "height": 1080}]
    return state


def run(state: dict, script: str) -> tuple[str, dict]:
    """Wykonuje skrypt na stanie; zwraca wypisany tekst i nowy stan."""
    fake = (HERE / "fake_plasmashell.js").read_text()
    with tempfile.TemporaryDirectory() as tmp:
        combined = (f"{fake}\nfunction __script() {{\n{script}\n}}\n"
                    f"function main() {{ return run({json.dumps(state)}, __script); }}\n")
        (Path(tmp) / "harness.js").write_text(combined)
        (Path(tmp) / "main.qml").write_text(
            'import QtQuick\nimport "harness.js" as H\n'
            # Wyjątek skryptu (np. „panel bez widoku”) nie może zawiesić qml6 do limitu czasu: kończymy z jego treścią.
            f'Item {{ Component.onCompleted: {{ try {{ console.warn("{RESULT}" + H.main()); }} '
            f'catch (e) {{ console.warn("TIDE_FAKE_ERROR: " + e); }} Qt.quit(); }} }}\n')
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_FORCE_STDERR_LOGGING": "1"}
        proc = subprocess.run([QML, str(Path(tmp) / "main.qml")], capture_output=True, text=True, env=env, timeout=60)
    log = proc.stdout + proc.stderr
    start = log.find(RESULT)
    if start < 0:
        raise RuntimeError(f"Skrypt nie zadziałał na atrapie plasmashell:\n{log}")
    value, _ = json.JSONDecoder().raw_decode(log[start + len(RESULT):])
    return value["out"], value["state"]
