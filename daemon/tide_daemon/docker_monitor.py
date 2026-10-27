"""Obszar Docker w usłudze: strumień zdarzeń w tle, odświeżanie, statystyki, dysk i akcje poza wątkiem D-Bus.

- osobny wątek trzyma `GET /events` (tylko kontenery); zdarzenie → po DEBOUNCE_MS odświeżenie
  migawki → Changed, gdy JSON się zmienił; zerwany strumień → ponowne połączenie z rosnącym
  odstępem (BACKOFF_MIN…BACKOFF_MAX), a w tym czasie odświeżanie co POLL_MS,
- przy działającym strumieniu i tak pełne odświeżenie co RESYNC_MS (na wypadek zgubionego zdarzenia),
- odświeżanie w jednym wątku roboczym, akcje w drugiej puli (wolne `stop` nie blokuje migawki),
  statystyki i dysk w trzeciej — wynik zawsze wraca do wątku głównego sygnałem Qt,
- Stats() tylko na żądanie (okno woła je, gdy jest widoczne), pamiętane STATS_TTL_S; Disk() — DISK_TTL_S,
- logi i powłoka w Konsoli tym samym mechanizmem co „Nowe zadanie” (tasks/terminal.py, launch.py):
  plik zadania z argv i karta w ostatnio używanym oknie albo nowe okno.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .docker.client import DEFAULT_SOCKET, DockerUnavailable

DEBOUNCE_MS = 300
POLL_MS = 10_000
RESYNC_MS = 300_000
BACKOFF_MIN = 1.0
BACKOFF_MAX = 30.0
STATS_TTL_S = 5.0
DISK_TTL_S = 30.0
CLAIM_TIMEOUT_S = 20.0  # tyle karta ma na przejęcie pliku zadania; potem plik jest wycofywany
SHELL_COMMAND = "command -v bash >/dev/null && exec bash || exec sh"


class DockerMonitor(QObject):
    changed = pyqtSignal()
    _finished = pyqtSignal(object, object)  # (co dalej, Future) — z wątku roboczego do głównego
    _event = pyqtSignal(str)                # ID kontenera ze strumienia zdarzeń
    _live = pyqtSignal(bool)                # strumień zdarzeń połączony / zerwany

    def __init__(self, engine, terminal=None, files=None, launchers=None, poll_ms: int = POLL_MS,
                 debounce_ms: int = DEBOUNCE_MS, resync_ms: int = RESYNC_MS, backoff=(BACKOFF_MIN, BACKOFF_MAX),
                 stats_ttl: float = STATS_TTL_S, disk_ttl: float = DISK_TTL_S, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.terminal = terminal      # TaskTerminal albo atrapa z open(plan) → "tab" | "window" | ""
        self.files = files            # TaskFiles — pliki zadań dla launch.py
        self.launchers = launchers
        self.backoff = backoff
        self.stats_ttl = stats_ttl
        self.disk_ttl = disk_ttl
        self.live = False
        self._snapshot = dict(engine.last)
        self._json = self._encode(self._snapshot)
        self._refreshing = False
        self._again = False
        self._stats = None            # (czas, wynik)
        self._stats_waiting: list = []
        self._disk = None
        self._disk_waiting: list = []
        self._stop = threading.Event()
        self._thread = None
        self._refresh_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-docker")
        self._action_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tide-docker-act")
        self._read_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tide-docker-read")
        self._finished.connect(self._deliver)
        self._event.connect(self._on_event)
        self._live.connect(self._on_live)
        self._debounce = QTimer(self, singleShot=True, interval=debounce_ms)
        self._debounce.timeout.connect(self.refresh)
        self._poll = QTimer(self, interval=max(50, poll_ms))
        self._poll.timeout.connect(self._tick)
        self._since_full = time.monotonic()
        self.resync_s = resync_ms / 1000.0
        self._logged = None

    # --- cykl życia ------------------------------------------------------------------------

    def start(self) -> None:
        self.refresh()
        self._poll.start()
        self._thread = threading.Thread(target=self._events_loop, name="tide-docker-events", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._poll.stop()
        self._debounce.stop()
        for pool in (self._refresh_pool, self._action_pool, self._read_pool):
            pool.shutdown(wait=False, cancel_futures=True)
        if self._thread is not None:
            self._thread.join(timeout=3)

    # --- odczyt ----------------------------------------------------------------------------

    def snapshot_json(self) -> str:
        return self._json

    def refresh(self) -> None:
        if self._refreshing:
            self._again = True
            return
        self._refreshing = True
        self._submit(self._refresh_pool, self.engine.refresh, self._after_refresh)

    def stats(self, reply) -> None:
        if self._stats and time.monotonic() - self._stats[0] < self.stats_ttl:
            reply(json.dumps(self._stats[1], ensure_ascii=False))
            return
        self._stats_waiting.append(reply)
        if len(self._stats_waiting) == 1:
            self._submit(self._read_pool, self.engine.stats, self._after_stats)

    def disk(self, reply) -> None:
        if self._disk and time.monotonic() - self._disk[0] < self.disk_ttl:
            reply(json.dumps(self._disk[1], ensure_ascii=False))
            return
        self._disk_waiting.append(reply)
        if len(self._disk_waiting) == 1:
            self._submit(self._read_pool, self.engine.disk, self._after_disk)

    # --- akcje -----------------------------------------------------------------------------

    def action(self, ident: str, action: str, reply) -> None:
        self._run(lambda: self.engine.action(ident, action), reply)

    def remove(self, ident: str, reply) -> None:
        self._run(lambda: self.engine.remove(ident), reply)

    def group_action(self, project: str, action: str, reply) -> None:
        self._run(lambda: self.engine.group_action(project, action), reply)

    def prune(self, selection: str, reply) -> None:
        self._run(lambda: self.engine.prune(selection), reply)

    def logs(self, ident: str, lines: int, reply) -> None:
        self._submit(self._action_pool, lambda: self.engine.logs(ident, int(lines)),
                     lambda res: reply(json.dumps(res or {"ok": False, "error": "błąd usługi", "lines": []},
                                                  ensure_ascii=False)))

    def _run(self, work, reply) -> None:
        def done(result):
            reply(json.dumps(result or {"ok": False, "error": "błąd usługi"}, ensure_ascii=False))
            self._stats = None
            self._disk = None
            self.refresh()  # zdarzenia i tak przyjdą, ale bez strumienia odświeżamy od razu
        self._submit(self._action_pool, work, done)

    # --- Konsole ---------------------------------------------------------------------------

    def docker_argv(self, *args: str) -> list[str]:
        search = self.launchers.search if self.launchers is not None else os.environ.get("PATH", "")
        argv = [shutil.which("docker", path=search) or "docker"]
        path = getattr(self.engine.client, "path", DEFAULT_SOCKET)
        if path and path != DEFAULT_SOCKET:
            argv += ["-H", f"unix://{path}"]  # karta Konsoli nie dziedziczy DOCKER_HOST usługi
        return argv + list(args)

    def open_logs(self, ident: str) -> bool:
        target = self.engine.find(ident)
        if target is None:
            return False
        return self._open_tab(target, self.docker_argv("logs", "-f", "--tail", "200", target["name"]))

    def open_shell(self, ident: str) -> bool:
        target = self.engine.find(ident)
        if target is None or not target["running"]:
            return False
        return self._open_tab(target, self.docker_argv("exec", "-it", target["name"], "sh", "-c", SHELL_COMMAND))

    def _open_tab(self, target: dict, argv: list[str]) -> bool:
        if self.terminal is None or self.files is None:
            return False
        cwd = self.launchers.home if self.launchers is not None else os.path.expanduser("~")
        try:
            task_file = self.files.write({"argv": argv, "cwd": cwd})
        except OSError as exc:
            print(f"tide-daemon: docker: nie udało się zapisać pliku zadania: {exc}", flush=True)
            return False
        plan = SimpleNamespace(cwd=cwd, title=f"docker · {target['name']}", task_file=task_file)
        try:
            mode = self.terminal.open(plan)
        except Exception as exc:  # Konsole to wygoda — nie może położyć usługi
            print(f"tide-daemon: docker: Konsole: {exc!r}", flush=True)
            mode = ""
        if not mode:
            self.files.cancel(task_file)
            return False
        # Nieprzejęty plik nie może czekać na inną kartę otwartą później w tym samym katalogu.
        QTimer.singleShot(int(CLAIM_TIMEOUT_S * 1000), lambda: self.files.cancel(task_file))
        return True

    # --- wnętrze ---------------------------------------------------------------------------

    @staticmethod
    def _encode(snapshot: dict) -> str:
        return json.dumps(snapshot, ensure_ascii=False)

    def _publish(self, snapshot: dict) -> None:
        snapshot = dict(snapshot, live=self.live and bool(snapshot.get("available")))
        self._snapshot = snapshot
        text = self._encode(snapshot)
        if text != self._json:
            self._json = text
            self.changed.emit()
        problem = snapshot.get("error") or ""
        if problem != self._logged:
            print(f"tide-daemon: Docker: {problem}" if problem else "tide-daemon: Docker: połączony", flush=True)
            self._logged = problem

    def _after_refresh(self, snapshot) -> None:
        self._refreshing = False
        self._since_full = time.monotonic()
        if snapshot is not None:
            self._publish(snapshot)
        if self._again:
            self._again = False
            self.refresh()

    def _after_stats(self, result) -> None:
        result = result or {"ok": False, "error": "błąd usługi", "containers": {}, "total": {}, "missing": []}
        if result.get("ok"):
            self._stats = (time.monotonic(), result)
        text = json.dumps(result, ensure_ascii=False)
        waiting, self._stats_waiting = self._stats_waiting, []
        for reply in waiting:
            reply(text)

    def _after_disk(self, result) -> None:
        result = result or {"ok": False, "error": "błąd usługi", "categories": [], "totalReclaimableBytes": 0}
        if result.get("ok"):
            self._disk = (time.monotonic(), result)
        text = json.dumps(result, ensure_ascii=False)
        waiting, self._disk_waiting = self._disk_waiting, []
        for reply in waiting:
            reply(text)

    def _tick(self) -> None:
        if not self.live or time.monotonic() - self._since_full >= self.resync_s:
            self.refresh()

    def _on_event(self, ident: str) -> None:
        self.engine.forget(ident)
        self._stats = None
        self._debounce.start()

    def _on_live(self, live: bool) -> None:
        self.live = live
        self.refresh()  # po połączeniu: nadrobienie tego, co działo się bez strumienia

    def _events_loop(self) -> None:
        low, high = self.backoff
        delay = low
        while not self._stop.is_set():
            state = {"connected": False}

            def connected():
                state["connected"] = True
                self._live.emit(True)

            try:
                self.engine.events(self._stop, lambda ident, _action: self._event.emit(ident), connected)
            except DockerUnavailable:
                pass
            except Exception as exc:  # strumień nie może zabić wątku — próbujemy dalej
                print(f"tide-daemon: docker: zdarzenia: {exc!r}", flush=True)
            if state["connected"]:
                if not self._stop.is_set():
                    self._live.emit(False)
                delay = low
            if self._stop.wait(delay):
                break
            if not state["connected"]:
                delay = min(delay * 2, high)

    def _submit(self, pool, work, then) -> None:
        try:
            future = pool.submit(work)
        except RuntimeError:  # pula zamknięta (koniec usługi)
            return
        future.add_done_callback(lambda f: self._finished.emit(then, f))

    def _deliver(self, then, future) -> None:
        if future.cancelled():
            return
        try:
            result = future.result()
        except Exception as exc:  # błąd w tle nie może położyć usługi
            print(f"tide-daemon: docker: {exc!r}", flush=True)
            result = None
        then(result)
