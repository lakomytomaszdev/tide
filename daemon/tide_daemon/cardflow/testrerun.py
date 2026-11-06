"""Ponowienie padniętych modułów wymuszane przez Tide (06.11, etap A deterministycznie).

31% czasu testów ticketów to były powtórki całego zestawu na niezmienionym kodzie. Kartki kroków opisują zasadę,
ale Tide nie polega na pamięci agenta: gdy polecenie poza piaskownicą to pełny przebieg (`make test`,
`make test-changed …`), a ostatni taki przebieg w tym samym worktree padł na tym samym HEAD przy czystym drzewie,
Tide uruchamia zamiast niego tylko moduły, które padły. Dotyczy repozytoriów z `tools/test_runner.py` (raport
`summary.json`); inne technologie zostają przy zasadzie z kartki. `TIDE_FULL_RUN=1` w poleceniu wymusza pełny przebieg.
"""

import json
import os
import re
import shlex
import subprocess

VALUE = r"[A-Za-z0-9_./:@+-]+"
FULL_TEST = re.compile(rf"^(?:[A-Z_]+={VALUE} )*make (?:test|test-changed)(?: [A-Z_]+={VALUE})*$")
PREFIX = re.compile(rf"^((?:[A-Z_]+={VALUE} )*)make ")
# Nagłówek przebiegu uruchamiacza (pierwsza taka linia dziennika); dowolne późniejsze „logi:” z wyjścia testu
# nie mogą wskazać cudzego raportu (review Astry 06.11).
HEADER = re.compile(r"^Moduły: (\d+), JOBS=\d+, logi: (\S+)$", re.M)
MODULE = re.compile(r"^[\w.-]+(?:/[\w.-]+)*\.py$")


def is_full_test(command: str) -> bool:
    command = (command or "").strip()
    return bool(FULL_TEST.match(command)) and "TIDE_FULL_RUN=1" not in command


def _git(worktree: str, *args: str) -> str | None:
    try:
        run = subprocess.run(["git", "-C", worktree, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return run.stdout if run.returncode == 0 else None


def head(worktree: str) -> str:
    """HEAD worktree, gdy repozytorium ma uruchamiacz Tide i czyste drzewo; inaczej pusty napis (bez podmiany)."""
    if not worktree or not os.path.isfile(os.path.join(worktree, "tools", "test_runner.py")):
        return ""
    status = _git(worktree, "status", "--porcelain")
    sha = _git(worktree, "rev-parse", "HEAD")
    return sha.strip() if status == "" and sha else ""


def failed_modules(log: str) -> list[str]:
    """Moduły z returncode różnym od 0 w raporcie przebiegu wskazanym nagłówkiem dziennika polecenia. Pusta lista
    (brak nagłówka lub raportu, raport niekompletny albo o innym schemacie, brak porażek) = nie podmieniaj."""
    try:
        with open(log, encoding="utf-8", errors="replace") as fh:
            header = HEADER.search(fh.read())
        with open(os.path.join(header.group(2), "summary.json"), encoding="utf-8") as fh:
            results = json.load(fh)["results"]
    except (OSError, AttributeError, ValueError, KeyError, TypeError):
        return []
    if not isinstance(results, list) or len(results) != int(header.group(1)) or not all(
            isinstance(r, dict) and isinstance(r.get("module"), str) and isinstance(r.get("returncode"), int)
            for r in results):
        return []
    modules = [r["module"] for r in results if r["returncode"]]
    return modules if modules and all(MODULE.match(m) and ".." not in m for m in modules) else []


def rerun_command(modules: list[str], sha: str, previous: str, command: str = "") -> str:
    """Każdy moduł z własnym `XDG_STATE_HOME`, jak w `tools/test_runner.py` (test bez izolacji nadpisał kiedyś stan
    działającej Tablicy), i ze zmiennymi środowiska z oryginalnego polecenia."""
    prefix = PREFIX.match(command.strip()).group(1) if PREFIX.match(command.strip()) else ""
    lines = [f"echo 'Tide: ten sam HEAD {sha[:12]} i czyste drzewo co w przebiegu {previous} — "
             f"ponawiam tylko padnięte moduły ({len(modules)}); TIDE_FULL_RUN=1 wymusza pełny przebieg'", "rc=0"]
    for module in modules:
        folder, name = os.path.split(module)
        lines.append(f'XDG_STATE_HOME="$(mktemp -d)" {prefix}/usr/bin/python3 -m unittest discover -t . '
                     f"-s {shlex.quote(folder)} -p {shlex.quote(name)} || rc=1")
    return "\n".join(lines + ["exit $rc"])


def substitute(events: list[dict], worktree: str, command: str) -> tuple[str, str, str]:
    """(polecenie do uruchomienia, HEAD do zapisania, krok ponawianego przebiegu albo "")."""
    if not is_full_test(command):
        return command, "", ""
    sha = head(worktree)
    if not sha:
        return command, "", ""
    finished = {e.get("step"): e for e in events if e.get("type") == "outside.finished"}
    for event in reversed(events):
        if event.get("type") != "outside.started" or event.get("worktree") != worktree \
                or not is_full_test(event.get("command") or ""):
            continue
        done = finished.get(event.get("step"))
        # Ten sam cel, baza i parametry doboru: inny zakres (np. `make test` po `test-changed`) to pełny przebieg.
        if event.get("head") != sha or not done or not done.get("exit") or \
                (event.get("command") or "").strip() != command.strip():
            return command, sha, ""
        modules = failed_modules(event.get("log") or "")
        if not modules:
            return command, sha, ""
        return rerun_command(modules, sha, event.get("step") or "", command), sha, event.get("step") or ""
    return command, sha, ""
