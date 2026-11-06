"""Decyzja go i jawne retry: niezmienne odpowiedzi oraz osobna projekcja startu."""
import hashlib
import os
import uuid

from . import issue_acceptance as acceptance
from .issues import VersionConflict, validate, fail
from .cardflow import startorder
from .cardflow.engine import FlowError

INTENTS = ".git/tide-issue-starts"
RESULTS = ".git/tide-issue-start-results"


def _key(request_id):
    validate({"title": "request", "report": "", "requestId": request_id})
    return hashlib.sha256(request_id.encode("utf-8")).hexdigest()


def _acceptance(store, ident):
    return (acceptance._read(store, f"{acceptance.INTENTS}/{ident}.done.json") or
            acceptance._read(store, f"{acceptance.INTENTS}/{ident}.json"))


def _load(store, ident):
    intent = acceptance._read(store, f"{INTENTS}/{ident}.json")
    accepted = _acceptance(store, ident)
    if intent is None:
        if accepted and accepted.get("done") and accepted.get("startIntent"):
            intent = accepted["startIntent"]
            _save(store, intent)
    if intent is not None:
        initial = (accepted or {}).get("startIntent") or {}
        immutable = ("intentId", "issue", "card", "acceptedVersion", "launch", "orderId", "acceptRequest")
        issue = store._show(ident)
        if (not isinstance(intent, dict) or any(key not in intent or intent[key] != initial.get(key) for key in immutable) or
                intent["issue"] != ident or issue["state"] != "accepted" or issue["card"] != intent["card"] or
                intent["acceptedVersion"] != accepted["issue"]["version"] or
                type(intent.get("startVersion")) is not int or intent["startVersion"] < 1 or
                intent.get("state") not in ("prepared", "dispatching", "queued", "started", "completed", "start-error", "reconciling") or
                not isinstance(intent.get("attemptId"), str) or not intent["attemptId"]):
            fail("Intencja startu różni się od przyjętej sprawy.", "broken-file")
    return intent


def _save(store, intent):
    acceptance._save(store, f"{INTENTS}/{intent['issue']}.json", intent)


def defaults(engine, project):
    # Poprzednie card.started jest już trwałym zapisem wyborów użytkownika.
    # Nie przenosimy bazy, gałęzi ani projektów poprzedniego zlecenia.
    previous = []
    for card in engine.journal.cards():
        previous += [e for e in engine.journal.read(card) if e["type"] == "card.started" and
                     e.get("project") == project.slug]
    latest = max(previous, key=lambda e: e["ts"], default={})
    cfg = engine.models_config() if engine.models_config else None
    catalog = cfg.catalog if cfg is not None else engine.catalog() if engine.catalog else None
    provider = latest.get("preferredProvider") or ("openai" if project.tool == "codex" else "anthropic")
    name = latest.get("executor")
    if not name and catalog is not None:
        names = [e.name for e in catalog.executors if e.provider == provider]
        wanted = "Codex standard" if provider == "openai" else "Sonnet"
        name = wanted if wanted in names else next(iter(names), "")
    return {"entry": "planning", "mode": "light", "executor": name or "",
            "preferredProvider": provider, "check": latest.get("check") or "auto"}


def _prepare(store, engine, card, issue, launch):
    project = store.board._load().projects.get(card.project)
    if project is None:
        fail("Zlecenie sprawy wymaga projektu z repozytorium.", "no-project")
    if launch is None:
        launch = defaults(engine, project)
    if not isinstance(launch, dict):
        fail("launch musi być obiektem ustawień StartCard.")
    # _prepare przechodzi przez właściwą walidację każdego wejścia, do granicy
    # pierwszego efektu. Nie publikuje wirtualnej karty ani nie rezerwuje numeru.
    pinned = engine._start(card.id, dict(launch), _prepare=card)
    ident = uuid.uuid4().hex
    return {"intentId": ident, "issue": issue["id"], "card": card.id,
            "acceptedVersion": issue["version"], "launch": pinned,
            "wasAccepted": store._show(issue["id"])["state"] == "accepted",
            "orderId": "issue-" + ident, "attemptId": uuid.uuid4().hex,
            "state": "prepared", "retryable": False, "startVersion": 1}


def _public(intent):
    return {k: intent[k] for k in ("intentId", "startVersion", "state", "retryable", "orderId", "attemptId", "error")
            if k in intent}


