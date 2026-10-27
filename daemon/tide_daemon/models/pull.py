"""Pobieranie modelu na serwer Ollamy (`POST /api/pull`, odpowiedź strumieniowa) z postępem.

Ollama wysyła JSON w liniach: {"status": "pulling manifest"}, potem dla każdej warstwy
{"status": "pulling <skrót>", "digest": …, "total": N, "completed": M}, na końcu „verifying sha256
digest”, „writing manifest” i {"status": "success"}; błąd to {"error": "…"} (także w trakcie). Postęp
całości to suma `completed` i `total` po warstwach. Bez terminu całości (duży model to nawet godzina),
za to z limitem ciszy (IDLE_S) i przerwaniem przez `Cancel` (zamykanie usługi). Każdy błąd to ModelError.
"""
from __future__ import annotations

import time

from .transport import ModelError, _clean, stream

CONNECT_S = 10.0
IDLE_S = 300.0            # sprawdzanie sumy dużego modelu trwa chwilę, a serwer wtedy milczy
PROGRESS_EVERY_S = 0.25   # częściej nie ma sensu przekazywać postępu do wątku głównego


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def pull(endpoint, model: str, on_progress, cancel=None, idle_timeout: float = IDLE_S, clock=time.monotonic) -> None:
    """Pobiera `model` na punkt `endpoint` (Ollama). `on_progress({status, completed, total})` w wątku
    wywołującego, najwyżej co PROGRESS_EVERY_S (i zawsze przy zmianie etapu). Błąd → ModelError."""
    layers: dict[str, tuple[int, int]] = {}
    last = {"status": "", "at": 0.0}

    def line(item) -> None:
        if not isinstance(item, dict):
            return
        error = item.get("error")
        if error:
            raise ModelError(f"Model lokalny: nie udało się pobrać {model}: {_clean(error)} ({endpoint.host})")
        status = item.get("status") if isinstance(item.get("status"), str) else ""
        digest = item.get("digest")
        total = _int(item.get("total"))
        if isinstance(digest, str) and total:
            layers[digest] = (min(_int(item.get("completed")), total), total)
        now = clock()
        if status == last["status"] and now - last["at"] < PROGRESS_EVERY_S:
            return
        last["status"], last["at"] = status, now
        on_progress({"status": _clean(status)[:80], "completed": sum(c for c, _t in layers.values()),
                     "total": sum(t for _c, t in layers.values())})

    stream(endpoint, "POST", "/api/pull", {"model": model, "stream": True}, line, connect_timeout=CONNECT_S,
           idle_timeout=idle_timeout, cancel=cancel)
    if last["status"] != "success":
        raise ModelError(f"Model lokalny: pobieranie {model} przerwane przez serwer ({endpoint.host})")
