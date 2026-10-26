"""Indeks historii sesji dla KRunnera na syntetycznych dziennikach (history.py): tytuły,
pierwsza wiadomość, odcięcie po 30 dniach i odczyt przyrostowy."""
import json
import os
import tempfile
import unittest

from . import history
from tide_daemon.agents.jsonl import TAIL_BYTES
from tide_daemon.krunner.index import TITLE_LIMIT, SessionIndex, claude_prompt, codex_prompt, short_title

U = history.codex_uid


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-krunner-index-")
        self.addCleanup(history.remove, self.tmp)
        self.p = history.install(self.tmp)
        self.index = SessionIndex(self.p["claude"], self.p["codex"], clock=lambda: history.NOW)

    def entries(self):
        return {e["id"]: e for e in self.index.entries()}

    def test_sessions_from_both_tools_within_30_days(self):
        self.index.refresh()
        self.assertEqual(set(self.entries()), {
            "claude:h-titled", "claude:h-custom", "claude:h-prompt", "claude:h-gone",
            f"codex:{U(101)}", f"codex:{U(102)}", f"codex:{U(104)}",
        })

    def test_entry_has_only_small_metadata(self):
        self.index.refresh()
        e = self.entries()["claude:h-titled"]
        self.assertEqual(e, {
            "id": "claude:h-titled", "tool": "claude", "sessionId": "h-titled", "cwd": self.p["alpha"],
            "project": "alpha", "branch": "feat/login", "title": "Login form fix",
            "lastActivity": history.NOW - 3 * 86400 + 60 - 2 * 3600,  # 2026-10-23T10:01Z
        })

    def test_order_is_most_recent_first(self):
        self.index.refresh()
        ids = [e["id"] for e in self.index.entries()]
        self.assertEqual(ids[:2], [f"codex:{U(102)}", "claude:h-prompt"])

    def test_claude_titles(self):
        self.index.refresh()
        e = self.entries()
        self.assertEqual(e["claude:h-custom"]["title"], "Release checklist")  # nadany przez użytkownika wygrywa
        # Bez tytułu: pierwsza prawdziwa wiadomość (wstawki poleceń i isMeta pominięte), spacje ściśnięte.
        self.assertEqual(e["claude:h-prompt"]["title"], "Refactor the parser module")

    def test_codex_titles(self):
        self.index.refresh()
        e = self.entries()
        self.assertEqual(e[f"codex:{U(101)}"]["title"], "Uploader retries")      # session_index.jsonl, ostatnia nazwa
        self.assertEqual(e[f"codex:{U(102)}"]["title"], "Investigate the flaky test")  # item_completed UserMessage
        self.assertEqual(e[f"codex:{U(104)}"]["title"], "Write docs for the KRunner plugin")  # po AGENTS.md
        self.assertEqual((e[f"codex:{U(101)}"]["project"], e[f"codex:{U(101)}"]["branch"]), ("beta", "feat/uploader"))

    def test_codex_title_from_app_state_db(self):
        history.add_state_db(self.p["codex"], {U(102): "Flaky test hunt", U(101): "Not used: user name wins"})
        self.index.refresh()
        e = self.entries()
        self.assertEqual(e[f"codex:{U(102)}"]["title"], "Flaky test hunt")
        self.assertEqual(e[f"codex:{U(101)}"]["title"], "Uploader retries")

    def test_broken_state_db_is_ignored(self):
        with open(os.path.join(self.p["codex"], "state_7.sqlite"), "w", encoding="utf-8") as fh:
            fh.write("to nie jest baza")
        self.index.refresh()
        self.assertEqual(self.entries()[f"codex:{U(102)}"]["title"], "Investigate the flaky test")

    def test_cutoff_uses_last_activity_not_only_mtime(self):
        # Plik dotknięty niedawno (np. skopiowany), ale ostatni wpis sprzed 55 dni — nadal poza oknem.
        old = os.path.join(self.p["claude"], "projects/-ROOT-work-alpha/h-old.jsonl")
        os.utime(old, (history.NOW, history.NOW))
        self.index.refresh()
        self.assertNotIn("claude:h-old", self.entries())

    def test_longer_window_includes_older_sessions(self):
        index = SessionIndex(self.p["claude"], self.p["codex"], max_age=60 * 86400, clock=lambda: history.NOW)
        index.refresh()
        ids = {e["id"] for e in index.entries()}
        self.assertIn("claude:h-old", ids)
        self.assertIn(f"codex:{U(105)}", ids)

    def test_incremental_refresh_reads_only_changed_files(self):
        self.assertEqual(self.index.refresh(), 8)  # 4 Claude + 4 Codex z ostatnich 30 dni (w tym podagent)
        self.assertEqual(self.index.refresh(), 0)  # nic się nie zmieniło — żaden plik nie jest otwierany
        path = os.path.join(self.p["claude"], "projects/-ROOT-work-alpha/h-titled.jsonl")
        history.append(path, [
            {"type": "custom-title", "customTitle": "Login, renamed", "sessionId": "h-titled"},
            {"type": "user", "timestamp": "2026-10-26T11:00:00.000Z", "cwd": self.p["alpha"],
             "gitBranch": "feat/login-2", "sessionId": "h-titled", "message": {"role": "user", "content": "more"}},
        ], ts=history.NOW - 3600)
        self.assertEqual(self.index.refresh(), 1)
        e = self.entries()["claude:h-titled"]
        self.assertEqual((e["title"], e["branch"]), ("Login, renamed", "feat/login-2"))
        self.assertEqual(e["lastActivity"], history.NOW - 3600)
        self.assertEqual(self.index.entries()[0]["id"], "claude:h-titled")

    def test_new_thread_name_is_picked_up(self):
        self.index.refresh()
        history.append(os.path.join(self.p["codex"], "session_index.jsonl"),
                       [{"id": U(104), "thread_name": "KRunner docs", "updated_at": "2026-10-26T11:00:00Z"}])
        self.index.refresh()
        self.assertEqual(self.entries()[f"codex:{U(104)}"]["title"], "KRunner docs")

    def test_large_file_gets_prompt_from_head_and_title_from_tail(self):
        cwd = self.p["alpha"]
        path = os.path.join(self.p["claude"], "projects/-ROOT-work-alpha/h-big.jsonl")
        filler = "x" * 2000
        records = [{"type": "user", "timestamp": "2026-10-26T08:00:00.000Z", "cwd": cwd, "gitBranch": "big",
                    "sessionId": "h-big", "message": {"role": "user", "content": "Start of a long session"}}]
        records += [{"type": "assistant", "timestamp": "2026-10-26T08:10:00.000Z", "cwd": cwd, "gitBranch": "big",
                     "sessionId": "h-big", "message": {"role": "assistant", "content": [{"type": "text", "text": filler}]}}
                    ] * (TAIL_BYTES * 2 // len(filler))
        history.append(path, records, ts=history.NOW - 600)
        index = SessionIndex(self.p["claude"], self.p["codex"], clock=lambda: history.NOW)
        index.refresh()
        self.assertGreater(os.path.getsize(path), TAIL_BYTES * 2)
        self.assertEqual({e["id"]: e for e in index.entries()}["claude:h-big"]["title"], "Start of a long session")
        history.append(path, [{"type": "ai-title", "aiTitle": "Long session title", "sessionId": "h-big"}])
        index.refresh()
        self.assertEqual({e["id"]: e for e in index.entries()}["claude:h-big"]["title"], "Long session title")

    def test_missing_directories_give_empty_index(self):
        index = SessionIndex(os.path.join(self.tmp, "nope"), os.path.join(self.tmp, "nope2"), clock=lambda: history.NOW)
        self.assertEqual(index.refresh(), 0)
        self.assertEqual(index.entries(), [])
        self.assertTrue(index.built)


class TitleHelpersTest(unittest.TestCase):
    def test_short_title_truncates(self):
        text = "słowo " * 40
        out = short_title(text)
        self.assertEqual(len(out), TITLE_LIMIT)
        self.assertTrue(out.endswith("…"))
        self.assertEqual(short_title("  a\n\tb  "), "a b")

    def test_prompt_filters(self):
        self.assertEqual(claude_prompt({"type": "user", "message": {"content": "Hi there"}}), "Hi there")
        self.assertEqual(claude_prompt({"type": "user", "isSidechain": True, "message": {"content": "x"}}), "")
        self.assertEqual(claude_prompt({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "1", "content": "out"}]}}), "")
        self.assertEqual(claude_prompt({"type": "user", "message": {
            "content": "[Request interrupted by user]"}}), "")
        self.assertEqual(codex_prompt({"type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "x"}]}}), "")
        self.assertEqual(codex_prompt({"type": "event_msg", "payload": {"type": "user_message",
                                                                        "message": "<heartbeat>"}}), "")
        self.assertEqual(json.loads(json.dumps(codex_prompt({"type": "x", "payload": []}))), "")


if __name__ == "__main__":
    unittest.main()
