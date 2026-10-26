"""Migawka Limitów AI do raportu: lokalne API usługi QuotaApp, tak jak widżet Limity AI
(widgets/limits/contents/ui/LimitsSource.qml, decyzja 21).

Port i token są w config.json usługi ($LIMITY_CONFIG, inaczej $XDG_CONFIG_HOME/limity-ai-widget).
Token idzie wyłącznie w nagłówku X-Limity-Token do 127.0.0.1 — nigdy do dziennika, komunikatu
błędu ani raportu. Zapytanie jest tylko do odczytu (GET /status), bez pośrednika (proxy).

Prognoza (`forecast`) to miejsce na później: raport pokaże ją, gdy funkcja zacznie coś zwracać.
"""
import json
import os
import urllib.error
import urllib.request

DEFAULT_PORT = 41817
TIMEOUT = 3.0
SERVICES = (("chatgpt", "GPT"), ("claude", "Claude"))


def config_path(env=None) -> str:
    env = os.environ if env is None else env
    if env.get("LIMITY_CONFIG"):
        return env["LIMITY_CONFIG"]
    config = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(config, "limity-ai-widget", "config.json")


def fetch_status(env=None, timeout: float = TIMEOUT) -> dict:
    """{"ok": True, "status": {...}} albo {"ok": False, "error": "<opis bez tokenu>"}."""
    path = config_path(env)
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
        port = int(config.get("port", DEFAULT_PORT))
        token = str(config.get("token", ""))
    except (OSError, ValueError, TypeError, AttributeError):
        return {"ok": False, "error": "brak konfiguracji usługi Limity AI"}
    if not token or not 0 < port < 65536:
        return {"ok": False, "error": "brak konfiguracji usługi Limity AI"}
    request = urllib.request.Request(f"http://127.0.0.1:{port}/status", headers={"X-Limity-Token": token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            status = json.loads(response.read(1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"usługa Limity AI odpowiedziała {exc.code}"}
    except (OSError, ValueError):
        return {"ok": False, "error": "usługa Limity AI nie odpowiada"}
    if not isinstance(status, dict):
        return {"ok": False, "error": "nieznana odpowiedź usługi Limity AI"}
    return {"ok": True, "status": status}


def snapshot(result: dict | None) -> dict:
    """Postać do raportu: {"ok", "error", "services": [{key, name, state, lastRead, metrics: [...]}]}."""
    if not result:
        return {"ok": False, "error": "", "services": []}
    if not result.get("ok"):
        return {"ok": False, "error": str(result.get("error") or ""), "services": []}
    status = result.get("status") or {}
    services = []
    for key, name in SERVICES:
        data = status.get(key)
        if not isinstance(data, dict):
            continue
        metrics = []
        for metric, label in (("session", "sesja"), ("weekly", "tydzień")):
            value = data.get(metric) if isinstance(data.get(metric), dict) else {}
            remaining = value.get("remaining")
            if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
                metrics.append({"metric": metric, "label": label, "remaining": float(remaining),
                                "reset": str(value.get("reset") or "")})
        services.append({"key": key, "name": name, "state": str(data.get("state") or ""),
                         "lastRead": str(data.get("lastRead") or ""), "metrics": metrics})
    return {"ok": True, "error": "", "services": services}


def forecast(snapshot_: dict, history=None):
    """Prognoza zużycia limitów — jeszcze nie liczona (zwraca None). Gdy zwróci listę
    {"name", "text"}, raport pokaże ją pod migawką bez zmian w szablonie."""
    return None
