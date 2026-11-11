"""Widok „Worktrees” okna Tide: worktrees repozytoriów — dane i akcje z tide-daemon przez D-Bus
(/Worktrees) albo stała lista z pliku (--data, np. wynik `tide-daemon.py --print-worktrees`;
usuwanie i otwieranie są wtedy wyłączone).

Moduł widoku (apps/tide/views.json): okno Tide ładuje go z views/worktrees/backend.py i woła
create(bus, data). Strona QML to qml/WorktreesView.qml (WorktreesPage z akcjami i skrótami).
Plakietka w pasku widoków: ile worktrees jest w pracy (bez głównych katalogów repozytoriów).
"""
import importlib.util
import json
import os

from PyQt6.QtCore import QMetaType, QObject, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBusArgument, QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply,
                          QDBusServiceWatcher)

HERE = os.path.dirname(os.path.abspath(__file__))


def load_common(name: str):
    """Moduł z apps/common: w pakiecie (widgets/stage.py) leży obok tego pliku, w repozytorium — w apps/common."""
    for folder in (HERE, os.path.join(os.path.dirname(HERE), "common")):
        path = os.path.join(folder, name + ".py")
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location(f"tide_common_{name}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise ImportError(f"brak wspólnego modułu {name} (apps/common)")


windowconfig = load_common("windowconfig")  # tryb diffa zapamiętany w ustawieniach okna (PT-350)

SERVICE, PATH, IFACE = "org.tide.Daemon", "/Worktrees", "org.tide.Daemon.Worktrees"
GIT_PATH, GIT_IFACE = "/Git", "org.tide.Daemon.Git"
DIFF_TIMEOUT_MS = 120_000  # odczyt gita w puli wątków usługi; duże repozytorium potrafi potrwać
NO_SERVICE_REPLY = {"ok": False, "error": "no-service",
                    "message": "Usługa tide-daemon nie odpowiada. Spróbuj za chwilę."}
PREVIEW_REPLY = {"ok": False, "error": "preview", "message": "Podgląd z pliku — zmiany są niedostępne."}
REMOVE_TIMEOUT_MS = 30 * 60 * 1000  # duże katalogi potrafią się usuwać długo
FETCH_TIMEOUT_MS = 270_000  # limit pobierania w usłudze to 120 s, plus do 120 s czekania na blokadę repozytorium


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
    fetchFinished = pyqtSignal(str, str)  # (repoPath, JSON wyniku Fetch albo "null")
    badgeChanged = pyqtSignal()
    replied = pyqtSignal(str, str)  # (tag z QML, odpowiedź `Git.Diff` jako JSON) — odbiera ją widok diffa
    diffModeChanged = pyqtSignal()

    def __init__(self, bus: QDBusConnection | None, data: list | None = None, parent=None):
        super().__init__(parent)
        self.bus = bus
        self._preview = data is not None  # stała lista z pliku (--data): bez usługi
        self._list = "[]"
        self._available = False
        self._launchers = json.dumps({"terminal": False, "rider": False})
        self._pending: set = set()  # QDBusPendingCallWatcher muszą żyć do odpowiedzi
        self._badge = ""
        self._diff_mode = windowconfig.DIFF_MODE_DEFAULT
        self._diff_unsaved = False   # zapis się nie udał: do końca sesji obowiązuje wybór z pamięci
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
        self._watcher.serviceRegistered.connect(lambda _name: self.reload())
        self._watcher.serviceUnregistered.connect(lambda _name: self._set_available(False))
        # Na start tylko tanie odczyty (lista z pamięci usługi i programy) — plakietka w pasku okna Tide.
        # Pełny przegląd repozytoriów (Refresh) woła widok przy pierwszym pokazaniu i przycisk „Odśwież”.
        self.reload()

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
        self.reload()

    @pyqtSlot()
    def reload(self) -> None:
        """Lista i programy z usługi bez przeglądania repozytoriów od nowa."""
        if self.bus is None:
            return
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

    @pyqtSlot(str)
    def fetch(self, repo_path: str) -> None:
        """„Pobierz”: git fetch ze zdalnych repozytorium (usługa odpowiada po zakończeniu, do 120 s plus
        ewentualne czekanie na blokadę repozytorium); wynik w fetchFinished(repoPath, json)."""
        if self.bus is None:
            self.fetchFinished.emit(repo_path, json.dumps({"ok": False, "reason": "unavailable",
                                                           "message": "podgląd bez usługi — nic nie pobrano"},
                                                          ensure_ascii=False))
            return
        self._call("Fetch", [repo_path], lambda ok, value: self.fetchFinished.emit(repo_path, value if ok else "null"),
                   timeout=FETCH_TIMEOUT_MS)

    # Tryb widoku diffa (PT-350): "unified" albo "split", wspólny z Tablicą (plik ustawień okna, czytany przy
    # każdym odczycie); podgląd z pliku trzyma go tylko w pamięci.
    @pyqtProperty(str, notify=diffModeChanged)
    def diffMode(self) -> str:
        return self._diff_mode if self._preview or self._diff_unsaved else windowconfig.diff_mode()

    @pyqtSlot(str, result=str)
    def setDiffMode(self, value: str) -> str:
        """Zapisuje tryb diffa; pusty tekst albo powód, gdy zapis się nie udał (wtedy działa do końca sesji)."""
        if value not in windowconfig.DIFF_MODE_VALUES:
            return "Nieznany tryb diffa."
        problem = ""
        if not self._preview:
            try:
                windowconfig.set_diff_mode(value)
            except OSError as exc:
                problem = f"Nie zapisano ustawienia: {exc.strerror or exc}."
        self._diff_unsaved = problem != ""
        if value != self._diff_mode:
            self._diff_mode = value
            self.diffModeChanged.emit()
        return problem

    @pyqtSlot(str, str)
    def gitDiff(self, tag: str, request: str) -> None:
        """`Git.Diff` (K-36, wspólny widok diffa apps/common/qml/git/DiffView.qml): zmiany worktree (źródło
        `worktree`) albo gałęzi względem głównej (`range`). Żądanie JSON, odpowiedź w `replied(tag, json)`.
        Odczyt — nie zmienia listy ani repozytorium; `path` waliduje usługa (lista Worktrees)."""
        if self.bus is None:
            self.replied.emit(tag, json.dumps(PREVIEW_REPLY if self._preview else NO_SERVICE_REPLY,
                                              ensure_ascii=False))
            return

        def done(ok: bool, value) -> None:
            self.replied.emit(tag, value if ok and isinstance(value, str)
                              else json.dumps(NO_SERVICE_REPLY, ensure_ascii=False))

        self._call("Diff", [request], done, timeout=DIFF_TIMEOUT_MS, path=GIT_PATH, iface=GIT_IFACE)

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

    def _call(self, member: str, args: list, then, timeout: int = 25_000, path: str = PATH,
              iface: str = IFACE) -> None:
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
    """Backend widoku dla okna Tide: z magistralą (na żywo) albo ze stałą listą (data: list)."""
    if data is not None:
        backend = Backend(None, data if isinstance(data, list) else [])
        backend._launchers = json.dumps({"terminal": True, "rider": True})  # podgląd z przyciskami
        return backend
    return Backend(bus)
