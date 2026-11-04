"""tide-tray na prywatnej magistrali: atrapy StatusNotifierWatcher, plasmashell i aplikacji z menu."""
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from gi.repository import Gio, GLib

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from tide_tray import plasma, rules  # noqa: E402
from tide_tray.mirror import SNI_INFO, MENU_INFO, WATCHER  # noqa: E402
from tide_tray.service import Service  # noqa: E402
from tide_tray.state import Owned  # noqa: E402

RULES = HERE.parent.parent / "theme" / "icons" / "tray.json"
TEST_BUS = None


HOST = None


class Host(threading.Thread):
    """Wątek z własną pętlą GLib dla atrap — jak osobne procesy, więc wywołania synchroniczne
    usługi (skrypt plasmashell) nie czekają na samych siebie."""

    def __init__(self):
        super().__init__(daemon=True)
        self.ctx = GLib.MainContext.new()
        self.loop = GLib.MainLoop(self.ctx)
        self.ready = threading.Event()

    def run(self):
        self.ctx.push_thread_default()
        self.ready.set()
        self.loop.run()

    def call(self, fn, *args):
        done = threading.Event()
        box = {}

        def job(*_):
            try:
                box["value"] = fn(*args)
            except Exception as error:  # noqa: BLE001
                box["error"] = error
            done.set()
            return GLib.SOURCE_REMOVE
        source = GLib.idle_source_new()
        source.set_callback(job)
        source.attach(self.ctx)
        done.wait(5)
        if "error" in box:
            raise box["error"]
        return box.get("value")


def setUpModule():
    global TEST_BUS, HOST
    TEST_BUS = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
    TEST_BUS.up()
    HOST = Host()
    HOST.start()
    HOST.ready.wait(5)


def tearDownModule():
    HOST.loop.quit()
    TEST_BUS.down()


def connect() -> Gio.DBusConnection:
    return Gio.DBusConnection.new_for_address_sync(
        TEST_BUS.get_bus_address(),
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


def spin(until, timeout=3.0) -> bool:
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        if until():
            return True
        time.sleep(0.01)
    return until()


def own(conn, name):
    conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                   GLib.Variant("(su)", (name, 4)), None, Gio.DBusCallFlags.NONE, 2000, None)


WATCHER_XML = """<node><interface name="org.kde.StatusNotifierWatcher">
<method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
<property name="RegisteredStatusNotifierItems" type="as" access="read"/>
<signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
<signal name="StatusNotifierItemUnregistered"><arg type="s"/></signal>
</interface></node>"""
SHELL_XML = """<node><interface name="org.kde.PlasmaShell">
<method name="evaluateScript"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
</interface></node>"""


class FakeWatcher:
    def __init__(self):
        self.conn = connect()
        self.items: list[str] = []
        info = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0]
        self.conn.register_object("/StatusNotifierWatcher", info, self.call, self.prop, None)
        own(self.conn, WATCHER)
        self.conn.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged", None, None,
                                   Gio.DBusSignalFlags.NONE, self.owner_changed)

    def call(self, conn, sender, path, iface, method, params, inv):
        arg = params.unpack()[0]
        item = f"{sender}{arg}" if arg.startswith("/") else (f"{arg}/StatusNotifierItem" if "/" not in arg else arg)
        if item not in self.items:
            self.items.append(item)
            conn.emit_signal(None, path, iface, "StatusNotifierItemRegistered", GLib.Variant("(s)", (item,)))
        inv.return_value(None)

    def prop(self, conn, sender, path, iface, name):
        return GLib.Variant("as", self.items)

    def owner_changed(self, conn, sender, path, iface, signal, params):
        name, old, new = params.unpack()
        if new:
            return
        for item in [i for i in self.items if i.split("/")[0] in (name, old)]:
            self.items.remove(item)
            if not self.conn.is_closed():
                self.conn.emit_signal(None, "/StatusNotifierWatcher", WATCHER, "StatusNotifierItemUnregistered",
                                      GLib.Variant("(s)", (item,)))


