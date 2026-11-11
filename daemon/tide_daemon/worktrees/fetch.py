"""„Pobierz” w obszarze worktrees: `git fetch <zdalny>` dla każdego zdalnego repozytorium z listy (PT-358).

Jedyne polecenie zapisujące to zwykły `git fetch` bez `--prune`, `--force`, `--tags` i bez refspeca
od Tide: zapisuje `refs/remotes/…` i `FETCH_HEAD` (z niego liczymy „pobrano przed chwilą”), nie
rusza HEAD, indeksu ani katalogów roboczych. Zasady:
- repozytorium musi być na liście Worktrees (porównanie po ścieżce kanonicznej, inaczej `bad-request`
  i żadnego uruchomienia gita; do gita idzie ścieżka z listy, nie ta z żądania),
- ta sama blokada repozytorium co scalanie karty (`cardflow.gitops.lock_for`, bez zmian w niej),
- bez powłoki z naszej strony, bez pytań: GIT_TERMINAL_PROMPT=0, puste GIT_ASKPASS/SSH_ASKPASS (inaczej
  git wywołałby program z otoczenia użytkownika), SSH w trybie wsadowym (BatchMode=yes),
- limit czasu całego pobierania (FETCH_TIMEOUT), poświadczenia użytkownika z otoczenia usługi
  (agent SSH, pomocnik poświadczeń gita),
- komunikaty i logi nie niosą poświadczeń z adresów URL (`mask_credentials`).
"""
import copy
import os
import re
import shlex
import subprocess
import time
from concurrent.futures import CancelledError

from .git import Git

FETCH_TIMEOUT = 120.0   # s na całe pobieranie (wszystkie zdalne)
LOCK_WAIT = 120.0       # s czekania na blokadę repozytorium trzymaną przez scalanie karty
MAX_DETAIL = 160
MAX_BRANCHES_IN_TEXT = 3

# Bezpieczne opcje pobierania: nic nie usuwa, nie rusza submodułów, nie uruchamia sprzątania w tle.
FETCH_OPTIONS = ("-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "fetch.prune=false",
                 "-c", "fetch.pruneTags=false", "-c", "fetch.recurseSubmodules=false",
                 "-c", "submodule.recurse=false", "-c", "protocol.ext.allow=never",
                 "-c", "core.askPass=")
_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s'\"]*@")

_AUTH_MARKERS = ("authentication failed", "permission denied", "could not read username", "could not read password",
                 "terminal prompts disabled", "host key verification failed", "invalid username or password",
                 "invalid username or token", "access denied", "publickey", "http basic: access denied",
                 "the requested url returned error: 401", "the requested url returned error: 403",
                 "repository not found", "no such identity")
_NETWORK_MARKERS = ("could not resolve host", "connection refused", "connection timed out", "network is unreachable",
                    "no route to host", "unable to access", "could not connect", "connection reset",
                    "operation timed out", "failed to connect", "temporary failure in name resolution",
                    "ssh: connect to host", "kex_exchange_identification", "connection closed")


def mask_credentials(text: str) -> str:
    """`https://user:token@host/…` → `https://***@host/…` (cała część użytkownik:hasło)."""
    return _USERINFO.sub(r"\1***@", text or "")


def fetch_word(n: int) -> str:
    """„1 nowy commit”, „3 nowe commity”, „5 nowych commitów”."""
    ones, tens = n % 10, n % 100
    if n == 1:
        return "1 nowy commit"
    return f"{n} nowe commity" if 2 <= ones <= 4 and not 12 <= tens <= 14 else f"{n} nowych commitów"


def classify(stderr: str, timed_out: bool) -> str:
    """Powód porażki jednego zdalnego: timeout | auth | network | git."""
    if timed_out:
        return "timeout"
    low = (stderr or "").lower()
    if any(marker in low for marker in _AUTH_MARKERS):
        return "auth"
    if any(marker in low for marker in _NETWORK_MARKERS):
        return "network"
    return "git"


def failure_sentence(name: str, reason: str, detail: str) -> str:
    """Jedno zdanie po polsku; szczegół z gita (zamaskowany, pierwsza linia) tylko jako krótki dopisek."""
    head = {
        "timeout": f"{name}: przekroczono limit czasu pobierania",
        "auth": f"{name}: brak dostępu (uwierzytelnienie albo klucz SSH — Tide nie pyta o hasło)",
        "network": f"{name}: nie można połączyć się ze zdalnym (sieć albo adres)",
    }.get(reason, f"{name}: git nie mógł pobrać")
    return f"{head} — {detail}" if detail and reason != "timeout" else head


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= MAX_DETAIL else line[:MAX_DETAIL - 1] + "…"
    return ""


def fetch_env(base: dict, ssh_base: str = "") -> dict:
    """Otoczenie pobierania: bez pytań o hasło i z SSH w trybie wsadowym; reszta (agent SSH, HOME) bez zmian."""
    env = dict(base)
    ssh = env.get("GIT_SSH_COMMAND") or ssh_base or (shlex.quote(env["GIT_SSH"]) if env.get("GIT_SSH") else "ssh")
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="", SSH_ASKPASS_REQUIRE="never",
               GIT_SSH_COMMAND=ssh + " -o BatchMode=yes", GIT_NO_LAZY_FETCH="1", LC_ALL="C")
    env.pop("GIT_SSH", None)
    return env


def _tips(git, repo: str, *patterns: str) -> dict[str, str]:
    res = git.run(repo, "for-each-ref", "--format=%(objectname) %(refname)", *patterns)
    tips = {}
    for line in res.out.splitlines() if res.ok else ():
        oid, _, ref = line.partition(" ")
        if oid and ref and not ref.endswith("/HEAD"):
            tips[ref] = oid
    return tips


