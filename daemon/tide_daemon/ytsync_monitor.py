"""Synchronizacja tablicy z YouTrackiem w usłudze (PT-7): zegar, wątek roboczy i import zgłoszeń.

Logika jest w board/ytsync.py (SyncEngine). Tu:
- BoardMonitor po każdym przeliczeniu migawki woła `observe(snapshot)` (stany, których chce Tide — z kart
  i z tablic koordynatorów) i `decorate(snapshot)` (stan synchronizacji przy kartach, konflikty w `errors`),
- zegar co TICK_MS pyta silnik, co jest do zrobienia (odliczanie 5 s, ponowienia), a sieć idzie w jednym
  wątku roboczym — wątek główny (D-Bus, Qt) nigdy nie czeka na YouTracka; wynik wraca sygnałem i przelicza
  migawkę tablicy (`changed`),
- zapis zaczyna się dopiero, gdy koordynacja ma pierwszy odczyt i minęło START_GRACE_S od startu —
  wcześniej karty nie mają jeszcze stanu z pracy agentów i wyglądałyby na cofnięte,
- import: lista zgłoszeń użytkownika (YouTrackIssues, odpowiedź D-Bus po pobraniu w wątku roboczym)
  i co `importCheckMinutes` ciche sprawdzenie, ile jest nowych (pasek okna: „N nowych zgłoszeń w YouTracku”).
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

TICK_MS = 1_000
START_GRACE_S = 20.0
IMPORT_FIRST_MS = 60_000


class YouTrackSyncMonitor(QObject):
    changed = pyqtSignal()
    _done = pyqtSignal(object, object)     # (funkcja w wątku głównym, future)

    def __init__(self, engine, coordinators=None, ready=None, tick_ms: int = TICK_MS,
                 start_grace_s: float = START_GRACE_S, import_first_ms: int = IMPORT_FIRST_MS, clock=time.monotonic,
                 parent=None):
        super().__init__(parent)
        self.engine = engine
        self.coordinators = coordinators or (lambda: [])
        self.ready_source = ready or (lambda: True)
        self.clock = clock
        self.start_grace_s = start_grace_s
        self._started = None
        self._stopped = False
        self._busy = False
        self._snapshot: dict = {}
        self._pool: ThreadPoolExecutor | None = None
        self._import_waiting: list = []
        self._done.connect(self._finish)
        self._tick = QTimer(self, interval=tick_ms)
        self._tick.timeout.connect(self.tick)
        self._import_timer = QTimer(self, singleShot=True, interval=import_first_ms)
        self._import_timer.timeout.connect(self._check_import)

    def start(self) -> None:
        self._stopped = False
        self._started = self.clock()
        self._tick.start()
        self._schedule_import(first=True)

    def stop(self) -> None:
        self._stopped = True      # wynik zapisu, który jeszcze trwa, nie wraca już do tablicy
        self._tick.stop()
        self._import_timer.stop()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    def ready(self) -> bool:
        if self._started is None or self.clock() - self._started < self.start_grace_s:
            return False
        try:
            return bool(self.ready_source())
        except Exception:
            return False

    # --- z BoardMonitor ------------------------------------------------------------------------

    def observe(self, snapshot: dict) -> None:
        self._snapshot = snapshot
        try:
            boards = list(self.coordinators() or [])
        except Exception as exc:  # koordynacja to dodatek — karty synchronizują się bez niej
            print(f"tide-daemon: YouTrack: koordynatorzy: {exc!r}", flush=True)
            boards = []
        self.engine.observe(snapshot, boards, ready=self.ready())

    def decorate(self, snapshot: dict) -> dict:
        return self.engine.decorate(snapshot)

    # --- zegar i wątek roboczy -----------------------------------------------------------------

    def tick(self) -> None:
        if self._busy or self._stopped:
            return
        if not self.engine.ready and self.ready() and self._snapshot:
            self.observe(self._snapshot)      # gotowość przyszła bez nowej migawki
        items = self.engine.due()
        if not items:
            return
        self._busy = True
        self._submit(self.engine.process, items, then=self._processed)

    def _processed(self, future) -> None:
        self._busy = False
        try:
            changed = future.result()
        except Exception as exc:  # błąd w kodzie nie może zatrzymać synchronizacji na zawsze
            print(f"tide-daemon: YouTrack: synchronizacja: {exc!r}", flush=True)
            changed = True
        if changed:
            self.changed.emit()

    def _submit(self, work, *args, then) -> bool:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-youtrack")
        try:
            future = self._pool.submit(work, *args)
        except RuntimeError:  # pula zamknięta (koniec usługi)
            return False
        future.add_done_callback(lambda f: self._done.emit(then, f))
        return True

    def _finish(self, then, future) -> None:
        if not future.cancelled() and not self._stopped:
            then(future)

    # --- import ----------------------------------------------------------------------------------

    def issues(self, snapshot: dict, reply, refresh: bool = True) -> None:
        """Zgłoszenia użytkownika do importu: reply(json) po pobraniu (wątek roboczy). Pokazane = przejrzane
        (licznik „nowych” spada do zera)."""
        def deliver(future):
            waiting, self._import_waiting = self._import_waiting, []
            try:
                result = future.result()
            except Exception as exc:
                print(f"tide-daemon: YouTrack: import: {exc!r}", flush=True)
                result = {"issues": None, "error": "błąd usługi — szczegóły w dzienniku"}
            text = self._issues_reply(self._snapshot or snapshot, result)
            for callback in waiting:
                callback(text)
            self.changed.emit()

        if not refresh and self.engine.imported.get("issues") is not None:
            reply(self._issues_reply(snapshot, self.engine.imported))
            self.changed.emit()
            return
        self._import_waiting.append(reply)
        if len(self._import_waiting) > 1:
            return   # pobieranie już trwa — ten sam wynik
        if not self._submit(self.engine.fetch_import, then=deliver):
            self._import_waiting = []

    def _issues_reply(self, snapshot: dict, result: dict) -> str:
        if result.get("issues") is None:
            return json.dumps({"ok": False, "error": "youtrack", "message": result.get("error") or "YouTrack nie odpowiada"},
                              ensure_ascii=False)
        items = self.engine.import_list(snapshot)
        self.engine.mark_seen([i["id"] for i in items])
        return json.dumps({"ok": True, "issues": items, "error": result.get("error", "")}, ensure_ascii=False)

    def _check_import(self) -> None:
        def done(future):
            if not future.cancelled() and future.exception() is None:
                self.changed.emit()
            self._schedule_import()
        if not self._submit(self.engine.fetch_import, then=done):
            return

    def _schedule_import(self, first: bool = False) -> None:
        minutes = int((self.engine.settings or {}).get("importCheckMinutes") or 0)
        if minutes <= 0:
            return
        if not first:
            self._import_timer.setInterval(minutes * 60_000)
        self._import_timer.start()
