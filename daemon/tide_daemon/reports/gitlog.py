"""Commity z okresu raportu w jednym repozytorium, pogrupowane po gałęziach — tylko lokalny git,
bez sieci (te same zmienne środowiska co obszar worktrees: worktrees/git.py).

Grupowanie:
- gałąź główna (master, main, develop): commity z okresu osiągalne z niej,
- każda inna lokalna gałąź: commity z okresu jeszcze poza gałęzią główną (`<gałąź> --not <główna>`).
  Gałąź założona z innej gałęzi zawiera jej commity — każdy commit pokazujemy raz, przy gałęzi
  z najmniejszą liczbą commitów (czyli tej, w której powstał),
- worktree z odłączonym HEAD: commity z okresu, których nie ma w żadnej gałęzi.

Gałęzie, których czubek jest starszy niż początek okresu, pomijamy bez pytania gita o log.
Gałąź, której wszystkie commity z okresu są też w większej gałęzi poza główną, dostaje
`merged_into` (scalona do gałęzi integracyjnej). Commit pamięta ścieżki zmienionych plików
(`--numstat`) — z nich „Na daily” liczy obszary zmian i odróżnia pracę od porządków.

Kto zrobił commit (pole `agent`): `claude` albo `codex`, gdy wskazuje na to stopka
Co-Authored-By albo adres autora, gałąź/worktree agenta (prefiks `codex/`, `claude/`, katalog
`~/.codex/worktrees`, `.claude/worktrees`) albo commit powstał w czasie sesji agenta pracującej
w tym katalogu. Inaczej pusty — raport nie twierdzi wtedy, że to „ty”, tylko nie ma znacznika.
"""
import os
import re
from dataclasses import dataclass, field

from ..worktrees.git import Git
from ..worktrees.naming import guess_tool, humanize_branch

FIELD = "\x1f"
RECORD = "\x1e"
FORMAT = RECORD + FIELD.join(["%H", "%an", "%ae", "%ct", "%P", "%s", "%(trailers:key=Co-authored-by,valueonly,separator=%x1d)"])
SUBJECT_LIMIT = 200
SESSION_SLACK = 5 * 60  # commit tuż po ostatnim wpisie sesji też jest jej (agent commituje na końcu)
PATHS_MAX = 200


@dataclass
class Commit:
    sha: str
    author: str
    email: str
    ts: float
    subject: str
    merge: bool = False
    files: int = 0
    added: int = 0
    deleted: int = 0
    agent: str = ""        # claude, codex albo pusty
    agent_why: str = ""    # trailer, author, branch, session
    parents: tuple = ()
    paths: list = field(default_factory=list)   # zmienione pliki z --numstat (najwyżej PATHS_MAX)


@dataclass
class BranchLog:
    name: str              # gałąź albo „odłączony HEAD <skrót>”
    branch: str            # nazwa gałęzi (pusta przy odłączonym HEAD)
    is_main: bool = False
    worktree: str = ""     # ścieżka worktree, w którym gałąź jest wybrana (albo pusta)
    is_main_checkout: bool = False
    tool: str = ""         # narzędzie z położenia/prefiksu worktree
    title: str = ""
    commits: list = field(default_factory=list)
    # Scalenia w gałęzi głównej: [{"sha", "branch", "members": [sha, …]}] — commity, które weszły
    # danym scaleniem (raport „Na daily” grupuje po nich pracę zamiast wypisywać tematy commitów).
    merges: list = field(default_factory=list)
    # Gałąź (poza główną), w której są już wszystkie commity tej gałęzi z okresu — np. gałąź ticketu
    # scalona do gałęzi integracyjnej; pusty, gdy takiej nie ma.
    merged_into: str = ""

    @property
    def added(self) -> int:
        return sum(c.added for c in self.commits)

    @property
    def deleted(self) -> int:
        return sum(c.deleted for c in self.commits)


