"""Obszar Modeli lokalnych w usłudze: co POLL_MS odczyt stanu serwerów modeli w wątku roboczym.

models.json i zapytania HTTP (krótkie limity, models/status.py) idą poza wątkiem głównym, więc
serwer, który nie odpowiada, nie blokuje D-Bus. Wynik wraca sygnałem Qt; Changed tylko wtedy,
gdy JSON stanu się zmienił. Refresh() w trakcie odczytu zleca jeszcze jeden zaraz po nim — żeby
po zmianie models.json albo załadowaniu modelu wynik był świeży, a nie sprzed chwili.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .models import config as models_config
from .models import status as models_status

POLL_MS = 15_000
FIRST_POLL_MS = 1000


def _dump(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, sort_keys=True)


class ModelsMonitor(QObject):
    changed = pyqtSignal()
    _fetched = pyqtSignal(object)    # stan (słownik z models/status.py) — z wątku roboczego

    def __init__(self, read=models_status.read, poll_ms: int | None = None, parent=None):
        super().__init__(parent)
        self.read = read
        try:
            cfg = models_config.load()
        except Exception as exc:  # nieczytelne ustawienia nie mogą zatrzymać startu usługi
            cfg = models_config.defaults(error=f"models.json: nieoczekiwany błąd ({type(exc).__name__}) — "
                                               f"ustawienia domyślne")
        self._json = _dump(models_status.pending(cfg))
        self._busy = False
        self._again = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-models")
        self._fetched.connect(self._deliver)
        self._timer = QTimer(self, interval=max(50, poll_ms if poll_ms is not None else POLL_MS))
        self._timer.timeout.connect(self.poll)
        self._logged: dict[str, str] = {}

    def start(self) -> None:
        QTimer.singleShot(min(FIRST_POLL_MS, self._timer.interval()), self.poll)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def status_json(self) -> str:
        return self._json

    def refresh(self) -> None:
        if self._busy:
            self._again = True
            return
        self.poll()

    def poll(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._pool.submit(self._work)

    def _work(self) -> None:
        try:
            self._fetched.emit(self.read())
        except Exception as exc:  # nieoczekiwany błąd nie może zatrzymać odpytywania
            print(f"tide-daemon: Modele lokalne: błąd odczytu stanu ({type(exc).__name__}: {exc})", flush=True)
            self._fetched.emit(None)

    def _deliver(self, state) -> None:
        self._busy = False
        if state is not None:
            self._log(state)
            text = _dump(state)
            if text != self._json:
                self._json = text
                self.changed.emit()
        if self._again:
            self._again = False
            self.poll()

    def _log(self, state: dict) -> None:
        """Do dziennika tylko zmiany: punkt zaczął albo przestał odpowiadać, błąd w models.json."""
        lines = {"config": (state.get("config") or {}).get("error", "")}
        for name, endpoint in (state.get("endpoints") or {}).items():
            lines[f"endpoint:{name}"] = (f"{name}: odpowiada (wersja {endpoint.get('version') or '?'})"
                                         if endpoint.get("online") else f"{name}: {endpoint.get('error', '')}")
        for key, line in lines.items():
            if line != self._logged.get(key, "" if key == "config" else None):
                if line:
                    print(f"tide-daemon: Modele lokalne: {line}", flush=True)
                self._logged[key] = line
