#!/usr/bin/python3
"""Narzędzie 10 okien CPU całego procesu usługi (K-105, PT-458) — ta sama miara w reproduktorze i na produkcji.

Miara rozstrzygająca: CPU **całego procesu** usługi (wszystkie wątki: `utime + stime` z `/proc/<pid>/stat`), bez
procesów potomnych (`cutime`/`cstime` nie są nawet czytane). 10 kolejnych okien po 60 s, w % jednego rdzenia na
okno; próg: **każde okno ≤ 30 %**. Ocena podaje wszystkie okna, średnią i maksimum. Zmiana PID (restart usługi),
zniknięcie procesu albo przekroczenie limitu czasu unieważnia próbę.

Czysty postój: rozgrzewka i 10 okien CPU nie wysyłają żądań do usługi (bez próbnika, `Snapshot` i innych wywołań
D-Bus). Próbnik usługi i seria 60 wywołań `Snapshot` idą w **osobnym oknie po** oknach CPU (`service.diagnostics`).
Bez progu: udział `engine.tick`, próbki `engine.tick`/s, CPU wątku workera przepływu, `Snapshot` p95 i pamięć
(`RssAnon`, `VmRSS`, `VmHWM`, `MemoryCurrent` przed i po oknach CPU).

Moduł nie uruchamia usługi; używają go `perf_idle_cpu.py --windows` (reproduktor) i `scripts/measure_idle_prod.py`
(produkcja, tylko odczyt). Zawiera też pomiar zatwierdzenia planu (`measure_approval`, kryterium M0 < 10 s).
Testy jednostkowe nie mierzą czasu: zegar, uśpienie i `/proc` są wstrzykiwane.
"""
import glob
import os
import time

try:
    from . import perf_idle_scale as scale_mode
except ImportError:
    import perf_idle_scale as scale_mode                  # uruchomienie jako skrypt

WINDOWS = 10
WINDOW_S = 60.0
CPU_LIMIT_PERCENT = 30.0              # każde okno ≤ 30 % jednego rdzenia
SNAPSHOT_P95_LIMIT_MS = 1000.0        # produkcja: Snapshot p95 < 1 s
PLAN_ROWS_MIN = 18                    # kryterium M0: plan ≥ 18 wierszy
APPROVE_LIMIT_S = 10.0                # kryterium M0: zatwierdzenie < 10 s
APPROVAL_EVENT = "plan.approved"      # zdarzenie dziennika karty zapisywane przez `Engine.approve_plan`
PROBE_EVERY_S = 3.0


class LimitExceeded(Exception):
    """Próba przekroczyła wewnętrzny limit czasu."""


def clock_ticks():
    try:
        return os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError):
        return 100


# --- odczyty /proc ----------------------------------------------------------------------------------

def parse_stat(text):
    """(nazwa, pola po nawiasie) z jednej linii `/proc/<pid>/stat`; nazwa może zawierać spacje i nawiasy."""
    return text[text.find("(") + 1: text.rfind(")")], text[text.rfind(")") + 2:].split()


def process_ticks(text):
    """(utime, stime, starttime) w tickach. `cutime` i `cstime` (dzieci) celowo pomijamy."""
    _name, fields = parse_stat(text)
    return int(fields[11]), int(fields[12]), int(fields[19])


def read_process(pid, proc_root="/proc"):
    """{utime, stime, start} procesu; OSError, gdy procesu nie ma."""
    with open(os.path.join(proc_root, str(pid), "stat"), encoding="utf-8", errors="replace") as fh:
        utime, stime, start = process_ticks(fh.read())
    return {"utime": utime, "stime": stime, "start": start}


def read_threads(pid, proc_root="/proc", hz=None):
    """tid → {name, cpuS} dla wszystkich wątków procesu."""
    hz = hz or clock_ticks()
    out = {}
    for task in glob.glob(os.path.join(proc_root, str(pid), "task", "*")):
        try:
            with open(os.path.join(task, "stat"), encoding="utf-8", errors="replace") as fh:
                name, fields = parse_stat(fh.read())
        except OSError:
            continue
        out[os.path.basename(task)] = {"name": name, "cpuS": (int(fields[11]) + int(fields[12])) / hz}
    return out


def threads_delta(before, after):
    out = {}
    for tid, item in after.items():
        base = (before.get(tid) or {}).get("cpuS", 0.0)
        out[tid] = {"name": item["name"], "cpuS": round(item["cpuS"] - base, 3)}
    return out