def parse_log(text: str) -> list[Commit]:
    """Wynik `git log --format=FORMAT --numstat`."""
    out = []
    for chunk in text.split(RECORD):
        if not chunk.strip():
            continue
        header, _, stats = chunk.partition("\n")
        parts = header.split(FIELD)
        if len(parts) < 7:
            continue
        sha, author, email, ts, parents, subject, trailers = parts[:7]
        if not ts.isdigit():
            continue
        commit = Commit(sha=sha, author=author, email=email, ts=float(ts), subject=_clip(subject, SUBJECT_LIMIT),
                        merge=len(parents.split()) > 1, parents=tuple(parents.split()))
        for line in stats.splitlines():
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            commit.files += 1
            if len(commit.paths) < PATHS_MAX:
                commit.paths.append(cols[2])
            commit.added += int(cols[0]) if cols[0].isdigit() else 0
            commit.deleted += int(cols[1]) if cols[1].isdigit() else 0
        tool = tool_from_text(trailers.replace("\x1d", " ") + " " + email)
        if tool:
            commit.agent, commit.agent_why = tool, "trailer" if tool_from_text(trailers) else "author"
        out.append(commit)
    return out


def tool_from_text(text: str) -> str:
    low = (text or "").lower()
    if "claude" in low or "anthropic" in low:
        return "claude"
    if "codex" in low or "openai" in low or "chatgpt" in low:
        return "codex"
    return ""


def repo_commits(git: Git, repo_path: str, main_branch: str, start: float, end: float,
                 worktrees: list[dict] = (), sessions: list = ()) -> list[BranchLog]:
    """Gałęzie z commitami z okresu [start, end]. worktrees: wpisy jak z /Worktrees List() tego
    repozytorium; sessions: SessionReport (cwd, first_ts, last_ts, tool) do rozpoznania agenta."""
    by_branch = {w.get("branch"): w for w in worktrees if w.get("branch")}
    tips = _branch_tips(git, repo_path)
    since = int(start) - 1
    candidates = []
    for branch, tip_ts in tips.items():
        if tip_ts < start:
            continue
        if branch == main_branch:
            args = [f"refs/heads/{branch}"]
        elif main_branch:
            args = [f"refs/heads/{branch}", "--not", f"refs/heads/{main_branch}"]
        else:
            args = [f"refs/heads/{branch}"]
        commits = [c for c in _log(git, repo_path, since, args) if start <= c.ts <= end]
        if commits:
            candidates.append((branch, commits))

    # Odłączone HEAD-y w worktrees: commity, których nie ma w żadnej gałęzi.
    for w in worktrees:
        if w.get("detached") and w.get("head") and not w.get("missing"):
            commits = [c for c in _log(git, w["path"], since, ["HEAD", "--not", "--branches"]) if start <= c.ts <= end]
            if commits:
                candidates.append(("", commits, w))

    # Gałąź scalona do innej gałęzi poza główną: jej commity z okresu zawierają się w tamtej.
    # Przy łańcuchu gałęzi (każda założona z poprzedniej, wszystkie scalone do integracyjnej)
    # wskazujemy największą — gałąź integracyjną, a nie następne ogniwo.
    shas = {item[0]: {c.sha for c in item[1]} for item in candidates if item[0] and item[0] != main_branch}
    merged_into = {}
    for name, own_shas in shas.items():
        holders = [other for other, other_shas in shas.items()
                   if other != name and len(other_shas) > len(own_shas) and own_shas <= other_shas]
        if holders:
            merged_into[name] = max(holders, key=lambda other: len(shas[other]))

    # Każdy commit raz: najpierw gałąź główna, potem od najmniejszej liczby commitów.
    candidates.sort(key=lambda c: (c[0] != main_branch or not c[0], len(c[1])))
    seen: set[str] = set()
    out: list[BranchLog] = []
    for item in candidates:
        branch, commits = item[0], item[1]
        wt = item[2] if len(item) > 2 else by_branch.get(branch, {})
        own = [c for c in commits if c.sha not in seen]
        seen.update(c.sha for c in own)
        if not own:
            continue
        tag, title = humanize_branch(branch)
        log = BranchLog(name=branch or f"odłączony HEAD {wt.get('head', '')}", branch=branch,
                        is_main=bool(branch) and branch == main_branch, worktree=wt.get("path", ""),
                        is_main_checkout=bool(wt.get("isMain")),
                        tool=guess_tool(wt.get("path", ""), tag) if wt or tag else "", title=title, commits=own,
                        merged_into=merged_into.get(branch, ""))
        _mark_agents(log, sessions)
        if log.is_main:
            log.merges = _merges(git, repo_path, log.commits)
        out.append(log)
    out.sort(key=lambda b: (not b.is_main, -max(c.ts for c in b.commits)))
    return out


