"""Git repozytorium tablicy: commity, zaległe commity, osierocona blokada indeksu, trwający revert.

Zasady:
- plik na dysku jest źródłem prawdy, commit to historia: gdy commit się nie uda (zajęty indeks,
  zawieszony git), zmiana zostaje w pliku, a commit czeka w kolejce (`pending`) i idzie przy
  następnym zapisie albo `sync()` — z tym samym komunikatem,
- git dostaje GIT_TIMEOUT sekund; zabity po czasie zostawia `.git/index.lock`, który usuwamy od
  razu, gdy w repozytorium nie działa żaden inny git, a cudzy — dopiero gdy jest starszy niż
  LOCK_STALE_S i nie ma żywego procesu git w katalogu tablicy,
- zaległe commity są tylko w pamięci: po restarcie usługi ich zmiany trafiają do „sync external
  changes”; wpis, który pada z trwałego powodu (np. ścieżki już nie ma), wypada z kolejki i też
  zostaje dla sync — kolejkę blokuje tylko zajęty indeks albo zawieszony git,
- numery kart: znacznik `.git/tide-next-card` przeżywa revert; historię gita przeglądamy tylko
  wtedy, gdy znacznika brak (np. kopia repozytorium), i od razu go zapisujemy,
- w trakcie revert, merge, cherry-pick albo rebase (pliki z konfliktami) niczego nie commitujemy:
  commit „sync” utrwaliłby znaczniki konfliktu,
- bez podpisu GPG i bez hooków (`core.hooksPath=/dev/null`): to zapis stanu tablicy, nie kod.
"""
import os
import re
import time

from ..worktrees.git import Git

GIT_TIMEOUT = 5
HISTORY_TIMEOUT = 120  # jednorazowe odbudowanie znacznika numerów z historii może trwać dłużej
LOCK_STALE_S = 10
HIGH_WATER_FILE = "tide-next-card"   # w .git — poza historią, więc `git revert` go nie cofa
IN_PROGRESS = (("MERGE_HEAD", "merge"), ("REVERT_HEAD", "revert"), ("CHERRY_PICK_HEAD", "cherry-pick"),
               ("rebase-merge", "rebase"), ("rebase-apply", "rebase"))
UNMERGED = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
COMMIT_OPTIONS = ("-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null")
FALLBACK_IDENTITY = ("-c", "user.name=Tide", "-c", "user.email=tide@localhost")
_CARD_PATH = re.compile(r"^cards/K-([1-9][0-9]{0,8})\.md$")


