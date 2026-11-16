#!/usr/bin/env python3
"""Pętla przebiegów jednego modułu testów pod obciążeniem (K-100, PT-363, PT-417).

Każdy przebieg to osobne wywołanie publicznego `tools/test_runner.py --modules … --output …`; narzędzie nie
wywołuje unittest samo. Tryby:

- dowód — palniki ograniczone (domyślny): palniki w czystym Pythonie trzymają loadavg1 w paśmie 0,8–1,0 × cel
  (domyślnie liczba rdzeni), nigdy powyżej — regulacja liczbą czynnych palników (SIGSTOP/SIGCONT) z liczbą
  procesów gotowych do biegu, w tym procesów samego testu;
- eksperyment — powolny silnik (`--slow [ZIARNO]`): bez palników; uruchamiacz dostaje `TIDE_TEST_SLOW=<profil>:
  <ziarno>:<przebieg>`, a fikstury i atrapy `daemon/tests/` wstrzykują deterministyczne opóźnienia
  (tests/slowengine.py). Tryb nieskalibrowany (PT-417: nie odtworzył porażki PT-366), więc nie daje dowodu;
- odtworzenie — palniki 1,5× (`--reproduce`): ⌈1,5 × rdzenie⌉ palników bez regulacji, badany moduł sam;
- odtworzenie — pula (`--pool`): badany moduł z towarzyszami w jednym wywołaniu uruchamiacza, palniki 1,5×.

Ważność: przebieg jest nieważny, gdy loadavg1 na starcie > 1,5 × rdzenie, gdy w trakcie biegło inne polecenie
poza piaskownicą (otwarte `outside.started` w dziennikach kart Tablicy, poza krokiem samej pętli) albo obcy
`tools/test_runner.py`. Seria jest poza oknem ciszy (bez przebiegów, kod 3), gdy loadavg1 przed palnikami ≥ próg
ciszy (domyślnie 8) albo trwało inne `outside`.

Raport (`summary.json`, `report.md`) trafia do katalogu wyjścia. Kody wyjścia: 0 — dowód: N/N ważnych przebiegów
z zielonym badanym modułem w trybie dowodowym; 1 — porażka albo nieważny przebieg; 2 — błąd (próg obciążenia
nieosiągnięty przed pierwszym przebiegiem, przerwanie); 3 — poza oknem ciszy; 4 — tryb odtworzenia albo
eksperymentalny bez porażki (to nie jest dowód). Instrukcja: docs/testing.md, „Pętla przebiegów modułu pod obciążeniem”.
"""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from test_runner import default_jobs  # noqa: E402
from test_suites import SERIAL_MODULES  # noqa: E402

# Stały zestaw towarzyszy trybu puli: moduły z prywatną magistralą D-Bus, procesami potomnymi i fiksturą
# `FlowInProcess`, w tym pozostałe moduły z listy K-100. Dwa moduły `[OSOBNO]` (review, dbus) biegną na końcu,
# więc równoległych jest 20 towarzyszy minus 2 `[OSOBNO]` + badany moduł = co najmniej JOBS.
DEFAULT_COMPANIONS = (
    "daemon/tests/test_cardflow_youtrack.py",
    "daemon/tests/test_cardflow_waves.py",
    "daemon/tests/test_daemon_lifecycle.py",
    "daemon/tests/test_cardflow_review.py",
    "daemon/tests/test_cardflow_dbus.py",
    "daemon/tests/test_service_smoke.py",
    "daemon/tests/test_dbus_contract.py",
    "daemon/tests/test_cardflow_base_change_dbus.py",
    "daemon/tests/test_cardflow_caretaker.py",
    "daemon/tests/test_cardflow_close.py",
    "daemon/tests/test_cardflow_merge_staged.py",
    "daemon/tests/test_cardflow_merge_recovery.py",
    "daemon/tests/test_cardflow_environment.py",
    "daemon/tests/test_cardflow_outside_stopped.py",
    "daemon/tests/test_cardflow_ticket_wait.py",
    "daemon/tests/test_cardflow_links_concurrency.py",
    "daemon/tests/test_cardflow_limits.py",
    "daemon/tests/test_runner_engine.py",
    "daemon/tests/test_worktrees_shutdown.py",
    "daemon/tests/test_cardflow_integration_k38.py",
)

