"""Projekt, gałąź i worktree dla katalogu roboczego — tylko odczyt plików .git, bez uruchamiania gita."""
import os
from dataclasses import dataclass


@dataclass
class Location:
    project: str
    branch: str
    worktree: str  # nazwa katalogu worktree albo "" dla głównego katalogu repozytorium


def describe(cwd: str, fallback_branch: str = "") -> Location:
    cwd = (cwd or "").rstrip("/") or "/"
    top, dotgit = _find_dotgit(cwd)
    if not top:
        return Location(os.path.basename(cwd) or cwd, fallback_branch, "")

    if os.path.isdir(dotgit):
        return Location(os.path.basename(top), _branch(dotgit) or fallback_branch, "")

    # Worktree: .git to plik „gitdir: <repo>/.git/worktrees/<nazwa>”.
    gitdir = _read_gitdir(dotgit, top)
    if not gitdir:
        return Location(os.path.basename(top), fallback_branch, "")
    common = gitdir
    try:
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as fh:
            common = os.path.normpath(os.path.join(gitdir, fh.read().strip()))
    except OSError:
        pass
    main_root = os.path.dirname(common) if os.path.basename(common) == ".git" else common
    project = os.path.basename(main_root.rstrip("/")) or os.path.basename(top)
    return Location(project, _branch(gitdir) or fallback_branch, os.path.basename(top))


def _find_dotgit(path: str) -> tuple[str, str]:
    while True:
        candidate = os.path.join(path, ".git")
        if os.path.exists(candidate):
            return path, candidate
        parent = os.path.dirname(path)
        if parent == path:
            return "", ""
        path = parent


def _read_gitdir(dotgit_file: str, top: str) -> str:
    try:
        with open(dotgit_file, encoding="utf-8") as fh:
            line = fh.readline().strip()
    except OSError:
        return ""
    if not line.startswith("gitdir:"):
        return ""
    return os.path.normpath(os.path.join(top, line[len("gitdir:"):].strip()))


def _branch(gitdir: str) -> str:
    try:
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
    except OSError:
        return ""
    if head.startswith("ref: refs/heads/"):
        return head[len("ref: refs/heads/"):]
    return head[:7]  # odłączony HEAD — skrót commita
