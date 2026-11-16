"""Runner (PT-180, K-38) — jedyne miejsce, przez które silnik kart uruchamia i zatrzymuje kroki w tle.

`Runner` to umowa: `start(step)`, `kill(step)`, `env(step)`. Dzisiejszy sposób — `agentcmd.background`,
`systemd-run --user --scope`, bwrap i zatrzymanie jednostki — to `HostRunner`; zachowuje się bit w bit jak przed
wprowadzeniem interfejsu (złoty plik `tests/fixtures/runner/host_argv.json`). Silnik dostaje runner w konstruktorze
(domyślnie `HostRunner`); nie wybiera go po niczym.

Polecenia nie-agentowe (K-109 PT-479, ADR-1): `run_command(request) -> CommandHandle` uruchamia jedno polecenie
jako przejściową usługę `systemd-run --user --wait --collect --unit=tide-cmd-<attempt>` (nigdy `--scope`)
z opakowaniem `tide-step --command`, plikiem `exit-code` i `RuntimeMaxSec` = limit czasu polecenia. Wynik pochodzi
wyłącznie z pliku `exit-code` (`command_result`): liczba albo `zatrzymane`. Klient `systemd-run --wait` jest tylko
obserwatorem — jego kod wyjścia i śmierć nie są wynikiem. Etap 1: `isolation: none` (dzisiejsze środowisko
i uprawnienia usługi Tide, przekazane przez `--setenv=NAZWA` bez wartości w argv).

Dowód braku procesu (K-109 PT-485, ADR-1 pkt 5): w katalogu próby, obok `exit-code`, leży jednorazowy znacznik
próby zakładany przez `open(O_CREAT|O_EXCL)`. Opakowanie zakłada go z treścią `run` przed startem polecenia (gdy już
jest — kończy się bez polecenia i zapisuje `zatrzymane`); odzyskiwanie zamiaru bez startu zakłada go z treścią
`abandoned` (`abandon_command`). Kto pierwszy założy znacznik, ten rozstrzyga: polecenie biegnie albo nie wystartuje
nigdy, także gdy jednostka powstanie po odzyskaniu.
"""
from __future__ import annotations

import os
import re
import secrets
import stat
import subprocess
from dataclasses import dataclass, field
from typing import Callable, NamedTuple, Protocol

from . import agentcmd, stepstop

# Opakowanie kroku pudełka w trybie polecenia (ten sam plik co `box.runner.TIDE_STEP_SCRIPT`).
TIDE_STEP_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "box", "tide-step")
COMMAND_PREFIX = "tide-cmd-"
STOPPED = "zatrzymane"          # wynik polecenia zatrzymanego bez kodu (jak OUTSIDE_STOPPED silnika)
EXECUTOR = "host-isolated"      # wykonawca hosta; izolacja w etapie 1 wyłączona
ISOLATION = "none"
EXIT_FILE = "exit-code"
LOG_FILE = "outside.log"
MARKER_FILE = "attempt-marker"  # znacznik próby (PT-485): `run` — opakowanie, `abandoned` — odzyskiwanie
MARK_RUN, MARK_ABANDONED = "run", "abandoned"
_ATTEMPT = re.compile(r"^[a-z0-9]{1,32}$")
# Zmienne menedżera usług i aktywacji: należą do usługi Tide, nie do polecenia (dawniej `env -u INVOCATION_ID`).
UNIT_ENV = frozenset({"INVOCATION_ID", "JOURNAL_STREAM", "NOTIFY_SOCKET", "LISTEN_FDS", "LISTEN_PID",
                      "LISTEN_FDNAMES", "MANAGERPID", "SYSTEMD_EXEC_PID", "WATCHDOG_PID", "WATCHDOG_USEC",
                      "MAINPID", "MONITOR_EXIT_CODE", "MONITOR_EXIT_STATUS", "MONITOR_INVOCATION_ID",
                      "MONITOR_SERVICE_RESULT", "MONITOR_UNIT", "TRIGGER_UNIT", "TRIGGER_PATH",
                      "TRIGGER_TIMER_REALTIME_USEC", "TRIGGER_TIMER_MONOTONIC_USEC"})
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class CommandRequest:
    """Prośba o jedno polecenie nie-agentowe. `folder` — katalog kroku (`<krok>/outside/<jednostka>/` trzyma plik
    wyniku i `exit-code`), `cwd` — katalog roboczy, `env` — pełne środowisko polecenia, `timeout` — RuntimeMaxSec
    (s), `attempt` — identyfikator próby (pusty: losowy przy starcie)."""
    command: str
    folder: str
    cwd: str = ""
    env: dict = field(default_factory=dict)
    timeout: int = 3600
    cpu_weight: int = 40
    attempt: str = ""