def _refresh(store, engine, intent):
    current = startorder.projection(engine, intent["card"], intent["orderId"])
    # Brak rezerwacji w dzienniku jest normalny tylko przed pierwszym przekazaniem.
    if current["state"] == "prepared" and intent["state"] != "prepared":
        current.update(state="reconciling", retryable=False)
    if intent["state"] == "dispatching" and current.get("attemptId") != intent["attemptId"]:
        current = {"orderId": intent["orderId"], "attemptId": intent["attemptId"],
                   "state": "reconciling", "retryable": False}
    changed = any(intent.get(k) != v for k, v in current.items()) or ("error" in intent and "error" not in current)
    if changed:
        intent.pop("error", None)
        intent.update(current)
        intent["startVersion"] += 1
    if "initialResponse" not in intent and intent["state"] in ("queued", "started", "completed", "start-error", "reconciling"):
        accepted = _acceptance(store, intent["issue"])
        intent["initialResponse"] = {**_response(store, intent),
                                     "alreadyAccepted": intent.get("wasAccepted", False),
                                     "commitError": accepted.get("commitError", "")}
        changed = True
    if changed:
        _save(store, intent)
    return _public(intent)


def _dispatch(store, engine, intent):
    intent.update(state="dispatching", retryable=False, startVersion=intent["startVersion"] + 1)
    intent.pop("error", None)
    _save(store, intent)
    try:
        engine.start(intent["card"], intent["launch"],
                     _order={"orderId": intent["orderId"], "attemptId": intent["attemptId"]})
    except Exception:
        # Sam wyjątek zapisu nie jest dowodem nieudanego przekazania.
        pass
    return _refresh(store, engine, intent)


def _response(store, intent):
    state = intent["state"]
    start_error = (intent.get("error") or {"code": "reconciling", "message": "Trwa rozliczanie przekazania."}) \
        if state in ("start-error", "reconciling") else None
    return {"accepted": True, "issue": store._show(intent["issue"]), "card": intent["card"],
            "acceptedVersion": intent["acceptedVersion"], "started": state in ("started", "completed"),
            "queued": state == "queued", "startError": start_error, "start": _public(intent)}


def accept(store, engine, ident, request):
    ident = store._id(ident)
    if not isinstance(request, dict):
        fail("Żądanie musi być obiektem JSON.")
    if "launch" in request and not isinstance(request["launch"], dict):
        fail("launch musi być obiektem ustawień StartCard.")
    key = _key(request.get("requestId"))
    with engine.journal.lock, store.board.transaction():
        receipt = acceptance._read(store, f"{RESULTS}/{key}.json")
        payload = {"id": ident, **request}
        if receipt:
            if acceptance._encoded(receipt["payload"]) != acceptance._encoded(payload):
                fail("requestId był już użyty z innymi danymi.", "conflict")
            return receipt["response"]
        acceptance.accept(store, ident, request,
            prepare=lambda card, issue: {**_prepare(store, engine, card, issue, request.get("launch")),
                                        "acceptRequest": {k: v for k, v in request.items() if k != "requestId"}})
        intent = _load(store, ident)
        if intent is None:
            fail("Brakuje intencji zlecenia przyjętej sprawy.", "broken-file")
        if intent["state"] == "prepared":
            _dispatch(store, engine, intent)
        else:
            _refresh(store, engine, intent)
        response = intent["initialResponse"]
        acceptance._save(store, f"{RESULTS}/{key}.json", {"payload": payload, "response": response})
        return response


