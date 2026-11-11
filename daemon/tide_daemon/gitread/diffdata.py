"""Diff jako dane (`DiffData`) dla interfejsu D-Bus `Git.Diff` (K-36, PT-357): źródło `card`.

`diff(reader, journal, source, options)` odpowiada na pytanie „co zmieniła ta karta”. Gita woła wyłącznie
runner z PT-348 (`reader`, funkcje `read`), tekst diffu parsuje `diffparse.parse_diff` (PT-359). Stan karty
czytają istniejące funkcje odczytu stanu (`Journal.has`/`Journal.state`, `state.base_of`, `state.ticket_of`)
— bez zapisu do dziennika, bez zmiany refów i bez ścieżek z żądania: repozytorium karty pochodzi z `card.started`.

Źródło `card` (`{"source": "card", "card": "K-N", "targetRef"?: ref}`):
- bez `targetRef` — od przypiętego `baseCommit` z dziennika do gałęzi karty („zmiany od startu”, także po ruchu
  gałęzi startowej),
- z `targetRef` — od merge-base celu i gałęzi karty.

Kształt `DiffData` (kontrakt dla #2, #29, #13, #14 i K-99 — zmiany tylko addytywne):

    {"source": "card", "card": "K-N", "repo": ścieżka, "title": tekst,
     "from": {"ref", "sha", "date"}, "to": {"ref", "sha", "date"},
     "files": [...], "totals": {"files", "additions", "deletions"},   # z parsera #28
     "truncated": false, "warnings": [...]}

`date` to data commitera w ISO 8601 (UTC); etykietę „Start od…” formatuje UI (`startLabel`, #13).
Błędy: `DiffError(code, message)` z kodami `bad-request`, `unknown-card`, `no-branch`, `unknown-base`
i kodami runnera (`timeout`, `cancelled`, `unavailable`, `unsafe-config`, `failed`).
"""
import json
import os
from datetime import datetime, timezone

from ..board.model import BoardError, card_num
from ..cardflow.state import base_of, ticket_of
from . import read
from .diffparse import parse_diff
from .runner import GitReadError

SOURCES = ("card",)


class DiffError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def request(reader, journal, text) -> dict:
    """Żądanie D-Bus (tekst JSON) → odpowiedź `{"ok": true, ...DiffData}` albo `{"ok": false, error, message}`.
    Nigdy nie rzuca wyjątku."""
    try:
        try:
            data = json.loads(text) if isinstance(text, str) else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise DiffError("bad-request", "żądanie musi być obiektem JSON")
        options = data.get("options", {})
        return {"ok": True, **diff(reader, journal, data, options)}
    except DiffError as exc:
        return {"ok": False, "error": exc.code, "message": exc.message}
    except GitReadError as exc:
        return {"ok": False, "error": exc.code, "message": exc.message}
    except Exception as exc:  # odczyt nie może wywrócić usługi
        return {"ok": False, "error": "failed", "message": f"{exc.__class__.__name__}: {exc}"}


def diff(reader, journal, source: dict, options: dict | None = None) -> dict:
    """`DiffData` dla źródła; `options` (path, context) dochodzą w #14 — dziś tylko sprawdzenie typu."""
    if not isinstance(source, dict):
        raise DiffError("bad-request", "źródło diffu musi być obiektem")
    if options is not None and not isinstance(options, dict):
        raise DiffError("bad-request", "options musi być obiektem")
    kind = source.get("source")
    if kind not in SOURCES:
        raise DiffError("bad-request", f"nieznane źródło diffu: {kind!r} (obsługiwane: {', '.join(SOURCES)})")
    return card_diff(reader, journal, source.get("card"), source.get("targetRef"))


def card_diff(reader, journal, card_id, target_ref=None) -> dict:
    if not isinstance(card_id, str):
        raise DiffError("bad-request", "brak identyfikatora karty (card: \"K-<numer>\")")
    try:
        card = f"K-{card_num(card_id)}"
    except BoardError as exc:
        raise DiffError("bad-request", str(exc)) from None
    if target_ref is not None and not isinstance(target_ref, str):
        raise DiffError("bad-request", "targetRef musi być napisem")
    if journal is None:
        raise DiffError("unavailable", "przepływ karty jest wyłączony w usłudze")
    if not journal.has(card):
        raise DiffError("unknown-card", f"{card}: karta nie ma dziennika przepływu")
    st = journal.state(card)
    if not st.started:
        raise DiffError("unknown-card", f"{card}: karta nie była zlecona")
    repo = (st.started or {}).get("repo") or ""
    branch = ticket_of(st)["cardBranch"]
    if not branch or not isinstance(repo, str) or not os.path.isabs(repo):
        raise DiffError("no-branch", f"{card}: karta nie ma gałęzi")
    if not os.path.isdir(repo):
        raise DiffError("no-branch", f"{card}: repozytorium karty {repo} nie istnieje")
    to_ref = f"refs/heads/{branch}"
    to_sha = read.rev_parse(reader, repo, to_ref)
    if to_sha is None:
        raise DiffError("no-branch", f"{card}: gałąź karty {branch} nie istnieje w {repo}")
    if target_ref:
        target_sha = read.rev_parse(reader, repo, target_ref)
        if target_sha is None:
            raise DiffError("bad-request", f"nieznany ref porównania: {target_ref}")
        from_sha = read.merge_base(reader, repo, target_sha, to_sha)
        if from_sha is None:
            raise DiffError("bad-request", f"{target_ref} nie ma wspólnej historii z gałęzią karty {branch}")
        from_ref = target_ref
        title = f"{card}: zmiany gałęzi {branch} względem {target_ref}"
    else:
        base = base_of(st)
        from_sha = read.rev_parse(reader, repo, base["baseCommit"]) if base["status"] == "known" else None
        if from_sha is None:
            raise DiffError("unknown-base", f"{card}: baza startu karty jest nieznana — wybierz jawny ref porównania "
                                            "(targetRef)")
        from_ref = base["baseRef"]
        title = f"{card}: zmiany gałęzi {branch} od startu"
    parsed = parse_diff(read.diff(reader, repo, from_sha, to_sha))
    return {"source": "card", "card": card, "repo": repo, "title": title,
            "from": {"ref": from_ref, "sha": from_sha, "date": _date(reader, repo, from_sha)},
            "to": {"ref": to_ref, "sha": to_sha, "date": _date(reader, repo, to_sha)},
            "files": parsed["files"], "totals": parsed["totals"], "truncated": False,
            "warnings": list(parsed["warnings"])}


def _date(reader, repo: str, sha: str) -> str:
    commits = read.log(reader, repo, [sha], max_count=1)
    if not commits:
        return ""
    return datetime.fromtimestamp(commits[0]["time"], timezone.utc).isoformat().replace("+00:00", "Z")
