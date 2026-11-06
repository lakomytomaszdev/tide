#!/usr/bin/python3
"""PT-185: rzeczywiste scenariusze 1 i 2, uruchamiane wyłącznie przez outside.

Nie zatwierdza profilu, nie buduje pudełka ani nie zapisuje plików Tide w projekcie.
CLI tworzy osobne worktrees testowe z HEAD repozytorium. Zostają do oceny użytkownika.
To próba CLI, nie zastępuje dwóch prawdziwych ticketów testowych na Tablicy.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "daemon"))
from tide_daemon.box.runner import box_no_proxy

MARK = "PT185 "
# Wyłącznie jawne pola próby, bez tokenów ani adresów uwierzytelnionego proxy.
PROBE = """console.log('PT185 '+JSON.stringify({
phase:process.argv[1],ms:Date.now(),port:process.env.PORT,e2e:process.env.E2E_PORT,
base:process.env.TIDE_PORT_BASE,chrome:process.env.CHROME_BIN,bridge:process.env.TIDE_BOX_BRIDGE,
no_proxy:process.env.no_proxy,NO_PROXY:process.env.NO_PROXY,cwd:process.cwd()}))"""


def command(stage):
    import shlex
    probe = "node -e " + shlex.quote(PROBE)
    tasks = ["npm ci", "npm install --save-dev angular-eslint@^19", "npm run lint",
             "npx playwright test --workers=2"] if stage == "parallel" else [
                 "npm ci", "npm test -- --browsers=ChromeHeadless --watch=false"]
    return ["bash", "-c", "; ".join(["set -eu", probe + " start", *tasks, probe + " end"])]


def markers(text):
    values = []
    for line in text.splitlines():
        if line.startswith(MARK):
            values.append(json.loads(line[len(MARK):]))
    return values


def assess(runs, stage):
    """Ocenia dowody z wnętrza pudełka, nie sam czas uruchomienia klienta CLI."""
    errors = []
    spans = []
    for run in runs:
        if run["code"]:
            errors.append(f"{run['name']}: kod {run['code']}")
        values = run["markers"]
        if [v.get("phase") for v in values] != ["start", "end"]:
            errors.append(f"{run['name']}: brak pary znaczników start/end")
            continue
        start, end = values
        try:
            port = int(start["base"])
            if not (4300 <= port <= 4390 and (port - 4300) % 10 == 0
                    and start["port"] == start["e2e"] == str(port)):
                raise ValueError("niespójny przydział portów")
            if end["ms"] <= start["ms"] or end["cwd"] != start["cwd"]:
                raise ValueError("niespójny czas lub worktree")
            for key in ("NO_PROXY", "no_proxy"):
                if not start.get("bridge") or start.get(key) != box_no_proxy(start["bridge"]):
                    raise ValueError(f"niepełne {key}")
            if stage == "karma" and not start.get("chrome"):
                raise ValueError("brak CHROME_BIN")
            spans.append((start["ms"], end["ms"], port, start["cwd"]))
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{run['name']}: {exc}")
        if run.get("proxy_denied") is None:
            errors.append(f"{run['name']}: brak odczytu dziennika odmów proxy")
        elif run["proxy_denied"].strip():
            errors.append(f"{run['name']}: odmowy proxy wymagają oceny")
    if stage == "parallel":
        if len(spans) != 2:
            errors.append("brak dwóch pełnych przebiegów")
        else:
            left, right = spans
            if max(left[0], right[0]) >= min(left[1], right[1]):
                errors.append("przebiegi nie nakładały się w pudełku")
            if abs(left[2] - right[2]) < 10:
                errors.append("bloki portów nie są rozłączne")
            if left[3] == right[3]:
                errors.append("przebiegi użyły tego samego worktree")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("parallel", "karma"))
    parser.add_argument("--repo", type=Path, default=Path("/home/user/DevWorkspace/toolssuite"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    runs = []
    active = []
    try:
        # Oba procesy startują przed czekaniem na którykolwiek. Logi na dysku nie blokują pipe'ów.
        for name in (["left", "right"] if args.stage == "parallel" else ["karma"]):
            stdout = (args.output / f"{name}.stdout.log").open("w")
            stderr = (args.output / f"{name}.stderr.log").open("w")
            try:
                cli = [sys.executable, str(ROOT / "daemon/tide.py"), "box", "run", "toolssuite",
                       "--worktree", str(args.repo.resolve()), "--", *command(args.stage)]
                proc = subprocess.Popen(cli, cwd=ROOT, stdout=stdout, stderr=stderr)
            finally:
                stdout.close()
                stderr.close()
            active.append((name, proc, time.monotonic()))
        for name, proc, started in active:
            code = proc.wait()
            out = (args.output / f"{name}.stdout.log").read_text(errors="replace")
            err = (args.output / f"{name}.stderr.log").read_text(errors="replace")
            found = re.search(r"^dziennik proxy: (.+)$", err, re.M)
            denied = None
            if found:
                path = Path(found[1])
                try:
                    denied = path.read_text()
                    (args.output / f"{name}.proxy-denied.log").write_text(denied)
                except OSError:
                    pass
            runs.append({"name": name, "code": code, "seconds": round(time.monotonic() - started, 3),
                         "markers": markers(out), "proxy_denied": denied})
    finally:
        # Nie zabijamy samego klienta incus: nie dowodzi to końca jednostki w pudełku.
        # Przy przerwaniu zachowujemy dane i drukujemy PID-y do dalszego sprawdzenia przez Tide.
        for name, proc, _ in active:
            if proc.poll() is None:
                print(f"UWAGA: klient {name} PID={proc.pid} nadal działa; sprawdź jednostkę pudełka", file=sys.stderr)
    errors = assess(runs, args.stage)
    report = {"stage": args.stage, "runs": runs, "errors": errors, "ok": not errors}
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
