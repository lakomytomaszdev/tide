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


def waiting_text(kind: object) -> str:
    """Tekst kafelka dla kroku, który czeka na decyzję użytkownika."""
    return f"{kind_label(kind)} czeka na decyzję"
