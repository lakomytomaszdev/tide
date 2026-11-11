"""Diff jako dane (`DiffData`) dla interfejsu D-Bus `Git.Diff` (K-36, PT-357, PT-349, PT-360): źródła `card`,
`ticket`, `commit`, `range` i `worktree`.

`diff(reader, journal, source, options)` odpowiada na pytanie „co zmieniła ta karta”. Gita woła wyłącznie
runner z PT-348 (`reader`, funkcje `read`), tekst diffu parsuje `diffparse.parse_diff` (PT-359). Stan karty
czytają istniejące funkcje odczytu stanu (`Journal.has`/`Journal.state`, `state.base_of`, `state.ticket_of`)
— bez zapisu do dziennika, bez zmiany refów i bez ścieżek z żądania: repozytorium karty pochodzi z `card.started`.

Źródło `card` (`{"source": "card", "card": "K-N", "targetRef"?: ref}`):
- bez `targetRef` — od przypiętego `baseCommit` z dziennika do gałęzi karty („zmiany od startu”, także po ruchu
  gałęzi startowej),
- z `targetRef` — od merge-base celu i gałęzi karty.

Źródło `ticket` (`{"source": "ticket", "card": "K-N", "ticket": "PT-N"}`): od merge-base gałęzi karty i gałęzi
ticketu do gałęzi ticketu. Repozytorium pochodzi wyłącznie z dziennika karty (`card.started`). Gdy gałęzi ticketu
nie ma (scalony i usunięty) — albo została, ale jest już w gałęzi karty (pusty zakres) — zakres bierze się z pary
zamiar/wynik akcji `merge` w dzienniku: merge-base(`cardTip`, `expectedTip`) → `expectedTip`. Bez takiego zapisu
(albo z SHA, którego nie ma w repozytorium) odpowiedź to `no-ticket-range` z jednym zdaniem, co dalej.

Źródło `commit` (`{"source": "commit", "repo": ścieżka, "sha": ref}`): pierwszy rodzic → commit (commit scalający
względem pierwszego rodzica, początkowy względem pustego drzewa: `from.sha` to puste drzewo, `from.ref` `empty-tree`).
Źródło `range` (`{"source": "range", "repo", "from", "to"}`): od merge-base(`from`, `to`) do `to`, jak
`git diff from...to`. `repo` tych źródeł przechodzi `validate.known_repo` (repozytorium projektu Tablicy albo
worktree z listy Worktrees, po ścieżce kanonicznej), a `sha`/`from`/`to` — `validate.commit_of`; inaczej `bad-request`.

Źródło `worktree` (`{"source": "worktree", "path": ścieżka}`, PT-360): niezacommitowana praca katalogu z listy
Worktrees. `path` przechodzi tę samą `validate.known_repo` co `repo`; pliki są w dwóch grupach (`groups[]`: `staged` —
indeks względem HEAD, `unstaged` — drzewo względem indeksu; wpis pliku ma `group`), a `untracked[]` to ścieżki plików
nieśledzonych bez treści. Odczyt nie zmienia indeksu ani refów (runner: `GIT_OPTIONAL_LOCKS=0`,
`diff.autoRefreshIndex=false`). Katalog, którego nie ma, to `no-worktree`. `options.group` wskazuje grupę dla
`options.path`.

Kształt `DiffData` (kontrakt dla #2, #29, #13, #14 i K-99 — zmiany tylko addytywne):

    {"source": "card", "card": "K-N", "repo": ścieżka, "title": tekst,
     "from": {"ref", "sha", "date"}, "to": {"ref", "sha", "date"},
     "files": [...], "totals": {"files", "additions", "deletions"},   # z parsera #28
     "truncated": false, "warnings": [...]}

`date` to data commitera w ISO 8601 (UTC); etykietę „Start od…” formatuje UI (`startLabel`, #13).

Duże diffy (PT-355, pola tylko dodane): całość czytana z limitem 2 MB albo 20 000 linii. Po przekroczeniu
lista plików jest pełna (z `--raw`/`--numstat`), `truncated: true`, w `warnings[]` ostrzeżenie, a pliki bez
przeczytanej w całości treści mają `truncated: true` i `hunks: []`. Plik ponad 3 000 zmienionych linii ma
`large: true`, `lines` i `hunks: []`. Plik binarny ma `oldSize`/`newSize` (bajty; None, gdy strony nie ma).
Pola per plik pojawiają się tylko, gdy dotyczą pliku. `options.path` zwraca ten sam kształt z jednym plikiem
(z treścią, także `large`/przyciętym) i polem `path`; `options.context` (0–100000, domyślnie 3) ustawia
kontekst tego pliku i wymaga `path`.
Błędy: `DiffError(code, message)` z kodami `bad-request`, `unknown-card`, `no-branch`, `unknown-base`,
`unknown-path`, `no-ticket-range`, `no-worktree` i kodami runnera (`timeout`, `cancelled`, `unavailable`, `unsafe-config`, `failed`).
"""
import json
import os
import re
from datetime import datetime, timezone

