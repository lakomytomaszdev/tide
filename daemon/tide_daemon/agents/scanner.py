"""Rdzeń: katalogi dzienników na wejściu, lista sesji ze stanami na wyjściu.

AgentScanner trzyma stan między odczytami (przesunięcia w plikach), więc usługa czyta tylko
to, co agenci dopisali. Funkcja scan() to jednorazowy przebieg dla testów i trybu --print.

Podagenci Codexa (wątki thread_spawn) nie są osobnymi pozycjami listy: zwijają się do
najwyższego znanego przodka, który dostaje licznik `subagents` i stan „najpilniejszy” z siebie
i podagentów (czeka > pracuje > skończył). Podagent, którego rodzica nie znamy (np. wątek
główny sprzed doby), zostaje osobną pozycją z `subagent: true`. Przejście do sesji z Codex
Desktop otwiera w aplikacji wątek główny (codex://threads/<id>) — który, pamięta `app_threads`.

Codex może mieć kilka katalogów danych (codex_homes.py: ~/.codex i snap) — czytamy wszystkie,
a ta sama sesja w dwóch katalogach jest jedną pozycją (nowsza aktywność wygrywa).
"""
import os

from ..processes import ProcessTable
from .. import codex_app, codex_homes, windows
from . import gitinfo
from .claude import ClaudeLog, escape_cwd, read_process_status, status_from
from .codex import CodexLog
from .jsonl import JsonlTail
from .state import DONE, ENDED, IDLE, MAX_AGE, WAITING, WORKING, SessionLog, derive_state
from .timeutil import iso

STATE_ORDER = {WAITING: 0, WORKING: 1, DONE: 2, IDLE: 3, ENDED: 4}
MAX_DEPTH = 32  # zabezpieczenie przed pętlą w łańcuchu rodziców
# Sposób przejścia do sesji (pole activation): wątek w aplikacji albo podniesienie okna.
DEEPLINK = "deeplink"
WINDOW = "window"


class AgentScanner:
    def __init__(self, claude_root: str, codex_root, max_age: float = MAX_AGE, codex_app=None):
        """codex_root: katalog danych Codexa albo ich lista (codex_homes.py)."""
        self.claude_projects = os.path.join(claude_root, "projects")
        self.claude_status_dir = os.path.join(claude_root, "sessions")
        self.codex_roots = codex_root
        self.max_age = max_age
        self.logs: dict[str, tuple[SessionLog, JsonlTail]] = {}
        self.claude_status: dict[int, dict] = {}
        self.bound_pids: dict[str, int] = {}  # id sesji → pid z ostatniego sessions()
        self.codex_app = codex_app             # CodexApp (codex_app.py) albo None — bez odnośników
        self.app_threads: dict[str, str] = {}  # id sesji → wątek główny do otwarcia w Codex Desktop

    # --- pliki -------------------------------------------------------------------------

    def codex_log_roots(self) -> tuple:
        """Katalogi sessions/ wszystkich katalogów danych Codexa, bez dowiązań — rozwiązywane przy
        każdym użyciu (snap po odświeżeniu przestawia ~/snap/codex/current na nową rewizję).
        Te same ścieżki idą do processes.probe(log_roots=…), żeby otwarte pliki procesów Codexa
        dało się porównać z plikami sesji."""
        return tuple(codex_homes.sessions_dirs(self.codex_roots))

    def discover(self, now: float) -> bool:
        """Szuka plików zmienionych w ostatniej dobie. Zwraca True, gdy zestaw się zmienił."""
        cutoff = now - self.max_age
        found: dict[str, type] = {}
        for path, mtime in _claude_files(self.claude_projects):
            if mtime >= cutoff:
                found[path] = ClaudeLog
        for root in self.codex_log_roots():
            for path, mtime in _codex_files(root):
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
        roots = self.codex_log_roots()
        dirs = [self.claude_projects, self.claude_status_dir, *roots]
        dirs += [os.path.dirname(p) for p in self.logs]
        for root in roots:
            dirs += _recent_codex_dirs(root)
        return sorted({d for d in dirs if os.path.isdir(d)})

    # --- stan ---------------------------------------------------------------------------

    def sessions(self, now: float, procs: ProcessTable | None = None) -> list[dict]:
        visible = [log for log, _ in self.logs.values()
                   if (not log.hidden or _parent(log)) and log.last_activity
                   and now - log.last_activity <= self.max_age]
        # Ten sam wątek Codexa w dwóch katalogach danych — zostaje plik z nowszą aktywnością.
        visible = codex_homes.newest(visible, key=lambda l: (l.tool, l.session_id) if l.tool == "codex" else id(l),
                                     stamp=lambda l: l.last_activity)
        bindings = self._bind(visible, procs)
        app_ready = self.codex_app is not None and self.codex_app.available()
        threads = {log.session_id: log for log in visible if log.tool == "codex"}
        self.bound_pids = {}
        self.app_threads = {}
        entries: dict[int, dict] = {}
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
            # Codex Desktop: wątek otwiera aplikacja, także bez procesu sesji (zakończony wątek
            # da się przeczytać), a okno podnosimy potem tak jak dotąd.
            desktop = log.tool == "codex" and (codex_app.is_desktop(getattr(log, "originator", ""))
                                               or bool(target and target["label"] == codex_app.LABEL))
            deeplink = desktop and app_ready
            if pid is not None and state != ENDED:
                self.bound_pids[sid] = pid
            if deeplink:
                self.app_threads[sid] = root_thread(log, threads)
            entries[id(log)] = {
                "id": sid,
                "tool": log.tool,
                "sessionId": log.session_id,
                "project": where.project,
                "branch": where.branch,
                "worktree": where.worktree,
                "cwd": log.cwd,
                "title": log.title,
                "state": state,
                "reason": reason if state != ENDED else "",
                "since": iso(since),
                "sinceTs": round(since, 3),
                "lastActivity": iso(log.last_activity),
                "lastActivityTs": round(log.last_activity, 3),
                "lastAction": log.last_action,
                "pid": pid or 0,
                "canActivate": deeplink or (bool(target) and state != ENDED),
                "terminal": codex_app.LABEL if deeplink else (target["label"] if target else ""),
                "activation": DEEPLINK if deeplink else (WINDOW if target and state != ENDED else ""),
                "subagents": 0,
                "subagent": bool(_parent(log)),
                "agentNickname": getattr(log, "nickname", ""),
            }
        out = _fold_subagents(visible, entries, threads, self.bound_pids, self.app_threads)
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
            if id(log) in bound or _parent(log):
                continue  # podagent żyje tylko wtedy, gdy proces trzyma jego dziennik
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


