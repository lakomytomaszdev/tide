"""Punkt wejścia tide-daemon.

Użycie: python3 tide-daemon.py [--print | --print-worktrees | --print-limits | --print-models] [--claude-dir DIR]
                               [--codex-dir DIR …] [--workspace DIR] [--worktree-depth N] [--no-processes]
                               [--no-notifications] [--no-krunner] [--no-tab-marks] [--now CZAS]
                               [--idle-after SEKUNDY | --no-idle] [--limits-poll SEKUNDY | --no-limits]
                               [--models-poll SEKUNDY | --no-models]
                               [--report [--report-days N]] [--report-dir DIR] [--report-at GG:MM]
                               [--no-report] [--report-delay SEKUNDY] [--report-summary claude|local|rules]
                               [--no-tasks]
                               [--print-coordination] [--coordination-days N] [--no-coordination]
                               [--no-youtrack] [--print-board] [--print-card-docs K-12] [--board-dir DIR]
                               [--no-board]
                               [--print-docker | --no-docker]
                               [--search-delay SEKUNDY]
                               [--no-activity-look] [--no-monitors] [--no-stream-shortcuts]

--print            jednorazowy odczyt: wypisuje JSON sesji i kończy, bez D-Bus,
--print-worktrees  jednorazowy odczyt worktrees (z rozmiarami) jako JSON, bez D-Bus,
--print-limits     prognoza limitów z historii i jednego GET /status jako JSON (niczego nie zapisuje),
--print-models     jednorazowo: stan serwerów modeli lokalnych i ról z models.json jako JSON (tylko GET,
                   niczego nie zapisuje), bez D-Bus,
--codex-dir        katalog danych Codexa, można powtórzyć (domyślnie $CODEX_HOME albo ~/.codex i
                   ~/snap/codex/current — Codex CLI ze snapa pisze do własnego katalogu),
--workspace        katalog projektów z repozytoriami (domyślnie $TIDE_WORKSPACE albo ~/DevWorkspace),
--worktree-depth   jak głęboko pod katalogiem projektów szukać repozytoriów (domyślnie 3),
--no-processes     nie czyta /proc (stan bez wiedzy o procesach, bez przechodzenia do okien),
--no-notifications bez powiadomień „Agent czeka”,
--no-krunner       bez wtyczki KRunnera (org.tide.Daemon.KRunner) i bez jej wyszukiwania po znaczeniu,
--search-delay     ile sekund po starcie usługi zacząć indeks wyszukiwania po znaczeniu w KRunnerze (domyślnie 60;
                   plik $XDG_DATA_HOME/tide/search/sessions.json, włączanie w Tide → Modele),
--no-tab-marks     bez znacznika stanu na kartach Konsole („● projekt · gałąź” i kolor karty),
--now              zamrożony zegar (ISO 8601) — dla testów i diagnostyki,
--idle-after       próg bezczynności w sekundach dla /Idle (domyślnie wallpaper.dozeAfterS z palety),
--no-idle          bez obiektu /Idle (bezczynność z kompozytora Wayland),
--limits-poll      co ile sekund pytać QuotaApp o /status (domyślnie limits.pollS z palety),
--no-limits        bez obiektu /Limits (historia, prognoza i powiadomienia Limitów AI),
--models-poll      co ile sekund odczytywać stan serwerów modeli lokalnych (domyślnie 15),
--no-models        bez obiektu /Models (stan serwerów modeli lokalnych z $XDG_CONFIG_HOME/tide/models.json),
--report           jednorazowy raport poranny za ostatnie --report-days dób (domyślnie 1) do --report-dir;
                   bez D-Bus, bez powiadomienia i bez pliku stanu — wypisuje ścieżkę index.html,
--report-dir       katalog raportów (domyślnie $XDG_DATA_HOME/tide/reports),
--report-at        godzina raportu porannego (domyślnie „at” z $XDG_CONFIG_HOME/tide/report.json albo 07:00),
--no-report        bez raportu porannego z terminu (Generate() z D-Bus działa dalej),
--report-delay     ile sekund po starcie usługi sprawdzić, czy raport jest należny (domyślnie 120),
--report-summary   opis „Na daily” i kart tablicy: claude (model przez `claude -p`), local (model lokalny z ról
                   summary i describe w models.json, nigdy Claude) albo rules (bez modelu); domyślnie
                   „summary” z $XDG_CONFIG_HOME/tide/report.json albo claude,
--no-tasks         bez obiektu /Tasks (nowe zadanie dla agenta: worktree i karta Konsoli),
--print-coordination  jednorazowo: koordynatorzy i ich tablice jako JSON (jak dane okna Tide Sesje), bez D-Bus,
--coordination-days   z ilu dni szukać koordynatorów (domyślnie 7),
--no-coordination  bez obiektu /Coordination (tablica koordynatorów, tickety, podagenci),
--no-youtrack      bez YouTracka (żadnych zapytań sieciowych): tablica koordynatorów bez tytułów i stanów,
                   tablica pracy bez synchronizacji stanu i importu zgłoszeń (PT-7),
--print-board      jednorazowo: migawka tablicy pracy jako JSON (tylko odczyt, niczego nie zakłada), bez D-Bus,
--print-card-docs  jednorazowo: plan i specyfikacja karty (jak /Board CardDocs, bez koordynatorów — plan tylko
                   z klucza `card`), bez D-Bus,
--board-dir        repozytorium tablicy pracy (domyślnie $TIDE_BOARD_DIR albo ~/DevWorkspace/tide-board),
--no-board         bez obiektu /Board (tablica pracy: karty, projekty, tagi).
--print-docker     jednorazowo: migawka kontenerów i miejsce na dysku jako JSON (tylko odczyt Engine API), bez D-Bus,
--no-docker        bez obiektu /Docker (kontenery, akcje, porządki; socket z $DOCKER_HOST albo /var/run/docker.sock).
--no-box           bez proxy pudełka (PT-168; i tak startuje tylko przy box.enabled w cardflow.json i mostku Incusa).
--no-activity-look bez nakładania wyglądu aktywności po CurrentActivityChanged i przy starcie (PT-35; silnik
                   z $XDG_DATA_HOME/tide/activity-look, make install-activity-look; pusty
                   $TIDE_ACTIVITY_LOOK_ENGINE też wyłącza).
--no-monitors      bez obiektu /Monitors (wejście prawego monitora PC ⇄ Xbox przez DDC/CI, PT-38; ddcutil szukany
                   w $TIDE_MONITORS_BIN zamiast PATH, jeśli ustawione) i bez powrotu na PC po wyjściu ze Streamu,
--no-stream-shortcuts  bez skrótów Stream (Pause, Meta+Pause, Meta+B tylko w aktywności Stream, PT-38; CLI rdzenia
                   z $TIDE_KAMP, pusty wyłącza).
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


def default_codex_dirs() -> list[str]:
    """Katalogi danych Codexa: $CODEX_HOME albo ~/.codex i ~/snap/codex/current (codex_homes.py)."""
    from . import codex_homes
    return codex_homes.default_homes()


def default_workspace() -> str:
    return os.environ.get("TIDE_WORKSPACE") or os.path.expanduser("~/DevWorkspace")


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="tide-daemon", description="Sesje agentów AI przez D-Bus")
    parser.add_argument("--claude-dir", default=default_claude_dir())
    # Powtarzalne: każdy --codex-dir to jeden katalog danych Codexa; podane zastępują domyślne.
    parser.add_argument("--codex-dir", action="append", help="katalog danych Codexa (można powtórzyć)")
    parser.add_argument("--no-processes", action="store_true")
    parser.add_argument("--no-notifications", action="store_true")
    parser.add_argument("--no-krunner", action="store_true")
    parser.add_argument("--search-delay", type=float, help="opóźnienie pierwszego indeksu wyszukiwania po znaczeniu")
    parser.add_argument("--no-tab-marks", action="store_true")
    parser.add_argument("--now", help="zamrożony zegar, np. 2026-10-26T12:00:00Z")
    parser.add_argument("--workspace", default=default_workspace())
    parser.add_argument("--worktree-depth", type=int, default=worktrees.MAX_DEPTH)
    parser.add_argument("--idle-after", type=float, help="próg bezczynności w sekundach")
    parser.add_argument("--no-idle", action="store_true")
    parser.add_argument("--limits-poll", type=float, help="odstęp odczytów /status w sekundach")
    parser.add_argument("--no-limits", action="store_true")
    parser.add_argument("--print-limits", action="store_true")
    parser.add_argument("--models-poll", type=float, help="odstęp odczytów stanu modeli w sekundach")
    parser.add_argument("--no-models", action="store_true")
    parser.add_argument("--print-models", action="store_true")
    parser.add_argument("--no-tasks", action="store_true")
    parser.add_argument("--print-coordination", action="store_true")
    parser.add_argument("--coordination-days", type=float, default=7.0)
    parser.add_argument("--no-coordination", action="store_true")
    parser.add_argument("--no-youtrack", action="store_true")
    parser.add_argument("--print-board", action="store_true")
    parser.add_argument("--print-card-docs", metavar="ID")
    parser.add_argument("--board-dir")
    # Tylko testy: atrapa /proc dla sprawdzenia nadawcy zatwierdzenia kontraktu testów (call_origin, PT-286).
    parser.add_argument("--proc-root", default="/proc", help=argparse.SUPPRESS)
    parser.add_argument("--no-board", action="store_true")
    parser.add_argument("--print-docker", action="store_true")
    parser.add_argument("--no-docker", action="store_true")
    parser.add_argument("--no-box", action="store_true")
    parser.add_argument("--no-activity-look", action="store_true")
    parser.add_argument("--no-monitors", action="store_true")
    parser.add_argument("--no-stream-shortcuts", action="store_true")
    parser.add_argument("--print", dest="print_once", action="store_true")
    parser.add_argument("--print-worktrees", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--report-days", type=float, default=1.0)
    parser.add_argument("--report-dir")
    parser.add_argument("--report-at")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--report-delay", type=float)
    parser.add_argument("--report-summary", choices=("claude", "local", "rules"))
    args = parser.parse_args(argv)
    if not args.codex_dir:
        args.codex_dir = default_codex_dirs()
    return args


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
        procs = None if args.no_processes else processes.probe(log_roots=scanner.codex_log_roots())
        agents = scanner.sessions(now, procs)
        cwds = worktrees.agent_cwds(agents, procs)
        index = worktrees.SessionIndex(args.claude_dir, args.codex_dir)
        print(json.dumps(worktrees.scan(args.workspace, cwds, args.worktree_depth, now=now, sessions=index,
                                        live=worktrees.live_sessions(agents)),
                         ensure_ascii=False, indent=2))
        return 0

    if args.print_docker:
        print(json.dumps(docker_once(args, clock), ensure_ascii=False, indent=2))
        return 0

    if args.print_limits:
        print(json.dumps(limits_once(clock), ensure_ascii=False, indent=2))
        return 0

    if args.print_models:
        from .models import status as models_status
        print(json.dumps(models_status.read(), ensure_ascii=False, indent=2))
        return 0

    if args.print_coordination:
        from . import processes
        now = clock()
        scanner.discover(now)
        scanner.update()
        procs = None if args.no_processes else processes.probe(log_roots=scanner.codex_log_roots())
        print(json.dumps(coordination_once(args, clock, scanner.sessions(now, procs), report_settings),
                         ensure_ascii=False, indent=2))
        return 0

    if args.print_board:
        print(json.dumps(board_store(args).snapshot(repos=worktrees.find_repositories(args.workspace,
                                                                                      args.worktree_depth)),
                         ensure_ascii=False, indent=2))
        return 0

    if args.print_card_docs:
        print(json.dumps(card_docs_once(args), ensure_ascii=False, indent=2))
        return 0

    if args.print_once:
        from . import processes
        now = clock()
        scanner.discover(now)
        scanner.update()
        procs = None if args.no_processes else processes.probe(log_roots=scanner.codex_log_roots())
        print(json.dumps(scanner.sessions(now, procs), ensure_ascii=False, indent=2))
        return 0

    from PyQt6.QtCore import QCoreApplication, QTimer
    from . import dbus_service
    from .monitor import AgentMonitor
    from .worktree_monitor import WorktreeMonitor

    app = QCoreApplication(sys.argv[:1])
    app.setApplicationName("tide-daemon")
    # Próbnik stosów na żądanie (plik $XDG_RUNTIME_DIR/tide-daemon-profile) — diagnoza zużycia CPU bez ptrace.
    from . import sampler
    sampler.start()
    monitor = AgentMonitor(scanner, use_processes=not args.no_processes, clock=clock)
    monitor.start()
    # Osobny, mały indeks sesji dla worktrees (KRunner ma własny, pełniejszy — krunner/index.py).
    worktree_sessions = worktrees.SessionIndex(args.claude_dir, args.codex_dir)
    launchers = worktrees.Launchers()
    # Worktree kroków w tle i kart w toku (przepływ karty, PT-58) — zajęte jak katalog agenta; sesje kroków nie są
    # wierszami agentów, więc bez tego wyglądałyby na wolne. Źródło dochodzi po starcie Tablicy.
    flow_places = []
    trees = WorktreeMonitor(worktrees.WorktreeScanner(args.workspace, args.worktree_depth, clock=clock,
                                                      sessions=worktree_sessions),
                            lambda: worktrees.agent_cwds(monitor.sessions, monitor.procs)
                            + [place for source in flow_places for place in source()],
                            launchers, refresh_agents=monitor.recompute,
                            live_source=lambda: worktrees.live_sessions(monitor.sessions),
                            card_places_source=lambda: [place for source in flow_places for place in source()])
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
    search = getattr(krunner, "semantic", None)  # wyszukiwanie po znaczeniu w KRunnerze (PT-19)
    models = None if args.no_models else start_models(app, args, report, coordination, search)
    if models is not None:
        scanner.known_local_models = models.known_models  # role i modele na serwerach ze stanu /Models
        # Drugi stopień rozpoznawania pytań (PT-42): model roli `classify` ocenia wątpliwe końcówki tur; wynik
        # przelicza stan w wątku Qt, zamknięcie usługi przerywa trwające wywołanie.
        from .agents.classify import Classifier
        scanner.classifier = Classifier(on_result=monitor.classified.emit)
        app.aboutToQuit.connect(scanner.classifier.close)
    tasks = None if args.no_tasks else start_tasks(app, args, launchers, trees, monitor)
    board = None if args.no_board else start_board(app, args, trees, monitor, coordination, clock, limits)
    if board is not None:
        # Notki „Skończone dziś”: ręczne sprawdzenie z karty tablicy pracy (check: manual, spec — PT-5).
        report.card_check = board_card_check(board)
        # Nazwy projektów z tablicy i karty, do których należy praca — w raporcie i jego tekście (reports/cards.py).
        report.board = board_snapshot(board)
    docker = None if args.no_docker else start_docker(app, args, clock, launchers)
    box_proxy = None if args.no_box else start_box_proxy(app)  # noqa: F841 — musi żyć do końca
    monitors = None
    if not args.no_monitors:
        from .monitors_service import MonitorsService
        monitor_notifier = None
        if not args.no_notifications:
            from .notifications import LimitsNotifier
            monitor_notifier = LimitsNotifier(parent=app).send  # nieudany powrót na PC po wyjściu ze Streamu
        monitors = MonitorsService(notify=monitor_notifier, parent=app)  # wejście prawego monitora PC ⇄ Xbox
    annotators = [a for a in (tasks.annotate if tasks is not None else None,
                              coordination.annotate if coordination is not None else None) if a]
    if annotators:
        def annotate(sessions):
            for annotator in annotators:
                annotator(sessions)
        monitor.annotate = annotate  # znacznik „nowe” (/Tasks) i postęp koordynatora (/Coordination)
    look_state = notification_filter = None
    if not args.no_activity_look:
        # Wygląd bieżącej aktywności dla widżetów (/ActivityLook) i filtr powiadomień Stream (PT-36).
        from .activity_state import ActivityState, NotificationFilter
        look_state = ActivityState(parent=app)
        notification_filter = NotificationFilter(look_state, parent=app)
    try:
        holder = dbus_service.register(monitor, trees, idle=idle, reports=report,  # noqa: F841 — musi żyć do końca
                                       limits=limits, tasks=tasks, coordination=coordination, board=board,
                                       docker=docker, models=models, activity_look=look_state,
                                       monitors=monitors)
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
        if board is not None and board.flow is not None:
            from .notifications import NoteNotifier
            flow_notifier = NoteNotifier(board.show_window, parent=app)

            def notify_card_step(item):
                labels = {"question": "pytanie", "denied": "potrzebna zgoda", "error": "błąd kroku",
                          "plan": "plan do zatwierdzenia", "review": "karta do sprawdzenia",
                          "limit": "limit dostawcy", "stalled": "karta stoi", "caretaker": "decyzja opiekuna"}
                kind = labels.get(item["result"], "krok czeka")
                flow_notifier.notify(f"flow:{item['card']}:{item['step']}",
                                     f"{item['card']}: {kind}" + (f" z {item['ticket']}" if item['ticket'] else ""),
                                     str(item.get("detail") or "")[:300], item["card"])

            board.flow.notifier = notify_card_step
        if limits is not None:
            from .notifications import LimitsNotifier
            limits_notifier = LimitsNotifier(parent=app)
            limits.alert.connect(limits_notifier.send)
    if limits is not None:
        limits.start()  # pierwszy odczyt /status po rejestracji i po podłączeniu powiadomień
    if models is not None:
        models.start()  # pierwszy odczyt stanu serwerów modeli w tle, po rejestracji na magistrali
    if search is not None:
        search.start()  # indeks po znaczeniu w tle, po --search-delay, z najniższym priorytetem kolejki modeli
    if board is not None:
        board.start()  # zmiany z czasu, gdy usługa nie działała, i obserwacja katalogu tablicy
        if board.flow is not None:
            flow_places.append(board.flow.engine.busy_places)
            board.flow.start()  # kroki w tle z dziennika kart: podpięcie po restarcie, koniec, strażnik
    if coordination is not None:
        # Żywy raport i notki „Skończone dziś”: każda nowa wersja tablic koordynacji.
        coordination.changed.connect(lambda: report.coordination_changed(coordination.boards))
        coordination.start()  # pierwszy odczyt dzienników koordynatorów w tle, chwilę po starcie
    if docker is not None:
        docker.start()  # migawka i strumień zdarzeń w tle; brak Dockera to stan, nie błąd usługi
    activity_look = None
    if not args.no_activity_look:
        from .activity_look_monitor import ActivityLookMonitor, default_engine
        activity_look = ActivityLookMonitor(default_engine(), parent=app)
        activity_look.start()  # nasłuch zmiany aktywności i kontrola wyglądu bieżącej (logowanie)
    if look_state is not None:
        look_state.start()  # bieżąca aktywność z ActivityManagera; filtr powiadomień idzie za jej wyglądem
    stream_keys = None
    if not args.no_stream_shortcuts:
        from .stream_shortcuts import KGlobalAccel, StreamShortcuts, StreamShortcutsMonitor
        stream_keys = StreamShortcutsMonitor(StreamShortcuts(KGlobalAccel()), parent=app)
        stream_keys.start()  # naciśnięcia skrótów Stream; rejestracja według aktywności niżej
    # Jedno źródło „czy Stream” (PT-38) dla skrótów i powrotu monitora na PC; po scaleniu z PT-36 do podmiany
    # na sygnał wyglądu aktywności — odbiorcy biorą tylko stream: bool.
    activity = None
    if stream_keys is not None or monitors is not None:
        from .current_activity import CurrentActivity
        activity = CurrentActivity(parent=app)
        if stream_keys is not None:
            activity.changed.connect(lambda _activity, stream: stream_keys.stream_changed(stream))
            activity.unknown.connect(stream_keys.activity_unknown)  # do odpowiedzi ActivityManagera — praca
        if monitors is not None:
            # Praca zawsze z obydwoma ekranami: wyjście ze Streamu (i start w pracy) z wejściem Xboxa → PC.
            activity.changed.connect(lambda _activity, stream: monitors.stream_changed(stream))
        activity.start()

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
        from . import tabmarks, windows
        from .konsole import KonsoleTabs
        marker = tabmarks.TabMarker(KonsoleTabs(), tabmarks.load_state_colors(), tabmarks.default_store())
        # Przejście do okna chwilowo dopisuje znacznik do tytułu karty (PT-33) — wtedy znacznik
        # stanu tej karty czeka, żeby nie wziął cudzego tytułu za zmianę użytkownika.
        windows.set_tab_guard(marker)

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
    if models is not None:
        models.stop()
    if search is not None:
        search.stop()
    if coordination is not None:
        coordination.stop()
    if board is not None:
        board.stop()
    if docker is not None:
        docker.stop()
    if activity_look is not None:
        activity_look.stop()
    if look_state is not None:
        look_state.stop()
        notification_filter.stop()  # inhibicja i dozwolone aplikacje nie zostają po usłudze
    if activity is not None:
        activity.stop()
    if stream_keys is not None:
        stream_keys.stop()  # klawisze wracają do pracy (Meta+B — profil zasilania)
    if monitors is not None:
        monitors.stop()
    from . import windows
    windows.give_back_tabs()  # tytuł karty ze znacznikiem przejścia do okna, jeśli jeszcze nie wrócił
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
    procs = None if args.no_processes else processes.probe(log_roots=scanner.codex_log_roots())
    days = max(0.01, min(args.report_days, schedule.MAX_PERIOD / 86400))
    try:
        index, report = reports.generate(
            report_root(args.report_dir), schedule.report_day(now, settings).isoformat(), now - days * 86400, now,
            palette=reports.load_palette(palette_candidates()), workspace=args.workspace,
            claude_root=args.claude_dir, codex_root=args.codex_dir, live=scanner.sessions(now, procs),
            max_depth=args.worktree_depth, coordination_cache=coordination_cache_file(args),
            summary=args.report_summary or settings.summary, summary_model=settings.summary_model,
            tracker=report_tracker(args, clock), board=report_board(args))
    except (OSError, RuntimeError) as exc:
        print(f"tide-daemon: raport: {exc}", file=sys.stderr)
        return 1
    print(index)
    print(f"{len(report.repos)} projektów w raporcie, {len(report.quiet)} bez ruchu", file=sys.stderr)
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


def board_card_check(board):
    """Hak card_check dla ReportMonitor: (tablica koordynacji, ticket) → {"check": "manual", "spec"} z karty
    tablicy pracy powiązanej z koordynatorem (reports/notes.card_for), albo None."""
    from .reports import notes as report_notes

    def check(coord_board, ticket):
        try:
            snapshot = json.loads(board.snapshot_json())
        except ValueError:
            return None
        return report_notes.card_check_from(snapshot, coord_board, ticket)
    return check


def board_snapshot(board):
    """Funkcja() → migawka tablicy pracy (/Board Snapshot()) dla raportu albo None."""
    def snapshot():
        try:
            data = json.loads(board.snapshot_json())
            data["flowRoot"] = board.store.root
            return data
        except ValueError:
            return None
    return snapshot


def report_board(args):
    """Migawka tablicy pracy dla jednorazowego raportu (--report); --no-board albo błąd — None."""
    if args.no_board:
        return None
    try:
        store = board_store(args)
        data = store.snapshot()
        data["flowRoot"] = store.root
        return data
    except Exception as exc:  # tablica to dodatek
        print(f"tide-daemon: raport: tablica pracy pominięta: {exc!r}", file=sys.stderr)
        return None


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
    from .board import default_board_dir
    # Katalogi dokumentów kart (addDirs) tylko z tej tablicy, którą obsługuje /Board.
    planner = Planner(args.workspace, args.worktree_depth, terminal_available=terminal.available,
                      board_root=args.board_dir or default_board_dir())
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
    („summary”, „summaryModel”; --report-summary ma pierwszeństwo; w trybie local model z roli `describe`
    w models.json), pamięć w $XDG_CACHE_HOME/tide."""
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


