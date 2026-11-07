"""Wszystkie plany repozytorium — do kontroli numerów ticketów (numbering.py), tylko odczyt.

Punkt 3 standardu (docs/agent-plan-standard.md): numer zajmuje plan w `docs/agents/plans/` głównego
katalogu, KAŻDEGO worktree (także niezacommitowany plik) i każdej gałęzi lokalnej. Plan w innym
worktree jest niewidoczny z głównego katalogu, a i tak zajmuje swoje numery.

- worktree: `git worktree list --porcelain` i pliki z dysku (jak plan.find_plans),
- gałęzie: `git for-each-ref refs/heads`, `git ls-tree` dla commitów, których jeszcze nie znamy,
  i `git cat-file --batch` dla nowych blobów — commit i blob się nie zmieniają, więc pamiętamy je
  na stałe; kolejne odczyty to dwa szybkie wywołania gita na repozytorium,
- deklaracja z CLAUDE.md/AGENTS.md (sekcja „Plan pracy agentów (Tide)”): prefiks, tracker, projekt,
- plany projektu z `ai-docs: tide` w repozytorium tablicy (`<tablica>/docs/<projekt>/plans/`,
  boardplans.py, standard 1.1) — z dysku, jak worktree; zajmują numery tak samo.

Wynik dla repozytorium trzymamy `ttl` sekund (usługa: 60 s, narzędzie wiersza poleceń: 0). Gdy git
nie działa (katalog bez repozytorium), zostają same katalogi podane w `roots`. Ten sam plan w kilku
miejscach z tą samą treścią liczy się raz; różne wersje tego samego pliku numbering.py łączy w jeden plan.
"""
import hashlib
import os
import subprocess
import time
from ..gitcommand import git_argv, git_launch_env

from . import boardplans, numbering, plan as plan_mod

GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1", "LC_ALL": "C"}
TIMEOUT = 10
MAX_BRANCHES = 400
DECLARATION_FILES = ("CLAUDE.md", "AGENTS.md")


def run_git(root: str, args: list, stdin: bytes | None = None) -> bytes | None:
    """Wyjście gita albo None przy błędzie (bez repozytorium, brak gita, przekroczony czas)."""
    try:
        result = subprocess.run(git_argv("-C", root, *args), input=stdin, capture_output=True, timeout=TIMEOUT,
                                env=git_launch_env())
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def main_root(root: str, git=run_git) -> str:
    """Główny katalog repozytorium (pierwszy z `git worktree list`), a bez gita — sam katalog."""
    out = git(root, ["worktree", "list", "--porcelain"]) if root else None
    for line in (out or b"").decode("utf-8", "replace").splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):].strip()
    return os.path.realpath(root) if root else ""


