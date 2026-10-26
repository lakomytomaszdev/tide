#!/usr/bin/python3
"""Okno „Tide Worktrees”: QML (qml/Main.qml) z danymi z tide-daemon przez D-Bus.

Użycie: /usr/bin/python3 tide-worktrees.py [--data PLIK.json] [--grab PLIK.png] [--width N --height N]

--data  stała lista worktrees z pliku (np. wynik tide-daemon.py --print-worktrees), bez D-Bus —
        do podglądu i testów; usuwanie i otwieranie są wtedy wyłączone,
--grab  zapisuje zrzut okna do pliku PNG i kończy (np. z QT_QPA_PLATFORM=offscreen).

Okno działa w jednym egzemplarzu: rejestruje nazwę org.tide.WorktreesWindow, a kolejne
uruchomienie (np. kliknięcie kafelka w docku) prosi pierwsze o pokazanie się i kończy pracę.
Identyfikator aplikacji (tide-worktrees) odpowiada plikowi tide-worktrees.desktop, więc pasek zadań
pokazuje okno z ikoną i nazwą „Tide Worktrees”.
"""
import argparse
import json
import os
import sys

from PyQt6.QtCore import QMetaType, QObject, QUrl, pyqtClassInfo, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBus, QDBusArgument, QDBusConnection, QDBusMessage, QDBusPendingCallWatcher,
                          QDBusPendingReply, QDBusServiceWatcher)
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtQml import QQmlApplicationEngine

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ID = "tide-worktrees"
SERVICE, PATH, IFACE = "org.tide.Daemon", "/Worktrees", "org.tide.Daemon.Worktrees"
WINDOW_SERVICE, WINDOW_PATH, WINDOW_IFACE = "org.tide.WorktreesWindow", "/Window", "org.tide.WorktreesWindow"
REMOVE_TIMEOUT_MS = 30 * 60 * 1000  # duże katalogi potrafią się usuwać długo