def _count_new(git, repo: str, new: str, known: set[str]) -> int | None:
    """Commity osiągalne z `new`, których nie było w żadnym czubku sprzed pobierania."""
    stdin = "\n".join([new, *("^" + oid for oid in sorted(known))]) + "\n"
    res = git.run(repo, "rev-list", "--count", "--stdin", input_text=stdin)
    try:
        return int(res.out.strip()) if res.ok else None
    except ValueError:
        return None


def _describe(branches: list[dict]) -> str:
    if not branches:
        return "brak nowych commitów"
    parts = []
    for b in branches[:MAX_BRANCHES_IN_TEXT]:
        n = b["commits"]
        if n is None:
            parts.append(f"zaktualizowano {b['branch']}")
        elif n == 0:
            parts.append(f"nowa gałąź {b['branch']}" if b["new"] else f"zaktualizowano {b['branch']}")
        else:
            parts.append(f"{fetch_word(n)} na {b['branch']}")
    rest = len(branches) - MAX_BRANCHES_IN_TEXT
    return ", ".join(parts) + (f" i {rest} innych gałęzi" if rest > 0 else "")


def _refused(reason: str, message: str, repo_path: str = "") -> dict:
    return {"ok": False, "reason": reason, "message": message, "repoPath": repo_path, "remotes": []}


def _acquire(lock, deadline: float, cancel) -> bool:
    while True:
        if cancel is not None:
            cancel.check()
        if lock.acquire(timeout=0.1):
            return True
        if time.monotonic() >= deadline:
            return False


def fetch_repository(scanner, repo_path) -> dict:
    """Pobiera ze wszystkich zdalnych repozytorium znanego skanerowi. Działa w wątku roboczym."""
    if not isinstance(repo_path, str) or not repo_path or "\0" in repo_path:
        return _refused("bad-request", "to nie jest repozytorium z listy Worktrees")
    try:
        wanted = os.path.realpath(repo_path)
    except (OSError, ValueError):
        return _refused("bad-request", "to nie jest repozytorium z listy Worktrees")
    with scanner._lock:
        known = [w.repo_path for w in scanner.items]
    repo = next((p for p in dict.fromkeys(known) if os.path.realpath(p) == wanted), "")
    if not repo:
        return _refused("bad-request", "to nie jest repozytorium z listy Worktrees")
    remotes = [r for r in scanner._remotes(repo) if not r.startswith("-")]
    if not remotes:
        return _refused("no-remote", "repozytorium nie ma zdalnego — nie ma z czego pobierać", repo)

    from ..cardflow.gitops import lock_for  # leniwie: cardflow importuje ten pakiet
    cancel = getattr(scanner, "_cancel", None)
    lock = lock_for(repo)
    if not _acquire(lock, time.monotonic() + LOCK_WAIT, cancel):
        return _refused("busy", "repozytorium jest zajęte (scalanie karty) — spróbuj za chwilę", repo)
    try:
        return _fetch_locked(scanner, repo, remotes, cancel)
    finally:
        lock.release()


def _runner(scanner, repo: str) -> Git:
    base = scanner.git
    if not isinstance(base, Git):
        return base  # atrapa z testów
    runner = copy.copy(base)  # te same korzenie, anulowanie i wykonywalny git, własny limit i otoczenie
    runner.timeout = FETCH_TIMEOUT
    configured = base.run(repo, "config", "--get", "core.sshCommand")
    runner.env = fetch_env(base.env, configured.out.strip() if configured.ok else "")
    return runner


def _fetch_locked(scanner, repo: str, remotes: list[str], cancel) -> dict:
    git = _runner(scanner, repo)
    deadline = time.monotonic() + FETCH_TIMEOUT
    before = _tips(git, repo, "refs/heads", "refs/remotes")
    known = set(before.values())
    results, failures = [], []
    for name in remotes:
        left = deadline - time.monotonic()
        if left <= 0:
            failures.append((name, "timeout", ""))
            results.append({"name": name, "ok": False, "reason": "timeout", "branches": [], "newCommits": 0})
            continue
        res = git.run(repo, *FETCH_OPTIONS, "fetch", "--no-prune", "--no-recurse-submodules", "--", name,
                      timeout=left)
        stderr = mask_credentials(res.err)
        if not res.ok:
            timed_out = res.code == -1 and "timed out" in res.err
            reason = classify(stderr, timed_out)
            detail = "" if timed_out else _first_line(stderr)
            failures.append((name, reason, detail))
            results.append({"name": name, "ok": False, "reason": reason, "branches": [], "newCommits": 0})
            print(f"tide-daemon: worktrees: pobieranie {name}: {reason}: {detail}", flush=True)
            continue
        after = _tips(git, repo, f"refs/remotes/{name}/")
        branches = []
        for ref, oid in sorted(after.items()):
            if before.get(ref) == oid:
                continue
            short = ref[len(f"refs/remotes/{name}/"):]
            branches.append({"branch": short, "ref": f"{name}/{short}", "new": ref not in before,
                             "commits": _count_new(git, repo, oid, known)})
        results.append({"name": name, "ok": True, "branches": branches,
                        "newCommits": sum(b["commits"] or 0 for b in branches)})
    total = sum(r["newCommits"] for r in results)
    fetched = [r for r in results if r["ok"]]
    out = {"ok": not failures, "repoPath": repo, "remotes": results, "newCommits": total}
    if failures:
        name, reason, detail = failures[0]
        out.update(reason=reason, message=failure_sentence(name, reason, detail))
        if fetched:
            out["message"] += f" (pobrano: {', '.join(r['name'] for r in fetched)})"
    else:
        out["message"] = "pobrano: " + "; ".join(f"{r['name']} — {_describe(r['branches'])}" for r in results)
    return out
