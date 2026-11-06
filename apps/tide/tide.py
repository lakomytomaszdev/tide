#!/usr/bin/python3
"""Okno „Tide”: pasek widoków (Tablica, Sprawy, Sesje, Worktrees, Docker, Raporty, Modele) i widok na wierzchu —
QML (qml/Main.qml), dane widoków z tide-daemon przez D-Bus.

Użycie: /usr/bin/python3 tide.py [--view WIDOK] [--select ELEMENT] [--data [WIDOK=]PLIK.json ...]
                                 [--preview KLUCZ=WARTOŚĆ ...] [--grab PLIK.png] [--width N --height N]

--view     widok na start: board, issues, sessions, worktrees, docker, reports, models (bez — domyślny z views.json;
           gdy okno już działa, bez --view zostaje na bieżącym widoku i tylko się pokazuje),
--select   element widoku: koordynator w Sesjach (claude:<sesja>), ścieżka worktree w Worktrees,
           kontener w Dockerze, serwer w Modelach (nazwa z models.json), w przyszłości karta, dzień raportu,
--data     stałe dane widoku z pliku, bez D-Bus — do podglądu i testów (akcje wyłączone). Samo PLIK
           to dane widoku z --view; WIDOK=PLIK — dane innego widoku (można powtórzyć, np. żeby pasek
           pokazał plakietki). Widoki bez danych są wtedy puste,
--preview  stan widoku do podglądu, np. ticket=TOM-140, result=<id przebiegu> (Sesje), pick=<ścieżka>,
           confirm=1 (Worktrees); klucz można powtórzyć,
--grab     zapisuje zrzut okna do PNG i kończy (np. z QT_QPA_PLATFORM=offscreen).

Okno działa w jednym egzemplarzu: rejestruje nazwę org.tide.Window (obiekt /Window, metody
Show(widok, element) → b i CurrentView() → s), a kolejne uruchomienie (dock, KRunner, widżety przez
tide-daemon) prosi pierwsze o pokazanie się na widoku i kończy. Dla zgodności z dawnymi oknami ten sam obiekt
odpowiada też jako org.tide.SessionsWindow (Raise(s id) → Sesje) i org.tide.WorktreesWindow
(Raise() → Worktrees), jeśli te nazwy są wolne.

Widoki opisuje views.json: widok z modułem (katalog z backend.py i qml/) dostaje backend z
create(bus, data); widok bez modułu — PlaceholderView z pustym stanem i zaczepem plakietki.

Sieć: silnik QML okna dostaje fabrykę menedżera sieci, który odmawia każdego żądania poza plikami
lokalnymi (file:, qrc:, data:) — żaden widok nie ładuje treści z sieci, a w ten sposób obrazek albo
inny zasób z dokumentu (plan, specyfikacja, raport) nie może niczego pobrać, nawet gdyby przeszedł
przez oczyszczanie treści (druga warstwa po apps/board/backend.py: render_markdown).
"""
import argparse
import importlib.util
import json
import os
import sys

from PyQt6 import sip
from PyQt6.QtCore import QObject, QUrl, pyqtClassInfo, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBus, QDBusAbstractAdaptor, QDBusConnection, QDBusMessage, QDBusPendingCallWatcher,
                          QDBusPendingReply, QDBusServiceWatcher)
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlNetworkAccessManagerFactory

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ID = "tide"
DAEMON = "org.tide.Daemon"
WINDOW_SERVICE, WINDOW_PATH, WINDOW_IFACE = "org.tide.Window", "/Window", "org.tide.Window"
# Nazwy dawnych okien — kto jeszcze woła Raise, trafia do okna Tide na właściwym widoku.
LEGACY_SESSIONS = "org.tide.SessionsWindow"
LEGACY_WORKTREES = "org.tide.WorktreesWindow"
TONES = ("muted", "waiting", "accent", "negative")


LOCAL_SCHEMES = ("file", "qrc", "data")
BLOCKED_URL = "tide-blocked:"   # nieznany schemat — QNetworkAccessManager od razu kończy żądanie błędem


class LocalOnlyNetwork(QNetworkAccessManager):
    """Menedżer sieci QML okna: żądanie spoza plików lokalnych zamienia na adres o nieznanym schemacie,
    więc kończy się błędem (ProtocolUnknownError) bez żadnego połączenia."""

    def createRequest(self, operation, request, data=None):
        if request.url().scheme().lower() not in LOCAL_SCHEMES:
            request = QNetworkRequest(QUrl(BLOCKED_URL + "network"))
        return super().createRequest(operation, request, data)


