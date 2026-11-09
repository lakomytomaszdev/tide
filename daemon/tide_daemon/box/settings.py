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
- `agents-version` — nadpisuje wersję agentów obrazu bazowego `box/agents.py` `AGENTS_VERSION` w hashu przepisu
  (puste = `AGENTS_VERSION`; jedno źródło: `builder.agents_version`),
- `ip-wait-seconds` — ile czekać na adres IP pudełka po starcie (domyślnie 30),
- `idle-hours` — ile godzin bez kroków przed auto-stopem (domyślnie 4, > 0),
- `agent-uid`, `agent-gid` — właściciel nowego wolumenu `/home/agent` w pudełku (inicjalizacja po starcie,
  domyślnie 1000;
  ostateczną wartość ustala obraz bazowy #16).

Adresu proxy nie ma w ustawieniach: builder bierze adres i port z uruchomionego proxy (`proxy.running()`, nasłuch
na adresie mostka), więc oba moduły mają jedno źródło. Brak uruchomionego proxy = budowa odmawia.

Odczyt pliku (`from_dict`, `load`): zła wartość → ostrzeżenie i wartość domyślna (zły `enabled` → wyłączone:
granica sieciowa nie włącza się sama). Zapis przez `SetCardFlowSettings` (PT-292) jest ścisły: `problem(patch)`
sprawdza te same reguły z katalogu `KEYS` i odmawia zamiast podstawiać wartość domyślną.
Opis: docs/tide-daemon.md (Proxy wyjściowe, Budowa pudełka), docs/box-cli-v1.md.
"""
import json
import math
import os
import re
from dataclasses import dataclass, field

DEFAULT_PORT = 18080
PORT_MIN, PORT_MAX = 1024, 65535   # wspólny zakres z `scripts/box/install.sh --proxy-port`
DEFAULT_BRIDGE = "incusbr0"
DEFAULT_AGENT_ID = 1000
_BRIDGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$")   # nazwa interfejsu Linuksa (≤ 15 znaków)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")   # projekt, obraz, pula, karta, wersja


def _default_project() -> str:
    return f"user-{os.getuid()}"


def _is_bool(value) -> bool:
    return isinstance(value, bool)


def _is_int(low, high):
    return lambda value: not isinstance(value, bool) and isinstance(value, int) and low <= value <= high


def _is_number(check):
    return lambda value: (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
                          and check(value))


def _is_name(required: bool):
    return lambda value: isinstance(value, str) and (bool(_NAME.match(value)) if value else not required)


@dataclass(frozen=True)
class Key:
    """Klucz sekcji `box`: atrybut `BoxSettings`, reguła poprawności, opis oczekiwanej wartości i opis wartości
    domyślnej w ostrzeżeniu odczytu."""
    attr: str
    valid: object
    expect: str
    fallback: str


# Jawny katalog obsługiwanych kluczy `box` — jedno źródło dla odczytu (`from_dict`) i zapisu (`problem`).
KEYS = {
    "enabled": Key("enabled", _is_bool, "oczekiwano true/false", "pudełko wyłączone"),
    "proxy-port": Key("proxy_port", _is_int(PORT_MIN, PORT_MAX),
                      f"oczekiwano portu {PORT_MIN}–{PORT_MAX}", str(DEFAULT_PORT)),
    "bridge": Key("bridge", lambda v: isinstance(v, str) and bool(_BRIDGE.match(v)), "niepoprawna nazwa mostka",
                  DEFAULT_BRIDGE),
    "project": Key("project", _is_name(True), "niepoprawna wartość", "user-<uid>"),
    "base-image": Key("base_image", _is_name(True), "niepoprawna wartość", "tide-base"),
    "pool": Key("pool", _is_name(True), "niepoprawna wartość", "tide"),
    "nic": Key("nic", _is_name(True), "niepoprawna wartość", "eth0"),
    "agents-version": Key("agents_version", _is_name(False), "niepoprawna wartość", "pusta"),
    "ipv6-filtering": Key("ipv6_filtering", _is_bool, "oczekiwano true/false", "false"),
    "ip-wait-seconds": Key("ip_wait_seconds", _is_number(lambda v: 0 <= v <= 600), "oczekiwano liczby 0–600", "30"),
    "idle-hours": Key("idle_hours", _is_number(lambda v: 0 < v <= 8760), "oczekiwano liczby > 0 i ≤ 8760", "4"),
    "agent-uid": Key("agent_uid", _is_int(1, 65535), "oczekiwano liczby 1–65535", str(DEFAULT_AGENT_ID)),
    "agent-gid": Key("agent_gid", _is_int(1, 65535), "oczekiwano liczby 1–65535", str(DEFAULT_AGENT_ID)),
}
_FLOATS = ("ip-wait-seconds", "idle-hours")


def problem(patch) -> str | None:
    """Ścisła walidacja obiektu `box` z żądania zapisu: obiekt, wyłącznie klucze z `KEYS`, poprawny typ i zakres
    (bool nie jest liczbą). `None` — poprawny; inaczej czytelny opis pierwszego błędu. Nic nie podstawia."""
    if not isinstance(patch, dict):
        return "box: oczekiwano obiektu JSON"
    unknown = sorted(str(k) for k in patch if k not in KEYS)
    if unknown:
        return f"box: nieznany klucz {', '.join(unknown)} (obsługiwane: {', '.join(KEYS)})"
    for key, value in patch.items():
        if not KEYS[key].valid(value):
            return f"box.{key}: {KEYS[key].expect}"
    return None


def view(settings: "BoxSettings") -> dict:
    """Skuteczne wartości w kluczach pliku (`GetCardFlowSettings`), tak jak je widzą proxy, builder i runner."""
    return {key: getattr(settings, spec.attr) for key, spec in KEYS.items()}


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
    idle_hours: float = 4.0


def from_dict(data) -> BoxSettings:
    """Odczyt łagodny: brak klucza albo zła wartość → wartość domyślna `BoxSettings` (zła — z ostrzeżeniem)."""
    box = data.get("box") if isinstance(data, dict) else None
    if box is None:
        return BoxSettings()
    if not isinstance(box, dict):
        return BoxSettings(warnings=("box: oczekiwano obiektu — pudełko wyłączone",))
    warnings, values = [], {}
    for key, spec in KEYS.items():
        if key not in box:
            continue
        value = box[key]
        if not spec.valid(value):
            warnings.append(f"box.{key}: {spec.expect} — {spec.fallback}")
            continue
        values[spec.attr] = float(value) if key in _FLOATS else value
    return BoxSettings(warnings=tuple(warnings), **values)


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
