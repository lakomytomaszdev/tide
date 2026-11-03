"""Dock Tide: dolny panel głównego monitora z aplikacjami pośrodku, systemem i Dockerem z lewej i sprawami AI z prawej.

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
# Kafelki, które wyszły z docka, ale mogą w nim jeszcze stać po starej instalacji: instalacja zdejmuje je
# skryptem dock.js (chyba że są na liście docka), a odinstalowanie usuwa razem z kafelkami Tide. Modele weszły
# do kafelka „AI” (org.tide.limits, PT-41), Tablicę i Agentów zastąpiła Praca (PT-40), „＋ Zadanie” — przycisk
# „＋” na kafelku (dawniej Agentów, dziś Pracy).
DOCK_RETIRED = ["org.tide.models", "org.tide.board", "org.tide.agents", "org.tide.newtask"]
# Kafelek, który zastąpił wycofane (PT-40: Praca — Tablicę i Agentów). Bez zainstalowanego nowego w jego miejscu
# stoją stare, jeśli są zainstalowane: wycofanie to `make uninstall-widget-work` i ponowne `make install-dock`
# (dock.js zdejmuje wtedy też aplet Pracy, którego pakietu już nie ma).
REPLACES = {"org.tide.work": ["org.tide.board", "org.tide.agents"]}
# Kafelki z lewej strony docka (przed aplikacjami): system, zaraz po nim Docker (mock „Kafelki Tablica
# i Docker”). Prośba użytkownika: „ja go chciałem po lewej mieć od ikonek z paska” — po prawej zostają
# sprawy AI. Worktrees za Dockerem (28.10: „Worktrees bym chciał mieć po lewej stronie, za ciasno się zrobiło
# po prawej”).
DOCK_LEFT = ["org.tide.system", "org.tide.docker", "org.tide.worktrees"]
# Kafelki z prawej strony docka (za aplikacjami), w tej kolejności: sprawy AI — Praca z „＋” (PT-40, zamiast
# Tablicy i Agentów) i kafelek „AI” z limitami i modelem lokalnym (org.tide.limits, PT-41).
DOCK_RIGHT = ["org.tide.work", "org.tide.limits"]
DOCK_WIDGETS = DOCK_LEFT + DOCK_RIGHT
# Typy, które dock.js zdejmuje z docka, gdy nie ma ich na liście (P.retired): wycofane i kafelki, które je
# zastąpiły (gdy nowego nie ma — po wycofaniu — jego aplet bez pakietu też znika).
REMOVABLE = DOCK_RETIRED + list(REPLACES)


def is_installed(widget: str) -> bool:
    return (DATA / "plasma" / "plasmoids" / widget).is_dir()


def installed_widgets(names: list[str] = DOCK_WIDGETS) -> list[str]:
    out = []
    for widget in names:
        if is_installed(widget):
            out.append(widget)
        else:
            out.extend(old for old in REPLACES.get(widget, []) if is_installed(old))
    return out


def params() -> dict:
    palette = json.loads(PALETTE.read_text())
    return {**screens(), "left": installed_widgets(DOCK_LEFT), "widgets": installed_widgets(DOCK_RIGHT),
            "retired": REMOVABLE, "dockHeight": palette["size"]["dock"]}


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
    shown = params()
    print(f"Dock Tide: panel {result['dock']}, z lewej: {', '.join(shown['left']) or 'brak'}, "
          f"z prawej: {', '.join(shown['widgets']) or 'brak'}.")


def uninstall() -> None:
    installed = load(INSTALLED)
    if not installed:
        print("Dock Tide nie był instalowany.")
        return
    evaluate(script("dock-uninstall", {**screens(), **installed, "widgets": DOCK_WIDGETS + DOCK_RETIRED}))
    INSTALLED.unlink(missing_ok=True)
    print("Dock Tide odinstalowany, przywrócono poprzedni układ dolnego panelu.")


def show() -> None:
    print(script("dock", params()))


def install_in_activity() -> None:
    """Instalacja i od razu panele z opisu bieżącej aktywności, pod blokadą nałożeń wyglądu (decyzja 80):
    w aktywności Stream dock pracy nie zostaje w stanie pracy nad konsolą — silnik chowa go z powrotem."""
    from scripts import activity_look
    activity_look.apply_current(["panels"], before=install)


if __name__ == "__main__":
    {"install": install_in_activity, "uninstall": uninstall, "show": show}[sys.argv[1]]()
