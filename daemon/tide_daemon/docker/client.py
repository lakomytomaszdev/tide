"""Klient Docker Engine API przez lokalny socket unix — sama biblioteka standardowa.

Socket: z $DOCKER_HOST (`unix:///ścieżka`), domyślnie /var/run/docker.sock. Inny schemat
(tcp://, ssh://, npipe://) oznacza stan „unsupported” — nie łączymy się z cudzym Dockerem
przez sieć. Każde zapytanie to osobne połączenie HTTP/1.1 z `Connection: close`: przez
socket unix to tanie, a odpowiedź kończy się razem z połączeniem albo z Content-Length,
albo z ostatnim kawałkiem `Transfer-Encoding: chunked`.

Ścieżki bez numeru wersji API — Docker odpowiada w swojej bieżącej wersji, a odczyt pól jest
pobłażliwy (snapshot.py, disk.py), więc starszy i nowszy silnik dają ten sam wynik.

Błędy połączenia zamieniamy na DockerUnavailable z krótkim powodem po polsku i stanem
(`unavailable`, `denied`, `unsupported`), błędy HTTP na DockerError ze statusem i komunikatem
Dockera.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import threading
from urllib.parse import quote, urlencode

DEFAULT_SOCKET = "/var/run/docker.sock"
TIMEOUT = 10.0


class DockerUnavailable(Exception):
    """Dockera nie da się zapytać: state = unavailable | denied | unsupported."""

    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state
        self.message = message


class DockerError(Exception):
    """Docker odpowiedział błędem HTTP; message to jego komunikat (po angielsku) albo nasz opis."""

    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


def socket_path(env=None) -> tuple[str, str]:
    """(ścieżka socketu, błąd). Błąd niepusty, gdy $DOCKER_HOST wskazuje coś innego niż unix://."""
    env = os.environ if env is None else env
    host = (env.get("DOCKER_HOST") or "").strip()
    if not host:
        return DEFAULT_SOCKET, ""
    if host.startswith("unix://"):
        path = host[len("unix://"):]
        return (path, "") if path.startswith("/") else ("", f"niepoprawny DOCKER_HOST={host}")
    scheme = host.split("://", 1)[0] if "://" in host else host
    return "", f"DOCKER_HOST={scheme}://… — Tide obsługuje tylko lokalny socket unix"


def host_label(path: str) -> str:
    return f"unix://{path}" if path else ""


class _Reader:
    """Bufor nad socketem: linie nagłówków, dokładna liczba bajtów, kawałki chunked."""

    def __init__(self, sock: socket.socket, stop: threading.Event | None = None):
        self.sock = sock
        self.buf = b""
        self.eof = False
        self.stop = stop

    def _fill(self) -> bool:
        while True:
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                if self.stop is not None and not self.stop.is_set():
                    continue  # strumień zdarzeń: cisza to nie błąd, póki nikt nie kazał przerwać
                raise
            break
        if not data:
            self.eof = True
            return False
        self.buf += data
        return True

    def line(self) -> bytes:
        while b"\r\n" not in self.buf:
            if not self._fill():
                raise ConnectionError("połączenie zamknięte w trakcie nagłówków")
        line, self.buf = self.buf.split(b"\r\n", 1)
        return line

    def exactly(self, n: int) -> bytes:
        while len(self.buf) < n:
            if not self._fill():
                raise ConnectionError("połączenie zamknięte w trakcie odpowiedzi")
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def available(self) -> bytes:
        """Cokolwiek przyszło (co najmniej jeden odczyt); b"" na końcu połączenia."""
        if not self.buf and not self._fill():
            return b""
        data, self.buf = self.buf, b""
        return data

    def rest(self) -> bytes:
        while self._fill():
            pass
        data, self.buf = self.buf, b""
        return data