class FakeShell:
    """plasmashell: wykonuje tylko to, co robi skrypt tide-tray (dopisz/zdejmij klucze)."""

    def __init__(self, disabled=(), shown=(), refuse=False):
        self.conn = connect()
        self.tray = {"tray": 1, "disabled": list(disabled), "shown": list(shown), "hidden": [], "showAll": False}
        self.refuse = refuse
        self.log: list[tuple[list, list]] = []
        info = Gio.DBusNodeInfo.new_for_xml(SHELL_XML).interfaces[0]
        self.conn.register_object("/PlasmaShell", info, self.call, None, None)
        own(self.conn, "org.kde.plasmashell")

    def call(self, conn, sender, path, iface, method, params, inv):
        source = params.unpack()[0]
        add = json.loads(re.search(r"var tideAdd = (\[.*?\])", source).group(1))
        remove = json.loads(re.search(r"tideRemove = (\[.*?\])", source).group(1))
        self.log.append((add, remove))
        if not self.refuse:
            for key in add:
                if key not in self.tray["disabled"]:
                    self.tray["disabled"].append(key)
        for key in remove:
            if key in self.tray["disabled"]:
                self.tray["disabled"].remove(key)
        inv.return_value(GLib.Variant("(s)", (json.dumps([self.tray]),)))


def pixmap(rgb) -> list:
    r, g, b = rgb
    return [(4, 4, bytes([255, r, g, b]) * 16)]