class LocalOnlyNetworkFactory(QQmlNetworkAccessManagerFactory):
    def __init__(self):
        super().__init__()
        self._managers = []   # obiekty Pythona muszą żyć — inaczej znika nadpisane createRequest

    def create(self, parent):
        manager = LocalOnlyNetwork(parent)
        sip.transferto(manager, None)    # usuwa go silnik QML (C++), nie Python
        self._managers.append(manager)   # jeden na wątek silnika — kilka obiektów na całe życie okna
        return manager


def guard_network(engine: QQmlEngine) -> QQmlNetworkAccessManagerFactory:
    """Silnik QML bez dostępu do sieci; fabrykę trzeba trzymać, póki żyje silnik."""
    factory = LocalOnlyNetworkFactory()
    engine.setNetworkAccessManagerFactory(factory)
    return factory


def load_views(base: str = HERE) -> list[dict]:
    with open(os.path.join(base, "views.json"), encoding="utf-8") as fh:
        views = list(json.load(fh).get("views") or [])
    for view in views:
        if view.get("module"):
            view["url"] = QUrl.fromLocalFile(os.path.join(base, "views", view["id"], "qml", view["qml"])).toString()
        else:
            view["url"] = ""
    return views


def default_view(views: list[dict]) -> str:
    return next((v["id"] for v in views if v.get("default")), views[0]["id"] if views else "")


def load_module(base: str, view_id: str):
    """backend.py widoku z views/<id>/ (pakiet zainstalowany albo złożony przez stage.py)."""
    path = os.path.join(base, "views", view_id, "backend.py")
    spec = importlib.util.spec_from_file_location(f"tide_view_{view_id}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_badge(value) -> tuple[str, str]:
    """Odpowiedź zaczepu plakietki: JSON {"text": "11", "tone": "muted"} → (tekst, ton); źle — ukryta."""
    try:
        badge = json.loads(value) if isinstance(value, str) else None
    except ValueError:
        badge = None
    if not isinstance(badge, dict):
        return "", "muted"
    text = str(badge.get("text") or "")[:12]
    tone = badge.get("tone") if badge.get("tone") in TONES else "muted"
    return text, tone


class PlaceholderBackend(QObject):
    """Backend widoku bez modułu (Tablica, Docker, Raporty, zanim powstaną): sama plakietka z zaczepu
    w tide-daemon (badge w views.json). Brak usługi, obiektu albo metody — plakietka ukryta, bez błędów."""

    badgeChanged = pyqtSignal()

    def __init__(self, bus: QDBusConnection | None, badge: dict | None = None, parent=None):
        super().__init__(parent)
        self.bus = bus
        self.spec = badge or {}
        self._text, self._tone = "", "muted"
        self._pending: set = set()
        if bus is None or not self.spec.get("path"):
            return
        bus.connect(DAEMON, self.spec["path"], self.spec.get("interface", ""), "Changed", self._changed)
        self._watcher = QDBusServiceWatcher(DAEMON, bus, QDBusServiceWatcher.WatchModeFlag.WatchForRegistration
                                            | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration, self)
        self._watcher.serviceRegistered.connect(lambda _name: self.reload())
        self._watcher.serviceUnregistered.connect(lambda _name: self._set("", "muted"))
        self.reload()

    @pyqtProperty(bool, constant=True)
    def available(self) -> bool:
        return False

    @pyqtProperty(str, notify=badgeChanged)
    def badgeText(self) -> str:
        return self._text

    @pyqtProperty(str, notify=badgeChanged)
    def badgeTone(self) -> str:
        return self._tone

    @pyqtSlot()
    def reload(self) -> None:
        if self.bus is None or not self.spec.get("path"):
            return
        message = QDBusMessage.createMethodCall(DAEMON, self.spec["path"], self.spec.get("interface", ""),
                                                self.spec.get("method", "Badge"))
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(message, 5000), self)
        self._pending.add(watcher)

        def finished(w):
            self._pending.discard(w)
            reply = QDBusPendingReply(w)
            ok = not reply.isError() and bool(reply.reply().arguments())
            w.deleteLater()
            self._set(*(parse_badge(reply.argumentAt(0)) if ok else ("", "muted")))

        watcher.finished.connect(finished)

    @pyqtSlot()
    def _changed(self) -> None:
        self.reload()

    def _set(self, text: str, tone: str) -> None:
        if (text, tone) != (self._text, self._tone):
            self._text, self._tone = text, tone
            self.badgeChanged.emit()


