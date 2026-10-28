"""Przejście do okna sesji, gdy Konsole ma kilka okien (PT-33).

Logika (windows.Activator) jest sprawdzana z atrapą Konsole w pamięci: okna z kartami, bieżąca karta
okna i tytuł okna równy wyświetlanemu tytułowi bieżącej karty (z dopiskiem „ — Konsola” od Qt).
Wygenerowany skrypt KWin wykonuje QJSEngine z atrapą `workspace`, więc sprawdzamy, które okno
zostaje aktywowane, a nie tekst skryptu. Cienki test kontraktu sprawdza metody okien zaplecza D-Bus
(konsole.py) na atrapie Konsole na prywatnej magistrali — prawdziwa Konsole ani KWin nie są dotykane.
"""
import json
import os
import sys
import unittest

from .privatebus import PrivateBus, requires_dbus

from tide_daemon import tabmarks, windows
from tide_daemon.konsole import child_nodes
from tide_daemon.processes import ProcessInfo, ProcessTable

try:
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtQml import QJSEngine
except ImportError:  # pragma: no cover
    QJSEngine = None

SERVICE = "org.kde.konsole-700"
SUFFIX = " — Konsola"


class FakeKonsole:
    """Jeden proces Konsole: okna (ścieżka → numery kart), bieżąca karta okna i karty jak w Konsole 26.08
    (setTitle(1) nadpisuje oba formaty). windows=None udaje nieudane wyliczenie okien."""

    def __init__(self, layout: dict, titles: dict, service: str = SERVICE):
        self.service = service
        self.layout = {w: list(s) for w, s in layout.items()}
        self.currents = {w: s[0] for w, s in layout.items() if s}
        self.tabs = {n: {"title": t, "local": "%d : %n", "remote": "(%u) %H", "color": "#000000"}
                     for n, t in titles.items()}
        self.enumerable = True
        self.calls = []

    # okna
    def windows(self, service):
        if service != self.service or not self.enumerable:
            return None
        return list(self.layout)

    def sessions(self, service, window):
        return list(self.layout[window]) if service == self.service and window in self.layout else None

    def current(self, service, window):
        return self.currents.get(window) if service == self.service else None

    def set_current(self, service, window, number):
        self.calls.append(("set_current", window, number))
        if service != self.service or window not in self.layout:
            return False  # nieistniejący obiekt D-Bus
        if number in self.layout[window]:
            self.currents[window] = number
        return True

    def title(self, service, number):
        tab = self.tabs.get(number) if service == self.service else None
        return tab["title"] if tab else None

    # karty (jak KonsoleTabs.read/apply)
    def read(self, key):
        service, path = tabmarks.split_key(key)
        tab = self.tabs.get(windows.session_number(path)) if service == self.service else None
        return dict(tab) if tab else None

    def apply(self, key, title=None, local=None, remote=None, color=None):
        self.calls.append(("apply", key, title))
        service, path = tabmarks.split_key(key)
        tab = self.tabs.get(windows.session_number(path)) if service == self.service else None
        if tab is None:
            return False
        if title is not None:
            tab["title"] = tab["local"] = tab["remote"] = title
        if local is not None:
            tab["local"] = local
        if remote is not None:
            tab["remote"] = remote
        if color is not None:
            tab["color"] = color or "#000000"
        return True

    def captions(self) -> dict:
        """Tytuły okien tak, jak widzi je KWin: tytuł bieżącej karty z dopiskiem Qt."""
        return {w: self.tabs[n]["title"] + SUFFIX for w, n in self.currents.items()}


class FakeKWin:
    def __init__(self):
        self.scripts = []

    def run(self, source):
        self.scripts.append(source)
        return True


class Later:
    def __init__(self):
        self.queue = []

    def __call__(self, ms, fn):
        self.queue.append((ms, fn))

    def fire(self):
        queue, self.queue = self.queue, []
        for _ms, fn in queue:
            fn()


class Guard:
    def __init__(self):
        self.events = []

    def hold(self, key):
        self.events.append(("hold", key))

    def release(self, key):
        self.events.append(("release", key))


def konsole_target(session: int, window: str = "/Windows/1", service: str = SERVICE, pid: int = 700) -> dict:
    return {"pids": [pid + 11, pid + 1, pid], "label": "Konsole",
            "konsole": {"service": service, "window": window, "session": f"/Sessions/{session}"}}


