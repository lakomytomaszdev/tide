#!/usr/bin/python3
"""Raport „przed” K-125 (PT-486): z wyników kolejki `perf_k105_queue.py measure before <box>` składa plik do repozytorium
`docs/agents/masterplan/2026-11-15/pomiar-k125-przed.json` (wzór: `pomiar-k113-przed.json`).

Wynik zawiera osobno dla `box.enabled` false i true: SHA bazy, loadavg (przed, początek i koniec okien CPU, po),
CPU okien i średnią, podział próbek z udziałami `outside`/`plan`/`inventory` względem `engine.py:tick`, liczbę wywołań
atrapy `systemctl` w oknie CPU, `Snapshot` p95 oraz kryteria czerwonego wyniku i rzędu produkcji profilu
(`perf_idle_scale.profile_check`; progi są stałymi modułu, ustalonymi przed pomiarem). Liczby pochodzą wyłącznie z wyniku
narzędzia pomiaru — raport niczego nie mierzy.

    /usr/bin/python3 daemon/tests/perf_k125_report.py [--dir build/k105-measure] [--out docs/agents/masterplan/2026-11-15/pomiar-k125-przed.json]

Kod wyjścia: 0 — oba tryby mają ważny wynik i spełniają kryteria (czerwony + profil); 1 — brak ważnego wyniku
któregoś trybu; 2 — wynik ważny, ale kryteria niespełnione (ticket kończy się wtedy pytaniem, nie `done`).
"""
import argparse
import json
import os
import sys

try:
    from . import perf_idle_scale as scale_mode
    from . import perf_k105_queue as queue
except ImportError:
    import perf_idle_scale as scale_mode
    import perf_k105_queue as queue

TOP_FRAMES = 15


def top_frames(report, count=TOP_FRAMES):
    """Najcięższe ramki z sekcji „czas łączny” raportu próbnika: [{samples, frame}]."""
    out, section = [], ""
    for line in (report or "").splitlines():
        if line.startswith("## "):
            section = line
        elif section.startswith("## Funkcje — czas łączny") and line.strip():
            number, _, name = line.strip().partition("  ")
            try:
                out.append({"samples": int(number), "frame": name.strip()})
            except ValueError:
                continue
            if len(out) >= count:
                break
    return out


def decisive(document):
    """Rozstrzygająca (pierwsza ważna) próba dokumentu konfiguracji albo None."""
    number = document.get("decisive")
    return next((a for a in document.get("attempts") or [] if a.get("n") == number and a.get("status") == "valid"),
                None)


def box_block(document):
    """Blok jednego trybu box z rozstrzygającej próby; None, gdy nie ma ważnej próby."""
    attempt = decisive(document)
    if attempt is None:
        return None
    result, evaluation = attempt.get("result") or {}, attempt.get("evaluation") or {}
    profile = result.get("profileWindow") or {}
    split = profile.get("split") or {}
    criteria = scale_mode.profile_check(result)
    mean = evaluation.get("meanPercent")
    return {
        "boxEnabled": document.get("boxEnabled"), "baseSha": document.get("baseSha"),
        "attempts": [{key: a.get(key) for key in ("n", "status", "startedAt", "finishedAt", "reason")}
                     for a in document.get("attempts") or []],
        "decisiveAttempt": document.get("decisive"),
        "firstValid": {
            "n": attempt["n"], "startedAt": attempt.get("startedAt"), "finishedAt": attempt.get("finishedAt"),
            "sha": result.get("sha"), "params": result.get("params"), "cpuCount": result.get("cpuCount"),
            "loadavg": {key: result.get(name) for key, name in (
                ("before", "loadavgBefore"), ("windowStart", "loadavgWindowStart"),
                ("end", "loadavgEnd"), ("after", "loadavgAfter"))},
            "windowsPercent": evaluation.get("windowsPercent"), "meanPercent": mean,
            "maxPercent": evaluation.get("maxPercent"), "limitPercent": evaluation.get("limitPercent"),
            "windowsOverLimit": evaluation.get("windowsOverLimit"), "verdict": evaluation.get("verdict"),
            "cpuWindows": (result.get("cpuWindows") or {}).get("windows"),
            "profile": {"busySamples": split.get("busySamples"), "samples": split.get("samples"),
                        "share": split.get("share"), "work": split.get("work"), "components": split.get("components"),
                        "frames": split.get("frames"), "topFrames": top_frames(profile.get("report"))},
            "tick": result.get("tick"), "fakeSystemctl": result.get("fakeSystemctl"),
            "windowMarks": result.get("windowMarks"), "snapshot": result.get("snapshot"),
            "flowThread": result.get("flowThread"), "memory": result.get("memory"),
            "scale": result.get("scale"), "daemonAlive": result.get("daemonAlive"),
        },
        "criteria": {"red": mean is not None and mean > scale_mode.RED_MEAN_PERCENT, "profile": criteria,
                     "profileOk": scale_mode.profile_ok(criteria)},
    }


def build(documents, ticket="PT-486", card="K-125"):
    """`documents`: {"false": dokument, "true": dokument} z `perf_k105_queue.result_path`. Brak ważnej próby trybu
    zostaje w raporcie jako `None` (kryteria niespełnione)."""
    blocks = {key: box_block(documents[key]) if documents.get(key) else None for key in ("false", "true")}
    present = [b for b in blocks.values() if b]
    red = any(b["criteria"]["red"] for b in present)
    profile_ok = len(present) == 2 and all(b["criteria"]["profileOk"] for b in present)
    shas = {(documents.get(key) or {}).get("baseSha") for key in ("false", "true")}
    return {
        "tool": "daemon/tests/perf_k125_report.py", "kind": "pomiar-przed-K-125", "side": "before",
        "baseSha": next(iter(shas)) if len(shas) == 1 else sorted(s for s in shas if s),
        "ticket": ticket, "card": card,
        "source": "build/k105-measure/before-box-<false|true>.json (pierwsza wazna proba)",
        "byBoxEnabled": blocks,
        "criteria": {
            "bothModesValid": len(present) == 2,
            "red": {"ok": red, "limitMeanPercent": scale_mode.RED_MEAN_PERCENT,
                    "meanPercentByBox": {k: (b or {}).get("firstValid", {}).get("meanPercent")
                                         for k, b in blocks.items()}},
            "profile": {"ok": profile_ok,
                        "failedByBox": {k: [c["name"] for c in b["criteria"]["profile"] if c["ok"] is not True]
                                        for k, b in blocks.items() if b}},
            "allMet": len(present) == 2 and red and profile_ok,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default=queue.MEASURE_DIR)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    documents = {key: queue.read_json(queue.result_path("before", key == "true", args.dir))
                 for key in ("false", "true")}
    report = build(documents)
    text = json.dumps(report, ensure_ascii=False, indent=1) + "\n"
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    criteria = report["criteria"]
    print(f"tryby ważne: {criteria['bothModesValid']}; czerwony: {criteria['red']['ok']} "
          f"{criteria['red']['meanPercentByBox']}; profil: {criteria['profile']['ok']} "
          f"{criteria['profile']['failedByBox']}", flush=True)
    if not criteria["bothModesValid"]:
        return 1
    return 0 if criteria["allMet"] else 2


if __name__ == "__main__":
    sys.exit(main())
