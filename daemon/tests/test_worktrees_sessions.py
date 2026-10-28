"""Czytelne tytuły gałęzi i powiązanie worktrees z sesjami Claude Code i Codexa.

Dzienniki są syntetyczne, pisane w teście (tylko struktura wpisów, bez prawdziwych rozmów).
Katalogi worktrees nie muszą istnieć — powiązanie porównuje ścieżki.
"""
import json
import os
import shutil
import tempfile
import time
import unittest

from . import gitrepos
from tide_daemon.agents.claude import escape_cwd
from tide_daemon.worktrees import SessionIndex, WorktreeScanner, guess_tool, humanize_branch, live_sessions
from tide_daemon.worktrees import sessions as sessions_mod

NOW = 1_793_000_000.0  # 2026-10-26
DAY = 86400


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


class HumanizeTest(unittest.TestCase):
    def test_branch_names_become_titles(self):
        cases = {
            "codex/summary-tabs-03-version-axis": ("codex", "summary-tabs 03 · version axis"),
            "codex/summary-tabs-fix-card-list-header": ("codex", "summary-tabs · fix card list header"),
            "codex/fix-unique-phrase-entry": ("codex", "fix · unique phrase entry"),
            "codex/hotfix-unique-search": ("codex", "hotfix · unique search"),
            "codex/tom-128-index-generation-plan": ("codex", "tom-128 · index generation plan"),
            "codex/la035-atomic-open-approval": ("codex", "la035 · atomic open approval"),
            "codex/oa-4-refusals-to-annotations": ("codex", "oa-4 · refusals to annotations"),
            "codex/anon-key-sync": ("codex", "anon-key-sync"),
            "feat/open-arithmetic-2026-10": ("feat", "open-arithmetic"),
            "feature/multidevice-f10-phone-variants-settings": ("feature", "multidevice f10 · phone variants settings"),
            "fix/devicecert-error-propagation": ("fix", "devicecert-error-propagation"),
            "hexeditor-improvements-v3": ("", "hexeditor-improvements v3"),
            "worktree-agent-a449db036274f016e": ("worktree", "agent a449db03"),
            "claude/tide-sddm": ("claude", "tide-sddm"),
            "feat/merged": ("feat", "merged"),
            "topic": ("", "topic"),
            "users/tomek/x": ("", "users · tomek · x"),
            "": ("", ""),
        }
        for branch, expected in cases.items():
            with self.subTest(branch=branch):
                self.assertEqual(humanize_branch(branch), expected)

    def test_tool_guess_without_session(self):
        self.assertEqual(guess_tool("/home/u/.codex/worktrees/1a37/repo", ""), "codex")
        self.assertEqual(guess_tool("/srv/repo/.claude/worktrees/agent-1", ""), "claude")
        self.assertEqual(guess_tool("/srv/repo-wt/x", "codex"), "codex")
        self.assertEqual(guess_tool("/srv/repo-wt/x", "feat"), "")


