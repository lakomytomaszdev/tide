"""Indeks historii sesji agentów dla KRunnera: Claude Code i Codex z ostatnich 30 dni.

Obszar Agents śledzi tylko sesje z ostatniej doby i ich stan. Tu potrzebujemy dłuższej historii,
ale tylko małych metadanych każdej sesji: id, narzędzie, katalog, projekt, gałąź, tytuł
(skrócony do TITLE_LIMIT znaków) i czas ostatniej aktywności. Treść rozmów nie jest
przechowywana — z pierwszej wiadomości użytkownika zostaje najwyżej skrócony tytuł.

Czytanie jest przyrostowe i korzysta z tych samych parserów co obszar Agents (ClaudeLog,
CodexLog, JsonlTail): przy pierwszym odczycie pliku bierzemy nagłówek (HEAD_BYTES — tam jest
pierwsza wiadomość) i ogon (ostatni 1 MB — tam jest najnowszy tytuł i gałąź), a przy kolejnych
tylko dopisane bajty. Plik bez zmian rozmiaru i czasu modyfikacji nie jest w ogóle otwierany.

Tytuł:
- Claude Code: custom-title (nadany przez użytkownika), inaczej ostatni ai-title, inaczej
  pierwsza wiadomość użytkownika,
- Codex: nazwa wątku z <CODEX_HOME>/session_index.jsonl (nadana przez użytkownika), inaczej
  tytuł wątku z bazy stanu aplikacji (state_<n>.sqlite, tylko odczyt, gdy jest), inaczej
  pierwsza wiadomość użytkownika.

Codex ma kilka katalogów danych (codex_homes.py: ~/.codex i ~/snap/codex/current) — dzienniki,
nazwy wątków i bazy stanu czytamy ze wszystkich, kopię tego samego pliku z drugiego katalogu
pomijamy, a segmenty jednego wątku (to samo id) są jedną pozycją z najnowszego segmentu. Pole `source` to plik dziennika: wznowienie sesji ze snapa
uruchamia Codexa ze snapa (worktrees/launchers.py), bo tylko on widzi swój katalog.

Indeks żyje wyłącznie w pamięci usługi — nic nie trafia na dysk ani poza komputer.
"""
import glob
import os
import re
import sqlite3
import threading
import time

from ..agents import gitinfo
from ..agents.claude import ClaudeLog
from ..agents.codex import CodexLog
from ..agents.jsonl import JsonlTail, read_head
from ..agents.scanner import _claude_files, _codex_files
from .. import codex_homes
from ..codex_app import is_desktop

HISTORY_AGE = 30 * 24 * 60 * 60
HEAD_BYTES = 256 * 1024
TITLE_LIMIT = 100

# Wiadomości „użytkownika”, które w rzeczywistości są wstawkami narzędzia (polecenia, kontekst,
# instrukcje) — nie nadają się na tytuł.
_NOT_A_PROMPT = ("<", "[Request interrupted", "Caveat:", "# AGENTS.md", "# CLAUDE.md")


