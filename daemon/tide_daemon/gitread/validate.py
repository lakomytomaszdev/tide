"""Walidacja wejścia odczytów gita (K-36, PT-349) — jedna funkcja granicy uprawnień dla `repo` i refów.

`Git.Diff` (źródła `commit`, `range`), a dalej `Git.Commits`, `Git.BranchMap` i `path` worktree (#29, K-99)
dostają `repo` i refy od klienta D-Bus. Metoda nie może być czytnikiem dowolnych katalogów ani sposobem na
wstrzyknięcie opcji do gita, więc każde takie wejście przechodzi tędy:

- `known_repo(repo, allowed)` — `repo` musi być ścieżką bezwzględną, bez składników `..`, a po rozwiązaniu
  (`realpath`, bez dowiązań) równą jednej z dozwolonych ścieżek (repozytoria projektów Tablicy i worktrees z listy
  Worktrees, także one rozwiązane). Zwraca ścieżkę kanoniczną, na której dopiero wolno uruchomić gita. Dowiązanie
  wskazujące poza listę, ścieżka z `..` i katalog spoza listy kończą się `bad-request` — bez uruchamiania gita,
- `commit_of(reader, repo, ref)` — `ref` to niepusty napis niezaczynający się od `-`, bez znaków sterujących,
  który `rev-parse --verify --end-of-options <ref>^{commit}` rozwiązuje do commita; zwraca pełny SHA.
  Inaczej `bad-request`.
"""
import os

from . import read
from .runner import GitReadError


def known_repo(repo, allowed) -> str:
    """Ścieżka kanoniczna `repo`, gdy jest repozytorium albo worktree z `allowed`; inaczej `GitReadError(bad-request)`."""
    if not isinstance(repo, str) or not repo:
        raise GitReadError("bad-request", "brak repozytorium (repo: ścieżka bezwzględna)")
    if "\0" in repo or any(ord(c) < 32 for c in repo):
        raise GitReadError("bad-request", "repo zawiera znak sterujący")
    if not os.path.isabs(repo):
        raise GitReadError("bad-request", f"repo musi być ścieżką bezwzględną: {repo!r}")
    if ".." in repo.split("/"):
        raise GitReadError("bad-request", f"repo nie może zawierać „..”: {repo!r}")
    real = os.path.realpath(repo)
    for item in allowed:
        if isinstance(item, str) and item and os.path.isabs(item) and os.path.realpath(item) == real:
            return real
    raise GitReadError("bad-request", f"nieznane repozytorium: {repo!r} — dozwolone są repozytoria projektów Tablicy "
                                      "i worktrees z listy Worktrees")


def commit_of(reader, repo: str, ref) -> str:
    """Pełny SHA commita, do którego rozwiązuje się `ref` w `repo` (już zwalidowanym `known_repo`)."""
    if not isinstance(ref, str) or not ref:
        raise GitReadError("bad-request", "brak refu albo SHA (niepusty napis)")
    if ref.startswith("-"):
        raise GitReadError("bad-request", f"ref nie może zaczynać się od „-”: {ref!r}")
    if any(ord(c) < 32 or ord(c) == 127 for c in ref):
        raise GitReadError("bad-request", f"ref ze znakiem sterującym: {ref!r}")
    sha = read.rev_parse(reader, repo, ref)
    if sha is None:
        raise GitReadError("bad-request", f"to nie jest commit: {ref!r}")
    return sha
