"""Obserwacja dzienników zdarzeniami plików (QFileSystemWatcher) i lekkie odświeżanie w tle.

- zmiana pliku sesji → doczytanie nowych wpisów po krótkiej chwili (zbiera serie zapisów),
- zmiana katalogu → szukanie nowych plików sesji,
- co TICK_MS → przeliczenie stanów bez czytania dzienników (progi czasowe, procesy),
- co RESCAN_MS → pełne przejrzenie katalogów (np. wznowiona stara sesja Codexa w starym katalogu).

Procesy (K-98 #4, PT-345): `processes.probe` biegnie w wątku sondy (`procprobe.ProcessProber`), nie na wątku
głównym, który obsługuje D-Bus. Przeliczenie stanu bierze ostatnią opublikowaną migawkę procesów i prosi o nową;
jej wynik wraca sygnałem do wątku głównego i przelicza listę jeszcze raz. Każda sesja ma jawny stan migawki
(`processes`: `none` przed pierwszym wynikiem, `fresh`, `stale` przy zawieszonej sondzie, `error`), a nie pustą
listę procesów udającą, że agent nie działa. Akcje użytkownika (aktywacja, otwarcie wątku, Meta+A) czekają krótko
i jawnie (`PROBE_WAIT_S`) na sondę rozpoczętą po wywołaniu — nigdy nie działają na starszym wyniku; metody D-Bus
`/Agents` odpowiadają z opóźnieniem (`*_later`), więc nawet to czekanie nie wstrzymuje wątku głównego.
"""
import json
import time

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from . import attention, codex_app, processes, windows
from .agents import AgentScanner
from .procprobe import ProcessProber

DEBOUNCE_MS = 250
TICK_MS = 5000
RESCAN_MS = 30000
PROBE_WAIT_S = 2.0                   # akcja użytkownika czeka najwyżej tyle na sondę nowszą niż wywołanie
STALE_S = 3 * TICK_MS / 1000.0       # wynik starszy niż trzy ticki — sonda zawieszona, migawka „stale”