class Response:
    def __init__(self, status: int, headers: dict, reader: _Reader, sock: socket.socket):
        self.status = status
        self.headers = headers
        self._reader = reader
        self._sock = sock

    @property
    def chunked(self) -> bool:
        return "chunked" in self.headers.get("transfer-encoding", "").lower()

    def pieces(self):
        """Treść odpowiedzi kawałkami, tak jak przychodzą (dla strumieni)."""
        if self.chunked:
            while True:
                size_line = self._reader.line().split(b";", 1)[0].strip()
                size = int(size_line or b"0", 16)
                if size == 0:
                    return
                yield self._reader.exactly(size)
                self._reader.exactly(2)  # \r\n po kawałku
        elif "content-length" in self.headers:
            left = int(self.headers["content-length"])
            while left > 0:
                data = self._reader.available()
                if not data:
                    raise ConnectionError("połączenie zamknięte przed końcem odpowiedzi")
                data, extra = data[:left], data[left:]
                self._reader.buf = extra + self._reader.buf
                left -= len(data)
                yield data
        else:
            while True:
                data = self._reader.available()
                if not data:
                    return
                yield data

    def read(self) -> bytes:
        return b"".join(self.pieces())

    def lines(self):
        """Strumień JSON-ów rozdzielonych nową linią (/events) — po jednej linii."""
        pending = b""
        for piece in self.pieces():
            pending += piece
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if line.strip():
                    yield line
        if pending.strip():
            yield pending

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def _message(body: bytes, status: int) -> str:
    try:
        data = json.loads(body.decode("utf-8", "replace"))
        if isinstance(data, dict) and data.get("message"):
            return str(data["message"])
    except ValueError:
        pass
    text = body.decode("utf-8", "replace").strip()
    return text[:300] or f"HTTP {status}"


class EngineClient:
    """Zapytania do Docker Engine API. Bezpieczny dla wielu wątków (każde zapytanie to nowe połączenie)."""

    def __init__(self, path: str | None = None, env=None, timeout: float = TIMEOUT):
        if path is None:
            path, problem = socket_path(env)
        else:
            problem = ""
        self.path = path
        self.problem = problem
        self.timeout = timeout

    @property
    def host(self) -> str:
        return host_label(self.path)

    def _connect(self, timeout: float | None) -> socket.socket:
        if self.problem:
            raise DockerUnavailable("unsupported", self.problem)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(self.path)
        except FileNotFoundError:
            sock.close()
            raise DockerUnavailable("unavailable", f"Docker nie działa — brak socketu {self.path}") from None
        except PermissionError:
            sock.close()
            raise DockerUnavailable("denied", f"brak uprawnień do {self.path} — dodaj się do grupy docker "
                                              "i zaloguj ponownie") from None
        except ConnectionRefusedError:
            sock.close()
            raise DockerUnavailable("unavailable", f"Docker nie odpowiada — {self.path} odrzuca połączenia") from None
        except socket.timeout:
            sock.close()
            raise DockerUnavailable("unavailable", f"Docker nie odpowiada w {timeout:g} s") from None
        except OSError as exc:
            sock.close()
            raise DockerUnavailable("unavailable", f"nie da się połączyć z {self.path}: {exc.strerror or exc}") from None
        return sock

    def open(self, method: str, path: str, query: dict | None = None, body=None, timeout: float | None = None,
             stop: threading.Event | None = None) -> Response:
        """Wysyła zapytanie i czyta nagłówki odpowiedzi. Treść czyta wywołujący (read, lines, pieces)."""
        timeout = self.timeout if timeout is None else timeout
        target = path
        if query:
            target += "?" + urlencode({k: v for k, v in query.items() if v is not None})
        payload = b""
        if body is not None:
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        head = [f"{method} {target} HTTP/1.1", "Host: docker", "User-Agent: tide-daemon", "Connection: close"]
        if body is not None:
            head += ["Content-Type: application/json", f"Content-Length: {len(payload)}"]
        elif method in ("POST", "PUT"):
            head.append("Content-Length: 0")
        sock = self._connect(timeout)
        try:
            sock.sendall(("\r\n".join(head) + "\r\n\r\n").encode() + payload)
            reader = _Reader(sock, stop)
            status_line = reader.line().decode("latin-1")
            parts = status_line.split(" ", 2)
            if len(parts) < 2 or not parts[0].startswith("HTTP/"):
                raise ConnectionError(f"niezrozumiała odpowiedź: {status_line[:80]}")
            status = int(parts[1])
            headers = {}
            while True:
                line = reader.line()
                if not line:
                    break
                name, _, value = line.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
        except socket.timeout:
            sock.close()
            raise DockerUnavailable("unavailable", f"Docker nie odpowiada w {timeout:g} s") from None
        except (ConnectionError, OSError, ValueError) as exc:
            sock.close()
            raise DockerUnavailable("unavailable", f"zerwane połączenie z Dockerem ({exc})") from None
        return Response(status, headers, reader, sock)

    def request(self, method: str, path: str, query: dict | None = None, body=None,
                timeout: float | None = None) -> tuple[int, bytes]:
        """Całe zapytanie: (status, treść). Błąd HTTP (≥ 400) → DockerError."""
        response = self.open(method, path, query, body, timeout)
        try:
            data = response.read()
        except socket.timeout:
            raise DockerUnavailable("unavailable", "Docker nie odpowiada w czasie odczytu") from None
        except (ConnectionError, OSError, ValueError) as exc:
            raise DockerUnavailable("unavailable", f"zerwane połączenie z Dockerem ({exc})") from None
        finally:
            response.close()
        if response.status >= 400:
            raise DockerError(response.status, _message(data, response.status))
        return response.status, data

    def get_json(self, path: str, query: dict | None = None, timeout: float | None = None):
        _status, data = self.request("GET", path, query, timeout=timeout)
        try:
            return json.loads(data.decode("utf-8", "replace")) if data else None
        except ValueError:
            raise DockerError(0, "niezrozumiała odpowiedź Dockera (nie JSON)") from None

    def post(self, path: str, query: dict | None = None, timeout: float | None = None) -> tuple[int, bytes]:
        return self.request("POST", path, query, timeout=timeout)

    # --- wygodne skróty -------------------------------------------------------------------

    def version(self) -> dict:
        return self.get_json("/version") or {}

    def containers(self, filters: dict | None = None) -> list:
        query = {"all": "1"}
        if filters:
            query["filters"] = json.dumps(filters)
        return self.get_json("/containers/json", query) or []

    def inspect(self, ident: str) -> dict:
        return self.get_json(f"/containers/{quote(ident, safe='')}/json") or {}


