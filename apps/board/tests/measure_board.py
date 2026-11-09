#!/usr/bin/python3
"""Sonda offscreen Tablicy (PT-323, karta K-92): diagnostyka i liczniki na istniejącym probe.py.

Nie jest testem (nazwa spoza wzorca test*.py — `make test-board` jej nie uruchamia, tools/test_suites.py bez zmian).
Podstawą progów CPU karty jest prawdziwe okno Tide (measure_window.py), nie ta sonda; sonda daje czas migawki
(snapshot_ms), licznik delegatów BoardCard i porównanie skali z oknem.

Użycie: /usr/bin/python3 apps/board/tests/measure_board.py [--data-dir KAT] [--warmup 10] [--duration 60]
                                                           [--repeats 3] [--out PLIK.json]
Dwa scenariusze z tych samych plików danych (measurekit.build_fixtures): idle (tablica bez aktywnych kart, tylko
niezmieniona migawka co 1 s) i active5 (5 aktywnych kart; zmieniona co 2 s i niezmieniona co 1 s). Migawki idą przez
`Backend._got_snapshot` prawdziwego backendu (probe.py podstawia tylko źródło danych), więc identyczna migawka jest
odfiltrowana przed QML jak w produkcji. Osobny scenariusz `direct` podaje identyczne migawki WPROST do QML
(Scripted.set_snapshot — przypadek „odporność”, w produkcji nie występuje) i liczy utworzone delegaty.

Drukuje JSON: idle_cpu_pct, active5_cpu_pct (mediana/min/max z powtórzeń), snapshot_ms (mediana i maksimum, active5),
delegate_creations, loadavg, direct_identical.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measurekit as mk  # noqa: E402

PROBE = os.path.join(mk.HERE, "probe.py")

# Scenariusz wykonywany wewnątrz probe.py (globals: backend, window, js, until, out…); parametry są wpisane na początku.
CADENCE_SCENARIO = """
import measurekit
page_obj = js("page")
driver = measurekit.Driver(backend, window, lambda: measurekit.read_counter(engine, page_obj),
                           cadence={cadence!r}, warmup={warmup!r}, duration={duration!r})
done = []
driver.start(done.append)
until(lambda: done, ms=int(({warmup!r} + {duration!r} + 30) * 1000))
out.update(done[0] if done else {{"error": "pomiar nie zakończył się"}})
"""

# Identyczne migawki wprost do QML (Scripted.set_snapshot zawsze emituje snapshotChanged).
DIRECT_SCENARIO = """
import measurekit
page_obj = js("page")
counter = lambda: measurekit.read_counter(engine, page_obj)
snap = json.loads(backend.snapshotJson)
wait(500)
before = counter()
per = []
for _ in range({count!r}):
    last = counter()
    set_snapshot(snap)
    per.append(counter() - last)
out["cards_in_snapshot"] = len(snap["cards"])
out["delegates_before"] = before
out["delegates_per_identical_snapshot"] = per
out["delegates_total"] = counter() - before
"""


def run_probe(pkg: str, data: str, scenario: str, timeout: float) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(scenario)
        path = fh.name
    try:
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        return mk.run_child(["/usr/bin/python3", PROBE, pkg, data, path], env, timeout)
    finally:
        os.unlink(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", default=os.path.join(mk.REPO, "build", "pt-323-measure", "data"))
    parser.add_argument("--warmup", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--direct-count", type=int, default=5)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    work = tempfile.mkdtemp(prefix="tide-measure-board-")
    try:
        pkg = mk.stage(work)
        fixtures = mk.build_fixtures(args.data_dir)
        timeout = args.warmup + args.duration + 120
        result = {"command": "/usr/bin/python3 " + " ".join([os.path.relpath(__file__, mk.REPO), *(argv or sys.argv[1:])]),
                  "platform": "offscreen (probe.py)", "warmup_s": args.warmup, "duration_s": args.duration,
                  "repeats": args.repeats, "data": fixtures}
        for name, cadence in (("idle", "unchanged"), ("active5", "full")):
            scenario = CADENCE_SCENARIO.format(cadence=cadence, warmup=args.warmup, duration=args.duration)
            runs = [run_probe(pkg, fixtures[name]["path"], scenario, timeout) for _ in range(args.repeats)]
            agg = mk.aggregate(runs)
            result[name] = agg
            result[f"{name}_cpu_pct"] = agg["cpu_pct"]
        result["snapshot_ms"] = result["active5"]["snapshot_ms"]
        result["delegate_creations"] = {"idle": result["idle"]["delegate_creations"],
                                        "active5": result["active5"]["delegate_creations"]}
        result["loadavg"] = {"idle": result["idle"]["loadavg"], "active5": result["active5"]["loadavg"]}
        result["direct_identical"] = run_probe(pkg, fixtures["active5"]["path"],
                                               DIRECT_SCENARIO.format(count=args.direct_count), 180)
        text = json.dumps(result, ensure_ascii=False, indent=1)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        print(text)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
