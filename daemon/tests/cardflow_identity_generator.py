"""PT-288: generator historii z modelu tożsamości procesów.

Dla każdego zdarzenia procesu i każdego pola z modelu budujemy kompletną poprawną historię
bazową, a następnie mutacje: brak, null, pusty tekst, spacje, lista, liczba i inna poprawna
wartość. Oczekiwany wynik wynika wyłącznie z roli pola (`expected`), nie z uruchomienia
reduktora. Dla zdarzeń zależnych usuwamy, przestawiamy i podmieniamy poprzednika, dodajemy
duplikaty i poprawny wpis po wadliwym. Każda historia występuje dla zwykłego (PT-999)
i chronionego (PT-176) ticketu.

Generator czyta model (role pól) — to on jest testowany. Niezależna kontrola kompletności
modelu względem kodu zapisującego dziennik jest w `cardflow_identity_catalog.py`.
"""
from __future__ import annotations

from dataclasses import dataclass

from tide_daemon.cardflow import processidentity as pi

STEP = "legacy-step"
OTHER_STEP = "other-step"
TICKETS = ("PT-999", "PT-176")
VARIANTS = ("missing", "null", "empty", "spaces", "list", "number", "other")
LEGAL, BAD = "legal", "bad"


@dataclass(frozen=True)
class Case:
    kind: str           # rodzaj zdarzenia (intent/result jako intent:step-start)
    field: str          # pole albo nazwa wariantu poprzednika
    variant: str
    ticket: str
    history: tuple
    expected: str       # legal / bad
    exact: str = ""     # oczekiwany dokładny stan, gdy wynika z roli
    group: str = "field"   # field / predecessor / duplicate / repair


def _card():
    return {"type": "card.started", "project": "proj"}


def _start(ticket, **extra):
    return {"type": "step.started", "step": STEP, "ticket": ticket, "kind": "ticket", "branch": "ticket/ordinary",
            "worktree": "/work", "stepDir": "/step", "runner": "box", "box": "proj", "boxSlug": "proj",
            "card": "K-38", "unit": "step.scope", "session": "s", "executor": "e", **extra}


def _intent(ticket):
    event = {**_start(ticket), "type": "intent", "action": "step-start", "key": STEP}
    del event["step"]
    return event


def _result():
    return {"type": "result", "action": "step-start", "key": STEP, "ok": True, "unit": "step.scope",
            "runner": "box", "box": "proj", "boxSlug": "proj", "gitDir": "/git"}


def _entered():
    return {"type": "step.entered", "step": STEP, "consoleUnit": "console.scope", "processFile": "/console.json",
            "session": "s", "source": "", "closeFile": "/closed", "taskFile": "/task.json"}


def _request(ticket):
    return {"type": "step.finished", "step": STEP, "ticket": ticket, "result": "denied", "outside": True,
            "command": "echo legacy", "canAllow": True}


def _decision(ticket):
    return {"type": "step.decision", "step": STEP, "ticket": ticket, "kind": "ticket", "choice": "run",
            "command": "", "executor": ""}


def _outside_start(ticket):
    return {"type": "outside.started", "step": STEP, "ticket": ticket, "command": "echo legacy",
            "unit": "outside.scope", "log": "/missing/log", "exitFile": "/missing/out/exit", "worktree": "/work"}


def _outside_finish(ticket):
    return {"type": "outside.finished", "step": STEP, "ticket": ticket, "command": "echo legacy", "exit": 0,
            "tail": ""}


