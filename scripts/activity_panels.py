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
- konsola streamu na pulpicie aktywności Stream na lewym monitorze — jeden aplet „Lewy monitor Stream”
  (org.tide.stream.desk, decyzja 82), a bez jego pakietu Sceny i czat; pulpity są osobne dla każdej aktywności,
  więc praca jej nie widzi; dokładana tylko, gdy jej brakuje, a stawiając desk, zdejmuje z tego pulpitu starą parę.

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
# Z prawej Kamera (PT-82), PC ⇄ Xbox (PT-38, pakiet w widgets/monitors — czyta tide-daemon) i „?”.
STREAM_LEFT = ["org.tide.stream", "org.tide.stream.profile", "org.tide.stream.observer"]
STREAM_RIGHT = ["org.tide.stream.camera", "org.tide.stream.monitors", "org.tide.stream.help"]
TASKS = "org.kde.plasma.icontasks"
SPACER = "org.kde.plasma.panelspacer"
# Konsola streamu na pulpicie lewego monitora. Od PT-83 (decyzja 82) to jeden aplet „Lewy monitor Stream”
# (desk: konsola streamu albo Granie) na całym pulpicie: margines 32, cała szerokość i wysokość nad dockiem Stream.
# Bez jego pakietu zostaje para z PT-36 (mock „Konsola streamu”): Sceny w kolumnie ok. 1/3 szerokości, czat
# w reszcie. Pulpit Plasmy układa aplety w siatce 16 px i dokłada do podanego rozmiaru ramkę kontenera apletu,
# a nachodzących na siebie prostokątów nie przyjmuje (czat dostawał wtedy swój domyślny rozmiar) — stąd
# wielokrotności 16 i odstęp z zapasem na ramki; widać go jako ok. 26 px z mocka.
CONSOLE = {"desk": "org.tide.stream.desk", "scenes": "org.tide.stream.scenes", "chat": "org.tide.stream.chat",
           "margin": 32, "gap": 48, "scenesFraction": 1 / 3}
SIDEBAR_HIDING = "autohide"
# Do tego pasek zwężony do minimum (decyzja 80, zgłoszenie po pierwszej instalacji: „autohide” sam wysuwał pusty
# pasek 420 px przy każdym dotknięciu lewej krawędzi). Chowany panel wysuwa się tylko przy swoim odcinku krawędzi,
# więc długość 1 px praktycznie wyłącza wysuwanie, a grubość 1 Plasma podnosi do swojego minimum. Długość i grubość
# sprzed Stream trafiają do tego samego zapisu co chowanie (stream-panels.json) i wracają przy wyjściu.
THIN_LENGTH = 1
THIN_THICKNESS = 1
SIDEBAR_KEYS = ("hiding", "height", "minimumLength", "maximumLength")
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
    """Oczekiwany dock i konsola Stream — tylko z zainstalowanych widżetów.

    Konsola to desk, a gdy go nie ma — para Sceny i czat (zgodność wstecz). Z deskiem para jest „retired”:
    zdejmuje ją z pulpitu Stream wejście, które stawia desk (migracja instalacji sprzed PT-83)."""
    desk = installed(data, [CONSOLE["desk"]])
    pair = [CONSOLE["scenes"], CONSOLE["chat"]]
    return {"left": installed(data, STREAM_LEFT), "right": installed(data, STREAM_RIGHT),
            "console": desk or installed(data, pair), "retired": pair if desk else []}


def expected_dock(layout: dict) -> list[str]:
    return [*layout["left"], SPACER, TASKS, SPACER, *layout["right"]]


def by_role(state: dict, role: str) -> list[dict]:
    return [p for p in state.get("panels", []) if p.get("role") == role]


def stream_differences(state: dict, layout: dict, desktop: bool, saved: dict | None = None) -> list[str]:
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
        if not sidebar_thin(sidebar):
            out.append(f"pasek boczny pracy niezwężony (długość {sidebar.get('maximumLength')})")
        elif (saved or {}).get("height") and sidebar["height"] >= saved["height"] > THIN_THICKNESS:
            # Grubości skryptem Plasma nie utrwala: po ponownym zalogowaniu w Stream wraca grubość sprzed Stream.
            out.append(f"pasek boczny pracy z grubością sprzed Stream ({sidebar['height']})")
    if desktop:
        on_desktop = state.get("desktop") or []
        missing = [t for t in layout["console"] if t not in on_desktop]
        retired = [t for t in layout.get("retired", []) if t in on_desktop]
        if missing and retired:
            out.append("konsola streamu na pulpicie lewego monitora do wymiany: "
                       + ", ".join(_short(t) for t in retired) + " → " + ", ".join(_short(t) for t in missing))
        elif missing:
            out.append("brak na pulpicie lewego monitora: " + ", ".join(_short(t) for t in missing))
    return out


