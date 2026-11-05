"""Przepływ karty w usłudze (PT-58): /Board StartCard, zegar kroków w tle i commity dziennika kart.

- wszystko, co dotyka gita projektu, procesów kroków albo trwa dłużej (zlecenie, zatrzymanie kroku, scalenie), idzie
  w jednym wątku roboczym (cardflow/engine.py) — po kolei; odpowiedź D-Bus na StartCard przychodzi później
  (opóźniona odpowiedź jak w CardDocs), a usługa w tym czasie odpowiada na inne wywołania,
- co `poll_ms` (domyślnie 5 s, $TIDE_FLOW_POLL_MS w testach) `engine.tick()`: koniec kroków, strażnik, scalenia,
  dziennik cofnięty z zewnątrz; kolejny tick nie startuje, dopóki poprzedni trwa,
- zmiany dziennika (i wyjście kroków) z jednej chwili to jeden commit repozytorium tablicy „K-12: card flow”, w wątku
  głównym (BoardRepo nie jest wielowątkowy); BoardMonitor.sync() najpierw commituje dziennik pod tą samą blokadą,
  więc zdarzenia nie trafiają do „sync external changes”,
- zatrzymanie usługi nie zatrzymuje kroków (własne jednostki systemd); po starcie tick podpina się pod nie z dziennika.
- odczyt hostingu PR/MR korzysta z osobnej, ograniczonej puli na lokalnej migawce; nie zatrzymuje StartCard ani tick.
"""
import json
from concurrent.futures import Future, ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .cardflow.engine import FlowError

POLL_MS = 5_000


