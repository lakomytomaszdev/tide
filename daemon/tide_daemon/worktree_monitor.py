"""Obszar worktrees w usłudze: odświeżanie w tle, rozmiary i sprzątanie poza wątkiem D-Bus.

Cała praca z gitem i dyskiem idzie przez jeden wątek roboczy (kolejno: odświeżenie, rozmiary,
usuwanie), a wynik wraca do wątku głównego sygnałem Qt. Wątek główny tylko składa JSON
z gotowych danych (snapshot), więc D-Bus odpowiada od razu.

- co REFRESH_MS → przegląd repozytoriów (git worktree list, status, rev-list),
- po odświeżeniu → rozmiary, których brakuje albo są starsze niż SIZE_TTL,
- zmiana sesji agentów → tylko przeliczenie flagi „aktywny agent”, bez gita.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

REFRESH_MS = 60_000


def openable(worktrees: list, path: str, app: str) -> bool:
    """Czy `path` wolno otworzyć programem `app`: katalog z listy Worktrees (jak dotąd) albo — tylko w Riderze, bez
    terminala — istniejący zwykły plik w takim katalogu (PT-361, „Otwórz w edytorze” z widoku diffa). Ścieżka plików
    jest rozwiązywana (symlinki), więc wyjście poza worktree przez `..` albo link nie przejdzie."""
    if not isinstance(path, str) or not os.path.isabs(path):
        return False
    live = [w["path"] for w in worktrees if not w["missing"]]
    if path in live:
        return True
    if app != "rider" or not os.path.isfile(path):
        return False
    real = os.path.realpath(path)
    return any(real.startswith(os.path.realpath(root).rstrip("/") + "/") for root in live)


class WorktreeMonitor(QObject):
    changed = pyqtSignal()
    _finished = pyqtSignal(object, object)  # (co dalej, Future) — z wątku roboczego do głównego

    def __init__(self, scanner, cwds_source, launchers=None, refresh_agents=None, refresh_ms: int = REFRESH_MS,
                 parent=None, live_source=None, card_places_source=None):
        super().__init__(parent)
        self.refresh_agents = refresh_agents  # funkcja() odświeżająca stan agentów tuż przed sprzątaniem
        self.scanner = scanner
        self.cwds_source = cwds_source  # funkcja() -> katalogi robocze żywych agentów
        self.live_source = live_source  # funkcja() -> żywe sesje (narzędzie, identyfikator) albo None
        self.card_places_source = card_places_source  # funkcja() -> worktree zajęte przez przepływ karty
        self.launchers = launchers
        self._json = "[]"
        self._refreshing = False
        self._again = False
        self._stopped = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-worktrees")
        self._fetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-worktrees-fetch")
        self._fetching: set = set()  # repozytoria, z których właśnie pobieramy
        self._finished.connect(self._deliver)
        self._timer = QTimer(self, interval=refresh_ms)
        self._timer.timeout.connect(self.refresh)

    def start(self) -> None:
        if self._stopped:
            return
        self.refresh()
        self._timer.start()

    def stop(self) -> None:
        self._stopped = True
        self._timer.stop()
        if hasattr(self.scanner, "stop"):
            self.scanner.stop()
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._fetch_pool.shutdown(wait=True, cancel_futures=True)

    def list_json(self) -> str:
        return self._json

    def refresh(self) -> None:
        """Odświeżenie na żądanie; gdy już trwa, jeszcze jedno zaraz po nim."""
        if self._stopped:
            return
        if self._refreshing:
            self._again = True
            return
        self._refreshing = True
        self._submit(self.scanner.refresh, self._after_refresh)

    def publish(self) -> None:
        if self._stopped:
            return
        live = self.live_source() if self.live_source else ()
        cards = self.card_places_source() if self.card_places_source else ()
        text = json.dumps(self.scanner.snapshot(self.cwds_source(), live=live, card_paths=cards), ensure_ascii=False)
        if text != self._json:
            self._json = text
            self.changed.emit()

    def remove(self, paths: list[str], reply, force: bool = False, delete_unmerged: bool = False,
               confirmed: str = "") -> None:
        """Sprzątanie (albo usuwanie wymuszone, force=True) w tle; reply(json) dostaje wynik
        w wątku głównym. confirmed: JSON {ścieżka: {dirty, unmerged, unpushed, branch}} — stan
        z potwierdzenia; niepoprawny JSON oznacza odmowę dla wszystkich ścieżek."""
        paths = list(paths)
        if self._stopped:
            reply(json.dumps([_refused(p, "unavailable", "usługa się zamyka") for p in paths], ensure_ascii=False))
            return
        seen = None
        if force and confirmed:
            try:
                seen = json.loads(confirmed)
            except ValueError:
                seen = None
            if not isinstance(seen, dict):
                reply(json.dumps([_refused(p, "bad-request", "niepoprawny opis potwierdzenia — nic nie usunięto")
                                  for p in paths], ensure_ascii=False))
                return
        if self.refresh_agents:
            self.refresh_agents()
        cwds = self.cwds_source()  # migawka agentów z chwili wywołania
        self._submit(lambda: self.scanner.remove(paths, cwds, force=force, delete_unmerged=delete_unmerged,
                                                 confirmed=seen),
                     lambda res: self._after_remove(paths, res, reply))

    def prune(self, repo_path: str, reply) -> None:
        """`git worktree prune` w tle (tylko wpisy bez katalogu); reply(json) w wątku głównym."""
        if self._stopped:
            reply(json.dumps({"repoPath": repo_path, "ok": False, "pruned": [], "message": "usługa się zamyka"}))
            return
        def done(result):
            if result is None:
                result = {"repoPath": repo_path, "ok": False, "pruned": [], "message": "błąd usługi"}
            reply(json.dumps(result, ensure_ascii=False))
            self.publish()
        self._submit(lambda: self.scanner.prune(repo_path), done)

    def fetch(self, repo_path: str, reply) -> None:
        """`git fetch` ze zdalnych repozytorium w tle; reply(json) w wątku głównym. Osobny wątek niż
        odświeżanie, bo sieć potrafi trwać do limitu czasu, a lista nie ma na to czekać. Powtórne
        żądanie dla repozytorium, które już pobieramy, dostaje „busy”."""
        def refused(reason, message):
            reply(json.dumps({"ok": False, "reason": reason, "message": message, "repoPath": repo_path,
                              "remotes": []}, ensure_ascii=False))
        if self._stopped:
            refused("unavailable", "usługa się zamyka")
            return
        if not isinstance(repo_path, str):
            refused("bad-request", "to nie jest repozytorium z listy Worktrees")
            return
        try:
            key = os.path.realpath(repo_path)
        except (OSError, ValueError):
            key = repo_path
        if key in self._fetching:
            refused("busy", "pobieranie tego repozytorium już trwa")
            return
        self._fetching.add(key)

        def done(result):
            self._fetching.discard(key)
            if result is None:
                result = {"ok": False, "reason": "error", "message": "błąd usługi przy pobieraniu",
                          "repoPath": repo_path, "remotes": []}
            reply(json.dumps(result, ensure_ascii=False))
            self.refresh()  # nowe liczby ahead/behind i „pobrano przed chwilą”
        future = self._fetch_pool.submit(lambda: self.scanner.fetch(repo_path))
        future.add_done_callback(lambda f: self._finished.emit(done, f))

    def open(self, path: str, app: str) -> bool:
        if self._stopped:
            return False
        known = openable(json.loads(self._json), path, app)
        if not (known and self.launchers):
            return False
        if os.path.isfile(path):
            # Plik (tylko Rider, PT-361): `Launchers.open` przyjmuje wyłącznie katalog, więc używamy publicznych
            # `command` i `spawn`, a proces startuje w katalogu pliku.
            return self.launchers.spawn(self.launchers.command(app, path), os.path.dirname(path))
        return self.launchers.open(app, path)

    def show_window(self) -> bool:
        return bool(not self._stopped and self.launchers and self.launchers.show_window())

    def launchers_json(self) -> str:
        return json.dumps(self.launchers.available() if self.launchers
                          else {"terminal": False, "rider": False, "window": False})

    # --- wnętrze ---------------------------------------------------------------------------

    def _after_refresh(self, _result) -> None:
        self._refreshing = False
        self.publish()
        self._measure()
        if self._again:
            self._again = False
            self.refresh()

    def _after_remove(self, paths, results, reply) -> None:
        if results is None:  # wyjątek w tle — nic nie zostało potwierdzone jako usunięte
            results = [_refused(p, "error", "błąd usługi") for p in paths]
        reply(json.dumps(results, ensure_ascii=False))
        self.publish()
        self._measure()

    def _measure(self) -> None:
        if self.scanner.stale_sizes():
            self._submit(self.scanner.measure_stale, lambda _n: self.publish())

    def _submit(self, work, then) -> None:
        if self._stopped:
            return
        future = self._pool.submit(work)
        future.add_done_callback(lambda f: self._finished.emit(then, f))

    def _deliver(self, then, future) -> None:
        if self._stopped or future.cancelled():
            return
        try:
            result = future.result()
        except Exception as exc:  # błąd w tle nie może położyć usługi
            print(f"tide-daemon: worktrees: {exc!r}", flush=True)
            result = None
        then(result)


def _refused(path: str, reason: str, message: str) -> dict:
    """Wynik dla ścieżki, której nie dotknęliśmy (te same pola co z WorktreeScanner.remove)."""
    return {"path": path, "removed": False, "reason": reason, "message": message, "branch": "",
            "branchDeleted": False, "branchMessage": "", "freed": 0, "status": "refused"}
