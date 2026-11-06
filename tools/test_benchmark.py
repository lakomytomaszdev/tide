"""PT-215: pomiar katalogów sekwencyjnych i równoległego make test na tej samej maszynie."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from test_runner import ROOT, default_jobs, modules_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dirs", nargs="+", required=True)
    parser.add_argument("--jobs", type=int, default=default_jobs())
    parser.add_argument("--output", type=Path, default=ROOT / "build/test-benchmark")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("JOBS musi być dodatnią liczbą całkowitą")
    args.output.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="comparison-", dir=args.output.resolve()))
    runner = ROOT / "tools/test_runner.py"
    print(f"Raport pomiaru: {output}", flush=True)
    report = {"started": datetime.now().astimezone().isoformat(), "jobs": args.jobs,
              "directories": args.dirs, "legacy": []}

    def save():
        (output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")

    # Jeden proces na katalog i pełny discover, dokładnie jak w starej pętli make.
    # Zbieramy dodatkowo identyfikatory; TextTestRunner wykonuje ten sam zestaw unittest.
    started = time.monotonic()
    for directory in args.dirs:
        stem = directory.replace("/", "__")
        result_file = output / (stem + ".json")
        log = output / (stem + ".log")
        before = time.monotonic()
        with log.open("w") as stream:
            proc = subprocess.run([sys.executable, str(runner), "--worker", directory,
                                   "--worker-directory", "--worker-report", str(result_file)],
                                  cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        data = json.loads(result_file.read_text()) if result_file.exists() else {"ids": [], "tests": 0}
        data.update(directory=directory, returncode=proc.returncode, seconds=time.monotonic() - before)
        report["legacy"].append(data)
        print(f"[sekwencyjnie] {directory}: {data['tests']} testów, {data['seconds']:.2f} s, "
              f"kod {proc.returncode}", flush=True)
        save()
    report["legacy_seconds"] = time.monotonic() - started
    save()

    # Używamy publicznego celu make test, z unikalnym katalogiem wyników tego pomiaru.
    parallel_output = output / "parallel"
    command = ["make", "test", f"JOBS={args.jobs}", f"TEST_OUTPUT={parallel_output}",
               "TEST_DIRS=" + " ".join(args.dirs)]
    started = time.monotonic()
    with (output / "parallel.log").open("w") as stream:
        proc = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            print(line, end="", flush=True)
            stream.write(line)
        report["parallel_returncode"] = proc.wait()
    report["parallel_seconds"] = time.monotonic() - started
    summaries = list(parallel_output.glob("run-*/summary.json"))
    if len(summaries) != 1:
        report["error"] = "Brak jednoznacznego summary.json z make test"
        save()
        return 1
    parallel = json.loads(summaries[0].read_text())
    legacy_ids = Counter(test for result in report["legacy"] for test in result["ids"])
    parallel_ids = Counter(test for result in parallel["results"] for test in result["ids"])
    report["same_tests"] = legacy_ids == parallel_ids
    report["missing"] = list((legacy_ids - parallel_ids).elements())
    report["extra"] = list((parallel_ids - legacy_ids).elements())
    report["legacy_tests"] = sum(legacy_ids.values())
    report["parallel_tests"] = sum(parallel_ids.values())
    report["same_results"] = True
    for key in ("failures", "errors", "unexpected_successes", "expected_failures", "skipped_tests"):
        before = Counter(json.dumps(item, ensure_ascii=False) for result in report["legacy"]
                         for item in result.get(key, []))
        after = Counter(json.dumps(item, ensure_ascii=False) for result in parallel["results"]
                        for item in result.get(key, []))
        if before != after:
            report["same_results"] = False
            report.setdefault("result_differences", {})[key] = {
                "legacy_only": list((before - after).elements()),
                "parallel_only": list((after - before).elements()),
            }
    report["legacy_skipped"] = sum(r.get("skipped", 0) for r in report["legacy"])
    report["parallel_skipped"] = sum(r["skipped"] for r in parallel["results"])
    report["modules"] = len(modules_for([(d, "test*.py") for d in args.dirs]))
    report["speedup"] = report["legacy_seconds"] / report["parallel_seconds"]
    save()
    text = (f"# PT-215 — pomiar testów\n\nData: {report['started']}\n\n"
            f"- Dawny discover, katalogi kolejno: {report['legacy_seconds']:.2f} s.\n"
            f"- Równoległy make test, JOBS={args.jobs}: {report['parallel_seconds']:.2f} s.\n"
            f"- Przyspieszenie: {report['speedup']:.2f}×.\n"
            f"- Testy przed/po: {report['legacy_tests']}/{report['parallel_tests']}; "
            f"zgodność identyfikatorów i powtórzeń: {report['same_tests']}.\n"
            f"- Zgodność błędów, pominięć i oczekiwanych porażek: {report['same_results']}; "
            f"pominięte przed/po: {report['legacy_skipped']}/{report['parallel_skipped']}.\n"
            f"- Moduły: {report['modules']}.\n"
            f"- Kody katalogów przed: {[r['returncode'] for r in report['legacy']]}; "
            f"make test po: {report['parallel_returncode']}.\n\n"
            "Szczegóły, pominięcia i błędy: comparison.json, logi katalogów i parallel.log.\n"
            "Przy błędzie pomiar obejmuje pozostałe katalogi, aby wykazać wszystkie wyniki.\n")
    (output / "report.md").write_text(text, encoding="utf-8")
    print(text, flush=True)
    return int(not report["same_tests"] or not report["same_results"] or bool(report["parallel_returncode"])
               or any(r["returncode"] for r in report["legacy"]))


if __name__ == "__main__":
    raise SystemExit(main())
