"""Kolejka wywołań modeli lokalnych: jedno wywołanie naraz na serwer, priorytety, czekanie osobno od pracy.

Ollama na tym komputerze obsługuje jedno zapytanie naraz (OLLAMA_NUM_PARALLEL=1, karta 8 GB, jeden model
w pamięci). Bez kolejki raport poranny i opisy kart czekały na siebie wewnątrz serwera, a czas czekania
liczył się do limitu wywołania — karta dostawała „nie odpowiedział w 90 s”, choć model tylko był zajęty.

Kolejka siedzi w `models.chat` (client.py), więc każde wywołanie modelu lokalnego w usłudze przez nią
przechodzi — także przyszłe (PT-17, PT-19) — bez pamiętania o tym w każdym miejscu. Jedna na proces
(`shared()`), osobny tor na każdy serwer — po znormalizowanym adresie `scheme://host:port` (`lane_of`),
więc dwie nazwy punktu na ten sam serwer dzielą jedno miejsce:

- na torze działa najwyżej `slots` wywołań naraz (domyślnie 1; więcej np. dla Maca — później),
- kolejność: najpierw priorytet (HIGH — krótkie rzeczy, na które użytkownik patrzy: opisy kart, zapytanie
  KRunnera; LOW — ciężka praca wsadowa: raport; IDLE — praca w tle: indeks wyszukiwania), w obrębie
  priorytetu kolejność przyjścia; trwającego wywołania nic nie przerywa,
- grupowanie modeli: w obrębie tego samego priorytetu pierwszeństwo ma wywołanie tym samym modelem,
  którym serwer pracował ostatnio (bez przerzucania modeli między kartą a pamięcią), ale tylko dopóki
  najstarsze czekające nie czeka dłużej niż GROUP_HOLD_S — potem zwykła kolejność przyjścia,
- czas: czekanie ma własny limit (`wait`, od wywołującego), a limit samego wywołania (`timeout` w chat)
  biegnie dopiero od chwili, gdy wywołanie naprawdę ruszyło. Po limicie czekania ModelError z uczciwym
  powodem („kolejka: czekał 240 s — serwer zajęty: raport poranny”),
- przerwanie (`transport.Cancel`, zamykanie usługi): czekające wywołanie wychodzi z kolejki od razu,
  a trwające traci gniazdo jak dotąd (transport.py).

Następnego na torze wybiera jedno miejsce (`_choose`), pod blokadą, przy każdej zmianie stanu (dołączenie,
start, koniec, wyjście, przeniesienie) i zapisuje wybór; czekający porównują się z zapisanym wyborem,
a zmiana wyboru budzi wszystkich. Gdy grupowanie trzyma najstarsze, czekający śpią najwyżej do końca
okna grupowania i wybór jest liczony od nowa — upływ czasu też jest zmianą stanu (review PT-16).

Cel wywołania (`resolve`) ustalamy w chwili, gdy wywołanie dostaje swoją kolej, nie przy dołączeniu:
po zmianie ustawień w czasie czekania (inny `default`, inny adres) wywołanie idzie już na nowy serwer —
a gdy to inny tor, przechodzi do jego kolejki z zachowaniem kolejności przyjścia i limitu czekania.

Wywołanie biegnie w wątku wywołującego (kolejka nie ma własnych wątków): run() czeka na swoją kolej
na zmiennej warunkowej, potem woła `fn()` i zwalnia miejsce także po błędzie. Stan dla D-Bus
(`snapshot()`) — tylko punkt, rola, model i od kiedy, nigdy treść polecenia. Słuchacze (`add_listener`)
dostają powiadomienie po każdej zmianie, w wątku, który ją zrobił (models_monitor.py przenosi to do wątku Qt).
"""
from __future__ import annotations

import itertools
import threading
import time
import urllib.parse
from datetime import datetime

from .transport import ModelError

HIGH = 0        # krótkie rzeczy, na które użytkownik patrzy (opisy kart, zapytanie KRunnera)
NORMAL = 5      # domyślnie
LOW = 10        # ciężka praca wsadowa (raport poranny)
IDLE = 20       # praca w tle, gdy nic innego nie czeka (indeks wyszukiwania po znaczeniu, PT-19)
ROLE_PRIORITY = {"describe": HIGH, "summary": LOW}
ROLE_WORDS = {"card-step": "krok karty", "describe": "opisy kart", "summary": "raport poranny", "embed": "wyszukiwanie w KRunnerze"}
DEFAULT_SLOTS = 1
DEFAULT_WAIT_S = 600.0
GROUP_HOLD_S = 60.0


