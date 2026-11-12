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
- K-98 #2 — odczyty niezależne od kolejki mutacji: kolejka (`tide-cardflow`) pozostaje jedynym miejscem zapisów
  przepływu. Proste odczyty `/Board` nie czekają na nią: GetCardFlowSettings czyta plik ustawień od razu,
  BoxStatus biegnie w osobnej puli odczytu (`tide-board-read`) na opublikowanej migawce BoardMonitor (bez blokady
  dziennika i Tablicy, bez zapisu stanu slotu), a migawka Tablicy (Snapshot, Panel karty) powstaje pod bramką
  `read_gate()`: bez blokady dziennika (odczyt prezentacji), z licznikiem epok akcji — w trakcie akcji z kolejki
  wątek główny oddaje ostatnią opublikowaną, spójną wersję i publikuje po jej zakończeniu. Po akcji worker bierze
  następne zadanie dopiero po doręczeniu jej wyniku (migawka ze skutkiem, commit, odpowiedź). Ciężkie podglądy Gita
  (CardBranchDiff, PreviewCardMerge, …) zachowują własny kontrakt w kolejce.
"""
import contextlib
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .cardflow.engine import FlowError
from .resources import ResourceError

POLL_MS = 5_000
DELIVER_RETRY_MS = 20   # wynik z kolejki czeka na wolną blokadę dziennika (commit i migawka przed odpowiedzią)
DELIVERY_WAIT_S = 5.0   # worker czeka najwyżej tyle na doręczenie wyniku, zanim weźmie następne zadanie


class CardFlowMonitor(QObject):
    _done = pyqtSignal(object, object)   # (dalszy ciąg w wątku głównym, future)
    _read_done = pyqtSignal(object, object)   # wynik odczytu z puli `tide-board-read` (bez commitu i publikacji)

    def __init__(self, engine, board=None, poll_ms: int = POLL_MS, notifier=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.board = board                 # BoardMonitor — publish() po zmianie dziennika
        self.notifier = notifier           # funkcja({card, ticket, result, detail}) w wątku głównym
        self.lock = engine.journal.lock
        self._pool: ThreadPoolExecutor | None = None
        self._read_pool: ThreadPoolExecutor | None = None
        self._hosting_pool: ThreadPoolExecutor | None = None
        self._hosting_pending = 0
        self._stopped = False
        self._ticking = False
        self._timer = QTimer(self, interval=poll_ms)
        self._timer.timeout.connect(self.tick)
        self._done.connect(self._deliver)
        self._read_done.connect(self._deliver_read)
        self._handshakes: set = set()     # zdarzenia „wynik doręczony”, na które czeka worker (K-98 #2)
        self._owner_thread = threading.current_thread()   # wątek główny (Qt), który doręcza wyniki
        self._epoch = 0   # licznik epok akcji (K-98 #2): nieparzysty — akcja z kolejki w toku
        self._deliveries: list = []       # [dalszy ciąg, future, opublikować] — wyniki z kolejki czekające na migawkę
        self._draining = False            # `_drain` w toku (bez ponownego wejścia z odbiorcy sygnału)
        self._retry = QTimer(self, singleShot=True, interval=DELIVER_RETRY_MS)
        self._retry.timeout.connect(self._drain)

    def start(self) -> None:
        self.tick()
        self._timer.start()

    def stop(self) -> None:
        if self._stopped:
            return
        self._timer.stop()
        self._retry.stop()
        self._stopped = True
        for delivered in list(self._handshakes):   # worker nie czeka na doręczenie, którego już nie będzie
            delivered.set()
        if self._hosting_pool is not None:
            self._hosting_pool.shutdown(wait=False, cancel_futures=True)
            self._hosting_pool = None
        if self._read_pool is not None:
            self._read_pool.shutdown(wait=False, cancel_futures=True)
            self._read_pool = None
        if self._pool is not None:
            # Bieżąca operacja zostawia trwały zamiar/wynik; niezależne jednostki nie są zatrzymywane.
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None
        self.commit()  # callback Qt może już nie dotrzeć po app.exec()

    # --- odczyt dla BoardMonitor -----------------------------------------------------------------

    @contextlib.contextmanager
    def read_gate(self):
        """Bramka publikacji migawki (K-98 #2), bez blokady dziennika. Razem z wczytaniem Tablicy bez czekania
        (`BoardStore.without_waiting` w BoardMonitor) publikacja nie czeka na mutację.

        Bilet jest fałszywy, gdy trwa akcja z kolejki (licznik epok nieparzysty): migawka nie pokazuje akcji
        w połowie, odczyt dostaje poprzednią wersję. W środku dziennik czytamy w trybie prezentacji (bez blokady,
        pełne zdarzenia). `still_valid()` po obliczeniu: fałsz, gdy w trakcie odczytu zaczęła się albo skończyła
        akcja — wynik odrzucamy i publikujemy ponownie. Tick nie jest akcją: jego zapisy widać zapis po zapisie,
        jak dotąd."""
        ticket = _ReadTicket(self)
        if not ticket:
            yield ticket
            return
        with self.engine.journal.presentation():
            yield ticket

    def box_wait(self, card_id: str) -> str:
        return self.engine.box_wait(card_id)

    def has_journal(self, card_id: str) -> bool:
        return self.engine.has_journal(card_id)

    def derived(self, card: dict, read=None):
        """`read`: kontekst odczytu Tablicy jednego odświeżenia BoardMonitor (K-98 #1) albo None — świeży odczyt."""
        return self.engine.derived(card, read)

    def card_base(self, card_id: str) -> dict:
        return self.engine.card_base(card_id)

    def tracker_tickets(self, card):
        return self.engine.tracker_tickets(card)

    def warnings(self) -> list:
        return self.engine.warnings()

    def queue_view(self):
        """Pole `queue` migawki (K-39 #4): gotowy słownik z ostatniego ticku silnika albo None."""
        return self.engine.queue_view()

    # --- akcje -----------------------------------------------------------------------------------

    def issue_call(self, service, method, args, reply):
        # Go i retry biegną w tym samym wątku co StartCard i tick, poza Qt.
        def answer(future):
            text = future.result()
            if method in ("AcceptIssue", "RetryIssueStart") and self.board is not None:
                self.board.publish()
            reply(text)
        if not self._submit(lambda: service.call(method, *args), answer):
            reply(json.dumps({"ok": False, "apiVersion": 1, "error": "unavailable",
                              "message": "Usługa się zamyka."}, ensure_ascii=False))

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
            except (FlowError, ResourceError) as exc:
                result = _failure(exc.code, str(exc))
            except Exception as exc:  # błąd w kodzie nie może położyć usługi
                print(f"tide-daemon: przepływ karty: StartCard: {exc!r}", flush=True)
                result = _failure("internal", "błąd usługi — szczegóły w dzienniku (journalctl --user -u tide-daemon)")
            reply(json.dumps(result, ensure_ascii=False))

        if not self._submit(lambda: self.engine.start(card_id, data), answer):
            reply(json.dumps(_failure("unavailable", "usługa się zamyka"), ensure_ascii=False))

    def enter_step(self, card_id: str, step_id: str, reply) -> None:
        self._action("EnterStep", lambda: self.engine.enter(card_id, step_id), reply)

    def talk_to_caretaker(self, card_id: str, request: str, reply) -> None:
        self._json_action("TalkToCaretaker", request,
                          lambda data: self.engine.talk_to_caretaker(card_id, data), reply)

    def abandon_step(self, card_id: str, step_id: str, reply) -> None:
        self._action("AbandonStep", lambda: self.engine.abandon(card_id, step_id), reply)

    def _action(self, name: str, work, reply, *, submit=None) -> None:
        def answer(future):
            try:
                result = future.result()
            except (FlowError, ResourceError) as exc:
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
        # Odczyt pliku ustawień (zapis jest atomowy) od razu na wątku głównym — nie w kolejce za `engine.tick`,
        # który po K-38 trwał dziesiątki sekund (09.11: GetCardFlowSettings 14–46 s, Tablica startowała długo).
        def immediate(work, then) -> bool:
            future = Future()
            try:
                future.set_result(work())
            except Exception as exc:
                future.set_exception(exc)
            then(future)
            return True
        self._action("GetCardFlowSettings", self.engine.get_flow_settings, reply, submit=immediate)

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
            except (FlowError, ResourceError) as exc:
                result = {**_failure(exc.code, str(exc)), **getattr(exc, "data", {})}
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
            except (FlowError, ResourceError) as exc:
                result = _failure(exc.code, str(exc))
            except Exception as exc:
                print(f"tide-daemon: przepływ karty: ResolveCardStep: {exc!r}", flush=True)
                result = _failure("internal", "błąd usługi — szczegóły w dzienniku")
            reply(json.dumps(result, ensure_ascii=False))

        if not self._submit(lambda: self.engine.resolve_step(card_id, data), answer):
            reply(json.dumps(_failure("unavailable", "usługa się zamyka"), ensure_ascii=False))

    def add_step_note(self, card_id: str, request: str, reply) -> None:
        self._json_action("AddCardStepNote", request, lambda data: self.engine.add_step_note(card_id, data), reply)

    def resolve_caretaker(self, card_id: str, request: str, reply) -> None:
        """Decyzja o problemie karty bez własnego kroku."""
        self._json_action("ResolveCardCaretaker", request,
                          lambda data: self.engine.resolve_caretaker(card_id, data), reply)

    def preview_step_fault(self, card_id: str, request: str, reply) -> None:
        """PreviewStepFault: podgląd karty z błędem kroku bez zapisu (PT-125)."""
        self._json_action("PreviewStepFault", request, lambda data: self.engine.preview_step_fault(card_id, data),
                          reply)

    def revoke_ticket_consent(self, card_id: str, request: str, reply) -> None:
        self._json_action("RevokeTicketConsent", request,
                          lambda data: self.engine.revoke_ticket_consent(card_id, data), reply)

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
            except (FlowError, ResourceError) as exc:
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
            except (FlowError, ResourceError) as exc:
                result = _failure(exc.code, str(exc))
            except Exception as exc:
                print(f"tide-daemon: przepływ karty: ApproveCardPlan: {exc!r}", flush=True)
                result = _failure("internal", "błąd usługi — szczegóły w dzienniku")
            reply(json.dumps(result, ensure_ascii=False))

        if not self._submit(lambda: self.engine.approve_plan(card_id), answer):
            reply(json.dumps(_failure("unavailable", "usługa się zamyka"), ensure_ascii=False))

    def approve_environment(self, card_id: str, yaml_text: str, only_plan: bool, reply) -> None:
        """ApproveCardEnvironment (PT-172): walidacja i zapis w tym samym szeregowym wątku co kroki; budowa w tle."""
        self._action("ApproveCardEnvironment", lambda: self.engine.approve_environment(card_id, yaml_text, only_plan), reply)

    def box_status(self, slug: str, reply, card: str = "") -> None:
        """BoxStatus/BoxCardStatus (K-98 #2): odczyt poza kolejką mutacji, w puli `tide-board-read`, na opublikowanej
        migawce Tablicy. Przed pierwszą publikacją — jawne „przygotowywanie” zamiast stanu gotowego pudełka."""
        source = getattr(self.board, "published", None)
        if source is None:   # silnik bez BoardMonitor (testy): świeży odczyt Tablicy, nadal poza kolejką
            work = lambda: self.engine.box_status(slug, card)
        else:
            published = source()
            if published is None:
                from .cardflow import envconsent
                reply(json.dumps(envconsent.preparing_status(), ensure_ascii=False))
                return
            work = lambda: self.engine.box_status(slug, card, published)
        self._action("BoxStatus", work, reply, submit=self._submit_read)

    def stop_box(self, slug: str, card: str, reply) -> None:
        self._action("StopBox", lambda: self.engine.stop_box(slug, card), reply)

    def card_diff(self, card_id: str, reply, target_ref: str = "") -> None:
        self._action("CardBranchDiff", lambda: self.engine.card_diff(card_id, target_ref), reply)

    def merge_card_to_main(self, card_id: str, reply) -> None:
        self._action("MergeCardToMain", lambda: self.engine.merge_card_to_main(card_id), reply)

    def preview_card_merge(self, card_id: str, request: str, reply) -> None:
        self._json_action("PreviewCardMerge", request, lambda data: self.engine.preview_card_merge(card_id, data), reply)

    def merge_card(self, card_id: str, request: str, reply) -> None:
        self._json_action("MergeCard", request, lambda data: self.engine.merge_card(card_id, data), reply)

    def preview_card_base_change(self, card_id: str, request: str, reply) -> None:
        self._json_action("PreviewCardBaseChange", request,
                          lambda data: self.engine.preview_card_base_change(card_id, data), reply)

    def request_card_base_change(self, card_id: str, request: str, reply) -> None:
        self._json_action("RequestCardBaseChange", request,
                          lambda data: self.engine.request_card_base_change(card_id, data), reply)

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

        if not self._submit(self.engine.tick, after, whole=False):
            self._ticking = False

    def commit(self, wait: bool = True):
        """Commit zmian dziennika i wyjścia kroków (wątek główny). True, gdy coś było do zapisania.
        `wait=False` (K-98 #2, wątek główny po wyniku z kolejki): gdy blokadę dziennika trzyma kolejna mutacja,
        nic nie robi i zwraca None — zmiany zostają do commitu po jej zakończeniu, a wątek główny nie czeka."""
        if not self.lock.acquire(blocking=wait):
            return None
        try:
            paths = self.engine.journal.take_dirty()
            if not paths:
                return False
            cards = sorted({p.split("/")[1] for p in paths if p.count("/") >= 2})
            error = self.engine.store.repo.commit(f"{', '.join(cards) or 'flow'}: card flow", paths)
        finally:
            self.lock.release()
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

    def _submit(self, work, then, *, whole: bool = True) -> bool:
        """Zadanie kolejki mutacji. K-98 #2: akcja (`whole`) podbija licznik epok na czas wykonania, więc
        publikacja migawki (bramka `read_gate`) widzi ją całą albo wcale — np. `card.moved` i `card.paused`
        z jednego ruchu karty — a worker czeka na doręczenie jej wyniku przed następnym zadaniem. Tick
        (`whole=False`) nie jest akcją: jego zapisy widać zapis po zapisie, każdy pełny."""
        if self._stopped:
            return False
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-cardflow")
        def atomic():
            self._epoch += 1          # nieparzysty: akcja w toku (czyta go bramka publikacji)
            try:
                return work()
            finally:
                self._epoch += 1
        try:
            future = self._pool.submit(atomic if whole else work)
        except RuntimeError:  # pula zamknięta (koniec usługi)
            return False
        delivered = threading.Event()
        future.tide_delivered = delivered
        self._handshakes.add(delivered)

        def done(f):
            # Wątek workera, zanim weźmie następne zadanie: wynik trafia do wątku głównego, a worker czeka (bez
            # blokady dziennika, najwyżej DELIVERY_WAIT_S), aż zostanie zapisany, opublikowany i odesłany. Kolejna
            # mutacja nie rusza więc przed migawką ze skutkiem poprzedniej — wątek główny nie czeka na kolejkę.
            self._done.emit(then, f)
            # Tylko w wątku workera: wywołanie w wątku głównym (zadanie skończone przed podpięciem, anulowanie przy
            # zatrzymaniu) nie może czekać na doręczenie, które sam ma wykonać.
            if whole and not self._stopped and threading.current_thread() is not self._owner_thread:
                delivered.wait(DELIVERY_WAIT_S)
            self._handshakes.discard(delivered)
        future.add_done_callback(done)
        return True

    def _submit_read(self, work, then) -> bool:
        """Odczyt poza kolejką mutacji (K-98 #2): osobna pula, wynik w wątku głównym bez commitu i publikacji."""
        if self._stopped:
            return False
        if self._read_pool is None:
            self._read_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tide-board-read")
        try:
            future = self._read_pool.submit(work)
        except RuntimeError:  # pula zamknięta (koniec usługi)
            return False
        future.add_done_callback(lambda f: self._read_done.emit(then, f))
        return True

    def _deliver_read(self, then, future) -> None:
        if self._stopped or future.cancelled():
            return
        then(future)

    def _deliver(self, then, future) -> None:
        if self._stopped:
            return
        self._deliveries.append([then, future, False, False])
        self._drain()

    def _drain(self) -> None:
        """Wyniki z kolejki po kolei. Najpierw commit i nowa migawka, potem odpowiedź: kto po StartCard pyta
        o Snapshot(), widzi już zlecenie. K-98 #2: gdy kolejna mutacja trzyma już blokadę dziennika, wątek główny
        nie czeka na nią — wynik (i następne za nim) czeka, a próba wraca co DELIVER_RETRY_MS. Bez ponownego
        wejścia: odbiorca `Changed` albo odpowiedzi, który synchronicznie woła Snapshot() (→ `settle`), nie doręcza
        tego samego wyniku drugi raz — zewnętrzna pętla dokończy kolejkę."""
        if self._draining:
            return
        self._draining = True
        try:
            self._drain_items()
        finally:
            self._draining = False

    def _drain_items(self) -> None:
        while self._deliveries and not self._stopped:
            item = self._deliveries[0]       # [dalszy ciąg, future, opublikowano, zapisano commit]
            dirty = self.engine.journal.has_dirty()
            committed = self.commit(wait=False)   # None — blokadę trzyma tick; commit później
            if committed is not None:
                item[3] = True
            # Publikacja nie potrzebuje blokady: skutek akcji trafia do migawki od razu, także gdy commit czeka.
            # Po commicie publikujemy jeszcze raz (ostatni commit Tablicy w migawce).
            if (dirty or committed) and (not item[2] or committed) and self.board is not None:
                if self.board.publish() is False:
                    self._retry_later()
                    return
                item[2] = True
            if not item[3]:
                self._retry_later()
                return
            self._deliveries.pop(0)
            self._answer(*item[:2])

    def settle(self) -> None:
        """Odczyt po zapisie (K-98 #2, Snapshot): zakończone, a jeszcze niedoręczone wyniki z kolejki publikujemy
        przed odpowiedzią — bez czekania (publikacja nie bierze blokady dziennika)."""
        if self._deliveries and not self._stopped:
            self._drain()

    def _retry_later(self) -> None:
        if not self._retry.isActive():
            self._retry.start()

    def _answer(self, then, future) -> None:
        try:
            self._send(then, future)
        finally:
            getattr(future, "tide_delivered", threading.Event()).set()   # worker może wziąć następne zadanie

    def _send(self, then, future) -> None:
        for notice in self.engine.take_notices():
            if self.notifier is not None:
                try:
                    self.notifier(notice)
                except Exception as exc:
                    print(f"tide-daemon: przepływ karty: powiadomienie: {exc!r}", flush=True)
        if not future.cancelled():
            then(future)


class _ReadTicket:
    """Bilet publikacji (seqlock na liczniku epok akcji): prawdziwy, gdy żadna akcja z kolejki nie trwa."""

    def __init__(self, monitor):
        self._monitor = monitor
        self._epoch = monitor._epoch

    def __bool__(self) -> bool:
        return self._epoch % 2 == 0

    def still_valid(self) -> bool:
        return bool(self) and self._monitor._epoch == self._epoch


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
