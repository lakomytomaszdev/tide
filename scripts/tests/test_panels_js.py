"""Skrypty paneli (scripts/panels/*.js) na atrapie API plasmashell uruchamianej w qml6."""
import json
import unittest
from unittest import mock
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
        # Skrót użytkownika (widżet „Ikona”) zostaje, tuż przed zasobnikiem.
        self.assertEqual(types(bar), ["kickoff", "appmenu", "panelspacer", "digitalclock", "panelspacer", "icon",
                                      "systemtray"])
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
                                                  "panelspacer", "icon", "systemtray"])

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


@unittest.skipUnless(fake.available(), "Brak qml6 do uruchomienia skryptów")
class DockScriptTest(unittest.TestCase):
    LEFT = ["org.tide.system"]
    WIDGETS = ["org.tide.agents", "org.tide.worktrees", "org.tide.limits"]

    def install(self, state, widgets=None, left=None, retired=None):
        from scripts import dock
        params = {**panels.screens(), "left": self.LEFT if left is None else left,
                  "widgets": self.WIDGETS if widgets is None else widgets,
                  "dockHeight": PALETTE["size"]["dock"]}
        params["retired"] = dock.REMOVABLE if retired is None else retired
        out, state = fake.run(state, panels.script("dock", params))
        res = result(out)
        out, state = fake.run(state, panels.script("order", {**panels.screens(),
                                                             "panels": [{"id": res["dock"], "order": res["order"]}]}))
        return res, state

    def test_dock_centres_apps_system_on_the_left_ai_on_the_right(self):
        res, state = self.install(fake.desktop_like_this_one())
        dock = find(state, 90)
        self.assertEqual(types(dock), ["system", "panelspacer", "icontasks", "panelspacer", "agents", "worktrees",
                                       "limits"])
        self.assertEqual((dock["lengthMode"], dock["height"]), ("fill", PALETTE["size"]["dock"]))
        self.assertIn(config(dock, "org.kde.plasma.icontasks", "/General")["fill"], (False, "false"))

    def test_reinstall_moves_system_from_the_right_to_the_left(self):
        # Dock po dawnej instalacji: wszystkie kafelki Tide z prawej (tak wrócił po restarcie).
        _, state = self.install(fake.desktop_like_this_one(), left=[],
                                widgets=["org.tide.agents", "org.tide.worktrees", "org.tide.system", "org.tide.limits"])
        self.assertEqual(types(find(state, 90))[-2], "system")
        _, state = self.install(state)
        self.assertEqual(types(find(state, 90)), ["system", "panelspacer", "icontasks", "panelspacer", "agents",
                                                  "worktrees", "limits"])

    def test_docker_tile_goes_right_after_system(self):
        """Kafelek Docker po lewej, zaraz za Systemem — kolejność z DOCK_LEFT w scripts/dock.py."""
        from scripts import dock
        self.assertEqual(dock.DOCK_LEFT[:2], ["org.tide.system", "org.tide.docker"])
        _, state = self.install(fake.desktop_like_this_one(), left=["org.tide.system", "org.tide.docker"])
        self.assertEqual(types(find(state, 90)), ["system", "docker", "panelspacer", "icontasks", "panelspacer",
                                                  "agents", "worktrees", "limits"])

    def test_worktrees_move_from_the_right_to_the_left_on_reinstall(self):
        """28.10: Worktrees z prawej strony przechodzą za Docker po lewej — bez duplikatu."""
        from scripts import dock
        self.assertEqual(dock.DOCK_LEFT, ["org.tide.system", "org.tide.docker", "org.tide.worktrees"])
        self.assertNotIn("org.tide.worktrees", dock.DOCK_RIGHT)
        _, state = self.install(fake.desktop_like_this_one(), left=["org.tide.system", "org.tide.docker"])
        _, state = self.install(state, left=dock.DOCK_LEFT, widgets=["org.tide.agents", "org.tide.limits"])
        self.assertEqual(types(find(state, 90)), ["system", "docker", "worktrees", "panelspacer", "icontasks",
                                                  "panelspacer", "agents", "limits"])

    def test_old_limits_widget_is_replaced_only_when_tide_limits_is_installed(self):
        _, state = self.install(fake.desktop_like_this_one(), widgets=["org.tide.agents"])
        self.assertIn("widget", types(find(state, 90)))  # org.limityai.widget zostaje

    def test_reinstall_does_not_duplicate_spacers_or_widgets(self):
        _, state = self.install(fake.desktop_like_this_one())
        _, state = self.install(state)
        self.assertEqual(types(find(state, 90)).count("panelspacer"), 2)
        self.assertEqual(types(find(state, 90)).count("agents"), 1)

    def test_models_tile_leaves_the_dock_on_reinstall(self):
        """PT-41: model lokalny jest w kafelku „AI” — ponowna instalacja zdejmuje stary kafelek Modele, bez duplikatów."""
        from scripts import dock
        self.assertNotIn("org.tide.models", dock.DOCK_RIGHT)
        old = ["org.tide.agents", "org.tide.limits", "org.tide.models"]
        _, state = self.install(fake.desktop_like_this_one(), widgets=old)
        self.assertEqual(types(find(state, 90))[-2:], ["limits", "models"])
        _, state = self.install(state, widgets=["org.tide.agents", "org.tide.limits"])
        _, state = self.install(state, widgets=["org.tide.agents", "org.tide.limits"])
        dock_types = types(find(state, 90))
        self.assertEqual(dock_types, ["system", "panelspacer", "icontasks", "panelspacer", "agents", "limits"])
        self.assertEqual(dock_types.count("limits"), 1)
        self.assertEqual([w["type"] for w in find(state, 90)["widgets"] if w["removed"]].count("org.tide.models"), 1)

    def test_retired_tiles_stay_when_wanted_on_either_side(self):
        """Wycofany kafelek zostaje, jeśli jest na liście docka — także z lewej (P.left), nie tylko z prawej."""
        _, state = self.install(fake.desktop_like_this_one(),
                                widgets=["org.tide.agents", "org.tide.limits", "org.tide.models", "org.tide.newtask"])
        _, state = self.install(state, left=["org.tide.system", "org.tide.models", "org.tide.newtask"],
                                widgets=["org.tide.agents", "org.tide.limits"])
        self.assertEqual(types(find(state, 90)), ["system", "models", "newtask", "panelspacer", "icontasks",
                                                  "panelspacer", "agents", "limits"])

    def test_dock_uninstall_also_removes_a_leftover_models_tile(self):
        """Dock sprzed PT-41 odinstalowany bez ponownej instalacji: stary kafelek Modele też znika (DOCK_RETIRED)."""
        from scripts import dock
        res, state = self.install(fake.desktop_like_this_one(),
                                  widgets=["org.tide.agents", "org.tide.limits", "org.tide.models"])
        params = {**panels.screens(), "dock": res["dock"], "created": res["created"], "previous": res["previous"],
                  "widgets": dock.DOCK_WIDGETS + dock.DOCK_RETIRED}
        out, state = fake.run(state, panels.script("dock-uninstall", params))
        self.assertIn("org.tide.models", result(out)["removed"])
        self.assertEqual(types(find(state, 90)), ["icontasks"])

    def test_work_tile_replaces_board_and_agents_on_reinstall(self):
        """PT-40: dock z Tablicą i Agentami po ponownej instalacji ma w ich miejscu jeden kafelek Praca."""
        from scripts import dock
        _, state = self.install(fake.desktop_like_this_one(),
                                widgets=["org.tide.board", "org.tide.agents", "org.tide.limits", "org.tide.models"])
        self.assertEqual(types(find(state, 90))[-4:], ["board", "agents", "limits", "models"])
        self.assertEqual(dock.DOCK_RIGHT, ["org.tide.work", "org.tide.limits"])
        _, state = self.install(state, widgets=dock.DOCK_RIGHT)
        self.assertEqual(types(find(state, 90)), ["system", "panelspacer", "icontasks", "panelspacer", "work", "limits"])
        _, state = self.install(state, widgets=dock.DOCK_RIGHT)
        self.assertEqual(types(find(state, 90)).count("work"), 1, "ponowna instalacja nie dubluje Pracy")
        removed = [w["type"] for w in find(state, 90)["widgets"] if w["removed"]]
        self.assertEqual(sorted(t for t in removed if t.startswith("org.tide.")),
                         ["org.tide.agents", "org.tide.board", "org.tide.models"])

    def test_rollback_brings_old_tiles_back_and_removes_the_work_applet(self):
        """Wycofanie Pracy (bez pakietu installed_widgets daje Tablicę i Agentów): aplet Pracy bez pakietu znika."""
        _, state = self.install(fake.desktop_like_this_one(), widgets=["org.tide.work", "org.tide.limits"])
        _, state = self.install(state, widgets=["org.tide.board", "org.tide.agents", "org.tide.limits"])
        self.assertEqual(types(find(state, 90)), ["system", "panelspacer", "icontasks", "panelspacer", "board", "agents",
                                                  "limits"])

    def test_installed_widgets_fall_back_to_replaced_tiles(self):
        from scripts import dock
        present = {"org.tide.board", "org.tide.agents", "org.tide.limits"}
        with mock.patch.object(dock, "is_installed", lambda w: w in present):
            self.assertEqual(dock.installed_widgets(dock.DOCK_RIGHT), ["org.tide.board", "org.tide.agents",
                                                                       "org.tide.limits"])
        with mock.patch.object(dock, "is_installed", lambda w: w in present | {"org.tide.work"}):
            self.assertEqual(dock.installed_widgets(dock.DOCK_RIGHT), ["org.tide.work", "org.tide.limits"])
        self.assertEqual(dock.params.__module__, "scripts.dock")
        self.assertIn("org.tide.work", dock.REMOVABLE)

    def test_uninstall_removes_board_and_agents_too(self):
        from scripts import dock
        res, state = self.install(fake.desktop_like_this_one(), widgets=["org.tide.board", "org.tide.agents"])
        params = {**panels.screens(), "dock": res["dock"], "created": False, "previous": res["previous"],
                  "widgets": dock.DOCK_WIDGETS + dock.DOCK_RETIRED}
        out, state = fake.run(state, panels.script("dock-uninstall", params))
        self.assertEqual(types(find(state, 90)), ["icontasks", "widget"])


