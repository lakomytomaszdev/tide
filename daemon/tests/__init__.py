"""Testy tide-daemon. Uruchamiane przez `make test` (/usr/bin/python3 -m unittest)."""
import os
import sys

# Musi poprzedzać każdy import produktu, także przy unittest i pytest bez runnera.
from tools.test_isolation import initialize

TEST_STATE_HOME = initialize()

DAEMON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

if DAEMON_DIR not in sys.path:
    sys.path.insert(0, DAEMON_DIR)

# Testy nigdy nie wołają prawdziwego `claude` (opis „Na daily” w raporcie porannym): pusty
# $TIDE_CLAUDE_BIN znaczy „brak programu”, więc raport bierze reguły. Testy podsumowania podają
# własną atrapę (test_report_standup.py), usługi uruchamiane w testach dziedziczą tę zmienną.
os.environ["TIDE_CLAUDE_BIN"] = ""

# 06.11: bezpośrednie `python3 -m unittest` (bez tools/test_runner.py) zapisało atrapę K-1 do
# ~/.local/state/tide/board-derived.json działającej usługi — karta-widmo na Tablicy użytkownika. Uruchamiacz daje
# każdemu modułowi własny katalog stanu (tide-test-state-*); przebieg poza nim dostaje tu własny katalog tymczasowy.
if "tide-test-state-" not in os.environ.get("XDG_STATE_HOME", ""):
    import atexit
    import shutil
    import tempfile
    _STATE = tempfile.mkdtemp(prefix="tide-test-state-")
    os.environ["XDG_STATE_HOME"] = _STATE
    atexit.register(shutil.rmtree, _STATE, True)

# Testy nigdy nie pytają prawdziwego Dockera (inne projekty na tym komputerze zależą od jego
# kontenerów): domyślnie nieistniejący socket, usługi w testach dziedziczą tę zmienną. Testy
# obszaru Docker podają socket atrapy Engine API (fake_docker.py).
os.environ["DOCKER_HOST"] = "unix:///nonexistent/tide-tests/docker.sock"

# Testy nigdy nie pytają prawdziwej Ollamy (127.0.0.1:11434 na tym komputerze — domyślny punkt modeli
# lokalnych): ustawienia modeli wskazują zamknięty port, usługi w testach dziedziczą tę zmienną.
# Testy modeli podają własny models.json z adresem atrapy (test_models_core.py, test_models_dbus.py).
os.environ["TIDE_MODELS_CONFIG"] = os.path.join(FIXTURES, "models-closed.json")
# Testy nigdy nie czytają prawdziwej tablicy pracy (~/DevWorkspace/tide-board): plany projektów
# z `ai-docs: tide` (coordination/boardplans.py) i katalogi agentów (/Tasks addDirs) bierzemy
# z nieistniejącego katalogu, chyba że test poda własną tablicę (TIDE_BOARD_DIR, --board-dir).
os.environ["TIDE_BOARD_DIR"] = "/nonexistent/tide-tests/board"
# Testy nigdy nie czytają prawdziwych dzienników Codexa (~/.codex, ~/snap/codex): bez --codex-dir
# usługa w teście dostaje nieistniejący $CODEX_HOME zamiast domyślnych katalogów (codex_homes.py).
os.environ["CODEX_HOME"] = "/nonexistent/tide-tests/codex"
# Testy nigdy nie nakładają wyglądu aktywności na pulpit (zainstalowany silnik w
# $XDG_DATA_HOME/tide/activity-look zmieniałby kolory, styl i tapetę sesji): pusty
# $TIDE_ACTIVITY_LOOK_ENGINE wyłącza nakładanie, usługi w testach dziedziczą tę zmienną.
# Testy wyglądu aktywności podają atrapę uruchamiania silnika (test_activity_look.py).
os.environ["TIDE_ACTIVITY_LOOK_ENGINE"] = ""
# Wygląd aktywności dla widżetów i filtr powiadomień Stream (activity_state.py): bez opisów wyglądów każda
# aktywność ma wygląd pracy, więc usługa w teście nigdy nie zmienia plasmanotifyrc — chyba że test poda
# własny katalog opisów i tymczasowy XDG_CONFIG_HOME (test_stream_mode.py).
os.environ["TIDE_ACTIVITY_LOOKS"] = "/nonexistent/tide-tests/activities"

# Testy nigdy nie zmieniają prawdziwego układu monitorów ani wejścia monitora (PT-38): kscreen-doctor i ddcutil
# usługa szuka w nieistniejącym katalogu, a testy układu podają katalog atrap (fake_screens.py). Skróty Stream
# nie wołają prawdziwego `kamp` (rdzeń Streamer) — pusty $TIDE_KAMP; prawdziwy KGlobalAccel jest tylko
# na magistrali pulpitu, której testy nie dotykają (privatebus.py).
os.environ["TIDE_MONITORS_BIN"] = "/nonexistent/tide-tests/monitors-bin"
os.environ["TIDE_KAMP"] = ""
