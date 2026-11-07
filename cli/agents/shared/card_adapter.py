#!/usr/bin/env python3
"""Adapter otwarcia karty: wynik i kod odmowy pochodzą z publicznego CLI."""
import argparse
import json
import re
import subprocess
import sys


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    parser = Parser(description="Otwórz opiekuna K-N albo przejmij próbę PT-N w Konsoli.")
    parser.add_argument("card")
    parser.add_argument("ticket", nargs="?")
    try:
        args = parser.parse_args(argv)
        if not re.fullmatch(r"K-[1-9][0-9]*", args.card) or \
                (args.ticket is not None and not re.fullmatch(r"PT-[1-9][0-9]*", args.ticket)):
            raise ValueError("Podaj K-N i opcjonalnie PT-N.")
        command = ["tide", "card", "open", args.card]
        if args.ticket:
            command += ["--ticket", args.ticket]
        return subprocess.run([*command, "--json"], check=False).returncode
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": "bad-request", "message": str(exc)}, ensure_ascii=False))
        return 2
    except OSError:
        print(json.dumps({"ok": False, "error": "unavailable", "message": "Nie można uruchomić CLI tide."}))
        return 3


if __name__ == "__main__":
    sys.exit(main())
