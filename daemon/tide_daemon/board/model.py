"""Model tablicy: kolumny, karty, projekty i tagi odczytane z plików, z walidacją.

Odczyt jest łagodny — zła wartość w pliku daje wpis w `errors` i wartość domyślną, a reszta
tablicy działa. Zapis (store.py) jest surowy — niepoprawne dane z D-Bus kończą się błędem
`BoardError` z komunikatem po polsku, zanim cokolwiek trafi na dysk.
"""
import os
import re
import unicodedata
from dataclasses import dataclass, field

from .frontmatter import Document

# Kolumny w kolejności tablicy. Ręcznie ustawiamy tylko `manual`; pozostałe wynikną ze stanu
# koordynatora karty (PT-5) — w pliku karty ich nie zapisujemy (pole `derived` w migawce).
COLUMNS = (
    {"id": "backlog", "label": "Backlog", "manual": True},
    {"id": "todo", "label": "Do zrobienia", "manual": True},
    {"id": "progress", "label": "W toku", "manual": False},
    {"id": "waiting", "label": "Czeka na mnie", "manual": False},
    {"id": "review", "label": "Do sprawdzenia", "manual": False},
    {"id": "done", "label": "Gotowe", "manual": True},
    {"id": "archived", "label": "Archiwum", "manual": True, "hidden": True},
)
COLUMN_IDS = tuple(c["id"] for c in COLUMNS)
MANUAL_COLUMNS = tuple(c["id"] for c in COLUMNS if c["manual"])
DEFAULT_COLUMN = "backlog"

AI_DOCS = ("repo", "tide")
TOOLS = ("claude", "codex")
MODES = ("full", "light", "spec")
DEFAULT_TOOL = "claude"
DEFAULT_MODE = "full"  # pełny przebieg (grilling, spec, plan, TDD, review), chyba że karta wybierze inaczej

# Kolory tagów to nazwy pasteli z palety (theme/palette: „pastel”); kolejność = kolejność wyboru
# koloru dla tagu bez pliku. Paleta może dodać własne nazwy — wtedy obowiązuje jej lista.
PASTELS = ("teal", "lavender", "peach", "butter", "rose", "mint", "sky")
SEED_TAGS = (("Firma", "sky", "Projekty dla klienta (komercyjne)."),
             ("własne", "lavender", "Projekty własne."))

CARD_PREFIX = "K-"
CARD_FILE_RE = re.compile(r"^K-([1-9][0-9]{0,8})\.md$")
CARD_ID_RE = re.compile(r"^K-([1-9][0-9]{0,8})$", re.IGNORECASE)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# Kolejność kluczy w nowych plikach (istniejące pliki zachowują swoją).
CARD_KEYS = ("id", "title", "project", "tags", "column", "order", "note", "created", "updated",
             "youtrack", "spec", "session", "branch")
RESERVED_CARD_KEYS = ("youtrack", "spec", "session", "branch")  # wypełnią PT-4, PT-5, PT-7
# Ostatnie uruchomienie agenta z karty (PT-4): tryb, narzędzie, katalog worktree i chwila startu.
# W nowych plikach tylko wtedy, gdy są ustawione (karta bez uruchomienia ich nie ma).
LAUNCH_KEYS = ("mode", "tool", "worktree", "launched")
PROJECT_KEYS = ("name", "repo", "tags", "ai-docs", "tool", "mode")
TAG_KEYS = ("name", "color")

TITLE_MAX = 200
NOTE_MAX = 300
TAG_MAX = 40
NAME_MAX = 80
FIELD_MAX = 200          # youtrack, session, branch
PATH_MAX = 1024          # spec, worktree (ścieżki)
DESCRIPTION_MAX = 64 * 1024
FILE_MAX = 256 * 1024    # większych plików nie czytamy (błąd w migawce)

_FOLD = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