# Tryb → (rodzaj, nazwa w raporcie). Tylko rodzaj „dowód” może dać kod 0.
MODES = {
    "bounded": ("dowód", "palniki-ograniczone"),
    "slow": ("eksperyment", "powolny-silnik"),     # nieskalibrowany (PT-417, followups/slow-engine-calibration.md)
    "burners": ("odtworzenie", "palniki-1,5x"),
    "pool": ("odtworzenie", "pula"),
}
PROOF, REPRODUCTION, EXPERIMENT = "dowód", "odtworzenie", "eksperyment"
BAND = (0.8, 1.0)            # pasmo loadavg1 palników ograniczonych względem celu
AIM = 0.9                    # liczba procesów gotowych, do której dąży regulacja (× cel)
START_LIMIT = 1.5            # loadavg1 na starcie > START_LIMIT × rdzenie → przebieg nieważny
QUIET_MAX = 8.0              # loadavg1 przed palnikami ≥ próg → seria poza oknem ciszy
SLOW_ENV = "TIDE_TEST_SLOW"
SLOW_PROFILE = "domyslny"
OWN_STEP_ENV = "TIDE_MODULE_LOOP_STEP"
EXIT_OK, EXIT_FAILED, EXIT_ERROR, EXIT_NOT_QUIET, EXIT_REPRODUCTION = 0, 1, 2, 3, 4

# Palnik kończy się, gdy rodzic zniknie: PR_SET_PDEATHSIG (działa także na palnik wstrzymany SIGSTOP)
# i straż rodzica w pętli, więc nie zostaje po SIGKILL narzędzia.
BURNER_CODE = (
    "import os, signal\n"
    "try:\n"
    "    import ctypes\n"
    "    ctypes.CDLL(None).prctl(1, int(signal.SIGKILL))\n"
    "except Exception:\n"
    "    pass\n"
    "ppid = os.getppid()\n"
    "while True:\n"
    "    for _ in range(2000000):\n"
    "        pass\n"
    "    if os.getppid() != ppid:\n"
    "        break\n"
)


def available_cores():
    return len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1


def read_loadavg():
    return os.getloadavg()


def read_runnable():
    """Liczba procesów gotowych do biegu teraz (4. pole /proc/loadavg, z tym odczytem); None, gdy brak."""
    try:
        return int(Path("/proc/loadavg").read_text(encoding="ascii").split()[3].split("/")[0])
    except (OSError, IndexError, ValueError):
        return None


def _spawn_burner():
    return subprocess.Popen([sys.executable, "-c", BURNER_CODE], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _kill_all(procs):
    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = signal.signal(sig, signal.SIG_IGN)
        except ValueError:  # poza głównym wątkiem
            pass
    try:
        for proc in procs:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler if handler is not None else signal.SIG_DFL)


class Burners:
    """Palniki CPU bez regulacji (tryby odtworzenia): każdy w osobnej grupie procesów, zatrzymywane przez killpg."""

    def __init__(self, count):
        self.count = count
        self.procs = []

    def start(self):
        for _ in range(self.count):
            self.procs.append(_spawn_burner())

    def alive(self):
        return bool(self.procs) and all(proc.poll() is None for proc in self.procs)

    def pids(self):
        return [proc.pid for proc in self.procs]

    def stop(self):
        procs, self.procs = self.procs, []
        _kill_all(procs)


class NoBurners:
    """Powolny silnik: maszyna bez sztucznego obciążenia."""

    count = 0

    def __init__(self, count=0):
        pass

    def start(self):
        pass

    def alive(self):
        return True

    def stop(self):
        pass


def regulate(load1, runnable, active, target, maximum):
    """Nowa liczba czynnych palników ograniczonych.

    `runnable` to procesy gotowe teraz (z odczytem i czynnymi palnikami): pozostałe to test i reszta maszyny.
    Regulacja dąży do AIM × cel procesów gotowych łącznie z testem; loadavg1 powyżej celu zawsze zmniejsza
    liczbę czynnych palników (aż do wstrzymania wszystkich), w paśmie BAND nie dokłada palników.
    """
    others = max(0, (runnable if runnable is not None else active + 1) - active - 1)
    room = math.floor(AIM * target) - others
    if load1 > BAND[1] * target:
        desired = min(room, active - 1)
    elif load1 >= BAND[0] * target:
        desired = min(room, active)
    else:
        desired = room
    return max(0, min(maximum, desired))