WORKSPACE = """
var activated = [];
var wins = [];
function addWindow(id, pid, caption, normal) {
    var handlers = [];
    wins.push({ id: id, pid: pid, caption: caption, normalWindow: normal !== false, minimized: false,
                captionChanged: { connect: function (f) { handlers.push(f); } }, handlers: handlers });
}
function setCaption(id, caption) {
    wins.forEach(function (w) {
        if (w.id === id) { w.caption = caption; w.handlers.forEach(function (f) { f(); }); }
    });
}
var workspace = {
    windowList: function () { return wins; },
    get activeWindow() { return null; },
    set activeWindow(w) { activated.push(w.id); }
};
"""


@unittest.skipIf(QJSEngine is None, "brak PyQt6.QtQml")
class KWinScriptMixin:
    def run_script(self, source: str, wins: list[tuple], changes: list[tuple] = ()) -> list[str]:
        """Wykonuje skrypt na oknach (id, pid, tytuł[, normalne]) i zwraca kolejne aktywowane okna.
        changes: późniejsze zmiany tytułów (id, tytuł) — jak tytuł, który dociera do KWin po starcie."""
        self.app = QCoreApplication.instance() or QCoreApplication([])
        engine = QJSEngine()
        setup = WORKSPACE + "".join(f"addWindow({json.dumps(w[0])}, {w[1]}, {json.dumps(w[2])}, "
                                    f"{json.dumps(w[3] if len(w) > 3 else True)});\n" for w in wins)
        result = engine.evaluate(setup + source)
        self.assertFalse(result.isError(), result.toString())
        for wid, caption in changes:
            out = engine.evaluate(f"setCaption({json.dumps(wid)}, {json.dumps(caption)});")
            self.assertFalse(out.isError(), out.toString())
        return json.loads(engine.evaluate("JSON.stringify(activated)").toString())


class ResolveWindowTest(unittest.TestCase):
    """Okno karty z /Windows/*, nie ze środowiska agenta."""

    def activator(self, konsole):
        self.kwin, self.later = FakeKWin(), Later()
        return windows.Activator(konsole, self.kwin, self.later)

    def test_stale_env_window_that_does_not_exist(self):
        # Karta powstała w /Windows/1, którego już nie ma; proces eksportuje tylko /Windows/2.
        konsole = FakeKonsole({"/Windows/2": [2]}, {2: "projekt : bash"})
        self.assertTrue(self.activator(konsole).activate(konsole_target(2, "/Windows/1")))
        self.assertEqual(konsole.calls, [("set_current", "/Windows/2", 2)])

    def test_tab_dragged_to_another_window_of_the_same_process(self):
        konsole = FakeKonsole({"/Windows/1": [1, 3], "/Windows/4": [4, 2]},
                              {1: "a", 2: "agent", 3: "c", 4: "d"})
        self.activator(konsole).activate(konsole_target(2, "/Windows/1"))
        self.assertEqual(konsole.calls, [("set_current", "/Windows/4", 2)])
        self.assertEqual(konsole.currents, {"/Windows/1": 1, "/Windows/4": 2})

    def test_falls_back_to_env_window_when_windows_cannot_be_listed(self):
        konsole = FakeKonsole({"/Windows/1": [1, 2]}, {1: "a", 2: "b"})
        konsole.enumerable = False
        self.activator(konsole).activate(konsole_target(2, "/Windows/1"))
        self.assertEqual(konsole.calls, [("set_current", "/Windows/1", 2)])
        self.assertIn('const want = "b"', self.kwin.scripts[0])

    def test_falls_back_to_env_window_when_no_window_reports_the_tab(self):
        konsole = FakeKonsole({"/Windows/1": [1]}, {1: "a", 5: "zamykana"})
        self.activator(konsole).activate(konsole_target(5, "/Windows/9"))
        self.assertEqual(konsole.calls, [("set_current", "/Windows/9", 5)])

    def test_non_konsole_target_only_runs_the_script(self):
        konsole = FakeKonsole({"/Windows/1": [1]}, {1: "a"})
        self.activator(konsole).activate({"pids": [5, 4], "label": "kitty"})
        self.assertEqual(konsole.calls, [])
        self.assertEqual(len(self.kwin.scripts), 1)

    def test_bad_session_path_does_not_switch_tabs(self):
        konsole = FakeKonsole({"/Windows/1": [1]}, {1: "a"})
        target = konsole_target(1)
        target["konsole"]["session"] = "/Sessions/x"
        self.activator(konsole).activate(target)
        self.assertEqual(konsole.calls, [])


