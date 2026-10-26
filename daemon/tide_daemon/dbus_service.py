"""Kontrakt D-Bus usługi (opis: docs/tide-daemon.md).

Usługa org.tide.Daemon.

Obiekt /Agents, interfejs org.tide.Daemon.Agents:
- Sessions() -> s            lista sesji jako JSON,
- ActivateSession(s id) -> b przejście do okna sesji, False gdy nie da się go ustalić,
- ActivateLongestWaiting() -> b  przejście do sesji czekającej najdłużej (skrót Meta+A);
                             False, gdy nic nie czeka albo okna nie da się ustalić; sesje
                             w stanie „done” (skończona tura) nie są brane pod uwagę,
- sygnał Changed()           lista sesji się zmieniła — warto pobrać ją ponownie.

Obiekt /Worktrees, interfejs org.tide.Daemon.Worktrees:
- List() -> s                lista worktrees jako JSON,
- Remove(as paths) -> s      sprzątanie; JSON z wynikiem dla każdej ścieżki (odpowiedź po zakończeniu),
- RemoveForced(as paths, b deleteUnmergedBranches, s confirmed) -> s
                             usuwanie po potwierdzeniu „Usuń mimo to”: `git worktree remove --force`,
                             gałąź -d gdy scalona, -D tylko z deleteUnmergedBranches; główny katalog,
                             gałąź główna, brakujący, zablokowany i z agentem — nigdy; confirmed to
                             JSON ze stanem z potwierdzenia (gorszy świeży stan = odmowa „changed”),
- Refresh()                  odświeżenie na żądanie (wynik przyjdzie sygnałem Changed),
- Open(s path, s app) -> b   otwarcie worktree: app = "terminal" albo "rider",
- Launchers() -> s           JSON {"terminal": bool, "rider": bool, "window": bool} — które przyciski pokazać,
- ShowWindow() -> b          otwiera okno Worktrees (False, gdy nie jest zainstalowane),
- Prune(s repoPath) -> s     `git worktree prune` w repozytorium z worktrees bez katalogu (odpowiedź po zakończeniu),
- sygnał Changed()           lista worktrees się zmieniła.

Obiekt /Idle, interfejs org.tide.Daemon.Idle (bezczynność użytkownika dla tapety z kotem):
- właściwość Idle (b)        od progu nie było klawiatury ani myszy (False, gdy źródło nie działa),
- właściwość Available (b)   czy kompozytor podaje bezczynność,
- właściwość ThresholdMs (i) próg bezczynności (paleta: wallpaper.dozeAfterS),
- IsIdle() -> b              to samo co Idle, jednym wywołaniem (dla QML),
- State() -> s               JSON {available, idle, thresholdMs, idleForMs, source} — diagnostyka,
- sygnał IdleChanged(b idle) bezczynność się zmieniła.
"""
from PyQt6.QtCore import QObject, pyqtClassInfo, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection, QDBusMessage

SERVICE = "org.tide.Daemon"
AGENTS_PATH = "/Agents"
AGENTS_IFACE = "org.tide.Daemon.Agents"
WORKTREES_PATH = "/Worktrees"
WORKTREES_IFACE = "org.tide.Daemon.Worktrees"
IDLE_PATH = "/Idle"
IDLE_IFACE = "org.tide.Daemon.Idle"

AGENTS_XML = f"""<interface name="{AGENTS_IFACE}">
  <method name="Sessions"><arg name="json" type="s" direction="out"/></method>
  <method name="ActivateSession">
    <arg name="id" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <method name="ActivateLongestWaiting"><arg name="ok" type="b" direction="out"/></method>
  <signal name="Changed"/>
</interface>"""

WORKTREES_XML = f"""<interface name="{WORKTREES_IFACE}">
  <method name="List"><arg name="json" type="s" direction="out"/></method>
  <method name="Remove">
    <arg name="paths" type="as" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="RemoveForced">
    <arg name="paths" type="as" direction="in"/>
    <arg name="deleteUnmergedBranches" type="b" direction="in"/>
    <arg name="confirmed" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="Refresh"/>
  <method name="Open">
    <arg name="path" type="s" direction="in"/>
    <arg name="app" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <method name="Launchers"><arg name="json" type="s" direction="out"/></method>
  <method name="ShowWindow"><arg name="ok" type="b" direction="out"/></method>
  <method name="Prune">
    <arg name="repoPath" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <signal name="Changed"/>
</interface>"""

