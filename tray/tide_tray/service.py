"""Usługa tide-tray: pilnuje elementów zasobnika i wystawia lustra z kafelkami Tide.

Kolejność jest taka, żeby na pasku nigdy nie zostały dwie ikony jednej aplikacji:
1. lustro rejestruje się w StatusNotifierWatcher,
2. dopiero potem klucz oryginału trafia do disabledStatusNotifiers (Plasma przestaje go pokazywać,
   także pod strzałką),
3. jeśli Plasma nie potwierdzi wyłączenia, lustro znika i zostaje sam oryginał.
Przy zatrzymaniu usługi lustra znikają, a klucze, które wyłączyła usługa, wracają — aplikacje znowu
pokazują swoje obrazki. Klucze wyłączone wcześniej przez użytkownika zostają nietknięte.
"""
from __future__ import annotations

import signal
from pathlib import Path

from gi.repository import Gio, GLib

from tide_tray import plasma, rules
from tide_tray.mirror import Mirror, WATCHER, SNI, tooltip_title, unpack_props
from tide_tray.state import Owned

DBUS = "org.freedesktop.DBus"
DEFAULT_PATH = "/StatusNotifierItem"
# Electron ustawia podpowiedź chwilę po zgłoszeniu elementu — bez niej reguła po tytule nie zadziała.
RETRIES = 5
RETRY_MS = 1000


def split_item(item: str) -> tuple[str, str]:
    """`:1.83/StatusNotifierItem` albo sama nazwa usługi → (usługa, ścieżka)."""
    if "/" in item:
        service, _, rest = item.partition("/")
        return service, "/" + rest
    return item, DEFAULT_PATH


