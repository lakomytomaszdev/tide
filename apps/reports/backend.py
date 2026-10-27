"""Widok „Raporty” okna Tide: raporty poranne z tide-daemon przez D-Bus (/Reports) albo ze stałego
pliku (--data). Widok nigdy nie czyta ani nie renderuje HTML-a raportu — dostaje report.json doby
przez /Reports Day(doba) (reports/data.py w usłudze) i pokazuje go jako zwykły tekst.

Moduł widoku (apps/tide/views.json): okno Tide ładuje go z views/reports/backend.py i woła
create(bus, data). Strona QML to qml/ReportsView.qml.

- na start tylko Status() — plakietka „nowy” (brzoskwinia), gdy najnowszego raportu jeszcze nie
  otwarto; lista dni (Days()) i raport doby (Day()) dopiero przy pierwszym pokazaniu widoku (load()),
- „Wygeneruj teraz” → Generate() (odpowiedź po złożeniu raportu, do kilku minut — generating),
- „Otwórz w przeglądarce” i „Pełna strona” → OpenDay(doba, projekt) — strona najnowszego raportu
  gasi kropkę w widżetach jak Open(),
- obejrzenie najnowszego raportu w oknie → MarkRead(doba) (widok woła markRead po chwili na wierzchu),
- tagi projektów z /Board Snapshot(), jeśli tablica działa (bez niej — bez tagów, bez błędów),
- „Kopiuj jako tekst” — schowek przez Qt (tekst z report.json, ten sam co przycisk na stronie).

Dane z pliku (--data): {"days": <jak Days()>, "reports": {doba: <jak Day()>}, "board": <jak Snapshot()>};
akcje (Generate, OpenDay) są wtedy wyłączone, przeczytanie zmienia tylko stan w pamięci.
"""
import json
import os

from PyQt6.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply,
                          QDBusServiceWatcher)
from PyQt6.QtGui import QGuiApplication

SERVICE = "org.tide.Daemon"
PATH, IFACE = "/Reports", "org.tide.Daemon.Reports"
BOARD_PATH, BOARD_IFACE = "/Board", "org.tide.Daemon.Board"
GENERATE_TIMEOUT_MS = 15 * 60 * 1000   # git, dzienniki i opis modelem (do 180 s) — z zapasem
EMPTY_DAYS = {"days": [], "latest": "", "unread": False, "today": "", "keepDays": 30, "root": ""}


def parse(text, fallback):
    try:
        value = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def board_tags(snapshot) -> dict:
    """Tagi projektów tablicy do kart raportu: {"path:<ścieżka repozytorium>": [...], "name:<nazwa>": [...]}
    z kolorem — nazwą pastelu palety. Zły albo pusty snapshot — pusty słownik."""
    if not isinstance(snapshot, dict):
        return {}
    colors = {}
    for tag in snapshot.get("tags") or []:
        if isinstance(tag, dict) and isinstance(tag.get("name"), str):
            colors[tag["name"].casefold()] = tag.get("color") if isinstance(tag.get("color"), str) else ""
    out = {}
    for project in snapshot.get("projects") or []:
        if not isinstance(project, dict):
            continue
        tags = [{"name": name, "color": colors.get(name.casefold(), "")}
                for name in project.get("tags") or [] if isinstance(name, str) and name]
        if not tags:
            continue
        repo = project.get("repo")
        if isinstance(repo, str) and repo:
            out["path:" + os.path.realpath(os.path.expanduser(repo))] = tags
        for key in ("name", "slug"):
            if isinstance(project.get(key), str) and project[key]:
                out.setdefault("name:" + project[key].casefold(), tags)
    return out


def with_tags(day: dict, tags: dict) -> dict:
    """Projekty doby z tagami tablicy: po ścieżce repozytorium, potem po nazwie."""
    if not tags or not isinstance(day.get("projects"), list):
        return day
    projects = []
    for project in day["projects"]:
        if not isinstance(project, dict):
            continue
        path = project.get("path") if isinstance(project.get("path"), str) else ""
        name = project.get("name") if isinstance(project.get("name"), str) else ""
        found = (tags.get("path:" + os.path.realpath(path)) if path else None) or tags.get("name:" + name.casefold())
        projects.append(dict(project, tags=found) if found else project)
    return dict(day, projects=projects)


