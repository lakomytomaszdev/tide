"""Kolejka wywołań modeli lokalnych: jedno wywołanie naraz na punkt, priorytety, czekanie osobno od pracy.

Ollama na tym komputerze obsługuje jedno zapytanie naraz (OLLAMA_NUM_PARALLEL=1, karta 8 GB, jeden model
w pamięci). Bez kolejki raport poranny i opisy kart czekały na siebie wewnątrz serwera, a czas czekania
liczył się do limitu wywołania — karta dostawała „nie odpowiedział w 90 s”, choć model tylko był zajęty.

Kolejka siedzi w `models.chat` (client.py), więc każde wywołanie modelu lokalnego w usłudze przez nią
przechodzi — także przyszłe (PT-17, PT-19) — bez pamiętania o tym w każdym miejscu. Jedna na proces
(`shared()`), osobny tor na każdy punkt z models.json (po nazwie punktu):

- na punkcie działa najwyżej `slots` wywołań naraz (domyślnie 1; więcej np. dla Maca — później),
- kolejność: najpierw priorytet (HIGH — krótkie rzeczy, na które użytkownik patrzy: opisy kart; LOW —
  ciężka praca wsadowa: raport), w obrębie priorytetu kolejność przyjścia; trwającego wywołania nic
  nie przerywa,
- grupowanie modeli: w obrębie tego samego priorytetu pierwszeństwo ma wywołanie tym samym modelem,
  którym punkt pracował ostatnio (bez przerzucania modeli między kartą a pamięcią), ale tylko dopóki
  najstarsze czekające nie czeka dłużej niż GROUP_HOLD_S — potem zwykła kolejność przyjścia,
- czas: czekanie ma własny limit (`wait`, od wywołującego), a limit samego wywołania (`timeout` w chat)
  biegnie dopiero od chwili, gdy wywołanie naprawdę ruszyło. Po limicie czekania ModelError z uczciwym
  powodem („kolejka: czekał 240 s — serwer zajęty: raport poranny”),
- przerwanie (`transport.Cancel`, zamykanie usługi): czekające wywołanie wychodzi z kolejki od razu,
  a trwające traci gniazdo jak dotąd (transport.py).

Wywołanie biegnie w wątku wywołującego (kolejka nie ma własnych wątków): run() czeka na swoją kolej
na zmiennej warunkowej, potem woła `fn()` i zwalnia miejsce także po błędzie. Stan dla D-Bus
(`snapshot()`) — tylko rola, model i od kiedy, nigdy treść polecenia. Słuchacze (`add_listener`) dostają
powiadomienie po każdej zmianie, w wątku, który ją zrobił (models_monitor.py przenosi to do wątku Qt).
"""
from __future__ import annotations

import itertools
import threading
import time
from datetime import datetime

from .transport import ModelError

HIGH = 0        # krótkie rzeczy, na które użytkownik patrzy (opisy kart)
NORMAL = 5      # domyślnie
LOW = 10        # ciężka praca wsadowa (raport poranny)
ROLE_PRIORITY = {"describe": HIGH, "summary": LOW}
ROLE_WORDS = {"describe": "opisy kart", "summary": "raport poranny"}
DEFAULT_SLOTS = 1
DEFAULT_WAIT_S = 600.0
GROUP_HOLD_S = 60.0


def priority_of(role: str | None) -> int:
    """Priorytet domyślny według roli: `describe` wysoki, `summary` niski, reszta zwykły."""
    return ROLE_PRIORITY.get(role or "", NORMAL)


def _iso(wall: float) -> str:
    return datetime.fromtimestamp(wall).astimezone().isoformat(timespec="seconds")


def _seconds(value: float) -> str:
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}".replace(".", ",")


class _Item:
    __slots__ = ("seq", "endpoint", "role", "model", "priority", "queued", "wall")

    def __init__(self, seq, endpoint, role, model, priority, queued, wall):
        self.seq, self.endpoint, self.role, self.model = seq, endpoint, role, model
        self.priority, self.queued, self.wall = priority, queued, wall

    def public(self) -> dict:
        return {"role": self.role, "model": self.model, "since": _iso(self.wall)}


