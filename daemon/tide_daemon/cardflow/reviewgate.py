"""Polityka recenzenta i autorzy ocenianej pracy. Ustawienie projektu podłączy PT-330.

Osobna sesja jest obowiązkiem startu review, niezależnym od polityki modelu.
Tożsamości autorów pochodzą z dziennika, nie z obecnych nazw w katalogu.
"""
from types import SimpleNamespace

from .plans import independent_reviewer


def authors(st, ticket: str, round_no: int) -> list[dict]:
    """Autorzy ticketu i wszystkie próby poprawek od poprzedniej rundy review.

    Wznowienie review tej samej rundy nie przesuwa granicy: nadal ocenia te same poprawki.
    Nie pomijamy nieudanych prób fix — mogły pozostawić WIP.
    """
    steps = [s for s in st.steps.values() if s.get("ticket") == ticket
             and s.get("step") not in st.superseded]
    boundary = max((s.get("seq", 0) for s in steps if s.get("kind") == "review"
                    and int(s.get("round") or 0) < round_no), default=0)
    return [s for s in steps if s.get("kind") == "ticket" or
            (s.get("kind") == "fix" and s.get("seq", 0) > boundary)]


def permits(reviewer, authored: list[dict], policy: str) -> bool:
    """Jedyny warunek modelu/rodziny nowej bramki; `other-model` zachowuje semantykę planu.

    Rodzina to dostawca/program z katalogu (dla wykonanych kroków zamrożone w dzienniku),
    nie nazwa wykonawcy. Nieznaną politykę waliduje wywołujący; tutaj nie osłabiamy jej po cichu.
    """
    if policy == "free":
        return True
    if policy == "other-model":
        return all(a.get("model") and independent_reviewer(
            SimpleNamespace(model=a["model"], effort=a.get("effort", "")), reviewer) for a in authored)
    if policy == "other-family":
        family = (reviewer.provider, reviewer.tool)
        return all(a.get("provider") and a.get("program") and
                   (a["provider"], a["program"]) != family for a in authored)
    raise ValueError(f"nieznana polityka review: {policy}")
