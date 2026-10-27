"""Widok „Tablica” okna Tide: karty ze wszystkich projektów — migawka i zapisy przez D-Bus
(tide-daemon /Board, docs/tide-daemon.md#tablica) albo stała migawka z pliku (--data).

Moduł widoku (apps/tide/views.json): okno Tide ładuje go z views/board/backend.py i woła
create(bus, data). Strona QML to qml/BoardView.qml (BoardPage z kolumnami, panelem karty
i stroną „Projekty i tagi”).

Zapisy (CreateCard, UpdateCard, MoveCard, UpsertProject, UpsertTag) idą asynchronicznie; wynik
wraca sygnałem replied(tag, json) z tagiem nadanym przez QML (np. "move:K-12"), żeby błąd pokazać
tam, gdzie powstał. Po udanym zapisie od razu pobieramy nową migawkę (usługa ma ją gotową
od razu po zapisie, a sygnał Changed przychodzi i tak).

Dane z pliku (--data) to wynik `tide-daemon.py --print-board` (opcjonalnie z „now”: chwila do
liczenia czasów i „home”: katalog domowy do skracania ścieżek); zapisy są wtedy wyłączone
(odpowiedź „preview”), a wywołania zostają w `callsJson` — do testów i podglądu.
Plakietka w pasku widoków: „● N” (brzoskwinia), gdy karty czekają na ciebie, inaczej liczba kart
w grze (bez Gotowych i Archiwum); zero — ukryta. Ta sama reguła co /Board Badge() w usłudze.
"""
import json
import os

from PyQt6.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply,
                          QDBusServiceWatcher)

SERVICE, PATH, IFACE = "org.tide.Daemon", "/Board", "org.tide.Daemon.Board"
PREVIEW_REPLY = {"ok": False, "error": "preview", "message": "Podgląd z pliku — zmiany są wyłączone."}
NO_SERVICE_REPLY = {"ok": False, "error": "no-service",
                    "message": "Usługa tide-daemon nie odpowiada — nic nie zapisano. Spróbuj za chwilę."}
WRITES = ("CreateCard", "UpdateCard", "MoveCard", "UpsertProject", "UpsertTag")


def badge(snapshot) -> tuple[str, str]:
    """(tekst, ton) plakietki z migawki: czekające karty albo karty w grze; ("", "muted") — ukryta."""
    cards = snapshot.get("cards") if isinstance(snapshot, dict) else None
    waiting = total = 0
    for card in cards if isinstance(cards, list) else []:
        if not isinstance(card, dict):
            continue
        column = card.get("boardColumn") or card.get("column") or "backlog"
        if column == "waiting":
            waiting += 1
        if column not in ("done", "archived"):
            total += 1
    if waiting:
        return f"● {waiting}", "waiting"
    return (str(total) if total else ""), "muted"


