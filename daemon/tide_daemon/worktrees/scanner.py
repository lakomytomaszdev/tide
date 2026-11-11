"""Rdzeń obszaru worktrees: katalog projektów na wejściu, lista worktrees i wynik sprzątania na wyjściu.

Bez sieci: tylko lokalne polecenia gita (git.py). „Scalony” znaczy: czubek gałęzi worktree jest
przodkiem lokalnej gałęzi głównej (master, main albo develop) — nie porównujemy z serwerem.

Co liczymy i skąd:
- repozytoria: katalogi z `.git` w katalogu projektów, do max_depth poziomów w głąb, bez
  katalogów ukrytych i zależności (node_modules itp.); w repozytorium nie schodzimy niżej,
- worktrees: `git worktree list --porcelain` każdego repozytorium (także te poza katalogiem projektów),
- zmiany: liczba pozycji w `git status --porcelain` (nieśledzony katalog to jedna pozycja),
- niescalone commity: `git rev-list --count <główna>..<HEAD>`; scalony = 0,
- commity tylko tutaj (unpushed): z HEAD, ale z żadnej innej lokalnej gałęzi ani gałęzi zdalnej
  (stan z ostatniego fetch) — liczone tylko dla niescalonych; to one przepadają po `branch -D`,
- ostatnia zmiana: najnowsze z czasu ostatniego commita HEAD i mtime plików wskazanych przez
  `git status` — tanie, bo i tak czytamy status, a nie chodzimy po wszystkich plikach,
- rozmiar: jak `du -s` (disk.py), w pamięci podręcznej i przeliczany najwyżej co SIZE_TTL,
- aktywny agent: katalog roboczy żywej sesji agenta (albo procesu claude/codex) leży w worktree;
  przy zagnieżdżonych worktrees liczy się najgłębszy,
- tytuł i narzędzie: z nazwy gałęzi (naming.py), opis z tematu ostatniego commita albo z tytułu
  powiązanej sesji agenta z ostatnich 30 dni (sessions.py; żywa sesja przed najświeższą),
- ostatnia aktywność: najnowsze z ostatniej zmiany i ostatniego wpisu powiązanej sesji,
- własne commity (own_commits): reflog gałęzi (albo HEAD odłączonego worktree) czytany wprost
  z plików — tylko dla scalonych i czystych, bez dodatkowego wywołania gita,
- grupa dla okna Worktrees (group_of): w pracy, do sprzątnięcia, do decyzji, gałęzie główne,
  brakujące katalogi.

Sprzątanie (remove) usuwa wyłącznie `git worktree remove` bez --force, a gałąź tylko `git branch -d`.
Usuwanie wymuszone (remove z force=True, RemoveForced w D-Bus) to świadoma decyzja użytkownika po
potwierdzeniu, co przepadnie: `git worktree remove --force`, gałąź `-d` tylko gdy scalona, `-D` tylko
na wyraźną prośbę. Twarde blokady (HARD_BLOCKERS) obowiązują także wtedy: główny katalog, gałąź
główna, brakujący katalog, blokada `git worktree lock`, działający w nim agent i inne worktrees w jego katalogu — dla nich nie ma
żadnej ścieżki wymuszenia.
"""
import os
import threading
import time
from dataclasses import dataclass, field

from ..agents.timeutil import iso
from ..board.marker import is_board_repo
from .disk import disk_usage
from .cancellation import Cancellation
from .git import (HEAD_RESTARTS, MAIN_CANDIDATES, Git, common_dir, parse_status, parse_worktree_list,
                  pick_main_branch, reflog_has_own_commit, worktree_gitdir)
from .naming import guess_tool, humanize_branch

MAX_DEPTH = 3
SIZE_TTL = 30 * 60
# Katalogi, w których nie szukamy repozytoriów: zależności i wyniki budowania.
SKIP_DIRS = {"node_modules", "bower_components", "vendor", "bin", "obj", "target", "build", "dist",
             "out", "__pycache__", "venv", "env", "Pods", "DerivedData"}

# Powody pominięcia przy sprzątaniu (kolejność = pierwszeństwo).
BLOCKERS = ("main", "protected", "missing", "locked", "agent", "nested", "dirty", "no-main", "unmerged")
# Twarde blokady: tych worktrees nie usuwamy nigdy, także po potwierdzeniu „Usuń mimo to”.
# Główny katalog i gałąź główna (master, main, develop) — po tym, jak sprzątanie omal nie skasowało
# master w LogViewer; brakujący katalog porządkuje tylko prune; blokadę zdejmuje się świadomie
# (`git worktree unlock`); agent pracujący w katalogu — usunięcie katalogu spod działającego procesu
# kończy się jego błędami albo odtworzeniem plików w pustym miejscu (decyzja 41); worktree, w którego
# katalogu leżą inne worktrees (np. ignorowane .claude/worktrees/) — `git worktree remove` bez --force
# kończy się sukcesem i kasuje je razem z ich niezacommitowanymi plikami, a agent w środku nie blokuje
# zewnętrznego (PT-30).
HARD_BLOCKERS = ("main", "protected", "missing", "locked", "agent", "nested")

