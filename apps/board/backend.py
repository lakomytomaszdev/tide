"""Widok „Tablica” okna Tide: karty ze wszystkich projektów — migawka i zapisy przez D-Bus
(tide-daemon /Board, docs/tide-daemon.md#tablica) albo stała migawka z pliku (--data).

Moduł widoku (apps/tide/views.json): okno Tide ładuje go z views/board/backend.py i woła
create(bus, data). Strona QML to qml/BoardView.qml (BoardPage z kolumnami, panelem karty
i stroną „Projekty i tagi”).

Uruchomienie agenta z karty: RenderLaunch (/Board) i Start (/Tasks, odpowiedź po starcie agenta —
do 10 min, jak w oknie „Nowe zadanie”) idą tą samą drogą co zapisy, z tagiem i sygnałem replied.

To, co okno „Co ma zrobić agent?” ma wspólnego z oknem „Nowe zadanie” (PT-43):
- zrzuty ekranu — ImageTray z apps/common/taskimages.py (w pakiecie obok tego pliku): wklejanie Ctrl+V
  (schowek tego procesu, okna Tide), przeciąganie plików, „Zrób zrzut” przez Spectacle, usuwanie; katalog
  zadania w `imagesDir` z /Tasks Defaults, ścieżki idą w `images` prośby Start. keepImages() po udanym
  starcie zostawia katalog agentowi (następne okno zaczyna od nowego), discardImages() — zamknięte bez
  startu — usuwa go; to samo przy zamykaniu okna Tide,
- model — modelsJson: Status() z /Models (rola `agent`, agentProgram), odświeżany po Changed, przed pierwszą
  odpowiedzią {"pending": true}, "null" — usługa go nie podaje; tasksJson: /Tasks Defaults (dostępne programy
  `tools`, zapamiętany `model`, `imagesDir`; od nowa przy otwarciu okna i po starcie — refreshTasks());
  openModels() — okno Tide na widoku Modele.
W podglądzie z pliku: klucze „models” i „tasks” danych (bez nich: null i {}), previewImages(ścieżki) — miniatury
bez kopiowania (tylko podgląd).

Zapisy (CreateCard, UpdateCard, MoveCard, UpsertProject, UpsertTag) idą asynchronicznie; wynik
wraca sygnałem replied(tag, json) z tagiem nadanym przez QML (np. "move:K-12"), żeby błąd pokazać
tam, gdzie powstał. Po udanym zapisie od razu pobieramy nową migawkę (usługa ma ją gotową
od razu po zapisie, a sygnał Changed przychodzi i tak).

Dane z pliku (--data) to wynik `tide-daemon.py --print-board` (opcjonalnie z „now”: chwila do
liczenia czasów i „home”: katalog domowy do skracania ścieżek); zapisy są wtedy wyłączone
(odpowiedź „preview”), a wywołania zostają w `callsJson` — do testów i podglądu.
Plakietka w pasku widoków: „● N” (brzoskwinia), gdy karty czekają na ciebie, inaczej liczba kart
w grze (bez Gotowych i Archiwum); zero — ukryta. Ta sama reguła co /Board Badge() w usłudze.

Plan i specyfikacja w panelu karty (PT-5): cardDocs(id) woła /Board CardDocs, a wynik wraca sygnałem
docsReplied(id, json). „Otwórz plik” i „Pokaż w katalogu” przyjmują wyłącznie ścieżki z odpowiedzi
CardDocs (plik w edytorze przez QDesktopServices, katalog — org.freedesktop.FileManager1 ShowItems
z zaznaczonym plikiem, bez niego katalog nadrzędny), a openLink — wyłącznie adresy http(s).
W podglądzie z pliku odpowiedź CardDocs bierzemy z klucza „cardDocs” ({"K-12": {...}}) danych,
a otwarcia trafiają do `callsJson` zamiast do systemu.

YouTrack (PT-7): youtrackIssues (lista do importu, /Board YouTrackIssues — odpowiedź po pobraniu w usłudze),
importYouTrack (ImportYouTrack) i resolveYouTrack (ResolveYouTrack — konflikt ze zmianą ręczną) idą tą samą
drogą co zapisy (tag i sygnał replied). W podglądzie z pliku lista do importu to klucz „youtrackIssues”
danych, a wywołania trafiają do `callsJson`. „Otwórz w YouTracku” to openLink (tylko http(s)).

Markdown planu i specyfikacji (usługa oddaje go nieoczyszczonego) renderuje render_markdown parserem
samego Qt — tym samym, który rozumie TextEdit — z MarkdownNoHTML (HTML zostaje tekstem), każdy obraz
zamienia na „(obraz: opis)”, link inny niż http(s) — na zwykły tekst, a do tego odstęp nad nagłówkami
i kolor linków z palety. QML pokazuje gotowy HTML (RichText) bez obrazów i bez obcych linków; okno
dodatkowo nie wpuszcza żadnych żądań sieciowych (apps/tide/tide.py: guard_network).
"""
import importlib.util
import json
import os
import re
from collections import OrderedDict

