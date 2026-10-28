#!/usr/bin/python3
"""Okno „Tide: nowe zadanie”: QML (qml/Main.qml) z danymi z tide-daemon (/Tasks i /Models) przez D-Bus.

Użycie: /usr/bin/python3 tide-new-task.py [--data PLIK.json] [--grab PLIK.png] [--width N --height N]
                                           [--prompt TEKST] [--error TEKST] [--image PLIK ...] [--no-worktree]

--data         stałe dane z pliku zamiast z usługi: {"repos": […], "defaults": {…}, "suggestion": {…},
               "models": <Status() z /Models albo null>} —
               do podglądu i testów; start niczego wtedy nie uruchamia,
--grab         zapisuje zrzut okna do PNG i kończy (np. z QT_QPA_PLATFORM=offscreen),
--prompt, --error, --image, --no-worktree
               stan formularza do podglądu (polecenie, błąd, miniatury zrzutów, wyłączony worktree).

Okno działa w jednym egzemplarzu: rejestruje nazwę org.tide.NewTaskWindow, a kolejne uruchomienie
(skrót, przycisk w widżecie) prosi pierwsze o pokazanie się i kończy pracę. Identyfikator
aplikacji (tide-new-task) odpowiada plikowi tide-new-task.desktop.

Model lokalny: okno czyta Status() obiektu /Models (rola `agent`: serwer, model, dostępność) i odświeża
go po sygnale Changed — opcja „Lokalny” gaśnie z powodem, gdy serwer przestanie odpowiadać. Przy „Lokalny”
program agenta (Codex albo Claude Code) też pochodzi z /Models (`agentProgram`, ustawiany w Tide → Modele):
okno pokazuje go w jednej linii zamiast przełącznika chmury. „Ustaw” i „zmień w Tide → Modele” otwierają okno
Tide na widoku `models` (drugie uruchomienie tide.py prosi działające okno o Show("models", "")).

Zrzuty ekranu (wklejone, przeciągnięte, „Zrób zrzut” przez Spectacle) trafiają do własnego katalogu
zadania w $XDG_DATA_HOME/tide/tasks/<czas>-<los>/ (katalog podaje usługa). Okno zamknięte bez startu
usuwa ten katalog; po starcie zostaje — agent czyta z niego obrazy. Obsługę zrzutów (ImageTray) dzieli
z oknem „Co ma zrobić agent?” na Tablicy: apps/common/taskimages.py (w pakiecie obok tego pliku).
"""
import argparse
import importlib.util
import json
import os
import sys

from PyQt6.QtCore import QObject, QProcess, QUrl, pyqtClassInfo, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBus, QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply,
                          QDBusServiceWatcher)
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtQml import QQmlApplicationEngine

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ID = "tide-new-task"
SERVICE, PATH, IFACE = "org.tide.Daemon", "/Tasks", "org.tide.Daemon.Tasks"
MODELS_PATH, MODELS_IFACE = "/Models", "org.tide.Daemon.Models"
MODELS_VIEW = "models"
# Stan modeli, zanim przyjdzie pierwsza odpowiedź Status() — „czekam”. Różny od null, który znaczy
# „usługa nie podaje stanu” (błąd odpowiedzi, brak /Models): zapamiętany „Lokalny” czeka tylko na ten pierwszy.
PENDING_MODELS = '{"pending": true}'
WINDOW_SERVICE, WINDOW_PATH, WINDOW_IFACE = "org.tide.NewTaskWindow", "/Window", "org.tide.NewTaskWindow"
SHORTCUT_ACTION = ("kwin", "TideNewAgentTask")
DEFAULT_SHORTCUT = "Meta+Alt+A"
START_TIMEOUT_MS = 10 * 60 * 1000   # worktree dużego repozytorium potrafi się zakładać długo


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


taskimages = load_common("taskimages")
Images = taskimages.Images
IMAGE_SUFFIXES, IMAGE_LIMIT, IMAGE_BYTES = taskimages.IMAGE_SUFFIXES, taskimages.IMAGE_LIMIT, taskimages.IMAGE_BYTES
data_home = taskimages.data_home


