"""Interfejs D-Bus `Git` w usłudze (K-36, PT-357): odczyty gita jako dane, poza wątkiem D-Bus.

`Diff(request)` liczy `DiffData` (`gitread/diffdata.py`) w puli wątków runnera odczytu (`GitReader.submit`),
a odpowiedź wraca do wątku głównego sygnałem Qt — jak `Fetch` w obszarze worktrees. Pętla usługi nie czeka
na gita. Dziennik kart przychodzi z przepływu karty (`journal_source()`); bez przepływu źródło `card`
odpowiada `unavailable`.

`BranchMap(request)` (K-99, PT-387) liczy mapę gałęzi (`gitread/branchmap.py`) w tej samej puli: projekcja wielu
dzienników nie biegnie w wątku głównym. Źródło Tablicy (`board_source()`, tylko odczyt: projekty i karty) jest
wołane w wątku głównym, jak lista repozytoriów `Diff`, i idzie do puli jako gotowa, stała kopia; repozytorium mapy
pochodzi z projektu Tablicy, nigdy z żądania.
"""
import json
import traceback

from PyQt6.QtCore import QObject, pyqtSignal

from .gitread import branchmap, diffdata
from .gitread.runner import GitReader


class GitMonitor(QObject):
    _finished = pyqtSignal(object, object)  # (odpowiedź, Future) — z wątku roboczego do głównego

    def __init__(self, journal_source=None, reader: GitReader | None = None, parent=None, compute=None,
                 repos_source=None, board_source=None, map_compute=None):
        super().__init__(parent)
        self.journal_source = journal_source or (lambda: None)
        # Dozwolone `repo` źródeł `commit` i `range`: repozytoria projektów Tablicy i worktrees z listy Worktrees.
        # Funkcja wołana w wątku głównym (`diff`), jak migawka Tablicy w report_monitor; lista idzie do puli
        # gotowa i stała na czas żądania. Bez źródła żadne `repo` nie jest znane.
        self.repos_source = repos_source or (lambda: [])
        # Tablica dla mapy gałęzi: `branchmap.board_view(...)` albo None (Tablica wyłączona → `unavailable`).
        self.board_source = board_source or (lambda: None)
        self.reader = reader or GitReader()
        self._compute = compute or self._request  # (reader, journal, tekst) -> dict; atrapa w testach
        self._map_compute = map_compute or self._branch_map  # (reader, journal, tablica, tekst) -> dict
        self._stopped = False
        self._finished.connect(self._deliver)

    def _request(self, reader, journal, text, repos) -> dict:
        return diffdata.request(reader, journal, text, repos)

    def _branch_map(self, reader, journal, board, text) -> dict:
        # `reader` nie jest używany: mapa to czysta projekcja dzienników i Tablicy, bez gita (#11 dołoży metryki).
        return branchmap.request(journal, board, text)

    def _known_repos(self) -> list:
        try:
            return list(self.repos_source())
        except Exception:  # noqa: BLE001 — nie umiemy ustalić listy: żadne repo nie jest znane (bad-request)
            return []

    def _journal(self):
        try:
            return self.journal_source()
        except Exception:  # noqa: BLE001 — brak przepływu to stan, nie błąd usługi
            return None

    def stop(self) -> None:
        self._stopped = True
        self.reader.close()

    def _refuse(self, reply) -> bool:
        if self._stopped:
            reply(json.dumps({"ok": False, "error": "cancelled", "message": "usługa się zamyka"}, ensure_ascii=False))
        return self._stopped

    def diff(self, request: str, reply) -> None:
        """`reply(tekst JSON)` wywołane później z wątku głównego; samo wywołanie wraca od razu."""
        if self._refuse(reply):
            return
        journal = self._journal()
        repos = self._known_repos()  # w wątku głównym: transakcja Tablicy nie biegnie w puli odczytu
        future = self.reader.submit(self._compute, journal, request, repos)
        future.add_done_callback(lambda f: self._finished.emit(reply, f))

    def branch_map(self, request: str, reply) -> None:
        """Mapa gałęzi; `reply(tekst JSON)` wywołane później z wątku głównego, samo wywołanie wraca od razu."""
        if self._refuse(reply):
            return
        journal = self._journal()
        try:
            board = self.board_source()  # w wątku głównym, jak `repos_source`
        except Exception:  # noqa: BLE001 — Tablicy nie da się odczytać: jak wyłączona (`unavailable`)
            print("tide-daemon: Git.BranchMap: odczyt Tablicy: " + traceback.format_exc(), flush=True)
            board = None
        future = self.reader.submit(self._map_compute, journal, board, request)
        future.add_done_callback(lambda f: self._finished.emit(reply, f))

    def _deliver(self, reply, future) -> None:
        if future.cancelled():
            result = {"ok": False, "error": "cancelled", "message": "odczyt anulowany przy zamykaniu usługi"}
        else:
            try:
                result = future.result()
            except Exception:  # błąd w tle nie może położyć usługi; treść wyjątku tylko w dzienniku usługi
                print("tide-daemon: odczyt /Git: " + traceback.format_exc(), flush=True)
                result = {"ok": False, "error": "failed", "message": "Odczyt nie powiódł się (szczegóły w dzienniku "
                                                                     "usługi)."}
        reply(json.dumps(result, ensure_ascii=False))