class Shell(QObject):
    """Stan okna dla QML: widoki, backendy i prośby o pokazanie (Show z D-Bus)."""

    showRequested = pyqtSignal(str, str)
    currentChanged = pyqtSignal()

    def __init__(self, views: list[dict], backends: dict, parent=None):
        super().__init__(parent)
        self.views = views
        self.backends = backends
        # Backendy żyją tyle co okno: rodzic po stronie C++ i jawna własność C++ — inaczej obiekt
        # zwrócony do QML przez backend(id) dostaje własność JavaScriptu i GC mógłby go usunąć
        # razem z połączeniami D-Bus, gdy QML nie trzyma do niego odwołania.
        for backend in backends.values():
            if backend.parent() is None:
                backend.setParent(self)
            QQmlEngine.setObjectOwnership(backend, QQmlEngine.ObjectOwnership.CppOwnership)
        self._json = json.dumps(views, ensure_ascii=False)
        self._current = ""

    # Bieżący widok — ustawia go QML (Main.qml) przy każdym przełączeniu; CurrentView() z D-Bus.
    @pyqtProperty(str, notify=currentChanged)
    def current(self) -> str:
        return self._current

    @current.setter
    def current(self, value: str) -> None:
        if value != self._current:
            self._current = value
            self.currentChanged.emit()

    @pyqtProperty(str, constant=True)
    def viewsJson(self) -> str:
        return self._json

    @pyqtSlot(str, result=QObject)
    def backend(self, view_id: str):
        return self.backends.get(view_id)

    def show(self, view: str, item: str = "") -> bool:
        """Pokazuje okno na widoku (pusty — zostaje bieżący) i elemencie. Nieznany widok → False."""
        if view and view not in {v["id"] for v in self.views}:
            return False
        self.showRequested.emit(view or "", item or "")
        return True


@pyqtClassInfo("D-Bus Interface", WINDOW_IFACE)
class WindowAdaptor(QDBusAbstractAdaptor):
    """org.tide.Window /Window: Show(widok, element) → b — otwarcie okna na widoku i elemencie."""

    def __init__(self, shell: Shell):
        super().__init__(shell)
        self.shell = shell

    @pyqtSlot(str, str, result=bool)
    def Show(self, view: str, item: str) -> bool:
        return self.shell.show(view, item)

    @pyqtSlot(result=str)
    def CurrentView(self) -> str:
        return self.shell.current


@pyqtClassInfo("D-Bus Interface", LEGACY_SESSIONS)
class LegacySessionsAdaptor(QDBusAbstractAdaptor):
    """Zgodność z oknem „Tide Sesje”: Raise(id) → widok Sesje z koordynatorem."""

    def __init__(self, shell: Shell):
        super().__init__(shell)
        self.shell = shell

    @pyqtSlot(str)
    def Raise(self, coordinator_id: str) -> None:
        self.shell.show("sessions", coordinator_id)


@pyqtClassInfo("D-Bus Interface", LEGACY_WORKTREES)
class LegacyWorktreesAdaptor(QDBusAbstractAdaptor):
    """Zgodność z oknem „Tide Worktrees”: Raise() → widok Worktrees."""

    def __init__(self, shell: Shell):
        super().__init__(shell)
        self.shell = shell

    @pyqtSlot()
    def Raise(self) -> None:
        self.shell.show("worktrees", "")


def export(bus: QDBusConnection, shell: Shell) -> bool:
    """Rejestruje org.tide.Window z obiektem /Window. False — okno już działa (nazwa zajęta).
    Nazwy dawnych okien bierzemy, jeśli są wolne (stare okno sprzed aktualizacji może je trzymać)."""
    if not bus.registerService(WINDOW_SERVICE):
        return False
    shell._adaptors = [WindowAdaptor(shell), LegacySessionsAdaptor(shell), LegacyWorktreesAdaptor(shell)]
    bus.registerObject(WINDOW_PATH, shell, QDBusConnection.RegisterOption.ExportAdaptors)
    for name in (LEGACY_SESSIONS, LEGACY_WORKTREES):
        bus.registerService(name)
    return True