class LogFixture(unittest.TestCase):
    """Katalogi ~/.claude i ~/.codex w tymczasowym katalogu."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-sessions-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.claude = os.path.join(self.tmp, "claude")
        self.codex = os.path.join(self.tmp, "codex")
        os.makedirs(os.path.join(self.claude, "projects"))
        os.makedirs(os.path.join(self.codex, "sessions"))

    def write(self, path: str, records: list, mtime: float | None = None) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write((record if isinstance(record, str) else json.dumps(record)) + "\n")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def claude_session(self, sid: str, cwd: str, ts: float, title: str | None = None, prompt: str = "zrób X",
                       extra: list = (), mtime: float | None = None) -> str:
        records = [
            {"type": "user", "timestamp": iso(ts - 60), "cwd": cwd, "sessionId": sid,
             "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},
            {"type": "user", "timestamp": iso(ts - 50), "cwd": cwd, "sessionId": sid, "isMeta": True,
             "message": {"role": "user", "content": "Caveat: meta"}},
            {"type": "user", "timestamp": iso(ts - 40), "cwd": cwd, "sessionId": sid,
             "message": {"role": "user", "content": prompt}},
            *extra,
            {"type": "assistant", "timestamp": iso(ts), "cwd": cwd, "sessionId": sid,
             "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}},
        ]
        if title:
            records.append({"type": "ai-title", "aiTitle": title, "sessionId": sid})
        path = os.path.join(self.claude, "projects", escape_cwd(cwd), f"{sid}.jsonl")
        return self.write(path, records, mtime if mtime is not None else ts)

    def codex_session(self, sid: str, cwd: str, ts: float, prompt: str = "napraw Y", subagent: bool = False,
                      later_cwd: str = "", mtime: float | None = None) -> str:
        day = time.strftime("%Y/%m/%d", time.gmtime(ts))
        stamp = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime(ts))
        source = {"subagent": {"thread_spawn": {}}} if subagent else "vscode"
        records = [
            {"timestamp": iso(ts - 120), "type": "session_meta",
             "payload": {"id": sid, "cwd": cwd, "source": source, "originator": "codex_cli_rs",
                         "base_instructions": {"text": "x" * 2000}}},
            {"timestamp": iso(ts - 110), "type": "response_item",
             "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<env>"}]}},
            {"timestamp": iso(ts - 100), "type": "event_msg", "payload": {"type": "user_message", "message": prompt}},
            {"timestamp": iso(ts - 90), "type": "turn_context", "payload": {"cwd": later_cwd or cwd}},
            {"timestamp": iso(ts), "type": "event_msg", "payload": {"type": "task_complete"}},
        ]
        path = os.path.join(self.codex, "sessions", day, f"rollout-{stamp}-{sid}.jsonl")
        return self.write(path, records, mtime if mtime is not None else ts)


    def imported_codex(self, sid: str, cwd: str, ts: float, imported_at: float, continued: bool = False) -> str:
        """Wątek zaimportowany przez Codex Desktop z Claude Code: tury external-import-turn-*, czasy
        wpisów z chwili importu (imported_at), bez turn_context — chyba że kontynuowany w Codexie."""
        day = time.strftime("%Y/%m/%d", time.gmtime(ts))
        stamp = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime(ts))
        records = [
            {"timestamp": iso(imported_at), "type": "session_meta",
             "payload": {"id": sid, "cwd": cwd, "source": "vscode", "originator": "Codex Desktop"}},
            {"timestamp": iso(imported_at), "type": "event_msg",
             "payload": {"type": "task_started", "turn_id": "external-import-turn-1"}},
            {"timestamp": iso(imported_at), "type": "event_msg",
             "payload": {"type": "user_message", "message": "zaimportowane polecenie"}},
            {"timestamp": iso(imported_at), "type": "event_msg",
             "payload": {"type": "task_complete", "turn_id": "external-import-turn-1"}},
        ]
        if continued:
            records += [
                {"timestamp": iso(imported_at + 60), "type": "turn_context", "payload": {"cwd": cwd}},
                {"timestamp": iso(imported_at + 120), "type": "event_msg",
                 "payload": {"type": "task_complete", "turn_id": "t-2"}},
            ]
        path = os.path.join(self.codex, "sessions", day, f"rollout-{stamp}-{sid}.jsonl")
        return self.write(path, records, imported_at)


class SessionIndexTest(LogFixture):
    def test_codex_import_of_claude_session_does_not_steal_the_worktree(self):
        wt = "/srv/twitch/.claude/worktrees/oprawa"
        self.claude_session("1233d658", wt + "/brand", NOW - DAY, title="Oprawa streamu")
        # Import jest późniejszy niż ostatni wpis Claude — dawniej wygrywał jako „najświeższy”.
        self.imported_codex("01a17ecf-bbfc-7872-b219-22d41e0e8c29", wt, NOW - 2 * DAY, imported_at=NOW - 3600)
        index = SessionIndex(self.claude, self.codex)
        self.assertEqual([s.session_id for s in index.refresh(NOW)], ["1233d658"])
        linked = index.link([(wt, "")])
        self.assertEqual((linked[wt].tool, linked[wt].session_id), ("claude", "1233d658"))

    def test_imported_thread_continued_in_codex_counts(self):
        sid = "01a17ecf-0000-4000-8000-0000000000ee"
        self.imported_codex(sid, "/srv/a", NOW - 2 * DAY, imported_at=NOW - 3600, continued=True)
        [session] = SessionIndex(self.claude, self.codex).refresh(NOW)
        self.assertEqual((session.tool, session.session_id, session.cwds), ("codex", sid, ("/srv/a",)))

    def test_live_session_wins_over_newer_ended_one(self):
        wt = "/srv/work/app-wt/logvalue"
        self.claude_session("a650a246", wt, NOW - 2 * DAY, title="Żywa sesja Claude")
        self.codex_session("0000000a-0000-4000-8000-0000000000ff", wt, NOW - 600, prompt="zakończony Codex")
        index = SessionIndex(self.claude, self.codex)
        index.refresh(NOW)
        self.assertEqual(index.link([(wt, "")])[wt].tool, "codex", "bez żywych: najświeższa")
        live = live_sessions([
            {"tool": "claude", "sessionId": "a650a246", "state": "idle"},
            {"tool": "codex", "sessionId": "0000000a-0000-4000-8000-0000000000ff", "state": "ended"},
        ])
        self.assertEqual(live, {("claude", "a650a246")})
        linked = index.link([(wt, "")], live)
        self.assertEqual((linked[wt].tool, linked[wt].title), ("claude", "Żywa sesja Claude"))


    def test_links_most_recent_session_in_deepest_worktree(self):
        wt = "/srv/work/app-wt/feature"
        nested = "/srv/work/app-wt/feature/sub-wt"
        self.claude_session("c-old", wt + "/src", NOW - 3 * DAY, title="Stary tytuł")
        self.claude_session("c-new", wt, NOW - DAY, title="Nowy tytuł")
        self.codex_session("0000000a-0000-4000-8000-0000000000aa", nested, NOW - 2 * DAY, prompt="zagnieżdżony")
        index = SessionIndex(self.claude, self.codex)
        index.refresh(NOW)
        linked = index.link([("/srv/work/app", ""), (wt, ""), (nested, "")])
        self.assertEqual(set(linked), {wt, nested})
        self.assertEqual((linked[wt].tool, linked[wt].session_id, linked[wt].title), ("claude", "c-new", "Nowy tytuł"))
        self.assertAlmostEqual(linked[wt].last_ts, NOW - DAY, places=0)
        self.assertEqual((linked[nested].tool, linked[nested].title), ("codex", "zagnieżdżony"))

    def test_claude_title_falls_back_to_first_real_prompt(self):
        self.claude_session("c1", "/srv/a", NOW - DAY, prompt="  Popraw   nagłówek\nlisty " + "x" * 200)
        index = SessionIndex(self.claude, self.codex)
        [session] = index.refresh(NOW)
        self.assertTrue(session.title.startswith("Popraw nagłówek listy x"))
        self.assertLessEqual(len(session.title), sessions_mod.TITLE_LIMIT)
        self.assertTrue(session.title.endswith("…"))

    def test_custom_title_wins_over_ai_title(self):
        extra = [{"type": "custom-title", "customTitle": "Mój tytuł", "sessionId": "c2"}]
        self.claude_session("c2", "/srv/a", NOW - DAY, title="Tytuł modelu", extra=extra)
        [session] = SessionIndex(self.claude, self.codex).refresh(NOW)
        self.assertEqual(session.title, "Mój tytuł")

    def test_codex_thread_name_from_session_index(self):
        sid = "0000000a-0000-4000-8000-0000000000bb"
        self.codex_session(sid, "/home/u/.codex/worktrees/1a37/app", NOW - DAY, later_cwd="/home/u/.codex/worktrees/1a37/app/web")
        self.write(os.path.join(self.codex, "session_index.jsonl"), [
            {"id": sid, "thread_name": "Pierwsza nazwa", "updated_at": iso(NOW - 2 * DAY)},
            {"id": sid, "thread_name": "Nazwa wątku", "updated_at": iso(NOW - DAY)},
        ])
        index = SessionIndex(self.claude, self.codex)
        [session] = index.refresh(NOW)
        self.assertEqual((session.tool, session.title), ("codex", "Nazwa wątku"))
        self.assertEqual(session.cwds, ("/home/u/.codex/worktrees/1a37/app", "/home/u/.codex/worktrees/1a37/app/web"))

    def test_skips_subagents_and_old_files(self):
        self.codex_session("0000000a-0000-4000-8000-0000000000cc", "/srv/a", NOW - DAY, subagent=True)
        self.claude_session("c-old", "/srv/a", NOW - 40 * DAY)
        self.claude_session("c-sub", "/srv/a", NOW - DAY)
        # Pliki podagentów Claude Code leżą w podkatalogu sesji — nie są osobnymi sesjami.
        self.write(os.path.join(self.claude, "projects", escape_cwd("/srv/a"), "c-sub", "subagents", "a.jsonl"),
                   [{"type": "user", "cwd": "/srv/b", "timestamp": iso(NOW)}], NOW)
        sessions = SessionIndex(self.claude, self.codex).refresh(NOW)
        self.assertEqual([s.session_id for s in sessions], ["c-sub"])

    def test_large_file_reads_only_head_and_tail(self):
        filler = [{"type": "assistant", "timestamp": iso(NOW - 2 * DAY), "message": {"content": "y" * 1000}}] * 600
        path = self.claude_session("c-big", "/srv/big", NOW - DAY, title="Duża sesja", extra=filler)
        self.assertGreater(os.path.getsize(path), sessions_mod.HEAD_BYTES + sessions_mod.TAIL_BYTES)
        [session] = SessionIndex(self.claude, self.codex).refresh(NOW)
        self.assertEqual((session.title, session.cwds), ("Duża sesja", ("/srv/big",)))
        self.assertAlmostEqual(session.last_ts, NOW - DAY, places=0)

    def test_unchanged_files_are_not_read_again(self):
        self.claude_session("c1", "/srv/a", NOW - DAY, title="A")
        index = SessionIndex(self.claude, self.codex)
        index.refresh(NOW)
        calls = []
        original = sessions_mod.read_claude
        sessions_mod.read_claude = lambda path: calls.append(path) or original(path)
        self.addCleanup(setattr, sessions_mod, "read_claude", original)
        index.refresh(NOW)
        self.assertEqual(calls, [])
        self.claude_session("c1", "/srv/a", NOW - 100, title="B")
        [session] = index.refresh(NOW)
        self.assertEqual((len(calls), session.title), (1, "B"))

    def test_broken_lines_and_missing_dirs_are_ignored(self):
        self.write(os.path.join(self.claude, "projects", "-srv-x", "c-bad.jsonl"),
                   ["{nie json", json.dumps({"type": "user", "cwd": "/srv/x", "timestamp": iso(NOW - 10),
                                             "message": {"content": "działa"}})], NOW)
        [session] = SessionIndex(self.claude, self.codex).refresh(NOW)
        self.assertEqual((session.cwds, session.title), (("/srv/x",), "działa"))
        self.assertEqual(SessionIndex(os.path.join(self.tmp, "nie-ma"), os.path.join(self.tmp, "nie-ma")).refresh(NOW), [])


@unittest.skipUnless(shutil.which("git"), "brak gita")
class ScannerLinkTest(LogFixture):
    """Sesja w worktree z prawdziwego repozytorium: narzędzie, tytuł i ostatnia aktywność."""

    def test_session_sets_tool_title_and_last_activity(self):
        root = os.path.join(self.tmp, "work")
        p = gitrepos.build(root)
        now = gitrepos.COMMIT_TS + 10 * DAY
        os.utime(os.path.join(p["dirty"], "README.md"), (gitrepos.COMMIT_TS, gitrepos.COMMIT_TS))
        self.claude_session("c-un", p["unmerged"], now - 2 * DAY, title="Niescalona praca", mtime=now - 2 * DAY)
        self.codex_session("0000000a-0000-4000-8000-0000000000dd", p["dirty"], now - 3600, mtime=now - 3600)
        scanner = WorktreeScanner(root, clock=lambda: now, sessions=SessionIndex(self.claude, self.codex))
        scanner.refresh()
        items = {os.path.basename(w["path"]): w for w in scanner.snapshot([])}
        unmerged = items["unmerged"]
        self.assertEqual((unmerged["tool"], unmerged["sessionTitle"], unmerged["group"]),
                         ("claude", "Niescalona praca", "decide"))
        self.assertAlmostEqual(unmerged["lastActivityTs"], now - 2 * DAY, places=0)
        self.assertEqual(unmerged["subject"], "add u2.txt")
        dirty = items["dirty"]
        self.assertEqual((dirty["tool"], dirty["sessionTitle"], dirty["group"]), ("codex", "napraw Y", "active"),
                         "sesja sprzed godziny to aktywność z ostatniej doby")
        self.assertEqual(items["merged"]["sessionTitle"], "")

    def test_badge_comes_from_live_session(self):
        root = os.path.join(self.tmp, "work")
        p = gitrepos.build(root)
        now = gitrepos.COMMIT_TS + 10 * DAY
        self.claude_session("c-live", p["unmerged"], now - 2 * DAY, title="Claude pracuje", mtime=now - 2 * DAY)
        self.codex_session("0000000a-0000-4000-8000-0000000000ab", p["unmerged"], now - 3600, mtime=now - 3600)
        scanner = WorktreeScanner(root, clock=lambda: now, sessions=SessionIndex(self.claude, self.codex))
        scanner.refresh()
        live = live_sessions([{"tool": "claude", "sessionId": "c-live", "state": "waiting"}])
        items = {os.path.basename(w["path"]): w for w in scanner.snapshot([], live=live)}
        self.assertEqual((items["unmerged"]["tool"], items["unmerged"]["sessionId"]), ("claude", "c-live"))


if __name__ == "__main__":
    unittest.main()
