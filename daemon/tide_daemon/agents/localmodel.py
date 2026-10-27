"""Czy sesja pracuje na modelu lokalnym — z tego, co zapisuje sam program agenta.

Lista dopuszczonych, nie wykluczeń: „lokalnie” mówi użytkownikowi, że dane nie wyszły z jego sprzętu,
więc w razie wątpliwości sesja NIE jest oznaczana (bramka do chmury z nieznaną nazwą modelu albo
dostawcy, Bedrock, Azure itp. zostają bez znacznika).

- Codex: `session_meta.model_provider` ∈ {`ollama`, `lmstudio`, `oss`} (wbudowani dostawcy lokalni,
  `--oss`) albo z przedrostkiem `tide-` (nasi dostawcy z okna „Nowe zadanie”, tasks/local.py); model
  z `turn_context.model`,
- Claude Code: `message.model` odpowiedzi modelu, lokalny gdy nie jest Claude'em (`claude`, `anthropic`
  w nazwie, aliasy, `<synthetic>`) i albo jest znany z bieżącego models.json (role i modele na serwerach
  ze stanu /Models — `known`), albo ma etykietę w stylu Ollamy `nazwa:tag` bez „/” (dokładnie jeden
  dwukropek, np. `qwen3-coder:30b`; ARN Bedrocka ma ich kilka, a `dostawca/model:free` to bramka),
- model Ollama Cloud (`…:cloud`, `…:120b-cloud`, modelnames.py) nigdy nie jest lokalny, także w Codexie
  z dostawcą `ollama` albo `oss` — lokalna Ollama tylko przekazuje rozmowę na ollama.com.

Wynik trafia do listy /Agents jako `model` i `localModel`, a widżet agentów dopisuje „lokalny · model”.
"""
import re

from ..modelnames import is_cloud_model  # noqa: F401 — wystawione też tutaj

LOCAL_CODEX_PROVIDERS = {"ollama", "lmstudio", "oss"}
TIDE_PROVIDER_PREFIX = "tide-"
CLAUDE_ALIASES = {"opus", "sonnet", "haiku", "fable", "opusplan", "default", "best"}
OLLAMA_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*:[A-Za-z0-9][A-Za-z0-9._-]*")  # bez „/” (bramki)


def is_local_codex_provider(provider) -> bool:
    if not isinstance(provider, str):
        return False
    name = provider.lower()
    return name in LOCAL_CODEX_PROVIDERS or (name.startswith(TIDE_PROVIDER_PREFIX)
                                             and len(name) > len(TIDE_PROVIDER_PREFIX))


def _key(name: str) -> str:
    """„bge-m3” i „bge-m3:latest” to ten sam model w Ollamie (jak models/status.py: model_key)."""
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


def is_claude(model: str) -> bool:
    name = model.lower()
    return not name or name.startswith("<") or "claude" in name or "anthropic" in name or name in CLAUDE_ALIASES


def is_local_claude_model(model, known=frozenset()) -> bool:
    if not isinstance(model, str) or is_claude(model) or is_cloud_model(model):
        return False
    if model in known or _key(model) in {_key(k) for k in known if isinstance(k, str) and k}:
        return True
    return bool(OLLAMA_TAG.fullmatch(model))


def session_is_local(log, known=frozenset()) -> bool:
    """Sesja (agents/claude.py, agents/codex.py) na modelu lokalnym."""
    if is_cloud_model(log.model):
        return False  # Ollama Cloud także przez dostawcę `ollama`/`oss`
    if log.tool == "codex":
        return is_local_codex_provider(getattr(log, "provider", ""))
    if log.tool == "claude":
        return is_local_claude_model(log.model, known)
    return False


def known_models(status) -> set:
    """Modele z bieżącego stanu /Models (models/status.py): z ról i pobrane na serwerach."""
    out = set()
    if not isinstance(status, dict):
        return out
    roles = status.get("roles")
    for role in (roles.values() if isinstance(roles, dict) else []):
        if isinstance(role, dict) and isinstance(role.get("model"), str) and role["model"]:
            out.add(role["model"])
    endpoints = status.get("endpoints")
    for endpoint in (endpoints.values() if isinstance(endpoints, dict) else []):
        models = endpoint.get("models") if isinstance(endpoint, dict) else None
        for model in (models if isinstance(models, list) else []):
            if isinstance(model, dict) and isinstance(model.get("name"), str) and model["name"]:
                out.add(model["name"])
    return out


def config_models(path: str | None = None) -> set:
    """Modele ról z models.json (bez pytania serwera) — gdy stanu /Models nie ma (np. `--print`)."""
    try:
        from ..models import config as models_config
        cfg = models_config.load(path)
    except Exception:  # zepsute ustawienia nie mogą położyć listy sesji
        return set()
    return {role.model for role in cfg.roles.values() if role.model}
