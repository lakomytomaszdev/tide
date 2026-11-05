"""CLI uwierzytelnia odczyt; bramka wykonuje jeden ustalony GET HTTPS bez przekierowań.

Poświadczenia z nagłówków istnieją tylko podczas przekazania żądania, nigdy w plikach,
dzienniku ani wyniku. Nie wywołujemy auth token, nie czytamy konfiguracji logowania.
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


class _Gateway(http.server.BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(TIMEOUT)

    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        self.send_error(403)

    def do_GET(self):
        # gh używa lokalnego gniazda UNIX; glab jawnego proxy HTTP.
        # Bramka sama zestawia TLS do ustalonego API, więc CLI nie zobaczy Location.
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
                       if k.lower() in ("authorization", "private-token", "job-token", "accept", "user-agent",
                                        "x-github-api-version")}
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
            return


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
    prefix = ("/" if host == "github.com" else "/api/v3/") if program == "gh" else "/api/v4/"
    with tempfile.TemporaryDirectory(prefix="tide-hosting-") as folder:
        address = os.path.join(folder, "api.sock") if program == "gh" else ("127.0.0.1", 0)
        server = (_UnixProxy if program == "gh" else _Proxy)(address, _Gateway)
        server.host, server.path, server.failure, server.requested = api_host, prefix + endpoint, "", False
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


def read_json(program, host, endpoint, cwd):
    """Tylko względny, zbudowany przez adapter endpoint. Stderr nigdy nie opuszcza tej funkcji."""
    deadline = time.monotonic() + TIMEOUT
    try:
        with host_gateway(program, host, endpoint) as (guard, proxy):
            env = dict(os.environ)
            for key in ("GH_DEBUG", "DEBUG", "GLAB_DEBUG", "ALL_PROXY", "all_proxy", "GH_HTTP_UNIX_SOCKET"):
                env.pop(key, None)
            env.update(HTTPS_PROXY=proxy, https_proxy=proxy, HTTP_PROXY=proxy, http_proxy=proxy,
                       NO_PROXY="", no_proxy="", GLAB_PROXY=proxy, GH_PROMPT_DISABLED="1",
                       GLAB_CHECK_UPDATE="false", GH_NO_UPDATE_NOTIFIER="1", GLAB_SEND_TELEMETRY="false",
                       GITLAB_API_HOST=host, GLAB_API_HOST=host, GLAB_API_PROTOCOL="http", API_PROTOCOL="http")
            if program == "gh":
                env["GH_HTTP_UNIX_SOCKET"] = proxy
            argv = [program, "api", "--hostname", host, "--method", "GET", endpoint]
            with subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
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
                                raise HostingError("Przekroczono czas odczytu metadanych (10 sekund).")
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
                            raise HostingError("Przekroczono czas odczytu metadanych (10 sekund).") from None
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
                    # Cała grupa, również gdy potomek nadal trzyma otwarte stdout/stderr.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
    except OSError:
        raise HostingError("Nie można uruchomić CLI hostingu lub tunelu HTTPS. Wybierz bazę lokalnie.") from None
