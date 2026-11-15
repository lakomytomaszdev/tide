"""Rezerwacja lokalnych numerów ticketów — żeby równoległe sesje nie wzięły tego samego numeru.

„Największy + 1” liczone osobno przez dwie sesje daje wyścig (27.10.2026 dwie sesje plasma-tide wzięły
PT-22). Dlatego nowy numer lokalny daje `reserve()` (tools/next_id.py, `make next-id`):

- rejestr `$XDG_STATE_HOME/tide/ticket-ids.json` (domyślnie ~/.local/state/tide/, plik 0600, katalog
  0700): {"version": 1, "reservations": [{"repo", "prefix", "number", "title", "ts"}]}, gdzie `repo` to
  główny katalog repozytorium (pierwszy z `git worktree list`), więc worktree dzielą numerację,
- cała operacja (odczyt rejestru, plany, zapis) idzie pod blokadą `flock` na pliku rejestru; zapis
  w miejscu (bez podmiany pliku), bo podmiana zgubiłaby blokadę czekającego procesu,
- największy numer = max(rezerwacje repozytorium z tym prefiksem, numery z tym prefiksem we wszystkich
  planach ze wszystkich worktree i gałęzi — repoplans.py); pusty rejestr zasiewa się więc z planów,
- prefiks z YouTracka (plan albo deklaracja `tracker: youtrack`) — odmowa: numer nadaje YouTrack.
- `request_key` (opcjonalny) zapisuje tożsamość żądania przy numerach; ponowienie tego samego żądania
  zwraca wcześniejsze numery nawet po awarii przed zapisem wyniku w dzienniku karty.
- `reserve_many()` — rezerwacja zbiorcza dla zatwierdzenia planu karty (K-102): wiele żądań z kluczami pod jedną
  blokadą, z jednym odczytem planów i jednym zapisem; wpisy jak z `reserve(count=1, request_key=…)`.

`load(repo)` czyta rezerwacje repozytorium bez blokady (tylko odczyt) — dla kontroli numerów
(numbering.py: numer w planie zarezerwowany dla innego tytułu).
"""
import fcntl
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone

from . import numbering
from .repoplans import RepoPlans, main_root

VERSION = 1
MAX_COUNT = 50
REFUSE_YOUTRACK = "numer nadaje YouTrack — załóż ticket tam"


class ReservationError(Exception):
    """Odmowa albo błąd rezerwacji — komunikat po polsku."""


def registry_path(env=None) -> str:
    env = os.environ if env is None else env
    state = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "tide", "ticket-ids.json")


def load(repo: str, path: str | None = None, git=None) -> list[dict]:
    """Rezerwacje repozytorium (wszystkie prefiksy) — bez blokady, pusty rejestr albo błąd → []."""
    try:
        with open(path or registry_path(), encoding="utf-8") as fh:
            data = _parse(fh.read())
    except OSError:
        return []
    key = main_root(repo, git) if git else main_root(repo)
    return [r for r in data["reservations"] if r.get("repo") == key]


