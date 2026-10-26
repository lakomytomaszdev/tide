"""Testy tide-daemon. Uruchamiane przez `make test` (/usr/bin/python3 -m unittest)."""
import os
import sys

DAEMON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

if DAEMON_DIR not in sys.path:
    sys.path.insert(0, DAEMON_DIR)
