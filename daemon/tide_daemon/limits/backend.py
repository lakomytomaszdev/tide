"""Lokalne API usługi QuotaApp (Limity AI): tylko GET /status.

Port i token są w config.json tej usługi (tworzy go jej instalator, tryb 0600). Czytamy je
przy każdym zapytaniu i trzymamy wyłącznie w pamięci tego wywołania — token nie trafia do
dziennika, historii, stanu ani odpowiedzi D-Bus. GET /status oddaje to, co usługa już
odczytała (bez wczytywania stron), więc pytanie co kilka minut niczego nie obciąża.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_PORT = 41817  # jak w widgets/stage.py (domyślny port QuotaApp)
TIMEOUT_S = 5


class BackendError(Exception):
    """Usługa nie odpowiedziała poprawnie (brak konfiguracji, nie działa, zły token)."""


def config_path() -> str:
    """config.json usługi: $LIMITY_CONFIG jak w jej backendzie, inaczej $XDG_CONFIG_HOME."""
    if os.environ.get("LIMITY_CONFIG"):
        return os.environ["LIMITY_CONFIG"]
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(config_home, "limity-ai-widget", "config.json")


def _endpoint(path: str) -> tuple[str, str]:
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
        port = int(config.get("port", DEFAULT_PORT))
        token = str(config.get("token", ""))
    except (OSError, ValueError, AttributeError) as exc:
        raise BackendError(f"brak konfiguracji usługi Limity AI ({type(exc).__name__})") from None
    if not token:
        raise BackendError("pusty token usługi Limity AI")
    return f"http://127.0.0.1:{port}/status", token


def fetch_status(path: str | None = None, timeout: float = TIMEOUT_S) -> dict:
    """Odpowiedź GET /status jako słownik. Błąd → BackendError (bez tokenu w treści)."""
    url, token = _endpoint(path or config_path())
    request = urllib.request.Request(url, headers={"X-Limity-Token": token})
    # Bez pośredników z $http_proxy — to zawsze pętla zwrotna.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise BackendError(f"usługa Limity AI odpowiedziała {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise BackendError(f"usługa Limity AI nie odpowiada ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise BackendError("nieoczekiwana odpowiedź usługi Limity AI")
    return data
