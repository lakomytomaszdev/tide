"""CLI uwierzytelnia odczyt HTTPS przez lokalną bramkę.

gh używa http_unix_socket z konfiguracji, glab jawnego proxy CONNECT do hosta:443.
Tunel glab nie odszyfrowuje TLS; przekierowania na ten sam host obsługuje CLI.
Nie wywołujemy auth token, nie czytamy konfiguracji logowania.
"""
from contextlib import contextmanager
import http.client
import http.server
import json
import os
import selectors
import signal
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

TIMEOUT = 10
MAX_OUTPUT = 1024 * 1024


class HostingError(Exception):
    pass


class HostingTimeout(HostingError):
    pass


class _Gateway(http.server.BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(TIMEOUT)

    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        # Nie używamy hosta z żądania do zestawienia połączenia.
        if (self.server.program != "glab" or self.path != self.server.host + ":443" or self.headers.get("Transfer-Encoding") or
                self.headers.get("Content-Length", "0") != "0"):
            self.server.failure = "Proxy odrzuciło połączenie poza ustalonym hostem HTTPS."
            self.send_error(403)
            return
        upstream = None
        try:
            upstream = socket.create_connection((self.server.host, 443), timeout=TIMEOUT)
            with self.server.stream_lock:
                if self.server.stopping:
                    return
                self.server.streams.update((upstream, self.connection))
            self.send_response(200, "Connection established")
            self.end_headers()
            self.wfile.flush()
            # Limit transportu uwzględnia narzut TLS; JSON/stdout/stderr mają osobno 1 MiB.
            total = 0
            with selectors.DefaultSelector() as streams:
                streams.register(self.connection, selectors.EVENT_READ, upstream)
                streams.register(upstream, selectors.EVENT_READ, self.connection)
                while not self.server.stopping:
                    for key, _ in streams.select(0.1):
                        chunk = key.fileobj.recv(65536)
                        if not chunk:
                            return
                        total += len(chunk)
                        if total > 2 * MAX_OUTPUT:
                            self.server.failure = "Transport hostingu przekracza limit 2 MiB TLS."
                            return
                        key.data.sendall(chunk)
        except OSError:
            # Bez danych wyjątku: mogą zawierać konfigurację sieci użytkownika.
            return
        finally:
            if upstream is not None:
                with self.server.stream_lock:
                    self.server.streams.difference_update((upstream, self.connection))
                upstream.close()

    def do_GET(self):
        if self.server.program == "gh":
            return self.github_get()
        self.server.failure = "Proxy odrzuciło nieszyfrowane żądanie HTTP."
        self.send_error(403)

    def github_get(self):
        try:
            url = urlsplit(self.path)
            path = url.path + ("?" + url.query if url.query else "")
            host = url.netloc or self.headers.get("Host", "")
            if (host != self.server.host or path != self.server.path or self.server.requested or
                    self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0") != "0"):
                self.send_error(403)
                return
            self.server.requested = True
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() in ("authorization", "accept", "user-agent", "x-github-api-version")}
            conn = http.client.HTTPSConnection(self.server.host, timeout=TIMEOUT)
            stream = None
            try:
                conn.request("GET", self.server.path, headers=headers)
                stream = conn.sock
                with self.server.stream_lock:
                    if self.server.stopping:
                        return
                    self.server.streams.add(stream)
                response = conn.getresponse()
                if 300 <= response.status < 400:
                    self.server.failure = "Hosting zwrócił przekierowanie. Nie wykonano kolejnego odczytu."
                    self.send_error(502)
                    return
                body = response.read(MAX_OUTPUT + 1)
                if len(body) > MAX_OUTPUT:
                    self.server.failure = "Odpowiedź hostingu przekracza limit 1 MiB."
                    self.send_error(502)
                    return
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            finally:
                with self.server.stream_lock:
                    self.server.streams.discard(stream)
                conn.close()
        except (OSError, http.client.HTTPException):
            self.send_error(502)


class _Proxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False

    def handle_error(self, request, client_address):
        # Błąd transportu nie może wypisać danych żądania ani poświadczeń.
        pass


class _UnixProxy(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False
    handle_error = _Proxy.handle_error


@contextmanager
def host_gateway(program, host, endpoint):
    api_host = "api.github.com" if program == "gh" and host == "github.com" else host
    with tempfile.TemporaryDirectory(prefix="tide-hosting-") as folder:
        address = os.path.join(folder, "api.sock") if program == "gh" else ("127.0.0.1", 0)
        server = (_UnixProxy if program == "gh" else _Proxy)(address, _Gateway)
        prefix = ("/" if host == "github.com" else "/api/v3/") if program == "gh" else "/api/v4/"
        server.host, server.path, server.program = api_host, prefix + endpoint, program
        server.failure, server.requested = "", False
        server.streams, server.stream_lock, server.stopping = set(), threading.Lock(), False
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield server, address if program == "gh" else f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            with server.stream_lock:
                server.stopping = True
                for stream in server.streams:
                    try:
                        stream.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            server.shutdown()
            server.server_close()
            thread.join()


@contextmanager
def cli_environment(program, proxy, env):
    if program != "gh":
        yield env
        return
    # gh rozpoznaje klucz konfiguracji, nie GH_HTTP_UNIX_SOCKET. Hosts pozostaje
    # w istniejącym magazynie; dowiązanie nie odczytuje ani nie kopiuje sekretów.
    config = env.get("GH_CONFIG_DIR") or os.path.join(
        env.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "gh")
    hosts = os.path.abspath(os.path.join(config, "hosts.yml"))
    # Bez `version` gh migruje konfigurację i zapisuje hosts.yml przez dowiązanie.
    # Niezmigrowany magazyn użytkownika zostaje nietknięty: ostrzeżenie zamiast gh.
    if os.path.lexists(hosts) and not _gh_config_current(os.path.join(config, "config.yml")):
        raise HostingError("Konfiguracja gh wymaga migracji; uruchom dowolne polecenie gh ręcznie.")
    with tempfile.TemporaryDirectory(prefix="tide-gh-") as folder:
        os.symlink(hosts, os.path.join(folder, "hosts.yml"))
        with open(os.path.join(folder, "config.yml"), "w", encoding="utf-8") as fh:
            fh.write(f'version: "{GH_CONFIG_VERSION}"\nhttp_unix_socket: ' + json.dumps(proxy) + "\n")
        yield {**env, "GH_CONFIG_DIR": folder}


GH_CONFIG_VERSION = "1"


def _gh_config_current(path):
    """Tylko klucz `version` z config.yml (bez sekretów); brak pliku = format niezmigrowany."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read(64 * 1024)
    except (OSError, UnicodeDecodeError):
        return False
    for line in text.splitlines():
        if line.startswith("version:"):
            value = line.split(":", 1)[1].split("#", 1)[0].strip().strip("\"'")
            return value == GH_CONFIG_VERSION
    return False


def read_json(program, host, endpoint, cwd, *, deadline=None):
    """Tylko względny, zbudowany przez adapter endpoint. Stderr nigdy nie opuszcza tej funkcji."""
    deadline = min(deadline, time.monotonic() + TIMEOUT) if deadline is not None else time.monotonic() + TIMEOUT
    if deadline <= time.monotonic():
        raise HostingTimeout("Przekroczono czas analizy linków PR/MR.")
    try:
        with host_gateway(program, host, endpoint) as (guard, proxy):
            if deadline <= time.monotonic():
                raise HostingTimeout("Przekroczono czas analizy linków PR/MR.")
            env = dict(os.environ)
            for key in ("GH_DEBUG", "DEBUG", "GLAB_DEBUG", "ALL_PROXY", "all_proxy", "GH_HTTP_UNIX_SOCKET"):
                env.pop(key, None)
            # Awaria obsługi gniazda gh nie może skutkować odczytem bezpośrednim.
            proxy_url = "http://127.0.0.1:1" if program == "gh" else proxy
            env.update(HTTPS_PROXY=proxy_url, https_proxy=proxy_url, HTTP_PROXY=proxy_url, http_proxy=proxy_url,
                       NO_PROXY="", no_proxy="", GLAB_PROXY=proxy, GH_PROMPT_DISABLED="1",
                       GLAB_CHECK_UPDATE="false", GH_NO_UPDATE_NOTIFIER="1", GLAB_SEND_TELEMETRY="false",
                       GITLAB_API_HOST=host, GLAB_API_HOST=host, GLAB_API_PROTOCOL="https", API_PROTOCOL="https",
                       GLAB_SKIP_TLS_VERIFY="false", SKIP_TLS_VERIFY="false", GLAB_DEBUG_HTTP="false")
            argv = [program, "api", "--hostname", host, "--method", "GET", endpoint]
            with cli_environment(program, proxy, env) as env, \
                    subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as proc:
                try:
                    output = bytearray()
                    total = 0
                    with selectors.DefaultSelector() as streams:
                        streams.register(proc.stdout, selectors.EVENT_READ)
                        streams.register(proc.stderr, selectors.EVENT_READ)
                        while streams.get_map():
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise HostingTimeout("Przekroczono czas analizy linków PR/MR.")
                            for key, _ in streams.select(min(remaining, 0.1)):
                                chunk = os.read(key.fileobj.fileno(), 65536)
                                if not chunk:
                                    streams.unregister(key.fileobj)
                                    continue
                                total += len(chunk)
                                if total > MAX_OUTPUT:
                                    raise HostingError("Odpowiedź CLI przekracza limit 1 MiB.")
                                if key.fileobj is proc.stdout:
                                    output.extend(chunk)
                        try:
                            proc.wait(timeout=max(0.001, deadline - time.monotonic()))
                        except subprocess.TimeoutExpired:
                            raise HostingTimeout("Przekroczono czas analizy linków PR/MR.") from None
                    if deadline <= time.monotonic():
                        raise HostingTimeout("Przekroczono czas analizy linków PR/MR.")
                    if guard.failure:
                        raise HostingError(guard.failure)
                    if proc.returncode:
                        raise HostingError("Nie można odczytać metadanych: sprawdź logowanie CLI, sieć i dostępność PR/MR.")
                    try:
                        value = json.loads(output)
                        if not isinstance(value, dict):
                            raise ValueError()
                        return value
                    except (ValueError, UnicodeError, RecursionError):
                        raise HostingError("CLI zwrócił niepoprawny JSON metadanych.") from None
                finally:
                    # Grupa może przetrwać timeout/limit, gdy potomek nadal trzyma strumienie.
                    # Po sukcesie wait zebrał lidera, więc nie ryzykujemy użycia jego PGID.
                    if proc.returncode is None:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    proc.wait()
    except OSError:
        raise HostingError("Nie można uruchomić CLI hostingu lub tunelu HTTPS. Wybierz bazę lokalnie.") from None
