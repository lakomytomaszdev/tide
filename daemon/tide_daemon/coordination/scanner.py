"""Koordynatorzy z ostatnich dni: odczyt dzienników, plany, YouTrack i tablice — bez Qt.

CoordinationScanner trzyma czytniki dzienników między odświeżeniami (przesunięcia w plikach),
więc kolejne `refresh()` czytają tylko dopisane bajty. Pierwszy odczyt dużego koordynatora
(100 MB) trwa poniżej sekundy — usługa robi go w wątku roboczym.

Koordynator:
- Claude Code: `<claude>/projects/<katalog>/<sesja>.jsonl` zmieniony w ostatnich `days` dniach,
  z katalogiem `<sesja>/subagents/` (tani test) i co najmniej jednym zleceniem Agent/Task,
- Codex: wątek główny z `<codex>/sessions/RRRR/MM/DD/` z tego samego okresu, który ma podagentów
  (wątki thread_spawn — także wnuki). Recenzent zgód (guardian) nie jest podagentem.
"""
import os
import time
from datetime import datetime, timedelta, timezone

from ..agents import gitinfo
from . import board as board_mod
from . import claude as claude_mod
from . import codex as codex_mod
from . import ids, plan as plan_mod

DAYS = 7


class CoordinationScanner:
    def __init__(self, claude_root: str, codex_root: str, days: float = DAYS, clock=time.time, tracker=None,
                 titles=None):
        self.claude_projects = os.path.join(claude_root, "projects")
        self.codex_root = codex_root
        self.codex_sessions = os.path.join(codex_root, "sessions")
        self.days = days
        self.clock = clock
        self.tracker = tracker          # YouTrackCache albo None (bez YouTracka)
        self.titles = titles            # funkcja() → {id wątku Codexa: nazwa} albo None
        self.claude_logs: dict[str, claude_mod.ClaudeCoordinatorLog] = {}
        self.codex_logs: dict[str, codex_mod.CodexThreadLog] = {}
        self.coordinators: dict[str, object] = {}   # id → Coordinator (ostatni odczyt)
        self._transcripts: dict = {}
        self._plans: dict = {}

    # --- odczyt ------------------------------------------------------------------------------

    def read(self) -> dict:
        """Doczytuje dzienniki i zwraca {id: Coordinator}."""
        now = self.clock()
        cutoff = now - self.days * 86400
        found: dict[str, object] = {}
        for path in _claude_candidates(self.claude_projects, cutoff):
            log = self.claude_logs.get(path)
            if log is None:
                log = self.claude_logs[path] = claude_mod.ClaudeCoordinatorLog(path)
            log.update()
            if log.coord.runs:
                found[log.coord.id] = log.coord
        for path in list(self.claude_logs):
            if not any(c.path == path for c in found.values()) and not _recent(path, cutoff):
                del self.claude_logs[path]

        threads = {}
        for path in _codex_files(self.codex_sessions, cutoff, now):
            log = self.codex_logs.get(path)
            if log is None:
                log = self.codex_logs[path] = codex_mod.CodexThreadLog(path)
            if log.read_meta() and log.id:
                threads[log.id] = log
        for path in list(self.codex_logs):
            if self.codex_logs[path].id not in threads:
                del self.codex_logs[path]
        children: dict[str, list] = {}
        for log in threads.values():
            if log.parent_id and not log.hidden:
                children.setdefault(log.parent_id, []).append(log)
        titles = None
        for log in threads.values():
            if log.parent_id or log.hidden or log.id not in children:
                continue
            family = _descendants(log.id, children)
            log.update()
            for child in family:
                child.update()
            if titles is None:
                titles = self._codex_titles()
            coord = codex_mod.build(log, family, title=titles.get(log.id, ""))
            if coord.runs:
                found[coord.id] = coord
        self.coordinators = found
        return found

    def refresh(self, live_sessions=(), force_tracker: bool = False) -> list[dict]:
        """Tablice wszystkich koordynatorów (najpierw działające, potem od najświeższej aktywności)."""
        coords = self.read()
        live = {s.get("id"): s for s in live_sessions or ()}
        now = self.clock()
        boards = []
        for coord in coords.values():
            boards.append(self.board(coord, live.get(coord.id), now, force_tracker))
        boards.sort(key=lambda b: (not b["live"], -b["lastActivityTs"]))
        return boards

    def board(self, coord, live, now: float, force_tracker: bool = False) -> dict:
        where = gitinfo.describe(coord.cwd) if coord.cwd else gitinfo.Location("", "", "")
        roots = repo_roots(coord.cwd)
        plans = [self._plan(path) for path in _plan_files(roots)]
        plan = plan_mod.pick([p for p in plans if p is not None], coord.session_id, coord.tool)
        aliases = ids.load_aliases(roots)
        issues, tracker = None, None
        if plan is not None:
            tracker = {"kind": plan.tracker, "url": plan.tracker_url, "project": plan.tracker_project}
            if plan.tracker == "youtrack" and self.tracker is not None:
                issues, tracker = self.tracker.get(plan.tracker_url, plan.tracker_project, force=force_tracker)
        return board_mod.build(coord, plan=plan, aliases=aliases, issues=issues, tracker=tracker, live=live,
                               now=now, info=self._info, project=where.project or os.path.basename(coord.cwd or ""),
                               repo_path=roots[-1] if roots else "")

    def run_detail(self, coord_id: str, run_id: str) -> dict | None:
        """Wynik podagenta i wycinek jego dziennika (przycisk „Pokaż wynik”)."""
        coord = self.coordinators.get(coord_id)
        run = coord.runs.get(run_id) if coord else None
        if run is None:
            return None
        result = next((r.result for r in reversed(run.rounds) if r.result), "")
        if run.tool == "claude":
            if not result and run.transcript:
                result = claude_mod.final_report(run.transcript)
            excerpt = claude_mod.excerpt(run.transcript) if run.transcript else []
        else:
            excerpt = codex_mod.excerpt(run.transcript) if run.transcript else []
        return {
            "coordinator": coord_id, "id": run.id, "description": run.description, "agentType": run.agent_type,
            "label": board_mod._agent_label(run), "model": board_mod._model_name(run.model),
            "role": board_mod.role_of(run),
            "rounds": [{"kind": r.kind, "status": r.status, "startTs": round(r.start, 3), "endTs": round(r.end, 3),
                        "summary": r.summary, "durationMs": r.duration_ms} for r in run.rounds],
            "result": result,
            "excerpt": [dict(item, ts=round(item["ts"], 3)) for item in excerpt],
            "transcript": run.transcript,
        }

    # --- wnętrze ---------------------------------------------------------------------------

    def _info(self, run) -> dict:
        if run.tool == "claude" and run.transcript:
            return claude_mod.transcript_info(run.transcript, self._transcripts)
        try:
            return {"mtime": os.path.getmtime(run.transcript), "branch": run.branch}
        except OSError:
            return {}

    def _plan(self, path: str):
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (path, st.st_size, st.st_mtime)
        if key not in self._plans:
            if len(self._plans) > 256:
                self._plans.clear()
            self._plans[key] = plan_mod.read(path)
        return self._plans[key]

    def _codex_titles(self) -> dict:
        if self.titles is not None:
            try:
                return self.titles() or {}
            except Exception:  # tytuły to tylko ozdoba
                return {}
        return {}


