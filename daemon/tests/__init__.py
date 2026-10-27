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

# Testy nigdy nie pytają prawdziwego Dockera (inne projekty na tym komputerze zależą od jego
# kontenerów): domyślnie nieistniejący socket, usługi w testach dziedziczą tę zmienną. Testy
# obszaru Docker podają socket atrapy Engine API (fake_docker.py).
os.environ["DOCKER_HOST"] = "unix:///nonexistent/tide-tests/docker.sock"

# Testy nigdy nie czytają prawdziwej tablicy pracy (~/DevWorkspace/tide-board): plany projektów
# z `ai-docs: tide` (coordination/boardplans.py) i katalogi agentów (/Tasks addDirs) bierzemy
# z nieistniejącego katalogu, chyba że test poda własną tablicę (TIDE_BOARD_DIR, --board-dir).
os.environ["TIDE_BOARD_DIR"] = "/nonexistent/tide-tests/board"
