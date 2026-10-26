"""Obszar koordynacji w usłudze: tablice koordynatorów odświeżane w tle, D-Bus /Coordination.

- odczyt dzienników, planów i YouTracka idzie w jednym wątku roboczym (coordination/scanner.py),
  a wynik wraca do wątku głównego sygnałem Qt — D-Bus zawsze odpowiada od razu z gotowych danych,
- odświeżenie: co REFRESH_MS, po zmianie sesji agentów (najwyżej co MIN_GAP_MS) i na żądanie
  (Refresh() — także z pominięciem pamięci YouTracka),
- po każdej zmianie: sygnał Changed, plik dla raportu porannego (coordination/actions.py)
  i pole `coordination` przy sesjach koordynatorów w /Agents.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .coordination import actions, board as board_mod

REFRESH_MS = 20_000
MIN_GAP_MS = 4_000
START_DELAY_MS = 1_500


class CoordinationMonitor(QObject):
    changed = pyqtSignal()
    _finished = pyqtSignal(object, object)

    def __init__(self, scanner, sessions_source=lambda: [], launchers=None, activate=None, worktrees_source=None,
                 cache_file: str | None = None, clock=time.time, refresh_ms: int = REFRESH_MS,
                 start_delay_ms: int = START_DELAY_MS, parent=None):
        super().__init__(parent)
        self.scanner = scanner
        self.sessions_source = sessions_source      # funkcja() → sesje obszaru Agents
        self.launchers = launchers
        self.activate_session = activate            # AgentMonitor.activate(id) → bool
        self.worktrees_source = worktrees_source or (lambda: [])
        self.cache_file = cache_file
        self.clock = clock
        self.boards: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._summary = "[]"
        self._boards_json: dict[str, str] = {}
        self._cache_text = ""
        self._busy = False
        self._again = False
        self._force = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-coordination")
        self._finished.connect(self._deliver)
        self._timer = QTimer(self, interval=refresh_ms)
        self._timer.timeout.connect(self.refresh)
        self._soon = QTimer(self, singleShot=True, interval=MIN_GAP_MS)
        self._soon.timeout.connect(self.refresh)
        self._start = QTimer(self, singleShot=True, interval=start_delay_ms)
        self._start.timeout.connect(self.refresh)
        self.ready = False

    def start(self) -> None:
        self._start.start()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._soon.stop()
        self._start.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def sessions_changed(self) -> None:
        """Zmiana w /Agents (np. podagent skończył) — odświeżenie, ale nie częściej niż co MIN_GAP_MS."""
        if not self._soon.isActive():
            self._soon.start()

    # --- D-Bus ------------------------------------------------------------------------------

    def coordinators_json(self) -> str:
        return self._summary

    def board_json(self, coordinator_id: str) -> str:
        return self._boards_json.get(coordinator_id, "{}")

    def run_detail_json(self, coordinator_id: str, run_id: str) -> str:
        try:
            detail = self.scanner.run_detail(coordinator_id, run_id)
        except Exception as exc:  # odczyt dziennika nie może położyć usługi
            print(f"tide-daemon: koordynacja: wynik podagenta: {exc!r}", flush=True)
            detail = None
        return json.dumps(detail or {}, ensure_ascii=False)

    def activate(self, coordinator_id: str) -> bool:
        """Przejdź do koordynatora: okno działającej sesji, a gdy jej nie ma — wznowienie w Konsoli."""
        board = self._by_id.get(coordinator_id)
        if board is None:
            return False
        if self.activate_session is not None and board.get("live"):
            try:
                if self.activate_session(coordinator_id):
                    return True
            except Exception as exc:
                print(f"tide-daemon: koordynacja: przejście do sesji: {exc!r}", flush=True)
        if self.launchers is None or not board.get("cwd"):
            return False
        argv = self.launchers.resume_command(board["tool"], board["sessionId"], board["cwd"])
        return self.launchers.spawn(argv, board["cwd"])

    def open_diff(self, coordinator_id: str, ticket: str) -> bool:
        board = self._by_id.get(coordinator_id)
        if board is None or self.launchers is None:
            return False
        target = actions.diff_target(board, ticket, self.worktrees_source())
        if target is None:
            return False
        if self.launchers.terminal:
            return self.launchers.spawn(self.launchers.terminal_command(target["repo"], actions.diff_argv(target)),
                                        target["repo"])
        path = actions.worktree_of(board, ticket, self.worktrees_source()) or target["repo"]
        return self.launchers.open("rider", path)

    def open_worktree(self, coordinator_id: str, ticket: str) -> bool:
        board = self._by_id.get(coordinator_id)
        if board is None or self.launchers is None:
            return False
        path = actions.worktree_of(board, ticket, self.worktrees_source())
        return bool(path) and self.launchers.open("terminal", path)

    def show_window(self, coordinator_id: str = "") -> bool:
        return bool(self.launchers and self.launchers.show_sessions(coordinator_id))

    def annotate(self, sessions: list) -> None:
        actions.annotate(sessions, self.boards)

    def refresh(self, force_tracker: bool = False) -> None:
        self._force = self._force or force_tracker
        if self._busy:
            self._again = True
            return
        self._busy = True
        force, self._force = self._force, False
        live = [dict(s) for s in (self.sessions_source() or [])]
        future = self._pool.submit(lambda: self.scanner.refresh(live, force_tracker=force))
        future.add_done_callback(lambda f: self._finished.emit(None, f))

    # --- wnętrze ---------------------------------------------------------------------------

    def _deliver(self, _then, future) -> None:
        self._busy = False
        try:
            boards = future.result()
        except Exception as exc:  # błąd odczytu nie może położyć usługi
            print(f"tide-daemon: koordynacja: {exc!r}", flush=True)
            boards = None
        if boards is not None:
            self._publish(boards)
        if self._again:
            self._again = False
            self.refresh()

    def _publish(self, boards: list) -> None:
        self.boards = boards
        self._by_id = {b["id"]: b for b in boards}
        worktrees = self.worktrees_source() or []
        for b in boards:
            for t in b["tickets"]:
                t["worktree"] = actions.worktree_of(b, t["key"], worktrees)
        summary = json.dumps([board_mod.summary(b) for b in boards], ensure_ascii=False)
        texts = {b["id"]: json.dumps(b, ensure_ascii=False) for b in boards}
        changed = summary != self._summary or texts != self._boards_json or not self.ready
        self._summary, self._boards_json = summary, texts
        self.ready = True
        if self.cache_file:
            payload = actions.cache_payload(boards, self.clock())
            text = json.dumps(payload["coordinators"], ensure_ascii=False, sort_keys=True)
            if text != self._cache_text:
                try:
                    actions.write_cache(self.cache_file, payload)
                    self._cache_text = text
                except OSError as exc:
                    print(f"tide-daemon: koordynacja: nie zapisano pliku dla raportu: {exc}", flush=True)
        if changed:
            self.changed.emit()
