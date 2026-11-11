"""Interfejs D-Bus `Git` w usłudze (K-36, PT-357): odczyty gita jako dane, poza wątkiem D-Bus.

`Diff(request)` liczy `DiffData` (`gitread/diffdata.py`) w puli wątków runnera odczytu (`GitReader.submit`),
a odpowiedź wraca do wątku głównego sygnałem Qt — jak `Fetch` w obszarze worktrees. Pętla usługi nie czeka
na gita. Dziennik kart przychodzi z przepływu karty (`journal_source()`); bez przepływu źródło `card`
odpowiada `unavailable`.
"""
import json

from PyQt6.QtCore import QObject, pyqtSignal

from .gitread import diffdata
from .gitread.runner import GitReader


class GitMonitor(QObject):
    _finished = pyqtSignal(object, object)  # (odpowiedź, Future) — z wątku roboczego do głównego

    def __init__(self, journal_source=None, reader: GitReader | None = None, parent=None, compute=None):
        super().__init__(parent)
        self.journal_source = journal_source or (lambda: None)
        self.reader = reader or GitReader()
        self._compute = compute or diffdata.request  # (reader, journal, tekst) -> dict; atrapa w testach
        self._stopped = False
        self._finished.connect(self._deliver)

    def stop(self) -> None:
        self._stopped = True
        self.reader.close()

    def diff(self, request: str, reply) -> None:
        """`reply(tekst JSON)` wywołane później z wątku głównego; samo wywołanie wraca od razu."""
        if self._stopped:
            reply(json.dumps({"ok": False, "error": "cancelled", "message": "usługa się zamyka"}, ensure_ascii=False))
            return
        try:
            journal = self.journal_source()
        except Exception:  # noqa: BLE001 — brak przepływu to stan, nie błąd usługi
            journal = None
        future = self.reader.submit(self._compute, journal, request)
        future.add_done_callback(lambda f: self._finished.emit(reply, f))

    def _deliver(self, reply, future) -> None:
        if future.cancelled():
            result = {"ok": False, "error": "cancelled", "message": "odczyt anulowany przy zamykaniu usługi"}
        else:
            try:
                result = future.result()
            except Exception as exc:  # błąd w tle nie może położyć usługi
                result = {"ok": False, "error": "failed", "message": f"{exc.__class__.__name__}: {exc}"}
        reply(json.dumps(result, ensure_ascii=False))
