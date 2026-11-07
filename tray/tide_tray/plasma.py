"""Ustawienia zasobnika przez skrypt plasmashell (nie przez edycję appletsrc, jak przy panelach)."""
from __future__ import annotations

import json

from gi.repository import Gio, GLib

SCRIPT = r"""
var tideAdd = %(add)s, tideRemove = %(remove)s, tideOut = [];
function tideList(v) { var out = []; if (v) { for (var i = 0; i < v.length; ++i) { if (v[i]) out.push(String(v[i])); } } return out; }
panels().forEach(function (p) {
  p.widgets("org.kde.plasma.systemtray").forEach(function (w) {
    w.currentConfigGroup = ["General"];
    var disabled = tideList(w.readConfig("disabledStatusNotifiers", []));
    var changed = false;
    tideAdd.forEach(function (k) { if (disabled.indexOf(k) < 0) { disabled.push(k); changed = true; } });
    tideRemove.forEach(function (k) { var i = disabled.indexOf(k); if (i >= 0) { disabled.splice(i, 1); changed = true; } });
    if (changed) { w.writeConfig("disabledStatusNotifiers", disabled); }
    tideOut.push({tray: w.id, disabled: disabled, shown: tideList(w.readConfig("shownItems", [])),
                  hidden: tideList(w.readConfig("hiddenItems", [])), showAll: !!w.readConfig("showAllItems", false)});
  });
});
print(JSON.stringify(tideOut));
"""


def script(add: list[str] = (), remove: list[str] = ()) -> str:
    return SCRIPT % {"add": json.dumps(list(add)), "remove": json.dumps(list(remove))}


def parse(output: str) -> list[dict]:
    """Wynik skryptu: lista zasobników z ich listami; pusta, gdy nie ma żadnego zasobnika."""
    line = output.strip().splitlines()[-1] if output.strip() else "[]"
    trays = json.loads(line)
    if not isinstance(trays, list):
        raise ValueError("nieoczekiwany wynik skryptu Plasmy")
    return trays


class TrayConfig:
    """Dopisuje i zdejmuje klucze z disabledStatusNotifiers we wszystkich zasobnikach."""

    def __init__(self, bus: Gio.DBusConnection, service: str = "org.kde.plasmashell"):
        self.bus = bus
        self.service = service

    def apply(self, add=(), remove=(), done=None):
        """Asynchronicznie; done(trays) albo done(None) przy błędzie."""
        def finished(conn, result):
            try:
                reply = conn.call_finish(result)
                trays = parse(reply.unpack()[0])
            except (GLib.Error, ValueError) as error:
                print(f"tide-tray: ustawienia zasobnika niedostępne ({error})", flush=True)
                trays = None
            if done:
                done(trays)

        self.bus.call(self.service, "/PlasmaShell", "org.kde.PlasmaShell", "evaluateScript",
                      GLib.Variant("(s)", (script(add, remove),)), GLib.VariantType("(s)"),
                      Gio.DBusCallFlags.NONE, 10000, None, finished)

    def apply_sync(self, add=(), remove=(), timeout_ms: int = 10000) -> list[dict] | None:
        try:
            reply = self.bus.call_sync(self.service, "/PlasmaShell", "org.kde.PlasmaShell", "evaluateScript",
                                       GLib.Variant("(s)", (script(add, remove),)), GLib.VariantType("(s)"),
                                       Gio.DBusCallFlags.NONE, timeout_ms, None)
            return parse(reply.unpack()[0])
        except (GLib.Error, ValueError) as error:
            print(f"tide-tray: ustawienia zasobnika niedostępne ({error})", flush=True)
            return None


def disabled_everywhere(trays: list[dict] | None, key: str) -> bool:
    return bool(trays) and all(key in t["disabled"] for t in trays)


def forced_shown(trays: list[dict] | None, key: str) -> bool:
    """Element wymuszony do pokazania — wyłączenie go nie schowa (calculateEffectiveStatus)."""
    return bool(trays) and any(t["showAll"] or key in t["shown"] for t in trays)


def hidden_somewhere(trays: list[dict] | None, key: str) -> bool:
    return bool(trays) and any(key in t["hidden"] for t in trays)
