"""Punkt wejścia tide-daemon.

Użycie: python3 tide-daemon.py [--print] [--claude-dir DIR] [--codex-dir DIR]
                               [--no-processes] [--now CZAS]

--print         jednorazowy odczyt: wypisuje JSON sesji i kończy, bez D-Bus,
--no-processes  nie czyta /proc (stan bez wiedzy o procesach, bez przechodzenia do okien),
--now           zamrożony zegar (ISO 8601) — dla testów i diagnostyki.
"""
import argparse
import json
import os
import signal
import sys
import time

from .agents import AgentScanner
from .agents.timeutil import parse_ts


def default_claude_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def default_codex_dir() -> str:
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="tide-daemon", description="Sesje agentów AI przez D-Bus")
    parser.add_argument("--claude-dir", default=default_claude_dir())
    parser.add_argument("--codex-dir", default=default_codex_dir())
    parser.add_argument("--no-processes", action="store_true")
    parser.add_argument("--now", help="zamrożony zegar, np. 2026-10-26T12:00:00Z")
    parser.add_argument("--print", dest="print_once", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    clock = time.time
    if args.now:
        frozen = parse_ts(args.now)
        if not frozen:
            print(f"tide-daemon: niepoprawny czas --now: {args.now}", file=sys.stderr)
            return 2
        clock = lambda: frozen  # noqa: E731

    scanner = AgentScanner(args.claude_dir, args.codex_dir)

    if args.print_once:
        from . import processes
        now = clock()
        scanner.discover(now)
        scanner.update()
        procs = None if args.no_processes else processes.probe(log_roots=(scanner.codex_sessions,))
        print(json.dumps(scanner.sessions(now, procs), ensure_ascii=False, indent=2))
        return 0

    from PyQt6.QtCore import QCoreApplication, QTimer
    from . import dbus_service
    from .monitor import AgentMonitor

    app = QCoreApplication(sys.argv[:1])
    app.setApplicationName("tide-daemon")
    monitor = AgentMonitor(scanner, use_processes=not args.no_processes, clock=clock)
    monitor.start()
    try:
        holder = dbus_service.register(monitor)  # noqa: F841 — musi żyć do końca
    except RuntimeError as exc:
        print(f"tide-daemon: {exc}", file=sys.stderr, flush=True)
        return 1

    # Python obsługuje sygnały tylko wtedy, gdy dostaje sterowanie — budzik co 500 ms.
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer(interval=500)
    wake.timeout.connect(lambda: None)
    wake.start()

    print(f"tide-daemon: działa, {len(scanner.logs)} plików sesji z ostatniej doby", flush=True)
    return app.exec()
