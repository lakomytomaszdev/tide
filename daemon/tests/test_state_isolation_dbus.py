"""PT-251: rzeczywista usługa dziedziczy izolację pakietu i zapisuje domyślną kopię dziennika."""
import json
import os
from pathlib import Path
import sys

from . import DAEMON_DIR, TEST_STATE_HOME
from .privatebus import SERVICE, requires_dbus, wait_until
from .test_cardflow_dbus import FlowCase
from tide_daemon.cardflow.journal import mirror_root


@requires_dbus
class InheritedDaemonStateTest(FlowCase):
    def spawn(self):
        # Bez spawn_daemon: ta fixture świadomie nadpisuje XDG własnym katalogiem.
        # Tu prawdziwy proces produktu ma odziedziczyć XDG rodzica bez nadpisania.
        return self.service.spawn([
            sys.executable, os.path.join(DAEMON_DIR, "tide-daemon.py"),
            "--claude-dir", os.path.join(self.base, "home", ".claude"),
            "--codex-dir", os.path.join(self.base, "codex"),
            "--workspace", os.path.join(self.base, "ws"), "--board-dir", self.board,
            "--no-processes", "--no-notifications", "--no-krunner", "--no-tab-marks",
            "--no-idle", "--no-limits", "--no-models", "--no-tasks", "--no-coordination",
            "--no-youtrack", "--no-docker", "--no-activity-look", "--no-monitors",
            "--no-stream-shortcuts", "--no-report",
        ], SERVICE, extra_env=self.env)

    def test_default_service_journal_is_written_in_inherited_package_state(self):
        card = self.card(description="Dane syntetyczne.\n\nATRAPA: pytanie")
        self.assertTrue(self.start(card)["ok"])
        mirror = Path(mirror_root(self.board)) / (card + ".jsonl")
        self.assertTrue(mirror.is_relative_to(TEST_STATE_HOME))

        def copies_match():
            try:
                events = [json.loads(line) for line in mirror.read_text().splitlines()]
            except (OSError, ValueError):
                return False
            return bool(events) and events == self.events(card)

        self.assertTrue(wait_until(copies_match, timeout=10), str(mirror))
        self.assertTrue(self.of(card, "step.started"))
        self.assertFalse((Path(self.env["HOME"]) / ".local/state/tide/cardflow").exists())
        self.assertFalse((Path(self.base) / "state/tide/cardflow").exists())
