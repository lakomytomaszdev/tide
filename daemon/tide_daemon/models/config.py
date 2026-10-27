"""Punkty dostępu i role modeli lokalnych z $XDG_CONFIG_HOME/tide/models.json (czytane w chwili wywołania).

Kształt pliku (wszystko opcjonalne):

    {"default": "local",
     "endpoints": {"local": {"url": "http://127.0.0.1:11434", "kind": "ollama"},
                   "mac": {"url": "http://mac-studio:11434", "kind": "ollama", "tokenFile": "~/.config/tide/mac.token"}},
     "roles": {"summary": "local:qwen3:8b",
               "describe": {"model": "local:qwen3:8b", "ctx": 8192},
               "agent": "qwen3-coder:30b"}}

Rola to „punkt:model” albo obiekt {"model": "punkt:model", "ctx": N}. Nazwy modeli mają dwukropki
(`qwen3:8b`, `hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M`), więc dzielimy tylko na pierwszym —
i tylko wtedy, gdy przed nim stoi nazwa znanego punktu. Inaczej cały napis to model na punkcie
`default` (przepięcie na Maca to zmiana `default` albo przedrostka w rolach).

`ctx` to kontekst (`num_ctx` w Ollamie). Role `summary` i `describe` bez `ctx` dostają 8192: na karcie
8 GB model 8B z kontekstem 16k nie mieści się w całości i pisze kilkanaście razy wolniej (PT-13);
pozostałe role bez `ctx` — kontekst serwera. `ctx: 0` też znaczy „kontekst serwera”.

Bez pliku: punkt `local` (http://127.0.0.1:11434, ollama) i role `summary`, `describe` → qwen3:8b.
Zepsuty plik (nie JSON, nie obiekt) → wartości domyślne i `error`; zły punkt albo rola są pomijane
z ostrzeżeniem w `warnings`, reszta działa. Brakujące role domyślne dochodzą na punkcie `default`.
Punkt jest zły także wtedy, gdy adresu nie da się rozebrać na host i port (port spoza 1…65535,
IPv6 bez nawiasów) albo adres ma dane logowania (`user:hasło@` — hasło w pliku ustawień i w adresie
widocznym w stanie; od tego jest `tokenFile`). Rola z przedrostkiem pominiętego punktu nie przechodzi
cicho na `default`: zostaje przy swoim punkcie jako niedostępna (`error`), wywołanie daje ModelError.

Token (`tokenFile`, z rozwinięciem `~`) czytamy przy każdym zapytaniu, tylko w pamięci, i wysyłamy
jako `Authorization: Bearer`; nie trafia do dziennika, komunikatów błędów ani odpowiedzi D-Bus.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass, field

from .transport import ModelError

KINDS = ("ollama", "openai")
DEFAULT_ENDPOINT = "local"
DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_CTX = 8192
# Role domyślne (bez punktu — idą na `default`) i kontekst, który dostają, gdy plik go nie podaje.
DEFAULT_ROLES = {"summary": DEFAULT_MODEL, "describe": DEFAULT_MODEL}
ROLE_CTX = {"summary": DEFAULT_CTX, "describe": DEFAULT_CTX}
MAX_CTX = 1 << 20
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}")


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str                 # adres serwera bez końcowego „/” (dla openai także bez „/v1”)
    kind: str                # "ollama" albo "openai"
    token_file: str = ""     # ścieżka po rozwinięciu „~”; pusta — bez nagłówka Authorization

    def address(self) -> tuple[str, str, int | None]:
        """(schemat, host, port) z adresu; adres, którego nie da się rozebrać → ModelError."""
        try:
            parts = urllib.parse.urlsplit(self.url)
            port = parts.port
        except ValueError:
            raise ModelError(f"Model lokalny: niepoprawny adres punktu „{self.name}”") from None
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ModelError(f"Model lokalny: niepoprawny adres punktu „{self.name}”")
        return parts.scheme, parts.hostname, port

    @property
    def host(self) -> str:
        """„127.0.0.1:11434” albo „[fd7a::1]:11434” — do komunikatów (bez danych logowania z adresu)."""
        try:
            _scheme, hostname, port = self.address()
        except ModelError:
            return self.name
        hostname = f"[{hostname}]" if ":" in hostname else hostname
        return hostname + (f":{port}" if port else "")

    def public_url(self) -> str:
        parts = urllib.parse.urlsplit(self.url)
        netloc = parts.netloc.rpartition("@")[2]
        return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))

    def headers(self) -> dict:
        """Nagłówki zapytania; token z pliku czytany teraz, tylko w pamięci tego wywołania."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token_file:
            try:
                with open(self.token_file, encoding="utf-8") as fh:
                    token = fh.read().strip()
            except (OSError, UnicodeDecodeError) as exc:
                raise ModelError(f"Model lokalny niedostępny: nie da się odczytać pliku tokenu punktu "
                                 f"„{self.name}” ({type(exc).__name__})") from None
            # Tylko drukowalne ASCII: nagłówek HTTP nie uniesie innych znaków (i nowej linii — wstrzyknięcia).
            if not token or not all(" " < c < "\x7f" for c in token):
                raise ModelError(f"Model lokalny niedostępny: pusty albo niepoprawny plik tokenu punktu „{self.name}”")
            headers["Authorization"] = f"Bearer {token}"
        return headers