def base(kind, ticket):
    """(poprzedniki, zdarzenie) kompletnej poprawnej historii z badanym zdarzeniem na końcu."""
    console = _start(ticket, unit="")          # runda 8: pusty unit kroku przed Konsolą
    if kind == "step.started":
        return [_card()], _start(ticket)
    if kind == "intent:step-start":
        return [_card()], _intent(ticket)
    if kind == "result:step-start":
        return [_card(), _intent(ticket)], _result()
    if kind == "step.entered":
        return [_card(), console], _entered()
    if kind in ("step.console_opened", "step.console_closed"):
        return [_card(), console, _entered()], {"type": kind, "step": STEP}
    if kind == "step.finished":
        return [_card(), _start(ticket)], _request(ticket)
    if kind == "step.decision":
        return [_card(), _start(ticket), _request(ticket)], _decision(ticket)
    if kind == "outside.started":
        return [_card(), _start(ticket), _request(ticket), _decision(ticket)], _outside_start(ticket)
    if kind == "outside.finished":
        return ([_card(), _start(ticket), _request(ticket), _decision(ticket), _outside_start(ticket)],
                _outside_finish(ticket))
    if kind == "outside.ticket-consent":
        return [_card(), _start(ticket)], {"type": kind, "step": STEP, "ticket": ticket, "baseVersion": 1,
                                           "orderSeq": 3}
    if kind == "card.started":
        return [], _card()
    raise KeyError(kind)


def model_kinds():
    """Zdarzenia procesu (i kontekstu) z modelu; zamiar/wynik według ACTIONS."""
    kinds = [k for k, e in pi.EVENTS.items() if e.role in pi.PROCESS_ROLES - {"action"} or e.role == "context"]
    kinds += [f"{kind}:{action}" for action, spec in pi.ACTIONS.items() if spec.role == "step-start"
              for kind in ("intent", "result")]
    return kinds


def fields_of(kind):
    if ":" in kind:
        source, action = kind.split(":")
        spec = pi.ACTIONS[action]
        return spec.intent if source == "intent" else spec.result
    return pi.EVENTS[kind].fields


def is_start(kind):
    return kind in ("step.started", "intent:step-start")


def mutate(event, field, variant):
    event = dict(event)
    if variant == "missing":
        event.pop(field, None)
        return event
    other = {"ticket": "PT-998", "kind": "review", "runner": "host", "outside": False, "choice": "deny",
             "action": "reserve", "project": "other", "step": OTHER_STEP, "key": OTHER_STEP}.get(field, "other.value")
    event[field] = {"null": None, "empty": "", "spaces": "   ", "list": [], "number": 17, "other": other}[variant]
    return event


def expected(kind, field, spec, variant, base_event, known=True):
    """Wynik z roli pola: (legal|bad, dokładny stan albo "").

    `known` — czy wcześniejszy dowód tego samego procesu ma już wartość tego pola."""
    present_in_base = field in base_event
    role, typ = spec.role, spec.type
    if role == "neutral":
        return LEGAL, ""
    if variant == "missing" and not present_in_base:
        return LEGAL, ""
    wrong_type = variant in ("null", "list", "number")
    if role == "name":
        return (LEGAL, "") if variant == "other" and is_start(kind) else (BAD, "")
    if role == "discriminator":
        if field == "action":
            # Inna rozpoznana akcja metadanych to legalne przeklasyfikowanie zdarzenia.
            return (LEGAL, "") if variant == "other" else (BAD, "unknown")
        if typ == "bool":
            return (LEGAL, "") if variant in ("missing", "other") else (BAD, "unknown")
        return (BAD, "unknown") if wrong_type else (LEGAL, "")
    if role == "context":
        return (LEGAL, "") if variant == "other" else (BAD, "unknown")
    if role == "own":
        if variant == "other":
            return LEGAL, ""
        return BAD, "unknown"
    if role == "inherited":
        if variant == "missing":
            return LEGAL, ""
        if variant == "other":
            return BAD, "inconsistent"
        return BAD, "unknown" if wrong_type else ""
    # identity / conditional
    if is_start(kind):
        if spec.domain == "resource":
            # Start deklarujący zasób musi być kompletny i spójny sam w sobie.
            return BAD, ""
        if variant == "missing":
            if spec.role == "conditional":
                return BAD, "unknown"     # ticket kroku ticketu
            return LEGAL, ""
        if wrong_type or variant == "spaces":
            return BAD, ""
        if variant == "empty":
            if spec.nonempty or spec.rule in ("ticket", "kind"):
                return BAD, ""
            return LEGAL, ""
        return LEGAL, ""          # inna poprawna wartość w pierwszym starcie
    if variant == "missing":
        return LEGAL, ""
    if wrong_type:
        return BAD, "unknown"
    if variant == "spaces" or (variant == "empty" and (spec.nonempty or spec.rule == "ticket" and known)):
        return BAD, ""
    if not known:
        return LEGAL, ""          # pierwsza jawna wartość pola w tym procesie
    return BAD, "inconsistent"    # pusty/inna wartość są sprzeczne z kompletem procesu


