"""Rdzeń: katalogi dzienników na wejściu, lista sesji ze stanami na wyjściu.

AgentScanner trzyma stan między odczytami (przesunięcia w plikach), więc usługa czyta tylko
to, co agenci dopisali. Funkcja scan() to jednorazowy przebieg dla testów i trybu --print.
"""
import os

from ..processes import ProcessTable
from .. import windows
from . import gitinfo
from .claude import ClaudeLog, escape_cwd, read_process_status, status_from
from .codex import CodexLog
from .jsonl import JsonlTail
from .state import ENDED, IDLE, MAX_AGE, WAITING, WORKING, SessionLog, derive_state
from .timeutil import iso

STATE_ORDER = {WAITING: 0, WORKING: 1, IDLE: 2, ENDED: 3}


class AgentScanner:
    def __init__(self, claude_root: str, codex_root: str, max_age: float = MAX_AGE):
        self.claude_projects = os.path.join(claude_root, "projects")
        self.claude_status_dir = os.path.join(claude_root, "sessions")
        self.codex_sessions = os.path.join(codex_root, "sessions")
        self.max_age = max_age
        self.logs: dict[str, tuple[SessionLog, JsonlTail]] = {}
        self.claude_status: dict[int, dict] = {}
        self.bound_pids: dict[str, int] = {}  # id sesji → pid z ostatniego sessions()

    # --- pliki -------------------------------------------------------------------------

    def discover(self, now: float) -> bool:
        """Szuka plików zmienionych w ostatniej dobie. Zwraca True, gdy zestaw się zmienił."""
        cutoff = now - self.max_age
        found: dict[str, type] = {}
        for path, mtime in _claude_files(self.claude_projects):
            if mtime >= cutoff:
                found[path] = ClaudeLog
        for path, mtime in _codex_files(self.codex_sessions):
            if mtime >= cutoff:
                found[path] = CodexLog
        changed = False
        for path in list(self.logs):
            if path not in found:
                del self.logs[path]
                changed = True
        for path, cls in found.items():
            if path not in self.logs:
                self.logs[path] = (cls(path), JsonlTail(path))
                changed = True
        return changed

    def update(self, path: str | None = None) -> bool:
        """Doczytuje nowe wpisy (jednego pliku albo wszystkich). Zwraca True, gdy coś przybyło."""
        targets = [path] if path else list(self.logs)
        grew = False
        for p in targets:
            entry = self.logs.get(p)
            if not entry:
                continue
            log, tail = entry
            for record in tail.read_new():
                log.feed(record)
                grew = True
        self.claude_status = read_process_status(self.claude_status_dir)
        return grew

    def watched_files(self) -> list[str]:
        return list(self.logs) + [os.path.join(self.claude_status_dir, f"{pid}.json") for pid in self.claude_status]

    def watched_dirs(self) -> list[str]:
        dirs = [self.claude_projects, self.claude_status_dir, self.codex_sessions]
        dirs += [os.path.dirname(p) for p in self.logs]
        dirs += _recent_codex_dirs(self.codex_sessions)
        return sorted({d for d in dirs if os.path.isdir(d)})

    # --- stan ---------------------------------------------------------------------------

    def sessions(self, now: float, procs: ProcessTable | None = None) -> list[dict]:
        visible = [log for log, _ in self.logs.values()
                   if not log.hidden and log.last_activity and now - log.last_activity <= self.max_age]
        bindings = self._bind(visible, procs)
        self.bound_pids = {}
        out = []
        for log in visible:
            pid = bindings.get(id(log))
            alive = None if procs is None else pid is not None
            status = None
            if log.tool == "claude" and pid is not None:
                data = self.claude_status.get(pid)
                if data and data.get("sessionId") == log.session_id:
                    status = status_from(data)

            def evidence(ts, pid=pid):
                return procs is not None and pid is not None and procs.started_after(pid, ts)

            state, reason, since = derive_state(log, now, alive, status, evidence)
            where = gitinfo.describe(log.cwd, log.branch)
            target = windows.target_for(procs, pid) if procs is not None and pid is not None else None
            sid = f"{log.tool}:{log.session_id}"
            if pid is not None and state != ENDED:
                self.bound_pids[sid] = pid
            out.append({
                "id": sid,
                "tool": log.tool,
                "sessionId": log.session_id,
                "project": where.project,
                "branch": where.branch,
                "worktree": where.worktree,
                "cwd": log.cwd,
                "title": log.title,
                "state": state,
                "reason": reason if state == WAITING else "",
                "since": iso(since),
                "sinceTs": round(since, 3),
                "lastActivity": iso(log.last_activity),
                "lastActivityTs": round(log.last_activity, 3),
                "lastAction": log.last_action,
                "pid": pid or 0,
                "canActivate": bool(target) and state != ENDED,
                "terminal": target["label"] if target else "",
            })
        out.sort(key=lambda s: (STATE_ORDER[s["state"]],
                                s["sinceTs"] if s["state"] == WAITING else -s["lastActivityTs"]))
        return out

    def _bind(self, logs: list[SessionLog], procs: ProcessTable | None) -> dict[int, int]:
        """Przypisuje sesjom procesy: id(log) → pid. Bez migawki procesów — tylko pliki statusu."""
        bound: dict[int, int] = {}
        used: set[int] = set()

        # Claude Code sam zapisuje, który proces prowadzi którą sesję.
        by_session = {}
        for pid, data in self.claude_status.items():
            if procs is None or (procs.get(pid) and procs.get(pid).tool == "claude"):
                by_session[data.get("sessionId")] = pid
        for log in logs:
            if log.tool == "claude" and log.session_id in by_session:
                bound[id(log)] = by_session[log.session_id]
                used.add(by_session[log.session_id])
        if procs is None:
            return bound

        # Codex trzyma otwarty plik dziennika aktywnej sesji.
        for proc in procs.agents("codex"):
            for log in logs:
                if log.tool == "codex" and log.path in proc.open_files:
                    bound[id(log)] = proc.pid
                    used.add(proc.pid)

        # Reszta (starsze wersje, Codex bez otwartego pliku): po katalogu roboczym,
        # najświeższa sesja dostaje proces uruchomiony najpóźniej przed jej początkiem.
        for log in sorted(logs, key=lambda l: -l.last_activity):
            if id(log) in bound:
                continue
            candidates = [p for p in procs.agents(log.tool)
                          if p.pid not in used and not p.open_files and _same_place(log, p.cwd)
                          and p.start_time <= log.last_activity + 5]
            if not candidates:
                continue
            before = [p for p in candidates if p.start_time <= log.first_activity + 5]
            pick = max(before, key=lambda p: p.start_time) if before else min(candidates, key=lambda p: p.start_time)
            bound[id(log)] = pick.pid
            used.add(pick.pid)
        return bound


