"""Okno Tide Sesje.

- pakiet: launcher, QML i biblioteka z Tokens.qml; bez kolorów wpisanych na sztywno,
- logika okna (sessions.js) w QJSEngine: kolumny, postęp, teksty kart, domyślny ticket, pojedyncze sesje,
- zrzuty offscreen z --data (tablica z syntetycznego świata coordfix.py, wynik podagenta) bez ostrzeżeń QML,
- dane po zakonserwowaniu (tools/sessions_preview.py --sanitize): bez treści, z tą samą strukturą,
- launcher na prywatnej magistrali z prawdziwym tide-daemon: lista i tablica z /Coordination, drugie
  uruchomienie tylko prosi pierwsze o pokazanie się,
- instalacja i odinstalowanie na tymczasowym HOME z atrapami narzędzi.
"""
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
from daemon.tests import coordfix as F
from daemon.tests.fakebin import calls, files_under, make_env
from daemon.tests.privatebus import PrivateBus, requires_dbus, wait_until

sys.path.insert(0, os.path.join(REPO, "daemon"))
from tide_daemon.coordination import board as board_mod, describe  # noqa: E402
from tide_daemon.coordination.scanner import CoordinationScanner  # noqa: E402

QML_PROBLEMS = r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|is not defined|" \
               r"Cannot read|unavailable|Invalid property|Binding loop|failed to load"
LIVE = [{"id": "claude:" + F.CLAUDE_COORD, "state": "working", "reason": "", "canActivate": True,
         "tool": "claude", "project": "demo"},
        {"id": "codex:" + F.CODEX_ROOT, "state": "waiting", "reason": "question", "canActivate": True,
         "tool": "codex", "project": "demo"},
        {"id": "claude:" + F.CLAUDE_SINGLE, "state": "working", "reason": "", "canActivate": True,
         "tool": "claude", "project": "TimeTracker"}]


def stage(out: str) -> str:
    subprocess.run(["/usr/bin/python3", os.path.join(REPO, "widgets", "stage.py"), "--app", APP_DIR, out], check=True)
    return out


def fixture_data(tmp: str) -> dict:
    world = F.build_world(os.path.join(tmp, "world"))
    scanner = CoordinationScanner(world["claude"], world["codex"], clock=lambda: F.NOW)
    boards = scanner.refresh(LIVE)
    describe.Describer(mode="rules").decorate(boards)  # jak usługa: opisy kart (tu z reguł)
    details = {"a140rev00000000001": scanner.run_detail("claude:" + F.CLAUDE_COORD, "a140rev00000000001")}
    return {"coordinators": [board_mod.summary(b) for b in boards], "boards": {b["id"]: b for b in boards},
            "sessions": LIVE, "details": details, "now": F.NOW}


