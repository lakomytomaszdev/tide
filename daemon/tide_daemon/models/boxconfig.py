"""`models.json` dla pudełka (PT-168): Ollama hosta widziana przez proxy Tide na adresie mostka.

Pudełko nie sięga `127.0.0.1:11434` hosta (to jego własna pętla zwrotna), a nftables wpuszczają z mostka tylko
port proxy. Dlatego punkt Ollamy hosta (`http://127.0.0.1:11434`, `localhost`, `[::1]`) dostaje adres
`http://<adres mostka>:<port proxy>`; proxy przekazuje go na `127.0.0.1:11434`, gdy profil pudełka ma
`host-ollama: true`. Inne punkty (np. serwer na innym komputerze) i dostawcy z tokenami nie trafiają do pudełka:
pudełko nie ma do nich drogi, a pliki tokenów hosta nie są w nim widoczne. Program agenta i role zostają, poza
rolami przypiętymi do pominiętego punktu — te znikają, więc w pudełku obowiązuje rola domyślna na Ollamie hosta.
"""
from __future__ import annotations

import copy
import urllib.parse

from .config import DEFAULT_URL

HOST_OLLAMA_PORT = 11434
_LOOPBACK = ("127.0.0.1", "localhost", "::1")
_KEEP = ("default", "roles", "agentProgram")


def is_host_ollama(url) -> bool:
    try:
        parts = urllib.parse.urlsplit(str(url).strip())
        port = parts.port
    except ValueError:
        return False
    return parts.scheme == "http" and parts.hostname in _LOOPBACK and (port or 80) == HOST_OLLAMA_PORT


def for_box(data, bridge: str, port: int) -> dict:
    """Kopia ustawień modeli dla pudełka: punkty Ollamy hosta → `http://<mostek>:<port>`, reszta punktów pominięta.

    `data` — obiekt z `models.json` hosta (albo `None`: wtedy punkt domyślny `local`)."""
    source = data if isinstance(data, dict) else {}
    endpoints = source.get("endpoints")
    if not isinstance(endpoints, dict) or not endpoints:
        endpoints = {"local": {"url": DEFAULT_URL, "kind": "ollama"}}
    url = f"http://{bridge}:{int(port)}"
    out = {key: copy.deepcopy(source[key]) for key in _KEEP if key in source}
    out["endpoints"] = {name: {"url": url, "kind": "ollama"}
                        for name, value in endpoints.items()
                        if isinstance(value, dict) and value.get("kind", "ollama") == "ollama"
                        and is_host_ollama(value.get("url"))}
    removed = set(endpoints) - set(out["endpoints"])
    roles = out.get("roles")
    if isinstance(roles, dict):
        # Rola przypięta do pominiętego punktu („mac:qwen3:8b”) w pudełku wzięłaby cały napis za nazwę modelu
        # na punkcie domyślnym — usuwamy ją, więc dochodzi rola domyślna.
        out["roles"] = {name: value for name, value in roles.items()
                        if _role_endpoint(value) not in removed}
    if out.get("default") in removed:
        del out["default"]
    return out


def _role_endpoint(value) -> str:
    model = value.get("model") if isinstance(value, dict) else value
    return model.partition(":")[0] if isinstance(model, str) else ""