def container_path(ident: str, action: str = "") -> str:
    path = f"/containers/{quote(ident, safe='')}"
    return f"{path}/{action}" if action else path


# --- strumień logów ----------------------------------------------------------------------

STDOUT, STDERR = 1, 2


def demux(data: bytes) -> list[tuple[int, bytes]]:
    """Rozdziela strumień logów kontenera bez TTY: ramki [strumień, 0, 0, 0, długość(4, BE)] + treść.

    Gdy dane nie wyglądają na ramki (kontener z TTY — surowy tekst), całość idzie jako stdout."""
    frames = []
    pos = 0
    while pos + 8 <= len(data):
        kind = data[pos]
        if kind not in (0, 1, 2) or data[pos + 1:pos + 4] != b"\0\0\0":
            break
        (size,) = struct.unpack(">I", data[pos + 4:pos + 8])
        if pos + 8 + size > len(data):
            break
        frames.append((STDERR if kind == 2 else STDOUT, data[pos + 8:pos + 8 + size]))
        pos += 8 + size
    if pos < len(data):
        if not frames:
            return [(STDOUT, data)]
        frames.append((STDOUT, data[pos:]))
    return frames


def log_lines(data: bytes, tty: bool | None = None, limit: int | None = None) -> list[dict]:
    """Linie logów: [{text, stderr}] — demultipleksowane (bez TTY) i pocięte na linie."""
    frames = [(STDOUT, data)] if tty else demux(data)
    lines: list[dict] = []
    pending: dict[int, str] = {}
    for kind, chunk in frames:
        text = pending.pop(kind, "") + chunk.decode("utf-8", "replace")
        parts = text.split("\n")
        pending[kind] = parts.pop()
        for part in parts:
            lines.append({"text": part.rstrip("\r"), "stderr": kind == STDERR})
    for kind, rest in pending.items():
        if rest:
            lines.append({"text": rest.rstrip("\r"), "stderr": kind == STDERR})
    if limit is not None and len(lines) > limit:
        lines = lines[-limit:]
    return lines