def card_docs_once(args) -> dict:
    """--print-card-docs: dokumenty karty z migawki tablicy, bez stanu z pracy agentów (tylko odczyt)."""
    from .board.docs import CardDocs
    from .board.model import BoardError, card_num
    store = board_store(args)
    try:
        ident = f"K-{card_num(args.print_card_docs)}"
    except BoardError as exc:
        return {"ok": False, "error": exc.code, "message": str(exc)}
    snapshot = store.snapshot()
    card = next((c for c in snapshot["cards"] if c["id"] == ident), None)
    if card is None:
        return {"ok": False, "error": "not-found", "message": f"nie ma karty {ident}"}
    project = next((p for p in snapshot["projects"] if card["project"] and p["slug"] == card["project"]), None)
    return CardDocs(store.root).read(card, project)


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


def start_board(app, args, trees, monitor=None, coordination=None, clock=time.time, limits=None):
    """Obszar /Board: tablica pracy w lokalnym repozytorium git (board/, board_monitor.py).
    Repozytoria do dodania jako projekty bierzemy z gotowej listy obszaru Worktrees, a stan kart
    (W toku, Czeka na mnie, Do sprawdzenia — board/derive.py) z sesji /Agents i tablic /Coordination."""
    from .board.derive import BranchState, CardDeriver
    from .board_monitor import BoardMonitor

    def repos():
        return sorted({item["repoPath"] for item in trees.scanner.snapshot()
                       if item.get("isMain") and not item.get("missing") and item.get("repoPath")})

    deriver = CardDeriver(sessions_source=(lambda: monitor.sessions) if monitor is not None else (lambda: []),
                          boards_source=(lambda: coordination.boards) if coordination is not None else (lambda: []),
                          branches=BranchState(clock=clock, background=True), clock=clock)
    youtrack = None
    if not getattr(args, "no_youtrack", True):   # bez opcji (testy z częściowymi argumentami) — bez YouTracka
        # Synchronizacja stanu kart i ticketów z YouTrackiem (PT-7): zapis dopiero po pierwszym odczycie koordynacji.
        from .board.ytsync import SyncEngine
        from .ytsync_monitor import START_GRACE_S, YouTrackSyncMonitor
        try:  # testy usługi skracają odczekanie po starcie (atrapa YouTracka)
            grace = float(os.environ.get("TIDE_YOUTRACK_START_GRACE") or START_GRACE_S)
        except ValueError:
            grace = START_GRACE_S
        # Zegar prawdziwy, nie --now: odliczanie i ponowienia to czas rzeczywisty.
        youtrack = YouTrackSyncMonitor(SyncEngine(), start_grace_s=grace,
                                       coordinators=(lambda: coordination.boards) if coordination is not None else None,
                                       ready=(lambda: coordination.ready) if coordination is not None else None,
                                       parent=app)
    board = BoardMonitor(board_store(args), repos_source=repos, derive=deriver, launchers=trees.launchers,
                         coordinators=(lambda: coordination.boards) if coordination is not None else None,
                         youtrack=youtrack, proc_root=getattr(args, "proc_root", None) or "/proc", parent=app)
    trees.changed.connect(board.publish)  # nowe repozytorium w katalogu projektów
    board.flow = start_card_flow(app, args, board, monitor, limits)
    # Git (czy gałąź scalona) idzie w wątku roboczym; nowy wynik przelicza karty w wątku głównym.
    deriver.branches.on_result = board.rederive_later
    if monitor is not None:
        monitor.changed.connect(board.rederive)  # agent zaczął, czeka, skończył — karta zmienia kolumnę
    if coordination is not None:
        coordination.changed.connect(board.rederive)  # tickety, plan, pytania koordynatora
    return board


