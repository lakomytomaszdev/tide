"""Rdzeń: katalogi z przykładowymi dziennikami na wejściu, lista sesji ze stanami na wyjściu.

Dzienniki w fixtures/ są syntetyczne — naśladują tylko strukturę wpisów Claude Code i Codexa.
Zegar jest zamrożony na NOW, a procesy podajemy jawnie (albo wcale).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from . import FIXTURES
from tide_daemon.agents import AgentScanner, scan
from tide_daemon.agents.timeutil import parse_ts
from tide_daemon.processes import ProcessInfo, ProcessTable

NOW = parse_ts("2026-10-26T12:00:00Z")
CLAUDE = os.path.join(FIXTURES, "claude")
CODEX = os.path.join(FIXTURES, "codex")


def codex_id(n: int) -> str:
    return f"codex:0000000a-0000-4000-8000-{n:012d}"


def by_id(sessions):
    return {s["id"]: s for s in sessions}


def at(text: str) -> float:
    return parse_ts(f"2026-10-26T{text}Z")


class ClaudeStatesTest(unittest.TestCase):
    def setUp(self):
        self.sessions = by_id(scan(CLAUDE, os.path.join(FIXTURES, "missing"), NOW))

    def test_session_mid_turn_is_working(self):
        s = self.sessions["claude:c-working"]
        self.assertEqual(s["state"], "working")
        self.assertEqual((s["tool"], s["project"], s["branch"]), ("claude", "alpha", "feat/login"))
        self.assertEqual(s["lastAction"], "Bash · Run tests")

    def test_finished_turn_is_done_not_waiting(self):
        # Koniec tury to „skończył”: agent oddał głos, ale niczego nie blokuje.
        s = self.sessions["claude:c-done"]
        self.assertEqual((s["state"], s["reason"]), ("done", ""))
        self.assertEqual(s["title"], "Module walkthrough")
        # Wpis podagenta (isSidechain) ani away_summary nie wznawiają pracy.
        self.assertEqual(s["lastActivity"], "2026-10-26T11:52:00Z")

    def test_long_wait_becomes_idle(self):
        self.assertEqual(self.sessions["claude:c-idle"]["state"], "idle")

    def test_question_to_user_waits_immediately(self):
        s = self.sessions["claude:c-question"]
        self.assertEqual((s["state"], s["reason"]), ("waiting", "question"))

    def test_interrupted_turn_is_done(self):
        s = self.sessions["claude:c-interrupted"]
        self.assertEqual((s["state"], s["lastAction"]), ("done", "Przerwano"))

    def test_tool_without_result_waits_for_approval(self):
        s = self.sessions["claude:c-approval"]
        self.assertEqual((s["state"], s["reason"]), ("waiting", "approval"))
        self.assertEqual(s["lastAction"], "Edit · crash.py")

    def test_status_reported_by_claude_wins(self):
        s = self.sessions["claude:c-status"]
        self.assertEqual((s["state"], s["reason"]), ("waiting", "permission"))

    def test_idle_status_is_done(self):
        # Status „idle” z sessions/<pid>.json to koniec tury, a nie czekanie na użytkownika.
        s = self.sessions["claude:c-status-idle"]
        self.assertEqual((s["state"], s["reason"], s["since"]), ("done", "", "2026-10-26T11:55:00Z"))

    def test_old_sessions_and_subagents_are_skipped(self):
        self.assertNotIn("claude:c-old", self.sessions)
        self.assertNotIn("claude:agent-a1", self.sessions)
        self.assertEqual(len(self.sessions), 8)


class CodexStatesTest(unittest.TestCase):
    def setUp(self):
        self.sessions = by_id(scan(os.path.join(FIXTURES, "missing"), CODEX, NOW))

    def test_turn_in_progress_is_working(self):
        s = self.sessions[codex_id(1)]
        self.assertEqual(s["state"], "working")
        self.assertEqual((s["tool"], s["project"], s["branch"]), ("codex", "beta", "main"))
        self.assertEqual(s["lastAction"], "shell · make test")

    def test_completed_task_is_done(self):
        s = self.sessions[codex_id(2)]
        self.assertEqual((s["state"], s["reason"]), ("done", ""))
        self.assertEqual(s["lastAction"], "apply_patch · report.py")

    def test_automation_turn_is_done_and_scheduled(self):
        # Turę zaczęło zaplanowane zadanie Codex Desktop (turn_attribution.turn_trigger).
        s = self.sessions[codex_id(8)]
        self.assertEqual((s["state"], s["reason"]), ("done", "scheduled"))

    def test_heartbeat_message_marks_turn_as_scheduled(self):
        # Starszy zapis bez turn_attribution: wiadomość z content_item_kinds = user.heartbeat.
        s = self.sessions[codex_id(9)]
        self.assertEqual((s["state"], s["reason"]), ("done", "scheduled"))

    def test_request_user_input_waits_with_question(self):
        # Pytanie czeka na użytkownika także przy approval_policy=never.
        s = self.sessions[codex_id(10)]
        self.assertEqual((s["state"], s["reason"], s["since"]), ("waiting", "question", "2026-10-26T11:45:00Z"))

    def test_call_without_output_waits_for_approval(self):
        s = self.sessions[codex_id(3)]
        self.assertEqual((s["state"], s["reason"]), ("waiting", "approval"))

    def test_no_approvals_policy_keeps_working(self):
        self.assertEqual(self.sessions[codex_id(4)]["state"], "working")

    def test_aborted_turn_is_done(self):
        s = self.sessions[codex_id(6)]
        self.assertEqual((s["state"], s["reason"], s["lastAction"]), ("done", "", "Przerwano"))

    def test_resumed_old_session_counts_by_last_entry(self):
        self.assertEqual(self.sessions[codex_id(7)]["state"], "idle")

    def test_subagent_threads_are_not_sessions(self):
        self.assertNotIn(codex_id(5), self.sessions)
        self.assertEqual(len(self.sessions), 9)


class OrderTest(unittest.TestCase):
    def test_waiting_first_longest_waiting_on_top(self):
        sessions = scan(CLAUDE, CODEX, NOW)
        states = [s["state"] for s in sessions]
        self.assertEqual(states, sorted(states, key=["waiting", "working", "done", "idle", "ended"].index))
        waiting = [s["sinceTs"] for s in sessions if s["state"] == "waiting"]
        self.assertEqual(waiting, sorted(waiting))
        # Skończone od najświeższej aktywności.
        done = [s["lastActivityTs"] for s in sessions if s["state"] == "done"]
        self.assertEqual(done, sorted(done, reverse=True))


class ProcessesTest(unittest.TestCase):
    """Procesy: kto żyje, co uruchomił i gdzie jest jego terminal."""

    def table(self, *extra):
        base = [
            ProcessInfo(100, 1, "systemd", at("06:00:00")),
            ProcessInfo(300, 100, "konsole", at("07:00:00")),
            ProcessInfo(301, 300, "bash", at("07:00:01")),
            ProcessInfo(500, 301, "claude", at("11:00:00"), cwd="/srv/work/gamma", tool="claude",
                        env={"KONSOLE_DBUS_SERVICE": ":1.5", "KONSOLE_DBUS_WINDOW": "/Windows/1",
                             "KONSOLE_DBUS_SESSION": "/Sessions/3"}),
            ProcessInfo(4242, 301, "claude", at("08:00:00"), cwd="/srv/work/alpha", tool="claude"),
            ProcessInfo(700, 100, "codex", at("11:00:00"), cwd="/srv/work/beta", tool="codex",
                        open_files=(os.path.join(CODEX, "sessions/2026/10/26/rollout-2026-10-26T11-30-00-"
                                                 "0000000a-0000-4000-8000-000000000001.jsonl"),)),
        ]
        return ProcessTable(base + list(extra))

    def test_sessions_without_process_have_ended(self):
        sessions = by_id(scan(CLAUDE, CODEX, NOW, self.table()))
        self.assertEqual(sessions["claude:c-status"]["state"], "waiting")
        self.assertEqual(sessions["claude:c-done"]["state"], "ended")
        self.assertEqual(sessions[codex_id(1)]["state"], "working")
        self.assertEqual(sessions[codex_id(2)]["state"], "ended")

    def test_tool_with_running_child_is_working(self):
        child = ProcessInfo(501, 500, "bash", at("11:58:30"))
        s = by_id(scan(CLAUDE, CODEX, NOW, self.table(child)))["claude:c-approval"]
        self.assertEqual(s["state"], "working")

    def test_tool_without_child_waits_for_approval(self):
        mcp = ProcessInfo(502, 500, "node", at("11:00:05"))  # serwer MCP sprzed wywołania się nie liczy
        s = by_id(scan(CLAUDE, CODEX, NOW, self.table(mcp)))["claude:c-approval"]
        self.assertEqual((s["state"], s["reason"]), ("waiting", "approval"))

    def test_session_in_konsole_is_clickable(self):
        s = by_id(scan(CLAUDE, CODEX, NOW, self.table()))["claude:c-approval"]
        self.assertTrue(s["canActivate"])
        self.assertEqual((s["terminal"], s["pid"]), ("Konsole", 500))

    def test_session_without_terminal_is_not_clickable(self):
        s = by_id(scan(CLAUDE, CODEX, NOW, self.table()))[codex_id(1)]
        self.assertFalse(s["canActivate"])


class IncrementalTest(unittest.TestCase):
    """Usługa doczytuje tylko nowe wpisy — stan musi iść za tym, co agent dopisał."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-test-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_state_follows_appended_entries(self):
        root = os.path.join(self.tmp, "codex")
        day = os.path.join(root, "sessions/2026/10/26")
        os.makedirs(day)
        name = "rollout-2026-10-26T11-30-00-0000000a-0000-4000-8000-000000000001.jsonl"
        shutil.copy(os.path.join(CODEX, "sessions/2026/10/26", name), day)
        scanner = AgentScanner(os.path.join(self.tmp, "claude"), root)
        scanner.discover(NOW)
        scanner.update()
        self.assertEqual(scanner.sessions(NOW)[0]["state"], "working")

        with open(os.path.join(day, name), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": "2026-10-26T11:59:55.000Z", "type": "event_msg",
                                 "payload": {"type": "task_complete", "turn_id": "t1"}}) + "\n")
        self.assertTrue(scanner.update(os.path.join(day, name)))
        self.assertEqual(scanner.sessions(NOW)[0]["state"], "done")

    def test_large_log_reads_header_and_tail(self):
        day = os.path.join(self.tmp, "codex/sessions/2026/10/26")
        os.makedirs(day)
        path = os.path.join(day, "rollout-2026-10-26T10-00-00-0000000a-0000-4000-8000-000000000099.jsonl")
        filler = json.dumps({"timestamp": "2026-10-26T11:00:00.000Z", "type": "response_item",
                             "payload": {"type": "reasoning", "summary": [], "pad": "x" * 2000}})
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": "2026-10-26T10:00:00.000Z", "type": "session_meta",
                                 "payload": {"id": "big", "cwd": "/srv/work/omega", "source": "cli",
                                             "git": {"branch": "big-branch"}}}) + "\n")
            for _ in range(1500):  # ok. 3 MB
                fh.write(filler + "\n")
            fh.write(json.dumps({"timestamp": "2026-10-26T11:59:00.000Z", "type": "event_msg",
                                 "payload": {"type": "task_complete"}}) + "\n")
        s = scan(os.path.join(self.tmp, "claude"), os.path.join(self.tmp, "codex"), NOW)[0]
        self.assertEqual((s["id"], s["project"], s["branch"], s["state"]),
                         ("codex:big", "omega", "big-branch", "done"))


