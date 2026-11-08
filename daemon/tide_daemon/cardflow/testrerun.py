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
# Po `make` tylko parametry doboru, które ponowienie odtwarza (BASE, JOBS); inne zmienne Make (np. LC_ALL=C)
# zmieniałyby recepturę, więc taki przebieg idzie w całości (review Astry 06.11, runda 2).
# TEST_OUTPUT wskazuje tylko katalog raportu (08.11, K-38/PT-264: agenci dopisują katalog kroku), więc nie zmienia
# receptury; przy porównaniu przebiegów jest pomijany, a ponowienie pisze raport tam, gdzie wskazuje bieżące polecenie.
FULL_TEST = re.compile(rf"^(?:[A-Z_]+={VALUE} )*make (?:test|test-changed)(?: (?:BASE|JOBS|TEST_OUTPUT)={VALUE})*$")
OUTPUT_TOKEN = re.compile(rf"(?:^| )TEST_OUTPUT={VALUE}(?= |$)")
# Ucięcie przez Tide (limit czasu, zatrzymanie jednostki): wtedy raport końcowy nie powstaje.
INTERRUPTED = {130, 137, 143}
PREFIX = re.compile(rf"^((?:[A-Z_]+={VALUE} )*)make ")
JOBS = re.compile(rf"(?:^| )JOBS=({VALUE})(?= |$)")
OUTPUT = re.compile(rf"(?:^| )TEST_OUTPUT=({VALUE})(?= |$)")
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


def runner_reruns(worktree: str) -> bool:
    """Czy uruchamiacz gałęzi umie `--modules` (pula maszyny i izolacja stanu jak w pełnym przebiegu)."""
    try:
        with open(os.path.join(worktree, "tools", "test_runner.py"), encoding="utf-8") as fh:
            return '"--modules"' in fh.read()
    except OSError:
        return False


def head(worktree: str) -> str:
    """HEAD worktree, gdy uruchamiacz gałęzi umie ponowienie i drzewo jest czyste; inaczej "" (bez podmiany)."""
    if not worktree or not runner_reruns(worktree):
        return ""
    status = _git(worktree, "status", "--porcelain")
    sha = _git(worktree, "rev-parse", "HEAD")
    return sha.strip() if status == "" and sha else ""


def scope(command: str) -> str:
    """Polecenie bez TEST_OUTPUT — dwa przebiegi o tym samym zakresie różnią się tylko katalogiem raportu."""
    return " ".join(OUTPUT_TOKEN.sub(" ", (command or "").strip()).split())


def interrupted(exit_code) -> bool:
    """Wynik ucięty przez Tide: opis zamiast kodu (\"przekroczony czas\") albo kod sygnału zatrzymania."""
    if isinstance(exit_code, bool):
        return False
    if isinstance(exit_code, str):
        text = exit_code.strip()
        if not text.lstrip("-").isdigit():
            return bool(text)
        exit_code = int(text)
    return isinstance(exit_code, int) and exit_code in INTERRUPTED


def _stem(module: str) -> str:
    return module.replace("/", "__").removesuffix(".py")


def unfinished_modules(directory: str, count: int, root: str) -> list[str]:
    """Przebieg ucięty bez summary.json: moduły padnięte albo bez wyniku. Lista modułów z `modules.json`
    uruchamiacza, a dla starszego uruchamiacza z logów modułów — tylko gdy liczba zgadza się z nagłówkiem,
    bo niezaczętego modułu nie wolno pominąć. Pusta lista = nie podmieniaj."""
    try:
        with open(os.path.join(directory, "modules.json"), encoding="utf-8") as fh:
            planned = json.load(fh)["modules"]
    except FileNotFoundError:
        try:
            planned = sorted(name[:-4].replace("__", "/") + ".py" for name in os.listdir(directory)
                             if name.endswith(".log"))
        except OSError:
            return []
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if not isinstance(planned, list) or len(planned) != count or len(set(planned)) != count or not all(
            isinstance(m, str) and MODULE.match(m) and ".." not in m and os.path.isfile(os.path.join(root, m))
            for m in planned):
        return []
    rerun = []
    for module in planned:
        try:
            with open(os.path.join(directory, _stem(module) + ".json"), encoding="utf-8") as fh:
                report = json.load(fh)
            ok = isinstance(report, dict) and not report.get("crash") and all(
                isinstance(report.get(k), list) and not report[k]
                for k in ("failures", "errors", "unexpected_successes"))
        except (OSError, ValueError):
            ok = False
        if not ok:
            rerun.append(module)
    return rerun


def failed_modules(log: str, cut: bool = False, root: str = "") -> list[str]:
    """Moduły z returncode różnym od 0 w raporcie przebiegu wskazanym nagłówkiem dziennika polecenia. Pusta lista
    (brak nagłówka lub raportu, raport niekompletny albo o innym schemacie, brak porażek) = nie podmieniaj.
    Przebieg ucięty przez Tide (`cut`) bez raportu: moduły padnięte i bez wyniku (`unfinished_modules`)."""
    try:
        with open(log, encoding="utf-8", errors="replace") as fh:
            header = HEADER.search(fh.read())
        directory = header.group(2)
    except (OSError, AttributeError):
        return []
    try:
        with open(os.path.join(directory, "summary.json"), encoding="utf-8") as fh:
            results = json.load(fh)["results"]
    except FileNotFoundError:
        return unfinished_modules(directory, int(header.group(1)), root) if cut and root else []
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if not isinstance(results, list) or len(results) != int(header.group(1)) or not all(
            isinstance(r, dict) and isinstance(r.get("module"), str) and isinstance(r.get("returncode"), int)
            for r in results):
        return []
    modules = [r["module"] for r in results if r["returncode"]]
    return modules if modules and all(MODULE.match(m) and ".." not in m for m in modules) else []


def rerun_command(modules: list[str], sha: str, previous: str, command: str = "") -> str:
    """Przez uruchamiacz projektu (`--modules`): ta sama pula maszyny, osobny katalog stanu każdego modułu i raport
    jak w pełnym przebiegu; zmienne środowiska sprzed `make` i JOBS zostają."""
    command = command.strip()
    prefix = PREFIX.match(command).group(1) if PREFIX.match(command) else ""
    # Zmienna z wiersza `make` ma pierwszeństwo przed środowiskiem, więc wygrywa ostatnie JOBS; TEST_OUTPUT Make
    # przekłada na --output (review Astry, runda 3).
    jobs = (JOBS.findall(command) or [None])[-1]
    output = (OUTPUT.findall(command) or [None])[-1]
    lines = [f"echo 'Tide: ten sam HEAD {sha[:12]} i czyste drzewo co w przebiegu {previous} — "
             f"ponawiam tylko padnięte moduły ({len(modules)}); TIDE_FULL_RUN=1 wymusza pełny przebieg'",
             f"{prefix}/usr/bin/python3 tools/test_runner.py"
             + (f" --jobs {shlex.quote(jobs)}" if jobs else "")
             + (f" --output {shlex.quote(output)}" if output else "")
             + " --modules " + " ".join(shlex.quote(module) for module in modules)]
    return "\n".join(lines)


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
                scope(event.get("command") or "") != scope(command):
            return command, sha, ""
        modules = failed_modules(event.get("log") or "", cut=interrupted(done.get("exit")), root=worktree)
        if not modules:
            return command, sha, ""
        return rerun_command(modules, sha, event.get("step") or "", command), sha, event.get("step") or ""
    return command, sha, ""
