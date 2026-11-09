#!/usr/bin/python3
"""PT-176: pojedyncza próba z hosta, wyłącznie po zgodzie na outside.

Nie zatwierdza profilu, nie buduje pudełka ani nie tworzy karty. Każde wywołanie
wykonuje jeden tide box run; install uruchamia make install wyłącznie tam.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def long_command():
    return ('set -eu\n# ' + 'PT-176 zachowanie długiego argumentu; ' * 12 + '\n'
            'tmp=$(mktemp -d /home/agent/pt176-XXXXXX)\n'
            'trap \'rm -rf -- "$tmp"\' EXIT\n'
            'printf "PT-176-find-ok\\n" > "$tmp/probe.txt"\n'
            'find "$tmp" -type f -name probe.txt -exec cat {} \\;\n')


def box_command(command, card=""):
    argv = [sys.executable, str(ROOT / "daemon/tide.py"), "box", "run", "plasma-tide",
            "--worktree", str(ROOT)]
    if card:
        argv += ["--card", card]
    return argv + ["--", *command]


def install_trial(host_file):
    # Brak rzeczywistego pliku na hoście nie daje dowodu niezmienności.
    path = Path(host_file).expanduser().resolve(strict=True)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = subprocess.run(box_command(["make", "install"]), cwd=ROOT)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    print(json.dumps({"host_file": str(path), "before": before, "after": after,
                      "unchanged": before == after, "install_exit": result.returncode}), flush=True)
    return result.returncode or int(before != after)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="scenario", required=True)
    sub.add_parser("long-command")
    install = sub.add_parser("install")
    install.add_argument("--host-file", required=True)
    args = parser.parse_args()
    if args.scenario == "install":
        return install_trial(args.host_file)
    command = long_command()
    print(json.dumps({"argument_length": len(command), "find-exec": True}), flush=True)
    result = subprocess.run(box_command(["sh", "-c", command]), cwd=ROOT,
                            capture_output=True, text=True)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        return result.returncode
    return 0 if result.stdout == "PT-176-find-ok\n" else 1


if __name__ == "__main__":
    sys.exit(main())
