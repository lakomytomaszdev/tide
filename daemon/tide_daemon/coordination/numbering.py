"""Kontrola numerów ticketów w planach repozytorium — bez I/O (reguły: docs/agent-plan-standard.md,
sekcja „Numery ticketów: jedno źródło”).

`conflicts(plans, tracker_projects, aliases, declared)` dostaje WSZYSTKIE plany repozytorium (główny
katalog, każdy worktree, każda gałąź lokalna — zbiera je repoplans.py) i zwraca listę ostrzeżeń
{kind, severity, ticket, prefix, plans, text}:

- `ticket-title` (twarde) — ten sam klucz ticketu (ids.key_of) z różnymi tytułami w dwóch RÓŻNYCH
  planach, także zakończonych. Kopie tego samego planu (ten sam plik w kilku worktree albo gałęziach,
  ewentualnie w różnych wersjach) to jeden plan: plany utożsamiamy po nazwie pliku i po tytule planu.
  Para planów `youtrack` się nie liczy — numer nadał YouTrack, a tytuł w planie może być parafrazą.
- `local-prefix-tracker` (twarde) — prefiks planu `local` (albo zadeklarowany w CLAUDE.md/AGENTS.md
  przy `tracker: local`) jest kluczem projektu w YouTracku. Bez listy projektów (None) — pomijamy.
- `prefix-alias` (twarde) — plan `youtrack`, którego ticket-prefix ≠ tracker-project, a repozytorium
  nie ma mapy migracji (docs/agents/youtrack-migration/).
- `reserved-elsewhere` (łagodne) — numer w planie `local`, którego tytuł nie ma rezerwacji, a numer
  jest zarezerwowany (tools/next_id.py, reservations.py) dla innego tytułu. Rezerwacja bez tytułu niczego
  nie rozstrzyga. Dawniej twarde, ale sam inny tytuł niczego nie dowodzi: koordynatorzy rezerwowali
  po angielsku i pisali wiersz planu po polsku (28.10: 15 fałszywych kolizji PT-29…PT-43). Prawdziwe
  zderzenie — ten sam numer z różnymi tytułami w dwóch planach — łapie twarde `duplicate-id`.
- `numbering-restart` (łagodne) — niezakończony plan `local`, którego nowe numery (te, których nie
  ma we wcześniejszych planach) zaczynają się nie wyżej niż największy numer wcześniejszych planów
  z tym prefiksem — numeracja od 1 zamiast ciągłej. Kolejność planów: data z nazwy pliku, a w obrębie
  dnia najniższy numer planu. Tracker i prefiks planu bierzemy z jego najnowszej kopii (`updated`).

Teksty po polsku, do pokazania jako zwykły tekst (okno Tide Sesje, raport poranny, tools/check_plans.py).
"""
import os
import re

from .ids import key_of

HARD, SOFT = "hard", "soft"
TICKET_RE = re.compile(r"([A-Za-z][A-Za-z0-9]{0,9})-(\d{1,6})")
PREFIX_RE = re.compile(r"[A-Z][A-Z0-9]{0,9}")
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
SECTION_HEAD = re.compile(r"^(#{1,6})\s+.*\(Tide\)\s*$")
DECLARED = {
    "prefix": re.compile(r"ticket-prefix:\s*`?\s*([A-Za-z][A-Za-z0-9]{0,9})\b"),
    "tracker": re.compile(r"(?<![-\w])tracker:\s*`?\s*(youtrack|local|none)\b"),
    "project": re.compile(r"tracker-project:\s*`?\s*([A-Za-z][A-Za-z0-9]{0,9})\b"),
}
MAX_WARNINGS = 50


def ticket_key(text: str) -> str:
    """Klucz ticketu z komórki planu („PT-07” → „PT-7”) albo pusty napis (krok bez ID)."""
    match = TICKET_RE.fullmatch((text or "").strip())
    return key_of(match.group(1), match.group(2)) if match else ""


def ticket_number(key: str) -> int:
    return _number(key)


def plan_keys(plan) -> list[str]:
    """Klucze ticketów z tabeli planu (wiersze bez ID pomijamy)."""
    return [k for k in (ticket_key(row.ticket) for row in plan.rows) if k]


def declared_prefix(text: str) -> dict:
    """{"prefix", "tracker", "project"} z sekcji „Plan pracy agentów (Tide)” (albo „Agent work plan
    (Tide)”) w CLAUDE.md/AGENTS.md; puste wartości, gdy sekcji albo pola nie ma."""
    out = {"prefix": "", "tracker": "", "project": ""}
    lines = (text or "").split("\n")
    body, level = [], 0
    for line in lines:
        head = re.match(r"^(#{1,6})\s", line)
        if level:
            if head and len(head.group(1)) <= level:
                break
            body.append(line)
        elif SECTION_HEAD.match(line.strip()):
            level = len(SECTION_HEAD.match(line.strip()).group(1))
    section = "\n".join(body)
    for key, pattern in DECLARED.items():
        match = pattern.search(section)
        if match:
            out[key] = match.group(1).upper() if key != "tracker" else match.group(1).lower()
    return out


