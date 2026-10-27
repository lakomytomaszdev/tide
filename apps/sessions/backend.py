"""Widok „Sesje” okna Tide: koordynatorzy, tablica ticketów i podagenci — dane z tide-daemon przez
D-Bus (/Coordination i /Agents) albo ze stałego pliku (--data).

Moduł widoku (apps/tide/views.json): okno Tide ładuje go z views/sessions/backend.py i woła
create(bus, data). Strona QML to qml/SessionsView.qml (SessionsPage z akcjami i skrótami).

Dane z pliku (--data) to wynik `tide-daemon.py --print-coordination`, opcjonalnie z „details”:
{id przebiegu: RunDetail} i „now”: chwila do liczenia czasów; akcje są wtedy wyłączone.
Plakietka w pasku widoków: „● N” (brzoskwinia) — ilu agentów czeka na ciebie (/Agents).
"""
import json

from PyQt6.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply,
                          QDBusServiceWatcher)

SERVICE = "org.tide.Daemon"
PATH, IFACE = "/Coordination", "org.tide.Daemon.Coordination"
AGENTS_PATH, AGENTS_IFACE = "/Agents", "org.tide.Daemon.Agents"


def waiting_count(sessions_json: str) -> int:
    """Ilu agentów czeka na ciebie — stan „waiting” (także z powodem „reply”: tura skończona pytaniem
    bez odpowiedzi, PT-29), jak Theme.sessionState."""
    try:
        sessions = json.loads(sessions_json or "[]")
    except ValueError:
        return 0
    if not isinstance(sessions, list):
        return 0
    return sum(1 for s in sessions if isinstance(s, dict) and s.get("state") == "waiting")


