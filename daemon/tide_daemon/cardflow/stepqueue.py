"""Kolejność kroków czekających na miejsce u dostawcy (K-39 #7, PT-377).

Uogólnia kolejkę opiekuna z PT-150 (`_earlier_waiters`) na wszystkie kroki: krok czekający na miejsce u dostawcy
(`cause: slots`) ustępuje krokom **innych kart** o wyższym kluczu porządku. Zwolnione miejsce bierze krok karty
o wyższym priorytecie, przy równym priorytecie krok czekający dłużej — a nie karta sprawdzana w ticku jako pierwsza.

Klucz porządku (`key`, mniejszy rusza pierwszy, wspólny dla wszystkich kart i totalny):

    (podbicie, priorytet karty, since, karta, ticket, rodzaj)

- podbicie — w tym tickecie zawsze fałszywe (ustawia je #11);
- priorytet karty — `priority` z pliku karty: `high` przed `normal` przed `low` (brak albo zła wartość = `normal`);
- since — początek czekania (`step.waiting.since`); krok jeszcze nieczekający przychodzi po wszystkich czekających
  o tym samym priorytecie;
- karta, ticket, rodzaj — stały porządek remisu w obrębie sekundy, więc dwa kroki nigdy nie czekają na siebie
  nawzajem.

`order` jest jedyną funkcją porządku: zwraca wpisy czekania w kolejności startu z bieżącą przyczyną i powodem.
Woła ją start kroku w silniku (`FlowEngine._earlier_waiters` → `ahead`), a w #10 także migawka — druga kopia porządku
nie powstaje. Moduł nie czyta dzienników ani Tablicy: dostaje stany kart z fazy odczytu ticku (`journal.state`, K-98)
i wywołania silnika dla tego, czego stan karty nie zawiera (priorytet z Tablicy, limit tygodniowy dostawcy, rezerwacja
własnego opiekuna, limit ticketów karty).

**Bieżąca przyczyna, nie zapisany `cause`.** W kolejce stoją wpisy `step.waiting` z zapisanym `cause` `slots` albo
`quota`, ale o miejscu decyduje stan z chwili decyzji: wpis, którego dostawca jest teraz ponad limitem tygodniowym
(albo w oknie limitu), czeka na limit i nie blokuje innych; wpis z `cause: quota`, którego limit się odnowił, wraca do
kolejki po miejsce. Tak samo nie zajmuje miejsca krok, który nie może ruszyć z innego powodu: karta czeka na decyzję
o bazie, ticket bez kroku czeka na miejsce w karcie, na testy po scaleniu albo trwającą operację karty, a krok jest
zablokowany rezerwacją własnego opiekuna (PT-150, runda 3 — inaczej powstaje cykl czekania). Karty zakończone,
wstrzymane, przeniesione do Gotowe/Archiwum/Backlogu, odrzucone i scalone tickety oraz trwające kroki wypadają
z listy całkowicie.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Iterable, Mapping

from ..board.model import DEFAULT_PRIORITY, PRIORITIES
from . import baseproposal, mergecheck, ticketlimit

HIGH, NORMAL, LOW = PRIORITIES      # klucz `priority` karty (board/model.py)
DEFAULT = DEFAULT_PRIORITY
LABELS = {HIGH: "Wysoki", NORMAL: "Normalny", LOW: "Niski"}
_RANK = {HIGH: 0, NORMAL: 1, LOW: 2}

SLOTS = "slots"                 # bieżąca przyczyna wpisu w kolejce po miejsce u dostawcy
QUEUED_CAUSES = ("slots", "quota")   # zapisane przyczyny, które biorą udział w kolejce dostawcy
GONE_COLUMNS = ("done", "archived", "backlog")
CARETAKER = "caretaker"         # rodzaj kroku opiekuna (caretaker.KIND; bez importu ciężkiego modułu)

HELD_BASE = "decyzja o bazie karty"
HELD_CARETAKER = "czeka na krok opiekuna swojej karty"
HELD_MERGE_CHECK = "czeka na testy po scaleniu"
HELD_INTENT = "czeka na zakończenie operacji karty"
WAITING_SLOT = "czeka na miejsce u dostawcy {provider}"


def valid(value) -> bool:
    return isinstance(value, str) and value in PRIORITIES


def of(value) -> str:
    """Priorytet karty: wartość z `PRIORITIES`; brak albo zła wartość — `normal`."""
    return value if valid(value) else DEFAULT


@dataclass(frozen=True)
class Entry:
    """Wpis kolejki: krok `(karta, ticket, rodzaj)` czekający u `provider` od `since` (ISO 8601; pusty — jeszcze
    nie czeka). `cause` to bieżąca przyczyna: `slots` — stoi w kolejce po miejsce; inna — czeka z innego powodu
    (`reason`) i nie zajmuje miejsca innym: `quota` (limit tygodniowy/okno dostawcy), `base` (decyzja o bazie
    karty), `ticket` (nowy ticket czeka na miejsce w karcie, testy po scaleniu albo operację karty), `caretaker`
    (rezerwacja własnego opiekuna)."""
    card: str
    ticket: str
    kind: str
    provider: str
    since: str = ""
    priority: str = DEFAULT
    bumped: bool = False
    cause: str = SLOTS
    reason: str = ""

    @property
    def queued(self) -> bool:
        return self.cause == SLOTS


def _since(value: str) -> float:
    """Chwila początku czekania (sekundy); brak albo nieczytelna — po wszystkich czekających."""
    if not value:
        return math.inf
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError, OverflowError):
        return math.inf


def key(entry: Entry) -> tuple:
    """Klucz porządku startu (mniejszy rusza pierwszy). Totalny: dwa różne kroki mają różne klucze."""
    return (0 if entry.bumped else 1, _RANK[of(entry.priority)], _since(entry.since), entry.card,
            entry.ticket or "", entry.kind or "")


def _new_ticket_held(st, ticket: str, limit: int | None) -> str:
    """Powód, dla którego ticket bez kroku (start nowego ticketu z `_plan_next`) teraz nie ruszy; pusty — może."""
    rows = (st.plan_approved or {}).get("rows") or []
    if not rows or any(s.get("ticket") == ticket for s in st.steps.values()):
        return ""
    if st.open_intents():
        return HELD_INTENT
    if mergecheck.gate(st):
        return HELD_MERGE_CHECK
    if limit is not None:
        running = len(ticketlimit.in_progress(st, rows))
        if running >= limit:
            return ticketlimit.wait_reason(running, limit)
    return ""


def _gone(st, ticket: str) -> bool:
    merged = set(st.imported_done) | {t for (action, t), r in st.results.items() if action == "merge" and r.get("ok")}
    return ticket in st.rejected or ticket in merged


def order(states: Mapping[str, object], *, priority_of: Callable[[str], str],
          quota_of: Callable[[str], str] | None = None,
          reserved_of: Callable[[str, object, Entry], bool] | None = None,
          ticket_limit_of: Callable[[str], int | None] | None = None,
          provider: str | None = None) -> list[Entry]:
    """Czekający w kolejności startu (`key`), z bieżącą przyczyną (`Entry.cause`) i powodem.

    `states` — karta → `FlowState` z fazy odczytu ticku; `priority_of(karta)` — priorytet z Tablicy;
    `quota_of(dostawca)` — powód czekania na limit tygodniowy/okno dostawcy albo pusty; `reserved_of(karta, stan,
    wpis)` — krok innego rodzaju niż opiekun zablokowany rezerwacją własnego opiekuna; `ticket_limit_of(karta)` —
    limit ticketów w toku karty; `provider` — tylko wpisy tego dostawcy (bez — wszystkie).
    Wywołania silnika są pamiętane na czas jednego `order`, więc koszt nie rośnie z liczbą wpisów."""
    memo: dict = {}

    def once(name, fn, *args):
        if fn is None:
            return None
        k = (name, *args)
        if k not in memo:
            memo[k] = fn(*args)
        return memo[k]

    out = []
    for card in sorted(states):
        st = states[card]
        if st.card_finished or st.paused or st.moved_to in GONE_COLUMNS:
            continue
        waits = [(k, w) for k, w in st.waiting.items()
                 if w.get("cause") in QUEUED_CAUSES and w.get("provider") and
                 (provider is None or w.get("provider") == provider)]
        if not waits:
            continue
        running = {(s.get("ticket"), s.get("kind")) for s in st.running()}
        base = baseproposal.blocked(st)
        for (ticket, kind), wait in waits:
            ticket = ticket or ""
            if (ticket, kind) in running or (ticket and _gone(st, ticket)):
                continue
            entry = Entry(card, ticket, kind or "", wait["provider"], wait.get("since") or "",
                          of(once("priority", priority_of, card)))
            cause, held = ("base", HELD_BASE) if base else ("quota", once("quota", quota_of, entry.provider) or "")
            if not held and kind == "ticket":
                cause, held = "ticket", _new_ticket_held(st, ticket, once("limit", ticket_limit_of, card))
            if not held and kind != CARETAKER and reserved_of is not None and \
                    once("reserved", lambda c, _p: reserved_of(c, st, entry), card, entry.provider):
                cause, held = CARETAKER, HELD_CARETAKER
            if held:
                out.append(replace(entry, cause=cause, reason=held))
            else:
                reason = wait.get("reason") if wait.get("cause") == SLOTS else ""
                out.append(replace(entry, reason=reason or WAITING_SLOT.format(provider=entry.provider)))
    out.sort(key=key)
    return out


def ahead(entries: Iterable[Entry], mine: Entry) -> int:
    """Ile kroków innych kart stoi w kolejce przed `mine` u tego samego dostawcy (liczą się jak trwające kroki)."""
    mine_key = key(mine)
    return sum(1 for e in entries if e.queued and e.provider == mine.provider and e.card != mine.card and
               key(e) < mine_key)