@dataclass(frozen=True)
class CommandHandle:
    """Uchwyt uruchomionego polecenia: identyfikator próby, jednostka, plik wyniku, plik `exit-code`, argv i proces
    klienta `systemd-run --wait` (tylko obserwator; None po restarcie)."""
    attempt: str
    unit: str
    log: str
    exit_file: str
    argv: tuple
    proc: object = None


def read_command_exit(path: str) -> int | str | None:
    """Wynik polecenia z pliku `exit-code`: liczba 0–255, `zatrzymane` albo None (brak, niepełny zapis, dowiązanie,
    inna treść). Plik otwierany bez podążania za dowiązaniem i bez blokowania na FIFO (pętla usługi)."""
    if not isinstance(path, str) or not path:
        return None
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32:
            return None
        raw = os.read(fd, 33)
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)
    text = raw.decode("ascii", "replace")
    if not text.endswith("\n"):
        return None             # zapis opakowania zawsze kończy się nową linią; inaczej niepełny
    text = text[:-1]
    if text == STOPPED:
        return STOPPED
    if text.isdigit() and len(text) <= 3 and str(int(text)) == text and int(text) <= 255:
        return int(text)
    return None


def command_unit(attempt: str) -> str:
    return COMMAND_PREFIX + attempt


def new_attempt() -> str:
    """Nowy identyfikator próby (trwały: zapisany w zamiarze przed startem, K-109 PT-485)."""
    return secrets.token_hex(8)


class CommandFiles(NamedTuple):
    """Jednostka i pliki próby `attempt` w katalogu kroku — wyznaczone przed startem (zamiar je zapisuje)."""
    unit: str
    folder: str
    exit_file: str
    log: str
    script: str
    marker: str


def command_files(folder: str, attempt: str) -> CommandFiles:
    if not isinstance(attempt, str) or not _ATTEMPT.match(attempt):
        raise ValueError(f"niepoprawny identyfikator próby: {attempt!r}")
    unit = command_unit(attempt)
    path = os.path.join(folder, "outside", unit)
    return CommandFiles(unit, path, *(os.path.join(path, name)
                                      for name in (EXIT_FILE, LOG_FILE, "command", MARKER_FILE)))


def marker_of(exit_file: str) -> str:
    """Znacznik próby obok pliku `exit-code` tej próby."""
    return os.path.join(os.path.dirname(exit_file), MARKER_FILE)


