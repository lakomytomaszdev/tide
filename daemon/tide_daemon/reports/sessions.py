"""Sesje Claude Code i Codexa z ruchem w okresie raportu — tylko odczyt dzienników.

Dla każdej sesji: tytuł, katalog, gałąź, pierwszy i ostatni wpis, stan na koniec okresu, pierwsze
polecenie użytkownika (zadanie — „co robimy”) i ostatnia wiadomość agenta (to ona mówi, „co zrobił”
— bez wołania żadnego modelu). Dla wątków Codexa
prowadzonych przez zaplanowane zadanie (automatyzacja „heartbeat” Codex Desktop) dodatkowo
przebiegi z okresu i ich wyniki.

Czytanie:
- pliki zmienione od początku okresu (mtime), wątki podagentów pomijane,
- stan, tytuł i ostatnia wiadomość — z ogona pliku (TAIL_BYTES) przez parsery obszaru Agents
  (ClaudeLog, CodexLog), początek pliku (HEAD_BYTES) — pierwszy wpis i pierwsze polecenie,
- przebiegi zaplanowanych zadań — od miejsca w pliku, w którym zaczyna się okres (wyszukiwanie
  binarne po znacznikach czasu), JSON parsowany tylko w liniach z początkiem albo końcem tury.

Codex ma kilka katalogów danych (codex_homes.py: ~/.codex i ~/snap/codex/current) — czytamy
wszystkie, a sesja obecna w dwóch katalogach trafia do raportu raz (z nowszym wpisem).

Stan na koniec okresu bierzemy z obszaru Agents (żywe sesje z ostatniej doby), a gdy sesji tam
nie ma — wyprowadzamy go z dziennika jak obszar Agents bez wiedzy o procesach.
"""
import os
import re
import tomllib
from dataclasses import dataclass, field

from .. import codex_homes
from ..agents import gitinfo
from ..agents.claude import ClaudeLog
from ..agents.codex import SCHEDULED_KINDS, CodexLog
from ..agents.jsonl import JsonlTail, _parse_lines, read_head
from ..agents.state import TURN_END, derive_state
from ..agents.timeutil import parse_ts
from ..krunner.index import _read_titles, _state_db, claude_prompt, codex_prompt, short_title
from ..worktrees.sessions import _claude_files, _codex_files

TAIL_BYTES = 2 * 1024 * 1024
HEAD_BYTES = 256 * 1024
MESSAGE_LIMIT = 8000       # tyle znaków ostatniej wiadomości trafia do raportu (reszta ucięta)
PROMPT_LIMIT = 1500        # pierwsze polecenie użytkownika (zadanie sesji)
RUN_LIMIT = 2000           # wynik jednego przebiegu zaplanowanego zadania
SEEK_SLACK = 64 * 1024
TS_RE = re.compile(rb'"timestamp"\s*:\s*"([^"]+)"')
TURN_MARKERS = (b'"task_started"', b'"task_complete"', b'"turn_aborted"', b'user.heartbeat')


@dataclass
class Run:
    """Jeden przebieg zaplanowanego zadania (tura Codexa zaczęta przez automatyzację)."""
    started: float
    ended: float = 0.0
    result: str = ""
    aborted: bool = False


@dataclass
class SessionReport:
    tool: str
    session_id: str
    path: str
    cwd: str = ""
    project: str = ""
    branch: str = ""
    worktree: str = ""
    title: str = ""
    first_ts: float = 0.0
    last_ts: float = 0.0
    state: str = ""            # working, waiting, done, idle, ended
    reason: str = ""
    since: float = 0.0
    last_action: str = ""
    final_message: str = ""
    final_ts: float = 0.0
    prompt: str = ""           # pierwsze polecenie użytkownika w sesji (jedna linia, do PROMPT_LIMIT znaków)
    live: bool = False         # stan z obszaru Agents (sesja z ostatniej doby)
    turn_ended: bool = False   # ostatnia tura skończyła się zwyczajnie (albo ją przerwano)
    runs: list = field(default_factory=list)
    automation: dict = field(default_factory=dict)  # name, status, rrule (gdy wątek ma automatyzację)

    @property
    def id(self) -> str:
        return f"{self.tool}:{self.session_id}"

    @property
    def interrupted(self) -> bool:
        return self.last_action == "Przerwano"