class BoundedBurners:
    """Palniki ograniczone: `maximum` procesów, czynnych tyle, ile pozwala `regulate` (pozostałe SIGSTOP).

    Wątek regulatora co `interval` s czyta loadavg i liczbę procesów gotowych; `step()` to jeden krok regulacji
    (wywoływany także wprost w testach z wstrzykniętym loadavg).
    """

    def __init__(self, maximum, target, loadavg=read_loadavg, runnable=read_runnable, interval=1.0,
                 signal_fn=None):
        self.count, self.target, self.interval = maximum, float(target), interval
        self._loadavg, self._runnable = loadavg, runnable
        self._signal = signal_fn or (lambda proc, sig: os.killpg(proc.pid, sig))
        self.procs, self.active = [], 0
        self._stop, self._thread, self._lock = threading.Event(), None, threading.Lock()
        self.range = [None, None]          # najmniej/najwięcej czynnych palników od `take_range`

    def start(self, regulate_in_thread=True):
        for _ in range(self.count):
            self.procs.append(_spawn_burner())
        self.active = self.count
        self.step()
        if regulate_in_thread:
            self._thread = threading.Thread(target=self._run, name="module-loop-regulator", daemon=True)
            self._thread.start()

    def _run(self):
        while not self._stop.wait(self.interval):
            self.step()

    def step(self):
        with self._lock:
            if not self.procs:
                return self.active
            desired = regulate(self._loadavg()[0], self._runnable(), self.active, self.target, len(self.procs))
            for index, proc in enumerate(self.procs):
                want = index < desired
                if want != (index < self.active):
                    try:
                        self._signal(proc, signal.SIGCONT if want else signal.SIGSTOP)
                    except (ProcessLookupError, PermissionError):
                        pass
            self.active = desired
            low, high = self.range
            self.range = [desired if low is None else min(low, desired), desired if high is None else max(high, desired)]
            return desired

    def take_range(self):
        with self._lock:
            found, self.range = self.range, [self.active, self.active]
            return found

    def alive(self):
        return bool(self.procs) and all(proc.poll() is None for proc in self.procs)

    def pids(self):
        return [proc.pid for proc in self.procs]

    def stop(self):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        with self._lock:
            procs, self.procs = self.procs, []
        _kill_all(procs)


def run_command(command, log_path, cwd=ROOT, env=None):
    """Uruchamia polecenie przebiegu z logiem; przy wyjątku lub sygnale zabija jego grupę procesów."""
    with open(log_path, "w", encoding="utf-8") as stream:
        proc = subprocess.Popen(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True,
                                env={**os.environ, **env} if env else None)
        try:
            return proc.wait()
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            raise


def head_sha(root=ROOT):
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def default_board():
    return os.environ.get("TIDE_BOARD_DIR") or os.path.expanduser("~/DevWorkspace/tide-board")


class OutsideScanner:
    """Otwarte `outside.started` bez `outside.finished` w dziennikach kart Tablicy (tylko odczyt).

    Krok samej pętli pomija po nazwie kroku (`own_steps`) albo po worktree polecenia (`own_worktrees`,
    domyślnie repozytorium narzędzia: Tide uruchamia polecenie `outside` w worktree ticketu).
    """

    def __init__(self, board, own_steps=(), own_worktrees=()):
        self.flow = Path(board) / "flow"
        self.own_steps = {step for step in own_steps if step}
        self.own_worktrees = {os.path.realpath(path) for path in own_worktrees if path}
        self._cache = {}

    def _open_in(self, journal):
        try:
            stat = journal.stat()
        except OSError:
            return []
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(journal)
        if cached and cached[0] == key:
            return cached[1]
        started = {}
        try:
            with journal.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "outside.started":
                        started[event.get("step")] = event
                    elif event.get("type") == "outside.finished":
                        started.pop(event.get("step"), None)
        except OSError:
            return []
        found = [{"card": journal.parent.name, "step": step or "", "command": event.get("command") or "",
                  "worktree": event.get("worktree") or ""} for step, event in started.items()]
        self._cache[journal] = (key, found)
        return found

    def __call__(self):
        found = []
        for journal in sorted(self.flow.glob("*/journal.jsonl")):
            for item in self._open_in(journal):
                if item["step"] in self.own_steps:
                    continue
                if item["worktree"] and os.path.realpath(item["worktree"]) in self.own_worktrees:
                    continue
                found.append(item)
        return found


