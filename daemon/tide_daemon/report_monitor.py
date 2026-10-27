"""Raport poranny w usłudze: termin (reports/schedule.py), praca w tle, powiadomienie.

- po starcie usługi (logowanie) i potem co minutę: czy raport doby jest należny,
- generowanie w wątku roboczym; dane z obszarów Agents i Worktrees bierzemy w wątku głównym
  (gotowe migawki), git i dzienniki czyta wątek roboczy,
- po raporcie z terminu jedno powiadomienie na dobę „Raport poranny gotowy — N projektów”
  z przyciskiem „Otwórz” (xdg-open index.html); Generate() z D-Bus nie powiadamia,
- retencja: katalogi starsze niż keepDays znikają po każdym raporcie,
- przeczytanie: Open() z D-Bus (widżety), „Otwórz” w powiadomieniu i KRunner „raport” otwierają
  raport przez opener (xdg-open przez systemd-run) i oznaczają go jako przeczytany w pliku stanu;
  każda zmiana (nowy raport, przeczytanie) to sygnał changed — widżety pobierają wtedy Status(),
- okno Tide (widok „Raporty”): lista dni i report.json doby (reports/data.py), otwarcie strony doby
  albo projektu (OpenDay) i mark_read — obejrzenie najnowszego raportu w oknie gasi kropkę jak Open(),
- żywy raport (reports/live.py): bieżąca doba odświeża się sama po zmianach w pracy (tickety, agent
  skończył), zbiorczo i najwyżej co 12 min; „czeka” przeliczane na żywo przy każdym odczycie
  (Day/Days/Status) z obszaru Agents, a jego zmiana to tylko sygnał changed,
- notki „Skończone dziś” (reports/notes.py): ticket przechodzi do gotowe → notka w raporcie doby
  i powiadomienie „<projekt>: skończone — …” z „Otwórz” (okno Tide, widok Raporty), w osobnym wątku.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import models, reports
from .reports import data as report_data, live as live_mod, notes as notes_mod, render, schedule


class ReportMonitor(QObject):
    ready = pyqtSignal(str)                  # ścieżka index.html po każdym raporcie
    changed = pyqtSignal()                   # nowy raport albo zmiana przeczytania (Status())
    _finished = pyqtSignal(object, object)   # (kontekst, Future) — z wątku roboczego do głównego
    _noted = pyqtSignal(object, object)      # (doba, Future z notkami) — z wątku notek do głównego

    def __init__(self, settings: schedule.Settings, *, root: str, state_file: str, palette_paths: list[str],
                 workspace: str, claude_root: str, codex_root: str | list[str], max_depth: int = 3,
                 sessions=lambda: [], worktrees=lambda: None, notifier=None, clock=time.time,
                 login_delay: float = schedule.LOGIN_DELAY, check_interval: float = schedule.CHECK_INTERVAL,
                 fetch_limits: bool = True, coordination_cache: str | None = None, opener=None,
                 summary: str | None = None, tracker=None, notes_file: str | None = None, note_notifier=None,
                 card_check=None, live_interval: float = live_mod.INTERVAL, live_settle: float = live_mod.SETTLE,
                 note_settle: float = notes_mod.NOTE_SETTLE,
                 parent=None):
        super().__init__(parent)
        self.settings = settings
        self.root = root
        self.state_file = state_file
        self.palette_paths = palette_paths
        self.workspace = workspace
        self.claude_root = claude_root
        self.codex_root = codex_root
        self.max_depth = max_depth
        self.sessions = sessions        # funkcja() → bieżące sesje obszaru Agents
        self.worktrees = worktrees      # funkcja() → lista worktrees (albo None: raport przejrzy repozytoria sam)
        self.notifier = notifier
        self.clock = clock
        self.fetch_limits = fetch_limits
        self.coordination_cache = coordination_cache  # plik tablicy koordynacji („Plany i tickety”) albo None
        self.tracker = tracker          # YouTrackCache (opisy ticketów planów w repozytoriach, tylko GET) albo None
        self.opener = opener            # funkcja(ścieżka index.html) -> bool (xdg-open) albo None
        self.summary = summary or settings.summary    # „Na daily”: claude, local albo rules (--report-summary)
        self.running = False
        self._waiting: list = []        # odpowiedzi Generate() czekające na bieżący raport
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-report")
        self._cancel = models.Cancel()  # stop() przerywa trwające wywołanie modelu lokalnego w raporcie
        self._finished.connect(self._deliver)
        # Żywy raport i notki po tickecie.
        self.live = live_mod.Live(interval=live_interval, settle=live_settle)
        self.notes = notes_mod.Store(notes_file) if notes_file else None
        self.note_tracker = notes_mod.Tracker(self.notes, settle=note_settle) if self.notes is not None else None
        self.note_notifier = note_notifier      # obiekt z notify(klucz, tytuł, treść, element widoku) albo None
        self.batcher = notes_mod.Batcher()
        # Hak karty tablicy pracy (PT-5): funkcja(board, ticket) → {"check": "manual", "spec": ścieżka} albo None.
        # board/ticket to tablica koordynacji i jej ticket (D-Bus Board()); wynik czyta notes.manual_check.
        # Podpinany w app.main po starcie tablicy pracy (app.board_card_check); bez niej — tylko plan.
        self.card_check = card_check
        self._ticket_states: dict | None = None
        self._session_states: dict | None = None
        self._waiting_sig: tuple | None = None
        self._note_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-report-notes")
        self._noted.connect(self._deliver_notes)
        self._live_timer = QTimer(self, singleShot=True)
        self._live_timer.timeout.connect(self._live_due)
        self._boards: list = []
        self._note_timer = QTimer(self, singleShot=True)   # tickety w gotowe czekające na notkę (settle)
        self._note_timer.timeout.connect(lambda: self._check_notes())
        self._login = QTimer(self, singleShot=True, interval=int(login_delay * 1000))
        self._login.timeout.connect(lambda: self.check(at_login=True))
        self._timer = QTimer(self, interval=int(check_interval * 1000))
        self._timer.timeout.connect(lambda: self.check(at_login=False))

    def start(self) -> None:
        if not self.settings.enabled:
            return
        self._login.start()
        self._timer.start()

    def stop(self) -> None:
        self._login.stop()
        self._timer.stop()
        self._live_timer.stop()
        self._note_timer.stop()
        self._cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._note_pool.shutdown(wait=False, cancel_futures=True)

    def latest(self) -> str:
        return schedule.latest_index(self.root)

    def today(self) -> str:
        return schedule.report_day(self.clock(), self.settings).isoformat()

    def _live_sessions(self) -> list:
        try:
            return list(self.sessions() or [])
        except Exception:  # obszar Agents niedostępny — bez przeliczenia na żywo
            return []

    def _notes_of(self, day: str) -> dict:
        return self.notes.notes(day) if self.notes is not None else {}

    def status(self) -> dict:
        """Status() z „czeka” przeliczonym na żywo dla raportu bieżącej doby (nieaktualne się nie liczą)."""
        out = schedule.status(self.root, schedule.load_state(self.state_file))
        today = self.today()
        if out.get("day") == today:
            data = report_data.load(self.root, today)
            if data is not None:
                live = report_data.apply_live(data, today, self._live_sessions(), self._notes_of(today))
                out["waiting"] = live["counts"].get("waiting", out.get("waiting"))
                out["waitingNow"] = live["counts"].get("waitingNow", 0)
                out["finished"] = live["counts"].get("finished", 0)
        return out

    def status_json(self) -> str:
        return json.dumps(self.status(), ensure_ascii=False)

    def days_json(self) -> str:
        today = self.today()
        return json.dumps(report_data.days(self.root, schedule.load_state(self.state_file), today,
                                           self.settings.keep_days, self._live_sessions(), self._notes_of),
                          ensure_ascii=False)

    def day_json(self, day: str) -> str:
        today = self.today()
        sessions = self._live_sessions() if day == today else None
        return json.dumps(report_data.day(self.root, day, schedule.load_state(self.state_file), today, sessions,
                                          self._notes_of(day) if report_data.valid_day(day) else None),
                          ensure_ascii=False)

    # --- żywy raport -----------------------------------------------------------------------

    def sessions_changed(self) -> None:
        """Zmiana w /Agents: inne „czeka” → changed (widok przelicza na żywo, bez składania raportu);
        agent skończył pracę → raport doby do odświeżenia (zbiorczo, live.py)."""
        sessions = self._live_sessions()
        signature = live_mod.waiting_signature(sessions)
        if self._waiting_sig is not None and signature != self._waiting_sig and self._has_today():
            self.changed.emit()
        self._waiting_sig = signature
        states, done = live_mod.finished_turns(self._session_states or {}, sessions)
        if self._session_states is not None and done:
            self.mark_dirty("agent skończył pracę")
        self._session_states = states

    def coordination_changed(self, boards: list) -> None:
        """Nowe tablice koordynacji: zmiana stanu ticketu → raport do odświeżenia; ticket skończony →
        notka „Skończone dziś” z powiadomieniem (od razu, w wątku notek)."""
        boards = list(boards or [])
        states, changes = live_mod.transitions(self._ticket_states or {}, boards)
        if self._ticket_states is not None and changes:
            self.mark_dirty("zmiana ticketu")
        self._ticket_states = states
        self._boards = boards
        self._check_notes()

    def _check_notes(self) -> None:
        if self.note_tracker is None:
            return
        boards = self._boards
        now = self.clock()
        day = schedule.report_day(now, self.settings)
        try:
            finished = self.note_tracker.update(boards, now, schedule.day_start_ts(day, self.settings))
        except OSError as exc:
            print(f"tide-daemon: raport: nie zapisano stanu notek: {exc}", flush=True)
            return
        if finished:
            self._write_notes(day.isoformat(), finished, now)
        wait = self.note_tracker.due(now)
        if wait is None:
            self._note_timer.stop()
        else:
            self._note_timer.start(int(wait * 1000) + 1000)

    def mark_dirty(self, reason: str) -> None:
        self.live.mark(reason, self.clock())
        self._arm_live()

    def _has_today(self) -> bool:
        return schedule.load_state(self.state_file).day == self.today()

    def _arm_live(self) -> None:
        delay = self.live.delay(self.clock())
        if delay is None:
            self._live_timer.stop()
            return
        self._live_timer.start(int(delay * 1000))

    def _live_due(self) -> None:
        if self.running:
            return   # _deliver uzbroi zegar ponownie
        delay = self.live.delay(self.clock())
        if delay is None:
            return
        if delay > 0:
            self._live_timer.start(int(delay * 1000))
            return
        if not self._has_today():
            self.live.ran(self.clock())   # przed porannym raportem doby nie ma czego odświeżać
            return
        print(f"tide-daemon: raport: żywe odświeżenie ({self.live.describe()})", flush=True)
        self._start(scheduled=False, live=True)

    # --- notki po tickecie -------------------------------------------------------------------

    def _write_notes(self, day: str, finished: list, now: float) -> None:
        jobs = list(finished)
        mode, model = self.summary, self.settings.summary_model
        card_check = None
        if self.card_check is not None:
            # Hak czyta migawkę tablicy pracy — w wątku głównym; wątek notek dostaje gotowe wyniki.
            checks = {}
            for board, ticket in jobs:
                try:
                    checks[notes_mod.ticket_id(board, ticket)] = self.card_check(board, ticket)
                except Exception as exc:  # karta to dodatek
                    print(f"tide-daemon: raport: karta tablicy dla notki: {exc!r}", flush=True)
            card_check = lambda board, ticket: checks.get(notes_mod.ticket_id(board, ticket))  # noqa: E731

        def work():
            return notes_mod.write_notes(jobs, mode=mode, model=model, card_check=card_check, now=now,
                                         home=os.path.expanduser("~"))
        try:
            future = self._note_pool.submit(work)
        except RuntimeError:  # pula zamknięta (usługa się kończy)
            return
        future.add_done_callback(lambda f, day=day: self._noted.emit(day, f))

    def _deliver_notes(self, day: str, future) -> None:
        try:
            written = future.result()
        except Exception as exc:  # notka nie może położyć usługi
            print(f"tide-daemon: raport: notki: {exc!r}", flush=True)
            return
        if not written or self.notes is None:
            return
        written = self.notes.add(day, written)   # tylko dodane albo zmienione — raport i powiadomienie zgodne
        if not written:
            return
        self.notes.prune(self.clock(), schedule.report_days(self.root) + [day])
        try:
            self.notes.save()
        except OSError as exc:
            print(f"tide-daemon: raport: nie zapisano notek: {exc}", flush=True)
        print(f"tide-daemon: raport {day}: {len(written)} skończonych ticketów", flush=True)
        self.changed.emit()
        if self.note_notifier is None:
            return
        by_project: dict[str, list] = {}
        for note in written:
            by_project.setdefault(note["project"], []).append(note)
        for project, items in by_project.items():
            target = f"{day}/{reports.collect.slug_base(project)}"
            for message in self.batcher.add(project, items, self.clock(), day):
                try:
                    self.note_notifier.notify(message["key"], message["summary"], message["body"], target)
                except Exception as exc:  # powiadomienie to dodatek
                    print(f"tide-daemon: raport: powiadomienie o notce: {exc!r}", flush=True)

    def open_day(self, day: str, project: str = "") -> bool:
        """Strona raportu doby (index.html albo <slug>.html projektu) w przeglądarce. Otwarcie którejkolwiek
        strony najnowszego raportu oznacza go jako przeczytany; starszego — nie zmienia kropki."""
        return self.open_path(report_data.page_path(self.root, day, project))

    def mark_read(self, day: str) -> bool:
        """Najnowszy raport obejrzany w oknie Tide: przeczytany jak po Open(). False — to nie
        najnowsza doba albo już był przeczytany."""
        if not report_data.valid_day(day):
            return False
        return self._mark_opened(os.path.join(self.root, day, "index.html"))

    def open(self) -> bool:
        """Najnowszy raport w przeglądarce (D-Bus Open(), KRunner); oznacza go jako przeczytany."""
        return self.open_path(self.latest())

    def open_path(self, path: str) -> bool:
        """Raport ze ścieżki (np. z powiadomienia). Przeczytany zostaje tylko wtedy, gdy to
        najnowszy raport — „Otwórz” w starym powiadomieniu nie gasi kropki przy nowszym."""
        if not path or self.opener is None:
            return False
        try:
            ok = bool(self.opener(path))
        except Exception as exc:  # otwarcie przeglądarki nie może położyć usługi
            print(f"tide-daemon: nie udało się otworzyć raportu: {exc}", flush=True)
            return False
        if ok:
            self._mark_opened(path)
        return ok

    def _mark_opened(self, path: str) -> bool:
        # Doba strony (index.html albo strona projektu) musi być dobą najnowszego raportu.
        day = schedule.day_of(path)
        if not day or day != schedule.day_of(self.latest()) \
                or os.path.dirname(os.path.dirname(os.path.abspath(path))) != os.path.abspath(self.root):
            return False
        state = schedule.load_state(self.state_file)
        if not schedule.mark_opened(state, day):
            return False
        try:
            schedule.save_state(self.state_file, state)
        except OSError as exc:
            print(f"tide-daemon: raport: nie zapisano stanu: {exc}", flush=True)
            return False
        self.changed.emit()
        return True

    def check(self, at_login: bool = False) -> bool:
        """Raport z terminu, jeśli należny. True, gdy właśnie ruszył."""
        if self.running:
            return False
        now = self.clock()
        if not schedule.due(now, schedule.load_state(self.state_file), self.settings, at_login):
            return False
        self._start(scheduled=True)
        return True

    def generate(self, reply=None) -> None:
        """Raport na żądanie (D-Bus Generate): bieżąca doba, bez powiadomienia. reply(ścieżka)."""
        if reply is not None:
            self._waiting.append(reply)
        if not self.running:
            self._start(scheduled=False)

    # --- wnętrze ---------------------------------------------------------------------------

    def _start(self, scheduled: bool, live: bool = False) -> None:
        now = self.clock()
        state = schedule.load_state(self.state_file)
        day = schedule.report_day(now, self.settings).isoformat()
        start, end = schedule.period(now, state, day)
        sessions = list(self.sessions() or [])
        trees = self.worktrees()
        notes = self._notes_of(day)
        context = {"scheduled": scheduled, "day": day, "start": start, "end": end, "live": live}
        self.running = True

        def work():
            palette = reports.load_palette(self.palette_paths)
            index, report = reports.generate(
                self.root, day, start, end, palette=palette, workspace=self.workspace,
                claude_root=self.claude_root, codex_root=self.codex_root, live=sessions, worktrees=trees,
                fetch_limits=self.fetch_limits, max_depth=self.max_depth,
                coordination_cache=self.coordination_cache, summary=self.summary,
                summary_model=self.settings.summary_model, tracker=self.tracker, notes=notes,
                cancel=self._cancel)
            schedule.apply_retention(self.root, schedule.report_day(end, self.settings), self.settings.keep_days)
            return index, report

        future = self._pool.submit(work)
        future.add_done_callback(lambda f: self._finished.emit(context, f))

    def _deliver(self, context: dict, future) -> None:
        self.running = False
        self.live.ran(context["end"])   # zdarzenia po starcie składania czekają na następne odświeżenie
        try:
            index, report = future.result()
        except Exception as exc:  # raport nie może położyć usługi
            print(f"tide-daemon: raport: {exc!r}", flush=True)
            index, report = "", None
        self._arm_live()
        waiting, self._waiting = self._waiting, []
        if report is not None:
            state = schedule.load_state(self.state_file)
            state.day, state.start, state.end = context["day"], context["start"], context["end"]
            state.projects, state.waiting = len(report.repos), len(report.waiting)
            notify = context["scheduled"] and schedule.should_notify(state, context["day"])
            if notify:
                state.notified = context["day"]
            try:
                schedule.save_state(self.state_file, state)
            except OSError as exc:
                print(f"tide-daemon: raport: nie zapisano stanu: {exc}", flush=True)
            calls = (getattr(report, "standup", None) or {}).get("calls", 0)
            print(f"tide-daemon: raport {context['day']}{' (na żywo)' if context.get('live') else ''}: {index} "
                  f"({len(report.active)} z ruchem, wywołań modelu: {calls})", flush=True)
            if notify and self.notifier is not None:
                self.notifier.notify(*notification_text(report), index)
            self.ready.emit(index)
            self.changed.emit()
        for reply in waiting:  # także prośby, które przyszły w trakcie składania
            reply(index)


def notification_text(report) -> tuple[str, str]:
    """(tytuł, treść) powiadomienia."""
    n = len(report.repos)  # projekty-repozytoria; sesje spoza nich liczą się niżej
    if n:
        summary = f"Raport poranny gotowy — {render.count(n, 'projekt', 'projekty', 'projektów')}"
    else:
        summary = "Raport poranny gotowy — bez ruchu w projektach"
    commits = sum(p.commits for p in report.active)
    sessions = sum(len(p.sessions) for p in report.active)
    bits = [render.count(commits, "commit", "commity", "commitów"),
            render.count(sessions, "sesja agenta", "sesje agentów", "sesji agentów")]
    waiting = len(report.waiting)
    if waiting:
        bits.append(f"{waiting} czeka na ciebie")
    return summary, " · ".join(bits)


def report_root(override: str | None = None) -> str:
    return os.path.abspath(override) if override else schedule.reports_root()
