"""Widok „Modele” okna Tide: serwery modeli lokalnych, role i tryb raportu — stan i zapis ustawień przez
tide-daemon (/Models, docs/tide-daemon.md#modele-lokalne) albo stałe dane z pliku (--data; zapis wyłączony).

Moduł widoku (apps/tide/views.json): okno Tide ładuje go z views/models/backend.py i woła create(bus, data).
Strona QML to qml/ModelsView.qml.

Plik dla --data: odpowiedź Status() (kształt z docs/tide-daemon.md, np. apps/models/tests/fixture.py albo
`tide-daemon.py --print-models` — ten nie ma `report` ani `pulls`, więc widok pokazuje wtedy tryb raportu
jako nieznany).

Backend jest cienki: przekazuje Status() dalej (z dopisanymi nazwami serwerów do wyświetlenia i listą
problemów — tym samym, z czego liczy plakietkę) i wysyła zapisy, które wybierze widok. Odpowiedź zapisu
wraca sygnałem replied(tag, metoda, wynik JSON): tag nadaje widok (np. „server:mac:test”), żeby błąd
usługi trafił obok kontrolki, która go wywołała. Po udanym zapisie usługa sama wysyła Changed, a my
i tak pytamy o Status() od razu — widok nie czeka na sygnał.
"""
import json
import re
from urllib.parse import urlsplit

from PyQt6.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply,
                          QDBusServiceWatcher)

SERVICE, PATH, IFACE = "org.tide.Daemon", "/Models", "org.tide.Daemon.Models"
# Metody zapisu i próby, które widok może wywołać (jeden argument: nazwa albo JSON).
WRITES = ("SetEndpoint", "RemoveEndpoint", "SetDefault", "SetRole", "SetReportSummary", "TestEndpoint", "PullModel")
WRITE_TIMEOUT_MS = 10_000
TEST_TIMEOUT_MS = 15_000          # TestEndpoint czyta serwer do 2 × 3 s, odpowiedź przychodzi po odczycie
PREVIEW_ERROR = "podgląd bez usługi — nic nie zmieniono"
LOST_ERROR = "usługa tide-daemon nie odpowiedziała — sprawdź stan po odświeżeniu"
THIS_PC = "Ten komputer"
# Role, których używa Tide, w kolejności widoku; summary i describe liczą się tylko w trybie raportu „local”.
ROLE_TITLES = {"summary": "Raport", "describe": "Opisy kart", "agent": "Agent"}


