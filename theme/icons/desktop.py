"""Lokalne kopie plików .desktop z ikoną z motywu.

Aplikacje, które w Icon= podają ścieżkę do pliku (snapy, Zed, Docker), dostają
kopię swojego .desktop w $XDG_DATA_HOME/applications pod tą samą nazwą. Kopia
różni się od oryginału tylko nazwą ikony, znacznikiem Tide i ewentualnie
StartupWMClass. Gdy oryginał sam leży w $XDG_DATA_HOME/applications (Zed
instaluje się do katalogu domowego), odkładamy go do $XDG_STATE_HOME/tide i
przywracamy przy odinstalowaniu.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from theme.icons.catalog import DesktopFile, Xdg, desktop_files, parse_ini

MARKER_KEY = "X-Tide-Icons"
MARKER_COMMENT = "# Kopia od Tide (tide-icons sync): ikona z motywu. Usuwa ją make uninstall."


def applications_dir(xdg: Xdg) -> Path:
    return xdg.data_home / "applications"


def originals_dir(xdg: Xdg) -> Path:
    return xdg.state_home / "tide" / "icons-desktop-originals"


def is_ours(desktop: DesktopFile) -> bool:
    return desktop.entry.get(MARKER_KEY) == "override"


def relative(desktop_id_path: Path, xdg: Xdg) -> Path:
    return desktop_id_path.relative_to(applications_dir(xdg))


def source_entries(xdg: Xdg) -> dict[str, DesktopFile]:
    """Pliki .desktop tak, jak wyglądałyby bez naszych kopii — od nich liczymy plan."""
    out: dict[str, DesktopFile] = {}
    for desktop_id, files in desktop_files(xdg).items():
        top = files[0]
        if not is_ours(top):
            out[desktop_id] = top
            continue
        saved = originals_dir(xdg) / relative(top.path, xdg)
        if saved.is_file():
            out[desktop_id] = DesktopFile(desktop_id, saved, parse_ini(saved.read_text(errors="replace")))
        elif len(files) > 1:
            out[desktop_id] = files[1]
    return out


def rewrite(text: str, icon: str, wm_class: str = "") -> str:
    """Kopia pliku .desktop z inną ikoną; reszta zostaje bajt w bajt."""
    lines = text.splitlines()
    out = [MARKER_COMMENT]
    group = None
    old_icon = None
    has_wm_class = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if group == "Desktop Entry" and wm_class and not has_wm_class:
                out.append(f"StartupWMClass={wm_class}")
            group = stripped[1:-1]
            out.append(line)
            if group == "Desktop Entry":
                out.append(f"{MARKER_KEY}=override")
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key == "Icon" and (group == "Desktop Entry" or value.strip() == old_icon):
            # Akcje z tą samą ścieżką co aplikacja też dostają ikonę z motywu.
            if group == "Desktop Entry":
                old_icon = value.strip()
            out.append(f"Icon={icon}")
            continue
        if group == "Desktop Entry" and key == "StartupWMClass":
            has_wm_class = True
        if line == MARKER_COMMENT or key == MARKER_KEY:
            continue
        out.append(line)
    if group == "Desktop Entry" and wm_class and not has_wm_class:
        out.append(f"StartupWMClass={wm_class}")
    return "\n".join(out) + "\n"


def apply(xdg: Xdg, sources: dict[str, DesktopFile], renamed: dict[str, str], wm_classes: dict[str, str]) -> list[Path]:
    """Zapisuje kopie dla `renamed` i usuwa nasze kopie, których plan już nie zawiera."""
    apps = applications_dir(xdg)
    written: list[Path] = []
    current = desktop_files(xdg)
    for desktop_id, icon in sorted(renamed.items()):
        source = sources[desktop_id]
        top = current.get(desktop_id, [source])[0]
        if top.path.is_relative_to(apps):
            target = top.path
            if not is_ours(top):
                # Plik użytkownika (np. instalator Zeda) — odkładamy oryginał przed nadpisaniem.
                saved = originals_dir(xdg) / relative(target, xdg)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
        else:
            target = apps / desktop_id
        content = rewrite(source.path.read_text(errors="replace"), icon, wm_classes.get(desktop_id, ""))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_text(errors="replace") != content:
            target.write_text(content)
        written.append(target)

    for desktop_id, files in current.items():
        if desktop_id not in renamed and is_ours(files[0]) and files[0].path.is_relative_to(apps):
            restore(files[0].path, xdg)
    return written


def restore(path: Path, xdg: Xdg) -> None:
    saved = originals_dir(xdg) / relative(path, xdg)
    if saved.is_file():
        shutil.move(saved, path)
    else:
        path.unlink(missing_ok=True)


def remove_all(xdg: Xdg) -> int:
    """Usuwa wszystkie nasze kopie (tylko pliki ze znacznikiem) i przywraca odłożone oryginały."""
    apps = applications_dir(xdg)
    removed = 0
    if apps.is_dir():
        for path in sorted(apps.rglob("*.desktop")):
            entry = parse_ini(path.read_text(errors="replace")).get("Desktop Entry", {})
            if entry.get(MARKER_KEY) == "override":
                restore(path, xdg)
                removed += 1
    shutil.rmtree(originals_dir(xdg), ignore_errors=True)
    return removed
