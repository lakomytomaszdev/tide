"""Zatrzymanie kroku w tle bez utraty zmian (PT-76).

1. Zatrzymanie programu: krok działa w jednostce `systemd-run --user --scope` (agentcmd.Background.start()), więc
   zatrzymanie to `systemctl --user kill --signal=SIGTERM <jednostka>.scope`, po `grace` sekundach SIGKILL do całej
   cgroup (także wnuki po `setsid`), potem sprawdzenie, że jednostka jest nieaktywna. Sprawdzone w PT-75: `claude -p`
   na SIGTERM kończy się kodem 143 bez zdarzenia `result`, polecenie narzędzia ginie, pliki w katalogu roboczym
   i dziennik sesji zostają, `--resume` działa. Codex: niesprawdzone (logowanie wygasło). Bez jednostki (zapas: sam
   pid) sygnał dostaje tylko żywy proces — jego grupa, gdy jest liderem — nigdy grupa martwego pidu (pid mógł
   zostać użyty ponownie).
2. Commit WIP na gałęzi kroku — przez usługę, nie przez program (ten już nie żyje). Git usługi działa na treści napisanej
   przez agenta (filtry, `.gitattributes`, `fsmonitor`, zagnieżdżone repozytoria), więc działa W PIASKOWNICY `bwrap`
   (decyzja 8, specyfikacja przepływu kart), nie poza nią: `--ro-bind / /`, pusty `tmpfs` na `$HOME` i `/tmp`, zapis tylko
   w worktree oraz w `.git/worktrees/<wt>`, `objects`, `refs`, `logs` (katalog gita worktree ustalony przez usługę
   przed krokiem, nie z pliku `.git`), reszta `<repo>/.git` do odczytu, bez sieci, IPC i cudzych procesów, czyste
   środowisko, `GIT_CONFIG_GLOBAL` kroku. Gdy `bwrap` nie działa: `error="no-sandbox"`, zmiany zostają w worktree —
   nigdy cichego przejścia na git bez piaskownicy. Tańsze zabezpieczenia zostają jako druga warstwa: jawne `--git-dir`
   i `--work-tree`, `GIT_COMMON_DIR` (plik `commondir` jest zapisywalny), `core.hooksPath=/dev/null`, bez atrybutów
   systemu i użytkownika, bez `maintenance`, `gc` i `untrackedCache`. Gałąź: `symbolic-ref HEAD` musi być
   `refs/heads/<branch>` (agent mógł przestawić HEAD), nie odłączony i nie główna. `git add -A` obejmuje nowe pliki,
   ale zagnieżdżone repozytoria (katalog z `.git` albo wyglądający na katalog gita) są pomijane z notatką — gitlink
   zgubiłby ich treść, a wciągnięcie `gd/` wpisałoby cudzą konfigurację do commita; bity `--assume-unchanged` i
   `--skip-worktree` na plikach, które istnieją na dysku, są zdejmowane (z notatką — żadnej cichej utraty zmian).
   Wykluczenia atrap piaskownicy (`<krok>/in/ignore`) tylko przez `core.excludesFile`. Pozostałe po zabitym kroku
   blokady (`index.lock`, `HEAD.lock`, `refs/heads/<gałąź>.lock`) usuwane dopiero po sprawdzeniu, że krok nie żyje.
3. Ostatnie wiadomości z dziennika sesji: Claude Code — `<projects>/*/<id>.jsonl` (wpisy `assistant` i `user`),
   Codex — najnowszy `rollout-*.jsonl` z `session_meta.cwd` równym katalogowi kroku (wpisy `response_item` typu
   `message`). Format Codexa z kodu czytnika agents/codex.py, nie z żywej sesji (PT-75: bez logowania).
"""
from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

from .. import codex_homes
from ..agents.claude import synthetic_user
from ..agents.jsonl import JsonlTail, read_head
from ..worktrees.git import MAIN_CANDIDATES, Result
from ..gitcommand import git_argv
from .agentcmd import EXIT_CODE_FILE

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


