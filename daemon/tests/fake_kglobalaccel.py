"""Atrapa rejestru skrótów KGlobalAccel (org.kde.kglobalaccel) dla testów skrótów Stream (PT-38).

FakeRegistry naśladuje to, co robi kglobalacceld (kglobalaccel 6.x, globalshortcut.cpp i kglobalacceld.cpp):
klawisz zajęty przez inną akcję nie zostaje przypisany (na jego miejscu jest pusty klawisz, w D-Bus 0),
setForeignShortcut ustawia klawisze cudzej akcji z tą samą kontrolą, unregister usuwa akcję. Stan rejestru
z dzisiejszego kglobalshortcutsrc (odczyt 28.10.2026): Meta+B i Battery ma „Przełącz profil zasilania”,
Pause i Meta+Pause są wolne.

Uruchomiony jako program (osobny proces, jak kglobalacceld w KWin) wystawia ten rejestr na prywatnej
magistrali ($TIDE_FAKE_ACCEL_BUS) pod nazwą org.kde.kglobalaccel z tymi samymi sygnaturami co prawdziwy
(qdbus6 org.kde.kglobalaccel /kglobalaccel) — sprawdza, że klient usługi przesyła kody klawiszy jako `ai`
i flagi jako `u`. Test steruje nim przez org.tide.Test.KGlobalAccel. Odmawia działania na magistrali sesji
pulpitu ($TIDE_DESKTOP_BUS).
"""
from __future__ import annotations

import copy
import json
import os
import sys

from PyQt6.QtCore import QCoreApplication, QMetaType, QObject, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusArgument, QDBusConnection, QDBusMessage

META = 0x10000000
BATTERY = 0x010000FE  # Qt::Key_Battery
POWER_PROFILE = ["org_kde_powerdevil", "powerProfile", "Zarządzanie energią", "Przełącz profil zasilania"]
PREVIOUS_ACTIVITY = ["plasmashell", "previous activity", "plasmashell", "Przełączaj między aktywnościami (odwrotnie)"]


class FakeRegistry:
    def __init__(self):
        # (komponent, akcja) → {"id": [4 napisy], "keys": [kody], "present": bool}
        self.actions: dict[tuple[str, str], dict] = {}
        self.defaults: dict[tuple[str, str], list[int]] = {}   # domyślne skróty (defaultShortcut)
        self.add(POWER_PROFILE, [BATTERY, META | 0x42], default=True)
        self.add(PREVIOUS_ACTIVITY, [META | 0x02000000 | 0x41])  # Meta+Shift+A, domyślnie brak
        self.calls: list[tuple] = []

    def add(self, action: list[str], keys: list[int], default: bool = False) -> None:
        self.actions[(action[0], action[1])] = {"id": list(action), "keys": list(keys), "present": True}
        if default:
            self.defaults[(action[0], action[1])] = list(keys)

    def default_keys(self, action: list[str]) -> list[int]:
        self.calls.append(("defaultShortcut", tuple(action[:2])))
        return list(self.defaults.get((action[0], action[1]), []))

    def components(self) -> list[list[str]]:
        """allMainComponents: [unikalna nazwa, nazwa przyjazna, "", ""]."""
        seen = {}
        for item in self.actions.values():
            seen.setdefault(item["id"][0], [item["id"][0], item["id"][2] if len(item["id"]) > 2 else "", "", ""])
        return list(seen.values())

    def component_actions(self, component: str) -> list[list[str]]:
        return [list(item["id"]) for (comp, _name), item in self.actions.items() if comp == component]

    def default_owners(self, key: int) -> list[list[str]]:
        """To samo co klient KGlobalAccel.default_owners, na rejestrze w pamięci."""
        return [a for c in self.components() for a in self.component_actions(c[0])
                if a[0] != "tide_stream" and key in self.default_keys(a)]

    def snapshot(self) -> dict:
        """Zawartość rejestru do porównania „dokładnie jak przed Streamem”."""
        return copy.deepcopy({k: v["keys"] for k, v in self.actions.items()})

    def _holder(self, key: int):
        for ident, item in self.actions.items():
            if key and key in item["keys"]:
                return ident
        return None

    # metody jak w org.kde.KGlobalAccel ----------------------------------------------------------

    def owner(self, key: int) -> list[str]:
        self.calls.append(("action", key))
        ident = self._holder(key)
        return list(self.actions[ident]["id"]) if ident else []

    def keys(self, action: list[str]) -> list[int]:
        self.calls.append(("shortcut", tuple(action[:2])))
        item = self.actions.get((action[0], action[1]))
        return [k for k in item["keys"] if k] if item else []

    def _set(self, action: list[str], keys: list[int]) -> list[int]:
        ident = (action[0], action[1])
        item = self.actions.get(ident)
        if item is None:
            return []
        item["keys"] = []
        out = []
        for key in keys:
            holder = self._holder(key)
            out.append(key if key and (holder is None or holder == ident) else 0)
        item["keys"] = out
        return out

    def set_foreign(self, action: list[str], keys: list[int]) -> None:
        self.calls.append(("setForeignShortcut", tuple(action[:2]), tuple(keys)))
        self._set(action, keys)

    def register(self, action: list[str], keys: list[int]) -> list[int]:
        self.calls.append(("register", tuple(action[:2]), tuple(keys)))
        self.actions.setdefault((action[0], action[1]), {"id": list(action), "keys": [], "present": True})
        return self._set(action, keys)

    def unregister(self, component: str, name: str) -> bool:
        self.calls.append(("unregister", component, name))
        return self.actions.pop((component, name), None) is not None

    def writes(self) -> list[tuple]:
        return [c for c in self.calls if c[0] in ("setForeignShortcut", "register", "unregister")]