def start_card_flow(app, args, board, monitor=None, limits=None):
    """Przepływ karty (PT-58, cardflow/, cardflow_monitor.py): StartCard, kroki w tle, dziennik kart. Sesje kroków
    znikają z /Agents (monitor.hidden). $TIDE_SYSTEMD_RUN i $TIDE_FLOW_POLL_MS — tylko testy usługi (atrapa
    systemd-run, krótszy zegar)."""
    from .cardflow import execution as flow_execution
    from .cardflow.engine import FlowEngine
    from .cardflow_monitor import POLL_MS, CardFlowMonitor
    from .models import config as models_config
    from .models import status as models_status
    from .tasks.terminal import KonsoleWindows, TaskTerminal
    try:
        poll_ms = int(os.environ.get("TIDE_FLOW_POLL_MS") or POLL_MS)
    except ValueError:
        poll_ms = POLL_MS
    claude_dir = getattr(args, "claude_dir", None) or default_claude_dir()   # testy podają częściowe argumenty
    probe = activate = None
    if not getattr(args, "no_processes", False):
        from . import processes, windows
        probe, activate = processes.probe, windows.activate
    terminal = TaskTerminal(board.launchers, KonsoleWindows(), probe=probe, activate=activate)
    engine = FlowEngine(board.store, claude_projects=os.path.join(claude_dir, "projects"),
                        systemd_run=os.environ.get("TIDE_SYSTEMD_RUN", ""),
                        which=flow_execution.agent_which,
                        catalog=lambda: models_config.load().catalog,
                        models_config=models_config.load, model_probe=models_status.endpoint_status,
                        codex_roots=getattr(args, "codex_dir", None), terminal=terminal,
                        limits_source=(lambda: json.loads(limits.forecast_json())) if limits is not None else None)
    engine.migrate_environment()   # PT-190: propozycja profilu z cardflow.json, gdy projekt nie ma profilu
    if monitor is not None:
        monitor.hidden = engine.is_hidden
        engine.sessions_source = lambda: monitor.raw_sessions
        monitor.raw_changed.connect(board.rederive)
    return CardFlowMonitor(engine, board, poll_ms=max(100, poll_ms), parent=app)