def grab(pkg: str, data_path: str, png: str, *extra, env=None) -> str:
    env = dict(os.environ if env is None else env, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
    result = subprocess.run(["/usr/bin/python3", os.path.join(pkg, "tide-sessions.py"), "--data", data_path,
                             "--grab", png, "--width", "1680", "--height", "980", *extra],
                            env=env, capture_output=True, text=True, timeout=60)
    return result.stderr


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-sesswin-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = stage(os.path.join(self.tmp, "pkg"))

    def test_package_contains_launcher_qml_and_library(self):
        files = files_under(self.pkg)
        for name in ("tide-sessions.py", "qml/Main.qml", "qml/SessionsPage.qml", "qml/TicketCard.qml",
                     "qml/TicketDetail.qml", "qml/ResultPanel.qml", "qml/sessions.js", "qml/tide/Tokens.qml",
                     "qml/tide/Theme.qml"):
            self.assertIn(name, files)
        self.assertFalse([f for f in files if f.startswith("tests/") or f.endswith(("__init__.py", "install.sh"))])

    def test_no_hardcoded_colors(self):
        for name in os.listdir(os.path.join(APP_DIR, "qml")):
            with open(os.path.join(APP_DIR, "qml", name), encoding="utf-8") as fh:
                self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', fh.read()), [], name)


class LogicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtQml import QJSEngine
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.engine = QJSEngine()
        with open(os.path.join(APP_DIR, "qml", "sessions.js"), encoding="utf-8") as fh:
            source = fh.read().replace(".pragma library", "")
        result = cls.engine.evaluate(source)
        assert not result.isError(), result.toString()

    def js(self, expression):
        value = self.engine.evaluate(f"JSON.stringify({expression})")
        self.assertFalse(value.isError(), value.toString())
        return json.loads(value.toString())

    def test_columns_progress_and_cards(self):
        board = json.dumps({"counts": {"gotowe": 2, "review": 1, "w-toku": 4, "czeka": 1, "total": 10, "done": 2},
                            "tickets": [{"key": "a", "column": "todo", "state": "todo", "inPlan": True},
                                        {"key": "b", "column": "w-toku", "state": "w-toku", "lastActivityTs": 5},
                                        {"key": "c", "column": "w-toku", "state": "w-toku", "lastActivityTs": 9},
                                        {"key": "d", "column": "czeka", "state": "czeka", "lastActivityTs": 1}]})
        self.assertEqual(self.js(f"column({board}.tickets, 'w-toku').map(t => t.key)"), ["c", "b"])
        self.assertEqual(self.js(f"progressText({board})"), "2 z 10 ticketów gotowych")
        self.assertEqual(self.js(f"percent({board})"), "20%")
        self.assertEqual(self.js(f"progressParts({board}).map(p => p.tint)"), ["mint", "lavender", "teal", "peach"])
        self.assertEqual(self.js(f"defaultTicket({board})"), "d", "najpierw to, co czeka")
        self.assertEqual(self.js("cardFooter({state: 'todo', inPlan: true})"), "w planie · bez agenta")
        self.assertEqual(self.js("cardFooter({state: 'gotowe', review: {ok: true}})"), "✓ review OK")
        self.assertEqual(self.js("cardFooter({state: 'gotowe', note: 'bez review'})"), "✓ bez review")

    def test_card_line_state_words_and_roles(self):
        done = "{state: 'gotowe', note: 'review OK', done: 'Kolumna działa', now: 'Review OK'}"
        self.assertEqual(self.js(f"cardLine({done})"), "Kolumna działa", "gotowe — co zrobiono")
        self.assertEqual(self.js(f"stateLine({done})"), "✓ review OK")
        self.assertEqual(self.js(f"stateTint({done})"), "mint")
        waiting = "{state: 'review', note: 'gotowe, czeka na review', done: 'Eksport działa', now: 'Gotowe, czeka na review'}"
        self.assertEqual(self.js(f"cardLine({waiting})"), "Eksport działa")
        self.assertEqual(self.js(f"stateLine({waiting})"), "gotowe, czeka na review")
        self.assertEqual(self.js(f"stateTint({waiting})"), "lavender")
        running = ("{state: 'w-toku', note: '', done: 'x', now: 'Trwają poprawki',"
                   " agents: [{state: 'running', role: 'implementer', word: 'poprawki'}, {state: 'completed'}]}")
        self.assertEqual(self.js(f"cardLine({running})"), "Trwają poprawki", "w toku — co teraz")
        self.assertEqual(self.js(f"stateLine({running})"), "", "działający podagenci zamiast słowa stanu")
        self.assertEqual(self.js(f"runningAgents({running}).map(a => agentWord(a))"), ["poprawki"])
        self.assertEqual(self.js("stateLine({state: 'w-toku', note: ''})"), "w trakcie")
        self.assertEqual(self.js("stateLine({state: 'w-toku', note: 'po review: 1 MAJOR — do poprawy'})"),
                         "po review — do poprawy")
        self.assertEqual(self.js("stateLine({state: 'w-toku', note: 'podagent zatrzymany'})"), "zatrzymane")
        self.assertEqual(self.js("cardLine({state: 'todo', now: 'Jeszcze nie zaczęte'})"), "")
        self.assertEqual(self.js("agentWord({role: 'helper'})"), "rozpoznanie")
        self.assertEqual(self.js("titleSources({title: 'Kolumna', officialTitle: 'Value column', officialFrom: 'tracker',"
                                 " taskTitle: 'Implement ticket 05'})"),
                         "w YouTracku: „Value column” · zlecenie: „Implement ticket 05”")
        self.assertEqual(self.js("sourceText({source: 'runs', describedBy: 'model'})"),
                         "stan z pracy podagentów · opis: model")
        self.assertEqual(self.js("duration(2 * 3600 + 14 * 60)"), "2 h 14 min")
        self.assertEqual(self.js("agentTime({state: 'running', startTs: 100}, 130)"), "teraz")
        self.assertEqual(self.js("agentTime({state: 'completed', startTs: 0, endTs: 0}, 130)"), "")
        self.assertEqual(self.js("singleSessions([{id: 'a', state: 'working'}, {id: 'b', state: 'ended'},"
                                 " {id: 'c', state: 'working'}], [{id: 'c'}]).map(s => s.id)"), ["a"])


class GrabTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-sesswin-grab-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = stage(os.path.join(self.tmp, "pkg"))
        self.data = fixture_data(self.tmp)
        self.path = os.path.join(self.tmp, "data.json")
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh)

    def assert_png(self, png, log):
        self.assertTrue(os.path.getsize(png) > 20_000 if os.path.exists(png) else False, log[-3000:])
        self.assertIsNone(re.search(QML_PROBLEMS, log), log[-3000:])

    def test_board_detail_and_result_render_without_warnings(self):
        png = os.path.join(self.tmp, "board.png")
        self.assert_png(png, grab(self.pkg, self.path, png, "--ticket", "TOM-140"))
        png = os.path.join(self.tmp, "codex.png")
        self.assert_png(png, grab(self.pkg, self.path, png, "--select", "codex:" + F.CODEX_ROOT))
        png = os.path.join(self.tmp, "result.png")
        self.assert_png(png, grab(self.pkg, self.path, png, "--ticket", "TOM-140", "--result", "a140rev00000000001"))

    def test_sanitized_data_keeps_structure_and_drops_content(self):
        out = subprocess.run(["/usr/bin/python3", os.path.join(REPO, "tools", "sessions_preview.py"), self.path,
                              "--sanitize"], capture_output=True, text=True, check=True).stdout
        for secret in ("Distribution guard", "przenieść lokalny backlog", "MAJOR-1: guard", "DistributionGuard.cs",
                       "Release candidate TOM-20", "Implement TOM-140", "tom-140-guard", "guard i 6 testów",
                       "implement distribution guard", "Retry dodany"):
            self.assertNotIn(secret, out)
        data = json.loads(out)
        board = data["boards"]["claude:" + F.CLAUDE_COORD]
        original = self.data["boards"]["claude:" + F.CLAUDE_COORD]
        self.assertEqual([(t["id"], t["state"]) for t in board["tickets"]],
                         [(t["id"], t["state"]) for t in original["tickets"]])
        self.assertEqual(board["counts"], original["counts"])
        sanitized = os.path.join(self.tmp, "sanitized.json")
        with open(sanitized, "w", encoding="utf-8") as fh:
            fh.write(out)
        png = os.path.join(self.tmp, "sanitized.png")
        self.assert_png(png, grab(self.pkg, sanitized, png))