def foreign_runners(own_pid=None, proc_root="/proc"):
    """Procesy `tools/test_runner.py` (bez `--worker`) spoza drzewa procesów narzędzia."""
    own_pid = own_pid or os.getpid()
    parents, commands = {}, {}
    for entry in Path(proc_root).iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            cmdline = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        try:
            parents[int(entry.name)] = int(stat.rpartition(")")[2].split()[1])
        except (IndexError, ValueError):
            continue
        commands[int(entry.name)] = [part.decode("utf-8", "replace") for part in cmdline if part]
    found = []
    for pid, argv in commands.items():
        if not any(part.endswith("tools/test_runner.py") for part in argv) or "--worker" in argv:
            continue
        ancestor, seen = pid, set()
        while ancestor and ancestor not in seen and ancestor != own_pid:
            seen.add(ancestor)
            ancestor = parents.get(ancestor, 0)
        if ancestor != own_pid:
            found.append({"pid": pid, "command": " ".join(argv)})
    return found


def wait_for_threshold(threshold, timeout, loadavg, sleep=time.sleep, clock=time.monotonic, poll=2.0):
    """Czeka, aż loadavg1 ≥ próg. Zwraca (osiągnięty, ostatni odczyt)."""
    deadline = clock() + timeout
    while True:
        reading = tuple(loadavg())
        if reading[0] >= threshold:
            return True, reading
        if clock() >= deadline:
            return False, reading
        sleep(poll)


def split_failures(summary_path, module):
    """Z summary.json uruchamiacza: (badany moduł zielony, nazwy nieudanych badanego, nazwy nieudanych towarzyszy)."""
    try:
        data = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, ["brak summary.json uruchamiacza"], []
    tested, companions, seen = [], [], False
    for result in data.get("results", []):
        is_tested = result["module"] == module
        seen = seen or is_tested
        if not result.get("returncode"):
            continue
        names = (result.get("failures", []) + result.get("errors", []) + result.get("unexpected_successes", [])
                 or [result.get("crash") or f"proces zakończony kodem {result['returncode']}"])
        (tested if is_tested else companions).extend(f"{result['module']}: {name}" for name in names)
    if not seen:
        tested.append("badany moduł nie ma wyniku w summary.json")
    return not tested, tested, companions


