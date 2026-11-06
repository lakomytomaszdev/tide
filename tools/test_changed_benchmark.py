"""Porównanie selektorów dla ostatnich commitów, na jednym niezmienianym drzewie kodu.

Najpierw wykonuje wymagane make test-changed względem przypiętej bazy. Każdy
unikalny zestaw modułów mierzy raz; identyczne zestawy współdzielą ten sam pomiar.
Pełne testy uruchamiają własne piaskownice, więc procedura wymaga środowiska hosta.
"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

if __package__:
    from . import test_runner as runner
    from .test_suites import SUITES
else:
    import test_runner as runner
    from test_suites import SUITES


def git(*args):
    return subprocess.check_output(["git", *args], cwd=runner.ROOT).decode("utf-8", "surrogateescape")


def snapshot(base):
    digest = hashlib.sha256(git("rev-parse", "HEAD").encode())
    for path in runner.git_paths(base):
        digest.update(path.encode("utf-8", "surrogateescape"))
        file = runner.ROOT / path
        digest.update(file.read_bytes() if file.is_file() else b"<deleted>")
    return digest.hexdigest()


def legacy_modules(paths, entries):
    selected = runner.changed_suites(paths)
    if selected is None:
        return runner.modules_for(entries)
    return runner.modules_for([pair for name in selected for pair in SUITES[name]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--jobs", type=int, default=runner.default_jobs())
    parser.add_argument("--output", type=Path, default=runner.ROOT / "build/test-changed-benchmark")
    args = parser.parse_args()
    if args.jobs < 1 or args.count < 1:
        parser.error("Liczba commitów i JOBS muszą być dodatnie")
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output.resolve()
    fingerprint = snapshot(args.base)
    entries = [(d, "test*.py") for d in sorted({d for pairs in SUITES.values() for d, _ in pairs})]
    full_modules = runner.modules_for(entries)
    report_file = output / "comparison.json"
    report = {"started": datetime.now().astimezone().isoformat(), "snapshot": fingerprint,
              "base": args.base, "jobs": args.jobs, "commits": [], "measurements": {}}
    if report_file.exists():
        previous = json.loads(report_file.read_text())
        if previous["snapshot"] != fingerprint or previous["jobs"] != args.jobs:
            parser.error("Drzewo kodu lub JOBS zmienione: wskaż nowy katalog --output")
        report = previous

    def save():
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def key(modules):
        return hashlib.sha256("\n".join(modules).encode()).hexdigest()[:16]

    def measure(modules):
        identifier = key(modules)
        if identifier in report["measurements"]:
            return identifier
        folder = output / identifier
        started = time.monotonic()
        folder.mkdir(exist_ok=True)
        with (folder / "runner.log").open("w", encoding="utf-8") as log, redirect_stdout(log):
            results = runner.execute(modules, args.jobs, folder)
        report["measurements"][identifier] = {
            "modules": modules, "seconds": time.monotonic() - started,
            "results": results, "origin": str(folder),
        }
        save()
        data = report["measurements"][identifier]
        print(f"Pomiar {identifier}: {len(modules)} modułów, {data['seconds']:.2f} s", flush=True)
        return identifier

    # Publiczne polecenie ticketu, wraz z logiem, wynikiem i możliwością wznowienia.
    if "verification" not in report:
        command = ["make", "test-changed", f"BASE={args.base}"]
        parent = Path(tempfile.mkdtemp(prefix="verification-", dir=output))
        log_path = parent / "make.log"
        print("Sprawdzam: " + " ".join(command), flush=True)
        with log_path.open("w", encoding="utf-8") as log:
            proc = subprocess.run(command, cwd=runner.ROOT, stdout=log, stderr=subprocess.STDOUT,
                                  env={**os.environ, "TEST_OUTPUT": str(parent), "JOBS": str(args.jobs)})
        report["verification"] = {"command": " ".join(command), "returncode": proc.returncode,
                                  "log": str(log_path)}
        summaries = list(parent.glob("run-*/summary.json"))
        if len(summaries) == 1:
            summary = json.loads(summaries[0].read_text())
            report["verification"]["summary"] = str(summaries[0])
            # Narzędzia/ i dokumentacja wybierają pełny przebieg. Nie powtarzamy go w pomiarze.
            if sorted(r["module"] for r in summary["results"]) == full_modules:
                report["measurements"][key(full_modules)] = {
                    "modules": full_modules, "seconds": summary["seconds"],
                    "results": summary["results"], "origin": str(summaries[0]),
                }
        save()
    if report["verification"]["returncode"]:
        print(f"Nieudany test-changed: {report['verification']['log']}; "
              "pomiar będzie kontynuowany, końcowy wynik pozostanie nieudany.", flush=True)

    commits = git("log", "--no-merges", f"-{args.count}", "--format=%H", args.base).splitlines()
    report["commits"] = []
    for commit in commits:
        paths = list(filter(None, git("diff", "--name-only", "--no-renames", "-z",
                                      commit + "^", commit, "--").split("\0")))
        started = time.monotonic()
        before = legacy_modules(paths, entries)
        before_selection = time.monotonic() - started
        started = time.monotonic()
        selection = runner.changed_modules(paths, entries)
        after_selection = time.monotonic() - started
        row = {"commit": commit, "title": git("show", "-s", "--format=%s", commit).strip(),
               "paths": paths, "before": measure(before), "after": measure(selection.modules),
               "selection_seconds_before": before_selection, "selection_seconds_after": after_selection,
               "reasons": selection.reasons}
        report["commits"].append(row)
        save()
    text = ("# PT-258 — pomiar selektora zmian\n\n"
            f"Data: {report['started']}. JOBS={args.jobs}.\n\n"
            "Pięć ostatnich commitów bez merge względem przypiętej bazy (także poprawki z 06.11). "
            "Diff każdego commita porównuje oba selektory na **tym samym bieżącym kodzie**; "
            "nie jest to pomiar historycznych wersji usługi. Czas to rzeczywisty przebieg wybranych "
            "modułów, z oczekiwaniem na wspólną pulę i serializacją; analiza AST ma osobny licznik "
            "w JSON. Identyczne zestawy współdzielą jeden pomiar, także pełne test-changed.\n\n"
            "| Commit | Moduły przed | Moduły po | Czas przed | Czas po |\n"
            "| --- | ---: | ---: | ---: | ---: |\n")
    for row in report["commits"]:
        before = report["measurements"][row["before"]]
        after = report["measurements"][row["after"]]
        text += (f"| {row['commit'][:8]} | {len(before['modules'])} | {len(after['modules'])} | "
                 f"{before['seconds']:.2f} s | {after['seconds']:.2f} s |\n")
    failures = [m for m, data in report["measurements"].items() if any(r["returncode"] for r in data["results"])]
    report["failed_measurements"] = failures
    text += (f"\nPolecenie ticketu: `{report['verification']['command']}` — kod "
             f"{report['verification']['returncode']}. Nieudane pomiary: {failures or 'brak'}.\n\n"
             "Szczegóły: comparison.json (moduły, powody, liczba testów, czasy i błędy); "
             "obok logi modułów i test-changed.\n")
    (output / "report.md").write_text(text, encoding="utf-8")
    save()
    print(text, flush=True)
    return int(bool(failures) or bool(report["verification"]["returncode"]))


if __name__ == "__main__":
    raise SystemExit(main())