def scan(claude_root: str, codex_root: str, now: float, procs: ProcessTable | None = None) -> list[dict]:
    """Jednorazowy przebieg: katalogi dzienników → lista sesji ze stanami."""
    scanner = AgentScanner(claude_root, codex_root)
    scanner.discover(now)
    scanner.update()
    return scanner.sessions(now, procs)


def _same_place(log: SessionLog, cwd: str) -> bool:
    if not cwd:
        return False
    if cwd == log.cwd:
        return True
    return isinstance(log, ClaudeLog) and escape_cwd(cwd) == log.project_dir


def _claude_files(projects: str):
    try:
        dirs = [e for e in os.scandir(projects) if e.is_dir()]
    except OSError:
        return
    for d in dirs:
        try:
            entries = list(os.scandir(d.path))
        except OSError:
            continue
        for e in entries:
            # Podkatalogi <id>/subagents/ to dzienniki podagentów — nie są osobnymi sesjami.
            if e.name.endswith(".jsonl") and e.is_file():
                try:
                    yield e.path, e.stat().st_mtime
                except OSError:
                    continue


def _codex_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        if depth >= 3:
            dirnames[:] = []
        for name in filenames:
            if name.endswith(".jsonl"):
                path = os.path.join(dirpath, name)
                try:
                    yield path, os.stat(path).st_mtime
                except OSError:
                    continue


def _recent_codex_dirs(root: str) -> list[str]:
    """Rok, miesiąc i dwa ostatnie dni — tam pojawiają się nowe pliki sesji."""
    out = []
    try:
        years = sorted(e.path for e in os.scandir(root) if e.is_dir())[-1:]
        for y in years:
            months = sorted(e.path for e in os.scandir(y) if e.is_dir())[-1:]
            out += [y] + months
            for m in months:
                out += sorted(e.path for e in os.scandir(m) if e.is_dir())[-2:]
    except OSError:
        pass
    return out
