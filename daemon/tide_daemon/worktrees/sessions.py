"""Powiązanie worktrees z sesjami Claude Code i Codexa (best effort, tylko odczyt).

Dla każdego worktree szukamy najświeższej sesji, której katalog roboczy leży w nim (przy
zagnieżdżonych worktrees liczy się najgłębszy). Bierzemy sesje z ostatnich MAX_AGE (30 dni):

- Claude Code: <claude>/projects/<katalog>/<sesja>.jsonl. Nazwa katalogu to ścieżka z każdym
  znakiem spoza [A-Za-z0-9] zamienionym na „-” (`/a/.codex/b` → `-a--codex-b`), więc nie da się
  jej odwrócić — katalog roboczy bierzemy z pola `cwd` wpisów. Tytuł: `custom-title`
  (nadany przez użytkownika), potem `ai-title`, `summary`, a w ostateczności pierwsze polecenie
  użytkownika.
- Codex: <codex>/sessions/RRRR/MM/DD/rollout-*.jsonl, katalog z `session_meta.payload.cwd`
  i `turn_context.payload.cwd` (worktrees Codexa leżą zwykle w ~/.codex/worktrees). Tytuł wątku
  z <codex>/session_index.jsonl (`thread_name`), inaczej pierwsze polecenie użytkownika
  (`event_msg/user_message`). Wątki podagentów pomijamy. Katalogów danych Codexa może być kilka
  (codex_homes.py: ~/.codex i ~/snap/codex/current) — czytamy wszystkie, ta sama sesja w dwóch
  liczy się raz (nowszy wpis wygrywa).

Z każdego pliku czytamy tylko początek i koniec (HEAD_BYTES, TAIL_BYTES), a JSON parsujemy tylko
w liniach, które mogą nieść potrzebne pola. Wynik pliku jest w pamięci do zmiany jego rozmiaru
albo mtime. W pamięci zostają wyłącznie metadane: narzędzie, identyfikator, tytuł (najwyżej
TITLE_LIMIT znaków), katalogi i czas ostatniego wpisu. Treść rozmów nigdzie nie jest zapisywana
ani logowana.

Moduł jest samodzielny (bez zależności od obszaru Agents), żeby dało się go później zastąpić
wspólnym indeksem sesji.
"""
import json
import os
import threading
from dataclasses import dataclass

from .. import codex_homes
from ..agents.claude import shorten
from ..agents.timeutil import parse_ts

MAX_AGE = 30 * 86400
HEAD_BYTES = 192 * 1024
TAIL_BYTES = 192 * 1024
TITLE_LIMIT = 80

CLAUDE_TITLES = {"custom-title": ("customTitle", 3), "ai-title": ("aiTitle", 2), "summary": ("summary", 1)}


@dataclass
class SessionInfo:
    tool: str               # claude albo codex
    session_id: str
    title: str = ""
    cwds: tuple = ()
    last_ts: float = 0.0
    real_cwds: tuple = ()   # cwds bez dowiązań — liczone w wątku roboczym, nie przy każdej liście


@dataclass
class _Parsed:
    """Wynik odczytu jednego pliku (przed dołożeniem tytułów Codexa z indeksu)."""
    tool: str
    session_id: str
    title: str
    prompt: str
    cwds: tuple
    last_ts: float


