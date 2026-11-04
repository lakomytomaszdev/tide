#!/usr/bin/env python3
"""Prototypowe proxy wyjściowe pudełka (PT-178, próby P-2, P-3, P-6 z docs/card-box-design.md).

To NIE jest kod usługi (ten powstaje w #5, `tide_daemon/box/proxy.py`) — mały skrypt do prób uruchamiany przez
użytkownika na adresie mostka Incusa. Jeden port obsługuje trzy rodzaje żądań:

- `CONNECT host:443` — tunel bez MITM do domeny z listy; spoza listy → 403 i wiersz w dzienniku odmów,
- `GET http://host/…` (forma bezwzględna) — zwykłe HTTP do domeny z listy (wariant awaryjny apt, decyzja
  w docs/box-decisions.md); spoza listy → 403 i wiersz w dzienniku odmów,
- `POST /anthropic/…`, `/chatgpt/…` (forma względna, `ANTHROPIC_BASE_URL`/`chatgpt_base_url` wskazuje proxy) —
  proxy wstrzykujące (K3, P-3): nagłówki uwierzytelnienia z pudełka (atrapa) są wyrzucane, proxy wstawia token
  z pliku hosta poza repozytorium i przekazuje żądanie po HTTPS do api.anthropic.com / chatgpt.com.

Lista domen to plik (jedna domena w wierszu, `#` komentarz, `*.example.com` albo `.example.com` = poddomeny);
proxy czyta go ponownie, gdy zmieni się czas modyfikacji — skrypty prób uzupełniają listę w trakcie.

Dzienniki (TSV, bez nagłówków żądań i bez tokenów):
- dziennik odmów: `czas  domena  port  klient  metoda`,
- dziennik dostępu (opcjonalny): `czas  klient  rodzaj  cel  wynik` dla każdego żądania — po nim próba NO_PROXY
  poznaje, że żądanie do localhost nie przeszło przez proxy.

Nasłuch tylko na podanym adresie (`--listen`, bez 0.0.0.0/::). Plik tokenu musi leżeć poza repozytorium.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import ipaddress
import json
import os
import signal
import ssl
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
HEAD_LIMIT = 64 * 1024
# Trasy proxy wstrzykującego: prefiks ścieżki → domyślny upstream (nadpisywany --upstream w testach).
ROUTES = {"anthropic": "https://api.anthropic.com", "chatgpt": "https://chatgpt.com"}
# Nagłówki uwierzytelnienia z pudełka, których proxy nigdy nie przekazuje dalej.
AUTH_HEADERS = {"authorization", "x-api-key", "chatgpt-account-id", "proxy-authorization", "cookie"}
HOP_HEADERS = {"connection", "keep-alive", "proxy-connection", "te", "trailer", "upgrade", "host"}
OAUTH_BETA = "oauth-2025-04-20"


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class DomainList:
    """Lista domen z pliku, czytana ponownie po zmianie mtime."""

    def __init__(self, path: Path):
        self.path = path
        self.mtime = None
        self.exact: set[str] = set()
        self.suffixes: tuple[str, ...] = ()

    def _reload(self):
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            mtime = -1
        if mtime == self.mtime:
            return
        self.mtime = mtime
        exact, suffixes = set(), []
        if mtime != -1:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                item = line.split("#", 1)[0].strip().lower().rstrip(".")
                if not item:
                    continue
                if item.startswith("*."):
                    suffixes.append(item[1:])
                elif item.startswith("."):
                    suffixes.append(item)
                else:
                    exact.add(item)
        self.exact, self.suffixes = exact, tuple(suffixes)

    def allows(self, host: str) -> bool:
        self._reload()
        host = host.lower().rstrip(".")
        return host in self.exact or (bool(self.suffixes) and host.endswith(self.suffixes))


def read_token(route: str, path: Path) -> dict[str, str]:
    """Nagłówki do wstawienia dla trasy; plik czytany przy każdym żądaniu (host sam odświeża token)."""
    text = path.read_text(encoding="utf-8").strip()
    data = None
    if text.startswith("{"):
        data = json.loads(text)
    if route == "anthropic":
        token = data["claudeAiOauth"]["accessToken"] if data else text
        return {"Authorization": f"Bearer {token}"}
    if not data:
        raise ValueError("plik tokenu chatgpt to auth.json Codexa (JSON z tokens.access_token)")
    tokens = data["tokens"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    if tokens.get("account_id"):
        headers["chatgpt-account-id"] = tokens["account_id"]
    return headers


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def repo_roots(repo: Path) -> list[Path]:
    """Worktree ze skryptem i główny checkout: w worktree `.git` to plik `gitdir: <główne>/.git/worktrees/<nazwa>`."""
    roots = [repo]
    git = repo / ".git"
    if git.is_file():
        text = git.read_text(errors="replace").strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text.partition(":")[2].strip())
            if not gitdir.is_absolute():
                gitdir = repo / gitdir
            gitdir = gitdir.resolve()
            if gitdir.parent.name == "worktrees":
                roots.append(gitdir.parent.parent.parent)
    return roots


class Proxy:
    def __init__(self, listen: str, port: int, domains: DomainList, deny_log: Path, access_log: Path | None,
                 connect_ports: set[int], http_ports: set[int], inject: dict[str, Path],
                 upstreams: dict[str, str]):
        self.listen, self.port = listen, port
        self.domains, self.deny_log, self.access_log = domains, deny_log, access_log
        self.connect_ports, self.http_ports = connect_ports, http_ports
        self.inject = inject
        self.upstreams = {**ROUTES, **upstreams}
        self.server: asyncio.base_events.Server | None = None
        # Otwarte strumienie (klienci i upstreamy) — zamykane przy zatrzymaniu, bo tunel CONNECT nie ma limitu czasu,
        # a Server.wait_closed() w 3.12 czeka na koniec wszystkich połączeń (review PT-178 runda 2, M-1).
        self.open: set[asyncio.StreamWriter] = set()

    # --- dzienniki ------------------------------------------------------------------------------------

    def _append(self, path: Path | None, fields: list[str]):
        if path is None:
            return
        clean = [str(f).replace("\t", " ").replace("\n", " ") for f in fields]
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\t".join(clean) + "\n")

    def denied(self, host: str, port: int, client: str, method: str):
        self._append(self.deny_log, [now(), host, str(port), client, method])

    def access(self, client: str, kind: str, target: str, result: str):
        self._append(self.access_log, [now(), client, kind, target, result])

    # --- obsługa połączenia -----------------------------------------------------------------------------

    async def start(self):
        self.server = await asyncio.start_server(self.handle, self.listen, self.port, limit=HEAD_LIMIT)
        return self.server

    async def shutdown(self, grace: float = 2.0) -> bool:
        """Zamyka nasłuch i wszystkie otwarte połączenia; False, gdy serwer nie zamknął się w `grace` s."""
        if self.server is None:
            return True
        self.server.close()
        for w in list(self.open):
            w.close()
        try:
            await asyncio.wait_for(self.server.wait_closed(), grace)
            return True
        except asyncio.TimeoutError:
            return False

    def track(self, writer: asyncio.StreamWriter) -> asyncio.StreamWriter:
        self.open.add(writer)
        return writer

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        client = peer[0] if peer else "?"
        self.track(writer)
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
            lines = head.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) != 3:
                await self.reply(writer, 400, "Bad Request")
                return
            method, target, version = parts
            headers = [line for line in lines[1:] if line]
            if method == "CONNECT":
                await self.connect(reader, writer, client, target)
            elif target.startswith("http://"):
                await self.forward(reader, writer, client, method, target, version, headers)
            elif target.startswith("/"):
                await self.reverse(reader, writer, client, method, target, version, headers)
            else:
                await self.reply(writer, 400, "Bad Request")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
            pass
        except Exception as exc:  # noqa: BLE001 — prototyp: błąd połączenia nie może zatrzymać proxy
            # Tylko typ wyjątku (jak w reverse()) — treść mogłaby nieść fragment żądania, a proxy.out trafia do repo.
            print(f"{now()} błąd {client}: {type(exc).__name__}", file=sys.stderr, flush=True)
        finally:
            self.open.discard(writer)
            writer.close()

    async def reply(self, writer, code: int, reason: str, body: str = ""):
        data = body.encode()
        writer.write(f"HTTP/1.1 {code} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                     f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
        await writer.drain()

    def permitted(self, host: str) -> bool:
        # Adres IP przechodzi tylko wtedy, gdy jest na liście tak samo jak nazwa (pudełko nie ma DNS —
        # nazwy rozwiązuje proxy, więc klient zwykle podaje nazwę).
        return self.domains.allows(host.strip("[]"))

    async def connect(self, reader, writer, client: str, target: str):
        host, _, port_s = target.rpartition(":")
        if not host or not port_s.isdigit():
            await self.reply(writer, 400, "Bad Request")
            return
        port = int(port_s)
        if port not in self.connect_ports or not self.permitted(host):
            self.denied(host, port, client, "CONNECT")
            self.access(client, "CONNECT", f"{host}:{port}", "403")
            await self.reply(writer, 403, "Forbidden", f"Tide proxy: {host}:{port} spoza listy domen\n")
            return
        try:
            up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host.strip("[]"), port), 30)
        except (OSError, asyncio.TimeoutError) as exc:
            self.access(client, "CONNECT", f"{host}:{port}", "502")
            await self.reply(writer, 502, "Bad Gateway", f"Tide proxy: {host}:{port}: {exc}\n")
            return
        self.track(up_writer)
        try:
            self.access(client, "CONNECT", f"{host}:{port}", "200")
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await self.pipe(reader, writer, up_reader, up_writer)
        finally:
            self.open.discard(up_writer)
            up_writer.close()

    async def forward(self, reader, writer, client, method, target, version, headers):
        url = urlsplit(target)
        host, port = url.hostname or "", url.port or 80
        if port not in self.http_ports or not self.permitted(host):
            self.denied(host, port, client, method)
            self.access(client, f"HTTP {method}", f"{host}:{port}", "403")
            await self.reply(writer, 403, "Forbidden", f"Tide proxy: {host}:{port} spoza listy domen\n")
            return
        path = (url.path or "/") + (f"?{url.query}" if url.query else "")
        kept = [h for h in headers if h.split(":", 1)[0].strip().lower() not in HOP_HEADERS | {"proxy-authorization"}]
        await self.relay(reader, writer, client, f"HTTP {method}", host, port, None,
                         f"{method} {path} {version}", kept + [f"Host: {url.netloc}"])

    async def reverse(self, reader, writer, client, method, target, version, headers):
        route, _, rest = target[1:].partition("/")
        if route not in self.inject:
            self.access(client, f"INJECT {method}", target.split("?", 1)[0], "404")
            await self.reply(writer, 404, "Not Found", "Tide proxy: nieznana trasa\n")
            return
        upstream = urlsplit(self.upstreams[route])
        try:
            extra = read_token(route, self.inject[route])
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            # Komunikat bez treści pliku: tylko rodzaj błędu.
            self.access(client, f"INJECT {method}", f"/{route}", "500")
            await self.reply(writer, 500, "Internal Server Error", f"Tide proxy: plik tokenu {route}: {type(exc).__name__}\n")
            return
        kept = []
        beta = None
        for h in headers:
            name = h.split(":", 1)[0].strip().lower()
            if name in AUTH_HEADERS or name in HOP_HEADERS:
                continue
            if name == "anthropic-beta":
                beta = h
                continue
            kept.append(h)
        if route == "anthropic":
            # Token subskrypcyjny (OAuth) wymaga bety oauth; klient z kluczem API (x-api-key) jej nie wysyła.
            values = [v.strip() for v in beta.split(":", 1)[1].split(",")] if beta else []
            if OAUTH_BETA not in values:
                values.append(OAUTH_BETA)
            kept.append("anthropic-beta: " + ",".join(v for v in values if v))
        kept += [f"{k}: {v}" for k, v in extra.items()]
        kept.append(f"Host: {upstream.netloc}")
        base = upstream.path.rstrip("/")
        port = upstream.port or (443 if upstream.scheme == "https" else 80)
        tls = ssl.create_default_context() if upstream.scheme == "https" else None
        await self.relay(reader, writer, client, f"INJECT {method}", upstream.hostname, port, tls,
                         f"{method} {base}/{rest} {version}", kept, label=f"/{route}/{rest.split('?', 1)[0]}")

    async def relay(self, reader, writer, client, kind, host, port, tls, request_line, headers, label=None):
        """Jedno żądanie do upstreamu z `Connection: close`; odpowiedź strumieniowo (SSE) do klienta."""
        target = label or f"{host}:{port}"
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=tls, server_hostname=host if tls else None), 30)
        except (OSError, asyncio.TimeoutError) as exc:
            self.access(client, kind, target, "502")
            await self.reply(writer, 502, "Bad Gateway", f"Tide proxy: {host}:{port}: {type(exc).__name__}\n")
            return
        self.track(up_writer)
        upload = None
        try:
            up_writer.write(("\r\n".join([request_line, *headers, "Connection: close"]) + "\r\n\r\n").encode("latin-1"))
            await up_writer.drain()
            upload = asyncio.ensure_future(self.copy(reader, up_writer, close=False))
            status_head = await up_reader.readuntil(b"\r\n\r\n")
            status_lines = status_head.decode("latin-1").split("\r\n")
            code = status_lines[0].split(" ")[1] if len(status_lines[0].split(" ")) > 1 else "?"
            self.access(client, kind, target, code)
            out = [status_lines[0]] + [h for h in status_lines[1:] if h
                                       and h.split(":", 1)[0].strip().lower() not in {"connection", "keep-alive"}]
            writer.write(("\r\n".join(out + ["Connection: close"]) + "\r\n\r\n").encode("latin-1"))
            await writer.drain()
            await self.copy(up_reader, writer, close=False)
        finally:
            if upload is not None:
                upload.cancel()
            self.open.discard(up_writer)
            up_writer.close()

    async def copy(self, src: asyncio.StreamReader, dst: asyncio.StreamWriter, close: bool = True):
        try:
            while data := await src.read(65536):
                dst.write(data)
                await dst.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            if close:
                dst.close()

    async def pipe(self, r1, w1, r2, w2):
        await asyncio.gather(self.copy(r1, w2), self.copy(r2, w1))


def parse_ports(text: str) -> set[int]:
    return {int(p) for p in text.split(",") if p.strip()}


def build(argv: list[str]) -> Proxy:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--listen", required=True, help="adres nasłuchu (adres mostka); 0.0.0.0 i :: odrzucone")
    p.add_argument("--port", type=int, default=18080, help="port (domyślnie 18080 = box.proxy-port)")
    p.add_argument("--domains", required=True, type=Path, help="plik listy domen (może nie istnieć = pusta)")
    p.add_argument("--deny-log", required=True, type=Path, help="dziennik odmów (TSV)")
    p.add_argument("--access-log", type=Path, help="dziennik wszystkich żądań (TSV, bez nagłówków)")
    p.add_argument("--connect-ports", default="443", help="porty dozwolone dla CONNECT (domyślnie 443)")
    p.add_argument("--http-ports", default="80", help="porty dozwolone dla zwykłego HTTP (domyślnie 80)")
    p.add_argument("--inject", action="append", default=[], metavar="TRASA=PLIK",
                   help="proxy wstrzykujące: anthropic=~/.claude/.credentials.json, chatgpt=~/.codex/auth.json")
    p.add_argument("--upstream", action="append", default=[], metavar="TRASA=URL",
                   help="upstream trasy (domyślnie api.anthropic.com / chatgpt.com; dla testów)")
    a = p.parse_args(argv)
    try:
        addr = ipaddress.ip_address(a.listen)
    except ValueError:
        p.error(f"--listen: adres IP, nie nazwa ({a.listen})")
    if addr.is_unspecified:
        p.error("--listen: proxy słucha tylko na adresie mostka, nie na wszystkich interfejsach")
    inject = {}
    for item in a.inject:
        route, _, path = item.partition("=")
        if route not in ROUTES or not path:
            p.error(f"--inject: TRASA=PLIK, trasa z {sorted(ROUTES)} (podano: {item})")
        token = Path(path).expanduser()
        if any(inside(token, root) for root in repo_roots(REPO)):
            p.error("--inject: plik tokenu musi leżeć poza repozytorium (worktree i główny checkout)")
        if not token.is_file():
            p.error(f"--inject: brak pliku tokenu dla trasy {route}")
        inject[route] = token
    upstreams = {}
    for item in a.upstream:
        route, _, url = item.partition("=")
        if route not in ROUTES or not url.startswith(("http://", "https://")):
            p.error(f"--upstream: TRASA=URL (podano: {item})")
        upstreams[route] = url
    return Proxy(a.listen, a.port, DomainList(a.domains), a.deny_log, a.access_log,
                 parse_ports(a.connect_ports), parse_ports(a.http_ports), inject, upstreams)


async def main(argv: list[str]):
    proxy = build(argv)
    # SIGTERM (proxy_stop w common.sh) zamyka serwer zamiast zabijać pętlę w trakcie połączeń; obsługa przed
    # komunikatem „słucha na”, na który czeka common.sh.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    server = await proxy.start()
    addrs = ", ".join(f"{s.getsockname()[0]}:{s.getsockname()[1]}" for s in server.sockets)
    routes = ", ".join(sorted(proxy.inject)) or "brak"
    print(f"{now()} proxy prototypowe słucha na {addrs}; lista: {proxy.domains.path}; wstrzykiwanie: {routes}",
          flush=True)
    await stop.wait()
    # Nie `async with server`: wyjście z niego czeka na koniec wszystkich tuneli bez limitu (review PT-178 r2, M-1).
    if not await proxy.shutdown():
        print(f"{now()} połączenia nie zamknęły się w 2 s — kończę mimo to", file=sys.stderr, flush=True)
    print(f"{now()} proxy prototypowe zatrzymane", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1:]))
    except KeyboardInterrupt:
        pass
