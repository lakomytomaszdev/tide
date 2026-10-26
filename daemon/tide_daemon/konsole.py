"""Karty Konsole przez D-Bus (org.kde.konsole.Session) — zaplecze dla tabmarks.TabMarker.

Konsole wystawia każdą kartę jako /Sessions/<n> pod nazwą usługi z PID procesu
(KONSOLE_DBUS_SERVICE w środowisku agenta). Używamy tylko metod skryptowalnych:
title(int), setTitle(int, s), tabTitleFormat(int), setTabTitleFormat(int, s), tabColor(),
setTabColor(s). Kontekst 0 to karta lokalna, 1 zdalna (np. ssh na pierwszym planie).

Wywołania są blokujące z krótkim limitem czasu: to kilka szybkich zapytań przy zmianie stanu,
a Konsole, która nie odpowiada, kończy się po LIMIT_MS, nie wiesza usługi.
"""
from PyQt6.QtCore import QMetaType
from PyQt6.QtDBus import QDBus, QDBusArgument, QDBusConnection, QDBusMessage

from .tabmarks import split_key

IFACE = "org.kde.konsole.Session"
LIMIT_MS = 1000
DISPLAYED_TITLE = 1   # Session::DisplayedTitleRole
LOCAL, REMOTE = 0, 1  # Session::LocalTabTitle, RemoteTabTitle


def _int(value: int) -> QDBusArgument:
    return QDBusArgument(value, QMetaType.Type.Int.value)


class KonsoleTabs:
    def __init__(self, bus: QDBusConnection | None = None):
        self.bus = bus or QDBusConnection.sessionBus()

    def _call(self, key: str, member: str, *args):
        service, path = split_key(key)
        msg = QDBusMessage.createMethodCall(service, path, IFACE, member)
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
