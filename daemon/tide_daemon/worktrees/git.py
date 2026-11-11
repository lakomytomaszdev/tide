"""Wywołania gita dla obszaru worktrees — wyłącznie lokalne, bez żadnej operacji sieciowej
(wyjątek: `fetch.py` używa kopii tej klasy do `git fetch`, tylko poza granicą Gita).

Zasady:
- GIT_OPTIONAL_LOCKS=0: `git status` nie odświeża indeksu i nie bierze index.lock, więc nasze
  odświeżanie w tle nie przeszkadza gitowi uruchomionemu przez użytkownika albo agenta,
- GIT_NO_LAZY_FETCH=1: w częściowych klonach git nie dociąga brakujących obiektów z serwera,
- GIT_TERMINAL_PROMPT=0: git nigdy nie czeka na hasło,
- LC_ALL=C: komunikaty i format wyjścia niezależne od języka systemu.
"""
import json
import os
import subprocess
from dataclasses import dataclass
from ..gitcommand import boundary_active, git_command

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
    timed_out: bool = False  # polecenie przerwane po limicie czasu (code == -1)

    @property
    def ok(self) -> bool:
        return self.code == 0


class Git:
    """Systemowy Git w granicy procesu; testy mogą podmieniać run/cancel."""

    def __init__(self, executable: str = "git", timeout: float = 60, cancel=None):
        self.executable = executable
        self.timeout = timeout
        self.env = dict(os.environ, **GIT_ENV)
        self.cancel = cancel
        self.read_roots = ()
        self.write_roots = ()
        self.private = False
        self.index = None

    def run(self, cwd: str, *args: str, timeout: float | None = None, input_text: str | None = None,
            checkout=None, checkout_ignored=False) -> Result:
        try:
            argv, env = git_command("-C", cwd, *args, executable=self.executable, env=self.env,
                                    identity=any(a in ("commit", "commit-tree", "var") for a in args),
                                    read_roots=self.read_roots, write_roots=self.write_roots, private=self.private,
                                    index=self.index, checkout=checkout, checkout_ignored=checkout_ignored)
            if self.cancel is not None:
                proc = self.cancel.run(argv, env=env, timeout=timeout or self.timeout, input_text=input_text)
            else:
                proc = subprocess.run(argv, capture_output=True, text=True,
                                      errors="replace", env=env, timeout=timeout or self.timeout,
                                      **({"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Result(-1, "", str(exc), timed_out=isinstance(exc, subprocess.TimeoutExpired))
        return Result(proc.returncode, proc.stdout, proc.stderr.strip())

    def read_checkout(self, cwd, *options, untracked_files="all", include_ignored=False):
        """Świeże odczyty migawki, bez cache stanu: w granicy jednym jej uruchomieniem, bez granicy
        tymi samymi poleceniami Git po kolei (jak przed PT-264)."""
        status = ("status", "--porcelain=v1", "-z", "--untracked-files=" + untracked_files,
                  "--ignore-submodules=none")
        if not self.private and not boundary_active(("-C", cwd, *options)):
            return self._read_checkout_direct(cwd, options, status, include_ignored)
        result = self.run(cwd, *options, *status, checkout=untracked_files, checkout_ignored=include_ignored)
        if not result.ok:
            raise OSError("nie można odczytać checkoutu: " + result.err)
        expected = {"head", "symbolic", "status", "index", "gitdir", "index_entries"}
        if include_ignored:
            expected.update(("ignored", "repository"))
        try:
            data = json.loads(result.out)
            if (not isinstance(data, dict) or set(data) != expected or
                    any(not isinstance(row, dict) or set(row) != {"code", "out", "err"} or
                        type(row["code"]) is not int or not isinstance(row["out"], str) or
                        not isinstance(row["err"], str) or row["code"] not in
                        ((0, 1) if name == "symbolic" else (0,)) for name, row in data.items())):
                raise ValueError("niepełny wynik")
            return {name: Result(**row) for name, row in data.items()}
        except (ValueError, TypeError) as exc:
            raise OSError("niepoprawny wynik migawki checkoutu") from exc

    def _read_checkout_direct(self, cwd, options, status, include_ignored):
        commands = [("head", ("rev-parse", "--verify", "HEAD")), ("symbolic", ("symbolic-ref", "--quiet", "HEAD")),
                    ("status", status), ("index", ("rev-parse", "--path-format=absolute", "--git-path", "index")),
                    ("gitdir", ("rev-parse", "--absolute-git-dir")),
                    ("index_entries", ("ls-files", "--stage", "-v", "-z"))]
        if include_ignored:
            commands += [("ignored", ("ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory")),
                         ("repository", ("rev-parse", "--path-format=absolute", "--git-common-dir"))]
        results = {}
        for name, tail in commands:
            result = self.run(cwd, *options, *tail)
            if result.code not in ((0, 1) if name == "symbolic" else (0,)):
                raise OSError("nie można odczytać checkoutu: " + result.err)
            results[name] = result
        return results


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


# Wpisy reflogu, które znaczą „tu powstał własny commit”: commit (także initial, amend, merge),
# cherry-pick, revert i przepisanie commitów przy rebase. Nie liczą się: założenie gałęzi
# („branch: Created from”), przewinięcie („merge master: Fast-forward”, „pull”), reset, checkout.
OWN_COMMIT_PREFIXES = ("commit", "cherry-pick", "revert", "rebase (pick)", "rebase -i (pick)",
                       "rebase (reword)", "rebase -i (reword)", "rebase (edit)", "rebase -i (edit)")


def common_dir(repo_path: str) -> str:
    """Wspólny katalog gita repozytorium (refs, logs, worktrees): <repo>/.git albo samo repozytorium bare."""
    dotgit = os.path.join(repo_path, ".git")
    return dotgit if os.path.isdir(dotgit) else repo_path


def worktree_gitdir(path: str) -> str:
    """Prywatny katalog gita dołączonego worktree (z pliku <worktree>/.git: „gitdir: …”), pusty, gdy brak."""
    try:
        with open(os.path.join(path, ".git"), encoding="utf-8", errors="replace") as fh:
            first = fh.readline().strip()
    except OSError:
        return ""
    if not first.startswith("gitdir:"):
        return ""
    gitdir = first[len("gitdir:"):].strip()
    return os.path.normpath(gitdir if os.path.isabs(gitdir) else os.path.join(path, gitdir))


# Wpisy, od których liczymy na nowo: gałąź założona albo przestawiona (`branch -f`, `checkout -B`),
# cofnięta resetem (`reset --hard master` — dawne commity już nie są w gałęzi) i — w reflogu HEAD
# odłączonego worktree — przejście na inny commit (`checkout`) albo reset (`reset --hard HEAD~1`).
# Reset po commicie wraca do okna ACTIVE_WINDOW: ostrożnie, bo z tego widoku się usuwa.
BRANCH_RESTARTS = ("branch: Created from", "branch: Reset to", "reset: moving to")
HEAD_RESTARTS = ("checkout:", "reset: moving to")


def reflog_has_own_commit(log_path: str, restarts: tuple = BRANCH_RESTARTS) -> bool | None:
    """Czy reflog (plik w logs/) ma wpis własnego commita (OWN_COMMIT_PREFIXES) po ostatnim
    ponownym starcie (restarts). None, gdy pliku nie ma albo nie da się go przeczytać (reflog
    wyłączony, reftable, wygasły) — wtedy nic nie wiemy. Plik czytamy wprost, bez gita."""
    own = False
    try:
        with open(log_path, "rb") as fh:
            for raw in fh:
                _, tab, message = raw.partition(b"\t")
                if not tab:
                    continue
                text = message.decode("utf-8", "replace")
                if text.startswith(restarts):
                    own = False
                elif text.startswith(OWN_COMMIT_PREFIXES):
                    own = True
    except OSError:
        return None
    return own


def pick_main_branch(existing: set[str], current: str) -> str:
    """Lokalna gałąź główna: ta z MAIN_CANDIDATES, na której stoi główny katalog, inaczej
    pierwsza istniejąca z listy. Pusty napis, gdy żadnej nie ma."""
    if current in existing and current in MAIN_CANDIDATES:
        return current
    return next((name for name in MAIN_CANDIDATES if name in existing), "")
