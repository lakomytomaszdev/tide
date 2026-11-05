"""Niezgodna usługa do testu negocjacji CLI; każde Create zostawia dowód wywołania."""
import json
from pathlib import Path
import sys

from PyQt6.QtCore import QCoreApplication, QObject, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection


@pyqtClassInfo("D-Bus Interface", "org.tide.Daemon.Issues")
class OldApi(QDBusAbstractAdaptor):
    @pyqtSlot(result=str)
    def Info(self):
        return json.dumps({"ok": True, "apiVersion": 2, "capabilities": ["issue.create"]})

    @pyqtSlot(str, result=str)
    def Create(self, data):
        Path(sys.argv[1]).write_text(data)
        return json.dumps({"ok": True, "apiVersion": 2})


if __name__ == "__main__":
    app = QCoreApplication([])
    bus = QDBusConnection.sessionBus()
    holder = QObject()
    holder.adaptor = OldApi(holder)
    bus.registerObject("/Issues", holder)
    bus.registerService("org.tide.Daemon")
    sys.exit(app.exec())
