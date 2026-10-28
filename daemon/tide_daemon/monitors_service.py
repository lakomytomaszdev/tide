"""Układ monitorów PC ⇄ Xbox w usłudze: obiekt /Monitors dla widżetu org.tide.stream.monitors (PT-38).

Przełączenie (monitors.Switcher) trwa kilka sekund — DDC/CI, przerwa na ustalenie wejścia, kscreen-doctor —
więc idzie w wątku roboczym, a SetLayout odpowiada od razu: {"accepted": true} albo powód odmowy.
Koniec przełączenia to sygnał Changed; wynik (błąd, uwagi „przełącz wejście ręcznie”) jest w Status().

Status() oddaje ostatni odczyt; odczyt układu z KWin (kscreen-doctor -j, tylko odczyt) idzie w tym samym
wątku przy starcie, po każdym przełączeniu i przy Status() starszym niż refresh_s — zmiana układu
w Ustawieniach systemowych dociera do widżetu bez osobnego nasłuchu KScreen.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from . import monitors

REFRESH_S = 30.0


class MonitorsService(QObject):
    changed = pyqtSignal()
    _finished = pyqtSignal(str, object)  # (co: "status" | "switch", wynik) — z wątku roboczego do głównego

    def __init__(self, switcher=None, refresh_s: float = REFRESH_S, clock=time.monotonic, parent=None):
        super().__init__(parent)
        self.switcher = switcher or monitors.Switcher()
        self.refresh_s = refresh_s
        self.clock = clock
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-monitors")
        self._status: dict = {"layout": "", "target": "", "available": False, "error": "", "notices": []}
        self._read_at: float | None = None
        self._reading = False
        self._switching = ""
        self._result: dict | None = None
        self._stopped = False
        self._finished.connect(self._on_finished)

    def start(self) -> None:
        self.refresh()

    def stop(self) -> None:
        self._stopped = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    # --- D-Bus ------------------------------------------------------------------------------

    def status_json(self) -> str:
        if not self._busy() and (self._read_at is None or self.clock() - self._read_at > self.refresh_s):
            self.refresh()
        return json.dumps({**self._status, "switching": self._switching, "result": self._result},
                          ensure_ascii=False)

    def set_layout(self, layout: str) -> str:
        if layout not in monitors.LAYOUTS:
            return json.dumps({"accepted": False, "error": f"nieznany układ „{layout}” (pc albo xbox)"},
                              ensure_ascii=False)
        if self._switching:
            return json.dumps({"accepted": False, "error": "przełączenie już trwa"}, ensure_ascii=False)
        self._switching = layout
        self._submit("switch", lambda: self.switcher.switch(layout))
        self.changed.emit()
        return json.dumps({"accepted": True, "error": ""})

    def refresh(self) -> None:
        if self._reading or self._stopped:
            return
        self._reading = True
        self._submit("status", self.switcher.status)

    # --- wnętrze ------------------------------------------------------------------------------

    def _busy(self) -> bool:
        return self._reading or bool(self._switching)

    def _submit(self, kind: str, work) -> None:
        def run():
            try:
                result = work()
            except Exception as exc:  # błąd przełącznika nie może położyć usługi
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "notices": []}
            self._finished.emit(kind, result)
        try:
            self._pool.submit(run)
        except RuntimeError:  # pula zamknięta (stop)
            self._reading = False
            self._switching = ""

    @pyqtSlot(str, object)
    def _on_finished(self, kind: str, result: dict) -> None:
        if kind == "status":
            self._reading = False
            self._read_at = self.clock()
            if "layout" in result:
                if result != self._status:
                    self._status = result
                    self.changed.emit()
            return
        self._switching = ""
        self._result = {**result, "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
        if not result.get("ok"):
            print(f"tide-daemon: monitory: {result.get('error')}", flush=True)
        for notice in result.get("notices") or []:
            print(f"tide-daemon: monitory: {notice}", flush=True)
        self.changed.emit()
        self._read_at = None
        self.refresh()  # układ po przełączeniu
