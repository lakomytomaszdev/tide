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
        self.assertEqual(types(bar), ["kickoff", "appmenu", "centerspacer", "digitalclock", "panelspacer", "icon",
                                      "systemtray"])
        self.assertEqual(bar["height"], PALETTE["size"]["bar"])
        self.assertTrue(bar["floating"])
        self.assertEqual(config(bar, "org.kde.plasma.kickoff", "/General")["icon"], "tide-logo")
        self.assertEqual(config(bar, "org.kde.plasma.digitalclock", "/Appearance")["showDate"], "true")
        self.assertEqual(config(bar, "org.kde.plasma.appmenu", "/Appearance")["allScreens"], "false")
        # Kafelki zasobnika rosną z paskiem (decyzja 95).
        self.assertEqual(config(bar, "org.kde.plasma.systemtray", "/General")["scaleIconsToFit"], "true")
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
        self.assertEqual(types(side), ["appmenu", "centerspacer", "digitalclock", "panelspacer"])
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
        self.assertEqual(types(find(state, 73)), ["kickoff", "appmenu", "centerspacer", "digitalclock",
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
        self.assertEqual(config(bar, "org.kde.plasma.systemtray", "/General")["scaleIconsToFit"], "false")
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


ORPHANS = (103, 118)
PANELS_DIR = Path(__file__).resolve().parents[1] / "panels"
# Skrypty, które jeszcze nie mają strażnika panelu bez widoku (PT-492, PT-496). PT-498 skreśla stąd order.js,
# uninstall.js, dock-uninstall.js i sidebar-uninstall.js, PT-497 — stream.js, stream-leave.js i stream-console.js,
# a ostatni usuwa listę całkiem. Nowy skrypt spoza listy i bez strażnika od razu oblewa test zbiorczy.
UNGUARDED = {"sidebar-uninstall.js", "dock-uninstall.js", "order.js", "uninstall.js", "stream.js", "stream-leave.js",
             "stream-console.js"}
# Skrypty instalacji zwracają listę `orphans`: panele bez widoku, które napotkały (PT-496).
INSTALL_SCRIPTS = ("sidebar.js", "install.js", "dock.js")


def directory_scripts(directory=PANELS_DIR):
    """Skrypty paneli z katalogu (bez common.js): lista nie jest zapisana w kodzie testu."""
    return sorted(p.name for p in directory.glob("*.js") if p.name != "common.js")


def guarded_scripts(directory=PANELS_DIR, unguarded=UNGUARDED):
    return [name for name in directory_scripts(directory) if name not in unguarded]


def unguarded_missing(directory=PANELS_DIR, unguarded=UNGUARDED):
    return sorted(name for name in unguarded if not (directory / name).is_file())


def script_params(name):
    """Parametry skryptu tak, jak składa je kod instalatora (reszta skryptów czyta tylko ekrany)."""
    if name == "install.js":
        return panels.install_params(PALETTE, {})
    if name == "sidebar.js":
        return {**panels.screens(), "sidebarPanel": None, "width": PALETTE["size"]["sidebar"],
                "widgets": SidebarScriptTest.WIDGETS, "header": SidebarScriptTest.HEADER, "shortcut": "Meta+S",
                "floatingGap": 8, "spacer": SidebarScriptTest.SPACER}
    if name == "dock.js":
        from scripts import dock
        return {**panels.screens(), "left": DockScriptTest.LEFT, "widgets": DockScriptTest.WIDGETS,
                "retired": dock.REMOVABLE, "dockHeight": PALETTE["size"]["dock"]}
    return panels.screens()


def run_read(name, state):
    """Skrypt na atrapie tak, jak go woła kod: stream-read.js bez common.js (jak w silniku), reszta z common.js."""
    from scripts import activity_panels
    if name == "stream-read.js":
        js = activity_panels.read_script(True)
    else:
        js = panels.script(name.removesuffix(".js"), script_params(name))
    return fake.run(state, js)


@unittest.skipUnless(fake.available(), "Brak qml6 do uruchomienia skryptów")
class FakePlasmashellTest(unittest.TestCase):
    """Atrapa musi naprawdę rzucać wyjątek dla panelu bez widoku, a nie tylko go logować."""

    PROBE = """
        var tideProbe = {};
        var tidePanel = panelById(103);
        ["hiding", "height", "minimumLength", "maximumLength", "alignment", "lengthMode", "floating", "offset",
         "opacity"].forEach(function (name) {
            try { tidePanel[name]; tideProbe["get " + name] = "ok"; } catch (e) { tideProbe["get " + name] = "throws"; }
            try { tidePanel[name] = 1; tideProbe["set " + name] = "ok"; } catch (e) { tideProbe["set " + name] = "throws"; }
        });
        try { screenGeometry(1); tideProbe.geometry = "ok"; } catch (e) { tideProbe.geometry = "throws"; }
        tideProbe.screenCount = screenCount;
        tideProbe.location = tidePanel.location;
        print("TIDE_JSON:" + JSON.stringify(tideProbe));
    """

    def test_reading_hiding_of_a_panel_without_view_throws(self):
        with self.assertRaisesRegex(RuntimeError, r"panel bez widoku:\s*hiding panelu 103"):
            fake.run(fake.desktop_like_15_11(), "var x = panelById(103).hiding;")

    def test_screen_geometry_of_a_missing_screen_throws(self):
        with self.assertRaisesRegex(RuntimeError, "screenGeometry"):
            fake.run(fake.desktop_like_15_11(), "screenGeometry(1);")

    def test_all_nine_view_properties_throw_for_getter_and_setter(self):
        out, state = fake.run(fake.desktop_like_15_11(), self.PROBE)
        probe = result(out)
        self.assertEqual(probe.pop("screenCount"), 1)
        self.assertEqual(probe.pop("location"), "top", "położenie jest kontenera, nie widoku")
        self.assertEqual(probe.pop("geometry"), "throws")
        self.assertEqual(len(probe), 18)
        self.assertEqual(set(probe.values()), {"throws"})
        self.assertEqual(len(find(state, 103)["log"]), 18, "każda próba zostawia ślad, a nic się nie zapisało")

    def test_panels_with_a_view_still_work_and_the_state_helper_matches_15_11(self):
        state = fake.desktop_like_15_11()
        self.assertEqual(state["screens"], ["HDMI-A-1"])
        self.assertEqual({p["id"]: p["screen"] for p in state["panels"]}, {73: 0, 90: 0, 103: 1, 118: 1})
        out, _ = fake.run(state, 'print("TIDE_JSON:" + JSON.stringify([panelById(73).hiding, screenGeometry(0).width]));')
        self.assertEqual(result(out), ["none", 1920])


@unittest.skipUnless(fake.available(), "Brak qml6 do uruchomienia skryptów")
class PanelsWithoutViewSweepTest(unittest.TestCase):
    """Test zbiorczy: każdy skrypt paneli z katalogu, który ma już strażnika, na stanie „jak 15.11”."""

    def test_guarded_scripts_come_from_the_directory_and_include_the_read_scripts(self):
        self.assertTrue({"stream-read.js", "dump.js", *INSTALL_SCRIPTS} <= set(guarded_scripts()))

    def test_every_install_script_reports_the_orphans_it_met(self):
        for name in INSTALL_SCRIPTS:
            with self.subTest(script=name):
                out, _ = run_read(name, fake.desktop_like_15_11())
                self.assertEqual(result(out)["orphans"], list(ORPHANS))

    def test_install_scripts_report_an_empty_orphans_list_when_every_panel_has_a_view(self):
        for name in INSTALL_SCRIPTS:
            with self.subTest(script=name):
                out, _ = run_read(name, fake.desktop_with_tide_panels())
                self.assertEqual(result(out)["orphans"], [])

    def test_every_guarded_script_finishes_without_touching_view_properties_of_orphans(self):
        for name in guarded_scripts():
            with self.subTest(script=name):
                out, state = run_read(name, fake.desktop_like_15_11())
                self.assertIn(MARKER, out)
                for pid in ORPHANS:
                    self.assertEqual(find(state, pid)["log"], [], f"{name}: panel {pid}")

    def test_every_name_on_the_exception_list_exists_in_the_directory(self):
        self.assertEqual(unguarded_missing(), [], "lista wyjątków ukrywa nazwę, której nie ma w scripts/panels/")

    def test_a_stale_name_on_the_exception_list_is_caught(self):
        self.assertEqual(unguarded_missing(unguarded=UNGUARDED | {"nie-ma-takiego.js"}), ["nie-ma-takiego.js"])

    def test_a_new_script_without_a_guard_enters_the_sweep(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in directory_scripts() + ["nowy.js"]:
                (directory / name).write_text("")
            self.assertIn("nowy.js", guarded_scripts(directory))
            self.assertNotIn("order.js", guarded_scripts(directory))
            # Skreślenie skryptu z listy wyjątków (jak robią to kolejne tickety) wciąga go do przeglądu.
            self.assertIn("order.js", guarded_scripts(directory, UNGUARDED - {"order.js"}))

    def test_stream_read_marks_orphans_and_keeps_view_keys_of_the_rest(self):
        out, _ = run_read("stream-read.js", fake.desktop_like_15_11())
        by_id = {p["id"]: p for p in result(out)["panels"]}
        for pid in ORPHANS:
            self.assertEqual(set(by_id[pid]), {"id", "screen", "location", "role", "widgets", "orphan"})
            self.assertTrue(by_id[pid]["orphan"])
        self.assertEqual(set(by_id[73]), {"id", "screen", "location", "role", "widgets", "hiding", "height",
                                          "minimumLength", "maximumLength"})

    def test_dump_marks_orphans_and_keeps_view_keys_of_the_rest(self):
        out, _ = run_read("dump.js", fake.desktop_like_15_11())
        by_id = {p["id"]: p for p in result(out)["panels"]}
        for pid in ORPHANS:
            self.assertEqual(set(by_id[pid]), {"id", "screen", "location", "orphan", "sideBar", "config", "widgets"})
            self.assertTrue(by_id[pid]["orphan"])
            self.assertTrue(by_id[pid]["widgets"])
        self.assertEqual(set(by_id[73]), {"id", "screen", "location", "height", "floating", "alignment", "lengthMode",
                                          "hiding", "opacity", "offset", "minimumLength", "maximumLength",
                                          "sideBar", "config", "widgets"})

    def test_panels_with_a_view_read_the_same_on_two_screens_as_before(self):
        for name in ("stream-read.js", "dump.js"):
            out, _ = run_read(name, fake.desktop_with_tide_panels())
            by_id = {p["id"]: p for p in result(out)["panels"]}
            self.assertEqual(by_id[118]["hiding"], "dodgewindows")
            self.assertEqual(by_id[118]["maximumLength"], 1042)
            self.assertTrue(all("orphan" not in p for p in by_id.values()))


def two_screens_with_orphan(panel_id):
    """Dwa ekrany jak zawsze, ale panel `panel_id` pamięta ekran 2 (>= screenCount), czyli nie ma widoku."""
    state = fake.desktop_with_tide_panels()
    find(state, panel_id)["screen"] = 2
    return state


@unittest.skipUnless(fake.available(), "Brak qml6 do uruchomienia skryptów")
class InstallScriptsOrphanTest(unittest.TestCase):
    """PT-496: skrypty instalacji nie zakładają duplikatu panelu bez widoku i nie liczą go do miejsca."""

    def run_script(self, name, state):
        out, state = fake.run(state, panels.script(name.removesuffix(".js"), script_params(name)))
        return result(out), state

    def roles(self, state, role):
        return [p for p in live(state) if p["config"].get("/Tide", {}).get("role") == role]

    def test_sidebar_without_view_is_reported_and_no_new_sidebar_is_created(self):
        state = two_screens_with_orphan(118)
        before = len(live(state))
        res, state = self.run_script("sidebar.js", state)
        self.assertEqual(res["orphans"], [118])
        self.assertIsNone(res["sidebar"])
        self.assertFalse(res["created"])
        self.assertEqual(len(self.roles(state, "sidebar")), 1)
        self.assertEqual(len(live(state)), before)
        self.assertEqual(find(state, 118)["log"], [])

    def test_sidebar_without_view_does_not_hide_a_sidebar_with_view(self):
        state = two_screens_with_orphan(118)
        live_sidebar = fake.panel(130, 1, "left", 420, [])
        live_sidebar["config"]["/Tide"] = {"role": "sidebar"}
        state["panels"].append(live_sidebar)
        res, state = self.run_script("sidebar.js", state)
        self.assertEqual((res["sidebar"], res["created"], res["orphans"]), (130, False, [118]))
        self.assertEqual(find(state, 118)["log"], [])

    def test_slim_bar_without_view_is_reported_and_no_second_slim_bar_is_created(self):
        state = two_screens_with_orphan(103)
        before = len(live(state))
        res, state = self.run_script("install.js", state)
        self.assertEqual(res["orphans"], [103])
        self.assertIsNone(res["side"])
        self.assertFalse(res["sideCreated"])
        self.assertEqual(len(self.roles(state, "side-bar")), 1)
        self.assertEqual(len(live(state)), before)
        self.assertEqual(find(state, 103)["log"], [])
        self.assertEqual(res["main"], 73)  # główny pasek dalej się instaluje

    def test_sidebar_geometry_ignores_an_unrelated_panel_without_view_at_the_same_location(self):
        _, plain = self.run_script("sidebar.js", fake.desktop_with_tide_panels())
        state = fake.desktop_with_tide_panels()
        state["panels"].append(fake.panel(140, 2, "top", 90, []))
        res, state = self.run_script("sidebar.js", state)
        self.assertEqual(res["orphans"], [140])
        keys = ("screen", "location", "offset", "height", "minimumLength", "maximumLength")
        self.assertEqual({k: find(state, 118)[k] for k in keys}, {k: find(plain, 118)[k] for k in keys})
        self.assertEqual(find(state, 140)["log"], [])

    def test_dock_ignores_a_bottom_panel_without_view(self):
        _, plain = self.run_script("dock.js", fake.desktop_with_tide_panels())
        state = fake.desktop_with_tide_panels()
        state["panels"].append(fake.panel(141, 2, "bottom", 90, []))
        res, state = self.run_script("dock.js", state)
        self.assertEqual((res["dock"], res["created"], res["orphans"]), (90, False, [141]))
        keys = ("screen", "location", "height", "lengthMode", "floating")
        self.assertEqual({k: find(state, 90)[k] for k in keys}, {k: find(plain, 90)[k] for k in keys})
        self.assertEqual(find(state, 141)["log"], [])