def claim_abandoned(exit_file: str) -> bool:
    """Odzyskiwanie zamiaru bez startu (K-109 PT-485): zakłada znacznik próby z treścią `abandoned`
    (`O_CREAT|O_EXCL`). True — polecenie tej próby nigdy nie wystartuje (znacznik założony teraz albo wcześniej przez
    odzyskiwanie); False — znacznik założyło opakowanie (`run` albo jego niepełny zapis), polecenie wystartowało.

    Brak katalogu próby: zamiar przerwany przed `run_command` — katalog powstaje tu (ponowny `run_command` z tym samym
    `attempt` i tak kończy się kolizją katalogu i nazwy jednostki). Inny błąd zapisu (`OSError`) przechodzi do
    wywołującego — bez rozstrzygnięcia."""
    marker = marker_of(exit_file)
    os.makedirs(os.path.dirname(marker), mode=0o700, exist_ok=True)
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        # Własny wcześniejszy zapis odzyskiwania (awaria przed `command.finished`) daje ten sam wynik; pusty albo
        # niepełny plik traktujemy jak `run` — rozstrzyga wtedy jednostka i `exit-code` (zachowawczo).
        return read_marker(marker) == MARK_ABANDONED
    try:
        os.write(fd, (MARK_ABANDONED + "\n").encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def read_marker(path: str) -> str:
    """Treść znacznika próby (`run`, `abandoned`) albo "" (brak, niepełny zapis, dowiązanie, inna treść)."""
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ""
        text = os.read(fd, 16).decode("ascii", "replace")
    except OSError:
        return ""
    finally:
        if fd is not None:
            os.close(fd)
    value = text[:-1] if text.endswith("\n") else ""
    return value if value in (MARK_RUN, MARK_ABANDONED) else ""


class Started(NamedTuple):
    """Wynik `start`: złożony krok (`unit`, `git_dir`, pliki) i uchwyt procesu, gdy runner go ma (Popen; None —
    brak, silnik zna wtedy krok tylko po jednostce, jak po restarcie usługi)."""
    background: agentcmd.Background
    proc: object


class Runner(Protocol):
    def start(self, step: agentcmd.Step, *, before_start: Callable[[agentcmd.Background], None] | None = None
              ) -> Started:
        """Składa krok, przygotowuje jego katalogi i pliki, woła `before_start(background)` (np. kopiowanie
        obrazów) i uruchamia program. Błędy: `agentcmd.StepError`, `agentcmd.UnknownTool`, `OSError`."""

    def kill(self, step: dict, *, proc: object = None, grace: float = 10.0) -> bool:
        """Zatrzymuje krok opisany wpisem dziennika (`unit`) i/lub uchwytem `proc`. True, gdy nic nie żyje."""

    def env(self, step: agentcmd.Step) -> dict:
        """Środowisko programu kroku — dokładnie `Background.env` (z zamianami punktu lokalnego); sekrety
        z `secret_env` dopisuje dopiero `start`."""

    def run_command(self, request: CommandRequest) -> CommandHandle:
        """Uruchamia jedno polecenie nie-agentowe (ADR-1) i zwraca uchwyt po starcie klienta jednostki. Błędy
        startu (`OSError`) przechodzą do wywołującego; zapis startu w dzienniku należy do wywołującego."""

    def command_result(self, unit: str, exit_file: str, *, age: float, grace: float,
                       state_timeout: float) -> int | str | None:
        """Wynik polecenia (liczba albo `zatrzymane`) wyłącznie z pliku `exit-code`; None — polecenie trwa albo
        stan jednostki nieznany. `zatrzymane` z pliku jest wynikiem dopiero po końcu jednostki (procesy polecenia
        mogą jeszcze żyć po SIGTERM). Jednostka bez procesów i bez pliku po `grace` sekundach od startu (`age`) —
        `zatrzymane`."""

    def command_state(self, unit: str, *, timeout: float = 3) -> str:
        """`active`, `gone` albo `unknown` jednostki polecenia; tylko `gone` potwierdza jej koniec."""

    def stop_command(self, unit: str) -> None:
        """Zatrzymanie jednostki polecenia bez czekania (opakowanie zapisze `zatrzymane`)."""

    def abandon_command(self, exit_file: str) -> bool:
        """Dowód braku procesu (K-109 PT-485): znacznik próby `abandoned` przez `O_EXCL` (`claim_abandoned`).
        True — polecenie nigdy nie wystartuje; False — opakowanie już je uruchomiło."""


class HostRunner:
    """Krok jako proces na tym komputerze: jednostka systemd (scope) + piaskownica Claude Code / Codexa."""

    def __init__(self, systemd_run: str = ""):
        self.systemd_run = systemd_run     # inny plik wykonywalny niż systemd-run (atrapa w testach usługi)

    def start(self, step: agentcmd.Step, *, before_start: Callable[[agentcmd.Background], None] | None = None
              ) -> Started:
        from ..box import trialgate
        from .inputs import selected
        trialgate.require_step(step)
        with selected(step, "host") as (adapted, _inputs):
            bg = agentcmd.background(adapted)
            bg.prepare()
            if before_start is not None:
                before_start(bg)
            trialgate.require_step(step)
            return Started(bg, bg.start(systemd_run=self.systemd_run))

    def kill(self, step: dict, *, proc: object = None, grace: float = 10.0) -> bool:
        return stepstop.terminate(proc, step.get("unit") or "", grace)

    def env(self, step: agentcmd.Step) -> dict:
        from .inputs import selected
        with selected(step, "host") as (adapted, _inputs):
            return agentcmd.background(adapted).env

    # --- polecenia nie-agentowe (K-109 PT-479, ADR-1) ------------------------------------------------------

    def command_argv(self, request: CommandRequest, unit: str, exit_file: str, log: str, script: str) -> list:
        """argv klienta jednostki polecenia. Treść polecenia leży w pliku `script` (nie w argv): systemd rozwija
        `${ZMIENNA}` w argumentach usługi przejściowej, a polecenie ma je dostać nietknięte dla powłoki.
        Środowisko — `--setenv=NAZWA` bez wartości (systemd-run bierze wartość z własnego środowiska, więc wartości
        nie trafiają do argv widocznego w `ps`)."""
        argv = [self.systemd_run or "systemd-run", "--user", "--wait", "--collect", "--quiet", f"--unit={unit}",
                "-p", f"CPUWeight={request.cpu_weight}", "-p", f"IOWeight={request.cpu_weight}",
                "-p", f"RuntimeMaxSec={int(request.timeout)}"]
        if request.cwd:
            argv.append(f"--working-directory={request.cwd}")
        argv += [f"--setenv={name}" for name in sorted(_command_env(request.env))]
        argv += ["--", "/bin/sh", TIDE_STEP_SCRIPT, "--command", exit_file, log, marker_of(exit_file), "--",
                 "/bin/sh", script]
        return argv

    def run_command(self, request: CommandRequest) -> CommandHandle:
        attempt = request.attempt or new_attempt()
        unit, folder, exit_file, log, script, marker = command_files(request.folder, attempt)
        if any(c in path for path in (exit_file, log, script, marker, TIDE_STEP_SCRIPT, request.cwd or "")
               for c in "$%\n"):
            # Argumenty jednostki (także `--working-directory=`) przechodzą przez rozwijanie zmiennych
            # i specyfikatorów systemd.
            raise ValueError(f"ścieżka polecenia ze znakiem specjalnym systemd: {folder} (cwd {request.cwd!r})")
        os.makedirs(request.folder, exist_ok=True)
        os.makedirs(os.path.join(request.folder, "outside"), exist_ok=True)
        os.mkdir(folder, 0o700)         # nowa próba = nowy katalog: stary `exit-code` nie jest wynikiem nowej
        fd = os.open(script, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(request.command + "\n")
        argv = self.command_argv(request, unit, exit_file, log, script)
        env = _command_env(request.env)
        with open(log, "ab") as out:
            proc = subprocess.Popen(argv, cwd=request.cwd or None, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        return CommandHandle(attempt, unit, log, exit_file, tuple(argv), proc)

    def command_state(self, unit: str, *, timeout: float = 3) -> str:
        """`active` (także activating/deactivating — procesy mogą żyć), `gone` (nieaktywna, nieudana albo zebrana
        przez `--collect`) albo `unknown` (błąd systemctl): z `unknown` nie wolno wnosić końca polecenia."""
        run = stepstop._systemctl("is-active", unit + ".service", timeout=timeout)
        state = (run.stdout or b"").decode("utf-8", "replace").strip()
        if run.returncode == 0:
            return "active"
        if run.returncode == 4 or (run.returncode == 3 and state in ("inactive", "failed")):
            return "gone"
        if state in ("active", "activating", "deactivating", "reloading", "refreshing"):
            return "active"
        return "unknown"

    def command_result(self, unit: str, exit_file: str, *, age: float, grace: float,
                       state_timeout: float) -> int | str | None:
        code = read_command_exit(exit_file)
        if code == STOPPED:
            # Opakowanie zapisuje `zatrzymane` od razu przy SIGTERM, a procesy polecenia mogą jeszcze żyć do
            # `TimeoutStopSec` (jak PT-315): wynik końcowy dopiero, gdy jednostki już nie ma — dotąd czekanie, żeby
            # nie zwolnić kolejki ani nie wznowić kroku obok wciąż piszącego polecenia. `unknown` też znaczy czekanie.
            return STOPPED if self.command_state(unit, timeout=state_timeout) == "gone" else None
        if code is not None:
            return code
        if age < grace:
            return None         # systemd-run rejestruje jednostkę chwilę po starcie klienta
        if self.command_state(unit, timeout=state_timeout) != "gone":
            return None
        # Opakowanie mogło skończyć zapis tuż po pierwszym odczycie: jeszcze raz plik, potem `zatrzymane`.
        code = read_command_exit(exit_file)
        return STOPPED if code is None else code

    def stop_command(self, unit: str) -> None:
        stepstop._systemctl("stop", "--no-block", unit + ".service", timeout=5)

    def abandon_command(self, exit_file: str) -> bool:
        return claim_abandoned(exit_file)


def _command_env(env: dict) -> dict:
    """Środowisko polecenia bez zmiennych menedżera usług (należą do usługi Tide) i bez niepoprawnych nazw."""
    return {k: v for k, v in (env or {}).items()
            if isinstance(k, str) and isinstance(v, str) and k not in UNIT_ENV and _ENV_NAME.match(k)
            and "\0" not in v}