from ..board.model import BoardError, card_num
from ..cardflow.state import base_of, ticket_of
from . import read
from .diffparse import parse_diff
from .runner import GitReadError
from .validate import commit_of, known_repo

SOURCES = ("card", "ticket", "commit", "range", "worktree")
# Grupy źródła `worktree` (PT-360): id, nazwa w widoku i argument `read.diff_capped` (`cached`).
WORKTREE_GROUPS = ("staged", "unstaged")
GROUP_NAMES = {"staged": "W indeksie", "unstaged": "Poza indeksem"}
MAX_UNTRACKED = 2000
UNTRACKED_MAX_BYTES = 1 << 20
EMPTY_TREES = {40: "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
               64: "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321"}
_TICKET = re.compile(r"[A-Za-z][A-Za-z0-9]*-[0-9]+\Z")

# Limity (PT-355). Całość diffu: 2 MB tekstu albo 20 000 linii — liczone przy czytaniu wyjścia gita, po
# przekroczeniu git jest przerywany. Plik ponad LARGE_LINES zmienionych linii jest `large` (bez hunków).
# Pojedynczy plik (`options.path`) ma luźniejszy, ale też ograniczony limit.
MAX_BYTES = 2 << 20
MAX_LINES = 20000
LARGE_LINES = 3000
PATH_MAX_BYTES = 10 << 20
PATH_MAX_LINES = 100000
DEFAULT_CONTEXT = 3
MAX_CONTEXT = 100000


class DiffError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def request(reader, journal, text, repos=()) -> dict:
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
        return {"ok": True, **diff(reader, journal, data, options, repos)}
    except DiffError as exc:
        return {"ok": False, "error": exc.code, "message": exc.message}
    except GitReadError as exc:
        return {"ok": False, "error": exc.code, "message": exc.message}
    except Exception as exc:  # odczyt nie może wywrócić usługi
        return {"ok": False, "error": "failed", "message": f"{exc.__class__.__name__}: {exc}"}


def diff(reader, journal, source: dict, options: dict | None = None, repos=()) -> dict:
    """`DiffData` dla źródła; `options`: `path` (treść jednego pliku) i `context` (linie kontekstu tego pliku).
    `repos`: ścieżki repozytoriów projektów Tablicy i worktrees (dozwolone `repo` źródeł `commit` i `range`);
    lista albo funkcja — funkcję wołamy dopiero, gdy źródło jej potrzebuje."""
    if not isinstance(source, dict):
        raise DiffError("bad-request", "źródło diffu musi być obiektem")
    if options is not None and not isinstance(options, dict):
        raise DiffError("bad-request", "options musi być obiektem")
    kind = source.get("source")
    if kind not in SOURCES:
        raise DiffError("bad-request", f"nieznane źródło diffu: {kind!r} (obsługiwane: {', '.join(SOURCES)})")
    if kind == "card":
        return card_diff(reader, journal, source.get("card"), source.get("targetRef"), options)
    if kind == "ticket":
        return ticket_diff(reader, journal, source.get("card"), source.get("ticket"), options)
    opts = _options(options)
    try:  # `worktree` podaje katalog w `path`, `commit` i `range` w `repo`; ta sama funkcja granicy
        repo = known_repo(source.get("path") if kind == "worktree" else source.get("repo"),
                          repos() if callable(repos) else repos)
    except GitReadError as exc:
        raise DiffError(exc.code, exc.message) from None
    if kind == "worktree":
        return worktree_diff(reader, repo, opts)
    if kind == "commit":
        return commit_diff(reader, repo, source.get("sha"), opts)
    return range_diff(reader, repo, source.get("from"), source.get("to"), opts)


