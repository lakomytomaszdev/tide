"""Zwykły nowy test produktu: izolacja pakietu działa bez fixture środowiska lub mirror."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from . import TEST_STATE_HOME

if os.environ.get("TIDE_TEST_ISOLATION_REPORT"):
    # Próba uruchamiana osobno przez każde publiczne wejście, przed pierwszym importem produktu.
    assert not any(name == "tide_daemon" or name.startswith("tide_daemon.") for name in sys.modules)
    assert os.environ["XDG_STATE_HOME"] == TEST_STATE_HOME

from tide_daemon.cardflow.journal import Journal


class DefaultJournalTest(unittest.TestCase):
    def test_default_journal_writes_both_copies_inside_package_isolation(self):
        state = Path(TEST_STATE_HOME)
        self.assertEqual(os.environ["XDG_STATE_HOME"], str(state))
        journal = Journal(str(state / "probe-board" / str(os.getpid())))
        event = journal.append("K-1", "isolation.probe", synthetic=True)
        paths = [Path(journal.path("K-1")), Path(journal.mirror_path("K-1"))]
        for path in paths:
            self.assertTrue(path.is_relative_to(state), str(path))
            self.assertEqual(json.loads(path.read_text()), event)
        child = subprocess.run([sys.executable, "-c", '''
import json, os
from pathlib import Path
from daemon.tests import TEST_STATE_HOME
from tide_daemon.cardflow.journal import Journal
journal = Journal(str(Path(TEST_STATE_HOME) / "child-board" / str(os.getpid())))
journal.append("K-2", "isolation.child", synthetic=True)
print(json.dumps({"state": TEST_STATE_HOME, "mirror": journal.mirror_path("K-2"),
                  "roots": json.loads(os.environ["TIDE_TEST_ORIGINAL_STATE_ROOTS"])}))
'''], capture_output=True, text=True, check=True, timeout=20)
        child = json.loads(child.stdout)
        self.assertEqual(child["state"], str(state))
        self.assertTrue(Path(child["mirror"]).is_relative_to(state))
        self.assertTrue(Path(child["mirror"]).is_file())
        self.assertEqual(child["roots"], json.loads(os.environ["TIDE_TEST_ORIGINAL_STATE_ROOTS"]))
        self.assertTrue(state.is_dir(), "potomek nie usuwa katalogu rodzica")
        report = os.environ.get("TIDE_TEST_ISOLATION_REPORT")
        if report:
            data = {
                "state": str(state), "home": os.environ.get("HOME"),
                "paths": [str(path) for path in paths],
                "roots": json.loads(os.environ["TIDE_TEST_ORIGINAL_STATE_ROOTS"]),
                "child": child,
            }
            Path(report).write_text(json.dumps(data), encoding="utf-8")
            if os.environ.get("TIDE_TEST_ISOLATION_WAIT"):
                print(json.dumps(data), flush=True)
                sys.stdin.readline()
