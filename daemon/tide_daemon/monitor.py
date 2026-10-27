"""Obserwacja dzienników zdarzeniami plików (QFileSystemWatcher) i lekkie odświeżanie w tle.

- zmiana pliku sesji → doczytanie nowych wpisów po krótkiej chwili (zbiera serie zapisów),
- zmiana katalogu → szukanie nowych plików sesji,
- co TICK_MS → przeliczenie stanów bez czytania dzienników (progi czasowe, procesy),
- co RESCAN_MS → pełne przejrzenie katalogów (np. wznowiona stara sesja Codexa w starym katalogu).
"""
import json
import time

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from . import attention, codex_app, processes, windows
from .agents import AgentScanner

DEBOUNCE_MS = 250
TICK_MS = 5000
RESCAN_MS = 30000


class AgentMonitor(QObject):
    changed = pyqtSignal()

    def __init__(self, scanner: AgentScanner, use_processes: bool = True, clock=time.time, parent=None):
        super().__init__(parent)
        self.scanner = scanner
        self.use_processes = use_processes
        self.clock = clock
        self._json = "[]"
        self.sessions: list[dict] = []   # ostatnia lista sesji (dla worktrees i powiadomień)
        self.procs = None                # ostatnia migawka procesów albo None
        self._dirty: set[str] = set()
        # funkcja(sessions) dopisująca pola od innych obszarów (np. newTask z /Tasks) albo None
        self.annotate = None
        self._discover = False

        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._file_changed)
        self.watcher.directoryChanged.connect(self._dir_changed)

        self._debounce = QTimer(self, singleShot=True, interval=DEBOUNCE_MS)
        self._debounce.timeout.connect(self._refresh)
        self._tick = QTimer(self, interval=TICK_MS)
        self._tick.timeout.connect(self.recompute)
        self._rescan = QTimer(self, interval=RESCAN_MS)
        self._rescan.timeout.connect(self.rescan)

    def start(self) -> None:
        self.rescan()
        self._tick.start()
        self._rescan.start()

    def sessions_json(self) -> str:
        return self._json

    def rescan(self) -> None:
        self.scanner.discover(self.clock())
        self.scanner.update()
        self._sync_watches()
        self.recompute()

    def recompute(self) -> None:
        procs = self._probe()
        sessions = self.scanner.sessions(self.clock(), procs)
        if self.annotate is not None:
            try:
                self.annotate(sessions)
            except Exception as exc:  # dodatek nie może zatrzymać listy sesji
                print(f"tide-daemon: dopisywanie pól sesji: {exc!r}", flush=True)
        self.sessions, self.procs = sessions, procs
        text = json.dumps(sessions, ensure_ascii=False)
        if text != self._json:
            self._json = text
            self.changed.emit()

    def activate(self, session_id: str) -> bool:
        procs = self._probe()
        self.scanner.sessions(self.clock(), procs)
        thread = self.scanner.app_threads.get(session_id)
        if thread:
            return self._open_in_app(session_id, thread, procs)
        if not self.use_processes:
            return False
        pid = self.scanner.bound_pids.get(session_id)
        target = windows.target_for(procs, pid) if pid else None
        if not target:
            return False
        try:
            return windows.activate(target)
        except Exception as exc:  # aktywacja okna nie może położyć usługi
            print(f"tide-daemon: nie udało się przejść do okna: {exc}", flush=True)
            return False

    def codex_threads(self):
        """Dla KRunnera: wątki Codex Desktop bez żywej sesji (available(), open_thread(id))."""
        monitor = self

        class Threads:
            @staticmethod
            def available() -> bool:
                return monitor.scanner.codex_app is not None and monitor.scanner.codex_app.available()

            @staticmethod
            def open_thread(thread_id: str) -> bool:
                return Threads.available() and monitor._open_in_app(None, thread_id, monitor._probe())

        return Threads()

    def _open_in_app(self, session_id: str | None, thread: str, procs) -> bool:
        """Codex Desktop: aplikacja przełącza się na wątek (codex://threads/<id>), a potem
        podnosimy jej okno — samo podniesienie pokazałoby ostatnio oglądaną rozmowę."""
        try:
            if not self.scanner.codex_app.open_thread(thread):
                return False
        except Exception as exc:  # uruchomienie programu nie może położyć usługi
            print(f"tide-daemon: nie udało się otworzyć wątku w Codex: {exc}", flush=True)
            return False
        pid = self.scanner.bound_pids.get(session_id) if session_id else None
        target = windows.target_for(procs, pid) if procs is not None and pid else None
        if not target or target["label"] != codex_app.LABEL:
            target = windows.app_target(procs, codex_app.WINDOW_PROCESSES, codex_app.LABEL)
        if target:  # aplikacja nie działała — sama pokaże nowe okno
            try:
                windows.activate(target)
            except Exception as exc:
                print(f"tide-daemon: nie udało się podnieść okna Codex: {exc}", flush=True)
        return True

    def activate_longest_waiting(self) -> bool:
        """Przejście do sesji czekającej najdłużej (skrót Meta+A). False, gdy nic nie czeka."""
        self.recompute()  # świeży stan i procesy — skrót wciska się teraz, nie 5 s temu
        session = attention.longest_waiting(self.sessions)
        return self.activate(session["id"]) if session else False

    def _probe(self):
        if not self.use_processes:
            return None
        return processes.probe(log_roots=self.scanner.codex_log_roots())

    def _file_changed(self, path: str) -> None:
        self._dirty.add(path)
        self._debounce.start()

    def _dir_changed(self, _path: str) -> None:
        self._discover = True
        self._debounce.start()

    def _refresh(self) -> None:
        if self._discover:
            self._discover = False
            self.scanner.discover(self.clock())
        dirty, self._dirty = self._dirty, set()
        for path in dirty:
            self.scanner.update(path)
        if not dirty:
            self.scanner.update()  # nowe pliki albo podmieniony plik statusu
        self._sync_watches()
        self.recompute()

    def _sync_watches(self) -> None:
        wanted = set(self.scanner.watched_files()) | set(self.scanner.watched_dirs())
        current = set(self.watcher.files()) | set(self.watcher.directories())
        stale = current - wanted
        if stale:
            self.watcher.removePaths(list(stale))
        missing = [p for p in wanted - current]
        if missing:
            self.watcher.addPaths(missing)
