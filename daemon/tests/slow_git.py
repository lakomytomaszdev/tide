#!/usr/bin/python3
"""Kontrolowana wolna operacja odczytu; poza uzbrojonym odczytem wywołuje prawdziwy git."""
import os
import sys
import time
import signal
import subprocess
from pathlib import Path

root = Path(os.environ.get("TIDE_LIFECYCLE_DIR", "/nonexistent/tide-lifecycle"))
if (root / "slow-arm").exists() and sys.argv[-3:] == ["worktree", "list", "--porcelain"]:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    (root / "slow-pid").write_text(str(os.getpid()) + " " + str(child.pid))
    time.sleep(120)
os.execv("/usr/bin/git", ["git", *sys.argv[1:]])