def scan(claude_root: str, codex_root, now: float, procs: ProcessTable | None = None,
         codex_app=None) -> list[dict]:
    """Jednorazowy przebieg: katalogi dzienników → lista sesji ze stanami."""
    scanner = AgentScanner(claude_root, codex_root, codex_app=codex_app)
    scanner.discover(now)
    scanner.update()
    return scanner.sessions(now, procs)


def _parent(log: SessionLog) -> str:
    """Id wątku rodzica dla podagenta Codexa (thread_spawn), inaczej pusty napis."""
    return getattr(log, "parent_id", "") or ""


def _top_known(log: SessionLog, threads: dict) -> SessionLog:
    """Najwyższy znany przodek (albo sam log): idziemy po parent_id, dopóki rodzica znamy."""
    seen = {log.session_id}
    node = log
    for _ in range(MAX_DEPTH):
        parent = threads.get(_parent(node))
        if parent is None or parent.session_id in seen:
            break
        seen.add(parent.session_id)
        node = parent
    return node


def root_thread(log: SessionLog, threads: dict) -> str:
    """Wątek główny do otwarcia w aplikacji (wątek podagenta nie ma w niej własnej rozmowy).

    Łańcuch rodziców po znanych sesjach, dowolnej głębokości. Gdy rodzic najwyższego znanego
    wątku jest nieznany (np. starszy niż doba), bierzemy jego payload.session_id (Codex zapisuje
    tam id wątku głównego), a bez niego — samego nieznanego rodzica.
    """
    top = _top_known(log, threads)
    if not _parent(top):
        return top.session_id
    return getattr(top, "root_hint", "") or _parent(top)


def _fold_subagents(logs, entries: dict, threads: dict, bound_pids: dict, app_threads: dict) -> list[dict]:
    """Zwija podagentów do najwyższego znanego przodka i zwraca pozycje listy.

    Zwinięty podagent nie jest osobnym celem (powiadomienia, Meta+A, karty Konsole) — jego stan
    niesie pozycja przodka, a przejście prowadzi do wątku głównego.
    """
    groups: dict[int, list[dict]] = {}
    for log in logs:
        if _parent(log):
            top = _top_known(log, threads)
            if top is not log:
                groups.setdefault(id(top), []).append(entries[id(log)])
    out = []
    for log in logs:
        if _parent(log) and _top_known(log, threads) is not log:
            sid = entries[id(log)]["id"]
            bound_pids.pop(sid, None)
            app_threads.pop(sid, None)
            continue
        entry = entries[id(log)]
        if id(log) in groups:
            _aggregate(entry, groups[id(log)])
        out.append(entry)
    return out


def _aggregate(entry: dict, children: list[dict]) -> None:
    """Pozycja przodka: stan najpilniejszego członka grupy (czeka > pracuje > skończył >
    bezczynna > zakończona; przy remisie zostaje przodek), z jego powodem, czasem i ostatnią
    czynnością. `subagents` liczy podagentów, którzy jeszcze działają (stan inny niż ended)."""
    entry["subagents"] = sum(1 for c in children if c["state"] != ENDED)
    entry["lastActivityTs"] = max([entry["lastActivityTs"]] + [c["lastActivityTs"] for c in children])
    entry["lastActivity"] = iso(entry["lastActivityTs"])
    child = min(children, key=lambda c: (STATE_ORDER[c["state"]], c["sinceTs"] if c["state"] == WAITING else 0))
    if STATE_ORDER[child["state"]] < STATE_ORDER[entry["state"]]:
        for key in ("state", "reason", "since", "sinceTs", "lastAction"):
            entry[key] = child[key]


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
