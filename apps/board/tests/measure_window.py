#!/usr/bin/python3
"""Pomiar prawdziwego okna Tide (PT-323, karta K-92): CPU procesu okna w wariantach i scenariuszach.

Nie jest testem (nazwa spoza wzorca test*.py). Podstawa progów CPU karty K-92; ticket „po” (#5) uruchamia TO SAMO
polecenie z tymi samymi parametrami.

Okno to prawdziwy `tide.main()` ze złożonego pakietu (widgets/stage.py --app apps/tide): Main.qml, widoki z views.json,
backend Tablicy ze stałego pliku JSON (ten sam kod co `tide.py --data board=PLIK`). Skrypt podstawia po swojej stronie
tylko to, co `--data` daje na stałe: timer w procesie woła `Backend._got_snapshot` z kadencją protokołu
(measurekit.Driver). Kod produkcyjny bez zmian (poza licznikiem delegatów BoardCard w board.js/BoardCard.qml).

Użycie (jedno powtórzenie = jeden nowy proces okna; domyślnie 3 powtórzenia, 10 s rozgrzewki + 60 s okna):
  /usr/bin/python3 apps/board/tests/measure_window.py --variant a|b --scenario idle|active5 [--cadence full|unchanged|none]
        [--no-animations] [--top WIDOK] [--platform auto|offscreen|<qpa>] [--data-dir KAT] [--out PLIK.json]
  /usr/bin/python3 apps/board/tests/measure_window.py --matrix [--only nazwa,nazwa] [--out PLIK.json]

--variant a   odwiedzona tylko Tablica; b — odwiedzone też Sprawy, Sesje, Worktrees, Docker, Raporty, Modele
              (skrypt przełącza widoki przez Shell.show, więc Loadery zostają załadowane), potem wraca na --top
              (domyślnie board),
--scenario    idle: tablica bez aktywnych kart, tylko niezmieniona migawka co 1 s; active5: 5 aktywnych kart,
              zmieniona co 2 s i niezmieniona co 1 s (--cadence zmienia kadencję: eksperyment „wstrzymane migawki”),
--no-animations  eksperyment: w KOPII pakietu animacja pracujących segmentów nie startuje (repozytorium bez zmian),
--platform    auto: sesja graficzna, gdy jest WAYLAND_DISPLAY/DISPLAY, inaczej offscreen (zapisane w wyniku).
Widoki poza Tablicą dostają puste dane (jak `tide.py --data`: bez usługi) — pomiar (b) nie obejmuje kosztu ich danych
z żywej usługi.
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

# Zestaw pomiarów karty: cztery podstawowe (wariant × scenariusz) i eksperymenty przypisania zużycia.
MATRIX = [
    ("a-idle", dict(variant="a", scenario="idle")),
    ("a-active5", dict(variant="a", scenario="active5")),
    ("b-idle", dict(variant="b", scenario="idle")),
    ("b-active5", dict(variant="b", scenario="active5")),
    ("a-active5-unchanged-only", dict(variant="a", scenario="active5", cadence="unchanged")),
    ("a-active5-no-snapshots", dict(variant="a", scenario="active5", cadence="none")),
    ("a-active5-no-animations", dict(variant="a", scenario="active5", no_animations=True)),
    ("a-active5-static-no-animations", dict(variant="a", scenario="active5", cadence="unchanged", no_animations=True)),
    ("b-active5-board-hidden", dict(variant="b", scenario="active5", top="sessions")),
]


def child(args) -> int:
    """Proces okna: tide.main() z pakietu, a w nim kadencja migawek i pomiar (wynik JSON w ostatniej linii)."""
    import importlib.util
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtTest import QTest

    spec = importlib.util.spec_from_file_location("tide_window_main", os.path.join(args.pkg, "tide.py"))
    tide = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tide)
    seen = {}

    class CapturingEngine(tide.QQmlApplicationEngine):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            seen["engine"] = self

    class CapturingShell(tide.Shell):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            seen["shell"] = self

    real_make = tide.make_backends

    def make_backends(views, bus, data, *rest):
        backends = real_make(views, bus, data, *rest)
        seen["backends"], seen["views"] = backends, views
        QTimer.singleShot(0, setup)   # po starcie pętli zdarzeń; okno jest wtedy już załadowane
        return backends

    tide.QQmlApplicationEngine, tide.Shell, tide.make_backends = CapturingEngine, CapturingShell, make_backends
    results = []

    def setup():
        try:
            window = seen["engine"].rootObjects()[0]
            shell = seen["shell"]
            if args.variant == "b":
                for view in seen["views"]:
                    shell.show(view["id"], "")
                    QTest.qWait(400)
            shell.show(args.top, "")
            QTest.qWait(600)
            board_view = mk.qml_value(seen["engine"], window, "loaders.itemAt(0).item")
            board_page = mk.qml_value(seen["engine"], board_view, "page")   # obiekt z BoardView.qml: tam jest Logic
            delegates = lambda: mk.read_counter(seen["engine"], board_page)
            driver = mk.Driver(seen["backends"]["board"], window, delegates, cadence=args.cadence,
                               warmup=args.warmup, duration=args.duration)
            driver.start(finish)
        except Exception as exc:  # wynik z błędem — rodzic pokaże przyczynę
            finish({"error": repr(exc)})

    def finish(result):
        results.append(result)
        QGuiApplication.quit()

    code = tide.main(["--view", "board", "--data", f"board={args.data}"])
    result = results[0] if results else {"error": f"okno zakończyło się bez wyniku (kod {code})"}
    result.update(variant=args.variant, scenario=args.scenario, top=args.top, platform=os.environ.get("QT_QPA_PLATFORM", "sesja"))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if "error" not in result else 1


def platform_env(choice: str) -> dict:
    env = dict(os.environ, QT_FORCE_STDERR_LOGGING="1")
    if choice == "auto":
        choice = "" if (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")) else "offscreen"
    if choice:
        env["QT_QPA_PLATFORM"] = choice
    return env


def run_combo(name: str, opts: dict, args, fixtures: dict, pkgs: dict) -> dict:
    variant, scenario = opts["variant"], opts["scenario"]
    cadence = opts.get("cadence") or ("unchanged" if scenario == "idle" else "full")
    top = opts.get("top", "board")
    animations = not opts.get("no_animations")
    if animations not in pkgs:
        pkgs[animations] = mk.stage(args.work)
        if not animations:
            mk.strip_animations(pkgs[animations])
    cmd = ["/usr/bin/python3", os.path.abspath(__file__), "--child", "--pkg", pkgs[animations],
           "--data", fixtures[scenario]["path"], "--variant", variant, "--scenario", scenario, "--cadence", cadence,
           "--top", top, "--warmup", str(args.warmup), "--duration", str(args.duration)]
    env = platform_env(args.platform)
    runs = [mk.run_child(cmd, env, args.warmup + args.duration + 180) for _ in range(args.repeats)]
    parts = ["/usr/bin/python3", "apps/board/tests/measure_window.py", "--variant", variant, "--scenario", scenario,
             "--cadence", cadence, "--top", top, "--warmup", str(args.warmup), "--duration", str(args.duration),
             "--repeats", str(args.repeats)] + ([] if animations else ["--no-animations"]) \
        + (["--platform", args.platform] if args.platform != "auto" else [])
    return {"name": name, "variant": variant, "scenario": scenario, "cadence": cadence, "top": top,
            "animations_disabled_in_copy": not animations, "command": " ".join(parts),
            "qpa_platform": env.get("QT_QPA_PLATFORM", "sesja graficzna"), **mk.aggregate(runs)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--variant", choices=("a", "b"), default="a")
    parser.add_argument("--scenario", choices=("idle", "active5"), default="idle")
    parser.add_argument("--cadence", choices=("full", "unchanged", "none"), default="")
    parser.add_argument("--no-animations", action="store_true")
    parser.add_argument("--top", default="board")
    parser.add_argument("--platform", default="auto")
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--only", default="")
    parser.add_argument("--data-dir", default=os.path.join(mk.REPO, "build", "pt-323-measure", "data"))
    parser.add_argument("--warmup", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", default="")
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--pkg", default="")
    parser.add_argument("--data", default="")
    args = parser.parse_args(argv)
    if args.child:
        return child(args)

    args.work = tempfile.mkdtemp(prefix="tide-measure-window-")
    try:
        fixtures = mk.build_fixtures(args.data_dir)
        if args.matrix:
            wanted = [n for n in args.only.split(",") if n]
            combos = [(n, o) for n, o in MATRIX if not wanted or n in wanted]
        else:
            combos = [("custom", dict(variant=args.variant, scenario=args.scenario, cadence=args.cadence,
                                      no_animations=args.no_animations, top=args.top))]
        pkgs: dict = {}
        result = {"warmup_s": args.warmup, "duration_s": args.duration, "repeats": args.repeats, "data": fixtures,
                  "cores": os.cpu_count(), "results": []}
        for name, opts in combos:
            item = run_combo(name, opts, args, fixtures, pkgs)
            result["results"].append(item)
            print(f"# {name}: cpu_pct {item['cpu_pct']}", file=sys.stderr, flush=True)
        text = json.dumps(result, ensure_ascii=False, indent=1)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        print(text)
        return 0
    finally:
        shutil.rmtree(args.work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
