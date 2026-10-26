"""Dock Tide: dolny panel głównego monitora z aplikacjami pośrodku i widżetami Tide z prawej.

Użycie: python3 dock.py install|uninstall|show

Widżety trafiają do docka tylko wtedy, gdy są zainstalowane (katalog w plasma/plasmoids),
więc kolejność `make install` nie ma znaczenia: ponowne uruchomienie dołoży brakujące.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.appearance import DATA, PALETTE, STATE  # noqa: E402
from scripts.panels import evaluate, load, persist_thickness, save, screens, script, settle  # noqa: E402

INSTALLED = STATE / "tide" / "dock-installed.json"
# Kolejność kafelków z prawej strony docka, jak w mocku Mist; „Zadanie” obok agentów (mock #22).
DOCK_WIDGETS = ["org.tide.agents", "org.tide.newtask", "org.tide.worktrees", "org.tide.system", "org.tide.limits"]


def installed_widgets() -> list[str]:
    return [w for w in DOCK_WIDGETS if (DATA / "plasma" / "plasmoids" / w).is_dir()]


def params() -> dict:
    palette = json.loads(PALETTE.read_text())
    return {**screens(), "widgets": installed_widgets(), "dockHeight": palette["size"]["dock"]}


def install() -> None:
    result = evaluate(script("dock", params()))
    settle()
    evaluate(script("order", {**screens(), "panels": [{"id": result["dock"], "order": result["order"]}]}))
    persist_thickness(result["dock"], params()["dockHeight"])
    previous = load(INSTALLED)
    save(INSTALLED, {
        "dock": result["dock"],
        # Stan sprzed Tide zapamiętujemy raz — kolejne instalacje go nie nadpisują.
        "previous": previous.get("previous") or result["previous"],
        "created": previous.get("created", result["created"]),
    })
    print(f"Dock Tide: panel {result['dock']}, widżety: {', '.join(params()['widgets']) or 'brak'}.")


def uninstall() -> None:
    installed = load(INSTALLED)
    if not installed:
        print("Dock Tide nie był instalowany.")
        return
    evaluate(script("dock-uninstall", {**screens(), **installed, "widgets": DOCK_WIDGETS}))
    INSTALLED.unlink(missing_ok=True)
    print("Dock Tide odinstalowany, przywrócono poprzedni układ dolnego panelu.")


def show() -> None:
    print(script("dock", params()))


if __name__ == "__main__":
    {"install": install, "uninstall": uninstall, "show": show}[sys.argv[1]]()
