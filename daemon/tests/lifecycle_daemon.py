"""Prawdziwy punkt wejścia usługi ze znacznikami diagnostycznymi dla PT-253."""
import faulthandler
import signal
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tide_daemon.app import main
from tide_daemon.worktrees.cancellation import Cancellation

# Wyłącznie harness testu: opóźniamy transport przed prawdziwym launcherem
# granicy, bez przełącznika ani wymiany /usr/bin/git w produkcie.
original_run = Cancellation.run


def delayed(self, argv, **kwargs):
    if len(argv) > 2 and argv[2].endswith("/git_sandbox.py"):
        argv = [str(Path(os.environ["TIDE_LIFECYCLE_DIR"]) / "fakebin/git"), *argv]
    return original_run(self, argv, **kwargs)


Cancellation.run = delayed

faulthandler.register(signal.SIGUSR1, all_threads=True)
code = main()
print("lifecycle: app.main returned", flush=True)
sys.exit(code)
