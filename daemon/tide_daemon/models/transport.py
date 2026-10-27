"""HTTP do serwera modeli: jedno zapytanie JSON, każdy błąd jako ModelError z krótkim komunikatem po polsku.

- Jeden termin na całe zapytanie (jak w module Docker): szukanie nazwy, łączenie, wysłanie i cały
  odczyt odpowiedzi mieszczą się w `timeout`. Szukanie nazwy idzie w osobnym wątku (getaddrinfo nie
  ma limitu czasu), a po terminie gniazdo jest zamykane — serwer, który sączy bajty, nie trzyma
  wątku roboczego usługi w nieskończoność.
- Bez przekierowań: odpowiedź 3xx to błąd. Przekierowanie zabrałoby token do innego hosta, a POST
  /api/chat po 302 stałby się GET-em — stan pokazywałby „online”, choć odpowiada inny serwer.
- Bez pośredników z $http_proxy — serwer jest na tym komputerze albo w sieci lokalnej (Tailscale), a
  pośrednik nie powinien widzieć ani promptów, ani tokenu (http.client nie czyta tych zmiennych).
- Przerwanie z zewnątrz (`Cancel`, np. zamykanie usługi): set() z innego wątku zamyka gniazda
  trwających zapytań, więc wywołanie kończy się od razu ModelError, a nie po terminie.
Treść błędu serwera trafia do komunikatu (przycięta, bez znaków sterujących) — nagłówki z tokenem nigdy.
"""
from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading
import time
import urllib.parse

MAX_BYTES = 16 * 1024 * 1024
DETAIL_MAX = 200


