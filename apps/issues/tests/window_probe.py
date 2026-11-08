"""Prawdziwe okno z testowym odczytem zaznaczenia, wyłącznie na prywatnym D-Bus."""
import importlib.util
import json
import sys

from PyQt6.QtCore import QObject, QUrl, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBusConnection
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine

package = sys.argv[1]
spec = importlib.util.spec_from_file_location("test_window", package + "/tide.py")
tide = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tide)
app = QGuiApplication([])
bus = QDBusConnection.sessionBus()
views = tide.load_views(package)
backends = tide.make_backends(views, bus, None, package)
shell = tide.Shell(views, backends)
assert tide.export(bus, shell)


@pyqtClassInfo("D-Bus Interface", "org.tide.Test.Window")
class Probe(QObject):
    @pyqtSlot(result=str)
    def Selected(self):
        return json.loads(backends["issues"].stateJson)["selected"]


probe = Probe()
bus.registerObject("/Probe", probe, QDBusConnection.RegisterOption.ExportAllSlots)
engine = QQmlApplicationEngine()
factory = tide.guard_network(engine)
backends["issues"].install_image_provider(engine)
engine.setInitialProperties({"shell": shell, "initialView": "board"})
engine.load(QUrl.fromLocalFile(package + "/qml/Main.qml"))
assert engine.rootObjects()
app.exec()
