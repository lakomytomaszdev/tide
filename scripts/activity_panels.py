"""Układy paneli wyglądów aktywności (PT-36, decyzja 80) — skrypty plasmashell i porównanie stanu.

Panele Plasmy 6.8 są wspólne dla wszystkich aktywności, więc Stream nie wymienia paneli pracy, tylko
dokłada swoje i chowa cudze:
- dock Stream na dole lewego monitora (scripts/panels/stream.js) powstaje przy wejściu do Stream
  i znika przy wyjściu (stream-leave.js) — jego widżety nie mają konfiguracji, którą warto chronić,
- kafelki pracy (dock DP-2 i pasek boczny) chowają się same: Plasmoid.status = HiddenStatus,
  gdy tide-daemon /ActivityLook mówi, że bieżący wygląd nie ma paneli pracy (widgets/lib/ActivityLook.qml);
  ich konfiguracja, kolejność i identyfikatory zostają nietknięte,
- pasek boczny pracy dostaje w Stream chowanie automatyczne (pusty, bo jego kafelki są ukryte, a nagłówek
  nie reaguje na Meta+S); wartość sprzed Stream leży w $XDG_STATE_HOME/tide/stream-panels.json
  i wraca przy wyjściu,
- konsola streamu (Sceny i czat) na pulpicie aktywności Stream na lewym monitorze — pulpity są osobne
  dla każdej aktywności, więc praca jej nie widzi; dokładana tylko, gdy jej brakuje.

Moduł nie importuje activity_look (silnik importuje ten moduł) — dostaje funkcję odczytu i zapisu.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "scripts" / "panels"
READ_TAG = "/* tide:read */"
MARKER = "TIDE_JSON:"

MAIN_OUTPUT = os.environ.get("TIDE_MAIN_OUTPUT", "DP-2")
SIDE_OUTPUT = os.environ.get("TIDE_SIDE_OUTPUT", "HDMI-A-1")

# Dock Stream na lewym monitorze (decyzja użytkownika 28.10: w trybie Xbox prawy monitor pokazuje Xboxa).
# Z lewej stan streamu, profil gracza i Observer (ten pokazuje się sam tylko w trybie Observer).
# Z prawej Kamera (PT-82) i „?”; PC ⇄ Xbox (org.tide.stream.monitors, PT-38) wejdzie przed „?”,
# gdy jego pakiet trafi do repozytorium.
STREAM_LEFT = ["org.tide.stream", "org.tide.stream.profile", "org.tide.stream.observer"]
STREAM_RIGHT = ["org.tide.stream.camera", "org.tide.stream.help"]
STREAM_FUTURE = {"org.tide.stream.monitors": "PT-38"}
TASKS = "org.kde.plasma.icontasks"
SPACER = "org.kde.plasma.panelspacer"
# Konsola streamu na pulpicie lewego monitora (mock „Konsola streamu”): Sceny w kolumnie ok. 1/3
# szerokości, czat w reszcie. Pulpit Plasmy układa aplety w siatce 16 px i dokłada do podanego rozmiaru
# ramkę kontenera apletu, a nachodzących na siebie prostokątów nie przyjmuje (czat dostawał wtedy swój
# domyślny rozmiar) — stąd wielokrotności 16 i odstęp z zapasem na ramki; widać go jako ok. 26 px z mocka.
CONSOLE = {"scenes": "org.tide.stream.scenes", "chat": "org.tide.stream.chat", "margin": 32, "gap": 48,
           "scenesFraction": 1 / 3}
SIDEBAR_HIDING = "autohide"
# Typ pulpitu (kontenera) aktywności: Plasma daje nowej aktywności ten sam co pracy (na tym komputerze
# org.kde.plasma.folder na obu monitorach, 28.10.2026); skrypt plasmashell typu zmienić nie może.
DESKTOP_TYPE = "org.kde.plasma.folder"
# Odstęp pływającego panelu od krawędzi ekranu (jak FLOATING_GAP w scripts/sidebar.py).
FLOATING_GAP = 8
STATE_FILE = "stream-panels.json"


def screens() -> dict:
    return {"mainOutput": MAIN_OUTPUT, "sideOutput": SIDE_OUTPUT}


def script(name: str, params: dict) -> str:
    """Skrypt zapisujący: parametry P, wspólne funkcje common.js i treść scripts/panels/<name>.js."""
    common = (JS_DIR / "common.js").read_text()
    body = (JS_DIR / f"{name}.js").read_text()
    return f"// tide:{name}\nvar P = {json.dumps(params, ensure_ascii=False)};\n{common}\n{body}"


def read_script(desktop: bool) -> str:
    """Skrypt tylko do odczytu (READ_TAG na początku, bez common.js) — check go przepuszcza."""
    params = {**screens(), "desktop": desktop}
    return f"{READ_TAG}\nvar P = {json.dumps(params)};\n" + (JS_DIR / "stream-read.js").read_text()


def result(output: str) -> dict:
    start = output.rfind(MARKER)
    if start < 0:
        raise ValueError(f"skrypt plasmashell nie zwrócił wyniku: {output.strip()[:200]}")
    value, _ = json.JSONDecoder().raw_decode(output[start + len(MARKER):].strip())
    return value


def installed(data: Path, types: list[str]) -> list[str]:
    roots = (data / "plasma" / "plasmoids", Path("/usr/share/plasma/plasmoids"))
    return [t for t in types if any((root / t).is_dir() for root in roots)]


def stream_layout(data: Path) -> dict:
    """Oczekiwany dock i konsola Stream — tylko z zainstalowanych widżetów."""
    console = installed(data, [CONSOLE["scenes"], CONSOLE["chat"]])
    return {"left": installed(data, STREAM_LEFT), "right": installed(data, STREAM_RIGHT),
            "console": console}


def expected_dock(layout: dict) -> list[str]:
    return [*layout["left"], SPACER, TASKS, SPACER, *layout["right"]]


def by_role(state: dict, role: str) -> list[dict]:
    return [p for p in state.get("panels", []) if p.get("role") == role]


def stream_differences(state: dict, layout: dict, desktop: bool) -> list[str]:
    side = state.get("screens", {}).get("side", -1)
    if side < 0:
        return []
    out = []
    docks = by_role(state, "stream-dock")
    if len(docks) != 1:
        out.append("brak docka Stream" if not docks else f"{len(docks)} docki Stream")
    else:
        dock = docks[0]
        if dock["screen"] != side or dock["location"] != "bottom":
            out.append(f"dock Stream nie na dole lewego monitora (ekran {dock['screen']}, {dock['location']})")
        if dock["widgets"] != expected_dock(layout):
            out.append("widżety docka Stream: " + ", ".join(_short(t) for t in dock["widgets"]))
    for sidebar in by_role(state, "sidebar"):
        if sidebar["hiding"] != SIDEBAR_HIDING:
            out.append(f"pasek boczny pracy widoczny ({sidebar['hiding']})")
    if desktop:
        missing = [t for t in layout["console"] if t not in (state.get("desktop") or [])]
        if missing:
            out.append("brak na pulpicie lewego monitora: " + ", ".join(_short(t) for t in missing))
    return out


def work_plan(state: dict, saved: dict) -> tuple[list[str], str | None]:
    """Różnice paneli pracy i chowanie paska bocznego do przywrócenia (None — nic do przywrócenia).

    Zapis sprzed Stream przywracamy tylko wtedy, gdy pasek ma nasze „autohide”. Inna wartość (a docka Stream
    już nie ma) to wybór użytkownika po Stream — zapis jest nieaktualny i znika, a pasek zostaje, jak jest."""
    out = [f"dock Stream (panel {p['id']}) do usunięcia" for p in by_role(state, "stream-dock")]
    hiding = saved.get("hiding")
    restore = None
    for sidebar in by_role(state, "sidebar"):
        if hiding and sidebar["hiding"] != hiding:
            if sidebar["hiding"] == SIDEBAR_HIDING:
                restore = hiding
                out.append(f"pasek boczny pracy: chowanie {sidebar['hiding']}, było {hiding}")
            else:
                out.append(f"nieaktualny zapis chowania paska ({hiding}; pasek ma {sidebar['hiding']}) — do usunięcia")
    if saved and not out:
        out.append("zapis chowania paska sprzed Stream bez potrzeby — do usunięcia")
    return out, restore


def leave_params(restore: str | None) -> dict:
    return {**screens(), "sidebarHiding": restore}


def desktop_type_note(state: dict) -> str:
    """Typ pulpitu Stream na lewym monitorze względem pulpitów innych aktywności (pracy) — tylko do opisu."""
    mine = state.get("desktopType")
    if not mine:
        return ""
    others = state.get("otherDesktopTypes") or [DESKTOP_TYPE]
    if mine in others:
        return f"typ pulpitu jak w pracy: {mine}"
    return (f"typ pulpitu {mine}, w pracy {', '.join(others)} — zmień w ustawieniach pulpitu "
            "(skrypty Plasmy nie zmieniają typu)")


def describe(state: dict) -> str:
    parts = []
    for panel in state.get("panels", []):
        role = f" {panel['role']}" if panel.get("role") else ""
        parts.append(f"{panel['id']}{role} ({panel['location']}, ekran {panel['screen']}, {panel['hiding']})")
    return "; ".join(parts) or "brak paneli"


def _short(widget_type: str) -> str:
    return widget_type.removeprefix("org.tide.").removeprefix("org.kde.plasma.")


def stream_params(layout: dict, dock_height: int, console: bool) -> dict:
    return {**screens(), "left": layout["left"], "right": layout["right"], "tasks": TASKS, "spacer": SPACER,
            "dockHeight": dock_height, "floatingGap": FLOATING_GAP, "sidebarHiding": SIDEBAR_HIDING,
            "console": {**CONSOLE, "scenes": CONSOLE["scenes"] if CONSOLE["scenes"] in layout["console"] else "",
                        "chat": CONSOLE["chat"] if CONSOLE["chat"] in layout["console"] else ""}
            if console and layout["console"] else None}


def order_params(dock: int, order: list[int]) -> dict:
    return {**screens(), "panels": [{"id": dock, "order": order}]}
