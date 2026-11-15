#!/usr/bin/python3
"""Raport „po” K-125 (PT-489): z wyników kolejki `perf_k105_queue.py measure after <box>` składa plik
`docs/agents/masterplan/2026-11-15/pomiar-k125-po.json` i porównuje go z `pomiar-k125-przed.json` (PT-486).

Raport niczego nie mierzy i nie zmienia miary: liczby pochodzą wyłącznie z wyniku narzędzia pomiaru. Zawiera jawne
werdykty karty:

- `threshold`: `pass` tylko, gdy w obu trybach `box.enabled` jest ważna próba i **każde** okno ≤ 30 % rdzenia (nie
  średnia); `fail` przy oknie ponad próg; `invalid`, gdy któryś tryb nie ma ważnej próby;
- `outsideShare`: udział kategorii `outside` (`engine.py:_consent_recheck`) w próbkach `engine.py:tick`, osobno dla
  każdego trybu;
- `recommendation`: reguła z karty — `pass` → „#5 i #6 odrzucić”; `fail` i `outside` ≥ 10 % próbek ticku w którymś
  trybie → „#5 potrzebny”; `fail` z kosztem gdzie indziej → pytanie z podziałem próbek (bez zmian kodu).

    /usr/bin/python3 daemon/tests/perf_k125_after.py --before <pomiar-k125-przed.json> [--dir build/k105-measure] [--out <pomiar-k125-po.json>]

Pliki „przed” i „po” leżą w `docs/agents/masterplan/2026-11-15/`; ścieżki podaje wywołujący (skan zależności testów
nie widzi wtedy odczytu pliku koordynacji w kodzie narzędzia).

Kod wyjścia: 0 — oba tryby ważne (`pass` albo `fail`; to wynik pomiaru, nie błąd); 1 — brak ważnego wyniku któregoś
trybu.
"""
import argparse
import json
import os
import sys

try:
    from . import perf_k105_queue as queue
    from . import perf_k125_report as base
except ImportError:
    import perf_k105_queue as queue
    import perf_k125_report as base

LIMIT_PERCENT = 30.0
OUTSIDE_NEEDED_SHARE = 0.10
EXTRA_FRAMES = ("_resume_step", "_inside")
RECOMMENDATION = {"pass": "#5 i #6 odrzucić", "outside": "#5 potrzebny",
                  "question": "wynik `question` z podziałem próbek i opcjami kolejnego kroku, bez zmian kodu",
                  "invalid": "brak ważnego pomiaru — powtórz polecenie `measure after`"}


def frame_samples(report, names=EXTRA_FRAMES):
    """Próbki (czas łączny) ramek o podanych nazwach funkcji z raportu próbnika: {nazwa: {frame: próbki}}.

    Dopasowanie po nazwie funkcji (część po `:`), bo `_inside` to metoda w `store.py`, a `_resume_step` w `engine.py`."""
    out, section = {name: {} for name in names}, ""
    for line in (report or "").splitlines():
        if line.startswith("## "):
            section = line
        elif section.startswith("## Funkcje — czas łączny") and line.strip():
            number, _, frame = line.strip().partition("  ")
            frame = frame.strip()
            function = frame.rsplit(":", 1)[-1]
            if function in out:
                try:
                    out[function][frame] = int(number)
                except ValueError:
                    continue
    return out


def attempt_report(document):
    attempt = base.decisive(document) or {}
    return ((attempt.get("result") or {}).get("profileWindow") or {}).get("report")


def threshold_of(blocks):
    """`pass` / `fail` / `invalid` z okien obu trybów — każde okno osobno względem progu."""
    if any(b is None for b in blocks.values()):
        return "invalid"
    windows = [w for b in blocks.values() for w in (b["firstValid"].get("windowsPercent") or [])]
    if len(windows) != 2 * 10 or any(w is None for w in windows):
        return "invalid"
    return "pass" if all(w <= LIMIT_PERCENT for w in windows) else "fail"


def recommendation_of(threshold, outside_share):
    if threshold == "invalid":
        return RECOMMENDATION["invalid"]
    if threshold == "pass":
        return RECOMMENDATION["pass"]
    if any(s is not None and s >= OUTSIDE_NEEDED_SHARE for s in outside_share.values()):
        return RECOMMENDATION["outside"]
    return RECOMMENDATION["question"]


