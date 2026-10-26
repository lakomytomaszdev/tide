"""Syntetyczna historia sesji (fixtures/history) w katalogu tymczasowym — dla testów KRunnera.

Fixtures mają w ścieżkach znacznik @ROOT@, zamieniany przy kopiowaniu na katalog tymczasowy, żeby
katalogi robocze sesji istniały naprawdę (KRunner pomija sesje, których katalogu już nie ma).
Czas modyfikacji każdego pliku sesji to czas jego ostatniego wpisu — tak jak w prawdziwych
dziennikach, więc odcięcie po 30 dniach działa na nich tak samo.

Sesje (NOW = 2026-10-26T12:00:00Z):
  claude:h-titled  alpha, feat/login, 3 dni temu, ai-title „Login form fix” (ostatni z dwóch)
  claude:h-custom  alpha, main, 16 dni temu, custom-title „Release checklist” (ai-title go nie zmienia)
  claude:h-prompt  alpha, feat/parser, wczoraj, bez tytułu → pierwsza wiadomość „Refactor the parser module”
  claude:h-old     alpha, 55 dni temu → poza oknem 30 dni
  claude:h-gone    katalog work/removed nie istnieje
  codex:…101       beta, feat/uploader, 2 dni temu, nazwa wątku z session_index.jsonl „Uploader retries”
  codex:…102       beta (Codex Desktop), wczoraj, tytuł tylko w bazie stanu (gdy test ją zbuduje),
                   inaczej pierwsza wiadomość „Investigate the flaky test”
  codex:…103       wątek podagenta → pomijany
  codex:…104       beta, docs/krunner, 24 dni temu, pierwsza wiadomość po instrukcjach AGENTS.md
  codex:…105       36 dni temu → poza oknem
"""
import json
import os
import shutil
import sqlite3

from . import FIXTURES
from tide_daemon.agents.timeutil import parse_ts

NOW_TEXT = "2026-10-26T12:00:00Z"
NOW = parse_ts(NOW_TEXT)
SOURCE = os.path.join(FIXTURES, "history")


def codex_uid(n: int) -> str:
    return f"0000000b-0000-4000-8000-{n:012d}"


def install(tmp: str) -> dict:
    """Kopiuje historię do tmp. Zwraca ścieżki: claude, codex, alpha, beta, removed."""
    paths = {"claude": os.path.join(tmp, "claude"), "codex": os.path.join(tmp, "codex"),
             "alpha": os.path.join(tmp, "work", "alpha"), "beta": os.path.join(tmp, "work", "beta"),
             "removed": os.path.join(tmp, "work", "removed")}
    for dirpath, _dirs, names in os.walk(SOURCE):
        for name in names:
            src = os.path.join(dirpath, name)
            dst = os.path.join(tmp, os.path.relpath(src, SOURCE))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(src, encoding="utf-8") as fh:
                text = fh.read().replace("@ROOT@", tmp)
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(text)
            last = _last_ts(text)
            if last:
                os.utime(dst, (last, last))
    # Pusty .git: nazwa projektu to nazwa katalogu, niezależnie od tego, co leży nad katalogiem
    # tymczasowym (np. obce repozytorium wyżej w drzewie).
    os.makedirs(os.path.join(paths["alpha"], ".git"))
    os.makedirs(os.path.join(paths["beta"], ".git"))
    return paths


def add_state_db(codex_dir: str, titles: dict, version: int = 5) -> str:
    """Baza stanu aplikacji Codex z tabelą threads (tylko kolumny, które czytamy, i kilka innych)."""
    path = os.path.join(codex_dir, f"state_{version}.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL DEFAULT '', "
                 "cwd TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, first_user_message TEXT NOT NULL DEFAULT '')")
    conn.executemany("INSERT INTO threads (id, title) VALUES (?, ?)", list(titles.items()))
    conn.commit()
    conn.close()
    return path


def append(path: str, records: list[dict], ts: float | None = None) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    if ts:
        os.utime(path, (ts, ts))


def _last_ts(text: str) -> float:
    last = 0.0
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        last = max(last, parse_ts(record.get("timestamp")))
    return last


def remove(tmp: str) -> None:
    shutil.rmtree(tmp, ignore_errors=True)
