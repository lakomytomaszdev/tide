"""Atrapa Konsole (org.kde.konsole.Session) na prywatnej magistrali testu.

Uruchamiana jako osobny proces: python3 fake_konsole.py <nazwa usługi> <liczba kart> [okna],
gdzie okna to JSON {"<numer okna>": [numery kart]} (domyślnie wszystkie karty w /Windows/1).
Naśladuje zachowanie Konsole 26.08 ważne dla znacznika kart: setTitle(1, t) ustawia wyświetlany
tytuł i nadpisuje oba formaty tytułu, setTabTitleFormat nie odświeża tytułu, a tabColor()
zwraca "#000000", gdy karta nie ma koloru. Okna (org.kde.konsole.Window) mają sessionList(),
currentSession() i setCurrentSession(int) — to ostatnie działa tylko dla karty z tego okna.
Metoda State() (spoza API Konsole) oddaje testowi stan wszystkich kart jako JSON.
"""
import json
import signal
import sys

from PyQt6.QtCore import QCoreApplication, QObject, QTimer, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection

IFACE = "org.kde.konsole.Session"
XML = f"""<interface name="{IFACE}">
  <method name="title"><arg name="role" type="i" direction="in"/><arg type="s" direction="out"/></method>
  <method name="setTitle">
    <arg name="role" type="i" direction="in"/><arg name="title" type="s" direction="in"/>
  </method>
  <method name="tabTitleFormat"><arg name="context" type="i" direction="in"/><arg type="s" direction="out"/></method>
  <method name="setTabTitleFormat">
    <arg name="context" type="i" direction="in"/><arg name="format" type="s" direction="in"/>
  </method>
  <method name="tabColor"><arg type="s" direction="out"/></method>
  <method name="setTabColor"><arg name="colorName" type="s" direction="in"/></method>
  <method name="State"><arg type="s" direction="out"/></method>
</interface>"""
WINDOW_IFACE = "org.kde.konsole.Window"
WINDOW_XML = f"""<interface name="{WINDOW_IFACE}">
  <method name="sessionList"><arg type="as" direction="out"/></method>
  <method name="currentSession"><arg type="i" direction="out"/></method>
  <method name="setCurrentSession"><arg name="sessionId" type="i" direction="in"/></method>
</interface>"""


@pyqtClassInfo("D-Bus Interface", IFACE)
@pyqtClassInfo("D-Bus Introspection", XML)
class FakeSession(QDBusAbstractAdaptor):
    def __init__(self, parent, number: int, tabs: dict):
        super().__init__(parent)
        self.tab = {"title": f"projekt-{number} : bash", "local": "%d : %n", "remote": "(%u) %H", "color": ""}
        tabs[number] = self.tab
        self.tabs = tabs

    @pyqtSlot(int, result=str)
    def title(self, role):
        return self.tab["title"] if role == 1 else "Powłoka"

    @pyqtSlot(int, str)
    def setTitle(self, role, title):
        if role == 1:
            self.tab["title"] = title
            self.tab["local"] = self.tab["remote"] = title

    @pyqtSlot(int, result=str)
    def tabTitleFormat(self, context):
        return self.tab["local" if context == 0 else "remote"]

    @pyqtSlot(int, str)
    def setTabTitleFormat(self, context, fmt):
        self.tab["local" if context == 0 else "remote"] = fmt

    @pyqtSlot(result=str)
    def tabColor(self):
        return self.tab["color"] or "#000000"

    @pyqtSlot(str)
    def setTabColor(self, color):
        self.tab["color"] = color

    @pyqtSlot(result=str)
    def State(self):
        return json.dumps(self.tabs)


@pyqtClassInfo("D-Bus Interface", WINDOW_IFACE)
@pyqtClassInfo("D-Bus Introspection", WINDOW_XML)
class FakeWindow(QDBusAbstractAdaptor):
    def __init__(self, parent, sessions: list[int]):
        super().__init__(parent)
        self.numbers = list(sessions)
        self.current = self.numbers[0] if self.numbers else -1

    @pyqtSlot(result="QStringList")
    def sessionList(self):
        return [str(n) for n in self.numbers]

    @pyqtSlot(result=int)
    def currentSession(self):
        return self.current

    @pyqtSlot(int)
    def setCurrentSession(self, number):
        if number in self.numbers:
            self.current = number


def main() -> int:
    service, count = sys.argv[1], int(sys.argv[2])
    layout = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {"1": list(range(1, count + 1))}
    app = QCoreApplication(sys.argv[:1])
    bus = QDBusConnection.sessionBus()
    tabs: dict = {}
    holders = []
    for number in range(1, count + 1):
        holder = QObject()
        holder.adaptor = FakeSession(holder, number, tabs)
        if not bus.registerObject(f"/Sessions/{number}", holder):
            return 1
        holders.append(holder)
    for number, sessions in layout.items():
        holder = QObject()
        holder.adaptor = FakeWindow(holder, sessions)
        if not bus.registerObject(f"/Windows/{number}", holder):
            return 1
        holders.append(holder)
    if not bus.registerService(service):
        return 1
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    wake = QTimer(interval=200)
    wake.timeout.connect(lambda: None)
    wake.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
