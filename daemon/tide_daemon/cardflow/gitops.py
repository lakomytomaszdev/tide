"""Git przepływu karty w repozytorium projektu: gałąź i worktree karty, gałąź i worktree ticketu, scalenie ticketu.

- wszystkie operacje na jednym repozytorium idą po kolei pod jedną blokadą (`lock_for`),
- każda akcja jest idempotentna (powtórka po restarcie usługi nie robi drugiej gałęzi ani drugiego scalenia):
  gałąź, która już jest w tym samym worktree, to sukces; ticket, który już jest w gałęzi karty, to sukces,
- worktree przepływu leżą w `<repo>-worktrees/` obok repozytorium (konwencja z okna „Nowe zadanie”), nie w ukrytym
  katalogu (`.claude/worktrees` byłby dla piaskownicy Claude Code chronionym katalogiem, a Codex ze snapa nie widzi
  ukrytych katalogów domowych), katalog kroku to `<worktree>.step` obok worktree,
- scalenie ticketu do gałęzi karty nie wykonuje treści napisanej przez agenta: `merge-tree --write-tree`,
  `commit-tree` i `update-ref` z porównaniem starej wartości działają na samych obiektach (bez filtrów, haków
  i katalogu roboczego). Worktree karty dostaje potem nowe drzewo przez `read-tree -m -u` (dwudrzewowe przejście,
  odmawia przy lokalnych zmianach); filtry przy zapisie plików mogą pochodzić tylko z konfiguracji użytkownika
  (`.git/config` repozytorium jest dla kroku tylko do odczytu), haki i fsmonitor są wyłączone.
"""
from __future__ import annotations

import os
import re
import threading

from ..worktrees.git import MAIN_CANDIDATES, Git, pick_main_branch

HARDENED = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.attributesFile=/dev/null",
            "-c", "submodule.recurse=false", "-c", "commit.gpgsign=false", "-c", "maintenance.auto=false",
            "-c", "gc.auto=0")
FALLBACK_IDENTITY = ("-c", "user.name=Tide", "-c", "user.email=tide@localhost")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def lock_for(repo: str) -> threading.Lock:
    key = os.path.realpath(repo)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


class FlowGitError(Exception):
    """Błąd gita z komunikatem po polsku."""


def worktrees_dir(repo: str) -> str:
    repo = os.path.normpath(repo)
    return repo + "-worktrees"


def worktree_for(repo: str, branch: str) -> str:
    name = branch.partition("/")[2] or branch
    return os.path.join(worktrees_dir(repo), re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "krok")


class FlowGit:
    def __init__(self, git: Git | None = None):
        self.git = git or Git(timeout=120)

    def run(self, cwd: str, *args, timeout=None):
        return self.git.run(cwd, *HARDENED, *args, timeout=timeout)

    def main_branch(self, repo: str) -> str:
        refs = [f"refs/heads/{name}" for name in MAIN_CANDIDATES]
        listed = self.run(repo, "for-each-ref", "--format=%(refname:short)", *refs)
        current = self.run(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
        existing = {line.strip() for line in listed.out.splitlines() if line.strip()} if listed.ok else set()
        return pick_main_branch(existing, current.out.strip() if current.ok else "")

    def sha(self, repo: str, ref: str) -> str:
        res = self.run(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return res.out.strip() if res.ok else ""

    def branch_worktree(self, repo: str, branch: str, path: str, base: str) -> dict:
        """Gałąź `branch` od `base` w worktree `path`; już jest (ta gałąź w tym worktree) — bez zmian."""
        with lock_for(repo):
            listed = self.run(repo, "worktree", "list", "--porcelain")
            for block in listed.out.split("\n\n") if listed.ok else []:
                lines = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
                if os.path.realpath(lines.get("worktree", "")) == os.path.realpath(path):
                    if lines.get("branch") == f"refs/heads/{branch}":
                        return {"branch": branch, "worktree": path, "base": self.sha(repo, branch), "existing": True}
                    raise FlowGitError(f"w {path} jest już inny worktree")
            if os.path.exists(path):
                raise FlowGitError(f"katalog {path} już istnieje")
            check = self.run(repo, "check-ref-format", "--branch", branch)
            if not check.ok:
                raise FlowGitError(f"niepoprawna nazwa gałęzi {branch}")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if self.sha(repo, f"refs/heads/{branch}"):
                added = self.run(repo, "worktree", "add", path, branch, timeout=600)
            else:
                start = self.sha(repo, base)
                if not start:
                    raise FlowGitError(f"nie ma gałęzi {base}")
                added = self.run(repo, "worktree", "add", "-b", branch, path, start, timeout=600)
            if not added.ok:
                raise FlowGitError("git worktree add: " + (added.err.splitlines() or ["błąd"])[0])
            return {"branch": branch, "worktree": path, "base": self.sha(repo, branch), "existing": False}

    def merge(self, repo: str, card_branch: str, ticket_branch: str, card_worktree: str, message: str) -> dict:
        """Ticket do gałęzi karty: {commit, already}; konflikt albo błąd — FlowGitError."""
        with lock_for(repo):
            old = self.sha(repo, f"refs/heads/{card_branch}")
            tip = self.sha(repo, f"refs/heads/{ticket_branch}")
            if not old or not tip:
                raise FlowGitError(f"nie ma gałęzi {card_branch if not old else ticket_branch}")
            if self.run(repo, "merge-base", "--is-ancestor", tip, old).ok:
                return {"commit": old, "already": True}
            tree = self.run(repo, "merge-tree", "--write-tree", "--no-messages", old, tip)
            if tree.code == 1:
                raise FlowGitError("konflikt przy scalaniu")
            if not tree.ok or not tree.out.strip():
                raise FlowGitError("git merge-tree: " + (tree.err.splitlines() or ["błąd"])[0])
            args = ("commit-tree", tree.out.split()[0], "-p", old, "-p", tip, "-m", message)
            made = self.run(repo, *args)
            if not made.ok:
                made = self.run(repo, *FALLBACK_IDENTITY, *args)
            if not made.ok:
                raise FlowGitError("git commit-tree: " + (made.err.splitlines() or ["błąd"])[0])
            commit = made.out.strip()
            moved = self.run(repo, "update-ref", "-m", message, f"refs/heads/{card_branch}", commit, old)
            if not moved.ok:
                raise FlowGitError("gałąź karty zmieniła się w trakcie scalania")
            if card_worktree and os.path.isdir(card_worktree):
                synced = self.run(card_worktree, "read-tree", "-m", "-u", old, commit)
                if not synced.ok:
                    raise FlowGitError("worktree karty nie przyjął scalenia (lokalne zmiany?): "
                                       + (synced.err.splitlines() or ["błąd"])[0])
            return {"commit": commit, "already": False}