class Backend(QObject):
    """Most między QML a tide-daemon: lista dni, raport wybranej doby (JSON) i akcje."""

    daysChanged = pyqtSignal()
    dayChanged = pyqtSignal()
    availableChanged = pyqtSignal()
    generatingChanged = pyqtSignal()
    badgeChanged = pyqtSignal()
    generateFinished = pyqtSignal(str)     # ścieżka index.html; pusta — błąd
    openFinished = pyqtSignal(bool)

    def __init__(self, bus: QDBusConnection | None, data: dict | None = None, parent=None):
        super().__init__(parent)
        self.bus = bus
        self._data = data
        self._days = dict(EMPTY_DAYS)
        self._day: dict = {}
        self._selected = ""
        self._available = False
        self._generating = False
        self._loaded = False
        self._unread = False
        self._tags: dict = {}
        self._pending: set = set()
        if data is not None:
            self._days = dict(EMPTY_DAYS)
            if isinstance(data.get("days"), dict):
                self._days.update(data["days"])
            self._reports = data.get("reports") if isinstance(data.get("reports"), dict) else {}
            self._tags = board_tags(data.get("board"))
            self._unread = bool(self._days.get("unread"))
            self._available = True
            return
        if bus is None:
            return
        bus.connect(SERVICE, PATH, IFACE, "Changed", self._changed)
        bus.connect(SERVICE, BOARD_PATH, BOARD_IFACE, "Changed", self._board_changed)
        self._watcher = QDBusServiceWatcher(SERVICE, bus, QDBusServiceWatcher.WatchModeFlag.WatchForRegistration
                                            | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration, self)
        self._watcher.serviceRegistered.connect(lambda _name: self._changed())
        self._watcher.serviceUnregistered.connect(lambda _name: self._lost())
        self._status()

    # --- właściwości dla QML ------------------------------------------------------------------

    @pyqtProperty(str, notify=daysChanged)
    def daysJson(self) -> str:
        return json.dumps(self._days, ensure_ascii=False)

    @pyqtProperty(str, notify=dayChanged)
    def dayJson(self) -> str:
        return json.dumps(with_tags(self._day, self._tags), ensure_ascii=False)

    @pyqtProperty(str, notify=dayChanged)
    def selectedDay(self) -> str:
        return self._selected

    @pyqtProperty(bool, notify=availableChanged)
    def available(self) -> bool:
        return self._available

    @pyqtProperty(bool, notify=generatingChanged)
    def generating(self) -> bool:
        return self._generating

    @pyqtProperty(bool, constant=True)
    def preview(self) -> bool:
        return self._data is not None

    @pyqtProperty(str, constant=True)
    def homeDir(self) -> str:
        return os.path.expanduser("~")

    # Plakietka w pasku widoków: „nowy” (brzoskwinia), gdy najnowszego raportu jeszcze nie otwarto.
    @pyqtProperty(str, notify=badgeChanged)
    def badgeText(self) -> str:
        return "nowy" if self._available and self._unread else ""

    @pyqtProperty(str, constant=True)
    def badgeTone(self) -> str:
        return "waiting"

    # --- akcje --------------------------------------------------------------------------------

    @pyqtSlot()
    def load(self) -> None:
        """Lista dni i raport wybranej doby (najnowszej, jeśli nic nie wybrano) — przy pierwszym pokazaniu."""
        self._loaded = True
        if self._data is not None:
            self._set_days(self._days)
            return
        self.reload()

    @pyqtSlot()
    def reload(self) -> None:
        if self.bus is None:
            return
        self._status()
        if self._loaded:
            self._call("Days", [], self._got_days)
            self._call_board()

    @pyqtSlot(str)
    def selectDay(self, day: str) -> None:
        if not day:
            return
        if day != self._selected:
            self._selected = day
            self._day = {}
            self.dayChanged.emit()
        self._fetch_day()

    @pyqtSlot()
    def generate(self) -> None:
        if self._generating:
            return
        if self.bus is None:
            self.generateFinished.emit("")
            return
        self._set_generating(True)
        self._call("Generate", [], self._generated, timeout=GENERATE_TIMEOUT_MS)

    @pyqtSlot(str, str)
    def openDay(self, day: str, project: str) -> None:
        if self.bus is None:
            self.openFinished.emit(False)
            return
        self._call("OpenDay", [day or "", project or ""], lambda ok, value: self.openFinished.emit(ok and value is True))

    @pyqtSlot(str)
    def markRead(self, day: str) -> None:
        """Najnowszy raport obejrzany w oknie — przeczytany w usłudze (kropka gaśnie też w widżetach)."""
        if not day:
            return
        if self._data is not None:
            if day == self._days.get("latest") and self._unread:
                self._unread = False
                self._days = dict(self._days, unread=False)
                self.daysChanged.emit()
                self.badgeChanged.emit()
                if self._day.get("day") == day:
                    self._day = dict(self._day, unread=False)
                    self.dayChanged.emit()
            return
        if self.bus is not None:
            self._call("MarkRead", [day], None)  # usługa wyśle Changed, a my pobierzemy stan na nowo

    @pyqtSlot(str, result=bool)
    def copyText(self, text: str) -> bool:
        clipboard = QGuiApplication.clipboard() if QGuiApplication.instance() else None
        if clipboard is None or not text:
            return False
        clipboard.setText(text)
        return True

    # --- wnętrze ------------------------------------------------------------------------------

    def _report(self, day: str) -> dict:
        """Raport doby z danych z pliku — z polami ok, latest i unread jak z Day()."""
        value = self._reports.get(day)
        if not isinstance(value, dict):
            return {"ok": False, "error": "missing", "day": day}
        latest = day == self._days.get("latest")
        out = {"legacy": False}
        out.update(value)
        out.update(ok=True, day=day, latest=latest, unread=latest and self._unread)
        return out

    @pyqtSlot()
    def _changed(self) -> None:
        self.reload()

    @pyqtSlot()
    def _board_changed(self) -> None:
        if self._loaded:
            self._call_board()

    def _lost(self) -> None:
        self._set_available(False)
        if self._unread:
            self._unread = False
            self.badgeChanged.emit()

    def _status(self) -> None:
        self._call("Status", [], self._got_status)

    def _got_status(self, ok: bool, value) -> None:
        if not ok:
            self._lost()
            return
        self._set_available(True)
        unread = bool(parse(value, {}).get("unread"))
        if unread != self._unread:
            self._unread = unread
            self.badgeChanged.emit()

    def _got_days(self, ok: bool, value) -> None:
        if not ok:
            self._lost()
            return
        self._set_available(True)
        days = dict(EMPTY_DAYS)
        days.update(parse(value, {}))
        if bool(days.get("unread")) != self._unread:
            self._unread = bool(days.get("unread"))
            self.badgeChanged.emit()
        self._set_days(days)

    def _set_days(self, days: dict) -> None:
        previous = self._days.get("latest")
        changed = days != self._days
        self._days = days
        if changed:
            self.daysChanged.emit()
        names = [d.get("day") for d in days.get("days") or [] if isinstance(d, dict)]
        # Bez wyboru, wybrana doba zniknęła albo był wybrany najnowszy raport — teraz najnowszy.
        if not self._selected or self._selected not in names or self._selected == previous:
            self._selected = days.get("latest") or ""
            self._day = {}
            self.dayChanged.emit()
        if self._selected:
            self._fetch_day()
        elif self._day:
            self._day = {}
            self.dayChanged.emit()

    def _fetch_day(self) -> None:
        day = self._selected
        if self._data is not None:
            self._got_day(day, True, json.dumps(self._report(day)))
            return
        if self.bus is not None:
            self._call("Day", [day], lambda ok, value: self._got_day(day, ok, value))

    def _got_day(self, day: str, ok: bool, value) -> None:
        if day != self._selected:
            return  # odpowiedź dla doby, z której użytkownik już przeszedł
        data = parse(value, {}) if ok else {"ok": False, "error": "unavailable", "day": day}
        if data != self._day:
            self._day = data
            self.dayChanged.emit()

    def _generated(self, ok: bool, value) -> None:
        self._set_generating(False)
        path = value if ok and isinstance(value, str) else ""
        if path:
            self._selected = ""   # nowy albo odświeżony raport doby — pokaż najnowszy
        self.generateFinished.emit(path)
        self.reload()

    def _call_board(self) -> None:
        self._call("Snapshot", [], self._got_board, path=BOARD_PATH, iface=BOARD_IFACE)

    def _got_board(self, ok: bool, value) -> None:
        tags = board_tags(parse(value, {})) if ok else {}
        if tags != self._tags:
            self._tags = tags
            self.dayChanged.emit()

    def _set_available(self, value: bool) -> None:
        if value != self._available:
            self._available = value
            self.availableChanged.emit()
            self.badgeChanged.emit()

    def _set_generating(self, value: bool) -> None:
        if value != self._generating:
            self._generating = value
            self.generatingChanged.emit()

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
    """Backend widoku dla okna Tide: z magistralą (na żywo) albo z danymi z pliku (data: dict)."""
    if data is not None:
        return Backend(None, data if isinstance(data, dict) else {})
    return Backend(bus)