class SessionIndex:
    def __init__(self, claude_root: str, codex_root, max_age: float = MAX_AGE):
        """codex_root: katalog danych Codexa albo ich lista (codex_homes.py)."""
        self.claude_projects = os.path.join(claude_root, "projects")
        self.codex_roots = codex_root
        self.max_age = max_age
        self._files: dict[str, tuple[int, float, _Parsed | None]] = {}
        self._codex_titles: dict[str, tuple[tuple, dict[str, str]]] = {}  # session_index.jsonl → (znacznik, nazwy)
        self._lock = threading.Lock()
        self.sessions: list[SessionInfo] = []

    def refresh(self, now: float) -> list[SessionInfo]:
        """Doczytuje zmienione pliki z ostatnich max_age. Wołać poza wątkiem D-Bus."""
        cutoff = now - self.max_age
        found = {path: ("claude", st) for path, st in _claude_files(self.claude_projects) if st.st_mtime >= cutoff}
        homes = codex_homes.resolve(self.codex_roots)
        for home in homes:
            found.update({path: ("codex", st) for path, st in _codex_files(os.path.join(home, codex_homes.SESSIONS))
                          if st.st_mtime >= cutoff})
        files = {}
        for path, (tool, st) in found.items():
            old = self._files.get(path)
            if old and old[0] == st.st_size and old[1] == st.st_mtime:
                files[path] = old
                continue
            parsed = read_claude(path) if tool == "claude" else read_codex(path)
            files[path] = (st.st_size, st.st_mtime, parsed)
        self._files = files
        titles = {}
        for home in homes:
            titles.update(self._read_codex_titles(os.path.join(home, "session_index.jsonl")))
        self._codex_titles = {k: v for k, v in self._codex_titles.items()
                              if os.path.dirname(k) in homes}

        sessions = []
        for _size, mtime, parsed in files.values():
            if parsed is None or not parsed.cwds:
                continue
            title = parsed.title or (titles.get(parsed.session_id, "") if parsed.tool == "codex" else "")
            sessions.append(SessionInfo(parsed.tool, parsed.session_id, shorten(title or parsed.prompt, TITLE_LIMIT),
                                        parsed.cwds, parsed.last_ts or mtime,
                                        tuple(os.path.realpath(c) for c in parsed.cwds)))
        # Ta sama sesja Codexa w dwóch katalogach danych — zostaje ta z nowszym wpisem.
        sessions = codex_homes.newest(sessions, key=lambda x: (x.tool, x.session_id) if x.tool == "codex" else id(x),
                                      stamp=lambda x: x.last_ts)
        with self._lock:
            self.sessions = sessions
        return sessions

    def link(self, worktrees) -> dict[str, SessionInfo]:
        """Ścieżka worktree → najświeższa sesja w nim. worktrees: pary (ścieżka, ścieżka bez dowiązań)."""
        with self._lock:
            sessions = list(self.sessions)
        bases = [(path, (real or path).rstrip("/")) for path, real in worktrees]
        out: dict[str, SessionInfo] = {}
        for session in sessions:
            hit = set()
            for real in session.real_cwds or session.cwds:
                best = _deepest(bases, real)
                if best:
                    hit.add(best)
            for path in hit:
                if path not in out or session.last_ts > out[path].last_ts:
                    out[path] = session
        return out

    def _read_codex_titles(self, index: str) -> dict[str, str]:
        """Nazwy wątków z jednego session_index.jsonl; plik bez zmian rozmiaru i mtime nie jest czytany."""
        try:
            st = os.stat(index)
        except OSError:
            self._codex_titles.pop(index, None)
            return {}
        stamp = (st.st_size, st.st_mtime)
        old = self._codex_titles.get(index)
        if old and old[0] == stamp:
            return old[1]
        titles = {}
        try:
            with open(index, "rb") as fh:
                for line in fh:
                    record = _loads(line)
                    if record and record.get("id") and isinstance(record.get("thread_name"), str):
                        titles[str(record["id"])] = record["thread_name"]  # późniejszy wpis wygrywa
        except OSError:
            return old[1] if old else {}
        self._codex_titles[index] = (stamp, titles)
        return titles


def _deepest(bases, real: str) -> str:
    best, best_len = "", -1
    for path, base in bases:
        if (real == base or real.startswith(base + "/")) and len(base) > best_len:
            best, best_len = path, len(base)
    return best


# --- pliki ------------------------------------------------------------------------------