def sidebar_thin(sidebar: dict) -> bool:
    return int(sidebar.get("maximumLength") or 0) <= THIN_LENGTH


def _stream_valued(sidebar: dict, key: str) -> bool:
    """Czy ta wartość paska to wartość Stream (wtedy nie może trafić do zapisu „sprzed Stream”). Grubości
    skryptem Plasma nie utrwala — po ponownym zalogowaniu w Stream wraca stara, choć długość dalej jest Stream —
    więc grubość i długości liczą się razem: pasek zwężony to wartości Stream."""
    if key == "hiding":
        return sidebar["hiding"] == SIDEBAR_HIDING
    return sidebar_thin(sidebar)


def sidebar_record(sidebar: dict, saved: dict) -> dict | None:
    """Zapis paska sprzed Stream przed nałożeniem Stream; None — zapis bez zmian.

    Nigdy nie zapisuje wartości Stream. Pasek całkiem „pracowy” (inne chowanie i pełna długość) mimo zapisu to
    pasek odbudowany po wejściu do Stream — jego wartości zastępują zapis. Inaczej zapis tylko się uzupełnia
    (np. zapis z pierwszej wersji, z samym chowaniem, dostaje długości i grubość)."""
    fresh = {key: sidebar[key] for key in SIDEBAR_KEYS if not _stream_valued(sidebar, key)}
    rebuilt = sidebar["hiding"] != SIDEBAR_HIDING and not sidebar_thin(sidebar)
    if saved and saved.get("sidebar", sidebar["id"]) == sidebar["id"] and not rebuilt:
        merged = {**{k: v for k, v in fresh.items() if k not in saved}, **saved, "sidebar": sidebar["id"]}
        return merged if merged != saved else None
    return {"sidebar": sidebar["id"], **fresh} if fresh else None


def sidebar_restore(sidebar: dict, saved: dict) -> dict:
    """Co z zapisu przywrócić przy powrocie do pracy. Tylko to, co dalej ma wartość Stream — zmiana zrobiona
    przez użytkownika w Stream (inna długość, inne chowanie lub grubość) zostaje."""
    if not saved or saved.get("sidebar", sidebar["id"]) != sidebar["id"]:
        return {}
    out = {}
    if sidebar_thin(sidebar):
        out.update({k: saved[k] for k in ("maximumLength", "minimumLength")
                    if k in saved and saved[k] != sidebar[k]})
    if ("height" in saved and sidebar["height"] != saved["height"]
            and (sidebar["height"] == saved.get("streamHeight")
                 or ("streamHeight" not in saved and sidebar_thin(sidebar)))):
        # Grubość wciąż jest nasza, nawet gdy użytkownik zmienił samą długość. Stary zapis bez
        # streamHeight rozpoznajemy po zwężonej długości; po nowym zapisie zachowujemy ręczną grubość.
        out["height"] = saved["height"]
    if sidebar["hiding"] == SIDEBAR_HIDING and saved.get("hiding") and saved["hiding"] != SIDEBAR_HIDING:
        out["hiding"] = saved["hiding"]
    return out


def work_plan(state: dict, saved: dict) -> tuple[list[str], dict]:
    """Różnice paneli pracy i wartości paska bocznego do przywrócenia z zapisu sprzed Stream ({} — nic).
    Zapis, z którego nie ma czego przywracać, jest nieaktualny (wybór użytkownika po Stream) i znika."""
    out = [f"dock Stream (panel {p['id']}) do usunięcia" for p in by_role(state, "stream-dock")]
    restore = {}
    for sidebar in by_role(state, "sidebar"):
        restore = sidebar_restore(sidebar, saved)
        if restore:
            out.append("pasek boczny pracy do przywrócenia: " + ", ".join(f"{k} {v}" for k, v in restore.items()))
    if saved and not out:
        out.append("nieaktualny zapis paska bocznego sprzed Stream — do usunięcia")
    return out, restore


def leave_params(restore: dict | None) -> dict:
    return {**screens(), "sidebar": restore or None}


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
    # Z typów konsoli skrypt dostaje tylko zainstalowane (pusty napis — brak); desk wyklucza parę.
    placed = {key: CONSOLE[key] if CONSOLE[key] in layout["console"] else "" for key in ("desk", "scenes", "chat")}
    return {**screens(), "left": layout["left"], "right": layout["right"], "tasks": TASKS, "spacer": SPACER,
            "dockHeight": dock_height, "floatingGap": FLOATING_GAP, "sidebar": {"hiding": SIDEBAR_HIDING, "length": THIN_LENGTH, "thickness": THIN_THICKNESS},
            "console": {**CONSOLE, **placed, "retired": layout["retired"]} if console and layout["console"] else None}


def order_params(dock: int, order: list[int]) -> dict:
    return {**screens(), "panels": [{"id": dock, "order": order}]}
