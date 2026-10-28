"""Zrzuty ekranu zadania agenta — wspólne dla okna „Nowe zadanie” (apps/newtask) i okna „Co ma zrobić agent?”
w widoku Tablica (apps/board, okno Tide): katalog zadania, kopie przeciągniętych plików, obraz ze schowka
i „Zrób zrzut” przez Spectacle.

widgets/stage.py kopiuje ten plik obok programu aplikacji (--app) i do katalogu każdego widoku okna Tide;
w repozytorium moduły biorą go z apps/common (funkcja load_common w ich kodzie).

Katalog zadania: <root>/<czas>-<los>/, gdzie root to `imagesDir` z /Tasks Defaults (domyślnie
$XDG_DATA_HOME/tide/tasks). /Tasks Start przyjmuje tylko obrazy z tego katalogu (najwyżej 10). Zamknięcie bez
startu usuwa katalog (discard), po starcie zostaje — agent czyta z niego obrazy (keep; release — do tego
następne zadanie w tym samym oknie dostaje nowy katalog).
"""
import json
import os
import secrets
import shutil
import time

from PyQt6.QtCore import QObject, QProcess, QUrl, pyqtSignal
from PyQt6.QtGui import QGuiApplication

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
IMAGE_LIMIT = 10
IMAGE_BYTES = 20 * 1024 * 1024


def data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def default_root() -> str:
    """Katalog zadań, zanim usługa poda `imagesDir` (ten sam domyślny co w tide-daemon /Tasks)."""
    return os.path.join(data_home(), "tide", "tasks")


