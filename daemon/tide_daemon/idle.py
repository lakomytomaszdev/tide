"""Bezczynność użytkownika dla tapety z kotem: „od dozeAfterS nie było klawiatury ani myszy”.

- IdleState — czysta logika stanu (bez Qt): kiedy bezczynny, od kiedy, czy źródło działa.
  Zegar jest wstrzykiwany, więc testy idą na zamrożonym czasie.
- IdleMonitor — obiekt Qt w usłudze: połączenie z kompozytorem (wayland_idle.IdleClient)
  pilnowane przez QSocketNotifier. Bez odpytywania: przy działającym połączeniu proces budzi się tylko,
  gdy kompozytor zgłosi bezczynność albo powrót wejścia. Gdy kompozytora nie ma albo
  połączenie się zerwie (np. restart KWina), stan wraca do „nie bezczynny” (kot czuwa),
  a ponowna próba idzie z rosnącą przerwą (2 s … 60 s).

Próg pochodzi z palety (`wallpaper.dozeAfterS`, kopia palety obok zainstalowanej usługi),
a nadpisuje go opcja --idle-after.
"""
from __future__ import annotations

import json
import time

from PyQt6.QtCore import QObject, QSocketNotifier, QTimer, pyqtSignal

from . import wayland_idle

DEFAULT_DOZE_AFTER_S = 30
RETRY_MS = (2_000, 60_000)  # pierwsza i najdłuższa przerwa przed ponownym połączeniem


def doze_after_s(paths: list[str]) -> float:
    """Próg bezczynności z pierwszej czytelnej palety; bez palety — DEFAULT_DOZE_AFTER_S."""
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                value = json.load(fh).get("wallpaper", {}).get("dozeAfterS")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return float(DEFAULT_DOZE_AFTER_S)


class IdleState:
    """Stan bezczynności z dwóch zdarzeń kompozytora: idled (po progu bez wejścia) i resumed.

    idled przychodzi dopiero po `threshold_ms` bez wejścia, więc ostatnie wejście było
    `threshold_ms` przed nim — stąd idle_for_ms() liczy od tej chwili, a nie od zdarzenia.
    """

    def __init__(self, threshold_ms: int, clock=time.monotonic):
        self.threshold_ms = int(threshold_ms)
        self.clock = clock
        self.available = False
        self.idle = False
        self.source = ""
        self._last_input = clock()  # do pierwszego zdarzenia zakładamy świeże wejście

    def connected(self, source: str) -> bool:
        """Źródło działa. Zwraca True, gdy zmieniła się bezczynność (tu nigdy)."""
        self.available = True
        self.source = source
        self._last_input = self.clock()
        return False

    def idled(self) -> bool:
        if not self.available or self.idle:
            return False
        self.idle = True
        self._last_input = self.clock() - self.threshold_ms / 1000
        return True

    def resumed(self) -> bool:
        self._last_input = self.clock()
        if not self.idle:
            return False
        self.idle = False
        return True

    def lost(self) -> bool:
        """Źródło zniknęło: bez wiedzy o wejściu kot czuwa (bezpieczny stan)."""
        was = self.idle
        self.available = False
        self.idle = False
        self.source = ""
        return was

    def idle_for_ms(self) -> int:
        """Ile trwa bezczynność (0, gdy użytkownik jest aktywny albo źródło nie działa)."""
        if not self.idle:
            return 0
        return max(0, int(round((self.clock() - self._last_input) * 1000)))

    def snapshot(self) -> dict:
        return {"available": self.available, "idle": self.idle, "thresholdMs": self.threshold_ms,
                "idleForMs": self.idle_for_ms(), "source": self.source}


class IdleMonitor(QObject):
    """Bezczynność z kompozytora w pętli zdarzeń Qt; sygnał idleChanged(bool) przy każdej zmianie."""

    idleChanged = pyqtSignal(bool)
    availableChanged = pyqtSignal(bool)

    def __init__(self, threshold_ms: int, client_factory=None, clock=time.monotonic,
                 retry_ms: tuple[int, int] = RETRY_MS, parent=None):
        super().__init__(parent)
        self.state = IdleState(threshold_ms, clock)
        self.client_factory = client_factory or (
            lambda timeout, on_idle, on_resume: wayland_idle.IdleClient(timeout, on_idle, on_resume))
        self.retry_first, self.retry_max = retry_ms
        self._retry = self.retry_first
        self._client = None
        self._notifier = None
        self._last_error = None
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._connect)

    @property
    def idle(self) -> bool:
        return self.state.idle

    @property
    def available(self) -> bool:
        return self.state.available

    def start(self) -> None:
        self._connect()

    def stop(self) -> None:
        self._timer.stop()
        self._drop()

    def state_json(self) -> str:
        return json.dumps(self.state.snapshot(), ensure_ascii=False)

    # --- wnętrze ---------------------------------------------------------------------------

    def _connect(self) -> None:
        client = self.client_factory(self.state.threshold_ms, self._on_idle, self._on_resume)
        try:
            client.connect()
        except wayland_idle.WaylandError as exc:
            self._report(f"bezczynność niedostępna: {exc}")
            self._schedule_retry()
            return
        self._client = client
        self._retry = self.retry_first
        self._notifier = QSocketNotifier(client.fileno(), QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._read)
        self.state.connected(client.source)
        self._report(f"bezczynność z kompozytora: {client.source}, próg {self.state.threshold_ms} ms")
        self.availableChanged.emit(True)

    def _read(self, *_args) -> None:
        if self._client is None:
            return
        try:
            self._client.dispatch()
        except wayland_idle.WaylandError as exc:
            self._report(f"bezczynność: {exc}")
            self._drop()
            if self.state.lost():
                self.idleChanged.emit(False)
            self.availableChanged.emit(False)
            self._schedule_retry()

    def _on_idle(self) -> None:
        if self.state.idled():
            self.idleChanged.emit(True)

    def _on_resume(self) -> None:
        if self.state.resumed():
            self.idleChanged.emit(False)

    def _drop(self) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        if self._client is not None:
            self._client.close()
            self._client = None

    def _schedule_retry(self) -> None:
        self._timer.start(self._retry)
        self._retry = min(self._retry * 2, self.retry_max)

    def _report(self, text: str) -> None:
        # Ten sam komunikat (np. brak kompozytora przy każdej próbie) tylko raz.
        if text != self._last_error:
            self._last_error = text
            print(f"tide-daemon: {text}", flush=True)