class Backend(QObject):
    """Most między QML a tide-daemon: migawka tablicy (JSON) i zapisy jako wywołania D-Bus."""

    snapshotChanged = pyqtSignal()
    availableChanged = pyqtSignal()
    badgeChanged = pyqtSignal()
    callsChanged = pyqtSignal()
    replied = pyqtSignal(str, str)  # tag z QML, odpowiedź usługi (JSON)

    def __init__(self, bus: QDBusConnection | None, data: dict | None = None, parent=None):
        super().__init__(parent)
        self.bus = bus
        self._snapshot = "{}"
        self._available = False
        self._badge = ("", "muted")
        self._calls: list = []
        self._pending: set = set()  # QDBusPendingCallWatcher muszą żyć do odpowiedzi
        self._preview = data is not None
        self._now = 0.0
        self._home = ""
        if data is not None:
            self._now = float(data.get("now") or 0)
            self._home = str(data.get("home") or "")
            self._snapshot = json.dumps(data, ensure_ascii=False)
            self._available = True
            self._update_badge()
            return
        if bus is None:
            return
        bus.connect(SERVICE, PATH, IFACE, "Changed", self._changed)
        self._watcher = QDBusServiceWatcher(SERVICE, bus, QDBusServiceWatcher.WatchModeFlag.WatchForRegistration
                                            | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration, self)
        self._watcher.serviceRegistered.connect(lambda _name: self.reload())
        self._watcher.serviceUnregistered.connect(lambda _name: self._set_available(False))
        self.reload()

    # --- właściwości dla QML ------------------------------------------------------------------

    @pyqtProperty(str, notify=snapshotChanged)
    def snapshotJson(self) -> str:
        return self._snapshot

    @pyqtProperty(bool, notify=availableChanged)
    def available(self) -> bool:
        return self._available

    @pyqtProperty(bool, constant=True)
    def preview(self) -> bool:
        return self._preview

    # Chwila „teraz” z pliku (--data, klucz „now”) — powtarzalne „zapisano 12:04” w podglądzie; 0 — zegar.
    @pyqtProperty(float, constant=True)
    def nowTs(self) -> float:
        return self._now

    @pyqtProperty(str, constant=True)
    def homeDir(self) -> str:
        return self._home or os.path.expanduser("~")

    # Wywołania zapisów w trybie podglądu (--data): [[metoda, argumenty…], …].
    @pyqtProperty(str, notify=callsChanged)
    def callsJson(self) -> str:
        return json.dumps(self._calls, ensure_ascii=False)

    # Plakietka w pasku widoków okna Tide; pusta — ukryta.
    @pyqtProperty(str, notify=badgeChanged)
    def badgeText(self) -> str:
        return self._badge[0]

    @pyqtProperty(str, notify=badgeChanged)
    def badgeTone(self) -> str:
        return self._badge[1]

    # --- akcje --------------------------------------------------------------------------------

    @pyqtSlot()
    def reload(self) -> None:
        if self.bus is None:
            return
        self._call("Snapshot", [], self._got_snapshot)

    @pyqtSlot(str, str)
    def createCard(self, tag: str, request: str) -> None:
        self._write(tag, "CreateCard", [request])

    @pyqtSlot(str, str, str)
    def updateCard(self, tag: str, card_id: str, request: str) -> None:
        self._write(tag, "UpdateCard", [card_id, request])

    @pyqtSlot(str, str, str, int)
    def moveCard(self, tag: str, card_id: str, column: str, order: int) -> None:
        self._write(tag, "MoveCard", [card_id, column, int(order)])

    @pyqtSlot(str, str)
    def upsertProject(self, tag: str, request: str) -> None:
        self._write(tag, "UpsertProject", [request])

    @pyqtSlot(str, str)
    def upsertTag(self, tag: str, request: str) -> None:
        self._write(tag, "UpsertTag", [request])

    # --- wnętrze ------------------------------------------------------------------------------

    def _write(self, tag: str, member: str, args: list) -> None:
        if self._preview or self.bus is None:
            self._calls.append([member, *args])
            self.callsChanged.emit()
            self.replied.emit(tag, json.dumps(PREVIEW_REPLY if self._preview else NO_SERVICE_REPLY,
                                              ensure_ascii=False))
            return

        def done(ok: bool, value) -> None:
            if not ok or not isinstance(value, str):
                self.replied.emit(tag, json.dumps(NO_SERVICE_REPLY, ensure_ascii=False))
                return
            self.replied.emit(tag, value)
            self.reload()  # usługa ma nową migawkę od razu po zapisie

        self._call(member, args, done)

    @pyqtSlot()
    def _changed(self) -> None:
        self.reload()

    def _got_snapshot(self, ok: bool, value) -> None:
        if not ok or not isinstance(value, str):
            self._set_available(False)
            return
        if value != self._snapshot:
            self._snapshot = value
            self.snapshotChanged.emit()
            self._update_badge()
        self._set_available(True)

    def _set_available(self, value: bool) -> None:
        if value != self._available:
            self._available = value
            self.availableChanged.emit()
            self._update_badge()

    def _update_badge(self) -> None:
        try:
            snapshot = json.loads(self._snapshot or "{}")
        except ValueError:
            snapshot = {}
        value = badge(snapshot) if self._available else ("", "muted")
        if value != self._badge:
            self._badge = value
            self.badgeChanged.emit()

    def _call(self, member: str, args: list, then, timeout: int = 15_000) -> None:
        message = QDBusMessage.createMethodCall(SERVICE, PATH, IFACE, member)
        if args:
            message.setArguments(args)
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(message, timeout), self)
        self._pending.add(watcher)

        def finished(w):
            self._pending.discard(w)
            reply = QDBusPendingReply(w)
            ok = not reply.isError()
            value = reply.argumentAt(0) if ok and reply.reply().arguments() else None
            w.deleteLater()
            if then:
                then(ok, value)

        watcher.finished.connect(finished)


def create(bus: QDBusConnection | None, data=None) -> Backend:
    """Backend widoku dla okna Tide: z magistralą (na żywo) albo ze stałą migawką (data: dict)."""
    return Backend(bus if data is None else None, data if isinstance(data, dict) else None)
