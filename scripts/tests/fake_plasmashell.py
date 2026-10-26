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


def run(state: dict, script: str) -> tuple[str, dict]:
    """Wykonuje skrypt na stanie; zwraca wypisany tekst i nowy stan."""
    fake = (HERE / "fake_plasmashell.js").read_text()
    with tempfile.TemporaryDirectory() as tmp:
        combined = (f"{fake}\nfunction __script() {{\n{script}\n}}\n"
                    f"function main() {{ return run({json.dumps(state)}, __script); }}\n")
        (Path(tmp) / "harness.js").write_text(combined)
        (Path(tmp) / "main.qml").write_text(
            'import QtQuick\nimport "harness.js" as H\n'
            f'Item {{ Component.onCompleted: {{ console.warn("{RESULT}" + H.main()); Qt.quit(); }} }}\n')
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_FORCE_STDERR_LOGGING": "1"}
        proc = subprocess.run([QML, str(Path(tmp) / "main.qml")], capture_output=True, text=True, env=env, timeout=60)
    log = proc.stdout + proc.stderr
    start = log.find(RESULT)
    if start < 0:
        raise RuntimeError(f"Skrypt nie zadziałał na atrapie plasmashell:\n{log}")
    value, _ = json.JSONDecoder().raw_decode(log[start + len(RESULT):])
    return value["out"], value["state"]