def loadavg(proc_root="/proc"):
    try:
        with open(os.path.join(proc_root, "loadavg"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


# --- pamięć -----------------------------------------------------------------------------------------

STATUS_KEYS = {"RssAnon": "rssAnonMb", "VmRSS": "vmRssMb", "VmHWM": "vmHwmMb"}


def parse_status(text):
    """`RssAnon`, `VmRSS`, `VmHWM` z `/proc/<pid>/status` w MB (kB / 1024); brak pola → None."""
    out = {name: None for name in STATUS_KEYS.values()}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in STATUS_KEYS and rest.split():
            out[STATUS_KEYS[key]] = round(int(rest.split()[0]) / 1024.0, 2)
    return out


def read_status(pid, proc_root="/proc"):
    try:
        with open(os.path.join(proc_root, str(pid), "status"), encoding="utf-8", errors="replace") as fh:
            return parse_status(fh.read())
    except OSError:
        return {name: None for name in STATUS_KEYS.values()}


def read_cgroup_memory(pid, proc_root="/proc", cgroup_root="/sys/fs/cgroup"):
    """`memory.current` cgroup procesu (reproduktor): ścieżka, bajty i znacznik, czy cgroup zawiera inne procesy."""
    out = {"path": None, "memoryCurrentBytes": None, "sharedWithOtherProcesses": None, "processes": None}
    try:
        with open(os.path.join(proc_root, str(pid), "cgroup"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return out
    path = next((line.split(":", 2)[2] for line in lines if line.startswith("0::")), None)
    if path is None:
        return out
    out["path"] = path
    directory = os.path.join(cgroup_root, path.lstrip("/"))
    try:
        with open(os.path.join(directory, "memory.current"), encoding="utf-8") as fh:
            out["memoryCurrentBytes"] = int(fh.read().strip())
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(directory, "cgroup.procs"), encoding="utf-8") as fh:
            procs = [int(item) for item in fh.read().split()]
        out["processes"] = len(procs)
        out["sharedWithOtherProcesses"] = any(item != int(pid) for item in procs)
    except (OSError, ValueError):
        pass
    return out


def memory_snapshot(pid, proc_root="/proc", memory_current=None, cgroup_root="/sys/fs/cgroup"):
    """Pamięć usługi w danej chwili. `memory_current` (produkcja): funkcja zwracająca `MemoryCurrent` jednostki w
    bajtach (`systemctl --user show`); bez niej (reproduktor) — `memory.current` cgroup procesu."""
    out = read_status(pid, proc_root)
    if memory_current is not None:
        value = memory_current()
        out["memoryCurrentMb"] = round(value / 1048576.0, 2) if value is not None else None
        out["memoryCurrentSource"] = "MemoryCurrent jednostki (systemctl --user show)"
        return out
    cgroup = read_cgroup_memory(pid, proc_root, cgroup_root)
    value = cgroup["memoryCurrentBytes"]
    out["memoryCurrentMb"] = round(value / 1048576.0, 2) if value is not None else None
    out["memoryCurrentSource"] = "memory.current cgroup procesu"
    out["cgroup"] = {"path": cgroup["path"], "processes": cgroup["processes"],
                     "sharedWithOtherProcesses": cgroup["sharedWithOtherProcesses"]}
    return out


# --- 10 okien CPU -----------------------------------------------------------------------------------

def _sleep_until(target, clock, sleep, deadline):
    """Śpi do `target` (czas zegara) w krótkich odcinkach, sprawdzając limit czasu próby."""
    while True:
        now = clock()
        if deadline is not None and now > deadline:
            raise LimitExceeded("limit czasu próby")
        if now >= target:
            return
        sleep(min(1.0, target - now))


def run_windows(pid, *, windows=WINDOWS, window_s=WINDOW_S, proc_root="/proc", clock=time.monotonic,
                sleep=time.sleep, pid_source=None, on_window=None, deadline=None, hz=None):
    """`windows` kolejnych okien po `window_s` s: przyrost `utime + stime` procesu `pid` (wszystkie wątki, bez dzieci).

    `pid_source` (produkcja): funkcja zwracająca bieżący `MainPID`; inny niż `pid` unieważnia próbę. `on_window`
    dostaje wynik cząstkowy po każdym oknie (zapis przyrostowy). Zwraca słownik z oknami, agregatem CPU i wątkami
    (kształt zgodny z `perf_idle_cpu.cpu_window`, żeby `select_flow_thread` działał bez zmian)."""
    hz = hz or clock_ticks()
    result = {"pid": pid, "hz": hz, "expected": windows, "windowS": window_s, "windows": [], "complete": False,
              "invalidReason": None, "cpuLimitPercent": CPU_LIMIT_PERCENT}
    try:
        first = read_process(pid, proc_root)
        threads0 = read_threads(pid, proc_root, hz)
    except OSError:
        result["invalidReason"] = "proces nie istnieje"
        return result
    last, last_at, started = first, clock(), clock()
    try:
        for index in range(windows):
            _sleep_until(last_at + window_s, clock, sleep, deadline)
            if pid_source is not None and pid_source() != pid:
                raise ValueError("zmiana PID (restart usługi)")
            now_at = clock()
            try:
                current = read_process(pid, proc_root)
            except OSError:
                raise ValueError("proces zniknął w trakcie okna") from None
            if current["start"] != first["start"]:
                raise ValueError("zmiana PID (restart usługi)")
            wall = now_at - last_at
            du, ds = current["utime"] - last["utime"], current["stime"] - last["stime"]
            cpu_s = (du + ds) / hz
            result["windows"].append({
                "index": index + 1, "wallS": round(wall, 3), "utimeTicks": du, "stimeTicks": ds,
                "cpuS": round(cpu_s, 3), "cpuPercent": round(100.0 * cpu_s / wall, 2) if wall > 0 else None})
            last, last_at = current, now_at
            if on_window is not None:
                on_window(result)
        result["complete"] = True
    except LimitExceeded as exc:
        result["invalidReason"] = str(exc)
    except ValueError as exc:
        result["invalidReason"] = str(exc)
    total_wall = last_at - started
    total_s = (last["utime"] + last["stime"] - first["utime"] - first["stime"]) / hz
    try:
        threads = threads_delta(threads0, read_threads(pid, proc_root, hz))
    except OSError:
        threads = {}
    result.update({"wallS": round(total_wall, 3), "cpuS": round(total_s, 3),
                   "cpuPercent": round(100.0 * total_s / total_wall, 2) if total_wall > 0 else None,
                   "threads": threads})
    return result


# --- ocena ------------------------------------------------------------------------------------------

def load_valid(result):
    """(ważny, powody): loadavg 1-min < rdzenie/2 przed pomiarem i po oknach CPU (reguła PT-362)."""
    cores = result.get("cpuCount") or 1
    reasons = []
    for key in ("loadavgBefore", "loadavgAfter"):
        value = scale_mode.load1(result.get(key))
        if value is None:
            reasons.append(f"brak odczytu {key}")
        elif value >= cores / 2:
            reasons.append(f"{key} {value} ≥ {cores / 2} (rdzenie/2)")
    return not reasons, reasons


def evaluate(result, limit=CPU_LIMIT_PERCENT):
    """Ocena 10 okien: niespełniony, gdy którekolwiek okno > `limit` % rdzenia (średnia nie ma znaczenia).

    Werdykt: `invalid` (próba niepełna, zmiana PID, limit czasu, wysoki loadavg, brak kroku-wolnego postoju),
    `fail` (okno ponad próg), `pass`. Reszta (`engine.tick`, wątek przepływu, `Snapshot`, pamięć) jest raportowana
    bez progu i nie zmienia werdyktu; produkcja dodatkowo ocenia `Snapshot` p95 < 1 s osobno (`snapshotP95`)."""
    block = result.get("cpuWindows") or {}
    windows = block.get("windows") or []
    percents = [item.get("cpuPercent") for item in windows]
    reasons = []
    if block.get("invalidReason"):
        reasons.append(block["invalidReason"])
    if len(windows) != (block.get("expected") or WINDOWS) or any(p is None for p in percents):
        reasons.append(f"niepełna seria okien ({len(windows)}/{block.get('expected') or WINDOWS})")
    ok_load, load_reasons = load_valid(result)
    reasons += load_reasons
    reasons += list(result.get("invalidExtra") or [])
    over = [item["index"] for item in windows if item.get("cpuPercent") is not None and item["cpuPercent"] > limit]
    verdict = "invalid" if reasons else ("fail" if over else "pass")
    known = [p for p in percents if p is not None]
    tick = result.get("tick") or {}
    snapshot = result.get("snapshot") or {}
    p95 = snapshot.get("p95Ms")
    return {
        "verdict": verdict, "valid": not reasons, "invalidReasons": reasons,
        "limitPercent": limit, "windowsPercent": percents,
        "meanPercent": round(sum(known) / len(known), 2) if known else None,
        "maxPercent": max(known) if known else None, "windowsOverLimit": over,
        "boxEnabled": result.get("boxEnabled"), "sha": result.get("sha"), "pid": block.get("pid"),
        "diagnostics": {"engineTickShare": tick.get("share"), "engineTickPerSecond": tick.get("perSecond"),
                        "flowThread": result.get("flowThread"), "snapshotP95Ms": p95,
                        "memory": result.get("memory"), "note": "bez progu — nie zmienia werdyktu"},
        "snapshotP95": {"valueMs": p95, "limitMs": SNAPSHOT_P95_LIMIT_MS,
                        "ok": None if p95 is None else p95 < SNAPSHOT_P95_LIMIT_MS,
                        "note": "ocena produkcji; w reproduktorze tylko raport"},
    }


def evaluation_text(evaluation):
    lines = [f"werdykt: {evaluation['verdict']}"]
    if evaluation["invalidReasons"]:
        lines.append("nieważny: " + "; ".join(evaluation["invalidReasons"]))
    lines.append("okna (% rdzenia): " + ", ".join(str(p) for p in evaluation["windowsPercent"]))
    lines.append(f"średnia {evaluation['meanPercent']} %, maksimum {evaluation['maxPercent']} % "
                 f"(próg każdego okna ≤ {evaluation['limitPercent']} %)")
    if evaluation["windowsOverLimit"]:
        lines.append(f"okna ponad próg: {evaluation['windowsOverLimit']}")
    diag = evaluation["diagnostics"]
    lines.append(f"bez progu: engine.tick {diag['engineTickShare']} ({diag['engineTickPerSecond']}/s), "
                 f"Snapshot p95 {diag['snapshotP95Ms']} ms")
    return "\n".join(lines)


# --- przebieg: rozgrzewka → 10 okien CPU → osobne okno diagnostyki ----------------------------------

def run_idle_measurement(pid, service, *, warmup_s, windows=WINDOWS, window_s=WINDOW_S, proc_root="/proc",
                         clock=time.monotonic, sleep=time.sleep, pid_source=None, on_window=None, deadline=None,
                         memory_current=None, cgroup_root="/sys/fs/cgroup", cpu_count=None, hz=None,
                         diagnostics_s=None, load=None, window_marks=None):
    """Przebieg pomiaru na czystym postoju. `service.diagnostics(seconds)` to jedyne wywołanie usługi i następuje
    dopiero po oknach CPU; w rozgrzewce i w oknach CPU narzędzie niczego do usługi nie wysyła.
    `window_marks` (K-125 PT-486): odczyt licznika (np. wywołań atrapy `systemctl`) tuż przed pierwszym i tuż po
    ostatnim oknie CPU; wynik w `windowMarks`. Nie zmienia pomiaru CPU.

    Zwraca wynik z kluczami `cpuWindows`, `cpuWindow` (agregat), `memory` (przed/po), `loadavg*`, `flowThread`,
    `tick`, `snapshot` i `diagnosticsSkipped` (limit czasu przed oknem diagnostyki — okna CPU zostają ważne)."""
    load = load or (lambda: loadavg(proc_root))
    result = {"cpuCount": cpu_count or os.cpu_count(), "loadavgBefore": load()}
    try:
        _sleep_until(clock() + warmup_s, clock, sleep, deadline)         # rozgrzewka: bez żądań
    except LimitExceeded:
        result.update(cpuWindows={"pid": pid, "expected": windows, "windows": [], "complete": False,
                                  "invalidReason": "limit czasu próby (rozgrzewka)"},
                      loadavgAfter=load(), memory=None, flowThread=None, tick=scale_mode.tick_stats(None, window_s),
                      snapshot=None, diagnosticsSkipped=None)
        return result
    result["loadavgWindowStart"] = load()
    before = memory_snapshot(pid, proc_root, memory_current, cgroup_root)
    marks_start = window_marks() if window_marks is not None else None       # K-125: licznik na granicach okien CPU
    block = run_windows(pid, windows=windows, window_s=window_s, proc_root=proc_root, clock=clock, sleep=sleep,
                        pid_source=pid_source, on_window=on_window, deadline=deadline, hz=hz)
    if window_marks is not None:
        result["windowMarks"] = {"start": marks_start, "end": window_marks()}
    after = memory_snapshot(pid, proc_root, memory_current, cgroup_root)
    result["loadavgAfter"] = load()
    result["cpuWindows"] = block
    result["cpuWindow"] = {key: block.get(key) for key in ("wallS", "cpuS", "cpuPercent", "threads")}
    result["memory"] = {"before": before, "after": after, "vmRssStartMb": before.get("vmRssMb"),
                        "vmRssEndMb": after.get("vmRssMb"), "vmHwmMb": after.get("vmHwmMb")}
    result["flowThread"] = scale_mode.select_flow_thread(block.get("threads"), pid, block.get("wallS"))
    result["diagnosticsSkipped"] = None
    result["tick"] = scale_mode.tick_stats(None, window_s)
    result["snapshot"] = None
    if not block["complete"]:
        return result
    span = diagnostics_s or window_s
    if deadline is not None and clock() + span > deadline:
        result["diagnosticsSkipped"] = "limit czasu przed oknem diagnostyki"
        return result
    diag = service.diagnostics(span) or {}                                # osobne okno po oknach CPU
    result["profileWindow"] = diag.get("profileWindow")
    split = (diag.get("profileWindow") or {}).get("split")
    result["tick"] = scale_mode.tick_stats(split, span)
    result["snapshot"] = diag.get("snapshot")
    result["loadavgEnd"] = load()
    return result


# --- zatwierdzenie planu (kryterium M0) -------------------------------------------------------------

def plan_rows(events):
    """Liczba wierszy ostatniego `plan.proposed` w dzienniku karty (0, gdy planu nie ma)."""
    rows = 0
    for event in events:
        if event.get("type") == "plan.proposed":
            rows = len(event.get("rows") or [])
    return rows


def measure_approval(start_call, read_events, *, allow_small=False, clock=time.monotonic, sleep=time.sleep,
                     poll_s=0.02, wait_limit_s=120.0, limit_s=APPROVE_LIMIT_S, min_rows=PLAN_ROWS_MIN):
    """Czas od wywołania `ApproveCardPlan` do zapisu zdarzenia `plan.approved` w dzienniku karty.

    `start_call()` uruchamia wywołanie bez blokowania i zwraca uchwyt z `done()`; `read_events()` czyta dziennik
    (tylko odczyt). To jedyny tryb, który wywołuje akcję usługi. Odmawia przy planie < `min_rows` wierszy, chyba że
    `allow_small` (wynik ma wtedy znacznik `outsideCriterion`)."""
    events = read_events()
    rows = plan_rows(events)
    if any(e.get("type") == APPROVAL_EVENT for e in events):
        return {"refused": True, "reason": "plan jest już zatwierdzony", "rows": rows}
    if rows < min_rows and not allow_small:
        return {"refused": True, "rows": rows,
                "reason": f"plan ma {rows} wierszy (< {min_rows}); pomiar M0 wymaga planu ≥ {min_rows} wierszy "
                          "(jawna zgoda na mniejszy plan: --allow-small-plan)"}
    known = len(events)
    started = clock()
    handle = start_call()
    elapsed = None
    while clock() - started < wait_limit_s:
        fresh = read_events()
        if any(e.get("type") == APPROVAL_EVENT for e in fresh[known:]):
            elapsed = clock() - started
            break
        sleep(poll_s)
    returned = clock() - started if handle.done() else None
    out = {"refused": False, "rows": rows, "event": APPROVAL_EVENT, "limitS": limit_s,
           "approvalS": round(elapsed, 3) if elapsed is not None else None,
           "callReturnedS": round(returned, 3) if returned is not None else None,
           "ok": elapsed is not None and elapsed < limit_s}
    if rows < min_rows:
        out["outsideCriterion"] = f"poza kryterium: plan {rows} wierszy (< {min_rows}), zgoda właściciela"
    if elapsed is None:
        out["error"] = f"zdarzenie {APPROVAL_EVENT} nie pojawiło się w {wait_limit_s} s"
    return out
