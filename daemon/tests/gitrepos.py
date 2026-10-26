"""Tymczasowe repozytoria git z worktrees we wszystkich stanach — wspólne dla testów rdzenia i D-Bus.

Układ (root = katalog projektów):
  root/alpha                 główny katalog, gałąź master
  root/alpha-wt/merged       feat/merged: commit scalony z master, czysty     → do sprzątnięcia
  root/alpha-wt/dirty        feat/dirty: scalony, ale ze zmianą w pliku       → pominięty (dirty)
  root/alpha-wt/unmerged     feat/unmerged: 2 commity poza master             → pominięty (unmerged)
  root/alpha-wt/agent        feat/agent: scalony, czysty, pracuje w nim agent → pominięty (agent)
  root/alpha-wt/detached     odłączony HEAD na commicie z master              → do sprzątnięcia
  root/group/beta            główny katalog na gałęzi trunk (brak master/main/develop)
  root/group/beta-wt/topic   topic: bez gałęzi głównej                        → pominięty (no-main)
  root/web/node_modules/dep  repozytorium w zależnościach — nie jest szukane
  root/.hidden/gamma         repozytorium w katalogu ukrytym — nie jest szukane
"""
import os
import subprocess

COMMIT_TS = 1_790_000_000  # stały czas commitów (2026-09)
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
           GIT_AUTHOR_DATE=f"@{COMMIT_TS} +0000", GIT_COMMITTER_DATE=f"@{COMMIT_TS} +0000")


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout


def commit(cwd, name, text="x\n", message=None):
    with open(os.path.join(cwd, name), "w", encoding="utf-8") as fh:
        fh.write(text)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", message or f"add {name}")


def build(root: str) -> dict:
    """Tworzy repozytoria i zwraca słownik nazwa → ścieżka."""
    p = {"root": root, "alpha": os.path.join(root, "alpha")}
    wt = os.path.join(root, "alpha-wt")
    os.makedirs(p["alpha"])
    git(p["alpha"], "init", "-q", "-b", "master")
    commit(p["alpha"], "README.md", "alpha\n")

    for name in ("merged", "dirty", "unmerged", "agent"):
        p[name] = os.path.join(wt, name)
        git(p["alpha"], "worktree", "add", "-q", "-b", f"feat/{name}", p[name])

    commit(p["merged"], "merged.txt")
    git(p["alpha"], "merge", "-q", "--no-ff", "-m", "merge feat/merged", "feat/merged")
    commit(p["unmerged"], "u1.txt")
    commit(p["unmerged"], "u2.txt")
    with open(os.path.join(p["dirty"], "README.md"), "a", encoding="utf-8") as fh:
        fh.write("zmiana\n")
    os.makedirs(os.path.join(p["agent"], "src"))  # agent pracuje w podkatalogu

    p["detached"] = os.path.join(wt, "detached")
    git(p["alpha"], "worktree", "add", "-q", "--detach", p["detached"], "master")

    p["beta"] = os.path.join(root, "group", "beta")
    os.makedirs(p["beta"])
    git(p["beta"], "init", "-q", "-b", "trunk")
    commit(p["beta"], "b.txt")
    p["topic"] = os.path.join(root, "group", "beta-wt", "topic")
    git(p["beta"], "worktree", "add", "-q", "-b", "topic", p["topic"])

    for hidden in (os.path.join(root, "web", "node_modules", "dep"), os.path.join(root, ".hidden", "gamma")):
        os.makedirs(hidden)
        git(hidden, "init", "-q", "-b", "master")
        commit(hidden, "h.txt")
    return p


def branches(repo: str) -> list[str]:
    return git(repo, "branch", "--format=%(refname:short)").split()