def collect(claude_root: str, codex_root, start: float, end: float,
            live: list[dict] | None = None) -> list[SessionReport]:
    """Sesje z wpisem w [start, end], od najświeższej. codex_root: katalog danych Codexa albo lista."""
    live_by_id = {s.get("id"): s for s in live or ()}
    titles = codex_titles(codex_root)
    automations = read_automations(codex_root)
    out = []
    claude_projects = os.path.join(claude_root, "projects")
    for path, st in _claude_files(claude_projects):
        if st.st_mtime >= start:
            report = read_session(path, ClaudeLog, start, end)
            if report:
                out.append(report)
    codex = []
    for root in codex_homes.sessions_dirs(codex_root):
        for path, st in _codex_files(root):
            if st.st_mtime >= start:
                report = read_session(path, CodexLog, start, end, automations)
                if report:
                    if not report.title:
                        report.title = titles.get(report.session_id, "")
                    codex.append(report)
    out += codex_homes.newest(codex, key=lambda r: r.session_id, stamp=lambda r: r.last_ts)
    places: dict[str, gitinfo.Location] = {}
    for report in out:
        if not report.title:
            report.title = "(bez tytułu)"
        where = places.get(report.cwd)
        if where is None:
            where = places[report.cwd] = gitinfo.describe(report.cwd, report.branch)
        report.project = where.project
        report.branch = report.branch or where.branch
        report.worktree = where.worktree
        state = live_by_id.get(report.id)
        if state:
            report.state, report.reason, report.live = state.get("state", report.state), state.get("reason", ""), True
            report.since = float(state.get("sinceTs") or report.since)
        if report.state == "idle" and report.turn_ended:
            # W raporcie „bezczynna” po skończonej turze to po prostu „skończył” — liczy się wynik,
            # nie to, że od 30 min nic się nie dzieje.
            report.state = "done"
    out.sort(key=lambda r: -r.last_ts)
    return out


def read_session(path: str, cls, start: float, end: float, automations: dict | None = None) -> SessionReport | None:
    log = cls(path)
    tail = JsonlTail(path, TAIL_BYTES)
    records = tail.read_new()
    head = read_head(path, HEAD_BYTES)
    final, final_ts = "", 0.0
    for record in records:
        log.feed(record)
        if log.hidden:
            return None  # wątek podagenta Codexa
        text, ts = final_message(record, cls is ClaudeLog)
        if text:
            final, final_ts = text, ts
    if not log.last_activity or log.last_activity < start or not log.cwd:
        return None
    first = _first_ts(head) or log.first_activity
    report = SessionReport(tool=log.tool, session_id=log.session_id, path=path, cwd=log.cwd, branch=log.branch,
                           title=short_title(log.title), first_ts=first, last_ts=log.last_activity,
                           last_action=log.last_action, final_message=_clip(final, MESSAGE_LIMIT), final_ts=final_ts)
    prompt_of = claude_prompt if cls is ClaudeLog else codex_prompt
    report.prompt = next((p for p in (prompt_of(r, PROMPT_LIMIT) for r in head) if p), "")
    if not report.title:
        report.title = short_title(report.prompt)
    state, reason, since = derive_state(log, end, None, None, lambda _ts: False)
    report.state, report.reason, report.since = state, reason, since
    report.turn_ended = log.phase == TURN_END
    if cls is CodexLog:
        report.automation = (automations or {}).get(log.session_id, {})
        report.runs = scheduled_runs(path, start, end)
    return report


def final_message(record: dict, claude: bool) -> tuple[str, float]:
    """Tekst wiadomości agenta z wpisu (albo pusty) i jej czas."""
    ts = parse_ts(record.get("timestamp"))
    if claude:
        if record.get("type") != "assistant" or record.get("isSidechain") or record.get("isApiErrorMessage"):
            return "", 0.0
        content = (record.get("message") or {}).get("content")
        if isinstance(content, str):
            return content.strip(), ts
        parts = [str(c.get("text") or "") for c in content or () if isinstance(c, dict) and c.get("type") == "text"]
        return "\n\n".join(p.strip() for p in parts if p.strip()), ts
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return "", 0.0
    kind, item = record.get("type"), payload.get("type")
    if kind == "event_msg" and item == "task_complete" and isinstance(payload.get("last_agent_message"), str):
        return payload["last_agent_message"].strip(), ts
    if kind == "event_msg" and item == "agent_message" and isinstance(payload.get("message"), str):
        return payload["message"].strip(), ts
    if kind == "response_item" and item == "message" and payload.get("role") == "assistant" \
            and payload.get("phase") in (None, "final_answer"):
        parts = [str(c.get("text") or "") for c in payload.get("content") or ()
                 if isinstance(c, dict) and c.get("type") == "output_text"]
        return "\n\n".join(p.strip() for p in parts if p.strip()), ts
    return "", 0.0


# --- zaplanowane zadania Codexa -------------------------------------------------------------