def ask_running(bus: QDBusConnection, view: str, item: str) -> bool:
    """Prosi działające okno o pokazanie się na widoku. True, gdy odpowiedziało."""
    message = QDBusMessage.createMethodCall(WINDOW_SERVICE, WINDOW_PATH, WINDOW_IFACE, "Show")
    message.setArguments([view, item])
    reply = bus.call(message, QDBus.CallMode.Block, 5000)
    return reply.type() == QDBusMessage.MessageType.ReplyMessage


def parse_pairs(values: list[str], option: str) -> dict:
    """KLUCZ=WARTOŚĆ (można powtórzyć) → {klucz: [wartości]}."""
    out: dict[str, list[str]] = {}
    for value in values:
        key, sep, rest = value.partition("=")
        if not sep or not key:
            raise SystemExit(f"tide: {option} oczekuje KLUCZ=WARTOŚĆ, dostało: {value}")
        out.setdefault(key, []).append(rest)
    return out


def parse_args(argv, views: list[dict]):
    ids = [v["id"] for v in views]
    parser = argparse.ArgumentParser(prog="tide", description="Okno Tide")
    parser.add_argument("--view", choices=ids, default="", help="widok na start")
    parser.add_argument("--select", default="", help="element widoku (koordynator, worktree…)")
    parser.add_argument("--data", action="append", default=[], help="[WIDOK=]PLIK.json: dane z pliku zamiast z D-Bus")
    parser.add_argument("--preview", action="append", default=[], help="KLUCZ=WARTOŚĆ: stan widoku do podglądu")
    parser.add_argument("--grab", help="zapisz zrzut okna do PNG i zakończ")
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    args = parser.parse_args(argv)
    # args.view zostaje puste bez --view: drugie uruchomienie (menu, dock, KRunner „Tide”) prosi wtedy
    # o Show("", element) i działające okno zostaje na swoim widoku. Domyślny widok dotyczy tylko
    # pierwszego egzemplarza (initial_view).
    args.initial_view = args.view or default_view(views)
    data_files = {}
    for value in args.data:
        view, sep, path = value.partition("=")
        if sep and view in ids:
            data_files[view] = path
        else:
            data_files[args.initial_view] = value
    args.data_files = data_files
    args.preview_map = parse_pairs(args.preview, "--preview")
    return args


def make_backends(views: list[dict], bus: QDBusConnection | None, data: dict | None, base: str = HERE) -> dict:
    """Backend każdego widoku. data: {widok: dane} w trybie podglądu (bez magistrali), None — na żywo."""
    backends = {}
    for view in views:
        if view.get("module"):
            module = load_module(base, view["id"])
            if data is None:
                backends[view["id"]] = module.create(bus, None)
            else:
                backends[view["id"]] = module.create(None, data.get(view["id"]))
        else:
            backends[view["id"]] = PlaceholderBackend(bus if data is None else None, view.get("badge"))
    return backends


def main(argv=None) -> int:
    views = load_views()
    args = parse_args(sys.argv[1:] if argv is None else argv, views)
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("Tide")
    app.setOrganizationName("Tide")
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon.fromTheme("tide", QIcon.fromTheme("view-list-details")))

    if args.data_files:
        data = {}
        for view, path in args.data_files.items():
            with open(path, encoding="utf-8") as fh:
                data[view] = json.load(fh)
        backends = make_backends(views, None, data)
        shell = Shell(views, backends)
    else:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            print("tide: brak magistrali sesji D-Bus", file=sys.stderr)
            return 1
        if bus.interface().isServiceRegistered(WINDOW_SERVICE).value():
            # Okno już działa — niech się pokaże na widoku, a my kończymy.
            return 0 if ask_running(bus, args.view, args.select) else 1
        backends = make_backends(views, bus, None)
        shell = Shell(views, backends)
        if not export(bus, shell):
            return 0 if ask_running(bus, args.view, args.select) else 1

    engine = QQmlApplicationEngine()
    network_factory = guard_network(engine)  # noqa: F841 — żyje tak długo jak main(), czyli silnik
    for backend in backends.values():
        if hasattr(backend, "install_image_provider"):
            backend.install_image_provider(engine)
    engine.setInitialProperties({"shell": shell, "grabPath": os.path.abspath(args.grab) if args.grab else "",
                                 "initialView": args.initial_view, "initialItem": args.select,
                                 "preview": args.preview_map})
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
