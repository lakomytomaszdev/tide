"""Ustawienia pudełka usługi: `$XDG_CONFIG_HOME/tide/box.json` (opcjonalny; brak pliku = wartości domyślne).

Klucze (wszystkie opcjonalne):
- `project` — projekt Incusa (domyślnie `user-<uid>`, projekt ograniczony z `scripts/box/install.sh`, P-5),
- `base-image` — obraz bazowy Tide (alias obrazu Incusa; powstaje w #16, domyślnie `tide-base`),
- `pool` — pula pamięci dla wolumenu `/home/agent` (domyślnie `tide`, D8),
- `nic` — nazwa karty sieciowej pudełka z profilu `default` (domyślnie `eth0`),
- `ipv6-filtering` — `security.ipv6_filtering` karty (domyślnie `false`: decyzja `filtrowanie-nic-bez-ipv6`
  w `docs/box-decisions.md` — Incus 6.0 nie startuje karty z tym ustawieniem bez `br_netfilter`),
- `proxy-address` — adres mostka, na którym słucha proxy Tide (#5); **bez domyślnej wartości** — brak = budowa
  odmawia przed pierwszym `incus exec` (brak proxy to błąd, nie „bez proxy”),
- `proxy-port` — port proxy Tide (domyślnie 18080, ta sama wartość co `BOX_PROXY_PORT` w `make box-install`),
- `agents-version` — wersja agentów w obrazie bazowym (wchodzi do hasha przepisu; ustawia #16),
- `ip-wait-seconds` — ile czekać na adres IP pudełka po starcie (domyślnie 30).

Zmienna `TIDE_BOX_SETTINGS` wskazuje inny plik (testy). Opis: docs/tide-daemon.md (Budowa pudełka).
"""
import json
import os
from dataclasses import dataclass


class SettingsError(ValueError):
    pass


DEFAULT_PORT = 18080


@dataclass(frozen=True)
class Settings:
    project: str
    base_image: str = "tide-base"
    pool: str = "tide"
    nic: str = "eth0"
    ipv6_filtering: bool = False
    proxy_address: str = ""
    proxy_port: int = DEFAULT_PORT
    agents_version: str = ""
    ip_wait_seconds: float = 30.0

    def proxy_url(self) -> str:
        """`http://<adres mostka>:<port>`; brak adresu → `SettingsError` (budowa bez proxy jest błędem)."""
        if not self.proxy_address:
            raise SettingsError("brak ustawień proxy Tide (proxy-address w box.json) — budowa bez proxy jest "
                                "niedozwolona")
        return f"http://{self.proxy_address}:{self.proxy_port}"


def path(env=None) -> str:
    env = os.environ if env is None else env
    if env.get("TIDE_BOX_SETTINGS"):
        return env["TIDE_BOX_SETTINGS"]
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "tide", "box.json")


def load(env=None) -> Settings:
    file = path(env)
    try:
        with open(file, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as exc:
        raise SettingsError(f"{file}: {exc}") from None
    if not isinstance(data, dict):
        raise SettingsError(f"{file}: oczekiwano obiektu JSON")
    port = data.get("proxy-port", DEFAULT_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise SettingsError(f"{file}: proxy-port musi być liczbą 1–65535")
    ipv6 = data.get("ipv6-filtering", False)
    if not isinstance(ipv6, bool):
        raise SettingsError(f"{file}: ipv6-filtering musi być true/false")
    text = {}
    for key, default in (("project", f"user-{os.getuid()}"), ("base-image", "tide-base"), ("pool", "tide"),
                         ("nic", "eth0"), ("proxy-address", ""), ("agents-version", "")):
        value = data.get(key, default)
        if not isinstance(value, str) or any(c.isspace() for c in value):
            raise SettingsError(f"{file}: {key} musi być napisem bez spacji")
        text[key] = value
    wait = data.get("ip-wait-seconds", 30)
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or wait < 0:
        raise SettingsError(f"{file}: ip-wait-seconds musi być liczbą ≥ 0")
    return Settings(project=text["project"], base_image=text["base-image"], pool=text["pool"], nic=text["nic"],
                    ipv6_filtering=ipv6, proxy_address=text["proxy-address"], proxy_port=port,
                    agents_version=text["agents-version"], ip_wait_seconds=float(wait))
