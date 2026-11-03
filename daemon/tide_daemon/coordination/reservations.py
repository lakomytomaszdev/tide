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

`load(repo)` czyta rezerwacje repozytorium bez blokady (tylko odczyt) — dla kontroli numerów
(numbering.py: numer w planie zarezerwowany dla innego tytułu).
"""
import fcntl
import json
import os
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
            at_least: int = 0) -> dict:
    """{"repo", "prefix", "ids", "numbers", "seed"} — kolejne `count` numerów; dry_run nic nie zapisuje.

    `seed`: skąd największy dotychczasowy numer („rejestr”, „plany” albo „brak” — pierwszy numer to 1).
    `request_key`: niepusty klucz oznacza jedno żądanie w obrębie repozytorium i prefiksu.
    Ponowienie wymaga tego samego tytułu i liczby numerów; pusty klucz zachowuje dawne działanie."""
    if not 1 <= int(count) <= MAX_COUNT:
        raise ReservationError(f"liczba numerów od 1 do {MAX_COUNT}")
    if not isinstance(at_least, int) or at_least < 0:
        raise ReservationError("minimalny numer musi być nieujemną liczbą całkowitą")
    if not isinstance(request_key, str) or (request_key and
            (len(request_key) > 128 or not request_key.isprintable() or
             request_key.strip() != request_key or any(c.isspace() for c in request_key))):
        raise ReservationError("niepoprawny klucz rezerwacji")
    normalized_title = " ".join((title or "").split())
    if not repo or not os.path.isdir(repo):
        raise ReservationError(f"nie ma katalogu repozytorium {repo}")
    reader = repo_plans or RepoPlans(ttl=0)
    key = main_root(repo, reader.git)
    path = path or registry_path()
    # Odmowę (YouTrack, brak prefiksu) sprawdzamy też przed blokadą — wtedy rejestr nie powstaje.
    plans, declared = reader.read([repo])
    _refuse_youtrack(_prefix(prefix, plans, declared), plans, declared)
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


# --- wnętrze -----------------------------------------------------------------------------------

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
    prefix = _prefix(prefix, plans, declared)
    _refuse_youtrack(prefix, plans, declared)
    mine = [r for r in data["reservations"] if r.get("repo") == key and r.get("prefix") == prefix]
    from_registry = max((_int(r.get("number")) for r in mine), default=0)
    from_plans = max((numbering.ticket_number(k) for p in plans for k in numbering.plan_keys(p)
                      if k.split("-")[0] == prefix), default=0)
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


def _prefix(prefix: str, plans, declared) -> str:
    prefix = (prefix or "").strip().upper()
    if not prefix:
        prefix = (declared or {}).get("prefix") or ""
    if not prefix:
        counts = Counter(p.prefix for p in plans if p.tracker == "local" and p.prefix)
        prefix = counts.most_common(1)[0][0] if counts else ""
    if not prefix:
        raise ReservationError("nie znam prefiksu — podaj --prefix albo zapisz ticket-prefix w CLAUDE.md")
    if not numbering.PREFIX_RE.fullmatch(prefix):
        raise ReservationError(f"niepoprawny prefiks „{prefix}”")
    return prefix


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