@unittest.skipUnless(shutil.which("git"), "brak gita")
class GitLocationTest(unittest.TestCase):
    def test_worktree_session_shows_repository_and_worktree(self):
        tmp = tempfile.mkdtemp(prefix="tide-git-")
        self.addCleanup(shutil.rmtree, tmp)
        repo = os.path.join(tmp, "myrepo")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null")
        run = lambda *a, cwd=repo: subprocess.run(a, cwd=cwd, env=env, check=True, capture_output=True)  # noqa: E731
        os.makedirs(repo)
        run("git", "init", "-q", "-b", "main")
        run("git", "commit", "-q", "--allow-empty", "-m", "init")
        worktree = os.path.join(tmp, "wt-feature")
        run("git", "worktree", "add", "-q", "-b", "feature/x", worktree)

        project_dir = os.path.join(tmp, "claude/projects", worktree.replace("/", "-"))
        os.makedirs(project_dir)
        with open(os.path.join(project_dir, "s1.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "timestamp": "2026-10-26T11:59:00.000Z", "cwd": worktree,
                                 "gitBranch": "stale-branch", "message": {"role": "user", "content": "hi"}}) + "\n")
        s = scan(os.path.join(tmp, "claude"), os.path.join(tmp, "codex"), NOW)[0]
        self.assertEqual((s["project"], s["worktree"], s["branch"]), ("myrepo", "wt-feature", "feature/x"))