class ModelError(Exception):
    """Model albo serwer nie dał odpowiedzi. str(exc) to gotowy komunikat dla użytkownika.

    status — kod HTTP, gdy serwer odpowiedział błędem; detail — treść błędu serwera (do decyzji
    w kodzie, np. ponowienie bez `think`)."""

    def __init__(self, message: str, status: int | None = None, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail


class _Expired(Exception):
    """Minął termin całego zapytania."""


class _Cancelled(Exception):
    """Wywołanie przerwane z zewnątrz (Cancel.set())."""


class Cancel:
    """Przerwanie wywołań modelu z innego wątku (zamykanie usługi). set() jest trwałe: trwające zapytania
    tracą gniazdo od razu, a nowe w ogóle nie startują."""

    def __init__(self):
        self._lock = threading.Lock()
        self._set = False
        self._hooks: set = set()

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        with self._lock:
            self._set = True
            hooks = list(self._hooks)
        for hook in hooks:
            hook()

    def _register(self, hook) -> bool:
        with self._lock:
            if self._set:
                return False
            self._hooks.add(hook)
            return True

    def _unregister(self, hook) -> None:
        with self._lock:
            self._hooks.discard(hook)


def _clean(text: str) -> str:
    text = "".join(c if c.isprintable() else " " for c in str(text))
    text = " ".join(text.split())
    return text if len(text) <= DETAIL_MAX else text[:DETAIL_MAX - 1] + "…"


def _detail(raw: bytes) -> str:
    """Komunikat z treści błędu: {"error": "..."} (Ollama), {"error": {"message": ...}} (OpenAI) albo tekst."""
    try:
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        return _clean(raw.decode("utf-8", "replace"))
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        error = error.get("message")
    return _clean(error) if isinstance(error, str) else ""


def _left(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise _Expired()
    return left


def _resolve(hostname: str, port: int, deadline: float) -> list:
    """getaddrinfo w osobnym wątku, czekamy najwyżej do terminu (wątek po terminie kończy się sam)."""
    box: dict = {}

    def work():
        try:
            box["result"] = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            box["error"] = exc

    worker = threading.Thread(target=work, name="tide-models-dns", daemon=True)
    worker.start()
    worker.join(_left(deadline))
    if worker.is_alive():
        raise _Expired()
    if "error" in box:
        raise box["error"]
    return box["result"]


def _connect(hostname: str, port: int, deadline: float) -> socket.socket:
    last: OSError | None = None
    for family, kind, proto, _name, sockaddr in _resolve(hostname, port, deadline):
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(_left(deadline))
            sock.connect(sockaddr)
            return sock
        except TimeoutError:
            sock.close()
            raise _Expired() from None
        except OSError as exc:
            sock.close()
            last = exc
        except _Expired:
            sock.close()
            raise
    raise last or OSError("brak adresu")


def _exchange(endpoint, method: str, path: str, data: bytes | None, headers: dict, deadline: float,
              cancel: Cancel | None = None):
    """(kod, treść) z jednego zapytania; po terminie albo po cancel.set() gniazdo jest zamykane z innego wątku."""
    scheme, hostname, port = endpoint.address()
    port = port or (443 if scheme == "https" else 80)
    if cancel is not None and cancel.is_set():
        raise _Cancelled()
    sock = _connect(hostname, port, deadline)
    expired = threading.Event()
    cancelled = threading.Event()

    def shut():
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def cut():
        expired.set()
        shut()

    def stop():
        cancelled.set()
        shut()

    timer = conn = None
    registered = False
    try:  # także _left() i konstruktor w try: termin może minąć tuż po połączeniu, gniazdo i tak zamykamy
        if cancel is not None:
            registered = cancel._register(stop)
            if not registered:
                raise _Cancelled()
        timer = threading.Timer(_left(deadline), cut)
        timer.daemon = True
        timer.start()
        conn = http.client.HTTPConnection(hostname, port)
        if scheme == "https":
            conn.default_port = 443  # nagłówek Host bez „:443”, jak w zwykłym kliencie https
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=hostname)
        conn.sock = sock  # połączone wyżej (z terminem); http.client tylko pisze i czyta
        sock.settimeout(_left(deadline))
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        raw = response.read(MAX_BYTES + 1)
        if cancelled.is_set():
            raise _Cancelled()
        if expired.is_set():  # odczyt przerwany zamknięciem gniazda kończy się krótszą treścią, nie błędem
            raise _Expired()
        return response.status, raw
    except (OSError, http.client.HTTPException):
        if cancelled.is_set():
            raise _Cancelled() from None
        if expired.is_set() or time.monotonic() >= deadline:
            raise _Expired() from None
        raise
    finally:
        if registered:
            cancel._unregister(stop)
        if timer:
            timer.cancel()
        if conn:
            conn.close()
        sock.close()


def _failure(exc: BaseException, host: str, waited: str) -> ModelError:
    """Wyjątek z gniazda albo terminu jako ModelError z krótkim komunikatem po polsku."""
    if isinstance(exc, _Cancelled):
        return ModelError(f"Model lokalny: wywołanie przerwane — usługa się zamyka ({host})")
    if isinstance(exc, _Expired):
        return ModelError(f"Model lokalny nie odpowiedział w {waited} ({host})")
    if isinstance(exc, socket.gaierror):
        return ModelError(f"Model lokalny niedostępny: nie znaleziono serwera ({host})")
    if isinstance(exc, ssl.SSLError):
        return ModelError(f"Model lokalny niedostępny: błąd TLS ({host}, {type(exc).__name__})")
    if isinstance(exc, ConnectionRefusedError):
        return ModelError(f"Model lokalny niedostępny: serwer nie odpowiada ({host})")
    if isinstance(exc, (http.client.HTTPException, ConnectionError)):
        return ModelError(f"Model lokalny niedostępny: połączenie przerwane ({host}, {type(exc).__name__})")
    return ModelError(f"Model lokalny niedostępny: serwer nie odpowiada ({host}, {type(exc).__name__})")


_FAILURES = (_Cancelled, _Expired, OSError, http.client.HTTPException)


def _http_error(status: int, raw: bytes, host: str) -> ModelError | None:
    if 300 <= status < 400:
        return ModelError(f"Model lokalny: serwer odpowiedział {status} — przekierowanie nie jest obsługiwane "
                          f"({host})", status=status)
    if status >= 400:
        detail = _detail(raw[:64 * 1024])
        suffix = f": {detail}" if detail else ""
        return ModelError(f"Model lokalny: serwer odpowiedział {status}{suffix} ({host})", status=status,
                          detail=detail)
    return None


def request(endpoint, method: str, path: str, body=None, timeout: float = 10.0, cancel: Cancel | None = None):
    """Zapytanie do `endpoint.url + path`, w całości najwyżej `timeout` s; odpowiedź JSON (dict albo list).
    `cancel` — przerwanie z innego wątku. Błąd → ModelError."""
    host = endpoint.host
    headers = endpoint.headers()  # ModelError, gdy pliku tokenu nie da się odczytać
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    full_path = urllib.parse.urlsplit(endpoint.url).path.rstrip("/") + path
    deadline = time.monotonic() + timeout
    try:
        status, raw = _exchange(endpoint, method, full_path, data, headers, deadline, cancel)
    except _FAILURES as exc:
        raise _failure(exc, host, f"{timeout:g} s") from None
    error = _http_error(status, raw, host)
    if error:
        raise error
    if len(raw) > MAX_BYTES:
        raise ModelError(f"Model lokalny: za duża odpowiedź serwera ({host})")
    try:
        result = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ModelError(f"Model lokalny: nieczytelna odpowiedź serwera ({host})") from None
    if not isinstance(result, (dict, list)):
        raise ModelError(f"Model lokalny: nieczytelna odpowiedź serwera ({host})")
    return result


LINE_MAX = 64 * 1024


def stream(endpoint, method: str, path: str, body, on_line, connect_timeout: float = 10.0,
           idle_timeout: float = 120.0, cancel: Cancel | None = None) -> None:
    """Zapytanie z odpowiedzią strumieniową (JSON w liniach, np. Ollama `POST /api/pull`): `on_line(obiekt)`
    dla każdej linii, w wątku wywołującego. Bez terminu całości (pobieranie modelu trwa nawet godzinę):
    łączenie i nagłówki najwyżej `connect_timeout` s, potem najwyżej `idle_timeout` s ciszy między
    porcjami danych. `cancel.set()` z innego wątku zamyka gniazdo od razu. Błąd → ModelError."""
    host = endpoint.host
    headers = endpoint.headers()
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    full_path = urllib.parse.urlsplit(endpoint.url).path.rstrip("/") + path
    scheme, hostname, port = endpoint.address()
    port = port or (443 if scheme == "https" else 80)
    deadline = time.monotonic() + connect_timeout
    cancelled = threading.Event()
    state = {"waited": f"{connect_timeout:g} s", "sock": None}

    def stop():
        cancelled.set()
        sock = state["sock"]
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    registered = False
    conn = None
    try:
        if cancel is not None:
            registered = cancel._register(stop)
            if not registered:
                raise _Cancelled()
        sock = _connect(hostname, port, deadline)
        state["sock"] = sock
        if cancelled.is_set():
            raise _Cancelled()
        conn = http.client.HTTPConnection(hostname, port)
        if scheme == "https":
            conn.default_port = 443
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=hostname)
            state["sock"] = sock
        conn.sock = sock
        sock.settimeout(_left(deadline))
        conn.request(method, full_path, body=data, headers=headers)
        response = conn.getresponse()
        state["waited"] = f"{idle_timeout:g} s"
        sock.settimeout(idle_timeout)
        if response.status >= 300:
            error = _http_error(response.status, response.read(64 * 1024), host)
            raise error
        while True:
            line = response.readline(LINE_MAX + 1)
            if cancelled.is_set():
                raise _Cancelled()
            if not line:
                return
            if len(line) > LINE_MAX:
                raise ModelError(f"Model lokalny: za długa linia odpowiedzi serwera ({host})")
            if not line.strip():
                continue
            try:
                item = json.loads(line.decode("utf-8"))
            except ValueError:
                raise ModelError(f"Model lokalny: nieczytelna odpowiedź serwera ({host})") from None
            on_line(item)
    except ModelError:
        if cancelled.is_set():
            raise _failure(_Cancelled(), host, "") from None
        raise
    except TimeoutError:
        raise _failure(_Cancelled() if cancelled.is_set() else _Expired(), host, state["waited"]) from None
    except _FAILURES as exc:
        raise _failure(_Cancelled() if cancelled.is_set() else exc, host, state["waited"]) from None
    finally:
        if registered:
            cancel._unregister(stop)
        if conn:
            conn.close()
        if state["sock"] is not None:
            state["sock"].close()
