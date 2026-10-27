"""HTTP do serwera modeli: jedno zapytanie JSON, każdy błąd jako ModelError z krótkim komunikatem po polsku.

Bez pośredników z $http_proxy — serwer jest na tym komputerze albo w sieci lokalnej (Tailscale), a
pośrednik nie powinien widzieć ani promptów, ani tokenu. Limit czasu dotyczy każdej operacji na
gnieździe; Ollama bez strumieniowania milczy do końca odpowiedzi, więc to w praktyce limit całości.
Treść błędu serwera trafia do komunikatu (przycięta, bez znaków sterujących) — nagłówki z tokenem nigdy.
"""
from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.request

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


def _timed_out(exc) -> bool:
    return isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in str(exc)


def request(endpoint, method: str, path: str, body=None, timeout: float = 10.0):
    """Zapytanie do `endpoint.url + path`; odpowiedź JSON (dict albo list). Błąd → ModelError."""
    host = endpoint.host
    headers = endpoint.headers()  # ModelError, gdy pliku tokenu nie da się odczytać
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint.url + path, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        try:
            detail = _detail(exc.read(64 * 1024))
        except (OSError, http.client.HTTPException):
            detail = ""
        finally:
            exc.close()
        suffix = f": {detail}" if detail else ""
        raise ModelError(f"Model lokalny: serwer odpowiedział {exc.code}{suffix} ({host})",
                         status=exc.code, detail=detail) from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if _timed_out(reason):
            raise ModelError(f"Model lokalny nie odpowiedział w {timeout:g} s ({host})") from None
        if isinstance(reason, socket.gaierror):
            raise ModelError(f"Model lokalny niedostępny: nie znaleziono serwera ({host})") from None
        raise ModelError(f"Model lokalny niedostępny: serwer nie odpowiada ({host})") from None
    except (TimeoutError, socket.timeout):
        raise ModelError(f"Model lokalny nie odpowiedział w {timeout:g} s ({host})") from None
    except (http.client.HTTPException, ConnectionError) as exc:
        raise ModelError(f"Model lokalny niedostępny: połączenie przerwane ({host}, {type(exc).__name__})") from None
    except OSError as exc:
        if _timed_out(exc):
            raise ModelError(f"Model lokalny nie odpowiedział w {timeout:g} s ({host})") from None
        raise ModelError(f"Model lokalny niedostępny: serwer nie odpowiada ({host}, {type(exc).__name__})") from None
    if len(raw) > MAX_BYTES:
        raise ModelError(f"Model lokalny: za duża odpowiedź serwera ({host})")
    try:
        result = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ModelError(f"Model lokalny: nieczytelna odpowiedź serwera ({host})") from None
    if not isinstance(result, (dict, list)):
        raise ModelError(f"Model lokalny: nieczytelna odpowiedź serwera ({host})")
    return result
