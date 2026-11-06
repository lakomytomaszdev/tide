"""Sprawdzenie propozycji planu karty przed nadaniem numerów ticketom.

Plik planu leży obok katalogu ``tickets``; kartka ``#2`` to ``tickets/2.md``.
Wynik zawiera wyłącznie dane sprawdzone i teksty kartek, gotowe do migawki w dzienniku karty.

Ticket z innego planu (K-33, decyzja D2): wiersz może mieć zamiast numeru roboczego ID istniejącego ticketu,
który w innym planie tego repozytorium ma stan ``todo`` i ten sam tytuł (``other_plans``). Numer zostaje,
a wiersz niesie ``fromPlan`` — ścieżkę planu, z którego go wzięto. Inny tytuł byłby twardą kolizją numerów.

Projekty zlecenia (K-33 #4, standard 1.3): ``projects`` to {slug: {name, prefix, tracker, trackerProject,
configured}} — projekty Tablicy, które użytkownik wybrał do zlecenia (pierwszy to projekt karty). Kolumna
``Projekt`` wiersza musi wskazywać jeden z nich (pusta = projekt karty), gotowe ID wiersza musi mieć prefiks
swojego projektu, a projekt z YouTrackiem wymaga skonfigurowanego trackera na Tablicy. Bez ``projects``
sprawdzanie działa jak przed 1.3: każdy wiersz należy do projektu karty.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import planartifacts

from ..board.model import PROJECTS_MAX
from ..coordination import plan as plan_mod
from ..models.executors import CLASSES

WORK_ID = re.compile(r"^#([1-9][0-9]{0,5})$")
CRITERION = re.compile(r"(?m)^\s*-\s*\[\s*\]\s*\S")
NUMBERED = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)-[1-9][0-9]*$")
# Projekty w jednym planie: projekt karty i najwyżej PROJECTS_MAX dodatkowych (jak `projects` karty).
PLAN_PROJECTS_MAX = PROJECTS_MAX + 1



def independent_reviewer(author, reviewer) -> bool:
    """Recenzent niezależny od autora: inny model albo ten sam model z inną siłą myślenia (05.11, decyzja
    użytkownika: Codex mocny może recenzować Codexa standard — przy kończącym się limicie Claude jedyną inną
    opcją był drogi Codex Astra)."""
    if author.model.lower() != reviewer.model.lower():
        return True
    return (getattr(author, "effort", "") or "") != (getattr(reviewer, "effort", "") or "")


def source_label(info: dict) -> str:
    """Źródło numerów projektu: „lokalny PT” albo „YouTrack APP5”."""
    if info.get("tracker") == "youtrack":
        return f"YouTrack {info.get('trackerProject') or info.get('prefix') or ''}".strip()
    return f"lokalny {info.get('prefix') or ''}".strip()


@dataclass
class Proposal:
    plan: object | None
    rows: list[dict]
    cards: dict[str, str]
    waves: list[list[str]]
    errors: list[str]
    artifacts: dict[str, bytes] = field(default_factory=dict)


def _same_title(a: str, b: str) -> bool:
    return " ".join((a or "").casefold().split()) == " ".join((b or "").casefold().split())


def read(path: str, card_id: str, catalog, *, allowed_roots=(), tracker="local", existing_ids=(), imported=False,
         other_plans=None, projects=None) -> Proposal:
    """Czyta plan i kartki wyłącznie z dozwolonych katalogów; nie rezerwuje numerów."""
    projects = dict(projects or {})
    main = next(iter(projects), "")
    errors: list[str] = []
    real = os.path.realpath(path)
    if not real.lower().endswith(".md") or not any(_inside(real, root) for root in allowed_roots):
        return Proposal(None, [], {}, [], ["plik planu musi być Markdownem w repozytorium lub katalogu kroku"])
    try:
        artifacts = planartifacts.read(os.path.dirname(path), plan_name=os.path.basename(path))
    except (OSError, UnicodeError, ValueError) as exc:
        return Proposal(None, [], {}, [], [f"niepoprawne artefakty planowania: {exc}"])
    try:
        content = artifacts[os.path.basename(path)].decode("utf-8")
    except (KeyError, UnicodeError, ValueError) as exc:
        return Proposal(None, [], {}, [], [f"nie można przeczytać planu: {exc}"])
    plan = plan_mod.parse(content, real)
    if plan is None:
        found = re.search(r"^tide-plan:\s*(\S+)", content, re.M)
        return Proposal(None, [], {}, [], [f"nagłówek ma tide-plan: {found.group(1)}, a powinien mieć tide-plan: 1"
                                            if found else "plik nie jest planem Tide (brak tide-plan: 1)"])
    errors.extend(plan.errors)
    if plan.card != card_id and not (imported and not plan.card):
        errors.append(f"plan musi wskazywać kartę {card_id} przez klucz card")
    if plan.tables != 1:
        errors.append(f"plan musi mieć jedną tabelę ticketów (ma {plan.tables})")
    if plan.tracker not in ('local', tracker):
        errors.append('tracker planu różni się od trackera projektu')
    if not plan.prefix:
        errors.append("plan nie ma ticket-prefix")
    if not plan.rows:
        errors.append("plan nie ma ticketów")
    seen, rows, cards = set(), [], {}
    used: list[str] = []
    folder = os.path.join(os.path.dirname(real), "tickets")
    for row in plan.rows:
        ident = row.ticket
        work = WORK_ID.fullmatch(ident)
        slug = (row.project or main) if projects else ""
        info = projects.get(slug)
        if projects and slug not in used:
            used.append(slug)
            if len(used) == PLAN_PROJECTS_MAX + 1:
                errors.append(f"{ident}: projekt „{slug}” to {len(used)}. projekt planu — plan może obejmować "
                              f"najwyżej {PLAN_PROJECTS_MAX} projektów (projekt karty i {PROJECTS_MAX} dodatkowych)")
        if projects and info is None:
            errors.append(f"{ident}: projekt „{slug}” w kolumnie Projekt jest spoza projektów zlecenia "
                          f"({', '.join(projects)})")
        elif info is not None and info.get("tracker") == "youtrack" and not info.get("configured", True):
            errors.append(f"{ident}: projekt „{info.get('name') or slug}” numeruje tickety w YouTracku "
                          f"({info.get('trackerProject') or info.get('prefix')}), a na Tablicy nie skonfigurowano "
                          f"trackera YouTrack")
        prefix = (info or {}).get("prefix") or plan.prefix
        numbered = NUMBERED.fullmatch(ident)
        owners = [s for s, p in projects.items() if numbered and p.get("prefix") == numbered.group(1)]
        foreign = bool(info) and bool(owners) and slug not in owners
        already_numbered = imported and ident in existing_ids and re.fullmatch(rf"{re.escape(prefix)}-[1-9][0-9]*", ident)
        other = None if work or ident in existing_ids else (other_plans or {}).get(ident)
        from_other = bool(other) and bool(re.fullmatch(rf"{re.escape(prefix)}-[1-9][0-9]*", ident))
        if foreign:
            errors.append(f"{ident}: gotowe ID ma prefiks projektu „{owners[0]}”, a kolumna Projekt wskazuje "
                          f"„{slug}” (prefiks {prefix})")
        elif from_other and not _same_title(other["title"], row.title):
            errors.append(f"{ident}: ticket z innego planu musi mieć ten sam tytuł co w "
                          f"{os.path.basename(other['path'])}: „{other['title']}”")
        elif not work and ident not in existing_ids and not already_numbered and not from_other:
            errors.append(f"{ident}: propozycja wymaga numeru roboczego #n; ID powstaje po zatwierdzeniu "
                          f"(istniejący ticket z innego planu musi tam mieć stan todo)")
        if ident in seen:
            errors.append(f"{ident}: powtórzony ticket")
        seen.add(ident)
        if not row.title:
            errors.append(f"{ident}: brak tytułu")
        if row.state != "todo" and not imported:
            errors.append(f"{ident}: propozycja wymaga stanu todo")
        klass = row.work_class or "standard"
        if klass not in CLASSES:
            errors.append(f"{ident}: nieznana klasa „{klass}”")
        author = catalog.resolve(row.agent, klass) if catalog is not None else None
        if author is None:
            errors.append(f"{ident}: nieznany wykonawca „{row.agent}”")
        elif klass == "lokalny" and author.provider != "local":
            errors.append(f"{ident}: klasa lokalny wymaga lokalnego wykonawcy")
        reviewer = (catalog.resolve(row.reviewer, klass, author.model if author is not None else "")
                    if catalog is not None and row.reviewer else None)
        if reviewer is None and author is not None and not row.reviewer:
            preferred = {"Haiku": "Sonnet", "Sonnet": "Opus", "Opus": "Sonnet",
                         "Codex mocny": "Codex Astra"}.get(author.name, "")
            options = ([catalog.get(preferred)] if preferred else []) + list(catalog.executors)
            reviewer = next((e for e in options if e is not None and e.provider == author.provider and
                             e.model.lower() != author.model.lower()), None)
        if reviewer is None:
            errors.append(f"{ident}: nieznany albo niedostępny recenzent „{row.reviewer}”")
        elif author is not None and (author.model.lower() == reviewer.model.lower() or
                                     (author.provider == "local") != (reviewer.provider == "local")):
            errors.append(f"{ident}: recenzent musi mieć inny model i zachować pracę lokalną")
        card_name = (work.group(1) if work else ident) + ".md"
        card_path = os.path.realpath(os.path.join(folder, card_name))
        if not _inside(card_path, folder):
            errors.append(f"{ident}: kartka wychodzi poza katalog tickets")
        else:
            try:
                body = artifacts["tickets/" + card_name].decode("utf-8")
                if not CRITERION.search(body):
                    errors.append(f"{ident}: kartka nie ma kryteriów akceptacji")
                cards[ident] = body
            except (KeyError, OSError, UnicodeError, ValueError):
                errors.append(f"{ident}: brak czytelnej kartki tickets/{card_name}")
        rows.append({"workId": ident, "title": row.title, "sourceState": row.state if imported else "",
                     "executor": author.name if author is not None else row.agent,
                     "reviewer": reviewer.name if reviewer else row.reviewer, "class": klass,
                     "note": row.note, "blockers": list(row.blockers),
                     **({"project": slug} if len(projects) > 1 else {}),
                     **({"fromPlan": other["path"]} if from_other else {})})
    published = {os.path.basename(path)} | {name for name in artifacts if name.startswith('followups/')}
    published.update('tickets/' + row['workId'].lstrip('#') + '.md' for row in rows)
    try:
        planartifacts.check_links(artifacts, published=published)
    except ValueError as exc:
        errors.append(str(exc))
    for row in rows:
        for blocker in row["blockers"]:
            if blocker not in seen:
                errors.append(f"{row['workId']}: bloker {blocker} jest spoza planu")
    waves = _waves(rows)
    if rows and not waves:
        stuck = _cycle(rows)
        through = sorted({r.get("project") or "" for r in rows if r["workId"] in stuck} - {""})
        errors.append(f"{', '.join(stuck)}: plan zawiera cykl blokerów"
                      + (f" przez projekty {', '.join(through)}" if len(through) > 1 else ""))
    return Proposal(plan, rows, cards, waves, errors, artifacts)


def _cycle(rows: list[dict]) -> list[str]:
    """Wiersze, które tworzą cykl (albo leżą między cyklami): po odcięciu rozwiązywalnych i tych, na które nic
    w reszcie nie czeka."""
    pending = {r["workId"]: set(r["blockers"]) for r in rows}
    changed = True
    while changed:
        changed = False
        for ident in list(pending):
            free = not (pending[ident] & set(pending))
            needed = any(ident in blockers for blockers in pending.values())
            if free or not needed:
                del pending[ident]
                changed = True
    return [r["workId"] for r in rows if r["workId"] in pending]


def _waves(rows: list[dict]) -> list[list[str]]:
    pending = {r["workId"]: set(r["blockers"]) for r in rows}
    done, waves = set(), []
    while pending:
        wave = [r["workId"] for r in rows if r["workId"] in pending and pending[r["workId"]] <= done]
        if not wave:
            return []
        waves.append(wave)
        done.update(wave)
        for ident in wave:
            del pending[ident]
    return waves


def _inside(path: str, root: str) -> bool:
    if not root:
        return False
    root = os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)
