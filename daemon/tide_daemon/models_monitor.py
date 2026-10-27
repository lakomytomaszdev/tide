"""Obszar Modeli lokalnych w usłudze: co POLL_MS odczyt stanu serwerów modeli w wątku roboczym.

models.json i zapytania HTTP (krótkie limity, models/status.py) idą poza wątkiem głównym, więc
serwer, który nie odpowiada, nie blokuje D-Bus. Wynik wraca sygnałem Qt; Changed tylko wtedy,
gdy JSON stanu się zmienił. Refresh() w trakcie odczytu zleca jeszcze jeden zaraz po nim — żeby
po zmianie models.json albo załadowaniu modelu wynik był świeży, a nie sprzed chwili.

Pole `queue` w stanie: kolejka wywołań modeli (models/queue.py) na każdy punkt — co trwa i co czeka
(rola, model, od kiedy; bez treści poleceń). Zmiany kolejki przychodzą z wątków roboczych (raport,
opisy kart) i trafiają do wątku głównego sygnałem Qt; Changed z ich powodu najwyżej co QUEUE_GAP_MS.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .models import config as models_config
from .models import queue as models_queue
from .models import status as models_status

POLL_MS = 15_000
FIRST_POLL_MS = 1000
QUEUE_GAP_MS = 1000
IDLE = {"running": None, "waiting": []}


def _dump(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, sort_keys=True)


class ModelsMonitor(QObject):
    changed = pyqtSignal()
    _fetched = pyqtSignal(object)    # stan (słownik z models/status.py) — z wątku roboczego
    _queued = pyqtSignal()           # zmiana w kolejce modeli — z dowolnego wątku

    def __init__(self, read=models_status.read, poll_ms: int | None = None, queue=None,
                 queue_gap_ms: int = QUEUE_GAP_MS, parent=None):
        super().__init__(parent)
        self.read = read
        try:
            cfg = models_config.load()
        except Exception as exc:  # nieczytelne ustawienia nie mogą zatrzymać startu usługi
            cfg = models_config.defaults(error=f"models.json: nieoczekiwany błąd ({type(exc).__name__}) — "
                                               f"ustawienia domyślne")
        self.queue = queue if queue is not None else models_queue.shared()
        self._state = models_status.pending(cfg)
        self._json = self._compose()
        self._queue_gap_ms = max(0, queue_gap_ms)
        self._queue_at = -1e18           # kiedy ostatnio (time.monotonic) przeliczyliśmy stan po zmianie kolejki
        self._queue_timer = QTimer(self, singleShot=True)
        self._queue_timer.timeout.connect(self._queue_publish)
        self._queued.connect(self._on_queue)
        self._listener = self._queued.emit  # ten sam obiekt do remove_listener w stop()
        self.queue.add_listener(self._listener)
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
        self._queue_timer.stop()
        self.queue.remove_listener(self._listener)
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
            self._state = state
            self._publish()
        if self._again:
            self._again = False
            self.poll()

    # --- kolejka modeli -------------------------------------------------------------------------

    def _compose(self) -> str:
        """JSON stanu z polem `queue` po nazwie punktu. Kolejka ma tory po serwerach (`queue.lane_of(url)`),
        więc punkty z tym samym adresem pokazują ten sam stan toru (wpisy mają `endpoint` — punkt, z którego
        przyszło wywołanie). Każdy punkt z konfiguracji jest w polu (bezczynny — running null, waiting []).
        Tor bez punktu w konfiguracji (wywołanie sprzed zmiany ustawień, np. na starym adresie punktu) nie
        znika: pod nazwą punktu z jego wpisów — jako cały wpis, gdy tej nazwy jeszcze nie ma, a obok
        bieżącego stanu w liście `other` ({lane, running, waiting}), gdy punkt ma już nowy adres."""
        snapshot = self.queue.snapshot()
        endpoints = self._state.get("endpoints") or {}
        out, used = {}, set()
        for name, endpoint in endpoints.items():
            lane = models_queue.lane_of((endpoint or {}).get("url") or "") or name
            used.add(lane)
            out[name] = snapshot.get(lane) or IDLE
        for lane, state in snapshot.items():
            if lane in used:
                continue
            entries = ([state["running"]] if state.get("running") else []) + list(state.get("waiting") or ())
            for name in dict.fromkeys(entry.get("endpoint") or lane for entry in entries):
                if name not in out:
                    out[name] = state
                else:
                    current = dict(out[name])
                    current["other"] = list(current.get("other") or ()) + [dict(state, lane=lane)]
                    out[name] = current
        return _dump(dict(self._state, queue=out))

    def _publish(self) -> None:
        text = self._compose()
        if text != self._json:
            self._json = text
            self.changed.emit()

    def _on_queue(self) -> None:
        """Zmiana kolejki: pierwsza od razu, kolejne najwyżej co QUEUE_GAP_MS (stan bierzemy z chwili
        przeliczenia, więc nic nie ginie — najwyżej kilka zmian schodzi się w jedno Changed)."""
        if self._queue_timer.isActive():
            return
        left = self._queue_gap_ms - (time.monotonic() - self._queue_at) * 1000
        if left <= 0:
            self._queue_publish()
        else:
            self._queue_timer.start(int(left) + 1)

    def _queue_publish(self) -> None:
        self._queue_at = time.monotonic()
        self._publish()

    def _log(self, state: dict) -> None:
        """Do dziennika tylko zmiany: punkt zaczął albo przestał odpowiadać, błąd albo ostrzeżenia w models.json."""
        config = state.get("config") or {}
        lines = {"config": config.get("error", ""), "warnings": " · ".join(config.get("warnings") or ())}
        for name, endpoint in (state.get("endpoints") or {}).items():
            lines[f"endpoint:{name}"] = (f"{name}: odpowiada (wersja {endpoint.get('version') or '?'})"
                                         if endpoint.get("online") else f"{name}: {endpoint.get('error', '')}")
        for key, line in lines.items():
            if line != self._logged.get(key, "" if key in ("config", "warnings") else None):
                if line:
                    print(f"tide-daemon: Modele lokalne: {line}", flush=True)
                self._logged[key] = line