class Queue:
    """Kolejka wywołań na punkt. `slots` — ile wywołań naraz na punkcie (liczba albo {punkt: liczba} z
    wartością domyślną DEFAULT_SLOTS dla pozostałych)."""

    def __init__(self, slots=DEFAULT_SLOTS, group_hold: float = GROUP_HOLD_S, wall=time.time):
        self._slots = dict(slots) if isinstance(slots, dict) else {}
        self._default_slots = slots if isinstance(slots, int) else DEFAULT_SLOTS
        self.group_hold = group_hold
        self._wall = wall
        self._cond = threading.Condition()
        self._waiting: dict[str, list[_Item]] = {}
        self._running: dict[str, list[_Item]] = {}
        self._last_model: dict[str, str] = {}
        self._seq = itertools.count()
        self._listeners: list = []

    # --- wywołanie ------------------------------------------------------------------------------

    def run(self, fn, *, endpoint: str, role: str = "", model: str = "", priority: int | None = None,
            wait: float | None = DEFAULT_WAIT_S, cancel=None, host: str = ""):
        """Wynik `fn()` po odczekaniu swojej kolejki na punkcie `endpoint`. `wait` — najwyżej tyle sekund
        w kolejce (None — bez limitu, tylko `cancel`); po nim albo po cancel.set() ModelError, a `fn` nie rusza.
        Błąd z `fn` przechodzi dalej bez zmian; miejsce na punkcie jest zwalniane zawsze."""
        item = _Item(next(self._seq), endpoint, role or "", model or "",
                     priority_of(role) if priority is None else priority, time.monotonic(), self._wall())
        wake = self._wake
        registered = cancel._register(wake) if cancel is not None else False
        with self._cond:
            self._waiting.setdefault(endpoint, []).append(item)
        self._notify()
        try:
            self._await(item, wait, cancel, host or endpoint)
        finally:
            if registered:
                cancel._unregister(wake)
        try:
            return fn()
        finally:
            with self._cond:
                running = self._running.get(endpoint) or []
                if item in running:
                    running.remove(item)
                if not running:
                    self._running.pop(endpoint, None)
                if item.model:
                    self._last_model[endpoint] = item.model
                self._cond.notify_all()
            self._notify()

    def _await(self, item: _Item, wait: float | None, cancel, host: str) -> None:
        limit = None if wait is None else time.monotonic() + max(0.0, wait)
        error = None
        with self._cond:
            while True:
                if cancel is not None and cancel.is_set():
                    error = ModelError(f"Model lokalny: wywołanie przerwane — usługa się zamyka ({host})")
                    break
                if len(self._running.get(item.endpoint) or ()) < self.slots(item.endpoint) \
                        and self._next(item.endpoint) is item:
                    self._waiting[item.endpoint].remove(item)
                    if not self._waiting[item.endpoint]:
                        del self._waiting[item.endpoint]
                    item.wall = self._wall()  # od teraz „since” to początek wywołania
                    self._running.setdefault(item.endpoint, []).append(item)
                    self._cond.notify_all()
                    break
                left = None if limit is None else limit - time.monotonic()
                if left is not None and left <= 0:
                    waited = time.monotonic() - item.queued
                    busy = ", ".join(dict.fromkeys(ROLE_WORDS.get(r.role, r.role or "inne wywołanie")
                                                   for r in self._running.get(item.endpoint) or ()))
                    reason = f"serwer zajęty: {busy}" if busy else "przed nim inne wywołania w kolejce"
                    error = ModelError(f"Model lokalny: kolejka: czekał {_seconds(waited)} s — {reason} ({host})")
                    break
                self._cond.wait(left)
            if error is not None:
                waiting = self._waiting.get(item.endpoint) or []
                if item in waiting:
                    waiting.remove(item)
                if not waiting:
                    self._waiting.pop(item.endpoint, None)
                self._cond.notify_all()  # zwolnione miejsce w kolejce — następny może być teraz pierwszy
        self._notify()
        if error is not None:
            raise error

    def _next(self, endpoint: str) -> _Item | None:
        """Następne do uruchomienia: najwyższy priorytet; w nim ten sam model co ostatnio (chyba że
        najstarsze czeka dłużej niż group_hold), inaczej kolejność przyjścia."""
        waiting = self._waiting.get(endpoint) or []
        if not waiting:
            return None
        best = min(i.priority for i in waiting)
        candidates = [i for i in waiting if i.priority == best]   # lista jest w kolejności przyjścia
        oldest = candidates[0]
        last = self._last_model.get(endpoint)
        if last and oldest.model != last and time.monotonic() - oldest.queued < self.group_hold:
            for candidate in candidates:
                if candidate.model == last:
                    return candidate
        return oldest

    def _wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    # --- stan -----------------------------------------------------------------------------------

    def slots(self, endpoint: str) -> int:
        return max(1, int(self._slots.get(endpoint, self._default_slots)))

    def snapshot(self) -> dict:
        """{punkt: {"running": {role, model, since} | None, "waiting": [{role, model, since}]}} — tylko punkty,
        na których coś jest. `running` to najstarsze trwające (przy slots 1 jedyne), `waiting` w kolejności,
        w jakiej ruszą według priorytetu. Bez treści poleceń."""
        with self._cond:
            out = {}
            for endpoint in sorted(set(self._waiting) | set(self._running)):
                running = self._running.get(endpoint) or []
                waiting = sorted(self._waiting.get(endpoint) or [], key=lambda i: (i.priority, i.seq))
                out[endpoint] = {"running": running[0].public() if running else None,
                                 "waiting": [i.public() for i in waiting]}
            return out

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
