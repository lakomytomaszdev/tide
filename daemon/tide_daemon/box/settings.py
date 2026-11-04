"""Ustawienia pudełka usługi: sekcja `box` w `$XDG_CONFIG_HOME/tide/cardflow.json` (czytana przy starcie).

    {"box": {"enabled": true, "proxy-port": 18080, "bridge": "incusbr0"}}

- `enabled` (`box.enabled`) — domyślnie `false`: bez tego usługa nie startuje proxy pudełka i działa jak przed K-38,
- `proxy-port` (`box.proxy-port`) — port proxy Tide na adresie mostka; ta sama wartość co `BOX_PROXY_PORT`
  w `make box-install` (reguły nftables są statyczne, więc zmiana wymaga ponownej instalacji),
- `bridge` — mostek Incusa, z którego czytamy adres nasłuchu (`incus network get <bridge> ipv4.address`).

Zła wartość → ostrzeżenie i wartość domyślna (zły `enabled` → wyłączone: granica sieciowa nie włącza się sama).
"""
import json
import os
import re
from dataclasses import dataclass

DEFAULT_PORT = 18080
DEFAULT_BRIDGE = "incusbr0"
_BRIDGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$")   # nazwa interfejsu Linuksa (≤ 15 znaków)


@dataclass(frozen=True)
class BoxSettings:
    enabled: bool = False
    proxy_port: int = DEFAULT_PORT
    bridge: str = DEFAULT_BRIDGE
    warnings: tuple = ()


def from_dict(data) -> BoxSettings:
    box = data.get("box") if isinstance(data, dict) else None
    if box is None:
        return BoxSettings()
    if not isinstance(box, dict):
        return BoxSettings(warnings=("box: oczekiwano obiektu — pudełko wyłączone",))
    warnings = []
    enabled = box.get("enabled", False)
    if not isinstance(enabled, bool):
        warnings.append("box.enabled: oczekiwano true/false — pudełko wyłączone")
        enabled = False
    port = box.get("proxy-port", DEFAULT_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        warnings.append(f"box.proxy-port: oczekiwano portu 1024–65535 — {DEFAULT_PORT}")
        port = DEFAULT_PORT
    bridge = box.get("bridge", DEFAULT_BRIDGE)
    if not isinstance(bridge, str) or not _BRIDGE.match(bridge):
        warnings.append(f"box.bridge: niepoprawna nazwa mostka — {DEFAULT_BRIDGE}")
        bridge = DEFAULT_BRIDGE
    return BoxSettings(enabled, port, bridge, tuple(warnings))


def settings_path(env=None) -> str:
    """Ten sam plik co `cardflow.engine.settings_path` (bez importu silnika karty)."""
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "tide", "cardflow.json")


def load(path: str | None = None) -> BoxSettings:
    path = path or settings_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return BoxSettings()
    except (OSError, ValueError, UnicodeError) as exc:
        return BoxSettings(warnings=(f"{os.path.basename(path)}: nie da się odczytać ({type(exc).__name__}) "
                                     "— pudełko wyłączone",))
    return from_dict(data)
