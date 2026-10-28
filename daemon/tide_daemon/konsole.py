"""Karty i okna Konsole przez D-Bus — zaplecze dla tabmarks.TabMarker i windows.Activator.

Konsole wystawia każdą kartę jako /Sessions/<n>, a każde okno jako /Windows/<m> pod nazwą usługi
z PID procesu (KONSOLE_DBUS_SERVICE w środowisku agenta). Jeden proces może mieć kilka okien
(np. karta przeciągnięta poza okno), a numery są wspólne dla całego procesu. Używamy tylko metod
skryptowalnych: org.kde.konsole.Session — title(int), setTitle(int, s), tabTitleFormat(int),
setTabTitleFormat(int, s), tabColor(), setTabColor(s); org.kde.konsole.Window — sessionList(),
currentSession(), setCurrentSession(int). Kontekst 0 to karta lokalna, 1 zdalna (np. ssh na
pierwszym planie).

Wywołania są blokujące z krótkim limitem czasu: to kilka szybkich zapytań przy zmianie stanu,
a Konsole, która nie odpowiada, kończy się po LIMIT_MS, nie wiesza usługi.
"""
from xml.etree import ElementTree

from PyQt6.QtCore import QMetaType
from PyQt6.QtDBus import QDBus, QDBusArgument, QDBusConnection, QDBusMessage

from .tabmarks import split_key

IFACE = "org.kde.konsole.Session"
WINDOW_IFACE = "org.kde.konsole.Window"
INTROSPECTABLE = "org.freedesktop.DBus.Introspectable"
LIMIT_MS = 1000
DISPLAYED_TITLE = 1   # Session::DisplayedTitleRole
LOCAL, REMOTE = 0, 1  # Session::LocalTabTitle, RemoteTabTitle


def _int(value: int) -> QDBusArgument:
    return QDBusArgument(value, QMetaType.Type.Int.value)


def child_nodes(xml: str) -> list[str]:
    """Nazwy obiektów potomnych z odpowiedzi Introspect (np. ["1", "3"] dla /Windows)."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []
    return [node.get("name") for node in root.findall("node") if node.get("name")]


def _number(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class KonsoleTabs:
    def __init__(self, bus: QDBusConnection | None = None):
        self.bus = bus or QDBusConnection.sessionBus()

    def _call(self, key: str, member: str, *args):
        service, path = split_key(key)
        return self._call_at(service, path, IFACE, member, *args)

    def _call_at(self, service: str, path: str, iface: str, member: str, *args):
        msg = QDBusMessage.createMethodCall(service, path, iface, member)
        if args:
            msg.setArguments(list(args))
        reply = self.bus.call(msg, QDBus.CallMode.Block, LIMIT_MS)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            return None
        values = reply.arguments()
        return values[0] if values else True

    def read(self, key: str) -> dict | None:
        """Formaty tytułu, wyświetlany tytuł i kolor karty; None, gdy karty nie ma."""
        values = {
            "local": self._call(key, "tabTitleFormat", _int(LOCAL)),
            "remote": self._call(key, "tabTitleFormat", _int(REMOTE)),
            "title": self._call(key, "title", _int(DISPLAYED_TITLE)),
            "color": self._call(key, "tabColor"),
        }
        if any(v is None for v in values.values()):
            return None
        return {k: str(v) for k, v in values.items()}

    def apply(self, key: str, title=None, local=None, remote=None, color=None) -> bool:
        """Zmienia podane elementy karty. Kolejność ma znaczenie: setTitle(1) nadpisuje oba
        formaty, więc formaty idą po nim."""
        ok = True
        if title is not None:
            ok &= self._call(key, "setTitle", _int(DISPLAYED_TITLE), title) is not None
        if local is not None:
            ok &= self._call(key, "setTabTitleFormat", _int(LOCAL), local) is not None
        if remote is not None:
            ok &= self._call(key, "setTabTitleFormat", _int(REMOTE), remote) is not None
        if color is not None:
            ok &= self._call(key, "setTabColor", color) is not None
        return bool(ok)

    # --- okna (przejście do sesji, windows.Activator) -------------------------------------

    def windows(self, service: str) -> list[str] | None:
        """Ścieżki okien procesu Konsole (np. ["/Windows/2"]); None, gdy nie da się ich wyliczyć."""
        xml = self._call_at(service, "/Windows", INTROSPECTABLE, "Introspect")
        if not isinstance(xml, str):
            return None
        return [f"/Windows/{name}" for name in child_nodes(xml)]

    def sessions(self, service: str, window: str) -> list[int] | None:
        """Numery kart okna; None, gdy okno nie odpowiada."""
        values = self._call_at(service, window, WINDOW_IFACE, "sessionList")
        if not isinstance(values, list):
            return None
        return [n for n in (_number(v) for v in values) if n is not None]

    def current(self, service: str, window: str) -> int | None:
        """Numer bieżącej karty okna."""
        value = self._call_at(service, window, WINDOW_IFACE, "currentSession")
        return None if isinstance(value, bool) else _number(value)

    def set_current(self, service: str, window: str, number: int) -> bool:
        return self._call_at(service, window, WINDOW_IFACE, "setCurrentSession", _int(number)) is not None

    def title(self, service: str, number: int) -> str | None:
        """Wyświetlany tytuł karty — ten sam tekst Konsole przekazuje oknu, gdy karta jest bieżąca."""
        value = self._call_at(service, f"/Sessions/{number}", IFACE, "title", _int(DISPLAYED_TITLE))
        return value if isinstance(value, str) else None
