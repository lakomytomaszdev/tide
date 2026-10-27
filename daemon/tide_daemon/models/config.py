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
Tak samo zły `default` (nieznany, pominięty, nie napis): role bez punktu są niedostępne, a `default`
w stanie to zapasowy punkt (`local` albo pierwszy) — tylko po to, żeby było co odczytywać.

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
    # Token podany wprost (tylko TestEndpoint: próba przed zapisem) — w pamięci, nigdy w repr ani w stanie.
    token: str = field(default="", repr=False, compare=False)

    def address(self) -> tuple[str, str, int | None]:
        """(schemat, host, port) z adresu; adres, którego nie da się rozebrać → ModelError."""
        try:
            parts = urllib.parse.urlsplit(self.url)
            port = parts.port
        except ValueError:
            raise ModelError(f"Model lokalny: niepoprawny adres serwera „{self.name}”") from None
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ModelError(f"Model lokalny: niepoprawny adres serwera „{self.name}”")
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
        if self.token:
            if not valid_token(self.token):
                raise ModelError(f"Model lokalny: niepoprawny token serwera „{self.name}”")
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.token_file:
            try:
                with open(self.token_file, encoding="utf-8") as fh:
                    token = fh.read().strip()
            except (OSError, UnicodeDecodeError) as exc:
                raise ModelError(f"Model lokalny niedostępny: nie da się odczytać pliku tokenu serwera "
                                 f"„{self.name}” ({type(exc).__name__})") from None
            if not valid_token(token):
                raise ModelError(f"Model lokalny niedostępny: pusty albo niepoprawny plik tokenu serwera „{self.name}”")
            headers["Authorization"] = f"Bearer {token}"
        return headers


def valid_token(token: str) -> bool:
    """Tylko drukowalne ASCII bez spacji: nagłówek HTTP nie uniesie innych znaków (i nowej linii — wstrzyknięcia)."""
    return bool(token) and all(" " < c < "\x7f" for c in token)


@dataclass(frozen=True)
class Role:
    name: str
    endpoint: str
    model: str
    ctx: int | None          # None — kontekst serwera
    error: str = ""          # rola niedostępna (np. jej punkt jest pominięty) — powód po polsku
    pinned: bool = False     # „punkt:model” w pliku — przypięta jawnie (także do obecnego `default`)


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
            raise ModelError(f"Model lokalny: nieznany serwer „{name}” w models.json")
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
        warnings.append(f"serwer „{name}”: oczekiwano obiektu — pominięty")
        return None
    url = value.get("url")
    try:
        parts = urllib.parse.urlsplit(url.strip()) if isinstance(url, str) else None
        port = parts.port if parts else None
    except ValueError:  # „http://[fd7a::1:11434”, „http://mac:abc”, port spoza zakresu, IPv6 bez nawiasów
        parts, port = None, None
    if not parts or parts.scheme not in ("http", "https") or not parts.hostname or port == 0:
        warnings.append(f"serwer „{name}”: niepoprawny adres (oczekiwano http(s)://host[:port], "
                        f"IPv6 w nawiasach) — pominięty")
        return None
    if "@" in parts.netloc:
        warnings.append(f"serwer „{name}”: dane logowania w adresie (token przez tokenFile) — pominięty")
        return None
    kind = value.get("kind", "ollama")
    if kind not in KINDS:
        warnings.append(f"serwer „{name}”: nieznany rodzaj „{str(kind)[:40]}” (ollama albo openai) — pominięty")
        return None
    base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).rstrip("/")
    if kind == "openai" and base.endswith("/v1"):
        base = base[:-3]  # adres podany razem z /v1 — ścieżki dokładamy sami
    token_file = value.get("tokenFile") or ""
    if not isinstance(token_file, str):
        warnings.append(f"serwer „{name}”: tokenFile musi być ścieżką — pominięty")
        return None
    return Endpoint(name, base, kind, os.path.expanduser(token_file) if token_file else "")


def _ctx(name, value, warnings) -> tuple[bool, int | None]:
    if value is None:
        return True, ROLE_CTX.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_CTX:
        warnings.append(f"rola „{name}”: ctx musi być liczbą całkowitą 0…{MAX_CTX} — pominięta")
        return False, None
    return True, value or None