def repo_roots(cwd: str) -> list[str]:
    """Katalog roboczy koordynatora (jego worktree) i główne repozytorium — tam szukamy planu."""
    if not cwd:
        return []
    path = cwd.rstrip("/") or "/"
    top = ""
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            top = path
            break
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    if not top:
        return [cwd] if os.path.isdir(cwd) else []
    roots = [top]
    dotgit = os.path.join(top, ".git")
    if os.path.isfile(dotgit):
        try:
            with open(dotgit, encoding="utf-8") as fh:
                line = fh.readline().strip()
            gitdir = os.path.normpath(os.path.join(top, line[len("gitdir:"):].strip())) if line.startswith("gitdir:") else ""
            common = gitdir
            if gitdir and os.path.isfile(os.path.join(gitdir, "commondir")):
                with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as fh:
                    common = os.path.normpath(os.path.join(gitdir, fh.read().strip()))
            main = os.path.dirname(common) if os.path.basename(common) == ".git" else ""
            if main and main != top and os.path.isdir(main):
                roots.append(main)
        except OSError:
            pass
    return roots


def _plan_files(roots) -> list[str]:
    out, seen = [], set()
    for root in roots:
        folder = os.path.join(root, plan_mod.PLANS_DIR)
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if plan_mod.NAME_RE.match(name) and name not in seen:
                seen.add(name)
                out.append(os.path.join(folder, name))
    return out


def _recent(path: str, cutoff: float) -> bool:
    try:
        return os.path.getmtime(path) >= cutoff
    except OSError:
        return False


def _claude_candidates(projects: str, cutoff: float):
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
            if not e.name.endswith(".jsonl"):
                continue
            try:
                if e.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
            if claude_mod.is_coordinator_candidate(e.path):
                yield e.path


def _codex_files(root: str, cutoff: float, now: float):
    """Pliki wątków z katalogów dni w oknie (plus dzień zapasu na strefę czasową)."""
    start = datetime.fromtimestamp(cutoff, timezone.utc).date() - timedelta(days=1)
    end = datetime.fromtimestamp(now, timezone.utc).date() + timedelta(days=1)
    day = start
    while day <= end:
        folder = os.path.join(root, f"{day.year:04d}", f"{day.month:02d}", f"{day.day:02d}")
        try:
            names = os.listdir(folder)
        except OSError:
            names = []
        for name in names:
            if name.endswith(".jsonl"):
                path = os.path.join(folder, name)
                if _recent(path, cutoff):
                    yield path
        day += timedelta(days=1)


def _descendants(root_id: str, children: dict) -> list:
    out, queue, seen = [], [root_id], {root_id}
    while queue:
        for child in children.get(queue.pop(0), []):
            if child.id not in seen:
                seen.add(child.id)
                out.append(child)
                queue.append(child.id)
    return out