class BoardError(ValueError):
    """Błąd zapisu z komunikatem dla człowieka (po polsku) i kodem dla programu — trafia do JSON
    `{ok: false, error: <kod>, message: <tekst>}` (jak /Tasks).

    Kody: bad-request (złe dane), not-found (nie ma karty, projektu, tagu), exists (już jest),
    broken-file (pliku nie da się przeczytać — popraw ręcznie), git-busy (w repozytorium tablicy
    trwa revert, merge, cherry-pick albo rebase), not-a-board (cudzy katalog)."""

    def __init__(self, message: str, code: str = "bad-request"):
        super().__init__(message)
        self.code = code


@dataclass
class Tag:
    name: str
    color: str
    description: str = ""
    slug: str = ""
    defined: bool = True      # False: tag użyty w karcie albo projekcie, ale bez pliku w tags/
    path: str = ""
    doc: Document | None = None


@dataclass
class Project:
    slug: str
    name: str
    repo: str = ""
    tags: list = field(default_factory=list)
    ai_docs: str = "tide"
    ai_docs_set: bool = True  # False: brak klucza w pliku — wartość domyślna z repozytorium
    tool: str = DEFAULT_TOOL
    mode: str = DEFAULT_MODE
    description: str = ""
    path: str = ""
    repo_missing: bool = False
    doc: Document | None = None


@dataclass
class Card:
    num: int
    title: str
    project: str = ""
    tags: list = field(default_factory=list)
    column: str = DEFAULT_COLUMN
    order: float = 0
    note: str = ""
    created: str = ""
    updated: str = ""
    youtrack: str = ""
    spec: str = ""
    session: str = ""
    branch: str = ""
    mode: str = ""
    tool: str = ""
    worktree: str = ""
    launched: str = ""
    description: str = ""
    path: str = ""
    doc: Document | None = None

    @property
    def id(self) -> str:
        return f"{CARD_PREFIX}{self.num}"


# --- nazwy ------------------------------------------------------------------------------------

def slugify(text: str, fallback: str = "projekt") -> str:
    """Nazwa pliku z nazwy: małe litery ASCII, cyfry, kropki, podkreślenia i myślniki."""
    folded = unicodedata.normalize("NFKD", (text or "").translate(_FOLD))
    folded = folded.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", folded).strip("-._")[:64].strip("-._")
    return slug or fallback


def card_num(card_id: str) -> int:
    match = CARD_ID_RE.match((card_id or "").strip())
    if not match:
        raise BoardError(f"niepoprawne ID karty „{card_id}” (oczekiwano K-<numer>)")
    return int(match.group(1))