class Backend(QObject):
    """Most między QML a tide-daemon: JSON-y list i tablicy, akcje jako wywołania D-Bus."""

    coordinatorsChanged = pyqtSignal()
    boardChanged = pyqtSignal()
    sessionsChanged = pyqtSignal()
    detailChanged = pyqtSignal()
    availableChanged = pyqtSignal()
    selectedChanged = pyqtSignal()
    badgeChanged = pyqtSignal()

    def __init__(self, bus: QDBusConnection | None, data: dict | None = None, select: str = "", parent=None):
        super().__init__(parent)
        self.bus = bus
        self._coordinators = "[]"
        self._board = "{}"
        self._sessions = "[]"
        self._detail = "{}"
        self._available = False
        self._selected = select
        self._now = 0.0
        self._data = data
        self._pending: set = set()
        self._badge = ""
        if data is not None:
            self._coordinators = json.dumps(data.get("coordinators") or [], ensure_ascii=False)
            self._sessions = json.dumps(data.get("sessions") or [], ensure_ascii=False)
            self._now = float(data.get("now") or 0)
            self._available = True
            if not self._selected and data.get("coordinators"):
                self._selected = data["coordinators"][0]["id"]
            self._board = json.dumps((data.get("boards") or {}).get(self._selected) or {}, ensure_ascii=False)
            self._update_badge()
            return
        if bus is None:
            return
        bus.connect(SERVICE, PATH, IFACE, "Changed", self._changed)
        bus.connect(SERVICE, AGENTS_PATH, AGENTS_IFACE, "Changed", self._agents_changed)
        self._watcher = QDBusServiceWatcher(SERVICE, bus, QDBusServiceWatcher.WatchModeFlag.WatchForRegistration
                                            | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration, self)
        self._watcher.serviceRegistered.connect(lambda _name: self.reload())
        self._watcher.serviceUnregistered.connect(lambda _name: self._set_available(False))
        self.reload()

    # --- właściwości dla QML ------------------------------------------------------------------

    @pyqtProperty(str, notify=coordinatorsChanged)
    def coordinatorsJson(self) -> str:
        return self._coordinators

    @pyqtProperty(str, notify=boardChanged)
    def boardJson(self) -> str:
        return self._board

    @pyqtProperty(str, notify=sessionsChanged)
    def sessionsJson(self) -> str:
        return self._sessions

    @pyqtProperty(str, notify=detailChanged)
    def detailJson(self) -> str:
        return self._detail

    @pyqtProperty(bool, notify=availableChanged)
    def available(self) -> bool:
        return self._available

    @pyqtProperty(str, notify=selectedChanged)
    def selected(self) -> str:
        return self._selected

    @pyqtProperty(bool, constant=True)
    def canDiff(self) -> bool:
        return self.bus is not None or self._data is not None

    @pyqtProperty(float, constant=True)
    def nowTs(self) -> float:
        return self._now

    # Plakietka w pasku widoków okna Tide; pusta — ukryta (bez usługi albo nikt nie czeka).
    @pyqtProperty(str, notify=badgeChanged)
    def badgeText(self) -> str:
        return self._badge

    @pyqtProperty(str, constant=True)
    def badgeTone(self) -> str:
        return "waiting"

    # --- akcje --------------------------------------------------------------------------------

    @pyqtSlot(str)
    def select(self, coordinator_id: str) -> None:
        if coordinator_id == self._selected:
            return
        self._selected = coordinator_id
        self.selectedChanged.emit()
        if self._data is not None:
            self._set("_board", json.dumps((self._data.get("boards") or {}).get(coordinator_id) or {},
                                           ensure_ascii=False), self.boardChanged)
            return
        self._load_board()

    @pyqtSlot(bool)
    def refresh(self, force: bool) -> None:
        if self.bus is not None:
            self._call(PATH, IFACE, "Refresh", [bool(force)], None)
            self.reload()

    @pyqtSlot()
    def reload(self) -> None:
        if self.bus is None:
            return
        self._call(PATH, IFACE, "Coordinators", [], self._got_coordinators)
        self._call(AGENTS_PATH, AGENTS_IFACE, "Sessions", [], self._got_sessions)

    @pyqtSlot(str)
    def activate(self, coordinator_id: str) -> None:
        if self.bus is not None and coordinator_id:
            self._call(PATH, IFACE, "Activate", [coordinator_id], None)

    @pyqtSlot(str)
    def activateSession(self, session_id: str) -> None:
        if self.bus is not None and session_id:
            self._call(AGENTS_PATH, AGENTS_IFACE, "ActivateSession", [session_id], None)

    @pyqtSlot(str, str)
    def openDiff(self, coordinator_id: str, ticket: str) -> None:
        if self.bus is not None:
            self._call(PATH, IFACE, "OpenDiff", [coordinator_id, ticket], None)

    @pyqtSlot(str, str)
    def openWorktree(self, coordinator_id: str, ticket: str) -> None:
        if self.bus is not None:
            self._call(PATH, IFACE, "OpenWorktree", [coordinator_id, ticket], None)

    @pyqtSlot(str, str)
    def loadDetail(self, coordinator_id: str, run_id: str) -> None:
        self._set("_detail", "{}", self.detailChanged)
        if self._data is not None:
            detail = (self._data.get("details") or {}).get(run_id) or {"id": run_id, "result": "", "excerpt": [],
                                                                        "rounds": [], "description": ""}
            self._set("_detail", json.dumps(detail, ensure_ascii=False), self.detailChanged)
            return
        if self.bus is not None:
            self._call(PATH, IFACE, "RunDetail", [coordinator_id, run_id],
                       lambda ok, value: self._set("_detail", value if ok and isinstance(value, str) else "{}",
                                                   self.detailChanged))

    # --- wnętrze ------------------------------------------------------------------------------

    @pyqtSlot()
    def _changed(self) -> None:
        self.reload()

    @pyqtSlot()
    def _agents_changed(self) -> None:
        self._call(AGENTS_PATH, AGENTS_IFACE, "Sessions", [], self._got_sessions)

    def _got_coordinators(self, ok: bool, value) -> None:
        if not ok or not isinstance(value, str):
            self._set_available(False)
            return
        self._set_available(True)
        self._set("_coordinators", value, self.coordinatorsChanged)
        try:
            ids = [c.get("id") for c in json.loads(value)]
        except (ValueError, AttributeError):
            ids = []
        if self._selected not in ids and ids:
            self._selected = ids[0]
            self.selectedChanged.emit()
        self._load_board()

    def _got_sessions(self, ok: bool, value) -> None:
        if ok and isinstance(value, str):
            self._set("_sessions", value, self.sessionsChanged)

    def _load_board(self) -> None:
        if self.bus is None or not self._selected:
            return
        selected = self._selected
        self._call(PATH, IFACE, "Board", [selected],
                   lambda ok, value: self._set("_board", value, self.boardChanged)
                   if ok and isinstance(value, str) and selected == self._selected else None)

    def _set(self, name: str, value: str, signal) -> None:
        if getattr(self, name) != value:
            setattr(self, name, value)
            signal.emit()
            if name == "_sessions":
                self._update_badge()

    def _set_available(self, value: bool) -> None:
        if value != self._available:
            self._available = value
            self.availableChanged.emit()
            self._update_badge()

    def _update_badge(self) -> None:
        count = waiting_count(self._sessions) if self._available else 0
        badge = f"● {count}" if count else ""
        if badge != self._badge:
            self._badge = badge
            self.badgeChanged.emit()

    def _call(self, path: str, iface: str, member: str, args: list, then, timeout: int = 15_000) -> None:
        message = QDBusMessage.createMethodCall(SERVICE, path, iface, member)
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
    """Backend widoku dla okna Tide: z magistralą (na żywo) albo ze stałymi danymi (data: dict)."""
    return Backend(bus if data is None else None, data if isinstance(data, dict) else None)
