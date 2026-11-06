#!/usr/bin/env python3
"""Wiersz poleceń Tide: `python3 daemon/tide.py box …` (pudełko projektu, docs/tide-daemon.md)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != "box":
        print("użycie: tide box base|build|status|reset|stop|start|destroy|approve|login|run …", file=sys.stderr)
        return 2
    from tide_daemon.box import cli
    return cli.main(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