def _card_context(journal, card_id):
    """(`K-N`, stan z dziennika, repozytorium z `card.started`, gałąź karty) — wspólne dla źródeł `card` i `ticket`."""
    if not isinstance(card_id, str):
        raise DiffError("bad-request", "brak identyfikatora karty (card: \"K-<numer>\")")
    try:
        card = f"K-{card_num(card_id)}"
    except BoardError as exc:
        raise DiffError("bad-request", str(exc)) from None
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
    return card, st, repo, branch


def card_diff(reader, journal, card_id, target_ref=None, options=None) -> dict:
    opts = _options(options)
    if target_ref is not None and not isinstance(target_ref, str):
        raise DiffError("bad-request", "targetRef musi być napisem")
    card, st, repo, branch = _card_context(journal, card_id)
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
    body = _body(reader, repo, from_sha, to_sha, opts)
    return {"source": "card", "card": card, "repo": repo, "title": title,
            "from": {"ref": from_ref, "sha": from_sha, "date": _date(reader, repo, from_sha)},
            "to": {"ref": to_ref, "sha": to_sha, "date": _date(reader, repo, to_sha)},
            **body}


def _known_ticket(st, ticket: str) -> bool:
    """Ticket należy do karty, gdy dziennik zna jego rezerwację, gałąź, krok, przydział albo scalenie."""
    info = ticket_of(st, ticket)
    return bool(info["id"] == ticket or info["branch"] or ("merge", ticket) in st.intents
                or ("merge", ticket) in st.results or ticket in st.assignments
                or any(s.get("ticket") == ticket for s in st.steps.values()))


def ticket_diff(reader, journal, card_id, ticket, options=None) -> dict:
    """Zmiany jednego ticketu karty (źródło `ticket`); zakres po scaleniu i usunięciu gałęzi — z dziennika."""
    opts = _options(options)
    if not isinstance(ticket, str) or not _TICKET.match(ticket):
        raise DiffError("bad-request", "brak ticketu (ticket: \"PT-<numer>\")")
    ticket = ticket.upper()
    card, st, repo, card_branch = _card_context(journal, card_id)
    if not _known_ticket(st, ticket):
        raise DiffError("bad-request", f"{card}: nieznany ticket {ticket}")
    card_ref = f"refs/heads/{card_branch}"
    card_tip = read.rev_parse(reader, repo, card_ref)
    if card_tip is None:
        raise DiffError("no-branch", f"{card}: gałąź karty {card_branch} nie istnieje w {repo}")
    branch = ticket_of(st, ticket)["branch"]
    to_ref = f"refs/heads/{branch}" if branch else ""
    tip = read.rev_parse(reader, repo, to_ref) if to_ref else None
    if tip is not None:
        from_sha = read.merge_base(reader, repo, card_tip, tip)
        if from_sha is None:
            raise DiffError("bad-request", f"gałąź ticketu {branch} nie ma wspólnej historii z gałęzią karty "
                                           f"{card_branch}")
        from_ref, to_sha = card_ref, tip
        if from_sha == tip:  # ticket już w gałęzi karty: zakres byłby pusty — szukamy zapisu scalenia
            recorded = _merged_range(reader, st, repo, ticket)
            if recorded:
                (from_sha, to_sha), from_ref, to_ref = recorded, recorded[0], recorded[1]
    else:
        recorded = _merged_range(reader, st, repo, ticket)
        if not recorded:
            raise DiffError("no-ticket-range", f"Gałąź ticketu {ticket} nie istnieje, a dziennik karty nie zapisał "
                                               "zakresu jego scalenia.")
        (from_sha, to_sha), from_ref, to_ref = recorded, recorded[0], recorded[1]
    body = _body(reader, repo, from_sha, to_sha, opts)
    return {"source": "ticket", "card": card, "ticket": ticket, "repo": repo,
            "title": f"{ticket} · zmiany ticketu względem gałęzi karty",
            "from": {"ref": from_ref, "sha": from_sha, "date": _date(reader, repo, from_sha)},
            "to": {"ref": to_ref, "sha": to_sha, "date": _date(reader, repo, to_sha)},
            **body}


