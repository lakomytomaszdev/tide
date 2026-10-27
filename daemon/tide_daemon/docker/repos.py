"""Katalog projektu compose → repozytorium git z katalogu projektów (bez uruchamiania gita).

Od `working_dir` w górę szukamy `.git`. Katalog `.git` to repozytorium główne; plik `.git`
(„gitdir: …/repo/.git/worktrees/x”) to worktree — wtedy nazwą jest repozytorium główne, a ścieżka
worktree idzie osobno. Dzięki temu compose uruchomiony z worktree Codexa w ~/.codex/worktrees
też trafia do swojego repozytorium, o ile repozytorium główne leży w katalogu projektów.
Repozytorium spoza katalogu projektów → None.
"""
from __future__ import annotations

import os


def _main_from_gitfile(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            line = fh.readline().strip()
    except OSError:
        return ""
    if not line.startswith("gitdir:"):
        return ""
    gitdir = line[len("gitdir:"):].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.normpath(os.path.join(os.path.dirname(path), gitdir))
    marker = os.sep + ".git" + os.sep + "worktrees" + os.sep
    if marker in gitdir:
        return gitdir.split(marker, 1)[0]
    return ""


def resolve(working_dir: str, workspace: str) -> dict | None:
    """{name, path, worktree} albo None. path to repozytorium główne, worktree — katalog worktree albo ""."""
    if not working_dir or not workspace:
        return None
    root = os.path.realpath(workspace)
    current = os.path.realpath(working_dir)
    while True:
        candidate = os.path.join(current, ".git")
        if os.path.isdir(candidate):
            main, worktree = current, ""
            break
        if os.path.isfile(candidate):
            main = _main_from_gitfile(candidate)
            if not main:
                return None
            main, worktree = os.path.realpath(main), current
            break
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    if main != root and not main.startswith(root + os.sep):
        return None
    return {"name": os.path.basename(main), "path": main, "worktree": worktree}


class RepoResolver:
    """resolve() z pamięcią na czas jednego odświeżenia (kilka kontenerów z tego samego katalogu)."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self._cache: dict[str, dict | None] = {}

    def __call__(self, working_dir: str) -> dict | None:
        if working_dir not in self._cache:
            self._cache[working_dir] = resolve(working_dir, self.workspace)
        found = self._cache[working_dir]
        return dict(found) if found else None
