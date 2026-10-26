"""Kontrakt D-Bus usługi (opis: docs/tide-daemon.md).

Usługa org.tide.Daemon, obiekt /Agents, interfejs org.tide.Daemon.Agents:
- Sessions() -> s            lista sesji jako JSON,
- ActivateSession(s id) -> b przejście do okna sesji, False gdy nie da się go ustalić,
- sygnał Changed()           lista sesji się zmieniła — warto pobrać ją ponownie.
"""
from PyQt6.QtCore import QObject, pyqtClassInfo, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection

SERVICE = "org.tide.Daemon"
AGENTS_PATH = "/Agents"
AGENTS_IFACE = "org.tide.Daemon.Agents"

AGENTS_XML = f"""<interface name="{AGENTS_IFACE}">
  <method name="Sessions"><arg name="json" type="s" direction="out"/></method>
  <method name="ActivateSession">
    <arg name="id" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <signal name="Changed"/>
</interface>"""


@pyqtClassInfo("D-Bus Interface", AGENTS_IFACE)
@pyqtClassInfo("D-Bus Introspection", AGENTS_XML)
class AgentsAdaptor(QDBusAbstractAdaptor):
    Changed = pyqtSignal()

    def __init__(self, parent: QObject, monitor):
        super().__init__(parent)
        self.monitor = monitor
        monitor.changed.connect(self.Changed)

    @pyqtSlot(result=str)
    def Sessions(self) -> str:
        return self.monitor.sessions_json()

    @pyqtSlot(str, result=bool)
    def ActivateSession(self, session_id: str) -> bool:
        return self.monitor.activate(session_id)


def register(monitor, bus: QDBusConnection | None = None) -> QObject:
    """Rejestruje obiekt /Agents i nazwę usługi. Zwraca obiekt, który trzeba trzymać przy życiu."""
    bus = bus or QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise RuntimeError("brak połączenia z magistralą sesji D-Bus")
    holder = QObject()
    holder.adaptor = AgentsAdaptor(holder, monitor)
    if not bus.registerObject(AGENTS_PATH, holder):
        raise RuntimeError(f"nie udało się zarejestrować obiektu {AGENTS_PATH}")
    if not bus.registerService(SERVICE):
        raise RuntimeError(f"nazwa {SERVICE} jest już zajęta (działa inna kopia usługi?)")
    return holder