class AgentMonitor(QObject):
    changed = pyqtSignal()
    raw_changed = pyqtSignal()
    classified = pyqtSignal()   # model ocenił końcówkę tury (wątek oceny) — stan przeliczamy w wątku Qt (PT-42)
    probed = pyqtSignal(object)  # wynik sondy procesów z wątku sondy → wątek monitora (PT-345)

    def __init__(self, scanner: AgentScanner, use_processes: bool = True, clock=time.time, parent=None,
                 probe_wait: float = PROBE_WAIT_S, stale_after: float = STALE_S):
        super().__init__(parent)
        self.scanner = scanner
        self.use_processes = use_processes
        self.clock = clock
        self.probe_wait = probe_wait
        self.stale_after = stale_after
        self._json = "[]"
        self.sessions: list[dict] = []   # ostatnia lista sesji (dla worktrees i powiadomień)
        self.raw_sessions: list[dict] = []  # także sesje kroków karty ukryte na liście agentów
        self._raw_state = ()
        self.procs = None                # ostatnia opublikowana migawka procesów (prezentacja) albo None
        self._result = None              # ProbeResult tej migawki (numer, czas)
        self._last = None                # ostatni przyjęty wynik sondy, także z błędem
        self._log_roots: tuple = ()      # katalogi dzienników Codexa dla sondy — ustawiane w wątku głównym
        self._waiting: list[list] = []   # akcje czekające na świeżą sondę: [numer, funkcja(ok, procs)]
        # Sonda woła `self._probe` w swoim wątku (testy podmieniają `_probe` na atrapę).
        self.prober = ProcessProber(lambda: self._probe(), delivered=self.probed.emit)
        self.probed.connect(self._probed)   # sygnał z wątku sondy trafia do kolejki wątku monitora
        # Wątek sondy jest wątkiem demona: kończy się z usługą; `stop()` zatrzymuje go wcześniej (testy).
        self._dirty: set[str] = set()
        # funkcja(sessions) dopisująca pola od innych obszarów (np. newTask z /Tasks) albo None
        self.annotate = None
        # funkcja(sesja) → True dla sesji, której nie pokazujemy jako agenta (krok w tle przepływu karty, PT-58):
        # nie ma jej w Sessions(), w kafelku Praca ani w powiadomieniach; KRunner ma własny indeks i ją znajduje.
        self.hidden = None
        self._discover = False
        self.classified.connect(self.recompute)   # sygnał z innego wątku trafia do kolejki wątku monitora

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

    def stop(self) -> None:
        self.prober.stop()

    def recompute(self) -> None:
        """Lista sesji na ostatniej opublikowanej migawce procesów; nowa sonda w tle (bez czekania)."""
        self._request_probe()
        self._publish()

    def processes_status(self) -> dict:
        """Stan migawki procesów: `off` (bez procesów), `none`, `fresh`, `stale`, `error` i wiek wyniku."""
        if not self.use_processes:
            return {"state": "off", "ageS": None, "seq": 0, "probingS": None, "error": ""}
        return self.prober.status(self.stale_after, good=self._result, last=self._last)

    def _request_probe(self) -> int:
        if not self.use_processes:
            return 0
        try:   # ścieżki rozwiązywane w wątku głównym; sonda dostaje gotową krotkę
            self._log_roots = tuple(self.scanner.codex_log_roots())
        except Exception as exc:
            print(f"tide-daemon: katalogi dzienników Codexa dla sondy: {exc!r}", flush=True)
        return self.prober.request()

    def _accept(self, result) -> bool:
        """Przyjmuje wynik sondy w wątku głównym. Tabela trafia do migawki jednym przypisaniem (stara albo nowa,
        nigdy częściowa); wynik nie nowszy niż przyjęty jest pomijany. True, gdy stan migawki się zmienił."""
        if result is None or (self._last is not None and result.seq <= self._last.seq):
            return False
        self._last = result
        if result.table is not None:
            self._result, self.procs = result, result.table
        return True

    def _probed(self, result) -> None:
        """Wynik sondy w wątku głównym: nowa migawka procesów, przeliczenie listy i czekające akcje."""
        if self._accept(result):
            self._publish()
        ready = [entry for entry in self._waiting if result is not None and result.seq >= entry[0]]
        for entry in ready:
            self._waiting.remove(entry)
            self._finish(entry, result.table is not None, result.table)

    def _publish(self) -> None:
        procs = self.procs if self.use_processes else None
        sessions = self.scanner.sessions(self.clock(), procs)
        if self.use_processes:
            state = self.processes_status()["state"]
            for s in sessions:
                s["processes"] = state   # jawny stan migawki procesów zamiast udawania braku procesów
        self.raw_sessions = sessions
        raw_state = tuple((s.get("id"), s.get("state"), s.get("reason"), s.get("waitingText"))
                          for s in sessions)
        raw_changed = raw_state != self._raw_state
        self._raw_state = raw_state
        if self.hidden is not None:
            try:
                sessions = [s for s in sessions if not self.hidden(s)]
            except Exception as exc:  # filtr nie może zatrzymać listy sesji
                print(f"tide-daemon: ukrywanie sesji kroków: {exc!r}", flush=True)
        if self.annotate is not None:
            try:
                self.annotate(sessions)
            except Exception as exc:  # dodatek nie może zatrzymać listy sesji
                print(f"tide-daemon: dopisywanie pól sesji: {exc!r}", flush=True)
        self.sessions = sessions
        text = json.dumps(sessions, ensure_ascii=False)
        if text != self._json:
            self._json = text
            self.changed.emit()
        if raw_changed:
            self.raw_changed.emit()

    # --- akcje użytkownika: zawsze na sondzie nowszej niż wywołanie ---------------------------

    def _fresh(self):
        """(ok, procs): sonda rozpoczęta po tym wywołaniu, czekanie najwyżej `probe_wait` s w wątku wywołującym.
        Bez procesów — (True, None). Zawieszona sonda albo jej wyjątek — (False, None): akcja odmawia, zamiast
        działać na przeterminowanej migawce."""
        if not self.use_processes:
            return True, None
        ticket = self._request_probe()
        result = self.prober.wait(ticket, self.probe_wait)
        if result is not None and result.table is None and self._accept(result):
            self._publish()     # jawny błąd sondy w migawce
        if result is None or result.table is None:
            print(f"tide-daemon: brak świeżej migawki procesów w {self.probe_wait:g} s — akcja pominięta"
                  + (f" ({result.error})" if result is not None and result.error else ""), flush=True)
            return False, None
        if self._accept(result):
            self._publish()
        return True, result.table

    def _when_fresh(self, then) -> None:
        """Jak `_fresh`, ale bez czekania: `then(ok, procs)` w wątku głównym, gdy przyjdzie sonda nowsza niż
        wywołanie, albo `then(False, None)` po `probe_wait` s (odpowiedź D-Bus z opóźnieniem)."""
        if not self.use_processes:
            self._finish([0, then], True, None)
            return
        entry = [self._request_probe(), then]
        self._waiting.append(entry)
        QTimer.singleShot(max(1, int(self.probe_wait * 1000)), lambda: self._expire(entry))

    def _expire(self, entry) -> None:
        if entry in self._waiting:
            self._waiting.remove(entry)
            print(f"tide-daemon: brak świeżej migawki procesów w {self.probe_wait:g} s — akcja pominięta", flush=True)
            self._finish(entry, False, None)

    @staticmethod
    def _finish(entry, ok, procs) -> None:
        try:
            entry[1](ok, procs)
        except Exception as exc:  # akcja nie może zatrzymać obsługi wyników sondy
            print(f"tide-daemon: akcja po sondzie procesów: {exc!r}", flush=True)

    def refresh_fresh(self) -> bool:
        """Lista sesji i `procs` przeliczone na sondzie nowszej niż wywołanie — przed decyzją, która zależy od tego,
        gdzie działa agent (usuwanie worktree). False: brak świeżej migawki, decyzja musi odmówić."""
        ok, _procs = self._fresh()
        if ok:
            self._publish()
        return ok

    def activate(self, session_id: str) -> bool:
        ok, procs = self._fresh()
        return ok and self._activate(session_id, procs)

    def activate_later(self, session_id: str, reply) -> None:
        """ActivateSession z D-Bus: odpowiedź `reply(bool)` po świeżej sondzie, bez czekania w wątku głównym."""
        self._when_fresh(lambda ok, procs: reply(bool(ok and self._activate(session_id, procs))))

    def _activate(self, session_id: str, procs) -> bool:
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
                if not Threads.available():
                    return False
                ok, procs = monitor._fresh()
                return ok and monitor._open_in_app(None, thread_id, procs)

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
        ok, procs = self._fresh()  # świeży stan i procesy — skrót wciska się teraz, nie 5 s temu
        return ok and self._longest_waiting(procs)

    def activate_longest_waiting_later(self, reply) -> None:
        """ActivateLongestWaiting z D-Bus (Meta+A): wybór i aktywacja na sondzie nowszej niż wciśnięcie."""
        self._when_fresh(lambda ok, procs: reply(bool(ok and self._longest_waiting(procs))))

    def _longest_waiting(self, procs) -> bool:
        # `procs` to świeży wynik już przyjęty do migawki (`_fresh`/`_probed`), więc `_publish` liczy stan sesji
        # na tej samej migawce, na której potem aktywujemy okno.
        self._publish()
        session = attention.longest_waiting(self.sessions)
        return self._activate(session["id"], procs) if session else False

    def _probe(self):
        """Wołane wyłącznie w wątku sondy (`ProcessProber`)."""
        return processes.probe(log_roots=self._log_roots)

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