def _merged_range(reader, st, repo: str, ticket: str):
    """(merge-base(`cardTip`, `expectedTip`), `expectedTip`) z pary zamiar/wynik akcji `merge` albo None, gdy
    dziennik takiego zapisu nie ma albo zapisanych SHA nie ma w repozytorium. Tylko odczyt stanu."""
    result = st.results.get(("merge", ticket)) or {}
    intent = st.intents.get(("merge", ticket)) or {}
    expected, card_tip = intent.get("expectedTip"), intent.get("cardTip")
    if result.get("ok") is not True or not isinstance(expected, str) or not isinstance(card_tip, str) \
            or not expected or not card_tip:
        return None
    try:
        expected_sha = read.rev_parse(reader, repo, expected)
        card_sha = read.rev_parse(reader, repo, card_tip)
        base = read.merge_base(reader, repo, card_sha, expected_sha) if expected_sha and card_sha else None
    except GitReadError as exc:
        if exc.code != "bad-request":  # zapis niebędący poprawnym refem to jak brak zapisu
            raise
        return None
    return (base, expected_sha) if base else None


def commit_diff(reader, repo: str, sha, opts) -> dict:
    """Zmiany jednego commita: względem pierwszego rodzica albo pustego drzewa (commit początkowy)."""
    to_sha = commit_of(reader, repo, sha)
    found = read.log(reader, repo, [to_sha], max_count=1)
    parents = found[0]["parents"] if found else []
    subject = found[0]["subject"] if found else ""
    if parents:
        from_sha = parents[0]
        from_info = {"ref": from_sha, "sha": from_sha, "date": _date(reader, repo, from_sha)}
    else:
        from_sha = EMPTY_TREES.get(len(to_sha), EMPTY_TREES[40])
        from_info = {"ref": "empty-tree", "sha": from_sha, "date": ""}
    body = _body(reader, repo, from_sha, to_sha, opts)
    return {"source": "commit", "repo": repo, "title": f"commit {to_sha[:7]} — {subject}",
            "from": from_info, "to": {"ref": to_sha, "sha": to_sha, "date": _date(reader, repo, to_sha)}, **body}


def range_diff(reader, repo: str, from_ref, to_ref, opts) -> dict:
    """`git diff from...to`: od merge-base(`from`, `to`) do `to`."""
    from_tip, to_sha = commit_of(reader, repo, from_ref), commit_of(reader, repo, to_ref)
    base = read.merge_base(reader, repo, from_tip, to_sha)
    if base is None:
        raise DiffError("bad-request", f"{from_ref} i {to_ref} nie mają wspólnej historii")
    body = _body(reader, repo, base, to_sha, opts)
    return {"source": "range", "repo": repo, "title": f"{_short(to_ref)} względem {_short(from_ref)}",
            "from": {"ref": from_ref, "sha": base, "date": _date(reader, repo, base)},
            "to": {"ref": to_ref, "sha": to_sha, "date": _date(reader, repo, to_sha)}, **body}


