"""Zgody ticketu: trwała projekcja, granica zlecenia i kontrola przed startem kolejki."""

GRANTED = "outside.ticket-consent"
REVOKED = "outside.ticket-consent-revoked"
INVALIDATED = "outside.decision-invalidated"
SKIPPED = "outside.auto-skipped"
RUN = ("run", "run-always", "run-ticket")
# Powody pominięcia automatycznej zgody outside (PT-311): odmowa człowieka i wstrzymana karta.
DENIED_BEFORE = "denied-before"
PAUSED = "paused"


def apply(st, event):
    """Ostatnia decyzja w bieżącym kontekście wygrywa; stare zdarzenia nadania też są czytelne."""
    if (event.get("orderSeq", (st.started or {}).get("seq")) != (st.started or {}).get("seq")
            or event.get("baseVersion", st.base_version) != st.base_version):
        return
    ticket = event.get("ticket")
    if not ticket:
        return
    st.ticket_consents[ticket] = event
    if event["type"] == REVOKED:
        resumed = {s.get("parentStep") for s in st.steps.values()}
        for name, decision in list(st.step_decisions.items()):
            if ((st.steps.get(name) or {}).get("ticket") == ticket
                    and name not in st.outside_launched and name not in resumed
                    and dependent(decision)):
                st.step_decisions.pop(name)


def dependent(decision):
    return decision.get("choice") in ("run", "run-always", "run-ticket") and (
        decision.get("auto") or decision.get("choice") == "run-ticket")


def automatic(engine, card, step, command):
    from .state import fold
    from ..board.commands import outside_allowed, outside_matches, outside_risky
    st = fold(engine.journal.read(card))
    consent = st.ticket_consents.get(step.get("ticket")) or {}
    if (consent.get("type") == REVOKED or step.get("step") in st.superseded
            or st.base_change and step.get("baseVersion", 0) != st.base_version):
        return False
    project = engine._project(card)[1]
    roots = (step.get("worktree") or "", project.repo, step.get("stepDir") or "")
    return outside_allowed(command, project.denied_commands, roots) and (
        consent.get("type") == GRANTED
        or outside_matches(command, engine._outside_remembered(project.slug))
        or engine._auto_outside(card) and not outside_risky(command, roots, engine._extra_repos(card)))


def _plain(command):
    return " ".join(str(command or "").split())


def denied_before(events, st, step, command):
    """Człowiek (panel albo koordynator, bez `auto`) odmówił już tego polecenia w tym tickecie bieżącego zlecenia
    i wersji bazy (PT-311). Automatyczna odmowa Tide, opiekun i inny ticket nie blokują zgody."""
    ticket = step.get("ticket") or ""
    wanted = _plain(command)
    order = (st.started or {}).get("seq")
    if not ticket or not wanted or order is None:
        return False
    invalidated = {e.get("decisionSeq") for e in events if e.get("type") == INVALIDATED}
    for e in events:
        if (e.get("type") != "step.decision" or e.get("choice") != "deny" or e.get("auto")
                or e.get("caretaker") or e.get("ticket") != ticket or e.get("seq") in invalidated
                or not isinstance(e.get("seq"), int) or e["seq"] <= order or _plain(e.get("command")) != wanted):
            continue
        denied = st.steps.get(e.get("step")) or {}
        if e.get("step") not in st.superseded and denied.get("baseVersion", 0) == st.base_version:
            return True
    return False


def skip_reason(engine, card, step, command):
    """Powód, dla którego automatyczna zgoda outside nie powstaje mimo reguł (`paused`, `denied-before`), albo ""."""
    events = engine.journal.read(card)
    from .state import fold
    st = fold(events)
    if st.paused:
        return PAUSED
    return DENIED_BEFORE if denied_before(events, st, step, command) else ""


def record_automatic(engine, card, step, command):
    """Zapis automatycznej zgody outside (wynik kroku, recheck): reguły, odmowa człowieka i pauza sprawdzone pod
    tą samą blokadą dziennika co zapis — odmowa ani wstrzymanie nie wejdą pomiędzy kontrolę a decyzję.
    Pominięcie zapisuje powód raz na krok i powód (recheck biegnie przy każdym przebiegu)."""
    name = step.get("step") or ""
    with engine.journal.lock:
        if not automatic(engine, card, step, command):
            return False
        reason = skip_reason(engine, card, step, command)
        if reason:
            last = next((e for e in reversed(engine.journal.read(card))
                         if e.get("type") == SKIPPED and e.get("step") == name), None)
            if (last or {}).get("reason") != reason:
                engine.journal.append(card, "outside.auto-skipped", step=name, ticket=step.get("ticket") or "",
                                      command=command, reason=reason)
            return False
        engine.journal.append(card, "step.decision", step=name, ticket=step.get("ticket") or "",
                              kind=step.get("kind") or "", choice="run", answer="", command=command,
                              executor="", auto=True)
        return True