def _claude_files(projects: str):
    try:
        dirs = [e for e in os.scandir(projects) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return
    for d in dirs:
        try:
            entries = list(os.scandir(d.path))
        except OSError:
            continue
        for entry in entries:
            if entry.name.endswith(".jsonl"):
                try:
                    if entry.is_file(follow_symlinks=False):
                        yield entry.path, entry.stat()
                except OSError:
                    continue


def _codex_files(sessions: str):
    for dirpath, _dirs, names in os.walk(sessions):
        for name in names:
            if name.startswith("rollout-") and name.endswith(".jsonl"):
                path = os.path.join(dirpath, name)
                try:
                    yield path, os.stat(path)
                except OSError:
                    continue


def head_and_tail(path: str) -> tuple[list[bytes], list[bytes]]:
    """Pełne linie z początku i z końca pliku (cały plik, gdy jest mały)."""
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size <= HEAD_BYTES + TAIL_BYTES:
                lines = fh.read().split(b"\n")
                return lines, lines
            head = fh.read(HEAD_BYTES).split(b"\n")[:-1]  # ostatnia linia początku jest ucięta
            fh.seek(size - TAIL_BYTES)
            tail = fh.read().split(b"\n")[1:]               # pierwsza linia końca też
            return head, tail
    except OSError:
        return [], []


def _loads(line: bytes):
    line = line.strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    return record if isinstance(record, dict) else None


def _last_ts(lines: list[bytes]) -> float:
    for line in reversed(lines):
        if b'"timestamp"' in line:
            record = _loads(line)
            ts = parse_ts(record.get("timestamp")) if record else 0.0
            if ts:
                return ts
    return 0.0


def read_claude(path: str) -> _Parsed | None:
    head, tail = head_and_tail(path)
    cwds: list[str] = []
    title, rank, prompt = "", 0, ""
    for part in (head, tail) if head is not tail else (head,):
        for line in part:
            has_cwd = b'"cwd"' in line
            has_title = b'-title"' in line or b'"summary"' in line
            wants_prompt = not prompt and b'"type":"user"' in line.replace(b" ", b"")
            if not (has_cwd or has_title or wants_prompt):
                continue
            record = _loads(line)
            if not record or record.get("isSidechain"):
                continue
            kind = record.get("type")
            if kind in CLAUDE_TITLES:
                key, weight = CLAUDE_TITLES[kind]
                value = record.get(key)
                if isinstance(value, str) and value.strip() and weight >= rank:
                    title, rank = value, weight  # w obrębie rodzaju wygrywa najpóźniejszy
                continue
            cwd = record.get("cwd")
            if isinstance(cwd, str) and cwd and cwd not in cwds:
                cwds.append(cwd)
            if not prompt and kind == "user" and not record.get("isMeta"):
                prompt = _prompt_text((record.get("message") or {}).get("content"))
    session_id = os.path.splitext(os.path.basename(path))[0]
    return _Parsed("claude", session_id, title, prompt, tuple(cwds), _last_ts(tail))


def _prompt_text(content) -> str:
    """Pierwsze polecenie użytkownika, bez znaczników poleceń i wyników narzędzi."""
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "")
                break
            if isinstance(item, dict) and item.get("type") == "tool_result":
                return ""
    text = text.strip()
    if not text or text.startswith(("<", "[Request interrupted", "Caveat:")):
        return ""
    return shorten(text, TITLE_LIMIT)


def read_codex(path: str) -> _Parsed | None:
    head, tail = head_and_tail(path)
    cwds: list[str] = []
    session_id, prompt = "", ""
    for part in (head, tail) if head is not tail else (head,):
        for line in part:
            if not (b'"session_meta"' in line or b'"turn_context"' in line
                    or (not prompt and b'"user_message"' in line)):
                continue
            record = _loads(line)
            payload = record.get("payload") if record else None
            if not isinstance(payload, dict):
                continue
            kind = record.get("type")
            if kind == "session_meta":
                source = payload.get("source")
                if isinstance(source, dict) and "subagent" in source:
                    return None  # wątek podagenta albo recenzenta zgód — nie jest osobną sesją
                session_id = str(payload.get("id") or session_id)
            elif kind == "event_msg" and payload.get("type") == "user_message" and not prompt:
                message = payload.get("message")
                prompt = _prompt_text(message) if isinstance(message, str) else ""
                continue
            elif kind != "turn_context":
                continue
            cwd = payload.get("cwd")
            if isinstance(cwd, str) and cwd and cwd not in cwds:
                cwds.append(cwd)
    if not session_id:
        name = os.path.basename(path)[:-len(".jsonl")]
        session_id = name[-36:]
    return _Parsed("codex", session_id, "", prompt, tuple(cwds), _last_ts(tail))
