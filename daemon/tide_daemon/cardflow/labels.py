"""Polskie etykiety rodzajów kroków i wyników — jedno miejsce dla tekstów kafelka i widoków przepływu karty."""

from __future__ import annotations

GENERIC_KIND = "krok"
GENERIC_RESULT = "wynik kroku"

KIND_LABELS = {
    "planning": "planowanie",
    "plan-import": "import planu",
    "plan-fix": "poprawa formatu planu",
    "plan-review": "review planu",
    "plan-revise": "poprawa planu",
    "plan-amend": "poprawa planu",
    "ticket": "ticket",
    "review": "review",
    "fix": "poprawki",
    "conflict": "konflikt",
    "close": "domknięcie",
    "interactive": "rozmowa",
    "caretaker": "opiekun",
    "caretaker-chat": "rozmowa z opiekunem",
}

RESULT_LABELS = {
    "done": "gotowe",
    "question": "pytanie",
    "denied": "brak zgody",
    "error": "błąd",
    "stopped": "zatrzymany",
}


def kind_label(kind: object) -> str:
    """Etykieta rodzaju kroku; nieznany lub pusty rodzaj daje etykietę ogólną, nigdy surową wartość."""
    return KIND_LABELS.get(kind, GENERIC_KIND) if isinstance(kind, str) else GENERIC_KIND


def result_label(result: object) -> str:
    """Etykieta wyniku kroku; nieznany lub pusty wynik daje etykietę ogólną."""
    return RESULT_LABELS.get(result, GENERIC_RESULT) if isinstance(result, str) else GENERIC_RESULT


def step_name(kind: object, ticket: object = "") -> str:
    """Krok po ludzku: „PT-7” dla ticketu, „review PT-7”, „review planu”; bez danych — „krok”."""
    ticket = ticket if isinstance(ticket, str) else ""
    if ticket and kind == "ticket":
        return ticket
    return f"{kind_label(kind)} {ticket}".strip()


def tide_fault_text(kind: object, ticket: object = "") -> str:
    """Tekst kafelka dla błędu kroku z winy Tide (PT-125)."""
    return f"błąd Tide w kroku {step_name(kind, ticket)}"


def waiting_text(kind: object, result: object = None) -> str:
    """Tekst kafelka dla kroku czekającego na decyzję; wynik (pytanie, brak zgody, błąd) dopisany w nawiasie."""
    text = f"{kind_label(kind)} czeka na decyzję"
    return text if result is None else f"{text} ({result_label(result)})"
