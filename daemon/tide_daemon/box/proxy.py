"""Proxy wyjściowe pudełka (PT-168, #5): jedyna droga pudełka do sieci i do Ollamy hosta.

Pudełko nie ma DNS ani NAT (statyczne nftables z `scripts/box/install.sh`, decyzja `nftables-statyczne`): z mostka
wolno tylko do tego proxy na `<adres mostka>:<box.proxy-port>`. Proxy obsługuje trzy rodzaje żądań:

- `CONNECT host:443` — tunel TCP bez MITM (proxy nie widzi treści TLS i niczego z niej nie zapisuje) do nazwy
  z polityki pudełka; dopasowanie dokładne albo `*.domena` (tylko poddomeny). Nazwę rozwiązuje proxy, a połączenie
  idzie na adres, który właśnie sprawdziło (bez drugiego rozwiązywania). Adres pętli zwrotnej, link-local,
  nieokreślony i multicast są zawsze odrzucane; adres prywatny tylko dla celu, który jest na liście pudełka i który
  profil uznał za wewnętrzny (pozycja czerwona przyjęta jawnie w profilu karty). Goły adres IP przechodzi tylko, gdy
  jest na liście dosłownie. Pełna sieć (`network.full` z jawną zgodą) otwiera tylko adresy publiczne — sieć
  lokalna i adres hosta na mostku nadal wymagają osobnej pozycji czerwonej na liście.
- zwykłe HTTP w formie względnej (`GET /api/tags`) albo bezwzględnej na adres samego proxy — przekazywane wyłącznie
  na `127.0.0.1:11434` (Ollama hosta), tylko dla pudełka z `ollama=True` i tylko ścieżki `/`, `/api/…`, `/v1/…`;
  odpowiedź płynie do pudełka bajt w bajt, bez buforowania (strumieniowanie Ollamy).
- K3 (PT-175, `box/k3.py`): `/anthropic/…` i `/chatgpt/…` w formie względnej (albo bezwzględnej na adres
  proxy) — jedno żądanie z atrapą kroku. Proxy sprawdza atrapę (adres IP pudełka, trasa, ważność) i ścieżkę
  (lista z P-3), wyrzuca nagłówki uwierzytelnienia z pudełka, wstawia token hosta czytany teraz z pliku i przekazuje
  żądanie po TLS wyłącznie do hosta trasy (`api.anthropic.com`, `chatgpt.com`). Treść idzie bez zmian według
  `Content-Length`/`chunked`; odpowiedź bajt w bajt. Zła atrapa → 403 `K3`, ścieżka spoza listy → 403 `K3PATH`,
  brak tokenu hosta → 502 (bez powrotu do poświadczeń w pudełku).
- każde inne zwykłe HTTP (`GET http://obca.domena/…`) → 403. Wariantu „zwykłe HTTP do domen z listy” nie ma —
  apt idzie przez HTTPS (decyzja `apt-przez-https` z P-6).

Polityka jest kluczowana adresem IP pudełka (`set_box_policy`). To bezpieczne tylko dlatego, że builder (#4) włącza
na NIC każdego pudełka `security.mac_filtering`/`ipv4_filtering`/`ipv6_filtering`: pudełko nie nada pakietu
z cudzym adresem. Proxy nie umie tego sprawdzić samo; dowód (podszycie pod IP innego pudełka) jest na czerwonej
liście #28. Nieznany adres = brak polityki = każde żądanie 403.

Odmowa = 403 i wiersz TSV w dzienniku odmów pudełka (`denied_log_path`, zwykle `proxy-denied.log` w katalogu
bieżącego kroku karty, przy budowie — ścieżka od buildera):

    czas<TAB>pudełko<TAB>adres IP<TAB>domena<TAB>port<TAB>rodzaj

`rodzaj`: `CONNECT` (cel spoza listy), `ADDRESS` (nazwa z listy, ale rozwiązana na adres niedozwolony — pętla,
sieć lokalna dla celu publicznego), `HTTP` (zwykłe HTTP do obcej domeny), `OLLAMA` (domena `host-ollama`:
pudełko bez `ollama=True`), `K3` (brak, zła albo wygasła atrapa; domena = host trasy) albo `K3PATH` (ścieżka
spoza listy trasy). Nigdy ścieżka, parametry, nagłówki, atrapa ani treść — tylko nazwa hosta.

Usługa nie woła `nft` ani `sudo`. Opis: docs/tide-daemon.md (Pudełko → Proxy wyjściowe).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import k3
from . import profile as box_profile
from . import templates

# Domeny modeli dokładane przez usługę (nie przez profil). Bez zdalnych repozytoriów i adresów wewnętrznych —
# pilnuje test. `chatgpt.com` i `auth.openai.com` są potrzebne Codexowi z własnym logowaniem w pudełku (K1);
# z K3 (PT-175) Codex idzie przez trasę proxy `/chatgpt` i `chatgpt.com` może zniknąć z tej listy — to zmiana
# listy domen, poza PT-175 (decyzja `k3-wdrozone` w docs/box-decisions.md).
MODEL_DOMAINS = ("api.anthropic.com", "statsig.anthropic.com", "api.openai.com", "chatgpt.com", "auth.openai.com")

CONNECT_PORTS = frozenset({443})
OLLAMA = ("127.0.0.1", 11434)
OLLAMA_PATHS = ("/api/", "/v1/")
HEAD_LIMIT = 64 * 1024
HEAD_TIMEOUT = 30.0
CONNECT_TIMEOUT = 30.0
RESOLVE_TIMEOUT = 15.0
OLLAMA_ITEM = "host-ollama"
# Nagłówki „hop-by-hop” i pośrednika: nie idą do Ollamy (Host i Connection ustawiamy sami).
HOP_HEADERS = frozenset({"connection", "keep-alive", "proxy-connection", "proxy-authorization", "proxy-authenticate",
                         "te", "trailer", "upgrade", "host"})
# Nagłówki uwierzytelnienia z pudełka: na trasach K3 nigdy nie idą dalej (zastępuje je token hosta).
AUTH_HEADERS = frozenset({"authorization", "x-api-key", "chatgpt-account-id", "cookie"})
CHUNK_LINE_LIMIT = 4096

_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")

_running = None   # usługa uruchomiona przez start_service (dla #18 i buildera z #4)


class ProxyError(RuntimeError):
    """Proxy nie wystartuje (zły adres nasłuchu, brak mostka, zajęty port) — opis po polsku."""


class Refused(Exception):
    """Cel połączenia odrzucony po rozwiązaniu nazwy (adres pętli, prywatny dla nazwy publicznej…)."""


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _stderr(message: str) -> None:
    print(f"tide-daemon: proxy pudełka: {message}", file=sys.stderr, flush=True)


# ------------------------------------------------------------ dopasowanie domen

# Linia żądania (RFC 9110 `token`, RFC 9112 `request-target`, `HTTP-version`) i rozmiar chunka (`chunk-size`).
_METHOD = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_TARGET = re.compile(r"[\x21-\x7e]+")
_VERSION = re.compile(r"HTTP/1\.[01]")
_CHUNK_SIZE = re.compile(rb"[0-9A-Fa-f]{1,16}")


def _ip(text: str):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def normalize_target(host: str) -> str | None:
    """Kanoniczna postać celu żądania albo `None`, gdy to nie jest ani nazwa hosta, ani adres IP.

    Małe litery, bez nawiasów IPv6 i bez jednej kropki końcowej. Nazwa z liczbową albo szesnastkową ostatnią
    etykietą (`10.1`, `3232235786`, `0xc0a80001`) nie jest nazwą — resolver zrobiłby z niej adres IPv4."""
    host = str(host).strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    ip = _ip(host)
    if ip is not None:
        return str(ip)
    if host.endswith("."):
        host = host[:-1]
    if not host or len(host) > 253:
        return None
    labels = host.split(".")
    if not all(_LABEL.match(label) for label in labels):
        return None
    last = labels[-1]
    if last.isdigit() or last.startswith("0x"):
        return None
    return host


class DomainMatcher:
    """Lista domen pudełka: nazwy dokładne, `*.domena` (tylko poddomeny) i dosłowne adresy IP."""

    def __init__(self, domains=()):
        exact, suffixes, ips, skipped = set(), set(), set(), []
        for entry in domains:
            raw = str(entry).strip().lower()
            wildcard = raw.startswith("*.")
            name = normalize_target(raw[2:] if wildcard else raw)
            if name is None or (wildcard and _ip(name) is not None):
                skipped.append(str(entry))
            elif _ip(name) is not None:
                ips.add(name)
            elif wildcard:
                suffixes.add("." + name)
            else:
                exact.add(name)
        self.exact, self.suffixes, self.ips = frozenset(exact), tuple(sorted(suffixes)), frozenset(ips)
        self.skipped = tuple(skipped)

    def allows(self, host: str) -> bool:
        name = normalize_target(host)
        if name is None:
            return False
        if _ip(name) is not None:
            return name in self.ips
        return name in self.exact or name.endswith(self.suffixes)


def address_allowed(address: str, internal: bool) -> bool:
    """Czy wolno połączyć się z adresem rozwiązanym dla nazwy z listy.

    Pętla zwrotna, link-local, nieokreślony, multicast i zarezerwowane — nigdy (to host i jego usługi). Prywatne
    i 100.64/10 — tylko gdy cel jest na liście i wewnętrzny według profilu (`internal`, `BoxPolicy.internal`),
    czyli przyjęty jawnie w profilu karty;
    nazwa publiczna, która nagle wskazuje do sieci lokalnej, nie przechodzi."""
    ip = _ip(address)
    if ip is None:
        return False
    if ip.is_loopback or ip.is_unspecified or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        return False
    if ip.is_private or (ip.version == 4 and ip in _CGNAT):
        return internal
    return True


# ------------------------------------------------------------ polityka pudełka

@dataclass(frozen=True)
class BoxPolicy:
    ip: str
    box: str
    matcher: DomainMatcher
    ollama: bool
    denied_log: str
    full: bool = False

    def allows(self, host: str) -> bool:
        if self.full:
            return normalize_target(host) is not None
        return self.matcher.allows(host)

    def internal(self, host: str) -> bool:
        """Czy cel może prowadzić do adresu prywatnego: tylko gdy jest na liście pudełka (przyjęty w profilu)
        i profil uznaje go za wewnętrzny. Pełna sieć (`full`) nie otwiera sieci lokalnej — cel spoza listy
        jest zawsze publiczny, więc adres prywatny/100.64/10 (także adres hosta na mostku) daje odmowę."""
        return self.matcher.allows(host) and bool(box_profile.internal_hosts([host]))


@dataclass(frozen=True)
class ComposedPolicy:
    """Wejście `set_box_policy` złożone z profili: domeny, Ollama hosta, pełna sieć i uwagi do dziennika."""
    domains: tuple
    ollama: bool
    full: bool
    notes: tuple


def compose_policy(project: dict | None, cards=None, known: dict | None = None, log=_stderr) -> ComposedPolicy:
    """Polityka pudełka = domeny modeli + `base` + szablony wg `stacks` + `network.domains` profilu projektu
    + profile aktywnych kart (`cards`: słownik karta → profil karty).

    Profil karty jest scalany z profilem projektu przez `profile.merge`, więc jego czerwone pozycje wchodzą tylko
    z `accepted_red`. `network.full` karty bez `network.full-consent: true` jest pomijane z wpisem w dzienniku
    (`log`, domyślnie dziennik usługi), reszta karty działa. Usługa sama dokłada wyłącznie `MODEL_DOMAINS`
    i domeny szablonów (`templates.build_domains` — ta sama funkcja co w builderze)."""
    project = box_profile.validate(project or {"tide-box": box_profile.SCHEMA_VERSION}, "project")
    known = templates.known_domains() if known is None else known
    domains = list(MODEL_DOMAINS) + list(templates.build_domains(project))
    ollama = bool((project.get("network") or {}).get("host-ollama"))
    full = False
    notes = []
    for card_id, card in (cards or {}).items():
        card = dict(card or {})
        net = dict(card.get("network") or {})
        if net.get("full") and net.get("full-consent") is not True:
            notes.append(f"karta {card_id}: network.full bez jawnej zgody (network.full-consent: true) — pominięte")
            net.pop("full", None)
            net.pop("full-consent", None)
            card["network"] = net
        try:
            merged = box_profile.merge(project, card, known)
        except box_profile.ProfileError as exc:
            notes.append(f"karta {card_id}: profil karty pominięty ({exc})")
            continue
        for item in merged.dropped_red:
            notes.append(f"karta {card_id}: pozycja czerwona {item!r} bez accepted_red — pominięta")
        domains += templates.build_domains(merged.profile)
        mnet = merged.profile.get("network") or {}
        ollama = ollama or bool(mnet.get("host-ollama"))
        full = full or (mnet.get("full") is True and mnet.get("full-consent") is True)
    for note in notes:
        log(note)
    return ComposedPolicy(tuple(dict.fromkeys(domains)), ollama, full, tuple(notes))


def _clean(value, limit: int = 255) -> str:
    text = "".join(c if " " <= c < "\x7f" else "?" for c in str(value))
    return text[:limit] or "?"


def append_denied(path: str, box: str, ip: str, host: str, port, kind: str) -> None:
    """Dopisuje wiersz odmowy (plik 0600, katalog tworzony w razie potrzeby)."""
    line = "\t".join(_clean(f) for f in (_now(), box or ip, ip, host, port, kind)) + "\n"
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode("ascii"))
    finally:
        os.close(fd)


# ------------------------------------------------------------ adres mostka

def _run(run, argv):
    try:
        done = run(argv, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, type(exc).__name__
    if done.returncode != 0:
        lines = (done.stderr or "").strip().splitlines()
        return None, _clean(lines[-1] if lines else f"kod {done.returncode}", 160)
    return done.stdout or "", ""


def bridge_address(bridge: str = "incusbr0", run=subprocess.run, log=None) -> str | None:
    """Adres IPv4 mostka Incusa (bez maski); `None`, gdy brak.

    Najpierw `incus network get <mostek> ipv4.address`. Z projektu ograniczonego (`incus-user`) to polecenie bywa
    niedostępne (próba ręczna PT-168), więc zapasem jest adres interfejsu w jądrze: `ip -j -4 addr show dev
    <mostek>` — to ten sam adres, który nftables z PT-166 wpuszczają jako cel z mostka. Zapas przyjmuje tylko
    interfejs, który jądro zna jako mostek (`ip -j -d link show`: `linkinfo.info_kind == "bridge"`), więc
    `box.bridge: wlan0` albo `eth0` nie przeniesie nasłuchu na adres sieci lokalnej."""
    out, why = _run(run, ["incus", "network", "get", bridge, "ipv4.address"])
    if out is not None:
        try:
            return str(ipaddress.ip_interface(out.strip()).ip)
        except ValueError:
            why = f"nieczytelny adres {_clean(out.strip(), 60)!r}"
    if log is not None:
        log(f"incus network get {bridge} ipv4.address: {why} — czytam adres interfejsu")
    exists, kind = _link_kind(bridge, run)
    if not exists:
        if log is not None:
            log(f"ip link show dev {bridge}: brak interfejsu")
        return None
    if kind != "bridge":
        raise ProxyError(f"interfejs {bridge} nie jest mostkiem ({kind or 'zwykła karta sieciowa'}) — "
                         "proxy słucha tylko na mostku Incusa")
    out, why = _run(run, ["ip", "-j", "-4", "addr", "show", "dev", bridge])
    if out is None:
        if log is not None:
            log(f"ip addr show dev {bridge}: {why}")
        return None
    try:
        for link in json.loads(out or "[]"):
            for info in link.get("addr_info") or []:
                if info.get("family") == "inet" and info.get("scope", "global") == "global":
                    ip = ipaddress.ip_address(info.get("local", ""))
                    if ip.version == 4:
                        return str(ip)
    except (ValueError, AttributeError, TypeError):
        pass
    return None


def _link_kind(name: str, run) -> tuple[bool, str | None]:
    """(czy interfejs istnieje, rodzaj z `ip -j -d link show dev <nazwa>`: `bridge`, `veth`…; `None` — zwykła
    karta albo nieczytelna odpowiedź)."""
    out, _why = _run(run, ["ip", "-j", "-d", "link", "show", "dev", name])
    if out is None:
        return False, None
    try:
        for link in json.loads(out or "[]"):
            if link.get("ifname") == name:
                kind = (link.get("linkinfo") or {}).get("info_kind")
                return True, (str(kind) if kind else None)
    except (ValueError, AttributeError, TypeError):
        return True, None
    return False, None


def check_listen(listen: str, bridge: str | None) -> str:
    """Adres nasłuchu musi być adresem mostka Incusa — nigdy `0.0.0.0`, `::` ani pętla zwrotna."""
    ip = _ip(str(listen))
    if ip is None:
        raise ProxyError(f"adres nasłuchu {listen!r} nie jest adresem IP")
    if ip.is_unspecified or ip.is_loopback:
        raise ProxyError(f"proxy słucha tylko na adresie mostka Incusa, nie na {ip}")
    if bridge is None or _ip(bridge) != ip:
        raise ProxyError(f"adres nasłuchu {ip} nie jest adresem mostka Incusa ({bridge or 'brak mostka'})")
    return str(ip)


# ------------------------------------------------------------ serwer

async def _default_connector(host: str, port: int, internal: bool):
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), RESOLVE_TIMEOUT)
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    allowed = [a for a in addresses if address_allowed(a, internal)]
    if not allowed:
        raise Refused(host)
    error = None
    for address in allowed:
        try:
            return await asyncio.wait_for(asyncio.open_connection(address, port), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as exc:
            error = exc
    raise error or OSError("brak adresu")


async def _default_k3_connector(route: k3.Route):
    """Połączenie z hostem trasy K3: adres sprawdzony jak dla CONNECT (cel publiczny); TLS klienta z weryfikacją
    certyfikatu robi `k3.open_upstream` — moduł proxy nie ma TLS (żadnego przechwytywania)."""
    try:
        return await k3.open_upstream(route, lambda a: address_allowed(a, False), RESOLVE_TIMEOUT, CONNECT_TIMEOUT)
    except k3.UpstreamRefused:
        raise Refused(route.host) from None


class BadRequest(Exception):
    """Żądanie K3 z niejednoznaczną długością treści (oba `Content-Length` i `Transfer-Encoding` itp.)."""


class ProxyService:
    """Serwer proxy w osobnym wątku z własną pętlą asyncio; polityki zmieniane z dowolnego wątku.

    Konstruktor nie sprawdza adresu nasłuchu — robi to `start_service` (jedyna droga w usłudze). Testy tworzą
    serwer wprost na 127.0.0.1 z własnym `connector` i `ollama`."""

    def __init__(self, listen: str, port: int, *, ollama=OLLAMA, connector=None, log=_stderr,
                 grants: k3.GrantRegistry | None = None, k3_connector=None):
        self.listen = listen
        self.port = port
        self.ollama_target = ollama
        self.connector = connector or _default_connector
        self.log = log
        # Atrapy kroków (K3): krok w pudełku woła `grants.issue(...)` na starcie i `grants.revoke_step(...)` na końcu.
        self.grants = grants if grants is not None else k3.GrantRegistry()
        self.k3_connector = k3_connector or _default_k3_connector
        self._policies: dict[str, BoxPolicy] = {}
        self._lock = threading.Lock()
        self._loop = None
        self._server = None
        self._thread = None
        self._open: set = set()

    # --- polityka (dowolny wątek) ----------------------------------------------------------------------

    def set_box_policy(self, ip: str, domains, ollama: bool, denied_log_path: str, *, full: bool = False,
                       box: str = "") -> BoxPolicy:
        """Ustawia (albo zastępuje) politykę pudełka o adresie `ip`; działa od następnego połączenia.

        `domains` — pełna lista (zwykle `compose_policy(...).domains`), `ollama` — Ollama hosta przez proxy,
        `denied_log_path` — dziennik odmów, `full` — pełna sieć (tylko z jawnej zgody w profilu karty),
        `box` — nazwa pudełka do dziennika. Otwarte już tunele nie są przerywane."""
        key = _ip(str(ip))
        if key is None:
            raise ValueError(f"adres pudełka {ip!r} nie jest adresem IP")
        if not denied_log_path:
            raise ValueError("polityka pudełka wymaga ścieżki dziennika odmów")
        matcher = DomainMatcher(domains)
        for entry in matcher.skipped:
            self.log(f"{box or key}: pozycja {_clean(entry)!r} nie jest nazwą hosta ani adresem IP — pominięta")
        policy = BoxPolicy(str(key), str(box), matcher, bool(ollama), str(denied_log_path), bool(full))
        with self._lock:
            self._policies = {**self._policies, policy.ip: policy}
        return policy

    def clear_box_policy(self, ip: str) -> None:
        key = str(_ip(str(ip)) or ip)
        with self._lock:
            self._policies = {k: v for k, v in self._policies.items() if k != key}

    def policy(self, ip: str) -> BoxPolicy | None:
        key = _ip(str(ip))
        return self._policies.get(str(key)) if key is not None else None

    # --- cykl życia -----------------------------------------------------------------------------------

    def start(self) -> "ProxyService":
        ready = threading.Event()
        failure = []

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                self._server = loop.run_until_complete(
                    asyncio.start_server(self._handle, self.listen, self.port, limit=HEAD_LIMIT))
            except OSError as exc:
                failure.append(exc)
                loop.close()
                ready.set()
                return
            self.port = self._server.sockets[0].getsockname()[1]
            ready.set()
            try:
                loop.run_forever()
            finally:
                tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in tasks:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                loop.close()

        self._thread = threading.Thread(target=run, name="tide-box-proxy", daemon=True)
        self._thread.start()
        if not ready.wait(10) or failure:
            reason = type(failure[0]).__name__ + f": {failure[0]}" if failure else "brak odpowiedzi"
            raise ProxyError(f"nie da się słuchać na {self.listen}:{self.port} ({reason})")
        return self

    def stop(self, timeout: float = 5.0) -> None:
        global _running
        if _running is self:
            _running = None
        loop = self._loop
        if loop is None or self._thread is None or loop.is_closed():
            return

        def shutdown():
            if self._server is not None:
                self._server.close()
            for writer in list(self._open):
                writer.close()
            loop.stop()

        loop.call_soon_threadsafe(shutdown)
        self._thread.join(timeout)

    # --- obsługa połączenia (wątek proxy) -------------------------------------------------------------

    async def _handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        client = str(_ip(peer[0]) or peer[0]) if peer else "?"
        self._open.add(writer)
        try:
            await self._serve(reader, writer, client)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
            pass
        except Exception as exc:  # noqa: BLE001 — błąd jednego połączenia nie może zatrzymać proxy
            self.log(f"błąd połączenia z {client}: {type(exc).__name__}")   # bez treści: mogłaby nieść żądanie
        finally:
            self._open.discard(writer)
            writer.close()

    async def _serve(self, reader, writer, client):
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
        lines = head[:-4].decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        headers = []
        for line in lines[1:]:
            name, sep, _value = line.partition(":")
            if not sep or not name or name != name.strip() or "\n" in line or "\r" in line:
                return await self._reply(writer, 400, "Bad Request")
            headers.append(line)
        # Linia żądania idzie (przy K3 — razem z tokenem hosta) do upstreamu: metoda tylko jako token HTTP, cel
        # tylko z widocznego ASCII. Goły LF/CR/TAB w zapytaniu dopisałby nagłówki przed wstawionymi przez proxy.
        # Cel CONNECT nigdzie nie jest przepisywany (idzie przez `normalize_target` i do dziennika odmów po
        # oczyszczeniu), więc zostaje przy dotychczasowej ścieżce odmowy 403 z wpisem w dzienniku.
        if (len(parts) != 3 or not _VERSION.fullmatch(parts[2]) or not _METHOD.fullmatch(parts[0])
                or (parts[0] != "CONNECT" and not _TARGET.fullmatch(parts[1]))):
            return await self._reply(writer, 400, "Bad Request")
        method, target, _version = parts
        policy = self.policy(client)
        if method == "CONNECT":
            return await self._connect(reader, writer, client, policy, target)
        if target.startswith("/"):
            return await self._local(reader, writer, client, policy, method, target, headers)
        try:
            url = urlsplit(target)
            host, port = url.hostname or "", url.port or (80 if url.scheme == "http" else 443)
        except ValueError:
            return await self._reply(writer, 400, "Bad Request")
        if url.scheme == "http" and _ip(host) == _ip(self.listen) and port == self.port:
            path = (url.path or "/") + (f"?{url.query}" if url.query else "")
            return await self._local(reader, writer, client, policy, method, path, headers)
        # Zwykłe HTTP do obcej domeny: zawsze odmowa (tunel tylko przez CONNECT, bez MITM).
        return await self._deny(writer, client, policy, host, port, "HTTP")

    async def _deny(self, writer, client, policy, host, port, kind):
        shown = normalize_target(host) or _clean(host, 80)
        if policy is None:
            self.log(f"{client}: brak polityki pudełka — odmowa {kind} {shown}:{port}")
        else:
            try:
                append_denied(policy.denied_log, policy.box, client, shown, port, kind)
            except OSError as exc:
                self.log(f"{policy.box or client}: nie da się zapisać dziennika odmów ({type(exc).__name__})")
        if kind == "K3":
            text = "Tide proxy: brak ważnej atrapy kroku dla tej trasy (K3)\n"
        elif kind == "K3PATH":
            text = "Tide proxy: ścieżka spoza listy trasy K3\n"
        elif kind == "OLLAMA":
            text = "Tide proxy: Ollama hosta niedostępna dla tego pudełka (host-ollama)\n"
        elif kind == "ADDRESS":
            text = f"Tide proxy: {shown}:{port} wskazuje adres niedozwolony (host albo sieć lokalna)\n"
        else:
            text = f"Tide proxy: {shown}:{port} spoza listy domen pudełka\n"
        await self._reply(writer, 403, "Forbidden", text)

    async def _reply(self, writer, code: int, reason: str, body: str = ""):
        data = body.encode("ascii", "replace")
        writer.write(f"HTTP/1.1 {code} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                     f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode("ascii") + data)
        await writer.drain()

    async def _connect(self, reader, writer, client, policy, target):
        host, sep, port_s = target.rpartition(":")
        if not sep or not host or not port_s.isdigit():
            return await self._reply(writer, 400, "Bad Request")
        port = int(port_s)
        name = normalize_target(host)
        if policy is None or name is None or port not in CONNECT_PORTS or not policy.allows(name):
            return await self._deny(writer, client, policy, host, port, "CONNECT")
        try:
            up_reader, up_writer = await self.connector(name, port, policy.internal(name))
        except Refused:
            # Nazwa jest na liście, ale wskazuje adres niedozwolony — osobny rodzaj, żeby nikt nie dopisywał
            # domeny, która już jest na liście.
            return await self._deny(writer, client, policy, host, port, "ADDRESS")
        except (OSError, asyncio.TimeoutError) as exc:
            return await self._reply(writer, 502, "Bad Gateway", f"Tide proxy: {name}:{port}: {type(exc).__name__}\n")
        self._open.add(up_writer)
        try:
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(self._copy(reader, up_writer), self._copy(up_reader, writer))
        finally:
            self._open.discard(up_writer)
            up_writer.close()

    async def _local(self, reader, writer, client, policy, method, path, headers):
        """Żądanie do samego proxy: trasa K3 (`/anthropic/…`, `/chatgpt/…`) albo Ollama hosta."""
        name, sep, _rest = path[1:].partition("/")
        route = self.grants.routes.get(name.split("?", 1)[0]) if name else None
        if route is not None and k3.k3_active(route.program):
            return await self._inject(reader, writer, client, policy, method, route, path[1 + len(name):] or "/",
                                      headers)
        return await self._ollama(reader, writer, client, policy, method, path, headers)

    async def _inject(self, reader, writer, client, policy, method, route, rest, headers):
        """K3: jedno żądanie z atrapą kroku → host trasy z tokenem hosta. Zmieniane są wyłącznie nagłówki
        uwierzytelnienia (plus beta OAuth Claude, bez której token subskrypcyjny nie działa) i nagłówki
        połączenia (`Host`, `Connection: close`); linia żądania, pozostałe nagłówki i treść — bez zmian."""
        if policy is None:
            return await self._deny(writer, client, policy, route.host, route.port, "K3")
        keys = set()
        for line in headers:
            name, _sep, value = line.partition(":")
            lname, value = name.strip().lower(), value.strip()
            if lname == "authorization":
                scheme, _sp, token = value.partition(" ")
                keys.add(token.strip() if scheme.lower() == "bearer" else value)
            elif lname == "x-api-key":
                keys.add(value)
        grant = self.grants.check(keys.pop(), client, route.name) if len(keys) == 1 else None
        if grant is None:
            return await self._deny(writer, client, policy, route.host, route.port, "K3")
        bare, qmark, query = rest.partition("?")
        if not k3.path_allowed(route, bare):
            return await self._deny(writer, client, policy, route.host, route.port, "K3PATH")
        if qmark and not k3.query_allowed(query):
            return await self._reply(writer, 400, "Bad Request")
        try:
            framing = self._framing(headers)
        except BadRequest:
            return await self._reply(writer, 400, "Bad Request", "Tide proxy: niejednoznaczna długość treści\n")
        try:
            auth = k3.auth_headers(route, now=self.grants.wall_clock)
        except k3.TokenError as exc:
            # Bez cichego powrotu do K1: krok dostaje błąd, użytkownik — wpis w dzienniku usługi (bez tokenu).
            self.log(f"{policy.box or client}: K3 {route.name}: {exc}")
            return await self._reply(writer, 502, "Bad Gateway", f"Tide proxy: token hosta ({route.name}): {exc}\n")
        drop = set(HOP_HEADERS) | AUTH_HEADERS
        for line in headers:
            name, _sep, value = line.partition(":")
            if name.strip().lower() == "connection":
                drop |= {v.strip().lower() for v in value.split(",") if v.strip()}
        kept, beta = [], False
        for line in headers:
            lname = line.partition(":")[0].strip().lower()
            if lname in drop:
                continue
            if lname == "anthropic-beta" and route.program == "claude":
                beta = True
                values = [v.strip() for v in line.partition(":")[2].split(",") if v.strip()]
                if k3.OAUTH_BETA not in values:
                    line = f"{line.partition(':')[0]}: {','.join(values + [k3.OAUTH_BETA])}"
            kept.append(line)
        if route.program == "claude" and not beta:
            kept.append(f"anthropic-beta: {k3.OAUTH_BETA}")
        host = route.host if route.port == (443 if route.tls else 80) else f"{route.host}:{route.port}"
        request = [f"{method} {route.base}{rest} HTTP/1.1", *kept, *(f"{n}: {v}" for n, v in auth),
                   f"Host: {host}", "Connection: close"]
        try:
            up_reader, up_writer = await self.k3_connector(route)
        except Refused:
            return await self._deny(writer, client, policy, route.host, route.port, "ADDRESS")
        except (OSError, asyncio.TimeoutError) as exc:      # także ssl.SSLError (podklasa OSError)
            return await self._reply(writer, 502, "Bad Gateway", f"Tide proxy: {route.host}: {type(exc).__name__}\n")
        self._open.add(up_writer)
        try:
            up_writer.write(("\r\n".join(request) + "\r\n\r\n").encode("latin-1"))
            await self._body(reader, up_writer, framing)
            await up_writer.drain()
            # Odpowiedź bajt w bajt do końca strumienia (upstream dostał `Connection: close`), bez buforowania SSE.
            await self._copy(up_reader, writer, close=False)
        finally:
            self._open.discard(up_writer)
            up_writer.close()

    @staticmethod
    def _framing(headers):
        """Długość treści żądania: `("length", n)`, `("chunked", None)` albo `("none", None)`. Oba nagłówki naraz,
        różne `Content-Length` albo inne kodowanie niż samo `chunked` → `BadRequest` (bez przemytu żądań)."""
        lengths, encodings = set(), []
        for line in headers:
            name, _sep, value = line.partition(":")
            lname = name.strip().lower()
            if lname == "content-length":
                lengths.add(value.strip())
            elif lname == "transfer-encoding":
                encodings += [v.strip().lower() for v in value.split(",") if v.strip()]
        if lengths and encodings:
            raise BadRequest()
        if encodings:
            if encodings != ["chunked"]:
                raise BadRequest()
            return "chunked", None
        if len(lengths) > 1:
            raise BadRequest()
        if lengths:
            value = lengths.pop()
            if not value.isdigit():
                raise BadRequest()
            return "length", int(value)
        return "none", None

    @staticmethod
    async def _body(reader, dst, framing):
        """Przepisuje treść jednego żądania bez zmian (także ramki `chunked`); nic poza nią nie idzie dalej."""
        kind, length = framing
        if kind == "length":
            while length > 0:
                data = await reader.read(min(length, 65536))
                if not data:
                    raise asyncio.IncompleteReadError(b"", length)
                dst.write(data)
                length -= len(data)
                await dst.drain()
        elif kind == "chunked":
            while True:
                line = await reader.readuntil(b"\r\n")
                if len(line) > CHUNK_LINE_LIMIT:
                    raise asyncio.LimitOverrunError("chunk", len(line))
                size_text = line[:-2].split(b";", 1)[0]
                # Tylko cyfry szesnastkowe (RFC 9112 `chunk-size`): `int()` przyjąłby też `0x5`, `+0`, `0_5`
                # i spacje, które ścisły parser upstreamu policzyłby inaczej (przemyt żądań).
                if not _CHUNK_SIZE.fullmatch(size_text):
                    raise asyncio.IncompleteReadError(b"", None)
                size = int(size_text, 16)
                dst.write(line)
                if size == 0:
                    while True:        # nagłówki końcowe aż do pustej linii
                        trailer = await reader.readuntil(b"\r\n")
                        dst.write(trailer)
                        if trailer == b"\r\n":
                            break
                    await dst.drain()
                    return
                left = size
                while left > 0:
                    part = await reader.readexactly(min(left, 65536))
                    dst.write(part)
                    left -= len(part)
                    await dst.drain()
                if await reader.readexactly(2) != b"\r\n":
                    raise asyncio.IncompleteReadError(b"", 2)
                dst.write(b"\r\n")
                await dst.drain()

    async def _ollama(self, reader, writer, client, policy, method, path, headers):
        if policy is None or not policy.ollama:
            return await self._deny(writer, client, policy, OLLAMA_ITEM, self.ollama_target[1], "OLLAMA")
        bare = path.split("?", 1)[0]
        if bare != "/" and not bare.startswith(OLLAMA_PATHS):
            return await self._reply(writer, 404, "Not Found", "Tide proxy: nieznana trasa\n")
        drop = set(HOP_HEADERS)
        for line in headers:
            name, _sep, value = line.partition(":")
            if name.strip().lower() == "connection":
                drop |= {v.strip().lower() for v in value.split(",") if v.strip()}
        kept = [h for h in headers if h.partition(":")[0].strip().lower() not in drop]
        host, port = self.ollama_target
        try:
            up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as exc:
            return await self._reply(writer, 502, "Bad Gateway", f"Tide proxy: Ollama hosta: {type(exc).__name__}\n")
        self._open.add(up_writer)
        upload = None
        try:
            request = [f"{method} {path} HTTP/1.1", *kept, f"Host: {host}:{port}", "Connection: close"]
            up_writer.write(("\r\n".join(request) + "\r\n\r\n").encode("latin-1"))
            await up_writer.drain()
            # Reszta strumienia klienta idzie surowo (także potokowane kolejne żądanie, bez sprawdzenia ścieżki).
            # Dziś nieszkodliwe: cel to zawsze tylko Ollama z `Connection: close`. Trasy z wstrzykiwaniem tokenu
            # (PT-175, `_inject`) kopiują treść według `Content-Length`/`chunked`, nie „do końca strumienia”.
            upload = asyncio.ensure_future(self._copy(reader, up_writer, close=False))
            await self._copy(up_reader, writer, close=False)   # odpowiedź bez buforowania, do końca strumienia
        finally:
            if upload is not None:
                upload.cancel()
            self._open.discard(up_writer)
            up_writer.close()

    @staticmethod
    async def _copy(src, dst, close: bool = True):
        try:
            while data := await src.read(65536):
                dst.write(data)
                await dst.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            if close:
                try:
                    if dst.can_write_eof():
                        dst.write_eof()
                    else:
                        dst.close()
                except (OSError, RuntimeError):
                    dst.close()


# ------------------------------------------------------------ start z usługą

def start_service(settings, *, listen: str | None = None, run=subprocess.run, which=shutil.which,
                  log=_stderr, factory=ProxyService) -> ProxyService | None:
    """Proxy pudełka razem z usługą: tylko gdy `box.enabled` i mostek Incusa istnieje; inaczej `None`
    i usługa działa jak przed pudełkiem. Adres nasłuchu = adres mostka (`listen`, jeśli podany, musi mu być
    równy). Zły adres → `ProxyError`; zajęty port → `ProxyError`."""
    global _running
    if not settings.enabled:
        return None
    for warning in settings.warnings:
        log(warning)
    if which("incus") is None:
        log("box.enabled, ale nie ma polecenia incus — proxy pudełka nie startuje")
        return None
    bridge = bridge_address(settings.bridge, run, log)
    if bridge is None:
        log(f"box.enabled, ale mostek {settings.bridge} nie istnieje albo nie ma adresu IPv4 — proxy nie startuje")
        return None
    address = check_listen(listen if listen is not None else bridge, bridge)
    service = factory(address, settings.proxy_port, log=log).start()
    log(f"słucha na {address}:{service.port}")
    _running = service
    return service


def running() -> ProxyService | None:
    """Proxy uruchomione przez `start_service` w tej usłudze (dla kroku w pudełku i buildera)."""
    return _running
