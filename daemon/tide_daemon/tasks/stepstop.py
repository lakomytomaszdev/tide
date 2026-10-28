"""Zatrzymanie kroku w tle bez utraty zmian (PT-76).

1. Zatrzymanie programu: krok działa w jednostce `systemd-run --user --scope` (agentcmd.Background.start()), więc
   zatrzymanie to `systemctl --user kill --signal=SIGTERM <jednostka>.scope`, po `grace` sekundach SIGKILL do całej
   cgroup (także wnuki po `setsid`), potem sprawdzenie, że jednostka jest nieaktywna. Sprawdzone w PT-75: `claude -p`
   na SIGTERM kończy się kodem 143 bez zdarzenia `result`, polecenie narzędzia ginie, pliki w katalogu roboczym
   i dziennik sesji zostają, `--resume` działa. Codex: niesprawdzone (logowanie wygasło). Bez jednostki (zapas: sam
   pid) sygnał dostaje tylko żywy proces — jego grupa, gdy jest liderem — nigdy grupa martwego pidu (pid mógł
   zostać użyty ponownie).
2. Commit WIP na gałęzi kroku — przez usługę, nie przez program (ten już nie żyje). Git usługi działa POZA piaskownicą,
   więc nie wolno mu ufać niczemu, co agent mógł zmienić (`step_git`): jawne `--git-dir=<repo>/.git/worktrees/<wt>`
   (ustalony przez usługę przed krokiem, nie z pliku `.git` w worktree), `--work-tree`, `GIT_COMMON_DIR`, bez
   konfiguracji użytkownika i systemu, `core.hooksPath=/dev/null`, bez atrybutów systemowych i użytkownika, plik
   `config.worktree` (agent ma zapis w `.git/worktrees/<wt>`, a git czyta go przy `extensions.worktreeConfig`, czego `-c`
   nie wyłącza — sprawdzone) odsunięty na czas gita. Gałąź: `symbolic-ref HEAD` musi być `refs/heads/<branch>` (agent
   mógł przestawić HEAD), nie odłączony i nie główna. `git add -A` (także nowe pliki) i commit; wykluczenia atrap
   piaskownicy (`<krok>/in/ignore`) tylko przez `core.excludesFile`. Pozostałe po zabitym kroku blokady (`index.lock`,
   `HEAD.lock`, `refs/heads/<gałąź>.lock`) usuwane dopiero po sprawdzeniu, że program nie żyje.
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
    if proc is None:
        return False
    if hasattr(proc, "poll"):
        return proc.poll() is None
    try:
        with open(f"/proc/{int(proc)}/stat", encoding="utf-8") as fh:
            return fh.read().rpartition(")")[2].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(args, 1, b"", b"")


def unit_active(unit: str) -> bool:
    return _systemctl("is-active", "--quiet", unit + ".scope").returncode == 0


def _wait(still_running, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while still_running():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def _terminate_unit(proc, unit: str, grace: float) -> bool:
    if unit_active(unit):
        _systemctl("kill", "--kill-whom=all", "--signal=SIGTERM", unit + ".scope")
        if not _wait(lambda: unit_active(unit), grace):
            _systemctl("kill", "--kill-whom=all", "--signal=SIGKILL", unit + ".scope")
            _wait(lambda: unit_active(unit), 5.0)
    if hasattr(proc, "wait"):
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return not unit_active(unit)


def _terminate_pid(proc, grace: float) -> bool:
    """Zapas bez jednostki: sygnały tylko do żywego procesu (jego grupy, gdy jest jej liderem)."""
    pid = _pid_of(proc)

    def send(sig):
        if not _alive(proc):
            return
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, sig)
            else:
                os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    send(signal.SIGTERM)
    if not _wait(lambda: _alive(proc), grace):
        send(signal.SIGKILL)
        _wait(lambda: _alive(proc), 5.0)
    return not _alive(proc)


def terminate(proc=None, unit: str = "", grace: float = 10.0) -> bool:
    """Zatrzymuje krok: jednostkę (cała cgroup), a bez niej proces `proc` (Popen albo pid). True, gdy nic nie żyje."""
    if unit:
        return _terminate_unit(proc, unit, grace)
    return proc is None or _terminate_pid(proc, grace)


# --- commit WIP -------------------------------------------------------------------------------

@dataclass
class Snapshot:
    committed: bool = False
    commit: str = ""
    files: int = 0
    error: str = ""         # "", detached, protected-branch, wrong-branch, git: <opis>
    notes: list = field(default_factory=list)


class StepGit:
    """Git usługi dla worktree kroku — poza piaskownicą, więc bez zaufania do tego, co agent mógł zmienić."""

    def __init__(self, repo: str, git_dir: str, worktree: str, step_dir: str, git: Git | None = None):
        self.worktree, self.git_dir = worktree, git_dir
        self.git = git or Git()
        # Bez konfiguracji użytkownika i systemu; wspólny katalog gita jawnie (plik `commondir` jest zapisywalny).
        self.git.env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_ATTR_NOSYSTEM="1",
                            GIT_COMMON_DIR=os.path.join(repo, ".git"))
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            self.git.env.pop(name, None)
        ignore = os.path.join(step_dir, "in", "ignore")
        self.opts = ["--git-dir=" + git_dir, "--work-tree=" + worktree,
                     "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false",
                     "-c", "core.attributesFile=/dev/null"]
        if os.path.isfile(ignore):
            self.opts += ["-c", f"core.excludesFile={ignore}"]

    def run(self, *args, timeout=120):
        return self.git.run(self.worktree, *self.opts, *args, timeout=timeout)


def _clear_stale_locks(git_dir: str, repo: str, branch: str) -> list:
    removed = []
    for lock in (os.path.join(git_dir, "index.lock"), os.path.join(git_dir, "HEAD.lock"),
                 os.path.join(repo, ".git", "refs", "heads", *branch.split("/")) + ".lock"):
        try:
            os.unlink(lock)
            removed.append(os.path.basename(lock))
        except OSError:
            pass
    return removed


def git_snapshot(repo: str, git_dir: str, worktree: str, step_dir: str, branch: str, git: Git | None = None,
                 stale_locks: bool = False, name: str = "Krok", email: str = "krok@tide.invalid") -> Snapshot:
    """Commit WIP ze wszystkimi zmianami worktree (śledzonymi i nowymi) na gałęzi `branch`. Nic do zacommitowania —
    bez commita. `stale_locks`: usuń blokady zabitego kroku (tylko gdy wiadomo, że program nie żyje)."""
    snap = Snapshot()
    if branch in MAIN_CANDIDATES:
        snap.error = "protected-branch"
        return snap
    step_git = StepGit(repo, git_dir, worktree, step_dir, git)
    step_git.opts += ["-c", f"user.name={name}", "-c", f"user.email={email}"]
    aside = os.path.join(git_dir, "config.worktree")
    parked = aside + ".tide-aside"
    moved = False
    try:
        try:
            os.rename(aside, parked)          # git czyta ten plik przy extensions.worktreeConfig; pisze go agent
            moved = True
        except OSError:
            pass
        head = step_git.run("symbolic-ref", "--quiet", "HEAD")
        if not head.ok or not head.out.strip():
            snap.error = "detached"
            return snap
        if head.out.strip() != f"refs/heads/{branch}":
            snap.error = "wrong-branch"
            return snap
        if stale_locks:
            removed = _clear_stale_locks(git_dir, repo, branch)
            if removed:
                snap.notes.append("usunięto blokady zatrzymanego kroku: " + ", ".join(removed))
        added = step_git.run("add", "-A")
        if not added.ok:
            snap.error = "git: " + (added.err.splitlines()[0] if added.err else "add")
            return snap
        changed = step_git.run("diff", "--cached", "--name-only", "-z")
        snap.files = len([n for n in changed.out.split("\0") if n]) if changed.ok else 0
        ignored = step_git.run("status", "--ignored", "--porcelain", "-z")
        skipped = [e[3:] for e in ignored.out.split("\0") if e.startswith("!! ")] if ignored.ok else []
        if skipped:
            snap.notes.append(f"pominięte przez .gitignore ({len(skipped)}): " + ", ".join(skipped[:10]))
        if not snap.files:
            return snap
        done = step_git.run("commit", "--no-verify", "-q", "-m", WIP_MESSAGE)
        if not done.ok:
            snap.error = "git: " + (done.err.splitlines()[0] if done.err else "commit")
            return snap
        sha = step_git.run("rev-parse", "HEAD")
        snap.committed, snap.commit = True, sha.out.strip()
        return snap
    finally:
        if moved:
            try:
                if os.path.exists(aside):
                    os.unlink(parked)         # agent zdążył napisać nowy — zostaje nowy, odsunięty był starszy
                else:
                    os.rename(parked, aside)
            except OSError:
                pass


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

def stop_step(proc=None, *, unit: str = "", worktree: str, step_dir: str, repo: str, git_dir: str, branch: str,
              tool: str, session_id: str = "", claude_projects: str = "", codex_roots=None, git: Git | None = None,
              grace: float = 10.0, limit: int = MESSAGE_LIMIT) -> StopResult:
    """Zatrzymuje krok, commituje WIP i czyta ostatnie wiadomości. `unit` (Background.unit) i/lub `proc` (Popen z
    Background.start() albo pid; None, gdy usługa się zrestartowała i zna tylko jednostkę). `git_dir` i `repo`
    z Background (ustalone przed krokiem), `branch` — gałąź kroku, na której ma wylądować commit.
    Commit robimy także wtedy, gdy program już nie żył — zmiany w worktree nie mogą zostać bez śladu."""
    stopped = terminate(proc, unit, grace)
    result = StopResult(stopped=stopped, exit_code=getattr(proc, "returncode", None))
    if not stopped:
        result.error = "not-stopped"        # nie dotykamy worktree, w którym program jeszcze pisze
        return result
    snap = git_snapshot(repo, git_dir, worktree, step_dir, branch, git, stale_locks=True)
    result.committed, result.commit, result.files = snap.committed, snap.commit, snap.files
    result.error, result.notes = snap.error, snap.notes
    result.session_log = (find_claude_log(claude_projects, session_id) if tool == "claude"
                          else find_codex_log(codex_roots, worktree, session_id))
    result.messages = last_messages(result.session_log, tool, limit)
    return result