class Service:
    def __init__(self, rules_path: Path, bus: Gio.DBusConnection | None = None, owned: Owned | None = None,
                 config: plasma.TrayConfig | None = None, address: str | None = None, proc: Path = Path("/proc")):
        self.bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.address = address
        self.rules = rules.load(rules_path)
        self.owned = owned or Owned()
        self.config = config or plasma.TrayConfig(self.bus)
        self.proc = proc
        self.mirrors: dict[str, Mirror] = {}  # klucz: "usługa ścieżka" oryginału
        self.pending: set[str] = set()
        self.subscriptions: list[int] = []
        self.loop = GLib.MainLoop()

    # ------------------------------------------------------------ start i stop

    def start(self) -> None:
        self.subscriptions = [
            self.bus.signal_subscribe(WATCHER, WATCHER, "StatusNotifierItemRegistered", "/StatusNotifierWatcher",
                                      None, Gio.DBusSignalFlags.NONE, self._registered),
            self.bus.signal_subscribe(WATCHER, WATCHER, "StatusNotifierItemUnregistered", "/StatusNotifierWatcher",
                                      None, Gio.DBusSignalFlags.NONE, self._unregistered),
            self.bus.signal_subscribe(DBUS, DBUS, "NameOwnerChanged", "/org/freedesktop/DBus", None,
                                      Gio.DBusSignalFlags.NONE, self._owner_changed),
        ]
        self.scan()

    def run(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._quit)
        self.start()
        self.loop.run()

    def _quit(self) -> bool:
        self.stop()
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def stop(self) -> None:
        """Oddaje oryginały: zdejmuje swoje klucze, potem zamyka lustra (bez chwili z pustym paskiem)."""
        for sub in self.subscriptions:
            self.bus.signal_unsubscribe(sub)
        self.subscriptions = []
        release(self.config, self.owned)
        for mirror in list(self.mirrors.values()):
            mirror.close()
        self.mirrors.clear()

    # ------------------------------------------------------------ elementy

    def scan(self) -> None:
        def finished(conn, result):
            try:
                items = conn.call_finish(result).unpack()[0]
            except GLib.Error as error:
                print(f"tide-tray: brak StatusNotifierWatcher ({error.message})", flush=True)
                return
            for item in items:
                self.consider(item)
        self.bus.call(WATCHER, "/StatusNotifierWatcher", "org.freedesktop.DBus.Properties", "Get",
                      GLib.Variant("(ss)", (WATCHER, "RegisteredStatusNotifierItems")), None,
                      Gio.DBusCallFlags.NONE, 5000, None, finished)

    def own_names(self) -> set[str]:
        return {m.name for m in self.mirrors.values()}

    def consider(self, item: str, attempt: int = 0) -> None:
        service, path = split_item(item)
        ident = f"{service} {path}"
        if ident in self.mirrors or ident in self.pending or service in self.own_names():
            return
        self.pending.add(ident)

        def got_props(conn, result):
            try:
                props = unpack_props(conn.call_finish(result).get_child_value(0))
            except GLib.Error:
                self.pending.discard(ident)
                return
            item_id = props.get("Id", "")
            if item_id.startswith("tide-tray-"):
                self.pending.discard(ident)
                return
            key = rules.plasma_key(item_id, self.process_of(service))
            rule = rules.match(self.rules, item_id, tooltip_title(props))
            if rule is None and key not in self.owned.keys:
                self.pending.discard(ident)
                if item_id.startswith(rules.CHROMIUM_PREFIX) and not tooltip_title(props) and attempt < RETRIES:
                    GLib.timeout_add(RETRY_MS, lambda: self.consider(item, attempt + 1) or GLib.SOURCE_REMOVE)
                return
            self.mirror(ident, service, path, props, rule, key)

        self.bus.call(service, path, "org.freedesktop.DBus.Properties", "GetAll", GLib.Variant("(s)", (SNI,)),
                      None, Gio.DBusCallFlags.NONE, 5000, None, got_props)

    def process_of(self, service: str) -> str:
        try:
            reply = self.bus.call_sync(DBUS, "/org/freedesktop/DBus", DBUS, "GetConnectionUnixProcessID",
                                       GLib.Variant("(s)", (service,)), None, Gio.DBusCallFlags.NONE, 2000, None)
            pid = reply.unpack()[0]
            base = self.proc / str(pid)
            return rules.process_name((base / "cmdline").read_bytes(), (base / "comm").read_text())
        except (GLib.Error, OSError):
            return ""

    def mirror(self, ident: str, service: str, path: str, props: dict, rule, key: str) -> None:
        trays = self.config.apply_sync()
        if trays is None:
            self.pending.discard(ident)
            return
        user_disabled = plasma.disabled_everywhere(trays, key) and key not in self.owned.keys
        if user_disabled or plasma.forced_shown(trays, key):
            # Użytkownik sam wyłączył albo wymusił pokazanie — wtedy lustro dałoby dwie ikony albo żadnej.
            print(f"tide-tray: {key} zostaje bez lustra (ustawienia użytkownika)", flush=True)
            self.pending.discard(ident)
            return
        mirror = Mirror(self.bus, service, path, props, rule, key, self.address)

        def registered(ok):
            if not ok:
                mirror.close()
                self.pending.discard(ident)
                return
            self.mirrors[ident] = mirror
            self.pending.discard(ident)
            if key in self.owned.keys and plasma.disabled_everywhere(trays, key):
                return

            def disabled(result):
                if plasma.disabled_everywhere(result, key):
                    print(f"tide-tray: lustro {mirror.value('Id')} za {key}", flush=True)
                else:
                    print(f"tide-tray: Plasma nie wyłączyła {key} — zostaje oryginał", flush=True)
                    if not any(m.key == key for i, m in self.mirrors.items() if i != ident):
                        self.owned.discard(key)
                    self.drop(ident)
            # Klucz jest „nasz” jeszcze przed odpowiedzią Plasmy: zatrzymanie usługi w tej chwili też go odda.
            self.owned.add(key)
            self.config.apply(add=[key], done=disabled)

        mirror.register(registered)

    def drop(self, ident: str) -> None:
        mirror = self.mirrors.pop(ident, None)
        if mirror is not None:
            mirror.close()

    # ------------------------------------------------------------ sygnały

    def _registered(self, conn, sender, path, iface, signal_name, params):
        self.consider(params.unpack()[0])

    def _unregistered(self, conn, sender, path, iface, signal_name, params):
        service, item_path = split_item(params.unpack()[0])
        self.drop(f"{service} {item_path}")

    def _owner_changed(self, conn, sender, path, iface, signal_name, params):
        name, old, new = params.unpack()
        if name == WATCHER and new:
            # Nowy watcher (restart kded albo plasmashell) nie zna luster — zgłaszamy je jeszcze raz.
            for mirror in self.mirrors.values():
                mirror.register()
            self.scan()
            return
        if new:
            return
        gone = [ident for ident, m in self.mirrors.items() if m.service in (name, old) or ident.split(" ")[0] == name]
        for ident in gone:
            self.drop(ident)


def release(config: plasma.TrayConfig, owned: Owned) -> bool:
    """Zdejmuje klucze wyłączone przez tide-tray; True, gdy Plasma to potwierdziła."""
    if not owned.keys:
        return True
    # Krótko: przy wylogowaniu plasmashell może już kończyć pracę, a klucze i tak zostają w pliku stanu.
    result = config.apply_sync(remove=sorted(owned.keys), timeout_ms=3000)
    if result is None:
        return False
    still = {k for k in owned.keys if any(k in t["disabled"] for t in result)}
    owned.keys = still
    owned.save()
    return not still