def worktree_diff(reader, repo: str, opts) -> dict:
    """Niezacommitowana praca worktree (źródło `worktree`): grupa `staged` (indeks względem HEAD) i `unstaged`
    (drzewo robocze względem indeksu) plus lista nieśledzonych plików bez treści. `repo` to już ścieżka
    kanoniczna z `known_repo`. Odczyt nie zmienia indeksu, refów ani plików (runner: `GIT_OPTIONAL_LOCKS=0`,
    `diff.autoRefreshIndex=false`). Z `options.path` (i opcjonalnie `options.group`) — treść jednego pliku."""
    if not os.path.isdir(repo):
        raise DiffError("no-worktree", f"Katalog worktree nie istnieje (został usunięty?): {repo}")
    head = read.rev_parse(reader, repo, "HEAD")  # None: gałąź bez commitów
    from_info = {"ref": "HEAD", "sha": head or "", "date": _date(reader, repo, head) if head else ""}
    to_info = {"ref": "worktree", "sha": "", "date": ""}
    title = f"Niezacommitowane zmiany: {repo}"
    if opts["path"] is not None:
        groups = [opts["group"]] if opts["group"] else list(WORKTREE_GROUPS)
        for group in groups:
            try:
                body = _changes(reader, repo, opts, cached=group == "staged")
                break
            except DiffError as exc:
                if exc.code != "unknown-path" or group == groups[-1]:
                    raise
        for f in body["files"]:
            f["group"] = group
        return {"source": "worktree", "repo": repo, "title": title, "from": from_info, "to": to_info, **body,
                "group": group}
    files, warnings, meta, truncated = [], [], [], False
    for group in WORKTREE_GROUPS:
        body = _changes(reader, repo, opts, cached=group == "staged")
        for f in body["files"]:
            f["group"] = group
        files += body["files"]
        warnings += [f"{GROUP_NAMES[group]}: {w}" for w in body["warnings"]]
        truncated = truncated or body["truncated"]
        meta.append({"id": group, "name": GROUP_NAMES[group], "totals": body["totals"]})
    untracked, cut = read.untracked(reader, repo, max_bytes=UNTRACKED_MAX_BYTES, max_count=MAX_UNTRACKED)
    if cut:
        warnings.append(f"Lista nieśledzonych plików jest niepełna (limit {MAX_UNTRACKED} plików).")
    totals = {"files": len(files), "additions": sum(f["additions"] for f in files),
              "deletions": sum(f["deletions"] for f in files)}
    return {"source": "worktree", "repo": repo, "title": title, "from": from_info, "to": to_info,
            "files": files, "groups": meta, "untracked": untracked, "totals": totals,
            "truncated": truncated, "warnings": warnings}


