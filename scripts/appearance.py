"""Instaluje i odinstalowuje podstawę wyglądu Tide: schemat kolorów i czcionki Geist.

Nakładany jest schemat z opisu wyglądu bieżącej aktywności (scripts/activity_look.py), więc
instalacja w aktywności Stream nie przestawia jej na Mist. Styl Plasmy nakłada plasma_style.py.

Użycie: python3 appearance.py install|uninstall

Schematy pozostałych wariantów palety (np. TideStreamer dla aktywności Stream) trafiają
obok TideMist, ale nakładany jest wyłącznie TideMist — wybór wariantu należy do przełącznika
wyglądu aktywności.

Poprzednie ustawienia zapisujemy przy pierwszej instalacji, więc odinstalowanie
przywraca stan sprzed Tide, nawet po kilku kolejnych instalacjach.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from theme.colors.generate import render, scheme  # noqa: E402
from theme import palette as palettes  # noqa: E402

PALETTE = ROOT / "theme" / "palette" / "mist.json"
GEIST_VERSION = "1.7.2"
GEIST_URL = f"https://github.com/vercel/geist-font/releases/download/v{GEIST_VERSION}/geist-font-v{GEIST_VERSION}.zip"
GEIST_SHA256 = "7fc800d2ac6b92844895196e5041aca55d814c15db70c44f79b3b83ab82b04e2"

# (plik, grupa, klucz) — wszystko, co zmieniamy i co odinstalowanie musi przywrócić.
SETTINGS = [
    ("kdeglobals", "General", "ColorScheme"),
    ("plasmarc", "Theme", "name"),
    ("kdeglobals", "General", "font"),
    ("kdeglobals", "General", "menuFont"),
    ("kdeglobals", "General", "toolBarFont"),
    ("kdeglobals", "General", "smallestReadableFont"),
    ("kdeglobals", "WM", "activeFont"),
]


def xdg(name: str, default: str) -> Path:
    return Path(os.environ.get(name) or Path.home() / default)


DATA = xdg("XDG_DATA_HOME", ".local/share")
STATE = xdg("XDG_STATE_HOME", ".local/state")
CACHE = xdg("XDG_CACHE_HOME", ".cache")
SCHEME_FILE = DATA / "color-schemes" / "TideMist.colors"
SCHEME_DIR = SCHEME_FILE.parent
FONT_DIR = DATA / "fonts" / "tide"
PREVIOUS = STATE / "tide" / "appearance-previous.json"


# Błędy wariantu palety (zła paleta, brak obrazów, plik nie do odczytu): instalacja Mist idzie dalej.
VARIANT_ERRORS = (ValueError, KeyError, OSError)
SCHEME_HEADER = render({}).splitlines()[0]  # pierwsza linia każdego schematu, który zapisał Tide


def skipped(name: str, error: Exception) -> None:
    print(f"Uwaga: wariant palety {name!r} pominięty ({type(error).__name__}: {error}); "
          "wygląd Mist zainstalowany bez zmian.", file=sys.stderr)


def variant_palettes() -> dict[str, dict]:
    """Palety wariantów poza Mist (nazwa pliku → paleta), np. {"streamer": {...}}; zepsute pomija."""
    result = {}
    for name in palettes.names():
        if name == palettes.BASE:
            continue
        try:
            result[name] = palettes.load(name)
        except VARIANT_ERRORS as error:
            skipped(name, error)
    return result


def for_each_variant(write) -> None:
    """write(nazwa, paleta) dla każdego wariantu; błąd jednego nie przerywa pozostałych ani instalacji."""
    for name, variant in variant_palettes().items():
        try:
            write(name, variant)
        except VARIANT_ERRORS as error:
            skipped(name, error)


def tide_scheme_files() -> list[Path]:
    """Schematy Tide*.colors zapisane przez Tide (z nagłówkiem generatora) — także po usuniętych wariantach."""
    found = []
    for path in sorted(SCHEME_DIR.glob("Tide*.colors")):
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.readline().rstrip("\n") == SCHEME_HEADER:
                    found.append(path)
        except OSError:
            pass
    return found


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def read_setting(file: str, group: str, key: str) -> str:
    return run("kreadconfig6", "--file", file, "--group", group, "--key", key)


def write_setting(file: str, group: str, key: str, value: str) -> None:
    if value:
        run("kwriteconfig6", "--notify", "--file", file, "--group", group, "--key", key, value)
    else:
        run("kwriteconfig6", "--notify", "--file", file, "--group", group, "--key", key, "--delete")


def qfont(family: str, size: int, weight: int = 400) -> str:
    # Format QFont::toString z Qt 6, tak jak zapisują go Ustawienia systemowe.
    return f"{family},{size},-1,5,{weight},0,0,0,0,0,0,0,0,0,0,1"


def notify_appearance_changed() -> None:
    for change in ("0", "1", "2"):  # paleta, czcionki, styl
        subprocess.run(["dbus-send", "--session", "--type=signal", "/KGlobalSettings",
                        "org.kde.KGlobalSettings.notifyChange", f"int32:{change}", "int32:0"], check=False)


def geist_archive() -> Path:
    archive = Path(os.environ.get("TIDE_GEIST_ZIP") or CACHE / "tide" / f"geist-font-v{GEIST_VERSION}.zip")
    expected = os.environ.get("TIDE_GEIST_SHA256") or GEIST_SHA256
    if not archive.is_file():
        archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(GEIST_URL, timeout=60) as response, open(archive, "wb") as out:
            shutil.copyfileobj(response, out)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected:
        archive.unlink(missing_ok=True)
        raise SystemExit(f"Suma kontrolna Geist się nie zgadza ({digest}), przerwano.")
    return archive


def install_fonts() -> None:
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(geist_archive()) as zf:
        zf.extractall(tmp)
        FONT_DIR.mkdir(parents=True, exist_ok=True)
        for pattern in ("*/Geist/ttf/*.ttf", "*/GeistMono/ttf/*.ttf", "*/OFL.txt"):
            for font in Path(tmp).glob(pattern):
                shutil.copy2(font, FONT_DIR / font.name)
    run("fc-cache", "-f", str(FONT_DIR))


def install() -> None:
    if not PREVIOUS.is_file():
        PREVIOUS.parent.mkdir(parents=True, exist_ok=True)
        PREVIOUS.write_text(json.dumps({"|".join(s): read_setting(*s) for s in SETTINGS}, indent=2) + "\n")

    SCHEME_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEME_FILE.write_text(render(scheme(json.loads(PALETTE.read_text()))))
    install_fonts()

    palette = json.loads(PALETTE.read_text())
    family, size = palette["fonts"]["ui"], palette["fonts"]["uiSizePt"]
    write_setting("kdeglobals", "General", "font", qfont(family, size))
    write_setting("kdeglobals", "General", "menuFont", qfont(family, size))
    write_setting("kdeglobals", "General", "toolBarFont", qfont(family, size))
    write_setting("kdeglobals", "General", "smallestReadableFont", qfont(family, size - 2))
    write_setting("kdeglobals", "WM", "activeFont", qfont(family, size, 500))

    # Schematy pozostałych wariantów (np. TideStreamer) tylko zapisane, przed nałożeniem — w Stream
    # nałożenie potrzebuje już pliku jej schematu. Zepsuty wariant jest pomijany i nie blokuje Mist.
    for_each_variant(lambda name, p: (SCHEME_DIR / f"Tide{p['name']}.colors").write_text(render(scheme(p))))

    # Nałożenie: schemat z opisu wyglądu bieżącej aktywności (w Stream nie nakładamy Mist).
    # Stylu Plasmy ta instalacja nie zmienia — ustawia go install-plasma-style według tego samego opisu.
    from scripts import activity_look
    report = activity_look.apply_current(["colorScheme"])
    notify_appearance_changed()
    print(f"Czcionki Geist i schemat kolorów TideMist zainstalowane (nałożony: {report.steps[0].target}).")


def uninstall() -> None:
    previous = json.loads(PREVIOUS.read_text()) if PREVIOUS.is_file() else {}
    for setting in SETTINGS:
        if setting[2] in ("ColorScheme", "name"):
            continue  # te przywracają narzędzia Plasmy niżej
        write_setting(*setting, previous.get("|".join(setting), ""))

    run("plasma-apply-colorscheme", previous.get("kdeglobals|General|ColorScheme") or "BreezeDark")
    run("plasma-apply-desktoptheme", previous.get("plasmarc|Theme|name") or "default")

    SCHEME_FILE.unlink(missing_ok=True)
    for path in tide_scheme_files():
        path.unlink(missing_ok=True)
    if FONT_DIR.is_dir():
        shutil.rmtree(FONT_DIR)
        run("fc-cache", "-f")
    PREVIOUS.unlink(missing_ok=True)
    notify_appearance_changed()
    print("Kolory i czcionki Tide odinstalowane, przywrócono poprzednie ustawienia.")


if __name__ == "__main__":
    {"install": install, "uninstall": uninstall}[sys.argv[1]]()
