"""Sonda procesów poza wątkiem głównym (K-98 #4, PT-345).

`processes.probe` czyta całe /proc (setki procesów, a u agentów także katalogi `fd`), więc na wątku głównym
wstrzymywała odpowiedzi D-Bus na czas każdego ticku monitora sesji. Tu sonda biegnie w jednym, stałym wątku
`tide-process-probe`:

- jedna sonda naraz; prośby w trakcie sondy zlewają się w jedną następną (bez kolejki i bez nowych wątków),
- `request()` zwraca numer sondy, która ZACZNIE SIĘ po wywołaniu — wynik o tym numerze lub wyższym jest nowszy
  niż prośba (akcje użytkownika: aktywacja, otwarcie wątku, Meta+A),
- wynik (`ProbeResult`) jest niezmiennym obiektem podmienianym jednym przypisaniem: odbiorca widzi starą albo
  nową migawkę, nigdy częściowo zapisaną listę,
- wyjątek sondy to wynik z `error` (bez tabeli); następna prośba uruchamia kolejną sondę,
- zawieszona sonda trzyma tylko swój wątek: odczyty działają na ostatnim wyniku, prośby czekają na jeden
  następny przebieg, a stan (`status`) mówi, od kiedy nie ma świeżego wyniku.

Wynik sondy jest wyłącznie prezentacją (lista sesji, okna do aktywacji). Żadna decyzja przepływu karty
(start kroku, odzyskiwanie, zgoda) go nie czyta — te czytają świeży, pełny dziennik w kolejce mutacji.
"""
import threading
import time
from dataclasses import dataclass

THREAD_NAME = "tide-process-probe"
_UNSET = object()


@dataclass(frozen=True)
class ProbeResult:
    seq: int                 # numer sondy (rosnący)
    table: object            # ProcessTable albo None, gdy sonda rzuciła wyjątek
    started: float           # chwila startu sondy (zegar monotoniczny)
    finished: float          # chwila końca sondy (zegar monotoniczny)
    error: str = ""


class ProcessProber:
    """Jeden wątek sondy; `probe()` woła wyłącznie ten wątek. `delivered(result)` — powiadomienie z wątku sondy
    (np. emisja sygnału Qt do wątku głównego); jego wyjątek nie zatrzymuje sondy."""

    def __init__(self, probe, delivered=None, monotonic=time.monotonic):
        self._probe = probe
        self._delivered = delivered
        self._monotonic = monotonic
        self._cond = threading.Condition()
        self._wanted = 0             # najwyższy numer sondy, o który ktoś prosi
        self._started = 0            # numer ostatnio rozpoczętej sondy
        self._running_since = None   # start trwającej sondy (monotoniczny) albo None
        self._last = None            # ostatni wynik (także z błędem)
        self._good = None            # ostatni wynik z tabelą
        self._thread = None
        self._stopped = False

    # --- prośby i wyniki -------------------------------------------------------------------

    def request(self) -> int:
        """Prosi o sondę rozpoczętą po tym wywołaniu; zwraca jej numer. Nie czeka."""
        with self._cond:
            ticket = self._started + 1
            if self._stopped:
                return ticket
            self._wanted = max(self._wanted, ticket)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name=THREAD_NAME, daemon=True)
                self._thread.start()
            self._cond.notify_all()
            return ticket

    def wait(self, ticket: int, timeout: float):
        """Wynik sondy o numerze ≥ `ticket` albo None po `timeout` s (jawne, krótkie czekanie akcji użytkownika)."""
        with self._cond:
            self._cond.wait_for(lambda: self._stopped or (self._last is not None and self._last.seq >= ticket),
                                timeout=max(0.0, timeout))
            last = self._last
        return last if last is not None and last.seq >= ticket else None

    @property
    def latest(self):
        """Ostatni wynik z tabelą procesów albo None (przed pierwszym udanym wynikiem)."""
        return self._good

    @property
    def last(self):
        """Ostatni wynik, także z błędem."""
        return self._last

    def running_since(self):
        with self._cond:
            return self._running_since

    def status(self, stale_after: float, good=_UNSET, last=_UNSET) -> dict:
        """Jawny stan migawki procesów: `none` (brak danych), `fresh`, `stale` (wynik starszy niż `stale_after` s —
        np. zawieszona sonda) albo `error` (ostatnia sonda rzuciła wyjątek); `ageS` — wiek ostatniego dobrego wyniku.
        `good`/`last` — wyniki już przyjęte przez odbiorcę (np. opublikowane w wątku głównym); domyślnie wyniki
        sondy z jej wątku."""
        with self._cond:
            running = self._running_since
            good = self._good if good is _UNSET else good
            last = self._last if last is _UNSET else last
        now = self._monotonic()
        age = None if good is None else max(0.0, now - good.finished)
        if last is not None and last.error:
            state = "error"
        elif good is None:
            state = "none"
        elif age > stale_after:
            state = "stale"
        else:
            state = "fresh"
        return {"state": state, "ageS": None if age is None else round(age, 3),
                "seq": good.seq if good is not None else 0,
                "probingS": None if running is None else round(max(0.0, now - running), 3),
                "error": last.error if last is not None else ""}

    def stop(self, timeout: float = 1.0) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)     # zawieszona sonda: wątek demona, nie czekamy dłużej

    # --- wątek sondy -----------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._stopped or self._wanted > self._started)
                if self._stopped:
                    return
                self._started += 1
                seq = self._started
                began = self._running_since = self._monotonic()
            table, error = None, ""
            try:
                table = self._probe()
            except Exception as exc:  # sonda nie może zatrzymać wątku ani usługi
                error = repr(exc)
                print(f"tide-daemon: sonda procesów: {error}", flush=True)
            result = ProbeResult(seq, table, began, self._monotonic(), error)
            with self._cond:
                self._running_since = None
                self._last = result
                if not error:
                    self._good = result
                self._cond.notify_all()
            if self._delivered is not None:
                try:
                    self._delivered(result)
                except Exception as exc:
                    print(f"tide-daemon: wynik sondy procesów: {exc!r}", flush=True)