def _mark_agents(log: BranchLog, sessions) -> None:
    where = os.path.realpath(log.worktree) if log.worktree else ""
    for commit in log.commits:
        if commit.agent:
            continue
        if log.tool:
            commit.agent, commit.agent_why = log.tool, "branch"
            continue
        if not where:
            continue
        for s in sessions:
            cwd = os.path.realpath(s.cwd) if s.cwd else ""
            if not cwd or not (cwd == where or cwd.startswith(where + "/")):
                continue
            if s.first_ts - 60 <= commit.ts <= s.last_ts + SESSION_SLACK:
                commit.agent, commit.agent_why = s.tool, "session"
                break


MERGE_SUBJECTS = (
    re.compile(r"^Merge (?:remote-tracking )?branch '(?:origin/)?([^']+)'"),
    re.compile(r"^Merge pull request #\d+ from [^/\s]+/(\S+)"),
    re.compile(r"^Merge branch \"([^\"]+)\""),
)
MERGE_MEMBERS = 500


def merged_branch(subject: str) -> str:
    """Nazwa scalonej gałęzi z tematu commita scalenia („Merge branch 'feat/x'”) albo pusty napis."""
    for pattern in MERGE_SUBJECTS:
        match = pattern.match(subject or "")
        if match:
            return match.group(1)
    return ""


def _merges(git: Git, repo_path: str, commits: list) -> list[dict]:
    """Dla każdego scalenia z okresu: commity drugiego rodzica, których nie ma w pierwszym."""
    out = []
    for commit in commits:
        if not commit.merge or len(commit.parents) < 2:
            continue
        res = git.run(repo_path, "rev-list", f"--max-count={MERGE_MEMBERS}",
                      f"{commit.parents[0]}..{commit.parents[1]}", "--")
        members = res.out.split() if res.ok else []
        out.append({"sha": commit.sha, "branch": merged_branch(commit.subject), "members": members})
    return out


def _branch_tips(git: Git, repo_path: str) -> dict[str, float]:
    res = git.run(repo_path, "for-each-ref", "--format=%(refname:short)" + FIELD + "%(committerdate:unix)",
                  "refs/heads")
    tips = {}
    for line in res.out.splitlines() if res.ok else ():
        name, _, ts = line.partition(FIELD)
        if name and ts.strip().isdigit():
            tips[name] = float(ts)
    return tips


def _log(git: Git, cwd: str, since: int, revs: list[str]) -> list[Commit]:
    res = git.run(cwd, "-c", "core.quotepath=off", "log", f"--since=@{since}", f"--format={FORMAT}", "--numstat",
                  "--no-renames", *revs, "--")
    return parse_log(res.out) if res.ok else []


def status_files(git: Git, path: str, limit: int = 40) -> tuple[list[tuple[str, str]], int]:
    """Niezacommitowane zmiany: ([(kod, ścieżka)], liczba wszystkich). Bez index.lock (GIT_OPTIONAL_LOCKS)."""
    res = git.run(path, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
    if not res.ok:
        return [], -1
    items = res.out.split("\0")
    out, i = [], 0
    while i < len(items):
        item = items[i]
        i += 1
        if len(item) < 4:
            continue
        code, rel = item[:2], item[3:]
        out.append((code.strip() or "?", rel))
        if "R" in code or "C" in code:
            i += 1
    return out[:limit], len(out)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
