"""Sprawdzenie propozycji planu karty przed nadaniem numerów ticketom.

Plik planu leży obok katalogu ``tickets``; kartka ``#2`` to ``tickets/2.md``.
Wynik zawiera wyłącznie dane sprawdzone i teksty kartek, gotowe do migawki w dzienniku karty.

Ticket z innego planu (K-33, decyzja D2): wiersz może mieć zamiast numeru roboczego ID istniejącego ticketu,
który w innym planie tego repozytorium ma stan ``todo`` i ten sam tytuł (``other_plans``). Numer zostaje,
a wiersz niesie ``fromPlan`` — ścieżkę planu, z którego go wzięto. Inny tytuł byłby twardą kolizją numerów.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ..box import profile as box_profile
from ..box import templates
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
    box: dict | None = None   # PT-190: rozszerzenie profilu środowiska z `box.yaml` obok plan.md (None = brak pliku)


def _same_title(a: str, b: str) -> bool:
    return " ".join((a or "").casefold().split()) == " ".join((b or "").casefold().split())


def read(path: str, card_id: str, catalog, *, allowed_roots=(), tracker="local", existing_ids=(), imported=False,
         other_plans=None) -> Proposal:
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
    folder = os.path.join(os.path.dirname(real), "tickets")
    for row in plan.rows:
        ident = row.ticket
        work = WORK_ID.fullmatch(ident)
        already_numbered = imported and ident in existing_ids and re.fullmatch(rf"{re.escape(plan.prefix)}-[1-9][0-9]*", ident)
        other = None if work or ident in existing_ids else (other_plans or {}).get(ident)
        from_other = bool(other) and bool(re.fullmatch(rf"{re.escape(plan.prefix)}-[1-9][0-9]*", ident))
        if from_other and not _same_title(other["title"], row.title):
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
        rows.append({"workId": ident, "title": row.title, "sourceState": row.state if imported else "",
                     "executor": row.agent,
                     "reviewer": reviewer.name if reviewer else row.reviewer, "class": klass,
                     "note": row.note, "blockers": list(row.blockers),
                     **({"fromPlan": other["path"]} if from_other else {})})
    for row in rows:
        for blocker in row["blockers"]:
            if blocker not in seen:
                errors.append(f"{row['workId']}: bloker {blocker} jest spoza planu")
    waves = _waves(rows)
    if rows and not waves:
        errors.append("plan zawiera cykl blokerów")
    box = _read_box(real, errors)
    return Proposal(plan, rows, cards, waves, errors, box)


def _read_box(plan_path: str, errors: list[str]) -> dict | None:
    """`box.yaml` obok `plan.md` (PT-190): walidacja schematem profilu karty z `box/profile.py`, jak tabela ticketów.

    Brak pliku nie jest błędem. Błędy formatu: nieznany klucz, zły typ, a także pozycja czerwona, której
    `accepted_red` nie wymienia jawnie. Pliku nie zapisujemy ani nie kopiujemy — zatwierdza go użytkownik.
    """
    if os.path.basename(plan_path) != "plan.md":
        return None   # plany w repozytorium dzielą katalog; profil dotyczy tylko wyniku kroku planowania
    folder = os.path.dirname(plan_path)
    path = os.path.join(folder, "box.yaml")
    if not os.path.lexists(path):
        return None
    real = os.path.realpath(path)
    if not _inside(real, folder) or not os.path.isfile(real):
        errors.append("box.yaml: musi być zwykłym plikiem obok plan.md")
        return None
    try:
        with open(real, encoding="utf-8") as fh:
            text = fh.read(box_profile._MAX_FILE + 1)
    except (OSError, UnicodeError) as exc:
        errors.append(f"box.yaml: nie można przeczytać: {exc}")
        return None
    if len(text) > box_profile._MAX_FILE:
        errors.append(f"box.yaml: plik większy niż {box_profile._MAX_FILE} bajtów")
        return None
    try:
        card = box_profile.parse(text, "card")
        merged = box_profile.merge({"tide-box": box_profile.SCHEMA_VERSION}, card, templates.known_domains())
    except box_profile.ProfileError as exc:
        errors.extend(f"box.yaml: {message}" for message in exc.errors)
        return None
    for item in merged.dropped_red:
        errors.append(f"box.yaml: pozycja czerwona {item!r} jest włączona bez jawnego zaznaczenia "
                      "(wymaga accepted_red; zostaw ją wyłączoną i zostaw decyzję użytkownikowi)")
    return card


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
