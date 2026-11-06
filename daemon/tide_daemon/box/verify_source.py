"""Migawka śledzonych źródeł do weryfikacji budowy; bez .git i danych ignorowanych."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile

from ..gitcommand import git_argv
from .builder import BuildError


def git(worktree, *args):
    result = subprocess.run(git_argv("-C", str(worktree), *args), capture_output=True, text=True)
    if result.returncode:
        raise BuildError("źródła verify: " + result.stderr.strip())
    return result.stdout.strip()


@contextmanager
def snapshot(worktree, repo):
    worktree, repo = Path(worktree).resolve(), Path(repo).resolve()
    common = Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if common != repo / ".git":
        raise BuildError("źródła verify nie należą do projektu w Tablicy")
    # Sprawdzamy śledzone zmiany. Dane ignorowane i nieśledzone nie wchodzą do archiwum.
    if git(worktree, "status", "--porcelain", "--untracked-files=no"):
        raise BuildError("źródła verify mają niezacommitowane zmiany śledzonych plików")
    commit = git(worktree, "rev-parse", "HEAD")
    with tempfile.TemporaryDirectory(prefix="tide-verify-source-") as folder:
        archive = Path(folder) / "source.tar"
        git(worktree, "archive", "--format=tar", "--output", str(archive), commit)
        yield str(archive), commit
