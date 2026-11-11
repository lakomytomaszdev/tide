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

Duże diffy (PT-355, pola tylko dodane): całość czytana z limitem 2 MB albo 20 000 linii. Po przekroczeniu
lista plików jest pełna (z `--raw`/`--numstat`), `truncated: true`, w `warnings[]` ostrzeżenie, a pliki bez
przeczytanej w całości treści mają `truncated: true` i `hunks: []`. Plik ponad 3 000 zmienionych linii ma
`large: true`, `lines` i `hunks: []`. Plik binarny ma `oldSize`/`newSize` (bajty; None, gdy strony nie ma).
Pola per plik pojawiają się tylko, gdy dotyczą pliku. `options.path` zwraca ten sam kształt z jednym plikiem
(z treścią, także `large`/przyciętym) i polem `path`; `options.context` (0–100000, domyślnie 3) ustawia
kontekst tego pliku i wymaga `path`.
Błędy: `DiffError(code, message)` z kodami `bad-request`, `unknown-card`, `no-branch`, `unknown-base`,
`unknown-path` i kodami runnera (`timeout`, `cancelled`, `unavailable`, `unsafe-config`, `failed`).
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
    """`DiffData` dla źródła; `options`: `path` (treść jednego pliku) i `context` (linie kontekstu tego pliku)."""
    if not isinstance(source, dict):
        raise DiffError("bad-request", "źródło diffu musi być obiektem")
    if options is not None and not isinstance(options, dict):
        raise DiffError("bad-request", "options musi być obiektem")
    kind = source.get("source")
    if kind not in SOURCES:
        raise DiffError("bad-request", f"nieznane źródło diffu: {kind!r} (obsługiwane: {', '.join(SOURCES)})")
    return card_diff(reader, journal, source.get("card"), source.get("targetRef"), options)


def card_diff(reader, journal, card_id, target_ref=None, options=None) -> dict:
    opts = _options(options)
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
    body = _body(reader, repo, from_sha, to_sha, opts)
    return {"source": "card", "card": card, "repo": repo, "title": title,
            "from": {"ref": from_ref, "sha": from_sha, "date": _date(reader, repo, from_sha)},
            "to": {"ref": to_ref, "sha": to_sha, "date": _date(reader, repo, to_sha)},
            **body}


def _options(options) -> dict:
    """Sprawdza `options` (`path`, `context`); nieznane klucze są ignorowane (kontrakt rośnie addytywnie)."""
    options = options or {}
    path, context = options.get("path"), options.get("context")
    if path is not None and (not isinstance(path, str) or not path):
        raise DiffError("bad-request", "options.path musi być niepustym napisem")
    if context is not None:
        if isinstance(context, bool) or not isinstance(context, int) or not 0 <= context <= MAX_CONTEXT:
            raise DiffError("bad-request", f"options.context musi być liczbą całkowitą 0–{MAX_CONTEXT}")
        if path is None:
            raise DiffError("bad-request", "options.context dotyczy jednego pliku — podaj też options.path")
    return {"path": path, "context": DEFAULT_CONTEXT if context is None else context}


def _body(reader, repo, from_sha, to_sha, opts) -> dict:
    """`files`, `totals`, `truncated`, `warnings` (i `path` dla pojedynczego pliku) zakresu `from_sha`→`to_sha`."""
    if opts["path"] is not None:
        return _one_file(reader, repo, from_sha, to_sha, opts)
    text, cut = read.diff_capped(reader, repo, from_sha, to_sha, max_bytes=MAX_BYTES, max_lines=MAX_LINES)
    parsed = parse_diff(text)
    files, totals, warnings = parsed["files"], parsed["totals"], list(parsed["warnings"])
    if cut:
        files, totals, more = _complete(reader, repo, from_sha, to_sha, files)
        warnings += more
    _mark_large(files)
    _binary_sizes(reader, repo, from_sha, to_sha, files)
    return {"files": files, "totals": totals, "truncated": cut, "warnings": warnings}


def _one_file(reader, repo, from_sha, to_sha, opts) -> dict:
    """Treść jednego pliku (także `large` i przyciętego) z kontekstem `options.context`."""
    path = opts["path"]
    text, cut = read.diff_capped(reader, repo, from_sha, to_sha, paths=[path], context=opts["context"],
                                 max_bytes=PATH_MAX_BYTES, max_lines=PATH_MAX_LINES)
    parsed = parse_diff(text)
    found = [f for f in parsed["files"] if f["path"] == path]
    if not found:
        raise DiffError("unknown-path", f"plik {path} nie ma zmian w tym zakresie")
    warnings = list(parsed["warnings"])
    if cut:
        found[0]["truncated"] = True
        warnings.append(f"Plik {path} przekracza limit podglądu pojedynczego pliku "
                        f"({PATH_MAX_BYTES >> 20} MB albo {PATH_MAX_LINES} linii): pokazano jego początek.")
    _binary_sizes(reader, repo, from_sha, to_sha, found)
    f = found[0]
    return {"files": found, "totals": {"files": 1, "additions": f["additions"], "deletions": f["deletions"]},
            "truncated": cut, "warnings": warnings, "path": path}


def _complete(reader, repo, from_sha, to_sha, parsed_files):
    """Diff ucięty limitem: pełna lista plików z `--raw`/`--numstat`; treść zostaje tylko plikom, które
    mieszczą się w przeczytanym tekście w całości, reszta dostaje `truncated: true` i pusty `hunks`."""
    meta, meta_cut = read.diff_files(reader, repo, from_sha, to_sha)
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
