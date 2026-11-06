#!/usr/bin/env python3
"""Czerwona lista w projekcie Tablicy, bez kopiowania skryptów Tide do jego repozytorium."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "daemon"))

from tide_daemon.box import cli
from tide_daemon.box.builder import BuildError
from tide_daemon.box.command import project_repo


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("--worktree", default="")
    args, probe_args = parser.parse_known_args(argv)
    if probe_args[:1] == ["--"]:
        probe_args = probe_args[1:]
    try:
        # CLI tworzy izolowany worktree, gdy dostanie główny katalog projektu.
        worktree = args.worktree or project_repo(args.slug)
        script = (ROOT / "scripts/box/check.sh").read_text()
    except (BuildError, OSError) as exc:
        print(f"box-check: {exc}", file=sys.stderr)
        return 1
    # bash wykonuje treść dopiero w pudełku. Argumenty prób pozostają osobnymi argv.
    return cli.main(["run", args.slug, "--worktree", worktree, "--",
                     "bash", "-c", script, "tide-box-check", *probe_args])


if __name__ == "__main__":
    sys.exit(main())
