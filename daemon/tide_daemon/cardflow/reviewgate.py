"""Polityka recenzenta projektu i autorzy ocenianej pracy (PT-312, PT-330, decyzja 97).

Osobna sesja jest obowiązkiem startu review, niezależnym od polityki modelu.
Tożsamości autorów pochodzą z dziennika, nie z obecnych nazw w katalogu.
Polityka pochodzi z ustawienia projektu Tablicy `review-independence` (JSON `reviewIndependence`):
`free` (domyślne), `other-model` albo `other-family` (opcjonalnie tylko dla klas z `review-independence-classes`).
"""
import logging
from types import SimpleNamespace

from .plans import independent_reviewer

log = logging.getLogger(__name__)

FREE, OTHER_MODEL, OTHER_FAMILY = "free", "other-model", "other-family"
POLICIES = (FREE, OTHER_MODEL, OTHER_FAMILY)
DEFAULT = FREE
_warned: set = set()


def project_policy(project, klass: str | None = None) -> str:
    """Skuteczna polityka projektu dla ticketu klasy `klass`.

    Brak projektu albo ustawienia — `free`. Nieznana wartość — `free` z ostrzeżeniem w logu (raz na wartość
    i plik projektu). Lista klas zawęża tylko `other-family`: ticket spoza listy dostaje `free`. `klass=None`
    (review planu, który obejmuje wszystkie klasy) bierze politykę bez zawężenia klas.
    Projekt czytamy przy każdym wywołaniu z bieżącej migawki Tablicy, więc zmiana nie wymaga restartu usługi.
    """
    raw = " ".join(str(getattr(project, "review_independence", "") or "").lower().split())
    if not raw:
        return DEFAULT
    if raw not in POLICIES:
        key = (getattr(project, "path", "") or getattr(project, "slug", ""), raw)
        if key not in _warned:
            _warned.add(key)
            log.warning("projekt %s: nieznana polityka review „%s” (%s) — stosuję %s",
                        getattr(project, "slug", "?"), raw, ", ".join(POLICIES), DEFAULT)
        return DEFAULT
    classes = list(getattr(project, "review_independence_classes", None) or ())
    if raw == OTHER_FAMILY and classes and klass is not None and (klass or "standard") not in classes:
        return DEFAULT
    return raw


def family(executor) -> tuple[str, str]:
    """Rodzina wykonawcy: para dostawca/program z katalogu (`Execution.tool` albo `Executor.program`)."""
    program = getattr(executor, "tool", None)
    if program is None:
        program = getattr(executor, "program", "")
    return (getattr(executor, "provider", "") or "", program or "")


def as_author(executor) -> dict:
    """Wykonawca z katalogu w kształcie zapisanego kroku autora (dla `permits`)."""
    provider, program = family(executor)
    return {"model": getattr(executor, "model", "") or "", "effort": getattr(executor, "effort", "") or "",
            "provider": provider, "program": program}


def allows(author, reviewer, policy: str) -> bool:
    """`permits` dla pary wykonawców z katalogu (plan, okno, Od razu, wybór recenzenta)."""
    return permits(reviewer, [as_author(author)], policy)


def rule(policy: str) -> str:
    """Wymóg polityki w zdaniu dla użytkownika."""
    return {OTHER_MODEL: "recenzent musi mieć inny model albo inną siłę myślenia niż autor",
            OTHER_FAMILY: "recenzent musi należeć do innej rodziny (dostawca/program z katalogu) niż autor"}.get(
        policy, "dowolny recenzent")


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
    """Jedyny warunek modelu/rodziny recenzenta; `other-model` zachowuje semantykę planu.

    Rodzina to dostawca/program z katalogu (dla wykonanych kroków zamrożone w dzienniku),
    nie nazwa wykonawcy. Nieznaną politykę normalizuje `project_policy`; tutaj nie osłabiamy jej po cichu.
    """
    if policy == FREE:
        return True
    if policy == OTHER_MODEL:
        return all(a.get("model") and independent_reviewer(
            SimpleNamespace(model=a["model"], effort=a.get("effort", "")), reviewer) for a in authored)
    if policy == OTHER_FAMILY:
        mine = family(reviewer)
        return all(a.get("provider") and a.get("program") and
                   (a["provider"], a["program"]) != mine for a in authored)
    raise ValueError(f"nieznana polityka review: {policy}")
