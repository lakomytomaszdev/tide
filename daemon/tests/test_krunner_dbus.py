"""Kontrakt wtyczki KRunnera na prywatnej magistrali: org.kde.krunner1 (Match, Actions, Run)
z sygnaturami, których oczekuje KRunner 6, na syntetycznej historii sesji i tymczasowych
repozytoriach. Programy (systemd-run, konsole, rider, claude, codex) są atrapami z fakebin.py —
Run tylko zapisuje wywołanie, żadne okno nie powstaje.

Klientem jest dbus-python (tak jak w usłudze), a dokładną sygnaturę odpowiedzi sprawdza QtDBus.
"""
import os
import tempfile
import time
import unittest

from . import gitrepos, history
from .fakebin import calls, make_env
from .privatebus import PrivateBus, requires_dbus, wait_until
from tide_daemon.krunner import service
from tide_daemon.worktrees.launchers import KEEP_SHELL

try:
    import dbus
    import dbus.bus
except ImportError:  # pragma: no cover
    dbus = None

try:
    from PyQt6.QtDBus import QDBusMessage
except ImportError:  # pragma: no cover
    pass

U = history.codex_uid


@requires_dbus
@unittest.skipUnless(dbus is not None and service.available(), "brak python3-dbus albo python3-gi")
class KRunnerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-krunner-dbus-")
        cls.p = history.install(cls.tmp)
        cls.root = os.path.join(cls.tmp, "projects")
        cls.repos = gitrepos.build(cls.root)
        cls.env = make_env(cls.tmp)
        cls.fake = os.path.join(cls.tmp, "fakebin")
        cls.service = PrivateBus("tide-krunner-test")
        extra = {k: cls.env[k] for k in ("HOME", "PATH", "TIDE_FAKE_LOG", "XDG_DATA_HOME", "XDG_CONFIG_HOME")}
        cls.daemon = cls.service.spawn_daemon(
            "--claude-dir", cls.p["claude"], "--codex-dir", cls.p["codex"], "--no-processes", "--no-notifications",
            "--now", history.NOW_TEXT, "--workspace", cls.root, extra_env=extra)
        cls.client = dbus.bus.BusConnection(cls.service.address)
        cls.runner = dbus.Interface(cls.client.get_object(service.SERVICE, service.PATH), service.IFACE)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.service.stop()
        history.remove(cls.tmp)

    def match(self, query):
        return self.runner.Match(query, timeout=20)

    def test_introspection_matches_krunner_api(self):
        xml = self.client.get_object(service.SERVICE, service.PATH).Introspect(
            dbus_interface="org.freedesktop.DBus.Introspectable")
        self.assertIn('<interface name="org.kde.krunner1">', xml)
        self.assertIn('<arg direction="out" type="a(sssida{sv})" />', xml)
        self.assertIn('<arg direction="out" type="a(sss)" />', xml)
        self.assertIn('<method name="Run">', xml)

    def test_match_reply_signature(self):
        self.match("login")  # pierwsze zapytanie czeka na zbudowanie indeksu
        reply = self.service.call(service.SERVICE, service.PATH, service.IFACE, "Match", "login")
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        self.assertEqual(reply.signature(), "a(sssida{sv})")

    def test_match_sessions_from_both_tools(self):
        first = self.match("login")[0]
        match_id, text, icon, category_relevance, relevance, props = first
        self.assertEqual((str(match_id), str(text), str(icon)),
                         ("session:claude:h-titled", "Claude · alpha · Login form fix", "utilities-terminal"))
        self.assertIsInstance(category_relevance, dbus.Int32)
        self.assertIsInstance(relevance, dbus.Double)
        self.assertEqual(str(props["subtext"]), "feat/login · 3 dni temu")
        self.assertEqual(str(props["category"]), "Sesje agentów")
        self.assertEqual([str(a) for a in props["actions"]], ["terminal", "rider"])
        texts = [str(m[1]) for m in self.match("ai beta")]
        self.assertIn("Codex · beta · Uploader retries", texts)

    def test_match_projects_and_worktrees(self):
        ids = wait_until(lambda: [str(m[0]) for m in self.match("alpha")
                                  if not str(m[0]).startswith("session:")], timeout=15)
        self.assertIn(f"project:{self.repos['alpha']}", ids)
        wt = [str(m[0]) for m in self.match("feat/unmerged")]
        self.assertIn(f"worktree:{self.repos['unmerged']}", wt)

    def test_actions(self):
        actions = [tuple(str(x) for x in a) for a in self.runner.Actions()]
        self.assertEqual(actions, [("terminal", "Otwórz katalog w terminalu", "utilities-terminal"),
                                   ("rider", "Otwórz w Riderze", "jetbrains-rider")])

    def test_run_resumes_session_in_konsole(self):
        self.match("login")
        self.runner.Run("session:claude:h-titled", "")
        line = wait_until(lambda: next((c for c in calls(self.env) if "--resume h-titled" in c), None))
        self.assertEqual(line, " ".join([
            "systemd-run", "--user", "--scope", "--collect", "--quiet", "--",
            os.path.join(self.fake, "konsole"), "--workdir", self.p["alpha"], "-e", "/bin/sh", "-c", KEEP_SHELL,
            os.path.join(self.fake, "claude"), "--resume", "h-titled"]))

    def test_run_codex_and_project(self):
        self.match("uploader")
        self.runner.Run(f"session:codex:{U(101)}", "")
        self.assertTrue(wait_until(lambda: any(c.endswith(f"codex resume {U(101)}") for c in calls(self.env))))
        wait_until(lambda: self.match("alpha"), timeout=15)
        self.runner.Run(f"project:{self.repos['alpha']}", "rider")
        expected = f"{os.path.join(self.fake, 'rider')} {self.repos['alpha']}"
        self.assertTrue(wait_until(lambda: any(c.endswith(expected) for c in calls(self.env))))

    def test_run_unknown_does_nothing(self):
        before = len(calls(self.env))
        self.runner.Run("project:/etc", "")
        self.runner.Run("session:claude:nie-ma", "")
        self.match("login")  # kolejne wywołanie — Run już się zakończyło
        self.assertEqual(len(calls(self.env)), before)


