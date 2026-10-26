"""Wywołania gita dla obszaru worktrees — wyłącznie lokalne, bez żadnej operacji sieciowej.

Zasady:
- GIT_OPTIONAL_LOCKS=0: `git status` nie odświeża indeksu i nie bierze index.lock, więc nasze
  odświeżanie w tle nie przeszkadza gitowi uruchomionemu przez użytkownika albo agenta,
- GIT_NO_LAZY_FETCH=1: w częściowych klonach git nie dociąga brakujących obiektów z serwera,
- GIT_TERMINAL_PROMPT=0: git nigdy nie czeka na hasło,
- LC_ALL=C: komunikaty i format wyjścia niezależne od języka systemu.
"""
import os
import subprocess
from dataclasses import dataclass

GIT_ENV = {
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
}
# Kandydaci na lokalną gałąź główną, w kolejności pierwszeństwa (gdy główny katalog
# nie stoi na żadnej z nich).
MAIN_CANDIDATES = ("master", "main", "develop")


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


class Git:
    """Uruchamia gita w podanym katalogu. Podmienialny w testach (np. inny plik wykonywalny)."""

    def __init__(self, executable: str = "git", timeout: float = 60):
        self.executable = executable
        self.timeout = timeout
        self.env = dict(os.environ, **GIT_ENV)

    def run(self, cwd: str, *args: str, timeout: float | None = None) -> Result:
        try:
            proc = subprocess.run([self.executable, "-C", cwd, *args], capture_output=True, text=True,
                                  errors="replace", env=self.env, timeout=timeout or self.timeout,
                                  stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Result(-1, "", str(exc))
        return Result(proc.returncode, proc.stdout, proc.stderr.strip())


@dataclass
class ListedWorktree:
    """Wpis z `git worktree list --porcelain`."""
    path: str
    head: str = ""
    branch: str = ""          # bez refs/heads/, pusty dla odłączonego HEAD
    detached: bool = False
    bare: bool = False
    locked: bool = False
    prunable: bool = False


def parse_worktree_list(text: str) -> list[ListedWorktree]:
    out: list[ListedWorktree] = []
    current: ListedWorktree | None = None
    for line in text.splitlines():
        if not line:
            current = None
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current = ListedWorktree(os.path.normpath(value))
            out.append(current)
        elif current is None:
            continue
        elif key == "HEAD":
            current.head = value
        elif key == "branch":
            current.branch = value[len("refs/heads/"):] if value.startswith("refs/heads/") else value
        elif key == "detached":
            current.detached = True
        elif key == "bare":
            current.bare = True
        elif key == "locked":
            current.locked = True
        elif key == "prunable":
            current.prunable = True
    return out


def parse_status(text: str) -> list[str]:
    """Ścieżki z `git status --porcelain=v1 -z` (jedna pozycja na zmieniony plik albo katalog)."""
    paths = []
    items = text.split("\0")
    i = 0
    while i < len(items):
        item = items[i]
        i += 1
        if len(item) < 4:
            continue
        code, path = item[:2], item[3:]
        paths.append(path)
        if "R" in code or "C" in code:
            i += 1  # po zmianie nazwy -z dokleja jeszcze starą ścieżkę
    return paths


def pick_main_branch(existing: set[str], current: str) -> str:
    """Lokalna gałąź główna: ta z MAIN_CANDIDATES, na której stoi główny katalog, inaczej
    pierwsza istniejąca z listy. Pusty napis, gdy żadnej nie ma."""
    if current in existing and current in MAIN_CANDIDATES:
        return current
    return next((name for name in MAIN_CANDIDATES if name in existing), "")