class Backend(QObject):
    """Most między QML a tide-daemon: lista (JSON), akcje i ich wyniki jako sygnały."""

    listChanged = pyqtSignal()
    availableChanged = pyqtSignal()
    launchersChanged = pyqtSignal()
    removeFinished = pyqtSignal(str)
    pruneFinished = pyqtSignal(str)
    raiseRequested = pyqtSignal()

    def __init__(self, bus: QDBusConnection | None, data: list | None = None, parent=None):
        super().__init__(parent)
        self.bus = bus
        self._list = "[]"
        self._available = False
        self._launchers = json.dumps({"terminal": False, "rider": False})
        self._pending: set = set()  # QDBusPendingCallWatcher muszą żyć do odpowiedzi
        if data is not None:
            self._list = json.dumps(data, ensure_ascii=False)
            self._available = True
            return
        if bus is None:
            return
        bus.connect(SERVICE, PATH, IFACE, "Changed", self._changed)
        self._watcher = QDBusServiceWatcher(SERVICE, bus, QDBusServiceWatcher.WatchModeFlag.WatchForRegistration
                                            | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration, self)
        self._watcher.serviceRegistered.connect(lambda _name: self.refresh())
        self._watcher.serviceUnregistered.connect(lambda _name: self._set_available(False))
        self.refresh()

    # --- właściwości dla QML ------------------------------------------------------------------

    @pyqtProperty(str, notify=listChanged)
    def listJson(self) -> str:
        return self._list

    @pyqtProperty(bool, notify=availableChanged)
    def available(self) -> bool:
        return self._available

    @pyqtProperty(str, notify=launchersChanged)
    def launchersJson(self) -> str:
        return self._launchers

    @pyqtProperty(str, constant=True)
    def homeDir(self) -> str:
        return os.path.expanduser("~")

    # --- akcje --------------------------------------------------------------------------------

    @pyqtSlot()
    def refresh(self) -> None:
        if self.bus is None:
            return
        self._call("Refresh", [], None)  # usługa przeczyta repozytoria na nowo i wyśle Changed
        self._load()
        self._call("Launchers", [], self._got_launchers)

    @pyqtSlot("QStringList")
    def remove(self, paths) -> None:
        if self.bus is None:
            self.removeFinished.emit(json.dumps([{"path": p, "removed": False, "reason": "error",
                                                  "message": "podgląd bez usługi — nic nie usunięto"} for p in paths],
                                                ensure_ascii=False))
            return
        argument = QDBusArgument(list(paths), QMetaType.Type.QStringList.value)  # sygnatura „as”
        self._call("Remove", [argument], self._removed, timeout=REMOVE_TIMEOUT_MS)

    def _removed(self, ok: bool, value) -> None:
        # Usługa odświeża listę w tle (pełny przegląd repozytoriów trwa kilka sekund), więc usunięte
        # wiersze zdejmujemy od razu, żeby okno nie pokazywało ich do następnego sygnału Changed.
        if ok and isinstance(value, str):
            try:
                gone = {r["path"] for r in json.loads(value) if r.get("removed")}
                items = [w for w in json.loads(self._list or "[]") if w.get("path") not in gone]
            except (ValueError, TypeError, KeyError):
                gone, items = set(), None
            if gone and items is not None:
                self._list = json.dumps(items, ensure_ascii=False)
                self.listChanged.emit()
        self.removeFinished.emit(value if ok else "null")

    @pyqtSlot(str)
    def prune(self, repo_path: str) -> None:
        if self.bus is None:
            return
        self._call("Prune", [repo_path], lambda ok, value: self.pruneFinished.emit(value if ok else "null"),
                   timeout=120_000)

    @pyqtSlot(str, str)
    def open(self, path: str, app: str) -> None:
        if self.bus is not None:
            self._call("Open", [path, app], None)

    # --- wnętrze ------------------------------------------------------------------------------

    @pyqtSlot()
    def _changed(self) -> None:
        self._load()

    def _load(self) -> None:
        self._call("List", [], self._got_list)

    def _got_list(self, ok: bool, value) -> None:
        if not ok:
            self._set_available(False)
            return
        if isinstance(value, str) and value != self._list:
            self._list = value
            self.listChanged.emit()
        self._set_available(True)

    def _got_launchers(self, ok: bool, value) -> None:
        if ok and isinstance(value, str) and value != self._launchers:
            self._launchers = value
            self.launchersChanged.emit()

    def _set_available(self, value: bool) -> None:
        if value != self._available:
            self._available = value
            self.availableChanged.emit()

    def _call(self, member: str, args: list, then, timeout: int = 25_000) -> None:
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


@pyqtClassInfo("D-Bus Interface", WINDOW_IFACE)
class WindowService(QObject):
    """org.tide.WorktreesWindow /Window: Raise() — prośba drugiego uruchomienia o pokazanie okna."""

    def __init__(self, backend: Backend):
        super().__init__()
        self.backend = backend

    @pyqtSlot()
    def Raise(self) -> None:
        self.backend.raiseRequested.emit()


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="tide-worktrees", description="Okno Worktrees (Tide)")
    parser.add_argument("--data", help="lista worktrees z pliku JSON zamiast z tide-daemon")
    parser.add_argument("--grab", help="zapisz zrzut okna do PNG i zakończ")
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("Tide Worktrees")
    app.setOrganizationName("Tide")
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon.fromTheme("vcs-branch"))

    if args.data:
        with open(args.data, encoding="utf-8") as fh:
            backend = Backend(None, json.load(fh))
        backend._launchers = json.dumps({"terminal": True, "rider": True})
    else:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            print("tide-worktrees: brak magistrali sesji D-Bus", file=sys.stderr)
            return 1
        if not bus.registerService(WINDOW_SERVICE):
            # Okno już działa — niech się pokaże, a my kończymy.
            bus.call(QDBusMessage.createMethodCall(WINDOW_SERVICE, WINDOW_PATH, WINDOW_IFACE, "Raise"),
                     QDBus.CallMode.Block, 5000)
            return 0
        backend = Backend(bus)
        service = WindowService(backend)
        bus.registerObject(WINDOW_PATH, service, QDBusConnection.RegisterOption.ExportAllSlots)

    engine = QQmlApplicationEngine()
    engine.setInitialProperties({"backend": backend, "grabPath": os.path.abspath(args.grab) if args.grab else ""})
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
