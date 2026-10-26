"""Model raportu: projekty z commitami, sesjami agentów, zaplanowanymi zadaniami i worktrees.

Projekt to repozytorium z katalogu projektów (z wszystkimi swoimi worktrees). Sesja agenta
należy do projektu, w którego worktree (najgłębszym) leży jej katalog roboczy; sesja spoza
repozytoriów tworzy projekt po nazwie katalogu. Projekt „z ruchem” ma w okresie commit, sesję
albo przebieg zaplanowanego zadania — tylko taki dostaje własną stronę; reszta trafia na
zwiniętą listę w indeksie (z tym, co w nich czeka: worktrees do sprzątnięcia, zmiany).
"""
import os
import re
from dataclasses import dataclass, field

from ..worktrees.git import Git
from ..worktrees.scanner import WorktreeScanner, agent_cwds
from . import gitlog, limits, plans as plans_mod, sessions as sessions_mod

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

    @property
    def active(self) -> list:
        return [p for p in self.projects if p.active]

    @property
    def quiet(self) -> list:
        return [p for p in self.projects if not p.active]

    @property
    def waiting(self) -> list:
        return [(p, s) for p in self.active for s in p.waiting]


def build(day: str, start: float, end: float, *, workspace: str, claude_root: str, codex_root: str,
          live: list[dict] | None = None, worktrees: list[dict] | None = None, limits_result: dict | None = None,
          git: Git | None = None, max_depth: int = 3, previous_day: str = "",
          coordination_cache: str | None = None) -> Report:
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

    plans_mod.attach(list(projects.values()), start, end, coordination_cache)
    ordered = sorted(projects.values(), key=lambda p: (not p.waiting, not p.active, -p.last_ts, p.name.lower()))
    assign_slugs(ordered)
    snap = limits.snapshot(limits_result)
    return Report(day=day, generated=end, start=start, end=end, projects=ordered, limits=snap,
                  forecast=limits.forecast(snap), previous_day=previous_day)


def assign_slugs(projects: list[Project]) -> None:
    """Bezpieczne nazwy plików: [a-z0-9._-], bez „index”, bez kropki na początku, unikalne."""
    used: set[str] = set()
    for project in projects:
        base = SLUG_RE.sub("-", project.name.lower()).strip("-.")[:60].strip("-.") or "projekt"
        if base in RESERVED:
            base = f"{base}-projekt"
        slug, n = base, 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        used.add(slug)
        project.slug = slug
