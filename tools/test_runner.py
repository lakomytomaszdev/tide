"""Testy unittest w osobnych procesach, z pełnym raportem po wszystkich modułach."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import fnmatch
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass

if __package__:
    from .test_suites import (CHANGE_MAP, SERIAL_MODULES, SUITES, IMPORT_ROOTS,
                             SERVICE_CONTRACT_MODULES, SERVICE_PREFIX, SHARED_TEST_MODULES)
    from .test_dependencies import ImportGraph, is_test_support
else:
    from test_suites import (CHANGE_MAP, SERIAL_MODULES, SUITES, IMPORT_ROOTS,
                            SERVICE_CONTRACT_MODULES, SERVICE_PREFIX, SHARED_TEST_MODULES)
    from test_dependencies import ImportGraph, is_test_support

ROOT = Path(__file__).resolve().parents[1]


def default_jobs():
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
    return max(1, cores - 2)


SLOT_HELD = "TIDE_TEST_SLOT_HELD"


def slots_dir():
    """Katalog wspólnej puli miejsc: jeden dla wszystkich przebiegów testów użytkownika na tej maszynie."""
    override = os.environ.get("TIDE_TEST_SLOTS_DIR")
    if override:
        return Path(override)
    runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(runtime) / "tide-test-slots"


class MachineSlots:
    """Wspólna pula miejsc na moduły testów dla wszystkich przebiegów naraz (06.11: cztery równoległe `make test`
    brały po 14 procesów na 16 rdzeniach — obciążenie 24, losowe porażki testów zależnych od czasu i kolejka
    poleceń poza piaskownicą). Miejsce to blokada `flock` na pliku: zwalnia się samo, gdy proces zginie.
    Moduł uruchomiony wewnątrz innego modułu (test samego uruchamiacza) nie czeka na pulę."""

    def __init__(self, size=None, directory=None):
        self.size = max(1, int(size or os.environ.get("TIDE_TEST_SLOTS") or default_jobs()))
        self.directory = Path(directory) if directory else slots_dir()
        self.disabled = bool(os.environ.get(SLOT_HELD))
        self.waited = False

    def acquire(self):
        if self.disabled:
            return None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            for index in range(self.size):
                (self.directory / f"slot-{index}.lock").touch()
        except OSError:
            # Piaskownica kroku widzi katalog puli tylko do odczytu (06.11: pięć przebiegów w piaskownicach Codexa
            # pominęło pulę — 45 procesów testów, obciążenie 75). `flock` działa na pliku otwartym do odczytu, więc
            # wystarczy, że miejsca założył wcześniej przebieg na hoście. Bez żadnego pliku — przebieg bez puli.
            if not any((self.directory / f"slot-{index}.lock").is_file() for index in range(self.size)):
                self.disabled = True
                return None
        while True:
            for index in range(self.size):
                path = self.directory / f"slot-{index}.lock"
                try:
                    handle = open(path, "a+")
                except OSError:
                    try:
                        handle = open(path, "r")
                    except OSError:
                        continue
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return handle
                except BlockingIOError:
                    handle.close()
            if not self.waited:
                self.waited = True
                print(f"Pula testów maszyny zajęta ({self.size} miejsc) — moduły czekają na inne przebiegi.",
                      flush=True)
            time.sleep(0.2)

    @staticmethod
    def release(handle):
        if handle is not None:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


def modules_for(entries, root=ROOT):
    modules = set()
    for directory, pattern in entries:
        folder = root / directory
        if not folder.is_dir():
            raise ValueError(f"Brak katalogu testów: {directory}")
        # Tak samo jak discover: test*.py także w podpakietach z __init__.py.
        def walk(path):
            for child in sorted(path.iterdir()):
                if child.is_file() and fnmatch.fnmatch(child.name, pattern) and child.suffix == ".py":
                    modules.add(child.relative_to(root).as_posix())
                elif child.is_dir() and (child / "__init__.py").is_file():
                    walk(child)
        walk(folder)
        # Hook pakietu może zmieniać zbiór testów. Nie dzielimy takiego discover.
        for package in [folder, *[p.parent for p in folder.rglob("__init__.py")]]:
            init = package / "__init__.py"
            if init.exists() and "load_tests" in init.read_text(encoding="utf-8"):
                raise ValueError(f"{init}: hook load_tests wymaga osobnej obsługi przed podziałem")
    return sorted(modules)


def git_paths(base, root=ROOT):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root).decode("utf-8", "surrogateescape")
    ancestor = git("merge-base", base, "HEAD").strip()
    # Porównanie z working tree obejmuje commity gałęzi, indeks i zmiany robocze.
    changed = git("diff", "--name-only", "--no-renames", "-z", ancestor, "--").split("\0")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    return sorted(set(filter(None, changed + untracked)))


def changed_suites(paths):
    """Dawna mapa katalogowa: dobór zasobów innych niż importowalny kod usługi."""
    selected = set()
    for path in paths:
        matches = [prefix for prefix in CHANGE_MAP if path.startswith(prefix)]
        if not matches:
            return None
        selected.add(CHANGE_MAP[max(matches, key=len)])
    return sorted(selected)


@dataclass
class Selection:
    reasons: dict
    full: bool = False

    @property
    def modules(self):
        return sorted(self.reasons)


def changed_modules(paths, entries, root=ROOT):
    """Dobór na drzewie roboczym; nieznane lub wspólne wejście oznacza pełny przebieg."""
    all_modules = modules_for(entries, root)
    test_dirs = sorted({directory for directory, _ in entries})

    def full(path, reason):
        return Selection({module: [f"{path} → pełny przebieg: {reason}"] for module in all_modules}, True)

    paths = sorted(set(paths))
    for path in paths:
        if changed_suites([path]) is None:
            return full(path, "ścieżka poza mapą")
        if is_test_support(path, test_dirs, SHARED_TEST_MODULES):
            return full(path, "wspólna infrastruktura lub dane testów")
        if not (root / path).is_file():
            return full(path, "usunięty lub przeniesiony plik")
        if path.endswith("/__init__.py"):
            return full(path, "inicjalizacja pakietu")

    precise = [path for path in paths if path.endswith(".py") and
               any(path.startswith(prefix) for prefix in IMPORT_ROOTS)]
    graph = None
    if precise or any(path in all_modules for path in paths):
        try:
            graph = ImportGraph(root, test_dirs)
        except ValueError as exc:
            return full("analiza AST", str(exc))
        for path in paths:
            if path in all_modules and graph.importers[path]:
                return full(path, "moduł testów importowany jako wspólna fixture")

    reasons = {}

    def add(module, reason):
        reasons.setdefault(module, []).append(reason)

    for path in paths:
        if path in all_modules:
            add(path, f"{path} → zmieniony moduł testów")
        elif path in precise:
            affected = graph.affected(path, set(all_modules))
            for module, chain in affected.items():
                uncertainty = [f"{item}:{line}" for item in chain[:-1]
                               for line in graph.uncertain.get(item, ())]
                reason = f"{path} → import AST: " + " → ".join(chain)
                if uncertainty:
                    reason += "; potencjalny import dynamiczny: " + ", ".join(uncertainty)
                add(module, reason)
            if not graph.affected(path, set(all_modules), include_uncertain=False):
                # Brak rozpoznanego testu nie może oznaczać samego dymnego sprawdzenia.
                # Potencjalni odbiorcy importów dynamicznych nie zastępują starego fallbacku.
                for suite in changed_suites([path]):
                    for module in modules_for(SUITES[suite], root):
                        add(module, f"{path} → zestaw {suite}: brak rozpoznanych importów testów")
        else:
            for suite in changed_suites([path]):
                for module in modules_for(SUITES[suite], root):
                    add(module, f"{path} → mapa katalogowa: {suite}")
        if path.startswith(SERVICE_PREFIX):
            for module in SERVICE_CONTRACT_MODULES:
                if module not in all_modules:
                    raise ValueError(f"Brak modułu kontraktowego w pełnym zestawie: {module}")
                add(module, f"{path} → kontrakt procesu usługi")
    return Selection(reasons)


def explain_selection(selection):
    print("Dobór zmian: " + ("pełny przebieg" if selection.full else "mapa importów i zestawów"))
    print("AST: wszyscy pośredni odbiorcy; nierozstrzygalny import dynamiczny poszerza dobór")
    print(f"Wybrane moduły: {len(selection.modules)}")
    for module in selection.modules:
        print(f"- {module}")
        for reason in selection.reasons[module]:
            print(f"    {reason}")


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ids = []

    def startTest(self, test):
        self.ids.append(test.id())
        super().startTest(test)


def run_worker(module, report, root=ROOT, directory=False, inventory=False):
    # Przy uruchomieniu skryptu sys.path[0] wskazuje tools/, nie repozytorium.
    sys.path.insert(0, str(root))
    suite = unittest.TestLoader().discover(
        str(root / module if directory else (root / module).parent),
        pattern="test*.py" if directory else Path(module).name, top_level_dir=str(root))
    if inventory:
        def ids(tests):
            for test in tests:
                if isinstance(test, unittest.TestSuite):
                    yield from ids(test)
                else:
                    yield test.id()
        Path(report).write_text(json.dumps(list(ids(suite))), encoding="utf-8")
        return 0
    result = unittest.TextTestRunner(verbosity=1, resultclass=RecordingResult).run(suite)
    data = {
        "tests": result.testsRun, "ids": result.ids, "skipped": len(result.skipped),
        "skipped_tests": [(test.id(), reason) for test, reason in result.skipped],
        "expected_failures": [test.id() for test, _ in result.expectedFailures],
        "failures": [test.id() for test, _ in result.failures],
        "errors": [test.id() for test, _ in result.errors],
        "unexpected_successes": [test.id() for test in result.unexpectedSuccesses],
    }
    Path(report).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return int(not result.wasSuccessful())


def run_module(module, output, root=ROOT, slots=None):
    handle = slots.acquire() if slots is not None else None
    try:
        return _run_module(module, output, root)
    finally:
        MachineSlots.release(handle)


def _run_module(module, output, root=ROOT):
    started = time.monotonic()
    stem = module.replace("/", "__").removesuffix(".py")
    log, report = output / (stem + ".log"), output / (stem + ".json")
    # Każdy moduł dostaje własny katalog stanu: test bez izolacji nie czyta ani nie nadpisuje stanu działającej
    # usługi użytkownika (05.11: test tablicy zapisał atrapę K-1 do ~/.local/state/tide/board-derived.json).
    state = tempfile.mkdtemp(prefix="tide-test-state-")
    env = {**os.environ, "XDG_STATE_HOME": state, SLOT_HELD: "1"}
    with log.open("w", encoding="utf-8") as stream:
        try:
            proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", module,
                                   "--worker-report", str(report)], cwd=root, stdout=stream,
                                  stderr=subprocess.STDOUT, env=env)
            returncode = proc.returncode
        except OSError as exc:
            stream.write(f"Nie można uruchomić procesu: {exc}\n")
            returncode = 1
    shutil.rmtree(state, ignore_errors=True)
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"tests": 0, "ids": [], "skipped": 0, "failures": [], "errors": [],
                "unexpected_successes": [], "crash": "Proces nie zapisał wyniku"}
        returncode = returncode or 1
    data.update(module=module, returncode=returncode, seconds=time.monotonic() - started,
                log=str(log))
    return data


def execute(modules, jobs, output, root=ROOT, slots=None):
    output.mkdir(parents=True, exist_ok=True)
    slots = slots if slots is not None else MachineSlots()
    results = []
    parallel = [m for m in modules if m not in SERIAL_MODULES]
    serial = [m for m in modules if m in SERIAL_MODULES]

    def collect(result):
        results.append(result)
        state = "OK" if result["returncode"] == 0 else "BŁĄD"
        print(f"[{state}] {result['module']}: {result['tests']} testów, {result['seconds']:.2f} s", flush=True)
        if result["returncode"]:
            print(Path(result["log"]).read_text(encoding="utf-8", errors="replace"), flush=True)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(run_module, module, output, root, slots) for module in parallel]
        for future in as_completed(futures):
            collect(future.result())
    for module in serial:
        print(f"[OSOBNO] {module}: {SERIAL_MODULES[module]}", flush=True)
        collect(run_module(module, output, root, slots))
    return sorted(results, key=lambda r: r["module"])


def summarize(results, seconds):
    failed = [r for r in results if r["returncode"]]
    print(f"\nRazem: {sum(r['tests'] for r in results)} testów, {len(results)} modułów, "
          f"pominięte: {sum(r['skipped'] for r in results)}, czas: {seconds:.2f} s")
    print("Wszystkie nieudane testy:")
    for result in failed:
        names = result["failures"] + result["errors"] + result["unexpected_successes"]
        for name in names or [result.get("crash", f"proces zakończony kodem {result['returncode']}")]:
            print(f"- {result['module']}: {name}")
    if not failed:
        print("brak")
    return int(bool(failed))


def main(root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dirs", nargs="+", default=sorted({d for pairs in SUITES.values() for d, _ in pairs}))
    parser.add_argument("--suite", choices=SUITES)
    parser.add_argument("--changed", action="store_true")
    parser.add_argument("--base", default="master")
    parser.add_argument("--jobs", type=int, default=default_jobs())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--list-suites", action="store_true")
    parser.add_argument("--list", action="store_true", help="wypisz moduły bez uruchamiania")
    parser.add_argument("--worker")
    parser.add_argument("--worker-report")
    parser.add_argument("--worker-directory", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--inventory", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.list_suites:
        print(" ".join(SUITES))
        return 0
    if args.worker:
        return run_worker(args.worker, args.worker_report, root=Path.cwd(), directory=args.worker_directory,
                          inventory=args.inventory)
    if args.jobs < 1:
        parser.error("JOBS musi być dodatnią liczbą całkowitą")
    if args.suite and args.changed:
        parser.error("--suite i --changed są osobnymi sposobami doboru testów")
    entries = [(d, "test*.py") for d in args.dirs]
    if args.suite:
        entries = SUITES[args.suite]
    selection = None
    if args.changed:
        try:
            paths = git_paths(args.base, root)
        except subprocess.CalledProcessError:
            parser.error(f"Nie można ustalić diff względem {args.base!r}; podaj BASE=<ref>")
    try:
        if args.changed:
            selection = changed_modules(paths, entries, root)
            explain_selection(selection)
            modules = selection.modules
        else:
            modules = modules_for(entries, root)
    except ValueError as exc:
        parser.error(str(exc))
    if args.list:
        print("\n".join(modules))
        return 0
    # Osobny katalog dla każdego przebiegu zapobiega nadpisywaniu logów innej sesji.
    parent = args.output or root / "build" / "test-results"
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    print(f"Moduły: {len(modules)}, JOBS={args.jobs}, logi: {output}", flush=True)
    started = time.monotonic()
    results = execute(modules, args.jobs, output, root)
    elapsed = time.monotonic() - started
    (output / "summary.json").write_text(json.dumps({"seconds": elapsed, "jobs": args.jobs,
        "selection": selection.reasons if selection else None,
        "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return summarize(results, elapsed)


if __name__ == "__main__":
    raise SystemExit(main())