@dataclass(frozen=True)
class Role:
    name: str
    endpoint: str
    model: str
    ctx: int | None          # None — kontekst serwera
    error: str = ""          # rola niedostępna (np. jej punkt jest pominięty) — powód po polsku


@dataclass(frozen=True)
class ModelsConfig:
    default: str
    endpoints: dict          # nazwa → Endpoint
    roles: dict              # nazwa → Role
    path: str = ""
    exists: bool = False
    error: str = ""          # plik nie do użycia (wtedy wszystko domyślne)
    warnings: tuple = field(default_factory=tuple)

    def endpoint(self, name: str | None = None) -> Endpoint:
        name = name or self.default
        if name not in self.endpoints:
            raise ModelError(f"Model lokalny: nieznany punkt „{name}” w models.json")
        return self.endpoints[name]

    def role(self, name: str) -> Role:
        if name not in self.roles:
            raise ModelError(f"Model lokalny: nieznana rola „{name}” w models.json")
        role = self.roles[name]
        if role.error:
            raise ModelError(f"Model lokalny niedostępny: {role.error}")
        return role

    def public(self) -> dict:
        """Stan pliku ustawień dla D-Bus (bez tokenów — tylko czy punkt go używa)."""
        return {"path": self.path, "exists": self.exists, "error": self.error, "warnings": list(self.warnings)}


def config_path(env=None) -> str:
    """$TIDE_MODELS_CONFIG (testy, diagnostyka), inaczej $XDG_CONFIG_HOME/tide/models.json."""
    env = os.environ if env is None else env
    if env.get("TIDE_MODELS_CONFIG"):
        return env["TIDE_MODELS_CONFIG"]
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "tide", "models.json")


def split_model(spec: str, endpoints) -> tuple[str | None, str]:
    """„local:qwen3:8b” → ("local", "qwen3:8b"); bez znanego punktu przed pierwszym „:” → (None, cały napis)."""
    head, sep, rest = spec.partition(":")
    if sep and head in endpoints and rest:
        return head, rest
    return None, spec


def _endpoint(name, value, warnings) -> Endpoint | None:
    if not isinstance(value, dict):
        warnings.append(f"punkt „{name}”: oczekiwano obiektu — pominięty")
        return None
    url = value.get("url")
    try:
        parts = urllib.parse.urlsplit(url.strip()) if isinstance(url, str) else None
        port = parts.port if parts else None
    except ValueError:  # „http://[fd7a::1:11434”, „http://mac:abc”, port spoza zakresu, IPv6 bez nawiasów
        parts, port = None, None
    if not parts or parts.scheme not in ("http", "https") or not parts.hostname or port == 0:
        warnings.append(f"punkt „{name}”: niepoprawny adres (oczekiwano http(s)://host[:port], "
                        f"IPv6 w nawiasach) — pominięty")
        return None
    if "@" in parts.netloc:
        warnings.append(f"punkt „{name}”: dane logowania w adresie — pominięty (token przez tokenFile)")
        return None
    kind = value.get("kind", "ollama")
    if kind not in KINDS:
        warnings.append(f"punkt „{name}”: nieznany rodzaj „{kind}” (ollama albo openai) — pominięty")
        return None
    base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).rstrip("/")
    if kind == "openai" and base.endswith("/v1"):
        base = base[:-3]  # adres podany razem z /v1 — ścieżki dokładamy sami
    token_file = value.get("tokenFile") or ""
    if not isinstance(token_file, str):
        warnings.append(f"punkt „{name}”: tokenFile musi być ścieżką — pominięty")
        return None
    return Endpoint(name, base, kind, os.path.expanduser(token_file) if token_file else "")


def _ctx(name, value, warnings) -> tuple[bool, int | None]:
    if value is None:
        return True, ROLE_CTX.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_CTX:
        warnings.append(f"rola „{name}”: ctx musi być liczbą całkowitą 0…{MAX_CTX} — pominięta")
        return False, None
    return True, value or None


