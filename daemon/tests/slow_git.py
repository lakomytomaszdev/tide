#!/usr/bin/python3
"""Kontrolowana wolna operacja odczytu; poza uzbrojonym odczytem wywołuje prawdziwy git."""
import os
import sys
import time
import signal
import subprocess
import json
from pathlib import Path

root = Path(os.environ.get("TIDE_LIFECYCLE_DIR", "/nonexistent/tide-lifecycle"))
wrapped = len(sys.argv) > 2 and sys.argv[1] == "/usr/bin/python3"
arguments = json.loads(sys.argv[-1])["args"] if wrapped else sys.argv[1:]
if (root / "slow-arm").exists() and arguments[-3:] == ["worktree", "list", "--porcelain"]:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    (root / "slow-pid").write_text(str(os.getpid()) + " " + str(child.pid))
    time.sleep(120)
if wrapped:
    os.execv(sys.argv[1], sys.argv[1:])
os.execv("/usr/bin/git", ["git", *sys.argv[1:]])