if __name__ == "__main__":
    unittest.main()


def _embed_route(handler):
    """Atrapa POST /api/embed Ollamy: wektory „pojęć” z test_krunner_semantic (bez prawdziwego modelu)."""
    import json as _json
    from .test_krunner_semantic import concept_vector
    texts = (handler.body or {}).get("input") or []
    data = _json.dumps({"model": "bge-m3", "embeddings": [concept_vector(t) for t in texts]}).encode()
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
    except OSError:
        pass


@requires_dbus
@unittest.skipUnless(dbus is not None and service.available(), "brak python3-dbus albo python3-gi")
class KRunnerMeaningTest(unittest.TestCase):
    """PT-19 na prywatnej magistrali: usługa indeksuje sesje atrapą Ollamy (--search-delay 0), a Match dla
    dłuższego zapytania dopisuje sesję podobną znaczeniowo; po wyłączeniu serwera — same słowa, bez błędu."""

    def setUp(self):
        import json as _json
        import stat as _stat
        from .test_models_core import start_server, write_models
        self._stat = _stat
        self.tmp = tempfile.mkdtemp(prefix="tide-krunner-meaning-")
        self.addCleanup(history.remove, self.tmp)
        self.p = history.install(self.tmp)
        self.env = make_env(self.tmp)
        self.server = start_server(self, {("POST", "/api/embed"): (200, _embed_route),
                                          ("GET", "/api/version"): (200, {"version": "0.40.11"})})
        models = write_models(self.tmp, {"endpoints": {"local": {"url": self.server.url, "kind": "ollama"}}})
        self.bus = PrivateBus("tide-krunner-meaning")
        self.addCleanup(self.bus.stop)
        extra = {k: self.env[k] for k in ("HOME", "PATH", "TIDE_FAKE_LOG", "XDG_DATA_HOME", "XDG_CONFIG_HOME")}
        extra["TIDE_MODELS_CONFIG"] = models
        self.bus.spawn_daemon("--claude-dir", self.p["claude"], "--codex-dir", self.p["codex"], "--no-processes",
                              "--no-notifications", "--now", history.NOW_TEXT, "--workspace", self.tmp,
                              "--search-delay", "0", extra_env=extra)
        self.client = dbus.bus.BusConnection(self.bus.address)
        self.addCleanup(self.client.close)
        self.runner = dbus.Interface(self.client.get_object(service.SERVICE, service.PATH), service.IFACE)
        self.index_file = os.path.join(self.env["XDG_DATA_HOME"], "tide", "search", "sessions.json")
        self._json = _json

    def ids(self, query):
        return [(str(m[0]), str(m[5]["subtext"])) for m in self.runner.Match(query, timeout=20)]

    def test_meaning_results_and_word_results_when_server_is_down(self):
        self.assertTrue(wait_until(lambda: os.path.exists(self.index_file), timeout=20), "indeks zapisany")
        from tide_daemon.krunner import semantic
        stored = semantic.Store(self.index_file)   # format 2: dziennik JSON Lines (K-98 #5, PT-346)
        self.assertEqual(stored.load(), "")
        self.assertEqual(stored.model, "bge-m3:latest")
        self.assertIn("claude:h-titled", stored.items)
        self.assertEqual(self._stat.S_IMODE(os.stat(self.index_file).st_mode), 0o600)
        posts = [r for r in self.server.requests if r["path"] == "/api/embed"]
        self.assertTrue(all(r["body"]["keep_alive"] == "2m" for r in posts))

        query = "gdzie poprawiałem formularz logowania"
        found = wait_until(lambda: [i for i in self.ids(query) if i[0] == "session:claude:h-titled"], timeout=10)
        self.assertTrue(found, self.ids(query))
        self.assertTrue(found[0][1].endswith("podobne znaczeniowo"), found)

        self.server.shutdown()
        self.server.server_close()
        started = time.monotonic()
        result = self.runner.Match("inna sesja o logowaniu i hasłach", timeout=20)
        self.assertLess(time.monotonic() - started, 3.0, "serwer nie odpowiada — KRunner nie czeka")
        self.assertEqual([str(m[0]) for m in result], [], "same słowa (tu żadnych), bez błędu D-Bus")
        self.assertEqual([str(m[0]) for m in self.runner.Match("login", timeout=20)][:1], ["session:claude:h-titled"])