@requires_dbus
class BackendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-sesswin-bus-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = stage(os.path.join(self.tmp, "pkg"))
        self.world = F.build_world(os.path.join(self.tmp, "world"))
        self.bus = PrivateBus("tide-sesswin-test")
        self.addCleanup(self.bus.stop)
        workspace = os.path.join(self.tmp, "ws")
        os.makedirs(workspace)
        self.bus.spawn_daemon("--claude-dir", self.world["claude"], "--codex-dir", self.world["codex"],
                              "--workspace", workspace, "--no-processes", "--no-notifications", "--no-krunner",
                              "--no-idle", "--no-limits", "--no-report", "--no-youtrack",
                              "--now", "2026-10-26T12:00:00Z")
        self.assertTrue(wait_until(lambda: self._listed(), timeout=15))

    def _listed(self):
        reply = self.bus.call("org.tide.Daemon", "/Coordination", "org.tide.Daemon.Coordination", "Coordinators")
        return bool(reply.arguments()) and len(json.loads(reply.arguments()[0])) == 2

    def test_window_reads_daemon_and_second_start_only_raises(self):
        env = dict(self.bus.env, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        first = subprocess.Popen(["/usr/bin/python3", os.path.join(self.pkg, "tide-sessions.py"),
                                  "--select", "codex:" + F.CODEX_ROOT], env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: first.poll() is None and first.kill())
        self.assertTrue(wait_until(lambda: self.bus.bus.interface().isServiceRegistered(
            "org.tide.SessionsWindow").value(), timeout=15), "okno rejestruje swoją nazwę")
        started = time.time()
        second = subprocess.run(["/usr/bin/python3", os.path.join(self.pkg, "tide-sessions.py"), "--select",
                                 "claude:" + F.CLAUDE_COORD, "--grab", os.path.join(self.tmp, "x.png")],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertLess(time.time() - started, 15)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x.png")), "drugie uruchomienie nie otwiera okna")
        self.assertIsNone(first.poll(), "pierwsze okno działa dalej")
        first.terminate()
        _out, err = first.communicate(timeout=10)
        self.assertIsNone(re.search(QML_PROBLEMS, err or ""), err)

    def test_grab_from_daemon(self):
        env = dict(self.bus.env, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        png = os.path.join(self.tmp, "live.png")
        result = subprocess.run(["/usr/bin/python3", os.path.join(self.pkg, "tide-sessions.py"), "--grab", png],
                                env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.getsize(png) > 20_000)
        self.assertIsNone(re.search(QML_PROBLEMS, result.stderr), result.stderr)


class InstallTest(unittest.TestCase):
    def test_install_then_uninstall(self):
        tmp = tempfile.mkdtemp(prefix="tide-sesswin-install-")
        self.addCleanup(shutil.rmtree, tmp)
        env = make_env(tmp)
        script = os.path.join(APP_DIR, "install.sh")
        subprocess.run(["bash", script, "install"], env=env, check=True, capture_output=True)
        dest = os.path.join(env["XDG_DATA_HOME"], "tide", "sessions-window")
        self.assertTrue(os.path.isfile(os.path.join(dest, "tide-sessions.py")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "qml", "tide", "Tokens.qml")))
        desktop = os.path.join(env["XDG_DATA_HOME"], "applications", "tide-sessions.desktop")
        with open(desktop, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Name=Tide Sesje", text)
        self.assertIn("StartupWMClass=tide-sessions", text)
        self.assertIn("X-Tide-Component=sessions-window", text)
        self.assertTrue(os.path.isfile(os.path.join(env["XDG_DATA_HOME"], "icons/hicolor/256x256/apps/tide-sessions.png")))
        self.assertTrue(any(c.startswith("kbuildsycoca6") for c in calls(env)))
        subprocess.run(["bash", script, "uninstall"], env=env, check=True, capture_output=True)
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(desktop))


if __name__ == "__main__":
    unittest.main()