# Grupy okna Worktrees. „W pracy”: agent albo aktywność w ostatniej dobie — z wyjątkiem scalonej
# pracy (scalony, czysty, z własnymi commitami), która od razu idzie „Do sprzątnięcia”.
ACTIVE_WINDOW = 24 * 3600
GROUPS = ("active", "clean", "decide", "main", "missing")
SUBJECT_LIMIT = 120


def group_of(wt: "Worktree", blocker: str, has_agent: bool, last_activity: float, now: float) -> str:
    """Grupa worktree w oknie. Kolejność reguł ma znaczenie:
    brakujący katalog → missing; główny katalog i gałąź główna → main (nigdy do sprzątania);
    agent → active; da się bezpiecznie usunąć i ma własne commity (praca skończona i scalona)
    → clean od razu; aktywność krótsza niż ACTIVE_WINDOW → active; da się bezpiecznie usunąć
    → clean; reszta (niescalone commity, zmiany, blokada, brak gałęzi głównej) → decide.

    Okno ACTIVE_WINDOW zostaje dla świeżych worktrees bez własnych commitów: taki też wygląda na
    scalony (0 commitów poza gałęzią główną), a to dopiero początek pracy. Gdy nie wiadomo, czy
    były własne commity (own_commits None: brak reflogu), obowiązuje okno jak dotąd."""
    if wt.missing:
        return "missing"
    if wt.is_main or wt.protected:
        return "main"
    if has_agent:
        return "active"
    if blocker == "" and wt.own_commits:
        return "clean"
    if now - last_activity < ACTIVE_WINDOW:
        return "active"
    return "clean" if blocker == "" else "decide"


@dataclass
class Worktree:
    repo: str            # nazwa repozytorium (katalog główny bez .git)
    repo_path: str       # katalog, w którym uruchamiamy gita dla repozytorium
    path: str
    head: str = ""
    branch: str = ""
    detached: bool = False
    is_main: bool = False
    locked: bool = False
    missing: bool = False
    main_branch: str = ""
    dirty: int = 0
    unmerged: int | None = None   # None: nie da się policzyć (brak gałęzi głównej)
    unpushed: int = 0             # commity tylko w tym worktree (bez innej gałęzi i gałęzi zdalnej); -1: nie wiadomo
    last_change: float = 0.0
    subject: str = ""             # temat ostatniego commita HEAD
    own_commits: bool | None = None  # czy w gałęzi powstał własny commit (reflog); None: nie wiadomo
    nested: tuple = ()            # inne worktrees wewnątrz tego katalogu
    real: str = ""                # ścieżka bez dowiązań — do porównań z katalogami agentów
    upstream: str = ""            # skrót upstreamu gałęzi (origin/master); pusty: „tylko lokalnie” albo odłączony HEAD
    upstream_ahead: int | None = None   # commity gałęzi poza upstreamem (stan z ostatniego fetch); None: brak liczb
    upstream_behind: int | None = None  # commity upstreamu poza gałęzią
    main_ahead: int | None = None       # to samo względem lokalnej gałęzi głównej; None: brak głównej
    main_behind: int | None = None
    remotes: tuple = ()           # nazwy zdalnych repozytorium (z konfiguracji, bez sieci)
    last_fetch: float = 0.0       # mtime FETCH_HEAD we wspólnym katalogu gita; 0: nigdy nie pobrano

    @property
    def merged(self) -> bool:
        return self.unmerged == 0

    @property
    def protected(self) -> bool:
        """Worktree z gałęzią główną (np. master w osobnym katalogu) — „scalony” sam ze sobą,
        ale jego usunięcie, a tym bardziej gałęzi, nie jest sprzątaniem."""
        return bool(self.branch) and (self.branch == self.main_branch or self.branch in MAIN_CANDIDATES)

    def blocker(self, has_agent: bool) -> str:
        """Pierwszy powód, dla którego nie wolno go usunąć, albo pusty napis."""
        checks = {
            "main": self.is_main,
            "protected": self.protected,
            "missing": self.missing,
            "locked": self.locked,
            "agent": has_agent,
            "nested": bool(self.nested),
            "dirty": self.dirty != 0,  # -1: statusu nie dało się odczytać — traktujemy jak zmiany
            "no-main": not self.main_branch,
            "unmerged": not self.merged,
        }
        return next((name for name in BLOCKERS if checks[name]), "")

    def force_blocker(self, has_agent: bool) -> str:
        """Pierwsza twarda blokada (HARD_BLOCKERS) — powód, dla którego nie usuniemy go nawet
        na wyraźne żądanie — albo pusty napis."""
        checks = {
            "main": self.is_main,
            "protected": self.protected,
            "missing": self.missing,
            "locked": self.locked,
            "agent": has_agent,
            "nested": bool(self.nested),
        }
        return next((name for name in HARD_BLOCKERS if checks[name]), "")


