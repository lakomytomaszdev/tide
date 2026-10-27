"""Testy tide-daemon. Uruchamiane przez `make test` (/usr/bin/python3 -m unittest)."""
import os
import sys

DAEMON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

if DAEMON_DIR not in sys.path:
    sys.path.insert(0, DAEMON_DIR)

# Testy nigdy nie wołają prawdziwego `claude` (opis „Na daily” w raporcie porannym): pusty
# $TIDE_CLAUDE_BIN znaczy „brak programu”, więc raport bierze reguły. Testy podsumowania podają
# własną atrapę (test_report_standup.py), usługi uruchamiane w testach dziedziczą tę zmienną.
os.environ["TIDE_CLAUDE_BIN"] = ""
