"""Zgody ticketu: trwała projekcja, granica zlecenia i kontrola przed startem kolejki."""

GRANTED = "outside.ticket-consent"
REVOKED = "outside.ticket-consent-revoked"


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


def before_start(engine, card, step, done, decision):
    """Wywoływane tylko przed startem: proces już uruchomiony zachowuje swoją zgodę."""
    from .state import fold
    from ..board.commands import outside_allowed
    st = fold(engine.journal.read(card))
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
    if decision.get("auto"):
        allowed = allowed and automatic(engine, card, step, done.get("command") or "")
    if allowed and not revoked:
        return True
    if name in st.step_decisions:
        engine.journal.append(card, "outside.decision-invalidated", step=name,
                              ticket=step.get("ticket") or "", decisionSeq=decision.get("seq"))
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