def find_summary(run_dir):
    found = sorted(Path(run_dir).glob("run-*/summary.json"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else Path(run_dir) / "run-missing" / "summary.json"


def _describe_outside(item):
    return f"karta {item['card']}, krok {item['step']}, polecenie `{item['command']}`"


def execute_run(index, command, run_dir, module, burners, setup, deps, first):
    """Jeden przebieg: oczekiwanie na próg, próbkowanie loadavg/`outside`/palników, uruchomienie polecenia."""
    run_dir.mkdir(parents=True, exist_ok=True)
    threshold = setup["threshold"]
    if threshold is None:
        reached, start = True, tuple(deps["loadavg"]())
    else:
        reached, start = wait_for_threshold(threshold, setup["wait_timeout"], deps["loadavg"], deps["sleep"],
                                            deps["clock"])
    run = {"index": index, "loadavg_start": list(start), "threshold_reached": reached, "warnings": [],
           "directory": str(run_dir), "seed": setup["slow"](index) if setup["slow"] else None}
    if not reached and first:
        run.update(valid=False, skipped=True, reasons=["próg loadavg1 nieosiągnięty przed pierwszym przebiegiem"])
        return run
    if hasattr(burners, "take_range"):
        burners.take_range()
    samples, burner_dead, done = [start[0]], [not burners.alive()], threading.Event()
    outside, runners = {}, {}

    def look():
        for item in deps["outside"]():
            outside[(item["card"], item["step"])] = item
        for item in deps["foreign_runners"]():
            runners[item["pid"]] = item

    look()

    def monitor():
        while not done.wait(setup["sample_interval"]):
            samples.append(deps["loadavg"]()[0])
            burner_dead[0] = burner_dead[0] or not burners.alive()
            look()

    thread = threading.Thread(target=monitor, daemon=True)
    started = time.monotonic()
    thread.start()
    try:
        env = {SLOW_ENV: run["seed"]} if run["seed"] else None
        returncode = deps["run_command"](command, run_dir / "runner.log", env=env)
    finally:
        done.set()
        thread.join()
    seconds = time.monotonic() - started
    end = tuple(deps["loadavg"]())
    samples.append(end[0])
    look()
    burner_dead[0] = burner_dead[0] or not burners.alive()
    tested_green, tested_failed, companion_failed = split_failures(find_summary(run_dir), module)
    reasons = []
    if not reached:
        reasons.append("loadavg1 na starcie poniżej progu (przekroczony czas oczekiwania)")
    if start[0] > START_LIMIT * setup["cores"]:
        reasons.append(f"loadavg1 na starcie {start[0]:.2f} > {START_LIMIT:g} × rdzenie ({START_LIMIT * setup['cores']:g})")
    if burner_dead[0]:
        reasons.append("palnik zginął w trakcie przebiegu")
    for item in outside.values():
        reasons.append("inne polecenie poza piaskownicą w trakcie przebiegu: " + _describe_outside(item))
    for item in runners.values():
        reasons.append(f"obcy tools/test_runner.py w trakcie przebiegu: pid {item['pid']}, `{item['command']}`")
    minimum, maximum = min(samples), max(samples)
    if threshold is not None and not reasons and minimum < threshold:
        run["warnings"].append(f"spadek obciążenia: minimum loadavg1 z próbek {minimum:.2f} < próg {threshold:g}")
    if setup["target"] is not None and maximum > setup["target"]:
        run["warnings"].append(f"loadavg1 ponad cel: maksimum z próbek {maximum:.2f} > cel {setup['target']:g}")
    if hasattr(burners, "take_range"):
        run["burners_active"] = burners.take_range()
    run.update(valid=not reasons, reasons=reasons, loadavg_end=list(end), loadavg1_min=minimum,
               loadavg1_max=maximum, samples=len(samples), returncode=returncode, seconds=seconds,
               module_green=tested_green, failed_tested=tested_failed, failed_companions=companion_failed,
               outside=list(outside.values()), foreign_runners=list(runners.values()))
    return run


def run_loop(module, runs=20, mode="bounded", companions=(), burner_count=None, threshold=None, target=None,
             wait_timeout=300.0, output=None, sample_interval=3.0, deps=None, root=ROOT, argv=None,
             slow_seed=None, slow_profile=SLOW_PROFILE, quiet_max=QUIET_MAX, cores=None, board=None,
             own_steps=(), own_worktrees=None):
    """Całość pętli. `deps` pozwala wstrzyknąć loadavg, palniki, polecenie przebiegu, dzienniki, sen i zegar."""
    if mode not in MODES:
        raise ValueError(f"nieznany tryb {mode!r}")
    kind, label = MODES[mode]
    cores = available_cores() if cores is None else cores
    deps = {"loadavg": read_loadavg, "runnable": read_runnable, "burners": Burners, "bounded": BoundedBurners,
            "run_command": run_command, "sleep": time.sleep, "clock": time.monotonic, "jobs": default_jobs,
            **(deps or {})}
    if "outside" not in deps:
        deps["outside"] = OutsideScanner(board or default_board(), own_steps,
                                         [str(root)] if own_worktrees is None else own_worktrees)
    deps.setdefault("foreign_runners", foreign_runners)
    target = None if mode != "bounded" else float(cores if target is None else target)
    if mode == "bounded":
        burner_count = math.ceil(target) if burner_count is None else burner_count
        threshold = BAND[0] * target if threshold is None else float(threshold)
    elif mode == "slow":
        burner_count, threshold = 0, None
    else:
        burner_count = math.ceil(1.5 * cores) if burner_count is None else burner_count
        threshold = float(cores) if threshold is None else float(threshold)
    if mode == "slow":
        slow_seed = str(slow_seed if slow_seed is not None else int(time.time()) % 100000)
    output = Path(output) if output else root / "build" / "module-loop" / (
        Path(module).stem + time.strftime("-%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    companions = [m for m in dict.fromkeys(companions) if m != module] if mode == "pool" else []
    modules = [module, *companions]
    jobs = deps["jobs"]()
    summary = {"head": head_sha(root), "command": " ".join(argv if argv is not None else sys.argv),
               "runner_command": None, "mode": mode, "mode_kind": kind, "mode_label": label, "module": module,
               "companions": companions, "module_serial": module in SERIAL_MODULES, "cores": cores,
               "burners": burner_count, "threshold": threshold, "target": target, "jobs": jobs,
               "slow_profile": slow_profile if mode == "slow" else None,
               "slow_seed": slow_seed if mode == "slow" else None, "quiet_max": quiet_max,
               "parallel_modules": len([m for m in modules if m not in SERIAL_MODULES]), "runs_requested": runs,
               "runs": [], "error": None}
    setup = {"threshold": threshold, "target": target, "cores": cores, "wait_timeout": wait_timeout,
             "sample_interval": sample_interval,
             "slow": (lambda index: f"{slow_profile}:{slow_seed}:{index}") if mode == "slow" else None}
    # Okno ciszy: odczyt przed uruchomieniem palników.
    before = tuple(deps["loadavg"]())
    other_outside = list(deps["outside"]())
    quiet_reasons = []
    if before[0] >= quiet_max:
        quiet_reasons.append(f"loadavg1 przed palnikami {before[0]:.2f} ≥ {quiet_max:g}")
    quiet_reasons += ["trwa inne polecenie poza piaskownicą: " + _describe_outside(item) for item in other_outside]
    summary.update(loadavg_before=list(before), quiet=not quiet_reasons, quiet_reasons=quiet_reasons,
                   outside_before=other_outside)
    interrupted = None
    if quiet_reasons:
        summary["error"] = "seria poza oknem ciszy: " + "; ".join(quiet_reasons) + "; bez przebiegów"
    else:
        if mode == "bounded":
            burners = deps["bounded"](burner_count, target, loadavg=deps["loadavg"], runnable=deps["runnable"])
        elif mode == "slow":
            burners = NoBurners()
        else:
            burners = deps["burners"](burner_count)
        try:
            burners.start()
            for index in range(1, runs + 1):
                run_dir = output / f"run-{index:02d}"
                command = [sys.executable, str(root / "tools" / "test_runner.py"), "--modules", *modules,
                           "--output", str(run_dir)]
                run = execute_run(index, command, run_dir, module, burners, setup, deps, first=index == 1)
                if run.get("skipped"):
                    summary["error"] = (run["reasons"][0] + f" ({run['loadavg_start'][0]:.2f} < {threshold:g}); "
                                        "bez przebiegów")
                    break
                summary["runs"].append(run)
                if index == 1:
                    summary["runner_command"] = " ".join(command)
        except BaseException as exc:
            summary["error"] = f"przerwano: {type(exc).__name__} {exc}".strip()
            interrupted = exc
        finally:
            burners.stop()
    runs_done = summary["runs"]
    valid = sum(1 for run in runs_done if run["valid"])
    green = sum(1 for run in runs_done if run["module_green"])
    companion_failures = sum(len(run["failed_companions"]) for run in runs_done)
    passed = summary["error"] is None and len(runs_done) == runs and valid == runs and green == runs
    ok = passed and kind == PROOF
    if ok:
        code = EXIT_OK
    elif not summary["quiet"]:
        code = EXIT_NOT_QUIET
    elif summary["error"]:
        code = EXIT_ERROR
    elif passed:
        code = EXIT_REPRODUCTION
    else:
        code = EXIT_FAILED
    summary.update(valid_runs=valid, green_runs=green, companion_failures=companion_failures, ok=ok, exit_code=code)
    write_report(summary, output)
    if interrupted is not None:
        raise interrupted
    return summary, code


def format_load(values):
    return "/".join(f"{value:.2f}" for value in values)


def _verdict(summary):
    if summary["ok"]:
        return "DOWÓD (kod 0)"
    if not summary["quiet"]:
        return f"POZA OKNEM CISZY — wszystkie przebiegi nieważne (kod {summary['exit_code']})"
    if summary["exit_code"] == EXIT_REPRODUCTION:
        what = "tryb eksperymentalny" if summary["mode_kind"] == EXPERIMENT else "tryb odtworzenia"
        return f"{what}: brak porażki, ale to NIE jest dowód (kod {summary['exit_code']})"
    return f"NIEZALICZONE (kod {summary['exit_code']})"


def write_report(summary, output):
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Pętla modułu {summary['module']}", "",
             f"- SHA HEAD: {summary['head']}", f"- Polecenie: `{summary['command']}`",
             f"- Polecenie przebiegu (pierwszego): `{summary['runner_command'] or 'brak'}`",
             f"- Tryb: {summary['mode_kind']}: {summary['mode_label']}",
             f"- Badany moduł: {summary['module']} ({'[OSOBNO] (SERIAL_MODULES)' if summary['module_serial'] else 'równolegle'})",
             f"- Towarzysze ({len(summary['companions'])}): " + (", ".join(summary["companions"]) or "brak"),
             f"- Rdzenie: {summary['cores']}, palniki: {summary['burners']}, "
             f"próg loadavg1: {'brak' if summary['threshold'] is None else format(summary['threshold'], 'g')}, "
             f"cel loadavg1: {'brak' if summary['target'] is None else format(summary['target'], 'g')}, "
             f"JOBS uruchamiacza: {summary['jobs']}"]
    if summary["mode"] == "slow":
        lines.append(f"- Powolny silnik: profil {summary['slow_profile']}, ziarno {summary['slow_seed']} "
                     f"(przebieg n: `{SLOW_ENV}={summary['slow_profile']}:{summary['slow_seed']}:n`)")
    if summary["mode_kind"] == REPRODUCTION:
        lines.append("- **Tryb odtworzenia:** służy do odtwarzania porażki; wynik nie jest dowodem stabilności "
                     "i nigdy nie daje kodu 0.")
    if summary["mode_kind"] == EXPERIMENT:
        lines.append("- **Tryb eksperymentalny:** powolny silnik nie jest skalibrowany (nie odtworzył znanej porażki "
                     "PT-366); wynik nie jest dowodem stabilności i nigdy nie daje kodu 0. Dowód K-100: palniki "
                     "ograniczone.")
    if summary["mode"] == "pool":
        full = summary["parallel_modules"] >= summary["jobs"]
        lines.append(f"- Moduły równoległe w wywołaniu: {summary['parallel_modules']} przy JOBS={summary['jobs']} "
                     f"({'pula pełna' if full else 'pula NIEPEŁNA: mniej modułów równoległych niż JOBS'})")
    lines.append(f"- Okno ciszy: {'tak' if summary['quiet'] else 'NIE'} (loadavg przed palnikami "
                 f"{format_load(summary['loadavg_before'])}, próg {summary['quiet_max']:g})")
    for reason in summary["quiet_reasons"]:
        lines.append(f"  - {reason}")
    if summary["error"]:
        lines.append(f"- **BŁĄD:** {summary['error']}")
    lines += ["", f"**Wynik:** {summary['valid_runs']}/{summary['runs_requested']} ważnych przebiegów, "
              f"badany moduł zielony w {summary['green_runs']}/{summary['runs_requested']}, "
              f"porażki towarzyszy: {summary['companion_failures']} — " + _verdict(summary), "",
              "| # | ważny | ziarno | loadavg start | loadavg koniec | loadavg1 min–max | palniki czynne | kod | "
              "czas [s] | badany zielony |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for run in summary["runs"]:
        active = run.get("burners_active")
        lines.append(f"| {run['index']} | {'tak' if run['valid'] else 'NIEWAŻNY'} | {run['seed'] or '—'} | "
                     f"{format_load(run['loadavg_start'])} | {format_load(run['loadavg_end'])} | "
                     f"{run['loadavg1_min']:.2f}–{run['loadavg1_max']:.2f} | "
                     f"{'—' if not active or active[0] is None else f'{active[0]}–{active[1]}'} | "
                     f"{run['returncode']} | {run['seconds']:.1f} | {'tak' if run['module_green'] else 'NIE'} |")
    for run in summary["runs"]:
        details = ([f"nieważny: {reason}" for reason in run["reasons"]] + [f"ostrzeżenie: {w}" for w in run["warnings"]]
                   + [f"nieudany badany: {name}" for name in run["failed_tested"]]
                   + [f"nieudany towarzysz: {name}" for name in run["failed_companions"]])
        if details:
            lines += ["", f"### Przebieg {run['index']} (logi: `{run['directory']}`)"] + [f"- {item}" for item in details]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("module", help="badany moduł testów, ścieżka względem repozytorium")
    parser.add_argument("--runs", type=int, default=20, help="liczba przebiegów (domyślnie 20)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--slow", nargs="?", const="", metavar="ZIARNO",
                       help="eksperyment (bez dowodu, nieskalibrowany): powolny silnik bez palników "
                            "(ziarno domyślnie z zegara, zapisane w raporcie)")
    modes.add_argument("--reproduce", action="store_true",
                       help="odtworzenie: ⌈1,5 × rdzenie⌉ palników bez regulacji (nie daje dowodu)")
    modes.add_argument("--pool", action="store_true",
                       help="odtworzenie: badany moduł razem z towarzyszami i palnikami 1,5× (nie daje dowodu)")
    parser.add_argument("--slow-profile", default=SLOW_PROFILE, help="profil powolnego silnika (tests/slowengine.py)")
    parser.add_argument("--with", dest="companions", nargs="+", metavar="MODUŁ",
                        help="towarzysze trybu puli (domyślnie stały zestaw DEFAULT_COMPANIONS)")
    parser.add_argument("--target", type=float,
                        help="cel loadavg1 palników ograniczonych (domyślnie liczba rdzeni dostępnych procesowi)")
    parser.add_argument("--burners", type=int,
                        help="liczba palników (ograniczone: najwięcej czynnych, domyślnie ⌈cel⌉; odtworzenie: "
                             "⌈1,5 × rdzenie⌉)")
    parser.add_argument("--threshold", type=float,
                        help="próg loadavg1 przed przebiegiem (ograniczone: 0,8 × cel; odtworzenie: liczba rdzeni)")
    parser.add_argument("--wait", type=float, default=300.0, help="najdłuższy czas dojścia do progu [s]")
    parser.add_argument("--quiet-max", type=float, default=QUIET_MAX,
                        help="okno ciszy: loadavg1 przed palnikami musi być poniżej (domyślnie 8)")
    parser.add_argument("--board", help="tablica Tide z dziennikami kart (domyślnie $TIDE_BOARD_DIR albo "
                                        "~/DevWorkspace/tide-board); tylko odczyt")
    parser.add_argument("--own-step", action="append", default=[],
                        help=f"krok Tide samej pętli pomijany przy szukaniu innych outside (także ${OWN_STEP_ENV})")
    parser.add_argument("--own-worktree", action="append", default=[],
                        help="worktree, którego polecenia outside należą do pętli (domyślnie to repozytorium)")
    parser.add_argument("--output", type=Path, help="katalog wyjścia (domyślnie build/module-loop/…)")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    mode = "slow" if args.slow is not None else "pool" if args.pool else "burners" if args.reproduce else "bounded"
    if args.runs < 1:
        parser.error("--runs musi być dodatnie")
    if args.companions and not args.pool:
        parser.error("--with wymaga --pool")
    if args.burners is not None and args.burners < 1:
        parser.error("--burners musi być dodatnie")
    if args.target is not None and (args.target <= 0 or mode != "bounded"):
        parser.error("--target dotyczy tylko palników ograniczonych i musi być dodatni")
    if mode == "slow" and (args.burners is not None or args.threshold is not None):
        parser.error("--slow biegnie bez palników: bez --burners i --threshold")
    if not (ROOT / args.module).is_file() or not Path(args.module).name.startswith("test"):
        parser.error(f"nieznany moduł testów: {args.module}")
    companions = args.companions or (DEFAULT_COMPANIONS if args.pool else ())
    missing = [m for m in companions if not (ROOT / m).is_file()]
    if missing:
        parser.error("nieznani towarzysze: " + ", ".join(missing))
    if args.pool and not args.companions:
        parallel = [m for m in dict.fromkeys([args.module, *companions]) if m not in SERIAL_MODULES]
        if len(parallel) < default_jobs():
            parser.error(f"zestaw domyślny daje {len(parallel)} modułów równoległych przy JOBS={default_jobs()}; "
                         "podaj więcej towarzyszy przez --with")
    if mode == "slow":
        sys.path.insert(0, str(ROOT / "daemon" / "tests"))
        import slowengine  # noqa: E402 — sprawdzenie profilu przed palnikami i przebiegami
        if args.slow_profile not in slowengine.PROFILES:
            parser.error(f"nieznany profil powolnego silnika: {args.slow_profile}")
    own_steps = [*args.own_step, os.environ.get(OWN_STEP_ENV, "")]
    own_worktrees = [str(ROOT), *args.own_worktree]

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    _, code = run_loop(args.module, args.runs, mode, companions, args.burners, args.threshold, args.target,
                       args.wait, args.output, slow_seed=args.slow or None, slow_profile=args.slow_profile,
                       quiet_max=args.quiet_max, board=args.board, own_steps=own_steps, own_worktrees=own_worktrees,
                       argv=[sys.argv[0], *(argv if argv is not None else sys.argv[1:])])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
