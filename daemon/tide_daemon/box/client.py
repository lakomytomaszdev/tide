"""Wąski transport klienta pudełka; nie importuje buildera, Tablicy ani Incusa."""
import json
import os


class ServiceError(RuntimeError):
    pass


def call(member, *args):
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        raise ServiceError("usługa Tide niedostępna: brak sesyjnej magistrali D-Bus hosta")
    try:
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage
    except ImportError:
        raise ServiceError("usługa Tide niedostępna: klient wymaga PyQt6.QtDBus") from None
    app = QCoreApplication.instance() or QCoreApplication([])  # utrzymuje Qt przy życiu do końca wywołania
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise ServiceError("usługa Tide niedostępna: brak połączenia z sesyjnym D-Bus")
    message = QDBusMessage.createMethodCall("org.tide.Daemon", "/Board", "org.tide.Daemon.Board", member)
    message.setAutoStartService(False)
    message.setArguments(list(args))
    reply = bus.call(message, QDBus.CallMode.Block, 60000)
    if reply.type() != QDBusMessage.MessageType.ReplyMessage:
        raise ServiceError(f"usługa Tide niedostępna lub błąd D-Bus: {reply.errorMessage()}")
    try:
        values = reply.arguments()
        result = json.loads(values[0]) if len(values) == 1 else None
    except (ValueError, TypeError):
        result = None
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise ServiceError("niepoprawna odpowiedź usługi Tide")
    return result