def is_local_url(url: str) -> bool:
    """Adres na tym komputerze: localhost, 127.x, [::1]."""
    try:
        host = (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return False
    return host in ("localhost", "::1") or host.endswith(".localhost") or re.fullmatch(r"127(\.\d{1,3}){3}", host) is not None


def server_label(name: str, url: str, endpoints: dict | None = None) -> str:
    """Nazwa serwera dla użytkownika: „Ten komputer” dla adresu lokalnego (z nazwą, gdy lokalnych jest kilka),
    inaczej nazwa punktu z odstępami zamiast - i _ („Mac-Studio” → „Mac Studio”)."""
    pretty = re.sub(r"[-_]+", " ", str(name)).strip() or str(name)
    if not is_local_url(url):
        return pretty
    local = [n for n, e in (endpoints or {}).items() if isinstance(e, dict) and is_local_url(e.get("url", ""))]
    return THIS_PC if len(local) <= 1 else f"{THIS_PC} · {pretty}"


def _endpoints(status: dict) -> dict:
    endpoints = status.get("endpoints") if isinstance(status, dict) else None
    return {k: v for k, v in endpoints.items() if isinstance(v, dict)} if isinstance(endpoints, dict) else {}


def label_of(status: dict, name: str) -> str:
    endpoint = _endpoints(status).get(name) or {}
    return server_label(name, endpoint.get("url", ""), _endpoints(status))


def used_roles(status: dict) -> list[str]:
    """Role, których Tide teraz używa: summary i describe w trybie raportu „local”, agent — gdy jest ustawiony."""
    roles = status.get("roles") if isinstance(status.get("roles"), dict) else {}
    report = status.get("report") if isinstance(status.get("report"), dict) else {}
    used = ["summary", "describe"] if report.get("summary") == "local" else []
    if "agent" in roles:
        used.append("agent")
    return [r for r in used if isinstance(roles.get(r), dict)]


def problems(status: dict) -> list[dict]:
    """Co wymaga uwagi: {text, endpoint, role}. Tylko po pierwszym odczycie (checked) i tylko to, co Tide
    naprawdę używa — wyłączony serwer, z którego nic nie korzysta, nie jest problemem. Serwer niesprawdzony
    (online: false bez błędu — „sprawdzam…”) też nie."""
    if not isinstance(status, dict) or not status:
        return []
    out = []
    config = status.get("config") if isinstance(status.get("config"), dict) else {}
    if config.get("error"):
        out.append({"text": f"Ustawienia modeli nie do użycia, działają domyślne: {config['error']}", "endpoint": "",
                    "role": ""})
    if not status.get("checked"):
        return out
    endpoints = _endpoints(status)
    roles = status.get("roles") if isinstance(status.get("roles"), dict) else {}
    used = used_roles(status)
    default = status.get("default") or ""
    default_state = endpoints.get(default) or {}
    default_down = bool(used) and not default_state.get("online") and bool(default_state.get("error"))
    if default_down:
        out.append({"text": f"Używany serwer „{label_of(status, default)}” nie odpowiada", "endpoint": default,
                    "role": ""})
    for name in used:
        role = roles[name]
        if role.get("available"):
            continue
        endpoint = role.get("endpoint") or ""
        state = endpoints.get(endpoint) or {}
        title = ROLE_TITLES.get(name, name)
        label = label_of(status, endpoint)
        if role.get("error"):
            text = f"{title}: {role['error']}"
        elif not state.get("online"):
            if not state.get("error") or (default_down and endpoint == default):
                continue  # jeszcze niesprawdzony albo już policzony jako używany serwer
            text = f"{title}: serwer „{label}” nie odpowiada"
        elif role.get("installed") is False:
            text = f"{title}: modelu {role.get('model', '')} nie ma na serwerze „{label}”"
        else:
            text = f"{title}: model niedostępny"
        out.append({"text": text, "endpoint": endpoint, "role": name})
    return out


def badge(status: dict) -> tuple[str, str]:
    """Plakietka w pasku okna: („● N”, waiting) — tyle rzeczy wymaga uwagi; nic — ukryta."""
    count = len(problems(status))
    return (f"● {count}", "waiting") if count else ("", "muted")


def decorate(status: dict) -> dict:
    """Status() dla QML: endpoints.*.label (nazwa dla użytkownika), endpoints.*.local i problems."""
    if not isinstance(status, dict):
        return {}
    out = dict(status)
    endpoints = _endpoints(status)
    out["endpoints"] = {name: dict(e, label=server_label(name, e.get("url", ""), endpoints),
                                   local=is_local_url(e.get("url", ""))) for name, e in endpoints.items()}
    out["problems"] = problems(status)
    return out


def _names_endpoint(argument: str) -> bool:
    """Próba zapisanego serwera ({name, …}), nie nowego adresu."""
    try:
        request = json.loads(argument)
    except ValueError:
        return False
    return isinstance(request, dict) and bool(request.get("name"))


class Backend(QObject):
    """Most między QML a tide-daemon: stan (JSON) i zapisy z wynikiem przez sygnał replied."""

    statusChanged = pyqtSignal()
    availableChanged = pyqtSignal()
    badgeChanged = pyqtSignal()
    replied = pyqtSignal(str, str, str)  # (tag widoku, metoda, wynik JSON {ok, error, …})

    def __init__(self, bus: QDBusConnection | None, data=None, parent=None):
        super().__init__(parent)
        self.bus = bus
        self._preview = data is not None
        status = data if isinstance(data, dict) else {}
        self._status = json.dumps(decorate(status), ensure_ascii=False)
        self._available = self._preview
        self._badge = badge(status) if self._preview else ("", "muted")
        self._pending: set = set()      # QDBusPendingCallWatcher muszą żyć do odpowiedzi
        self._loading = False
        self._again = False
        if self._preview or bus is None:
            return
        bus.connect(SERVICE, PATH, IFACE, "Changed", self._changed)
        self._watcher = QDBusServiceWatcher(SERVICE, bus, QDBusServiceWatcher.WatchModeFlag.WatchForRegistration
                                            | QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration, self)
        self._watcher.serviceRegistered.connect(lambda _name: self.reload())
        self._watcher.serviceUnregistered.connect(lambda _name: self._set_available(False))
        self.reload()

    # --- właściwości dla QML ------------------------------------------------------------------

    @pyqtProperty(str, notify=statusChanged)
    def statusJson(self) -> str:
        return self._status

    # Usługa odpowiada na /Models (albo dane z pliku).
    @pyqtProperty(bool, notify=availableChanged)
    def available(self) -> bool:
        return self._available

    @pyqtProperty(bool, constant=True)
    def preview(self) -> bool:
        return self._preview

    @pyqtProperty(str, notify=badgeChanged)
    def badgeText(self) -> str:
        return self._badge[0]

    @pyqtProperty(str, notify=badgeChanged)
    def badgeTone(self) -> str:
        return self._badge[1]

    # --- odczyt i zapis -----------------------------------------------------------------------

    @pyqtSlot()
    def reload(self) -> None:
        """Status() z pamięci usługi (tanio, bez pytania serwerów)."""
        if self.bus is None or self._preview:
            return
        if self._loading:
            self._again = True
            return
        self._loading = True
        self._call("Status", [], self._got_status)

    @pyqtSlot()
    def refresh(self) -> None:
        """Odczyt serwerów teraz (Refresh) — wynik przyjdzie sygnałem Changed."""
        if self.bus is None or self._preview:
            return
        self._call("Refresh", [], None)

    @pyqtSlot(str, str, str)
    def request(self, member: str, argument: str, tag: str) -> None:
        """Zapis albo próba: metoda z WRITES z jednym argumentem (nazwa albo JSON). Wynik — replied(tag, …)."""
        def refuse(error: str) -> None:
            self.replied.emit(tag, member, json.dumps({"ok": False, "error": error}, ensure_ascii=False))
        if member not in WRITES:
            refuse(f"nieznana operacja: {member}")
            return
        if self._preview or self.bus is None:
            refuse(PREVIEW_ERROR if self._preview else LOST_ERROR)
            return

        def done(ok: bool, value) -> None:
            if not ok or not isinstance(value, str):
                refuse(LOST_ERROR)
                return
            self.replied.emit(tag, member, value)
            try:
                result = json.loads(value)
            except ValueError:
                result = {}
            if member == "TestEndpoint":
                # Próba zapisanego serwera: niech karta pokaże stan od razu, nie po kolejnym cyklu.
                if isinstance(result, dict) and result.get("ok") and _names_endpoint(argument):
                    self.refresh()
            elif isinstance(result, dict) and result.get("ok"):
                self.reload()
        self._call(member, [argument], done, timeout=TEST_TIMEOUT_MS if member == "TestEndpoint" else WRITE_TIMEOUT_MS)

    # --- wnętrze ------------------------------------------------------------------------------

    @pyqtSlot()
    def _changed(self) -> None:
        self.reload()

    def _got_status(self, ok: bool, value) -> None:
        self._loading = False
        if not ok or not isinstance(value, str):
            self._set_available(False)
        else:
            try:
                status = json.loads(value)
            except ValueError:
                status = {}
            text = json.dumps(decorate(status), ensure_ascii=False)
            if text != self._status:
                self._status = text
                self.statusChanged.emit()
            self._set_available(True)
            self._set_badge(badge(status))
        if self._again:
            self._again = False
            self.reload()

    def _set_badge(self, value: tuple[str, str]) -> None:
        if value != self._badge:
            self._badge = value
            self.badgeChanged.emit()

    def _set_available(self, value: bool) -> None:
        if value != self._available:
            self._available = value
            self.availableChanged.emit()
        if not value:
            self._set_badge(("", "muted"))

    def _call(self, member: str, args: list, then, timeout: int = WRITE_TIMEOUT_MS) -> None:
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
    """Backend widoku dla okna Tide: z magistralą (na żywo) albo ze stałymi danymi (data: dict z pliku)."""
    if data is not None:
        return Backend(None, data if isinstance(data, dict) else {})
    return Backend(bus)
