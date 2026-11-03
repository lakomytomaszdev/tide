"""Atrapa serwera powiadomień (org.freedesktop.Notifications) na prywatnej magistrali testu.

Uruchamiana jako osobny proces z DBUS_SESSION_BUS_ADDRESS prywatnej magistrali. Zapisuje każde
Notify i CloseNotification, a metody pomocnicze pozwalają testowi je odczytać (Calls) i udać
kliknięcie przycisku w powiadomieniu (Invoke → sygnał ActionInvoked).
"""
import json
import signal
import sys

from PyQt6.QtCore import QCoreApplication, QObject, QTimer, pyqtClassInfo, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection

IFACE = "org.freedesktop.Notifications"
XML = f"""<interface name="{IFACE}">
  <method name="Notify">
    <arg name="app_name" type="s" direction="in"/>
    <arg name="replaces_id" type="u" direction="in"/>
    <arg name="app_icon" type="s" direction="in"/>
    <arg name="summary" type="s" direction="in"/>
    <arg name="body" type="s" direction="in"/>
    <arg name="actions" type="as" direction="in"/>
    <arg name="hints" type="a{{sv}}" direction="in"/>
    <arg name="expire_timeout" type="i" direction="in"/>
    <arg name="id" type="u" direction="out"/>
  </method>
  <method name="CloseNotification"><arg name="id" type="u" direction="in"/></method>
  <method name="GetCapabilities"><arg name="caps" type="as" direction="out"/></method>
  <method name="Inhibit">
    <arg name="desktop_entry" type="s" direction="in"/>
    <arg name="reason" type="s" direction="in"/>
    <arg name="hints" type="a{{sv}}" direction="in"/>
    <arg name="cookie" type="u" direction="out"/>
  </method>
  <method name="UnInhibit"><arg name="cookie" type="u" direction="in"/></method>
  <method name="Calls"><arg name="json" type="s" direction="out"/></method>
  <method name="Invoke">
    <arg name="id" type="u" direction="in"/>
    <arg name="action" type="s" direction="in"/>
  </method>
  <signal name="ActionInvoked"><arg name="id" type="u"/><arg name="action_key" type="s"/></signal>
  <signal name="NotificationClosed"><arg name="id" type="u"/><arg name="reason" type="u"/></signal>
</interface>"""


@pyqtClassInfo("D-Bus Interface", IFACE)
@pyqtClassInfo("D-Bus Introspection", XML)
class FakeNotifications(QDBusAbstractAdaptor):
    ActionInvoked = pyqtSignal("uint", str)
    NotificationClosed = pyqtSignal("uint", "uint")

    def __init__(self, parent):
        super().__init__(parent)
        self.calls = []
        self.next_id = 100

    @pyqtSlot(str, "uint", str, str, str, "QStringList", "QVariantMap", int, result="uint")
    def Notify(self, app, replaces, icon, summary, body, actions, hints, timeout):
        self.next_id += 1
        self.calls.append({"method": "Notify", "id": self.next_id, "app": app, "replaces": replaces, "icon": icon,
                           "summary": summary, "body": body, "actions": list(actions), "timeout": timeout})
        return self.next_id

    @pyqtSlot("uint")
    def CloseNotification(self, notice):
        self.calls.append({"method": "CloseNotification", "id": notice})
        self.NotificationClosed.emit(notice, 3)

    @pyqtSlot(result="QStringList")
    def GetCapabilities(self):
        return ["actions", "body"]

    # Rozszerzenie Plasmy: „Nie przeszkadzać” na prośbę aplikacji, aż do UnInhibit albo zniknięcia wołającego.
    @pyqtSlot(str, str, "QVariantMap", result="uint")
    def Inhibit(self, desktop_entry, reason, hints):
        self.next_id += 1
        self.calls.append({"method": "Inhibit", "cookie": self.next_id, "desktopEntry": desktop_entry,
                           "reason": reason})
        return self.next_id

    @pyqtSlot("uint")
    def UnInhibit(self, cookie):
        self.calls.append({"method": "UnInhibit", "cookie": cookie})

    @pyqtSlot(result=str)
    def Calls(self):
        return json.dumps(self.calls)

    @pyqtSlot("uint", str)
    def Invoke(self, notice, action):
        self.ActionInvoked.emit(notice, action)


def main() -> int:
    app = QCoreApplication(sys.argv[:1])
    bus = QDBusConnection.sessionBus()
    holder = QObject()
    holder.adaptor = FakeNotifications(holder)
    if not bus.registerObject("/org/freedesktop/Notifications", holder) or not bus.registerService(IFACE):
        return 1
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    wake = QTimer(interval=200)
    wake.timeout.connect(lambda: None)
    wake.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