from PyQt6.QtCore import QCoreApplication, QObject, QProcess, QTimer, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply,
                          QDBusServiceWatcher)
from PyQt6.QtGui import QColor, QDesktopServices, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICE, PATH, IFACE = "org.tide.Daemon", "/Board", "org.tide.Daemon.Board"
PREVIEW_REPLY = {"ok": False, "error": "preview", "message": "Podgląd z pliku — zmiany są wyłączone."}
NO_SERVICE_REPLY = {"ok": False, "error": "no-service",
                    "message": "Usługa tide-daemon nie odpowiada — nic nie zapisano. Spróbuj za chwilę."}
WRITES = ("CreateCard", "UpdateCard", "MoveCard", "UpsertProject", "UpsertTag", "ImportYouTrack", "ResolveYouTrack",
          "StartCard", "ListCardBranches", "SuggestCardBase", "ResolveCardReview", "EnterStep", "AbandonStep", "ResolveCardStep", "ApproveCardPlan", "SetCardPaused",
          "EditCardPlan", "MergeCardToMain", "MergeCard", "SetCardFlowSettings", "ReportStepFault", "RequestCardBaseChange")
YOUTRACK_TIMEOUT_MS = 25_000   # YouTrackIssues: dwa zapytania do YouTracka w sieci lokalnej (po 4 s) i zapas
TASKS_PATH, TASKS_IFACE = "/Tasks", "org.tide.Daemon.Tasks"
COORDINATION_PATH, COORDINATION_IFACE = "/Coordination", "org.tide.Daemon.Coordination"
AGENTS_PATH, AGENTS_IFACE = "/Agents", "org.tide.Daemon.Agents"
MODELS_PATH, MODELS_IFACE = "/Models", "org.tide.Daemon.Models"
LIMITS_PATH, LIMITS_IFACE = "/Limits", "org.tide.Daemon.Limits"
MODELS_VIEW = "models"
PENDING_MODELS = '{"pending": true}'   # przed pierwszą odpowiedzią Status() — jak w oknie „Nowe zadanie”
START_TIMEOUT_MS = 10 * 60 * 1000  # jak w oknie „Nowe zadanie”: worktree dużego repozytorium zakłada się długo
DOCS_TIMEOUT_MS = 20_000    # CardDocs odpowiada po odczycie plików w wątku roboczym usługi
FM_SERVICE, FM_PATH, FM_IFACE = "org.freedesktop.FileManager1", "/org/freedesktop/FileManager1", \
    "org.freedesktop.FileManager1"
LINK_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
HEADING_MARGINS = {1: (18, 6), 2: (16, 4), 3: (12, 3)}  # poziom nagłówka → (odstęp nad, pod) w px; niższe jak 3
MARKDOWN = QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML
RENDER_CACHE = 24   # tyle ostatnich dokumentów trzymamy wyrenderowanych (odświeżanie co 5 s nie renderuje od nowa)
_BODY_STYLE = re.compile(r'<body style="[^"]*">')
_IMG_TAG = re.compile(r"<img[^>]*>")


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
windowconfig = load_common("windowconfig")


