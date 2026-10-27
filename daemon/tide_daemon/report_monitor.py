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
  albo projektu (OpenDay) i mark_read — obejrzenie najnowszego raportu w oknie gasi kropkę jak Open().
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import models, reports
from .reports import data as report_data, render, schedule


class ReportMonitor(QObject):
    ready = pyqtSignal(str)                  # ścieżka index.html po każdym raporcie
    changed = pyqtSignal()                   # nowy raport albo zmiana przeczytania (Status())
    _finished = pyqtSignal(object, object)   # (kontekst, Future) — z wątku roboczego do głównego

    def __init__(self, settings: schedule.Settings, *, root: str, state_file: str, palette_paths: list[str],
                 workspace: str, claude_root: str, codex_root: str, max_depth: int = 3,
                 sessions=lambda: [], worktrees=lambda: None, notifier=None, clock=time.time,
                 login_delay: float = schedule.LOGIN_DELAY, check_interval: float = schedule.CHECK_INTERVAL,
                 fetch_limits: bool = True, coordination_cache: str | None = None, opener=None,
                 summary: str | None = None, tracker=None, parent=None):
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
        self._cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def latest(self) -> str:
        return schedule.latest_index(self.root)

    def status(self) -> dict:
        return schedule.status(self.root, schedule.load_state(self.state_file))

    def status_json(self) -> str:
        return json.dumps(self.status(), ensure_ascii=False)

    def days_json(self) -> str:
        today = schedule.report_day(self.clock(), self.settings).isoformat()
        return json.dumps(report_data.days(self.root, schedule.load_state(self.state_file), today,
                                           self.settings.keep_days), ensure_ascii=False)

    def day_json(self, day: str) -> str:
        return json.dumps(report_data.day(self.root, day, schedule.load_state(self.state_file)), ensure_ascii=False)

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

    def _start(self, scheduled: bool) -> None:
        now = self.clock()
        state = schedule.load_state(self.state_file)
        day = schedule.report_day(now, self.settings).isoformat()
        start, end = schedule.period(now, state, day)
        live = list(self.sessions() or [])
        trees = self.worktrees()
        context = {"scheduled": scheduled, "day": day, "start": start, "end": end}
        self.running = True

        def work():
            palette = reports.load_palette(self.palette_paths)
            index, report = reports.generate(
                self.root, day, start, end, palette=palette, workspace=self.workspace,
                claude_root=self.claude_root, codex_root=self.codex_root, live=live, worktrees=trees,
                fetch_limits=self.fetch_limits, max_depth=self.max_depth,
                coordination_cache=self.coordination_cache, summary=self.summary,
                summary_model=self.settings.summary_model, tracker=self.tracker, cancel=self._cancel)
            schedule.apply_retention(self.root, schedule.report_day(end, self.settings), self.settings.keep_days)
            return index, report

        future = self._pool.submit(work)
        future.add_done_callback(lambda f: self._finished.emit(context, f))

    def _deliver(self, context: dict, future) -> None:
        self.running = False
        try:
            index, report = future.result()
        except Exception as exc:  # raport nie może położyć usługi
            print(f"tide-daemon: raport: {exc!r}", flush=True)
            index, report = "", None
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
            print(f"tide-daemon: raport {context['day']}: {index} ({len(report.active)} z ruchem)", flush=True)
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
