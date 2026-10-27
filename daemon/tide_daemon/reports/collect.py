"""Model raportu: projekty z commitami, sesjami agentów, zaplanowanymi zadaniami i worktrees.

Projekt to repozytorium z katalogu projektów (z wszystkimi swoimi worktrees). Sesja agenta
należy do projektu, w którego worktree (najgłębszym) leży jej katalog roboczy; sesja spoza
repozytoriów tworzy „projekt” po nazwie katalogu (np. korzeń przestrzeni roboczej) — to nie jest
prawdziwy projekt: bez strony i bez „Na daily”, jego sesje są w indeksie w zwiniętej sekcji „Poza
projektami”. Repozytorium „z ruchem” ma w okresie commit, sesję albo przebieg zaplanowanego zadania
— tylko takie dostaje własną stronę; reszta trafia na zwiniętą listę w indeksie (z tym, co w nich
czeka: worktrees do sprzątnięcia, zmiany).
"""
import os
import re
from dataclasses import dataclass, field

from ..worktrees.git import Git
from ..worktrees.scanner import WorktreeScanner, agent_cwds
from . import cards as cards_mod, gitlog, limits, plans as plans_mod, sessions as sessions_mod

SLUG_RE = re.compile(r"[^a-z0-9._-]+")
RESERVED = {"index", ""}


@dataclass
class Project:
    key: str
    name: str
    path: str
    is_repo: bool = True
    main_branch: str = ""
    slug: str = ""
    branches: list = field(default_factory=list)       # gitlog.BranchLog
    sessions: list = field(default_factory=list)       # sessions.SessionReport
    to_clean: list = field(default_factory=list)       # wpisy worktrees gotowe do sprzątnięcia
    unpushed: list = field(default_factory=list)       # worktrees z pracą tylko tutaj albo zmianami
    main_dirty: list = field(default_factory=list)     # [(kod, ścieżka)] w głównym katalogu
    main_dirty_count: int = 0
    plans: list = field(default_factory=list)          # plans.attach: plany i tickety (opcjonalny dodatek)
    label: str = ""                                    # nazwa projektu z tablicy pracy (pusta — nazwa katalogu)

    @property
    def display(self) -> str:
        """Nazwa do pokazania: z tablicy pracy („LegalApp”), inaczej nazwa katalogu."""
        return self.label or self.name

    @property
    def commits(self) -> int:
        return sum(len(b.commits) for b in self.branches)

    @property
    def runs(self) -> int:
        return sum(len(s.runs) for s in self.sessions)

    @property
    def waiting(self) -> list:
        return [s for s in self.sessions if s.state == "waiting"]

    @property
    def working(self) -> list:
        return [s for s in self.sessions if s.state == "working"]

    @property
    def active(self) -> bool:
        return bool(self.commits or self.sessions)

    @property
    def last_ts(self) -> float:
        times = [s.last_ts for s in self.sessions] + [c.ts for b in self.branches for c in b.commits]
        return max(times, default=0.0)


@dataclass
class Report:
    day: str
    generated: float
    start: float
    end: float
    projects: list
    limits: dict = field(default_factory=dict)
    forecast: object = None
    previous_day: str = ""
    standup: dict = field(default_factory=dict)   # „Na daily” (summarize.summarize), dokłada generate()
    notes: dict = field(default_factory=dict)     # „Skończone dziś”: projekt → notki po tickecie (reports/notes.py)
    board: object = None                          # cards.BoardInfo z migawki tablicy pracy (nazwy projektów, karty)

    @property
    def active(self) -> list:
        return [p for p in self.projects if p.active]

    @property
    def repos(self) -> list:
        """Projekty-repozytoria z ruchem — każdy ma własną stronę z „Na daily”."""
        return [p for p in self.active if p.is_repo]

    @property
    def outside(self) -> list:
        """Sesje spoza repozytoriów (katalogi po nazwie) — sekcja „Poza projektami” w indeksie."""
        return [p for p in self.active if not p.is_repo]

    @property
    def quiet(self) -> list:
        return [p for p in self.projects if not p.active]

    @property
    def waiting(self) -> list:
        return [(p, s) for p in self.active for s in p.waiting]