def _role(name, value, endpoints, skipped, default, unreachable, warnings) -> Role | None:
    ctx = None
    if isinstance(value, dict):
        ok, ctx = _ctx(name, value.get("ctx"), warnings)
        if not ok:
            return None
        value = value.get("model")
    else:
        ctx = ROLE_CTX.get(name)
    if not isinstance(value, str) or not value.strip():
        warnings.append(f"rola „{name}”: oczekiwano „serwer:model” — pominięta")
        return None
    endpoint, model = split_model(value.strip(), endpoints)
    if endpoint is None:
        head, _sep, rest = value.strip().partition(":")
        if head in skipped and rest:
            reason = f"rola „{name}”: serwer „{head}” jest pominięty w models.json"
            warnings.append(f"{reason} — rola niedostępna")
            return Role(name, head, rest, ctx, reason, pinned=True)
    if endpoint is None and unreachable:
        return Role(name, unreachable[0], model, ctx, unreachable[1])
    return Role(name, endpoint or default, model, ctx, pinned=endpoint is not None)


def bad_name(what: str, name) -> str:
    """Komunikat o złej nazwie punktu albo roli — ten sam przy odczycie pliku i przy zapisie z D-Bus."""
    return f"{what} „{str(name)[:40]}”: niepoprawna nazwa (litery, cyfry, _.-)"


def _refusal(warnings: list) -> str:
    """Ostrzeżenie z odczytu pliku jako powód odmowy zapisu (bez dopisku „— pominięty”)."""
    text = warnings[-1] if warnings else "niepoprawne ustawienie"
    return re.sub(r" — pominięt[ya]$", "", text)


def check_endpoint(name, value) -> tuple[Endpoint | None, str]:
    """Punkt tak, jak przeczytałby go load(): (Endpoint, "") albo (None, powód po polsku).

    Zapis z D-Bus (models/settings.py) i odczyt pliku mają jedną walidację: ten sam kod i te same komunikaty."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return None, bad_name("serwer", name)
    warnings: list[str] = []
    endpoint = _endpoint(name, value, warnings)
    return endpoint, "" if endpoint else _refusal(warnings)


def check_role(name, value, cfg: "ModelsConfig") -> tuple[Role | None, str]:
    """Rola tak, jak przeczytałby ją load() przy punktach z `cfg`: (Role, "") albo (None, powód po polsku)."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return None, bad_name("rola", name)
    warnings: list[str] = []
    role = _role(name, value, cfg.endpoints, set(), cfg.default, None, warnings)
    return role, "" if role else _refusal(warnings)


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
        warnings.append("endpoints: oczekiwano obiektu — serwer domyślny")
        raw_endpoints = None
    for name, value in (raw_endpoints or {}).items():
        if not NAME_RE.fullmatch(name):
            warnings.append(f"{bad_name('serwer', name)} — pominięty")
            continue
        endpoint = _endpoint(name, value, warnings)
        if endpoint:
            endpoints[name] = endpoint
    if not endpoints:
        if raw_endpoints:
            warnings.append(f"żaden serwer nie jest poprawny — serwer domyślny {DEFAULT_ENDPOINT} ({DEFAULT_URL})")
        endpoints = {DEFAULT_ENDPOINT: Endpoint(DEFAULT_ENDPOINT, DEFAULT_URL, "ollama")}

    skipped = set(raw_endpoints or {}) - set(endpoints)

    # Zły `default` nie przenosi ról bez punktu na inny serwer: są niedostępne z powodem (jak rola na
    # pominiętym punkcie). `default` w stanie to wtedy zapasowy punkt — tylko do odczytu stanu.
    default = data.get("default")
    fallback = DEFAULT_ENDPOINT if DEFAULT_ENDPOINT in endpoints else next(iter(endpoints))
    unreachable = None
    if default is None:
        default = fallback
    elif not isinstance(default, str) or default not in endpoints:
        if not isinstance(default, str):
            name, reason = "default", "default: oczekiwano nazwy serwera"
        elif default in skipped:
            name, reason = default, f"default: serwer „{default[:40]}” jest pominięty w models.json"
        else:
            name, reason = default[:40], f"default: nieznany serwer „{default[:40]}”"
        warnings.append(f"{reason} — role bez serwera niedostępne")
        unreachable = (name, reason)
        default = fallback

    roles: dict = {}
    raw_roles = data.get("roles")
    if raw_roles is not None and not isinstance(raw_roles, dict):
        warnings.append("roles: oczekiwano obiektu — role domyślne")
        raw_roles = None
    for name, value in (raw_roles or {}).items():
        if not NAME_RE.fullmatch(name):
            warnings.append(f"{bad_name('rola', name)} — pominięta")
            continue
        role = _role(name, value, endpoints, skipped, default, unreachable, warnings)
        if role:
            roles[name] = role
    for name, model in DEFAULT_ROLES.items():
        if unreachable:
            roles.setdefault(name, Role(name, unreachable[0], model, ROLE_CTX.get(name), unreachable[1]))
        else:
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
