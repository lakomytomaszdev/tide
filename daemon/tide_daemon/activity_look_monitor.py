"""Wygląd aktywności w usłudze: po każdej zmianie aktywności nakłada wygląd docelowej (PT-35).

Każda droga przełączenia (skrót, Meta+Q, widżet, KDE Connect) kończy się sygnałem
org.kde.ActivityManager.Activities.CurrentActivityChanged — usługa go słucha i uruchamia silnik
`activity_look.py apply --activity ID` z kopii w $XDG_DATA_HOME/tide/activity-look (make
install-activity-look; decyzja 3: kopia, nie dowiązanie). Silnik porównuje cały wygląd i nakłada
tylko różnice, w stałej kolejności, z dziennikiem w $XDG_STATE_HOME/tide/activity-look.json.

Kolejka: jedno nałożenie naraz; zdarzenia z krótkiego okna (debounce) i te, które przyszły w trakcie
nałożenia, zlewają się w jedno — wygrywa ostatnia aktywność. Przy starcie usługi (logowanie)
silnik dostaje `apply` bez aktywności: porównuje pulpit z wyglądem bieżącej aktywności i naprawia
różnice, a przy okazji dokańcza przełączenie przerwane przed wylogowaniem. Kod 75 (usługi D-Bus
jeszcze nie ma) to ponowienie z rosnącą przerwą, bez limitu prób i bez wpływu na licznik porażek;
inny błąd czeka na następne zdarzenie.

Wyłącznik awaryjny (PT-495): kod 76 (plasmashell przerwał wywołanie, PT-494) albo FAILURE_LIMIT
zakończeń z kodem innym niż 0 i 75 w oknie FAILURE_WINDOW (od pierwszej porażki w oknie) wyłącza
nakładanie — nie ma ponowienia, a oczekujące żądanie przepada. Start usługi, ponowienia i pojawienie
się plasmashell go nie zdejmują; robi to dopiero CurrentActivityChanged (zeruje licznik i kolejkuje
nową aktywność). Wyłącznik jest w dzienniku silnika, w kluczu `breaker` (powód, czas, kod, liczba
porażek; inne klucze zostają, zapis atomowy): nowy proces usługi, np. aktywowany przez D-Bus z restartującej
się Plasmy, nie nakłada przy starcie. Monitor pisze do dziennika tylko wtedy, gdy silnik nie biegnie.

Odczekanie: monitor obserwuje org.kde.plasmashell na szynie sesji. Pierwsze nałożenie po rejestracji
rusza dopiero po SHELL_SETTLE_MS; wyrejestrowanie wstrzymuje uruchamianie (trwającego nałożenia nie
zabija), a ponowna rejestracja uzbraja odczekanie od nowa. Zegary idą przez fabrykę `timer`.

Silnik działa w osobnym procesie: plasma-apply-* trwają chwilę i nie mogą blokować pętli usługi,
a błąd w nim nie kładzie usługi. Istnienie kopii silnika jest sprawdzane przy każdym uruchomieniu
(make install-activity-look i uninstall-activity-look w trakcie działania usługi). Zatrzymanie
usługi zabija trwające nałożenie — dziennik zostaje w stanie „applying”, a dokończy je następny start.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSlot
from PyQt6.QtDBus import QDBusConnection, QDBusServiceWatcher

SERVICE = "org.kde.ActivityManager"
PATH = "/ActivityManager/Activities"
IFACE = "org.kde.ActivityManager.Activities"
SHELL = "org.kde.plasmashell"
TEMPFAIL = 75
PLASMASHELL_LOST = 76  # plasmashell przerwał wywołanie (PT-494)
DEBOUNCE_MS = 400
RETRY_MS = (2_000, 60_000)  # pierwsza i najdłuższa przerwa przed ponowieniem
SHELL_SETTLE_MS = 5_000  # odczekanie po rejestracji plasmashell, zanim ruszy nałożenie
FAILURE_LIMIT = 3  # tyle porażek (kod inny niż 0 i 75) w oknie włącza wyłącznik
FAILURE_WINDOW = 120  # sekundy, liczone od pierwszej porażki w oknie
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


def default_journal() -> str:
    """Dziennik silnika $XDG_STATE_HOME/tide/activity-look.json (tu też stoi wyłącznik awaryjny)."""
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state, "tide", "activity-look.json")


def read_journal(path: str) -> dict:
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_journal(path: str, data: dict) -> None:
    """Zapis atomowy (plik tymczasowy + rename), jak w silniku."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