class FakeApp:
    """Aplikacja Electron w zasobniku: Id chrome_status_icon_1, menu dbusmenu, liczy kliknięcia."""

    LAYOUT = (7, (0, {}, [GLib.Variant("(ia{sv}av)", (1, {"label": GLib.Variant("s", "Otwórz")}, []))]))

    def __init__(self, title="ChatGPT", item_id="chrome_status_icon_1", color=(240, 240, 240)):
        self.conn = connect()
        self.props = {"Id": item_id, "Category": "ApplicationStatus", "Status": "Active", "Title": "",
                      "IconPixmap": pixmap(color), "ToolTip": ("", [], title, ""), "Menu": "/MenuBar"}
        self.activated: list[tuple[int, int]] = []
        self.conn.register_object("/StatusNotifierItem", SNI_INFO, self.item_call, self.item_prop, None)
        self.conn.register_object("/MenuBar", MENU_INFO, self.menu_call, None, None)
        self.conn.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                       GLib.Variant("(s)", (self.conn.get_unique_name(),)), None, Gio.DBusCallFlags.NONE,
                       2000, None, None)

    def item_prop(self, conn, sender, path, iface, name):
        from tide_tray.mirror import DEFAULTS, PROPERTY_TYPES
        return GLib.Variant(PROPERTY_TYPES[name], self.props.get(name, DEFAULTS[name]))

    def item_call(self, conn, sender, path, iface, method, params, inv):
        if method == "Activate":
            self.activated.append(params.unpack())
        inv.return_value(None)

    def menu_call(self, conn, sender, path, iface, method, params, inv):
        if method == "GetLayout":
            inv.return_value(GLib.Variant("(u(ia{sv}av))", self.LAYOUT))
        else:
            inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def recolor(self, rgb):
        self.props["IconPixmap"] = pixmap(rgb)
        self.conn.emit_signal(None, "/StatusNotifierItem", "org.kde.StatusNotifierItem", "NewIcon", None)

    def quit(self):
        self.conn.close_sync(None)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.watcher = HOST.call(FakeWatcher)
        self.tmp = tempfile.TemporaryDirectory()
        self.owned = Owned(Path(self.tmp.name) / "tray-mirror.json")
        self.bus = connect()

    def tearDown(self):
        if getattr(self, "service", None):
            self.service.stop()
        for obj in ("app", "shell"):
            if getattr(self, obj, None):
                try:
                    getattr(self, obj).conn.close_sync(None)
                except GLib.Error:
                    pass
        self.watcher.conn.close_sync(None)
        spin(lambda: False, 0.1)
        self.bus.close_sync(None)
        self.tmp.cleanup()

    def start(self, **shell):
        self.shell = HOST.call(lambda: FakeShell(**shell))
        self.service = Service(RULES, bus=self.bus, owned=self.owned, config=plasma.TrayConfig(self.bus),
                               address=TEST_BUS.get_bus_address())
        self.service.start()

    def key(self, item_id="chrome_status_icon_1"):
        import os
        base = Path("/proc") / str(os.getpid())
        return rules.plasma_key(item_id, rules.process_name((base / "cmdline").read_bytes(), (base / "comm").read_text()))

    def call(self, service, path, iface, method, args):
        """Wywołanie z kręceniem pętli — obiekty lustra obsługuje ten sam wątek co test."""
        box = {}
        self.bus.call(service, path, iface, method, args, None, Gio.DBusCallFlags.NONE, 2000, None,
                      lambda conn, res: box.update(reply=conn.call_finish(res)))
        self.assertTrue(spin(lambda: "reply" in box))
        return box["reply"].unpack()

    def mirror_prop(self, name):
        mirror = next(iter(self.service.mirrors.values()))
        return self.call(mirror.name, "/StatusNotifierItem", "org.freedesktop.DBus.Properties", "Get",
                         GLib.Variant("(ss)", ("org.kde.StatusNotifierItem", name)))[0]

    def test_mirror_registers_before_the_original_is_disabled(self):
        self.start()
        self.app = HOST.call(lambda: FakeApp())
        self.assertTrue(spin(lambda: self.key() in self.shell.tray["disabled"]))
        self.assertEqual(len(self.watcher.items), 2)  # oryginał (wyłączony w Plasmie) i lustro
        self.assertEqual(self.mirror_prop("IconName"), "tide-tray-chatgpt")
        self.assertEqual(self.mirror_prop("Id"), "tide-tray-chatgpt")
        self.assertEqual(self.mirror_prop("IconPixmap"), [])
        self.assertIn(self.key(), self.owned.keys)

    def test_menu_and_clicks_reach_the_application(self):
        self.start()
        self.app = HOST.call(lambda: FakeApp())
        self.assertTrue(spin(lambda: self.service.mirrors))
        mirror = next(iter(self.service.mirrors.values()))
        layout = self.call(mirror.name, "/MenuBar", "com.canonical.dbusmenu", "GetLayout",
                           GLib.Variant("(iias)", (0, -1, [])))
        self.assertEqual(layout[1][2][0][1]["label"], "Otwórz")
        self.call(mirror.name, "/StatusNotifierItem", "org.kde.StatusNotifierItem", "Activate",
                  GLib.Variant("(ii)", (10, 20)))
        self.assertTrue(spin(lambda: self.app.activated == [(10, 20)]))

    def test_state_follows_the_application_picture(self):
        self.start()
        self.app = HOST.call(lambda: FakeApp(title="Discord"))
        self.assertTrue(spin(lambda: self.service.mirrors))
        self.assertEqual(self.mirror_prop("IconName"), "tide-tray-discord")
        HOST.call(self.app.recolor, (230, 40, 40))
        self.assertTrue(spin(lambda: self.mirror_prop("IconName") == "tide-tray-discord-attention"))
        HOST.call(self.app.recolor, (40, 200, 90))
        self.assertTrue(spin(lambda: self.mirror_prop("IconName") == "tide-tray-discord-voice"))

    def test_mirror_leaves_with_the_application(self):
        self.start()
        self.app = HOST.call(lambda: FakeApp())
        self.assertTrue(spin(lambda: self.service.mirrors))
        HOST.call(self.app.quit)
        self.assertTrue(spin(lambda: not self.service.mirrors and not self.watcher.items))

    def test_stop_gives_the_originals_back(self):
        self.start()
        self.app = HOST.call(lambda: FakeApp())
        self.assertTrue(spin(lambda: self.key() in self.shell.tray["disabled"]))
        self.service.stop()
        self.service = None
        self.assertNotIn(self.key(), self.shell.tray["disabled"])
        self.assertFalse(self.owned.keys)
        self.assertTrue(spin(lambda: len(self.watcher.items) == 1))

    def test_user_disabled_application_stays_hidden_without_mirror(self):
        self.start(disabled=[self.key()])
        self.app = HOST.call(lambda: FakeApp())
        spin(lambda: False, 0.5)
        self.assertFalse(self.service.mirrors)
        self.service.stop()
        self.service = None
        self.assertIn(self.key(), self.shell.tray["disabled"])  # nie oddajemy cudzego wyłączenia

    def test_forced_shown_application_gets_no_mirror(self):
        self.start(shown=[self.key()])
        self.app = HOST.call(lambda: FakeApp())
        spin(lambda: False, 0.5)
        self.assertFalse(self.service.mirrors)
        self.assertEqual(self.shell.tray["disabled"], [])

    def test_when_plasma_does_not_disable_the_mirror_goes_away(self):
        self.start(refuse=True)
        self.app = HOST.call(lambda: FakeApp())
        self.assertTrue(spin(lambda: self.shell.log and not self.service.mirrors and len(self.watcher.items) == 1))

    def test_unknown_application_sharing_the_key_is_mirrored_with_its_own_picture(self):
        self.start()
        self.app = HOST.call(lambda: FakeApp())
        self.assertTrue(spin(lambda: self.key() in self.shell.tray["disabled"]))
        other = HOST.call(lambda: FakeApp(title="Slack"))
        try:
            self.assertTrue(spin(lambda: len(self.service.mirrors) == 2))
            mirror = [m for m in self.service.mirrors.values() if m.service == other.conn.get_unique_name()][0]
            self.assertIsNone(mirror.rule)
            (w, h, data), = mirror.value("IconPixmap")
            self.assertEqual((w, h, bytes(data)), other.props["IconPixmap"][0])
        finally:
            HOST.call(other.quit)

    def test_unrelated_items_are_left_alone(self):
        self.start()
        self.app = HOST.call(lambda: FakeApp(title="Coś", item_id="org.example.app"))
        spin(lambda: False, 0.4)
        self.assertFalse(self.service.mirrors)
        self.assertEqual(self.shell.log, [])


class RulesTest(unittest.TestCase):
    def test_key_matches_plasma(self):
        self.assertEqual(rules.plasma_key("chrome_status_icon_1", "ChatGPT"), "chrome_status_icon_1@ChatGPT")
        self.assertEqual(rules.plasma_key("chrome_status_icon_1", ""), "chrome_status_icon_1")
        self.assertEqual(rules.plasma_key("obs", "obs"), "obs")
        self.assertEqual(rules.plasma_key("dropbox-client-123", "x"), "dropbox-client-PID")

    def test_process_name_prefers_the_asar_directory(self):
        self.assertEqual(rules.process_name(b"/usr/bin/electron\0/usr/lib/vesktop/app.asar\0", "electron"), "vesktop")
        self.assertEqual(rules.process_name(b"/usr/lib/chatgpt/ChatGPT\0", "ChatGPT\n"), "ChatGPT")

    def test_colors(self):
        self.assertEqual(rules.colors(pixmap((240, 240, 240))), set())
        self.assertEqual(rules.colors(pixmap((230, 40, 40))), {"red"})
        self.assertEqual(rules.colors(pixmap((40, 200, 90))), {"green"})
        self.assertEqual(rules.colors([]), set())


if __name__ == "__main__":
    unittest.main()