class PickWindowTest(KWinScriptMixin, unittest.TestCase):
    """Kilka okien jednego procesu (ten sam PID): które okno KWin aktywuje."""

    def setUp(self):
        self.kwin, self.later, self.guard = FakeKWin(), Later(), Guard()

    def jump(self, konsole, session, window="/Windows/1"):
        activator = windows.Activator(konsole, self.kwin, self.later, self.guard)
        self.assertTrue(activator.activate(konsole_target(session, window)))
        return self.kwin.scripts[-1]

    def kwin_windows(self, konsole, order=None, pid=700):
        captions = konsole.captions()
        return [(w, pid, captions[w]) for w in (order or list(captions))]

    def test_distinct_titles_pick_the_window_of_the_tab_in_any_order(self):
        konsole = FakeKonsole({"/Windows/1": [1], "/Windows/2": [2, 3]},
                              {1: "● tide · master", 2: "○ diag · feat", 3: "● tide · pt-33"})
        script = self.jump(konsole, 3)
        self.assertEqual(self.guard.events, [])  # bez znacznika — tytuł wystarcza
        for order in (["/Windows/1", "/Windows/2"], ["/Windows/2", "/Windows/1"]):
            self.assertEqual(self.run_script(script, self.kwin_windows(konsole, order)), ["/Windows/2"])

    def test_title_that_is_a_prefix_of_another_does_not_confuse(self):
        konsole = FakeKonsole({"/Windows/1": [1], "/Windows/2": [2]}, {1: "tide", 2: "tide · master"})
        script = self.jump(konsole, 1)
        self.assertEqual(self.run_script(script, self.kwin_windows(konsole, ["/Windows/2", "/Windows/1"])),
                         ["/Windows/1"])

    def test_never_picks_a_window_showing_another_tab(self):
        # KWin ma jeszcze stary tytuł okna karty, a drugie okno pokazuje swoją kartę: wybór pada
        # na okno o nieznanym tytule, a po odświeżeniu tytułu zostaje przy nim.
        konsole = FakeKonsole({"/Windows/1": [1], "/Windows/2": [2, 3]}, {1: "a", 2: "b", 3: "agent"})
        script = self.jump(konsole, 3)
        wins = [("/Windows/1", 700, "a" + SUFFIX), ("/Windows/2", 700, "b" + SUFFIX)]
        self.assertEqual(self.run_script(script, wins, [("/Windows/2", "agent" + SUFFIX)]), ["/Windows/2"])

    def test_late_caption_corrects_the_choice_once(self):
        # Okno karty ma w KWin tytuł innej karty sprzed przełączenia, więc pierwszy wybór jest
        # niepewny; gdy KWin dostanie właściwy tytuł, skrypt raz przenosi fokus.
        konsole = FakeKonsole({"/Windows/1": [1], "/Windows/2": [2, 3]}, {1: "a", 2: "b", 3: "agent"})
        script = self.jump(konsole, 3)
        wins = [("/Windows/1", 700, "obcy" + SUFFIX), ("/Windows/2", 700, "stary" + SUFFIX)]
        self.assertEqual(self.run_script(script, wins, [("/Windows/2", "agent" + SUFFIX),
                                                        ("/Windows/1", "agent" + SUFFIX)]),
                         ["/Windows/1", "/Windows/2"])

    def test_identical_titles_get_a_temporary_marker(self):
        konsole = FakeKonsole({"/Windows/1": [1], "/Windows/2": [2]},
                              {1: "● plasma-tide · master", 2: "● plasma-tide · master"})
        konsole.tabs[2].update(local="● %d : %n", remote="● (%u) %H")
        script = self.jump(konsole, 2)
        key = f"{SERVICE} /Sessions/2"
        marked = "● plasma-tide · master" + windows.MARKER
        self.assertEqual(konsole.tabs[2], {"title": marked, "local": "● %d : %n", "remote": "● (%u) %H",
                                           "color": "#000000"})
        self.assertEqual(self.guard.events, [("hold", key)])
        for order in (["/Windows/1", "/Windows/2"], ["/Windows/2", "/Windows/1"]):
            self.assertEqual(self.run_script(script, self.kwin_windows(konsole, order)), ["/Windows/2"])
        self.later.fire()
        self.assertEqual(konsole.tabs[2], {"title": "● plasma-tide · master", "local": "● %d : %n",
                                           "remote": "● (%u) %H", "color": "#000000"})
        self.assertEqual(self.guard.events, [("hold", key), ("release", key)])

    def test_marker_reaching_kwin_late_still_wins(self):
        konsole = FakeKonsole({"/Windows/1": [1], "/Windows/2": [2]}, {1: "x", 2: "x"})
        script = self.jump(konsole, 2)
        wins = [("/Windows/1", 700, "x" + SUFFIX), ("/Windows/2", 700, "x" + SUFFIX)]
        activated = self.run_script(script, wins, [("/Windows/2", "x" + windows.MARKER + SUFFIX)])
        self.assertEqual(activated[-1], "/Windows/2")

    def test_user_title_change_during_marker_is_kept(self):
        konsole = FakeKonsole({"/Windows/1": [1], "/Windows/2": [2]}, {1: "x", 2: "x"})
        self.jump(konsole, 2)
        konsole.tabs[2]["title"] = "moja nazwa"
        self.later.fire()
        self.assertEqual(konsole.tabs[2]["title"], "moja nazwa")
        self.assertEqual(self.guard.events[-1], ("release", f"{SERVICE} /Sessions/2"))

    def test_second_jump_before_restore_keeps_the_original_title(self):
        konsole = FakeKonsole({"/Windows/1": [1], "/Windows/2": [2]}, {1: "x", 2: "x"})
        self.jump(konsole, 2)
        self.jump(konsole, 2)
        self.later.fire()
        self.assertEqual(konsole.tabs[2]["title"], "x")

    def test_other_konsole_processes_and_apps_are_not_candidates(self):
        konsole = FakeKonsole({"/Windows/1": [1]}, {1: "agent"})
        script = self.jump(konsole, 1)
        wins = [("inna", 999, "agent" + SUFFIX), ("dialog", 700, "agent" + SUFFIX, False),
                ("/Windows/1", 700, "agent" + SUFFIX)]
        self.assertEqual(self.run_script(script, wins), ["/Windows/1"])

    def test_plain_terminal_uses_the_nearest_process(self):
        script = windows.kwin_script([5, 4, 3])
        self.assertEqual(self.run_script(script, [("daleki", 3, "a"), ("bliski", 4, "b")]), ["bliski"])

    def test_konsole_without_titles_in_window_captions_falls_back_to_the_process(self):
        konsole = FakeKonsole({"/Windows/1": [1]}, {1: "agent"})
        script = self.jump(konsole, 1)
        self.assertEqual(self.run_script(script, [("/Windows/1", 700, "Konsola")]), ["/Windows/1"])


