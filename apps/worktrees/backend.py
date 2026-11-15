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
BOARD_PATH, BOARD_IFACE = "/Board", "org.tide.Daemon.Board"
DIFF_TIMEOUT_MS = 120_000  # odczyt gita w puli wątków usługi; duże repozytorium potrafi potrwać
NO_SERVICE_REPLY = {"ok": False, "error": "no-service",
                    "message": "Usługa tide-daemon nie odpowiada. Spróbuj za chwilę."}
PREVIEW_REPLY = {"ok": False, "error": "preview", "message": "Podgląd z pliku — zmiany są niedostępne."}
PREVIEW_MAP_REPLY = {"ok": False, "error": "preview", "message": "Podgląd z pliku — mapa gałęzi jest niedostępna."}
PREVIEW_BOARD_REPLY = {"ok": False, "error": "preview", "message": "Podgląd z pliku — scalanie jest niedostępne."}
# Okno scalania z mapy gałęzi (K-99, PT-395): jedyne metody `/Board`, które Worktrees może wywołać. Ten sam kontrakt
# co Tablica — lista celów, podgląd z tokenem i zgoda `{targetRef, previewToken}` bieżącego podglądu. Bez zakładania
# kart i bez starej drogi scalenia bez tokenu.
BOARD_CALLS = ("ListCardBranches", "PreviewCardMerge", "MergeCard")
MERGE_TIMEOUT_MS = 10 * 60 * 1000  # podgląd i scalenie czekają na blokadę repozytorium; jak w Tablicy
REMOVE_TIMEOUT_MS = 30 * 60 * 1000  # duże katalogi potrafią się usuwać długo
FETCH_TIMEOUT_MS = 150_000  # limit pobierania w usłudze to 120 s (bez czekania na blokadę silnika, K-36 M1)
PULL_TIMEOUT_MS = 120_000   # „Zaciągnij”: do 10 s czekania na blokadę repozytorium i 60 s na merge z checkoutem


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
    pullFinished = pyqtSignal(str, str)  # (ścieżka worktree, JSON wyniku PullFastForward albo "null")
    badgeChanged = pyqtSignal()
    replied = pyqtSignal(str, str)  # (tag z QML, odpowiedź `Git.Diff` jako JSON) — odbiera ją widok diffa
    diffModeChanged = pyqtSignal()
    branchMapReplied = pyqtSignal(str, str)  # (tag z QML, odpowiedź `Git.BranchMap` jako JSON) — odbiera ją tryb Gałęzie
    boardChanged = pyqtSignal()               # Tablica się zmieniła (Changed usługi) — mapa gałęzi do odświeżenia
    boardReplied = pyqtSignal(str, str)       # (tag z QML, odpowiedź metody `/Board` z BOARD_CALLS) — okno scalania
    viewModeChanged = pyqtSignal()
    branchProjectChanged = pyqtSignal()

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
        # Tryb okna (Katalogi/Gałęzie) i projekt trybu Gałęzie (PT-388): w ustawieniach okna jak tryb diffa;
        # podgląd z pliku trzyma je tylko w pamięci.
        self._view_mode = windowconfig.WORKTREES_MODE_DEFAULT if data is not None else windowconfig.worktrees_mode()
        self._branch_project = "" if data is not None else windowconfig.worktrees_project()
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
        bus.connect(SERVICE, BOARD_PATH, BOARD_IFACE, "Changed", self._board_changed)
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
        """„Pobierz”: git fetch ze zdalnych repozytorium (usługa odpowiada po zakończeniu, do 120 s); wynik
        w fetchFinished(repoPath, json)."""
        if self.bus is None:
            self.fetchFinished.emit(repo_path, json.dumps({"ok": False, "reason": "unavailable",
                                                           "message": "podgląd bez usługi — nic nie pobrano"},
                                                          ensure_ascii=False))
            return
        self._call("Fetch", [repo_path], lambda ok, value: self.fetchFinished.emit(repo_path, value if ok else "null"),
                   timeout=FETCH_TIMEOUT_MS)

    @pyqtSlot(str)
    def pull(self, path: str) -> None:
        """„Zaciągnij”: `merge --ff-only` do upstreamu w worktree (PullFastForward, odpowiedź po zakończeniu);
        wynik w pullFinished(path, json). Lista odświeża się sygnałem Changed usługi."""
        if self.bus is None:
            self.pullFinished.emit(path, json.dumps({"ok": False, "reason": "unavailable",
                                                     "message": "podgląd bez usługi — nic nie zaciągnięto"},
                                                    ensure_ascii=False))
            return
        self._call("PullFastForward", [path],
                   lambda ok, value: self.pullFinished.emit(path, value if ok and isinstance(value, str) else "null"),
                   timeout=PULL_TIMEOUT_MS)

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

    # Tryb okna i projekt mapy gałęzi (PT-388). Wartości niezapisane (błąd zapisu, podgląd) działają do końca sesji.
    @pyqtProperty(str, notify=viewModeChanged)
    def viewMode(self) -> str:
        return self._view_mode

    @pyqtSlot(str, result=str)
    def setViewMode(self, value: str) -> str:
        """Zapisuje tryb okna („dirs” albo „branches”); pusty tekst albo powód, gdy zapis się nie udał."""
        if value not in windowconfig.WORKTREES_MODE_VALUES:
            return "Nieznany tryb okna."
        problem = self._persist(windowconfig.set_worktrees_mode, value)
        if value != self._view_mode:
            self._view_mode = value
            self.viewModeChanged.emit()
        return problem

    @pyqtProperty(str, notify=branchProjectChanged)
    def branchProject(self) -> str:
        return self._branch_project

    @pyqtSlot(str, result=str)
    def setBranchProject(self, slug: str) -> str:
        """Zapisuje projekt trybu Gałęzie (slug z `Git.BranchMap({})`); pusty tekst albo powód błędu zapisu."""
        if not slug or len(slug) > windowconfig.PROJECT_MAX:
            return "Nieprawidłowy projekt."
        problem = self._persist(windowconfig.set_worktrees_project, slug)
        if slug != self._branch_project:
            self._branch_project = slug
            self.branchProjectChanged.emit()
        return problem

    def _persist(self, setter, value: str) -> str:
        if self._preview:
            return ""
        try:
            setter(value)
        except OSError as exc:
            return f"Nie zapisano ustawienia: {exc.strerror or exc}."
        return ""

    @pyqtSlot(str, str)
    def branchMap(self, tag: str, request: str) -> None:
        """`Git.BranchMap` (K-99, PT-387): `{}` — projekty Tablicy z repozytorium, `{project}` — drzewo gałęzi kart
        i ticketów. Żądanie JSON, odpowiedź w `branchMapReplied(tag, json)`. Odczyt — nie zmienia Tablicy ani
        repozytorium; ścieżki repozytorium z UI nie ma (usługa bierze ją z projektu Tablicy)."""
        if self.bus is None:
            self.branchMapReplied.emit(tag, json.dumps(PREVIEW_MAP_REPLY if self._preview else NO_SERVICE_REPLY,
                                                       ensure_ascii=False))
            return

        def done(ok: bool, value) -> None:
            self.branchMapReplied.emit(tag, value if ok and isinstance(value, str)
                                       else json.dumps(NO_SERVICE_REPLY, ensure_ascii=False))

        self._call("BranchMap", [request], done, timeout=DIFF_TIMEOUT_MS, path=GIT_PATH, iface=GIT_IFACE)

    # Okno scalania z mapy gałęzi (K-99, PT-395): wspólny komponent git/MergeWindow.qml. Backend tylko przekłada
    # sygnały okna na wywołania usługi — sam nie buduje żądań scalenia, nie pamięta tokenów i niczego nie ponawia.
    # Odpowiedź (także odmowa usługi) wraca jedną emisją `boardReplied(tag, json)`.

    @pyqtSlot(str, str, str)
    def listCardBranches(self, tag: str, card_id: str, request: str) -> None:
        """`ListCardBranches(id, {purpose: "merge", text, limit})` — cele scalenia i skróty dla okna (odczyt)."""
        self._board(tag, "ListCardBranches", [card_id, request])

    @pyqtSlot(str, str, str)
    def previewCardMerge(self, tag: str, card_id: str, request: str) -> None:
        """`PreviewCardMerge(id, {targetRef})` — podgląd z `previewToken` (odczyt, bez zgody)."""
        self._board(tag, "PreviewCardMerge", [card_id, request])

    @pyqtSlot(str, str, str)
    def mergeCard(self, tag: str, card_id: str, request: str) -> None:
        """`MergeCard(id, {targetRef, previewToken})` — zgoda z kliknięcia „Scal do <cel>”; żądanie przekazane
        bez zmian, jedno wywołanie na jedno kliknięcie."""
        self._board(tag, "MergeCard", [card_id, request])

    def _board(self, tag: str, member: str, args: list) -> None:
        if member not in BOARD_CALLS:
            self.boardReplied.emit(tag, json.dumps({"ok": False, "error": "not-allowed",
                                                    "message": f"Okno Worktrees nie wywołuje {member}."},
                                                   ensure_ascii=False))
            return
        if self.bus is None:
            self.boardReplied.emit(tag, json.dumps(PREVIEW_BOARD_REPLY if self._preview else NO_SERVICE_REPLY,
                                                   ensure_ascii=False))
            return

        def done(ok: bool, value) -> None:
            self.boardReplied.emit(tag, value if ok and isinstance(value, str)
                                   else json.dumps(NO_SERVICE_REPLY, ensure_ascii=False))

        timeout = 25_000 if member == "ListCardBranches" else MERGE_TIMEOUT_MS
        self._call(member, args, done, timeout=timeout, path=BOARD_PATH, iface=BOARD_IFACE)

    @pyqtSlot(str, str)
    def open(self, path: str, app: str) -> None:
        if self.bus is not None:
            self._call("Open", [path, app], None)

    # --- wnętrze ------------------------------------------------------------------------------

    @pyqtSlot()
    def _changed(self) -> None:
        self._load()

    @pyqtSlot()
    def _board_changed(self) -> None:
        self.boardChanged.emit()

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
