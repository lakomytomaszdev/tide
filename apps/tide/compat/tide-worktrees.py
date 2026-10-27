#!/usr/bin/python3
"""Zgodność z dawnym oknem „Tide Worktrees”: to samo wywołanie otwiera okno Tide na widoku Worktrees.

Instalowany pod starą ścieżką ($XDG_DATA_HOME/tide/worktrees-window/tide-worktrees.py), żeby działały
wszystkie miejsca, które ją znają (tide-daemon sprzed aktualizacji, przypięte skróty, skrypty).
Opcje jak dawniej: --data PLIK, --grab PNG, --width/--height, --select ŚCIEŻKA (zaznaczenie do
podglądu, można powtórzyć), --confirm (otwarte potwierdzenie usunięcia do podglądu).
Okno Tide: $TIDE_WINDOW, obok (../tide-window/tide.py po instalacji) albo w pakiecie (../tide.py).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def tide_window() -> str:
    candidates = [os.environ.get("TIDE_WINDOW", ""), os.path.join(HERE, "..", "tide-window", "tide.py"),
                  os.path.join(HERE, "..", "tide.py")]
    return next((os.path.normpath(c) for c in candidates if c and os.path.isfile(c)), "")


def translate(argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(prog="tide-worktrees", description="Okno Tide na widoku Worktrees")
    parser.add_argument("--data")
    parser.add_argument("--grab")
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    parser.add_argument("--select", action="append", default=[])
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    out = ["--view", "worktrees"]
    if args.data:
        out += ["--data", args.data]
    if args.grab:
        out += ["--grab", args.grab]
    for path in args.select:
        out += ["--preview", "pick=" + path]
    if args.confirm:
        out += ["--preview", "confirm=1"]
    if args.width and args.height:
        out += ["--width", str(args.width), "--height", str(args.height)]
    return out


def main(argv=None) -> int:
    args = translate(sys.argv[1:] if argv is None else argv)
    target = tide_window()
    if not target:
        print("tide-worktrees: nie ma okna Tide (make install-tide-window)", file=sys.stderr)
        return 1
    os.execv(sys.executable, [sys.executable, "-s", target, *args])
    return 1


if __name__ == "__main__":
    sys.exit(main())
