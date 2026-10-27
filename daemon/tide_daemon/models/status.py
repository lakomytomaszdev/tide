"""Stan serwerów modeli: jeden odczyt wszystkich punktów z models.json i ról, bez tokenów.

- `ollama` — `GET /api/version`, `/api/tags` (pobrane modele) i `/api/ps` (załadowane: rozmiar, część na
  karcie, kontekst, kiedy zwolni pamięć). Żadne z nich nie ładuje modelu.
- `openai` — `GET /v1/models`; wersji i załadowanych modeli taki serwer nie podaje (`null`).

Krótkie limity czasu (STATUS_TIMEOUT_S na zapytanie): odczyt idzie w wątku roboczym usługi
(models_monitor.py), ale i tak nie może wisieć. Serwer, który nie odpowiada na pierwsze zapytanie,
jest od razu `online: false` z komunikatem z ModelError. Brak zmiennych pól (czasu odczytu), więc
ten sam stan serwera daje ten sam JSON — usługa wysyła Changed tylko przy prawdziwej zmianie.
"""
from __future__ import annotations

from . import config as config_mod
from .transport import ModelError, request

STATUS_TIMEOUT_S = 3.0


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value) -> str:
    return value if isinstance(value, str) else ""


def _entries(data, key: str) -> list[dict]:
    items = data.get(key) if isinstance(data, dict) else None
    return [e for e in items if isinstance(e, dict)] if isinstance(items, list) else []


def model_key(name: str) -> str:
    """„bge-m3” i „bge-m3:latest” to ten sam model w Ollamie."""
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


def _ollama_models(tags) -> list[dict]:
    out = []
    for entry in _entries(tags, "models"):
        if not _str(entry.get("name") or entry.get("model")):
            continue
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        out.append({"name": _str(entry.get("name") or entry.get("model")), "size": _int(entry.get("size")),
                    "family": _str(details.get("family")), "parameterSize": _str(details.get("parameter_size")),
                    "quantization": _str(details.get("quantization_level"))})
    return sorted(out, key=lambda m: m["name"])


def _ollama_loaded(ps) -> list[dict]:
    out = []
    for entry in _entries(ps, "models"):
        if not _str(entry.get("name") or entry.get("model")):
            continue
        size, vram = _int(entry.get("size")), _int(entry.get("size_vram"))
        out.append({"name": _str(entry.get("name") or entry.get("model")), "size": size, "sizeVram": vram,
                    "gpuPercent": round(100 * vram / size) if size and vram is not None else None,
                    "contextLength": _int(entry.get("context_length")), "expiresAt": _str(entry.get("expires_at"))})
    return sorted(out, key=lambda m: m["name"])


def _openai_models(listing) -> list[dict]:
    names = {_str(e.get("id")) for e in _entries(listing, "data")} - {""}
    return [{"name": name} for name in sorted(names)]


def _offline(endpoint, error: str = "") -> dict:
    """Punkt bez odczytu: `loaded` to lista tylko dla Ollamy (serwer zgodny z OpenAI go nie podaje)."""
    return {"name": endpoint.name, "url": endpoint.public_url(), "kind": endpoint.kind,
            "auth": bool(endpoint.token_file or endpoint.token), "online": False, "error": error, "version": None, "models": [],
            "loaded": [] if endpoint.kind == "ollama" else None}


def endpoint_status(endpoint, timeout: float = STATUS_TIMEOUT_S) -> dict:
    """Stan jednego punktu: {name, url, kind, auth, online, error, version, models, loaded}."""
    result = _offline(endpoint)
    try:
        if endpoint.kind == "ollama":
            version = request(endpoint, "GET", "/api/version", timeout=timeout)
            result["version"] = _str(version.get("version")) if isinstance(version, dict) else ""
            result["online"] = True
            result["models"] = _ollama_models(request(endpoint, "GET", "/api/tags", timeout=timeout))
            result["loaded"] = _ollama_loaded(request(endpoint, "GET", "/api/ps", timeout=timeout))
        else:
            result["models"] = _openai_models(request(endpoint, "GET", "/v1/models", timeout=timeout))
            result["online"] = True
    except ModelError as exc:
        # Serwer odpowiedział na /api/version, a potem nie — też „niedostępny”, z pustymi listami.
        return _offline(endpoint, str(exc))
    except Exception as exc:  # przeoczony przypadek w jednym punkcie nie kładzie odczytu pozostałych
        return _offline(endpoint, f"Model lokalny: błąd odczytu stanu ({type(exc).__name__})")
    return result


