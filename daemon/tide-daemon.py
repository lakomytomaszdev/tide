#!/usr/bin/python3
"""Uruchamia tide-daemon (pakiet tide_daemon leży obok tego pliku)."""
import os
import sys

# Usługa systemd użytkownika nie musi mieć ~/.local/bin w PATH, choć tam instalują się programy agentów.
local_bin = os.path.join(os.path.expanduser("~"), ".local", "bin")
path = os.environ.get("PATH") or os.defpath
if local_bin not in path.split(os.pathsep):
    os.environ["PATH"] = path + os.pathsep + local_bin

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tide_daemon.app import main  # noqa: E402

sys.exit(main())
