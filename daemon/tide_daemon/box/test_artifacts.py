#!/usr/bin/python3
"""Samodzielna owijka tide-test, kopiowana przez runner do in/bin kroku."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time


def counts(text):
    """Znane podsumowania; brak rozpoznania nie jest błędem testów."""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    found = []
    for line in text.splitlines():
        if line.lstrip().startswith("Test Suites:"):
            continue
        # pytest, Playwright, Jest/Vitest (npm test).
        pairs = re.findall(r"\b(\d+)\s+(passed|failed)\b", line)
        if pairs:
            found.append({kind: int(n) for n, kind in pairs})
        # dotnet test (podsumowanie VSTest).
        dotnet = re.search(r"Failed:\s*(\d+).*Passed:\s*(\d+)", line)
        if dotnet:
            found.append({"failed": int(dotnet[1]), "passed": int(dotnet[2])})
        # Mocha (npm test).
        mocha = re.search(r"\b(\d+)\s+(passing|failing)\b", line)
        if mocha:
            found.append({"passed" if mocha[2] == "passing" else "failed": int(mocha[1])})
    suites = re.findall(r"Ran (\d+) tests? in [^\n]*\n\s*\n?"
                        r"(OK|FAILED)(?:\s*\(([^\n]*)\))?", text)
    if suites:
        total = {"passed": 0, "failed": 0}
        for ran, _status, details in suites:
            values = dict((key, int(n)) for key, n in re.findall(r"([a-z ]+)=(\d+)", details))
            values = {key.strip(): value for key, value in values.items()}
            failed = (values.get("failures", 0) + values.get("errors", 0)
                      + values.get("unexpected successes", 0))
            total["failed"] += failed
            total["passed"] += max(0, int(ran) - failed - values.get("skipped", 0)
                                   - values.get("expected failures", 0))
        return total
    if not found:
        return {}
    # Kilka zestawów unittest/make może wypisać więcej podsumowań; test suites
    # Jest nie są liczbą testów. Dla pozostałych formatów ostatni licznik wygrywa.
    return {key: next((item[key] for item in reversed(found) if key in item), 0)
            for key in ("passed", "failed")}


def run(argv, out):
    if not argv:
        print("użycie: tide-test <polecenie> [argumenty…]", file=sys.stderr)
        return 2
    folder = Path(out) / "tests"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        number = 1
        while (folder / f"{number}.log").exists():
            number += 1
        # Rezerwacja numeru przed uruchomieniem pozwala na równoległe testy.
        log = (folder / f"{number}.log").open("xb")
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    begin = time.monotonic()
    with log:
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as exc:
            log.write((str(exc) + "\n").encode())
            code = 127 if isinstance(exc, FileNotFoundError) else 126
        else:
            while True:
                chunk = proc.stdout.read1(65536)
                if not chunk:
                    break
                log.write(chunk)
                try:
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
                except BrokenPipeError:
                    pass
            proc.stdout.close()
            code = proc.wait()
            if code < 0:
                code = 128 - code
    duration = time.monotonic() - begin
    entry = {"command": shlex.join(argv), "argv": argv, "exit_code": code,
             "started_at": started, "duration": duration, "log": f"{number}.log"}
    try:
        entry.update(counts((folder / f"{number}.log").read_text(errors="replace")))
    except Exception:
        pass  # Awaria parsera nigdy nie zmienia wyniku polecenia.
    try:
        with (folder / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            summary = folder / "summary.json"
            rows = json.loads(summary.read_text()) if summary.exists() else []
            rows.append(entry)
            temporary = folder / "summary.json.tmp"
            temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
            temporary.replace(summary)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"tide-test: nie zapisano podsumowania: {exc}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:], os.path.join(os.environ["TIDE_STEP"], "out")))
