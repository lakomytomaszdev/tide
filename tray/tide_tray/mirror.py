"""Lustro jednego elementu zasobnika: ten sam element z ikoną Tide, kliknięcia i menu idą do oryginału.

Każde lustro ma własne połączenie z magistralą sesji. StatusNotifierWatcher usuwa element, gdy znika
nazwa, która go zarejestrowała, więc zamknięcie połączenia zdejmuje lustro z paska od razu.
Menu (com.canonical.dbusmenu) przekazujemy bez zmian: Gio niesie argumenty i odpowiedzi jako GVariant
razem z typami, więc menu w Plasmie jest dokładnie menu aplikacji.
"""
from __future__ import annotations

from gi.repository import Gio, GLib

from tide_tray import rules

SNI = "org.kde.StatusNotifierItem"
WATCHER = "org.kde.StatusNotifierWatcher"
MENU = "com.canonical.dbusmenu"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
NO_MENU = "/NO_DBUSMENU"

PROPERTY_TYPES = {
    "Category": "s", "Id": "s", "Title": "s", "Status": "s", "WindowId": "i", "IconThemePath": "s",
    "Menu": "o", "ItemIsMenu": "b", "IconName": "s", "IconPixmap": "a(iiay)", "OverlayIconName": "s",
    "OverlayIconPixmap": "a(iiay)", "AttentionIconName": "s", "AttentionIconPixmap": "a(iiay)",
    "AttentionMovieName": "s", "ToolTip": "(sa(iiay)ss)",
}
DEFAULTS = {"Category": "ApplicationStatus", "Id": "", "Title": "", "Status": "Active", "WindowId": 0,
            "IconThemePath": "", "Menu": NO_MENU, "ItemIsMenu": False, "IconName": "", "IconPixmap": [],
            "OverlayIconName": "", "OverlayIconPixmap": [], "AttentionIconName": "", "AttentionIconPixmap": [],
            "AttentionMovieName": "", "ToolTip": ("", [], "", "")}

SNI_XML = """<node><interface name="org.kde.StatusNotifierItem">
%s
<method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
<method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
<method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
<signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewAttentionIcon"/><signal name="NewOverlayIcon"/>
<signal name="NewMenu"/><signal name="NewToolTip"/>
<signal name="NewStatus"><arg name="status" type="s"/></signal>
<signal name="NewIconThemePath"><arg name="icon_theme_path" type="s"/></signal>
</interface></node>""" % "\n".join(f'<property name="{k}" type="{t}" access="read"/>' for k, t in PROPERTY_TYPES.items())

MENU_XML = """<node><interface name="com.canonical.dbusmenu">
<property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
<property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
<method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
<arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
<method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/>
<arg type="a(ia{sv})" direction="out"/></method>
<method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
<method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/>
<arg type="u" direction="in"/></method>
<method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
<method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
<method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
<signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
<signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
<signal name="ItemActivationRequested"><arg type="i"/><arg type="u"/></signal>
</interface></node>"""
MENU_DEFAULTS = {"Version": ("u", 3), "TextDirection": ("s", "ltr"), "Status": ("s", "normal"),
                 "IconThemePath": ("as", [])}

SNI_INFO = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
MENU_INFO = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
TIMEOUT_MS = 5000


def unpack_props(variant: GLib.Variant) -> dict:
    """a{sv} z GetAll → słownik wartości Pythona dla znanych właściwości."""
    out = {}
    for key, value in variant.unpack().items():
        if key in PROPERTY_TYPES:
            out[key] = value
    return out


def tooltip_title(props: dict) -> str:
    tip = props.get("ToolTip")
    if isinstance(tip, (tuple, list)) and len(tip) >= 3 and tip[2]:
        return tip[2]
    return props.get("Title", "") or ""


