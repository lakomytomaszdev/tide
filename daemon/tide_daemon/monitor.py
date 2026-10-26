"""Obserwacja dzienników zdarzeniami plików (QFileSystemWatcher) i lekkie odświeżanie w tle.

- zmiana pliku sesji → doczytanie nowych wpisów po krótkiej chwili (zbiera serie zapisów),
- zmiana katalogu → szukanie nowych plików sesji,
- co TICK_MS → przeliczenie stanów bez czytania dzienników (progi czasowe, procesy),
- co RESCAN_MS → pełne przejrzenie katalogów (np. wznowiona stara sesja Codexa w starym katalogu).
"""
import json
import time

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from . import processes, windows
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
        self._dirty: set[str] = set()
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
        sessions = self.scanner.sessions(self.clock(), self._probe())
        text = json.dumps(sessions, ensure_ascii=False)
        if text != self._json:
            self._json = text
            self.changed.emit()

    def activate(self, session_id: str) -> bool:
        if not self.use_processes:
            return False
        procs = self._probe()
        self.scanner.sessions(self.clock(), procs)
        pid = self.scanner.bound_pids.get(session_id)
        target = windows.target_for(procs, pid) if pid else None
        if not target:
            return False
        try:
            return windows.activate(target)
        except Exception as exc:  # aktywacja okna nie może położyć usługi
            print(f"tide-daemon: nie udało się przejść do okna: {exc}", flush=True)
            return False

    def _probe(self):
        if not self.use_processes:
            return None
        return processes.probe(log_roots=(self.scanner.codex_sessions,))

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
