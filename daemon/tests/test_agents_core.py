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

    def test_finished_turn_waits_for_reply(self):
        s = self.sessions["claude:c-waiting"]
        self.assertEqual((s["state"], s["reason"]), ("waiting", "reply"))
        self.assertEqual(s["title"], "Module walkthrough")
        # Wpis podagenta (isSidechain) ani away_summary nie wznawiają pracy.
        self.assertEqual(s["lastActivity"], "2026-10-26T11:52:00Z")

    def test_long_wait_becomes_idle(self):
        self.assertEqual(self.sessions["claude:c-idle"]["state"], "idle")

    def test_question_to_user_waits_immediately(self):
        s = self.sessions["claude:c-question"]
        self.assertEqual((s["state"], s["reason"]), ("waiting", "question"))

    def test_interrupted_turn_waits(self):
        self.assertEqual(self.sessions["claude:c-interrupted"]["state"], "waiting")

    def test_tool_without_result_waits_for_approval(self):
        s = self.sessions["claude:c-approval"]
        self.assertEqual((s["state"], s["reason"]), ("waiting", "approval"))
        self.assertEqual(s["lastAction"], "Edit · crash.py")

    def test_status_reported_by_claude_wins(self):
        s = self.sessions["claude:c-status"]
        self.assertEqual((s["state"], s["reason"]), ("waiting", "permission"))

    def test_old_sessions_and_subagents_are_skipped(self):
        self.assertNotIn("claude:c-old", self.sessions)
        self.assertNotIn("claude:agent-a1", self.sessions)
        self.assertEqual(len(self.sessions), 7)


class CodexStatesTest(unittest.TestCase):
    def setUp(self):
        self.sessions = by_id(scan(os.path.join(FIXTURES, "missing"), CODEX, NOW))

    def test_turn_in_progress_is_working(self):
        s = self.sessions[codex_id(1)]
        self.assertEqual(s["state"], "working")
        self.assertEqual((s["tool"], s["project"], s["branch"]), ("codex", "beta", "main"))
        self.assertEqual(s["lastAction"], "shell · make test")

    def test_completed_task_waits_for_reply(self):
        s = self.sessions[codex_id(2)]
        self.assertEqual((s["state"], s["reason"]), ("waiting", "reply"))
        self.assertEqual(s["lastAction"], "apply_patch · report.py")

    def test_call_without_output_waits_for_approval(self):
        s = self.sessions[codex_id(3)]
        self.assertEqual((s["state"], s["reason"]), ("waiting", "approval"))

    def test_no_approvals_policy_keeps_working(self):
        self.assertEqual(self.sessions[codex_id(4)]["state"], "working")

    def test_aborted_turn_waits(self):
        self.assertEqual(self.sessions[codex_id(6)]["state"], "waiting")

    def test_resumed_old_session_counts_by_last_entry(self):
        self.assertEqual(self.sessions[codex_id(7)]["state"], "idle")

    def test_subagent_threads_are_not_sessions(self):
        self.assertNotIn(codex_id(5), self.sessions)
        self.assertEqual(len(self.sessions), 6)


class OrderTest(unittest.TestCase):
    def test_waiting_first_longest_waiting_on_top(self):
        sessions = scan(CLAUDE, CODEX, NOW)
        states = [s["state"] for s in sessions]
        self.assertEqual(states, sorted(states, key=["waiting", "working", "idle", "ended"].index))
        waiting = [s["sinceTs"] for s in sessions if s["state"] == "waiting"]
        self.assertEqual(waiting, sorted(waiting))


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
        self.assertEqual(sessions["claude:c-waiting"]["state"], "ended")
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
        self.assertEqual(scanner.sessions(NOW)[0]["state"], "waiting")

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
                         ("codex:big", "omega", "big-branch", "waiting"))


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


if __name__ == "__main__":
    unittest.main()