IDLE_XML = f"""<interface name="{IDLE_IFACE}">
  <property name="Idle" type="b" access="read"/>
  <property name="Available" type="b" access="read"/>
  <property name="ThresholdMs" type="i" access="read"/>
  <method name="IsIdle"><arg name="idle" type="b" direction="out"/></method>
  <method name="State"><arg name="json" type="s" direction="out"/></method>
  <signal name="IdleChanged"><arg name="idle" type="b"/></signal>
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

    @pyqtSlot(result=bool)
    def ActivateLongestWaiting(self) -> bool:
        return self.monitor.activate_longest_waiting()


@pyqtClassInfo("D-Bus Interface", WORKTREES_IFACE)
@pyqtClassInfo("D-Bus Introspection", WORKTREES_XML)
class WorktreesAdaptor(QDBusAbstractAdaptor):
    Changed = pyqtSignal()

    def __init__(self, parent: QObject, monitor, bus: QDBusConnection):
        super().__init__(parent)
        self.monitor = monitor
        self.bus = bus
        monitor.changed.connect(self.Changed)

    @pyqtSlot(result=str)
    def List(self) -> str:
        return self.monitor.list_json()

    @pyqtSlot("QStringList", QDBusMessage, result=str)
    def Remove(self, paths, message: QDBusMessage) -> str:
        # Usuwanie może trwać (duże katalogi), więc odpowiadamy później, z wątku głównego,
        # a pętla zdarzeń w tym czasie obsługuje inne wywołania.
        self.monitor.remove(list(paths), self._delayed(message, "Remove"))
        return ""

    @pyqtSlot("QStringList", bool, str, QDBusMessage, result=str)
    def RemoveForced(self, paths, delete_unmerged: bool, confirmed: str, message: QDBusMessage) -> str:
        self.monitor.remove(list(paths), self._delayed(message, "RemoveForced"), force=True,
                            delete_unmerged=bool(delete_unmerged), confirmed=confirmed)
        return ""

    def _delayed(self, message: QDBusMessage, name: str):
        """Odpowiedź z opóźnieniem: funkcja, która odeśle tekst, gdy praca w tle się skończy."""
        message.setDelayedReply(True)
        request = QDBusMessage(message)  # kopia — oryginał żyje tylko do końca tego wywołania

        def reply(text: str) -> None:
            if not self.bus.send(request.createReply([text])):
                print(f"tide-daemon: nie udało się odesłać wyniku {name}", flush=True)

        return reply

    @pyqtSlot()
    def Refresh(self) -> None:
        self.monitor.refresh()

    @pyqtSlot(str, str, result=bool)
    def Open(self, path: str, app: str) -> bool:
        return self.monitor.open(path, app)

    @pyqtSlot(result=str)
    def Launchers(self) -> str:
        return self.monitor.launchers_json()

    @pyqtSlot(result=bool)
    def ShowWindow(self) -> bool:
        return self.monitor.show_window()

    @pyqtSlot(str, QDBusMessage, result=str)
    def Prune(self, repo_path: str, message: QDBusMessage) -> str:
        message.setDelayedReply(True)
        request = QDBusMessage(message)
        self.monitor.prune(repo_path, lambda text: self.bus.send(request.createReply([text])))
        return ""


@pyqtClassInfo("D-Bus Interface", IDLE_IFACE)
@pyqtClassInfo("D-Bus Introspection", IDLE_XML)
class IdleAdaptor(QDBusAbstractAdaptor):
    IdleChanged = pyqtSignal(bool)

    def __init__(self, parent: QObject, monitor):
        super().__init__(parent)
        self.monitor = monitor
        monitor.idleChanged.connect(self.IdleChanged)

    @pyqtProperty(bool)
    def Idle(self) -> bool:
        return self.monitor.idle

    @pyqtProperty(bool)
    def Available(self) -> bool:
        return self.monitor.available

    @pyqtProperty(int)
    def ThresholdMs(self) -> int:
        return self.monitor.state.threshold_ms

    @pyqtSlot(result=bool)
    def IsIdle(self) -> bool:
        return self.monitor.idle

    @pyqtSlot(result=str)
    def State(self) -> str:
        return self.monitor.state_json()


def register(monitor, worktrees=None, bus: QDBusConnection | None = None, idle=None) -> QObject:
    """Rejestruje obiekty i nazwę usługi. Zwraca obiekt, który trzeba trzymać przy życiu."""
    bus = bus or QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise RuntimeError("brak połączenia z magistralą sesji D-Bus")
    holder = QObject()
    holder.agents = QObject(holder)
    holder.agents.adaptor = AgentsAdaptor(holder.agents, monitor)
    if not bus.registerObject(AGENTS_PATH, holder.agents):
        raise RuntimeError(f"nie udało się zarejestrować obiektu {AGENTS_PATH}")
    if worktrees is not None:
        holder.worktrees = QObject(holder)
        holder.worktrees.adaptor = WorktreesAdaptor(holder.worktrees, worktrees, bus)
        if not bus.registerObject(WORKTREES_PATH, holder.worktrees):
            raise RuntimeError(f"nie udało się zarejestrować obiektu {WORKTREES_PATH}")
    if idle is not None:
        holder.idle = QObject(holder)
        holder.idle.adaptor = IdleAdaptor(holder.idle, idle)
        if not bus.registerObject(IDLE_PATH, holder.idle):
            raise RuntimeError(f"nie udało się zarejestrować obiektu {IDLE_PATH}")
    if not bus.registerService(SERVICE):
        raise RuntimeError(f"nazwa {SERVICE} jest już zajęta (działa inna kopia usługi?)")
    return holder
