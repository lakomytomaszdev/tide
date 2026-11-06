"""Trwała granica przekazania do istniejącego StartCard, bez drugiego wykonawcy.

order.reserved poprzedza każdą próbę; card.started wiąże przyjęcie zlecenia
z jego skutkami. Brak odpowiedzi nie dowodzi braku skutków. Tylko zapisane
order.failed bez przyjęcia zlecenia pozwala na nową, jawną próbę.
"""
import hashlib
import json
import os
import stat

from .state import fold, base_of
from .gitops import FlowGitError


def events(engine, card):
    """Przy rozliczaniu startu urwana linia nie może wyglądać jak brak zdarzenia."""
    path = engine.journal.mirror_path(card)
    try:
        os.lstat(path)
    except FileNotFoundError:
        path = engine.journal.path(card)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    with os.fdopen(fd, encoding="utf-8") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("dziennik nie jest zwykłym plikiem")
        text = stream.read()
    out = []
    for line in text.splitlines(keepends=True):
        event = json.loads(line)
        if not line.endswith("\n") or not isinstance(event, dict) or event.get("seq") != len(out) + 1:
            raise ValueError("niepełny dziennik zlecenia")
        out.append(event)
    return out


def generation(engine, card, log=None):
    log = events(engine, card) if log is None else log
    state = fold(log)
    item, project = engine._project(card)
    return {"cardHash": hashlib.sha256(item.doc.text().encode("utf-8")).hexdigest(),
            "repo": project.repo, "startSeq": (state.started or {}).get("seq", 0),
            "baseVersion": state.base_version, "base": base_of(state)}


def projection(engine, card, order_id):
    from .engine import FlowError
    try:
        log = events(engine, card)
        st = fold(log)
        begun = next((e for e in log if e["type"] == "card.started" and e.get("orderId") == order_id), None)
        attempts = [e for e in log if e["type"] == "order.dispatching" and e.get("orderId") == order_id]
        attempt = attempts[-1]["attemptId"] if attempts else None
        common = {"orderId": order_id, **({"attemptId": attempt} if attempt else {})}
        if begun:
            # Zlecenie zostało przyjęte; dalsze skutki rozlicza zwykły silnik karty.
            # Nowsze zlecenie nie zmienia wyniku historycznego przekazania.
            following = [e for e in log if e["seq"] > begun["seq"]]
            next_start = next((e["seq"] for e in following if e["type"] == "card.started"), float("inf"))
            own = [e for e in following if e["seq"] < next_start]
            completed = any(e["type"] in ("card.closed", "card.finished", "card.merged", "card.main-merged") for e in own)
            started = any(e["type"] == "step.started" for e in own)
            failure = next((e for e in reversed(own) if e["type"] == "order.delivery-error"), None)
            if failure and not started and not completed:
                return {**common, "state": "start-error", "retryable": False, "error": failure["error"]}
            return {**common, "state": "completed" if completed else "started" if started else "queued",
                    "retryable": False}
        reserved = next((e for e in log if e["type"] == "order.reserved" and e.get("orderId") == order_id), None)
        failed = next((e for e in reversed(log) if e["type"] == "order.failed" and
                       e.get("orderId") == order_id and e.get("attemptId") == attempt), None)
        if failed and reserved and generation(engine, card, log) == failed["generation"] and not st.active():
            engine.git.resolve_base(reserved["generation"]["repo"], reserved["base"]["baseRef"],
                                    reserved["base"]["baseCommit"])
            return {**common, "state": "start-error", "retryable": True, "error": failed["error"]}
        if not reserved and not attempts:
            return {**common, "state": "prepared", "retryable": False}
        return {**common, "state": "reconciling", "retryable": False}
    except (OSError, ValueError, KeyError, FlowError, FlowGitError):
        return {"orderId": order_id, "state": "reconciling", "retryable": False}


def start(engine, card, request, *, resume=False, order=None):
    from .engine import FlowError
    log = events(engine, card)
    if resume:
        return engine._start(card, request, _resume=True)
    # Zgoda go jest trwała już przy publikacji akceptacji, zanim powstanie
    # order.reserved. Zwykły StartCard nie może wyprzedzić jej po restarcie.
    from ..issues import IssueStore
    from ..issue_acceptance import INTENTS, _read
    item = engine.store.load().cards.get(int(card.removeprefix("K-")))
    if item is not None and getattr(item, "source_issue", ""):
        store = IssueStore(engine.store)
        accepted = (_read(store, f"{INTENTS}/{item.source_issue}.done.json") or
                    _read(store, f"{INTENTS}/{item.source_issue}.json") or {})
        pending = accepted.get("startIntent")
        if pending and pending["orderId"] != (order or {}).get("orderId") and not any(
                e["type"] == "card.started" and e.get("orderId") == pending["orderId"] for e in log):
            raise FlowError("start-reserved", "sprawa ma już zgodę go; sprawdź jej bieżący start")
    reservations = [e for e in log if e["type"] == "order.reserved"]
    for reserved in reservations:
        if reserved["orderId"] != (order or {}).get("orderId") and not any(
                e["type"] == "card.started" and e.get("orderId") == reserved["orderId"] for e in log):
            raise FlowError("start-reserved", "karta ma niedokończone przekazanie; sprawdź bieżący stan sprawy")
    if order is None:
        return engine._start(card, request)
    order_id, attempt = order["orderId"], order["attemptId"]
    current = projection(engine, card, order_id)
    if current["state"] in ("queued", "started", "completed"):
        return current
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    reserved = next((r for r in reservations if r["orderId"] == order_id), None)
    if reserved:
        if reserved["fingerprint"] != fingerprint:
            raise FlowError("conflict", "zarezerwowane zlecenie ma inne ustawienia")
        if current["state"] != "start-error" or not current["retryable"] or current.get("attemptId") == attempt:
            return current
    else:
        reserved = engine.journal.append(card, "order.reserved", orderId=order_id, fingerprint=fingerprint,
                                         generation=generation(engine, card, log),
                                         base={k: request[k] for k in ("baseRef", "baseCommit")})
    engine.journal.append(card, "order.dispatching", orderId=order_id, attemptId=attempt)
    try:
        engine._start(card, request, _order=order_id)
    except Exception as exc:
        after = events(engine, card)
        if any(e["type"] == "card.started" and e.get("orderId") == order_id for e in after):
            engine.journal.append(card, "order.delivery-error", orderId=order_id, attemptId=attempt,
                                  error={"code": getattr(exc, "code", "io-error"), "message": str(exc)})
        # FlowError przed card.started pochodzi ze sprawdzeń StartCard. Błędy
        # zapisu (także po fsync) pozostają niejednoznaczne, nigdy retryable.
        if isinstance(exc, FlowError) and not any(e["type"] == "card.started" for e in after) and \
                generation(engine, card, after) == reserved["generation"]:
            engine.journal.append(card, "order.failed", orderId=order_id, attemptId=attempt,
                                  error={"code": exc.code, "message": str(exc)},
                                  generation=generation(engine, card, after))
    return projection(engine, card, order_id)
