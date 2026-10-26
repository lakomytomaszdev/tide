"""Instaluje i odinstalowuje ikony Tide: nakładkę, lokalne kopie .desktop i polecenie tide-icons.

Użycie: python3 icons.py install [--base MOTYW] | uninstall

Instalacja kopiuje narzędzie tide-icons do $XDG_DATA_HOME/plasma-tide (żeby
działało niezależnie od gałęzi repozytorium), buduje nakładkę „Tide” na
motywie bazowym, ustawia ją jako motyw ikon i pozwala aplikacjom Flatpak
czytać ~/.local/share/icons. Poprzedni motyw i stan nadpisań Flatpaka
zapisujemy przy pierwszej instalacji, a odinstalowanie je przywraca.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.appearance import DATA, STATE, read_setting  # noqa: E402
from theme.icons import desktop, tide_icons  # noqa: E402
from theme.icons.audit import OVERLAY  # noqa: E402
from theme.icons.catalog import Xdg  # noqa: E402

PREVIOUS = STATE / "tide" / "icons-previous.json"
TOOL_DIR = DATA / "plasma-tide" / "tide-icons"
LAUNCHER = Path(os.environ.get("HOME") or Path.home()) / ".local" / "bin" / "tide-icons"
# Pliki potrzebne narzędziu po instalacji, względem katalogu głównego repozytorium.
TOOL_FILES = [
    "theme/__init__.py",
    "theme/icons/__init__.py",
    "theme/icons/catalog.py",
    "theme/icons/audit.py",
    "theme/icons/overlay.py",
    "theme/icons/desktop.py",
    "theme/icons/tide_icons.py",
    "theme/icons/fixes.json",
    "theme/icons/glyphs.py",
    "theme/icons/glyphs.json",
    *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "theme/icons/glyphs").glob("*.svg")),
    "theme/palette/mist.json",
    "tools/make_tile.py",
]
CHANGEICONS = ("/usr/lib/x86_64-linux-gnu/libexec/plasma-changeicons", "/usr/libexec/plasma-changeicons")
FLATPAK_ICONS = "xdg-data/icons"


def change_icons(theme: str) -> None:
    tool = shutil.which("plasma-changeicons") or next((p for p in CHANGEICONS if os.access(p, os.X_OK)), None)
    if tool is None:
        raise SystemExit("Nie znaleziono plasma-changeicons.")
    subprocess.run([tool, theme], check=True, capture_output=True)


def flatpak_overrides() -> Path:
    user_dir = os.environ.get("FLATPAK_USER_DIR")
    return (Path(user_dir) if user_dir else DATA / "flatpak") / "overrides" / "global"


def flatpak_has_icons() -> bool:
    path = flatpak_overrides()
    if not path.is_file():
        return False
    for line in path.read_text().splitlines():
        if line.startswith("filesystems="):
            return any(f.split(":")[0] == FLATPAK_ICONS for f in line.split("=", 1)[1].split(";"))
    return False


def flatpak_drop_icons() -> None:
    """Usuwa nasz wpis z globalnych nadpisań Flatpaka.

    `flatpak override --nofilesystem` dopisałby zakaz zamiast cofnąć zgodę, a
    `--reset` skasowałby też cudze nadpisania, więc zmieniamy tylko ten jeden wpis.
    """
    path = flatpak_overrides()
    if not path.is_file():
        return
    lines = []
    for line in path.read_text().splitlines():
        if line.startswith("filesystems="):
            kept = [f for f in line.split("=", 1)[1].split(";") if f and f.split(":")[0] != FLATPAK_ICONS]
            if not kept:
                continue
            line = "filesystems=" + ";".join(kept) + ";"
        lines.append(line)
    if all(not l.strip() or l.strip().startswith("[") for l in lines):
        path.unlink()  # zostały same nagłówki grup — plik był nasz
    else:
        path.write_text("\n".join(lines) + "\n")


def install_tool() -> None:
    if TOOL_DIR.exists():
        shutil.rmtree(TOOL_DIR)
    for name in TOOL_FILES:
        target = TOOL_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHER.write_text(f'#!/bin/sh\nexec /usr/bin/python3 "{TOOL_DIR}/theme/icons/tide_icons.py" "$@"\n')
    LAUNCHER.chmod(0o755)


def install(base: str | None) -> None:
    if not PREVIOUS.is_file():
        theme = read_setting("kdeglobals", "Icons", "Theme")
        PREVIOUS.parent.mkdir(parents=True, exist_ok=True)
        PREVIOUS.write_text(json.dumps({
            "theme": "" if theme == OVERLAY else theme,
            "flatpakIcons": flatpak_has_icons(),
        }, indent=2) + "\n")

    install_tool()
    tide_icons.main(["sync", *(["--base", base] if base else [])])
    change_icons(OVERLAY)
    if shutil.which("flatpak") and not flatpak_has_icons():
        subprocess.run(["flatpak", "override", "--user", f"--filesystem={FLATPAK_ICONS}:ro"],
                       check=True, capture_output=True)
    print("Ikony Tide zainstalowane. Polecenie tide-icons audit sprawdza nowe aplikacje.")


def uninstall() -> None:
    previous = json.loads(PREVIOUS.read_text()) if PREVIOUS.is_file() else {}
    xdg = Xdg.from_env()
    if read_setting("kdeglobals", "Icons", "Theme") == OVERLAY:
        change_icons(previous.get("theme") or "breeze")
    removed = desktop.remove_all(xdg)
    shutil.rmtree(tide_icons.overlay_dir(xdg), ignore_errors=True)
    if not previous.get("flatpakIcons", False):
        flatpak_drop_icons()
    shutil.rmtree(TOOL_DIR, ignore_errors=True)
    LAUNCHER.unlink(missing_ok=True)
    tide_icons.refresh_desktop()
    PREVIOUS.unlink(missing_ok=True)
    print(f"Ikony Tide odinstalowane (usunięte kopie .desktop: {removed}), przywrócono poprzedni motyw ikon.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Instalacja ikon Tide.")
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--base", help="motyw bazowy nakładki (domyślnie McMojave-circle-dark)")
    args = parser.parse_args()
    install(args.base) if args.action == "install" else uninstall()