class Busy(ModelError):
    """Limit czekania w kolejce minął — serwer był zajęty, a nie niedostępny (wywołujący może spróbować później)."""


def priority_of(role: str | None) -> int:
    """Priorytet domyślny według roli: `describe` wysoki, `summary` niski, reszta zwykły."""
    return ROLE_PRIORITY.get(role or "", NORMAL)


def lane_of(url: str) -> str:
    """Tor kolejki dla adresu serwera: „http://127.0.0.1:11434” (małe litery, port domyślny dopisany, bez
    ścieżki). Adres, którego nie da się rozebrać, jest torem sam dla siebie; pusty — pusty napis."""
    try:
        parts = urllib.parse.urlsplit(url or "")
        port = parts.port
    except ValueError:
        return url or ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return url or ""
    host = parts.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{host}:{port or (443 if parts.scheme == 'https' else 80)}"


def seconds(value: float) -> str:
    """Sekundy do komunikatu jak w transport.py: pełne od 10 s, poniżej z jednym miejscem („0.3”, „240”)."""
    value = round(value) if value >= 10 else round(value, 1)
    return f"{value:g}"


def _iso(wall: float) -> str:
    return datetime.fromtimestamp(wall).astimezone().isoformat(timespec="seconds")


class _Item:
    __slots__ = ("seq", "lane", "endpoint", "role", "model", "host", "priority", "queued", "wall", "started")

    def __init__(self, seq, lane, endpoint, role, model, host, priority, queued, wall):
        self.seq, self.lane, self.endpoint, self.role, self.model = seq, lane, endpoint, role, model
        self.host, self.priority, self.queued = host, priority, queued
        self.wall = wall          # dołączenie do kolejki (zegar ścienny) — zostaje także po przeniesieniu toru
        self.started = wall       # start wywołania — ustawiany w _start

    def public(self, running: bool = False) -> dict:
        return {"endpoint": self.endpoint, "role": self.role, "model": self.model,
                "since": _iso(self.started if running else self.wall)}


