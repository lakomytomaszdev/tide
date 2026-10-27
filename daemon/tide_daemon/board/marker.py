"""Rozpoznanie repozytorium tablicy po znaczniku, nie po nazwie katalogu.

Tablica to zwykłe repozytorium git w katalogu projektów, więc skaner repozytoriów
(worktrees/scanner.py: find_repositories) widziałby je jak każdy projekt. Znacznik to plik
`board.md` w głównym katalogu z kluczem `tide-board` we frontmatterze — działa także dla
tablicy w innym miejscu (`--board-dir`, `$TIDE_BOARD_DIR`).

Moduł bez zależności od reszty pakietu tablicy: importuje go skaner worktrees.
"""
import os

MARKER_FILE = "board.md"
MARKER_KEY = "tide-board"
FORMAT_VERSION = "1"
_HEAD_BYTES = 4096


def default_board_dir() -> str:
    return os.environ.get("TIDE_BOARD_DIR") or os.path.expanduser("~/DevWorkspace/tide-board")


def is_board_repo(path: str) -> bool:
    """Czy katalog to tablica Tide: `board.md` z `tide-board: <wersja>` we frontmatterze."""
    try:
        with open(os.path.join(path, MARKER_FILE), encoding="utf-8", errors="replace") as fh:
            head = fh.read(_HEAD_BYTES)
    except OSError:
        return False
    lines = head.lstrip("﻿").split("\n")
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            return False
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == MARKER_KEY and value.strip().strip("\"'"):
            return True
    return False
