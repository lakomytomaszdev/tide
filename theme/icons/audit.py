"""Audyt ikon widocznych aplikacji i plan nakładki Tide.

Audyt patrzy na aktywny łańcuch motywów (nakładka Tide → motyw bazowy → jego
Inherits → hicolor) i dla każdej widocznej aplikacji mówi, skąd przychodzi jej
ikona. Plan mówi, co nakładka musi zawierać, żeby każda aplikacja miała ikonę
z motywu albo kafelek: stonowane glify aplikacji z docka (`glyphs.json`),
aliasy, kafelki z oryginalnych ikon i glify dla aplikacji bez ikony.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from theme.icons.catalog import (HICOLOR, DesktopFile, IconTheme, Xdg, find_pixmap, icon_name,
                                 theme_chain)

OVERLAY = "Tide"
MANIFEST = "tide-icons.json"
FIXES = Path(__file__).resolve().with_name("fixes.json")

# kategoria → (opis po polsku, czy ikona jest spójna z motywem)
CATEGORIES = {
    "themed": ("z motywu", True),
    "overlay": ("z nakładki Tide", True),
    "wrong": ("błędne mapowanie w motywie", False),
    "fallback": ("z motywu zapasowego, w innym kształcie", False),
    "hicolor": ("własna ikona aplikacji", False),
    "path": ("ścieżka do pliku zamiast nazwy", False),
    "missing": ("brak ikony", False),
}

SLUG = re.compile(r"[^a-z0-9.]+")
MIN_TILE_SOURCE = 48  # mniejsze bitmapy po powiększeniu są rozmyte — wtedy lepszy glif


def family(theme: str) -> str:
    """Rodzina motywu: McMojave-circle-dark i McMojave-circle, breeze-dark i breeze."""
    return re.split(r"[-_ ]", theme, maxsplit=1)[0].lower()


def load_fixes(path: Path = FIXES) -> dict:
    fixes = json.loads(path.read_text())
    for key in ("aliases", "desktopIcons", "startupWMClass", "glyphs"):
        fixes.setdefault(key, {})
    fixes.setdefault("patterns", [])
    fixes.setdefault("genericIconStems", [])
    return fixes


class Resolver:
    """Szukanie ikon w łańcuchu motywów zaczynającym się od podanego motywu."""

    def __init__(self, xdg: Xdg, theme: str, fixes: dict, extra_dirs: tuple[Path, ...] = ()):
        self.xdg = xdg
        self.fixes = fixes
        self.chain: list[IconTheme] = theme_chain(theme, xdg.icon_paths(extra_dirs))
        bases = [t.name for t in self.chain if t.name != OVERLAY]
        # Motyw bazowy to pierwszy prawdziwy motyw za nakładką; jego rodzina wyznacza kształt ikon.
        self.base = bases[0] if bases else theme
        self.family = family(self.base)

    def in_family(self, theme: IconTheme) -> bool:
        return theme.name not in (OVERLAY, HICOLOR) and family(theme.name) == self.family

    def lookup(self, icon: str) -> tuple[IconTheme | None, Path | None]:
        for theme in self.chain:
            path = theme.find(icon)
            if path is not None:
                return theme, path
        return None, None

    def themed(self, icon: str) -> Path | None:
        """Plik ikony z rodziny motywu bazowego, o ile nie jest znanym błędem mapowania."""
        if icon in self.fixes["aliases"]:
            return None
        for theme in self.chain:
            if not self.in_family(theme):
                continue
            path = theme.find(icon)
            if path is not None:
                return path
        return None

    def own(self, icon: str) -> Path | None:
        """Oryginalna ikona aplikacji spoza rodziny motywu: hicolor, pixmaps, a na końcu motyw zapasowy."""
        hicolor = next((t for t in self.chain if t.name == HICOLOR), None)
        path = (hicolor.find(icon) if hicolor else None) or find_pixmap(icon, self.xdg)
        if path is None:
            path = next((p for t in self.chain if t.name not in (OVERLAY, HICOLOR) and not self.in_family(t)
                         for p in [t.find(icon)] if p), None)
        return path

    def manifest(self) -> dict:
        overlay = next((t for t in self.chain if t.name == OVERLAY), None)
        if overlay is None:
            return {}
        for root in overlay.roots:
            if (root / MANIFEST).is_file():
                return json.loads((root / MANIFEST).read_text())
        return {}


# ---------------------------------------------------------------- audyt

@dataclass
class Finding:
    desktop_id: str
    name: str
    icon: str
    category: str
    theme: str = ""
    detail: str = ""

    @property
    def covered(self) -> bool:
        return CATEGORIES[self.category][1]


def classify(app: DesktopFile, resolver: Resolver, manifest: dict) -> Finding:
    finding = Finding(app.id, app.name, app.icon, "missing")
    raw = app.icon
    if not raw:
        return finding
    if raw.startswith("/"):
        finding.category = "path" if Path(raw).is_file() else "missing"
        return finding
    icon = icon_name(raw)
    theme, path = resolver.lookup(icon)
    if theme is None:
        if resolver.own(icon):
            finding.category, finding.theme = "hicolor", "pixmaps"
        return finding
    finding.theme = theme.name
    if theme.name == OVERLAY:
        finding.category = "overlay"
        entry = manifest.get("icons", {}).get(icon, {})
        finding.detail = entry.get("kind", "") + (f" → {entry['target']}" if entry.get("target") else "")
    elif theme.name == HICOLOR:
        finding.category = "hicolor"
    elif icon in resolver.fixes["aliases"]:
        finding.category = "wrong"
        finding.detail = f"powinno być {resolver.fixes['aliases'][icon]}"
    elif not resolver.in_family(theme):
        finding.category = "fallback"
    else:
        finding.category = "themed"
    return finding


def audit(apps: list[DesktopFile], resolver: Resolver) -> list[Finding]:
    manifest = resolver.manifest()
    return [classify(app, resolver, manifest) for app in apps]


# ---------------------------------------------------------------- przypięte w docku

APPLETSRC = "plasma-org.kde.plasma.desktop-appletsrc"
# Skróty „preferowanej aplikacji” w menedżerze zadań → plik .desktop, który Plasma za nimi podstawia.
PREFERRED = {"preferred://filemanager": "org.kde.dolphin.desktop"}


def pinned_launchers(xdg: Xdg) -> list[str]:
    """Identyfikatory .desktop przypięte w menedżerach zadań (dock, paski). Tylko odczyt appletsrc."""
    path = xdg.config_home / APPLETSRC
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[str] = []
    for line in text.splitlines():
        key, _, value = line.strip().partition("=")
        if key != "launchers":
            continue
        for launcher in filter(None, (v.strip() for v in value.split(","))):
            if launcher.startswith("applications:"):
                desktop_id = launcher.removeprefix("applications:")
            elif launcher.startswith("file://") and launcher.endswith(".desktop"):
                desktop_id = Path(launcher.removeprefix("file://")).name
            else:
                desktop_id = PREFERRED.get(launcher, launcher)
            if desktop_id not in out:
                out.append(desktop_id)
    return out


def muted(finding: Finding) -> bool:
    """Czy aplikacja ma stonowany glif Tide (a nie tylko ikonę z motywu albo kafelek)."""
    return finding.category == "overlay" and finding.detail.startswith("muted")


def pinned_without_glyph(findings: list[Finding], pinned: list[str]) -> list[str]:
    by_id = {f.desktop_id: f for f in findings}
    return [d for d in pinned if d not in by_id or not muted(by_id[d])]


def report(findings: list[Finding], theme: str, pinned: list[str] | None = None) -> str:
    lines = [f"Motyw ikon: {theme}", f"Widoczne aplikacje: {len(findings)}", ""]
    for category, (label, _) in CATEGORIES.items():
        group = [f for f in findings if f.category == category]
        if not group:
            continue
        lines.append(f"{label}: {len(group)}")
        if category == "themed":
            continue  # tych jest najwięcej i są w porządku
        for f in group:
            extra = f" ({f.detail})" if f.detail else ""
            lines.append(f"  {f.name} [{f.desktop_id}] Icon={f.icon}{extra}")
        lines.append("")
    plain = [f for f in findings if not muted(f)]
    lines.append(f"Ze stonowanym glifem Tide: {len(findings) - len(plain)}, bez glifu: {len(plain)}")
    for f in plain:
        lines.append(f"  {f.name} [{f.desktop_id}] Icon={f.icon}")
    lines.append("")
    if pinned:
        missing = pinned_without_glyph(findings, pinned)
        lines.append(f"Przypięte w menedżerach zadań: {len(pinned)}, bez glifu: {len(missing)}")
        lines += [f"  {d}" for d in missing]
        lines.append("")
    uncovered = [f for f in findings if not f.covered]
    lines.append("Wszystkie aplikacje mają ikonę z motywu albo kafelek." if not uncovered else
                 f"Bez spójnej ikony: {len(uncovered)}. Uruchom `tide-icons sync`.")
    return "\n".join(lines).rstrip() + "\n"


def to_json(findings: list[Finding], theme: str, pinned: list[str] | None = None) -> str:
    counts = {c: sum(f.category == c for f in findings) for c in CATEGORIES}
    pinned = pinned or []
    return json.dumps({
        "theme": theme,
        "total": len(findings),
        "counts": counts,
        "uncovered": sum(not f.covered for f in findings),
        "muted": sum(muted(f) for f in findings),
        "pinned": pinned,
        "pinnedWithoutGlyph": pinned_without_glyph(findings, pinned),
        "apps": [{**asdict(f), "covered": f.covered, "muted": muted(f)} for f in findings],
    }, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------- plan nakładki

@dataclass
class IconPlan:
    name: str
    kind: str  # alias | tile | glyph | muted
    source: Path | None = None
    target: str = ""
    glyph: str = ""
    color: str = ""
    label: str = ""
    art: Any = None  # glyphs.Glyph dla stonowanych glifów

    def manifest(self) -> dict:
        entry = {"kind": self.kind}
        if self.target:
            entry["target"] = self.target
        if self.kind == "muted":
            entry["glyph"] = self.glyph
        return entry


@dataclass
class Plan:
    base: str
    icons: dict[str, IconPlan] = field(default_factory=dict)
    # identyfikator .desktop → nazwa ikony dla aplikacji, które podają ścieżkę do pliku
    renamed: dict[str, str] = field(default_factory=dict)


def alias_target(icon: str, base: Resolver) -> tuple[str, Path] | None:
    fixes = base.fixes
    candidates = []
    if icon in fixes["aliases"]:
        candidates.append(fixes["aliases"][icon])
    for pattern in fixes["patterns"]:
        match = re.match(pattern["match"], icon)
        if match:
            product = match.group("product").lower()
            candidates += [c.format(product=product) for c in pattern["candidates"]]
    for candidate in candidates:
        path = base.themed(candidate)
        if path is not None:
            return candidate, path
    return None


def glyph_plan(name: str, label: str, fixes: dict) -> IconPlan:
    spec = fixes["glyphs"].get(name, {})
    return IconPlan(name, "glyph", glyph=spec.get("glyph", "letter"), color=spec.get("color", ""), label=label)


def muted_key(icon: str, base: Resolver, glyphs: dict) -> str | None:
    """Nazwa z glyphs.json, pod którą jest glif dla `icon` — wprost albo przez wzorce z fixes.json."""
    if icon in glyphs:
        return icon
    for pattern in base.fixes["patterns"]:
        match = re.match(pattern["match"], icon)
        if match:
            product = match.group("product").lower()
            for candidate in pattern["candidates"]:
                if candidate.format(product=product) in glyphs:
                    return candidate.format(product=product)
    return None


def muted_plan(icon: str, label: str, base: Resolver, glyphs: dict) -> IconPlan | None:
    key = muted_key(icon, base, glyphs)
    if key is None:
        return None
    art = glyphs[key]
    return IconPlan(icon, "muted", source=art.path, target="" if key == icon else key, glyph=art.origin,
                    color=art.color, label=art.app, art=art)


def plan_icon(icon: str, label: str, base: Resolver, glyphs: dict | None = None) -> IconPlan | None:
    """Co nakładka musi mieć pod nazwą `icon`; None, gdy wystarczy motyw bazowy."""
    muted = muted_plan(icon, label, base, glyphs or {})
    if muted is not None:
        return muted  # stonowany glif zasłania ikonę motywu, także tę poprawną
    if base.themed(icon) is not None:
        return None
    alias = alias_target(icon, base)
    if alias is not None:
        return IconPlan(icon, "alias", source=alias[1], target=alias[0], label=label)
    own = base.own(icon)
    if own is not None and tileable(own):
        return IconPlan(icon, "tile", source=own, label=label)
    return glyph_plan(icon, label, base.fixes)


def tileable(path: Path) -> bool:
    if path.suffix == ".svg":
        return True
    try:
        from PIL import Image
        with Image.open(path) as image:
            return min(image.size) >= MIN_TILE_SOURCE
    except OSError:
        return False


def name_candidates(app: DesktopFile, fixes: dict) -> list[str]:
    """Kandydaci na nazwę ikony dla aplikacji, która podaje ścieżkę do pliku."""
    entry = app.entry
    stem = Path(app.icon).name
    for ext in (".png", ".svg", ".xpm"):
        stem = stem.removesuffix(ext)
    raw = [
        fixes["desktopIcons"].get(app.id, ""),
        entry.get("X-SnapInstanceName", ""),
        entry.get("Name", ""),
        entry.get("StartupWMClass", ""),
        "" if stem.lower() in fixes["genericIconStems"] else stem,
    ]
    out = []
    for value in raw:
        slug = SLUG.sub("-", value.lower()).strip("-.")
        if slug and slug not in out:
            out.append(slug)
    return out


def path_icon_name(app: DesktopFile, base: Resolver, glyphs: dict | None = None) -> tuple[str, IconPlan | None]:
    """Nazwa z motywu dla aplikacji ze ścieżką; gdy motyw jej nie zna — kafelek z oryginału."""
    glyphs = glyphs or {}
    entry = app.entry
    name = entry.get("X-SnapInstanceName") or app.id.removesuffix(".desktop")
    for candidate in [name, *name_candidates(app, base.fixes)]:
        muted = muted_plan(candidate, app.name, base, glyphs)
        if muted is not None:
            return candidate, muted
    for candidate in name_candidates(app, base.fixes):
        if base.themed(candidate) is not None:
            return candidate, None
        alias = alias_target(candidate, base)
        if alias is not None:
            return alias[0], None
    source = Path(app.icon)
    if source.is_file() and tileable(source):
        return name, IconPlan(name, "tile", source=source, label=app.name)
    return name, glyph_plan(name, app.name, base.fixes)


def plan(apps: list[DesktopFile], base: Resolver, base_name: str, glyphs: dict | None = None) -> Plan:
    """Plan nakładki; `glyphs` to nazwa ikony → glyphs.Glyph z rozwiązanym źródłem."""
    glyphs = glyphs or {}
    result = Plan(base_name)
    # Stonowane glify są w nakładce pod każdą swoją nazwą, także gdy żadna aplikacja jej dziś nie używa.
    for name in glyphs:
        result.icons[name] = muted_plan(name, name, base, glyphs)
    # Znane błędy mapowania poprawiamy zawsze, nawet gdy żadna aplikacja ich dziś nie używa.
    for icon in base.fixes["aliases"]:
        item = plan_icon(icon, icon, base)
        if item is not None and item.kind == "alias" and icon not in result.icons:
            result.icons[icon] = item
    for app in apps:
        if not app.icon:
            # Bez Icon= — lokalna kopia .desktop dostanie nazwę, a nakładka glif.
            name = app.id.removesuffix(".desktop")
            result.renamed[app.id] = name
            item = muted_plan(name, app.name, base, glyphs) or glyph_plan(name, app.name, base.fixes)
        elif app.icon.startswith("/"):
            name, item = path_icon_name(app, base, glyphs)
            result.renamed[app.id] = name
        else:
            item = plan_icon(icon_name(app.icon), app.name, base, glyphs)
        if item is not None:
            result.icons.setdefault(item.name, item)
    return result
