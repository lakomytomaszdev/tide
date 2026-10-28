"""Zatrzymanie kroku w tle bez utraty zmian (PT-76).

1. Zatrzymanie programu: SIGTERM do grupy procesów kroku (agentcmd.Background.start() uruchamia go we własnej
   sesji, więc grupa obejmuje też dzieci narzędzi), po `grace` sekundach SIGKILL. Sprawdzone w PT-75:
   `claude -p` na SIGTERM kończy się kodem 143 bez zdarzenia `result`, polecenie narzędzia ginie, pliki
   w katalogu roboczym i dziennik sesji zostają, `--resume` działa. Codex: niesprawdzone (logowanie wygasło).
   Zamiast jednostki `systemd-run --user` zwykły proces: środowisko kroku jest celowo puste (martwy D-Bus),
   a `systemd-run` do rozmowy z menedżerem potrzebuje właśnie D-Bus, `--setenv` wystawiłby zaś środowisko
   (także token punktu) w argv i we właściwościach jednostki.
2. Commit WIP na gałęzi kroku — przez usługę, nie przez program (ten już nie żyje): `git add -A` (także nowe,
   nieśledzone pliki), git z `-c core.hooksPath=/dev/null` (haki z repozytorium nie wykonują się poza
   piaskownicą), bez konfiguracji użytkownika, autor z gitconfigu kroku. Wykluczenia atrap piaskownicy
   (`<krok>/in/ignore`) tylko przez `core.excludesFile` — repozytorium użytkownika nie dostaje wpisów.
   Nie na odłączonym HEAD ani na gałęziach głównych.
3. Ostatnie wiadomości z dziennika sesji: Claude Code — `<projects>/*/<id>.jsonl` (wpisy `assistant` i `user`),
   Codex — najnowszy `rollout-*.jsonl` z `session_meta.cwd` równym katalogowi kroku (wpisy `response_item` typu
   `message`). Format Codexa z kodu czytnika agents/codex.py, nie z żywej sesji (PT-75: bez logowania).
"""
from __future__ import annotations

import glob
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field

from .. import codex_homes
from ..agents.claude import synthetic_user
from ..agents.jsonl import JsonlTail, read_head
from ..worktrees.git import MAIN_CANDIDATES, Git

WIP_MESSAGE = "WIP: step stopped before it finished"
MESSAGE_LIMIT = 3          # ile ostatnich wiadomości zwracamy
MESSAGE_CHARS = 4000       # każda przycięta do tylu znaków


@dataclass
class Snapshot:
    committed: bool = False
    commit: str = ""
    files: int = 0
    error: str = ""         # "", "detached", "protected-branch", "git: <opis>"
    notes: list = field(default_factory=list)


@dataclass
class StopResult(Snapshot):
    stopped: bool = False
    exit_code: int | None = None
    messages: list = field(default_factory=list)   # [{"role": "user"|"assistant", "text": …}], od najstarszej
    session_log: str = ""


# --- zatrzymanie programu ---------------------------------------------------------------------

def _pid_of(proc) -> int:
    return proc.pid if hasattr(proc, "pid") else int(proc)


