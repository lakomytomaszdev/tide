#!/usr/bin/python3
"""Ocena bramki K-98 (PT-345) z surowych próbek `perf_gate_k98.sh` — progi wpisane na stałe, bez obniżania.

Progi HEAD (kandydat):
- odświeżenie Tablicy (30 kart, ≥ 30 kolejnych): każde ≤ 500 ms i najwyżej jedno wczytanie Tablicy na odświeżenie,
  bez obciążenia i przy zajętym workerze,
- p95 nearest-rank (100 żądań) < 1000 ms dla Snapshot (= Panel karty), GetCardFlowSettings i BoxStatus, bez obciążenia
  i przy zajętym workerze (scenariusz PT-343),
- p95 pierwszego wywołania serii D-Bus (20 serii) < 1000 ms, przed (początek ticketu) i po (HEAD).
Wyniki bazy karty są raportowane obok (z oceną tych samych progów) i nie wpływają na wynik kandydata, z wyjątkiem
pomiaru „przed” pierwszego wywołania, który kryterium wymienia jawnie (raportowany, ocena osobno).

Użycie: /usr/bin/python3 daemon/tests/perf_gate_k98_eval.py <katalog wyniku> [--attempt N]
Wynik: <katalog>/gate.json, <katalog>/gate.txt; kod 0 — wszystkie progi kandydata spełnione w ważnej próbie,
1 — próg niespełniony, 2 — brak ważnej próby albo brak danych.
"""
import argparse
import json
import math
import os
import sys

REFRESH_MAX_MS = 500.0
REFRESH_MIN_COUNT = 30
REFRESH_MAX_LOADS = 1
METHOD_P95_MS = 1000.0
FIRST_CALL_P95_MS = 1000.0
FIRST_CALL_SERIES = 20
METHOD_REQUESTS = 100
GATED_METHODS = ("Snapshot", "GetCardFlowSettings", "BoxStatus")


def nearest_rank(samples, q=0.95):
    if not samples:
        return None
    ordered = sorted(samples)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def refresh_checks(data, label):
    checks = []
    if data is None:
        return [{"check": f"{label}: dane", "ok": False, "detail": "brak wyniku"}]
    samples = data["refresh"]["samplesMs"]
    loads = data["refresh"]["boardLoads"]
    over = [round(s, 1) for s in samples if s > REFRESH_MAX_MS]
    checks.append({"check": f"{label}: ≥ {REFRESH_MIN_COUNT} kolejnych odświeżeń, każde ≤ {REFRESH_MAX_MS:g} ms",
                   "ok": len(samples) >= REFRESH_MIN_COUNT and not over,
                   "detail": f"n={len(samples)}, max={max(samples, default=0):.1f} ms, ponad progiem: {over}"})
    checks.append({"check": f"{label}: najwyżej {REFRESH_MAX_LOADS} wczytanie Tablicy na odświeżenie",
                   "ok": bool(loads) and max(loads) <= REFRESH_MAX_LOADS, "detail": f"max={max(loads, default=None)}"})
    for method in GATED_METHODS:
        values = data["methods"].get(method, {}).get("samplesMs") or []
        p95 = nearest_rank(values)
        checks.append({"check": f"{label}: {method} p95 < {METHOD_P95_MS:g} ms ({METHOD_REQUESTS} żądań)",
                       "ok": len(values) >= METHOD_REQUESTS and p95 is not None and p95 < METHOD_P95_MS,
                       "detail": f"n={len(values)}, p95={p95:.2f} ms" if p95 is not None else "brak próbek"})
    return checks


def first_call_checks(data, label):
    if data is None:
        return [{"check": f"{label}: dane", "ok": False, "detail": "brak wyniku"}]
    samples = data.get("firstCallMs") or []
    p95 = nearest_rank(samples)
    return [{"check": f"{label}: p95 pierwszego wywołania serii < {FIRST_CALL_P95_MS:g} ms ({FIRST_CALL_SERIES} serii)",
             "ok": len(samples) >= FIRST_CALL_SERIES and p95 is not None and p95 < FIRST_CALL_P95_MS
             and not data.get("errors"),
             "detail": (f"n={len(samples)}, p95={p95:.1f} ms, max={max(samples):.1f} ms, błędy={len(data.get('errors') or [])}"
                        if samples else "brak próbek")}]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--attempt", type=int)
    args = parser.parse_args(argv)
    report = {"attempt": args.attempt, "candidate": [], "base": [], "cold": [], "firstCall": {}}
    if args.attempt is None:
        report["verdict"] = "brak ważnej próby (loadavg) — odbiór wstrzymany, pytanie do koordynatora"
        code = 2
    else:
        d = os.path.join(args.out, f"attempt-{args.attempt}")
        for scenario in ("refresh-idle", "refresh-busy"):
            report["candidate"] += refresh_checks(load(os.path.join(d, scenario, "head.json")), f"HEAD {scenario}")
            report["base"] += refresh_checks(load(os.path.join(d, scenario, "base.json")), f"baza {scenario}")
        for i in range(1, 6):
            for side in ("base", "head"):
                data = load(os.path.join(d, f"cold-{i}", f"{side}.json")) or {}
                cold = data.get("coldStart") or {}
                report["cold"].append({"run": i, "side": side, "ms": cold.get("ms"), "boardLoads": cold.get("boardLoads"),
                                       "sha": data.get("sha"), "loadavgBefore": data.get("loadavgBefore")})
        before = load(os.path.join(d, "first-call-base.json"))
        after = load(os.path.join(d, "first-call-head.json"))
        report["firstCall"] = {"before": first_call_checks(before, "przed (początek ticketu)"),
                               "after": first_call_checks(after, "po (HEAD)"),
                               "beforeSamplesMs": (before or {}).get("firstCallMs"),
                               "afterSamplesMs": (after or {}).get("firstCallMs")}
        report["candidate"] += report["firstCall"]["after"]
        candidate_ok = all(c["ok"] for c in report["candidate"])
        before_ok = all(c["ok"] for c in report["firstCall"]["before"])
        report["verdict"] = ("progi kandydata spełnione" if candidate_ok else "próg kandydata NIESPEŁNIONY") + \
            ("; pomiar „przed” pierwszego wywołania spełnia próg" if before_ok else
             "; pomiar „przed” pierwszego wywołania NIE spełnia progu (jawnie w notce)")
        code = 0 if candidate_ok else 1
    with open(os.path.join(args.out, "gate.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    lines = [f"werdykt: {report['verdict']}"]
    for key in ("candidate", "base"):
        lines += [f"[{key}] {'OK ' if c['ok'] else 'NIE'} {c['check']} — {c['detail']}" for c in report[key]]
    for c in report["firstCall"].get("before") or []:
        lines.append(f"[before] {'OK ' if c['ok'] else 'NIE'} {c['check']} — {c['detail']}")
    lines += [f"[cold] {c['side']} #{c['run']}: {c['ms']} ms, wczytania {c['boardLoads']}" for c in report["cold"]]
    with open(os.path.join(args.out, "gate.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
