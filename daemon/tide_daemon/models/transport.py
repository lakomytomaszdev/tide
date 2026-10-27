"""HTTP do serwera modeli: jedno zapytanie JSON, każdy błąd jako ModelError z krótkim komunikatem po polsku.

- Jeden termin na całe zapytanie (jak w module Docker): szukanie nazwy, łączenie, wysłanie i cały
  odczyt odpowiedzi mieszczą się w `timeout`. Szukanie nazwy idzie w osobnym wątku (getaddrinfo nie
  ma limitu czasu), a po terminie gniazdo jest zamykane — serwer, który sączy bajty, nie trzyma
  wątku roboczego usługi w nieskończoność.
- Bez przekierowań: odpowiedź 3xx to błąd. Przekierowanie zabrałoby token do innego hosta, a POST
  /api/chat po 302 stałby się GET-em — stan pokazywałby „online”, choć odpowiada inny serwer.
- Bez pośredników z $http_proxy — serwer jest na tym komputerze albo w sieci lokalnej (Tailscale), a
  pośrednik nie powinien widzieć ani promptów, ani tokenu (http.client nie czyta tych zmiennych).
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


def _exchange(endpoint, method: str, path: str, data: bytes | None, headers: dict, deadline: float):
    """(kod, treść) z jednego zapytania; po terminie gniazdo jest zamykane z innego wątku."""
    scheme, hostname, port = endpoint.address()
    port = port or (443 if scheme == "https" else 80)
    sock = _connect(hostname, port, deadline)
    expired = threading.Event()

    def cut():
        expired.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    timer = threading.Timer(_left(deadline), cut)
    timer.daemon = True
    timer.start()
    conn = http.client.HTTPConnection(hostname, port)
    try:
        if scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=hostname)
        conn.sock = sock  # połączone wyżej (z terminem); http.client tylko pisze i czyta
        sock.settimeout(_left(deadline))
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        raw = response.read(MAX_BYTES + 1)
        if expired.is_set():  # odczyt przerwany zamknięciem gniazda kończy się krótszą treścią, nie błędem
            raise _Expired()
        return response.status, raw
    except (OSError, http.client.HTTPException):
        if expired.is_set() or time.monotonic() >= deadline:
            raise _Expired() from None
        raise
    finally:
        timer.cancel()
        conn.close()
        sock.close()


def request(endpoint, method: str, path: str, body=None, timeout: float = 10.0):
    """Zapytanie do `endpoint.url + path`, w całości najwyżej `timeout` s; odpowiedź JSON (dict albo list).
    Błąd → ModelError."""
    host = endpoint.host
    headers = endpoint.headers()  # ModelError, gdy pliku tokenu nie da się odczytać
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    full_path = urllib.parse.urlsplit(endpoint.url).path.rstrip("/") + path
    deadline = time.monotonic() + timeout
    try:
        status, raw = _exchange(endpoint, method, full_path, data, headers, deadline)
    except _Expired:
        raise ModelError(f"Model lokalny nie odpowiedział w {timeout:g} s ({host})") from None
    except socket.gaierror:
        raise ModelError(f"Model lokalny niedostępny: nie znaleziono serwera ({host})") from None
    except ssl.SSLError as exc:
        raise ModelError(f"Model lokalny niedostępny: błąd TLS ({host}, {type(exc).__name__})") from None
    except ConnectionRefusedError:
        raise ModelError(f"Model lokalny niedostępny: serwer nie odpowiada ({host})") from None
    except (http.client.HTTPException, ConnectionError) as exc:
        raise ModelError(f"Model lokalny niedostępny: połączenie przerwane ({host}, {type(exc).__name__})") from None
    except OSError as exc:
        raise ModelError(f"Model lokalny niedostępny: serwer nie odpowiada ({host}, {type(exc).__name__})") from None
    if 300 <= status < 400:
        raise ModelError(f"Model lokalny: serwer odpowiedział {status} — przekierowanie nie jest obsługiwane "
                         f"({host})", status=status)
    if status >= 400:
        detail = _detail(raw[:64 * 1024])
        suffix = f": {detail}" if detail else ""
        raise ModelError(f"Model lokalny: serwer odpowiedział {status}{suffix} ({host})", status=status, detail=detail)
    if len(raw) > MAX_BYTES:
        raise ModelError(f"Model lokalny: za duża odpowiedź serwera ({host})")
    try:
        result = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ModelError(f"Model lokalny: nieczytelna odpowiedź serwera ({host})") from None
    if not isinstance(result, (dict, list)):
        raise ModelError(f"Model lokalny: nieczytelna odpowiedź serwera ({host})")
    return result