def _short(ref: str) -> str:
    for prefix in ("refs/heads/", "refs/remotes/", "refs/tags/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref[:7] if re.fullmatch(r"[0-9a-f]{40,64}", ref) else ref


def _options(options) -> dict:
    """Sprawdza `options` (`path`, `context`); nieznane klucze są ignorowane (kontrakt rośnie addytywnie)."""
    options = options or {}
    path, context, group = options.get("path"), options.get("context"), options.get("group")
    if path is not None and (not isinstance(path, str) or not path):
        raise DiffError("bad-request", "options.path musi być niepustym napisem")
    if group is not None and group not in WORKTREE_GROUPS:
        raise DiffError("bad-request", f"options.group musi być jednym z: {', '.join(WORKTREE_GROUPS)}")
    if context is not None:
        if isinstance(context, bool) or not isinstance(context, int) or not 0 <= context <= MAX_CONTEXT:
            raise DiffError("bad-request", f"options.context musi być liczbą całkowitą 0–{MAX_CONTEXT}")
        if path is None:
            raise DiffError("bad-request", "options.context dotyczy jednego pliku — podaj też options.path")
    return {"path": path, "context": DEFAULT_CONTEXT if context is None else context, "group": group}


def _body(reader, repo, from_sha, to_sha, opts) -> dict:
    """`files`, `totals`, `truncated`, `warnings` (i `path` dla pojedynczego pliku) zakresu `from_sha`→`to_sha`."""
    body = _changes(reader, repo, opts, base=from_sha, head=to_sha)
    _binary_sizes(reader, repo, from_sha, to_sha, body["files"])
    return body


def _changes(reader, repo, opts, **spec) -> dict:
    """Zmiany wskazane przez `spec` (argumenty `read.diff_capped`: `base`/`head` albo `cached`): pliki z treścią,
    sumy, `truncated` i ostrzeżenia; z `opts["path"]` — jeden plik. Nie czyta rozmiarów plików binarnych."""
    if opts["path"] is not None:
        return _one_file(reader, repo, opts, **spec)
    text, cut = read.diff_capped(reader, repo, max_bytes=MAX_BYTES, max_lines=MAX_LINES, **spec)
    parsed = parse_diff(text)
    files, totals, warnings = parsed["files"], parsed["totals"], list(parsed["warnings"])
    if cut:
        files, totals, more = _complete(reader, repo, files, **spec)
        warnings += more
    _mark_large(files)
    return {"files": files, "totals": totals, "truncated": cut, "warnings": warnings}


def _one_file(reader, repo, opts, **spec) -> dict:
    """Treść jednego pliku (także `large` i przyciętego) z kontekstem `options.context`."""
    path = opts["path"]
    text, cut = read.diff_capped(reader, repo, paths=[path], context=opts["context"],
                                 max_bytes=PATH_MAX_BYTES, max_lines=PATH_MAX_LINES, **spec)
    parsed = parse_diff(text)
    found = [f for f in parsed["files"] if f["path"] == path]
    if not found:
        raise DiffError("unknown-path", f"plik {path} nie ma zmian w tym zakresie")
    warnings = list(parsed["warnings"])
    if cut:
        found[0]["truncated"] = True
        warnings.append(f"Plik {path} przekracza limit podglądu pojedynczego pliku "
                        f"({PATH_MAX_BYTES >> 20} MB albo {PATH_MAX_LINES} linii): pokazano jego początek.")
    f = found[0]
    return {"files": found, "totals": {"files": 1, "additions": f["additions"], "deletions": f["deletions"]},
            "truncated": cut, "warnings": warnings, "path": path}


def _complete(reader, repo, parsed_files, **spec):
    """Diff ucięty limitem: pełna lista plików z `--raw`/`--numstat`; treść zostaje tylko plikom, które
    mieszczą się w przeczytanym tekście w całości, reszta dostaje `truncated: true` i pusty `hunks`."""
    meta, meta_cut = read.diff_files(reader, repo, spec.get("base"), spec.get("head"),
                                     cached=spec.get("cached", False))
    by_path = {f["path"]: f for f in parsed_files}
    files, shown = [], 0
    for entry in meta:
        read_file = by_path.get(entry["path"])
        if read_file and not read_file["warnings"] and \
                (read_file["additions"], read_file["deletions"], read_file["binary"]) == \
                (entry["additions"], entry["deletions"], entry["binary"]):
            files.append(read_file)
            shown += 1
        else:
            entry["truncated"] = True
            files.append(entry)
    totals = {"files": len(files), "additions": sum(f["additions"] for f in files),
              "deletions": sum(f["deletions"] for f in files)}
    warnings = [f"Diff przekracza limit ({MAX_BYTES >> 20} MB albo {MAX_LINES} linii): pokazano część zmian "
                f"({shown} z {len(files)} plików z treścią). Wybierz plik, żeby doczytać jego zmiany."]
    if meta_cut:
        warnings.append("Lista plików jest niepełna: wynik gita przekroczył limit odczytu.")
    return files, totals, warnings


def _mark_large(files) -> None:
    """Plik z ponad `LARGE_LINES` zmienionymi liniami: `large: true`, `lines` i bez hunków (treść przez
    `options.path`). `large` wypiera `truncated`."""
    for f in files:
        lines = f["additions"] + f["deletions"]
        if lines > LARGE_LINES:
            f.pop("truncated", None)
            f.update(large=True, lines=lines, hunks=[])


def _binary_sizes(reader, repo, from_sha, to_sha, files) -> None:
    """`oldSize`/`newSize` (bajty; None, gdy strony nie ma) plikom binarnym — z drzew obu commitów."""
    binary = [f for f in files if f["binary"]]
    if not binary:
        return
    old = read.blob_sizes(reader, repo, from_sha, [f["path"] for f in binary if f["status"] != "A"])
    new = read.blob_sizes(reader, repo, to_sha, [f["path"] for f in binary if f["status"] != "D"])
    for f in binary:
        f["oldSize"] = old.get(f["path"]) if f["status"] != "A" else None
        f["newSize"] = new.get(f["path"]) if f["status"] != "D" else None


def _date(reader, repo: str, sha: str) -> str:
    commits = read.log(reader, repo, [sha], max_count=1)
    if not commits:
        return ""
    return datetime.fromtimestamp(commits[0]["time"], timezone.utc).isoformat().replace("+00:00", "Z")
