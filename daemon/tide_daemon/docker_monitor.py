"""Obszar Docker w usłudze: strumień zdarzeń w tle, odświeżanie, statystyki, dysk i akcje poza wątkiem D-Bus.

- osobny wątek trzyma `GET /events` (tylko kontenery); zdarzenie → po DEBOUNCE_MS odświeżenie
  migawki → Changed, gdy JSON się zmienił; zerwany strumień → ponowne połączenie z rosnącym
  odstępem (BACKOFF_MIN…BACKOFF_MAX), a w tym czasie odświeżanie co POLL_MS,
- przy działającym strumieniu i tak pełne odświeżenie co RESYNC_MS (na wypadek zgubionego zdarzenia),
- odświeżanie w jednym wątku roboczym, akcje w drugiej puli (wolne `stop` nie blokuje migawki),
  statystyki i dysk w trzeciej — wynik zawsze wraca do wątku głównego sygnałem Qt,
- Stats() tylko na żądanie (okno woła je, gdy jest widoczne), pamiętane STATS_TTL_S; Disk() — DISK_TTL_S,
- Prune i GroupAction to zadania: wywołanie od razu oddaje `{ok, job}`, a wynik przychodzi sygnałem
  actionFinished(job, json) — porządki i zatrzymywanie projektu trwają dłużej niż limit odpowiedzi D-Bus,
- wynik Stats/Disk liczony przed końcem akcji (albo przed zdarzeniem) nie trafia do pamięci (licznik zmian),
- logi i powłoka w Konsoli tym samym mechanizmem co „Nowe zadanie” (tasks/terminal.py, launch.py):
  plik zadania z argv i karta w ostatnio używanym oknie albo nowe okno. Każda karta dostaje własny,
  pusty katalog roboczy, bo karta z profilu przejmuje najstarsze zadanie dla swojego katalogu — dwa
  szybkie OpenShell z tym samym katalogiem mogłyby się zamienić kontenerami.
"""
from __future__ import annotations

import itertools
import json
import os
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .docker import disk as disk_mod
from .docker import snapshot as snap
from .docker.client import DEFAULT_SOCKET, DockerUnavailable
from .docker.engine import ACTIONS, REPLY_BUDGET

DEBOUNCE_MS = 300
POLL_MS = 10_000
RESYNC_MS = 300_000
BACKOFF_MIN = 1.0
BACKOFF_MAX = 30.0
STATS_TTL_S = 5.0
DISK_TTL_S = 30.0
CLAIM_TIMEOUT_S = 20.0  # tyle karta ma na przejęcie pliku zadania; potem plik jest wycofywany
# Puste katalogi kart sprzątamy przy następnej karcie, gdy są starsze niż 10 min (dłużej niż przejęcie
# zadania): po przejęciu launch.py przechodzi do katalogu domowego, więc nikt w nich nie zostaje.
TAB_DIR_TTL_S = 600
SHELL_COMMAND = "command -v bash >/dev/null && exec bash || exec sh"