def read_automations(codex_root) -> dict[str, dict]:
    """id wątku → {name, status, rrule} z <codex>/automations/*/automation.toml (tylko „heartbeat”)
    każdego katalogu danych Codexa."""
    out = {}
    for home in codex_homes.resolve(codex_root):
        out.update(_read_automations(os.path.join(home, "automations")))
    return out


def _read_automations(root: str) -> dict[str, dict]:
    out = {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        path = os.path.join(root, name, "automation.toml")
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        thread = data.get("target_thread_id")
        if isinstance(thread, str) and thread:
            out[thread] = {"name": str(data.get("name") or name), "status": str(data.get("status") or ""),
                           "rrule": str(data.get("rrule") or ""), "kind": str(data.get("kind") or "")}
    return out


def scheduled_runs(path: str, start: float, end: float) -> list[Run]:
    """Tury zaczęte przez automatyzację w [start, end] z wynikiem (ostatnia wiadomość agenta)."""
    runs: list[Run] = []
    current: Run | None = None
    scheduled = False
    try:
        fh = open(path, "rb")
    except OSError:
        return runs
    with fh:
        fh.seek(seek_since(fh, start))
        if fh.tell():
            fh.readline()  # wyrównanie do początku linii
        for line in fh:
            if not any(marker in line for marker in TURN_MARKERS):
                continue
            records = _parse_lines([line])
            if not records:
                continue
            record = records[0]
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            ts = parse_ts(record.get("timestamp"))
            kind, item = record.get("type"), payload.get("type")
            if kind == "event_msg" and item == "task_started":
                attribution = payload.get("turn_attribution")
                trigger = attribution.get("turn_trigger") if isinstance(attribution, dict) else None
                scheduled = isinstance(trigger, str) and trigger.startswith("automation")
                current = Run(started=ts) if start <= ts <= end else None
            elif kind == "response_item" and item == "message" and payload.get("role") == "user":
                meta = payload.get("internal_chat_message_metadata_passthrough")
                kinds = meta.get("content_item_kinds") if isinstance(meta, dict) else None
                if isinstance(kinds, list) and SCHEDULED_KINDS & {k for k in kinds if isinstance(k, str)}:
                    scheduled = True
            elif kind == "event_msg" and item in ("task_complete", "turn_aborted"):
                if current is not None and scheduled:
                    current.ended = ts
                    current.aborted = item == "turn_aborted"
                    message = payload.get("last_agent_message")
                    current.result = _clip(message.strip(), RUN_LIMIT) if isinstance(message, str) else ""
                    runs.append(current)
                current, scheduled = None, False
            if ts and ts > end:
                break
    if current is not None and scheduled:
        runs.append(current)  # przebieg trwa
    return runs


def seek_since(fh, ts: float) -> int:
    """Przybliżone przesunięcie w pliku JSONL, od którego wpisy mają czas ≥ ts (wyszukiwanie
    binarne po polu timestamp; dzienniki rosną w czasie). Zwraca miejsce trochę wcześniejsze."""
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    lo, hi = 0, size
    while hi - lo > SEEK_SLACK:
        mid = (lo + hi) // 2
        fh.seek(mid)
        fh.readline()
        found = None
        for _ in range(8):  # kilka linii, bo nie każda ma timestamp
            line = fh.readline()
            if not line:
                break
            match = TS_RE.search(line[:4096])
            if match:
                found = parse_ts(match.group(1).decode("ascii", "replace"))
                break
        if found is None or found >= ts:
            hi = mid
        else:
            lo = mid
    return max(0, lo - SEEK_SLACK)


def codex_titles(codex_root) -> dict[str, str]:
    """Nazwy wątków Codexa ze wszystkich katalogów danych: session_index.jsonl (nadane przez
    użytkownika) wygrywa z bazą stanu aplikacji."""
    homes = codex_homes.resolve(codex_root)
    titles: dict[str, str] = {}
    for home in homes:
        db = _state_db(home)
        if db:
            titles.update(_read_titles(db) or {})
    for home in homes:
        index = JsonlTail(os.path.join(home, "session_index.jsonl"), tail_bytes=None)
        for record in index.read_new():
            tid, name = record.get("id"), record.get("thread_name")
            if isinstance(tid, str) and isinstance(name, str) and name.strip():
                titles[tid] = short_title(name)
    return titles


def _first_ts(records: list[dict]) -> float:
    for record in records:
        ts = parse_ts(record.get("timestamp"))
        if not ts and isinstance(record.get("payload"), dict):
            ts = parse_ts(record["payload"].get("timestamp"))
        if ts:
            return ts
    return 0.0


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
