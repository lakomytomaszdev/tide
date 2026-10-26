"""tide-icons — audyt i utrzymanie ikon Tide.

Użycie:
  tide-icons audit [--json] [--theme NAZWA] [--icons-dir KATALOG]...
      Skąd przychodzą ikony widocznych aplikacji. Kod wyjścia 1, gdy któraś
      nie ma ikony z motywu ani kafelka.
  tide-icons sync [--base NAZWA]
      Odświeża nakładkę w $XDG_DATA_HOME/icons/Tide i lokalne kopie .desktop
      dla aplikacji ze ścieżką do ikony.
  tide-icons build [--base NAZWA] [--out KATALOG]
      Buduje samą nakładkę do podglądu, bez zmian w systemie.
  tide-icons themes
      Porównuje zainstalowane motywy ikon jako kandydatów na motyw bazowy.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from theme.icons import desktop  # noqa: E402
from theme.icons.audit import (MANIFEST, OVERLAY, Resolver, audit, load_fixes, plan,  # noqa: E402
                               report, to_json)
from theme.icons.catalog import HICOLOR, Xdg, visible_apps  # noqa: E402
from theme.icons.overlay import build  # noqa: E402

PALETTE = ROOT / "theme" / "palette" / "mist.json"
DEFAULT_BASE = "McMojave-circle-dark"


def overlay_dir(xdg: Xdg) -> Path:
    return xdg.data_home / "icons" / OVERLAY


def active_theme() -> str:
    try:
        value = subprocess.run(["kreadconfig6", "--file", "kdeglobals", "--group", "Icons", "--key", "Theme"],
                               check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        value = ""
    return value or "breeze"


def base_theme(xdg: Xdg, requested: str | None) -> str:
    """Motyw bazowy: z opcji, z TIDE_ICON_BASE, z zainstalowanej nakładki albo domyślny."""
    if requested:
        return requested
    if os.environ.get("TIDE_ICON_BASE"):
        return os.environ["TIDE_ICON_BASE"]
    manifest = overlay_dir(xdg) / MANIFEST
    if manifest.is_file():
        return json.loads(manifest.read_text()).get("base") or DEFAULT_BASE
    return DEFAULT_BASE


def base_resolver(xdg: Xdg, base: str) -> Resolver:
    resolver = Resolver(xdg, base, load_fixes())
    if not resolver.chain or resolver.chain[0].name != base:
        raise SystemExit(f"Nie znaleziono motywu ikon {base} — zainstaluj go albo podaj inny przez --base.")
    return resolver


def build_overlay(xdg: Xdg, base: str, out: Path, sources: list | None = None):
    resolver = base_resolver(xdg, base)
    apps = sources if sources is not None else visible_apps(xdg)
    result = plan(apps, resolver, base)
    manifest = build(result, resolver, json.loads(PALETTE.read_text()), out)
    return result, manifest


def summary(manifest: dict) -> str:
    kinds = [entry["kind"] for entry in manifest["icons"].values()]
    return (f"aliasy: {kinds.count('alias')}, kafelki: {kinds.count('tile')}, glify: {kinds.count('glyph')}, "
            f"foldery: {manifest['folders']}")


def refresh_desktop() -> None:
    """Odświeża menu i, gdy Tide jest aktywny, ikony w działających programach."""
    if shutil.which("kbuildsycoca6"):
        subprocess.run(["kbuildsycoca6"], check=False, capture_output=True)
    if active_theme() == OVERLAY and shutil.which("dbus-send"):
        for group in range(6):  # grupy KIconLoader: Desktop, Toolbar, MainToolbar, Small, Panel, Dialog
            subprocess.run(["dbus-send", "--session", "--type=signal", "/KIconLoader",
                            "org.kde.KIconLoader.iconChanged", f"int32:{group}"], check=False)


def cmd_audit(args) -> int:
    xdg = Xdg.from_env()
    theme = args.theme or active_theme()
    resolver = Resolver(xdg, theme, load_fixes(), tuple(Path(d) for d in args.icons_dir))
    findings = audit(visible_apps(xdg), resolver)
    sys.stdout.write(to_json(findings, theme) if args.json else report(findings, theme))
    return 0 if all(f.covered for f in findings) else 1


def cmd_sync(args) -> int:
    xdg = Xdg.from_env()
    base = base_theme(xdg, args.base)
    sources = desktop.source_entries(xdg)
    apps = sorted((d for d in sources.values() if d.visible(xdg)), key=lambda d: d.name.lower())
    result, manifest = build_overlay(xdg, base, overlay_dir(xdg), apps)
    written = desktop.apply(xdg, sources, result.renamed, load_fixes()["startupWMClass"])
    refresh_desktop()
    print(f"Nakładka {OVERLAY} na {base}: {summary(manifest)}.")
    print(f"Lokalne kopie .desktop z ikoną z motywu: {len(written)}.")
    return 0


def cmd_build(args) -> int:
    xdg = Xdg.from_env()
    base = base_theme(xdg, args.base)
    out = Path(args.out) if args.out else ROOT / "build" / "icons" / OVERLAY
    _, manifest = build_overlay(xdg, base, out)
    print(f"Nakładka zbudowana w {out}: {summary(manifest)}.")
    return 0


def cmd_themes(args) -> int:
    """Ile widocznych aplikacji ma ikonę z rodziny każdego zainstalowanego motywu."""
    xdg = Xdg.from_env()
    apps = visible_apps(xdg)
    fixes = load_fixes()
    names = sorted({p.name for base in xdg.icon_paths() if base.is_dir() for p in base.iterdir()
                    if (p / "index.theme").is_file()} - {OVERLAY, HICOLOR})
    rows = []
    for name in names:
        resolver = Resolver(xdg, name, fixes)
        theme = resolver.chain[0] if resolver.chain else None
        if theme is None or theme.name != name or theme.index.get("Hidden", "").lower() == "true":
            continue
        if not any(info.get("Context") == "Applications" for info in theme.subdirs.values()):
            continue  # motywy kursorów i same emblematy
        rows.append((sum(f.category == "themed" for f in audit(apps, resolver)), name))
    print(f"Widoczne aplikacje: {len(apps)}. Ikony z rodziny motywu (reszta to kafelki i aliasy):")
    for themed, name in sorted(rows, key=lambda r: (-r[0], r[1].lower())):
        print(f"  {themed:3d}  {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tide-icons", description="Audyt i utrzymanie ikon Tide.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_audit = sub.add_parser("audit", help="skąd przychodzą ikony widocznych aplikacji")
    p_audit.add_argument("--json", action="store_true", help="wynik w JSON")
    p_audit.add_argument("--theme", help="motyw ikon (domyślnie aktywny w Plasmie)")
    p_audit.add_argument("--icons-dir", action="append", default=[],
                         help="dodatkowy katalog z motywami, przeszukiwany jako pierwszy")
    p_audit.set_defaults(func=cmd_audit)
    p_sync = sub.add_parser("sync", help="odświeża nakładkę i lokalne kopie .desktop")
    p_sync.add_argument("--base", help=f"motyw bazowy (domyślnie {DEFAULT_BASE})")
    p_sync.set_defaults(func=cmd_sync)
    p_build = sub.add_parser("build", help="buduje nakładkę do podglądu")
    p_build.add_argument("--base", help=f"motyw bazowy (domyślnie {DEFAULT_BASE})")
    p_build.add_argument("--out", help="katalog wyjściowy (domyślnie build/icons/Tide)")
    p_build.set_defaults(func=cmd_build)
    p_themes = sub.add_parser("themes", help="porównuje kandydatów na motyw bazowy")
    p_themes.set_defaults(func=cmd_themes)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
