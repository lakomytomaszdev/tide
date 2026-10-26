"""Znacznik stanu agenta na karcie Konsole (#11): która karta dostaje jaki znacznik, oznaczanie,
przywracanie i sprzątanie po restarcie usługi.

Logika (tabmarks.py) jest sprawdzana z atrapą Konsole w pamięci, która naśladuje jej zachowanie
(setTitle(1) nadpisuje formaty tytułu). Cienki test kontraktu uruchamia atrapę Konsole jako usługę
na prywatnej magistrali i sprawdza zaplecze D-Bus (konsole.py) — prawdziwa Konsole nie jest dotykana.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

from . import DAEMON_DIR
from .privatebus import PrivateBus, requires_dbus

from tide_daemon import tabmarks
from tide_daemon.processes import ProcessInfo, ProcessTable

REPO = os.path.dirname(DAEMON_DIR)
COLORS = {"working": "#6fd6c6", "waiting": "#f5b99a"}
KEY = "org.kde.konsole-500 /Sessions/2"


def konsole_env(session: int, service: str = "org.kde.konsole-500") -> dict:
    return {"KONSOLE_DBUS_SERVICE": service, "KONSOLE_DBUS_WINDOW": "/Windows/1",
            "KONSOLE_DBUS_SESSION": f"/Sessions/{session}"}


def table() -> ProcessTable:
    """Konsole (500) z dwiema kartami i kitty (600) z jedną; agenci claude/codex w powłokach."""
    return ProcessTable([
        ProcessInfo(500, 10, "konsole"),
        ProcessInfo(501, 500, "bash"),
        ProcessInfo(511, 501, "claude", env=konsole_env(1), tool="claude"),
        ProcessInfo(502, 500, "bash"),
        ProcessInfo(521, 502, "claude", env=konsole_env(2), tool="claude"),
        ProcessInfo(522, 521, "codex", env=konsole_env(2), tool="codex"),
        ProcessInfo(600, 10, "kitty"),
        ProcessInfo(601, 600, "bash"),
        ProcessInfo(611, 601, "claude", tool="claude"),
    ])


class FakeKonsole:
    """Karty Konsole w pamięci z tą samą semantyką co prawdziwe API."""

    def __init__(self, tabs: dict | None = None):
        self.tabs = tabs if tabs is not None else {
            KEY: {"title": "tide : bash", "local": "%d : %n", "remote": "(%u) %H", "color": "#000000"},
        }
        self.calls = []

    def read(self, key):
        self.calls.append(("read", key))
        tab = self.tabs.get(key)
        return dict(tab) if tab else None

    def apply(self, key, title=None, local=None, remote=None, color=None):
        self.calls.append(("apply", key))
        tab = self.tabs.get(key)
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


class DesiredTabsTest(unittest.TestCase):
    """Odwzorowanie stan sesji → znacznik karty."""

    def test_maps_working_and_waiting_sessions_to_their_konsole_tabs(self):
        sessions = [
            {"id": "claude:a", "state": "working"},
            {"id": "claude:b", "state": "working"},
            {"id": "codex:c", "state": "waiting"},
            {"id": "claude:kitty", "state": "waiting"},
        ]
        pids = {"claude:a": 511, "claude:b": 521, "codex:c": 522, "claude:kitty": 611}
        self.assertEqual(tabmarks.desired_tabs(sessions, pids, table()), {
            "org.kde.konsole-500 /Sessions/1": "working",
            # Dwie sesje w jednej karcie: „czeka” wygrywa z „pracuje”.
            "org.kde.konsole-500 /Sessions/2": "waiting",
        })

    def test_idle_ended_and_unknown_sessions_are_not_marked(self):
        sessions = [
            {"id": "claude:a", "state": "idle"},
            {"id": "claude:b", "state": "ended"},
            {"id": "claude:x", "state": "working"},       # brak procesu
            {"id": "claude:kitty", "state": "working"},   # terminal bez API kart
        ]
        pids = {"claude:a": 511, "claude:b": 521, "claude:kitty": 611}
        self.assertEqual(tabmarks.desired_tabs(sessions, pids, table()), {})
        self.assertEqual(tabmarks.desired_tabs([{"id": "claude:a", "state": "working"}], {"claude:a": 511}, None), {})

    def test_done_session_gets_the_quiet_mark(self):
        # Agent skończył turę: puste kółko bez koloru karty (#26), nie wezwanie.
        self.assertEqual(tabmarks.desired_tabs([{"id": "claude:a", "state": "done"}], {"claude:a": 511}, table()),
                         {"org.kde.konsole-500 /Sessions/1": "done"})
        # Pracujący agent w tej samej karcie wygrywa ze skończonym.
        sessions = [{"id": "claude:b", "state": "done"}, {"id": "codex:c", "state": "working"}]
        self.assertEqual(tabmarks.desired_tabs(sessions, {"claude:b": 521, "codex:c": 522}, table()),
                         {"org.kde.konsole-500 /Sessions/2": "working"})

    def test_state_colors_come_from_palette(self):
        with open(os.path.join(REPO, "theme/palette/mist.json"), encoding="utf-8") as fh:
            palette = json.load(fh)
        colors = tabmarks.state_colors(palette)
        self.assertEqual(colors["working"], palette["pastel"][palette["semantic"]["working"]].lower())
        self.assertEqual(colors["waiting"], palette["pastel"][palette["semantic"]["waiting"]].lower())
        self.assertEqual(tabmarks.load_state_colors([os.path.join(REPO, "theme/palette/mist.json")]), colors)
        self.assertEqual(tabmarks.load_state_colors(["/nie/ma/takiej/palety.json"]), {})

    def test_targets_carry_project_and_branch(self):
        sessions = [
            {"id": "claude:a", "state": "working", "project": "plasma-tide", "branch": "feat/x"},
            {"id": "claude:b", "state": "done", "project": "legal-agents", "branch": ""},
            {"id": "codex:c", "state": "waiting", "project": "LogViewer", "branch": "master"},
        ]
        pids = {"claude:a": 511, "claude:b": 521, "codex:c": 522}
        self.assertEqual(tabmarks.tab_targets(sessions, pids, table()), {
            "org.kde.konsole-500 /Sessions/1": ("working", "plasma-tide · feat/x"),
            "org.kde.konsole-500 /Sessions/2": ("waiting", "LogViewer · master"),
        })
        self.assertEqual(tabmarks.session_label({"project": "tide"}), "tide")
        self.assertEqual(tabmarks.session_label({"branch": "master"}), "")

    def test_strip_marker_removes_only_our_prefixes(self):
        self.assertEqual(tabmarks.strip_marker("◆ ◐ ● ○ ◆ tide : bash"), "tide : bash")
        self.assertEqual(tabmarks.strip_marker("◆tide"), "◆tide")
        self.assertEqual(tabmarks.strip_marker("tide ◆ bash"), "tide ◆ bash")


class TabMarkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-tabs-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.store = os.path.join(self.tmp, "state/tide/konsole-tabs.json")
        self.konsole = FakeKonsole()
        self.marker = tabmarks.TabMarker(self.konsole, COLORS, self.store)

    def tab(self):
        return self.konsole.tabs[KEY]

    def test_working_then_waiting_then_idle(self):
        self.marker.update({KEY: "working"})
        self.assertEqual(self.tab(), {"title": "● tide : bash", "local": "● %d : %n", "remote": "● (%u) %H",
                                      "color": COLORS["working"]})
        self.assertTrue(os.path.isfile(self.store))

        self.marker.update({KEY: "waiting"})
        self.assertEqual(self.tab(), {"title": "◆ tide : bash", "local": "◆ %d : %n", "remote": "◆ (%u) %H",
                                      "color": COLORS["waiting"]})

        self.marker.update({})
        self.assertEqual(self.tab(), {"title": "tide : bash", "local": "%d : %n", "remote": "(%u) %H",
                                      "color": "#000000"})
        self.assertFalse(os.path.exists(self.store))  # nic oznaczonego — pliku stanu nie ma

    def test_other_tabs_are_never_touched(self):
        other = "org.kde.konsole-500 /Sessions/1"
        self.konsole.tabs[other] = {"title": "inna : vim", "local": "%n", "remote": "%H", "color": "#336699"}
        self.marker.update({KEY: "waiting"})
        self.marker.update({})
        self.assertEqual(self.konsole.tabs[other], {"title": "inna : vim", "local": "%n", "remote": "%H",
                                                    "color": "#336699"})
        self.assertFalse([c for c in self.konsole.calls if c[1] == other])

    def test_own_tab_color_is_restored(self):
        self.tab()["color"] = "#336699"
        self.marker.update({KEY: "working"})
        self.marker.update({})
        self.assertEqual(self.tab()["color"], "#336699")

    def test_unchanged_state_does_not_talk_to_konsole(self):
        self.marker.update({KEY: "working"})
        before = len(self.konsole.calls)
        self.marker.update({KEY: "working"})
        self.assertEqual(len(self.konsole.calls), before)

    def test_tab_renamed_by_user_keeps_the_new_title(self):
        self.marker.update({KEY: "working"})
        self.tab()["local"] = self.tab()["remote"] = self.tab()["title"] = "moja karta"
        self.marker.update({})
        self.assertEqual(self.tab()["local"], "moja karta")
        self.assertEqual(self.tab()["title"], "moja karta")
        self.assertEqual(self.tab()["color"], "#000000")  # kolor wciąż był nasz — wraca

    def test_rename_while_marked_becomes_the_new_original(self):
        self.marker.update({KEY: "working"})
        self.tab()["local"] = self.tab()["remote"] = "%w"
        self.marker.update({KEY: "waiting"})
        self.assertEqual(self.tab()["local"], "◆ %w")
        self.marker.update({})
        self.assertEqual((self.tab()["local"], self.tab()["remote"]), ("%w", "%w"))

    def test_closed_tab_is_forgotten(self):
        self.marker.update({KEY: "working"})
        del self.konsole.tabs[KEY]
        self.marker.update({})
        self.assertEqual(self.marker.records, {})
        self.marker.update({KEY: "waiting"})
        self.assertEqual(self.marker.records, {})

    def test_restart_restores_tabs_marked_by_previous_run(self):
        self.marker.update({KEY: "waiting"})
        restarted = tabmarks.TabMarker(self.konsole, COLORS, self.store)
        self.assertIn(KEY, restarted.records)
        restarted.update({})  # sesja w międzyczasie przestała czekać
        self.assertEqual(self.tab(), {"title": "tide : bash", "local": "%d : %n", "remote": "(%u) %H",
                                      "color": "#000000"})

    def test_leftover_marks_without_state_file_are_not_doubled(self):
        # Usługa zabita twardo i bez pliku stanu: karta została ze znacznikiem i naszym kolorem.
        self.konsole.tabs[KEY] = {"title": "◐ tide : bash", "local": "● %d : %n", "remote": "◐ (%u) %H",
                                  "color": COLORS["working"]}
        marker = tabmarks.TabMarker(self.konsole, COLORS, None)
        marker.update({KEY: "waiting"})
        self.assertEqual(self.tab()["local"], "◆ %d : %n")
        marker.clear()
        self.assertEqual(self.tab(), {"title": "tide : bash", "local": "%d : %n", "remote": "(%u) %H",
                                      "color": "#000000"})

    def test_without_palette_only_title_is_marked(self):
        marker = tabmarks.TabMarker(self.konsole, {}, None)
        marker.update({KEY: "waiting"})
        self.assertEqual(self.tab()["title"], "◆ tide : bash")
        self.assertEqual(self.tab()["color"], "#000000")
        marker.clear()
        self.assertEqual(self.tab()["title"], "tide : bash")

    def test_label_replaces_the_title_and_comes_back(self):
        self.marker.update({KEY: ("working", "plasma-tide · feat/x")})
        self.assertEqual(self.tab(), {"title": "● plasma-tide · feat/x", "local": "● plasma-tide · feat/x",
                                      "remote": "● plasma-tide · feat/x", "color": COLORS["working"]})
        self.marker.update({KEY: ("waiting", "plasma-tide · feat/x")})
        self.assertEqual(self.tab()["title"], "◆ plasma-tide · feat/x · czeka")
        self.assertEqual(self.tab()["color"], COLORS["waiting"])
        self.marker.update({KEY: ("done", "plasma-tide · feat/x")})
        self.assertEqual(self.tab()["title"], "○ plasma-tide · feat/x")
        self.assertEqual(self.tab()["color"], "#000000")  # skończony bez koloru — oryginał karty
        self.marker.update({})
        self.assertEqual((self.tab()["local"], self.tab()["remote"], self.tab()["color"]),
                         ("%d : %n", "(%u) %H", "#000000"))
        self.assertEqual(self.tab()["title"], "plasma-tide · feat/x")  # do najbliższego przeliczenia formatu

    def test_branch_switch_updates_the_label(self):
        self.marker.update({KEY: ("working", "tide · a")})
        self.marker.update({KEY: ("working", "tide · b")})
        self.assertEqual(self.tab()["title"], "● tide · b")
        self.marker.update({})
        self.assertEqual(self.tab()["local"], "%d : %n")

    def test_percent_in_label_is_not_a_format_variable(self):
        self.marker.update({KEY: ("working", "tide · fix/100%")})
        self.assertEqual(self.tab()["local"], "● tide · fix/100%%")
        self.assertEqual(self.tab()["title"], "● tide · fix/100%")

    def test_broken_state_file_is_ignored(self):
        os.makedirs(os.path.dirname(self.store))
        with open(self.store, "w", encoding="utf-8") as fh:
            fh.write("{nie json")
        self.assertEqual(tabmarks.TabMarker(self.konsole, COLORS, self.store).records, {})


@requires_dbus
class KonsoleDbusTest(unittest.TestCase):
    """Zaplecze D-Bus na atrapie Konsole: te same nazwy metod i typy argumentów co w Konsole."""

    SERVICE = "org.kde.konsole-4242"

    def setUp(self):
        self.priv = PrivateBus("tide-tabs-test")
        self.addCleanup(self.priv.stop)
        self.priv.spawn([sys.executable, os.path.join(os.path.dirname(__file__), "fake_konsole.py"),
                         self.SERVICE, "2"], self.SERVICE)
        from tide_daemon.konsole import KonsoleTabs
        self.tabs = KonsoleTabs(self.priv.bus)

    def state(self) -> dict:
        reply = self.priv.call(self.SERVICE, "/Sessions/1", "org.kde.konsole.Session", "State")
        return json.loads(reply.arguments()[0])

    def test_marks_and_restores_a_tab(self):
        key = f"{self.SERVICE} /Sessions/1"
        self.assertEqual(self.tabs.read(key), {"title": "projekt-1 : bash", "local": "%d : %n",
                                               "remote": "(%u) %H", "color": "#000000"})
        marker = tabmarks.TabMarker(self.tabs, COLORS, None)
        marker.update({key: "waiting"})
        self.assertEqual(self.state()["1"], {"title": "◆ projekt-1 : bash", "local": "◆ %d : %n",
                                             "remote": "◆ (%u) %H", "color": COLORS["waiting"]})
        self.assertEqual(self.state()["2"]["title"], "projekt-2 : bash")
        marker.clear()
        self.assertEqual(self.state()["1"], {"title": "projekt-1 : bash", "local": "%d : %n",
                                             "remote": "(%u) %H", "color": ""})

    def test_missing_konsole_reads_as_closed_tab(self):
        self.assertIsNone(self.tabs.read("org.kde.konsole-1 /Sessions/1"))
        self.assertFalse(self.tabs.apply("org.kde.konsole-1 /Sessions/1", title="x"))


if __name__ == "__main__":
    unittest.main()
