"""Plik stanu agentów według katalogu dla promptu powłoki (#26)."""
import os
import shutil
import tempfile
import unittest

from . import DAEMON_DIR  # noqa: F401 — ustawia sys.path

from tide_daemon import cwdstate


class ByCwdTest(unittest.TestCase):
    def test_most_important_state_wins_per_directory(self):
        sessions = [
            {"cwd": "/w/tide", "state": "done", "tool": "codex"},
            {"cwd": "/w/tide/", "state": "working", "tool": "claude"},
            {"cwd": "/w/tide", "state": "waiting", "tool": "codex"},
            {"cwd": "/w/other", "state": "working", "tool": "claude"},
        ]
        self.assertEqual(cwdstate.by_cwd(sessions), {"/w/tide": ("waiting", "codex"),
                                                     "/w/other": ("working", "claude")})

    def test_idle_ended_and_odd_paths_are_skipped(self):
        sessions = [
            {"cwd": "/w/a", "state": "idle", "tool": "claude"},
            {"cwd": "/w/b", "state": "ended", "tool": "claude"},
            {"cwd": "", "state": "working", "tool": "claude"},
            {"cwd": "/w/tab\there", "state": "working", "tool": "claude"},
            {"cwd": "/w/new\nline", "state": "working", "tool": "claude"},
        ]
        self.assertEqual(cwdstate.by_cwd(sessions), {})

    def test_render_is_tab_separated_with_directory_last(self):
        text = cwdstate.render({"/w/z z": ("working", "claude"), "/w/a": ("done", "codex")})
        self.assertEqual(text, "done\tcodex\t/w/a\nworking\tclaude\t/w/z z\n")

    def test_default_path_follows_xdg_cache_home(self):
        old = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = "/tmp/c"
        try:
            self.assertEqual(cwdstate.default_path(), "/tmp/c/tide/agents-by-cwd.tsv")
        finally:
            if old is None:
                del os.environ["XDG_CACHE_HOME"]
            else:
                os.environ["XDG_CACHE_HOME"] = old


class CwdStateFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-cwd-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = os.path.join(self.tmp, "tide", "agents-by-cwd.tsv")
        self.file = cwdstate.CwdStateFile(self.path)

    def test_writes_only_on_change_and_clears(self):
        sessions = [{"cwd": "/w/tide", "state": "working", "tool": "claude"}]
        self.assertTrue(self.file.update(sessions))
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "working\tclaude\t/w/tide\n")
        self.assertFalse(self.file.update(sessions))
        self.assertTrue(self.file.update([]))
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "")
        self.file.clear()
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(os.listdir(os.path.dirname(self.path)), [])  # bez plików tymczasowych


if __name__ == "__main__":
    unittest.main()