class BoardRepo:
    def __init__(self, root: str, git: Git | None = None, proc_root: str = "/proc"):
        self.root = root
        self.git = git or Git(timeout=GIT_TIMEOUT)
        self.proc_root = proc_root
        self.pending: list[tuple[str, list[str]]] = []   # (komunikat, ścieżki) commitów, które się nie udały
        self.last_error = ""

    @property
    def git_dir(self) -> str:
        return os.path.join(self.root, ".git")

    def initialized(self) -> bool:
        return os.path.isdir(self.git_dir)

    # --- stan ----------------------------------------------------------------------------------

    def busy(self) -> str:
        """Nazwa trwającej operacji gita z konfliktami (revert, merge, …) albo pusty napis."""
        if not self.initialized():
            return ""
        for name, operation in IN_PROGRESS:
            if os.path.exists(os.path.join(self.git_dir, name)):
                return operation
        status = self.git.run(self.root, "status", "--porcelain")
        if status.ok and any(line[:2] in UNMERGED for line in status.out.splitlines()):
            return "merge"
        return ""

    def busy_message(self, operation: str) -> str:
        return (f"w repozytorium tablicy trwa {operation} z konfliktami — dokończ go (git {operation} --continue) "
                f"albo przerwij (git {operation} --abort); do tego czasu tablica niczego nie zapisuje")

    def history_max(self) -> int | None:
        """Najwyższy numer karty, jaki kiedykolwiek powstał w historii (także cofniętej revertem);
        None, gdy git zawiódł. Liniowe względem liczby commitów — tylko do odbudowy znacznika."""
        res = self.git.run(self.root, "log", "--all", "--no-renames", "--diff-filter=A", "--name-only",
                           "--format=", "--", "cards", timeout=HISTORY_TIMEOUT)
        if not res.ok:
            return None
        nums = [int(m.group(1)) for m in map(_CARD_PATH.match, res.out.split()) if m]
        return max(nums, default=0)

    def high_water(self, rebuild: bool = False) -> int:
        """Następny wolny numer według znacznika. Bez znacznika i z rebuild=True — z historii gita
        (raz: wynik od razu trafia do znacznika); bez rebuild — 0 (migawka nigdy nie czyta historii)."""
        path = os.path.join(self.git_dir, HIGH_WATER_FILE)
        try:
            with open(path, encoding="utf-8") as fh:
                return int(fh.read().strip() or 0)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            return 0
        if not rebuild or not self.initialized():
            return 0
        top = self.history_max()
        if top is None:
            return 0
        self.set_high_water(top + 1)
        return top + 1

    def set_high_water(self, next_num: int) -> None:
        if not self.initialized() or next_num <= self.high_water():
            return
        path = os.path.join(self.git_dir, HIGH_WATER_FILE)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(f"{next_num}\n")
        os.replace(tmp, path)

    def last_commit(self) -> tuple[str, int, str] | None:
        res = self.git.run(self.root, "log", "-1", "--format=%H%x00%ct%x00%s")
        if not res.ok or not res.out.strip():
            return None
        sha, ts, subject = (res.out.strip().split("\0") + ["", ""])[:3]
        return sha, int(ts) if ts.isdigit() else 0, subject

    # --- zapis ---------------------------------------------------------------------------------

    def init(self) -> str:
        res = self.git.run(self.root, "init", "-q", "-b", "master")
        return "" if res.ok else (res.err or res.out or "git init się nie udał")

    def commit(self, message: str, paths: list[str]) -> str:
        """Commit tylko podanych ścieżek; najpierw zaległe. Pusty napis albo opis błędu (wtedy
        commit czeka w `pending`)."""
        self.pending.append((message, list(paths)))
        return self.flush()

    def flush(self) -> str:
        """Próbuje zaległych commitów po kolei. Przejściowy błąd (zajęty indeks, zawieszony git)
        zatrzymuje kolejkę; trwały — usuwa wpis (jego zmiany zbierze `sync()`)."""
        while self.pending:
            message, paths = self.pending[0]
            error, transient = self._commit_now(message, paths)
            if error and transient:
                self.last_error = error
                return error
            if error:
                print(f"tide-daemon: tablica: commit „{message}” pominięty ({error}) — zmiany zbierze sync",
                      flush=True)
            self.pending.pop(0)
        self.last_error = ""
        return ""

    def sync(self, message: str) -> bool:
        """Zaległe commity, a potem wszystko inne (zmiany z zewnątrz) jednym commitem."""
        if not self.initialized() or self.busy():
            return False
        before = len(self.pending)
        if self.flush():
            return False
        committed = before > 0
        status = self.git.run(self.root, "status", "--porcelain", "-uall")
        if not status.ok or not status.out.strip():
            return committed
        error, _ = self._commit_now(message, [])
        if error:
            self.last_error = error
            return committed
        return True

    # --- wnętrze -------------------------------------------------------------------------------

    def _commit_now(self, message: str, paths: list[str]) -> tuple[str, bool]:
        """(opis błędu albo pusty napis, czy błąd przejściowy)."""
        self.clear_stale_lock()
        if paths:
            # ścieżka, której już nie ma i której git nie zna, wywróciłaby `git add` („pathspec”)
            tracked = self.git.run(self.root, "ls-files", "-z", "--", *paths)
            known = set(tracked.out.split("\0")) if tracked.ok else set()
            paths = [p for p in paths if os.path.lexists(os.path.join(self.root, p)) or p in known]
            if not paths:
                return "", False
        add = self._run("add", "-A", "--", *paths) if paths else self._run("add", "-A")
        if not add.ok:
            return _describe(add), _transient(add)
        status = self.git.run(self.root, "status", "--porcelain", "--", *paths) if paths else \
            self.git.run(self.root, "status", "--porcelain")
        if status.ok and not status.out.strip():
            return "", False  # nic do zapisania (np. ktoś zrobił commit ręcznie)
        args = [*COMMIT_OPTIONS, "commit", "--no-verify", "-q", "-m", message]
        if paths:
            args += ["--", *paths]
        res = self._run(*args)
        if not res.ok and _no_identity(res.err):
            res = self._run(*COMMIT_OPTIONS, *FALLBACK_IDENTITY, *args[len(COMMIT_OPTIONS):])
        if not res.ok:
            if "nothing to commit" in res.err or "nothing added" in res.err:
                return "", False
            return _describe(res), _transient(res)
        return "", False

    def _run(self, *args):
        res = self.git.run(self.root, *args)
        if res.code == -1 and "timed out" in res.err:
            # nasz git zabity po czasie zostawia blokadę indeksu — inaczej każdy kolejny zapis by padał
            self.clear_stale_lock(max_age=0)
        return res

    def clear_stale_lock(self, max_age: float = LOCK_STALE_S) -> bool:
        """Usuwa `.git/index.lock`, gdy jest starszy niż max_age i nie działa żaden git w tablicy."""
        lock = os.path.join(self.git_dir, "index.lock")
        try:
            age = time.time() - os.stat(lock).st_mtime
        except OSError:
            return False
        if age < max_age or self._live_git():
            return False
        try:
            os.remove(lock)
        except OSError:
            return False
        print(f"tide-daemon: tablica: usunięto osieroconą blokadę {lock}", flush=True)
        return True

    def _live_git(self) -> bool:
        """Czy działa jakiś proces git z katalogiem roboczym w tablicy (git -C też zmienia katalog)."""
        real_root = os.path.realpath(self.root)
        try:
            pids = [p for p in os.listdir(self.proc_root) if p.isdigit()]
        except OSError:
            return True  # bez /proc nie wiemy — lepiej nie usuwać
        for pid in pids:
            try:
                with open(os.path.join(self.proc_root, pid, "comm"), encoding="utf-8") as fh:
                    if not fh.read().strip().startswith("git"):
                        continue
                cwd = os.path.realpath(os.readlink(os.path.join(self.proc_root, pid, "cwd")))
            except OSError:
                continue
            if cwd == real_root or cwd.startswith(real_root + os.sep):
                return True
        return False


def _transient(res) -> bool:
    """Błąd, który minie sam: zajęty indeks albo git zabity po czasie."""
    return res.code == -1 or "index.lock" in res.err or "timed out" in res.err


def _describe(res) -> str:
    text = (res.err or res.out or f"kod {res.code}").strip().splitlines()
    return f"commit się nie udał: {text[0] if text else ''}".strip()


def _no_identity(err: str) -> bool:
    return any(text in err for text in ("Please tell me who you are", "unable to auto-detect email",
                                        "empty ident name", "Author identity unknown"))