def reserve(repo: str, prefix: str = "", count: int = 1, title: str = "", dry_run: bool = False,
            path: str | None = None, repo_plans=None, clock=time.time, request_key: str = "",
            at_least: int = 0, local_fallback: bool = False) -> dict:
    """{"repo", "prefix", "ids", "numbers", "seed"} — kolejne `count` numerów; dry_run nic nie zapisuje.

    `seed`: skąd największy dotychczasowy numer („rejestr”, „plany” albo „brak” — pierwszy numer to 1).
    `request_key`: niepusty klucz oznacza jedno żądanie w obrębie repozytorium i prefiksu.
    Ponowienie wymaga tego samego tytułu i liczby numerów; pusty klucz zachowuje dawne działanie."""
    if not 1 <= int(count) <= MAX_COUNT:
        raise ReservationError(f"liczba numerów od 1 do {MAX_COUNT}")
    if not isinstance(at_least, int) or at_least < 0:
        raise ReservationError("minimalny numer musi być nieujemną liczbą całkowitą")
    _check_key(request_key)
    normalized_title = " ".join((title or "").split())
    if not repo or not os.path.isdir(repo):
        raise ReservationError(f"nie ma katalogu repozytorium {repo}")
    reader = repo_plans or RepoPlans(ttl=0)
    key = main_root(repo, reader.git)
    path = path or registry_path()
    # Odmowę (YouTrack, brak prefiksu) sprawdzamy też przed blokadą — wtedy rejestr nie powstaje.
    plans, declared = reader.read([repo])
    resolved = _prefix(prefix, plans, declared, key)
    if local_fallback and not (prefix or "").strip():
        # Przepływ karty bez integracji YouTracka w Tide (04.11, K-49 mobileapp): repozytorium deklaruje YouTrack,
        # ale Tide go dla projektu nie używa — karta dostaje lokalny numer z osobnym prefiksem zamiast odmowy
        # „załóż ticket tam”. Prefiks lokalny nie może być kluczem YouTracka, żeby numery się nie myliły.
        resolved = _local_prefix(resolved, plans, declared, key)
        prefix = resolved
    _refuse_youtrack(resolved, plans, declared)
    if dry_run:
        # Podgląd: bez tworzenia rejestru, pod blokadą współdzieloną, gdy plik już jest.
        try:
            fh = open(path, encoding="utf-8")
        except FileNotFoundError:
            fh = None
        if fh is None:
            numbers, from_registry, from_plans, prefix = _next(reader, repo, key, prefix, count, {"reservations": []}, at_least)
            existing = None
        else:
            with fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
                data = _parse(fh.read())
                numbers, from_registry, from_plans, prefix = _next(reader, repo, key, prefix, count, data, at_least)
                existing = _existing_request(data, key, prefix, request_key, int(count), normalized_title)
    else:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)  # zwalnia ją zamknięcie pliku
            data = _parse(fh.read())
            # Plany czytamy pod blokadą: sesja przed nami jest już w rejestrze albo w swoim planie.
            numbers, from_registry, from_plans, prefix = _next(reader, repo, key, prefix, count, data, at_least)
            existing = _existing_request(data, key, prefix, request_key, int(count), normalized_title)
            if existing is None:
                seed = "rejestr" if from_registry >= from_plans and from_registry else "plany" if from_plans else "brak"
                stamp = datetime.fromtimestamp(clock(), timezone.utc).astimezone().isoformat(timespec="seconds")
                for n in numbers:
                    entry = {"repo": key, "prefix": prefix, "number": n,
                             "title": normalized_title[:200], "ts": stamp}
                    if request_key:
                        entry.update({"request_key": request_key, "request_count": int(count),
                                      "request_title": normalized_title, "request_seed": seed})
                    data["reservations"].append(entry)
                fh.seek(0)
                fh.truncate()
                json.dump(data, fh, ensure_ascii=False, indent=1)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
    if existing is None:
        seed = "rejestr" if from_registry >= from_plans and from_registry else "plany" if from_plans else "brak"
    else:
        numbers, seed = existing
    return {"repo": key, "prefix": prefix, "numbers": numbers, "ids": [f"{prefix}-{n}" for n in numbers],
            "seed": seed, "dryRun": bool(dry_run)}


