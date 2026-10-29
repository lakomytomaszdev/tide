"""Wejście prawego monitora PC ⇄ Xbox w usłudze: obiekt /Monitors dla widżetu org.tide.stream.monitors (PT-38).

Przełączenie (monitors.Switcher: `ddcutil detect`, odczyt i zapis VCP 0x60 z ponowieniami) trwa sekundy, więc idzie
w wątku roboczym, a SetLayout odpowiada od razu: {"accepted": true} albo powód odmowy. Koniec przełączenia to
sygnał Changed; wynik (błąd „przełącz wejście przyciskiem”) jest w Status().result. Status() czyta tylko plik
stanu — bez DDC i bez KWin.

Praca zawsze ma oba ekrany jak dziś: wyjście z aktywności Stream (i start usługi w pracy), gdy prawy monitor jest
na wejściu Xboxa albo przełączenie zostało przerwane, przełącza go z powrotem na PC (stream_changed(False)).
Wyjście ze Streamu w trakcie przełączania na Xbox jest zapamiętane i powrót rusza po jego końcu. Nieudany
automatyczny powrót (monitor nie odpowiada na DDC) to powiadomienie z prośbą o przycisk na monitorze.
Źródłem „czy Stream” jest jeden sygnał (current_activity.CurrentActivity.changed), podpinany w app.py.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from . import monitors


def log(message: str) -> None:
    print(f"tide-daemon: monitory: {message}", flush=True)


class MonitorsService(QObject):
    changed = pyqtSignal()
    _finished = pyqtSignal(str, object)  # (układ docelowy, wynik) — z wątku roboczego do głównego

    def __init__(self, switcher=None, notify=None, parent=None):
        super().__init__(parent)
        self.switcher = switcher or monitors.Switcher()
        self.notify = notify          # notify({id, icon, summary, body}) — powiadomienie pulpitu albo None
        self._left_stream = False     # wyjście ze Streamu w trakcie przełączania — powrót po jego końcu
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-monitors")
        self._switching = ""
        self._auto = False
        self._result: dict | None = None
        self._stopped = False
        self._finished.connect(self._on_finished)

    def stop(self) -> None:
        self._stopped = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    # --- D-Bus ------------------------------------------------------------------------------

    def status(self) -> dict:
        return {**self.switcher.status(), "switching": self._switching, "result": self._result}

    def status_json(self) -> str:
        return json.dumps(self.status(), ensure_ascii=False)

    def set_layout(self, layout: str, auto: bool = False) -> str:
        if layout not in monitors.LAYOUTS:
            return json.dumps({"accepted": False, "error": f"nieznany układ „{layout}” (pc albo xbox)"},
                              ensure_ascii=False)
        if self._switching:
            return json.dumps({"accepted": False, "error": "przełączenie już trwa"}, ensure_ascii=False)
        self._switching, self._auto = layout, auto

        def run():
            try:
                result = self.switcher.switch(layout)
            except Exception as exc:  # błąd przełącznika nie może położyć usługi
                result = {"ok": False, "layout": "", "changed": False, "error": f"{type(exc).__name__}: {exc}"}
            self._finished.emit(layout, result)
        try:
            self._pool.submit(run)
        except RuntimeError:  # pula zamknięta (stop)
            self._switching = ""
            return json.dumps({"accepted": False, "error": "usługa się zatrzymuje"}, ensure_ascii=False)
        self.changed.emit()
        return json.dumps({"accepted": True, "error": ""})

    # --- aktywność ------------------------------------------------------------------------------

    @pyqtSlot(bool)
    def stream_changed(self, stream: bool) -> None:
        """Praca (wyjście ze Streamu albo start usługi w pracy) z prawym monitorem na Xboxie → z powrotem PC."""
        if stream or self._stopped or self._switching == "pc":
            self._left_stream = False if stream else self._left_stream
            return
        if self._switching:
            self._left_stream = True   # SetLayout odmówiłby („przełączenie już trwa”) — dokończ po końcu
            return
        if self.switcher.layout() == "xbox":
            log("praca — wejście prawego monitora wraca na DisplayPort")
            self.set_layout("pc", auto=True)

    # --- wnętrze ------------------------------------------------------------------------------

    @pyqtSlot(str, object)
    def _on_finished(self, layout: str, result: dict) -> None:
        self._switching = ""
        self._result = {**result, "auto": self._auto,
                        "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
        if not result.get("ok"):
            log(str(result.get("error")))
            if self._auto and self.notify is not None:
                self.notify({"id": "monitors/auto-pc", "icon": "video-display",
                             "summary": "Prawy monitor został na Xboxie",
                             "body": f"Po wyjściu ze Stream nie udało się wrócić na PC: {result.get('error')}"})
        self.changed.emit()
        if self._left_stream:
            self._left_stream = False
            self.stream_changed(False)
