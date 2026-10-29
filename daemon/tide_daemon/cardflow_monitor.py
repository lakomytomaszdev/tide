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
"""
import json
from concurrent.futures import ThreadPoolExecutor

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
        self._ticking = False
        self._timer = QTimer(self, interval=poll_ms)
        self._timer.timeout.connect(self.tick)
        self._done.connect(self._deliver)

    def start(self) -> None:
        self.tick()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    # --- odczyt dla BoardMonitor -----------------------------------------------------------------

    def has_journal(self, card_id: str) -> bool:
        return self.engine.has_journal(card_id)

    def derived(self, card: dict):
        return self.engine.derived(card)

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

    def _action(self, name: str, work, reply) -> None:
        def answer(future):
            try:
                result = future.result()
            except FlowError as exc:
                result = _failure(exc.code, str(exc))
            except Exception as exc:
                print(f"tide-daemon: przepływ karty: {name}: {exc!r}", flush=True)
                result = _failure("internal", "błąd usługi — szczegóły w dzienniku (journalctl --user -u tide-daemon)")
            reply(json.dumps(result, ensure_ascii=False))
        if not self._submit(work, answer):
            reply(json.dumps(_failure("unavailable", "usługa się zamyka"), ensure_ascii=False))

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

    def _submit(self, work, then) -> bool:
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