def reserve_many(repo: str, requests, prefix: str = "", at_least: int = 0, local_fallback: bool = False,
                 path: str | None = None, repo_plans=None, clock=time.time) -> dict:
    """Rezerwacja zbiorcza (K-102 #1): po jednym numerze na żądanie `{"request_key", "title"}`, jednym
    przebiegiem — jedna blokada rejestru, jeden odczyt planów repozytorium, najwyżej jeden zapis.

    Wynik: {"repo", "prefix", "numbers", "ids", "seeds"} w kolejności żądań. Żądanie z kluczem zapisanym
    wcześniej (także przez `reserve(count=1, request_key=…)`) zwraca zapisany numer; brakujące dostają kolejne
    numery od max(rejestr, plany, at_least) + 1, w kolejności żądań. Nowe wpisy mają kształt jak z
    `reserve(count=1, request_key=…)`, więc pojedyncze ponowienie (odzyskanie zamiaru po restarcie) zwraca ten sam
    numer. Więcej niż MAX_COUNT nowych numerów, odmowa (YouTrack, prefiks) albo błąd żądania — ReservationError
    bez żadnego zapisu; odmowa nie tworzy rejestru."""
    if not isinstance(at_least, int) or at_least < 0:
        raise ReservationError("minimalny numer musi być nieujemną liczbą całkowitą")
    if not isinstance(requests, (list, tuple)) or not requests:
        raise ReservationError("rezerwacja zbiorcza wymaga co najmniej jednego żądania")
    wanted = []
    for request in requests:
        request_key = request.get("request_key") if isinstance(request, dict) else None
        _check_key(request_key)
        if not request_key:
            raise ReservationError("rezerwacja zbiorcza wymaga klucza każdego żądania")
        wanted.append((request_key, " ".join((request.get("title") or "").split())))
    if len({k for k, _t in wanted}) != len(wanted):
        raise ReservationError("rezerwacja zbiorcza: powtórzony klucz żądania")
    if not repo or not os.path.isdir(repo):
        raise ReservationError(f"nie ma katalogu repozytorium {repo}")
    reader = repo_plans or RepoPlans(ttl=0)
    key = main_root(repo, reader.git)
    path = path or registry_path()
    read = None
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        # Rejestru jeszcze nie ma: odmowę sprawdzamy przed jego utworzeniem (odmowa nie tworzy pliku). Ten odczyt
        # planów służy też do liczenia pod blokadą — sesja, która zdąży przed nami, ma swoje numery w rejestrze
        # (czytanym już pod blokadą), bo rezerwacja zapisuje rejestr przed planem; jeden odczyt planów na wywołanie.
        read = reader.read([repo])
        _resolve_prefix(prefix, local_fallback, *read, key)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)  # zwalnia ją zamknięcie pliku
        data = _parse(fh.read())
        plans, declared = read if read is not None else reader.read([repo])  # rejestr był — plany pod blokadą
        resolved = _resolve_prefix(prefix, local_fallback, plans, declared, key)
        by_key = {}
        for r in data["reservations"]:
            if r.get("repo") == key and r.get("prefix") == resolved and r.get("request_key"):
                by_key.setdefault(r["request_key"], []).append(r)
        found = {}
        for request_key, title in wanted:
            existing = _existing_request({"reservations": by_key.get(request_key, [])}, key, resolved,
                                         request_key, 1, title)
            if existing is not None:
                found[request_key] = existing
        new = [(k, t) for k, t in wanted if k not in found]
        if len(new) > MAX_COUNT:
            raise ReservationError(f"rezerwacja zbiorcza: żądanie {len(new)} nowych numerów przekracza limit "
                                   f"{MAX_COUNT} — nic nie zarezerwowano")
        if new:
            mine = [r for r in data["reservations"] if r.get("repo") == key and r.get("prefix") == resolved]
            from_registry = max((_int(r.get("number")) for r in mine), default=0)
            from_plans = _plans_top(plans, resolved)
            top = max(from_registry, from_plans, at_least)
            seed = "rejestr" if from_registry >= from_plans and from_registry else "plany" if from_plans else "brak"
            stamp = datetime.fromtimestamp(clock(), timezone.utc).astimezone().isoformat(timespec="seconds")
            for offset, (request_key, title) in enumerate(new, start=1):
                data["reservations"].append({"repo": key, "prefix": resolved, "number": top + offset,
                                             "title": title[:200], "ts": stamp, "request_key": request_key,
                                             "request_count": 1, "request_title": title, "request_seed": seed})
                found[request_key] = ([top + offset], seed)
            fh.seek(0)
            fh.truncate()
            json.dump(data, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    numbers = [found[k][0][0] for k, _t in wanted]
    return {"repo": key, "prefix": resolved, "numbers": numbers, "ids": [f"{resolved}-{n}" for n in numbers],
            "seeds": [found[k][1] for k, _t in wanted], "dryRun": False}


# --- wnętrze -----------------------------------------------------------------------------------

def _check_key(request_key) -> None:
    if not isinstance(request_key, str) or (request_key and
            (len(request_key) > 128 or not request_key.isprintable() or
             request_key.strip() != request_key or any(c.isspace() for c in request_key))):
        raise ReservationError("niepoprawny klucz rezerwacji")


def _resolve_prefix(prefix: str, local_fallback: bool, plans, declared, key: str) -> str:
    """Prefiks rezerwacji jak w `reserve`: jawny, zadeklarowany albo z planów; odmowa dla YouTracka."""
    resolved = _prefix(prefix, plans, declared, key)
    if local_fallback and not (prefix or "").strip():
        resolved = _local_prefix(resolved, plans, declared, key)
    _refuse_youtrack(resolved, plans, declared)
    return resolved


def _plans_top(plans, prefix: str) -> int:
    return max((numbering.ticket_number(k) for p in plans for k in numbering.plan_keys(p)
                if k.split("-")[0] == prefix), default=0)


def _existing_request(data, repo, prefix, request_key, count, title):
    if not request_key:
        return None
    entries = [r for r in data["reservations"] if r.get("repo") == repo and
               r.get("prefix") == prefix and r.get("request_key") == request_key]
    if not entries:
        return None
    if (len(entries) != count or any(r.get("request_count") != count or
                                     r.get("request_title") != title for r in entries)):
        raise ReservationError("klucz rezerwacji użyty dla innego żądania albo niepełny zapis")
    numbers = sorted(_int(r.get("number")) for r in entries)
    if numbers[0] < 1 or numbers != list(range(numbers[0], numbers[0] + count)):
        raise ReservationError("rezerwacja dla klucza ma niepoprawne numery")
    seed = entries[0].get("request_seed")
    if seed not in ("rejestr", "plany", "brak") or any(r.get("request_seed") != seed for r in entries):
        raise ReservationError("rezerwacja dla klucza ma niepoprawne źródło")
    return numbers, seed

def _next(reader, repo, key, prefix, count, data, at_least=0) -> tuple[list, int, int, str]:
    """(kolejne numery, największy z rejestru, największy z planów, prefiks)."""
    plans, declared = reader.read([repo])
    prefix = _prefix(prefix, plans, declared, key)
    _refuse_youtrack(prefix, plans, declared)
    mine = [r for r in data["reservations"] if r.get("repo") == key and r.get("prefix") == prefix]
    from_registry = max((_int(r.get("number")) for r in mine), default=0)
    from_plans = _plans_top(plans, prefix)
    top = max(from_registry, from_plans, at_least)
    return list(range(top + 1, top + 1 + int(count))), from_registry, from_plans, prefix


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

def _parse(text: str) -> dict:
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        raise ReservationError("rejestr numerów jest uszkodzony — popraw albo usuń plik ticket-ids.json") from None
    if not isinstance(data, dict):
        data = {}
    items = data.get("reservations")
    data = {"version": VERSION, "reservations": [r for r in items if isinstance(r, dict)]
            if isinstance(items, list) else []}
    return data


def repo_prefix(repo: str) -> str:
    """Prefiks z nazwy repozytorium, gdy projekt go nie deklaruje: inicjały słów (`legal-agents` → LA,
    `LegalApp` → LA) albo pierwsze cztery litery jednego słowa (`streamer` → KAMP). Deterministyczny, więc
    kolejne rezerwacje tego repozytorium dostają ten sam prefiks (K-41, 04.11: pierwszy start nowego projektu
    kończył się „nie znam prefiksu” i kazał użytkownikowi edytować CLAUDE.md)."""
    name = os.path.basename(os.path.normpath(repo or ""))
    words = [w for w in re.split(r"[^A-Za-z]+|(?<=[a-z])(?=[A-Z])", name) if w]
    prefix = "".join(w[0] for w in words).upper() if len(words) > 1 else (words[0][:4].upper() if words else "")
    return prefix if numbering.PREFIX_RE.fullmatch(prefix) and len(prefix) >= 2 else "PT"


def _prefix(prefix: str, plans, declared, repo: str = "") -> str:
    prefix = (prefix or "").strip().upper()
    if not prefix:
        prefix = (declared or {}).get("prefix") or ""
    if not prefix:
        counts = Counter(p.prefix for p in plans if p.tracker == "local" and p.prefix)
        prefix = counts.most_common(1)[0][0] if counts else ""
    if not prefix and repo:
        prefix = repo_prefix(repo)
    if not prefix:
        raise ReservationError("nie znam prefiksu — podaj --prefix albo zapisz ticket-prefix w CLAUDE.md")
    if not numbering.PREFIX_RE.fullmatch(prefix):
        raise ReservationError(f"niepoprawny prefiks „{prefix}”")
    return prefix


def _youtrack_prefix(prefix: str, plans, declared) -> bool:
    try:
        _refuse_youtrack(prefix, plans, declared)
    except ReservationError:
        return True
    return False


def _local_prefix(prefix: str, plans, declared, repo: str) -> str:
    """Prefiks dla lokalnej numeracji karty: ten sam, gdy nie należy do YouTracka; inaczej z nazwy repozytorium,
    a gdy i ten jest kluczem YouTracka — z dopiskiem L."""
    if not _youtrack_prefix(prefix, plans, declared):
        return prefix
    for candidate in (repo_prefix(repo) if repo else "", (repo_prefix(repo) if repo else prefix) + "L"):
        if candidate and numbering.PREFIX_RE.fullmatch(candidate) and \
                not _youtrack_prefix(candidate, plans, declared):
            return candidate
    raise ReservationError(f"{prefix}: {REFUSE_YOUTRACK}")


def _refuse_youtrack(prefix: str, plans, declared) -> None:
    declared = declared or {}
    if declared.get("tracker") == "youtrack" and prefix in (declared.get("prefix"), declared.get("project")):
        raise ReservationError(f"{prefix}: {REFUSE_YOUTRACK} ({declared.get('source') or 'CLAUDE.md'}: tracker youtrack)")
    newest = {}
    for plan in plans:
        if plan.prefix == prefix and plan.updated_ts >= newest.get(os.path.basename(plan.path), (-1, ""))[0]:
            newest[os.path.basename(plan.path)] = (plan.updated_ts, plan.tracker)
        if plan.tracker == "youtrack" and plan.tracker_project.upper() == prefix:
            raise ReservationError(f"{prefix}: {REFUSE_YOUTRACK} (projekt YouTracka w planie)")
    for name, (_ts, tracker) in sorted(newest.items()):
        if tracker == "youtrack":
            raise ReservationError(f"{prefix}: {REFUSE_YOUTRACK} (plan {name[:-3]}: tracker youtrack)")