def _alive(proc) -> bool:
    if hasattr(proc, "poll"):
        return proc.poll() is None
    try:
        with open(f"/proc/{int(proc)}/stat", encoding="utf-8") as fh:
            return fh.read().rpartition(")")[2].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def _signal(pid: int, sig: int) -> None:
    """Sygnał do grupy kroku — tylko gdy proces jest jej liderem (start_new_session); inaczej do samego procesu,
    żeby nigdy nie trafić w grupę usługi."""
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _wait(proc, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while _alive(proc):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def terminate(proc, grace: float = 10.0) -> bool:
    """Zatrzymuje proces kroku (Popen albo pid) i jego grupę. True, gdy lider nie żyje."""
    pid = _pid_of(proc)
    if _alive(proc):
        _signal(pid, signal.SIGTERM)
        if not _wait(proc, grace):
            _signal(pid, signal.SIGKILL)
            _wait(proc, 5.0)
    # Po liderze mogły zostać dzieci narzędzi (ignorujące SIGTERM) — grupa żyje, dopóki ma członków.
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    return not _alive(proc)


# --- commit WIP -------------------------------------------------------------------------------

def _service_git() -> Git:
    git = Git()
    # Bez globalnej konfiguracji użytkownika i systemu (hooki, podpisy, aliasy); potrzebne ustawienia dajemy przez -c.
    git.env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    return git


def git_snapshot(worktree: str, step_dir: str, git: Git | None = None, name: str = "Krok",
                 email: str = "krok@tide.invalid") -> Snapshot:
    """Commit WIP ze wszystkimi zmianami worktree (śledzonymi i nowymi). Nic do zacommitowania — bez commita."""
    git = git or _service_git()
    snap = Snapshot()
    ignore = os.path.join(step_dir, "in", "ignore")
    opts = ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false",
            "-c", f"user.name={name}", "-c", f"user.email={email}"]
    if os.path.isfile(ignore):
        opts += ["-c", f"core.excludesFile={ignore}"]

    def run(*args, timeout=120):
        return git.run(worktree, *opts, *args, timeout=timeout)

    branch = run("symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch.ok or not branch.out.strip():
        snap.error = "detached"
        return snap
    if branch.out.strip() in MAIN_CANDIDATES:
        snap.error = "protected-branch"
        return snap
    gitdir = run("rev-parse", "--absolute-git-dir")
    added = run("add", "-A")
    if not added.ok and "index.lock" in added.err and gitdir.ok:
        # Zatrzymany program mógł zostawić blokadę indeksu (zabity w trakcie `git add`); nikt inny jej nie trzyma —
        # krok nie żyje, a worktree należy do kroku.
        lock = os.path.join(gitdir.out.strip(), "index.lock")
        try:
            os.unlink(lock)
            snap.notes.append("usunięto index.lock zatrzymanego kroku")
        except OSError:
            pass
        added = run("add", "-A")
    if not added.ok:
        snap.error = "git: " + added.err.splitlines()[0] if added.err else "git: add"
        return snap
    changed = run("diff", "--cached", "--name-only", "-z")
    snap.files = len([n for n in changed.out.split("\0") if n]) if changed.ok else 0
    if not snap.files:
        return snap
    done = run("commit", "--no-verify", "-q", "-m", WIP_MESSAGE)
    if not done.ok:
        snap.error = "git: " + (done.err.splitlines()[0] if done.err else "commit")
        return snap
    head = run("rev-parse", "HEAD")
    snap.committed, snap.commit = True, head.out.strip()
    return snap


# --- dziennik sesji ---------------------------------------------------------------------------

def _text_of(content, kinds) -> str:
    if isinstance(content, str):
        return content.strip()
    parts = [str(part.get("text") or "") for part in content or []
             if isinstance(part, dict) and part.get("type") in kinds]
    return "\n".join(parts).strip()


def _claude_messages(records) -> list:
    out = []
    for record in records:
        kind = record.get("type")
        if kind not in ("user", "assistant") or record.get("isSidechain"):
            continue
        if kind == "user" and synthetic_user(record):
            continue
        message = record.get("message") or {}
        text = _text_of(message.get("content"), ("text",))
        if text and not (kind == "user" and text.startswith("<")):
            out.append({"role": kind, "text": text})
    return out


def _codex_messages(records) -> list:
    out = []
    for record in records:
        payload = record.get("payload")
        if record.get("type") != "response_item" or not isinstance(payload, dict) or payload.get("type") != "message":
            continue
        role = payload.get("role")
        text = _text_of(payload.get("content"), ("output_text", "input_text", "text"))
        if role in ("user", "assistant") and text and not text.startswith("<"):
            out.append({"role": role, "text": text})
    return out


def find_claude_log(projects: str, session_id: str) -> str:
    if not projects or not session_id:
        return ""
    found = glob.glob(os.path.join(glob.escape(projects), "*", glob.escape(session_id) + ".jsonl"))
    return max(found, key=lambda p: os.stat(p).st_mtime) if found else ""


def find_codex_log(roots, worktree: str, session_id: str = "") -> str:
    """Najnowszy dziennik Codexa z katalogu kroku (`session_meta.cwd`), w katalogach danych `roots`."""
    files = []
    for folder in codex_homes.sessions_dirs(roots or []):
        files += glob.glob(os.path.join(glob.escape(folder), "**", "rollout-*.jsonl"), recursive=True)
    want = os.path.realpath(worktree)
    for path in sorted(files, key=lambda p: os.stat(p).st_mtime, reverse=True):
        if session_id and session_id not in os.path.basename(path):
            continue
        for record in read_head(path, 64 * 1024):
            payload = record.get("payload")
            if record.get("type") == "session_meta" and isinstance(payload, dict):
                if os.path.realpath(str(payload.get("cwd") or "")) == want:
                    return path
                break
    return ""


def last_messages(path: str, tool: str, limit: int = MESSAGE_LIMIT) -> list:
    if not path:
        return []
    records = JsonlTail(path).read_new()
    messages = _claude_messages(records) if tool == "claude" else _codex_messages(records)
    return [{"role": m["role"], "text": m["text"][:MESSAGE_CHARS]} for m in messages[-limit:]]


# --- całość -----------------------------------------------------------------------------------

def stop_step(proc, *, worktree: str, step_dir: str, tool: str, session_id: str = "", claude_projects: str = "",
              codex_roots=None, git: Git | None = None, grace: float = 10.0, limit: int = MESSAGE_LIMIT) -> StopResult:
    """Zatrzymuje krok, commituje WIP i czyta ostatnie wiadomości. `proc` — Popen z Background.start() albo pid.
    Commit robimy także wtedy, gdy program już nie żył (np. padł) — zmiany w worktree nie mogą zostać bez śladu."""
    stopped = terminate(proc, grace)
    result = StopResult(stopped=stopped, exit_code=getattr(proc, "returncode", None))
    if not stopped:
        result.error = "not-stopped"        # nie dotykamy worktree, w którym program jeszcze pisze
        return result
    snap = git_snapshot(worktree, step_dir, git)
    result.committed, result.commit, result.files = snap.committed, snap.commit, snap.files
    result.error, result.notes = snap.error, snap.notes
    result.session_log = (find_claude_log(claude_projects, session_id) if tool == "claude"
                          else find_codex_log(codex_roots, worktree, session_id))
    result.messages = last_messages(result.session_log, tool, limit)
    return result
