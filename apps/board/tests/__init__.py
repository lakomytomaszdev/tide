"""Testy widoku Tablica okna Tide: logika (board.js), QML na przykładowej tablicy, zrzuty, backend na prywatnej magistrali."""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(APP_DIR))

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
