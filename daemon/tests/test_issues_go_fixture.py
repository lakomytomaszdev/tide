"""Atrapa awarii zachowuje kontrakt dziennika także dla metadanych kind."""
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

from . import DAEMON_DIR
from tide_daemon import issue_acceptance
from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.journal import Journal


class GoFaultFixtureTest(unittest.TestCase):
    def test_journal_wrapper_accepts_event_name_and_kind_metadata_independently(self):
        fixture = Path(__file__).with_name("fixtures") / "issues_go_daemon.py"
        load_fixture = runpy.run_path
        with tempfile.TemporaryDirectory(prefix="tide-go-fault-") as root:
            # Uruchamiamy prawdziwy dekorator fixture; tylko wejście całej usługi
            # pomijamy, aby sygnał błędu nie zależał od prywatnego D-Bus.
            with mock.patch.object(sys, "argv", [str(fixture), str(Path(root, "control.json")),
                                                str(Path(DAEMON_DIR, "tide-daemon.py"))]), \
                    mock.patch.object(sys, "path", list(sys.path)), \
                    mock.patch.object(FlowEngine, "_start", FlowEngine._start), \
                    mock.patch.object(Journal, "append", Journal.append), \
                    mock.patch.object(issue_acceptance, "_save", issue_acceptance._save), \
                    mock.patch.object(runpy, "run_path"):
                load_fixture(str(fixture))
                journal = Journal(str(Path(root, "board")), mirror=str(Path(root, "state")))
                event = journal.append("K-1", "step.waiting", kind="planning", ticket="")
                self.assertEqual((event["type"], event["kind"]), ("step.waiting", "planning"))
                self.assertEqual(journal.read("K-1"), [event])
