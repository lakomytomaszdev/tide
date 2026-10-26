"""Codex Desktop: odnośnik codex://threads/<id> do wątku głównego i podagenci zwinięci do rodzica.

Bez prawdziwej aplikacji: plik .desktop i program to atrapy w katalogu tymczasowym
(fakebin.py: `chatgpt` i `systemd-run` tylko zapisują wywołanie), a uruchomienie w większości
testów przechwytuje atrapa Popen. Dzienniki są syntetyczne, pisane w teście.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from .fakebin import calls, make_env
from tide_daemon import attention, codex_app, windows
from tide_daemon.agents import AgentScanner, scan
from tide_daemon.agents.codex import CodexLog
from tide_daemon.agents.scanner import root_thread
from tide_daemon.agents.timeutil import parse_ts
from tide_daemon.codex_app import (CodexApp, exec_argv, find_handler, graphical_env, parse_environment,
                                   thread_url)
from tide_daemon.processes import ProcessInfo, ProcessTable

NOW = parse_ts("2026-10-26T12:00:00Z")
DESKTOP = """[Desktop Entry]
Name=ChatGPT
Exec=chatgpt %U
Type=Application
MimeType=x-scheme-handler/codex;x-scheme-handler/http;
"""


def uid(n: int) -> str:
    return f"0000000c-0000-4000-8000-{n:012d}"


def at(text: str) -> str:
    return f"2026-10-26T{text}.000Z"


def meta(sid, originator="Codex Desktop", parent=None, root=None, nickname="", kind="thread_spawn", cwd="/srv/w"):
    payload = {"id": sid, "timestamp": at("11:00:00"), "cwd": cwd, "originator": originator,
               "git": {"branch": "main"}}
    if parent is None and kind == "thread_spawn":
        payload["source"] = "vscode" if originator != "codex_cli_rs" else "cli"
        payload["session_id"] = sid
    elif kind == "thread_spawn":
        payload["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1,
                                                           "agent_nickname": nickname}}}
        payload["parent_thread_id"] = parent
        payload["session_id"] = root or ""
    else:  # recenzent zgód
        payload["source"] = {"subagent": {"other": kind}}
        payload["parent_thread_id"] = parent
        payload["session_id"] = root or parent
    return {"timestamp": at("11:00:00"), "type": "session_meta", "payload": payload}


def event(kind, ts, **extra):
    return {"timestamp": at(ts), "type": "event_msg", "payload": {"type": kind, **extra}}


def call(name, ts, call_id="c1"):
    return {"timestamp": at(ts), "type": "response_item",
            "payload": {"type": "function_call", "name": name, "call_id": call_id, "arguments": "{}"}}


DONE = [event("task_started", "11:10:00"), event("task_complete", "11:20:00")]
WORKING = [event("task_started", "11:59:00")]
ASKS = [event("task_started", "11:40:00"), call("request_user_input", "11:41:00")]


class Logs:
    """Katalog Codexa z dziennikami pisanymi w teście."""

    def __init__(self, test):
        self.tmp = tempfile.mkdtemp(prefix="tide-codex-desktop-")
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.codex = os.path.join(self.tmp, "codex")
        self.day = os.path.join(self.codex, "sessions", "2026", "10", "26")
        os.makedirs(self.day)
        self.paths = {}

    def write(self, sid, records):
        path = os.path.join(self.day, f"rollout-2026-10-26T11-00-00-{sid}.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")
        last = parse_ts(records[-1]["timestamp"])
        os.utime(path, (last, last))
        self.paths[sid] = path
        return path


class FakeApp:
    """Zamiast CodexApp: zapisuje otwierane wątki."""

    def __init__(self, ready=True):
        self.ready = ready
        self.opened = []

    def available(self):
        return self.ready

    def open_thread(self, thread_id):
        self.opened.append(thread_id)
        return True


# --- odnośnik i polecenie ---------------------------------------------------------------------

class UrlTest(unittest.TestCase):
    def test_thread_url_only_for_uuid(self):
        self.assertEqual(thread_url(uid(1)), f"codex://threads/{uid(1)}")
        for bad in ("", "abc", f"{uid(1)}/../settings", f"{uid(1)} --flag", "codex://threads/x"):
            self.assertEqual(thread_url(bad), "", bad)

    def test_desktop_originators(self):
        self.assertTrue(codex_app.is_desktop("Codex Desktop"))
        self.assertTrue(codex_app.is_desktop("codex_work_desktop"))
        self.assertFalse(codex_app.is_desktop("codex_cli_rs"))
        self.assertFalse(codex_app.is_desktop(""))


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-codex-app-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = make_env(self.tmp)
        del self.env["TIDE_CODEX_DESKTOP"]  # szukanie w katalogach XDG jak w usłudze
        self.env["XDG_DATA_DIRS"] = os.path.join(self.tmp, "system")
        self.fake = os.path.join(self.tmp, "fakebin")
        self.apps = os.path.join(self.env["XDG_DATA_HOME"], "applications")
        os.makedirs(self.apps)

    def desktop(self, name="chatgpt.desktop", text=DESKTOP, where=None):
        path = os.path.join(where or self.apps, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_exec_line_with_field_codes(self):
        chatgpt = os.path.join(self.fake, "chatgpt")
        url = thread_url(uid(1))
        self.assertEqual(exec_argv("chatgpt %U", url, self.env["PATH"]), [chatgpt, url])
        self.assertEqual(exec_argv("chatgpt --new-window %u %i %c", url, self.env["PATH"]),
                         [chatgpt, "--new-window", url])
        # Bez kodu pola odnośnik idzie na koniec; cudzysłowy jak w specyfikacji Exec.
        self.assertEqual(exec_argv('"chatgpt" --flag="a b"', url, self.env["PATH"]),
                         [chatgpt, "--flag=a b", url])
        self.assertEqual(exec_argv("nie-ma-takiego %U", url, self.env["PATH"]), [])
        self.assertEqual(exec_argv('chatgpt "niezamknięty', url, self.env["PATH"]), [])

    def test_handler_lookup(self):
        self.assertEqual(find_handler(self.env), "")
        # Inny program z obsługą codex: (np. inna paczka) — zapasowo po MimeType.
        other = self.desktop("openai-codex.desktop", where=os.path.join(self.tmp, "system", "applications"))
        self.desktop("firefox.desktop", "[Desktop Entry]\nExec=firefox %u\nMimeType=x-scheme-handler/http;\n")
        self.assertEqual(find_handler(self.env), other)
        # chatgpt.desktop ma pierwszeństwo, plik użytkownika przed systemowym.
        mine = self.desktop()
        self.assertEqual(find_handler(self.env), mine)
        self.desktop(text=DESKTOP + "Hidden=true\n")
        self.assertEqual(find_handler(self.env), other)

    def test_command_is_an_argv_list_in_a_scope(self):
        self.desktop()
        app = CodexApp(env=self.env)
        self.assertTrue(app.available())
        self.assertEqual(app.command(uid(7)), [
            os.path.join(self.fake, "systemd-run"), "--user", "--scope", "--collect", "--quiet", "--",
            os.path.join(self.fake, "chatgpt"), f"codex://threads/{uid(7)}"])
        self.assertEqual(app.command("nie-uuid"), [])

    def test_without_app_nothing_is_available(self):
        app = CodexApp(env=self.env)
        self.assertFalse(app.available())
        self.assertFalse(app.open_thread(uid(1)))
        # Program z Exec zniknął — też niedostępna.
        self.desktop()
        os.remove(os.path.join(self.fake, "chatgpt"))
        self.assertFalse(CodexApp(env=dict(self.env, PATH=self.fake)).available())

    def test_override_points_at_a_file_or_disables(self):
        self.desktop()
        self.assertFalse(CodexApp(env=dict(self.env, TIDE_CODEX_DESKTOP="")).available())
        other = self.desktop("x.desktop", where=os.path.join(self.tmp, "elsewhere"))
        self.assertEqual(CodexApp(env=dict(self.env, TIDE_CODEX_DESKTOP=other)).handler(), other)

    def test_open_thread_passes_argv_and_graphical_env(self):
        self.desktop()
        env = {k: v for k, v in self.env.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY")}
        launched = []

        class Proc:
            def wait(self):
                return 0

        def popen(argv, **kwargs):
            launched.append((argv, kwargs))
            return Proc()

        manager = mock.Mock(return_value={"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":1", "PATH": "/zly"})
        app = CodexApp(env=env, manager=manager, popen=popen)
        self.assertTrue(app.open_thread(uid(3)))
        argv, kwargs = launched[0]
        self.assertEqual(argv[-2:], [os.path.join(self.fake, "chatgpt"), f"codex://threads/{uid(3)}"])
        self.assertNotIn("shell", kwargs)
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual((kwargs["env"]["WAYLAND_DISPLAY"], kwargs["env"]["DISPLAY"]), ("wayland-1", ":1"))
        self.assertEqual(kwargs["env"]["PATH"], self.env["PATH"], "uzupełniamy tylko zmienne graficzne")

    def test_open_thread_runs_the_handler(self):
        # Prawdziwe uruchomienie, ale atrapy: systemd-run i chatgpt z fakebin tylko zapisują wywołanie.
        self.desktop()
        env = dict(self.env, WAYLAND_DISPLAY="wayland-test")
        self.assertTrue(CodexApp(env=env).open_thread(uid(4)))
        deadline = time.monotonic() + 5
        while not calls(env) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(calls(env), [
            f"systemd-run --user --scope --collect --quiet -- {os.path.join(self.fake, 'chatgpt')} "
            f"codex://threads/{uid(4)}"])


class EnvironmentTest(unittest.TestCase):
    def test_parse_show_environment(self):
        text = "WAYLAND_DISPLAY=wayland-1\nDISPLAY=:1\nWEIRD=$'a\\'b'\nnot a line\n=x\n"
        self.assertEqual(parse_environment(text), {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":1", "WEIRD": "a'b"})

    def test_display_present_means_no_lookup(self):
        manager = mock.Mock(return_value={"WAYLAND_DISPLAY": "inny"})
        self.assertEqual(graphical_env({"WAYLAND_DISPLAY": "wayland-0"}, manager)["WAYLAND_DISPLAY"], "wayland-0")
        self.assertEqual(graphical_env({"DISPLAY": ":0"}, manager), {"DISPLAY": ":0"})
        manager.assert_not_called()

    def test_missing_display_is_taken_from_the_user_manager(self):
        manager = mock.Mock(return_value={"WAYLAND_DISPLAY": "wayland-1", "XDG_RUNTIME_DIR": "/run/user/1",
                                          "HOME": "/obcy"})
        env = graphical_env({"HOME": "/home/u", "XDG_RUNTIME_DIR": "/run/user/1000"}, manager)
        self.assertEqual(env, {"HOME": "/home/u", "XDG_RUNTIME_DIR": "/run/user/1000", "WAYLAND_DISPLAY": "wayland-1"})

    def test_manager_failure_is_harmless(self):
        def broken(*_a, **_k):
            raise OSError("brak systemctl")
        self.assertEqual(codex_app.manager_environment(run=broken), {})
        failed = mock.Mock(return_value=subprocess.CompletedProcess([], 1, "", "err"))
        self.assertEqual(codex_app.manager_environment(run=failed), {})


# --- dziennik i zwijanie podagentów -----------------------------------------------------------

class SubagentLogTest(unittest.TestCase):
    def feed(self, *records):
        log = CodexLog(f"rollout-x-{uid(99)}.jsonl")
        for record in records:
            log.feed(record)
        return log

    def test_thread_spawn_keeps_parent_root_and_state(self):
        log = self.feed(meta(uid(2), parent=uid(1), root=uid(1), nickname="Pascal"), *WORKING)
        self.assertEqual((log.hidden, log.parent_id, log.root_hint, log.nickname), (True, uid(1), uid(1), "Pascal"))
        self.assertEqual(log.phase, "mid_turn", "stan podagenta jest liczony, bo trafia do rodzica")

    def test_approval_reviewer_is_skipped_entirely(self):
        log = self.feed(meta(uid(3), parent=uid(1), kind="guardian"), *WORKING)
        self.assertEqual((log.hidden, log.parent_id, log.last_activity), (True, "", parse_ts(at("11:00:00"))))

    def test_root_thread_follows_known_parents(self):
        logs = {uid(n): self.feed(m) for n, m in (
            (1, meta(uid(1))),
            (2, meta(uid(2), parent=uid(1), root=uid(1))),
            (3, meta(uid(3), parent=uid(2), root=uid(1))),
            (4, meta(uid(4), parent=uid(3), root="")),  # bez podpowiedzi — i tak po łańcuchu
        )}
        logs[uid(1)].session_id = uid(1)
        for n in (2, 3, 4):
            logs[uid(n)].session_id = uid(n)
        for n in (1, 2, 3, 4):
            self.assertEqual(root_thread(logs[uid(n)], logs), uid(1), n)

    def test_root_thread_with_missing_parent(self):
        orphan = self.feed(meta(uid(5), parent=uid(50), root=uid(60)))
        orphan.session_id = uid(5)
        self.assertEqual(root_thread(orphan, {uid(5): orphan}), uid(60), "payload.session_id to wątek główny")
        bare = self.feed(meta(uid(6), parent=uid(50), root=""))
        bare.session_id = uid(6)
        self.assertEqual(root_thread(bare, {uid(6): bare}), uid(50), "bez podpowiedzi — nieznany rodzic")
        child = self.feed(meta(uid(7), parent=uid(6), root=""))
        child.session_id = uid(7)
        self.assertEqual(root_thread(child, {uid(6): bare, uid(7): child}), uid(50))

    def test_parent_cycle_does_not_hang(self):
        a = self.feed(meta(uid(8), parent=uid(9), root=""))
        b = self.feed(meta(uid(9), parent=uid(8), root=""))
        a.session_id, b.session_id = uid(8), uid(9)
        self.assertIn(root_thread(a, {uid(8): a, uid(9): b}), (uid(8), uid(9)))


class FoldingTest(unittest.TestCase):
    """Lista sesji: podagenci zwinięci do rodzica, stan najpilniejszy, wątek główny do otwarcia."""

    def setUp(self):
        self.logs = Logs(self)
        w = self.logs.write
        w(uid(1), [meta(uid(1))] + DONE)                                                 # wątek główny: skończył
        w(uid(2), [meta(uid(2), parent=uid(1), root=uid(1), nickname="Pascal")] + WORKING)  # podagent: pracuje
        w(uid(3), [meta(uid(3), parent=uid(2), root=uid(1))] + ASKS)                     # pod-podagent: pyta
        w(uid(4), [meta(uid(4), parent=uid(1), kind="guardian")] + WORKING)              # recenzent zgód
        w(uid(5), [meta(uid(5), parent=uid(50), root=uid(60), nickname="Ada")] + WORKING)  # rodzic nieznany
        w(uid(6), [meta(uid(6), originator="codex_cli_rs")] + DONE)                      # CLI w Konsoli
        w(uid(7), [meta(uid(7), originator="codex_cli_rs", parent=uid(6), root=uid(6))] + WORKING)

    def scanner(self, app=None, procs=None):
        scanner = AgentScanner(os.path.join(self.logs.tmp, "brak"), self.logs.codex, codex_app=app)
        scanner.discover(NOW)
        scanner.update()
        return scanner, {s["id"]: s for s in scanner.sessions(NOW, procs)}

    def test_subagents_fold_into_the_root_with_the_most_urgent_state(self):
        _scanner, sessions = self.scanner(FakeApp())
        self.assertEqual(sorted(sessions), [f"codex:{uid(n)}" for n in (1, 5, 6)])
        root = sessions[f"codex:{uid(1)}"]
        # Pod-podagent pyta → cała rozmowa czeka (czeka > pracuje > skończył).
        self.assertEqual((root["state"], root["reason"], root["subagents"], root["subagent"]),
                         ("waiting", "question", 2, False))
        self.assertEqual(root["since"], "2026-10-26T11:41:00Z")
        self.assertEqual(root["lastActivityTs"], NOW - 60, "ostatnia aktywność całej grupy")
        cli = sessions[f"codex:{uid(6)}"]
        self.assertEqual((cli["state"], cli["subagents"]), ("working", 1))

    def test_parent_state_wins_a_tie_and_less_urgent_children(self):
        self.logs.write(uid(3), [meta(uid(3), parent=uid(2), root=uid(1))] + DONE)
        self.logs.write(uid(1), [meta(uid(1))] + ASKS)
        _scanner, sessions = self.scanner(FakeApp())
        root = sessions[f"codex:{uid(1)}"]
        self.assertEqual((root["state"], root["since"]), ("waiting", "2026-10-26T11:41:00Z"))

    def test_orphan_subagent_is_listed_with_a_label(self):
        _scanner, sessions = self.scanner(FakeApp())
        orphan = sessions[f"codex:{uid(5)}"]
        self.assertEqual((orphan["subagent"], orphan["agentNickname"], orphan["subagents"]), (True, "Ada", 0))
        self.assertEqual(orphan["state"], "working")

    def test_desktop_sessions_open_the_root_thread(self):
        scanner, sessions = self.scanner(FakeApp())
        self.assertEqual(scanner.app_threads, {f"codex:{uid(1)}": uid(1), f"codex:{uid(5)}": uid(60)})
        for sid in (f"codex:{uid(1)}", f"codex:{uid(5)}"):
            s = sessions[sid]
            self.assertEqual((s["canActivate"], s["activation"], s["terminal"]), (True, "deeplink", "Codex"), sid)
        # Codex z wiersza poleceń bez procesu i okna — jak dotąd nieklikalny.
        cli = sessions[f"codex:{uid(6)}"]
        self.assertEqual((cli["canActivate"], cli["activation"], cli["terminal"]), (False, "", ""))

    def test_without_the_app_desktop_sessions_need_a_window(self):
        for app in (None, FakeApp(ready=False)):
            scanner, sessions = self.scanner(app)
            self.assertEqual(scanner.app_threads, {})
            self.assertEqual({s["activation"] for s in sessions.values()}, {""})
            self.assertFalse(any(s["canActivate"] for s in sessions.values()))

    def test_waiting_subagent_is_a_jump_target_through_its_root(self):
        _scanner, sessions = self.scanner(FakeApp())
        self.assertEqual(attention.longest_waiting(list(sessions.values()))["id"], f"codex:{uid(1)}")

    def test_cli_subagent_uses_the_parent_terminal(self):
        procs = ProcessTable([
            ProcessInfo(100, 1, "systemd"),
            ProcessInfo(300, 100, "konsole"),
            ProcessInfo(301, 300, "bash"),
            ProcessInfo(700, 301, "codex", tool="codex", cwd="/srv/w",
                        open_files=(self.logs.paths[uid(6)], self.logs.paths[uid(7)]),
                        env={"KONSOLE_DBUS_SERVICE": ":1.5", "KONSOLE_DBUS_WINDOW": "/Windows/1",
                             "KONSOLE_DBUS_SESSION": "/Sessions/2"}),
        ])
        scanner, sessions = self.scanner(FakeApp(), procs)
        cli = sessions[f"codex:{uid(6)}"]
        self.assertEqual((cli["state"], cli["canActivate"], cli["activation"], cli["terminal"], cli["subagents"]),
                         ("working", True, "window", "Konsole", 1))
        self.assertEqual(scanner.bound_pids, {f"codex:{uid(6)}": 700}, "zwinięty podagent nie jest osobnym celem")
        self.assertNotIn(f"codex:{uid(6)}", scanner.app_threads)

    def test_desktop_subagents_held_by_the_app_are_counted(self):
        held = tuple(self.logs.paths[uid(n)] for n in (1, 2, 3, 5))
        procs = ProcessTable([
            ProcessInfo(10, 1, "systemd"),
            ProcessInfo(20, 10, "ChatGPT"),
            ProcessInfo(21, 20, "ChatGPT"),
            ProcessInfo(30, 20, "codex", tool="codex", cwd="/", open_files=held),
        ])
        scanner, sessions = self.scanner(FakeApp(), procs)
        root = sessions[f"codex:{uid(1)}"]
        self.assertEqual((root["state"], root["subagents"], root["activation"]), ("waiting", 2, "deeplink"))
        self.assertEqual(set(scanner.bound_pids), {f"codex:{uid(1)}", f"codex:{uid(5)}"})

    def test_scan_keeps_the_old_listing_without_subagents(self):
        sessions = scan(os.path.join(self.logs.tmp, "brak"), self.logs.codex, NOW)
        self.assertEqual(len(sessions), 3)


# --- przejście do sesji ---------------------------------------------------------------------

class ActivateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        from tide_daemon.monitor import AgentMonitor
        self.logs = Logs(self)
        self.logs.write(uid(1), [meta(uid(1))] + DONE)
        self.logs.write(uid(3), [meta(uid(3), parent=uid(1), root=uid(1))] + ASKS)
        self.logs.write(uid(6), [meta(uid(6), originator="codex_cli_rs")] + DONE)
        self.app = FakeApp()
        scanner = AgentScanner(os.path.join(self.logs.tmp, "brak"), self.logs.codex, codex_app=self.app)
        scanner.discover(NOW)
        scanner.update()
        self.monitor = AgentMonitor(scanner, use_processes=True, clock=lambda: NOW)
        self.procs = ProcessTable([
            ProcessInfo(10, 1, "systemd"),
            ProcessInfo(20, 10, "ChatGPT", start_time=5.0),
            ProcessInfo(21, 20, "ChatGPT", start_time=6.0),   # proces potomny Electrona — nie ma okna
            # codex app-server trzyma dzienniki wątku i podagenta
            ProcessInfo(30, 20, "codex", tool="codex", cwd="/",
                        open_files=(self.logs.paths[uid(1)], self.logs.paths[uid(3)])),
        ])
        self.monitor._probe = lambda: self.procs
        self.raised = []
        patcher = mock.patch.object(windows, "activate", side_effect=lambda t: self.raised.append(t) or True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_desktop_session_opens_its_thread_and_raises_the_app(self):
        self.assertTrue(self.monitor.activate(f"codex:{uid(1)}"))
        self.assertEqual(self.app.opened, [uid(1)])
        # Okno z łańcucha przodków app-servera (ChatGPT), jak dotąd.
        self.assertEqual(self.raised, [{"pids": [20], "label": "Codex"}])

    def test_meta_a_on_a_waiting_subagent_opens_the_root_thread(self):
        self.assertTrue(self.monitor.activate_longest_waiting())
        self.assertEqual(self.app.opened, [uid(1)])

    def test_unbound_desktop_session_raises_the_main_app_process(self):
        self.procs = ProcessTable([p for p in self.procs.by_pid.values() if p.pid != 30])
        self.assertTrue(self.monitor.activate(f"codex:{uid(1)}"))
        self.assertEqual(self.raised, [{"pids": [20], "label": "Codex"}])

    def test_app_not_running_still_opens_the_thread(self):
        self.procs = ProcessTable([ProcessInfo(10, 1, "systemd")])
        self.assertTrue(self.monitor.activate(f"codex:{uid(1)}"))
        self.assertEqual((self.app.opened, self.raised), ([uid(1)], []))

    def test_cli_session_without_window_is_not_opened_in_the_app(self):
        self.assertFalse(self.monitor.activate(f"codex:{uid(6)}"))
        self.assertEqual((self.app.opened, self.raised), ([], []))

    def test_failed_launch_reports_false(self):
        self.app.open_thread = mock.Mock(side_effect=OSError("brak"))
        self.assertFalse(self.monitor.activate(f"codex:{uid(1)}"))
        self.assertEqual(self.raised, [])

    def test_krunner_adapter(self):
        threads = self.monitor.codex_threads()
        self.assertTrue(threads.available())
        self.assertTrue(threads.open_thread(uid(42)))
        self.assertEqual(self.app.opened, [uid(42)])
        self.app.ready = False
        self.assertFalse(threads.open_thread(uid(43)))


if __name__ == "__main__":
    unittest.main()
