"""Reguły lustra: które elementy zasobnika podmieniamy i jaką ikoną (czyste funkcje, bez D-Bus)."""
from __future__ import annotations

import colorsys
import json
from dataclasses import dataclass, field
from pathlib import Path

# Plasma 6.8 (systemtraymodel.cpp, extractItemId): Electron/Chromium mają wspólne Id, więc klucz
# ustawień to Id@nazwa procesu; nazwa to katalog pliku .asar z wiersza poleceń albo /proc/PID/comm.
CHROMIUM_PREFIX = "chrome_status_icon_"
DROPBOX_PREFIX = "dropbox-client-"
# Ile nieprzezroczystych pikseli w danym kolorze uznajemy za stan (kropka, głośnik w rozmowie).
STATE_SHARE = 0.015
HUES = {"red": ((0, 20), (340, 360)), "green": ((85, 165),)}


@dataclass
class Rule:
    app: str
    id: str
    icon: str
    tooltip: str = ""
    states: list[dict] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.icon.removeprefix("tide-tray-")


def load(path: Path) -> list[Rule]:
    data = json.loads(Path(path).read_text())
    return [Rule(app=r["app"], id=r["id"], icon=r["icon"], tooltip=r.get("tooltip", ""),
                 states=r.get("states", [])) for r in data.get("mirror", [])]


def process_name(cmdline: bytes, comm: str) -> str:
    """Nazwa procesu tak jak liczy ją Plasma: katalog pliku .asar albo comm."""
    for arg in cmdline.split(b"\0"):
        if arg.endswith(b".asar"):
            return Path(arg.decode(errors="replace")).parent.name
    return comm.strip()


def plasma_key(item_id: str, process: str) -> str:
    """Klucz elementu w disabledStatusNotifiers/hiddenItems/shownItems."""
    if item_id.startswith(DROPBOX_PREFIX):
        return "dropbox-client-PID"
    if item_id.startswith(CHROMIUM_PREFIX) and process:
        return f"{item_id}@{process}"
    return item_id


def match(rules: list[Rule], item_id: str, title: str) -> Rule | None:
    for rule in rules:
        if not item_id.startswith(rule.id):
            continue
        if rule.tooltip and rule.tooltip != title:
            continue
        return rule
    return None


def colors(pixmaps: list[tuple[int, int, bytes]]) -> set[str]:
    """Kolory stanu widoczne na największym obrazku (ARGB32, kolejność sieciowa)."""
    if not pixmaps:
        return set()
    width, height, data = max(pixmaps, key=lambda p: p[0] * p[1])
    if len(data) < width * height * 4:
        return set()
    counts = {name: 0 for name in HUES}
    opaque = 0
    for i in range(0, width * height * 4, 4):
        a, r, g, b = data[i], data[i + 1], data[i + 2], data[i + 3]
        if a < 128:
            continue
        opaque += 1
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.45 or v < 0.45:
            continue
        hue = h * 360
        for name, ranges in HUES.items():
            if any(lo <= hue < hi for lo, hi in ranges):
                counts[name] += 1
    if not opaque:
        return set()
    return {name for name, n in counts.items() if n / opaque >= STATE_SHARE}


def icon_for(rule: Rule, pixmaps: list[tuple[int, int, bytes]]) -> str:
    seen = colors(pixmaps)
    for state in rule.states:
        if state["when"] in seen:
            return state["icon"]
    return rule.icon


def mirror_id(rule: Rule | None, key: str) -> str:
    """Id lustra: stały dla aplikacji z regułą, żeby dało się je ustawić w zasobniku."""
    if rule is not None:
        return f"tide-tray-{rule.slug}"
    safe = "".join(c if c.isalnum() else "-" for c in key).strip("-")
    return f"tide-tray-{safe}"
