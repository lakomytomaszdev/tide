"""Historia odczytów limitów: $XDG_STATE_HOME/tide/limits-history.jsonl.

Jedna linia na odczyt jednego okna jednej usługi, tylko liczby — bez etykiet i bez tokenu:
    {"t":1793016060,"s":"claude","w":"session","u":62,"r":1793030400}
(t — czas odczytu strony, u — zużyty procent, r — chwila resetu albo null).

- dopisujemy tylko nowe odczyty (lastRead późniejszy niż ostatni zapisany dla tego okna),
- plik obejmuje `historyDays` dni (domyślnie 14): starsze linie wypadają przy wczytaniu
  i najwyżej raz na godzinę przy dopisywaniu (przepisanie przez plik tymczasowy i rename),
- okna limitu nie są zapisywane — wynikają z odczytów (split_windows): nowe okno zaczyna
  się, gdy minął reset poprzedniego, reset przesunął się dalej niż o `resetJumpMin` albo
  zużycie wyraźnie spadło. Dzięki temu po restarcie usługi okna wychodzą takie same.
"""
from __future__ import annotations

import json
import os
import time

from .readings import Reading

DROP_POINTS = 3          # spadek zużycia o więcej niż tyle punktów = limit się wyzerował
COMPACT_EVERY_S = 3600


def default_path() -> str:
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state, "tide", "limits-history.jsonl")


def is_break(prev: Reading, cur: Reading, window_cfg) -> bool:
    """Czy `cur` należy już do następnego okna limitu niż `prev`."""
    if prev.reset is not None and cur.t >= prev.reset:
        return True  # reset poprzedniego okna już minął
    if prev.reset is not None and cur.reset is not None and cur.reset - prev.reset > window_cfg.reset_jump_s:
        return True  # strona podaje reset o całe okno dalej
    if cur.used < prev.used - DROP_POINTS:
        return True  # zużycie spadło — limit się odnowił (np. bez etykiety resetu)
    return cur.t - prev.t > window_cfg.length_s  # przerwa dłuższa niż całe okno


def split_windows(readings: list[Reading], window_cfg) -> list[list[Reading]]:
    windows: list[list[Reading]] = []
    for reading in readings:
        if windows and not is_break(windows[-1][-1], reading, window_cfg):
            windows[-1].append(reading)
        else:
            windows.append([reading])
    return windows


def current_window(readings: list[Reading], window_cfg) -> list[Reading]:
    """Odczyty bieżącego (ostatniego) okna, od najstarszego."""
    if not readings:
        return []
    start = len(readings) - 1
    while start > 0 and not is_break(readings[start - 1], readings[start], window_cfg):
        start -= 1
    return readings[start:]


def encode(reading: Reading) -> str:
    return json.dumps({"t": int(reading.t), "s": reading.service, "w": reading.window, "u": reading.used,
                       "r": None if reading.reset is None else int(reading.reset)}, separators=(",", ":"))


def decode(line: str) -> Reading | None:
    try:
        row = json.loads(line)
        t, used, reset = row["t"], row["u"], row.get("r")
        if not isinstance(row["s"], str) or row["w"] not in ("session", "weekly"):
            return None
        if not isinstance(t, (int, float)) or not isinstance(used, int) or not 0 <= used <= 100:
            return None
        if reset is not None and not isinstance(reset, (int, float)):
            return None
        return Reading(row["s"], row["w"], float(t), used, None if reset is None else float(reset))
    except (ValueError, KeyError, TypeError):
        return None


class History:
    def __init__(self, path: str | None = None, days: float = 14, clock=time.time):
        self.path = path or default_path()
        self.keep_s = days * 86400
        self.clock = clock
        self.by_key: dict[str, list[Reading]] = {}
        self._pending: list[Reading] = []
        self._compacted_at = 0.0
        self._needs_compaction = False

    # --- odczyt ------------------------------------------------------------------------

    def load(self) -> None:
        self.by_key = {}
        cutoff = self.clock() - self.keep_s
        dropped = False
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    reading = decode(line)
                    if reading is None or reading.t < cutoff:
                        dropped = True
                        continue
                    self._insert(reading)
        except OSError:
            return
        self._needs_compaction = dropped

    def readings(self, key: str) -> list[Reading]:
        return self.by_key.get(key, [])

    def keys(self) -> list[str]:
        return sorted(self.by_key)

    def window(self, key: str, window_cfg) -> list[Reading]:
        return current_window(self.readings(key), window_cfg)

    # --- zapis -------------------------------------------------------------------------

    def add(self, reading: Reading) -> bool:
        """Dopisuje odczyt, jeśli jest nowszy niż ostatni z tego okna. Zwraca, czy dopisał."""
        last = self.by_key.get(reading.key)
        if last and last[-1].t >= reading.t:
            return False
        self._insert(reading)
        self._pending.append(reading)
        return True

    def _insert(self, reading: Reading) -> None:
        rows = self.by_key.setdefault(reading.key, [])
        if rows and rows[-1].t >= reading.t:
            if any(r.t == reading.t for r in rows):
                return  # powtórzona linia
            rows.append(reading)
            rows.sort(key=lambda r: r.t)
        else:
            rows.append(reading)

    def flush(self) -> None:
        """Dopisuje nowe odczyty do pliku; co jakiś czas wyrzuca odczyty starsze niż historyDays."""
        now = self.clock()
        cutoff = now - self.keep_s
        stale = any(rows and rows[0].t < cutoff for rows in self.by_key.values())
        if stale and now - self._compacted_at >= COMPACT_EVERY_S:
            for key in list(self.by_key):
                self.by_key[key] = [r for r in self.by_key[key] if r.t >= cutoff]
                if not self.by_key[key]:
                    del self.by_key[key]
            self._needs_compaction = True
        if self._needs_compaction:
            self._rewrite()
            self._compacted_at = now
            self._needs_compaction = False
            self._pending = []
            return
        if not self._pending:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("".join(encode(r) + "\n" for r in self._pending))
        self._pending = []

    def _rewrite(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        rows = sorted((r for rows in self.by_key.values() for r in rows), key=lambda r: (r.t, r.key))
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("".join(encode(r) + "\n" for r in rows))
        os.replace(tmp, self.path)