class BackgroundTaskTest(unittest.TestCase):
    """Claude skończył turę, ale w tle działa jego zadanie (status „shell”) — wróci do niego sam,
    więc sesja pracuje, a nie czeka na użytkownika."""

    def derive(self, status_name):
        from tide_daemon.agents.claude import status_from
        from tide_daemon.agents.state import SessionLog, derive_state
        log = SessionLog("x.jsonl")
        log.last_activity = NOW - 60
        status = status_from({"status": status_name, "statusUpdatedAt": (NOW - 30) * 1000})
        return derive_state(log, NOW, True, status, lambda ts: False)

    def test_background_shell_counts_as_working(self):
        state, reason, _ = self.derive("shell")
        self.assertEqual((state, reason), ("working", "background"))

    def test_unknown_status_falls_back_to_the_log(self):
        state, reason, _ = self.derive("something-new")
        self.assertEqual((state, reason), ("done", ""))


class DoneStateTest(unittest.TestCase):
    """Wyprowadzanie stanu wprost z derive_state: „czeka” tylko przy blokadzie, koniec tury to „skończył”."""

    def log(self, phase="turn_end", last=NOW - 60):
        from tide_daemon.agents.state import SessionLog
        log = SessionLog("x.jsonl")
        log.phase, log.last_activity = phase, last
        return log

    def derive(self, log, status=None, now=NOW):
        from tide_daemon.agents.state import derive_state
        return derive_state(log, now, True, status, lambda ts: False)

    def status(self, name, ago, waiting_for=None):
        from tide_daemon.agents.claude import status_from
        data = {"status": name, "statusUpdatedAt": (NOW - ago) * 1000}
        if waiting_for:
            data["waitingFor"] = waiting_for
        return status_from(data)

    def test_claude_idle_status_is_done_then_idle(self):
        self.assertEqual(self.derive(self.log(), self.status("idle", 30))[:2], ("done", ""))
        old = self.log(last=NOW - 3600)
        self.assertEqual(self.derive(old, self.status("idle", 31 * 60))[:2], ("idle", ""))

    def test_claude_waiting_status_waits(self):
        self.assertEqual(self.derive(self.log(), self.status("waiting", 30, "permission"))[:2],
                         ("waiting", "permission"))
        self.assertEqual(self.derive(self.log(), self.status("waiting", 30))[:2], ("waiting", "approval"))

    def test_done_turns_idle_after_30_minutes(self):
        self.assertEqual(self.derive(self.log(last=NOW - 29 * 60))[0], "done")
        self.assertEqual(self.derive(self.log(last=NOW - 31 * 60))[0], "idle")

    def test_scheduled_reason_survives_into_idle(self):
        log = self.log(last=NOW - 31 * 60)
        log.scheduled = True
        self.assertEqual(self.derive(log)[:2], ("idle", "scheduled"))