def own_commits(repo_path: str, wt: Worktree) -> bool | None:
    """Czy w worktree powstał własny commit — dowód z reflogu, czytany wprost z plików (bez gita):
    gałąź → <wspólny>/logs/refs/heads/<gałąź> (commity po jej założeniu); odłączony HEAD →
    logs/HEAD z prywatnego katalogu worktree (commity po ostatnim checkout). None, gdy reflogu
    nie ma (wyłączony, repozytorium bare, reftable) — wtedy grupę wyznacza okno ACTIVE_WINDOW."""
    if wt.branch:
        return reflog_has_own_commit(os.path.join(common_dir(repo_path), "logs", "refs", "heads", wt.branch))
    gitdir = worktree_gitdir(wt.path)
    return reflog_has_own_commit(os.path.join(gitdir, "logs", "HEAD"), HEAD_RESTARTS) if gitdir else None


def find_repositories(root: str, max_depth: int = MAX_DEPTH, *, check=lambda: None) -> list[str]:
    """Katalogi repozytoriów (z katalogiem .git albo repozytoria bare) pod root — bez repozytorium
    tablicy Tide (rozpoznanego po znaczniku), więc nie ma go w worktrees, KRunnerze ani nowym zadaniu."""
    found: list[str] = []

    def walk(path: str, depth: int) -> None:
        check()
        dotgit = os.path.join(path, ".git")
        if os.path.isdir(dotgit) or _is_bare(path):
            if not is_board_repo(path):  # tablica Tide to dane, nie projekt (board/marker.py)
                found.append(path)
            return
        if os.path.exists(dotgit):
            return  # plik .git: worktree albo submoduł — znajdziemy go przez główne repozytorium
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name in SKIP_DIRS:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    walk(entry.path, depth + 1)
            except OSError:
                continue

    walk(os.path.normpath(root), 0)
    return found


def _is_bare(path: str) -> bool:
    return (os.path.isfile(os.path.join(path, "HEAD")) and os.path.isdir(os.path.join(path, "objects"))
            and os.path.isdir(os.path.join(path, "refs")))


def agent_cwds(sessions: list[dict], procs=None) -> list[str]:
    """Katalogi robocze żywych agentów: sesje inne niż zakończone i procesy claude/codex."""
    cwds = [s.get("cwd", "") for s in sessions if s.get("state") != "ended"]
    if procs is not None:
        for tool in ("claude", "codex"):
            cwds += [p.cwd for p in procs.agents(tool)]
    return sorted({c for c in cwds if c})


def live_sessions(sessions: list[dict]) -> set[tuple[str, str]]:
    """Żywe sesje z obszaru Agents jako pary (narzędzie, identyfikator) — przy kilku sesjach
    w jednym worktree znaczek narzędzia bierzemy z tej, która tam teraz pracuje."""
    return {(s.get("tool", ""), s.get("sessionId", "")) for s in sessions
            if s.get("state") != "ended" and s.get("tool") and s.get("sessionId")}