class TabGuardTest(unittest.TestCase):
    """Znacznik stanu (tabmarks) i chwilowy znacznik przejścia do okna nie walczą o tytuł karty."""

    def test_state_marker_waits_for_the_borrowed_tab_and_catches_up(self):
        konsole = FakeKonsole({"/Windows/1": [1], "/Windows/2": [2]}, {1: "tide : bash", 2: "tide : bash"})
        key = f"{SERVICE} /Sessions/2"
        marker = tabmarks.TabMarker(konsole, {"working": "#6fd6c6", "waiting": "#f5b99a"}, None)
        marker.update({f"{SERVICE} /Sessions/1": ("working", "tide · master"), key: ("working", "tide · master")})
        later = Later()
        activator = windows.Activator(konsole, FakeKWin(), later, marker)
        activator.activate(konsole_target(2))
        self.assertTrue(konsole.tabs[2]["title"].endswith(windows.MARKER))
        # Agent zaczyna czekać, zanim tytuł wróci: karta jest wstrzymana.
        marker.update({f"{SERVICE} /Sessions/1": ("working", "tide · master"), key: ("waiting", "tide · master")})
        self.assertTrue(konsole.tabs[2]["title"].endswith(windows.MARKER))
        later.fire()
        self.assertEqual(konsole.tabs[2]["title"], "◆ tide · master · czeka")
        self.assertEqual(marker.records[key]["local"], "%d : %n")  # oryginał bez śladu znacznika okna
        marker.clear()
        self.assertEqual(konsole.tabs[2]["local"], "%d : %n")

    def test_release_without_hold_does_nothing(self):
        konsole = FakeKonsole({"/Windows/1": [1]}, {1: "a"})
        marker = tabmarks.TabMarker(konsole, {}, None)
        marker.release(f"{SERVICE} /Sessions/1")
        self.assertEqual(konsole.calls, [])

    def test_give_back_all_restores_before_shutdown(self):
        konsole = FakeKonsole({"/Windows/1": [1], "/Windows/2": [2]}, {1: "x", 2: "x"})
        activator = windows.Activator(konsole, FakeKWin(), Later())
        activator.activate(konsole_target(2))
        activator.give_back_all()
        self.assertEqual(konsole.tabs[2]["title"], "x")