def build(day: str, start: float, end: float, *, workspace: str, claude_root: str, codex_root: str,
          live: list[dict] | None = None, worktrees: list[dict] | None = None, limits_result: dict | None = None,
          git: Git | None = None, max_depth: int = 3, previous_day: str = "",
          coordination_cache: str | None = None, tracker=None, board=None) -> Report:
    """board: migawka tablicy pracy (/Board Snapshot()) albo None — nazwy projektów i karty (cards.py)."""
    git = git or Git()
    if worktrees is None:
        scanner = WorktreeScanner(workspace, max_depth, git=git, clock=lambda: end)
        scanner.refresh()
        worktrees = scanner.snapshot(agent_cwds(live or []), now=end)

    found = sessions_mod.collect(claude_root, codex_root, start, end, live)

    projects: dict[str, Project] = {}
    for item in worktrees:
        key = item.get("repoPath") or item.get("path")
        project = projects.get(key)
        if project is None:
            project = projects[key] = Project(key=key, name=item.get("repo") or os.path.basename(key),
                                              path=key, main_branch=item.get("mainBranch") or "")
        if item.get("isMain"):
            project.path = item.get("path") or project.path
            if item.get("dirty") and not item.get("missing"):
                project.main_dirty, project.main_dirty_count = gitlog.status_files(git, item["path"])
            continue
        if item.get("missing"):
            continue
        if item.get("removable"):
            project.to_clean.append(item)
        elif not item.get("isMain") and item.get("branch") not in ("master", "main", "develop") \
                and ((item.get("unpushed") or 0) > 0 or (item.get("dirty") or 0) != 0):
            project.unpushed.append(item)

    # Sesje → projekt po najgłębszym worktree z katalogiem roboczym sesji.
    bases = [(os.path.realpath(w.get("path") or ""), w.get("repoPath") or w.get("path")) for w in worktrees
             if w.get("path")]
    for report in found:
        real = os.path.realpath(report.cwd)
        best, best_len = None, -1
        for base, key in bases:
            if (real == base or real.startswith(base.rstrip("/") + "/")) and len(base) > best_len:
                best, best_len = key, len(base)
        if best is None:
            # Np. worktree agenta w ~/.codex/worktrees, którego główne repozytorium już go nie wymienia:
            # gitinfo zna nazwę repozytorium — jeśli to jednoznacznie nasz projekt, sesja należy do niego.
            same = [k for k, p in projects.items() if p.is_repo and p.name == report.project]
            if len(same) == 1:
                projects[same[0]].sessions.append(report)
                continue
            key = "dir:" + report.project
            project = projects.get(key)
            if project is None:
                project = projects[key] = Project(key=key, name=report.project or os.path.basename(report.cwd),
                                                  path=report.cwd, is_repo=False)
        else:
            project = projects[best]
        project.sessions.append(report)

    for project in projects.values():
        if not project.is_repo:
            continue
        repo_items = [w for w in worktrees if (w.get("repoPath") or w.get("path")) == project.key]
        project.branches = gitlog.repo_commits(git, project.key, project.main_branch, start, end,
                                               repo_items, project.sessions)

    plans_mod.attach(list(projects.values()), start, end, coordination_cache, tracker)
    ordered = sorted(projects.values(), key=lambda p: (not p.waiting, not p.active, -p.last_ts, p.name.lower()))
    assign_slugs(ordered)
    info = board if isinstance(board, cards_mod.BoardInfo) else cards_mod.BoardInfo(board)
    assign_labels(ordered, info)
    snap = limits.snapshot(limits_result)
    return Report(day=day, generated=end, start=start, end=end, projects=ordered, limits=snap,
                  forecast=limits.forecast(snap), previous_day=previous_day, board=info)


def assign_labels(projects: list[Project], info: "cards_mod.BoardInfo") -> None:
    """Nazwy z tablicy pracy dla repozytoriów, które są jej projektami (po ścieżce `repo`). Dwa projekty
    raportu z tą samą nazwą do pokazania dostają w nawiasie nazwę katalogu — nazwa musi wskazywać jeden."""
    for project in projects:
        project.label = (info.label(project.path) or info.label(project.key)) if project.is_repo else ""
    shown: dict[str, int] = {}
    for project in projects:
        shown[project.display.casefold()] = shown.get(project.display.casefold(), 0) + 1
    for project in projects:
        if shown[project.display.casefold()] > 1 and project.label and project.label != project.name:
            project.label = f"{project.label} ({project.name})"


def slug_base(name: str) -> str:
    """Nazwa pliku strony projektu bez numeru przy powtórce (assign_slugs) — też dla notek po tickecie."""
    base = SLUG_RE.sub("-", (name or "").lower()).strip("-.")[:60].strip("-.") or "projekt"
    return f"{base}-projekt" if base in RESERVED else base


def assign_slugs(projects: list[Project]) -> None:
    """Bezpieczne nazwy plików: [a-z0-9._-], bez „index”, bez kropki na początku, unikalne."""
    used: set[str] = set()
    for project in projects:
        base = slug_base(project.name)
        slug, n = base, 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        used.add(slug)
        project.slug = slug