IFACE = "org.kde.KGlobalAccel"
XML = """<interface name="org.kde.KGlobalAccel">
  <method name="action"><arg type="i" direction="in"/><arg type="as" direction="out"/></method>
  <method name="shortcut"><arg type="as" direction="in"/><arg type="ai" direction="out"/></method>
  <method name="defaultShortcut"><arg type="as" direction="in"/><arg type="ai" direction="out"/></method>
  <method name="allMainComponents"><arg type="aas" direction="out"/></method>
  <method name="allActionsForComponent"><arg type="as" direction="in"/><arg type="aas" direction="out"/></method>
  <method name="setForeignShortcut"><arg type="as" direction="in"/><arg type="ai" direction="in"/></method>
  <method name="doRegister"><arg type="as" direction="in"/></method>
  <method name="setShortcut"><arg type="as" direction="in"/><arg type="ai" direction="in"/>
    <arg type="u" direction="in"/><arg type="ai" direction="out"/></method>
  <method name="unregister"><arg type="s" direction="in"/><arg type="s" direction="in"/>
    <arg type="b" direction="out"/></method>
</interface>"""
TEST_IFACE = "org.tide.Test.KGlobalAccel"
TEST_XML = """<interface name="org.tide.Test.KGlobalAccel">
  <method name="Snapshot"><arg type="s" direction="out"/></method>
  <method name="Calls"><arg type="s" direction="out"/></method>
  <method name="Press"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
</interface>"""


@pyqtClassInfo("D-Bus Interface", IFACE)
@pyqtClassInfo("D-Bus Introspection", XML)
class _Adaptor(QDBusAbstractAdaptor):
    def __init__(self, parent, registry: FakeRegistry):
        super().__init__(parent)
        self.registry = registry

    @pyqtSlot(int, result="QStringList")
    def action(self, key):
        return self.registry.owner(key)

    @pyqtSlot("QStringList", result="QList<int>")
    def shortcut(self, action):
        return self.registry.keys(list(action))

    def _reply_lists(self, message, lists) -> None:
        """Odpowiedź `aas` złożona ręcznie (PyQt6 nie ma typu QList<QStringList> dla wyniku slotu)."""
        arg = QDBusArgument()
        arg.beginArray(QMetaType.Type.QStringList.value)
        for item in lists:
            arg.add([str(x) for x in item], QMetaType.Type.QStringList.value)
        arg.endArray()
        message.setDelayedReply(True)
        QDBusConnection.connectToBus(os.environ["TIDE_FAKE_ACCEL_BUS"], "fake-kglobalaccel").send(
            message.createReply([arg]))

    @pyqtSlot(QDBusMessage)
    def allMainComponents(self, message):
        self._reply_lists(message, self.registry.components())

    @pyqtSlot("QStringList", QDBusMessage)
    def allActionsForComponent(self, component, message):
        self._reply_lists(message, self.registry.component_actions(component[0]))

    @pyqtSlot("QStringList", result="QList<int>")
    def defaultShortcut(self, action):
        return self.registry.default_keys(list(action))

    @pyqtSlot("QStringList", "QList<int>")
    def setForeignShortcut(self, action, keys):
        self.registry.set_foreign(list(action), list(keys))

    @pyqtSlot("QStringList")
    def doRegister(self, action):
        self.registry.calls.append(("doRegister", tuple(action[:2])))
        self.registry.actions.setdefault((action[0], action[1]), {"id": list(action), "keys": [], "present": True})

    @pyqtSlot("QStringList", "QList<int>", "uint", result="QList<int>")
    def setShortcut(self, action, keys, flags):
        self.registry.calls.append(("flags", int(flags)))
        return self.registry.register(list(action), list(keys))

    @pyqtSlot(str, str, result=bool)
    def unregister(self, component, name):
        return self.registry.unregister(component, name)


@pyqtClassInfo("D-Bus Interface", TEST_IFACE)
@pyqtClassInfo("D-Bus Introspection", TEST_XML)
class _TestAdaptor(QDBusAbstractAdaptor):
    """Sterowanie atrapą z testu: stan rejestru, lista wywołań i naciśnięcie klawisza."""

    def __init__(self, parent, registry: FakeRegistry, bus: QDBusConnection):
        super().__init__(parent)
        self.registry = registry
        self.bus = bus

    @pyqtSlot(result=str)
    def Snapshot(self):
        return json.dumps([[list(k), v] for k, v in sorted(self.registry.snapshot().items())])

    @pyqtSlot(result=str)
    def Calls(self):
        return json.dumps(self.registry.calls)

    @pyqtSlot(str, str)
    def Press(self, component, name):
        signal = QDBusMessage.createSignal(f"/component/{component}", "org.kde.kglobalaccel.Component",
                                           "globalShortcutPressed")
        signal.setArguments([component, name, QDBusArgument(0, QMetaType.Type.LongLong.value)])
        self.bus.send(signal)


def main() -> int:
    """Osobny proces na prywatnej magistrali (PrivateBus.spawn) — jak prawdziwy kglobalacceld w KWin."""
    address = os.environ.get("TIDE_FAKE_ACCEL_BUS", "")
    if not address or address == os.environ.get("TIDE_DESKTOP_BUS", "-"):
        print("atrapa KGlobalAccel działa tylko na prywatnej magistrali", file=sys.stderr)
        return 2
    app = QCoreApplication([])
    bus = QDBusConnection.connectToBus(address, "fake-kglobalaccel")
    registry = FakeRegistry()
    root = QObject()
    root.adaptor = _Adaptor(root, registry)
    root.test = _TestAdaptor(root, registry, bus)
    if not bus.registerObject("/kglobalaccel", root) or not bus.registerService("org.kde.kglobalaccel"):
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
