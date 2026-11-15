"""Górne paski Tide na obu monitorach: instalacja, odinstalowanie i podgląd skryptów.

Użycie: python3 panels.py install|uninstall|dump|show

- install   — logo Tide jako ikona `tide-logo` (hicolor), moduł kded „appmenu”, zrzut
              paneli do $XDG_STATE_HOME/tide/panels-previous.json (tylko przy pierwszej
              instalacji) i skrypty plasmashell zmieniające panele,
- uninstall — usuwa pasek lewego monitora i przywraca główny pasek ze zrzutu,
- dump      — tylko odczyt: wypisuje bieżące panele w formacie zrzutu,
- show      — wypisuje skrypty JS, które uruchomiłaby instalacja (niczego nie zmienia).

Panele zmieniamy przez API skryptowe plasmashell (org.kde.PlasmaShell.evaluateScript).
Główny pasek to górny panel głównego monitora, a pasek boczny ma w konfiguracji znacznik
[Tide] role=side-bar — numery paneli są tylko podpowiedzią.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import activity_panels  # noqa: E402
from scripts.appearance import DATA, PALETTE, STATE, run  # noqa: E402
from theme.logo.generate import svg as logo_svg  # noqa: E402

JS_DIR = ROOT / "scripts" / "panels"
PREVIOUS = STATE / "tide" / "panels-previous.json"
INSTALLED = STATE / "tide" / "panels-installed.json"
ICON_DIR = DATA / "icons" / "hicolor" / "scalable" / "apps"
ICON_NAME = "tide-logo"
MARKER = "TIDE_JSON:"

# Złącza monitorów (kscreen-doctor -o). Na innym komputerze można je podmienić zmiennymi.
MAIN_OUTPUT = os.environ.get("TIDE_MAIN_OUTPUT", "DP-2")
SIDE_OUTPUT = os.environ.get("TIDE_SIDE_OUTPUT", "HDMI-A-1")
MAIN_PANEL_HINT = 73  # tylko podpowiedź, gdy na głównym monitorze jest kilka górnych paneli

# Klucze, które instalacja zmienia w widżetach zostawionych na pasku, i ich wartości domyślne.
CHANGED_KEYS = {
    "org.kde.plasma.kickoff": ["/General|icon"],
    "org.kde.plasma.digitalclock": ["/Appearance|showDate"],
    "org.kde.plasma.systemtray": ["/General|scaleIconsToFit"],
}
DEFAULTS = {
    "org.kde.plasma.kickoff": {"/General|icon": "start-here-kde-symbolic"},
    "org.kde.plasma.digitalclock": {"/Appearance|showDate": "true"},
    "org.kde.plasma.systemtray": {"/General|scaleIconsToFit": "false"},
}

KDED = ("qdbus6", "org.kde.kded6", "/kded")


def script(name: str, params: dict) -> str:
    """Skrypt dla plasmashell: parametry P, wspólne funkcje i treść z scripts/panels/<name>.js."""
    common = (JS_DIR / "common.js").read_text()
    body = (JS_DIR / f"{name}.js").read_text()
    return f"// tide:{name}\nvar P = {json.dumps(params, ensure_ascii=False)};\n{common}\n{body}"


def outputs_disabled() -> bool:
    """Strażnik instalatorów: przy wyłączonym wyjściu z układu Tide nie ruszamy paneli (ani skryptem, ani zapisem
    stanu). Odczyt z kscreen-doctor, bez plasmashella; błąd odczytu tylko się zgłasza."""
    disabled, failure = activity_panels.guard(lambda argv: run(*argv))
    if failure:
        print(f"Uwaga: {failure}.")
    if disabled:
        print(f"Panele pominięte: wyłączone wyjście {', '.join(disabled)} (włącz monitor i uruchom ponownie).")
    return bool(disabled)


def evaluate(js: str) -> dict:
    out = run("qdbus6", "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", js)
    start = out.rfind(MARKER)
    if start < 0:
        raise SystemExit(f"Skrypt plasmashell nie zwrócił wyniku:\n{out}")
    value, _ = json.JSONDecoder().raw_decode(out[start + len(MARKER):].strip())
    return value


def orphans_note(result: dict) -> None:
    """Notka instalatora o panelach bez widoku, które skrypt pominął. Wynik starego skryptu bez klucza
    `orphans` albo z pustą listą nie jest błędem i nie daje notki."""
    ids = result.get("orphans") or []
    if ids:
        print(f"Panele bez widoku pominięte: {', '.join(str(i) for i in ids)}")


def screens() -> dict:
    return {"mainOutput": MAIN_OUTPUT, "sideOutput": SIDE_OUTPUT}


def install_params(palette: dict, installed: dict) -> dict:
    return {**screens(), "mainHint": installed.get("mainPanel") or MAIN_PANEL_HINT,
            "sidePanel": installed.get("sidePanel"), "icon": ICON_NAME,
            "barHeight": palette["size"]["bar"], "slimHeight": palette["size"]["barSlim"]}


def order_params(result: dict) -> dict:
    palette = json.loads(PALETTE.read_text())
    sizes = palette.get("size", {})
    panels = [{"id": result["main"], "order": result["mainOrder"], "height": sizes.get("bar")}]
    if result.get("side"):
        # Wysokość ustawiamy ponownie po przeniesieniu panelu — Plasma nadpisuje ją przy zmianie ekranu.
        panels.append({"id": result["side"], "order": result["sideOrder"], "height": sizes.get("barSlim")})
    return {**screens(), "panels": [p for p in panels if p["id"]]}


def settle() -> None:
    # Plasma dokłada nowe widżety do układu panelu asynchronicznie; kolejność ustawiamy po chwili.
    time.sleep(float(os.environ.get("TIDE_PANEL_SETTLE", "1.0")))


def persist_thickness(panel_id: int, thickness: int) -> None:
    """Plasma nie zapisuje wysokości ustawionej skryptem (po zalogowaniu wracało 56 px), więc
    wpisujemy ją wprost do plasmashellrc — stamtąd panel czyta grubość przy starcie."""
    run("kwriteconfig6", "--file", "plasmashellrc", "--group", "PlasmaViews", "--group", f"Panel {panel_id}",
        "--group", "Defaults", "--key", "thickness", str(thickness))


def load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.is_file() else {}


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def icons(palette: dict) -> dict[Path, str]:
    # Mała wersja (bez kafelka, czytelna od 18 px) jako przycisk menu; symboliczna obok.
    return {ICON_DIR / f"{ICON_NAME}.svg": logo_svg(palette, small=True),
            ICON_DIR / f"{ICON_NAME}-symbolic.svg": logo_svg(palette, symbolic=True)}


def icons_changed() -> None:
    subprocess.run(["dbus-send", "--session", "--type=signal", "/KIconLoader",
                    "org.kde.KIconLoader.iconChanged", "int32:0"], check=False)


def dump() -> dict:
    """Zrzut paneli razem z plikami .desktop widżetów „Ikona” (Plasma kasuje je z widżetem)."""
    value = evaluate(script("dump", screens()))
    for panel in value["panels"]:
        for widget in panel["widgets"]:
            local = widget["config"].get("/", {}).get("localPath")
            if widget["type"] == "org.kde.plasma.icon" and local and Path(local).is_file():
                widget["localFile"] = {"path": local, "content": Path(local).read_text()}
    return value


def appmenu_module(enable: bool) -> bool:
    """Globalne menu potrzebuje modułu kded „appmenu”. Zwraca True, jeśli coś zmieniliśmy."""
    try:
        loaded = run(*KDED, "org.kde.kded6.isModuleAutoloaded", "appmenu") == "true"
        if enable and not loaded:
            run(*KDED, "org.kde.kded6.setModuleAutoloading", "appmenu", "true")
            run(*KDED, "org.kde.kded6.loadModule", "appmenu")
            return True
        if not enable and loaded:
            run(*KDED, "org.kde.kded6.setModuleAutoloading", "appmenu", "false")
            run(*KDED, "org.kde.kded6.unloadModule", "appmenu")
            return True
    except subprocess.CalledProcessError as error:
        print(f"Uwaga: nie udało się sprawdzić modułu kded appmenu ({error.stderr.strip()}).")
    return False


def install() -> None:
    if outputs_disabled():
        return
    palette = json.loads(PALETTE.read_text())
    for path, content in icons(palette).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    icons_changed()

    if not PREVIOUS.is_file():
        save(PREVIOUS, dump())
    installed = load(INSTALLED)
    enabled = appmenu_module(True) or installed.get("kdedAppmenuEnabled", False)

    result = evaluate(script("install", install_params(palette, installed)))
    orphans_note(result)
    settle()
    order = evaluate(script("order", order_params(result)))
    for panel in order_params(result)["panels"]:
        if panel.get("height"):
            persist_thickness(panel["id"], panel["height"])

    save(INSTALLED, {
        "mainPanel": result["main"],
        "mainCreated": result["mainCreated"] or installed.get("mainCreated", False),
        "sidePanel": result.get("side"),
        "kdedAppmenuEnabled": enabled,
        "screens": result["screens"],
    })
    for panel in order["panels"]:
        print(f"Panel {panel['id']}: {', '.join(t.rsplit('.', 1)[-1] for t in panel['order'])}")
    if result.get("side") is None:
        if result["screens"].get("side", -1) >= 0:
            print("Uwaga: pasek lewego monitora nie ma widoku — pominięty, nowy nie powstał.")
        else:
            print("Uwaga: nie ma drugiego monitora — pasek lewego monitora nie powstał.")
    print("Górne paski Tide zainstalowane. Aplikacje uruchomione wcześniej pokażą globalne menu po restarcie.")


def uninstall() -> None:
    if outputs_disabled():
        return
    previous, installed = load(PREVIOUS), load(INSTALLED)
    for panel in previous.get("panels", []):
        for widget in panel["widgets"]:
            local = widget.get("localFile")
            if local and not Path(local["path"]).exists():
                Path(local["path"]).parent.mkdir(parents=True, exist_ok=True)
                Path(local["path"]).write_text(local["content"])

    params = {**screens(), "previous": previous or None, "mainPanel": installed.get("mainPanel"),
              "mainCreated": installed.get("mainCreated", False),
              "changedKeys": CHANGED_KEYS, "defaults": DEFAULTS}
    result = evaluate(script("uninstall", params))
    if result.get("main"):
        settle()
        evaluate(script("order", {**screens(), "panels": [{"id": result["main"], "order": result["mainOrder"]}]}))

    if installed.get("kdedAppmenuEnabled"):
        appmenu_module(False)
    for path in icons(json.loads(PALETTE.read_text())):
        path.unlink(missing_ok=True)
    icons_changed()
    PREVIOUS.unlink(missing_ok=True)
    INSTALLED.unlink(missing_ok=True)
    print("Górne paski Tide odinstalowane, przywrócono poprzedni układ głównego paska.")


def show() -> None:
    palette = json.loads(PALETTE.read_text())
    print(script("install", install_params(palette, load(INSTALLED))))
    example = {"main": MAIN_PANEL_HINT, "mainOrder": ["<kickoff>", "<appmenu>", "<spacer>", "<clock>",
                                                      "<spacer>", "<tray>"],
               "side": "<pasek boczny>", "sideOrder": ["<appmenu>", "<spacer>", "<clock>", "<spacer>"]}
    print("\n// ---- potem, z identyfikatorami zwróconymi przez skrypt wyżej:\n")
    print(script("order", order_params(example)))


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "dump":
        print(json.dumps(dump(), indent=2, ensure_ascii=False))
    else:
        {"install": install, "uninstall": uninstall, "show": show}[command]()