def split_tags(value) -> list[str]:
    """Lista tagów z napisu „a, b” (także „[a, b]” w stylu YAML) albo listy; bez pustych i powtórzeń
    (wielkość liter bez znaczenia), bez cudzysłowów wokół nazw."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        items = text.split(",")
    else:
        items = list(value or [])
    out, seen = [], set()
    for item in items:
        name = " ".join(str(item).split())
        if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'":
            name = name[1:-1].strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return out


def merge_tags(*lists) -> list[str]:
    return split_tags([name for names in lists for name in names])


def default_ai_docs(repo: str) -> str:
    """`repo`, gdy repozytorium ma już docs/agents (plany agentów w repozytorium), inaczej `tide`."""
    return "repo" if repo and os.path.isdir(os.path.join(repo, "docs", "agents")) else "tide"


def fallback_color(name: str, colors) -> str:
    """Stały kolor dla tagu bez pliku: z nazwy, żeby nie skakał między odczytami."""
    names = list(colors) or list(PASTELS)
    return names[sum(name.casefold().encode("utf-8")) % len(names)]


def order_value(value) -> float:
    number = float(value)
    return int(number) if number == int(number) else number


# --- odczyt plików ----------------------------------------------------------------------------

def parse_card(doc: Document, path: str, num: int, errors: list) -> Card:
    name = os.path.basename(path)
    declared = doc.get("id").strip()
    if declared and declared.upper() != f"{CARD_PREFIX}{num}":
        errors.append(_err(path, f"{name}: id „{declared}” nie zgadza się z nazwą pliku — obowiązuje nazwa pliku"))
    card = Card(num=num, title=" ".join(doc.get("title").split()), path=path, doc=doc,
                description=doc.body.lstrip("\n"))
    if not card.title:
        errors.append(_err(path, f"{name}: brak tytułu"))
        card.title = card.id
    card.project = doc.get("project").strip()
    card.tags = split_tags(doc.get("tags"))
    column = doc.get("column").strip().lower() or DEFAULT_COLUMN
    if column not in MANUAL_COLUMNS:
        errors.append(_err(path, f"{name}: nieznana kolumna „{column}” ({', '.join(MANUAL_COLUMNS)}) — karta w Backlogu"))
        column = DEFAULT_COLUMN
    card.column = column
    raw_order = doc.get("order").strip()
    try:
        card.order = order_value(raw_order) if raw_order else 0
    except (ValueError, OverflowError):
        errors.append(_err(path, f"{name}: niepoprawna kolejność „{raw_order}”"))
        card.order = 0
    card.note = doc.get("note").strip()
    card.created = doc.get("created").strip()
    card.updated = doc.get("updated").strip()
    for key in RESERVED_CARD_KEYS:
        setattr(card, key, doc.get(key).strip())
    card.mode = _choice(doc.get("mode"), MODES, "", f"{name}: nieznany tryb uruchomienia", path, errors)
    card.tool = _choice(doc.get("tool"), TOOLS, "", f"{name}: nieznane narzędzie uruchomienia", path, errors)
    card.worktree = doc.get("worktree").strip()
    card.launched = doc.get("launched").strip()
    return card


def parse_project(doc: Document, path: str, slug: str, errors: list) -> Project:
    name = os.path.basename(path)
    project = Project(slug=slug, name=" ".join(doc.get("name").split()) or slug, path=path, doc=doc,
                      description=doc.body.strip("\n"))
    repo = doc.get("repo").strip()
    if repo and not os.path.isabs(repo):
        errors.append(_err(path, f"{name}: repo musi być ścieżką bezwzględną („{repo}”)"))
        repo = ""
    project.repo = os.path.normpath(repo) if repo else ""
    project.repo_missing = bool(project.repo) and not os.path.isdir(project.repo)
    project.tags = split_tags(doc.get("tags"))
    ai_docs = doc.get("ai-docs").strip().lower()
    if not ai_docs:
        project.ai_docs, project.ai_docs_set = default_ai_docs(project.repo), False
    elif ai_docs not in AI_DOCS:
        errors.append(_err(path, f"{name}: nieznane ai-docs „{ai_docs}” (repo, tide)"))
        project.ai_docs, project.ai_docs_set = default_ai_docs(project.repo), False
    else:
        project.ai_docs = ai_docs
    if project.ai_docs == "repo" and not project.repo:
        errors.append(_err(path, f"{name}: ai-docs „repo” bez repozytorium — dokumenty w tablicy"))
        project.ai_docs = "tide"
    project.tool = _choice(doc.get("tool"), TOOLS, DEFAULT_TOOL, f"{name}: nieznane narzędzie", path, errors)
    project.mode = _choice(doc.get("mode"), MODES, DEFAULT_MODE, f"{name}: nieznany tryb", path, errors)
    return project


def parse_tag(doc: Document, path: str, slug: str, colors, errors: list) -> Tag:
    name = " ".join(doc.get("name").split()) or slug
    color = doc.get("color").strip().lower()
    if color not in colors:
        if color:
            errors.append(_err(path, f"{os.path.basename(path)}: nieznany kolor „{color}” ({', '.join(colors)})"))
        color = fallback_color(name, colors)
    return Tag(name=name, color=color, description=doc.body.strip("\n"), slug=slug, path=path, doc=doc)


def _choice(raw: str, allowed, default: str, label: str, path: str, errors: list) -> str:
    value = raw.strip().lower()
    if not value:
        return default
    if value not in allowed:
        errors.append(_err(path, f"{label} „{value}” ({', '.join(allowed)})"))
        return default
    return value


def _err(path: str, message: str) -> dict:
    return {"path": path, "message": message}