def unit_state(unit: str) -> str:
    """"active", "inactive" (jednostki nie ma albo jest nieaktywna: kody 3 i 4 `systemctl is-active`) albo "unknown"
    (błąd samego systemctl, np. brak magistrali, timeout) — z "unknown" nie wolno wnosić, że krok się zatrzymał."""
    code = _systemctl("is-active", "--quiet", unit + ".scope").returncode
    return "active" if code == 0 else "inactive" if code in (3, 4) else "unknown"


def freeze_unit(unit: str, frozen: bool) -> bool:
    """Zamrożenie (`frozen`) albo odmrożenie całej cgroup kroku (`systemctl --user freeze|thaw`, systemd ≥ 246):
    procesy kroku z potomkami stoją, dopóki Tide nie skończy własnego działania (strażnik opiekuna, PT-158).
    `systemctl freeze` wraca po zamrożeniu. True — udało się."""
    if not unit:
        return False
    return _systemctl("freeze" if frozen else "thaw", unit + ".scope").returncode == 0


CPU_BUCKET_NS = 10_000_000_000


def unit_cpu_bucket(unit: str) -> int | None:
    """Czas procesora jednostki kroku w 10-sekundowych przedziałach (None — nie wiadomo). Długi test bez wyjścia
    (K-35: `make test` w tle przez 25 min) zużywa procesor, a bezczynnie wiszący krok — prawie nic."""
    if not unit:
        return None
    run = _systemctl("show", "-p", "CPUUsageNSec", "--value", unit + ".scope")
    try:
        return int(run.stdout.strip()) // CPU_BUCKET_NS if run.returncode == 0 else None
    except (ValueError, AttributeError):
        return None


