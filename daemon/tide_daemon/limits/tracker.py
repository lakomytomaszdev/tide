"""Spina obszar limitów: odpowiedź /status → historia → prognozy → powiadomienia → JSON dla D-Bus.

Bez Qt i bez sieci — usługa (limits_monitor.py) podaje odpowiedź /status, a tracker zwraca
powiadomienia do wysłania i JSON prognoz. Zegar i strefa są wstrzykiwane (testy).
"""
from __future__ import annotations

import json
import time

from . import readings as readings_mod
from .alerts import AlertBook
from .forecast import forecast
from .history import History

SERVICE_ORDER = ("chatgpt", "claude")
WINDOW_ORDER = ("session", "weekly")


class LimitsTracker:
    def __init__(self, config, history: History | None = None, alerts: AlertBook | None = None,
                 clock=time.time, tz=None):
        self.config = config
        self.clock = clock
        self.tz = tz
        self.history = history or History(days=config.history_days, clock=clock)
        self.history.load()
        self.alerts = alerts if alerts is not None else AlertBook(config, tz=tz)
        self.available = False
        self.error = ""

    def ingest(self, status: dict) -> list[dict]:
        """Nowa odpowiedź /status. Zwraca powiadomienia do wysłania (tylko dla nowych odczytów)."""
        self.available, self.error = True, ""
        fresh = [r for r in readings_mod.from_status(status) if self.history.add(r)]
        try:
            self.history.flush()
        except OSError as exc:  # pełny dysk nie zatrzymuje prognozy z pamięci
            print(f"tide-daemon: nie udało się zapisać historii limitów: {exc}", flush=True)
        now = self.clock()
        out = []
        for reading in fresh:
            window_cfg = self.config.window(reading.window)
            fc = forecast(self.history.window(reading.key, window_cfg), now, window_cfg, self.tz)
            out.extend(self.alerts.check(reading.service, reading.window, fc, now))
        return out

    def failed(self, error: str) -> None:
        self.available, self.error = False, error

    def snapshot(self) -> dict:
        now = self.clock()
        services: dict[str, dict] = {}
        for key in self.history.keys():
            service, window = key.split("/", 1)
            if window not in WINDOW_ORDER:
                continue
            window_cfg = self.config.window(window)
            fc = forecast(self.history.window(key, window_cfg), now, window_cfg, self.tz)
            services.setdefault(service, {})[window] = fc
        ordered = {s: services[s] for s in sorted(services, key=lambda s: (s not in SERVICE_ORDER, s))}
        return {"available": self.available, "error": self.error, "services": ordered}

    def json(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False, sort_keys=True)
