#!/usr/bin/python3
"""Ocena pomiarów reproduktora skali produkcji K-98 (`perf_scale_k98.py`, PT-362) — progi wpisane na stałe.

Tryb `reproduce` (pomiar „przed”, jeden wynik `box.enabled=true`): objaw odtworzony, gdy w fazie `load`
p95 `GetCardFlowSettings` albo `Snapshot` ≥ 1000 ms ALBO udział `_rewatch` w próbkach wątku głównego ≥ 10 %.
Skala poniżej produkcji (`belowProduction`) albo brak próbek to brak odtworzenia.

Tryb `gate` (pomiar „po” = końcowa bramka karty; wyniki `box.enabled` true i false, wynik „przed” do porównania,
opcjonalnie wyniki reproduktora PT-342 `perf_board_refresh.py` idle i busy):
- faza `load`, każda wartość `box`: p95 < 1000 ms i max < 2000 ms obu metod oraz udział `_rewatch` < 2 %,
  a gdy p95/max nie spełniają progu — wariant warunkowy: `_rewatch` < 2 %, spadek p95 każdej metody ≥ 50 % względem
  „przed” i podział kosztu wskazujący `scanner.sessions`/`gitinfo.describe` (ich udział większy niż `_rewatch`):
  ticket do odbioru, karta K-98 zablokowana do scalenia (#7),
- faza `idle`, każda wartość `box`: CPU procesu usługi < 30 % (z podziałem kosztu z próbnika w wyniku),
- regresja bramki PT-345 reproduktorem PT-342 (progi i sprawdzenia z `perf_gate_k98_eval.refresh_checks`).

Użycie: /usr/bin/python3 daemon/tests/perf_scale_k98_eval.py reproduce <wynik.json> --out <katalog>
        /usr/bin/python3 daemon/tests/perf_scale_k98_eval.py gate --before <przed.json> --on <po-on.json>
            --off <po-off.json> [--refresh-idle <head.json>] [--refresh-busy <head.json>] --out <katalog>
Wynik: <katalog>/<tryb>.json i <tryb>.txt; kod 0 — spełnione (także warunkowo, z jawnym opisem), 1 — niespełnione,
2 — brak danych.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import perf_gate_k98_eval as k98  # noqa: E402

METHODS = ("GetCardFlowSettings", "Snapshot")
REWATCH = "board_monitor.py:_rewatch"
SCAN = ("scanner.py:sessions", "gitinfo.py:describe")
REPRODUCE_P95_MS = 1000.0
REPRODUCE_REWATCH = 0.10
GATE_P95_MS = 1000.0
GATE_MAX_MS = 2000.0
GATE_REWATCH = 0.02
GATE_DROP = 0.50
IDLE_CPU_PERCENT = 30.0


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        return None


def phase(result, name):
    return next((p for p in (result or {}).get("phases") or [] if p.get("phase") == name), None)


def share(item, name):
    return (((item or {}).get("profile") or {}).get("shares") or {}).get(name)


def reproduce(result):
    checks = []
    if result is None:
        return [{"check": "wynik „przed”", "ok": False, "detail": "brak danych"}], False
    below = result.get("belowProduction")
    checks.append({"check": "skala produkcji", "ok": not below, "detail": str(result.get("scale")) +
                   (f"; poniżej: {below}" if below else "")})
    load_phase = phase(result, "load")
    slow = []
    for method in METHODS:
        data = ((load_phase or {}).get("methods") or {}).get(method) or {}
        p95 = data.get("p95Ms")
        checks.append({"check": f"{method} p95 ≥ {REPRODUCE_P95_MS:g} ms (objaw)",
                       "ok": p95 is not None and p95 >= REPRODUCE_P95_MS,
                       "detail": f"n={data.get('n')}, p95={p95}, max={data.get('maxMs')}"})
        slow.append(p95 is not None and p95 >= REPRODUCE_P95_MS)
    rewatch = share(load_phase, REWATCH)
    checks.append({"check": f"udział _rewatch ≥ {REPRODUCE_REWATCH:.0%} (objaw)",
                   "ok": rewatch is not None and rewatch >= REPRODUCE_REWATCH, "detail": f"udział={rewatch}"})
    reproduced = not below and (any(slow) or (rewatch is not None and rewatch >= REPRODUCE_REWATCH))
    return checks, reproduced


def gate_side(after, before, label):
    checks, conditional = [], []
    if after is None:
        return [{"check": f"{label}: dane", "ok": False, "detail": "brak wyniku"}], True
    below = after.get("belowProduction")
    checks.append({"check": f"{label}: skala produkcji", "ok": not below, "detail": str(after.get("scale"))})
    load_phase, idle_phase = phase(after, "load"), phase(after, "idle")
    before_load = phase(before, "load")
    rewatch = share(load_phase, REWATCH)
    rewatch_ok = rewatch is not None and rewatch < GATE_REWATCH
    checks.append({"check": f"{label}: udział _rewatch < {GATE_REWATCH:.0%} (load)", "ok": rewatch_ok,
                   "detail": f"udział={rewatch}"})
    idle_rewatch = share(idle_phase, REWATCH)
    checks.append({"check": f"{label}: udział _rewatch < {GATE_REWATCH:.0%} (idle)",
                   "ok": idle_rewatch is not None and idle_rewatch < GATE_REWATCH, "detail": f"udział={idle_rewatch}"})
    timing_ok = True
    drops_ok = True
    for method in METHODS:
        data = ((load_phase or {}).get("methods") or {}).get(method) or {}
        p95, peak = data.get("p95Ms"), data.get("maxMs")
        ok = p95 is not None and peak is not None and p95 < GATE_P95_MS and peak < GATE_MAX_MS
        timing_ok = timing_ok and ok
        checks.append({"check": f"{label}: {method} p95 < {GATE_P95_MS:g} ms i max < {GATE_MAX_MS:g} ms", "ok": ok,
                       "detail": f"n={data.get('n')}, p95={p95}, max={peak}"})
        old = (((before_load or {}).get("methods") or {}).get(method) or {}).get("p95Ms")
        drop = (1.0 - p95 / old) if p95 is not None and old else None
        drops_ok = drops_ok and drop is not None and drop >= GATE_DROP
        conditional.append(f"{method}: p95 przed {old}, po {p95}, spadek {drop if drop is None else round(drop, 3)}")
    errors = len((load_phase or {}).get("errors") or [])
    checks.append({"check": f"{label}: wywołania bez błędów", "ok": errors == 0, "detail": f"błędy={errors}"})
    cpu = (idle_phase or {}).get("cpu") or {}
    percent = cpu.get("percent")
    checks.append({"check": f"{label}: CPU usługi na postoju < {IDLE_CPU_PERCENT:g} % (idle {idle_phase and idle_phase.get('seconds')} s)",
                   "ok": percent is not None and percent < IDLE_CPU_PERCENT,
                   "detail": f"CPU={percent} %, z potomnymi {cpu.get('percentWithChildren')} %; "
                             f"udziały idle: {((idle_phase or {}).get('profile') or {}).get('shares')}"})
    scan = max((share(load_phase, name) or 0.0) for name in SCAN)
    scan_dominates = scan > (rewatch or 0.0)
    conditional_ok = not timing_ok and rewatch_ok and drops_ok and scan_dominates
    if not timing_ok:
        checks.append({"check": f"{label}: wariant warunkowy (_rewatch < 2 %, spadek p95 ≥ 50 %, koszt w "
                                f"scanner.sessions/gitinfo.describe)", "ok": conditional_ok,
                       "detail": "; ".join(conditional) + f"; udział scanner/gitinfo={scan}, _rewatch={rewatch}"})
    # Drugie pole: czasy tej strony spełnione wprost albo wariantem warunkowym (brak danych to osobna porażka).
    return checks, timing_ok or conditional_ok


def gate(before, on, off, refresh_idle, refresh_busy):
    """(sprawdzenia, czasy obu stron spełnione wprost albo warunkowo)."""
    checks, timing = [], True
    for label, after in (("box on", on), ("box off", off)):
        side, ok = gate_side(after, before, label)
        timing = timing and ok
        checks += side
    for label, data in (("PT-342 refresh-idle", refresh_idle), ("PT-342 refresh-busy", refresh_busy)):
        if data is not None:
            checks += k98.refresh_checks(data, label)
        else:
            checks.append({"check": f"{label}: dane", "ok": False, "detail": "brak wyniku"})
    return checks, timing


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("reproduce", "gate"))
    parser.add_argument("result", nargs="?")
    parser.add_argument("--before")
    parser.add_argument("--on")
    parser.add_argument("--off")
    parser.add_argument("--refresh-idle")
    parser.add_argument("--refresh-busy")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    if args.mode == "reproduce":
        result = load(args.result)
        checks, reproduced = reproduce(result)
        verdict = ("objaw odtworzony" if reproduced else
                   "objaw NIE odtworzony — dalsze kroki wstrzymane, pytanie do koordynatora")
        code = 2 if result is None else 0 if reproduced else 1
        report = {"mode": "reproduce", "verdict": verdict, "reproduced": reproduced, "checks": checks}
    else:
        before = load(args.before)
        checks, timing_ok = gate(before, load(args.on), load(args.off), load(args.refresh_idle),
                                 load(args.refresh_busy))
        hard = [c for c in checks if not c["ok"] and "wariant warunkowy" not in c["check"]
                and not ("p95 <" in c["check"] and "max <" in c["check"])]
        timing = [c for c in checks if not c["ok"] and "p95 <" in c["check"] and "max <" in c["check"]]
        conditional = bool(timing) and timing_ok
        if not hard and not timing:
            verdict, code = "bramka spełniona", 0
        elif not hard and conditional:
            verdict, code = ("spełnione WARUNKOWO (koszt w scanner/gitinfo): ticket do odbioru, karta K-98 "
                             "zablokowana do scalenia, koordynator dopisuje #7"), 0
        else:
            verdict, code = "bramka NIESPEŁNIONA", 1
        if before is None:
            verdict, code = "brak wyniku „przed”", 2
        report = {"mode": "gate", "verdict": verdict, "conditional": conditional, "checks": checks}
    with open(os.path.join(args.out, f"{args.mode}.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    lines = [f"werdykt: {report['verdict']}"]
    lines += [f"{'OK ' if c['ok'] else 'NIE'} {c['check']} — {c['detail']}" for c in report["checks"]]
    with open(os.path.join(args.out, f"{args.mode}.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
