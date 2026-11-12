#!/usr/bin/python3
"""Pomiar produkcji po instalacji (K-105, PT-458): CPU całego procesu usługi w 10 oknach, `Snapshot` p95 i zatwierdzenie
planu. **Tylko odczyt** — niczego nie instaluje, nie zmienia konfiguracji (także `box.enabled`), nie zapisuje w
`~/.local/share`, `~/.config` ani w Tablicy; poza wynikiem (`--out`) powstaje tylko plik żądania próbnika w katalogu
uruchomieniowym i raport próbnika, który zapisuje sama usługa.

Tryby (jawnie nazwane):

  idle (domyślny)   sprawdza ważność (loadavg, brak kroków w toku jednym odczytem przed rozgrzewką), czeka do
                    `--wait-idle-min` (10) min na postój bez kroków albo przyjmuje `--owner-idle-min` i go zapisuje,
                    rozgrzewka `--warmup` s, potem 10 okien po 60 s CPU procesu `MainPID` jednostki (utime + stime
                    wszystkich wątków, bez dzieci) na czystym postoju — bez żądań do usługi. Dopiero po oknach: osobne okno
                    próbnika usługi (60 s) z serią 60 wywołań `Snapshot` co 0,5 s. Pamięć przed i po oknach CPU
                    (`RssAnon`, `VmRSS`, `VmHWM`, `MemoryCurrent` jednostki) i bieżące `box.enabled` z konfiguracji.
                    Ocena: każde okno ≤ 30 %, `Snapshot` p95 < 1 s.
  approve-plan      JEDYNY tryb, który wywołuje akcję usługi (`ApproveCardPlan`). Uruchamia go wyłącznie właściciel dla
                    karty z planem ≥ 18 wierszy, którą i tak zatwierdza. Mierzy czas od wywołania do zapisu zdarzenia
                    `plan.approved` w dzienniku karty (kryterium M0: < 10 s). Odmawia przy planie < 18 wierszy, chyba że
                    `--allow-small-plan` (wynik ma wtedy znacznik „poza kryterium”).

Użycie (z katalogu repozytorium):
    /usr/bin/python3 scripts/measure_idle_prod.py --out ~/pomiar-k105-box-false.json
    /usr/bin/python3 scripts/measure_idle_prod.py --mode approve-plan --card K-105 --out ~/pomiar-k105-zatwierdzenie.json

Kody wyjścia: 0 — w progach; 1 — ponad próg (otwarty bloker M0); 2 — pomiar nieważny albo odmowa.
Procedura: docs/tide-daemon.md, „Wydajność postoju”.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "daemon", "tests"))
import perf_cpu_windows as cpuwin            # noqa: E402
import perf_idle_cpu as perf                  # noqa: E402

UNIT = "tide-daemon.service"
SERVICE = "org.tide.Daemon"
DEFAULT_WAIT_IDLE_MIN = 10.0
POLL_S = 15.0
NOT_SET = (2 ** 64 - 1,)


def forbidden_output(path):
    """Skrypt nie zapisuje w `~/.local/share`, `~/.config` ani w Tablicy — też wyniku."""
    real = os.path.realpath(os.path.expanduser(path))
    home = os.path.expanduser("~")
    board = os.path.realpath(os.environ.get("TIDE_BOARD_DIR") or os.path.join(home, "DevWorkspace", "tide-board"))
    blocked = [os.path.join(home, ".local", "share"), os.path.join(home, ".config"), board]
    return next((b for b in blocked if real == b or real.startswith(b + os.sep)), None)


class SystemHost:
    """Odczyty produkcji: `systemctl --user show`, `systemctl --user list-units`, konfiguracja, magistrala sesji."""

    def __init__(self, unit=UNIT, board=None):
        self.unit = unit
        self.board = board or os.environ.get("TIDE_BOARD_DIR") or os.path.expanduser("~/DevWorkspace/tide-board")
        self.clock, self.sleep = time.monotonic, time.sleep

    def show(self, prop):
        done = subprocess.run(["systemctl", "--user", "show", "-p", prop, "--value", self.unit],
                              capture_output=True, text=True, timeout=30)
        return done.stdout.strip() if done.returncode == 0 else ""

    def main_pid(self):
        value = self.show("MainPID")
        return int(value) if value.isdigit() else 0

    def memory_current(self):
        value = self.show("MemoryCurrent")
        return int(value) if value.isdigit() and int(value) not in NOT_SET else None

    def active_steps(self):
        """Jednostki kroków i poleceń `outside` w toku (tylko odczyt systemd, bez wywołań usługi)."""
        done = subprocess.run(["systemctl", "--user", "list-units", "--plain", "--no-legend",
                               "--state=active,activating", "tide-step-*", "tide-outside-*"],
                              capture_output=True, text=True, timeout=30)
        return [line.split()[0] for line in done.stdout.splitlines() if line.strip()]

    def box_enabled(self):
        sys.path.insert(0, os.path.join(REPO, "daemon"))
        from tide_daemon.box import settings            # noqa: E402 — ten sam odczyt co usługa
        return bool(settings.load().enabled)

    def service(self, pid):
        return ProdService(pid)

    def read_events(self, card):
        path = os.path.join(self.board, "flow", card, "journal.jsonl")
        events = []
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        break                           # niedokończona ostatnia linia
        except OSError:
            pass
        return events

    def start_approve(self, card):
        process = subprocess.Popen(["busctl", "--user", "--timeout=120", "call", SERVICE, "/Board",
                                    "org.tide.Daemon.Board", "ApproveCardPlan", "s", card],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return _Handle(process)


class _Handle:
    def __init__(self, process):
        self.process = process

    def done(self):
        return self.process.poll() is not None


class ProdService:
    """Jedyne żądania idle: osobne okno po oknach CPU — próbnik usługi (plik żądania) i seria `Snapshot`."""

    def __init__(self, pid):
        self.pid = pid

    def diagnostics(self, seconds):
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtDBus import QDBusConnection
        QCoreApplication.instance() or QCoreApplication([])
        series = perf.scale_mode.SnapshotSeries(perf.snapshot_caller(QDBusConnection.sessionBus()))
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        state = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "tide")
        window = perf.profile_window("", self.pid, seconds, series, state=state,
                                     request=os.path.join(runtime, "tide-daemon-profile"))
        return {"profileWindow": window, "snapshot": series.result()}


def wait_idle(host, wait_s, owner_idle_min=None, poll_s=POLL_S):
    """Ważność „brak kroków w toku”: jeden odczyt; gdy są kroki, czeka do `wait_s` na postój. `owner_idle_min`
    (czas postoju podany przez właściciela) zastępuje czekanie i jest zapisywany."""
    started = host.clock()
    steps = host.active_steps()
    while steps and owner_idle_min is None and host.clock() - started < wait_s:
        host.sleep(poll_s)
        steps = host.active_steps()
    return {"stepsAtCheck": steps, "waitedS": round(host.clock() - started, 1), "ownerIdleMinutes": owner_idle_min,
            "ownerIdleNote": "czas postoju podany przez właściciela, niezweryfikowany" if owner_idle_min is not None
            else None}


def write_result(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def run_idle(args, host, out=print):
    pid = host.main_pid()
    if not pid:
        out(f"jednostka {getattr(host, 'unit', UNIT)} nie ma MainPID (usługa nie działa)")
        return 2, None
    idle = wait_idle(host, args.wait_idle_min * 60.0, args.owner_idle_min)
    extra = []
    if idle["stepsAtCheck"] and args.owner_idle_min is None:
        extra.append(f"kroki w toku po {idle['waitedS']} s czekania: {', '.join(idle['stepsAtCheck'])}")
    elif idle["stepsAtCheck"]:
        extra.append("kroki w toku mimo podanego postoju: " + ", ".join(idle["stepsAtCheck"]))
    box = host.box_enabled()
    result = {"tool": "scripts/measure_idle_prod.py", "mode": "idle", "unit": getattr(host, "unit", UNIT),
              "sha": perf.git_sha(REPO), "boxEnabled": box, "idle": idle, "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "params": {"warmupS": args.warmup, "windows": args.windows, "windowS": args.window}}
    if extra:
        result.update(invalidExtra=extra)
        result["evaluation"] = _evaluate(result)
        return 2, result
    partial = lambda block: write_result(args.out, {**result, "cpuWindows": block, "partial": True})
    result.update(cpuwin.run_idle_measurement(
        pid, host.service(pid), warmup_s=args.warmup, windows=args.windows, window_s=args.window,
        pid_source=host.main_pid, memory_current=host.memory_current, on_window=partial,
        proc_root=getattr(host, "proc_root", "/proc"), clock=host.clock, sleep=host.sleep,
        cgroup_root=getattr(host, "cgroup_root", "/sys/fs/cgroup"), hz=getattr(host, "hz", None),
        cpu_count=getattr(host, "cpu_count", None)))
    result["boxEnabled"] = box
    result["evaluation"] = _evaluate(result)
    return (0 if result["evaluation"]["ok"] else (2 if not result["evaluation"]["valid"] else 1)), result


def _evaluate(result):
    evaluation = cpuwin.evaluate(result)
    p95 = evaluation["snapshotP95"]
    evaluation["ok"] = evaluation["verdict"] == "pass" and p95["ok"] is True
    evaluation["openM0Blocker"] = evaluation["valid"] and not evaluation["ok"]
    if evaluation["valid"] and p95["ok"] is None:
        evaluation["invalidReasons"].append("brak serii Snapshot (p95) — pomiar niepełny")
        evaluation["valid"], evaluation["ok"], evaluation["openM0Blocker"] = False, False, False
        evaluation["verdict"] = "invalid"
    return evaluation


def run_approve(args, host, out=print):
    if not args.card:
        out("tryb approve-plan wymaga --card K-<numer>")
        return 2, None
    outcome = cpuwin.measure_approval(lambda: host.start_approve(args.card), lambda: host.read_events(args.card),
                                      allow_small=args.allow_small_plan, clock=host.clock, sleep=host.sleep)
    result = {"tool": "scripts/measure_idle_prod.py", "mode": "approve-plan", "card": args.card,
              "sha": perf.git_sha(REPO), "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "approval": outcome}
    if outcome["refused"]:
        out("odmowa: " + outcome["reason"])
        return 2, result
    return (0 if outcome["ok"] else 1), result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("idle", "approve-plan"), default="idle")
    parser.add_argument("--out", required=True, help="plik wyniku JSON (nie w ~/.local/share, ~/.config ani w Tablicy)")
    parser.add_argument("--unit", default=UNIT)
    parser.add_argument("--warmup", type=float, default=60.0, help="rozgrzewka w s po sprawdzeniu ważności (bez żądań)")
    parser.add_argument("--window", type=float, default=cpuwin.WINDOW_S)
    parser.add_argument("--windows", type=int, default=cpuwin.WINDOWS)
    parser.add_argument("--wait-idle-min", type=float, default=DEFAULT_WAIT_IDLE_MIN)
    parser.add_argument("--owner-idle-min", type=float, default=None,
                        help="czas postoju bez kroków podany przez właściciela (zapisywany w wyniku)")
    parser.add_argument("--card", default="", help="approve-plan: karta z planem do zatwierdzenia")
    parser.add_argument("--allow-small-plan", action="store_true",
                        help="approve-plan: zgoda właściciela na plan < 18 wierszy (wynik „poza kryterium”)")
    parser.add_argument("--board", default=None, help="katalog Tablicy (tylko odczyt dziennika); domyślnie TIDE_BOARD_DIR")
    return parser


def main(argv=None, host=None, out=print):
    args = build_parser().parse_args(argv)
    blocked = forbidden_output(args.out)
    if blocked:
        out(f"odmowa: wynik nie może trafić do {blocked}")
        return 2
    host = host or SystemHost(args.unit, args.board)
    code, result = run_approve(args, host, out) if args.mode == "approve-plan" else run_idle(args, host, out)
    if result is not None:
        result.pop("partial", None)
        write_result(args.out, result)
        evaluation = result.get("evaluation")
        out(cpuwin.evaluation_text(evaluation) if evaluation else json.dumps(result.get("approval"), ensure_ascii=False))
        if evaluation and evaluation.get("openM0Blocker"):
            out("WYNIK POWYŻEJ PROGU: otwarty bloker M0 (zapisz wynik w karcie i zgłoś właścicielowi)")
    return code


if __name__ == "__main__":
    sys.exit(main())
