"""Okno Tide z paskiem widoków.

- pakiet: tide.py, views.json, QML paska, moduły widoków w views/<id> z własną biblioteką,
  przekierowania dawnych okien; bez kolorów wpisanych na sztywno; spójny views.json,
- CLI: widok na start, --data dla widoku i WIDOK=PLIK, --preview; dawne opcje okien Sesje
  i Worktrees tłumaczone na okno Tide,
- backendy w trybie podglądu: plakietki Sesji (czekający agenci) i Worktrees (w pracy), widoki
  bez modułu bez plakietki,
- plakietki na prywatnej magistrali: bez tide-daemon i bez obiektów /Board, /Docker, /Reports
  ukryte i bez błędów; zaczep Badge() atrapy usługi pokazuje się i odświeża po Changed,
- okno na prywatnej magistrali: Show(widok, element) przełącza widok (CurrentView), nieznany widok
  → False, dawne Raise z org.tide.SessionsWindow i org.tide.WorktreesWindow, drugie uruchomienie
  (także przez dawny launcher) tylko prosi pierwsze okno o pokazanie się,
- zrzuty offscreen każdego widoku z danymi z pliku bez ostrzeżeń QML,
- silnik QML okna bez sieci (guard_network): obrazek z http nie łączy się z serwerem,
- tide-daemon (Launchers): okno Tide na widoku, gdy jest zainstalowane, inaczej dawne okno,
- instalacja, reinstalacja na dawnych oknach i odinstalowanie na tymczasowym HOME z atrapami.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from . import APP_DIR, REPO
from apps.sessions.tests.test_window import fixture_data
from apps.worktrees.tests.test_window import sample
from daemon.tests.fakebin import calls, files_under, make_env
from daemon.tests.privatebus import PrivateBus, requires_dbus, wait_until

sys.path.insert(0, os.path.join(REPO, "daemon"))
from tide_daemon.worktrees.launchers import Launchers  # noqa: E402

QML_PROBLEMS = r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|is not defined|" \
               r"Cannot read|unavailable|Invalid property|Binding loop|failed to load|Only binding to one"
VIEWS = ["board", "issues", "sessions", "worktrees", "docker", "reports", "models"]
WINDOW = ("org.tide.Window", "/Window", "org.tide.Window")


def stage(out: str) -> str:
    subprocess.run(["/usr/bin/python3", os.path.join(REPO, "widgets", "stage.py"), "--app", APP_DIR, out], check=True)
    return out


def load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tide_module():
    return load(os.path.join(APP_DIR, "tide.py"), "tide_window_launcher")


def offscreen_env(base=None) -> dict:
    env = dict(os.environ if base is None else base, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
    return env


class PackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-win-pkg-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_package_layout(self):
        files = files_under(self.pkg)
        for name in ("tide.py", "views.json", "qml/Main.qml", "qml/Rail.qml", "qml/RailItem.qml", "qml/RailIcon.qml",
                     "qml/RailInfo.qml", "qml/TideWaves.qml", "qml/PlaceholderView.qml", "qml/tide/Tokens.qml",
                     "views/sessions/backend.py", "views/sessions/qml/SessionsView.qml",
                     "views/sessions/qml/tide/Tokens.qml", "views/worktrees/backend.py",
                     "views/worktrees/qml/WorktreesView.qml", "views/worktrees/qml/tide/Tokens.qml",
                     "compat/tide-sessions.py", "compat/tide-worktrees.py"):
            self.assertIn(name, files)
        self.assertFalse([f for f in files if "/tests/" in f or f.startswith("tests/")
                          or f.endswith(("__init__.py", "install.sh"))])

    def test_no_hardcoded_colors(self):
        for name in os.listdir(os.path.join(APP_DIR, "qml")):
            with open(os.path.join(APP_DIR, "qml", name), encoding="utf-8") as fh:
                self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', fh.read()), [], name)

    def test_rail_texts_are_plain(self):
        """Teksty paska i pustego stanu (plakietki i railInfo mogą przyjść z usługi) — nigdy jako HTML."""
        for name in ("RailItem.qml", "RailInfo.qml", "PlaceholderView.qml"):
            with open(os.path.join(APP_DIR, "qml", name), encoding="utf-8") as fh:
                source = fh.read()
            blocks = re.findall(r"\n(\s*)Text \{\n(.*?)\n\1\}", source, re.S)
            self.assertTrue(blocks, name)
            for _indent, body in blocks:
                self.assertIn("textFormat: Text.PlainText", body, f"{name}: {body[:80]}")

    def test_views_registry_is_consistent(self):
        with open(os.path.join(APP_DIR, "views.json"), encoding="utf-8") as fh:
            views = json.load(fh)["views"]
        self.assertEqual([v["id"] for v in views], VIEWS, "kolejność paska jak na mocku")
        self.assertEqual([v["title"] for v in views], ["Tablica", "Sprawy", "Sesje", "Worktrees", "Docker", "Raporty", "Modele"])
        self.assertEqual(sum(1 for v in views if v.get("default")), 1)
        with open(os.path.join(APP_DIR, "qml", "RailIcon.qml"), encoding="utf-8") as fh:
            icons = fh.read()
        for view in views:
            self.assertIn(f'"{view["icon"]}":', icons, view["id"])
            if view.get("module"):
                self.assertTrue(os.path.isfile(os.path.join(REPO, view["module"], "backend.py")), view["id"])
                self.assertTrue(os.path.isfile(os.path.join(REPO, view["module"], "qml", view["qml"])), view["id"])
            else:
                self.assertTrue(view["placeholder"]["title"] and view["placeholder"]["text"], view["id"])
                self.assertTrue(view["badge"]["path"].startswith("/"), view["id"])


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tide = tide_module()
        cls.views = cls.tide.load_views(APP_DIR)

    def parse(self, *argv):
        return self.tide.parse_args(list(argv), self.views)

    def test_view_select_data_and_preview(self):
        args = self.parse()
        self.assertEqual((args.view, args.initial_view, args.select, args.data_files), ("", "board", "", {}),
                         "bez --view: domyślny widok (Tablica) tylko na start, działające okno dostaje pusty widok")
        self.assertEqual(self.parse("--data", "s.json").data_files, {"board": "s.json"})
        args = self.parse("--view", "worktrees", "--select", "/w/a", "--data", "w.json", "--data", "sessions=s.json",
                          "--preview", "pick=/w/a", "--preview", "pick=/w/b", "--preview", "confirm=1")
        self.assertEqual((args.view, args.select), ("worktrees", "/w/a"))
        self.assertEqual(args.data_files, {"worktrees": "w.json", "sessions": "s.json"})
        self.assertEqual(args.preview_map, {"pick": ["/w/a", "/w/b"], "confirm": ["1"]})
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.parse("--view", "nieznany")
        with self.assertRaises(SystemExit):
            self.parse("--preview", "bez-znaku-rownosci")

    def test_legacy_launchers_translate_old_options(self):
        sessions = load(os.path.join(APP_DIR, "compat", "tide-sessions.py"), "legacy_sessions")
        self.assertEqual(sessions.translate(["--select", "claude:a", "--data", "d.json", "--grab", "x.png",
                                             "--ticket", "TOM-1", "--result", "r1", "--width", "900", "--height", "700"]),
                         ["--view", "sessions", "--select", "claude:a", "--data", "d.json", "--grab", "x.png",
                          "--preview", "ticket=TOM-1", "--preview", "result=r1", "--width", "900", "--height", "700"])
        self.assertEqual(sessions.translate([]), ["--view", "sessions"])
        worktrees = load(os.path.join(APP_DIR, "compat", "tide-worktrees.py"), "legacy_worktrees")
        self.assertEqual(worktrees.translate(["--data", "w.json", "--select", "/a", "--select", "/b", "--confirm"]),
                         ["--view", "worktrees", "--data", "w.json", "--preview", "pick=/a", "--preview", "pick=/b",
                          "--preview", "confirm=1"])
        for module in (sessions, worktrees):
            parsed = self.parse(*module.translate(["--data", "d.json"]))
            self.assertIn(parsed.view, ("sessions", "worktrees"))

    def test_legacy_launcher_without_tide_window_fails_clearly(self):
        tmp = tempfile.mkdtemp(prefix="tide-win-legacy-")
        self.addCleanup(shutil.rmtree, tmp)
        script = os.path.join(tmp, "sessions-window", "tide-sessions.py")
        os.makedirs(os.path.dirname(script))
        shutil.copy(os.path.join(APP_DIR, "compat", "tide-sessions.py"), script)
        env = dict(os.environ, TIDE_WINDOW="")
        result = subprocess.run(["/usr/bin/python3", script], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn("nie ma okna Tide", result.stderr)


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class RailQmlTest(unittest.TestCase):
    """Pasek z atrapą okna: plakietki z backendów, link i ramka railInfo (umowa widoku)."""

    def test_rail_badges_link_and_box(self):
        tmp = tempfile.mkdtemp(prefix="tide-win-rail-")
        self.addCleanup(shutil.rmtree, tmp)
        pkg = stage(os.path.join(tmp, "pkg"))
        path = os.path.join(pkg, "qml", "RailSmoke.qml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("""import QtQuick
