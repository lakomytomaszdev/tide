"""Testy widoku Tablica okna Tide: logika (board.js), QML na przykładowej tablicy, zrzuty, backend na prywatnej magistrali."""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(APP_DIR))

# 06.11: bezpośrednie `python3 -m unittest` (bez tools/test_runner.py) zapisało atrapę K-1 do
# ~/.local/state/tide/board-derived.json działającej usługi — karta-widmo na Tablicy użytkownika. Uruchamiacz daje
# każdemu modułowi własny katalog stanu (tide-test-state-*); przebieg poza nim dostaje tu własny katalog tymczasowy.
if "tide-test-state-" not in os.environ.get("XDG_STATE_HOME", ""):
    import tempfile
    # Bez sprzątania przy wyjściu: strażnik stanu testów (PT-254) traktuje usuwanie katalogu stanu jako naruszenie.
    os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tide-test-state-")