def _role(name, value, endpoints, skipped, default, warnings) -> Role | None:
    ctx = None
    if isinstance(value, dict):
        ok, ctx = _ctx(name, value.get("ctx"), warnings)
        if not ok:
            return None
        value = value.get("model")
    else:
        ctx = ROLE_CTX.get(name)
    if not isinstance(value, str) or not value.strip():
        warnings.append(f"rola „{name}”: oczekiwano „punkt:model” — pominięta")
        return None
    endpoint, model = split_model(value.strip(), endpoints)
    if endpoint is None:
        head, _sep, rest = value.strip().partition(":")
        if head in skipped and rest:
            reason = f"rola „{name}”: punkt „{head}” jest pominięty w models.json"
            warnings.append(f"{reason} — rola niedostępna")
            return Role(name, head, rest, ctx, reason)
    return Role(name, endpoint or default, model, ctx)


def defaults(path: str = "", exists: bool = False, error: str = "", warnings=()) -> ModelsConfig:
    endpoints = {DEFAULT_ENDPOINT: Endpoint(DEFAULT_ENDPOINT, DEFAULT_URL, "ollama")}
    roles = {name: Role(name, DEFAULT_ENDPOINT, model, ROLE_CTX.get(name)) for name, model in DEFAULT_ROLES.items()}
    return ModelsConfig(DEFAULT_ENDPOINT, endpoints, roles, path, exists, error, tuple(warnings))


def from_dict(data, path: str = "", exists: bool = True) -> ModelsConfig:
    """Konfiguracja z obiektu pliku; złe części pomijane z ostrzeżeniem."""
    if not isinstance(data, dict):
        return defaults(path, exists, "models.json: oczekiwano obiektu JSON — ustawienia domyślne")
    warnings: list[str] = []
    endpoints: dict = {}
    raw_endpoints = data.get("endpoints")
    if raw_endpoints is not None and not isinstance(raw_endpoints, dict):
        warnings.append("endpoints: oczekiwano obiektu — punkt domyślny")
        raw_endpoints = None
    for name, value in (raw_endpoints or {}).items():
        if not NAME_RE.fullmatch(name):
            warnings.append(f"punkt „{name[:40]}”: niepoprawna nazwa (litery, cyfry, _.-) — pominięty")
            continue
        endpoint = _endpoint(name, value, warnings)
        if endpoint:
            endpoints[name] = endpoint
    if not endpoints:
        if raw_endpoints:
            warnings.append(f"żaden punkt nie jest poprawny — punkt domyślny {DEFAULT_ENDPOINT} ({DEFAULT_URL})")
        endpoints = {DEFAULT_ENDPOINT: Endpoint(DEFAULT_ENDPOINT, DEFAULT_URL, "ollama")}

    skipped = set(raw_endpoints or {}) - set(endpoints)

    default = data.get("default")
    fallback = DEFAULT_ENDPOINT if DEFAULT_ENDPOINT in endpoints else next(iter(endpoints))
    if default is None:
        default = fallback
    elif not isinstance(default, str):
        warnings.append(f"default: oczekiwano nazwy punktu — używam „{fallback}”")
        default = fallback
    elif default not in endpoints:
        warnings.append(f"default: nieznany punkt „{default[:40]}” — używam „{fallback}”")
        default = fallback

    roles: dict = {}
    raw_roles = data.get("roles")
    if raw_roles is not None and not isinstance(raw_roles, dict):
        warnings.append("roles: oczekiwano obiektu — role domyślne")
        raw_roles = None
    for name, value in (raw_roles or {}).items():
        if not NAME_RE.fullmatch(name):
            warnings.append(f"rola „{name[:40]}”: niepoprawna nazwa (litery, cyfry, _.-) — pominięta")
            continue
        role = _role(name, value, endpoints, skipped, default, warnings)
        if role:
            roles[name] = role
    for name, model in DEFAULT_ROLES.items():
        roles.setdefault(name, Role(name, default, model, ROLE_CTX.get(name)))
    return ModelsConfig(default, endpoints, roles, path, exists, "", tuple(warnings))


def load(path: str | None = None) -> ModelsConfig:
    """Ustawienia z pliku (czytane teraz); brak pliku — domyślne, zepsuty plik — domyślne z `error`."""
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return defaults(path)
    except ValueError as exc:
        line = f" (linia {exc.lineno})" if isinstance(exc, json.JSONDecodeError) else ""
        return defaults(path, True, f"models.json: niepoprawny JSON{line} — ustawienia domyślne")
    except OSError as exc:
        return defaults(path, True, f"models.json: nie da się odczytać ({type(exc).__name__}) — ustawienia domyślne")
    try:
        return from_dict(data, path)
    except Exception as exc:  # przeoczony kształt pliku nie może położyć usługi ani odczytu stanu
        return defaults(path, True, f"models.json: nieoczekiwany błąd ({type(exc).__name__}) — ustawienia domyślne")
