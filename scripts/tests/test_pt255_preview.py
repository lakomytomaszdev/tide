"""Syntetyczna kontrola osłony podglądu; bez dostępu do usługi i stanu hosta."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("pt255_preview", TOOLS / "pt255_cleanup_preview.py")
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


class PreviewPreflightTest(unittest.TestCase):
    def metadata(self, state=b"", active="active", pid=123):
        result = subprocess.CompletedProcess([], 0,
            f"ActiveState={active}\nMainPID={pid}\nExecMainStartTimestamp=now\n")
        environment = b"HOME=/synthetic-home\0SECRET=never-publish\0"
        if state:
            environment += b"XDG_STATE_HOME=" + state + b"\0"
        return patch.object(preview.subprocess, "run", return_value=result), patch.object(
            Path, "read_bytes", return_value=environment)

    def test_uses_actual_service_xdg_without_disclosing_environment(self):
        command, environment = self.metadata(b"/synthetic-state")
        with command, environment:
            result = preview.service_metadata("/synthetic-state/tide/cardflow")
        self.assertEqual(result["source"], "/synthetic-state/tide/cardflow")
        self.assertNotIn("never-publish", str(result))

    def test_default_comes_from_service_home(self):
        command, environment = self.metadata()
        with command, environment:
            result = preview.service_metadata("/synthetic-home/.local/state/tide/cardflow")
        self.assertEqual(result["main_pid"], 123)

    def test_wrong_source_and_relative_xdg_refuse_preview(self):
        for state in (b"/different", b"relative"):
            command, environment = self.metadata(state)
            with command, environment, self.assertRaises(ValueError):
                preview.service_metadata("/synthetic-home/.local/state/tide/cardflow")

    def test_inactive_service_never_reads_process_environment(self):
        command, environment = self.metadata(active="inactive", pid=0)
        with command, environment as read, self.assertRaises(ValueError):
            preview.service_metadata("/synthetic-home/.local/state/tide/cardflow")
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
