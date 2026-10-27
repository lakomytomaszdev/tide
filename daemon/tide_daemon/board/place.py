"""Gdzie naprawdę pracuje sesja agenta, która weszła do repozytorium tablicy Tide (PT-28).

Agent karty projektu z `ai-docs: tide` pisze plany w `<tablica>/docs/<projekt>/`. Gdy zrobi tam `cd`,
jego katalog roboczy to repozytorium tablicy — bez tej poprawki /Agents, raport poranny, KRunner,
Sesje i stan kart przypisałyby sesję „projektowi” tide-board na gałęzi master, a karta straciłaby
połączenie z pracą. `work_cwd(cwd, launch_cwd)` to katalog pracy sesji:

- cwd poza tablicą — bez zmian,
- cwd w tablicy, a katalog startu sesji poza nią — katalog startu (worktree albo repozytorium karty),
- inaczej `docs/<slug>/…` — repozytorium projektu `<slug>` z tablicy (`projects/<slug>.md`),
- inaczej cwd (np. `docs/_inbox` karty bez projektu — to naprawdę praca w tablicy).

Tablicę poznajemy po znaczniku (`board.md`, marker.py) w głównym katalogu repozytorium, gdziekolwiek
leży — nie po nazwie ani po `$TIDE_BOARD_DIR`. Wynik dla katalogu trzymamy CACHE_TTL sekund: lista
sesji liczy się kilka razy na sekundę, a katalog rzadko staje się tablicą.
"""
import os
import threading
import time

from .marker import is_board_repo

CACHE_TTL = 30.0
CACHE_MAX = 512

_lock = threading.Lock()
_roots: dict[str, tuple[float, str]] = {}   # katalog → (czas sprawdzenia, katalog tablicy albo "")


def board_root(path: str) -> str:
    """Główny katalog tablicy Tide, w której leży `path`, albo pusty napis."""
    if not path:
        return ""
    now = time.monotonic()
    with _lock:
        hit = _roots.get(path)
    if hit is not None and now - hit[0] < CACHE_TTL:
        return hit[1]
    top = _git_top(path)
    root = top if top and is_board_repo(top) else ""
    with _lock:
        if len(_roots) >= CACHE_MAX:
            _roots.clear()
        _roots[path] = (now, root)
    return root


def in_board(path: str) -> bool:
    return bool(board_root(path))


def work_cwd(cwd: str, launch_cwd: str = "") -> str:
    """Katalog pracy sesji (opis w nagłówku modułu). Pusty cwd — katalog startu (albo pusty)."""
    if not cwd:
        return launch_cwd or ""
    board = board_root(cwd)
    if not board:
        return cwd
    if launch_cwd and not board_root(launch_cwd):
        return launch_cwd
    repo = docs_project_repo(cwd, board)
    return repo or cwd


def docs_project_repo(path: str, board: str) -> str:
    """`<tablica>/docs/<slug>/…` → repozytorium projektu `<slug>` (istniejący katalog) albo pusty napis."""
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(board))
    parts = rel.split(os.sep)
    if len(parts) < 2 or parts[0] != "docs" or parts[1] in ("", ".", ".."):
        return ""
    from ..coordination import boardplans  # tu: boardplans czyta pliki projektów tablicy (z pamięcią)
    for repo, slug, _ai_docs in boardplans.projects(board):
        if slug == parts[1] and os.path.isdir(repo):
            return repo
    return ""


def _git_top(path: str) -> str:
    path = os.path.abspath(path).rstrip("/") or "/"
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return ""
        path = parent


def clear_cache() -> None:
    """Dla testów: zapomnij wyniki (katalog mógł właśnie stać się tablicą)."""
    with _lock:
        _roots.clear()
