"""Tablica pracy: karty („rzeczy do zrobienia”) ze wszystkich projektów w lokalnym repozytorium git.

- marker.py      — rozpoznanie repozytorium tablicy po `board.md` (skaner repozytoriów je pomija),
- frontmatter.py — płaski frontmatter z zachowaniem nieznanych kluczy przy zapisie,
- model.py       — kolumny, karty, projekty, tagi; odczyt z walidacją,
- flows.py       — szablony poleceń trybów full, light i spec,
- store.py       — odczyt, zapis z commitem każdej zmiany, migawka dla D-Bus.

Celowo bez importu store.py: marker.py importuje skaner worktrees, a store.py — moduły worktrees.
Opis: docs/tide-daemon.md (Tablica).
"""
from .marker import MARKER_FILE, default_board_dir, is_board_repo

__all__ = ["MARKER_FILE", "default_board_dir", "is_board_repo"]
