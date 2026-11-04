"""Pole `boxWait` stanu karty (PT-172, karta K-38): na co karta czeka, zanim jej krok wejdzie do pudełka.

Wartości (jedno źródło dla pakietu zgód i silnika #18):
- `environment` — „Tylko plan”: plan zatwierdzony, środowisko nie. Ustawia i czyści PT-172 (`ApproveCardEnvironment`),
- `start` — pudełko zatrzymane, trwa start. Ustawia i czyści #18 (silnik),
- `rebuild` — hash przepisu niezgodny, pudełko czeka na przebudowę zatwierdzoną z planem. Ustawia i czyści #18.
Brak pola (pusty napis) — nic nie blokuje.

Pole jest w dzienniku karty jako zdarzenie `box.wait` z `value` (pusty = wyczyszczone) i składa się w `FlowState.box_wait`.
Czyta je wyłącznie `card_box_wait(card)`; #18 woła tę funkcję, nie czyta dziennika sam.
Opis: docs/tide-daemon.md (Pakiet zgód: środowisko karty).
"""
ENVIRONMENT, START, REBUILD = "environment", "start", "rebuild"
VALUES = (ENVIRONMENT, START, REBUILD)

LABELS = {ENVIRONMENT: "czeka na środowisko", START: "czeka na pudełko", REBUILD: "do przebudowy"}

EVENT = "box.wait"


def label(value: str) -> str:
    return LABELS.get(value, "")


def card_box_wait(card) -> str:
    """Wartość `boxWait` karty: `""` albo jedna z `VALUES`. `card` to `FlowState` (pole `box_wait`), słownik karty
    z `boxWait` albo z `derived.flow.boxWait`; nieznana wartość liczy się jak brak."""
    if isinstance(card, dict):
        flow = (card.get("derived") or {}).get("flow") or {}
        value = card.get("boxWait") or flow.get("boxWait") or ""
    else:
        value = getattr(card, "box_wait", "") or ""
    return value if value in VALUES else ""


def set_box_wait(journal, card_id: str, value: str, **extra) -> dict | None:
    """Zapisuje zmianę `boxWait` w dzienniku karty; `value == ""` czyści. Bez zdarzenia, gdy nic się nie zmienia."""
    from .state import fold
    if value and value not in VALUES:
        raise ValueError(f"nieznana wartość boxWait: {value!r}")
    if card_box_wait(fold(journal.read(card_id))) == value:
        return None
    return journal.append(card_id, EVENT, value=value, **extra)
