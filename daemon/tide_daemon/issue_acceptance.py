"""Przyjęcie przeczytanej wersji sprawy: trwała intencja i odtwarzalna publikacja.

Blokadę Tablicy współdzielą zwykłe CreateCard, odczyty oraz mutacje spraw. Intencja
wyprzedza rezerwację K-N. Dopóki publikacja się nie domknie, odczyty próbują ją
odtworzyć albo zwracają błąd, nigdy częściowy sukces. Commit zapisuje historię.
"""
import hashlib
import os
from datetime import datetime, timezone

from . import issue_attachments as attachments
from .issues import IssueStore, VersionConflict, _atomic, _encoded, _json, _sync_dir, fail, validate
from .board.frontmatter import Document
from .board.store import _card_values

INTENTS = ".git/tide-acceptances"
REQUESTS = ".git/tide-accept-requests"
INTENT_MAX = 2 * 1024 * 1024
SOURCE_KEYS = {"sourceIssue": "source-issue", "sourceIssueVersion": "source-issue-version",
               "sourceIssueHash": "source-issue-hash", "sourceIssueReport": "source-issue-report",
               "sourceIssueManifest": "source-issue-manifest"}


def _save(store, rel, value):
    folder = store._path(rel).parent
    folder.mkdir(exist_ok=True)
    _sync_dir(folder.parent)
    text = _encoded(value) + "\n"
    if len(text.encode("utf-8")) > INTENT_MAX:
        fail("Intencja akceptacji jest za duża.", "broken-file")
    _atomic(store._path(rel), text)


def _read(store, rel):
    try:
        return _json(store._read(rel, INTENT_MAX))
    except FileNotFoundError:
        return None


def recover(board):
    """Wyłącznie pod BoardStore.transaction; nie wywołuje publicznych odczytów."""
    store = IssueStore(board)
    folder = store._path(INTENTS)
    if not folder.exists():
        return
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json") or name.endswith(".done.json"):
            continue
        ident = store._id(name[:-5])
        intent = _read(store, f"{INTENTS}/{name}")
        if not isinstance(intent, dict) or intent.get("issue", {}).get("id") != ident:
            fail("Uszkodzona intencja akceptacji.", "broken-file")
        if not intent.get("done"):
            _finish(store, intent)
        else:
            _complete(store, ident)


def _complete(store, ident):
    # Zakończonych intencji nie wczytujemy przy każdym odświeżeniu Tablicy.
    os.replace(store._path(f"{INTENTS}/{ident}.json"), store._path(f"{INTENTS}/{ident}.done.json"))
    _sync_dir(store._path(INTENTS))


def _finish(store, intent):
    board = store.board
    busy = store.repo.busy()
    if busy:
        fail(store.repo.busy_message(busy), "git-busy")
    issue, card_id = intent["issue"], intent["card"]
    ident = store._id(issue["id"])
    number = int(card_id[2:])
    rel = f"{INTENTS}/{ident}.json"
    if not intent.get("published"):
        # Rezerwacja jest trwała także wtedy, gdy proces zginie przed utworzeniem karty.
        high = max(store.repo.high_water(), number + 1)
        _atomic(store._path(".git/tide-next-card"), str(high) + "\n")
        folder = store._path(f"issues/{ident}/accepted")
        folder.mkdir(exist_ok=True)
        _sync_dir(folder.parent)
        for name, text in (("report.md", intent["reportText"]), ("attachments.json", intent["manifestText"])):
            path = store._path(f"issues/{ident}/accepted/{name}")
            if not path.exists():
                _atomic(path, text)
            elif store._read(str(path.relative_to(store.root)), INTENT_MAX) != text:
                fail("Przyjęty raport różni się od intencji.", "broken-file")
        card_path = store._path(f"cards/{card_id}.md")
        if not card_path.exists():
            _atomic(card_path, intent["cardText"])
        else:
            doc = Document.parse(store._read(f"cards/{card_id}.md", INTENT_MAX))
            if any(doc.get(key) != str(value) for key, value in intent["source"].items()):
                fail("Numer karty intencji jest zajęty przez inną kartę.", "broken-file")
        current = board._load()
        if int(current.meta.get("next-card") or 1) <= number:
            current.meta.set("next-card", number + 1)
            _atomic(store._path("board.md"), current.meta.text())
        accepted = dict(issue, state="accepted", version=issue["version"] + 1,
                        card=card_id, updated=intent["updated"])
        current_issue = store._show(ident)
        if current_issue != accepted:
            if current_issue != issue:
                fail("Sprawa różni się od intencji akceptacji.", "broken-file")
            _atomic(store._path(f"issues/{ident}/report.md"), store._report_text(accepted))
        intent["published"] = True
        _save(store, rel, intent)
    # Nie przepisujemy karty nawet po utracie odpowiedzi lub błędzie historii.
    paths = [f"issues/{ident}", f"cards/{card_id}.md", "board.md"]
    try:
        error = store.repo.commit(f"{ident}: accept issue as {card_id}", paths)
        if not error:
            # Kolejka usuwa trwałe błędy, pozostawiając pliki do sync. To również
            # wymaga ostrzeżenia, mimo że sam flush zwraca wtedy pusty napis.
            status = store.repo.git.run(store.board.root, "status", "--porcelain", "--", *paths)
            if not status.ok or status.out.strip():
                error = "Akceptacja jest zapisana; historia zmian wymaga synchronizacji."
    except OSError as exc:
        error = f"Akceptacja jest zapisana; błąd historii: {exc}."
    intent.update(done=True, commitError=error)
    _save(store, rel, intent)
    _complete(store, ident)


