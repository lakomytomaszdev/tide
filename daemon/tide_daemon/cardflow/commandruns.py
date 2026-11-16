"""Jeden widok przebiegów poleceń z dziennika karty (K-109 PT-481; krok „rozszerz” przed zmianą zapisu w PT-479).

Ten sam przebieg polecenia poza piaskownicą opisują dwie rodziny zdarzeń:

- historyczna `outside.started` → `outside.finished` — zapis dzisiejszej drogi (jednostka `tide-outside-<krok>`);
- nowa `command.intent` → `command.started` → `command.finished` — protokół jednego wykonania (ADR-1): zamiar przed
  startem, start z jednostką `tide-cmd-<attempt>`, wynik z pliku `exit-code`. Zapisują ją PT-479 i PT-485; w PT-481
  nic jej jeszcze nie zapisuje.

Konsumenci historii (stan karty `state.fold`, tożsamość procesów `processidentity`, strażnik opiekuna `guard`,
ponowienie testów `testrerun`, inwentarz prób `trialhold`, liczniki `tools/coordinator/metrics.py`, a przez stan karty
kolejka `queueview`, zgody `consent`, `mergecheck` i `commands`) nie porównują nazw tych zdarzeń: pytają o fazę
zdarzenia (`phase`) albo czytają gotowe przebiegi (`runs`). Adapter tylko czyta — nie przepisuje dziennika, nie zapisuje
zdarzeń i nie zmienia ich pól.

Schemat pól (te same klucze w obu rodzinach, więc konsument czyta pole startu i końca tak samo):

- start (`outside.started`, `command.started`): `step`, `ticket`, `command`, `unit`, `log`, `exitFile`, `worktree`,
  `head`, `rerunOf`, `ts`, `seq` (`COMMON_START`);
- koniec (`outside.finished`, `command.finished`): `step`, `ticket`, `command`, `exit`, `tail`, `ts`, `seq`
  (`COMMON_FINISH`);
- tylko `command.*` (`COMMAND_ONLY`): `attempt` (identyfikator próby; łączy zamiar, start i koniec), `executor`,
  `isolation`, `class`, `consentVersion`, `source`, `loadavg`, `cores`, `recovered`, a w końcu `reason`
  (np. `not-started`).

Moduł nie importuje niczego z pakietu (liść): czytają go także moduły, które importuje `commands.py`
(`state`, `processidentity`, `trialhold`, `mergecheck`, `consent`), więc adapter wewnątrz `commands.py` dałby cykl
importów. `commands.py` udostępnia ten sam widok pod tymi samymi nazwami.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType

OUTSIDE, COMMAND = "outside", "command"
LEGACY_STARTED, LEGACY_FINISHED = "outside.started", "outside.finished"
INTENT, STARTED, FINISHED = "command.intent", "command.started", "command.finished"

# Rodzaj zdarzenia → faza przebiegu. Jedyne miejsce w pakiecie, które zna nazwy obu rodzin przebiegu.
PHASES = MappingProxyType({LEGACY_STARTED: "started", LEGACY_FINISHED: "finished",
                           INTENT: "intent", STARTED: "started", FINISHED: "finished"})
STARTS = frozenset(kind for kind, value in PHASES.items() if value == "started")
FINISHES = frozenset(kind for kind, value in PHASES.items() if value == "finished")
INTENTS = frozenset(kind for kind, value in PHASES.items() if value == "intent")

COMMON_START = ("step", "ticket", "command", "unit", "log", "exitFile", "worktree", "head", "rerunOf", "ts", "seq")
COMMON_FINISH = ("step", "ticket", "command", "exit", "tail", "ts", "seq")
COMMAND_ONLY = ("attempt", "executor", "isolation", "class", "consentVersion", "source", "loadavg", "cores",
                "recovered", "reason")
# Rezerwacja budżetu w zamiarze (K-109 PT-485, ADR-1 pkt 2): klasa z bieżącej decyzji kolejki i miejsce (projekt).
INTENT_BUDGET = ("heavy", "exclusive", "project")
NOT_STARTED = "not-started"     # `reason` końca zamiaru rozstrzygniętego bez startu polecenia
NOT_STARTED_NOTE = ("Polecenie nie wystartowało: usługa Tide przerwała start i po odzyskaniu porzuciła tę próbę "
                    "(nic nie zostało uruchomione). Jeśli nadal jest potrzebne, poproś o nie ponownie.")


def phase(event) -> str:
    """Faza przebiegu zdarzenia: `intent`, `started`, `finished` albo "" (zdarzenie spoza przebiegów)."""
    kind = event.get("type") if isinstance(event, dict) else None
    return PHASES.get(kind, "") if isinstance(kind, str) else ""


def family(kind) -> str:
    """Rodzina rodzaju zdarzenia (`outside`, `command`) albo "" — także dla nieznanych `outside.*`/`command.*`."""
    if isinstance(kind, str):
        for name in (OUTSIDE, COMMAND):
            if kind.startswith(name + "."):
                return name
    return ""


@dataclass(frozen=True)
class Run:
    """Przebieg polecenia: start i (opcjonalnie) jego koniec, niezależnie od rodziny zapisu.

    `started_seq`/`finished_seq` — `seq` zdarzeń startu i końca (`finished_seq` None — polecenie trwa albo koniec
    utracono); `family` — rodzina zdarzenia startu; `start`/`end` — zdarzenia źródłowe (do odczytu, bez porównania)."""
    step: str
    ticket: str = ""
    command: str = ""
    family: str = ""
    attempt: str = ""
    since: str = ""
    unit: str = ""
    log: str = ""
    exit_file: str = ""
    worktree: str = ""
    head: str = ""
    rerun_of: str = ""
    intent: bool = False
    started_seq: object = None
    finished: bool = False
    finished_seq: object = None
    exit: object = None
    tail: str = ""
    start: dict = field(default=None, compare=False, repr=False)
    end: dict = field(default=None, compare=False, repr=False)


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _attempt(event) -> str:
    value = event.get("attempt")
    return value if isinstance(value, str) else "" if value is None else str(value)


def runs(events) -> list[Run]:
    """Przebiegi w kolejności startów. Start otwiera przebieg swojego kroku (późniejszy start tego samego kroku
    zastępuje otwarty), pierwszy koniec po starcie go zamyka; koniec bez otwartego przebiegu pomijamy. Zamiar
    `command.intent` oznacza następny start tego kroku (`intent`); koniec i start mogą być z różnych rodzin
    (przebieg zaczęty przed przełączeniem zapisu, zakończony po nim)."""
    found: list[Run] = []
    running: dict = {}
    intents: dict = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        current = phase(event)
        if not current:
            continue
        name = event.get("step") or ""
        if current == "intent":
            intents[name] = _attempt(event)
        elif current == "started":
            attempt = _attempt(event)
            planned = intents.pop(name, None)
            run = Run(step=name, ticket=_text(event.get("ticket")), command=_text(event.get("command")),
                      family=family(event.get("type")), attempt=attempt, since=_text(event.get("ts")),
                      unit=_text(event.get("unit")), log=_text(event.get("log")),
                      exit_file=_text(event.get("exitFile")), worktree=_text(event.get("worktree")),
                      head=_text(event.get("head")), rerun_of=_text(event.get("rerunOf")),
                      intent=planned is not None and (not planned or not attempt or planned == attempt),
                      started_seq=event.get("seq"), start=event)
            found.append(run)
            running[name] = len(found) - 1
        elif name in running:
            index = running.pop(name)
            found[index] = replace(found[index], finished=True, finished_seq=event.get("seq"),
                                   exit=event.get("exit"), tail=_text(event.get("tail")), end=event)
        else:
            intents.pop(name, None)     # zamiar rozstrzygnięty bez startu (`reason: not-started`, PT-485)
    return found


@dataclass(frozen=True)
class Attempt:
    """Ostatni zamiar `command.intent` kroku i jego los (K-109 PT-485): `started` — start tej próby (także
    odzyskany), `finished` — koniec (z `reason: not-started`, gdy polecenie nigdy nie wystartowało)."""
    intent: dict
    started: dict | None = None
    finished: dict | None = None

    @property
    def pending(self) -> bool:
        return self.started is None and self.finished is None

    @property
    def not_started(self) -> bool:
        return self.started is None and self.finished is not None


def attempt_of(events, name: str) -> Attempt | None:
    """Ostatni zamiar kroku `name` z jego startem i końcem albo None (krok bez zamiaru, np. przebieg `outside.*`).
    Start i koniec należą do zamiaru, gdy przyszły po nim (start z innym `attempt` — nie)."""
    found = None
    for event in events:
        current = phase(event)
        if not current or (event.get("step") or "") != name:
            continue
        if current == "intent":
            found = Attempt(event)
        elif found is None:
            continue
        elif current == "started" and found.started is None and found.finished is None:
            attempt, planned = _attempt(event), _attempt(found.intent)
            if not attempt or not planned or attempt == planned:
                found = replace(found, started=event)
        elif current == "finished" and found.finished is None:
            found = replace(found, finished=event)
    return found


def intended(events) -> set:
    """Kroki z jakimkolwiek zamiarem `command.intent`: jedna decyzja kroku = jeden zamiar, ponowny start wymaga nowej
    decyzji (nowego kroku) i nowego zamiaru (K-109 PT-485)."""
    return {event.get("step") or "" for event in events if phase(event) == "intent"}


def pending(events) -> dict:
    """Krok → identyfikator próby zamiarów `command.intent` bez startu i bez końca (zamiar przed uruchomieniem,
    ADR-1 pkt 1). Rozstrzyga je odzyskiwanie (`commands.outside_recover`, PT-485)."""
    waiting: dict = {}
    for event in events:
        current = phase(event)
        if not current:
            continue
        name = event.get("step") or ""
        if current == "intent":
            waiting[name] = _attempt(event)
        elif current in ("started", "finished"):
            waiting.pop(name, None)
    return waiting