def probe(endpoint, timeout: float = STATUS_TIMEOUT_S, cancel=None) -> dict:
    """Próba połączenia (TestEndpoint z D-Bus, przed zapisem): wersja i liczba modeli, bez /api/ps.
    {ok, error, version, models} — `ok` to „serwer odpowiada”; błąd to komunikat z ModelError."""
    try:
        if endpoint.kind == "ollama":
            version = request(endpoint, "GET", "/api/version", timeout=timeout, cancel=cancel)
            tags = request(endpoint, "GET", "/api/tags", timeout=timeout, cancel=cancel)
            return {"ok": True, "error": "", "version": _str(version.get("version")) if isinstance(version, dict)
                    else "", "models": len(_ollama_models(tags))}
        listing = request(endpoint, "GET", "/v1/models", timeout=timeout, cancel=cancel)
        return {"ok": True, "error": "", "version": None, "models": len(_openai_models(listing))}
    except ModelError as exc:
        return {"ok": False, "error": str(exc), "version": None, "models": None}
    except Exception as exc:  # przeoczony przypadek nie może zostawić wywołującego bez odpowiedzi
        return {"ok": False, "error": f"Model lokalny: błąd próby połączenia ({type(exc).__name__})",
                "version": None, "models": None}


def snapshot(cfg, timeout: float = STATUS_TIMEOUT_S) -> dict:
    """Stan wszystkich punktów i ról z konfiguracji. Role: {endpoint, model, ctx, installed, available, error}."""
    endpoints = {name: endpoint_status(endpoint, timeout) for name, endpoint in cfg.endpoints.items()}
    return assemble(cfg, endpoints)


def same_endpoint(state: dict, endpoint) -> bool:
    """Stan punktu z poprzedniego odczytu nadal opisuje ten punkt (ta sama nazwa, adres, rodzaj i token)."""
    return (isinstance(state, dict) and state.get("name") == endpoint.name and state.get("url") == endpoint.public_url()
            and state.get("kind") == endpoint.kind and state.get("auth") == bool(endpoint.token_file))


def assemble(cfg, endpoints: dict, checked: bool = True) -> dict:
    """Stan z konfiguracji i stanów punktów. Punkt bez stanu (np. dopiero dodany z D-Bus) jest „nie sprawdzony”:
    `online: false` bez błędu — do następnego odczytu."""
    endpoints = {name: endpoints[name] if same_endpoint(endpoints.get(name), endpoint) else _offline(endpoint)
                 for name, endpoint in cfg.endpoints.items()}
    roles = {}
    for name, role in cfg.roles.items():
        state = endpoints.get(role.endpoint) or {}
        installed = None
        if state.get("online"):
            names = {m["name"] for m in state.get("models") or []}
            if state.get("kind") == "ollama":
                installed = model_key(role.model) in {model_key(n) for n in names}
            else:
                installed = role.model in names
        roles[name] = {"endpoint": role.endpoint, "model": role.model, "ctx": role.ctx, "installed": installed,
                       "available": bool(state.get("online") and installed and not role.error), "error": role.error}
    return {"checked": checked, "default": cfg.default, "config": cfg.public(), "endpoints": endpoints, "roles": roles}


def pending(cfg) -> dict:
    """Stan przed pierwszym odczytem (usługa dopiero wystartowała): `checked: false`, nic nie wiadomo."""
    return assemble(cfg, {}, checked=False)


def read(path: str | None = None, timeout: float = STATUS_TIMEOUT_S) -> dict:
    """Jeden odczyt: models.json czytany teraz, potem wszystkie punkty po kolei."""
    return snapshot(config_mod.load(path), timeout)
