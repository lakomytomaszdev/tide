"""Koordynatorzy z ostatnich dni: odczyt dzienników, plany, YouTrack i tablice — bez Qt.

CoordinationScanner trzyma czytniki dzienników między odświeżeniami (przesunięcia w plikach),
więc kolejne `refresh()` czytają tylko dopisane bajty. Pierwszy odczyt dużego koordynatora
(100 MB) trwa poniżej sekundy — usługa robi go w wątku roboczym.

Koordynator:
- Claude Code: `<claude>/projects/<katalog>/<sesja>.jsonl` zmieniony w ostatnich `days` dniach,
  z katalogiem `<sesja>/subagents/` (tani test) i co najmniej jednym zleceniem Agent/Task,
- Codex: wątek główny z `<codex>/sessions/RRRR/MM/DD/` z tego samego okresu, który ma podagentów
  (wątki thread_spawn — także wnuki). Recenzent zgód (guardian) nie jest podagentem.
  Katalog dnia to dzień ZAŁOŻENIA wątku, więc długo żyjący koordynator (np. założony dwa tygodnie
  temu, a dziś wciąż zlecający) leży poza oknem — rodzica podagenta z okna szukamy wtedy po ID
  w nazwie pliku (`rollout-…-<id>.jsonl`) we wszystkich dniach i w `archived_sessions`.
  Katalogów danych Codexa może być kilka (codex_homes.py: ~/.codex i ~/snap/codex/current) —
  czytamy wszystkie; kopię tego samego pliku z drugiego katalogu pomijamy (nowszy plik wygrywa).

Tablica dostaje też `warnings` — kontrolę numerów ticketów (numbering.py) na planach ze wszystkich
worktree i gałęzi repozytorium (repoplans.py), rezerwacjach numerów (reservations.py) i liście projektów
YouTracka (tylko GET, raz na godzinę).
"""
import os
import re
import time
from datetime import datetime, timedelta, timezone

from .. import codex_homes
from ..agents import gitinfo
from ..board.place import work_cwd
from . import board as board_mod
from . import claude as claude_mod
from . import codex as codex_mod
from . import ids, plan as plan_mod, repoplans

DAYS = 7
MISSING_RETRY = 600.0   # s, po tylu znów szukamy rodzica, którego pliku nie było
BACKLOG_DIR = os.path.join("docs", "agents", "backlog")
BACKLOG_NAME = re.compile(r"^([A-Za-z]{1,10})-(\d{1,6})\.md$")
BACKLOG_HEADING = re.compile(r"^#\s+[A-Za-z]{1,10}-\d{1,6}\s*[:—–-]\s*(\S.*?)\s*$")
BACKLOG_STATUS = re.compile(r"^\*\*Status:?\*\*:?\s*(.+)$")
BACKLOG_FILES = 400     # tyle plików backlogu czytamy najwyżej (nagłówki, nie całość)


