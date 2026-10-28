"""Atrapa org.kde.ActivityManager (interfejs Activities) na prywatnej magistrali testu.

Uruchamiana jako osobny proces z DBUS_SESSION_BUS_ADDRESS prywatnej magistrali (nigdy na magistrali
pulpitu: bez adresu z katalogu tymczasowego testu odmawia startu). Stan początkowy z argumentu JSON
{"activities": {"<ID>": {"name", "icon"}}, "current": "<ID>"}; State() oddaje bieżący stan testowi.
SetCurrentActivity wysyła CurrentActivityChanged jak prawdziwy kactivitymanagerd.
"""
import json
import os
import signal
import sys
import uuid

from PyQt6.QtCore import QCoreApplication, QObject, QTimer, pyqtClassInfo, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection

SERVICE = "org.kde.ActivityManager"
PATH = "/ActivityManager/Activities"
IFACE = "org.kde.ActivityManager.Activities"
XML = f"""<interface name="{IFACE}">
  <method name="AddActivity"><arg name="name" type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="RemoveActivity"><arg name="activity" type="s" direction="in"/></method>
  <method name="ListActivities"><arg type="as" direction="out"/></method>
  <method name="ActivityName"><arg name="activity" type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="ActivityIcon"><arg name="activity" type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="SetActivityIcon">
    <arg name="activity" type="s" direction="in"/><arg name="icon" type="s" direction="in"/>
  </method>
  <method name="CurrentActivity"><arg type="s" direction="out"/></method>
  <method name="SetCurrentActivity"><arg name="activity" type="s" direction="in"/><arg type="b" direction="out"/></method>
  <method name="State"><arg name="json" type="s" direction="out"/></method>
  <signal name="CurrentActivityChanged"><arg name="activity" type="s"/></signal>
  <signal name="ActivityAdded"><arg name="activity" type="s"/></signal>
  <signal name="ActivityRemoved"><arg name="activity" type="s"/></signal>
</interface>"""


@pyqtClassInfo("D-Bus Interface", IFACE)
@pyqtClassInfo("D-Bus Introspection", XML)
class FakeActivities(QDBusAbstractAdaptor):
    CurrentActivityChanged = pyqtSignal(str)
    ActivityAdded = pyqtSignal(str)
    ActivityRemoved = pyqtSignal(str)

    def __init__(self, parent, state: dict):
        super().__init__(parent)
        self.activities = state.get("activities", {})
        self.current = state.get("current", "")
        self.calls: list[list[str]] = []

    @pyqtSlot(str, result=str)
    def AddActivity(self, name):
        self.calls.append(["AddActivity", name])
        activity = str(uuid.uuid4())
        self.activities[activity] = {"name": name, "icon": ""}
        self.ActivityAdded.emit(activity)
        return activity

    @pyqtSlot(str)
    def RemoveActivity(self, activity):
        self.calls.append(["RemoveActivity", activity])
        # Jak kactivitymanagerd: bieżącej aktywności nie da się usunąć (zostaje).
        if activity in self.activities and activity != self.current:
            del self.activities[activity]
            self.ActivityRemoved.emit(activity)

    @pyqtSlot(result="QStringList")
    def ListActivities(self):
        return list(self.activities)

    @pyqtSlot(str, result=str)
    def ActivityName(self, activity):
        return self.activities.get(activity, {}).get("name", "")

    @pyqtSlot(str, result=str)
    def ActivityIcon(self, activity):
        return self.activities.get(activity, {}).get("icon", "")

    @pyqtSlot(str, str)
    def SetActivityIcon(self, activity, icon):
        self.calls.append(["SetActivityIcon", activity, icon])
        if activity in self.activities:
            self.activities[activity]["icon"] = icon

    @pyqtSlot(result=str)
    def CurrentActivity(self):
        return self.current

    @pyqtSlot(str, result=bool)
    def SetCurrentActivity(self, activity):
        if activity not in self.activities:
            return False
        if activity != self.current:
            self.current = activity
            self.CurrentActivityChanged.emit(activity)
        return True

    @pyqtSlot(result=str)
    def State(self):
        return json.dumps({"activities": self.activities, "current": self.current, "calls": self.calls})


def main() -> int:
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if "tide-bus-" not in address:
        print("atrapa ActivityManager działa tylko na prywatnej magistrali testu", file=sys.stderr)
        return 2
    app = QCoreApplication(sys.argv[:1])
    state = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    bus = QDBusConnection.sessionBus()
    holder = QObject()
    holder.adaptor = FakeActivities(holder, state)
    if not bus.registerObject(PATH, holder) or not bus.registerService(SERVICE):
        return 1
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    wake = QTimer(interval=200)
    wake.timeout.connect(lambda: None)
    wake.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
