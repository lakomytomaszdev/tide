"""Szablony poleceń trybów uruchomienia (pełny, lekki, tylko spec) i ich wypełnianie.

Szablon to `flows/<tryb>.md` w tablicy, a dla jednego projektu można go nadpisać plikiem
`flows/<projekt>/<tryb>.md`. Frontmatter szablonu (opcjonalny) nie trafia do polecenia.
Zastępujemy wyłącznie znane pola w klamrach (PLACEHOLDERS) — inne klamry, np. przykład JSON
w szablonie, zostają bez zmian.

Treść poniżej to tylko zaczyn: prawdziwe polecenia trybów dopisze PT-4. Gdy tablicy jeszcze
nie ma, wypełniamy te same szablony z kodu.
"""
import re

from .frontmatter import Document, FrontmatterError
from .model import MODES

PLACEHOLDERS = ("card_id", "title", "description", "project", "repo", "docs_dir", "branch")
_FIELD = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")

_COMMON = """Karta {card_id}: {title}
Projekt: {project}
Repozytorium: {repo}
Gałąź: {branch}
Plany i specyfikacje: {docs_dir}

Opis karty:

{description}
"""

SEED_FLOWS = {
    "full": """---
tide-flow: 1
mode: full
---
""" + _COMMON + """
Tryb pełny: dopytaj o wymagania, napisz specyfikację, rozpisz tickety w planie, zaimplementuj je
przez TDD i zleć review. Plan zakładasz z kluczem `card: {card_id}`.

(Szablon tymczasowy — pełną treść dopisze PT-4.)
""",
    "light": """---
tide-flow: 1
mode: light
---
""" + _COMMON + """
Tryb lekki: krótka specyfikacja i jeden–kilka ticketów w planie z kluczem `card: {card_id}`,
potem praca.

(Szablon tymczasowy — pełną treść dopisze PT-4.)
""",
    "spec": """---
tide-flow: 1
mode: spec
---
""" + _COMMON + """
Tryb „tylko spec”: dopytaj i napisz specyfikację, bez zmian w kodzie. Specyfikację zapisz
w {docs_dir}.

(Szablon tymczasowy — pełną treść dopisze PT-4.)
""",
}


def body_of(text: str) -> str:
    """Treść szablonu bez frontmattera (plik bez frontmattera to sama treść)."""
    try:
        return Document.parse(text).body.lstrip("\n")
    except FrontmatterError:
        return text


def render(template: str, values: dict) -> str:
    """Wypełnia pola z PLACEHOLDERS; brakująca wartość to pusty napis."""
    return _FIELD.sub(lambda m: str(values.get(m.group(1)) or ""), body_of(template))


assert set(SEED_FLOWS) == set(MODES)
