"""Odczyt niskiego limitu i ostrożne rozpoznanie wyczerpania w logu kroku."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo


SERVICE = {"anthropic": "claude", "openai": "chatgpt"}
STALE_SECONDS = {"session": 30 * 60, "weekly": 6 * 60 * 60}
WINDOW_LABEL = {"session": "pięciogodzinnym", "weekly": "tygodniowym"}
WARSAW = ZoneInfo("Europe/Warsaw")
WEEKDAYS = ("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela")
LIMIT_WORDS = re.compile(r"you(?:'|’)ve hit your limit|usage limit reached|rate limit (?:reached|exceeded)|"
                         r"too many requests|limit (?:wyczerpany|osiągnięty)", re.I)


def reset_label(value, now: float) -> str:
    """Godzina resetu jako polski tekst dla panelu, bez surowego znacznika czasu."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= now:
        return ""
    when = datetime.fromtimestamp(value, WARSAW)
    today = datetime.fromtimestamp(now, WARSAW)
    clock = f"{when:%H:%M}"
    return clock if when.date() == today.date() else f"{WEEKDAYS[when.weekday()]} {clock}"


def low_limit(snapshot: dict, provider: str, threshold: float, now: float) -> dict | None:
    """Najnowsze świeże okno poniżej progu. Brak danych w jednym oknie nie maskuje drugiego."""
    service = SERVICE.get(provider)
    if not service or not isinstance(snapshot, dict) or not snapshot.get("available"):
        return None
    windows = (snapshot.get("services") or {}).get(service) or {}
    candidates = []
    for name, stale in STALE_SECONDS.items():
        row = windows.get(name) or {}
        remaining, read = row.get("remaining"), row.get("readTs")
        if (not isinstance(remaining, (int, float)) or isinstance(remaining, bool) or
                not isinstance(read, (int, float)) or isinstance(read, bool) or
                not 0 <= remaining <= 100 or not -60 <= now - read <= stale or
                row.get("reason") == "resetPassed" or remaining > threshold):
            continue
        reset = reset_label(row.get("resetTs"), now)
        candidates.append({"provider": provider, "service": service, "window": name,
                           "remaining": remaining, "reset": reset, "resetTs": row.get("resetTs") or 0})
    return min(candidates, key=lambda item: item["remaining"]) if candidates else None


def recovered(snapshot: dict, provider: str, threshold: float, now: float, window: str = "") -> bool:
    """Odnowienie potwierdza świeży odczyt wyczerpanego okna; brak danych go nie zastępuje."""
    service = SERVICE.get(provider)
    if not service or not isinstance(snapshot, dict) or not snapshot.get("available"):
        return False
    windows = (snapshot.get("services") or {}).get(service) or {}
    fresh = set()
    for name, stale in STALE_SECONDS.items():
        row = windows.get(name) or {}
        remaining, read = row.get("remaining"), row.get("readTs")
        if (not isinstance(remaining, (int, float)) or isinstance(remaining, bool) or
                not isinstance(read, (int, float)) or isinstance(read, bool) or
                not 0 <= remaining <= 100 or not -60 <= now - read <= stale or
                row.get("reason") == "resetPassed"):
            continue
        fresh.add(name)
        if remaining <= threshold:
            return False
    return window in fresh if window else fresh == set(STALE_SECONDS)


def wait_reason(info: dict) -> str:
    provider = {"anthropic": "Claude", "openai": "Codex"}.get(info["provider"], info["provider"])
    tail = f" do {info['reset']}" if info.get("reset") else ""
    return f"czeka na limit {provider}{tail} (pozostało {info['remaining']:g}% w oknie {WINDOW_LABEL[info['window']]})"


def planning_warning(info: dict) -> str:
    """Ostrzeżenie dla planowania w Konsoli bez sugestii, że rozmowa została wstrzymana."""
    provider = {"anthropic": "Claude", "openai": "Codex"}.get(info["provider"], info["provider"])
    tail = f"; odnowienie {info['reset']}" if info.get("reset") else ""
    return f"Mało limitu {provider}: pozostało {info['remaining']:g}% w oknie {WINDOW_LABEL[info['window']]}{tail}"


def exhausted_log(path: str, tool: str) -> bool:
    """Tylko jawny błąd dostawcy; zwykły tekst agenta i ostrzeżenie o limicie nie wystarczą."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 1024 * 1024))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()[-2000:]
    except OSError:
        return False
    for line in lines:
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type") or ""
        if tool == "claude" and kind == "rate_limit_event":
            info = event.get("rate_limit_info") or {}
            if isinstance(info, dict) and info.get("status") == "rejected":
                return True
        error = kind == "error" or kind == "result" and event.get("is_error") is True
        if not error:
            continue
        detail = event.get("error") or {}
        status = event.get("api_error_status") or (detail.get("status") if isinstance(detail, dict) else None)
        if status == 429 or str(status) == "429":
            return True
        message = event.get("message") or event.get("result") or event.get("error") or ""
        if isinstance(message, dict):
            message = message.get("message") or ""
        if isinstance(message, str) and LIMIT_WORDS.search(message):
            return True
    return False