def short_title(text: str, limit: int = TITLE_LIMIT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _prompt_text(text, limit: int = TITLE_LIMIT) -> str:
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if not text or text.startswith(_NOT_A_PROMPT):
        return ""
    return short_title(text, limit)


def claude_prompt(record: dict, limit: int = TITLE_LIMIT) -> str:
    """Pierwsza wiadomość użytkownika z wpisu Claude Code albo pusty napis (skrócona do limit znaków)."""
    if record.get("type") != "user" or record.get("isMeta") or record.get("isSidechain"):
        return ""
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return ""
        content = next((c.get("text") for c in content if isinstance(c, dict) and c.get("type") == "text"), "")
    return _prompt_text(content, limit)


def codex_prompt(record: dict, limit: int = TITLE_LIMIT) -> str:
    """Wiadomość użytkownika z wpisu Codexa (CLI albo aplikacja) albo pusty napis."""
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return ""
    kind = record.get("type")
    if kind == "event_msg" and payload.get("type") == "user_message":
        return _prompt_text(payload.get("message"), limit)
    if kind == "event_msg" and payload.get("type") == "item_completed":
        item = payload.get("item") or {}
        if isinstance(item, dict) and item.get("type") == "UserMessage":
            return _first_text(item.get("content"), "text", limit)
    if kind == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
        return _first_text(payload.get("content"), "input_text", limit)
    return ""


def _first_text(content, kind: str, limit: int = TITLE_LIMIT) -> str:
    for item in content if isinstance(content, list) else ():
        if isinstance(item, dict) and item.get("type") == kind:
            text = _prompt_text(item.get("text"), limit)
            if text:
                return text
    return ""


class _Entry:
    """Jeden plik sesji: parser z obszaru Agents, czytnik przyrostowy i pierwsza wiadomość."""

    def __init__(self, path: str, cls):
        self.log = cls(path)
        self.tail = JsonlTail(path)
        self.prompt = ""
        self.stamp = (-1, -1.0)  # (rozmiar, mtime) z ostatniego odczytu

    def read(self, prompt_of) -> None:
        if self.tail.offset == 0:
            try:
                size = os.path.getsize(self.log.path)
            except OSError:
                return
            if size > self.tail.tail_bytes:
                # Duży plik: JsonlTail weźmie tylko pierwszą linię i ogon, więc pierwsza
                # wiadomość użytkownika musi przyjść z osobnego odczytu początku.
                head = read_head(self.log.path, HEAD_BYTES)
                if not self.prompt:
                    self.prompt = next((p for p in map(prompt_of, head) if p), "")
        records = self.tail.read_new()
        for record in records:
            self.log.feed(record)
            if not self.prompt and not self.log.hidden:
                self.prompt = prompt_of(record)
        self.stamp = (self.tail.size, self.tail.mtime)


class SessionIndex:
    """Metadane sesji z ostatnich max_age sekund. refresh() jest ciężkie — wołać w tle."""

    def __init__(self, claude_root: str, codex_root, max_age: float = HISTORY_AGE, clock=time.time):
        """codex_root: katalog danych Codexa albo ich lista (codex_homes.py)."""
        self.claude_projects = os.path.join(claude_root, "projects")
        self.codex_roots = codex_root
        self.max_age = max_age
        self.clock = clock
        self._files: dict[str, _Entry] = {}
        self._thread_names: dict[str, JsonlTail] = {}  # katalog danych → czytnik session_index.jsonl
        self.codex_names: dict[str, str] = {}    # id wątku → nazwa nadana przez użytkownika
        self.codex_titles: dict[str, str] = {}   # id wątku → tytuł z bazy stanu aplikacji
        self._db_titles: dict[str, tuple] = {}   # katalog danych → (znacznik bazy, {id wątku: tytuł})
        self._places: dict[str, gitinfo.Location] = {}
        self._entries: list[dict] = []
        self._lock = threading.Lock()
        self.built = False
        self.last_refresh = 0.0

    def entries(self) -> list[dict]:
        """Sesje od najświeższej. Tania migawka z ostatniego refresh()."""
        with self._lock:
            return list(self._entries)

    def refresh(self) -> int:
        """Szuka plików z ostatnich max_age i doczytuje tylko zmienione. Zwraca liczbę odczytanych."""
        now = self.clock()
        cutoff = now - self.max_age
        found = {}
        for path, mtime in _claude_files(self.claude_projects):
            if mtime >= cutoff:
                found[path] = (ClaudeLog, mtime)
        homes = codex_homes.resolve(self.codex_roots)
        roots = [os.path.join(home, codex_homes.SESSIONS) for home in homes]
        codex = [(path, mtime) for root in roots for path, mtime in _codex_files(root)]
        for path, mtime in codex_homes.unique_files(codex, roots):
            if mtime >= cutoff:
                found[path] = (CodexLog, mtime)
        for path in list(self._files):
            if path not in found:
                del self._files[path]

        read = 0
        for path, (cls, mtime) in found.items():
            entry = self._files.get(path)
            if entry is None:
                entry = self._files[path] = _Entry(path, cls)
            elif entry.log.hidden:
                continue  # wątek podagenta — po pierwszej linii wiadomo, że go nie pokazujemy
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if entry.stamp == (size, mtime):
                continue
            entry.read(claude_prompt if cls is ClaudeLog else codex_prompt)
            read += 1

        self._read_thread_names(homes)
        self._read_thread_titles(homes)
        self._places = {}  # gałąź i projekt mogły się zmienić — liczone od nowa, raz na katalog
        entries = [e for e in (self._describe(entry) for entry in self._files.values()) if e]
        entries = [e for e in entries if e["lastActivity"] >= cutoff]
        # Jeden wątek = jedno dopasowanie: segmenty wątku Codexa (to samo id) — najnowszy.
        entries = codex_homes.newest(entries, key=lambda e: e["id"], stamp=lambda e: e["lastActivity"])
        entries.sort(key=lambda e: -e["lastActivity"])
        with self._lock:
            self._entries = entries
        self.built = True
        self.last_refresh = now
        return read

    # --- wnętrze ---------------------------------------------------------------------------

    def _describe(self, entry: _Entry) -> dict | None:
        log = entry.log
        if log.hidden or not log.cwd or not log.session_id:
            return None
        where = self._places.get(log.cwd)
        if where is None:
            where = self._places[log.cwd] = gitinfo.describe(log.cwd)
        if log.tool == "claude":
            title = log.title
        else:
            title = self.codex_names.get(log.session_id) or self.codex_titles.get(log.session_id) or ""
        return {
            "id": f"{log.tool}:{log.session_id}",
            "tool": log.tool,
            "sessionId": log.session_id,
            "cwd": log.cwd,
            "project": where.project,
            # Gałąź z chwili sesji (z dziennika), a dopiero bez niej bieżąca z katalogu.
            "branch": log.branch or where.branch,
            "title": short_title(title) or entry.prompt,
            "lastActivity": log.last_activity or entry.stamp[1],
            # Wątek z Codex Desktop — Uruchom otwiera go w aplikacji zamiast `codex resume` w Konsoli.
            "desktop": log.tool == "codex" and is_desktop(getattr(log, "originator", "")),
            # Plik dziennika — po nim wznowienie wybiera program (Codex ze snapa czyta tylko swój katalog).
            "source": log.path,
        }

    def _read_thread_names(self, homes: list[str]) -> None:
        for home in list(self._thread_names):
            if home not in homes:
                del self._thread_names[home]  # np. stara rewizja snapa po odświeżeniu
        for home in homes:
            tail = self._thread_names.get(home)
            if tail is None:
                tail = self._thread_names[home] = JsonlTail(os.path.join(home, "session_index.jsonl"),
                                                            tail_bytes=None)
            for record in tail.read_new():
                tid, name = record.get("id"), record.get("thread_name")
                if isinstance(tid, str) and isinstance(name, str) and name.strip():
                    self.codex_names[tid] = short_title(name)

    def _read_thread_titles(self, homes: list[str]) -> None:
        """Tytuły wątków Codexa z baz stanu wszystkich katalogów danych — najlepszy możliwy wysiłek,
        tylko odczyt. Baza bez zmian od ostatniego odczytu nie jest otwierana."""
        wanted = None
        for home in homes:
            db = _state_db(home)
            if not db:
                self._db_titles.pop(home, None)
                continue
            stamp = (db,) + tuple(_mtime(p) for p in (db, db + "-wal"))
            if self._db_titles.get(home, (None,))[0] == stamp:
                continue
            if wanted is None:
                wanted = {e.log.session_id for e in self._files.values()
                          if e.log.tool == "codex" and not e.log.hidden}
            titles = _read_titles(db, wanted)
            if titles is not None:  # inna wersja schematu albo baza zajęta — zostają poprzednie tytuły
                self._db_titles[home] = (stamp, titles)
        for home in list(self._db_titles):
            if home not in homes:
                del self._db_titles[home]
        merged: dict[str, str] = {}
        for home in homes:
            merged.update(self._db_titles.get(home, (None, {}))[1])
        self.codex_titles = merged


def _read_titles(db: str, wanted: set | None = None) -> dict[str, str] | None:
    """id wątku → tytuł z tabeli threads bazy stanu Codexa (tylko odczyt); None przy błędzie bazy."""
    titles = {}
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        try:
            for tid, title in conn.execute("SELECT id, title FROM threads WHERE title != ''"):
                if isinstance(tid, str) and isinstance(title, str) and (wanted is None or tid in wanted):
                    titles[tid] = short_title(title)
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return titles


def _state_db(codex_root: str) -> str:
    """Najnowsza baza stanu Codexa (state_<wersja>.sqlite) albo pusty napis."""
    def version(path):
        match = re.search(r"state_(\d+)\.sqlite$", path)
        return int(match.group(1)) if match else -1
    found = [p for p in glob.glob(os.path.join(codex_root, "state_*.sqlite")) if version(p) >= 0]
    return max(found, key=version) if found else ""


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0
