#!/usr/bin/env python3
"""Pętla przebiegów jednego modułu testów pod obciążeniem CPU (K-100, PT-363).

Każdy przebieg to osobne wywołanie publicznego `tools/test_runner.py --modules … --output …`; narzędzie nie
wywołuje unittest samo. Obciążenie robią palniki CPU w czystym Pythonie (bez `stress-ng`). Dwa tryby:

- palniki (domyślny): w przebiegu biegnie tylko badany moduł;
- pula (`--pool`): badany moduł biegnie w jednym wywołaniu uruchamiacza razem z towarzyszami, z domyślnym
  `--jobs`, więc uruchamiacz dzieli moduły na równoległe i `[OSOBNO]` tak jak w pełnym `make test`.

Raport (`summary.json`, `report.md`) trafia do katalogu wyjścia; kod wyjścia 0 tylko dla N/N ważnych przebiegów
z zielonym badanym modułem. Instrukcja: docs/testing.md, „Pętla przebiegów modułu pod obciążeniem”.
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

# Palnik kończy się, gdy rodzic zniknie (straż rodzica), więc nie zostaje po SIGKILL narzędzia.
BURNER_CODE = (
    "import os\n"
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


class Burners:
    """Palniki CPU: każdy w osobnej grupie procesów, zatrzymywane przez killpg."""

    def __init__(self, count):
        self.count = count
        self.procs = []

    def start(self):
        for _ in range(self.count):
            self.procs.append(subprocess.Popen([sys.executable, "-c", BURNER_CODE], start_new_session=True,
                                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL))

    def alive(self):
        return bool(self.procs) and all(proc.poll() is None for proc in self.procs)

    def pids(self):
        return [proc.pid for proc in self.procs]

    def stop(self):
        previous = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous[sig] = signal.signal(sig, signal.SIG_IGN)
            except ValueError:  # poza głównym wątkiem
                pass
        try:
            for proc in self.procs:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            for proc in self.procs:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            self.procs = []
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler if handler is not None else signal.SIG_DFL)


def run_command(command, log_path, cwd=ROOT):
    """Uruchamia polecenie przebiegu z logiem; przy wyjątku lub sygnale zabija jego grupę procesów."""
    with open(log_path, "w", encoding="utf-8") as stream:
        proc = subprocess.Popen(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
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


def execute_run(index, command, run_dir, module, burners, threshold, wait_timeout, sample_interval, deps,
                first):
    """Jeden przebieg: oczekiwanie na próg, próbkowanie loadavg i palników, uruchomienie polecenia."""
    run_dir.mkdir(parents=True, exist_ok=True)
    reached, start = wait_for_threshold(threshold, wait_timeout, deps["loadavg"], deps["sleep"], deps["clock"])
    run = {"index": index, "loadavg_start": list(start), "threshold_reached": reached, "warnings": [],
           "directory": str(run_dir)}
    if not reached and first:
        run.update(valid=False, skipped=True, reasons=["próg loadavg1 nieosiągnięty przed pierwszym przebiegiem"])
        return run
    samples, burner_dead, done = [start[0]], [not burners.alive()], threading.Event()

    def monitor():
        while not done.wait(sample_interval):
            samples.append(deps["loadavg"]()[0])
            burner_dead[0] = burner_dead[0] or not burners.alive()

    thread = threading.Thread(target=monitor, daemon=True)
    started = time.monotonic()
    thread.start()
    try:
        returncode = deps["run_command"](command, run_dir / "runner.log")
    finally:
        done.set()
        thread.join()
    seconds = time.monotonic() - started
    end = tuple(deps["loadavg"]())
    samples.append(end[0])
    burner_dead[0] = burner_dead[0] or not burners.alive()
    tested_green, tested_failed, companion_failed = split_failures(find_summary(run_dir), module)
    reasons = []
    if not reached:
        reasons.append("loadavg1 na starcie poniżej progu (przekroczony czas oczekiwania)")
    if burner_dead[0]:
        reasons.append("palnik zginął w trakcie przebiegu")
    minimum = min(samples)
    if reasons == [] and minimum < threshold:
        run["warnings"].append(f"spadek obciążenia: minimum loadavg1 z próbek {minimum:.2f} < próg {threshold:g}")
    run.update(valid=not reasons, reasons=reasons, loadavg_end=list(end), loadavg1_min=minimum,
               samples=len(samples), returncode=returncode, seconds=seconds, module_green=tested_green,
               failed_tested=tested_failed, failed_companions=companion_failed)
    return run


def run_loop(module, runs=20, pool=False, companions=(), burner_count=None, threshold=None, wait_timeout=300.0,
             output=None, sample_interval=3.0, deps=None, root=ROOT, argv=None):
    """Całość pętli. `deps` pozwala wstrzyknąć loadavg, palniki, polecenie przebiegu, sen i zegar."""
    cores = available_cores()
    burner_count = math.ceil(1.5 * cores) if burner_count is None else burner_count
    threshold = float(cores) if threshold is None else float(threshold)
    deps = {"loadavg": read_loadavg, "burners": Burners, "run_command": run_command, "sleep": time.sleep,
            "clock": time.monotonic, "jobs": default_jobs, **(deps or {})}
    output = Path(output) if output else root / "build" / "module-loop" / (
        Path(module).stem + time.strftime("-%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    companions = [m for m in dict.fromkeys(companions) if m != module] if pool else []
    modules = [module, *companions]
    jobs = deps["jobs"]()
    summary = {"head": head_sha(root), "command": " ".join(argv if argv is not None else sys.argv),
               "runner_command": None, "mode": "pool" if pool else "burners", "module": module, "companions": companions,
               "module_serial": module in SERIAL_MODULES, "cores": cores, "burners": burner_count,
               "threshold": threshold, "jobs": jobs,
               "parallel_modules": len([m for m in modules if m not in SERIAL_MODULES]), "runs_requested": runs, "runs": [], "error": None}
    burners = deps["burners"](burner_count)
    try:
        burners.start()
        for index in range(1, runs + 1):
            run_dir = output / f"run-{index:02d}"
            command = [sys.executable, str(root / "tools" / "test_runner.py"), "--modules", *modules,
                       "--output", str(run_dir)]
            run = execute_run(index, command, run_dir, module, burners, threshold, wait_timeout, sample_interval,
                              deps, first=index == 1)
            if run.get("skipped"):
                summary["error"] = run["reasons"][0] + f" ({run['loadavg_start'][0]:.2f} < {threshold:g}); bez przebiegów"
                break
            summary["runs"].append(run)
            if index == 1:
                summary["runner_command"] = " ".join(command)
    except BaseException as exc:
        summary["error"] = f"przerwano: {type(exc).__name__} {exc}".strip()
        interrupted = exc
    else:
        interrupted = None
    finally:
        burners.stop()
    runs_done = summary["runs"]
    valid = sum(1 for run in runs_done if run["valid"])
    green = sum(1 for run in runs_done if run["module_green"])
    companion_failures = sum(len(run["failed_companions"]) for run in runs_done)
    ok = (summary["error"] is None and len(runs_done) == runs and valid == runs and green == runs)
    summary.update(valid_runs=valid, green_runs=green, companion_failures=companion_failures, ok=ok)
    write_report(summary, output)
    if interrupted is not None:
        raise interrupted
    return summary, (0 if ok else 2 if summary["error"] else 1)


def format_load(values):
    return "/".join(f"{value:.2f}" for value in values)


def write_report(summary, output):
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Pętla modułu {summary['module']}", "",
             f"- SHA HEAD: {summary['head']}", f"- Polecenie: `{summary['command']}`",
             f"- Polecenie przebiegu (pierwszego): `{summary['runner_command'] or 'brak'}`",
             f"- Tryb: {'pula (z towarzyszami)' if summary['mode'] == 'pool' else 'palniki'}",
             f"- Badany moduł: {summary['module']} ({'[OSOBNO] (SERIAL_MODULES)' if summary['module_serial'] else 'równolegle'})",
             f"- Towarzysze ({len(summary['companions'])}): " + (", ".join(summary["companions"]) or "brak"),
             f"- Rdzenie: {summary['cores']}, palniki: {summary['burners']}, próg loadavg1: {summary['threshold']:g}, "
             f"JOBS uruchamiacza: {summary['jobs']}"]
    if summary["mode"] == "pool":
        full = summary["parallel_modules"] >= summary["jobs"]
        lines.append(f"- Moduły równoległe w wywołaniu: {summary['parallel_modules']} przy JOBS={summary['jobs']} "
                     f"({'pula pełna' if full else 'pula NIEPEŁNA: mniej modułów równoległych niż JOBS'})")
    if summary["error"]:
        lines.append(f"- **BŁĄD:** {summary['error']}")
    lines += ["", f"**Wynik:** {summary['valid_runs']}/{summary['runs_requested']} ważnych przebiegów, "
              f"badany moduł zielony w {summary['green_runs']}/{summary['runs_requested']}, "
              f"porażki towarzyszy: {summary['companion_failures']} — "
              + ("OK (kod 0)" if summary["ok"] else "NIEZALICZONE (kod ≠ 0)"), "",
              "| # | ważny | loadavg start | loadavg koniec | min loadavg1 | kod | czas [s] | badany zielony |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for run in summary["runs"]:
        lines.append(f"| {run['index']} | {'tak' if run['valid'] else 'NIEWAŻNY'} | {format_load(run['loadavg_start'])} | "
                     f"{format_load(run['loadavg_end'])} | {run['loadavg1_min']:.2f} | {run['returncode']} | "
                     f"{run['seconds']:.1f} | {'tak' if run['module_green'] else 'NIE'} |")
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
    parser.add_argument("--pool", action="store_true", help="tryb puli: badany moduł razem z towarzyszami")
    parser.add_argument("--with", dest="companions", nargs="+", metavar="MODUŁ",
                        help="towarzysze trybu puli (domyślnie stały zestaw DEFAULT_COMPANIONS)")
    parser.add_argument("--burners", type=int, help="liczba palników (domyślnie ⌈1,5 × rdzenie⌉)")
    parser.add_argument("--threshold", type=float, help="próg loadavg1 (domyślnie liczba rdzeni)")
    parser.add_argument("--wait", type=float, default=300.0, help="najdłuższy czas dojścia do progu [s]")
    parser.add_argument("--output", type=Path, help="katalog wyjścia (domyślnie build/module-loop/…)")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs musi być dodatnie")
    if args.companions and not args.pool:
        parser.error("--with wymaga --pool")
    if args.burners is not None and args.burners < 1:
        parser.error("--burners musi być dodatnie")
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

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    _, code = run_loop(args.module, args.runs, args.pool, companions, args.burners, args.threshold, args.wait,
                       args.output, argv=[sys.argv[0], *(argv if argv is not None else sys.argv[1:])])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
