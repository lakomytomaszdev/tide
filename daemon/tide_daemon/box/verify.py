"""PT-184: verify na kopii zatwierdzonego commita, bez montowania repozytorium hosta."""
from contextlib import contextmanager
from dataclasses import dataclass
import os
import subprocess
import tempfile

from ..gitcommand import git_argv, git_launch_env


@dataclass(frozen=True)
class Source:
    archive: str
    commit: str


def _git(worktree, *args, **kwargs):
    proc = subprocess.run(git_argv("-C", worktree, *args, boundary=True), stdin=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, timeout=120, env=git_launch_env(), **kwargs)
    if proc.returncode:
        raise ValueError("verify: git: " + proc.stderr.decode(errors="replace").strip())
    return proc.stdout.decode().strip() if proc.stdout is not None else ""


@contextmanager
def source_archive(slug, worktree):
    if not worktree:
        yield None
        return
    # Te same granice przynależności co box run; tylko odczyt, bez tworzenia worktree.
    from .command import project_repo
    from .runner import worktrees_root, _within
    repo = project_repo(slug)
    path = os.path.realpath(worktree)
    root = worktrees_root(repo)
    if path == root or not _within(path, root):
        raise ValueError(f"verify: wymagany odizolowany worktree w {root}")
    common = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir", stdout=subprocess.PIPE)
    top = _git(path, "rev-parse", "--show-toplevel", stdout=subprocess.PIPE)
    if os.path.realpath(common) != os.path.join(repo, ".git") or os.path.realpath(top) != path:
        raise ValueError("verify: worktree nie należy do projektu")
    if _git(path, "status", "--porcelain", "--untracked-files=normal", stdout=subprocess.PIPE):
        raise ValueError("verify: worktree ma niezapisane zmiany; wymagany czysty commit")
    commit = _git(path, "rev-parse", "HEAD", stdout=subprocess.PIPE)
    with tempfile.TemporaryDirectory(prefix="tide-verify-") as tmp:
        archive = os.path.join(tmp, "source.tar")
        with open(archive, "wb") as output:
            _git(path, "archive", "--format=tar", commit, stdout=output)
        yield Source(archive, commit)


@contextmanager
def in_box(incus, box, source, env, *, agent_uid, agent_gid):
    if source is None:
        yield {}
        return
    root = "/opt/tide/verify"
    work = root + "/work"

    def require(command, **kwargs):
        if incus.exec(box, command, env, **kwargs):
            raise ValueError("verify: nie udało się przygotować lub usunąć kopii źródeł")

    # Katalog nadrzędny należy do roota, kod projektu biegnie jako agent. Bez .git, sekretów,
    # plików ignorowanych i niezapisanych zmian. Archiwum rozpakowujemy tylko w pudełku.
    require(["rm", "-rf", "--", root])
    try:
        incus.file_push(source.archive, box, root + "/source.tar")
        require(["chmod", "0755", root])
        require(["chmod", "0644", root + "/source.tar"])
        require(["install", "-d", "-o", str(agent_uid), "-g", str(agent_gid), work])
        require(["tar", "-xf", root + "/source.tar", "--no-same-owner", "--no-same-permissions", "-C", work],
                user=agent_uid, group=agent_gid)
        yield {"user": agent_uid, "group": agent_gid, "cwd": work}
    finally:
        require(["rm", "-rf", "--", root])
