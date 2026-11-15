"""Karty w spoczynku (K-105 PT-412): zakończona karta bez żadnej pracy nie przechodzi pełnego przebiegu karty w ticku.

Kontrakt dla następców (opis: docs/tide-daemon.md, „Karty w spoczynku”):

- warunek spoczynku to **jedna funkcja** `at_rest` nad **rejestrem czynników wykluczających** `FACTORS`; karta jest
  w spoczynku tylko wtedy, gdy żaden czynnik jej nie wyklucza (pierwszy czynnik, `card-unfinished`, wyklucza każdą
  kartę niezakończoną),
- każdy czynnik ma nazwę i odpowiada na pytanie „ta karta ma pracę”; wyjątek albo błąd odczytu w czynniku wyklucza
  spoczynek (`Verdict.error`),
- rejestr jest jedynym miejscem, w którym kolejne karty dopisują swoje czynniki (`register`): K-108 (unieważnienie
  spoczynku po zmianie blokera), K-109 (`command.*`), K-107 (otwarte etapy i Konsola), silnikowa część K-39. Zasada:
  **każdy nowy czynnik pracy musi wykluczać spoczynek** — nowa praca karty bez czynnika w rejestrze nie zostanie
  obsłużona, dopóki karta jest w spoczynku (najdłużej do okresu kontroli pełnej),
- operacje kolejek mają jeden nazwany punkt wejścia `has_queued_operation` („karta ma operację w kolejce”) nad
  rejestrem źródeł `QUEUES` (`heavyqueue`, `stepqueue`): każda oczekująca albo trwająca operacja dowolnej kolejki
  wyklucza spoczynek każdej karty, niezależnie od karty, która ją dostarczyła; błąd odczytu kolejki też. Kolejne
  kolejki dopisują się w `QUEUES` (`register_queue`), nie osobnym warunkiem w ticku.

Pamięć między tickami (`RestMemory`) trzyma po każdym udanym pełnym przebiegu karty, która była wtedy w spoczynku,
znacznik: wersję dziennika (K-105 PT-411, `Journal.version`), kolumnę i `move_id` z Tablicy oraz chwilę przebiegu.
Karta pomija pełny przebieg (`TickRest.resting`) tylko przy ważnym znaczniku i spełnionym `at_rest`; pełny przebieg
wraca przy zmianie wersji dziennika, kolumny albo ruchu karty, w pierwszym ticku silnika (pusta pamięć), po upływie
okresu kontroli pełnej (`PERIOD`, zegar `Journal.monotonic`), po wyjątku w poprzednim pełnym przebiegu i gdy
którykolwiek czynnik wykluczy spoczynek. Znacznik powstaje dopiero po pełnym przebiegu, w którym żaden czynnik nie
wykluczał spoczynku — karta wykluczona (np. operacją kolejki) wraca do spoczynku po kolejnym pełnym przebiegu.

Moduł niczego nie zapisuje: czynniki czytają stan karty (`Journal.state` z pamięci), kontekst odczytu Tablicy ticku
(K-98 #3) i pamięć silnika. Bramki przed skutkiem dalej czytają świeżo w pełnym przebiegu.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ..board.model import card_num
from . import baseproposal, caretaker, mergecheck, queueview, stepqueue, trialhold
from .journal import FULL_READ_PERIOD
from .state import outside_queued

# Okres kontroli pełnej: najdłużej tyle karta w spoczynku nie przechodzi pełnego przebiegu. Ta sama stała i zegar
# (`Journal.monotonic`) co okres pełnego odczytu pamięci dziennika (K-105 PT-411).
PERIOD = FULL_READ_PERIOD
FINISHED_COLUMNS = ("done", "archived")


class Probe:
    """Dane jednej oceny spoczynku karty: silnik, karta, stan karty (`Journal.state`), kontekst odczytu Tablicy ticku
    i wspólny dla ticku odczyt kolejek (`TickRest`). Czynniki tylko czytają."""

    def __init__(self, engine, card: str, st, read=None, tick: "TickRest | None" = None):
        self.engine, self.card, self.st, self.read, self.tick = engine, card, st, read, tick

    @property
    def board_item(self):
        board = self.read.board if self.read is not None else self.engine.store.load()
        return board.cards.get(card_num(self.card))

    def queue_operations(self) -> list:
        return self.tick.queue_operations() if self.tick is not None else queue_operations(self.engine)


@dataclass(frozen=True)
class Factor:
    """Czynnik wykluczający spoczynek: `name` — nazwa w dokumentacji i w diagnostyce, `question` — zdanie „ta karta ma
    pracę, gdy…”, `has_work(probe)` — prawda, gdy karta ma tę pracę."""
    name: str
    question: str
    has_work: Callable[[Probe], bool]


@dataclass(frozen=True)
class Verdict:
    """Wynik `at_rest`: `rest` — karta w spoczynku; `factor` — nazwa pierwszego czynnika, który ją wykluczył;
    `error` — opis wyjątku, gdy czynnik nie dał odpowiedzi (błąd wyklucza spoczynek)."""
    rest: bool
    factor: str = ""
    error: str = ""


def at_rest(probe: Probe, factors: list[Factor] | None = None) -> Verdict:
    """Warunek spoczynku karty — jedyna funkcja: żaden czynnik rejestru nie wyklucza spoczynku. Wyjątek w czynniku
    wyklucza spoczynek. Rejestr czytany przy każdym wywołaniu, więc czynnik dopisany przez `register` działa od razu."""
    for factor in FACTORS if factors is None else factors:
        try:
            if factor.has_work(probe):
                return Verdict(False, factor.name)
        except Exception as exc:        # odczyt nieudany = nie wiemy, że karta nie ma pracy
            return Verdict(False, factor.name, f"{exc.__class__.__name__}: {exc}")
    return Verdict(True)


# --- czynniki --------------------------------------------------------------------------------------------------


def _unfinished(p: Probe) -> bool:
    if not p.st.card_finished:
        return True
    item = p.board_item
    return item is None or item.column not in FINISHED_COLUMNS


def _steps_and_consoles(p: Probe) -> bool:
    return bool(p.st.running() or p.st.open_consoles())


def _intents(p: Probe) -> bool:
    return bool(p.st.open_intents(include_background=True))


def _held(p: Probe) -> bool:
    st = p.st
    return trialhold.preserves_project(st) or any(trialhold.held(step) for step in
                                                  (*st.steps.values(), *st.caretakers.values()))


def _k38_public(p: Probe) -> bool:
    # Inwentaryzacja publicznych procesów (`trialhold.public_inventory`) biegnie tylko w pełnym przebiegu karty K-38.
    return p.card == "K-38"


def _main_merge(p: Probe) -> bool:
    return bool(p.st.merge_waiting) or p.engine._pending_main_merge(p.card) is not None


def _base(p: Probe) -> bool:
    return bool(baseproposal.blocked(p.st) or p.st.base_request)


def _outside(p: Probe) -> bool:
    st = p.st
    if st.outside_started or outside_queued(st) or mergecheck.gate(st):
        return True
    engine = p.engine
    live = [*(engine._outside_active or {}).values(), *(engine._outside_waiting or {}).values()]
    return any(entry[0] == p.card for entry in live)


def _caretaker(p: Probe) -> bool:
    st = p.st
    return bool(any(name not in st.caretaker_outcomes for name in st.caretaker_runs) or
                any(name not in st.finished for name in st.caretakers))


def _stage_errors(p: Probe) -> bool:
    return bool(p.st.flow_errors)


def _decisions(p: Probe) -> bool:
    return bool(caretaker._waiting_decisions(p.st))


def _plan(p: Probe) -> bool:
    # Review i zatwierdzenie planu (`_advance_plan_review`, `approve_plan`) oraz poprawa planu (`_advance_user_revision`).
    st = p.st
    return bool(st.plan_revision or (st.plan_proposed and not st.plan_approved))


def _reviews(p: Probe) -> bool:
    # Odtworzenie werdyktu review (`_recover_verdicts`) i odrzucenia ticketu (`_recover_review_rejections`).
    st = p.st
    for name, done in st.finished.items():
        step = st.steps.get(name) or {}
        if name not in st.superseded and step.get("kind") == "review" and done.get("result") == "done" and \
                (step.get("ticket"), step.get("round")) not in st.verdicts:
            return True
    return any(d.get("choice") == "reject" and ticket not in st.rejected
               for (ticket, _round), d in st.review_decisions.items())


def _tracker(p: Probe) -> bool:
    # Notatki YouTracka po scaleniu ticketu (`_tracker_notes`): scalenie bez wyniku `youtrack-note` w projekcie z YouTrackiem.
    st = p.st
    pending = [t for (action, t), r in st.results.items()
               if action == "merge" and r.get("ok") and ("youtrack-note", t) not in st.results]
    if not pending:
        return False
    _, project = p.engine._project_read(p.card, p.read)
    return bool(project.youtrack)


def _notices(p: Probe) -> bool:
    st = p.st
    if any(n.get("card") == p.card for n in list(p.engine._notices)):
        return True             # powiadomienie w pamięci silnika jeszcze nie odebrane przez monitor
    for name, done in st.finished.items():
        step = st.steps.get(name) or {}
        result = done.get("result")
        if name in st.caretakers or name in st.step_notices or name in st.step_decisions:
            continue
        if result in ("question", "denied") or result == "error" and (
                int(step.get("attempt") or 0) >= 1 or done.get("reason") == "limit"):
            return True
    return False


def _queues(p: Probe) -> bool:
    return has_queued_operation(p)


FACTORS: list[Factor] = [
    Factor("card-unfinished", "karta nie jest zakończona (`card.finished`) albo nie stoi w Gotowe/Archiwum", _unfinished),
    Factor("steps", "krok albo krok opiekuna trwa, albo Konsola kroku interaktywnego jest otwarta", _steps_and_consoles),
    Factor("intents", "zamiar akcji bez wyniku, także w tle (`youtrack-note`)", _intents),
    Factor("held", "tożsamość procesu chroniona (`held`, K-38)", _held),
    Factor("k38-public", "karta K-38 prowadzi inwentaryzację publicznych procesów", _k38_public),
    Factor("main-merge", "scalenie karty do głównej czeka albo nie ma punktu kontrolnego `projected`", _main_merge),
    Factor("base", "propozycja, prośba albo zmiana bazy karty w toku", _base),
    Factor("outside", "polecenie poza piaskownicą trwa albo czeka w kolejce (także próba testów po scaleniu)", _outside),
    Factor("caretaker", "opiekun karty pracuje albo jego wynik czeka na akcję", _caretaker),
    Factor("stage-errors", "błąd etapu (`flow.error`) czeka na ponowienie", _stage_errors),
    Factor("decisions", "decyzja czeka (`_waiting_decisions`: pytanie, zgoda, plan, review)", _decisions),
    Factor("plan", "plan czeka na review, zatwierdzenie albo poprawę", _plan),
    Factor("reviews", "werdykt review albo odrzucenie ticketu czeka na odtworzenie", _reviews),
    Factor("tracker", "notatka YouTracka po scaleniu ticketu czeka na wysłanie", _tracker),
    Factor("notices", "powiadomienie czeka na wysłanie", _notices),
    Factor("queues", "karta ma operację w kolejce (`has_queued_operation`)", _queues),
]


def register(name: str, question: str, has_work: Callable[[Probe], bool]) -> Factor:
    """Dopisuje czynnik do rejestru (punkt podpięcia K-107/K-108/K-109/K-39). Nazwa musi być nowa."""
    if any(f.name == name for f in FACTORS):
        raise ValueError(f"czynnik spoczynku {name} już jest w rejestrze")
    factor = Factor(name, question, has_work)
    FACTORS.append(factor)
    return factor


# --- kolejki ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class QueueSource:
    """Źródło operacji kolejki: `operations(silnik, stany kart)` zwraca listę opisów oczekujących i trwających
    operacji. Wyjątek oznacza brak odpowiedzi kolejki — wyklucza spoczynek."""
    name: str
    operations: Callable[[object, dict], list]


def _states(engine) -> dict:
    states = {}
    for card in engine.journal.cards():
        st = engine.journal.state(card)
        if st.started is not None:
            states[card] = st
    return states


def _heavyqueue(engine, states: dict) -> list:
    """Kolejka poleceń poza piaskownicą z kolejką ciężkich (K-39 PT-373): trwające i czekające, ciężkie i lekkie,
    z próbami testów po scaleniu — ze stanów kart (jak pole `queue` migawki) i z żywej pamięci silnika."""
    found = [("heavyqueue", e["card"], e["step"]) for e in queueview.operations(states, now=time.time())]
    for name, entry in (engine._outside_active or {}).items():
        found.append(("heavyqueue", entry[0], name))
    for name, entry in (engine._outside_waiting or {}).items():
        found.append(("heavyqueue", entry[0], name))
    return found


def _stepqueue(engine, states: dict) -> list:
    """Kolejka kroków u dostawcy (K-39 PT-377): każdy wpis czekania, z dowolną bieżącą przyczyną."""
    return [("stepqueue", e.card, f"{e.ticket}:{e.kind}")
            for e in stepqueue.order(states, priority_of=lambda _card: stepqueue.DEFAULT)]


QUEUES: list[QueueSource] = [QueueSource("heavyqueue", _heavyqueue), QueueSource("stepqueue", _stepqueue)]


def register_queue(name: str, operations: Callable[[object, dict], list]) -> QueueSource:
    """Dopisuje źródło kolejki do punktu wejścia „karta ma operację w kolejce”."""
    if any(q.name == name for q in QUEUES):
        raise ValueError(f"kolejka {name} już jest w rejestrze")
    source = QueueSource(name, operations)
    QUEUES.append(source)
    return source


def queue_operations(engine) -> list:
    """Wszystkie oczekujące i trwające operacje wszystkich kolejek z `QUEUES`. Błąd źródła propaguje."""
    states = _states(engine)
    found = []
    for source in list(QUEUES):
        found.extend(source.operations(engine, states))
    return found


def has_queued_operation(probe: Probe) -> bool:
    """Punkt wejścia „karta ma operację w kolejce” (poprawka po audycie 12.11): oczekująca albo trwająca operacja
    którejkolwiek kolejki wyklucza spoczynek karty bezwarunkowo, także gdy operacja należy do innej karty. Błąd odczytu
    stanu kolejki propaguje (wyklucza spoczynek)."""
    return bool(probe.queue_operations())


# --- pamięć między tickami -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Mark:
    """Znacznik ostatniego udanego pełnego przebiegu karty w spoczynku."""
    version: object
    column: str
    move_id: str
    at: float


class RestMemory:
    """Znaczniki pełnych przebiegów kart (karta → `Mark`) między tickami jednego silnika. Nowy silnik (restart
    usługi) ma pustą pamięć, więc jego pierwszy tick przechodzi pełnym przebiegiem każdą kartę."""

    def __init__(self, monotonic: Callable[[], float]):
        self.monotonic = monotonic
        self.marks: dict[str, Mark] = {}

    def begin(self, engine, read) -> "TickRest":
        return TickRest(self, engine, read)

    def forget_except(self, cards) -> None:
        keep = set(cards)
        for card in [c for c in self.marks if c not in keep]:
            del self.marks[card]


class TickRest:
    """Ocena spoczynku w jednym ticku: wspólny odczyt kolejek (raz na tick, przy pierwszym pytaniu) i decyzje
    o pełnym przebiegu kart."""

    def __init__(self, memory: RestMemory, engine, read):
        self.memory, self.engine, self.read = memory, engine, read
        self._queue: tuple | None = None        # (operacje, wyjątek)

    def queue_operations(self) -> list:
        if self._queue is None:
            try:
                self._queue = (queue_operations(self.engine), None)
            except Exception as exc:
                self._queue = (None, exc)
        if self._queue[1] is not None:
            raise self._queue[1]
        return self._queue[0]

    def probe(self, card: str, st) -> Probe:
        return Probe(self.engine, card, st, self.read, self)

    def _position(self, card: str) -> tuple[str, str]:
        item = self.read.board.cards.get(card_num(card)) if self.read is not None else \
            self.engine.store.load().cards.get(card_num(card))
        return (item.column, item.move_id or "") if item is not None else ("", "")

    def resting(self, card: str, st) -> bool:
        """Czy karta pomija pełny przebieg w tym ticku: ważny znacznik (ta sama wersja dziennika, kolumna i ruch,
        okres kontroli pełnej nie minął) i `at_rest`. Każdy błąd — pełny przebieg."""
        mark = self.memory.marks.get(card)
        if mark is None:
            return False
        try:
            if self.memory.monotonic() - mark.at >= PERIOD:
                return False
            if self.engine.journal.version(card) != mark.version:
                return False
            if self._position(card) != (mark.column, mark.move_id):
                return False
        except Exception:
            return False
        return at_rest(self.probe(card, st)).rest

    def passed(self, card: str, ok: bool) -> None:
        """Po pełnym przebiegu karty: znacznik tylko po przebiegu bez wyjątku, gdy karta jest teraz w spoczynku."""
        self.memory.marks.pop(card, None)
        if not ok:
            return
        try:
            st = self.engine.journal.state(card)
            if not at_rest(self.probe(card, st)).rest:
                return
            version = self.engine.journal.version(card)
            column, move_id = self._position(card)
        except Exception:
            return
        self.memory.marks[card] = Mark(version, column, move_id, self.memory.monotonic())

    def drop(self, card: str) -> None:
        self.memory.marks.pop(card, None)
