"""Git przepływu karty w repozytorium projektu: gałąź i worktree karty, gałąź i worktree ticketu, scalenie ticketu.

- wszystkie operacje na jednym repozytorium idą po kolei pod jedną blokadą (`lock_for`),
- każda akcja jest idempotentna (powtórka po restarcie usługi nie robi drugiej gałęzi ani drugiego scalenia):
  gałąź, która już jest w tym samym worktree, to sukces; ticket, który już jest w gałęzi karty, to sukces,
- worktree przepływu leżą w `<repo>-worktrees/` obok repozytorium (konwencja z okna „Nowe zadanie”), nie w ukrytym
  katalogu (`.claude/worktrees` byłby dla piaskownicy Claude Code chronionym katalogiem, a Codex ze snapa nie widzi
  ukrytych katalogów domowych), katalog kroku to `<worktree>.step` obok worktree,
- scalanie na obiektach (`merge-tree`, `commit-tree`) odbywa się bez katalogu roboczego; każde polecenie gita
  dotykające worktree karty działa w piaskownicy StepGit. Najpierw materializujemy drzewo, potem przesuwamy ref;
  ponowienie sprawdza i naprawia rozjazd indeksu/worktree z gałęzią, zanim zwróci `already`.
"""
from __future__ import annotations

import os
import re
import threading

