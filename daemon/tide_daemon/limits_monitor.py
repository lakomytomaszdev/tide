"""Obszar Limitów AI w usłudze: co `pollS` pytanie QuotaApp o /status w wątku roboczym.

Zapytanie HTTP (pętla zwrotna, limit 5 s) idzie poza wątkiem głównym, więc wiszące API nie
blokuje D-Bus. Wynik wraca sygnałem Qt, a tracker (limits/tracker.py) dopisuje historię,
liczy prognozy i zwraca powiadomienia. Changed tylko wtedy, gdy JSON prognoz się zmienił.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .limits import backend

FIRST_POLL_MS = 1500


class LimitsMonitor(QObject):
    changed = pyqtSignal()
    alert = pyqtSignal(object)            # słownik {id, summary, body} — limits/alerts.py
    _fetched = pyqtSignal(object, str)    # (odpowiedź /status albo None, błąd) — z wątku roboczego

    def __init__(self, tracker, fetch=backend.fetch_status, poll_ms: int | None = None, parent=None):
        super().__init__(parent)
        self.tracker = tracker
        self.fetch = fetch
        self._json = tracker.json()
        self._busy = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-limits")
        self._fetched.connect(self._deliver)
        interval = poll_ms if poll_ms is not None else int(tracker.config.poll_s * 1000)
        self._timer = QTimer(self, interval=max(50, interval))
        self._timer.timeout.connect(self.poll)
        self._logged_error = None

    def start(self) -> None:
        QTimer.singleShot(min(FIRST_POLL_MS, self._timer.interval()), self.poll)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def forecast_json(self) -> str:
        return self._json

    def poll(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._pool.submit(self._work)

    def _work(self) -> None:
        try:
            self._fetched.emit(self.fetch(), "")
        except backend.BackendError as exc:
            self._fetched.emit(None, str(exc))
        except Exception as exc:  # nieoczekiwany błąd nie może zatrzymać odpytywania
            self._fetched.emit(None, f"błąd odczytu limitów ({type(exc).__name__})")

    def _deliver(self, status, error: str) -> None:
        self._busy = False
        alerts = []
        if status is not None:
            try:
                alerts = self.tracker.ingest(status)
            except Exception as exc:  # zła odpowiedź nie kładzie usługi
                print(f"tide-daemon: nie udało się przetworzyć odczytu limitów: {exc}", flush=True)
            error = ""
        else:
            self.tracker.failed(error)
        if error != self._logged_error:
            print(f"tide-daemon: Limity AI: {error}" if error else "tide-daemon: Limity AI: usługa odpowiada",
                  flush=True)
            self._logged_error = error
        self.publish()
        for alert in alerts:
            self.alert.emit(alert)

    def publish(self) -> None:
        text = self.tracker.json()
        if text != self._json:
            self._json = text
            self.changed.emit()