class CodexScheduledTurnTest(unittest.TestCase):
    """Rozpoznanie tury z automatyzacji Codex Desktop na wpisach podawanych po kolei."""

    def feed(self, *records):
        from tide_daemon.agents.codex import CodexLog
        from tide_daemon.agents.state import derive_state
        log = CodexLog("rollout-x.jsonl")
        for n, record in enumerate(records):
            record.setdefault("timestamp", f"2026-10-26T11:50:{n:02d}.000Z")
            log.feed(record)
        return derive_state(log, NOW, True, None, lambda ts: False)[:2]

    @staticmethod
    def started(trigger=None):
        payload = {"type": "task_started", "turn_id": "t"}
        if trigger:
            payload["turn_attribution"] = {"turn_trigger": trigger}
        return {"type": "event_msg", "payload": payload}

    @staticmethod
    def user(*kinds):
        return {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [],
                "internal_chat_message_metadata_passthrough": {"content_item_kinds": list(kinds)}}}

    @staticmethod
    def complete():
        return {"type": "event_msg", "payload": {"type": "task_complete"}}

    def test_trigger_marks_turn(self):
        self.assertEqual(self.feed(self.started("automation_heartbeat_scheduled"), self.complete()),
                         ("done", "scheduled"))

    def test_heartbeat_message_marks_turn(self):
        self.assertEqual(self.feed(self.started(), self.user("user.heartbeat"), self.complete()),
                         ("done", "scheduled"))

    def test_next_turn_typed_by_user_is_not_scheduled(self):
        self.assertEqual(self.feed(self.started("automation_heartbeat_scheduled"), self.user("user.heartbeat"),
                                   self.complete(), self.started("composer"), self.user("user.text"),
                                   self.complete()), ("done", ""))
        self.assertEqual(self.feed(self.started(), self.user("user.heartbeat"), self.complete(),
                                   self.started(), self.user("environments.environment_context"),
                                   self.user("user.text"), self.complete()), ("done", ""))

    def test_user_message_during_scheduled_turn_takes_over(self):
        self.assertEqual(self.feed(self.started(), self.user("user.heartbeat"), self.user("user.text"),
                                   self.complete()), ("done", ""))

    def test_scheduled_turn_still_waits_for_question(self):
        ask = {"type": "response_item", "payload": {"type": "function_call", "name": "request_user_input",
                                                     "arguments": "{}", "call_id": "q"}}
        self.assertEqual(self.feed(self.started("automation_heartbeat_scheduled"), ask), ("waiting", "question"))


if __name__ == "__main__":
    unittest.main()
