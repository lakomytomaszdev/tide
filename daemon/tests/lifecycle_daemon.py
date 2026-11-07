"""Prawdziwy punkt wejścia usługi ze znacznikami diagnostycznymi dla PT-253."""
import faulthandler
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tide_daemon.app import main

faulthandler.register(signal.SIGUSR1, all_threads=True)
code = main()
print("lifecycle: app.main returned", flush=True)
sys.exit(code)