Item {
    width: 200; height: 700
    QtObject { id: waiting; property string badgeText: "● 2"; property string badgeTone: "waiting" }
    QtObject {
        id: shell
        function backend(id) { return id === "sessions" ? waiting : null }
    }
    property var requests: []
    property int activated: 0
    Rail {
        id: rail
        anchors.fill: parent
        views: [{id: "board", title: "Tablica", icon: "board"}, {id: "sessions", title: "Sesje", icon: "sessions"}]
        current: "board"
        shell: shell
        onViewRequested: (id, item) => requests.push(id + ":" + item)
    }
    Component.onCompleted: {
        rail.info = {link: {text: "Projekty i tagi", view: "board", item: "projekty"}}
        const onlyLink = [rail.footer.visible, rail.footer.hasBox].join("|")
        rail.footer.activateLink()
        rail.info = {link: {text: "Akcja", activate: function () { activated++ }},
                     label: "Dane tablicy", value: "~/DevWorkspace/tide-board", note: "<b>lokalne</b> repo"}
        rail.footer.activateLink()
        const withBox = [rail.footer.visible, rail.footer.hasBox].join("|")
        rail.info = null
        console.warn("RAIL " + [onlyLink, requests.join(","), activated, withBox, rail.footer.visible].join("|"))
        Qt.quit()
    }
}
""")
        env = offscreen_env()
        log = subprocess.run(["qml6", path], env=env, capture_output=True, text=True, timeout=60).stderr
        match = re.search(r"RAIL (.*)$", log, re.M)
        self.assertIsNotNone(match, log[-3000:])
        self.assertEqual(match.group(1).strip(), "true|false|board:projekty|1|true|true|false")
        self.assertIsNone(re.search(QML_PROBLEMS, log), log[-3000:])


class BackendsTest(unittest.TestCase):
    """make_backends w trybie podglądu: każdy widok ma backend, plakietki z danych z pliku."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.tmp = tempfile.mkdtemp(prefix="tide-win-backends-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))
        cls.tide = load(os.path.join(cls.pkg, "tide.py"), "tide_window_staged")
        cls.views = cls.tide.load_views(cls.pkg)
        cls.sessions = fixture_data(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fixture_badges(self):
        backends = self.tide.make_backends(self.views, None, {"sessions": self.sessions, "worktrees": sample()},
                                           base=self.pkg)
        self.assertEqual(sorted(backends), sorted(VIEWS))
        self.assertEqual((backends["sessions"].badgeText, backends["sessions"].badgeTone), ("● 1", "waiting"),
                         "jeden agent czeka na odpowiedź")
        self.assertEqual((backends["worktrees"].badgeText, backends["worktrees"].badgeTone), ("1", "muted"),
                         "jeden worktree w pracy (bez głównych katalogów)")
        for view in ("board", "docker", "reports", "models"):
            self.assertEqual(backends[view].badgeText, "", view)
        self.assertTrue(backends["sessions"].available and backends["worktrees"].available)

    def test_backends_survive_transient_qml_references(self):
        """Backend zwrócony do QML przez shell.backend(id) należy do okna — GC JavaScriptu go nie usuwa."""
        from PyQt6 import sip
        from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
        from PyQt6.QtQml import QQmlComponent, QQmlEngine
        backends = self.tide.make_backends(self.views, None, {"sessions": self.sessions}, base=self.pkg)
        shell = self.tide.Shell(self.views, backends)
        engine = QQmlEngine()
        component = QQmlComponent(engine)
        component.setData(b'import QtQml\nQtObject { property var shell\n'
                          b'  function poke() { for (const id of ["board", "sessions", "worktrees", "docker", '
                          b'"reports", "models"]) shell.backend(id) } }', QUrl())
        holder = component.createWithInitialProperties({"shell": shell})
        holder.poke()
        for _ in range(5):
            engine.collectGarbage()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
            QCoreApplication.processEvents()
        for view, backend in backends.items():
            self.assertFalse(sip.isdeleted(backend), view)
            self.assertIs(backend.parent(), shell, view)
            self.assertEqual(QQmlEngine.objectOwnership(backend), QQmlEngine.ObjectOwnership.CppOwnership, view)
        self.assertEqual(backends["sessions"].badgeText, "● 1")

    def test_views_without_data_are_empty(self):
        backends = self.tide.make_backends(self.views, None, {"worktrees": sample()}, base=self.pkg)
        self.assertFalse(backends["sessions"].available)
        self.assertEqual(backends["sessions"].badgeText, "")
        self.assertEqual(json.loads(backends["sessions"].coordinatorsJson), [])

    def test_badge_rules(self):
        sessions = load(os.path.join(self.pkg, "views", "sessions", "backend.py"), "sessions_backend")
        self.assertEqual(sessions.waiting_count(json.dumps([{"state": "waiting", "reason": "question"},
                                                            {"state": "waiting", "reason": "reply"},
                                                            {"state": "working"}])), 2,
                         "„reply” to pytanie na końcu tury (PT-29) — też czeka")
        self.assertEqual(sessions.waiting_count("nie json"), 0)
        worktrees = load(os.path.join(self.pkg, "views", "worktrees", "backend.py"), "worktrees_backend")
        self.assertEqual(worktrees.active_count(json.dumps([{"group": "active"}, {"group": "active", "isMain": True},
                                                            {"group": "clean"}])), 1)
        self.assertEqual(self.tide.parse_badge('{"text": "11", "tone": "waiting"}'), ("11", "waiting"))
        self.assertEqual(self.tide.parse_badge('{"text": "nowy", "tone": "dziwny"}'), ("nowy", "muted"))
        self.assertEqual(self.tide.parse_badge("[]"), ("", "muted"))
        self.assertEqual(self.tide.parse_badge(None), ("", "muted"))


@requires_dbus
class BadgeOverDbusTest(unittest.TestCase):
    """Plakietki na prywatnej magistrali: bez usługi i bez obiektów — ukryte, z zaczepem — widoczne."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-win-badge-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))
        cls.tide = load(os.path.join(cls.pkg, "tide.py"), "tide_window_badges")
        cls.views = cls.tide.load_views(cls.pkg)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.bus = PrivateBus("tide-win-badge")
        self.addCleanup(self.bus.stop)

    def test_without_daemon_every_badge_is_hidden(self):
        backends = self.tide.make_backends(self.views, self.bus.bus, None, base=self.pkg)
        wait_until(lambda: False, timeout=1.0)  # odpowiedzi (błędy) z magistrali zdążą dojść
        self.assertEqual({k: b.badgeText for k, b in backends.items()}, {k: "" for k in VIEWS})
        self.assertFalse(backends["sessions"].available or backends["worktrees"].available)

    def test_badge_hook_of_fake_daemon(self):
        from PyQt6.QtCore import QObject, pyqtClassInfo, pyqtSlot
        from PyQt6.QtDBus import QDBusConnection, QDBusMessage

        # Zaczep plakietki ma tylko widok bez modułu (pierwszy z views.json) — widoki z modułem liczą ją same.
        hooks = [v for v in self.views if not v.get("module") and v.get("badge")]
        if not hooks:
            self.skipTest("każdy widok ma już moduł — nie ma zaczepu plakietki do sprawdzenia")
        view, others = hooks[0], hooks[1:]
        spec = view["badge"]

        @pyqtClassInfo("D-Bus Interface", spec["interface"])
        class FakeArea(QObject):
            text = "11"

            @pyqtSlot(result=str)
            def Badge(self):
                return json.dumps({"text": self.text, "tone": "muted"})

        daemon = QDBusConnection.connectToBus(self.bus.address, "tide-win-fake-daemon")
        self.addCleanup(QDBusConnection.disconnectFromBus, "tide-win-fake-daemon")
        area = FakeArea()
        self.assertTrue(daemon.registerObject(spec["path"], area, QDBusConnection.RegisterOption.ExportAllSlots))
        self.assertTrue(daemon.registerService("org.tide.Daemon"))
        backends = self.tide.make_backends(self.views, self.bus.bus, None, base=self.pkg)
        backend = backends[view["id"]]
        self.assertTrue(wait_until(lambda: backend.badgeText == "11", timeout=5), f"zaczep {spec['path']} Badge()")
        area.text = "12"
        daemon.send(QDBusMessage.createSignal(spec["path"], spec["interface"], "Changed"))
        self.assertTrue(wait_until(lambda: backend.badgeText == "12", timeout=5), "odświeżenie po Changed")
        wait_until(lambda: False, timeout=0.5)
        self.assertEqual([backends[v["id"]].badgeText for v in others], ["" for _ in others],
                         "usługa bez pozostałych obiektów — plakietki ukryte")
        daemon.unregisterService("org.tide.Daemon")
        self.assertTrue(wait_until(lambda: backend.badgeText == "", timeout=5), "usługa znika — plakietka też")


@requires_dbus
class KeepWindowStateTest(unittest.TestCase):
    def test_show_request_keeps_maximized_window(self):
        # Uwaga użytkownika: „czemu mi zmienia rozmiar okna? zmaksymalizowałem sobie”. Window.show() w Qt
        # wraca do zwykłego rozmiaru, więc Show() z usługi nie może go wołać na widocznym oknie.
        with open(os.path.join(REPO, "apps", "tide", "qml", "Main.qml"), encoding="utf-8") as fh:
            source = fh.read()
        handler = source[source.index("function onShowRequested"):]
        handler = handler[:handler.index("\n        }\n")]
        self.assertNotIn("win.show()", handler)
        self.assertIn("win.lastVisibility", handler, "zminimalizowane okno wraca do stanu sprzed minimalizacji")
        self.assertIn("lastVisibility = visibility", source)


class WindowOverDbusTest(unittest.TestCase):
    """Okno (offscreen) na prywatnej magistrali bez tide-daemon."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-win-bus-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = stage(os.path.join(self.tmp, "pkg"))
        self.bus = PrivateBus("tide-win-test")
        self.addCleanup(self.bus.stop)
        self.env = offscreen_env(self.bus.env)
        self.window = subprocess.Popen(["/usr/bin/python3", os.path.join(self.pkg, "tide.py"), "--view", "board"],
                                       env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: self.window.poll() is None and self.window.kill())
        self.assertTrue(wait_until(lambda: self.bus.bus.interface().isServiceRegistered(WINDOW[0]).value(),
                                   timeout=15), "okno rejestruje org.tide.Window")
        self.assertTrue(wait_until(lambda: self.current() == "board", timeout=10))

    def current(self) -> str:
        reply = self.bus.call(*WINDOW, "CurrentView")
        return reply.arguments()[0] if reply.arguments() else ""

    def show(self, view, item="") -> bool:
        reply = self.bus.call(*WINDOW, "Show", view, item)
        self.assertEqual(reply.errorMessage(), "")
        return reply.arguments()[0]

    def finish(self):
        self.window.terminate()
        _out, err = self.window.communicate(timeout=10)
        self.assertIsNone(re.search(QML_PROBLEMS, err or ""), err)

    def test_show_switches_views_and_rejects_unknown(self):
        for view in ("worktrees", "docker", "reports", "models", "sessions", "board"):
            self.assertTrue(self.show(view))
            self.assertTrue(wait_until(lambda: self.current() == view, timeout=5), view)
        self.assertFalse(self.show("nieznany"))
        self.assertTrue(self.show("", "claude:x"), "pusty widok — zostaje bieżący")
        self.assertEqual(self.current(), "board")
        self.finish()

    def test_legacy_raise_calls_open_the_right_view(self):
        names = self.bus.bus.interface().registeredServiceNames().value()
        self.assertIn("org.tide.SessionsWindow", names)
        self.assertIn("org.tide.WorktreesWindow", names)
        reply = self.bus.call("org.tide.WorktreesWindow", "/Window", "org.tide.WorktreesWindow", "Raise")
        self.assertEqual(reply.errorMessage(), "")
        self.assertTrue(wait_until(lambda: self.current() == "worktrees", timeout=5))
        reply = self.bus.call("org.tide.SessionsWindow", "/Window", "org.tide.SessionsWindow", "Raise", "claude:x")
        self.assertEqual(reply.errorMessage(), "")
        self.assertTrue(wait_until(lambda: self.current() == "sessions", timeout=5))
        self.finish()

    def test_second_start_without_view_keeps_current_view(self):
        """Menu, dock, KRunner „Tide”: tide.py bez --view tylko pokazuje okno, nie przełącza widoku."""
        self.assertTrue(self.show("worktrees"))
        self.assertTrue(wait_until(lambda: self.current() == "worktrees", timeout=5))
        for argv in ([], ["--select", "/w/x"]):
            second = subprocess.run(["/usr/bin/python3", os.path.join(self.pkg, "tide.py"), *argv], env=self.env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(second.returncode, 0, second.stderr)
            wait_until(lambda: False, timeout=0.5)
            self.assertEqual(self.current(), "worktrees", argv)
        self.finish()

    def test_second_start_only_asks_running_window(self):
        started = time.time()
        grab = os.path.join(self.tmp, "x.png")
        second = subprocess.run(["/usr/bin/python3", os.path.join(self.pkg, "tide.py"), "--view", "docker",
                                 "--grab", grab], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertLess(time.time() - started, 15)
        self.assertFalse(os.path.exists(grab), "drugie uruchomienie nie otwiera okna")
        self.assertTrue(wait_until(lambda: self.current() == "docker", timeout=5))
        legacy = subprocess.run(["/usr/bin/python3", os.path.join(self.pkg, "compat", "tide-worktrees.py")],
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertTrue(wait_until(lambda: self.current() == "worktrees", timeout=5), "dawny launcher Worktrees")
        legacy = subprocess.run(["/usr/bin/python3", os.path.join(self.pkg, "compat", "tide-sessions.py"),
                                 "--select", "claude:x"], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertTrue(wait_until(lambda: self.current() == "sessions", timeout=5), "dawny launcher Sesje")
        self.assertIsNone(self.window.poll(), "pierwsze okno działa dalej")
        self.finish()


NETWORK_PROBE = r"""
import json, socket, sys, threading
sys.path.insert(0, sys.argv[1])
from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine
import tide
app = QGuiApplication([])
server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen(8)
server.settimeout(0.2)
port = server.getsockname()[1]
hits = []
def accept():
    while not done:
        try:
            conn, _ = server.accept()
            hits.append(1)
            conn.close()
        except OSError:
            pass
done = False
threading.Thread(target=accept, daemon=True).start()
engine = QQmlApplicationEngine()
factory = tide.guard_network(engine) if sys.argv[2] == "guard" else None
engine.loadData(("import QtQuick\nWindow { visible: true; width: 200; height: 200; property int st: img.status\n"
                 "  Image { id: img; source: 'http://127.0.0.1:%d/a.png' }\n"
                 "  TextEdit { textFormat: TextEdit.RichText; text: '<img src=\"http://127.0.0.1:%d/b.png\">' } }"
                 % (port, port)).encode(), QUrl.fromLocalFile("/tmp/probe.qml"))
def finish():
    global done
    done = True
    try:
        print(json.dumps({"hits": len(hits), "status": engine.rootObjects()[0].property("st")}))
    finally:
        app.quit()
QTimer.singleShot(1500, finish)
app.exec()
"""


class NetworkGuardTest(unittest.TestCase):
    """Silnik QML okna nie wpuszcza żadnych żądań sieciowych (druga warstwa po oczyszczaniu treści)."""

    def run_probe(self, mode):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", no_proxy="*", NO_PROXY="*")
        env.pop("http_proxy", None)
        env.pop("HTTP_PROXY", None)
        result = subprocess.run(["/usr/bin/python3", "-c", NETWORK_PROBE, APP_DIR, mode], env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_remote_image_is_refused_without_connection(self):
        self.assertGreater(self.run_probe("plain")["hits"], 0, "kontrola: bez strażnika Image łączy się z serwerem")
        guarded = self.run_probe("guard")
        self.assertEqual(guarded["hits"], 0, "ze strażnikiem — żadnego połączenia")
        self.assertEqual(guarded["status"], 3, "Image.Error")

    def test_window_installs_the_guard(self):
        with open(os.path.join(APP_DIR, "tide.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("network_factory = guard_network(engine)", source)


class GrabTest(unittest.TestCase):
    """Zrzuty każdego widoku z danymi z pliku (bez magistrali)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-win-grab-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))
        cls.sessions = os.path.join(cls.tmp, "sessions.json")
        with open(cls.sessions, "w", encoding="utf-8") as fh:
            json.dump(fixture_data(cls.tmp), fh)
        cls.worktrees = os.path.join(cls.tmp, "worktrees.json")
        with open(cls.worktrees, "w", encoding="utf-8") as fh:
            json.dump(sample(), fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def grab(self, name, *argv) -> str:
        png = os.path.join(self.tmp, name + ".png")
        env = offscreen_env()
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)  # --data nie potrzebuje magistrali
        result = subprocess.run(["/usr/bin/python3", os.path.join(self.pkg, "tide.py"), *argv, "--grab", png,
                                 "--width", "1600", "--height", "900"], env=env, capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIsNone(re.search(QML_PROBLEMS, result.stderr), result.stderr[-3000:])
        with open(png, "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        return png

    def test_every_view_renders(self):
        data = ["--data", "sessions=" + self.sessions, "--data", "worktrees=" + self.worktrees]
        sizes = {view: os.path.getsize(self.grab(view, "--view", view, *data)) for view in VIEWS}
        self.assertGreater(sizes["sessions"], 60_000, "widok Sesje z tablicą z pliku")
        self.assertGreater(sizes["worktrees"], 60_000, "widok Worktrees z listą z pliku")
        for view in ("board", "docker", "reports", "models"):
            self.assertGreater(sizes[view], 15_000, view)

    def test_data_for_shown_view_and_select(self):
        first = self.grab("first", "--view", "sessions", "--data", self.sessions)
        codex = self.grab("codex", "--view", "sessions", "--data", self.sessions, "--select",
                          next(c["id"] for c in fixture_data(os.path.join(self.tmp, "w2"))["coordinators"]
                               if c["id"].startswith("codex:")))
        with open(first, "rb") as a, open(codex, "rb") as b:
            self.assertNotEqual(a.read(), b.read(), "--select otwiera innego koordynatora")
        empty = self.grab("empty", "--view", "worktrees", "--data", "sessions=" + self.sessions)
        self.assertGreater(os.path.getsize(empty), 10_000, "widok bez danych ma pusty stan, nie błąd")


class LaunchersTest(unittest.TestCase):
    """tide-daemon otwiera okno Tide na widoku; bez niego — dawne okno (zgodność i testy usługi)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-win-launch-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.data = os.path.join(self.tmp, "data")
        self.tide = os.path.join(self.data, "tide", "tide-window", "tide.py")

    def launchers(self, **extra):
        launchers = Launchers(env={"HOME": self.tmp, "XDG_DATA_HOME": self.data, "PATH": "", **extra}, path="")
        launchers.spawned = []
        launchers.spawn = lambda argv, path: launchers.spawned.append(argv) or True
        return launchers

    def touch(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()

    def test_tide_window_opens_at_view(self):
        launchers = self.launchers()
        self.assertFalse(launchers.show_sessions("claude:a"), "nic nie jest zainstalowane")
        self.assertEqual(launchers.sessions_window, "", "KRunner nie proponuje „Tide Sesje”")
        self.touch(self.tide)
        self.assertTrue(launchers.show_sessions("claude:a"))
        self.assertTrue(launchers.show_window())
        self.assertTrue(launchers.show_view("docker", "abc"))
        self.assertEqual(launchers.spawned, [
            ["/usr/bin/python3", "-s", self.tide, "--view", "sessions", "--select", "claude:a"],
            ["/usr/bin/python3", "-s", self.tide, "--view", "worktrees"],
            ["/usr/bin/python3", "-s", self.tide, "--view", "docker", "--select", "abc"]])
        self.assertEqual(launchers.sessions_window, self.tide)
        self.assertTrue(launchers.available()["window"], "widżet worktrees ma „Otwórz okno”")
        self.assertEqual(launchers.command("window", ""), ["/usr/bin/python3", "-s", self.tide, "--view", "worktrees"])

    def test_legacy_window_without_tide_and_explicit_override(self):
        legacy = os.path.join(self.data, "tide", "sessions-window", "tide-sessions.py")
        self.touch(legacy)
        launchers = self.launchers()
        self.assertTrue(launchers.show_sessions("claude:a"))
        self.assertEqual(launchers.spawned[-1], ["/usr/bin/python3", "-s", legacy, "--select", "claude:a"])
        self.assertFalse(launchers.show_view("docker"), "dawne okna nie znają Dockera")
        self.touch(self.tide)
        fake = os.path.join(self.tmp, "fake-sessions.py")
        self.touch(fake)
        overridden = self.launchers(TIDE_SESSIONS_WINDOW=fake)
        self.assertTrue(overridden.show_sessions())
        self.assertEqual(overridden.spawned[-1], ["/usr/bin/python3", "-s", fake], "test wskazuje okno wprost")
        self.assertTrue(overridden.show_window())
        self.assertEqual(overridden.spawned[-1][2:4], [self.tide, "--view"])
        custom = self.launchers(TIDE_WINDOW=os.path.join(self.tmp, "brak.py"))
        self.assertFalse(custom.show_view("board"), "$TIDE_WINDOW wskazuje brakujący plik")


@unittest.skipUnless(shutil.which("make"), "brak make")
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-win-install-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.data = self.env["XDG_DATA_HOME"]
        self.dest = os.path.join(self.data, "tide", "tide-window")

    def make(self, target):
        subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True, capture_output=True, text=True)

    def read(self, rel):
        with open(os.path.join(self.data, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_install_reinstall_over_old_windows_and_uninstall(self):
        # Dawne okna zainstalowane przed aktualizacją: pełne pakiety i pliki .desktop ze starym znacznikiem.
        old = os.path.join(self.data, "tide", "sessions-window", "qml", "Main.qml")
        os.makedirs(os.path.dirname(old))
        open(old, "w").close()
        os.makedirs(os.path.join(self.data, "applications"))
        with open(os.path.join(self.data, "applications", "tide-worktrees.desktop"), "w", encoding="utf-8") as fh:
            fh.write("[Desktop Entry]\nName=Tide Worktrees\nX-Tide-Component=worktrees-window\n")

        self.make("install-tide-window")
        files = files_under(self.dest)
        for name in ("tide.py", "views.json", "qml/Main.qml", "qml/tide/Tokens.qml", "views/sessions/backend.py",
                     "views/worktrees/qml/WorktreesView.qml"):
            self.assertIn(name, files)
        self.assertTrue(os.access(os.path.join(self.dest, "tide.py"), os.X_OK))
        main = self.read("applications/tide.desktop")
        self.assertIn("Name=Tide\n", main)
        self.assertIn(f"Exec=/usr/bin/python3 -s {self.dest}/tide.py\n", main)
        self.assertIn("StartupWMClass=tide\n", main)
        self.assertIn("Icon=tide\n", main)
        self.assertIn("Actions=board;sessions;worktrees;docker;reports;models;\n", main)
        self.assertIn(f"Exec=/usr/bin/python3 -s {self.dest}/tide.py --view reports\n", main)
        self.assertIn("X-Tide-Component=tide-window\n", main)
        for view, name, title in (("sessions", "tide-sessions", "Tide Sesje"), ("worktrees", "tide-worktrees",
                                                                               "Tide Worktrees")):
            entry = self.read(f"applications/{name}.desktop")
            self.assertIn(f"Name={title}\n", entry)
            self.assertIn(f"Exec=/usr/bin/python3 -s {self.dest}/tide.py --view {view}\n", entry)
            self.assertIn(f"Icon={name}\n", entry)
            self.assertIn("X-Tide-Component=tide-window\n", entry)
            wrapper = os.path.join(self.data, "tide", f"{view}-window", f"{name}.py")
            self.assertTrue(os.access(wrapper, os.X_OK), wrapper)
            self.assertEqual(files_under(os.path.dirname(wrapper)), [f"{name}.py"], "tylko przekierowanie")
            with open(os.path.join(self.data, f"icons/hicolor/256x256/apps/{name}.png"), "rb") as fh:
                self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertIn("<svg", self.read("icons/hicolor/scalable/apps/tide.svg"))
        if shutil.which("desktop-file-validate"):
            for name in ("tide", "tide-sessions", "tide-worktrees"):
                path = os.path.join(self.data, "applications", name + ".desktop")
                check = subprocess.run(["desktop-file-validate", path], capture_output=True, text=True)
                self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn("kbuildsycoca6", [c.strip() for c in calls(self.env)])

        # Przekierowanie z zainstalowanego miejsca znajduje okno Tide obok (bez $TIDE_WINDOW).
        legacy = load(os.path.join(self.data, "tide", "worktrees-window", "tide-worktrees.py"), "installed_legacy")
        self.assertEqual(legacy.tide_window(), os.path.join(self.dest, "tide.py"))

        # Reinstalacja nie zostawia starych plików; dawne cele make prowadzą do okna Tide.
        stale = os.path.join(self.dest, "qml", "Stary.qml")
        open(stale, "w").close()
        self.make("install-sessions-window")
        self.assertFalse(os.path.exists(stale))

        self.make("uninstall-tide-window")
        self.assertEqual(files_under(self.data), [])

    def test_install_does_not_overwrite_foreign_desktop_file(self):
        os.makedirs(os.path.join(self.data, "applications"))
        foreign = os.path.join(self.data, "applications", "tide.desktop")
        with open(foreign, "w", encoding="utf-8") as fh:
            fh.write("[Desktop Entry]\nName=Cudze\n")
        result = subprocess.run(["make", "-s", "-C", REPO, "install-tide-window"], env=self.env, capture_output=True,
                                text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nie jest plikiem Tide", result.stderr)
        self.assertEqual(self.read("applications/tide.desktop"), "[Desktop Entry]\nName=Cudze\n")
        self.assertIn("X-Tide-Component=tide-window\n", self.read("applications/tide-sessions.desktop"))
        self.make("uninstall-tide-window")
        self.assertEqual(files_under(self.data), ["applications/tide.desktop"])

    def test_uninstall_keeps_foreign_files(self):
        os.makedirs(os.path.join(self.data, "applications"))
        for name in ("tide", "tide-sessions"):
            with open(os.path.join(self.data, "applications", name + ".desktop"), "w", encoding="utf-8") as fh:
                fh.write("[Desktop Entry]\nName=Cudze\n")
        daemon_file = os.path.join(self.data, "tide", "daemon", "x")
        os.makedirs(os.path.dirname(daemon_file))
        open(daemon_file, "w").close()
        self.make("uninstall-tide-window")
        self.assertTrue(os.path.exists(os.path.join(self.data, "applications", "tide.desktop")))
        self.assertTrue(os.path.exists(os.path.join(self.data, "applications", "tide-sessions.desktop")))
        self.assertTrue(os.path.exists(daemon_file))


if __name__ == "__main__":
    unittest.main()
