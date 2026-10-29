"""Wygląd aktywności w usłudze: po każdej zmianie aktywności nakłada wygląd docelowej (PT-35).

Każda droga przełączenia (skrót, Meta+Q, widżet, KDE Connect) kończy się sygnałem
org.kde.ActivityManager.Activities.CurrentActivityChanged — usługa go słucha i uruchamia silnik
`activity_look.py apply --activity ID` z kopii w $XDG_DATA_HOME/tide/activity-look (make
install-activity-look; decyzja 3: kopia, nie dowiązanie). Silnik porównuje cały wygląd i nakłada
tylko różnice, w stałej kolejności, z dziennikiem w $XDG_STATE_HOME/tide/activity-look.json.

Kolejka: jedno nałożenie naraz; zdarzenia z krótkiego okna (debounce) i te, które przyszły w trakcie
nałożenia, zlewają się w jedno — wygrywa ostatnia aktywność. Przy starcie usługi (logowanie)
silnik dostaje `apply` bez aktywności: porównuje pulpit z wyglądem bieżącej aktywności i naprawia
różnice, a przy okazji dokańcza przełączenie przerwane przed wylogowaniem. Kod 75 (plasmashell
albo ActivityManager jeszcze nie odpowiada) to ponowienie z rosnącą przerwą; inny błąd czeka
na następne zdarzenie.

Silnik działa w osobnym procesie: plasma-apply-* trwają chwilę i nie mogą blokować pętli usługi,
a błąd w nim nie kładzie usługi. Istnienie kopii silnika jest sprawdzane przy każdym uruchomieniu
(make install-activity-look i uninstall-activity-look w trakcie działania usługi). Zatrzymanie
usługi zabija trwające nałożenie — dziennik zostaje w stanie „applying”, a dokończy je następny start.
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSlot
from PyQt6.QtDBus import QDBusConnection

SERVICE = "org.kde.ActivityManager"
PATH = "/ActivityManager/Activities"
IFACE = "org.kde.ActivityManager.Activities"
TEMPFAIL = 75
DEBOUNCE_MS = 400
RETRY_MS = (2_000, 60_000)  # pierwsza i najdłuższa przerwa przed ponowieniem
CURRENT = None  # „bieżąca aktywność” — silnik pyta sam


def qt_timer(parent: QObject, interval_ms: int, callback) -> QTimer:
    timer = QTimer(parent, singleShot=True, interval=interval_ms)
    timer.timeout.connect(callback)
    return timer


def default_engine() -> str | None:
    """Zainstalowany silnik; $TIDE_ACTIVITY_LOOK_ENGINE podaje inny, a pusty wyłącza nakładanie."""
    value = os.environ.get("TIDE_ACTIVITY_LOOK_ENGINE")
    if value is not None:
        return value or None
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(data, "tide", "activity-look", "scripts", "activity_look.py")


class EngineProcess(QObject):
    """Uruchomienie silnika przez QProcess; done(kod, wyjście) po zakończeniu."""

    def __init__(self, python: str = sys.executable, parent=None):
        super().__init__(parent)
        self.python = python
        self._proc = None

    def __call__(self, args: list[str], done) -> None:
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

        def finished(code, _status):
            output = bytes(proc.readAll()).decode("utf-8", "replace")
            proc.deleteLater()
            self._proc = None
            done(code if _status == QProcess.ExitStatus.NormalExit else 1, output)

        def failed(error):
            if error == QProcess.ProcessError.FailedToStart:
                proc.deleteLater()
                self._proc = None
                done(1, f"nie udało się uruchomić {self.python}")

        proc.finished.connect(finished)
        proc.errorOccurred.connect(failed)
        self._proc = proc
        proc.start(self.python, args)

    def kill(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.waitForFinished(2000)


class ActivityLookMonitor(QObject):
    def __init__(self, engine: str | None, launcher=None, bus: QDBusConnection | None = None,
                 debounce_ms: int = DEBOUNCE_MS, retry_ms: tuple[int, int] = RETRY_MS, subscribe: bool = True,
                 exists=os.path.isfile, timer=qt_timer, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.launcher = launcher or EngineProcess(parent=self)
        self.bus = bus
        self.subscribe = subscribe
        self.exists = exists
        self._missing_reported = False
        self.retry_first, self.retry_max = retry_ms
        self._retry = self.retry_first
        self._pending: tuple[str | None] | None = None  # ostatnie żądanie, jeszcze nieuruchomione
        self._last: tuple[str | None] | None = None     # żądanie w toku (do ponowienia)
        self._running = False
        self._stopped = False
        self._debounce = timer(self, debounce_ms, self._launch)
        self._retry_timer = timer(self, self.retry_first, self._launch)

    def start(self) -> None:
        if not self.engine:
            print("tide-daemon: wygląd aktywności wyłączony (brak silnika — make install-activity-look)", flush=True)
            return
        if self.subscribe:
            bus = self.bus or QDBusConnection.sessionBus()
            if not bus.connect(SERVICE, PATH, IFACE, "CurrentActivityChanged", self.activity_changed):
                print("tide-daemon: wygląd aktywności: nie da się słuchać CurrentActivityChanged", flush=True)
        self.request(CURRENT)  # logowanie: porównanie z wyglądem bieżącej aktywności i naprawa

    def stop(self) -> None:
        self._stopped = True
        self._debounce.stop()
        self._retry_timer.stop()
        kill = getattr(self.launcher, "kill", None)
        if self._running and kill is not None:
            kill()
        if self.subscribe and self.engine:
            bus = self.bus or QDBusConnection.sessionBus()
            bus.disconnect(SERVICE, PATH, IFACE, "CurrentActivityChanged", self.activity_changed)

    @pyqtSlot(str)
    def activity_changed(self, activity: str) -> None:
        self.request(activity)

    def request(self, activity: str | None) -> None:
        if not self.engine or self._stopped:
            return
        self._pending = (activity,)
        self._retry_timer.stop()
        self._retry = self.retry_first
        self._debounce.start()

    # --- wnętrze ---------------------------------------------------------------------------

    def _launch(self) -> None:
        if self._running or self._pending is None or self._stopped:
            return  # w toku: żądanie czeka i ruszy po zakończeniu (wygrywa ostatnie)
        if not self.exists(self.engine):
            # Kopii silnika nie ma (jeszcze albo już) — żądanie przepada, następne zdarzenie spróbuje znowu.
            if not self._missing_reported:
                print(f"tide-daemon: wygląd aktywności: brak {self.engine} (make install-activity-look)", flush=True)
                self._missing_reported = True
            self._pending = None
            return
        self._missing_reported = False
        self._last, self._pending = self._pending, None
        (activity,) = self._last
        args = ["-s", self.engine, "apply", "--source", "daemon"]
        if activity:
            args += ["--activity", activity]
        self._running = True
        self.launcher(args, self._done)

    def _done(self, code: int, output: str) -> None:
        self._running = False
        for line in output.strip().splitlines():
            print(f"tide-daemon: wygląd aktywności: {line}", flush=True)
        if self._stopped:
            return
        if self._pending is not None:
            if not self._debounce.isActive():
                self._launch()
            return
        if code == TEMPFAIL:
            self._pending = self._last
            self._retry_timer.setInterval(self._retry)
            self._retry_timer.start()
            self._retry = min(self._retry * 2, self.retry_max)
            return
        self._retry = self.retry_first
        if code != 0:
            print(f"tide-daemon: wygląd aktywności: silnik zakończył się kodem {code}", flush=True)
