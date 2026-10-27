"""Widok „Worktrees” okna Tide: worktrees repozytoriów — dane i akcje z tide-daemon przez D-Bus
(/Worktrees) albo stała lista z pliku (--data, np. wynik `tide-daemon.py --print-worktrees`;
usuwanie i otwieranie są wtedy wyłączone).

Moduł widoku (apps/tide/views.json): okno Tide ładuje go z views/worktrees/backend.py i woła
create(bus, data). Strona QML to qml/WorktreesView.qml (WorktreesPage z akcjami i skrótami).
Plakietka w pasku widoków: ile worktrees jest w pracy (bez głównych katalogów repozytoriów).
"""
import json
import os

from PyQt6.QtCore import QMetaType, QObject, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBusArgument, QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply,
                          QDBusServiceWatcher)

SERVICE, PATH, IFACE = "org.tide.Daemon", "/Worktrees", "org.tide.Daemon.Worktrees"
REMOVE_TIMEOUT_MS = 30 * 60 * 1000  # duże katalogi potrafią się usuwać długo


def active_count(list_json: str) -> int:
    """Worktrees w pracy jak w kafelku i nagłówku okna: grupa „active”, bez głównych katalogów."""
    try:
        items = json.loads(list_json or "[]")
    except ValueError:
        return 0
    if not isinstance(items, list):
        return 0
    return sum(1 for w in items if isinstance(w, dict) and not w.get("isMain") and w.get("group") == "active")


class Backend(QObject):
    """Most między QML a tide-daemon: lista (JSON), akcje i ich wyniki jako sygnały."""

    listChanged = pyqtSignal()
    availableChanged = pyqtSignal()
    launchersChanged = pyqtSignal()
    removeFinished = pyqtSignal(str)
    pruneFinished = pyqtSignal(str)
    badgeChanged = pyqtSignal()

    def __init__(self, bus: QDBusConnection | None, data: list | None = None, parent=None):
        super().__init__(parent)
        self.bus = bus
        self._list = "[]"
        self._available = False
        self._launchers = json.dumps({"terminal": False, "rider": False})
        self._pending: set = set()  # QDBusPendingCallWatcher muszą żyć do odpowiedzi
        self._badge = ""
        self.listChanged.connect(self._update_badge)
        self.availableChanged.connect(self._update_badge)
        if data is not None:
            self._list = json.dumps(data, ensure_ascii=False)
            self._available = True
            self._update_badge()
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

    # Plakietka w pasku widoków okna Tide; pusta — ukryta (bez usługi albo nic nie jest w pracy).
    @pyqtProperty(str, notify=badgeChanged)
    def badgeText(self) -> str:
        return self._badge

    @pyqtProperty(str, constant=True)
    def badgeTone(self) -> str:
        return "muted"

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

    @pyqtSlot("QStringList", bool, str)
    def removeForced(self, paths, delete_branches: bool, confirmed: str) -> None:
        """Usuwanie po potwierdzeniu „Usuń mimo to” (RemoveForced). confirmed: JSON ze stanem,
        który użytkownik widział — usługa nic nie usunie, jeśli od tej chwili przybyło pracy."""
        if self.bus is None:
            self.removeFinished.emit(json.dumps([{"path": p, "removed": False, "reason": "error", "status": "refused",
                                                  "message": "podgląd bez usługi — nic nie usunięto"} for p in paths],
                                                ensure_ascii=False))
            return
        argument = QDBusArgument(list(paths), QMetaType.Type.QStringList.value)
        self._call("RemoveForced", [argument, bool(delete_branches), confirmed or ""], self._removed,
                   timeout=REMOVE_TIMEOUT_MS)

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

    def _update_badge(self) -> None:
        count = active_count(self._list) if self._available else 0
        badge = str(count) if count else ""
        if badge != self._badge:
            self._badge = badge
            self.badgeChanged.emit()

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


def create(bus: QDBusConnection | None, data=None) -> Backend:
    """Backend widoku dla okna Tide: z magistralą (na żywo) albo ze stałą listą (data: list)."""
    if data is not None:
        backend = Backend(None, data if isinstance(data, list) else [])
        backend._launchers = json.dumps({"terminal": True, "rider": True})  # podgląd z przyciskami
        return backend
    return Backend(bus)