class CardFlowMonitor(QObject):
    _done = pyqtSignal(object, object)   # (dalszy ciąg w wątku głównym, future)

    def __init__(self, engine, board=None, poll_ms: int = POLL_MS, notifier=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.board = board                 # BoardMonitor — publish() po zmianie dziennika
        self.notifier = notifier           # funkcja({card, ticket, result, detail}) w wątku głównym
        self.lock = engine.journal.lock
        self._pool: ThreadPoolExecutor | None = None
        self._hosting_pool: ThreadPoolExecutor | None = None
        self._hosting_pending = 0
        self._stopped = False
        self._ticking = False
        self._timer = QTimer(self, interval=poll_ms)
        self._timer.timeout.connect(self.tick)
        self._done.connect(self._deliver)

    def start(self) -> None:
        self.tick()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._stopped = True
        if self._hosting_pool is not None:
            self._hosting_pool.shutdown(wait=False, cancel_futures=True)
            self._hosting_pool = None
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    # --- odczyt dla BoardMonitor -----------------------------------------------------------------

    def has_journal(self, card_id: str) -> bool:
        return self.engine.has_journal(card_id)

    def derived(self, card: dict):
        return self.engine.derived(card)

    def card_base(self, card_id: str) -> dict:
        return self.engine.card_base(card_id)

    def tracker_tickets(self, card):
        return self.engine.tracker_tickets(card)

    def warnings(self) -> list:
        return self.engine.warnings()

    # --- akcje -----------------------------------------------------------------------------------

    def start_card(self, card_id: str, request: str, reply) -> None:
        """StartCard(id, json): reply(json) po zleceniu (w wątku roboczym) — {ok, ticket, …} albo {ok: false, error, message}."""
        try:
            data = json.loads(request or "")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            reply(json.dumps(_failure("bad-request", "oczekiwano obiektu JSON"), ensure_ascii=False))
            return

        def answer(future):
            try:
                result = future.result()
            except FlowError as exc:
                result = _failure(exc.code, str(exc))
            except Exception as exc:  # błąd w kodzie nie może położyć usługi
                print(f"tide-daemon: przepływ karty: StartCard: {exc!r}", flush=True)
                result = _failure("internal", "błąd usługi — szczegóły w dzienniku (journalctl --user -u tide-daemon)")
            reply(json.dumps(result, ensure_ascii=False))

        if not self._submit(lambda: self.engine.start(card_id, data), answer):
            reply(json.dumps(_failure("unavailable", "usługa się zamyka"), ensure_ascii=False))

    def enter_step(self, card_id: str, step_id: str, reply) -> None:
        self._action("EnterStep", lambda: self.engine.enter(card_id, step_id), reply)

    def abandon_step(self, card_id: str, step_id: str, reply) -> None:
        self._action("AbandonStep", lambda: self.engine.abandon(card_id, step_id), reply)

    def _action(self, name: str, work, reply, *, submit=None) -> None:
        def answer(future):
            try:
                result = future.result()
            except FlowError as exc:
                result = _failure(exc.code, str(exc))
            except Exception as exc:
                print(f"tide-daemon: przepływ karty: {name}: {exc!r}", flush=True)
                result = _failure("internal", "błąd usługi — szczegóły w dzienniku (journalctl --user -u tide-daemon)")
            reply(json.dumps(result, ensure_ascii=False))
        if not (submit or self._submit)(work, answer):
            reply(json.dumps(_failure("unavailable", "usługa się zamyka"), ensure_ascii=False))

    def set_paused(self, card_id: str, paused: bool, reply) -> None:
        self._action("SetCardPaused", lambda: self.engine.set_paused(card_id, paused), reply)

    def get_settings(self, reply) -> None:
        self._action("GetCardFlowSettings", self.engine.get_flow_settings, reply)

    def set_settings(self, request: str, reply) -> None:
        try:
            data = json.loads(request or "")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            reply(json.dumps(_failure("bad-request", "oczekiwano obiektu JSON"), ensure_ascii=False))
            return
        self._action("SetCardFlowSettings", lambda: self.engine.set_flow_settings(data), reply)

    def moved(self, card_id: str, column: str, shown: str = "", move_id: str = "") -> None:
        self._submit(lambda: self.engine.moved(card_id, column, shown, move_id), _report("MoveCard"))

    def resolve_review(self, card_id: str, choice: str, reply) -> None:
        """Odpowiedź na pytanie po limicie rund review, zapisana w dzienniku."""
        def answer(future):
            try:
                result = future.result()
            except FlowError as exc:
                result = _failure(exc.code, str(exc))
            except Exception as exc:
                print(f"tide-daemon: przepływ karty: ResolveCardReview: {exc!r}", flush=True)
                result = _failure("internal", "błąd usługi — szczegóły w dzienniku")
            reply(json.dumps(result, ensure_ascii=False))

        if not self._submit(lambda: self.engine.resolve_review(card_id, choice), answer):
            reply(json.dumps(_failure("unavailable", "usługa się zamyka"), ensure_ascii=False))

    def resolve_step(self, card_id: str, request: str, reply) -> None:
        """Odpowiedź, zgoda albo ponowienie kroku karty przez dziennik."""
        try:
            data = json.loads(request or "")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            reply(json.dumps(_failure("bad-request", "oczekiwano obiektu JSON"), ensure_ascii=False))
            return

        def answer(future):
            try:
                result = future.result()
            except FlowError as exc:
                result = _failure(exc.code, str(exc))
            except Exception as exc:
                print(f"tide-daemon: przepływ karty: ResolveCardStep: {exc!r}", flush=True)
                result = _failure("internal", "błąd usługi — szczegóły w dzienniku")
            reply(json.dumps(result, ensure_ascii=False))

        if not self._submit(lambda: self.engine.resolve_step(card_id, data), answer):
            reply(json.dumps(_failure("unavailable", "usługa się zamyka"), ensure_ascii=False))

    def preview_step_fault(self, card_id: str, request: str, reply) -> None:
        """PreviewStepFault: podgląd karty z błędem kroku bez zapisu (PT-125)."""
        self._json_action("PreviewStepFault", request, lambda data: self.engine.preview_step_fault(card_id, data),
                          reply)

    def report_step_fault(self, card_id: str, request: str, reply) -> None:
        """ReportStepFault: karta z błędem w Backlogu plasma-tide (albo nowe wystąpienie) i ponowienie kroku."""
        self._json_action("ReportStepFault", request, lambda data: self.engine.report_step_fault(card_id, data),
                          reply)

    def _json_action(self, name: str, request: str, work, reply, *, submit=None) -> None:
        try:
            data = json.loads(request or "")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            reply(json.dumps(_failure("bad-request", "oczekiwano obiektu JSON"), ensure_ascii=False))
            return
        self._action(name, lambda: work(data), reply, submit=submit)

    def edit_plan(self, card_id: str, request: str, reply) -> None:
        def work():
            try:
                data = json.loads(request)
            except (ValueError, TypeError):
                raise FlowError("bad-request", "zmiana planu musi być obiektem JSON") from None
            if not isinstance(data, dict):
                raise FlowError("bad-request", "zmiana planu musi być obiektem JSON")
            return self.engine.edit_plan(card_id, data)

        def answer(future):
            try:
                result = future.result()
            except FlowError as exc:
                result = _failure(exc.code, str(exc))
            except Exception as exc:
                print(f"tide-daemon: EditCardPlan: {exc!r}", flush=True)
                result = _failure("internal", "błąd usługi — szczegóły w dzienniku")
            reply(json.dumps(result, ensure_ascii=False))

        if not self._submit(work, answer):
            reply(json.dumps(_failure("unavailable", "usługa się zamyka"), ensure_ascii=False))

    def approve_plan(self, card_id: str, reply) -> None:
        """Zatwierdzenie planu z panelu karty, w tym samym szeregowym wątku co kroki."""
        def answer(future):
            try:
                result = future.result()
            except FlowError as exc:
                result = _failure(exc.code, str(exc))
            except Exception as exc:
                print(f"tide-daemon: przepływ karty: ApproveCardPlan: {exc!r}", flush=True)
                result = _failure("internal", "błąd usługi — szczegóły w dzienniku")
            reply(json.dumps(result, ensure_ascii=False))

        if not self._submit(lambda: self.engine.approve_plan(card_id), answer):
            reply(json.dumps(_failure("unavailable", "usługa się zamyka"), ensure_ascii=False))

    def card_diff(self, card_id: str, reply) -> None:
        self._action("CardBranchDiff", lambda: self.engine.card_diff(card_id), reply)

    def merge_card_to_main(self, card_id: str, reply) -> None:
        self._action("MergeCardToMain", lambda: self.engine.merge_card_to_main(card_id), reply)

    def preview_card_merge(self, card_id: str, request: str, reply) -> None:
        self._json_action("PreviewCardMerge", request, lambda data: self.engine.preview_card_merge(card_id, data), reply)

    def list_card_branches(self, card_id: str, request: str, reply) -> None:
        self._json_action("ListCardBranches", request, lambda data: self.engine.list_card_branches(card_id, data), reply)

    def suggest_card_base(self, card_id: str, request: str, reply) -> None:
        self._json_action("SuggestCardBase", request,
                          lambda data: self.engine.prepare_card_base_suggestion(card_id, data), reply,
                          submit=self._submit_suggestion)

    def tick(self) -> None:
        if self._ticking:
            return
        self._ticking = True

        def after(future):
            self._ticking = False
            _report("tick")(future)

        if not self._submit(self.engine.tick, after):
            self._ticking = False

    def commit(self) -> bool:
        """Commit zmian dziennika i wyjścia kroków (wątek główny). True, gdy coś było do zapisania."""
        with self.lock:
            paths = self.engine.journal.take_dirty()
            if not paths:
                return False
            cards = sorted({p.split("/")[1] for p in paths if p.count("/") >= 2})
            error = self.engine.store.repo.commit(f"{', '.join(cards) or 'flow'}: card flow", paths)
        if error:
            print(f"tide-daemon: przepływ karty: commit dziennika czeka: {error}", flush=True)
        return True

    # --- wnętrze ---------------------------------------------------------------------------------

    def _submit_suggestion(self, work, then) -> bool:
        def prepared(future):
            if future.cancelled() or future.exception() is not None or not callable(future.result()):
                then(future)
                return
            if self._stopped or self._hosting_pending >= 2:
                rejected = Future()
                rejected.set_result(_failure("unavailable", "Odczyt hostingu zajęty lub usługa się zamyka. Wybierz bazę ręcznie."))
                then(rejected)
                return
            if self._hosting_pool is None:
                self._hosting_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tide-hosting")
            self._hosting_pending += 1

            def completed(result):
                self._hosting_pending -= 1
                then(result)

            hosted = self._hosting_pool.submit(future.result())
            hosted.add_done_callback(lambda f: self._done.emit(completed, f))

        # Migawka karty, refów i remote powstaje po kolei z akcjami przepływu.
        # Tylko zwrócony odczyt hostingu biegnie osobno, bez blokad gita/dziennika.
        return self._submit(work, prepared)

    def _submit(self, work, then) -> bool:
        if self._stopped:
            return False
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-cardflow")
        try:
            future = self._pool.submit(work)
        except RuntimeError:  # pula zamknięta (koniec usługi)
            return False
        future.add_done_callback(lambda f: self._done.emit(then, f))
        return True

    def _deliver(self, then, future) -> None:
        # Najpierw commit i nowa migawka, potem odpowiedź: kto po StartCard pyta o Snapshot(), widzi już zlecenie.
        if self.commit() and self.board is not None:
            self.board.publish()
        for notice in self.engine.take_notices():
            if self.notifier is not None:
                try:
                    self.notifier(notice)
                except Exception as exc:
                    print(f"tide-daemon: przepływ karty: powiadomienie: {exc!r}", flush=True)
        if not future.cancelled():
            then(future)


def _report(what: str):
    def check(future) -> None:
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            print(f"tide-daemon: przepływ karty: {what}: {exc!r}", flush=True)
    return check


def _failure(code: str, message: str) -> dict:
    return {"ok": False, "error": code, "message": message}
