"""Punkt wejścia tide-daemon.

Użycie: python3 tide-daemon.py [--print | --print-worktrees | --print-limits] [--claude-dir DIR]
                               [--codex-dir DIR] [--workspace DIR] [--worktree-depth N] [--no-processes]
                               [--no-notifications] [--no-krunner] [--no-tab-marks] [--now CZAS]
                               [--idle-after SEKUNDY | --no-idle] [--limits-poll SEKUNDY | --no-limits]
                               [--report [--report-days N]] [--report-dir DIR] [--report-at GG:MM]
                               [--no-report] [--report-delay SEKUNDY] [--report-summary claude|rules]
                               [--no-tasks]
                               [--print-coordination] [--coordination-days N] [--no-coordination]
                               [--no-youtrack] [--print-board] [--board-dir DIR] [--no-board]
                               [--print-docker | --no-docker]

--print            jednorazowy odczyt: wypisuje JSON sesji i kończy, bez D-Bus,
--print-worktrees  jednorazowy odczyt worktrees (z rozmiarami) jako JSON, bez D-Bus,
--print-limits     prognoza limitów z historii i jednego GET /status jako JSON (niczego nie zapisuje),
--workspace        katalog projektów z repozytoriami (domyślnie $TIDE_WORKSPACE albo ~/DevWorkspace),
--worktree-depth   jak głęboko pod katalogiem projektów szukać repozytoriów (domyślnie 3),
--no-processes     nie czyta /proc (stan bez wiedzy o procesach, bez przechodzenia do okien),
--no-notifications bez powiadomień „Agent czeka”,
--no-krunner       bez wtyczki KRunnera (org.tide.Daemon.KRunner),
--no-tab-marks     bez znacznika stanu na kartach Konsole („● projekt · gałąź” i kolor karty),
--now              zamrożony zegar (ISO 8601) — dla testów i diagnostyki,
--idle-after       próg bezczynności w sekundach dla /Idle (domyślnie wallpaper.dozeAfterS z palety),
--no-idle          bez obiektu /Idle (bezczynność z kompozytora Wayland),
--limits-poll      co ile sekund pytać QuotaApp o /status (domyślnie limits.pollS z palety),
--no-limits        bez obiektu /Limits (historia, prognoza i powiadomienia Limitów AI),
--report           jednorazowy raport poranny za ostatnie --report-days dób (domyślnie 1) do --report-dir;
                   bez D-Bus, bez powiadomienia i bez pliku stanu — wypisuje ścieżkę index.html,
--report-dir       katalog raportów (domyślnie $XDG_DATA_HOME/tide/reports),
--report-at        godzina raportu porannego (domyślnie „at” z $XDG_CONFIG_HOME/tide/report.json albo 07:00),
--no-report        bez raportu porannego z terminu (Generate() z D-Bus działa dalej),
--report-delay     ile sekund po starcie usługi sprawdzić, czy raport jest należny (domyślnie 120),
--report-summary   opis „Na daily”: claude (model przez `claude -p`) albo rules (bez modelu); domyślnie
                   „summary” z $XDG_CONFIG_HOME/tide/report.json albo claude,
--no-tasks         bez obiektu /Tasks (nowe zadanie dla agenta: worktree i karta Konsoli),
--print-coordination  jednorazowo: koordynatorzy i ich tablice jako JSON (jak dane okna Tide Sesje), bez D-Bus,
--coordination-days   z ilu dni szukać koordynatorów (domyślnie 7),
--no-coordination  bez obiektu /Coordination (tablica koordynatorów, tickety, podagenci),
--no-youtrack      tablica bez YouTracka (żadnych zapytań sieciowych),
--print-board      jednorazowo: migawka tablicy pracy jako JSON (tylko odczyt, niczego nie zakłada), bez D-Bus,
--board-dir        repozytorium tablicy pracy (domyślnie $TIDE_BOARD_DIR albo ~/DevWorkspace/tide-board),
--no-board         bez obiektu /Board (tablica pracy: karty, projekty, tagi).
--print-docker     jednorazowo: migawka kontenerów i miejsce na dysku jako JSON (tylko odczyt Engine API), bez D-Bus,
--no-docker        bez obiektu /Docker (kontenery, akcje, porządki; socket z $DOCKER_HOST albo /var/run/docker.sock).
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
    parser.add_argument("--no-tab-marks", action="store_true")
    parser.add_argument("--now", help="zamrożony zegar, np. 2026-10-26T12:00:00Z")
    parser.add_argument("--workspace", default=default_workspace())
    parser.add_argument("--worktree-depth", type=int, default=worktrees.MAX_DEPTH)
    parser.add_argument("--idle-after", type=float, help="próg bezczynności w sekundach")
    parser.add_argument("--no-idle", action="store_true")
    parser.add_argument("--limits-poll", type=float, help="odstęp odczytów /status w sekundach")
    parser.add_argument("--no-limits", action="store_true")
    parser.add_argument("--print-limits", action="store_true")
    parser.add_argument("--no-tasks", action="store_true")
    parser.add_argument("--print-coordination", action="store_true")
    parser.add_argument("--coordination-days", type=float, default=7.0)
    parser.add_argument("--no-coordination", action="store_true")
    parser.add_argument("--no-youtrack", action="store_true")
    parser.add_argument("--print-board", action="store_true")
    parser.add_argument("--board-dir")
    parser.add_argument("--no-board", action="store_true")
    parser.add_argument("--print-docker", action="store_true")
    parser.add_argument("--no-docker", action="store_true")
    parser.add_argument("--print", dest="print_once", action="store_true")
    parser.add_argument("--print-worktrees", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--report-days", type=float, default=1.0)
    parser.add_argument("--report-dir")
    parser.add_argument("--report-at")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--report-delay", type=float)
    parser.add_argument("--report-summary", choices=("claude", "rules"))
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

    from .codex_app import CodexApp
    # Codex Desktop: przejście do sesji otwiera jej wątek w aplikacji (codex://threads/<id>).
    scanner = AgentScanner(args.claude_dir, args.codex_dir, codex_app=CodexApp())

    from .reports import schedule as report_schedule
    try:
        report_settings, warnings = report_schedule.load_settings(
            report_schedule.config_path(), args.report_at, False if args.no_report else None)
    except ValueError as exc:
        print(f"tide-daemon: --report-at: {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"tide-daemon: {warning}", file=sys.stderr, flush=True)

    if args.report:
        return print_report(args, clock, scanner, report_settings)

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

    if args.print_docker:
        print(json.dumps(docker_once(args, clock), ensure_ascii=False, indent=2))
        return 0

    if args.print_limits:
        print(json.dumps(limits_once(clock), ensure_ascii=False, indent=2))
        return 0

    if args.print_coordination:
        from . import processes
        now = clock()
        scanner.discover(now)
        scanner.update()
        procs = None if args.no_processes else processes.probe(log_roots=(scanner.codex_sessions,))
        print(json.dumps(coordination_once(args, clock, scanner.sessions(now, procs), report_settings),
                         ensure_ascii=False, indent=2))
        return 0

    if args.print_board:
        print(json.dumps(board_store(args).snapshot(repos=worktrees.find_repositories(args.workspace,
                                                                                      args.worktree_depth)),
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
    launchers = worktrees.Launchers()
    trees = WorktreeMonitor(worktrees.WorktreeScanner(args.workspace, args.worktree_depth, clock=clock,
                                                      sessions=worktree_sessions),
                            lambda: worktrees.agent_cwds(monitor.sessions, monitor.procs),
                            launchers, refresh_agents=monitor.recompute)
    monitor.changed.connect(trees.publish)  # nowy albo zakończony agent zmienia flagę „aktywny agent”
    # KRunner przed główną nazwą: gdy org.tide.Daemon jest już na magistrali, wtyczka też.
    coordination = None if args.no_coordination else start_coordination(app, args, clock, monitor, trees,
                                                                         launchers, report_settings)
    # Raport przed KRunnerem: „raport” w KRunnerze otwiera go przez ReportMonitor (oznacza przeczytany).
    report = start_report(app, args, clock, report_settings, monitor, trees)
    krunner = None if args.no_krunner else start_krunner(app, args, clock, monitor, trees,  # noqa: F841
                                                         coordination, report)
    idle = None if args.no_idle else start_idle(app, args)
    limits = None if args.no_limits else start_limits(app, args, clock)
    tasks = None if args.no_tasks else start_tasks(app, args, launchers, trees, monitor)
    board = None if args.no_board else start_board(app, args, trees)
    docker = None if args.no_docker else start_docker(app, args, clock, launchers)
    annotators = [a for a in (tasks.annotate if tasks is not None else None,
                              coordination.annotate if coordination is not None else None) if a]
    if annotators:
        def annotate(sessions):
            for annotator in annotators:
                annotator(sessions)
        monitor.annotate = annotate  # znacznik „nowe” (/Tasks) i postęp koordynatora (/Coordination)
    try:
        holder = dbus_service.register(monitor, trees, idle=idle, reports=report,  # noqa: F841 — musi żyć do końca
                                       limits=limits, tasks=tasks, coordination=coordination, board=board,
                                       docker=docker)
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
    if idle is not None:
        idle.start()  # połączenie z kompozytorem po rejestracji — /Idle odpowiada od razu (Idle = false)
    report.start()  # pierwsze sprawdzenie terminu raportu po --report-delay (logowanie)

    if not args.no_notifications:
        from .notifications import Notifier
        notifier = Notifier(monitor.activate)
        notifier.update(monitor.sessions)  # stan wyjściowy: czekające już wcześniej nie dostają powiadomień
        monitor.changed.connect(lambda: notifier.update(monitor.sessions))
        if limits is not None:
            from .notifications import LimitsNotifier
            limits_notifier = LimitsNotifier(parent=app)
            limits.alert.connect(limits_notifier.send)
    if limits is not None:
        limits.start()  # pierwszy odczyt /status po rejestracji i po podłączeniu powiadomień
    if board is not None:
        board.start()  # zmiany z czasu, gdy usługa nie działała, i obserwacja katalogu tablicy
    if coordination is not None:
        # Żywy raport i notki „Skończone dziś”: każda nowa wersja tablic koordynacji.
        coordination.changed.connect(lambda: report.coordination_changed(coordination.boards))
        coordination.start()  # pierwszy odczyt dzienników koordynatorów w tle, chwilę po starcie
    if docker is not None:
        docker.start()  # migawka i strumień zdarzeń w tle; brak Dockera to stan, nie błąd usługi

    # Stan agentów według katalogu dla promptu powłoki (cwdstate.py) — plik, nie D-Bus, bo prompt
    # czyta go przy każdym znaku zachęty.
    from .cwdstate import CwdStateFile
    cwd_state = CwdStateFile()

    def write_cwd_state() -> None:
        try:
            cwd_state.update(monitor.sessions)
        except Exception as exc:  # plik dla promptu nie może położyć usługi
            print(f"tide-daemon: nie udało się zapisać stanu agentów dla promptu: {exc}", flush=True)

    write_cwd_state()
    monitor.changed.connect(write_cwd_state)

    marker = None
    if not args.no_tab_marks and not args.no_processes:
        from . import tabmarks
        from .konsole import KonsoleTabs
        marker = tabmarks.TabMarker(KonsoleTabs(), tabmarks.load_state_colors(), tabmarks.default_store())

        def mark_tabs() -> None:
            try:
                marker.update(tabmarks.tab_targets(monitor.sessions, monitor.scanner.bound_pids, monitor.procs))
            except Exception as exc:  # znacznik na karcie nie może położyć usługi
                print(f"tide-daemon: nie udało się oznaczyć kart Konsole: {exc}", flush=True)

        mark_tabs()  # także sprząta karty oznaczone przed restartem usługi
        monitor.changed.connect(mark_tabs)

    print(f"tide-daemon: działa, {len(scanner.logs)} plików sesji z ostatniej doby", flush=True)
    code = app.exec()
    trees.stop()
    report.stop()
    if tasks is not None:
        tasks.stop()
    if idle is not None:
        idle.stop()
    if limits is not None:
        limits.stop()
    if coordination is not None:
        coordination.stop()
    if board is not None:
        board.stop()
    if docker is not None:
        docker.stop()
    if marker is not None:
        marker.clear()  # karty wracają do swoich tytułów i kolorów, gdy usługa się kończy
    cwd_state.clear()  # prompt nie pokazuje stanu agentów, których nikt już nie śledzi
    return code


def print_report(args, clock, scanner, settings) -> int:
    """Jednorazowy raport (--report): bez D-Bus, bez powiadomienia, bez pliku stanu."""
    from . import processes, reports
    from .report_monitor import report_root
    from .reports import schedule
    from .tabmarks import palette_candidates
    now = clock()
    scanner.discover(now)
    scanner.update()
    procs = None if args.no_processes else processes.probe(log_roots=(scanner.codex_sessions,))
    days = max(0.01, min(args.report_days, schedule.MAX_PERIOD / 86400))
    try:
        index, report = reports.generate(
            report_root(args.report_dir), schedule.report_day(now, settings).isoformat(), now - days * 86400, now,
            palette=reports.load_palette(palette_candidates()), workspace=args.workspace,
            claude_root=args.claude_dir, codex_root=args.codex_dir, live=scanner.sessions(now, procs),
            max_depth=args.worktree_depth, coordination_cache=coordination_cache_file(args),
            summary=args.report_summary or settings.summary, summary_model=settings.summary_model,
            tracker=report_tracker(args, clock))
    except (OSError, RuntimeError) as exc:
        print(f"tide-daemon: raport: {exc}", file=sys.stderr)
        return 1
    print(index)
    print(f"{len(report.repos)} projektów z ruchem, {len(report.quiet)} bez ruchu", file=sys.stderr)
    return 0


def start_report(app, args, clock, settings, monitor, trees):
    """Raport poranny (report_monitor.py): termin, D-Bus /Reports, powiadomienie z „Otwórz”."""
    from .report_monitor import ReportMonitor, report_root
    from .reports import schedule
    from .tabmarks import palette_candidates

    from .reports import notes as report_notes

    notifier = note_notifier = None
    holder = {}
    if not args.no_notifications:
        from .notifications import NoteNotifier, ReportNotifier
        # „Otwórz” w powiadomieniu idzie przez ReportMonitor, żeby oznaczyć raport jako przeczytany.
        notifier = ReportNotifier(lambda path: holder["monitor"].open_path(path), parent=app)
        # Notka po tickecie: „Otwórz” → okno Tide, widok Raporty, doba/projekt.
        note_notifier = NoteNotifier(lambda item: trees.launchers.show_view("reports", item), parent=app)

    def worktree_items():
        # Gotowa lista z obszaru Worktrees (z rozmiarami); przed pierwszym odczytem raport przejrzy repozytoria sam.
        items = trees.scanner.snapshot(worktrees.agent_cwds(monitor.sessions, monitor.procs))
        return items or None

    kwargs = {}
    if args.report_delay is not None:
        kwargs["login_delay"] = max(0.0, args.report_delay)
    holder["monitor"] = ReportMonitor(
        settings, root=report_root(args.report_dir), state_file=schedule.state_path(),
        palette_paths=palette_candidates(), workspace=args.workspace, claude_root=args.claude_dir,
        codex_root=args.codex_dir, max_depth=args.worktree_depth, sessions=lambda: monitor.sessions,
        worktrees=worktree_items, notifier=notifier, clock=clock, coordination_cache=coordination_cache_file(args),
        opener=trees.launchers.open_report, summary=args.report_summary or settings.summary,
        tracker=report_tracker(args, clock), notes_file=report_notes.state_path(), note_notifier=note_notifier,
        parent=app, **kwargs)
    # Żywy raport: zmiany w /Agents („czeka”, agent skończył pracę); tablice koordynacji podłącza
    # start_coordination niżej w main (notki po tickecie, zmiany ticketów).
    monitor.changed.connect(holder["monitor"].sessions_changed)
    return holder["monitor"]


def report_tracker(args, clock):
    """YouTrack dla „Na daily” (opisy ticketów z planów w repozytoriach, tylko GET); --no-youtrack — bez."""
    if args.no_youtrack:
        return None
    from .coordination.youtrack import YouTrackCache
    return YouTrackCache(clock=clock)


def coordination_cache_file(args) -> str | None:
    """Plik tablicy koordynacji dla raportu („Plany i tickety”); bez obszaru — brak pliku."""
    if args.no_coordination:
        return None
    from .coordination import actions
    return actions.cache_path()


def start_tasks(app, args, launchers, trees, monitor):
    """Obszar /Tasks: nowe zadanie dla agenta (tasks/, task_monitor.py)."""
    from .task_monitor import TaskMonitor
    from .tasks import Planner
    from .tasks.core import CLAIM_TIMEOUT
    from .tasks.terminal import KonsoleWindows, TaskTerminal

    probe = activate = None
    if not args.no_processes:
        from . import processes, windows
        probe, activate = processes.probe, windows.activate
    terminal = TaskTerminal(launchers, KonsoleWindows(), probe=probe, activate=activate)
    planner = Planner(args.workspace, args.worktree_depth, terminal_available=terminal.available)
    try:
        timeout = float(os.environ.get("TIDE_TASK_CLAIM_TIMEOUT") or CLAIM_TIMEOUT)
    except ValueError:
        timeout = CLAIM_TIMEOUT
    tasks = TaskMonitor(planner, terminal, launchers, trees, claim_timeout=timeout,
                        sessions_source=lambda: monitor.sessions, parent=app)
    return tasks


def coordination_scanner(args, clock):
    """Skaner koordynatorów z YouTrackiem (tylko odczyt) i nazwami wątków Codexa."""
    from .coordination import CoordinationScanner
    from .coordination.youtrack import YouTrackCache
    from .reports.sessions import codex_titles
    cache = {"ts": 0.0, "titles": {}}

    def titles():
        if clock() - cache["ts"] > 300:  # nazwy wątków zmieniają się rzadko
            cache["titles"], cache["ts"] = codex_titles(args.codex_dir), clock()
        return cache["titles"]

    tracker = None if args.no_youtrack else YouTrackCache(clock=clock)
    return CoordinationScanner(args.claude_dir, args.codex_dir, days=max(0.1, args.coordination_days), clock=clock,
                               tracker=tracker, titles=titles)


def card_describer(args, clock, settings):
    """Opisy kart tablicy (coordination/describe.py): tryb i model z konfiguracji raportu porannego
    („summary”, „summaryModel”; --report-summary ma pierwszeństwo), pamięć w $XDG_CACHE_HOME/tide."""
    from .coordination import describe
    mode = args.report_summary or (settings.summary if settings else "rules")
    model = settings.summary_model if settings else describe.summarize.DEFAULT_MODEL
    return describe.Describer(mode=mode, model=model, cache_file=describe.cache_path(), clock=clock)


def coordination_once(args, clock, live, settings=None) -> dict:
    """--print-coordination: dane okna Tide Sesje (koordynatorzy, tablice, sesje) — tylko odczyt;
    opisy kart z pamięci podręcznej albo reguł (bez wywołania modelu)."""
    from .coordination import board as board_mod
    scanner = coordination_scanner(args, clock)
    boards = scanner.refresh(live)
    card_describer(args, clock, settings).decorate(boards)
    return {"coordinators": [board_mod.summary(b) for b in boards], "boards": {b["id"]: b for b in boards},
            "sessions": live}


def start_coordination(app, args, clock, monitor, trees, launchers, settings=None):
    """Obszar /Coordination: koordynatorzy, tickety i podagenci (coordination/, coordination_monitor.py)."""
    from .coordination import actions
    from .coordination_monitor import CoordinationMonitor
    coordination = CoordinationMonitor(coordination_scanner(args, clock), sessions_source=lambda: monitor.sessions,
                                       launchers=launchers, activate=monitor.activate,
                                       worktrees_source=lambda: trees.scanner.snapshot(),
                                       cache_file=actions.cache_path(), clock=clock,
                                       describer=card_describer(args, clock, settings), parent=app)
    monitor.changed.connect(coordination.sessions_changed)
    coordination.changed.connect(monitor.recompute)  # postęp koordynatora przy jego sesji w /Agents
    return coordination


def board_store(args):
    """Repozytorium tablicy pracy (board/store.py) z kolorami tagów z palety."""
    from .board import default_board_dir
    from .board.store import BoardStore
    from .tabmarks import palette_candidates
    return BoardStore(args.board_dir or default_board_dir(), colors=pastel_colors(palette_candidates()))


def pastel_colors(paths) -> dict | None:
    """Pastele z palety (nazwa → #rrggbb) — kolory tagów; bez palety None (same nazwy z board/model.py)."""
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                pastel = json.load(fh).get("pastel")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(pastel, dict) and pastel:
            return {str(k): str(v) for k, v in pastel.items()}
    return None


def start_board(app, args, trees):
    """Obszar /Board: tablica pracy w lokalnym repozytorium git (board/, board_monitor.py).
    Repozytoria do dodania jako projekty bierzemy z gotowej listy obszaru Worktrees."""
    from .board_monitor import BoardMonitor

    def repos():
        return sorted({item["repoPath"] for item in trees.scanner.snapshot()
                       if item.get("isMain") and not item.get("missing") and item.get("repoPath")})

    board = BoardMonitor(board_store(args), repos_source=repos, launchers=trees.launchers, parent=app)
    trees.changed.connect(board.publish)  # nowe repozytorium w katalogu projektów
    return board
def docker_once(args, clock) -> dict:
    """--print-docker: migawka kontenerów i miejsce na dysku — wyłącznie zapytania GET do Engine API."""
    from .docker import DockerEngine
    engine = DockerEngine(workspace=args.workspace, clock=clock)
    snapshot = engine.refresh()
    disk = engine.disk() if snapshot.get("available") else {"ok": False, "error": snapshot.get("error", "")}
    return {"snapshot": snapshot, "disk": disk}


def start_docker(app, args, clock, launchers):
    """Obszar /Docker: kontenery przez Engine API (docker/, docker_monitor.py); logi i powłoka w karcie
    Konsoli tym samym mechanizmem co „Nowe zadanie”."""
    from .docker import DockerEngine
    from .docker_monitor import DockerMonitor
    from .tasks.core import TaskFiles, runtime_dir
    from .tasks.terminal import KonsoleWindows, TaskTerminal
    probe = activate = None
    if not args.no_processes:
        from . import processes, windows
        probe, activate = processes.probe, windows.activate
    terminal = TaskTerminal(launchers, KonsoleWindows(), probe=probe, activate=activate)
    return DockerMonitor(DockerEngine(workspace=args.workspace, clock=clock), terminal=terminal,
                         files=TaskFiles(runtime_dir()), launchers=launchers, parent=app)


def start_idle(app, args):
    """Bezczynność użytkownika z kompozytora (idle.py) dla tapety z kotem; brak kompozytora nie zatrzymuje usługi."""
    from .idle import IdleMonitor, doze_after_s
    from .tabmarks import palette_candidates
    seconds = args.idle_after if args.idle_after and args.idle_after > 0 else doze_after_s(palette_candidates())
    return IdleMonitor(int(round(seconds * 1000)), parent=app)


def start_limits(app, args, clock):
    """Historia i prognoza Limitów AI (limits/, limits_monitor.py); progi z palety (sekcja „limits”)."""
    from .limits import config as limits_config
    from .limits.tracker import LimitsTracker
    from .limits_monitor import LimitsMonitor
    from .tabmarks import palette_candidates
    cfg = limits_config.load(palette_candidates())
    poll_ms = int(round(args.limits_poll * 1000)) if args.limits_poll and args.limits_poll > 0 else None
    return LimitsMonitor(LimitsTracker(cfg, clock=clock), poll_ms=poll_ms, parent=app)


def limits_once(clock) -> dict:
    """--print-limits: historia z dysku plus jeden GET /status, tylko w pamięci (bez zapisu i powiadomień)."""
    from .limits import backend, readings
    from .limits import config as limits_config
    from .limits.tracker import LimitsTracker
    from .tabmarks import palette_candidates
    tracker = LimitsTracker(limits_config.load(palette_candidates()), clock=clock)
    try:
        for reading in readings.from_status(backend.fetch_status()):
            tracker.history.add(reading)
        tracker.available = True
    except backend.BackendError as exc:
        tracker.failed(str(exc))
    return tracker.snapshot()


def start_krunner(app, args, clock, monitor, trees, coordination=None, report=None):
    """Wtyczka KRunnera na osobnym połączeniu D-Bus (krunner/service.py). Jej brak nie zatrzymuje usługi."""
    from .krunner import service
    if not service.available():
        print("tide-daemon: brak python3-dbus albo python3-gi — wtyczka KRunnera wyłączona", flush=True)
        return None
    from .krunner.index import SessionIndex
    from .krunner.runner import KRunnerCore, LazyIndex

    index = SessionIndex(args.claude_dir, args.codex_dir, clock=clock)
    from .report_monitor import report_root
    from .reports.schedule import latest_index
    reports_dir = report_root(args.report_dir)
    core = KRunnerCore(index, lambda: trees.scanner.snapshot(), trees.launchers,
                       report=lambda: latest_index(reports_dir),
                       open_report=report.open if report is not None else None,
                       live=lambda: monitor.sessions, activate=monitor.activate, clock=clock,
                       codex=monitor.codex_threads(),
                       coordination=(lambda: json.loads(coordination.coordinators_json())) if coordination else None)
    try:
        return service.register(core, lambda post: LazyIndex(index, post), app)
    except Exception as exc:  # np. nazwa zajęta — reszta usługi działa dalej
        print(f"tide-daemon: wtyczka KRunnera niedostępna: {exc}", flush=True)
        return None
