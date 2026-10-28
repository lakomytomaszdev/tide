"""widgets/stale.py: widżety nowsze od działającej powłoki (PT-32)."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stale  # noqa: E402


class StaleWidgetsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tide-stale-")
        self.addCleanup(shutil.rmtree, self.root)

    def widget(self, name, mtime):
        path = os.path.join(self.root, name, "contents", "ui")
        os.makedirs(path)
        file = os.path.join(path, "main.qml")
        open(file, "w").close()
        os.utime(file, (mtime, mtime))

    def test_only_tide_widgets_newer_than_the_shell(self):
        self.widget("org.tide.agents", 2000)
        self.widget("org.tide.board", 3000)
        self.widget("org.tide.limits", 500)
        self.widget("org.kde.other", 3000)
        self.assertEqual(stale.stale_widgets(self.root, 1000), ["org.tide.agents", "org.tide.board"])
        self.assertEqual(stale.stale_widgets(self.root, None), [], "powłoka nie działa")
        self.assertEqual(stale.stale_widgets(os.path.join(self.root, "brak"), 1000), [])

    def test_message_names_widgets_and_the_restart(self):
        self.assertEqual(stale.message([]), "")
        text = stale.message(["org.tide.agents", "org.tide.board"])
        self.assertIn("agents, board", text)
        self.assertIn("systemctl --user restart plasma-plasmashell", text)

    def test_shell_start_from_proc(self):
        self.assertIsNone(stale.shell_started(self.root), "bez /proc/stat")


if __name__ == "__main__":
    unittest.main()