class CoordinationScanner:
    def __init__(self, claude_root: str, codex_root, days: float = DAYS, clock=time.time, tracker=None,
                 titles=None, repo_plans=None, registry=None):
        """codex_root: katalog danych Codexa albo ich lista (codex_homes.py)."""
        self.claude_projects = os.path.join(claude_root, "projects")
        self.codex_roots = codex_root
        self.days = days
        self.clock = clock
        self.tracker = tracker          # YouTrackCache albo None (bez YouTracka)
        self.titles = titles            # funkcja() → {id wątku Codexa: nazwa} albo None
        self.claude_logs: dict[str, claude_mod.ClaudeCoordinatorLog] = {}
        self.codex_logs: dict[str, codex_mod.CodexThreadLog] = {}
        self.coordinators: dict[str, object] = {}   # id → Coordinator (ostatni odczyt)
        self._transcripts: dict = {}
        self._plans: dict = {}
        self._backlog: dict = {}                    # ścieżka → ((rozmiar, mtime), wpis)
        self._codex_paths: dict[str, str] = {}      # id wątku → plik (rodzice spoza okna)
        self._codex_missing: dict[str, float] = {}  # id wątku → kiedy go nie znaleziono
        self.repo_plans = repo_plans or repoplans.RepoPlans(clock=clock)
        self.registry = registry        # rejestr rezerwacji numerów (reservations.py) albo None — domyślny

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
        roots = codex_homes.sessions_dirs(self.codex_roots)
        paths = [path for root in roots for path in _codex_files(root, cutoff, now)]
        # mtime liczy się tylko przy kopii tego samego pliku w drugim katalogu danych.
        for path in codex_homes.unique_files(paths, roots, path=lambda p: p, stamp=_mtime):
            log = self.codex_logs.get(path)
            if log is None:
                log = self.codex_logs[path] = codex_mod.CodexThreadLog(path)
            if log.read_meta() and log.id:
                threads[log.id] = log
        self._add_codex_parents(threads, now)
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

    def _add_codex_parents(self, threads: dict, now: float) -> None:
        """Dokłada do `threads` rodziców (i dalszych przodków) spoza okna dni."""
        pending = [log.parent_id for log in threads.values() if log.parent_id and not log.hidden]
        while pending:
            tid = pending.pop()
            if tid in threads:
                continue
            path = self._codex_thread_path(tid, now)
            if not path:
                continue
            log = self.codex_logs.get(path)
            if log is None:
                log = self.codex_logs[path] = codex_mod.CodexThreadLog(path)
            if log.read_meta() and log.id == tid:
                threads[tid] = log
                if log.parent_id:
                    pending.append(log.parent_id)

    def _codex_thread_path(self, tid: str, now: float) -> str:
        path = self._codex_paths.get(tid)
        if path and os.path.isfile(path):
            return path
        if now - self._codex_missing.get(tid, -MISSING_RETRY) < MISSING_RETRY:
            return ""
        path = next((p for p in (_find_codex_thread(home, tid) for home in codex_homes.resolve(self.codex_roots))
                     if p), "")
        if path:
            self._codex_paths[tid] = path
            self._codex_missing.pop(tid, None)
        else:
            self._codex_missing[tid] = now
        return path

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
        # Koordynator, który wszedł do repozytorium tablicy Tide (pisze plan w <tablica>/docs/<projekt>/),
        # nadal pracuje w swoim katalogu startu — tam szukamy repozytorium i planu (PT-28).
        cwd = work_cwd(coord.cwd, getattr(coord, "launch_cwd", ""))
        where = gitinfo.describe(cwd) if cwd else gitinfo.Location("", "", "")
        branch = coord.branch if cwd == coord.cwd else (where.branch or coord.branch)
        roots = repo_roots(cwd) if cwd and os.path.isdir(cwd) else []
        if not roots:
            # Katalog koordynatora zniknął (np. usunięty worktree Codexa) — repozytorium z katalogów
            # podagentów, od najczęstszego (szukanie w górę od nieistniejącego katalogu mogłoby trafić
            # w obce repozytorium wyżej).
            counts: dict[str, int] = {}
            for run in coord.runs.values():
                if run.cwd and run.cwd != cwd:
                    counts[run.cwd] = counts.get(run.cwd, 0) + 1
            for cwd in sorted(counts, key=lambda c: -counts[c]):
                roots = repo_roots(cwd) if os.path.isdir(cwd) else []
                if roots:
                    break
        if not roots:
            roots = repo_roots(cwd)
        plans = [self._plan(path) for path in _plan_files(roots)]
        plan = plan_mod.pick([p for p in plans if p is not None], coord.session_id, coord.tool, branch or "")
        aliases = ids.load_aliases(roots)
        backlog = self._backlog_entries(roots, aliases)
        issues, tracker = None, None
        if plan is not None:
            tracker = {"kind": plan.tracker, "url": plan.tracker_url, "project": plan.tracker_project}
            if plan.tracker == "youtrack" and self.tracker is not None:
                issues, tracker = self.tracker.get(plan.tracker_url, plan.tracker_project, force=force_tracker)
        return board_mod.build(coord, plan=plan, aliases=aliases, issues=issues, tracker=tracker, live=live,
                               now=now, info=self._info, project=where.project or os.path.basename(cwd or ""),
                               repo_path=roots[-1] if roots else "", backlog=backlog,
                               warnings=self.warnings(roots, aliases), cwd=cwd, work_branch=branch)

    def warnings(self, roots, aliases=None) -> list[dict]:
        """Ostrzeżenia o numerach ticketów w repozytorium (numbering.conflicts); błąd — pusta lista."""
        try:
            return repoplans.check(roots, self.repo_plans, self.tracker, aliases or {}, self.registry)
        except Exception as exc:  # kontrola to dodatek — tablica ma się pokazać i bez niej
            print(f"tide-daemon: koordynacja: kontrola numerów: {exc!r}", flush=True)
            return []

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

    def _backlog_entries(self, roots, aliases) -> dict:
        """Tytuły ticketów z lokalnego backlogu (`docs/agents/backlog/LA-040.md`, pierwsza linia
        „# LA-040: tytuł”, linia „**Status:** …”) — {klucz: {"title", "status"}}; najpierw worktree."""
        out: dict = {}
        for root in roots:
            folder = os.path.join(root, BACKLOG_DIR)
            try:
                names = sorted(n for n in os.listdir(folder) if BACKLOG_NAME.match(n))[:BACKLOG_FILES]
            except OSError:
                continue
            for name in names:
                match = BACKLOG_NAME.match(name)
                key = ids.key_of(match.group(1), match.group(2))
                key = aliases.get(key, key)
                if key in out:
                    continue
                entry = self._backlog_entry(os.path.join(folder, name))
                if entry:
                    out[key] = entry
        return out

    def _backlog_entry(self, path: str) -> dict | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        stamp = (st.st_size, st.st_mtime)
        cached = self._backlog.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
        entry = None
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096).splitlines()
        except OSError:
            head = []
        for i, line in enumerate(head[:3]):
            match = BACKLOG_HEADING.match(line)
            if match:
                entry = {"title": match.group(1)[:200], "status": ""}
                for rest in head[i + 1:i + 12]:
                    status = BACKLOG_STATUS.match(rest.strip())
                    if status:
                        entry["status"] = " ".join(status.group(1).replace("`", "").split())[:300]
                        break
                break
        if len(self._backlog) > 2000:
            self._backlog.clear()
        self._backlog[path] = (stamp, entry)
        return entry

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
    """Pliki planów repozytorium: docs/agents/plans worktree i głównego katalogu, potem katalog
    projektu w tablicy (`ai-docs: tide`, standard 1.1)."""
    out, seen = [], set()
    for folder in plan_mod.plan_folders(roots):
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


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _find_codex_thread(codex_root: str, tid: str) -> str:
    """Plik wątku po ID w jednym katalogu danych: najpierw najnowsze dni w sessions/, potem archived_sessions/."""
    if not tid or "/" in tid or tid.startswith("."):
        return ""
    suffix = f"-{tid}.jsonl"
    sessions = os.path.join(codex_root, "sessions")

    def listdir(folder):
        try:
            return sorted(os.listdir(folder), reverse=True)
        except OSError:
            return []
    for year in listdir(sessions):
        for month in listdir(os.path.join(sessions, year)):
            for day in listdir(os.path.join(sessions, year, month)):
                folder = os.path.join(sessions, year, month, day)
                for name in listdir(folder):
                    if name.endswith(suffix):
                        return os.path.join(folder, name)
    archived = os.path.join(codex_root, "archived_sessions")
    for name in listdir(archived):
        if name.endswith(suffix):
            return os.path.join(archived, name)
    return ""


def _descendants(root_id: str, children: dict) -> list:
    out, queue, seen = [], [root_id], {root_id}
    while queue:
        for child in children.get(queue.pop(0), []):
            if child.id not in seen:
                seen.add(child.id)
                out.append(child)
                queue.append(child.id)
    return out
