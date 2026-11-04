"""Ustawienia pudełka usługi: sekcja `box` w `$XDG_CONFIG_HOME/tide/cardflow.json` (czytana przy starcie usługi
i przez `tide box`). Jeden moduł dla proxy (PT-168) i buildera (PT-167).

    {"box": {"enabled": true, "proxy-port": 18080, "bridge": "incusbr0", "project": "user-1000"}}

Proxy (PT-168):
- `enabled` (`box.enabled`) — domyślnie `false`: bez tego usługa nie startuje proxy pudełka i działa jak przed K-38,
- `proxy-port` (`box.proxy-port`) — port proxy Tide na adresie mostka; ta sama wartość co `BOX_PROXY_PORT`
  w `make box-install` (reguły nftables są statyczne, więc zmiana wymaga ponownej instalacji),
- `bridge` — mostek Incusa, z którego czytamy adres nasłuchu (`incus network get <bridge> ipv4.address`).

Builder (PT-167):
- `project` — projekt Incusa (domyślnie `user-<uid>`, projekt ograniczony z `scripts/box/install.sh`, P-5),
- `base-image` — obraz bazowy Tide (alias obrazu Incusa; powstaje w #16, domyślnie `tide-base`),
- `pool` — pula pamięci dla wolumenu `/home/agent` (domyślnie `tide`, D8),
- `nic` — nazwa karty sieciowej pudełka z profilu `default` (domyślnie `eth0`),
- `ipv6-filtering` — `security.ipv6_filtering` karty (domyślnie `false`: decyzja `filtrowanie-nic-bez-ipv6`
  w `docs/box-decisions.md` — Incus 6.0 nie startuje karty z tym ustawieniem bez `br_netfilter`),
- `agents-version` — wersja agentów w obrazie bazowym (wchodzi do hasha przepisu; ustawia #16),
- `ip-wait-seconds` — ile czekać na adres IP pudełka po starcie (domyślnie 30),
- `agent-uid`, `agent-gid` — właściciel wolumenu `/home/agent` w pudełku (`initial.uid/gid`, domyślnie 1000;
  ostateczną wartość ustala obraz bazowy #16).

Adresu proxy nie ma w ustawieniach: builder bierze adres i port z uruchomionego proxy (`proxy.running()`, nasłuch
na adresie mostka), więc oba moduły mają jedno źródło. Brak uruchomionego proxy = budowa odmawia.

Zła wartość → ostrzeżenie i wartość domyślna (zły `enabled` → wyłączone: granica sieciowa nie włącza się sama).
Opis: docs/tide-daemon.md (Proxy wyjściowe, Budowa pudełka).
"""
import json
import os
import re
from dataclasses import dataclass, field

DEFAULT_PORT = 18080
DEFAULT_BRIDGE = "incusbr0"
DEFAULT_AGENT_ID = 1000
_BRIDGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$")   # nazwa interfejsu Linuksa (≤ 15 znaków)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")   # projekt, obraz, pula, karta, wersja


def _default_project() -> str:
    return f"user-{os.getuid()}"


@dataclass(frozen=True)
class BoxSettings:
    enabled: bool = False
    proxy_port: int = DEFAULT_PORT
    bridge: str = DEFAULT_BRIDGE
    warnings: tuple = ()
    project: str = field(default_factory=_default_project)
    base_image: str = "tide-base"
    pool: str = "tide"
    nic: str = "eth0"
    ipv6_filtering: bool = False
    agents_version: str = ""
    ip_wait_seconds: float = 30.0
    agent_uid: int = DEFAULT_AGENT_ID
    agent_gid: int = DEFAULT_AGENT_ID


_TEXT = (("project", "project", None), ("base-image", "base_image", "tide-base"), ("pool", "pool", "tide"),
         ("nic", "nic", "eth0"), ("agents-version", "agents_version", ""))


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
    extra = {}
    for key, attr, default in _TEXT:
        default = _default_project() if default is None else default
        value = box.get(key, default)
        if not isinstance(value, str) or (value and not _NAME.match(value)) or (not value and default):
            warnings.append(f"box.{key}: niepoprawna wartość — {default or 'pusta'}")
            value = default
        extra[attr] = value
    ipv6 = box.get("ipv6-filtering", False)
    if not isinstance(ipv6, bool):
        warnings.append("box.ipv6-filtering: oczekiwano true/false — false")
        ipv6 = False
    wait = box.get("ip-wait-seconds", 30)
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not 0 <= wait <= 600:
        warnings.append("box.ip-wait-seconds: oczekiwano liczby 0–600 — 30")
        wait = 30
    ids = {}
    for key in ("agent-uid", "agent-gid"):
        value = box.get(key, DEFAULT_AGENT_ID)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            warnings.append(f"box.{key}: oczekiwano liczby 1–65535 — {DEFAULT_AGENT_ID}")
            value = DEFAULT_AGENT_ID
        ids[key.replace("-", "_")] = value
    return BoxSettings(enabled=enabled, proxy_port=port, bridge=bridge, warnings=tuple(warnings),
                       ipv6_filtering=ipv6, ip_wait_seconds=float(wait), **extra, **ids)


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