def retry(store, engine, ident, request):
    ident = store._id(ident)
    if (not isinstance(request, dict) or request.keys() != {"intentId", "expectedStartVersion", "requestId"} or
            not isinstance(request["intentId"], str) or not request["intentId"] or
            type(request["expectedStartVersion"]) is not int or request["expectedStartVersion"] < 1):
        fail("Podaj wyłącznie intentId, expectedStartVersion i requestId.")
    key = _key(request["requestId"])
    payload = {"id": ident, "operation": "retry-start", **request}
    path = f"{RESULTS}/{key}.json"
    with engine.journal.lock, store.board.transaction():
        previous = acceptance._read(store, path)
        used = acceptance._read(store, f"{acceptance.REQUESTS}/{key}.json")
        if (previous and acceptance._encoded(previous["payload"]) != acceptance._encoded(payload)) or \
                (used and acceptance._encoded(used) != acceptance._encoded(payload)):
            fail("requestId był już użyty z innymi danymi.", "conflict")
        if previous and "response" in previous:
            return previous["response"]
        issue = store._show(ident)
        intent = _load(store, ident)
        if not intent:
            fail("Sprawa nie ma intencji startu.", "start-conflict")
        _refresh(store, engine, intent)
        if previous and intent["attemptId"] == previous["attemptId"]:
            # Utracona odpowiedź retry: projekcja tej samej próby, bez nowej decyzji.
            response = _response(store, intent)
            acceptance._save(store, path, {**previous, "response": response})
            return response
        eligible = (issue["state"] == "accepted" and issue["card"] == intent["card"] and
                    request["intentId"] == intent["intentId"] and
                    request["expectedStartVersion"] == intent["startVersion"] and
                    intent["state"] == "start-error" and intent["retryable"])
        if not eligible:
            response = {"ok": False, "error": "start-conflict", "message": "Start nie może zostać ponowiony w tym stanie.",
                        "start": _public(intent), "card": intent["card"]}
            acceptance._save(store, f"{acceptance.REQUESTS}/{key}.json", payload)
            acceptance._save(store, path, {"payload": payload, "response": response})
            return response
        # Dziennik, niezmienność karty/generacji i brak przyjętego zlecenia zostały
        # sprawdzone pod tą samą granicą, którą trzyma engine.start.
        attempt = previous["attemptId"] if previous else uuid.uuid4().hex
        receipt = {"payload": payload, "attemptId": attempt}
        acceptance._save(store, f"{acceptance.REQUESTS}/{key}.json", payload)
        acceptance._save(store, path, receipt)
        intent["attemptId"] = attempt
        _dispatch(store, engine, intent)
        response = _response(store, intent)
        acceptance._save(store, path, {**receipt, "response": response})
        return response


def show(store, engine, ident):
    ident = store._id(ident)
    with engine.journal.lock, store.board.transaction():
        issue = store._show(ident)
        intent = _load(store, ident)
        return {"issue": issue, **({"start": _refresh(store, engine, intent)} if intent else {})}


def recover(store, engine):
    """Zgoda zapisana razem z akceptacją może dokończyć pierwsze przekazanie po restarcie."""
    folder = store._path(acceptance.INTENTS)
    if not folder.exists():
        return
    for name in os.listdir(folder):
        if not name.endswith(".done.json"):
            continue
        ident = name.removesuffix(".done.json")
        with engine.journal.lock, store.board.transaction():
            intent = _load(store, ident)
            if intent:
                if intent["state"] == "prepared":
                    _dispatch(store, engine, intent)
                else:
                    _refresh(store, engine, intent)


def preview(store, engine, ident, request):
    """Dane obecnego okna zlecenia bez publikacji sprawy ani rezerwacji K-N."""
    ident = store._id(ident)
    if (not isinstance(request, dict) or request.keys() - {"expectedVersion", "text"} or
            type(request.get("expectedVersion")) is not int or not isinstance(request.get("text", ""), str)):
        fail("Podaj expectedVersion oraz opcjonalny tekst wyszukiwania gałęzi.")
    with store._lock():
        issue = store._show(ident)
        if issue["version"] != request["expectedVersion"]:
            raise VersionConflict(issue["version"])
        if issue["state"] != "ready":
            fail("Zlecić można gotową sprawę.")
        board = store.board._load()
        project = board.projects.get(issue["project"])
        if project is None or not project.repo or project.repo_missing:
            fail("Sprawa potrzebuje projektu z dostępnym repozytorium.", "no-project")
        from .board.store import _card_values
        from .board.plan_candidates import candidates
        planned = store.board._plan_create(board, _card_values({"title": issue["title"],
                                              "project": issue["project"]}, create=True))
        card = {"id": planned.result["id"], "title": issue["title"], "description": issue["report"],
                "project": issue["project"], "projects": [], "tags": [], "column": "backlog", "planCandidates": []}
        card["planCandidates"] = [p for p in candidates(project.repo, store.board.root)
                                   if p["card"] in ("", card["id"]) and p["status"] != "done"]
        branches = engine.git.list_branches(project.repo, request.get("text", ""), 50, "base")
        return {"snapshot": {**store.board.snapshot(), "cards": [card]}, "card": card["id"],
                "branches": branches, "defaults": defaults(engine, project)}
