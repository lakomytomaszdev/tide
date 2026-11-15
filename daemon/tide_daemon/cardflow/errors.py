"""Wyjątki przepływu karty (PT-461): liść bez importów usługi.

Moduły pomocnicze i kod poza pakietem rzucają i łapią błędy przepływu przez ten moduł, więc nie ciągną silnika
(`engine.py`). Silnik importuje te same klasy i zostawia ich nazwy, więc `engine.FlowError is errors.FlowError`.
"""


class FlowDeferred(Exception):
    """Krok czeka na wolne miejsce; nie jest to błąd karty."""


class FlowError(Exception):
    def __init__(self, code: str, message: str, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.data = data or {}     # dodatkowe pola odpowiedzi (np. `reviews` przy `ambiguous-review`)