class Mirror:
    """Odpowiednik elementu `service``path` z ikoną z reguły (albo z obrazkiem oryginału bez reguły)."""

    def __init__(self, bus: Gio.DBusConnection, service: str, path: str, props: dict,
                 rule: rules.Rule | None, key: str, address: str | None = None):
        self.bus = bus
        self.service = service
        self.path = path
        self.rule = rule
        self.key = key
        self.props = dict(props)
        self.menu_path = props.get("Menu") or NO_MENU
        self.menu_props: dict = {}
        self.subscriptions: list[int] = []
        self.closed = False
        address = address or Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        self.conn = Gio.DBusConnection.new_for_address_sync(
            address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        self.registrations = [self.conn.register_object(ITEM_PATH, SNI_INFO, self._item_call, self._item_prop, None)]
        if self.has_menu:
            self.registrations.append(self.conn.register_object(MENU_PATH, MENU_INFO, self._menu_call,
                                                                self._menu_prop, None))
        self._subscribe()
        if self.has_menu:
            self._load_menu_props()

    # ------------------------------------------------------------ stan

    @property
    def name(self) -> str:
        return self.conn.get_unique_name()

    @property
    def has_menu(self) -> bool:
        return bool(self.menu_path) and self.menu_path not in (NO_MENU, "/")

    @property
    def icon(self) -> str:
        if self.rule is None:
            return ""
        return rules.icon_for(self.rule, self.props.get("IconPixmap") or [])

    def value(self, name: str):
        props = self.props
        if name == "Id":
            return rules.mirror_id(self.rule, self.key)
        if name == "Menu":
            return MENU_PATH if self.has_menu else NO_MENU
        if name == "Title":
            return props.get("Title") or tooltip_title(props)
        if self.rule is not None:
            if name == "IconName":
                return self.icon
            if name in ("IconPixmap", "AttentionIconPixmap", "OverlayIconPixmap"):
                return []
            if name in ("AttentionIconName", "OverlayIconName", "AttentionMovieName", "IconThemePath"):
                return ""
        return props.get(name, DEFAULTS[name])

    # ------------------------------------------------------------ rejestracja

    def register(self, done=None) -> None:
        """Zgłasza lustro do StatusNotifierWatcher; done(ok)."""
        def finished(conn, result):
            try:
                conn.call_finish(result)
                ok = True
            except GLib.Error as error:
                print(f"tide-tray: watcher odrzucił lustro {self.key} ({error.message})", flush=True)
                ok = False
            if done:
                done(ok)

        self.conn.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                       GLib.Variant("(s)", (self.name,)), None, Gio.DBusCallFlags.NONE, TIMEOUT_MS, None, finished)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for sub in self.subscriptions:
            self.bus.signal_unsubscribe(sub)
        for reg in self.registrations:
            self.conn.unregister_object(reg)
        self.conn.close_sync(None)

    # ------------------------------------------------------------ sygnały oryginału

    def _subscribe(self) -> None:
        self.subscriptions.append(self.bus.signal_subscribe(
            self.service, SNI, None, self.path, None, Gio.DBusSignalFlags.NONE, self._item_signal))
        if self.has_menu:
            self.subscriptions.append(self.bus.signal_subscribe(
                self.service, MENU, None, self.menu_path, None, Gio.DBusSignalFlags.NONE, self._menu_signal))

    def _item_signal(self, conn, sender, path, iface, signal, params):
        def refreshed(props):
            if self.closed or props is None:
                return
            old_icon = self.icon
            self.props.update(props)
            self._emit(SNI, ITEM_PATH, signal, params if signal in ("NewStatus", "NewIconThemePath") else None)
            if signal != "NewIcon" and self.icon != old_icon:
                self._emit(SNI, ITEM_PATH, "NewIcon", None)
        self.refresh(refreshed)

    def _menu_signal(self, conn, sender, path, iface, signal, params):
        if not self.closed:
            self._emit(MENU, MENU_PATH, signal, params)

    def _emit(self, iface, path, signal, params) -> None:
        try:
            self.conn.emit_signal(None, path, iface, signal, params)
        except GLib.Error as error:
            print(f"tide-tray: nie wysłano {signal} lustra {self.key} ({error.message})", flush=True)

    def refresh(self, done) -> None:
        def finished(conn, result):
            try:
                done(unpack_props(conn.call_finish(result).get_child_value(0)))
            except GLib.Error:
                done(None)
        self.bus.call(self.service, self.path, "org.freedesktop.DBus.Properties", "GetAll",
                      GLib.Variant("(s)", (SNI,)), None, Gio.DBusCallFlags.NONE, TIMEOUT_MS, None, finished)

    def _load_menu_props(self) -> None:
        def finished(conn, result):
            try:
                values = conn.call_finish(result).get_child_value(0)
                for key in MENU_DEFAULTS:
                    value = values.lookup_value(key, None)
                    if value is not None:
                        self.menu_props[key] = value
            except GLib.Error:
                pass
        self.bus.call(self.service, self.menu_path, "org.freedesktop.DBus.Properties", "GetAll",
                      GLib.Variant("(s)", (MENU,)), None, Gio.DBusCallFlags.NONE, TIMEOUT_MS, None, finished)

    # ------------------------------------------------------------ wywołania na lustrze

    def _item_prop(self, conn, sender, path, iface, name):
        return GLib.Variant(PROPERTY_TYPES[name], self.value(name))

    def _item_call(self, conn, sender, path, iface, method, params, invocation):
        self._forward(self.path, SNI, method, params, invocation)

    def _menu_prop(self, conn, sender, path, iface, name):
        if name in self.menu_props:
            return self.menu_props[name]
        kind, value = MENU_DEFAULTS[name]
        return GLib.Variant(kind, value)

    def _menu_call(self, conn, sender, path, iface, method, params, invocation):
        self._forward(self.menu_path, MENU, method, params, invocation)

    def _forward(self, path, iface, method, params, invocation) -> None:
        def finished(conn, result):
            try:
                invocation.return_value(conn.call_finish(result))
            except GLib.Error as error:
                name = Gio.DBusError.get_remote_error(error) or "org.tide.Tray.Failed"
                Gio.DBusError.strip_remote_error(error)
                invocation.return_dbus_error(name, error.message)
        self.bus.call(self.service, path, iface, method, params, None, Gio.DBusCallFlags.NONE, TIMEOUT_MS,
                      None, finished)