def accept(store, ident, request, *, prepare=None):
    ident = store._id(ident)
    required = {"expectedVersion", "destination", "requestId"}
    go = isinstance(request, dict) and request.get("destination") == "go" and prepare is not None
    if (not isinstance(request, dict) or not required <= request.keys() or
            request.keys() - required - ({"launch"} if go else set()) or
            type(request.get("expectedVersion")) is not int or request["expectedVersion"] < 1 or
            request.get("destination") not in (("backlog", "go") if go else ("backlog",))):
        fail("Podaj expectedVersion, destination: backlog/go oraz requestId; launch jest opcjonalny dla go.")
    validate({"title": "request", "report": "", "requestId": request["requestId"]})
    if not store.board.exists():
        fail(f"Nie ma sprawy {ident}.", "not-found")
    payload = {"id": ident, **request}
    key = hashlib.sha256(request["requestId"].encode("utf-8")).hexdigest()
    receipt_path = f"{REQUESTS}/{key}.json"
    with store._lock():
        previous = _read(store, receipt_path)
        if previous is not None and _encoded(previous) != _encoded(payload):
            fail("requestId był już użyty z innymi danymi.", "conflict")
        intent = (_read(store, f"{INTENTS}/{ident}.json") or
                  _read(store, f"{INTENTS}/{ident}.done.json"))
        if intent:
            if request["expectedVersion"] != intent["issue"]["version"]:
                raise VersionConflict(store._show(ident)["version"])
            if go and "startIntent" in intent and _encoded(intent["startIntent"]["acceptRequest"]) != _encoded({
                    k: v for k, v in request.items() if k != "requestId"}):
                fail("Sprawa ma już intencję z innymi ustawieniami; retry zachowuje zapisane zlecenie.", "conflict")
            if go and "startIntent" not in intent:
                from .board.model import card_num
                card = store.board._load().cards.get(card_num(intent["card"]))
                if card is None:
                    fail("Brakuje przyjętej karty.", "broken-file")
                intent["startIntent"] = prepare(card, intent["issue"])
                _save(store, f"{INTENTS}/{ident}.done.json", intent)
            _save(store, receipt_path, payload)
            return _result(store, intent, True)
        busy = store.repo.busy()
        if busy:
            fail(store.repo.busy_message(busy), "git-busy")
        issue = store._show(ident)
        if request["expectedVersion"] != issue["version"]:
            raise VersionConflict(issue["version"])
        if issue["state"] != "ready":
            fail("Przyjąć można wyłącznie gotową sprawę (ready).")
        board = store.board._load()
        if issue["project"] is not None and issue["project"] not in board.projects:
            fail(f"Nie ma projektu „{issue['project']}”.")
        for entry in issue["attachments"]:
            attachments.checked_copy(store.root / "issues" / ident, entry)
        manifest = store._read(issue["paths"]["manifest"])
        digest = hashlib.sha256(issue["report"].encode("utf-8") + b"\0" + manifest.encode("utf-8")).hexdigest()
        source = {"sourceIssue": ident, "sourceIssueVersion": issue["version"], "sourceIssueHash": digest,
                  "sourceIssueReport": f"issues/{ident}/accepted/report.md",
                  "sourceIssueManifest": f"issues/{ident}/accepted/attachments.json"}
        values = _card_values({"title": issue["title"],
                               "project": issue["project"] or "", "column": "backlog"}, create=True)
        change = store.board._plan_create(board, values)
        card_id = change.result["id"]
        if change.high_water > 1000000000:
            fail("Wyczerpano numery kart.", "internal")
        doc = Document.parse(change.commits[-1][1][0][1])
        doc.body = issue["report"]
        source_doc = {SOURCE_KEYS[key]: value for key, value in source.items()}
        for field, value in source_doc.items():
            doc.set(field, value)
        intent = {"issue": issue, "card": card_id, "source": source_doc,
                  "reportText": store._report_text(issue), "manifestText": manifest, "cardText": doc.text(),
                  "updated": datetime.fromtimestamp(store.board.clock(), timezone.utc).isoformat(),
                  "published": False, "done": False, "commitError": ""}
        if go:
            from .board.model import parse_card
            errors = []
            card = parse_card(doc, f"cards/{card_id}.md", int(card_id[2:]), errors)
            if errors:
                fail("Nie można przygotować karty sprawy.", "broken-file")
            intent["startIntent"] = prepare(card, issue)
        # Żaden numer K-N nie został jeszcze zużyty. Oba zapisy poprzedzają rezerwację.
        _save(store, receipt_path, payload)
        _save(store, f"{INTENTS}/{ident}.json", intent)
        _finish(store, intent)
        return _result(store, intent, False)


def _result(store, intent, existing):
    issue = dict(intent["issue"], state="accepted", version=intent["issue"]["version"] + 1,
                 card=intent["card"], updated=intent["updated"])
    return {"issue": issue, "card": intent["card"], "acceptedVersion": intent["issue"]["version"],
            "alreadyAccepted": existing, "commitError": intent["commitError"] or store.repo.last_error}