def conflicts(plans, tracker_projects=None, aliases=None, declared=None, reservations=None) -> list[dict]:
    """Ostrzeżenia o numerach ticketów w planach jednego repozytorium (opis w nagłówku modułu).

    plans: obiekty plan.Plan (path, title, tracker, tracker_project, prefix, status, updated_ts, rows),
    tracker_projects: klucze projektów w YouTracku albo None (nieznane — kontrola prefiksu lokalnego
    pominięta), aliases: mapa migracji (ids.load_aliases) — niepusta wyłącza `prefix-alias`,
    declared: {"prefix", "tracker", "project", "source"} z CLAUDE.md/AGENTS.md albo None,
    reservations: rezerwacje numerów repozytorium [{"prefix", "number", "title"}] (reservations.load).
    """
    groups = _groups(plans)
    out = []
    out += _ticket_titles(groups)
    out += _local_prefix(groups, tracker_projects, declared)
    out += _prefix_alias(groups, aliases, declared)
    out += _restarts(groups)
    out += _reserved(groups, reservations)
    out.sort(key=lambda w: (w["severity"] != HARD, w["kind"], w["prefix"], _number(w["ticket"])))
    return out[:MAX_WARNINGS]


def hard(warnings) -> list[dict]:
    return [w for w in warnings or () if w.get("severity") == HARD]


# --- wnętrze -----------------------------------------------------------------------------------

class _Group:
    """Jeden plan: wszystkie jego kopie (worktree, gałęzie) i wersje."""

    def __init__(self, name: str):
        self.name = name                    # nazwa pliku bez .md — tak plan nazywamy w tekstach
        self.title = ""
        self.tracker = "none"
        self.project = ""
        self.prefixes: set[str] = set()     # ticket-prefix najnowszej kopii
        self.status = "done"
        self.date = ""
        self.updated = 0.0
        self.titles: dict[str, dict] = {}   # klucz → {tytuł znormalizowany: tytuł do pokazania}

    def add(self, plan) -> None:
        if not self.title:
            self.title = plan.title
        if plan.updated_ts >= self.updated:
            # Tracker i prefiks planu to te z najnowszej kopii: stara kopia w zapomnianym worktree
            # (np. sprzed zmiany prefiksu) nie zmienia, czym plan jest dziś.
            self.tracker, self.project = plan.tracker, plan.tracker_project.upper()
            self.prefixes = {plan.prefix.upper()} if plan.prefix else set()
        self.updated = max(self.updated, plan.updated_ts)
        if plan.status != "done":
            self.status = plan.status
        for row in plan.rows:
            key = ticket_key(row.ticket)
            if key:
                self.titles.setdefault(key, {})[_norm(row.title)] = row.title.strip()

    def numbers(self, prefix: str) -> set[int]:
        return {_number(k) for k in self.titles if k.split("-")[0] == prefix}

    def row_prefixes(self) -> set[str]:
        return {k.split("-")[0] for k in self.titles}


def _groups(plans) -> list[_Group]:
    by_name: dict[str, _Group] = {}
    by_title: dict[str, _Group] = {}
    out: list[_Group] = []
    for plan in plans or ():
        name = _name(plan.path)
        title = _norm(plan.title)
        group = by_name.get(name) or (by_title.get(title) if title else None)
        if group is None:
            group = _Group(name)
            match = DATE_RE.match(name)
            group.date = match.group(1) if match else ""
            out.append(group)
        by_name.setdefault(name, group)
        if title:
            by_title.setdefault(title, group)
        group.add(plan)
    return out


def _name(path: str) -> str:
    base = os.path.basename(path or "")
    return base[:-3] if base.endswith(".md") else base


def _norm(title: str) -> str:
    text = " ".join((title or "").casefold().split())
    return text.rstrip(" .;:,")


def _number(key: str) -> int:
    tail = (key or "").rpartition("-")[2]
    return int(tail) if tail.isdigit() else 0


def _warning(kind, severity, text, plans, ticket="", prefix="") -> dict:
    return {"kind": kind, "severity": severity, "ticket": ticket, "prefix": prefix,
            "plans": sorted(set(plans)), "text": text}


def _ticket_titles(groups) -> list[dict]:
    keys: dict[str, list] = {}
    for group in groups:
        for key, titles in group.titles.items():
            keys.setdefault(key, []).append((group, titles))
    out = []
    for key, entries in keys.items():
        involved = []
        for i, (a, ta) in enumerate(entries):
            for b, tb in entries[i + 1:]:
                if a.tracker == "youtrack" and b.tracker == "youtrack":
                    continue
                if not (set(ta) & set(tb)):
                    for g, titles in ((a, ta), (b, tb)):
                        if g not in [x for x, _ in involved]:
                            involved.append((g, titles))
        if not involved:
            continue
        parts = [f"„{next(iter(titles.values())) or '(bez tytułu)'}” ({g.name})" for g, titles in involved]
        text = (f"{key} ma różne tytuły w planach: " + ", ".join(parts)
                + " — jeden numer to jedno zadanie; nowy ticket dostaje największy numer + 1.")
        out.append(_warning("ticket-title", HARD, text, [g.name for g, _ in involved], ticket=key,
                            prefix=key.split("-")[0]))
    return out


