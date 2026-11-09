"""Pole `boxWait` stanu karty (PT-172, karta K-38): na co karta czeka, zanim jej krok wejdzie do pudełka.

Wartości (jedno źródło dla pakietu zgód i silnika #18):
- `environment` — „Tylko plan”: plan zatwierdzony, środowisko nie. Ustawia i czyści PT-172 (`ApproveCardEnvironment`),
- `start` — pudełko zatrzymane, trwa start. Ustawia i czyści #18 (silnik),
- `rebuild` — hash przepisu niezgodny, pudełko czeka na przebudowę zatwierdzoną z planem. Ustawia i czyści #18.
Brak pola (pusty napis) — nic nie blokuje.

Pole jest w dzienniku karty jako zdarzenie `box.wait` z `value` (pusty = wyczyszczone) i składa się w `FlowState.box_wait`.
Czyta je wyłącznie `card_box_wait(card)`; #18 woła tę funkcję, nie czyta dziennika sam.

D1 (PT-290): oczekiwanie `environment` po zatwierdzeniu planu powstaje tylko przy `box.enabled: true`
(`require_environment`). Przy wyłączonym pudełku plan jest zatwierdzany bez oczekiwania; dziennik dostaje wtedy
`box.wait` z pustym `value` i `deferred: environment` (zgoda odroczona, `FlowState.box_env_deferred`). Odroczenie
niczego nie blokuje przy wyłączonym pudełku; po ponownym włączeniu silnik zamienia je na `environment`, zanim krok
wykonawczy ruszy (`environment_pending`) — samo zatwierdzenie planu nie staje się zgodą na środowisko. Kasuje je
dopiero następna zmiana `boxWait` (zgoda środowiska przy włączonym pudełku albo zamiana na `environment`).
Opis: docs/tide-daemon.md (Pakiet zgód: środowisko karty).
"""
ENVIRONMENT, START, REBUILD = "environment", "start", "rebuild"
VALUES = (ENVIRONMENT, START, REBUILD)

LABELS = {ENVIRONMENT: "czeka na środowisko", START: "czeka na pudełko", REBUILD: "do przebudowy"}

EVENT = "box.wait"
DEFERRED = "deferred"     # pole `box.wait` (PT-290): `environment` — zgoda środowiska odroczona przy wyłączonym pudełku


def label(value: str) -> str:
    return LABELS.get(value, "")


def environment_deferred(card) -> bool:
    """Plan zatwierdzony przy `box.enabled: false` bez późniejszej zgody środowiska (PT-290)."""
    return bool(getattr(card, "box_env_deferred", False))


def environment_pending(card) -> bool:
    """Karta nie ma zgody środowiska: czeka na nią (`environment`) albo zgoda jest odroczona (PT-290)."""
    return card_box_wait(card) == ENVIRONMENT or environment_deferred(card)


def require_environment(engine, card_id: str, source: str) -> str:
    """D1 (PT-290): po zatwierdzeniu planu (nowego albo poprawki) karta wymaga zgody środowiska.

    Przy `box.enabled: true` ustawia `boxWait: environment`. Przy wyłączonym pudełku nie tworzy oczekiwania
    i nie blokuje hostowego kroku; zapisuje tylko odroczenie (o ile karta nie czeka już na środowisko). Istniejące
    oczekiwanie z dawnej historii zostaje bez zmian. Zwraca bieżącą wartość `boxWait`."""
    from .boxstep import settings_of
    from .state import fold
    if settings_of(engine).enabled:
        set_box_wait(engine.journal, card_id, ENVIRONMENT, source=source)
        return ENVIRONMENT
    st = fold(engine.journal.read(card_id))
    if not environment_pending(st):
        set_box_wait(engine.journal, card_id, "", source=source, **{DEFERRED: ENVIRONMENT})
    return card_box_wait(st)


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
    """Zapisuje zmianę `boxWait` w dzienniku karty; `value == ""` czyści (także odroczenie PT-290).
    Bez zdarzenia, gdy nic się nie zmienia."""
    from .state import fold
    if value and value not in VALUES:
        raise ValueError(f"nieznana wartość boxWait: {value!r}")
    deferred = extra.get(DEFERRED) == ENVIRONMENT
    if DEFERRED in extra and (value or not deferred):
        raise ValueError("odroczenie zgody środowiska tylko z pustym boxWait")
    st = fold(journal.read(card_id))
    if card_box_wait(st) == value and environment_deferred(st) == deferred:
        return None
    return journal.append(card_id, EVENT, value=value, **extra)