class Queue:
    """Kolejka wywołań na tor (serwer). `slots` — ile wywołań naraz na torze (liczba albo {tor: liczba}
    z wartością domyślną DEFAULT_SLOTS dla pozostałych). `clock` — zegar monotoniczny (testy)."""

    def __init__(self, slots=DEFAULT_SLOTS, group_hold: float = GROUP_HOLD_S, wall=time.time, clock=None):
        self._slots = dict(slots) if isinstance(slots, dict) else {}
        self._default_slots = slots if isinstance(slots, int) else DEFAULT_SLOTS
        self.group_hold = group_hold
        self._wall = wall
        self._clock = clock
        self._cond = threading.Condition()
        self._waiting: dict[str, list[_Item]] = {}
        self._running: dict[str, list[_Item]] = {}
        self._chosen: dict[str, _Item] = {}
        self._step_claims: dict[str, _Item] = {}
        self._last_model: dict[str, str] = {}
        self._last_end: dict[str, float] = {}     # tor → koniec ostatniego wywołania (zegar kolejki)
        self._seq = itertools.count()
        self._listeners: list = []

    def _now(self) -> float:
        return self._clock() if self._clock is not None else time.monotonic()

    # --- wywołanie ------------------------------------------------------------------------------

    def claim_step(self, key: str, *, endpoint: str, model: str = "", recovering: bool = False):
        """Nieblokujące zajęcie toru przez krok lokalny. Raport i opisy już czekające mają pierwszeństwo.
        Zwrócony uchwyt zwalnia usługa dopiero po końcu kroku, także przy błędzie."""
        lane = lane_of(endpoint)
        with self._cond:
            if key in self._step_claims:
                return self._step_claims[key]
            if not recovering and (self._running.get(lane) or self._waiting.get(lane)):
                return None
            item = _Item(next(self._seq), lane, endpoint, "card-step", model, endpoint,
                         IDLE, self._now(), self._wall())
            self._running.setdefault(lane, []).append(item)
            self._step_claims[key] = item
            self._choose(lane)
        self._notify()
        return item

    def release_step(self, claim) -> None:
        with self._cond:
            self._step_claims = {key: item for key, item in self._step_claims.items() if item is not claim}
        self._finish(claim, ran=True)

    def run(self, fn, *, endpoint: str, lane: str | None = None, role: str = "", model: str = "",
            priority: int | None = None, wait: float | None = DEFAULT_WAIT_S, cancel=None, host: str = "",
            resolve=None):
        """Wynik `fn()` po odczekaniu swojej kolejki na torze `lane` (domyślnie nazwa punktu `endpoint`).
        `wait` — najwyżej tyle sekund w kolejce (None — bez limitu, tylko `cancel`); po nim albo po
        cancel.set() ModelError, a `fn` nie rusza. `resolve()` → (tor, punkt, model, host) — cel ustalany
        w chwili, gdy wywołanie dostaje kolej (inny tor → przejście do jego kolejki); błąd z `resolve`
        przechodzi dalej. Błąd z `fn` przechodzi dalej bez zmian; miejsce jest zwalniane zawsze."""
        queued = self._now()
        item = _Item(next(self._seq), lane or endpoint, endpoint, role or "", model or "", host or endpoint,
                     priority_of(role) if priority is None else priority, queued, self._wall())
        limit = None if wait is None else queued + max(0.0, wait)
        wake = self._wake
        registered = cancel._register(wake) if cancel is not None else False
        with self._cond:
            self._waiting.setdefault(item.lane, []).append(item)
            self._choose(item.lane)
        self._notify()
        try:
            while True:
                self._await(item, limit, cancel)
                if resolve is None:
                    break
                try:
                    target = resolve()
                except BaseException:
                    self._finish(item, ran=False)
                    raise
                if self._retarget(item, *target):
                    break
        finally:
            if registered:
                cancel._unregister(wake)
        try:
            return fn()
        finally:
            self._finish(item, ran=True)

    def _await(self, item: _Item, limit: float | None, cancel) -> None:
        """Czeka, aż `item` zostanie wybrany i ruszy (przeniesiony do trwających), albo zgłasza ModelError."""
        error = None
        with self._cond:
            while True:
                self._choose(item.lane)  # także upływ czasu (koniec okna grupowania) zmienia wybór
                if cancel is not None and cancel.is_set():
                    error = ModelError(f"Model lokalny: wywołanie przerwane — usługa się zamyka ({item.host})")
                    break
                if self._chosen.get(item.lane) is item:
                    self._start(item)
                    break
                now = self._now()
                timeouts = []
                if limit is not None:
                    left = limit - now
                    if left <= 0:
                        error = self._expired(item, now)
                        break
                    timeouts.append(left)
                hold = self._hold_left(item.lane, now)
                if hold is not None:
                    timeouts.append(hold)
                self._cond.wait(min(timeouts) if timeouts else None)
            if error is not None:
                self._drop(item)
        self._notify()
        if error is not None:
            raise error

    def _expired(self, item: _Item, now: float) -> Busy:
        busy = ", ".join(dict.fromkeys(ROLE_WORDS.get(r.role, r.role or "inne wywołanie")
                                       for r in self._running.get(item.lane) or ()))
        reason = f"serwer zajęty: {busy}" if busy else "przed nim inne wywołania w kolejce"
        return Busy(f"Model lokalny: kolejka: czekał {seconds(now - item.queued)} s — {reason} ({item.host})")

    # --- stan pod blokadą -----------------------------------------------------------------------

    def _start(self, item: _Item) -> None:
        self._remove(self._waiting, item)
        item.started = self._wall()  # „since” trwającego to początek wywołania; czekającego — dołączenie
        self._running.setdefault(item.lane, []).append(item)
        self._choose(item.lane)

    def _drop(self, item: _Item) -> None:
        self._remove(self._waiting, item)
        self._choose(item.lane)

    def _finish(self, item: _Item, ran: bool) -> None:
        with self._cond:
            self._remove(self._running, item)
            if ran and item.model:
                self._last_model[item.lane] = item.model
            if ran:
                self._last_end[item.lane] = self._now()
            self._choose(item.lane)
        self._notify()

    def _retarget(self, item: _Item, lane: str, endpoint: str, model: str, host: str) -> bool:
        """Cel ustalony przy starcie. Ten sam tor — True (wywołanie rusza). Inny tor — wywołanie wraca do
        czekających na nowym torze (miejsce w kolejności przyjścia zostaje) i False."""
        with self._cond:
            item.endpoint, item.model, item.host = endpoint, model or "", host or endpoint
            if lane == item.lane:
                return True
            self._remove(self._running, item)
            self._choose(item.lane)
            item.lane = lane
            waiting = self._waiting.setdefault(lane, [])
            waiting.append(item)
            waiting.sort(key=lambda i: i.seq)
            self._choose(lane)
        self._notify()
        return False

    @staticmethod
    def _remove(table: dict, item: _Item) -> None:
        items = table.get(item.lane) or []
        if item in items:
            items.remove(item)
        if not items:
            table.pop(item.lane, None)

    def _choose(self, lane: str) -> None:
        """Jedno miejsce wyboru następnego na torze: gdy jest wolne miejsce — `_next`, inaczej nikt.
        Zmiana wyboru budzi wszystkich czekających."""
        chosen = self._next(lane) if len(self._running.get(lane) or ()) < self.slots(lane) else None
        if self._chosen.get(lane) is not chosen:
            if chosen is None:
                self._chosen.pop(lane, None)
            else:
                self._chosen[lane] = chosen
            self._cond.notify_all()

    def _candidates(self, lane: str) -> list:
        waiting = self._waiting.get(lane) or []
        if not waiting:
            return []
        best = min(i.priority for i in waiting)
        return [i for i in waiting if i.priority == best]   # lista jest w kolejności przyjścia

    def _next(self, lane: str) -> _Item | None:
        """Następne do uruchomienia: najwyższy priorytet; w nim ten sam model co ostatnio (chyba że
        najstarsze czeka dłużej niż group_hold), inaczej kolejność przyjścia."""
        candidates = self._candidates(lane)
        if not candidates:
            return None
        oldest = candidates[0]
        last = self._last_model.get(lane)
        if last and oldest.model != last and self._now() - oldest.queued < self.group_hold:
            for candidate in candidates:
                if candidate.model == last:
                    return candidate
        return oldest

    def _hold_left(self, lane: str, now: float) -> float | None:
        """Ile jeszcze grupowanie trzyma najstarsze czekające (wybrany jest ktoś inny) — po tym czasie
        wybór trzeba policzyć od nowa; None, gdy grupowanie nikogo nie trzyma."""
        chosen = self._chosen.get(lane)
        candidates = self._candidates(lane)
        if chosen is None or not candidates or chosen is candidates[0]:
            return None
        return max(0.0, candidates[0].queued + self.group_hold - now)

    def _wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    # --- stan -----------------------------------------------------------------------------------

    def slots(self, lane: str) -> int:
        return max(1, int(self._slots.get(lane, self._default_slots)))

    def snapshot(self) -> dict:
        """{tor: {"running": {endpoint, role, model, since} | None, "waiting": [{endpoint, role, model, since}]}}
        — tylko tory, na których coś jest. `running` to najstarsze trwające (przy slots 1 jedyne), `waiting`
        w kolejności, w jakiej ruszą według priorytetu. Bez treści poleceń."""
        with self._cond:
            out = {}
            for lane in sorted(set(self._waiting) | set(self._running)):
                running = self._running.get(lane) or []
                waiting = sorted(self._waiting.get(lane) or [], key=lambda i: (i.priority, i.seq))
                out[lane] = {"running": running[0].public(running=True) if running else None,
                             "waiting": [i.public() for i in waiting]}
            return out

    def quiet(self, lane: str) -> tuple[float | None, str]:
        """(ile sekund tor stoi bez pracy, model ostatniego wywołania): None — coś trwa albo czeka,
        float("inf") — od startu nic tu nie było. Praca w tle (indeks wyszukiwania) czeka na dłuższą ciszę,
        żeby nie wypychać z karty modelu, którego raport albo opisy kart zaraz znowu użyją."""
        with self._cond:
            if self._waiting.get(lane) or self._running.get(lane):
                return None, self._last_model.get(lane, "")
            end = self._last_end.get(lane)
            return (float("inf") if end is None else max(0.0, self._now() - end)), self._last_model.get(lane, "")

    def busy(self) -> bool:
        with self._cond:
            return bool(self._waiting or self._running)

    def add_listener(self, listener) -> None:
        with self._cond:
            self._listeners.append(listener)

    def remove_listener(self, listener) -> None:
        with self._cond:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _notify(self) -> None:
        with self._cond:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener()
            except Exception as exc:  # słuchacz (np. zamknięty monitor) nie może zepsuć wywołania modelu
                print(f"tide-daemon: Modele lokalne: kolejka: błąd słuchacza ({type(exc).__name__})", flush=True)


_SHARED = Queue()


def shared() -> Queue:
    """Kolejka całego procesu — jedna dla raportu, opisów kart i wszystkich przyszłych wywołań."""
    return _SHARED