class WorktreeScanner:
    def __init__(self, root: str, max_depth: int = MAX_DEPTH, size_ttl: float = SIZE_TTL,
                 git: Git | None = None, clock=time.time, sessions=None):
        self.root = root
        self.sessions = sessions  # SessionIndex albo None (bez powiązań z sesjami)
        self.max_depth = max_depth
        self.size_ttl = size_ttl
        self.git = git or Git()
        self._cancel = Cancellation()
        if isinstance(self.git, Git):
            self.git.cancel = self._cancel
        self.clock = clock
        self.items: list[Worktree] = []
        self._sizes: dict[str, tuple[int, float]] = {}  # ścieżka → (bajty, kiedy policzone)
        self._lock = threading.Lock()

    def stop(self) -> None:
        self._cancel.close()

    # --- odczyt (praca w tle) -------------------------------------------------------------

    def refresh(self) -> list[Worktree]:
        """Przegląda repozytoria i odświeża listę. Ciężkie — wołać poza wątkiem D-Bus."""
        items: list[Worktree] = []
        for repo in find_repositories(self.root, self.max_depth, check=self._cancel.check):
            self._cancel.check()
            items += self.scan_repo(repo)
        items = _dedupe(items)
        _mark_nested(items)
        if self.sessions is not None:
            try:
                self.sessions.refresh(self.clock())
            except Exception as exc:  # powiązanie z sesjami jest dodatkiem — nie może zatrzymać listy
                print(f"tide-daemon: worktrees: sesje: {exc!r}", flush=True)
        with self._lock:
            self.items = items
        return items

    def scan_repo(self, repo_path: str) -> list[Worktree]:
        listed = self.git.run(repo_path, "worktree", "list", "--porcelain")
        if not listed.ok:
            return []
        entries = parse_worktree_list(listed.out)
        if not entries:
            return []
        main_entry = entries[0]  # pierwszy wpis to zawsze główny katalog (albo repozytorium bare)
        name = os.path.basename(main_entry.path.rstrip("/"))
        if main_entry.bare and name.endswith(".git"):
            name = name[:-4]

        refs = self.git.run(repo_path, "for-each-ref", "--format=%(refname:short)",
                            "refs/heads/master", "refs/heads/main", "refs/heads/develop")
        existing = set(refs.out.split()) if refs.ok else set()
        main_branch = pick_main_branch(existing, "" if main_entry.bare else main_entry.branch)

        heads = [e.head for e in entries if e.head and not e.bare]
        commits = self._commits(repo_path, heads)
        upstreams = self._upstreams(repo_path)
        remotes = self._remotes(repo_path)
        last_fetch = _mtime(os.path.join(common_dir(repo_path), "FETCH_HEAD"))

        out = []
        for entry in entries:
            self._cancel.check()
            if entry.bare:
                continue
            wt = Worktree(repo=name, repo_path=repo_path, path=entry.path, head=entry.head[:7],
                          branch=entry.branch, detached=entry.detached, is_main=entry is main_entry,
                          locked=entry.locked, main_branch=main_branch,
                          missing=entry.prunable or not os.path.isdir(entry.path),
                          remotes=remotes, last_fetch=last_fetch)
            wt.real = os.path.realpath(entry.path)
            wt.last_change, wt.subject = commits.get(entry.head, (0.0, ""))
            if not wt.missing:
                self._read_status(wt)
            if main_branch and entry.head:
                count = self.git.run(repo_path, "rev-list", "--count", f"refs/heads/{main_branch}..{entry.head}")
                if count.ok and count.out.strip().isdigit():
                    wt.unmerged = int(count.out)
            if entry.head:
                self._read_divergence(wt, entry.head, upstreams.get(entry.branch) if entry.branch else None)
            if entry.head and not wt.missing and not wt.is_main and not wt.protected and wt.unmerged != 0:
                wt.unpushed = self._only_here(repo_path, entry.head, entry.branch)
            if wt.merged and wt.dirty == 0 and not wt.missing and not wt.is_main and not wt.protected:
                wt.own_commits = own_commits(repo_path, wt)
            out.append(wt)
        return out

    def _upstreams(self, repo_path: str) -> dict[str, tuple[str, str]]:
        """gałąź → (pełna nazwa upstreamu, skrót) — jedno wywołanie gita na repozytorium; gałęzie bez
        upstreamu pomijamy."""
        res = self.git.run(repo_path, "for-each-ref", "--format=%(refname:short)%09%(upstream)%09%(upstream:short)",
                           "refs/heads")
        out = {}
        for line in res.out.splitlines() if res.ok else ():
            name, _, rest = line.partition("\t")
            full, _, short = rest.partition("\t")
            if name and full:
                out[name] = (full, short or full)
        return out

    def _remotes(self, repo_path: str) -> tuple:
        """Nazwy zdalnych z lokalnej konfiguracji (bez `git remote` i bez sieci)."""
        res = self.git.run(repo_path, "config", "--local", "--list", "--name-only")
        names = []
        for key in res.out.splitlines() if res.ok else ():
            if key.startswith("remote.") and key.rsplit(".", 1)[-1] in ("url", "fetch", "pushurl"):
                name = key[len("remote."):key.rindex(".")]
                if name and name not in names:
                    names.append(name)
        return tuple(names)

    def _read_divergence(self, wt: Worktree, head: str, upstream) -> None:
        """Ahead/behind względem upstreamu i lokalnej głównej: jedno `rev-list --left-right --count`
        na każde porównanie (lewa liczba: commity HEAD, prawa: commity drugiej strony)."""
        if upstream:
            wt.upstream = upstream[1]
            wt.upstream_ahead, wt.upstream_behind = self._left_right(wt.repo_path, head, upstream[0])
        if wt.main_branch:
            wt.main_ahead, wt.main_behind = self._left_right(wt.repo_path, head, f"refs/heads/{wt.main_branch}")

    def _left_right(self, repo_path: str, head: str, other: str) -> tuple:
        res = self.git.run(repo_path, "rev-list", "--left-right", "--count", f"{head}...{other}")
        parts = res.out.split() if res.ok else []
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            return int(parts[0]), int(parts[1])
        return None, None

    def _only_here(self, repo_path: str, head: str, branch: str) -> int:
        """Commity osiągalne z head, a z żadnej innej lokalnej gałęzi ani gałęzi zdalnej (stan
        z ostatniego fetch, bez sieci). To one przepadną po `git branch -D` (zostaje tylko reflog).
        -1, gdy nie da się policzyć."""
        exclude = [f"--exclude={branch}"] if branch else []
        res = self.git.run(repo_path, "rev-list", "--count", head, "--not", *exclude, "--branches", "--remotes")
        return int(res.out) if res.ok and res.out.strip().isdigit() else -1

    def _commits(self, repo_path: str, heads: list[str]) -> dict[str, tuple[float, str]]:
        """sha → (czas commita, temat) — jedno wywołanie gita na repozytorium."""
        if not heads:
            return {}
        res = self.git.run(repo_path, "show", "-s", "--format=%H %ct %s", *sorted(set(heads)))
        out = {}
        for line in res.out.splitlines() if res.ok else ():
            sha, _, rest = line.partition(" ")
            ts, _, subject = rest.partition(" ")
            if ts.isdigit():
                out[sha] = (float(ts), _clip(subject.strip(), SUBJECT_LIMIT))
        return out

    def _read_status(self, wt: Worktree) -> None:
        res = self.git.run(wt.path, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
        if not res.ok:
            wt.dirty = -1 if res.code else 0
            return
        paths = parse_status(res.out)
        wt.dirty = len(paths)
        for rel in paths:
            try:
                wt.last_change = max(wt.last_change, os.lstat(os.path.join(wt.path, rel.rstrip("/"))).st_mtime)
            except OSError:
                continue  # usunięty plik — nie ma mtime

    # --- rozmiary ------------------------------------------------------------------------

    def stale_sizes(self) -> list[Worktree]:
        now = self.clock()
        with self._lock:
            items = list(self.items)
            sizes = dict(self._sizes)
        return [w for w in items if not w.missing
                and (w.path not in sizes or now - sizes[w.path][1] > self.size_ttl)]

    def measure_stale(self) -> int:
        """Przelicza rozmiary bez wyniku albo starsze niż size_ttl. Zwraca liczbę przeliczonych."""
        stale = self.stale_sizes()
        for wt in stale:
            self._cancel.check()
            self.measure(wt)
        return len(stale)

    def measure(self, wt: Worktree) -> int:
        size = disk_usage(wt.path, wt.nested, check=self._cancel.check)
        with self._lock:
            self._sizes[wt.path] = (size, self.clock())
        return size

    def size_of(self, path: str) -> int:
        with self._lock:
            return self._sizes.get(path, (-1, 0))[0]

    # --- wynik -----------------------------------------------------------------------------

    def snapshot(self, cwds: list[str] = (), now: float | None = None, live=(), card_paths=()) -> list[dict]:
        """Lista dla D-Bus z bieżących danych — tania, bez wołania gita. live: żywe sesje
        (live_sessions) — mają pierwszeństwo przy powiązaniu worktree z sesją."""
        with self._lock:
            items = list(self.items)
        agents = _agents_by_worktree(items, cwds)
        cards = _agents_by_worktree(items, card_paths)
        linked = (self.sessions.link([(w.path, w.real) for w in items], live)
                  if self.sessions is not None else {})
        now = self.clock() if now is None else now
        out = [self._describe(w, w.path in agents, linked.get(w.path), now, w.path in cards) for w in items]
        out.sort(key=lambda d: (d["repo"].lower(), d["repoPath"], not d["isMain"], -d["lastChangeTs"]))
        return out

    def _describe(self, wt: Worktree, has_agent: bool, session=None, now: float = 0.0,
                  has_card: bool = False) -> dict:
        blocker = wt.blocker(has_agent)
        force_blocker = wt.force_blocker(has_agent)
        tag, title = humanize_branch(wt.branch)
        if not title:
            title = f"odłączony HEAD {wt.head}" if wt.detached else os.path.basename(wt.path.rstrip("/"))
        session_ts = session.last_ts if session else 0.0
        last_activity = max(wt.last_change, session_ts)
        return {
            "repo": wt.repo,
            "repoPath": wt.repo_path,
            "path": wt.path,
            "name": os.path.basename(wt.path.rstrip("/")),
            "branch": wt.branch,
            "detached": wt.detached,
            "head": wt.head,
            "isMain": wt.is_main,
            "hasAgent": has_agent,
            "hasCard": has_card,
            "dirty": wt.dirty,
            "mainBranch": wt.main_branch,
            "merged": wt.merged,
            "unmerged": wt.unmerged,
            "unpushed": wt.unpushed,
            "upstream": wt.upstream,
            "upstreamAhead": wt.upstream_ahead,
            "upstreamBehind": wt.upstream_behind,
            "mainAhead": wt.main_ahead,
            "mainBehind": wt.main_behind,
            "remotes": list(wt.remotes),
            "lastFetch": iso(wt.last_fetch) if wt.last_fetch else "",
            "lastChange": iso(wt.last_change),
            "lastChangeTs": round(wt.last_change, 3),
            "size": self.size_of(wt.path),
            "locked": wt.locked,
            "missing": wt.missing,
            "removable": blocker == "",
            "blocker": blocker,
            # Usuwanie wymuszone (RemoveForced): możliwe tylko bez twardej blokady.
            "forceRemovable": force_blocker == "",
            "forceBlocker": force_blocker,
            # Dla okna Worktrees:
            "group": group_of(wt, blocker, has_agent, last_activity, now),
            "title": title,
            "tag": tag,
            "subject": wt.subject,
            "tool": session.tool if session else guess_tool(wt.path, tag),
            "sessionId": session.session_id if session else "",
            "sessionTitle": session.title if session else "",
            "sessionLastActivityTs": round(session_ts, 3),
            "lastActivity": iso(last_activity),
            "lastActivityTs": round(last_activity, 3),
        }

    # --- sprzątanie ------------------------------------------------------------------------

    def remove(self, paths: list[str], cwds: list[str] = (), force: bool = False,
               delete_unmerged: bool = False, confirmed: dict | None = None) -> list[dict]:
        """Usuwa wskazane worktrees, jeśli wolno. Wynik dla każdej ścieżki w kolejności wejścia.

        Przed usunięciem stan repozytorium jest czytany na nowo — decyzja nie opiera się na
        danych sprzed minuty.

        force=False (sprzątanie): tylko worktrees bez żadnej blokady, `git worktree remove` bez
        --force, gałąź `git branch -d`.
        force=True (po potwierdzeniu w oknie): twarde blokady (HARD_BLOCKERS) dalej obowiązują,
        resztę usuwa `git worktree remove --force`. Gałąź: `-d`, gdy scalona z lokalną gałęzią
        główną, `-D` tylko z delete_unmerged, inaczej zostaje. confirmed: ścieżka → stan widziany
        w potwierdzeniu (dirty, unmerged, unpushed, branch); gdy świeży stan jest gorszy (więcej
        zmian albo commitów, inna gałąź), worktree jest pomijany z powodem „changed”.
        delete_unmerged bez force nic nie zmienia — sprzątanie nigdy nie woła `-D`.
        """
        seen_by_path = {os.path.normpath(k): v for k, v in (confirmed or {}).items() if k}
        results = []
        for raw in paths:
            path = os.path.normpath(raw) if raw else ""
            result = self._remove_one(path, cwds, force, force and delete_unmerged, seen_by_path.get(path))
            results.append(_finish(result))
        self.refresh()
        return results

    def _remove_one(self, path: str, cwds, force: bool = False, delete_unmerged: bool = False,
                    seen: dict | None = None) -> dict:
        result = {"path": path, "removed": False, "reason": "", "message": "", "branch": "",
                  "branchDeleted": False, "branchMessage": "", "freed": 0, "forced": force, "status": ""}
        with self._lock:
            known = next((w for w in self.items if w.path == path), None)
        if not known:
            return _skip(result, "unknown")
        fresh_items = self.scan_repo(known.repo_path)
        with self._lock:
            others = [w for w in self.items if w.repo_path != known.repo_path]
        # Zagnieżdżone liczymy ze świeżego stanu repozytorium i ze znanych worktrees innych repozytoriów.
        _mark_nested(fresh_items + others)
        wt = next((w for w in fresh_items if w.path == path), None)
        if not wt:
            return _skip(result, "unknown")
        result["branch"] = wt.branch
        has_agent = path in _agents_by_worktree(fresh_items, cwds)
        blocker = wt.force_blocker(has_agent) if force else wt.blocker(has_agent)
        if blocker:
            return _skip(result, blocker, wt)
        # Druga linia obrony: główny katalog i gałąź główna nigdy, niezależnie od listy blokad wyżej.
        if wt.is_main or wt.protected:
            return _skip(result, "main" if wt.is_main else "protected", wt)
        if force:
            changed = _changed_since(wt, seen)
            if changed:
                return _skip(result, "changed", wt, changed)

        size = self.size_of(path)
        # Jedno --force: zmiany i pliki nieśledzone idą razem z katalogiem, ale blokady
        # (git worktree lock) git i tak nie zdejmie — do tego trzeba by -f -f, którego nie używamy.
        args = ("worktree", "remove", "--force", wt.path) if force else ("worktree", "remove", wt.path)
        removed = self.git.run(wt.repo_path, *args, timeout=600)
        if not removed.ok:
            return _skip(result, "git", wt, removed.err)
        result["removed"] = True
        result["freed"] = max(size, 0)
        result["message"] = "usunięty razem ze zmianami" if force and wt.dirty else "usunięty"
        with self._lock:
            self._sizes.pop(path, None)
        # Druga linia obrony: gałęzi głównych nie kasujemy nigdy, nawet gdyby blokada wyżej zawiodła.
        if wt.branch and not wt.protected and wt.branch not in MAIN_CANDIDATES:
            self._delete_branch(wt, result, force, delete_unmerged)
        return result

    def _delete_branch(self, wt: Worktree, result: dict, force: bool, delete_unmerged: bool) -> None:
        """Gałąź po usuniętym worktree. Sprzątanie: zawsze `-d` (git sam pilnuje scalenia).
        Wymuszone: `-D` tylko na wyraźną prośbę, `-d`, gdy scalona z lokalną gałęzią główną,
        inaczej gałąź zostaje i wynik mówi dlaczego."""
        if force and not delete_unmerged and not wt.merged:
            count = (f"{wt.unmerged} {_commits_word(wt.unmerged)} poza {wt.main_branch}" if wt.unmerged
                     else "nie wiadomo, co jest scalone — brak lokalnej gałęzi głównej")
            result["branchMessage"] = f"gałąź {wt.branch} zostaje ({count})"
            return
        flag = "-D" if force and delete_unmerged else "-d"
        deleted = self.git.run(wt.repo_path, "branch", flag, wt.branch)
        result["branchDeleted"] = deleted.ok
        result["branchMessage"] = "gałąź usunięta" if deleted.ok else \
            f"gałąź zostaje: {_first_line(deleted.err)}"


    # --- brakujące katalogi -------------------------------------------------------------------

    def prune(self, repo_path: str) -> dict:
        """`git worktree prune` w znanym repozytorium, które ma worktrees bez katalogu.

        Git usuwa wtedy wyłącznie wpisy administracyjne worktrees, których katalogu już nie ma
        (zablokowane zostawia), nie dotyka plików ani gałęzi. Repozytorium jest czytane na nowo,
        a polecenie pada tylko wtedy, gdy rzeczywiście brakuje jakiegoś katalogu."""
        result = {"repoPath": repo_path, "ok": False, "pruned": [], "message": ""}
        with self._lock:
            known = any(w.repo_path == repo_path for w in self.items)
        if not known:
            result["message"] = "to nie jest znane repozytorium"
            return result
        missing = [w.path for w in self.scan_repo(repo_path) if w.missing and not w.locked]
        if not missing:
            result.update(ok=True, message="nie ma czego porządkować")
            return result
        res = self.git.run(repo_path, "worktree", "prune")
        if not res.ok:
            result["message"] = f"git odmówił: {_first_line(res.err)}"
            return result
        left = {w.path for w in self.scan_repo(repo_path) if w.missing}
        result.update(ok=True, pruned=[p for p in missing if p not in left],
                      message="usunięto wpisy worktrees bez katalogu")
        self.refresh()
        return result


MESSAGES = {
    "unknown": "to nie jest znany worktree",
    "main": "główny katalog repozytorium — nigdy go nie usuwamy",
    "protected": "stoi na gałęzi głównej (master, main, develop) — nigdy go nie usuwamy",
    "missing": "katalogu już nie ma (git worktree prune)",
    "locked": "zablokowany (git worktree lock)",
    "agent": "pracuje w nim agent",
    "nested": "zawiera inne worktrees — usunięcie skasowałoby je razem z ich plikami",
    "dirty": "ma niezacommitowane zmiany",
    "no-main": "brak lokalnej gałęzi głównej (master, main, develop)",
    "unmerged": "nie jest scalony z lokalną gałęzią główną",
    "git": "git odmówił usunięcia",
    "changed": "stan zmienił się od potwierdzenia — nic nie usunięto, sprawdź jeszcze raz",
}
# Przy usuwaniu wymuszonym te powody mówią, co zrobić, żeby jednak usunąć.
FORCE_MESSAGES = {
    "agent": "pracuje w nim agent — zamknij sesję, zanim usuniesz katalog",
    "locked": "zablokowany (git worktree lock) — najpierw git worktree unlock",
    "nested": "zawiera inne worktrees — najpierw usuń te w środku",
}


def _skip(result: dict, reason: str, wt: Worktree | None = None, detail: str = "") -> dict:
    message = FORCE_MESSAGES.get(reason, MESSAGES[reason]) if result.get("forced") else MESSAGES[reason]
    if wt is not None and reason == "dirty":
        message += f" ({wt.dirty})"
    elif wt is not None and reason == "unmerged" and wt.unmerged:
        message = f"niescalone commity względem {wt.main_branch}: {wt.unmerged}"
    if detail:
        message += f": {_first_line(detail)}"
    result.update(reason=reason, message=message)
    return result


def _finish(result: dict) -> dict:
    """status: removed (worktree i jego gałąź, jeśli była, zniknęły), branch-kept (worktree
    usunięty, gałąź została — powód w branchMessage), refused (nic nie usunięto — powód w reason
    i message)."""
    if not result["removed"]:
        result["status"] = "refused"
    elif result["branch"] and not result["branchDeleted"]:
        result["status"] = "branch-kept"
    else:
        result["status"] = "removed"
    return result


def _changed_since(wt: Worktree, seen) -> str:
    """Czym świeży stan jest gorszy od widzianego w potwierdzeniu; pusty napis, gdy niczym.
    Bez wpisu (seen None) nie ma z czym porównać — okno zawsze go podaje."""
    if not isinstance(seen, dict):
        return ""
    if "branch" in seen and (seen.get("branch") or "") != wt.branch:
        return f"gałąź: było {seen.get('branch') or 'odłączony HEAD'}, jest {wt.branch or 'odłączony HEAD'}"
    for key, label in (("dirty", "zmiany"), ("unmerged", "niescalone commity"), ("unpushed", "commity tylko tutaj")):
        was = seen.get(key)
        if not isinstance(was, int) or isinstance(was, bool):
            continue
        now = getattr(wt, key)
        unknown = now is None or now < 0
        if (unknown and was >= 0) or (not unknown and was >= 0 and now > was):
            return f"{label}: było {was}, jest {'?' if unknown else now}"
    return ""


def _commits_word(n: int) -> str:
    ones, tens = n % 10, n % 100
    if n == 1:
        return "commit"
    return "commity" if 2 <= ones <= 4 and not 12 <= tens <= 14 else "commitów"


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _first_line(text: str) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return lines[0].strip() if lines else ""


def _dedupe(items: list[Worktree]) -> list[Worktree]:
    """To samo repozytorium znalezione dwa razy (np. przez dowiązanie) — zostaje pierwsze."""
    seen, out = set(), []
    for wt in items:
        if wt.path not in seen:
            seen.add(wt.path)
            out.append(wt)
    return out


def _mark_nested(items: list[Worktree]) -> None:
    for wt in items:
        prefix = wt.path.rstrip("/") + "/"
        wt.nested = tuple(other.path for other in items if other.path.startswith(prefix))


def _agents_by_worktree(items: list[Worktree], cwds) -> set[str]:
    """Ścieżki worktrees z agentem. Katalog agenta przypisujemy najgłębszemu worktree."""
    hit = set()
    for cwd in cwds:
        real = os.path.realpath(cwd)
        best = None
        for wt in items:
            base = wt.real or wt.path
            if real == base or real.startswith(base.rstrip("/") + "/"):
                if best is None or len(base) > len(best.real or best.path):
                    best = wt
        if best is not None:
            hit.add(best.path)
    return hit


def scan(root: str, cwds: list[str] = (), max_depth: int = MAX_DEPTH, measure: bool = True,
         now: float | None = None, sessions=None, live=()) -> list[dict]:
    """Jednorazowy przebieg dla testów i trybu --print: katalog projektów → lista worktrees."""
    scanner = WorktreeScanner(root, max_depth, sessions=sessions,
                              clock=(lambda: now) if now is not None else time.time)
    scanner.refresh()
    if measure:
        scanner.measure_stale()
    return scanner.snapshot(cwds, live=live)