class Images:
    """Zrzuty zadania w jednym katalogu: kopie przeciągniętych plików, obrazy ze schowka, Spectacle."""

    def __init__(self, root: str):
        self.root = root
        self.folder = ""
        self.paths: list[str] = []
        self.kept = False

    def ensure_folder(self) -> str:
        if not self.folder:
            os.makedirs(self.root, exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
            self.folder = os.path.join(self.root, name)
            os.makedirs(self.folder, mode=0o700)
        return self.folder

    def next_path(self, suffix: str = ".png") -> str:
        folder = self.ensure_folder()
        n = 1
        while True:
            path = os.path.join(folder, f"zrzut-{n}{suffix}")
            if not os.path.exists(path):
                return path
            n += 1

    def room(self) -> bool:
        return len(self.paths) < IMAGE_LIMIT

    def add_file(self, source: str) -> str:
        """Kopiuje obraz do katalogu zadania. Zwraca opis problemu albo pusty napis."""
        name = os.path.basename(source)
        if not source.lower().endswith(IMAGE_SUFFIXES):
            return f"{name} to nie obraz (PNG, JPG, WEBP, GIF)"
        if not os.path.isfile(source):
            return f"nie ma pliku {name}"
        if os.path.getsize(source) > IMAGE_BYTES:
            return f"{name} jest większy niż 20 MB"
        if not self.room():
            return f"najwyżej {IMAGE_LIMIT} zrzutów"
        target = self.next_path(os.path.splitext(source)[1].lower())
        shutil.copyfile(source, target)
        self.paths.append(target)
        return ""

    def add_existing(self, path: str) -> None:
        if path not in self.paths:
            self.paths.append(path)

    def remove(self, path: str) -> None:
        if path in self.paths:
            self.paths.remove(path)
            if self.folder and os.path.dirname(path) == self.folder:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def discard(self) -> None:
        """Okno zamknięte bez startu: katalog zadania znika (tylko nasz, świeżo założony)."""
        if self.kept or not self.folder:
            return
        if os.path.dirname(self.folder) == os.path.normpath(self.root):
            shutil.rmtree(self.folder, ignore_errors=True)
        self.folder, self.paths = "", []


class ImageTray(QObject):
    """Zrzuty jednego zadania dla QML: wklejanie, przeciąganie, „Zrób zrzut”, usuwanie i sprzątanie.

    Wyniki wracają sygnałami (backend okna przekazuje je dalej swoimi): imagesChanged — lista się zmieniła,
    imageProblem(tekst) — coś pominięto, captureFinished(tekst) — Spectacle skończył (pusty napis: bez błędu,
    także anulowane zaznaczanie)."""

    imagesChanged = pyqtSignal()
    imageProblem = pyqtSignal(str)
    captureFinished = pyqtSignal(str)

    def __init__(self, spectacle: str | None = None, root: str | None = None, parent=None):
        super().__init__(parent)
        self.spectacle = shutil.which("spectacle") if spectacle is None else spectacle
        self.images = Images(root or default_root())
        self._capture = None

    @property
    def can_capture(self) -> bool:
        return bool(self.spectacle)

    def paths_json(self) -> str:
        return json.dumps(self.images.paths, ensure_ascii=False)

    def set_root(self, root: str) -> None:
        """`imagesDir` z /Tasks Defaults — tylko zanim powstał katalog zadania."""
        if root and not self.images.folder:
            self.images.root = root

    def paste(self) -> bool:
        """Obraz ze schowka (albo skopiowane pliki obrazów) → zrzut zadania. False: zwykły tekst."""
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is None:
            return False
        if mime.hasImage():
            image = clipboard.image()
            if image.isNull():
                return False
            if not self.images.room():
                self.imageProblem.emit(f"Najwyżej {IMAGE_LIMIT} zrzutów")
                return True
            path = self.images.next_path(".png")
            if not image.save(path, "PNG"):
                self.imageProblem.emit("Nie udało się zapisać obrazu ze schowka")
                return True
            self.images.add_existing(path)
            self.imagesChanged.emit()
            return True
        if mime.hasUrls():
            files = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            if files and all(f.lower().endswith(IMAGE_SUFFIXES) for f in files):
                self.add_files([QUrl.fromLocalFile(f).toString() for f in files])
                return True
        return False

    def add_files(self, urls) -> None:
        problems = []
        for raw in urls:
            url = QUrl(str(raw))
            path = url.toLocalFile() if url.isLocalFile() else str(raw)
            problem = self.images.add_file(path)
            if problem:
                problems.append(problem)
        self.imagesChanged.emit()
        if problems:
            self.imageProblem.emit("Pominięto: " + "; ".join(problems))

    def remove(self, path: str) -> None:
        self.images.remove(path)
        self.imagesChanged.emit()

    def capture(self) -> None:
        """Zrzut zaznaczonego obszaru: `spectacle -b -n -r -o PLIK` (okno jest wtedy schowane)."""
        if not self.spectacle or self._capture is not None:
            self.captureFinished.emit("" if self.spectacle else "Brak programu Spectacle")
            return
        if not self.images.room():
            self.captureFinished.emit(f"Najwyżej {IMAGE_LIMIT} zrzutów")
            return
        target = self.images.next_path(".png")
        proc = QProcess(self)
        self._capture = proc

        def finished(*_args):
            self._capture = None
            proc.deleteLater()
            if os.path.isfile(target) and os.path.getsize(target) > 0:
                self.images.add_existing(target)
                self.imagesChanged.emit()
            self.captureFinished.emit("")  # bez pliku: zaznaczanie anulowane — to nie błąd

        def failed(error):
            if error == QProcess.ProcessError.FailedToStart:
                self._capture = None
                proc.deleteLater()
                self.captureFinished.emit("Nie udało się uruchomić Spectacle")

        proc.finished.connect(finished)
        proc.errorOccurred.connect(failed)
        proc.start(self.spectacle, ["-b", "-n", "-r", "-o", target])

    def keep(self) -> None:
        """Zadanie wystartowało — katalog ze zrzutami zostaje dla agenta."""
        self.images.kept = True

    def release(self) -> None:
        """Jak keep, a następne zadanie w tym samym oknie (Tablica) zaczyna od pustej listy i nowego katalogu."""
        root = self.images.root
        self.images.kept = True
        self.images = Images(root)
        self.imagesChanged.emit()

    def discard(self) -> None:
        """Zamknięte bez startu: katalog zadania znika, lista jest pusta."""
        had = bool(self.images.paths)
        self.images.discard()
        if had:
            self.imagesChanged.emit()

