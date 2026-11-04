"""K3 (PT-175, #12): pudełko bez poświadczeń agentów — atrapa per krok, token wstawia proxy Tide na hoście.

Wynik P-3 (`docs/box-prototype.md`, decyzje `k3-claude-mozliwe`, `k3-codex-mozliwe`): K3 jest możliwe dla
Claude Code (`ANTHROPIC_BASE_URL` + `CLAUDE_CODE_OAUTH_TOKEN`) i dla Codexa (własny dostawca modelu `tide`
+ `chatgpt_base_url`). `k3_active(program)` to jedyne miejsce, z którego krok w pudełku, `tide box login`, dziennik
karty (#18) i pakiet zgód (#9) czytają, czy program działa z K3.

Przepływ jednego kroku:

1. krok w pudełku (PT-181) woła `GrantRegistry.issue(step, ip pudełka, program)` → atrapa (losowa, ważna tylko dla
   tego kroku, tego adresu IP pudełka i jednej trasy),
2. `step_credentials(...)` daje zmienne do `in/env` (base URL na proxy + atrapa) i — dla Codexa — treść
   `~/.codex/auth.json` z samymi atrapami (Codex bez pliku nie startuje; plik nie niesie poświadczeń),
3. proxy (`box/proxy.py`) dla `/anthropic/…` i `/chatgpt/…` sprawdza atrapę (adres IP, trasa, ważność), wyrzuca
   nagłówki uwierzytelnienia z pudełka i wstawia prawdziwe, czytane **przy każdym żądaniu** z pliku hosta
   (`~/.claude/.credentials.json`, `~/.codex/auth.json` hosta — poza katalogiem domowym pudełka, nigdy montowane),
   więc token odświeżony na hoście działa bez restartu kroku; pudełko nigdy nie odświeża tokenu,
4. po kroku `revoke_step(step)`; atrapa po czasie `ttl` wygasa także bez tego.

Token nigdy nie trafia do dziennika, odpowiedzi ani pudełka: błędy niosą tylko rodzaj (`TokenError`).
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field

# Wynik P-3 dla programów agentów. Zmiana na False przywraca K1 (osobne logowanie w pudełku) tylko dla tego programu.
K3_PROGRAMS = {"claude": True, "codex": True}

DEFAULT_TTL = 24 * 3600.0          # zapas: krok kończy się revoke_step, atrapa i tak nie żyje dłużej
OAUTH_BETA = "oauth-2025-04-20"    # token subskrypcyjny Claude wymaga tej bety (P-3)


def k3_active(program: str) -> bool:
    """Czy program agenta działa w pudełku z K3 (atrapa + proxy wstrzykujące), a nie z własnym logowaniem (K1)."""
    return bool(K3_PROGRAMS.get(str(program), False))


def login_programs(programs=("claude", "codex")) -> tuple:
    """Programy, które `tide box login` ma zalogować w obrazie (K1) — wszystkie bez K3."""
    return tuple(p for p in programs if not k3_active(p))


class TokenError(RuntimeError):
    """Brak albo nieczytelny token hosta. Komunikat bez treści pliku — tylko rodzaj problemu."""


# ------------------------------------------------------------ trasy

@dataclass(frozen=True)
class Route:
    """Trasa proxy wstrzykującego: `/<name>/<ścieżka>` z pudełka → `https://<host><base><ścieżka>`.

    `paths` — dozwolone ścieżki (po `/<name>`): wpis kończący się `/` to przedrostek, inny — dokładna ścieżka.
    Lista pochodzi z dziennika P-3; reszta → 403 (bez ogólnego dostępu do API konta: „confused deputy”)."""
    name: str
    program: str
    host: str
    port: int
    tls: bool
    base: str
    paths: tuple
    token_file: str


def _home(env=None) -> str:
    env = os.environ if env is None else env
    return env.get("HOME") or os.path.expanduser("~")


def default_routes(env=None) -> dict:
    home = _home(env)
    return {
        "anthropic": Route("anthropic", "claude", "api.anthropic.com", 443, True, "",
                           ("/v1/messages", "/v1/messages/count_tokens"),
                           os.path.join(home, ".claude", ".credentials.json")),
        "chatgpt": Route("chatgpt", "codex", "chatgpt.com", 443, True, "",
                         ("/backend-api/codex/", "/backend-api/wham/accounts/check", "/backend-api/wham/settings/user",
                          "/backend-api/ps/plugins/", "/backend-api/ps/mcp", "/backend-api/plugins/featured"),
                         os.path.join(home, ".codex", "auth.json")),
    }


PROGRAM_ROUTE = {"claude": "anthropic", "codex": "chatgpt"}

_PATH = re.compile(r"/[A-Za-z0-9._~/-]*")
_QUERY = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=:@/?%-]*")


def path_allowed(route: Route, path: str) -> bool:
    """Ścieżka (bez zapytania) z listy trasy. Bez `..`, `//`, `%` i znaków spoza bezpiecznego zestawu — żeby
    `/v1/messages/../organizations` czy `%2e%2e` nie wyszły poza listę po normalizacji po stronie serwera."""
    if not _PATH.fullmatch(path) or "//" in path or any(seg in (".", "..") for seg in path.split("/")):
        return False
    return any(path.startswith(p) if p.endswith("/") else path == p for p in route.paths)


def query_allowed(query: str) -> bool:
    """Zapytanie (bez `?`) tylko z bezpiecznego zestawu RFC 3986 — bez spacji, CR/LF/TAB, `#` i znaków spoza ASCII."""
    return _QUERY.fullmatch(query) is not None


class UpstreamRefused(Exception):
    """Host trasy rozwiązany wyłącznie na adresy niedozwolone (pętla, sieć lokalna)."""


async def open_upstream(route: Route, address_ok, resolve_timeout: float, connect_timeout: float):
    """Połączenie klienta z hostem trasy: nazwa rozwiązana tutaj, adres sprawdzony przez `address_ok`, połączenie na
    ten sam adres; TLS z domyślną weryfikacją certyfikatu i nazwy `route.host`. Proxy po stronie pudełka nie ma
    TLS — to jedyne miejsce z `ssl` w pudełkowej części usługi, i tylko jako klient."""
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(loop.getaddrinfo(route.host, route.port, type=socket.SOCK_STREAM), resolve_timeout)
    allowed = [a for a in dict.fromkeys(info[4][0] for info in infos) if address_ok(a)]
    if not allowed:
        raise UpstreamRefused(route.host)
    context = ssl.create_default_context() if route.tls else None
    error = None
    for address in allowed:
        try:
            return await asyncio.wait_for(asyncio.open_connection(
                address, route.port, ssl=context, server_hostname=route.host if context else None), connect_timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            error = exc
    raise error or OSError("brak adresu")


# ------------------------------------------------------------ token hosta

def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise TokenError("brak pliku tokenu na hoście") from None
    except (OSError, ValueError, UnicodeError) as exc:
        raise TokenError(f"nieczytelny plik tokenu ({type(exc).__name__})") from None


def auth_headers(route: Route, now=time.time) -> list:
    """Nagłówki uwierzytelnienia dla trasy, z pliku hosta czytanego teraz (host sam odświeża token)."""
    data = _read_json(route.token_file)
    if route.program == "claude":
        oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
        token = oauth.get("accessToken") if isinstance(oauth, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise TokenError("plik tokenu Claude bez claudeAiOauth.accessToken")
        expires = oauth.get("expiresAt")
        if isinstance(expires, (int, float)) and not isinstance(expires, bool) and expires / 1000 <= now():
            raise TokenError("token Claude na hoście wygasł — odśwież go na hoście (dowolne `claude` w Konsoli)")
        return [("Authorization", f"Bearer {token.strip()}")]
    tokens = data.get("tokens") if isinstance(data, dict) else None
    token = tokens.get("access_token") if isinstance(tokens, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise TokenError("plik tokenu Codexa bez tokens.access_token")
    headers = [("Authorization", f"Bearer {token.strip()}")]
    account = _account(tokens)[0]
    if account:
        headers.append(("chatgpt-account-id", account))
    return headers


def _claims(jwt) -> dict:
    try:
        part = str(jwt).split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return data if isinstance(data, dict) else {}
    except (IndexError, ValueError, UnicodeError):
        return {}


def codex_account(route: Route) -> tuple:
    """(konto, plan) z `auth.json` hosta; bez pliku — puste konto (żądania przez proxy i tak dostaną błąd tokenu)."""
    try:
        data = _read_json(route.token_file)
    except TokenError:
        return "", "plus"
    tokens = data.get("tokens") if isinstance(data, dict) else None
    return _account(tokens) if isinstance(tokens, dict) else ("", "plus")


def _account(tokens: dict) -> tuple:
    """(identyfikator konta, plan) z `auth.json` hosta — identyfikatory, nie poświadczenia."""
    auth = _claims(tokens.get("id_token", "")).get("https://api.openai.com/auth") or {}
    account = tokens.get("account_id") or auth.get("chatgpt_account_id") or ""
    plan = auth.get("chatgpt_plan_type") or "plus"
    return (str(account) if isinstance(account, str) else ""), (str(plan) if isinstance(plan, str) else "plus")


# ------------------------------------------------------------ atrapy per krok

@dataclass(frozen=True)
class Grant:
    step: str
    ip: str
    route: str
    key: str = field(repr=False)
    expires: float = 0.0


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()


class GrantRegistry:
    """Atrapy aktywnych kroków. Atrapa = losowe 256 bitów; przechowywany jest tylko jej skrót (słownik po skrócie).

    Atrapa działa wyłącznie: z adresu IP pudełka, dla którego ją wydano, na jednej trasie (programu kroku), do
    `revoke_step` albo upływu `ttl`. Wszystko inne → `None` (proxy odpowiada 403)."""

    def __init__(self, clock=time.monotonic, routes: dict | None = None, wall_clock=time.time):
        self.clock = clock
        self.wall_clock = wall_clock     # czas epoki do sprawdzania `expiresAt` tokenu hosta (`auth_headers`)
        self.routes = routes if routes is not None else default_routes()
        self._grants: dict[str, Grant] = {}
        self._lock = threading.Lock()

    def issue(self, step: str, ip: str, program: str, ttl: float = DEFAULT_TTL) -> Grant:
        if not k3_active(program):
            raise ValueError(f"program {program!r} nie działa z K3")
        if not step:
            raise ValueError("atrapa wymaga identyfikatora kroku")
        try:
            address = ipaddress.ip_address(str(ip))
        except ValueError:
            raise ValueError(f"adres pudełka {ip!r} nie jest adresem IP") from None
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped      # jak `_ip` w proxy: `::ffff:a.b.c.d` → `a.b.c.d`
        route = PROGRAM_ROUTE[program]
        token = secrets.token_urlsafe(32)
        if program == "claude":
            # Claude Code przyjmuje token subskrypcyjny w postaci `sk-ant-oat01-…` (P-3).
            key = f"sk-ant-oat01-tide-k3-{token}"
        else:
            # Codex porównuje konto z atrapy z listą kont z `wham/accounts/check` (P-3, przebieg 3), więc atrapa
            # niesie prawdziwy identyfikator konta i plan hosta (identyfikatory, nie poświadczenia).
            key = _dummy_jwt(token, *codex_account(self.routes["chatgpt"]))
        grant = Grant(str(step), str(address), route, key, self.clock() + float(ttl))
        with self._lock:
            self._grants[_digest(key)] = grant
        return grant

    def revoke_step(self, step: str) -> int:
        with self._lock:
            kept = {k: g for k, g in self._grants.items() if g.step != str(step)}
            removed = len(self._grants) - len(kept)
            self._grants = kept
        return removed

    def check(self, key: str, ip: str, route: str) -> Grant | None:
        if not key:
            return None
        now = self.clock()
        with self._lock:
            self._grants = {k: g for k, g in self._grants.items() if g.expires > now}
            grant = self._grants.get(_digest(key))
        if grant is None or grant.ip != str(ip) or grant.route != route:
            return None
        return grant

    def active(self) -> int:
        now = self.clock()
        with self._lock:
            return sum(1 for g in self._grants.values() if g.expires > now)


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()


def _dummy_jwt(nonce: str, account: str = "", plan: str = "plus") -> str:
    claims = {"email": "box@tide.invalid", "exp": 4102444800, "tide_k3": nonce,
              "https://api.openai.com/auth": {"chatgpt_plan_type": plan, "chatgpt_account_id": account}}
    return f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}.atrapa"


# ------------------------------------------------------------ środowisko kroku

@dataclass(frozen=True)
class StepCredentials:
    """Co krok w pudełku przekazuje agentowi: zmienne do `in/env`, pliki w katalogu domowym pudełka (ścieżka
    względna → treść) i argumenty programu. Same atrapy i adresy — żadnego prawdziwego poświadczenia."""
    env: dict
    files: dict
    args: tuple


def step_credentials(grant: Grant, proxy_base: str) -> StepCredentials:
    """Środowisko kroku dla atrapy `grant`; `proxy_base` = `http://<mostek>:<box.proxy-port>`."""
    base = proxy_base.rstrip("/")
    if grant.route == "anthropic":
        return StepCredentials({"ANTHROPIC_BASE_URL": f"{base}/anthropic", "CLAUDE_CODE_OAUTH_TOKEN": grant.key},
                               {}, ())
    account = (_claims(grant.key).get("https://api.openai.com/auth") or {}).get("chatgpt_account_id", "")
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    # `access_token` = atrapa sprawdzana przez proxy; `refresh_token` bezwartościowy — Codex w pudełku nie odświeża
    # (exp atrapy w 2100 r.), a `auth.openai.com` dla K3 nie jest potrzebne (P-3: 0 żądań).
    auth = {"OPENAI_API_KEY": None, "tokens": {"id_token": grant.key, "access_token": grant.key,
                                               "refresh_token": "tide-k3-atrapa", "account_id": account},
            "last_refresh": now}
    chatgpt = f"{base}/chatgpt/backend-api/"
    args = ("-c", f'chatgpt_base_url="{chatgpt}"', "-c", 'model_provider="tide"',
            "-c", f'model_providers.tide={{ name = "tide", base_url = "{base}/chatgpt/backend-api/codex", '
                  'wire_api = "responses", requires_openai_auth = true }')
    return StepCredentials({}, {".codex/auth.json": json.dumps(auth)}, args)
