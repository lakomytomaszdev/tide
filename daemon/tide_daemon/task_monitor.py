"""Obszar „Nowe zadanie” w usłudze: git i dysk w wątku roboczym, D-Bus Konsoli w wątku głównym.

Start zadania (start):
1. w tle: Planner.prepare — sprawdzenie, `git worktree add`, plik zadania,
2. w wątku głównym: karta Konsoli (TaskTerminal.open — newSession albo nowe okno),
3. co CLAIM_POLL_MS: czy launch.py w karcie przejął plik zadania; po przejęciu odpowiedź „ok”,
   zapamiętanie wyboru i odświeżenie listy worktrees,
4. bez przejęcia w czasie claim_timeout albo gdy Konsoli nie dało się otworzyć — wycofanie
   (w tle: plik zadania, worktree, nowa gałąź) i odpowiedź z błędem.
Wątek główny nigdy nie czeka na gita.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .tasks import Planner, TaskError, list_repos
from .tasks.core import CLAIM_TIMEOUT, TaskFiles, failure

CLAIM_POLL_MS = 100


class TaskMonitor(QObject):
    _finished = pyqtSignal(object, object)

    def __init__(self, planner: Planner, terminal, launchers=None, worktrees=None,
                 claim_timeout: float = CLAIM_TIMEOUT, parent=None):
        super().__init__(parent)
        self.planner = planner
        self.terminal = terminal
        self.launchers = launchers
        self.worktrees = worktrees          # WorktreeMonitor albo None
        self.claim_timeout = claim_timeout
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-tasks")
        self._finished.connect(self._deliver)
        self._polls: set = set()

    def stop(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # --- odczyt --------------------------------------------------------------------------

    def repos(self, reply) -> None:
        """Repozytoria od ostatnio aktywnych. Z gotowej listy worktrees, gdy usługa już ją ma;
        inaczej (tuż po starcie) — jednorazowy przegląd w tle."""
        items = self.worktrees.scanner.snapshot() if self.worktrees is not None else []
        if items:
            reply(json.dumps(list_repos(items), ensure_ascii=False))
            return

        def work():
            from .worktrees import WorktreeScanner
            scanner = WorktreeScanner(self.planner.workspace, self.planner.max_depth, git=self.planner.git)
            scanner.refresh()
            return list_repos(scanner.snapshot())

        self._submit(work, lambda repos: reply(json.dumps(repos or [], ensure_ascii=False)))

    def defaults_json(self) -> str:
        return json.dumps(self.planner.defaults(), ensure_ascii=False)

    def suggest(self, repo_path: str, tool: str, prompt: str, reply) -> None:
        def work():
            try:
                return {"ok": True, "error": "", "message": "", **self.planner.suggest(repo_path, tool, prompt)}
            except TaskError as exc:
                return {"ok": False, "error": exc.code, "message": exc.message, "branch": "", "path": "", "base": ""}
        self._submit(work, lambda res: reply(json.dumps(res or {"ok": False, "error": "error",
                                                                "message": "błąd usługi"}, ensure_ascii=False)))

    def show_dialog(self) -> bool:
        return bool(self.launchers and self.launchers.show_task_dialog())

    # --- start ---------------------------------------------------------------------------

    def start(self, request_text: str, reply) -> None:
        try:
            request = json.loads(request_text or "")
        except ValueError:
            request = None
        if not isinstance(request, dict):
            reply(json.dumps(failure(TaskError("bad-request")), ensure_ascii=False))
            return

        def work():
            self.planner.files.sweep()
            try:
                return self.planner.prepare(request)
            except TaskError as exc:
                return exc

        self._submit(work, lambda plan: self._launch(plan, reply))

    def _launch(self, plan, reply) -> None:
        if plan is None:
            reply(json.dumps(failure(TaskError("launch", detail=" (błąd usługi)")), ensure_ascii=False))
            return
        if isinstance(plan, TaskError):
            reply(json.dumps(failure(plan), ensure_ascii=False))
            return
        try:
            mode = self.terminal.open(plan)
        except Exception as exc:  # błąd Konsoli nie może zostawić worktree bez agenta
            print(f"tide-daemon: zadania: Konsole: {exc!r}", flush=True)
            mode = ""
        if not mode:
            self._fail(plan, reply, " (nie udało się otworzyć Konsoli)")
            return
        deadline = time.monotonic() + self.claim_timeout
        timer = QTimer(self, interval=CLAIM_POLL_MS)
        self._polls.add(timer)

        def check():
            if TaskFiles.claimed(plan.task_file):
                self._done(timer)
                self.planner.remember(plan)
                if self.worktrees is not None and plan.worktree:
                    self.worktrees.refresh()
                reply(json.dumps(plan.result(mode), ensure_ascii=False))
            elif time.monotonic() >= deadline:
                self._done(timer)
                if TaskFiles.cancel(plan.task_file):
                    self._fail(plan, reply, f" w {int(self.claim_timeout)} s")
                else:  # launcher zdążył w ostatniej chwili
                    check_late()

        def check_late():
            self.planner.remember(plan)
            if self.worktrees is not None and plan.worktree:
                self.worktrees.refresh()
            reply(json.dumps(plan.result(mode), ensure_ascii=False))

        timer.timeout.connect(check)
        timer.start()

    def _done(self, timer: QTimer) -> None:
        timer.stop()
        self._polls.discard(timer)
        timer.deleteLater()

    def _fail(self, plan, reply, detail: str) -> None:
        error = TaskError("launch", detail=detail)

        def undo():
            return self.planner.rollback(plan)

        def answer(problems):
            result = failure(error)
            if problems:
                result["message"] += "; nie udało się posprzątać: " + "; ".join(problems)
            reply(json.dumps(result, ensure_ascii=False))
            if self.worktrees is not None:
                self.worktrees.refresh()

        self._submit(undo, answer)

    # --- wnętrze -------------------------------------------------------------------------

    def _submit(self, work, then) -> None:
        future = self._pool.submit(work)
        future.add_done_callback(lambda f: self._finished.emit(then, f))

    def _deliver(self, then, future) -> None:
        try:
            result = future.result()
        except Exception as exc:  # błąd w tle nie może położyć usługi
            print(f"tide-daemon: zadania: {exc!r}", flush=True)
            result = None
        then(result)
