"""Sprawdzenie propozycji planu karty przed nadaniem numerów ticketom.

Plik planu leży obok katalogu ``tickets``; kartka ``#2`` to ``tickets/2.md``.
Wynik zawiera wyłącznie dane sprawdzone i teksty kartek, gotowe do migawki w dzienniku karty.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ..coordination import plan as plan_mod
from ..models.executors import CLASSES

WORK_ID = re.compile(r"^#([1-9][0-9]{0,5})$")
CRITERION = re.compile(r"(?m)^\s*-\s*\[\s*\]\s*\S")


@dataclass
class Proposal:
    plan: object | None
    rows: list[dict]
    cards: dict[str, str]
    waves: list[list[str]]
    errors: list[str]


def read(path: str, card_id: str, catalog, *, allowed_roots=(), existing_ids=()) -> Proposal:
    """Czyta plan i kartki wyłącznie z dozwolonych katalogów; nie rezerwuje numerów."""
    errors: list[str] = []
    real = os.path.realpath(path)
    if not real.lower().endswith(".md") or not any(_inside(real, root) for root in allowed_roots):
        return Proposal(None, [], {}, [], ["plik planu musi być Markdownem w repozytorium lub katalogu kroku"])
    try:
        if os.path.getsize(real) > plan_mod.MAX_BYTES:
            raise ValueError("plan przekracza 512 KB")
        with open(real, encoding="utf-8") as fh:
            content = fh.read()
    except (OSError, UnicodeError, ValueError) as exc:
        return Proposal(None, [], {}, [], [f"nie można przeczytać planu: {exc}"])
    plan = plan_mod.parse(content, real)
    if plan is None:
        return Proposal(None, [], {}, [], ["plik nie jest planem Tide (brak tide-plan: 1)"])
    errors.extend(plan.errors)
    if plan.card != card_id:
        errors.append(f"plan musi wskazywać kartę {card_id} przez klucz card")
    if plan.tables != 1:
        errors.append(f"plan musi mieć jedną tabelę ticketów (ma {plan.tables})")
    if plan.tracker != "local":
        errors.append("ten etap zatwierdza plany z lokalnymi ticketami")
    if not plan.prefix:
        errors.append("plan nie ma ticket-prefix")
    if not plan.rows:
        errors.append("plan nie ma ticketów")
    seen, rows, cards = set(), [], {}
    folder = os.path.join(os.path.dirname(real), "tickets")
    for row in plan.rows:
        ident = row.ticket
        work = WORK_ID.fullmatch(ident)
        if not work and ident not in existing_ids:
            errors.append(f"{ident}: propozycja wymaga numeru roboczego #n; ID powstaje po zatwierdzeniu")
        if ident in seen:
            errors.append(f"{ident}: powtórzony ticket")
        seen.add(ident)
        if not row.title:
            errors.append(f"{ident}: brak tytułu")
        if row.state != "todo":
            errors.append(f"{ident}: propozycja wymaga stanu todo")
        klass = row.work_class or "standard"
        if klass not in CLASSES:
            errors.append(f"{ident}: nieznana klasa „{klass}”")
        author = catalog.get(row.agent) if catalog is not None else None
        if author is None:
            errors.append(f"{ident}: nieznany wykonawca „{row.agent}”")
        elif klass == "lokalny" and author.provider != "local":
            errors.append(f"{ident}: klasa lokalny wymaga lokalnego wykonawcy")
        reviewer = catalog.get(row.reviewer) if catalog is not None and row.reviewer else None
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
                if os.path.getsize(card_path) > plan_mod.MAX_BYTES:
                    raise ValueError("kartka przekracza 512 KB")
                with open(card_path, encoding="utf-8") as fh:
                    body = fh.read()
                if not CRITERION.search(body):
                    errors.append(f"{ident}: kartka nie ma kryteriów akceptacji")
                cards[ident] = body
            except (OSError, UnicodeError, ValueError):
                errors.append(f"{ident}: brak czytelnej kartki tickets/{card_name}")
        rows.append({"workId": ident, "title": row.title, "executor": row.agent,
                     "reviewer": reviewer.name if reviewer else row.reviewer, "class": klass,
                     "note": row.note, "blockers": list(row.blockers)})
    for row in rows:
        for blocker in row["blockers"]:
            if blocker not in seen:
                errors.append(f"{row['workId']}: bloker {blocker} jest spoza planu")
    waves = _waves(rows)
    if rows and not waves:
        errors.append("plan zawiera cykl blokerów")
    return Proposal(plan, rows, cards, waves, errors)


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