def docker_once(args, clock) -> dict:
    """--print-docker: migawka kontenerów i miejsce na dysku — wyłącznie zapytania GET do Engine API."""
    from .docker import DockerEngine
    engine = DockerEngine(workspace=args.workspace, clock=clock)
    snapshot = engine.refresh()
    disk = engine.disk() if snapshot.get("available") else {"ok": False, "error": snapshot.get("error", "")}
    return {"snapshot": snapshot, "disk": disk}


def start_box_proxy(app, settings_file: str | None = None, **kwargs):
    """Proxy wyjściowe pudełka (PT-168): tylko przy `box.enabled` i istniejącym mostku Incusa. Każda
    przeszkoda (brak Incusa, brak mostka, zły adres, zajęty port) to wpis w dzienniku i usługa bez proxy."""
    from .box import proxy as box_proxy
    from .box import settings as box_settings
    try:
        service = box_proxy.start_service(box_settings.load(settings_file), **kwargs)
    except box_proxy.ProxyError as exc:
        print(f"tide-daemon: proxy pudełka nie startuje: {exc}", file=sys.stderr, flush=True)
        return None
    if service is not None and app is not None:
        app.aboutToQuit.connect(service.stop)
    return service


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


def start_models(app, args, report=None, coordination=None, search=None):
    """Modele lokalne (models/, models_monitor.py): stan serwerów (models.json czytany przy każdym odczycie)
    i zapis ustawień z okna Tide. SetReportSummary przełącza od razu raport i opisy kart (bez restartu),
    chyba że tryb wymusza --report-summary."""
    from .models_monitor import ModelsMonitor, ReportLink
    poll_ms = int(round(args.models_poll * 1000)) if args.models_poll and args.models_poll > 0 else None

    def live():
        return args.report_summary or (report.summary if report is not None else None)

    def apply(mode):
        if report is not None:
            report.summary = mode
        describer = getattr(coordination, "describer", None)
        if describer is not None:
            describer.set_mode(mode)

    link = ReportLink(live=live, apply=apply, pinned=bool(args.report_summary))
    return ModelsMonitor(poll_ms=poll_ms, report=link, search=search, parent=app)


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
    from .krunner.semantic import FIRST_DELAY_S, SemanticSearch
    semantic = SemanticSearch(index, tickets=coordination_tickets(coordination), clock=clock,
                              first_delay=args.search_delay if args.search_delay is not None else FIRST_DELAY_S)
    core = KRunnerCore(index, lambda: trees.scanner.snapshot(), trees.launchers, semantic=semantic,
                       report=lambda: latest_index(reports_dir),
                       open_report=report.open if report is not None else None,
                       live=lambda: monitor.sessions, activate=monitor.activate, clock=clock,
                       codex=monitor.codex_threads(),
                       coordination=(lambda: json.loads(coordination.coordinators_json())) if coordination else None)
    try:
        holder = service.register(core, lambda post: LazyIndex(index, post), app)
    except Exception as exc:  # np. nazwa zajęta — reszta usługi działa dalej
        print(f"tide-daemon: wtyczka KRunnera niedostępna: {exc}", flush=True)
        return None
    holder.semantic = semantic
    return holder


def coordination_tickets(coordination):
    """Funkcja() → {id sesji koordynatora: [„PT-19 tytuł”, …]} z tablic koordynacji (do indeksu po znaczeniu)."""
    if coordination is None:
        return None

    def tickets():
        out = {}
        for board in list(getattr(coordination, "boards", None) or ()):
            items = [f"{t.get('id', '')} {t.get('title', '')}".strip() for t in board.get("tickets") or ()
                     if isinstance(t, dict)]
            if board.get("id") and items:
                out[board["id"]] = items
        return out
    return tickets