def compare(before, after):
    """Porównanie „przed” → „po” jednego trybu (bloki `firstValid`); brak strony daje `None` w polu."""
    def get(block, *path):
        for key in path:
            block = (block or {}).get(key) if isinstance(block, dict) else None
        return block

    def pair(*path):
        return {"before": get(before, *path), "after": get(after, *path)}

    comps = {name: pair("profile", "components", name, "shareOfTick") for name in ("outside", "plan", "inventory")}
    return {"windowsPercent": pair("windowsPercent"), "meanPercent": pair("meanPercent"),
            "maxPercent": pair("maxPercent"), "tickShare": pair("tick", "share"),
            "tickShareOfWork": pair("profile", "work", "tickShare"),
            "tickSamples": pair("tick", "samples"), "componentShareOfTick": comps,
            "componentSamples": {name: pair("profile", "components", name, "samples") for name in comps},
            "systemctlCallsInCpuWindow": pair("fakeSystemctl", "callsInCpuWindow"),
            "systemctlCallsPerTick": pair("fakeSystemctl", "callsPerTick"),
            "snapshotP95Ms": pair("snapshot", "p95Ms"),
            "vmRssStartMb": pair("memory", "vmRssStartMb"), "vmRssEndMb": pair("memory", "vmRssEndMb"),
            "vmHwmMb": pair("memory", "vmHwmMb"),
            "sameRoles": get(before, "scale", "roles") == get(after, "scale", "roles")}


def build(documents, before_report):
    """`documents`: {"false": dokument, "true": dokument} strony „po”; `before_report`: wczytany plik „przed”."""
    blocks = {key: base.box_block(documents[key]) if documents.get(key) else None for key in ("false", "true")}
    for key, block in blocks.items():
        if block:
            block["firstValid"]["profile"]["resumeStoreFrames"] = frame_samples(attempt_report(documents[key]))
    before_blocks = {key: ((before_report or {}).get("byBoxEnabled") or {}).get(key) for key in ("false", "true")}
    outside = {key: ((b or {}).get("firstValid", {}).get("profile", {}).get("components", {})
                     .get("outside", {}).get("shareOfTick")) for key, b in blocks.items()}
    threshold = threshold_of(blocks)
    shas = {(documents.get(key) or {}).get("baseSha") for key in ("false", "true")}
    run_shas = {((b or {}).get("firstValid") or {}).get("sha") for b in blocks.values()}
    return {
        "tool": "daemon/tests/perf_k125_after.py", "kind": "pomiar-po-K-125", "side": "after",
        "sha": next(iter(run_shas)) if len(run_shas) == 1 else sorted(s for s in run_shas if s),
        "beforeSha": (before_report or {}).get("baseSha"), "ticket": "PT-489", "card": "K-125",
        "source": "build/k105-measure/after-box-<false|true>.json (pierwsza wazna proba); przed: pomiar-k125-przed.json",
        "limitPercent": LIMIT_PERCENT,
        "threshold": threshold, "outsideShare": outside,
        "outsideNeededShare": OUTSIDE_NEEDED_SHARE,
        "recommendation": recommendation_of(threshold, outside),
        "byBoxEnabled": blocks,
        "comparison": {key: compare((before_blocks[key] or {}).get("firstValid"),
                                    (blocks[key] or {}).get("firstValid")) for key in ("false", "true")},
        "criteria": {
            "bothModesValid": all(blocks.values()),
            "snapshotP95UnderOneSecond": {key: (((b or {}).get("firstValid") or {}).get("snapshot") or {})
                                          .get("p95Ms", 1e9) < 1000.0 for key, b in blocks.items()},
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default=queue.MEASURE_DIR)
    parser.add_argument("--before", required=True, help="plik „przed” z `perf_k125_report.py` (PT-486)")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    documents = {key: queue.read_json(queue.result_path("after", key == "true", args.dir))
                 for key in ("false", "true")}
    report = build(documents, queue.read_json(args.before))
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    print(f"tryby ważne: {report['criteria']['bothModesValid']}; threshold: {report['threshold']}; "
          f"outsideShare: {report['outsideShare']}; rekomendacja: {report['recommendation']}", flush=True)
    return 0 if report["criteria"]["bothModesValid"] else 1


if __name__ == "__main__":
    sys.exit(main())