@unittest.skipUnless(fake.available(), "Brak qml6 do uruchomienia skryptów")
class SidebarScriptTest(unittest.TestCase):
    HEADER = "org.tide.sidebar"
    WIDGETS = ["org.tide.sidebar", "org.tide.agents", "org.tide.system", "org.tide.worktrees", "org.tide.limits"]
    SPACER = "org.kde.plasma.panelspacer"
    LAYOUT = ["sidebar", "agents", "system", "worktrees", "limits", "panelspacer"]

    def install(self, state, installed=None, shortcut="Meta+S", widgets=None):
        params = {**panels.screens(), "sidebarPanel": (installed or {}).get("sidebar"),
                  "width": PALETTE["size"]["sidebar"], "widgets": self.WIDGETS if widgets is None else widgets,
                  "header": self.HEADER, "shortcut": shortcut, "floatingGap": 8, "spacer": self.SPACER}
        out, state = fake.run(state, panels.script("sidebar", params))
        res = result(out)
        if res["sidebar"] is not None:
            order = {"panels": [{"id": res["sidebar"], "order": res["order"], "height": params["width"]}]}
            out, state = fake.run(state, panels.script("order", order))
        return res, state

    def sidebars(self, state):
        return [p for p in live(state) if p["config"].get("/Tide", {}).get("role") == "sidebar"]

    def test_sidebar_is_an_autohiding_panel_on_the_left_edge_of_the_left_monitor(self):
        res, state = self.install(fake.desktop_like_this_one())
        bar = find(state, res["sidebar"])
        self.assertEqual(bar["screen"], 1)  # HDMI-A-1, nie główny DP-2
        self.assertEqual(bar["location"], "left")
        self.assertEqual(bar["hiding"], "dodgewindows")
        self.assertTrue(bar["floating"])
        self.assertEqual(bar["height"], PALETTE["size"]["sidebar"])
        self.assertTrue(res["created"])
        # Bez górnego paska na lewym monitorze: cała wysokość ekranu.
        self.assertEqual((bar["lengthMode"], bar["alignment"], bar["offset"]), ("custom", "left", 0))
        self.assertEqual((bar["minimumLength"], bar["maximumLength"]), (1080, 1080))

    def test_sidebar_starts_below_the_slim_top_bar(self):
        out, state = fake.run(fake.desktop_like_this_one(), panels.script("install", panels.install_params(PALETTE, {})))
        res, state = self.install(state)
        bar = find(state, res["sidebar"])
        top = PALETTE["size"]["barSlim"] + 8  # pływający pasek: 8 px od krawędzi ekranu
        self.assertEqual(bar["offset"], top)
        self.assertEqual((bar["minimumLength"], bar["maximumLength"]), (1080 - top, 1080 - top))

    def test_widgets_go_top_to_bottom_in_full_view(self):
        res, state = self.install(fake.desktop_like_this_one())
        bar = find(state, res["sidebar"])
        self.assertEqual(types(bar), self.LAYOUT)
        for type_ in self.WIDGETS[1:]:
            self.assertEqual(config(bar, type_, "/Configuration/General")["sidebarMode"], "true")
        header = next(w for w in bar["widgets"] if w["type"] == self.HEADER)
        self.assertNotIn("/Configuration/General", header["config"])
        # Rozciągany odstęp na końcu: kafelki leżą od góry, wolne miejsce zostaje na dole.
        self.assertEqual(config(bar, self.SPACER, "/General")["expanding"], "true")
        self.assertNotIn("/Configuration/General", next(w for w in bar["widgets"] if w["type"] == self.SPACER)["config"])

    def test_reinstall_keeps_a_single_spacer_at_the_end(self):
        res, state = self.install(fake.desktop_like_this_one())
        res2, state = self.install(state, {"sidebar": res["sidebar"]})
        bar = find(state, res["sidebar"])
        self.assertEqual(types(bar).count("panelspacer"), 1)
        self.assertEqual(types(bar)[-1], "panelspacer")
        self.assertEqual(res2["order"][-1], res["order"][-1])

    def test_header_gets_the_meta_s_shortcut(self):
        res, state = self.install(fake.desktop_like_this_one())
        self.assertEqual(res["shortcut"], "Meta+S")
        self.assertEqual(state["shortcuts"], {"Meta+S": f"plasmashell/activate widget {res['header']}"})

    def test_taken_shortcut_is_reported_as_missing(self):
        state = fake.desktop_like_this_one()
        state["shortcuts"] = {"Meta+S": "kwin/Something"}
        res, state = self.install(state)
        self.assertEqual(res["shortcut"], "")
        self.assertEqual(state["shortcuts"], {"Meta+S": "kwin/Something"})

    def test_without_shortcut_parameter_the_header_is_left_alone(self):
        res, state = self.install(fake.desktop_like_this_one(), shortcut="")
        self.assertEqual(res["shortcut"], "")
        self.assertEqual(state.get("shortcuts", {}), {})

    def test_reinstall_reuses_the_panel_and_the_header(self):
        res, state = self.install(fake.desktop_like_this_one())
        res2, state = self.install(state, {"sidebar": res["sidebar"]})
        self.assertEqual((res2["sidebar"], res2["header"], res2["created"]), (res["sidebar"], res["header"], False))
        self.assertEqual(len(self.sidebars(state)), 1)
        self.assertEqual(types(find(state, res["sidebar"])), self.LAYOUT)
        self.assertEqual(res2["shortcut"], "Meta+S")

    def test_reinstall_with_work_removes_agents_and_board(self):
        """PT-40: pasek z Agentami i Tablicą po ponownej instalacji ma jeden kafelek Praca w trybie paska."""
        from scripts import sidebar
        old = ["org.tide.sidebar", "org.tide.agents", "org.tide.board", "org.tide.system", "org.tide.worktrees",
               "org.tide.limits"]
        res, state = self.install(fake.desktop_like_this_one(), widgets=old)
        self.assertEqual(types(find(state, res["sidebar"]))[1:3], ["agents", "board"])
        new = [sidebar.HEADER, *sidebar.WIDGETS]
        res2, state = self.install(state, {"sidebar": res["sidebar"]}, widgets=new)
        bar = find(state, res["sidebar"])
        self.assertEqual(types(bar), ["sidebar", "work", "system", "worktrees", "limits", "panelspacer"])
        self.assertEqual(sorted(res2["removed"]), ["org.tide.agents", "org.tide.board"])
        self.assertEqual(config(bar, "org.tide.work", "/Configuration/General")["sidebarMode"], "true")
        res3, state = self.install(state, {"sidebar": res["sidebar"]}, widgets=new)
        self.assertEqual(types(find(state, res["sidebar"])).count("work"), 1)

    def test_reinstall_finds_the_panel_by_marker_and_adds_new_widgets(self):
        res, state = self.install(fake.desktop_like_this_one(), widgets=self.WIDGETS[:2])
        self.assertEqual(types(find(state, res["sidebar"])), ["sidebar", "agents", "panelspacer"])
        res2, state = self.install(state)  # bez podpowiedzi identyfikatora
        self.assertEqual(res2["sidebar"], res["sidebar"])
        self.assertEqual(types(find(state, res["sidebar"])), self.LAYOUT)

    def test_other_panels_are_untouched(self):
        _, state = self.install(fake.desktop_like_this_one())
        self.assertEqual(types(find(state, 73)), ["kickoff", "icon", "panelspacer", "digitalclock", "panelspacer",
                                                  "systemtray"])
        self.assertEqual(types(find(state, 90)), ["icontasks", "widget"])

    def test_single_monitor_gets_no_sidebar(self):
        res, state = self.install(fake.desktop_like_this_one(screens=("DP-2",)))
        self.assertIsNone(res["sidebar"])
        self.assertEqual(len(live(state)), 2)

    def test_uninstall_removes_the_sidebar_and_frees_the_shortcut(self):
        res, state = self.install(fake.desktop_like_this_one())
        out, state = fake.run(state, panels.script("sidebar-uninstall", {**panels.screens(), "header": self.HEADER}))
        undo = result(out)
        self.assertEqual(undo["removed"], [res["sidebar"]])
        self.assertEqual(undo["shortcutsCleared"], [res["header"]])
        self.assertEqual(self.sidebars(state), [])
        self.assertEqual(state["shortcuts"], {})
        self.assertEqual([p["id"] for p in live(state)], [73, 90])

    def test_top_bars_and_sidebar_do_not_touch_each_other(self):
        res, state = self.install(fake.desktop_like_this_one())
        out, state = fake.run(state, panels.script("install", panels.install_params(PALETTE, {})))
        top = result(out)
        self.assertNotEqual(top["side"], res["sidebar"])
        self.assertEqual(find(state, top["side"])["location"], "top")
        self.assertEqual(find(state, res["sidebar"])["location"], "left")
        # Odinstalowanie górnych pasków usuwa tylko smukły pasek, nie pasek boczny.
        params = {**panels.screens(), "previous": None, "mainPanel": 73, "mainCreated": False,
                  "changedKeys": panels.CHANGED_KEYS, "defaults": panels.DEFAULTS}
        out, state = fake.run(state, panels.script("uninstall", params))
        self.assertEqual(result(out)["removed"], [top["side"]])
        self.assertEqual(len(self.sidebars(state)), 1)