class ShowsTest(unittest.TestCase):
    def test_caption_matching(self):
        self.assertTrue(windows.shows("● tide · master — Konsola", "● tide · master"))
        self.assertTrue(windows.shows("tide  :  bash", "tide : bash"))
        self.assertFalse(windows.shows("tide · master — Konsola", "tide"))
        self.assertFalse(windows.shows("cokolwiek", ""))

    def test_child_nodes(self):
        xml = ('<node><interface name="org.freedesktop.DBus.Introspectable"/>'
               '<node name="2"/><node name="5"/></node>')
        self.assertEqual(child_nodes(xml), ["2", "5"])
        self.assertEqual(child_nodes("nie xml"), [])


class TargetTest(unittest.TestCase):
    def test_konsole_target_keeps_env_window_only_as_fallback(self):
        env = {"KONSOLE_DBUS_SERVICE": SERVICE, "KONSOLE_DBUS_WINDOW": "/Windows/1",
               "KONSOLE_DBUS_SESSION": "/Sessions/2"}
        table = ProcessTable([ProcessInfo(700, 1, "konsole"), ProcessInfo(701, 700, "bash"),
                              ProcessInfo(711, 701, "claude", env=env, tool="claude")])
        self.assertEqual(windows.target_for(table, 711)["konsole"],
                         {"service": SERVICE, "window": "/Windows/1", "session": "/Sessions/2"})


@requires_dbus
class KonsoleWindowsDbusTest(unittest.TestCase):
    """Metody okien zaplecza D-Bus na atrapie Konsole: introspekcja /Windows, sessionList i setCurrentSession."""

    SERVICE = "org.kde.konsole-4343"

    def setUp(self):
        self.priv = PrivateBus("tide-windows-test")
        self.addCleanup(self.priv.stop)
        self.priv.spawn([sys.executable, os.path.join(os.path.dirname(__file__), "fake_konsole.py"),
                         self.SERVICE, "3", json.dumps({"2": [1], "5": [3, 2]})], self.SERVICE)
        from tide_daemon.konsole import KonsoleTabs
        self.tabs = KonsoleTabs(self.priv.bus)

    def test_windows_sessions_and_switching(self):
        self.assertEqual(sorted(self.tabs.windows(self.SERVICE)), ["/Windows/2", "/Windows/5"])
        self.assertEqual(self.tabs.sessions(self.SERVICE, "/Windows/5"), [3, 2])
        self.assertEqual(self.tabs.current(self.SERVICE, "/Windows/5"), 3)
        self.assertTrue(self.tabs.set_current(self.SERVICE, "/Windows/5", 2))
        self.assertEqual(self.tabs.current(self.SERVICE, "/Windows/5"), 2)
        self.assertEqual(self.tabs.title(self.SERVICE, 2), "projekt-2 : bash")

    def test_activator_switches_in_the_window_that_has_the_tab(self):
        activator = windows.Activator(self.tabs, FakeKWin(), Later())
        activator.activate(konsole_target(2, "/Windows/1", self.SERVICE))
        self.assertEqual(self.tabs.current(self.SERVICE, "/Windows/5"), 2)

    def test_missing_objects_read_as_none(self):
        self.assertIsNone(self.tabs.sessions(self.SERVICE, "/Windows/1"))
        self.assertFalse(self.tabs.set_current(self.SERVICE, "/Windows/1", 2))
        self.assertIsNone(self.tabs.windows("org.kde.konsole-1"))
        self.assertIsNone(self.tabs.title(self.SERVICE, 9))


if __name__ == "__main__":
    unittest.main()
