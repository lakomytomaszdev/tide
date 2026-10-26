"""Pasek boczny Tide na lewym monitorze: instalacja, odinstalowanie i podgląd skryptu.

Użycie: python3 sidebar.py install|uninstall|show

Pionowy panel przy lewej, zewnętrznej krawędzi lewego monitora (HDMI-A-1), chowany
automatycznie (nie zabiera miejsca oknom) i pływający, szeroki na `size.sidebar` z palety.
Od góry: nagłówek org.tide.sidebar ze skrótem, agenci, system, worktrees i Limity AI
w widoku szczegółowym. Widżety Tide trafiają do paska tylko wtedy, gdy są zainstalowane,
więc cel idzie na koniec `make install` (LATE_INSTALL_TARGETS), po widżetach.

Wysuwanie: Plasma sama wysuwa pasek, gdy mysz dotknie lewej krawędzi ekranu. Skrót Meta+S
to globalny skrót nagłówka („Aktywuj widżet”, komponent plasmashell w KGlobalAccel) — nagłówek
po aktywacji podnosi swój status, a chowany panel nie chowa się, dopóki któryś aplet wymaga
uwagi. Szczegóły w widgets/sidebar/contents/ui/main.qml i docs/decisions.md.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kwin.shortcuts import conflicts  # noqa: E402
from scripts.appearance import DATA, PALETTE, STATE, xdg  # noqa: E402
from scripts.panels import evaluate, load, persist_thickness, save, screens, script, settle  # noqa: E402

INSTALLED = STATE / "tide" / "sidebar-installed.json"
SHORTCUTS = xdg("XDG_CONFIG_HOME", ".config") / "kglobalshortcutsrc"
HEADER = "org.tide.sidebar"
# Kolejność od góry, jak na mocku „Sidebar” (lewy monitor).
WIDGETS = ["org.tide.agents", "org.tide.system", "org.tide.worktrees", "org.tide.limits"]
SHORTCUT = "Meta+S"
# Odstęp pływającego panelu od krawędzi ekranu: Plasma 6.8 bierze 8 px, gdy styl nie ma ramki
# „floating” (Panel.qml w powłoce org.kde.plasma.desktop) — styl Tide jej nie ma.
FLOATING_GAP = 8


def installed_widgets() -> list[str]:
    return [w for w in [HEADER, *WIDGETS] if (DATA / "plasma" / "plasmoids" / w).is_dir()]


def action(header_id: int | None) -> str:
    """Nazwa akcji skrótu widżetu w KGlobalAccel (Plasma::Applet::setGlobalShortcut)."""
    return f"plasmashell/activate widget {header_id}" if header_id else ""


def taken_by(installed: dict) -> list[str]:
    """Inne akcje z aktywnym Meta+S — KGlobalAccel nie nada wtedy klawisza nagłówkowi."""
    return conflicts(str(SHORTCUTS), SHORTCUT, action(installed.get("header")))


def params(installed: dict, shortcut: str) -> dict:
    palette = json.loads(PALETTE.read_text())
    return {**screens(), "sidebarPanel": installed.get("sidebar"), "width": palette["size"]["sidebar"],
            "widgets": installed_widgets(), "header": HEADER, "shortcut": shortcut, "floatingGap": FLOATING_GAP}


def unregister(header_id: int | None) -> None:
    """Najlepszy możliwy wysiłek: akcja usuniętego nagłówka nie powinna trzymać klawisza."""
    if header_id:
        subprocess.run(["dbus-send", "--session", "--type=method_call", "--print-reply",
                        "--dest=org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel.unregister",
                        "string:plasmashell", f"string:activate widget {header_id}"],
                       check=False, capture_output=True)


def install() -> None:
    previous = load(INSTALLED)
    if HEADER not in installed_widgets():
        print(f"Uwaga: widżet {HEADER} nie jest zainstalowany — pasek boczny nie będzie miał skrótu.")
    taken = taken_by(previous)
    values = params(previous, "" if taken else SHORTCUT)
    result = evaluate(script("sidebar", values))
    if result.get("sidebar") is None:
        print("Uwaga: nie ma drugiego monitora — pasek boczny Tide nie powstał.")
        return
    settle()
    evaluate(script("order", {**screens(), "panels": [{"id": result["sidebar"], "order": result["order"],
                                                        "height": values["width"]}]}))
    persist_thickness(result["sidebar"], values["width"])
    save(INSTALLED, {
        "sidebar": result["sidebar"],
        "header": result.get("header"),
        "created": previous.get("created", result["created"]),
        "shortcut": result.get("shortcut", ""),
    })
    names = ", ".join(t.rsplit(".", 1)[-1] for t in values["widgets"]) or "brak"
    print(f"Pasek boczny Tide: panel {result['sidebar']} przy lewej krawędzi, widżety: {names}.")
    if taken:
        print(f"Uwaga: {SHORTCUT} ma już przypisane:")
        for item in taken:
            print(f"  - {item}")
        print("KDE nie nada wtedy skrótu paskowi. Zwolnij go w Ustawieniach systemowych → Skróty,")
        print(f"a potem przypisz {SHORTCUT} nagłówkowi paska (prawy przycisk na nagłówku → Konfiguruj →")
        print("Skróty klawiszowe) albo uruchom ponownie make install-sidebar.")
    elif result.get("header") and result.get("shortcut") != SHORTCUT:
        print(f"Uwaga: Plasma nie przypisała skrótu {SHORTCUT} (jest: {result.get('shortcut') or 'brak'}).")
    else:
        print(f"Pasek wysuwa się przy lewej krawędzi ekranu albo skrótem {result.get('shortcut') or SHORTCUT}.")


def uninstall() -> None:
    installed = load(INSTALLED)
    result = evaluate(script("sidebar-uninstall", {**screens(), "header": HEADER}))
    unregister(installed.get("header"))
    INSTALLED.unlink(missing_ok=True)
    if result["removed"]:
        print(f"Pasek boczny Tide odinstalowany (panel {', '.join(map(str, result['removed']))}).")
    else:
        print("Pasek boczny Tide nie był zainstalowany.")


def show() -> None:
    installed = load(INSTALLED)
    print(script("sidebar", params(installed, "" if taken_by(installed) else SHORTCUT)))


if __name__ == "__main__":
    {"install": install, "uninstall": uninstall, "show": show}[sys.argv[1]]()