class ShellWatcher(QObject):
    """Obserwator org.kde.plasmashell na szynie: watch(callback(zarejestrowany)) → stan początkowy."""

    def __init__(self, bus: QDBusConnection | None = None, parent=None):
        super().__init__(parent)
        self.bus = bus
        self._watcher = None

    def watch(self, callback) -> bool:
        bus = self.bus or QDBusConnection.sessionBus()
        flags = QDBusServiceWatcher.WatchModeFlag.WatchForRegistration | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration
        self._watcher = QDBusServiceWatcher(SHELL, bus, flags, self)
        self._watcher.serviceRegistered.connect(lambda _name: callback(True))
        self._watcher.serviceUnregistered.connect(lambda _name: callback(False))
        return bool(bus.interface().isServiceRegistered(SHELL).value())

    def stop(self) -> None:
        if self._watcher is not None:
            self._watcher.deleteLater()
            self._watcher = None


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
                 exists=os.path.isfile, timer=qt_timer, journal: str | None = None, watcher=None,
                 settle_ms: int = SHELL_SETTLE_MS, now=time.monotonic, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.launcher = launcher or EngineProcess(parent=self)
        self.bus = bus
        self.subscribe = subscribe
        self.exists = exists
        self.journal = journal or default_journal()
        self.now = now
        self._watcher = watcher  # obserwator plasmashell (wstrzykiwany w testach); bez niego i bez subskrypcji brak odczekania
        self._gated = watcher is not None or subscribe
        self._shell_ready = not self._gated  # plasmashell zarejestrowany i odczekany
        self._missing_reported = False
        self.retry_first, self.retry_max = retry_ms
        self._retry = self.retry_first
        self._pending: tuple[str | None] | None = None  # ostatnie żądanie, jeszcze nieuruchomione
        self._last: tuple[str | None] | None = None     # żądanie w toku (do ponowienia)
        self._running = False
        self._stopped = False
        self._failures = 0                # porażki (kod inny niż 0 i 75) w bieżącym oknie
        self._window_start = 0.0
        self.breaker: dict | None = None  # włączony wyłącznik awaryjny (to samo, co klucz `breaker` w dzienniku)
        self._debounce = timer(self, debounce_ms, self._launch)
        self._retry_timer = timer(self, self.retry_first, self._launch)
        self._settle = timer(self, settle_ms, self._settled)

    @property
    def breaker_tripped(self) -> bool:
        return self.breaker is not None

    def start(self) -> None:
        if not self.engine:
            print("tide-daemon: wygląd aktywności wyłączony (brak silnika — make install-activity-look)", flush=True)
            return
        if self.subscribe:
            bus = self.bus or QDBusConnection.sessionBus()
            if not bus.connect(SERVICE, PATH, IFACE, "CurrentActivityChanged", self.activity_changed):
                print("tide-daemon: wygląd aktywności: nie da się słuchać CurrentActivityChanged", flush=True)
        if self._gated:
            if self._watcher is None:
                self._watcher = ShellWatcher(self.bus, self)
            self._shell_ready = False
            if self._watcher.watch(self._shell_changed):
                self._settle.start()
        stored = read_journal(self.journal).get("breaker")
        if isinstance(stored, dict):
            # Wyłącznik z poprzedniego procesu: nie nakładamy przy starcie (usługę mogła aktywować restartująca się Plasma).
            self.breaker = stored
            print(f"tide-daemon: wygląd aktywności: wyłącznik awaryjny — {stored.get('reason', '?')}; "
                  "do następnej zmiany aktywności", flush=True)
            return
        self.request(CURRENT)  # logowanie: porównanie z wyglądem bieżącej aktywności i naprawa

    def stop(self) -> None:
        self._stopped = True
        self._debounce.stop()
        self._retry_timer.stop()
        self._settle.stop()
        if self._watcher is not None:
            self._watcher.stop()
        kill = getattr(self.launcher, "kill", None)
        if self._running and kill is not None:
            kill()
        if self.subscribe and self.engine:
            bus = self.bus or QDBusConnection.sessionBus()
            bus.disconnect(SERVICE, PATH, IFACE, "CurrentActivityChanged", self.activity_changed)

    @pyqtSlot(str)
    def activity_changed(self, activity: str) -> None:
        if self.breaker is not None:
            self._lift_breaker()
        self.request(activity)

    def request(self, activity: str | None) -> None:
        if not self.engine or self._stopped:
            return
        self._pending = (activity,)
        self._retry_timer.stop()
        self._retry = self.retry_first
        self._debounce.start()

    # --- wnętrze ---------------------------------------------------------------------------

    def _shell_changed(self, registered: bool) -> None:
        """Rejestracja albo zniknięcie plasmashell: wstrzymuje uruchamianie (trwającego nałożenia nie zabija),
        a rejestracja uzbraja odczekanie od nowa."""
        if self._stopped:
            return
        self._shell_ready = False
        self._settle.stop()
        if registered:
            self._settle.start()

    def _settled(self) -> None:
        self._shell_ready = True
        if self._pending is not None and not self._debounce.isActive():
            self._launch()

    def _lift_breaker(self) -> None:
        self.breaker = None
        self._failures = 0
        if self._running:
            return  # dziennik należy wtedy do silnika, a jego pełne apply i tak przepisuje go bez klucza
        data = read_journal(self.journal)
        if data.pop("breaker", None) is not None:
            self._save_journal(data)

    def _save_journal(self, data: dict) -> None:
        try:
            write_journal(self.journal, data)
        except OSError as error:
            print(f"tide-daemon: wygląd aktywności: nie da się zapisać {self.journal}: {error}", flush=True)

    def _count_failure(self, code: int) -> None:
        """Porażka (kod inny niż 0 i 75); okno liczy się od pierwszej porażki w oknie. Włącza wyłącznik."""
        at = self.now()
        if self._failures == 0 or at - self._window_start > FAILURE_WINDOW:
            self._window_start, self._failures = at, 0
        self._failures += 1
        if code == PLASMASHELL_LOST:
            reason = f"plasmashell przerwał wywołanie (kod {code})"
        elif self._failures >= FAILURE_LIMIT:
            reason = f"{self._failures} porażki silnika w ciągu {FAILURE_WINDOW} s (ostatni kod {code})"
        else:
            return
        self.breaker = {"reason": reason, "since": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "code": code, "failures": self._failures}
        self._pending = None
        self._debounce.stop()
        self._retry_timer.stop()
        print(f"tide-daemon: wygląd aktywności: wyłącznik awaryjny — {reason}; do następnej zmiany aktywności",
              flush=True)
        data = read_journal(self.journal)  # silnik nie biegnie: kończy się właśnie jego nałożenie
        data["breaker"] = self.breaker
        self._save_journal(data)

    def _launch(self) -> None:
        if self._running or self._pending is None or self._stopped:
            return  # w toku: żądanie czeka i ruszy po zakończeniu (wygrywa ostatnie)
        if self.breaker is not None:
            self._pending = None  # do zmiany aktywności nic nie rusza silnika
            return
        if not self._shell_ready:
            return  # plasmashell nie ma albo jeszcze się ustala: żądanie czeka na koniec odczekania
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
        if code == 0:
            self._failures = 0
        elif code != TEMPFAIL:  # 75 nie liczy się do progu i nie zeruje licznika
            self._count_failure(code)
            if self.breaker is not None:
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
