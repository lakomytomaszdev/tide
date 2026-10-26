#!/usr/bin/python3
"""Okno „Tide Sesje”: koordynatorzy, tablica ticketów i podagenci — QML (qml/Main.qml) z danymi
z tide-daemon przez D-Bus (/Coordination i /Agents).

Użycie: /usr/bin/python3 tide-sessions.py [--select ID] [--data PLIK.json] [--grab PLIK.png]
                                          [--ticket KLUCZ] [--result ID-PRZEBIEGU] [--width N --height N]

--select  koordynator do pokazania (np. claude:<sesja>); bez niego — pierwszy z listy,
--data    stałe dane z pliku (wynik `tide-daemon.py --print-coordination`, opcjonalnie z „details”:
          {id przebiegu: RunDetail} i „now”: chwila do liczenia czasów), bez D-Bus — do podglądu
          i testów; akcje są wtedy wyłączone,
--grab    zapisuje zrzut okna do PNG i kończy (np. z QT_QPA_PLATFORM=offscreen),
--ticket, --result  zaznaczony ticket i otwarty wynik podagenta — do podglądu.

Okno działa w jednym egzemplarzu: rejestruje nazwę org.tide.SessionsWindow, a kolejne uruchomienie
(widżet agentów, KRunner „sesje”) prosi pierwsze o pokazanie się z koordynatorem (Raise(id)) i kończy.
"""
import argparse
import json
import os
import sys

from PyQt6.QtCore import QObject, QUrl, pyqtClassInfo, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBus, QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply,
                          QDBusServiceWatcher)
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtQml import QQmlApplicationEngine

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ID = "tide-sessions"
SERVICE = "org.tide.Daemon"
PATH, IFACE = "/Coordination", "org.tide.Daemon.Coordination"
AGENTS_PATH, AGENTS_IFACE = "/Agents", "org.tide.Daemon.Agents"
WINDOW_SERVICE, WINDOW_PATH, WINDOW_IFACE = "org.tide.SessionsWindow", "/Window", "org.tide.SessionsWindow"


class Backend(QObject):
    """Most między QML a tide-daemon: JSON-y list i tablicy, akcje jako wywołania D-Bus."""

    coordinatorsChanged = pyqtSignal()
    boardChanged = pyqtSignal()
    sessionsChanged = pyqtSignal()
    detailChanged = pyqtSignal()
    availableChanged = pyqtSignal()
    selectedChanged = pyqtSignal()
    raiseRequested = pyqtSignal()

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
        if data is not None:
            self._coordinators = json.dumps(data.get("coordinators") or [], ensure_ascii=False)
            self._sessions = json.dumps(data.get("sessions") or [], ensure_ascii=False)
            self._now = float(data.get("now") or 0)
            self._available = True
            if not self._selected and data.get("coordinators"):
                self._selected = data["coordinators"][0]["id"]
            self._board = json.dumps((data.get("boards") or {}).get(self._selected) or {}, ensure_ascii=False)
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

    def _set_available(self, value: bool) -> None:
        if value != self._available:
            self._available = value
            self.availableChanged.emit()

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


@pyqtClassInfo("D-Bus Interface", WINDOW_IFACE)
class WindowService(QObject):
    """org.tide.SessionsWindow /Window: Raise(id) — prośba drugiego uruchomienia o pokazanie okna."""

    def __init__(self, backend: Backend):
        super().__init__()
        self.backend = backend

    @pyqtSlot(str)
    def Raise(self, coordinator_id: str) -> None:
        if coordinator_id:
            self.backend.select(coordinator_id)
        self.backend.raiseRequested.emit()


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="tide-sessions", description="Okno Tide Sesje")
    parser.add_argument("--select", default="", help="koordynator do pokazania")
    parser.add_argument("--data", help="dane z pliku JSON zamiast z tide-daemon")
    parser.add_argument("--grab", help="zapisz zrzut okna do PNG i zakończ")
    parser.add_argument("--ticket", default="", help="zaznaczony ticket (podgląd)")
    parser.add_argument("--result", default="", help="otwarty wynik podagenta (podgląd)")
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("Tide Sesje")
    app.setOrganizationName("Tide")
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon.fromTheme("tide-sessions", QIcon.fromTheme("view-list-details")))

    if args.data:
        with open(args.data, encoding="utf-8") as fh:
            backend = Backend(None, json.load(fh), select=args.select)
    else:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            print("tide-sessions: brak magistrali sesji D-Bus", file=sys.stderr)
            return 1
        if not bus.registerService(WINDOW_SERVICE):
            # Okno już działa — niech się pokaże (z wybranym koordynatorem), a my kończymy.
            message = QDBusMessage.createMethodCall(WINDOW_SERVICE, WINDOW_PATH, WINDOW_IFACE, "Raise")
            message.setArguments([args.select])
            bus.call(message, QDBus.CallMode.Block, 5000)
            return 0
        backend = Backend(bus, select=args.select)
        service = WindowService(backend)
        bus.registerObject(WINDOW_PATH, service, QDBusConnection.RegisterOption.ExportAllSlots)

    engine = QQmlApplicationEngine()
    engine.setInitialProperties({"backend": backend, "grabPath": os.path.abspath(args.grab) if args.grab else "",
                                 "preticket": args.ticket, "preresult": args.result})
    engine.load(QUrl.fromLocalFile(os.path.join(HERE, "qml", "Main.qml")))
    roots = engine.rootObjects()
    if not roots:
        return 1
    if args.width and args.height:
        roots[0].setProperty("width", args.width)
        roots[0].setProperty("height", args.height)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