from ..worktrees.git import MAIN_CANDIDATES, Git, pick_main_branch
from ..tasks.agentcmd import StepError, resolve_git_dir
from ..tasks.stepstop import StepGit, sandbox_available

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

    def diff(self, repo: str, card_branch: str, ticket_branch: str) -> str:
        """Diff obiektów bez wywołań zewnętrznego diffa ani textconv."""
        result = self.run(repo, "diff", "--no-ext-diff", "--no-textconv", "--binary",
                          f"refs/heads/{card_branch}", f"refs/heads/{ticket_branch}", "--")
        if not result.ok:
            raise FlowGitError("nie można odczytać diffu ticketu: " + (result.err.splitlines() or ["błąd"])[0])
        if len(result.out) > 300_000:
            raise FlowGitError("diff ticketu przekracza 300 000 znaków — podziel pracę na mniejszy ticket")
        return result.out

    def clean_tip(self, repo: str, branch: str, path: str) -> str:
        """Przed i po review potwierdza, że recenzent nie zmienił gałęzi ani plików ticketu."""
        with lock_for(repo):
            tip = self.sha(repo, f"refs/heads/{branch}")
            if not tip or not sandbox_available():
                raise FlowGitError("nie można bezpiecznie sprawdzić worktree ticketu")
            try:
                git_dir = resolve_git_dir(repo, path)
            except StepError as exc:
                raise FlowGitError(f"nie można ustalić katalogu gita worktree ticketu: {exc}") from exc
            status = StepGit(repo, git_dir, path, path + ".step").run(
                "status", "--porcelain", "--untracked-files=all")
            if not status.ok or status.out.strip():
                raise FlowGitError("worktree ticketu ma lokalne zmiany")
            return tip

    def remove_worktree(self, repo: str, branch: str, path: str) -> dict:
        """Sprząta worktree po APPROVE pod tą samą blokadą co zakładanie i scalanie."""
        with lock_for(repo):
            listed = self.run(repo, "worktree", "list", "--porcelain")
            if not listed.ok:
                raise FlowGitError("nie można odczytać listy worktree")
            found = next((dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
                          for block in listed.out.split("\n\n")
                          if any(line == f"worktree {os.path.realpath(path)}" for line in block.splitlines())), None)
            if found is None:
                return {"removed": True, "already": True}
            if found.get("branch") != f"refs/heads/{branch}":
                raise FlowGitError("worktree należy do innej gałęzi — nie usuwam")
            if not sandbox_available():
                raise FlowGitError("piaskownica gita ticketu jest niedostępna")
            try:
                git_dir = resolve_git_dir(repo, path)
            except StepError as exc:
                raise FlowGitError(f"nie można ustalić katalogu gita worktree ticketu: {exc}") from exc
            status = StepGit(repo, git_dir, path, path + ".step").run(
                "status", "--porcelain", "--untracked-files=all")
            if not status.ok or status.out.strip():
                raise FlowGitError("worktree ma lokalne zmiany — nie usuwam")
            removed = self.run(repo, "worktree", "remove", path, timeout=600)
            if not removed.ok:
                raise FlowGitError("nie udało się usunąć worktree: " + (removed.err.splitlines() or ["błąd"])[0])
            return {"removed": True, "already": False}

    def merge(self, repo: str, card_branch: str, ticket_branch: str, card_worktree: str, message: str,
              *, expected_tip: str) -> dict:
        """Scala dokładnie zatwierdzony commit; sukces oznacza zgodność refu, indeksu i plików worktree."""
        with lock_for(repo):
            old = self.sha(repo, f"refs/heads/{card_branch}")
            tip = self.sha(repo, f"refs/heads/{ticket_branch}")
            if not old or not tip:
                raise FlowGitError(f"nie ma gałęzi {card_branch if not old else ticket_branch}")
            if not expected_tip or tip != expected_tip:
                raise FlowGitError("gałąź ticketu zmieniła się po zatwierdzeniu review")
            if not card_worktree or not os.path.isdir(card_worktree):
                raise FlowGitError("nie ma worktree karty")
            listed = self.run(repo, "worktree", "list", "--porcelain")
            if not listed.ok or not any(
                os.path.realpath(block.get("worktree", "")) == os.path.realpath(card_worktree)
                and block.get("branch") == f"refs/heads/{card_branch}"
                for block in (dict(line.split(" ", 1) for line in part.splitlines() if " " in line)
                              for part in listed.out.split("\n\n"))
            ):
                raise FlowGitError("worktree karty nie jest przypisany do jej gałęzi")
            if not sandbox_available():
                raise FlowGitError("piaskownica gita karty jest niedostępna")
            try:
                git_dir = resolve_git_dir(repo, card_worktree)
            except StepError as exc:
                raise FlowGitError(f"nie można ustalić katalogu gita worktree karty: {exc}") from exc
            work_git = StepGit(repo, git_dir, card_worktree, card_worktree + ".step")

            def tree_of(commit: str) -> str:
                result = self.run(repo, "rev-parse", "--verify", f"{commit}^{{tree}}")
                if not result.ok:
                    raise FlowGitError("nie można odczytać drzewa commita karty")
                return result.out.strip()

            def index_tree() -> str:
                result = work_git.run("write-tree")
                if not result.ok:
                    raise FlowGitError("nie można odczytać indeksu worktree karty: " + result.err)
                return result.out.strip()

            def clean_files() -> None:
                work_git.run("update-index", "--refresh")
                result = work_git.run("diff-files", "--quiet", "--")
                if not result.ok:
                    raise FlowGitError("worktree karty ma lokalne zmiany albo błąd gita: " + result.err)

            def sync(source: str, target: str) -> None:
                result = work_git.run("read-tree", "-m", "-u", source, target)
                if not result.ok:
                    raise FlowGitError("worktree karty nie przyjął scalenia (lokalne zmiany?): "
                                       + (result.err.splitlines() or ["błąd"])[0])
                if index_tree() != tree_of(target):
                    raise FlowGitError("indeks worktree karty nie odpowiada scaleniu")
                clean_files()

            current_tree = tree_of(old)
            staged_tree = index_tree()
            clean_files()
            if self.run(repo, "merge-base", "--is-ancestor", tip, old).ok:
                if staged_tree != current_tree:
                    parent = self.run(repo, "rev-parse", "--verify", "--quiet", f"{old}^1")
                    if not parent.ok or staged_tree != tree_of(parent.out.strip()):
                        raise FlowGitError("indeks worktree karty nie odpowiada gałęzi ani poprzedniemu scaleniu")
                    sync(parent.out.strip(), old)
                return {"commit": old, "already": True}
            tree = work_git.run("merge-tree", "--write-tree", "--no-messages", old, tip)
            if tree.code == 1:
                raise FlowGitError("konflikt przy scalaniu")
            if not tree.ok or not tree.out.strip():
                raise FlowGitError("git merge-tree: " + (tree.err.splitlines() or ["błąd"])[0])
            target_tree = tree.out.split()[0]
            if staged_tree not in (current_tree, target_tree):
                raise FlowGitError("indeks worktree karty nie odpowiada gałęzi ani przygotowanemu scaleniu")
            args = ("commit-tree", target_tree, "-p", old, "-p", tip, "-m", message)
            made = self.run(repo, *args)
            if not made.ok:
                made = self.run(repo, *FALLBACK_IDENTITY, *args)
            if not made.ok:
                raise FlowGitError("git commit-tree: " + (made.err.splitlines() or ["błąd"])[0])
            commit = made.out.strip()
            if staged_tree == current_tree:
                sync(old, commit)
            moved = self.run(repo, "update-ref", "-m", message, f"refs/heads/{card_branch}", commit, old)
            if not moved.ok:
                latest = self.sha(repo, f"refs/heads/{card_branch}")
                if latest and index_tree() == target_tree:
                    # Ref pozostał stary (np. błąd blokady): przed zapisaniem wyniku
                    # ok:false przywróć stare pliki i indeks. Gdy wygrał obcy git,
                    # wyrównaj worktree do jego refu. Obie operacje są w piaskownicy.
                    clean_files()
                    sync(commit, latest)
                if latest == old:
                    raise FlowGitError("nie udało się przesunąć gałęzi karty")
                raise FlowGitError("gałąź karty zmieniła się w trakcie scalania")
            return {"commit": commit, "already": False}