def tide_window_script() -> str:
    """Zainstalowane okno Tide ($TIDE_WINDOW albo XDG_DATA_HOME/tide/tide-window/tide.py) — jak w usłudze."""
    return os.environ.get("TIDE_WINDOW") or os.path.join(data_home(), "tide", "tide-window", "tide.py")


def shortcut_text(path: str | None = None) -> str:
    """Bieżący klawisz akcji „Tide: nowe zadanie” z kglobalshortcutsrc (tylko odczyt)."""
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    path = path or os.path.join(config, "kglobalshortcutsrc")
    group, (want_group, want_key) = "", SHORTCUT_ACTION
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    group = line[1:-1]
                elif group == want_group and line.startswith(want_key + "="):
                    active = line.split("=", 1)[1].split(",")[0].split("\t")[0].strip()
                    return "" if active.lower() == "none" else active
    except OSError:
        pass
    return DEFAULT_SHORTCUT


class Backend(QObject):
    """Most między QML a tide-daemon (/Tasks): dane jako JSON, akcje i ich wyniki jako sygnały."""

    reposChanged = pyqtSignal()
    modelsChanged = pyqtSignal()
    defaultsChanged = pyqtSignal()
    suggestionChanged = pyqtSignal()
    imagesChanged = pyqtSignal()
    availableChanged = pyqtSignal()
    busyChanged = pyqtSignal()
    startFinished = pyqtSignal(str)
    captureFinished = pyqtSignal(str)
    imageProblem = pyqtSignal(str)
    raiseRequested = pyqtSignal()

    def __init__(self, bus: QDBusConnection | None, data: dict | None = None, spectacle: str | None = None,
                 parent=None):
        super().__init__(parent)
        self.bus = bus
        self.data = data
        self._repos = "[]"
        self._defaults = "{}"
        self._suggestion = "{}"
        self._models = "null"
        self._available = False
        self._busy = False
        self._pending: set = set()
        self.tray = taskimages.ImageTray(spectacle, parent=self)
        self.tray.imagesChanged.connect(self.imagesChanged)
        self.tray.imageProblem.connect(self.imageProblem)
        self.tray.captureFinished.connect(self.captureFinished)
        self.shortcut = shortcut_text()
        if data is not None:
            self._repos = json.dumps(data.get("repos") or [], ensure_ascii=False)
            self._defaults = json.dumps(data.get("defaults") or {}, ensure_ascii=False)
            self._suggestion = json.dumps(data.get("suggestion") or {}, ensure_ascii=False)
            self._models = json.dumps(data.get("models"), ensure_ascii=False)
            self._available = True
            return
        if bus is None:
            return
        self._watcher = QDBusServiceWatcher(SERVICE, bus, QDBusServiceWatcher.WatchModeFlag.WatchForRegistration
                                            | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration, self)
        self._watcher.serviceRegistered.connect(lambda _name: self.refresh())
        self._watcher.serviceUnregistered.connect(lambda _name: self._set_available(False))
        self._models = PENDING_MODELS
        bus.connect(SERVICE, MODELS_PATH, MODELS_IFACE, "Changed", self._models_changed)
        self.refresh()

    # --- właściwości dla QML ------------------------------------------------------------------

    @pyqtProperty(str, notify=reposChanged)
    def reposJson(self) -> str:
        return self._repos

    @pyqtProperty(str, notify=defaultsChanged)
    def defaultsJson(self) -> str:
        return self._defaults

    @pyqtProperty(str, notify=suggestionChanged)
    def suggestionJson(self) -> str:
        return self._suggestion

    @pyqtProperty(str, notify=modelsChanged)
    def modelsJson(self) -> str:
        """Status() z /Models (JSON), PENDING_MODELS przed pierwszą odpowiedzią albo "null", gdy usługa
        go nie podaje."""
        return self._models

    @property
    def images(self):
        """Zrzuty zadania (taskimages.Images) — testy ustawiają `images.root`."""
        return self.tray.images

    @property
    def spectacle(self) -> str:
        return self.tray.spectacle

    @pyqtProperty(str, notify=imagesChanged)
    def imagesJson(self) -> str:
        return self.tray.paths_json()

    @pyqtProperty(bool, notify=availableChanged)
    def available(self) -> bool:
        return self._available

    @pyqtProperty(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @pyqtProperty(bool, constant=True)
    def canCapture(self) -> bool:
        return bool(self.spectacle)

    @pyqtProperty(str, constant=True)
    def homeDir(self) -> str:
        return os.path.expanduser("~")

    @pyqtProperty(str, constant=True)
    def shortcutText(self) -> str:
        return self.shortcut

    @pyqtProperty(float, constant=True)
    def previewNow(self) -> float:
        """Zamrożony zegar z --data ("now") — powtarzalne „2 h” w podglądzie; 0 = prawdziwy czas."""
        return float((self.data or {}).get("now") or 0)

    # --- dane z usługi ------------------------------------------------------------------------

    @pyqtSlot()
    def refresh(self) -> None:
        if self.bus is None:
            return
        self._call("Defaults", [], self._got_defaults)
        self._call("Repos", [], self._got_repos, timeout=120_000)
        self._models_changed()

    @pyqtSlot()
    def _models_changed(self) -> None:
        if self.bus is not None:
            self._call("Status", [], self._got_models, path=MODELS_PATH, iface=MODELS_IFACE)

    def _got_models(self, ok: bool, value) -> None:
        text = value if ok and isinstance(value, str) else "null"
        if text != self._models:
            self._models = text
            self.modelsChanged.emit()

    def _got_defaults(self, ok: bool, value) -> None:
        if not ok or not isinstance(value, str):
            self._set_available(False)
            return
        try:
            root = json.loads(value).get("imagesDir")
        except (ValueError, AttributeError):
            root = None
        self.tray.set_root(root)
        self._defaults = value
        self.defaultsChanged.emit()
        self._set_available(True)

    def _got_repos(self, ok: bool, value) -> None:
        if ok and isinstance(value, str) and value != self._repos:
            self._repos = value
            self.reposChanged.emit()

    @pyqtSlot(str, str, str)
    def suggest(self, repo_path: str, tool: str, prompt: str) -> None:
        if self.bus is None:
            return
        self._call("SuggestBranch", [repo_path, tool, prompt], self._got_suggestion)

    def _got_suggestion(self, ok: bool, value) -> None:
        if ok and isinstance(value, str) and value != self._suggestion:
            self._suggestion = value
            self.suggestionChanged.emit()

    # --- start --------------------------------------------------------------------------------

    @pyqtSlot(str)
    def start(self, request: str) -> None:
        if self._busy:
            return
        if self.bus is None:
            self.startFinished.emit(json.dumps({"ok": False, "error": "preview",
                                                "message": "Podgląd bez usługi — nic nie uruchomiono"},
                                               ensure_ascii=False))
            return
        self._set_busy(True)

        def done(ok, value):
            self._set_busy(False)
            self.startFinished.emit(value if ok and isinstance(value, str) else "null")

        self._call("Start", [request], done, timeout=START_TIMEOUT_MS)

    @pyqtSlot(result=str)
    def openModels(self) -> str:
        """Okno Tide na widoku Modele. Zwraca opis problemu albo pusty napis."""
        script = tide_window_script()
        if not os.path.isfile(script):
            return "Okno Tide nie jest zainstalowane (make install-tide-window)"
        if self.bus is None:
            return ""  # podgląd — nic nie uruchamiamy
        ok = QProcess.startDetached("/usr/bin/python3", ["-s", script, "--view", MODELS_VIEW])
        ok = ok[0] if isinstance(ok, tuple) else ok
        return "" if ok else "Nie udało się otworzyć okna Tide"

    @pyqtSlot()
    def finish(self) -> None:
        """Zadanie wystartowało — katalog ze zrzutami zostaje dla agenta."""
        self.tray.keep()

    @pyqtSlot()
    def discard(self) -> None:
        self.tray.discard()

    # --- zrzuty ekranu ------------------------------------------------------------------------

    @pyqtSlot(result=bool)
    @pyqtSlot(bool, result=bool)
    def pasteImage(self, in_text_field: bool = False) -> bool:
        """Obraz ze schowka (albo skopiowane pliki obrazów) → zrzut zadania. False: zwykły tekst.
        in_text_field: Ctrl+V w polu tekstowym — schowek z tekstem i obrazem wkleja wtedy tekst."""
        return self.tray.paste(bool(in_text_field))

    @pyqtSlot("QVariantList")
    def addFiles(self, urls) -> None:
        self.tray.add_files(urls)

    @pyqtSlot(str)
    def removeImage(self, path: str) -> None:
        self.tray.remove(path)

    @pyqtSlot()
    def capture(self) -> None:
        """Zrzut zaznaczonego obszaru: `spectacle -b -n -r -o PLIK` (okno jest wtedy schowane)."""
        self.tray.capture()

    # --- wnętrze ------------------------------------------------------------------------------

    def _set_available(self, value: bool) -> None:
        if value != self._available:
            self._available = value
            self.availableChanged.emit()

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit()

    def _call(self, member: str, args: list, then, timeout: int = 25_000, path: str = PATH, iface: str = IFACE) -> None:
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
    """org.tide.NewTaskWindow /Window: Raise() — prośba drugiego uruchomienia o pokazanie okna."""

    def __init__(self, backend: Backend):
        super().__init__()
        self.backend = backend

    @pyqtSlot()
    def Raise(self) -> None:
        self.backend.raiseRequested.emit()


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="tide-new-task", description="Nowe zadanie dla agenta (Tide)")
    parser.add_argument("--data", help="dane z pliku JSON zamiast z tide-daemon")
    parser.add_argument("--grab", help="zapisz zrzut okna do PNG i zakończ")
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    parser.add_argument("--prompt", default="", help="polecenie w formularzu (podgląd)")
    parser.add_argument("--error", default="", help="komunikat błędu (podgląd)")
    parser.add_argument("--image", action="append", default=[], help="miniatura zrzutu (podgląd)")
    parser.add_argument("--no-worktree", action="store_true", help="wyłączony „Nowy worktree” (podgląd)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("Tide: nowe zadanie")
    app.setOrganizationName("Tide")
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon.fromTheme(APP_ID, QIcon.fromTheme("list-add")))

    if args.data:
        with open(args.data, encoding="utf-8") as fh:
            backend = Backend(None, json.load(fh))
    else:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            print("tide-new-task: brak magistrali sesji D-Bus", file=sys.stderr)
            return 1
        if not bus.registerService(WINDOW_SERVICE):
            # Okno już działa — niech się pokaże, a my kończymy.
            bus.call(QDBusMessage.createMethodCall(WINDOW_SERVICE, WINDOW_PATH, WINDOW_IFACE, "Raise"),
                     QDBus.CallMode.Block, 5000)
            return 0
        backend = Backend(bus)
        service = WindowService(backend)
        bus.registerObject(WINDOW_PATH, service, QDBusConnection.RegisterOption.ExportAllSlots)
    for path in args.image:
        backend.images.add_existing(os.path.abspath(path))

    engine = QQmlApplicationEngine()
    engine.setInitialProperties({"backend": backend, "grabPath": os.path.abspath(args.grab) if args.grab else "",
                                 "initialPrompt": args.prompt, "initialError": args.error,
                                 "noWorktree": bool(args.no_worktree)})
    engine.load(QUrl.fromLocalFile(os.path.join(HERE, "qml", "Main.qml")))
    roots = engine.rootObjects()
    if not roots:
        return 1
    if args.width and args.height:
        roots[0].setProperty("width", args.width)
        roots[0].setProperty("height", args.height)
    code = app.exec()
    backend.discard()
    return code


if __name__ == "__main__":
    sys.exit(main())
