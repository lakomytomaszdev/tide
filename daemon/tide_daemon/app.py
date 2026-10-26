"""Punkt wejścia tide-daemon.

Użycie: python3 tide-daemon.py [--print | --print-worktrees] [--claude-dir DIR] [--codex-dir DIR]
                               [--workspace DIR] [--worktree-depth N] [--no-processes]
                               [--no-notifications] [--no-krunner] [--now CZAS]

--print            jednorazowy odczyt: wypisuje JSON sesji i kończy, bez D-Bus,
--print-worktrees  jednorazowy odczyt worktrees (z rozmiarami) jako JSON, bez D-Bus,
--workspace        katalog projektów z repozytoriami (domyślnie $TIDE_WORKSPACE albo ~/DevWorkspace),
--worktree-depth   jak głęboko pod katalogiem projektów szukać repozytoriów (domyślnie 3),
--no-processes     nie czyta /proc (stan bez wiedzy o procesach, bez przechodzenia do okien),
--no-notifications bez powiadomień „Agent czeka”,
--no-krunner       bez wtyczki KRunnera (org.tide.Daemon.KRunner),
--now              zamrożony zegar (ISO 8601) — dla testów i diagnostyki.
"""
import argparse
import json
import os
import signal
import sys
import time

from .agents import AgentScanner
from .agents.timeutil import parse_ts
from . import worktrees


def default_claude_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def default_codex_dir() -> str:
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def default_workspace() -> str:
    return os.environ.get("TIDE_WORKSPACE") or os.path.expanduser("~/DevWorkspace")


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="tide-daemon", description="Sesje agentów AI przez D-Bus")
    parser.add_argument("--claude-dir", default=default_claude_dir())
    parser.add_argument("--codex-dir", default=default_codex_dir())
    parser.add_argument("--no-processes", action="store_true")
    parser.add_argument("--no-notifications", action="store_true")
    parser.add_argument("--no-krunner", action="store_true")
    parser.add_argument("--now", help="zamrożony zegar, np. 2026-10-26T12:00:00Z")
    parser.add_argument("--workspace", default=default_workspace())
    parser.add_argument("--worktree-depth", type=int, default=worktrees.MAX_DEPTH)
    parser.add_argument("--print", dest="print_once", action="store_true")
    parser.add_argument("--print-worktrees", action="store_true")
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

    if args.print_worktrees:
        from . import processes
        now = clock()
        scanner.discover(now)
        scanner.update()
        procs = None if args.no_processes else processes.probe(log_roots=(scanner.codex_sessions,))
        cwds = worktrees.agent_cwds(scanner.sessions(now, procs), procs)
        index = worktrees.SessionIndex(args.claude_dir, args.codex_dir)
        print(json.dumps(worktrees.scan(args.workspace, cwds, args.worktree_depth, now=now, sessions=index),
                         ensure_ascii=False, indent=2))
        return 0

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
    from .worktree_monitor import WorktreeMonitor

    app = QCoreApplication(sys.argv[:1])
    app.setApplicationName("tide-daemon")
    monitor = AgentMonitor(scanner, use_processes=not args.no_processes, clock=clock)
    monitor.start()
    # Osobny, mały indeks sesji dla worktrees (KRunner ma własny, pełniejszy — krunner/index.py).
    worktree_sessions = worktrees.SessionIndex(args.claude_dir, args.codex_dir)
    trees = WorktreeMonitor(worktrees.WorktreeScanner(args.workspace, args.worktree_depth, clock=clock,
                                                      sessions=worktree_sessions),
                            lambda: worktrees.agent_cwds(monitor.sessions, monitor.procs),
                            worktrees.Launchers(), refresh_agents=monitor.recompute)
    monitor.changed.connect(trees.publish)  # nowy albo zakończony agent zmienia flagę „aktywny agent”
    # KRunner przed główną nazwą: gdy org.tide.Daemon jest już na magistrali, wtyczka też.
    krunner = None if args.no_krunner else start_krunner(app, args, clock, monitor, trees)  # noqa: F841
    try:
        holder = dbus_service.register(monitor, trees)  # noqa: F841 — musi żyć do końca
    except RuntimeError as exc:
        print(f"tide-daemon: {exc}", file=sys.stderr, flush=True)
        return 1

    # Python obsługuje sygnały tylko wtedy, gdy dostaje sterowanie — budzik co 500 ms.
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer(interval=500)
    wake.timeout.connect(lambda: None)
    wake.start()
    trees.start()  # pierwsze odczytanie repozytoriów już w tle, po rejestracji na magistrali

    if not args.no_notifications:
        from .notifications import Notifier
        notifier = Notifier(monitor.activate)
        notifier.update(monitor.sessions)  # stan wyjściowy: czekające już wcześniej nie dostają powiadomień
        monitor.changed.connect(lambda: notifier.update(monitor.sessions))

    print(f"tide-daemon: działa, {len(scanner.logs)} plików sesji z ostatniej doby", flush=True)
    code = app.exec()
    trees.stop()
    return code


def start_krunner(app, args, clock, monitor, trees):
    """Wtyczka KRunnera na osobnym połączeniu D-Bus (krunner/service.py). Jej brak nie zatrzymuje usługi."""
    from .krunner import service
    if not service.available():
        print("tide-daemon: brak python3-dbus albo python3-gi — wtyczka KRunnera wyłączona", flush=True)
        return None
    from .krunner.index import SessionIndex
    from .krunner.runner import KRunnerCore, LazyIndex

    index = SessionIndex(args.claude_dir, args.codex_dir, clock=clock)
    core = KRunnerCore(index, lambda: trees.scanner.snapshot(), trees.launchers,
                       live=lambda: monitor.sessions, activate=monitor.activate, clock=clock)
    try:
        return service.register(core, lambda post: LazyIndex(index, post), app)
    except Exception as exc:  # np. nazwa zajęta — reszta usługi działa dalej
        print(f"tide-daemon: wtyczka KRunnera niedostępna: {exc}", flush=True)
        return None
