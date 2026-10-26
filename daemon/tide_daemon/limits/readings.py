"""Odpowiedź GET /status usługi QuotaApp → odczyty limitów.

Kształt (backend/app.py w QuotaApp):
    {"claude": {"session": {"remaining": 38, "reset": "poniedziałek 18:30"},
                "weekly": {"remaining": 71, "reset": "~czwartek 11:51"},
                "lastRead": "2026-10-26T16:41+01:00", "state": "ready"}, "chatgpt": {...}, ...}

- remaining to pozostały procent; w historii trzymamy zużycie (100 − remaining), bo progi
  powiadomień i tempo liczy się od strony zużycia,
- reset to etykieta z parsera usługi: dzień tygodnia i godzina po polsku, z „~”, gdy strona
  podała czas względny („Resets in 2 hr 13 min”). Etykieta jest liczona względem chwili
  odczytu (lastRead), więc znacznik czasu resetu to najbliższy taki dzień i godzina od tej chwili,
- lastRead (minuty, ze strefą) to czas odczytu strony — nowy odczyt poznajemy po jego zmianie.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    LABEL_TZ = ZoneInfo("Europe/Warsaw")  # strefa, w której QuotaApp pisze etykiety resetu (TIMEZONE)
except Exception:  # pragma: no cover — brak bazy stref: strefa z lastRead
    LABEL_TZ = None

WEEKDAYS = {"poniedziałek": 0, "wtorek": 1, "środa": 2, "czwartek": 3, "piątek": 4, "sobota": 5, "niedziela": 6}
LABEL = re.compile(r"^\s*~?\s*(?:(?P<day>[^\W\d_]+)\s+)?(?P<h>\d{1,2}):(?P<m>\d{2})\s*$")
# Etykieta „~” jest liczona od chwili odczytu z dokładnością do minuty — dopuszczamy chwilę wstecz.
SLACK_S = 120


@dataclass(frozen=True)
class Reading:
    service: str
    window: str          # session | weekly
    t: float             # czas odczytu strony (epoch)
    used: int            # zużyty procent 0–100
    reset: float | None  # chwila resetu okna (epoch) albo None

    @property
    def key(self) -> str:
        return f"{self.service}/{self.window}"


def parse_read_time(value) -> float | None:
    """lastRead „2026-10-26T16:41+01:00” → epoch; bez strefy przyjmujemy strefę etykiet."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=LABEL_TZ or timezone.utc)
    return moment.timestamp()


def parse_reset(label, read_ts: float) -> float | None:
    """Etykieta resetu („~poniedziałek 11:41”, „sobota 18:00”, „18:30”) → epoch.

    Najbliższa taka chwila nie wcześniej niż odczyt (z małym zapasem na zaokrąglenie „~”).
    """
    if not isinstance(label, str):
        return None
    match = LABEL.match(label)
    if not match:
        return None
    hour, minute = int(match.group("h")), int(match.group("m"))
    if hour > 23 or minute > 59:
        return None
    day = match.group("day")
    weekday = None
    if day:
        weekday = WEEKDAYS.get(day.casefold())
        if weekday is None:
            return None
    tz = LABEL_TZ or timezone.utc
    base = datetime.fromtimestamp(read_ts, tz)
    start = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    for days in range(0, 8):
        candidate = start + timedelta(days=days)
        if weekday is not None and candidate.weekday() != weekday:
            continue
        # Przeliczenie przez datę i godzinę lokalną: przejście na czas zimowy/letni nie przesuwa godziny.
        candidate = datetime(candidate.year, candidate.month, candidate.day, hour, minute, tzinfo=tz)
        if candidate.timestamp() >= read_ts - SLACK_S:
            return candidate.timestamp()
    return None


def from_status(status: dict) -> list[Reading]:
    """Wszystkie odczyty z odpowiedzi /status (usługi z liczbą i czasem odczytu)."""
    out = []
    if not isinstance(status, dict):
        return out
    for service, info in sorted(status.items()):
        if not isinstance(info, dict) or not any(k in info for k in ("session", "weekly")):
            continue
        read_ts = parse_read_time(info.get("lastRead"))
        if read_ts is None:
            continue
        for window in ("session", "weekly"):
            metric = info.get(window)
            if not isinstance(metric, dict):
                continue
            remaining = metric.get("remaining")
            if not isinstance(remaining, (int, float)) or isinstance(remaining, bool) or not 0 <= remaining <= 100:
                continue
            out.append(Reading(service, window, read_ts, int(round(100 - remaining)),
                               parse_reset(metric.get("reset"), read_ts)))
    return out
