"""Prognoza wyczerpania limitu z odczytów bieżącego okna.

Metoda (opis i uzasadnienie: docs/tide-daemon.md, „Prognoza”):

1. Bierzemy odczyty bieżącego okna (history.current_window) — odczyty sprzed resetu nie liczą się.
2. Oś czasu „pracy”: idąc od najnowszego odczytu wstecz, płaski odcinek (zużycie się nie zmienia)
   liczy się najwyżej jako `idleGapMin` — przerwa na obiad nie rozcieńcza tempa, a krótkie
   plateau między kolejnymi procentami (strona podaje całe procenty) liczy się w całości.
   `idleGapMin` = 0 wyłącza skracanie (okno tygodniowe: noce i przerwy to część rytmu tygodnia).
3. Z odczytów z ostatnich `lookbackMin` takiego czasu (sesja: 90 min, tydzień: doba) prosta
   metodą najmniejszych kwadratów: nachylenie to tempo w punktach procentowych na sekundę.
4. Od ostatniego odczytu: wyczerpanie = t + (100 − zużycie) / tempo. Przed resetem → „wyczerpie
   się ok. 17:40”, po resecie albo przy zerowym tempie → „starczy do resetu”.

Bez prognozy (state „unknown”, pusty tekst): mniej niż 3 odczyty albo rozpiętość krótsza niż
`minSpanMin`, nieznany reset, reset już minął, ostatni odczyt starszy niż `staleMin`.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .readings import Reading

UNKNOWN = "unknown"
LASTS = "lasts"
RUNS_OUT = "runs-out"
EXHAUSTED = "exhausted"
MIN_POINTS = 3

WEEKDAY_NAMES = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]


def effective_points(window: list[Reading], window_cfg) -> list[tuple[float, int]]:
    """(czas pracy względem ostatniego odczytu ≤ 0, zużycie) — od najnowszego, w granicach lookback."""
    if not window:
        return []
    points = [(0.0, window[-1].used)]
    x = 0.0
    flat = 0.0  # długość bieżącego płaskiego odcinka (idąc wstecz)
    for i in range(len(window) - 1, 0, -1):
        newer, older = window[i], window[i - 1]
        dt = max(0.0, newer.t - older.t)
        if newer.used > older.used or window_cfg.idle_gap_s <= 0:
            effective = dt
            flat = 0.0
        else:
            effective = min(dt, max(0.0, window_cfg.idle_gap_s - flat))
            flat += dt
        x -= effective
        if -x > window_cfg.lookback_s:
            break
        points.append((x, older.used))
    return points


def slope(points: list[tuple[float, int]]) -> float | None:
    n = len(points)
    mx = sum(p[0] for p in points) / n
    mu = sum(p[1] for p in points) / n
    den = sum((p[0] - mx) ** 2 for p in points)
    if den <= 0:
        return None
    return sum((p[0] - mx) * (p[1] - mu) for p in points) / den


def round_to(ts: float, step: float) -> float:
    return round(ts / step) * step if step > 0 else ts


def when_text(ts: float, now: float, tz=None) -> str:
    """„17:40” dziś, „jutro 09:30”, dalej „czwartek 11:00” (czas lokalny albo podana strefa)."""
    moment = datetime.fromtimestamp(ts, tz)
    today = datetime.fromtimestamp(now, tz).date()
    clock = f"{moment:%H:%M}"  # jak etykiety resetu z QuotaApp
    if moment.date() == today:
        return clock
    if moment.date() == today + timedelta(days=1):
        return "jutro " + clock
    return f"{WEEKDAY_NAMES[moment.weekday()]} {clock}"


def forecast(window: list[Reading], now: float, window_cfg, tz=None) -> dict:
    """Prognoza dla jednego okna jednej usługi (słownik gotowy do JSON)."""
    result = {"state": UNKNOWN, "reason": "", "used": None, "remaining": None, "readTs": None, "resetTs": None,
              "exhaustTs": None, "marginS": None, "ratePerHour": None, "points": 0,
              "windowStartTs": None, "whenText": "", "text": "", "short": ""}
    if not window:
        result["reason"] = "noData"
        return result
    last = window[-1]
    result.update(used=last.used, remaining=100 - last.used, readTs=int(last.t),
                  resetTs=None if last.reset is None else int(last.reset), windowStartTs=int(window[0].t))
    if last.used >= 100:
        result.update(state=EXHAUSTED, reason="exhausted")
        return result
    if last.reset is None:
        result["reason"] = "noReset"
        return result
    if now >= last.reset:
        result["reason"] = "resetPassed"
        return result
    if now - last.t > window_cfg.stale_s:
        result["reason"] = "stale"
        return result
    points = effective_points(window, window_cfg)
    result["points"] = len(points)
    span = -min(p[0] for p in points)
    if len(points) < MIN_POINTS or span < max(window_cfg.min_span_s, 1):
        result["reason"] = "tooLittleData"
        return result
    rate = slope(points)
    if rate is None:
        result["reason"] = "tooLittleData"
        return result
    rate = max(0.0, rate)
    result["ratePerHour"] = round(rate * 3600, 2)
    exhaust = last.t + (100 - last.used) / rate if rate > 1e-9 else None
    if exhaust is None or exhaust >= last.reset:
        result.update(state=LASTS, text="starczy do resetu", short="starczy do resetu")
        if exhaust is not None:
            result["exhaustTs"] = int(exhaust)
        return result
    shown = round_to(exhaust, window_cfg.round_s)
    when = when_text(shown, now, tz)
    result.update(state=RUNS_OUT, exhaustTs=int(exhaust), marginS=int(last.reset - exhaust),
                  text=f"wyczerpie się ok. {when}", short=f"koniec ok. {when}", whenText=when)
    return result