class RepoPlans:
    def __init__(self, git=run_git, clock=time.time, ttl: float = 60.0):
        self.git = git
        self.clock = clock
        self.ttl = ttl
        self._trees: dict[str, list] = {}       # commit → [(ścieżka, blob)] planów
        self._blobs: dict[str, str | None] = {}  # blob → treść planu albo None (to nie plan Tide)
        self._files: dict[tuple, object] = {}   # (ścieżka, rozmiar, mtime) → Plan albo None
        self._repos: dict[str, tuple] = {}      # klucz repozytorium → (czas, plany, deklaracja)

    def read(self, roots) -> tuple[list, dict]:
        """(plany ze wszystkich worktree i gałęzi, deklaracja z CLAUDE.md/AGENTS.md) dla repozytorium,
        do którego należą `roots` (worktree koordynatora i główny katalog)."""
        roots = [r for r in roots or () if r]
        if not roots:
            return [], {}
        key = roots[-1]
        now = self.clock()
        cached = self._repos.get(key)
        if cached and self.ttl > 0 and now - cached[0] < self.ttl:
            return cached[1], cached[2]
        worktrees = self._worktrees(roots[-1])
        dirs = list(dict.fromkeys(worktrees + roots))
        plans, seen = [], set()
        folders = [os.path.join(folder, plan_mod.PLANS_DIR) for folder in dirs]
        folders += [f for f in boardplans.plan_dirs(dirs) if f not in folders]
        for folder in folders:
            for plan, digest in self._folder_plans(folder):
                if (os.path.basename(plan.path), digest) not in seen:
                    seen.add((os.path.basename(plan.path), digest))
                    plans.append(plan)
        for label, blob in self._branch_plans(roots[-1]):
            if (os.path.basename(label), blob) not in seen:
                seen.add((os.path.basename(label), blob))
                # Plan z gałęzi: ścieżka „gałąź:docs/agents/plans/…” — nazwa pliku zostaje ta sama.
                plan = plan_mod.parse(self._blobs[blob], label)
                if plan is not None:
                    plans.append(plan)
        declared = declaration(worktrees[0] if worktrees else roots[-1])
        if len(self._repos) > 64:
            self._repos.clear()
        self._repos[key] = (now, plans, declared)
        return plans, declared

    # --- wnętrze ---------------------------------------------------------------------------

    def _worktrees(self, root: str) -> list[str]:
        out = self.git(root, ["worktree", "list", "--porcelain"])
        if out is None:
            return []
        paths = []
        for line in out.decode("utf-8", "replace").splitlines():
            if line.startswith("worktree "):
                path = line[len("worktree "):].strip()
                if os.path.isdir(path):
                    paths.append(path)
        return paths

    def _folder_plans(self, folder: str):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return
        for name in names:
            if not plan_mod.NAME_RE.match(name):
                continue
            path = os.path.join(folder, name)
            try:
                st = os.stat(path)
                if st.st_size > plan_mod.MAX_BYTES:
                    continue
                stamp = (path, st.st_size, st.st_mtime)
                if stamp not in self._files:
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()
                    if len(self._files) > 2000:
                        self._files.clear()
                    plan = plan_mod.parse(text, path, st.st_mtime)
                    self._files[stamp] = (plan, _digest(text))
            except (OSError, UnicodeDecodeError):
                continue
            plan, digest = self._files[stamp]
            if plan is not None:
                yield plan, digest

    def _branch_plans(self, root: str) -> list[tuple[str, str]]:
        """[(„gałąź:ścieżka”, blob)] planów ze wszystkich gałęzi lokalnych (treść w self._blobs)."""
        out = self.git(root, ["for-each-ref", "--format=%(objectname) %(refname:short)", "refs/heads"])
        if out is None:
            return []
        refs = []
        for line in out.decode("utf-8", "replace").splitlines()[:MAX_BRANCHES]:
            sha, _sep, name = line.partition(" ")
            if sha and name:
                refs.append((sha, name))
        for sha, _name in refs:
            if sha not in self._trees:
                listing = self.git(root, ["ls-tree", "-r", "--full-tree", sha, "--", plan_mod.PLANS_DIR + "/"])
                entries = []
                for line in (listing or b"").decode("utf-8", "replace").splitlines():
                    meta, _tab, path = line.partition("\t")
                    parts = meta.split()
                    if len(parts) == 3 and parts[1] == "blob" and os.path.dirname(path) == plan_mod.PLANS_DIR \
                            and plan_mod.NAME_RE.match(os.path.basename(path)):
                        entries.append((path, parts[2]))
                if len(self._trees) > 4000:
                    self._trees.clear()
                self._trees[sha] = entries
        missing = sorted({blob for sha, _ in refs for _path, blob in self._trees.get(sha, []) if blob not in self._blobs})
        if missing:
            self._read_blobs(root, missing)
        return [(f"{name}:{path}", blob) for sha, name in refs for path, blob in self._trees.get(sha, [])
                if self._blobs.get(blob) is not None]

    def _read_blobs(self, root: str, blobs: list) -> None:
        out = self.git(root, ["cat-file", "--batch"], stdin=("\n".join(blobs) + "\n").encode())
        found = {}
        data = out or b""
        pos = 0
        while pos < len(data):
            end = data.find(b"\n", pos)
            if end < 0:
                break
            header = data[pos:end].decode("ascii", "replace").split()
            pos = end + 1
            if len(header) != 3 or header[1] != "blob":
                continue
            size = int(header[2]) if header[2].isdigit() else 0
            body = data[pos:pos + size]
            pos += size + 1
            if size <= plan_mod.MAX_BYTES:
                found[header[0]] = body.decode("utf-8", "replace")
        if len(self._blobs) > 4000:
            self._blobs.clear()
        for blob in blobs:
            text = found.get(blob)
            self._blobs[blob] = text if text is not None and plan_mod.parse(text, "x.md") is not None else None


def declaration(root: str) -> dict:
    """Prefiks, tracker i projekt zadeklarowane w CLAUDE.md (pierwszeństwo) i AGENTS.md głównego katalogu."""
    out = {"prefix": "", "tracker": "", "project": "", "source": ""}
    for name in DECLARATION_FILES:
        try:
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                found = numbering.declared_prefix(fh.read(256 * 1024))
        except (OSError, UnicodeDecodeError):
            continue
        if not any(found.values()):
            continue
        for key, value in found.items():
            if value and not out[key]:
                out[key] = value
        out["source"] = out["source"] or name
    return out if out["source"] else {}


def check(roots, reader=None, tracker=None, aliases=None, registry: str | None = None) -> list[dict]:
    """Ostrzeżenia o numerach ticketów repozytorium (numbering.conflicts) — wspólne dla tablicy,
    raportu porannego i tools/check_plans.py: plany ze wszystkich worktree i gałęzi, deklaracja
    z CLAUDE.md/AGENTS.md, rezerwacje numerów i — gdy są plany lokalne — lista projektów YouTracka
    (`tracker.projects()`, tylko GET, z pamięcią). aliases=None — mapa migracji z repozytorium."""
    from . import ids, reservations  # tu, nie na górze: reservations importuje ten moduł
    roots = [r for r in roots or () if r]
    reader = reader or RepoPlans(ttl=0)
    plans, declared = reader.read(roots)
    if not plans and not declared:
        return []
    projects = None
    local = any(p.tracker == "local" for p in plans) or (declared or {}).get("tracker") == "local"
    if local and tracker is not None and hasattr(tracker, "projects"):
        projects, _error = tracker.projects()
    if aliases is None:
        aliases = ids.load_aliases(roots)
    reserved = reservations.load(roots[-1], registry, git=reader.git) if local else []
    return numbering.conflicts(plans, projects, aliases, declared, reserved)


def _digest(text: str) -> str:
    """Skrót jak `git hash-object` — ta sama treść w worktree i na gałęzi liczy się raz."""
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
