#!/usr/bin/python3
"""Zgodność z dawnym oknem „Tide Sesje”: to samo wywołanie otwiera okno Tide na widoku Sesje.

Instalowany pod starą ścieżką ($XDG_DATA_HOME/tide/sessions-window/tide-sessions.py), żeby działały
wszystkie miejsca, które ją znają (tide-daemon sprzed aktualizacji, przypięte skróty, skrypty).
Opcje jak dawniej: --select ID, --data PLIK, --grab PNG, --ticket KLUCZ, --result ID, --width/--height.
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
    parser = argparse.ArgumentParser(prog="tide-sessions", description="Okno Tide na widoku Sesje")
    parser.add_argument("--select", default="")
    parser.add_argument("--data")
    parser.add_argument("--grab")
    parser.add_argument("--ticket", default="")
    parser.add_argument("--result", default="")
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    args = parser.parse_args(argv)
    out = ["--view", "sessions"]
    if args.select:
        out += ["--select", args.select]
    if args.data:
        out += ["--data", args.data]
    if args.grab:
        out += ["--grab", args.grab]
    if args.ticket:
        out += ["--preview", "ticket=" + args.ticket]
    if args.result:
        out += ["--preview", "result=" + args.result]
    if args.width and args.height:
        out += ["--width", str(args.width), "--height", str(args.height)]
    return out


def main(argv=None) -> int:
    args = translate(sys.argv[1:] if argv is None else argv)
    target = tide_window()
    if not target:
        print("tide-sessions: nie ma okna Tide (make install-tide-window)", file=sys.stderr)
        return 1
    os.execv(sys.executable, [sys.executable, "-s", target, *args])
    return 1


if __name__ == "__main__":
    sys.exit(main())