def deny_queued(engine, card, name, request):
    """Odmowa człowieka po automatycznej zgodzie outside (PT-311): przed `outside.started` unieważnia zgodę
    i zapisuje odmowę; po starcie `already-started`, a proces działa dalej. Kontrola i zapis są pod blokadą
    dziennika, pod którą `_start_outside` sprawdza decyzję i zapisuje start."""
    from .engine import FlowError
    answer = str(request.get("answer") or "").strip()
    instead = str(request.get("instead") or "").strip()
    if len(answer) > 4000 or len(instead) > 4000:
        raise FlowError("bad-answer", "odpowiedź musi mieć do 4000 znaków")
    with engine.journal.lock:
        st = engine.journal.state(card)
        step = st.steps.get(name) or {}
        prior = st.step_decisions.get(name) or {}
        if st.card_finished or not step:
            raise FlowError("no-step-question", f"{card}: ten krok nie czeka na taką decyzję")
        if name in st.outside_launched:
            raise FlowError("already-started", "polecenie poza piaskownicą już się uruchomiło — działa dalej")
        if prior.get("choice") not in RUN or not prior.get("auto"):
            raise FlowError("already-decided", f"{card}: odpowiedź na ten krok jest już zapisana")
        engine._trial_check(step)
        done = st.finished.get(name) or {}
        engine.journal.append(card, "outside.decision-invalidated", step=name, ticket=step.get("ticket") or "",
                              decisionSeq=prior.get("seq"), reason="denied")
        event = engine.journal.append(card, "step.decision", step=name, ticket=step.get("ticket") or "",
                                      choice="deny", answer=answer, command=done.get("command") or "",
                                      executor="", **({"instead": instead} if instead else {}))
    engine._outside_waiting.pop(name, None)
    return {"ok": True, "id": card, "step": name, "choice": "deny", "seq": event["seq"], "invalidated": True}


def before_start(engine, card, step, done, decision):
    """Wywoływane tylko przed startem: proces już uruchomiony zachowuje swoją zgodę."""
    from .state import fold
    from ..board.commands import outside_allowed
    events = engine.journal.read(card)
    st = fold(events)
    name = step.get("step") or ""
    if (name in st.superseded or st.base_change and step.get("baseVersion", 0) != st.base_version
            or decision.get("seq") is not None
            and (st.step_decisions.get(name) or {}).get("seq") != decision["seq"]):
        engine._outside_waiting.pop(name, None)
        return False
    consent = st.ticket_consents.get(step.get("ticket")) or {}
    project = engine._project(card)[1]
    roots = (step.get("worktree") or "", project.repo, step.get("stepDir") or "")
    allowed = outside_allowed(done.get("command") or "", project.denied_commands, roots)
    revoked = consent.get("type") == REVOKED and dependent(decision)
    reason = ""
    if decision.get("auto"):
        allowed = allowed and automatic(engine, card, step, done.get("command") or "")
        # Zgoda zapisana przed odmową człowieka (np. w innym kroku ticketu) nie startuje (PT-311).
        if allowed and denied_before(events, st, step, done.get("command") or ""):
            allowed, reason = False, DENIED_BEFORE
    if allowed and not revoked:
        return True
    if name in st.step_decisions:
        engine.journal.append(card, "outside.decision-invalidated", step=name, ticket=step.get("ticket") or "",
                              decisionSeq=decision.get("seq"), **({"reason": reason} if reason else {}))
    engine._outside_waiting.pop(name, None)
    return False


def revoke(engine, card_id, request):
    from .engine import FlowError
    from .state import fold
    from ..board.model import BoardError, card_num
    try:
        card = f"K-{card_num(card_id)}"
    except BoardError as exc:
        raise FlowError(exc.code, str(exc)) from None
    if not isinstance(request, dict) or set(request) != {"ticket", "orderSeq", "baseVersion"}:
        raise FlowError("bad-request", "podaj ticket, orderSeq i baseVersion")
    events = engine.journal.read(card)
    st = fold(events)
    if (not st.started or type(request["orderSeq"]) is not int
            or request["orderSeq"] != st.started.get("seq")
            or type(request["baseVersion"]) is not int or request["baseVersion"] != st.base_version):
        raise FlowError("stale-context", "zgoda należy do innego zlecenia lub wersji bazy")
    tickets = {r.get("id") for r in (st.plan_approved or {}).get("rows") or []}
    tickets.update(e.get("ticket") for e in events if e.get("type") == "result"
                   and e.get("action") == "reserve" and e.get("ok")
                   and e.get("seq", 0) > st.started["seq"])
    ticket = request["ticket"]
    if not isinstance(ticket, str) or not ticket or ticket not in tickets:
        raise FlowError("bad-ticket", "ticket nie należy do bieżącego zlecenia karty")
    if st.card_finished:
        raise FlowError("stale-context", "zlecenie karty jest zakończone")
    event = st.ticket_consents.get(ticket) or {}
    if event.get("type") != REVOKED:
        event = engine.journal.append(card, REVOKED, **request)
    for name, step in st.steps.items():
        if step.get("ticket") == ticket and name not in st.outside_started:
            engine._outside_waiting.pop(name, None)
    return {"ok": True, "ticket": ticket, "active": False, "seq": event["seq"]}


def view(st):
    return [{"ticket": ticket, "active": event["type"] == GRANTED,
             "revoked": event["type"] == REVOKED, "seq": event["seq"],
             "canRevoke": event["type"] == GRANTED and not st.card_finished,
             "orderSeq": (st.started or {}).get("seq"), "baseVersion": st.base_version}
            for ticket, event in st.ticket_consents.items()]
