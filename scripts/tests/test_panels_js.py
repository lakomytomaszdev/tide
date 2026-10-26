"""Skrypty paneli (scripts/panels/*.js) na atrapie API plasmashell uruchamianej w qml6."""
import json
import unittest
from pathlib import Path

from scripts import panels
from scripts.tests import fake_plasmashell as fake

PALETTE = json.loads((Path(__file__).resolve().parents[2] / "theme/palette/mist.json").read_text())
MARKER = "TIDE_JSON:"


def result(out):
    return json.loads(out[out.rfind(MARKER) + len(MARKER):])


def live(state):
    return [p for p in state["panels"] if not p["removed"]]


def types(panel_state):
    by_id = {w["id"]: w for w in panel_state["widgets"]}
    return [by_id[i]["type"].rsplit(".", 1)[-1] for i in panel_state["layout"]]


def find(state, pid):
    return next(p for p in state["panels"] if p["id"] == pid)


def config(panel_state, type_, group):
    widget = next(w for w in panel_state["widgets"] if w["type"] == type_ and not w["removed"])
    return widget["config"].get(group, {})


@unittest.skipUnless(fake.available(), "Brak qml6 do uruchomienia skryptów")
class PanelScriptsTest(unittest.TestCase):
    def install(self, state, installed=None):
        out, state = fake.run(state, panels.script("install", panels.install_params(PALETTE, installed or {})))
        res = result(out)
        out, state = fake.run(state, panels.script("order", panels.order_params(res)))
        return res, state

    def test_main_bar_becomes_the_tide_top_bar(self):
        res, state = self.install(fake.desktop_like_this_one())
        bar = find(state, 73)
        self.assertEqual(res["main"], 73)
        self.assertEqual(types(bar), ["kickoff", "appmenu", "panelspacer", "digitalclock", "panelspacer", "systemtray"])
        self.assertEqual(bar["height"], PALETTE["size"]["bar"])
        self.assertTrue(bar["floating"])
        self.assertEqual(config(bar, "org.kde.plasma.kickoff", "/General")["icon"], "tide-logo")
        self.assertEqual(config(bar, "org.kde.plasma.digitalclock", "/Appearance")["showDate"], "true")
        self.assertEqual(config(bar, "org.kde.plasma.appmenu", "/Appearance")["allScreens"], "false")
        # Zegar i zasobnik to te same widżety co wcześniej, z zachowaną konfiguracją.
        self.assertIn("/Applets/89", next(w for w in bar["widgets"] if w["id"] == 78)["config"])
        self.assertEqual(types(find(state, 90)), ["icontasks", "widget"])

    def test_left_monitor_gets_a_slim_marked_bar(self):
        res, state = self.install(fake.desktop_like_this_one())
        side = find(state, res["side"])
        self.assertEqual(side["screen"], 1)
        self.assertEqual(side["location"], "top")
        self.assertEqual(side["height"], PALETTE["size"]["barSlim"])
        self.assertEqual(side["config"]["/Tide"]["role"], "side-bar")
        self.assertEqual(types(side), ["appmenu", "panelspacer", "digitalclock", "panelspacer"])
        self.assertEqual(config(side, "org.kde.plasma.digitalclock", "/Appearance")["showDate"], "true")

    def test_screens_are_found_by_connector_not_by_number(self):
        res, state = self.install(fake.desktop_like_this_one(screens=("HDMI-A-1", "DP-2")))
        self.assertEqual(res["screens"], {"count": 2, "main": 1, "side": 0})
        self.assertEqual(find(state, res["side"])["screen"], 0)

    def test_reinstall_reuses_the_left_bar(self):
        res, state = self.install(fake.desktop_like_this_one())
        res2, state = self.install(state, {"mainPanel": 73, "sidePanel": res["side"]})
        self.assertEqual(res2["side"], res["side"])
        self.assertEqual(len(live(state)), 3)
        self.assertEqual(types(find(state, 73)), ["kickoff", "appmenu", "panelspacer", "digitalclock",
                                                  "panelspacer", "systemtray"])

    def test_single_monitor_gets_no_left_bar(self):
        res, state = self.install(fake.desktop_like_this_one(screens=("DP-2",)))
        self.assertIsNone(res["side"])
        self.assertEqual(len(live(state)), 2)

    def test_uninstall_restores_the_previous_layout(self):
        state = fake.desktop_like_this_one()
        out, state = fake.run(state, panels.script("dump", panels.screens()))
        previous = result(out)
        res, state = self.install(state)
        params = {**panels.screens(), "previous": previous, "mainPanel": 73, "mainCreated": False,
                  "changedKeys": panels.CHANGED_KEYS, "defaults": panels.DEFAULTS}
        out, state = fake.run(state, panels.script("uninstall", params))
        undo = result(out)
        out, state = fake.run(state, panels.script("order", {"panels": [{"id": 73, "order": undo["mainOrder"]}]}))

        self.assertIn(res["side"], undo["removed"])
        self.assertEqual([p["id"] for p in live(state)], [73, 90])
        bar = find(state, 73)
        self.assertEqual(types(bar), ["kickoff", "icon", "panelspacer", "digitalclock", "panelspacer", "systemtray"])
        self.assertEqual(bar["height"], 36)
        self.assertEqual(config(bar, "org.kde.plasma.kickoff", "/General")["icon"], "go-home-large-symbolic")
        icon = config(bar, "org.kde.plasma.icon", "/")
        self.assertEqual(icon["url"], "file:///tmp/index.html")
        # Zachowane widżety to te same obiekty (ten sam identyfikator).
        self.assertEqual([i for i in bar["layout"] if i in (74, 76, 78)], [74, 76, 78])

    def test_dump_reads_widget_config_in_panel_order(self):
        out, _ = fake.run(fake.desktop_like_this_one(), panels.script("dump", panels.screens()))
        top = next(p for p in result(out)["panels"] if p["id"] == 73)
        self.assertEqual([w["id"] for w in top["widgets"]], [74, 96, 75, 76, 77, 78])
        self.assertEqual(top["widgets"][0]["config"]["/General"]["icon"], "go-home-large-symbolic")
        # Konfiguracja widżetów potomnych zasobnika nie trafia do zrzutu panelu.
        self.assertFalse(any(k.startswith("/Applets") for k in top["widgets"][5]["config"]))


if __name__ == "__main__":
    unittest.main()