def tide_window_script() -> str:
    """tide.py okna, w którym działa widok (pakiet: views/board/ → ../../tide.py), inaczej zainstalowany."""
    packaged = os.path.join(os.path.dirname(os.path.dirname(HERE)), "tide.py")
    if os.path.isfile(packaged):
        return packaged
    return os.environ.get("TIDE_WINDOW") or os.path.join(taskimages.data_home(), "tide", "tide-window", "tide.py")


def render_markdown(markdown: str, link: QColor | None = None) -> str:
    """HTML do TextEdit (RichText) z Markdownu, bez obrazów, bez HTML ze źródła i bez linków innych niż http(s).

    Parser to md4c z Qt — ten sam, którego używa TextEdit.MarkdownText — więc nie ma rozjazdu „co jest
    obrazem” między oczyszczaniem a renderowaniem. Obraz (także zdefiniowany odnośnikiem) → kursywą
    „(obraz: opis)”; HTML ze źródła z MarkdownNoHTML jest zwykłym tekstem; link file:, javascript:,
    względny → zwykły tekst. Krój i wielkość czcionki zostają z TextEdit (bez stylu <body>)."""
    doc = QTextDocument()
    doc.setMarkdown(markdown or "", MARKDOWN)
    images, anchors = [], []
    block = doc.begin()
    while block.isValid():
        fmt = block.blockFormat()
        level = fmt.headingLevel()
        if level:
            top, bottom = HEADING_MARGINS.get(level, HEADING_MARGINS[3])
            fmt.setTopMargin(0 if block.blockNumber() == 0 else top)
            fmt.setBottomMargin(bottom)
            QTextCursor(block).setBlockFormat(fmt)
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            char = fragment.charFormat()
            if char.isImageFormat():
                alt = str(char.property(QTextFormat.Property.ImageAltText) or "").strip()
                name = char.toImageFormat().name()
                last = images[-1] if images else None
                if last and last[0] + last[1] == fragment.position() and last[3] == name:
                    # md4c dzieli opis z kilku linii na kilka fragmentów tego samego obrazu — jeden obraz
                    images[-1] = (last[0], last[1] + fragment.length(), " ".join(p for p in (last[2], alt) if p), name)
                else:
                    images.append((fragment.position(), fragment.length(), alt, name))
            elif char.isAnchor():
                anchors.append((fragment.position(), fragment.length(), char))
            it += 1
        block = block.next()
    for position, length, char in anchors:
        span = QTextCursor(doc)
        span.setPosition(position)
        span.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        if LINK_RE.match(char.anchorHref() or ""):
            if link is not None:
                char.setForeground(link)
        else:
            char.setAnchor(False)
            char.setAnchorHref("")
            char.clearForeground()
            char.setFontUnderline(False)
        span.setCharFormat(char)
    for position, length, alt, _name in reversed(images):   # od końca — wcześniejsze pozycje się nie przesuwają
        span = QTextCursor(doc)
        span.setPosition(position)
        span.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        plain = QTextCharFormat()
        plain.setFontItalic(True)
        span.insertText(f"(obraz: {alt or 'bez opisu'})", plain)
    html = _BODY_STYLE.sub("<body>", doc.toHtml(), count=1)
    return _IMG_TAG.sub("", html)   # zabezpieczenie: po zamianie nie powinno już być żadnego <img>


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
    docsReplied = pyqtSignal(str, str)  # ID karty, odpowiedź CardDocs (JSON)
    imagesChanged = pyqtSignal()
    imageProblem = pyqtSignal(str)
    captureFinished = pyqtSignal(str)
    modelsChanged = pyqtSignal()
    tasksChanged = pyqtSignal()
    limitsChanged = pyqtSignal()
    flowSettingsChanged = pyqtSignal()
    cardOpenChanged = pyqtSignal()

    def __init__(self, bus: QDBusConnection | None, data: dict | None = None, parent=None,
                 spectacle: str | None = None):
        super().__init__(parent)
        self.tray = taskimages.ImageTray(spectacle, parent=self)
        self.tray.imagesChanged.connect(self.imagesChanged)
        self.tray.imageProblem.connect(self.imageProblem)
        self.tray.captureFinished.connect(self.captureFinished)
        self._models = "null"
        self._tasks = "{}"
        self._limits = "{}"
        self._flow_settings = "{}"
        # Ustawienie okna (PT-133): na żywo z window.json; podgląd trzyma je tylko w pamięci i nie rusza plików.
        self._card_open = windowconfig.CARD_OPEN_DEFAULT if data is not None else windowconfig.card_open()
        # Sesje zapisują to samo ustawienie w tym samym oknie — nadążamy za plikiem (PT-131).
        self._card_open_watcher = windowconfig.watch(self._card_open_from_file, self) if data is None else None
        app =QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.discardImages)   # okno Tide zamknięte z otwartym oknem uruchomienia
        self.bus = bus
        self._snapshot = "{}"
        self._available = False
        self._badge = ("", "muted")
        self._calls: list = []
        self._pending: set = set()  # QDBusPendingCallWatcher muszą żyć do odpowiedzi
        self._preview = data is not None
        self._now = 0.0
        self._home = ""
        self._docs_data: dict = {}      # podgląd: ID karty → odpowiedź CardDocs
        self._issues_data = None        # podgląd: odpowiedź YouTrackIssues
        self._doc_paths: set = set()    # ścieżki dokumentów z odpowiedzi CardDocs
        self._current_doc_card = ""
        self._docs_requests = 0
        self._rendered: OrderedDict = OrderedDict()   # (markdown, kolor linku) → HTML
        if data is not None:
            self._now = float(data.get("now") or 0)
            self._home = str(data.get("home") or "")
            data = dict(data)
            docs = data.pop("cardDocs", None)
            self._docs_data = docs if isinstance(docs, dict) else {}
            self._issues_data = data.pop("youtrackIssues", None)
            self._models = json.dumps(data.pop("models", None), ensure_ascii=False)
            self._tasks = json.dumps(data.pop("tasks", None) or {}, ensure_ascii=False)
            self._limits = json.dumps(data.pop("limits", None) or {}, ensure_ascii=False)
            self._flow_settings = json.dumps(data.pop("flowSettings", None) or {}, ensure_ascii=False)
            preview_open = data.pop("cardOpen", None)
            if preview_open in windowconfig.CARD_OPEN_VALUES:
                self._card_open = preview_open
            self._snapshot = json.dumps(data, ensure_ascii=False)
            self._available = True
            self._update_badge()
            return
        if bus is None:
            return
        bus.connect(SERVICE, PATH, IFACE, "Changed", self._changed)
        self._watcher = QDBusServiceWatcher(SERVICE, bus, QDBusServiceWatcher.WatchModeFlag.WatchForRegistration
                                            | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration, self)
        self._watcher.serviceRegistered.connect(lambda _name: (self.reload(), self._refresh_tasks(),
                                                                 self._refresh_limits(), self._refresh_flow_settings()))
        self._watcher.serviceUnregistered.connect(lambda _name: self._set_available(False))
        self._models = PENDING_MODELS
        bus.connect(SERVICE, MODELS_PATH, MODELS_IFACE, "Changed", self._models_changed)
        bus.connect(SERVICE, LIMITS_PATH, LIMITS_IFACE, "Changed", self._refresh_limits)
        self.reload()
        self._refresh_tasks()
        self._refresh_limits()
        self._refresh_flow_settings()

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

    # Ile razy panel poprosił o CardDocs (testy odświeżania).
    @pyqtProperty(int, notify=callsChanged)
    def docsRequests(self) -> int:
        return self._docs_requests

    # Okno „Co ma zrobić agent?” (PT-43): zrzuty, model i to, co /Tasks podaje oknu „Nowe zadanie”.
    @pyqtProperty(str, notify=imagesChanged)
    def imagesJson(self) -> str:
        return self.tray.paths_json()

    @pyqtProperty(bool, constant=True)
    def canCapture(self) -> bool:
        return self.tray.can_capture

    @pyqtProperty(str, notify=modelsChanged)
    def modelsJson(self) -> str:
        return self._models

    @pyqtProperty(str, notify=tasksChanged)
    def tasksJson(self) -> str:
        return self._tasks

    @pyqtProperty(str, notify=limitsChanged)
    def limitsJson(self) -> str:
        return self._limits

    @pyqtProperty(str, notify=flowSettingsChanged)
    def flowSettingsJson(self) -> str:
        return self._flow_settings

    # Co otwiera kafelek z „Czeka na mnie” i powiadomienie: "decision" (okno decyzji) albo "full" (pełna karta).
    @pyqtProperty(str, notify=cardOpenChanged)
    def cardOpen(self) -> str:
        return self._card_open

    def _card_open_from_file(self, value: str) -> None:
        if value != self._card_open:
            self._card_open = value
            self.cardOpenChanged.emit()

    @pyqtSlot(str, result=str)
    def setCardOpen(self, value: str) -> str:
        """Zapisuje wybór w konfiguracji okna. Zwraca pusty tekst albo powód, gdy zapis się nie udał
        (wtedy ustawienie działa do końca sesji okna, ale nie przeżyje restartu)."""
        if value not in windowconfig.CARD_OPEN_VALUES:
            return "Nieznane ustawienie otwierania karty."
        problem = ""
        if not self._preview:
            try:
                windowconfig.set_card_open(value)
            except OSError as exc:
                problem = f"Nie zapisano ustawienia: {exc.strerror or exc}."
        if value != self._card_open:
            self._card_open = value
            self.cardOpenChanged.emit()
        return problem

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

    @pyqtSlot(str, str, str)
    def renderLaunch(self, tag: str, card_id: str, request: str) -> None:
        self._write(tag, "RenderLaunch", [card_id, request])

    @pyqtSlot(str, str)
    def startTask(self, tag: str, request: str) -> None:
        self._write(tag, "Start", [request])

    @pyqtSlot(str, str, str)
    def listCardBranches(self, tag: str, card_id: str, request: str) -> None:
        self._write(tag, "ListCardBranches", [card_id, request])

    @pyqtSlot(str, str, str)
    def suggestCardBase(self, tag: str, card_id: str, request: str) -> None:
        self._write(tag, "SuggestCardBase", [card_id, request])

    @pyqtSlot(str, str, str)
    def startCard(self, tag: str, card_id: str, request: str) -> None:
        """Zlecenie w przepływie karty (/Board StartCard, wejście „Od razu”): krok w tle, bez Konsoli."""
        self._write(tag, "StartCard", [card_id, request])

    @pyqtSlot(str, str, str)
    def enterStep(self, tag: str, card_id: str, step_id: str) -> None:
        self._write(tag, "EnterStep", [card_id, step_id])

    @pyqtSlot(str, str, str)
    def abandonStep(self, tag: str, card_id: str, step_id: str) -> None:
        self._write(tag, "AbandonStep", [card_id, step_id])

    @pyqtSlot(str, str, str)
    def resolveCardReview(self, tag: str, card_id: str, choice: str) -> None:
        self._write(tag, "ResolveCardReview", [card_id, choice])

    @pyqtSlot(str, str, str)
    def resolveCardStep(self, tag: str, card_id: str, request: str) -> None:
        self._write(tag, "ResolveCardStep", [card_id, request])

    @pyqtSlot(str, str, str)
    def previewStepFault(self, tag: str, card_id: str, request: str) -> None:
        """Podgląd karty z błędem kroku przed zapisem (/Board PreviewStepFault, PT-125) — usługa niczego nie zapisuje."""
        self._write(tag, "PreviewStepFault", [card_id, request])

    @pyqtSlot(str, str, str)
    def reportStepFault(self, tag: str, card_id: str, request: str) -> None:
        """„Opisz problem i ponów”: karta z błędem w Backlogu plasma-tide i ponowienie kroku (/Board ReportStepFault)."""
        self._write(tag, "ReportStepFault", [card_id, request])

    @pyqtSlot(str, str, bool)
    def setCardPaused(self, tag: str, card_id: str, paused: bool) -> None:
        self._write(tag, "SetCardPaused", [card_id, paused])

    @pyqtSlot(str, str)
    def setCardFlowSettings(self, tag: str, request: str) -> None:
        self._write(tag, "SetCardFlowSettings", [request])

    @pyqtSlot()
    def refreshCardFlowSettings(self) -> None:
        self._refresh_flow_settings()

    @pyqtSlot(str, str, str)
    def editCardPlan(self, tag: str, card_id: str, request: str) -> None:
        self._write(tag, "EditCardPlan", [card_id, request])

    @pyqtSlot(str, str)
    def approveCardPlan(self, tag: str, card_id: str) -> None:
        self._write(tag, "ApproveCardPlan", [card_id])

    @pyqtSlot(str, str)
    def cardBranchDiff(self, tag: str, card_id: str) -> None:
        self._write(tag, "CardBranchDiff", [card_id])

    @pyqtSlot(str, str)
    def mergeCardToMain(self, tag: str, card_id: str) -> None:
        self._write(tag, "MergeCardToMain", [card_id])

    @pyqtSlot(str, str, str)
    def previewCardMerge(self, tag: str, card_id: str, request: str) -> None:
        self._write(tag, "PreviewCardMerge", [card_id, request])

    @pyqtSlot(str, str, str)
    def mergeCard(self, tag: str, card_id: str, request: str) -> None:
        self._write(tag, "MergeCard", [card_id, request])

    @pyqtSlot(str, str, str)
    def previewCardBaseChange(self, tag: str, card_id: str, request: str) -> None:
        self._write(tag, "PreviewCardBaseChange", [card_id, request])

    @pyqtSlot(str, str, str)
    def requestCardBaseChange(self, tag: str, card_id: str, request: str) -> None:
        self._write(tag, "RequestCardBaseChange", [card_id, request])

    # --- zrzuty ekranu i model w oknie uruchomienia (PT-43) ------------------------------------

    @pyqtSlot(result=bool)
    @pyqtSlot(bool, result=bool)
    def pasteImage(self, in_text_field: bool = False) -> bool:
        """Ctrl+V w oknie uruchomienia: obraz ze schowka (albo pliki obrazów) → zrzut. False: zwykły tekst.
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
        """„Zrób zrzut” (spectacle -b -n -r -o PLIK); okno Tide chowa QML na czas zaznaczania."""
        self.tray.capture()

    @pyqtSlot()
    def refreshTasks(self) -> None:
        """/Tasks Defaults i stan modeli od nowa — przy otwarciu okna uruchomienia i po udanym starcie
        (zapamiętany model, programy w PATH)."""
        self._refresh_tasks()

    @pyqtSlot()
    def keepImages(self) -> None:
        """Agent ruszył: katalog zrzutów zostaje dla niego, następne okno zaczyna od pustej listy."""
        self.tray.release()

    @pyqtSlot()
    def discardImages(self) -> None:
        """Okno zamknięte bez startu: katalog zrzutów znika."""
        self.tray.discard()

    @pyqtSlot("QVariantList")
    def previewImages(self, paths) -> None:
        """Podgląd z pliku: miniatury z podanych plików, bez kopiowania (poza podglądem nic nie robi)."""
        if not self._preview:
            return
        for path in paths:
            self.tray.images.add_existing(os.path.abspath(str(path)))
        self.imagesChanged.emit()

    @pyqtSlot(result=str)
    def openModels(self) -> str:
        """Okno Tide na widoku Modele (drugie uruchomienie tide.py prosi to okno o Show). Zwraca problem albo ""."""
        if self._preview or self.bus is None:
            self._record("openModels")
            return ""
        script = tide_window_script()
        if not os.path.isfile(script):
            return "Okno Tide nie jest zainstalowane (make install-tide-window)"
        ok = QProcess.startDetached("/usr/bin/python3", ["-s", script, "--view", MODELS_VIEW])
        ok = ok[0] if isinstance(ok, tuple) else ok
        return "" if ok else "Nie udało się otworzyć widoku Modele"

    # YouTrack (PT-7): lista zgłoszeń do importu, import i rozstrzygnięcie konfliktu — odpowiedź sygnałem replied.
    @pyqtSlot(str, bool)
    def youtrackIssues(self, tag: str, refresh: bool) -> None:
        if self._preview and self._issues_data is not None:
            self._record("YouTrackIssues", bool(refresh))
            text = json.dumps(self._issues_data, ensure_ascii=False)
            QTimer.singleShot(0, lambda: self.replied.emit(tag, text))
            return
        self._write(tag, "YouTrackIssues", [bool(refresh)])

    @pyqtSlot(str, str)
    def importYouTrack(self, tag: str, request: str) -> None:
        self._write(tag, "ImportYouTrack", [request])

    @pyqtSlot(str, str, str)
    def resolveYouTrack(self, tag: str, ident: str, choice: str) -> None:
        self._write(tag, "ResolveYouTrack", [ident, choice])

    # „Otwórz w Sesjach →” / „Przejdź do agenta” na karcie: koordynator karty — okno Tide na Sesjach
    # (/Coordination ShowWindow), zwykła sesja — jej okno (/Agents ActivateSession). Bez odpowiedzi do QML.
    @pyqtSlot(str)
    def showCoordinator(self, coordinator_id: str) -> None:
        self._open("ShowWindow", coordinator_id, COORDINATION_PATH, COORDINATION_IFACE)

    @pyqtSlot(str)
    def activateSession(self, session_id: str) -> None:
        self._open("ActivateSession", session_id, AGENTS_PATH, AGENTS_IFACE)

    # Plan i specyfikacja karty: odpowiedź sygnałem docsReplied(id, json).
    @pyqtSlot(str)
    def cardDocs(self, card_id: str) -> None:
        self._current_doc_card = card_id
        self._docs_requests += 1
        self.callsChanged.emit()
        if self._preview or self.bus is None:
            reply = self._docs_data.get(card_id) if self._preview else None
            if reply is None:
                reply = {"ok": True, "card": card_id, "plan": None, "spec": None, "others": [], "warnings": []} \
                    if self._preview else NO_SERVICE_REPLY
            text = json.dumps(reply, ensure_ascii=False)
            QTimer.singleShot(0, lambda: self._got_docs(card_id, text))
            return

        def done(ok: bool, value) -> None:
            text = value if ok and isinstance(value, str) else json.dumps(NO_SERVICE_REPLY, ensure_ascii=False)
            self._got_docs(card_id, text)

        self._call("CardDocs", [card_id], done, timeout=DOCS_TIMEOUT_MS)

    @pyqtSlot(str, result=bool)
    def openFile(self, path: str) -> bool:
        """„Otwórz plik”: dokument z CardDocs albo kartka bieżącej karty z migawki."""
        if path not in self._doc_paths and not self._plan_card_path(path):
            return False
        if self._preview or self.bus is None:
            self._record("openFile", path)
            return True
        return QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _plan_card_path(self, path: str) -> bool:
        """Kartka musi pochodzić z aktualnej migawki otwartej karty."""
        try:
            snapshot = json.loads(self._snapshot)
            for card in snapshot.get("cards") or []:
                if card.get("id") != self._current_doc_card:
                    continue
                rows = (((card.get("derived") or {}).get("flow") or {}).get("plan") or {}).get("rows") or []
                return any(row.get("cardPath") == path and os.path.isabs(path) for row in rows)
        except (ValueError, TypeError, AttributeError):
            pass
        return False

    @pyqtSlot(str, result=bool)
    def showInFolder(self, path: str) -> bool:
        """„Pokaż w katalogu”: menedżer plików z zaznaczonym plikiem (FileManager1), bez niego — katalog."""
        if path not in self._doc_paths:
            return False
        if self._preview or self.bus is None:
            self._record("showInFolder", path)
            return True
        message = QDBusMessage.createMethodCall(FM_SERVICE, FM_PATH, FM_IFACE, "ShowItems")
        message.setArguments([[QUrl.fromLocalFile(path).toString()], ""])
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(message, 5_000), self)
        self._pending.add(watcher)

        def finished(w):
            self._pending.discard(w)
            if QDBusPendingReply(w).isError():
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
            w.deleteLater()

        watcher.finished.connect(finished)
        return True

    @pyqtSlot(str, result=bool)
    def openLink(self, url: str) -> bool:
        """Link z planu albo specyfikacji — wyłącznie http(s), w przeglądarce."""
        if not LINK_RE.match(url or ""):
            return False
        if self._preview or self.bus is None:
            self._record("openLink", url)
            return True
        return QDesktopServices.openUrl(QUrl(url))

    @pyqtSlot(str, QColor, result=str)
    def renderMarkdown(self, markdown: str, link: QColor) -> str:
        """render_markdown dla QML, z pamięcią ostatnich dokumentów (ta sama treść — ten sam HTML)."""
        key = (markdown, link.name())
        html = self._rendered.get(key)
        if html is None:
            html = render_markdown(markdown, link)
            self._rendered[key] = html
            while len(self._rendered) > RENDER_CACHE:
                self._rendered.popitem(last=False)
        else:
            self._rendered.move_to_end(key)
        return html

    # --- wnętrze ------------------------------------------------------------------------------

    def _got_docs(self, card_id: str, text: str) -> None:
        try:
            reply = json.loads(text)
        except ValueError:
            reply = {}
        if isinstance(reply, dict):
            for doc in [reply.get("plan"), reply.get("spec"), *(reply.get("others") or [])]:
                if isinstance(doc, dict) and isinstance(doc.get("path"), str) and doc["path"]:
                    self._doc_paths.add(doc["path"])
        self.docsReplied.emit(card_id, text)

    def _record(self, member: str, *args) -> None:
        self._calls.append([member, *args])
        self.callsChanged.emit()

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
            if member in WRITES:
                self.reload()  # usługa ma nową migawkę od razu po zapisie
            if member == "SetCardFlowSettings":
                self._refresh_flow_settings()

        if member == "Start":
            self._call(member, args, done, timeout=START_TIMEOUT_MS, path=TASKS_PATH, iface=TASKS_IFACE)
        elif member in ("StartCard", "EnterStep"):   # zatrzymanie kroku i karta Konsoli mogą potrwać
            self._call(member, args, done, timeout=START_TIMEOUT_MS)
        elif member == "YouTrackIssues":
            self._call(member, args, done, timeout=YOUTRACK_TIMEOUT_MS)
        elif member in ("PreviewCardMerge", "MergeCard", "PreviewCardBaseChange", "RequestCardBaseChange", "ResolveCardStep"):
            self._call(member, args, done, timeout=START_TIMEOUT_MS)
        else:
            self._call(member, args, done)

    def _open(self, member: str, ident: str, path: str, iface: str) -> None:
        if self._preview or self.bus is None:
            self._calls.append([path + " " + member, ident])
            self.callsChanged.emit()
            return
        self._call(member, [ident], None, path=path, iface=iface)

    @pyqtSlot()
    def _changed(self) -> None:
        self.reload()

    def _refresh_tasks(self) -> None:
        """/Tasks Defaults (programy, zapamiętany model, katalog zrzutów) i stan modeli — przy starcie i po
        powrocie usługi; modele też po /Models Changed."""
        if self.bus is None:
            return
        self._call("Defaults", [], self._got_tasks, path=TASKS_PATH, iface=TASKS_IFACE)
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

    @pyqtSlot()
    def _refresh_limits(self) -> None:
        if self.bus is not None:
            self._call("Forecast", [], self._got_limits, path=LIMITS_PATH, iface=LIMITS_IFACE)

    def _got_limits(self, ok: bool, value) -> None:
        text = value if ok and isinstance(value, str) else "{}"
        if text != self._limits:
            self._limits = text
            self.limitsChanged.emit()

    def _refresh_flow_settings(self) -> None:
        if self.bus is not None:
            self._call("GetCardFlowSettings", [], self._got_flow_settings)

    def _got_flow_settings(self, ok: bool, value) -> None:
        text = value if ok and isinstance(value, str) else "{}"
        if text != self._flow_settings:
            self._flow_settings = text
            self.flowSettingsChanged.emit()

    def _got_tasks(self, ok: bool, value) -> None:
        if not ok or not isinstance(value, str):
            return
        try:
            root = json.loads(value).get("imagesDir")
        except (ValueError, AttributeError):
            root = None
        self.tray.set_root(root)
        if value != self._tasks:
            self._tasks = value
            self.tasksChanged.emit()

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

    def _call(self, member: str, args: list, then, timeout: int = 15_000, path: str = PATH, iface: str = IFACE) -> None:
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
    """Backend widoku dla okna Tide: z magistralą (na żywo) albo ze stałą migawką (data: dict)."""
    return Backend(bus if data is None else None, data if isinstance(data, dict) else None)