def field_cases():
    for kind in model_kinds():
        for ticket in TICKETS:
            before, event = base(kind, ticket)
            # Kontekst karty jest sprawdzany na procesie, który po nim startuje.
            after = (_start(ticket),) if kind == "card.started" else ()
            for field, spec in fields_of(kind).items():
                known = any(field in e and e.get("type") != "card.started" for e in before)
                for variant in VARIANTS:
                    outcome, exact = expected(kind, field, spec, variant, event, known)
                    history = (*before, mutate(event, field, variant), *after)
                    yield Case(kind, field, variant, ticket, history, outcome, exact)
                    if outcome == BAD:
                        # Późniejszy poprawny wpis, zakończenie ani duplikat nie przywracają ok.
                        yield Case(kind, field, variant, ticket,
                                   (*history, event, *after, *_closing(kind, ticket)), BAD, "", "repair")


def _closing(kind, ticket):
    if kind == "card.started":
        return ()
    return ({"type": "step.finished", "step": STEP, "ticket": ticket, "result": "done"},)


PREDECESSORS = {
    # rodzaj → nazwy zdarzeń, które muszą wcześniej należeć do tego samego procesu
    "result:step-start": ("intent",),
    "step.entered": ("step.started",),
    "step.console_opened": ("step.started", "step.entered"),
    "step.console_closed": ("step.started", "step.entered"),
    "step.finished": ("step.started",),
    "step.decision": ("step.started",),
    "outside.started": ("step.started",),
    "outside.finished": ("step.started", "outside.started"),
    "outside.ticket-consent": ("step.started",),
}


def predecessor_cases():
    for kind, required in PREDECESSORS.items():
        for ticket in TICKETS:
            before, event = base(kind, ticket)
            for name in required:
                index = next(i for i, e in enumerate(before) if e["type"] == name)
                removed = before[index]
                rest = before[:index] + before[index + 1:]
                yield Case(kind, name, "removed", ticket, (*rest, event), BAD, "unknown", "predecessor")
                yield Case(kind, name, "moved", ticket, (*rest, event, removed), BAD, "unknown", "predecessor")
                other = dict(removed)
                other["key" if "key" in other else "step"] = OTHER_STEP
                yield Case(kind, name, "foreign", ticket, (*rest, other, event), BAD, "", "predecessor")
            # Duplikat kompletnego zdarzenia nie jest błędem; kopia ze sprzeczną wartością jest.
            yield Case(kind, "*", "duplicate", ticket, (*before, event, event), LEGAL, "", "duplicate")


def cases():
    yield from field_cases()
    yield from predecessor_cases()


def legal_histories():
    """Prawidłowe historie kontrolne z legalnymi brakami/dziedziczeniem (do barier dodatnich)."""
    for ticket in ("PT-999",):
        before, event = base("outside.finished", ticket)
        yield "outside-finish-inherits-unit", (*before, event)
        yield "outside-finish-same-unit", (*before, {**event, "unit": "outside.scope"})
        before, event = base("step.console_closed", ticket)
        yield "console-inherits-entered", (*before, {"type": "step.console_opened", "step": STEP}, event,
                                           {"type": "step.finished", "step": STEP, "ticket": ticket,
                                            "result": "done"})
        before, event = base("step.started", ticket)
        yield "start-empty-unit", (*before, {**event, "unit": ""})
        before, event = base("step.decision", ticket)
        yield "trace-omits-identity", (*before, {"type": "step.decision", "step": STEP, "choice": "deny"})