def _local_prefix(groups, tracker_projects, declared) -> list[dict]:
    if tracker_projects is None:
        return []
    known = {str(p).upper() for p in tracker_projects if p}
    found: dict[str, list] = {}
    for group in groups:
        if group.tracker != "local":
            continue
        for prefix in group.prefixes or group.row_prefixes():
            if prefix in known:
                found.setdefault(prefix, []).append(group.name)
    if declared and declared.get("tracker") == "local" and (declared.get("prefix") or "").upper() in known:
        found.setdefault(declared["prefix"].upper(), []).append(declared.get("source") or "CLAUDE.md")
    out = []
    for prefix, names in sorted(found.items()):
        text = (f"Prefiks lokalny {prefix} ({', '.join(sorted(set(names)))}) jest kluczem projektu w YouTracku — "
                f"{prefix}-n w planie i w YouTracku to różne zadania; wybierz wolny prefiks.")
        out.append(_warning("local-prefix-tracker", HARD, text, names, prefix=prefix))
    return out


def _prefix_alias(groups, aliases, declared) -> list[dict]:
    if aliases:
        return []
    out = []
    items = [(g.name, g.prefixes, g.project) for g in groups if g.tracker == "youtrack"]
    if declared and declared.get("tracker") == "youtrack":
        items.append((declared.get("source") or "CLAUDE.md", {(declared.get("prefix") or "").upper()} - {""},
                      (declared.get("project") or "").upper()))
    for name, prefixes, project in items:
        for prefix in sorted(prefixes):
            if project and prefix != project:
                text = (f"{name}: ticket-prefix {prefix} ≠ tracker-project {project}, a repozytorium nie ma mapy "
                        f"migracji (docs/agents/youtrack-migration/) — ID w planie mają być kluczami z YouTracka "
                        f"({project}-n).")
                out.append(_warning("prefix-alias", HARD, text, [name], prefix=prefix))
    return out


def _restarts(groups) -> list[dict]:
    local = [g for g in groups if g.tracker == "local"]
    prefixes = sorted({p for g in local for p in (g.prefixes or g.row_prefixes())})
    out = []
    for prefix in prefixes:
        # Kolejność założenia: data z nazwy pliku, a w obrębie dnia — od najniższego numeru (plany
        # założone jednego dnia z ciągłą numeracją nie dają ostrzeżenia).
        mine = [g for g in local if prefix in (g.prefixes or g.row_prefixes()) and g.numbers(prefix)]
        mine.sort(key=lambda g: (g.date or "9999", min(g.numbers(prefix)), g.name))
        for i, group in enumerate(mine):
            if group.status == "done" or i == 0:
                continue
            earlier = mine[:i]
            used = set().union(*(g.numbers(prefix) for g in earlier))
            fresh = group.numbers(prefix) - used
            top = max(used)
            if fresh and min(fresh) <= top:
                text = (f"Plan {group.name} zaczyna nowe numery od {prefix}-{min(fresh)}, a wcześniejsze plany "
                        f"doszły już do {prefix}-{top} — numeracja jest ciągła w repozytorium, następny numer to "
                        f"{prefix}-{top + 1}.")
                names = [group.name] + [g.name for g in earlier if top in g.numbers(prefix)]
                out.append(_warning("numbering-restart", SOFT, text, names, ticket=f"{prefix}-{min(fresh)}",
                                    prefix=prefix))
    return out


def _reserved(groups, reservations) -> list[dict]:
    reserved: dict[str, set] = {}
    shown: dict[str, str] = {}
    for item in reservations or ():
        prefix = str(item.get("prefix") or "").upper()
        number = str(item.get("number") or "")
        title = " ".join(str(item.get("title") or "").split())
        if not (prefix and number.isdigit() and title):
            continue
        key = key_of(prefix, number)
        reserved.setdefault(key, set()).add(_norm(title))
        shown.setdefault(key, title)
    out = []
    for key in sorted(reserved, key=lambda k: (k.split("-")[0], _number(k))):
        for group in groups:
            titles = group.titles.get(key)
            if group.tracker != "local" or not titles or set(titles) & reserved[key]:
                continue
            text = (f"{key} („{next(iter(titles.values())) or '(bez tytułu)'}”, {group.name}) jest zarezerwowany "
                    f"dla „{shown[key]}” — jeśli to inny ticket, weź nowy numer z "
                    f"tide next-id; jeśli ten sam (inny język albo słowa), nic nie rób.")
            out.append(_warning("reserved-elsewhere", SOFT, text, [group.name], ticket=key, prefix=key.split("-")[0]))
    return out
