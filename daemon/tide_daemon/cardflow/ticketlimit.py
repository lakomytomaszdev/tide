"""Limit równoległych ticketów na kartę (PT-375, K-39 #5).

Karta nie uruchamia więcej ticketów naraz, niż pozwala limit: ustawienie przepływu `ticketsPerCard` (1..6, domyślnie 2)
albo nadpisanie na karcie (`max-tickets`, 1..6). Ticket w toku to ticket planu z co najmniej jednym krokiem,
niescalony i nieodrzucony — także czekający na decyzję użytkownika, review, poprawki i scalenie. Limit dotyczy tylko
startu nowego ticketu w `_plan_next`; kolejne kroki ticketu w toku (review, poprawki, scalenie, opiekun) nim nie są
wstrzymywane, a zmniejszenie limitu niczego nie zatrzymuje.
"""
from __future__ import annotations

MIN_LIMIT, MAX_LIMIT, DEFAULT_LIMIT = 1, 6, 2
CAUSE = "card-limit"      # `step.waiting.cause` ticketu, który czeka na miejsce w karcie


def valid(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and MIN_LIMIT <= value <= MAX_LIMIT


def default_of(value) -> int:
    """`ticketsPerCard` z ustawień przepływu; brak albo wartość spoza 1..6 (także bool i ułamek) — domyślne 2."""
    return value if valid(value) else DEFAULT_LIMIT


def problem(value, what: str = "liczba ticketów naraz na kartę") -> str:
    """Opis błędu wartości do `bad-settings`/`bad-request`; pusty — poprawna wartość."""
    if not valid(value):
        return f"{what} musi być liczbą całkowitą od {MIN_LIMIT} do {MAX_LIMIT}"
    return ""


def effective(card_max, default) -> int:
    """Limit obowiązujący kartę: `max-tickets` karty (gdy poprawny), inaczej ustawienie domyślne."""
    return card_max if valid(card_max) else default_of(default)


def in_progress(st, rows) -> list[str]:
    """Tickety planu w toku: mają krok, nie są scalone ani odrzucone (kolejność planu)."""
    started = {step.get("ticket") for step in st.steps.values()}
    merged = set(st.imported_done)
    out = []
    for row in rows:
        ticket = row["id"]
        if ticket in merged or ticket in st.rejected or (st.results.get(("merge", ticket)) or {}).get("ok"):
            continue
        if ticket in started:
            out.append(ticket)
    return out


def wait_reason(count: int, limit: int) -> str:
    return f"czeka na miejsce w karcie ({count} z {limit})"
