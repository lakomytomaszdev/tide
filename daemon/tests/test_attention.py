"""Skok do czekającego agenta: wybór najdłużej czekającej sesji, powiadomienia bez powtórzeń
i ich wiązanie z D-Bus (atrapa serwera powiadomień na prywatnej magistrali).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

from . import FIXTURES
from .privatebus import SERVICE, PrivateBus, requires_dbus, wait_until
from . import test_agents_core as core  # moduł, nie klasy — inaczej unittest uruchomiłby je drugi raz
from .test_agents_core import CLAUDE, CODEX, NOW, at, codex_id
from tide_daemon.agents import scan
from tide_daemon.attention import WaitingTracker, longest_waiting, notification_text
from tide_daemon.processes import ProcessInfo

NOTIFY_SERVICE = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"
FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_notifications.py")
CODEX_DAY = os.path.join(CODEX, "sessions/2026/10/26")
CODEX_2 = os.path.join(CODEX_DAY, "rollout-2026-10-26T11-40-00-0000000a-0000-4000-8000-000000000002.jsonl")
CODEX_10 = os.path.join(CODEX_DAY, "rollout-2026-10-26T11-44-00-0000000a-0000-4000-8000-000000000010.jsonl")
ASK = {"timestamp": "2026-10-26T11:59:58.000Z", "type": "response_item",
       "payload": {"type": "function_call", "name": "request_user_input", "arguments": "{}", "call_id": "q9"}}
DONE = {"timestamp": "2026-10-26T11:59:57.000Z", "type": "event_msg", "payload": {"type": "task_complete"}}


def session(sid, state, since=0.0, can=True, **extra):
    return dict({"id": sid, "state": state, "sinceTs": since, "canActivate": can, "project": "p", "tool": "claude",
                 "reason": "question", "branch": "", "title": "", "lastAction": ""}, **extra)


class LongestWaitingTest(unittest.TestCase):
    def table(self):
        # Codex w Konsoli pyta od 11:45 — dłużej niż sesje Claude Code (od 11:58). Drugi Codex
        # w Konsoli skończył turę jeszcze wcześniej (11:45), ale „skończył” to nie „czeka”.
        asking = ProcessInfo(800, 301, "codex", at("11:40:00"), cwd="/srv/work/kappa", tool="codex",
                             open_files=(CODEX_10,))
        finished = ProcessInfo(801, 301, "codex", at("11:39:00"), cwd="/srv/work/beta", tool="codex",
                               open_files=(CODEX_2,))
        return core.ProcessesTest().table(asking, finished)

    def test_picks_activatable_session_waiting_longest(self):
        sessions = scan(CLAUDE, CODEX, NOW, self.table())
        waiting = [s["id"] for s in sessions if s["state"] == "waiting" and s["canActivate"]]
        self.assertEqual(sorted(waiting), sorted(["claude:c-status", "claude:c-approval", codex_id(10)]))
        self.assertEqual(longest_waiting(sessions)["id"], codex_id(10))

    def test_done_session_is_never_a_target(self):
        # Meta+A prowadzi wyłącznie do czekających — skończona sesja z oknem nie jest celem,
        # nawet gdy nikt inny nie czeka.
        sessions = scan(CLAUDE, CODEX, NOW, self.table())
        done = {s["id"]: s for s in sessions}[codex_id(2)]
        self.assertEqual((done["state"], done["canActivate"]), ("done", True))
        self.assertIsNone(longest_waiting([done, session("w", "working")]))
        self.assertIsNone(longest_waiting([session("d", "done", 1, reason="scheduled")]))

    def test_without_window_or_without_waiting_nothing_is_picked(self):
        self.assertIsNone(longest_waiting(scan(CLAUDE, CODEX, NOW)))  # bez procesów nie ma okien
        self.assertIsNone(longest_waiting([session("a", "working"), session("b", "idle", 1)]))
        self.assertIsNone(longest_waiting([]))

    def test_session_without_window_is_skipped(self):
        sessions = [session("old", "waiting", 100, can=False), session("newer", "waiting", 200)]
        self.assertEqual(longest_waiting(sessions)["id"], "newer")


class WaitingTrackerTest(unittest.TestCase):
    def test_sessions_waiting_at_start_are_not_announced(self):
        tracker = WaitingTracker()
        self.assertEqual(tracker.update([session("a", "waiting", 5)]), ([], []))
        self.assertEqual(tracker.update([session("a", "waiting", 5)]), ([], []))

    def test_announced_once_until_state_changes(self):
        tracker = WaitingTracker()
        tracker.update([session("a", "working")])
        started, stopped = tracker.update([session("a", "waiting", 10)])
        self.assertEqual(([s["id"] for s in started], stopped), (["a"], []))
        for _ in range(3):  # kolejne przeliczenia stanu niczego nie powtarzają
            self.assertEqual(tracker.update([session("a", "waiting", 10)]), ([], []))
        self.assertEqual(tracker.update([session("a", "working")]), ([], ["a"]))
        started, _ = tracker.update([session("a", "waiting", 20)])
        self.assertEqual([s["id"] for s in started], ["a"])

    def test_idle_ended_and_vanished_count_as_leaving(self):
        tracker = WaitingTracker()
        tracker.update([])
        tracker.update([session("a", "waiting"), session("b", "waiting"), session("c", "waiting")])
        _, stopped = tracker.update([session("a", "idle"), session("b", "ended")])
        self.assertEqual(stopped, ["a", "b", "c"])

    def test_several_new_sessions_oldest_first(self):
        tracker = WaitingTracker()
        tracker.update([])
        started, _ = tracker.update([session("x", "waiting", 30), session("y", "waiting", 10)])
        self.assertEqual([s["id"] for s in started], ["y", "x"])

    def test_done_is_not_announced(self):
        tracker = WaitingTracker()
        tracker.update([session("a", "working"), session("b", "waiting", 5)])
        # Koniec tury (także z zaplanowanego zadania) nie jest wejściem w „czeka”,
        # a przejście z „czeka” w „skończył” zamyka powiadomienie.
        started, stopped = tracker.update([session("a", "done", 10, reason=""),
                                           session("b", "done", 11, reason="scheduled")])
        self.assertEqual((started, stopped), ([], ["b"]))

    def test_fixtures_appended_entries_announce_only_real_waiting(self):
        tmp = tempfile.mkdtemp(prefix="tide-attn-")
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copytree(CODEX, os.path.join(tmp, "codex"))
        tracker = WaitingTracker()
        tracker.update(scan(CLAUDE, os.path.join(tmp, "codex"), NOW))
        log = os.path.join(tmp, "codex/sessions/2026/10/26/"
                                "rollout-2026-10-26T11-30-00-0000000a-0000-4000-8000-000000000001.jsonl")
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(DONE) + "\n")
        self.assertEqual(tracker.update(scan(CLAUDE, os.path.join(tmp, "codex"), NOW)), ([], []))
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ASK) + "\n")
        started, stopped = tracker.update(scan(CLAUDE, os.path.join(tmp, "codex"), NOW))
        self.assertEqual(([(s["id"], s["reason"]) for s in started], stopped), ([(codex_id(1), "question")], []))


class NotificationTextTest(unittest.TestCase):
    def test_title_and_reason(self):
        s = session("a", "waiting", project="alpha", tool="codex", reason="approval", lastAction="shell · make",
                    branch="feat/x")
        self.assertEqual(notification_text(s), ("Agent czeka: alpha", "Czeka na zgodę: shell · make\nCodex · feat/x"))
        s = session("b", "waiting", project="beta", reason="question", lastAction="AskUserQuestion",
                    title="Refaktor")
        self.assertEqual(notification_text(s), ("Agent czeka: beta", "Pyta o decyzję: AskUserQuestion\n"
                                                                     "Claude Code · Refaktor"))


@requires_dbus
class NotifierWiringTest(unittest.TestCase):
    """Notifier na prywatnej magistrali z atrapą serwera powiadomień."""

    def setUp(self):
        self.bus = PrivateBus("tide-notify-test")
        self.addCleanup(self.bus.stop)
        self.bus.spawn([sys.executable, FAKE], NOTIFY_SERVICE)

    def calls(self):
        reply = self.bus.call(NOTIFY_SERVICE, NOTIFY_PATH, NOTIFY_SERVICE, "Calls")
        return json.loads(reply.arguments()[0])

    def notifies(self):
        return [c for c in self.calls() if c["method"] == "Notify"]

    def test_notify_jump_and_close(self):
        from tide_daemon.notifications import Notifier
        from PyQt6.QtCore import QMetaType
        from PyQt6.QtDBus import QDBusArgument

        jumped = []
        notifier = Notifier(lambda sid: jumped.append(sid) or True, bus=self.bus.bus)
        notifier.update([session("s1", "waiting", 1), session("s2", "working")])  # stan wyjściowy
        notifier.update([session("s1", "waiting", 1), session("s2", "waiting", 2, project="beta", tool="codex",
                                                                 reason="approval", lastAction="shell · a<b")])
        sent = wait_until(self.notifies)
        self.assertEqual(len(sent), 1, "tylko nowo czekająca sesja dostaje powiadomienie")
        self.assertEqual((sent[0]["app"], sent[0]["summary"]), ("Tide", "Agent czeka: beta"))
        self.assertEqual(sent[0]["body"], "Czeka na zgodę: shell · a&lt;b\nCodex")
        self.assertEqual(sent[0]["actions"], ["default", "Przejdź", "jump", "Przejdź"])

        # To samo jeszcze raz — bez powtórki.
        notifier.update([session("s2", "waiting", 2, project="beta")])
        wait_until(lambda: False, timeout=0.3)
        self.assertEqual(len(self.notifies()), 1)

        # Kliknięcie „Przejdź” w powiadomieniu przenosi do okna sesji.
        wait_until(lambda: notifier.notice_of.get("s2"))
        self.bus.call(NOTIFY_SERVICE, NOTIFY_PATH, NOTIFY_SERVICE, "Invoke",
                      QDBusArgument(sent[0]["id"], QMetaType.Type.UInt.value), "jump")
        self.assertEqual(wait_until(lambda: jumped), ["s2"])

        # Sesja przestała czekać → powiadomienie znika; ponowne czekanie → nowe powiadomienie.
        notifier.update([session("s2", "working")])
        closed = wait_until(lambda: [c for c in self.calls() if c["method"] == "CloseNotification"])
        self.assertEqual(closed[0]["id"], sent[0]["id"])
        notifier.update([session("s2", "waiting", 9, project="beta")])
        self.assertEqual(len(wait_until(lambda: len(self.notifies()) == 2 and self.notifies())), 2)

    def test_session_without_window_gets_no_button(self):
        from tide_daemon.notifications import Notifier
        notifier = Notifier(lambda sid: True, bus=self.bus.bus)
        notifier.update([])
        notifier.update([session("s3", "waiting", 1, can=False)])
        sent = wait_until(self.notifies)
        self.assertEqual(sent[0]["actions"], [])


@requires_dbus
class DaemonNotificationTest(unittest.TestCase):
    """Cała usługa: wejście sesji w „czeka” → powiadomienie; skrót bez czekających okien nic nie robi."""

    def test_daemon_announces_newly_waiting_session(self):
        tmp = tempfile.mkdtemp(prefix="tide-notify-")
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copytree(os.path.join(FIXTURES, "codex"), os.path.join(tmp, "codex"))
        os.makedirs(os.path.join(tmp, "work"))
        bus = PrivateBus("tide-daemon-notify-test")
        self.addCleanup(bus.stop)
        bus.spawn([sys.executable, FAKE], NOTIFY_SERVICE)
        bus.spawn_daemon("--claude-dir", os.path.join(tmp, "claude"), "--codex-dir", os.path.join(tmp, "codex"),
                         "--no-processes", "--now", "2026-10-26T12:00:00Z", "--workspace", os.path.join(tmp, "work"))

        def notifies():
            reply = bus.call(NOTIFY_SERVICE, NOTIFY_PATH, NOTIFY_SERVICE, "Calls")
            return [c for c in json.loads(reply.arguments()[0]) if c["method"] == "Notify"]

        wait_until(lambda: False, timeout=0.5)
        self.assertEqual(notifies(), [], "sesje czekające przed startem nie dostają powiadomień")

        log = os.path.join(tmp, "codex/sessions/2026/10/26/"
                                "rollout-2026-10-26T11-30-00-0000000a-0000-4000-8000-000000000001.jsonl")
        # Skończona tura nie jest czekaniem — bez powiadomienia.
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(DONE) + "\n")
        wait_until(lambda: False, timeout=1.5)
        self.assertEqual(notifies(), [], "koniec tury nie powiadamia")
        # Pytanie do użytkownika — powiadomienie.
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ASK) + "\n")
        sent = wait_until(notifies, timeout=5)
        self.assertEqual([n["summary"] for n in sent], ["Agent czeka: beta"])
        self.assertEqual(sent[0]["actions"], [], "bez procesów nie ma okna, więc nie ma przycisku")

        # Meta+A: czekają sesje, ale żadnej nie da się pokazać — cicho zwraca false.
        reply = bus.call(SERVICE, "/Agents", "org.tide.Daemon.Agents", "ActivateLongestWaiting")
        self.assertEqual(reply.arguments(), [False])


if __name__ == "__main__":
    unittest.main()