class DockerMonitor(QObject):
    changed = pyqtSignal()
    actionFinished = pyqtSignal(str, str)   # (job, wynik JSON) — Prune i GroupAction
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
        self._again_full = False
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
        self._generation = 0           # rośnie przy każdej akcji i zdarzeniu — stary wynik Stats/Disk nie trafia do pamięci
        self._jobs = itertools.count(1)
        self._job_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-docker-job")

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
        for pool in (self._refresh_pool, self._action_pool, self._read_pool, self._job_pool):
            pool.shutdown(wait=False, cancel_futures=True)
        if self._thread is not None:
            self._thread.join(timeout=3)

    # --- odczyt ----------------------------------------------------------------------------

    def snapshot_json(self) -> str:
        return self._json

    def badge_json(self) -> str:
        """Plakietka widoku Docker w pasku okna Tide: JSON {text, tone} (docker/snapshot.py badge)."""
        return json.dumps(snap.badge(self._snapshot), ensure_ascii=False)

    def show_window(self, ident: str = "") -> bool:
        """Okno Tide na widoku Docker; ident — kontener (ID, początek ID, nazwa) albo grupa z migawki
        („compose:<projekt>”, „standalone”, „testcontainers”), który widok odszuka i podświetli. Nieznany
        element nie trafia do polecenia — okno otwiera się wtedy na samym widoku."""
        item = ""
        ident = (ident or "").strip()[:200]
        if ident:
            groups = {g.get("id") for g in self._snapshot.get("groups") or []}
            if ident in groups:
                item = ident
            else:
                target = self.engine.find(ident)
                item = target["id"] if target else ""
        return bool(self.launchers and self.launchers.show_view("docker", item))

    def refresh(self, full: bool = False) -> None:
        if self._refreshing:
            self._again = True
            self._again_full = self._again_full or full
            return
        self._refreshing = True
        self._submit(self._refresh_pool, lambda: self.engine.refresh(full=full), self._after_refresh)

    def stats(self, reply) -> None:
        if self._stats and time.monotonic() - self._stats[0] < self.stats_ttl:
            reply(json.dumps(self._stats[1], ensure_ascii=False))
            return
        self._stats_waiting.append(reply)
        if len(self._stats_waiting) == 1:
            generation = self._generation
            self._submit(self._read_pool, self.engine.stats, lambda res: self._after_stats(res, generation))

    def disk(self, reply) -> None:
        if self._disk and time.monotonic() - self._disk[0] < self.disk_ttl:
            reply(json.dumps(self._disk[1], ensure_ascii=False))
            return
        self._disk_waiting.append(reply)
        if len(self._disk_waiting) == 1:
            generation = self._generation
            self._submit(self._read_pool, self.engine.disk, lambda res: self._after_disk(res, generation))

    # --- akcje -----------------------------------------------------------------------------

    # Pojedyncze akcje mają jeden termin od chwili wywołania D-Bus (REPLY_BUDGET): czekanie w kolejce
    # wątków też się w nim mieści, więc piąta akcja przy czterech wolnych `stop` dostaje odmowę na czas,
    # a nie odpowiedź po limicie klienta.

    def action(self, ident: str, action: str, reply) -> None:
        deadline = time.monotonic() + REPLY_BUDGET
        self._run(lambda: self.engine.action(ident, action, deadline), reply)

    def remove(self, ident: str, reply) -> None:
        deadline = time.monotonic() + REPLY_BUDGET
        self._run(lambda: self.engine.remove(ident, deadline), reply)

    def group_action(self, project: str, action: str) -> str:
        """Zadanie w tle; od razu JSON {ok, error, job}. Wynik: actionFinished(job, json)."""
        if action not in ACTIONS:
            return json.dumps({"ok": False, "error": f"nieznana akcja: {action}", "job": ""}, ensure_ascii=False)
        if not project:
            return json.dumps({"ok": False, "error": "brak nazwy projektu", "job": ""}, ensure_ascii=False)
        return self._job("group", lambda: self.engine.group_action(project, action))

    def prune(self, selection: str) -> str:
        """Zadanie w tle; od razu JSON {ok, error, job} — zła lista kategorii to odmowa bez zadania."""
        _chosen, problem = disk_mod.parse_selection(selection)
        if problem:
            return json.dumps({"ok": False, "error": problem, "job": ""}, ensure_ascii=False)
        return self._job("prune", lambda: self.engine.prune(selection))

    def _job(self, kind: str, work) -> str:
        job = f"{kind}-{next(self._jobs)}-{secrets.token_hex(3)}"
        self._generation += 1

        def done(result):
            result = dict(result or {"ok": False, "error": "błąd usługi"}, job=job)
            self._invalidate()
            self.actionFinished.emit(job, json.dumps(result, ensure_ascii=False))
            self.refresh()
        self._submit(self._job_pool, work, done)
        return json.dumps({"ok": True, "error": "", "job": job}, ensure_ascii=False)

    def logs(self, ident: str, lines: int, reply) -> None:
        deadline = time.monotonic() + REPLY_BUDGET
        self._submit(self._action_pool, lambda: self.engine.logs(ident, int(lines), deadline),
                     lambda res: reply(json.dumps(res or {"ok": False, "error": "błąd usługi", "lines": []},
                                                  ensure_ascii=False)))

    def _run(self, work, reply) -> None:
        self._generation += 1

        def done(result):
            self._invalidate()
            reply(json.dumps(result or {"ok": False, "error": "błąd usługi"}, ensure_ascii=False))
            self.refresh()  # zdarzenia i tak przyjdą, ale bez strumienia odświeżamy od razu
        self._submit(self._action_pool, work, done)

    def _invalidate(self) -> None:
        self._generation += 1
        self._stats = None
        self._disk = None

    # --- Konsole ---------------------------------------------------------------------------

    def docker_argv(self, *args: str) -> list[str]:
        search = self.launchers.search if self.launchers is not None else os.environ.get("PATH", "")
        argv = [shutil.which("docker", path=search) or "docker"]
        # Zawsze ten sam socket co usługa: karta Konsoli nie dziedziczy DOCKER_HOST usługi, a powłoka
        # użytkownika może mieć inny (albo aktywny `docker context`).
        path = getattr(self.engine.client, "path", "") or DEFAULT_SOCKET
        return argv + ["-H", f"unix://{path}"] + list(args)

    def open_logs(self, ident: str) -> bool:
        target = self.engine.find(ident)
        if target is None:
            return False
        # Pełne ID, nie nazwa: nazwę można w międzyczasie nadać innemu kontenerowi.
        return self._open_tab(target, self.docker_argv("logs", "-f", "--tail", "200", target["id"]))

    def open_shell(self, ident: str) -> bool:
        target = self.engine.find(ident)
        if target is None or not target["running"]:
            return False
        return self._open_tab(target, self.docker_argv("exec", "-it", target["id"], "sh", "-c", SHELL_COMMAND))

    def _open_tab(self, target: dict, argv: list[str]) -> bool:
        if self.terminal is None or self.files is None:
            return False
        try:
            cwd = self._tab_dir()
            home = self.launchers.home if self.launchers is not None else os.path.expanduser("~")
            # cwd — własny katalog karty (do przejęcia zadania), workdir — tam idzie polecenie i zostaje powłoka.
            task_file = self.files.write({"argv": argv, "cwd": cwd, "workdir": home})
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

    def _tab_dir(self) -> str:
        """Własny, pusty katalog karty obok plików zadań (0700): karta z profilu przejmuje tylko swoje zadanie."""
        root = os.path.join(os.path.dirname(self.files.folder), "docker-tabs")
        os.makedirs(root, mode=0o700, exist_ok=True)
        now = time.time()
        try:
            for name in os.listdir(root):
                old = os.path.join(root, name)
                try:
                    if now - os.stat(old).st_mtime > TAB_DIR_TTL_S:
                        os.rmdir(old)  # tylko pusty; karta mogła w nim coś zostawić — wtedy zostaje
                except OSError:
                    continue
        except OSError:
            pass
        path = os.path.join(root, f"{time.time_ns():020d}-{secrets.token_hex(4)}")
        os.mkdir(path, 0o700)
        return path

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
        if snapshot is not None:
            self._publish(snapshot)
        if self._again:
            full, self._again, self._again_full = self._again_full, False, False
            self.refresh(full)

    def _after_stats(self, result, generation: int) -> None:
        result = result or {"ok": False, "error": "błąd usługi", "containers": {}, "total": {}, "missing": []}
        if result.get("ok") and generation == self._generation:
            self._stats = (time.monotonic(), result)
        text = json.dumps(result, ensure_ascii=False)
        waiting, self._stats_waiting = self._stats_waiting, []
        for reply in waiting:
            reply(text)

    def _after_disk(self, result, generation: int) -> None:
        result = result or {"ok": False, "error": "błąd usługi", "categories": [], "totalReclaimableBytes": 0}
        if result.get("ok") and generation == self._generation:
            self._disk = (time.monotonic(), result)
        text = json.dumps(result, ensure_ascii=False)
        waiting, self._disk_waiting = self._disk_waiting, []
        for reply in waiting:
            reply(text)

    def _tick(self) -> None:
        if not self.live:
            self.refresh(full=True)  # bez zdarzeń nie wiemy, który kontener się zmienił
        elif time.monotonic() - self._since_full >= self.resync_s:
            self._since_full = time.monotonic()
            self.refresh(full=True)

    def _on_event(self, ident: str) -> None:
        self.engine.forget(ident)
        self._generation += 1
        self._stats = None
        self._debounce.start()

    def _on_live(self, live: bool) -> None:
        self.live = live
        self.refresh(full=True)  # po połączeniu: nadrobienie tego, co działo się bez strumienia

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
