"""Progi i parametry prognozy limitów z palety (sekcja „limits” w theme/palette/mist.json).

Usługa czyta kopię palety obok siebie (palette.json, kopiowana przez make install-daemon),
więc po zmianie progów wystarczy `make install-daemon`. Brak sekcji albo klucza = wartość
domyślna z DEFAULTS; nieznane klucze są pomijane.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass

WINDOWS = ("session", "weekly")

DEFAULTS = {
    # Co ile sekund pytać QuotaApp o /status. Usługa sama czyta strony co 5 min, a /status
    # oddaje tylko jej pamięć — nowy odczyt poznajemy po zmianie lastRead.
    "pollS": 120,
    "historyDays": 14,
    # Powiadomienie raz na okno, gdy zużycie przekroczy próg (procent zużyty, nie pozostały).
    "alertUsedPercent": [80, 95],
    # Powiadomienie raz na okno, gdy prognoza mówi, że limit skończy się więcej niż tyle minut przed resetem.
    "alertRunoutMarginMin": 30,
    "session": {
        "lengthMin": 300,       # okno sesji: 5 h
        "lookbackMin": 90,      # tempo z ostatnich 90 min „czasu pracy” w oknie
        "minSpanMin": 15,       # mniej danych = brak prognozy
        "idleGapMin": 15,       # płaski odcinek liczy się najwyżej jako 15 min (przerwa nie rozcieńcza tempa)
        "staleMin": 30,         # ostatni odczyt starszy niż tyle = brak prognozy
        "resetJumpMin": 30,     # reset przesunięty o więcej = nowe okno
        "roundMin": 5,          # „ok. 17:40” — zaokrąglenie godziny wyczerpania
    },
    "weekly": {
        "lengthMin": 10080,     # okno tygodniowe: 7 dni
        "lookbackMin": 1440,    # tempo z ostatniej doby (z nocami i przerwami — to rytm tygodnia)
        "minSpanMin": 180,
        "idleGapMin": 0,        # 0 = przerw nie skracamy
        "staleMin": 360,
        "resetJumpMin": 360,
        "roundMin": 30,
    },
}


@dataclass(frozen=True)
class WindowConfig:
    length_s: float
    lookback_s: float
    min_span_s: float
    idle_gap_s: float
    stale_s: float
    reset_jump_s: float
    round_s: float


@dataclass(frozen=True)
class LimitsConfig:
    poll_s: float
    history_days: float
    alert_used: tuple[int, ...]
    alert_runout_margin_s: float
    windows: dict  # nazwa okna → WindowConfig

    def window(self, name: str) -> WindowConfig:
        return self.windows[name]


def _number(value, default, minimum=0.0):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= minimum \
        else float(default)


def from_dict(section: dict | None) -> LimitsConfig:
    """Konfiguracja z sekcji „limits” palety (albo z pustej — same wartości domyślne)."""
    merged = copy.deepcopy(DEFAULTS)
    section = section if isinstance(section, dict) else {}
    for key, value in section.items():
        if key in WINDOWS and isinstance(value, dict):
            merged[key].update({k: v for k, v in value.items() if k in DEFAULTS[key]})
        elif key in DEFAULTS and key not in WINDOWS:
            merged[key] = value
    used = merged["alertUsedPercent"]
    if not isinstance(used, list) or not all(isinstance(v, (int, float)) and 0 < v <= 100 for v in used):
        used = DEFAULTS["alertUsedPercent"]
    windows = {}
    for name in WINDOWS:
        w, d = merged[name], DEFAULTS[name]
        windows[name] = WindowConfig(
            length_s=_number(w["lengthMin"], d["lengthMin"], 1) * 60,
            lookback_s=_number(w["lookbackMin"], d["lookbackMin"], 1) * 60,
            min_span_s=_number(w["minSpanMin"], d["minSpanMin"]) * 60,
            idle_gap_s=_number(w["idleGapMin"], d["idleGapMin"]) * 60,
            stale_s=_number(w["staleMin"], d["staleMin"], 1) * 60,
            reset_jump_s=_number(w["resetJumpMin"], d["resetJumpMin"], 1) * 60,
            round_s=_number(w["roundMin"], d["roundMin"], 1) * 60,
        )
    return LimitsConfig(
        poll_s=_number(merged["pollS"], DEFAULTS["pollS"], 1),
        history_days=_number(merged["historyDays"], DEFAULTS["historyDays"], 1),
        alert_used=tuple(sorted({int(v) for v in used})),
        alert_runout_margin_s=_number(merged["alertRunoutMarginMin"], DEFAULTS["alertRunoutMarginMin"]) * 60,
        windows=windows,
    )


def load(paths: list[str]) -> LimitsConfig:
    """Sekcja „limits” z pierwszej czytelnej palety; bez palety — wartości domyślne."""
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                palette = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(palette, dict):
            return from_dict(palette.get("limits"))
    return from_dict(None)
