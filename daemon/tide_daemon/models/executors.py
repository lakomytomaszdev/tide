"""Katalog wykonawców i zamiana klasy na wykonawcę (PT-60, specyfikacja przepływu kart, sekcja „Wykonawcy”).

Wykonawca to program (claude albo codex) z modelem i punktem pod jedną nazwą. Katalog żyje w models.json
(klucze `executors` i `providers`), obok punktów i ról — zgodnie wstecz: bez tych kluczy działa katalog
domyślny, a pierwszy zapis z okna Tide zapisuje cały katalog (domyślny plus zmiana). Ten moduł nie zna Qt,
plików ani sieci: parsuje i waliduje katalog (config.from_dict), a `resolve` / `reviewer` zamieniają klasę
na wykonawcę. Zapis: models/settings.py (SetExecutor, SetProvider…), D-Bus: /Models.

Dostawca (`provider`): `anthropic`, `openai`, `local` (wbudowani) albo nazwa innego dostawcy z `providers`
({"label", "url", "api": "openai"|"anthropic", "tokenFile"}). Token innego dostawcy leży w pliku
`tokens/providers/<nazwa>` (0600) obok models.json — w katalogu i w odpowiedziach D-Bus jest tylko ścieżka
pliku i znacznik „ma token”, nigdy sam token (decyzja 70).

Wykonawca lokalny: `endpoint` to punkt z models.json (pusty — punkt roli `agent`), a pusty `model` i `program`
znaczą „jak agent lokalny”: model z roli `agent`, program z `agentProgram` (PT-17).

Zamiana klasy (lekki, standard, mocny, lokalny) na wykonawcę — preferowany dostawca × klasa:

    anthropic / openai / inny  → wykonawca tego dostawcy, który obsługuje klasę (kolejność katalogu);
                                 klasa lokalny → wykonawca lokalny,
    local (tylko lokalnie)     → wykonawca lokalny dla każdej klasy,
    mixed (mieszany)           → podpowiedź (Anthropic, OpenAI, inni, potem lokalny) i lista kandydatów
                                 z całego katalogu; wybiera planujący.

Praca lokalna nigdy nie przechodzi do chmury: gdy serwer wykonawcy lokalnego nie odpowiada (albo nie ma na
nim modelu), wynik to `ok: false` z jasnym komunikatem — bez zastępstwa z chmury. Recenzent zawsze innym
modelem niż autor; w trybie mieszanym u innego dostawcy; autor lokalny — recenzent też lokalny (diff nie
wychodzi z komputera).
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

from ..modelnames import is_cloud_model

CLASSES = ("lekki", "standard", "mocny", "lokalny")
CLASS_ORDER = ("lekki", "standard", "mocny")          # siła rosnąco
PROGRAMS = ("claude", "codex")
API_KINDS = ("openai", "anthropic")
BUILTIN_PROVIDERS = {"anthropic": "Anthropic", "openai": "OpenAI", "local": "Lokalny"}
LOCAL = "local"
# Wartości „preferowanego dostawcy” ze zlecenia: anthropic, openai, mixed, local (tylko lokalnie) albo nazwa innego.
MIXED = "mixed"
RESERVED = set(BUILTIN_PROVIDERS) | {MIXED}
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}")
EXECUTOR_NAME_MAX = 60
TOKEN_SUBDIR = "providers"        # tokens/providers/<nazwa> — osobno od tokenów punktów (tokens/<punkt>)
EFFORTS = ("low", "medium", "high", "xhigh")


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    url: str
    api: str                  # "openai" albo "anthropic"
    token_file: str = ""      # ścieżka bezwzględna; pusty — bez tokenu


@dataclass(frozen=True)
class Executor:
    name: str
    program: str              # "claude" / "codex"; pusty tylko u lokalnego (jak agentProgram)
    model: str                # pusty tylko u lokalnego (jak rola agent)
    provider: str
    endpoint: str = ""        # lokalny: punkt z models.json (pusty — punkt roli agent)
    classes: tuple = ()
    effort: str = ""          # wysiłek rozumowania Codexa: low|medium|high|xhigh (pusty — domyślny programu)


DEFAULT_EXECUTORS = (
    Executor("Haiku", "claude", "haiku", "anthropic", classes=("lekki",)),
    Executor("Sonnet", "claude", "sonnet", "anthropic", classes=("standard",)),
    Executor("Opus", "claude", "opus", "anthropic", classes=("mocny",)),
    Executor("Codex lekki", "codex", "gpt-6-luna", "openai", classes=("lekki",)),
    Executor("Codex standard", "codex", "gpt-6.1-sol", "openai", classes=("standard",), effort="medium"),
    Executor("Codex mocny", "codex", "gpt-6.1-sol", "openai", classes=("mocny",), effort="high"),
    # Do planowania trudnych kart; bez klas, więc żadna klasa nie zamienia się na niego (tylko wybór ręczny
    # i podpowiedzi trybu mieszanego).
    Executor("Codex Astra", "codex", "gpt-6-astra", "openai", effort="high"),
    # Najmocniejszy model Claude do planowania trudnych kart (03.11, K-38); jak Astra — bez klas, tylko wybór ręczny.
    Executor("Fable", "claude", "claude-fable-5-1", "anthropic"),
    Executor("Lokalny", "", "", LOCAL, classes=("lokalny",)),
)


@dataclass(frozen=True)
class Catalog:
    executors: tuple
    providers: dict           # nazwa → Provider (tylko inni dostawcy)
    stored: bool = False      # katalog zapisany w models.json (inaczej domyślny)
    warnings: tuple = field(default_factory=tuple)

    def get(self, name):
        return next((e for e in self.executors if e.name == name), None)

    def api_of(self, provider: str) -> str:
        """Rodzaj API dostawcy: anthropic, openai albo „” (lokalny: oba)."""
        if provider == "anthropic":
            return "anthropic"
        if provider == "openai":
            return "openai"
        other = self.providers.get(provider)
        return other.api if other else ""

    def known_provider(self, name) -> bool:
        return name in BUILTIN_PROVIDERS or name in self.providers


def default_catalog() -> Catalog:
    return Catalog(DEFAULT_EXECUTORS, {}, False)


# --- odczyt z models.json ---------------------------------------------------------------------------

def url_problem(url) -> str:
    """Powód po polsku, gdy adres dostawcy się nie nadaje ("" — dobry)."""
    try:
        parts = urllib.parse.urlsplit(url.strip()) if isinstance(url, str) else None
        port = parts.port if parts else None
    except ValueError:
        parts, port = None, None
    if not parts or parts.scheme not in ("http", "https") or not parts.hostname or port == 0:
        return "niepoprawny adres (oczekiwano http(s)://host[:port])"
    if "@" in parts.netloc:
        return "dane logowania w adresie (token w pliku, nie w adresie)"
    return ""


def clean_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).rstrip("/")


def _provider(name, value, warnings) -> Provider | None:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name) or name in RESERVED:
        warnings.append(f"dostawca „{str(name)[:40]}”: niepoprawna albo zarezerwowana nazwa — pominięty")
        return None
    if not isinstance(value, dict):
        warnings.append(f"dostawca „{name}”: oczekiwano obiektu — pominięty")
        return None
    problem = url_problem(value.get("url"))
    if problem:
        warnings.append(f"dostawca „{name}”: {problem} — pominięty")
        return None
    api = value.get("api")
    if api not in API_KINDS:
        warnings.append(f"dostawca „{name}”: nieznane API „{str(api)[:40]}” (openai albo anthropic) — pominięty")
        return None
    token_file = value.get("tokenFile") or ""
    if not isinstance(token_file, str) or (token_file and not token_file.startswith(("/", "~"))):
        warnings.append(f"dostawca „{name}”: tokenFile musi być ścieżką bezwzględną — pominięty")
        return None
    label = value.get("label")
    label = label.strip()[:EXECUTOR_NAME_MAX] if isinstance(label, str) and label.strip() else name
    return Provider(name, label, clean_url(value["url"]), api, token_file)


def api_problem(program: str, api: str) -> str:
    """Claude Code mówi tylko z API Anthropic, Codex — z API OpenAI; lokalny (api „”) może oba."""
    if api == "anthropic" and program != "claude":
        return "dostawca z API Anthropic obsługuje tylko program claude"
    if api == "openai" and program != "codex":
        return "dostawca z API OpenAI obsługuje tylko program codex"
    return ""


def executor_problem(value, providers: dict, endpoints=None) -> tuple[Executor | None, str]:
    """Wykonawca z surowego obiektu: (Executor, "") albo (None, powód po polsku). Jedna walidacja dla odczytu
    pliku i zapisu z D-Bus. `endpoints` (nazwy punktów) — gdy podane, lokalny sprawdza swój punkt."""
    if not isinstance(value, dict):
        return None, "wykonawca: oczekiwano obiektu"
    name = value.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > EXECUTOR_NAME_MAX \
            or any(ord(c) < 32 for c in name):
        return None, f"wykonawca „{str(name)[:40]}”: nazwa 1…{EXECUTOR_NAME_MAX} znaków"
    name = name.strip()
    provider = value.get("provider")
    if not isinstance(provider, str) or not (provider in BUILTIN_PROVIDERS or provider in providers):
        return None, f"wykonawca „{name}”: nieznany dostawca „{str(provider)[:40]}”"
    program = value.get("program") or ""
    model = value.get("model")
    model = model.strip() if isinstance(model, str) else ("" if model is None else None)
    if program not in PROGRAMS and not (program == "" and provider == LOCAL):
        return None, f"wykonawca „{name}”: program „{str(program)[:40]}” (claude albo codex)"
    if model is None or len(model) > 200 or any(ord(c) < 32 for c in model):
        return None, f"wykonawca „{name}”: niepoprawny model"
    if not model and provider != LOCAL:
        return None, f"wykonawca „{name}”: podaj model"
    if model and provider == LOCAL and is_cloud_model(model):
        return None, f"wykonawca „{name}”: model „{model[:60]}” działa w chmurze Ollamy, nie lokalnie"
    effort = value.get("effort") or ""
    if effort not in EFFORTS and effort != "":
        return None, f"wykonawca „{name}”: wysiłek to low, medium, high albo xhigh"
    if effort and program == "claude":
        return None, f"wykonawca „{name}”: wysiłek rozumowania dotyczy tylko Codexa"
    api = "anthropic" if provider == "anthropic" else "openai" if provider == "openai" else \
        providers[provider].api if provider in providers else ""
    if program:
        problem = api_problem(program, api)
        if problem:
            return None, f"wykonawca „{name}”: {problem}"
    endpoint = value.get("endpoint") or ""
    if not isinstance(endpoint, str):
        return None, f"wykonawca „{name}”: niepoprawny serwer"
    if provider != LOCAL:
        endpoint = ""    # chmura i inny dostawca: punkt wynika z dostawcy
    elif endpoint and endpoints is not None and endpoint not in endpoints:
        return None, f"wykonawca „{name}”: nieznany serwer „{endpoint[:40]}”"
    classes = value.get("classes", [])
    if not isinstance(classes, (list, tuple)) or any(c not in CLASSES for c in classes):
        return None, f"wykonawca „{name}”: klasy to lekki, standard, mocny, lokalny"
    ordered = tuple(c for c in CLASSES if c in classes)
    return Executor(name, program, model, provider, endpoint, ordered, effort), ""


def parse(data, endpoints=None) -> Catalog:
    """Katalog z obiektu models.json; zły wykonawca albo dostawca jest pomijany z ostrzeżeniem.
    Bez klucza `executors` — katalog domyślny (i `providers`, jeśli są)."""
    if not isinstance(data, dict):
        return default_catalog()
    warnings: list[str] = []
    providers: dict = {}
    raw_providers = data.get("providers")
    if raw_providers is not None and not isinstance(raw_providers, dict):
        warnings.append("providers: oczekiwano obiektu — bez innych dostawców")
        raw_providers = None
    for name, value in (raw_providers or {}).items():
        provider = _provider(name, value, warnings)
        if provider:
            providers[name] = provider
    raw = data.get("executors")
    if raw is None:
        return Catalog(DEFAULT_EXECUTORS, providers, False, tuple(warnings))
    if not isinstance(raw, list):
        warnings.append("executors: oczekiwano listy — katalog domyślny")
        return Catalog(DEFAULT_EXECUTORS, providers, False, tuple(warnings))
    executors, seen = [], set()
    for value in raw:
        executor, problem = executor_problem(value, providers, endpoints)
        if executor is not None and executor.name in seen:
            executor, problem = None, f"wykonawca „{executor.name}”: nazwa występuje drugi raz"
        if executor is None:
            warnings.append(f"{problem} — pominięty")
            continue
        seen.add(executor.name)
        executors.append(executor)
    return Catalog(tuple(executors), providers, True, tuple(warnings))


# --- zapis (kształt pliku) --------------------------------------------------------------------------

def executor_to_dict(e: Executor) -> dict:
    return {"name": e.name, "program": e.program, "model": e.model, "provider": e.provider,
            "endpoint": e.endpoint, "classes": list(e.classes), "effort": e.effort}


def catalog_to_data(catalog: Catalog) -> list:
    return [executor_to_dict(e) for e in catalog.executors]


# --- zamiana klasy na wykonawcę ---------------------------------------------------------------------

@dataclass
class Context:
    """Co potrzebne do zamiany: katalog i stan modeli lokalnych (config.ModelsConfig + models/status.py)."""
    catalog: Catalog
    agent_program: str = "codex"
    agent_endpoint: str = ""          # punkt roli `agent`
    agent_model: str = ""             # model roli `agent` (pusty, gdy rola jest niedostępna)
    agent_error: str = ""             # powód niedostępności roli `agent` (np. model chmurowy Ollamy)
    endpoint_urls: dict = field(default_factory=dict)    # punkt → adres (bez danych logowania)
    status: dict | None = None        # Status().endpoints / checked; None — dostępność nieznana

    def resolved(self, e: Executor) -> dict:
        """Wykonawca ze wszystkimi polami (lokalny: program, model i punkt z agenta lokalnego)."""
        local = e.provider == LOCAL
        program = e.program or (self.agent_program if local else "")
        model = e.model or (self.agent_model if local else "")
        endpoint = (e.endpoint or self.agent_endpoint) if local else ""
        provider = self.catalog.providers.get(e.provider)
        if local:
            url = self.endpoint_urls.get(endpoint, "")
        elif provider:
            url = provider.url
        else:
            url = ""
        return {"name": e.name, "program": program, "model": model, "provider": e.provider,
                "endpoint": endpoint, "url": url, "classes": list(e.classes), "local": local, "effort": e.effort}

    def unavailable(self, e: Executor) -> str:
        """Powód, dla którego wykonawca lokalny nie zadziała teraz ("" — działa albo nie wiadomo).
        Chmura: nieznane, więc ""."""
        if e.provider != LOCAL:
            return ""
        info = self.resolved(e)
        if not info["model"]:
            if self.agent_error and not e.model:
                return f"wykonawca „{e.name}”: rola agent niedostępna — {self.agent_error}"
            return f"wykonawca „{e.name}”: brak modelu — ustaw rolę agent albo model wykonawcy"
        if is_cloud_model(info["model"]):
            return f"wykonawca „{e.name}”: model „{info['model'][:60]}” działa w chmurze Ollamy, nie lokalnie"
        if not isinstance(self.status, dict) or not self.status.get("checked", True):
            return ""
        endpoints = self.status.get("endpoints")
        state = endpoints.get(info["endpoint"]) if isinstance(endpoints, dict) else None
        if not isinstance(state, dict):
            return f"wykonawca „{e.name}”: nieznany serwer „{info['endpoint']}”"
        if not state.get("online"):
            if not state.get("error"):
                return ""    # jeszcze niesprawdzony
            return f"wykonawca „{e.name}”: serwer „{info['endpoint']}” nie odpowiada"
        names = {m.get("name") for m in state.get("models") or [] if isinstance(m, dict)}
        if state.get("kind") == "ollama":
            from .status import model_key
            installed = model_key(info["model"]) in {model_key(n) for n in names if isinstance(n, str)}
        else:
            installed = info["model"] in names
        if not installed:
            return f"wykonawca „{e.name}”: modelu {info['model']} nie ma na serwerze „{info['endpoint']}”"
        return ""


@dataclass
class Resolution:
    ok: bool
    executor: dict | None = None
    error: str = ""
    provider: str = ""            # preferowany dostawca z zapytania
    klass: str = ""
    local: bool = False           # wynik (albo odmowa) dotyczy pracy lokalnej: do chmury nie przejdzie
    suggested: bool = False       # tryb mieszany: podpowiedź, wybiera planujący
    candidates: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "error": self.error, "executor": self.executor, "provider": self.provider,
                "class": self.klass, "local": self.local, "suggested": self.suggested,
                "candidates": list(self.candidates)}


def _strength(klass: str, e: Executor) -> int:
    """Im mniej, tym lepsze dopasowanie do klasy: klasa wprost, potem mocniejsze rosnąco, potem słabsze."""
    if klass in e.classes:
        return 0
    if klass not in CLASS_ORDER:
        return 99
    want = CLASS_ORDER.index(klass)
    best = 99
    for c in e.classes:
        if c in CLASS_ORDER:
            diff = CLASS_ORDER.index(c) - want
            best = min(best, diff if diff > 0 else 10 - diff)
    return best


def _same_model(ctx: Context, a: Executor, b: Executor) -> bool:
    ra, rb = ctx.resolved(a), ctx.resolved(b)
    return a.name == b.name or (ra["model"].lower() == rb["model"].lower() and bool(ra["model"]))


def _pick_local(ctx: Context, pool: list, provider: str, klass: str, empty: str) -> Resolution:
    """Pierwszy działający wykonawca lokalny z puli; nigdy zastępstwo z chmury."""
    if not pool:
        return Resolution(False, error=empty, provider=provider, klass=klass, local=True)
    reasons = []
    for e in pool:
        reason = ctx.unavailable(e)
        if not reason:
            return Resolution(True, ctx.resolved(e), provider=provider, klass=klass, local=True)
        reasons.append(reason)
    reason = reasons[0]
    return Resolution(False, error=f"{reason[:1].upper() + reason[1:]}. Praca lokalna nie przejdzie do chmury.",
                      provider=provider, klass=klass, local=True)


def _local_pool(ctx: Context) -> list:
    return [e for e in ctx.catalog.executors if e.provider == LOCAL]


def _check_request(ctx: Context, provider, klass) -> str:
    if not isinstance(klass, str) or not isinstance(provider, str):
        return "preferowany dostawca i klasa muszą być napisami"
    if klass not in CLASSES:
        return f"nieznana klasa „{str(klass)[:40]}” (lekki, standard, mocny, lokalny)"
    if provider != MIXED and not ctx.catalog.known_provider(provider):
        return f"nieznany preferowany dostawca „{str(provider)[:40]}”"
    return ""


def resolve(ctx: Context, provider: str, klass: str) -> Resolution:
    """Klasa × preferowany dostawca → wykonawca (tabela ze specyfikacji, sekcja „Wykonawcy”)."""
    problem = _check_request(ctx, provider, klass)
    if problem:
        return Resolution(False, error=problem, provider=str(provider), klass=str(klass))
    no_local = "w katalogu nie ma wykonawcy lokalnego — dodaj go w widoku Modele"
    if provider == LOCAL or klass == "lokalny":
        # Tylko lokalnie: każda klasa na wykonawcę lokalnego; klasa „lokalny” — tak samo, bez względu na dostawcę.
        return _pick_local(ctx, _local_pool(ctx), provider, klass, no_local)
    if provider == MIXED:
        every = sorted(ctx.catalog.executors, key=lambda e: (_strength(klass, e),))
        names = [e.name for e in every]
        order = {"anthropic": 0, "openai": 1, LOCAL: 9}
        cloud = [e for e in every if klass in e.classes and e.provider != LOCAL]
        cloud.sort(key=lambda e: order.get(e.provider, 2))
        if not cloud:
            return Resolution(False, error=f"w katalogu nie ma wykonawcy klasy {klass}", provider=provider,
                              klass=klass, candidates=names)
        return Resolution(True, ctx.resolved(cloud[0]), provider=provider, klass=klass, suggested=True,
                          candidates=names)
    pool = [e for e in ctx.catalog.executors if e.provider == provider and klass in e.classes]
    if not pool:
        label = BUILTIN_PROVIDERS.get(provider) or ctx.catalog.providers[provider].label
        return Resolution(False, error=f"dostawca {label} nie ma wykonawcy klasy {klass}", provider=provider,
                          klass=klass)
    return Resolution(True, ctx.resolved(pool[0]), provider=provider, klass=klass)


def reviewer(ctx: Context, provider: str, klass: str, author: str) -> Resolution:
    """Recenzent dla ticketu autora `author` (nazwa wykonawcy): zawsze inny model niż autor; w trybie
    mieszanym u innego dostawcy; autor lokalny (albo klasa lokalny, albo tylko lokalnie) — recenzent lokalny."""
    problem = _check_request(ctx, provider, klass)
    if problem:
        return Resolution(False, error=problem, provider=str(provider), klass=str(klass))
    who = ctx.catalog.get(author)
    if who is None:
        return Resolution(False, error=f"nieznany autor „{str(author)[:40]}” w katalogu", provider=provider,
                          klass=klass)
    others = [e for e in ctx.catalog.executors if not _same_model(ctx, e, who)]
    if provider == LOCAL or klass == "lokalny" or who.provider == LOCAL:
        return _pick_local(ctx, [e for e in others if e.provider == LOCAL], provider, klass,
                           "brak innego wykonawcy lokalnego niż autor — dodaj drugi model lokalny w widoku Modele")
    if provider == MIXED:
        pool = [e for e in others if e.provider not in (who.provider, LOCAL)]
        label = "u innego dostawcy niż autor"
    else:
        pool = [e for e in others if e.provider == provider]
        label = f"u dostawcy {BUILTIN_PROVIDERS.get(provider) or ctx.catalog.providers[provider].label}"
    if provider == "openai" and klass == "mocny" and who.name == "Codex mocny":
        astra = next((e for e in pool if e.name == "Codex Astra"), None)
        if astra is not None:
            return Resolution(True, ctx.resolved(astra), provider=provider, klass=klass)
    # Wykonawcy bez klas (Codex Astra, Fable) są tylko do wyboru ręcznego — nie trafiają do automatycznego doboru.
    pool = [e for e in pool if e.classes]
    pool.sort(key=lambda e: _strength(klass, e))
    if not pool:
        return Resolution(False, error=f"brak recenzenta innego niż autor {label}", provider=provider, klass=klass)
    return Resolution(True, ctx.resolved(pool[0]), provider=provider, klass=klass, suggested=provider == MIXED,
                      candidates=[e.name for e in pool])


# --- stan dla Status() ------------------------------------------------------------------------------

def context_from(cfg, status: dict | None = None) -> Context:
    """Kontekst z konfiguracji (config.ModelsConfig) i, jeśli jest, ze Status() (online, modele)."""
    agent = cfg.roles.get("agent")
    return Context(cfg.catalog, cfg.agent_program, agent.endpoint if agent else cfg.default,
                   agent.model if agent and not agent.error else "", agent.error if agent else "",
                   {name: e.public_url() for name, e in cfg.endpoints.items()}, status)


def public_state(cfg, endpoints_state: dict | None, checked: bool) -> dict:
    """Pola `executors` i `providers` dla Status(): bez tokenów, z dostępnością wykonawców lokalnych."""
    ctx = context_from(cfg, {"checked": checked, "endpoints": endpoints_state or {}})
    executors = []
    for e in cfg.catalog.executors:
        info = ctx.resolved(e)
        reason = ctx.unavailable(e)
        info["where"] = "local" if info["local"] else "cloud"
        info["available"] = (not reason) if info["local"] else None   # chmura: nie sprawdzamy
        info["error"] = reason
        info["builtin"] = e.name in {d.name for d in DEFAULT_EXECUTORS}
        # Zapisane pola (okno edytuje je, nie rozwiązane wartości agenta lokalnego).
        info["modelSet"], info["programSet"], info["endpointSet"] = bool(e.model), bool(e.program), bool(e.endpoint)
        executors.append(info)
    providers = {name: {"label": label, "url": "", "api": api, "auth": False, "builtin": True}
                 for name, label, api in (("anthropic", "Anthropic", "anthropic"), ("openai", "OpenAI", "openai"),
                                          (LOCAL, "Lokalny", ""))}
    for name, p in cfg.catalog.providers.items():
        providers[name] = {"label": p.label, "url": p.url, "api": p.api, "auth": bool(p.token_file),
                           "builtin": False}
    return {"stored": cfg.catalog.stored, "list": executors, "providers": providers,
            "warnings": list(cfg.catalog.warnings)}
