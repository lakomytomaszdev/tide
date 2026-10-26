#!/usr/bin/python3
"""Uruchamia tide-daemon (pakiet tide_daemon leży obok tego pliku)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tide_daemon.app import main  # noqa: E402

sys.exit(main())