def read_exit_code(step_dir: str) -> int | None:
    """Kod zakończenia zapisany przez zaufany wrapper jednostki, dostępny także po restarcie usługi.

    Brak, niepełny zapis lub dowiązanie nie dowodzą sukcesu kroku. Katalog `in` i sam plik otwieramy bez
    podążania za dowiązaniami; agent ma tam tylko odczyt, a plik powstaje po jego zakończeniu.
    """
    folder = None
    status = None
    try:
        folder = os.open(os.path.join(step_dir, "in"), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        status = os.open(EXIT_CODE_FILE, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
        info = os.fstat(status)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4:
            return None
        raw = os.read(status, 5)
    except OSError:
        return None
    finally:
        if status is not None:
            os.close(status)
        if folder is not None:
            os.close(folder)
    text = raw.decode("ascii", errors="replace")
    if not text.endswith("\n") or not text[:-1].isdigit() or str(int(text[:-1])) != text[:-1]:
        return None
    code = int(text[:-1])
    return code if code <= 255 else None


def _wait(still_running, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while still_running():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def _terminate_unit(proc, unit: str, grace: float) -> bool:
    state = unit_state(unit)
    if state == "active":
        _systemctl("kill", "--kill-whom=all", "--signal=SIGTERM", unit + ".scope")
        if not _wait(lambda: unit_state(unit) == "active", grace):
            _systemctl("kill", "--kill-whom=all", "--signal=SIGKILL", unit + ".scope")
            _wait(lambda: unit_state(unit) == "active", 5.0)
        state = unit_state(unit)
    if state != "inactive":
        return False                         # nie wiadomo albo wciąż działa — worktree zostaje nietknięty
    if hasattr(proc, "wait"):
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return not _alive(proc)                  # nieaktywna jednostka nie dowodzi, że proces z `proc` nie żyje


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

BWRAP = "/usr/bin/bwrap"
GIT_DIRS_WRITABLE = ("objects", "refs", "logs")
SANDBOX_PATH = "/usr/local/bin:/usr/bin:/bin"


def sandbox_available() -> bool:
    """Czy `bwrap` działa z potrzebnymi przestrzeniami (profil AppArmor: scripts/sandbox/check.py)."""
    try:
        return subprocess.run([BWRAP, "--ro-bind", "/", "/", "--unshare-net", "--unshare-ipc", "--unshare-pid",
                               "--die-with-parent", "--new-session", "true"], capture_output=True, timeout=20,
                              stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class StepGit:
    """Git usługi dla worktree kroku, uruchamiany w piaskownicy `bwrap` (decyzja 8): treść napisana przez agenta
    (filtry, `.gitattributes`, `fsmonitor`, zagnieżdżone repozytoria) może zrobić najwyżej to, co agent mógł
    w swoim kroku. Katalog domowy i /tmp są puste, zapis tylko w worktree i katalogach zapisu gita kroku."""

    def __init__(self, repo: str, git_dir: str, worktree: str, step_dir: str, home: str | None = None,
                 name: str = "Krok", email: str = "krok@tide.invalid", *, objects_only: bool = False):
        self.objects_only = objects_only
        self.worktree, self.git_dir = os.path.realpath(worktree), git_dir
        self.common = os.path.join(os.path.realpath(repo), ".git")
        self.home = os.path.realpath(home or os.path.expanduser("~"))
        self.inp = os.path.join(os.path.realpath(step_dir), "in")
        gitconfig = os.path.join(self.inp, "gitconfig")
        self.gitconfig = gitconfig if os.path.isfile(gitconfig) else "/dev/null"
        self.opts = ["--git-dir=" + git_dir, "--work-tree=" + self.worktree,
                     "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false",
                     "-c", "core.attributesFile=/dev/null", "-c", "maintenance.auto=false", "-c", "gc.auto=0",
                     "-c", "core.untrackedCache=false", "-c", f"user.name={name}", "-c", f"user.email={email}"]
        ignore = os.path.join(self.inp, "ignore")
        if os.path.isfile(ignore):
            self.opts += ["-c", f"core.excludesFile={ignore}"]

    def sandbox(self, index_dir: str | None = None) -> list:
        argv = [BWRAP, "--die-with-parent", "--new-session", "--unshare-net", "--unshare-ipc", "--unshare-pid",
                "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/run", "--tmpfs", self.home,
                "--ro-bind", self.common, self.common,
                "--ro-bind" if self.objects_only else "--bind", self.worktree, self.worktree,
                "--ro-bind" if self.objects_only else "--bind", self.git_dir, self.git_dir]
        for name in (("objects",) if self.objects_only else GIT_DIRS_WRITABLE):
            argv += ["--bind", os.path.join(self.common, name), os.path.join(self.common, name)]
        if index_dir is not None:
            # Prywatna kopia indeksu przeżywa kolejne wywołania, ale nie odsłania innych plików /tmp.
            argv += ["--bind", index_dir, "/tmp/tide-index"]
        argv += ["--ro-bind-try", self.inp, self.inp, "--clearenv"]
        env = {"PATH": SANDBOX_PATH, "HOME": self.home, "TMPDIR": "/tmp", "LC_ALL": "C",
               "GIT_CONFIG_GLOBAL": self.gitconfig, "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1",
               "GIT_COMMON_DIR": self.common, "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1",
               # Kontrola statusu nie może odświeżać ani konwertować indeksu (np. do split-index).
               "GIT_OPTIONAL_LOCKS": "0",
               "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false",
               "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent/tide-step/bus"}
        if index_dir is not None:
            env["GIT_INDEX_FILE"] = "/tmp/tide-index/index"
        for key, value in env.items():
            argv += ["--setenv", key, value]
        return argv + ["--chdir", self.worktree, "--"]

    def run(self, *args, timeout=120, index_dir: str | None = None) -> Result:
        try:
            proc = subprocess.run([*self.sandbox(index_dir), *git_argv(*self.opts, *args)], capture_output=True, text=True,
                                  errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
                                  env={"PATH": "/usr/bin:/bin"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Result(-1, "", str(exc))
        return Result(proc.returncode, proc.stdout, proc.stderr.strip())


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


def _restore_commondir(git_dir: str, notes: list) -> None:
    """Git bierze ścieżkę refów z pliku `commondir` w katalogu worktree (zmienna GIT_COMMON_DIR tego nie przesłania —
    sprawdzone na gicie 2.43), a Codex ma tam zapis. Podmieniony plik przekierowałby gałąź kroku do katalogu agenta;
    przywracamy `../..` (atomowo, podmianą pliku, nie zapisem przez ewentualny dowiązanie)."""
    path = os.path.join(git_dir, "commondir")
    try:
        with open(path, encoding="utf-8") as fh:
            current = fh.read().strip()
    except OSError:
        current = None
    if current == "../.." and not os.path.islink(path):
        return
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="commondir.", dir=git_dir)     # O_EXCL: nie podąża za podłożonym dowiązaniem
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("../..\n")
            os.fchmod(fh.fileno(), 0o644)                                # po deskryptorze, nie po ścieżce
        os.replace(tmp, path)
        tmp = None
        notes.append("przywrócono plik commondir katalogu worktree (krok go zmienił)")
    except OSError:
        pass
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _looks_like_git_dir(path: str) -> bool:
    return (os.path.isfile(os.path.join(path, "HEAD")) and os.path.isdir(os.path.join(path, "objects"))
            and os.path.isdir(os.path.join(path, "refs")))


def _nested_repositories(worktree: str, untracked_dirs) -> list:
    """Katalogi nieśledzone, które są zagnieżdżonym repozytorium (`.git` plik albo katalog) albo katalogiem gita —
    względem worktree. Przeglądamy tylko nieśledzone katalogi z `ls-files -o --directory`, bez wchodzenia w linki."""
    found = []

    def is_nested(path: str) -> bool:
        return os.path.lexists(os.path.join(path, ".git")) or _looks_like_git_dir(path)

    for rel in untracked_dirs:
        top = os.path.join(worktree, rel)
        if is_nested(top):
            found.append(rel)
            continue
        for folder, dirs, _files in os.walk(top, followlinks=False):
            nested = [d for d in dirs if d != ".git" and is_nested(os.path.join(folder, d))]
            for d in nested:
                found.append(os.path.relpath(os.path.join(folder, d), worktree))
            dirs[:] = [d for d in dirs if d != ".git" and d not in nested]
    return sorted(found)


def _clear_hidden_bits(step_git: StepGit, worktree: str, notes: list) -> None:
    """Agent mógł ustawić `--assume-unchanged` albo `--skip-worktree` (git przestaje widzieć zmiany pliku). Zdejmujemy
    bity z plików, które istnieją na dysku. Brakujący plik z `skip-worktree` to zwykle sparse checkout — zostaje."""
    listing = step_git.run("ls-files", "-v", "-z")
    if not listing.ok:
        return
    assumed, skipped = [], []
    for entry in listing.out.split("\0"):
        if len(entry) < 3:
            continue
        tag, path = entry[0], entry[2:]
        if not os.path.lexists(os.path.join(worktree, path)):
            continue
        if tag.islower():
            assumed.append(path)
        elif tag == "S":
            skipped.append(path)
    for flag, paths, label in (("--no-assume-unchanged", assumed, "assume-unchanged"),
                               ("--no-skip-worktree", skipped, "skip-worktree")):
        if paths:
            done = _update_index(step_git, flag, paths)
            if done:
                notes.append(f"zdjęto {label} z {len(paths)} plików: " + ", ".join(paths[:10]))


def _update_index(step_git: StepGit, flag: str, paths: list) -> bool:
    for start in range(0, len(paths), 200):
        if not step_git.run("update-index", flag, "--", *paths[start:start + 200]).ok:
            return False
    return True


def git_snapshot(repo: str, git_dir: str, worktree: str, step_dir: str, branch: str, stale_locks: bool = False,
                 home: str | None = None, name: str = "Krok", email: str = "krok@tide.invalid") -> Snapshot:
    """Commit WIP ze wszystkimi zmianami worktree (śledzonymi i nowymi) na gałęzi `branch`, gitem w piaskownicy.
    Nic do zacommitowania — bez commita. `stale_locks`: usuń blokady zabitego kroku (tylko gdy wiadomo, że krok nie żyje)."""
    snap = Snapshot()
    if branch in MAIN_CANDIDATES:
        snap.error = "protected-branch"
        return snap
    if not sandbox_available():
        snap.error = "no-sandbox"           # nigdy git bez piaskownicy: zmiany zostają w worktree
        return snap
    _restore_commondir(git_dir, snap.notes)
    step_git = StepGit(repo, git_dir, worktree, step_dir, home, name, email)
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
    _clear_hidden_bits(step_git, step_git.worktree, snap.notes)
    untracked = step_git.run("ls-files", "-o", "--directory", "--exclude-standard", "-z")
    dirs = [e.rstrip("/") for e in untracked.out.split("\0") if e.endswith("/")] if untracked.ok else []
    nested = _nested_repositories(step_git.worktree, dirs)
    if nested:
        snap.notes.append("pominięto zagnieżdżone repozytoria (nie weszły do WIP, zostają na dysku): "
                          + ", ".join(nested[:10]))
    added = step_git.run("add", "-A", "--", ".", *[f":(exclude,literal){path}" for path in nested])
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
    if not session_id:
        return ""
    from ..box.homes import claude_projects
    from ..worktrees.sessions import _claude_files
    roots = ([projects] if projects else []) + claude_projects()
    found = [(path, st.st_mtime) for root in roots for path, st in _claude_files(root)
             if os.path.basename(path) == session_id + ".jsonl"]
    return max(found, key=lambda p: p[1])[0] if found else ""


def find_codex_log(roots, worktree: str, session_id: str = "") -> str:
    """Najnowszy dziennik Codexa z katalogu kroku (`session_meta.cwd`), w katalogach danych `roots`."""
    from ..worktrees.sessions import _codex_files
    files = []
    for folder in codex_homes.sessions_dirs([*codex_homes.resolve(roots or []), *codex_homes.box_homes()]):
        files.extend(_codex_files(folder))
    want = os.path.realpath(worktree)
    for path, _stat in sorted(files, key=lambda p: p[1].st_mtime, reverse=True):
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
              tool: str, session_id: str = "", claude_projects: str = "", codex_roots=None,
              grace: float = 10.0, limit: int = MESSAGE_LIMIT,
              recovered_snapshot: Snapshot | None = None, commit_wip: bool = True, kill=None) -> StopResult:
    """Zatrzymuje krok, commituje WIP i czyta ostatnie wiadomości. `unit` (Background.unit) i/lub `proc` (Popen z
    Background.start() albo pid; None, gdy usługa się zrestartowała i zna tylko jednostkę). `git_dir` i `repo`
    z Background (ustalone przed krokiem), `branch` — gałąź kroku, na której ma wylądować commit.
    Commit robimy także wtedy, gdy program już nie żył — zmiany w worktree nie mogą zostać bez śladu.
    `commit_wip=False` (krok opiekuna, PT-158: pisze tylko do `out`) — bez commita: worktree i gałąź kroku zostają,
    jakie są, a ewentualny zapis kroku wykrywa strażnik."""
    # `kill` pochodzi z runnera; bez niego zatrzymujemy jednostkę lub proces hosta.
    stopped = kill() if kill is not None else terminate(proc, unit, grace)
    result = StopResult(stopped=stopped, exit_code=getattr(proc, "returncode", None))
    if not stopped:
        result.error = "not-stopped"        # nie dotykamy worktree, w którym program jeszcze pisze
        return result
    snap = recovered_snapshot if recovered_snapshot is not None else \
        git_snapshot(repo, git_dir, worktree, step_dir, branch, stale_locks=True) if commit_wip else Snapshot()
    result.committed, result.commit, result.files = snap.committed, snap.commit, snap.files
    result.error, result.notes = snap.error, snap.notes
    result.session_log = (find_claude_log(claude_projects, session_id) if tool == "claude"
                          else find_codex_log(codex_roots, worktree, session_id))
    result.messages = last_messages(result.session_log, tool, limit)
    return result
