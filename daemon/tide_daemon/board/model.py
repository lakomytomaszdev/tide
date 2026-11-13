"""Model tablicy: kolumny, karty, projekty i tagi odczytane z plików, z walidacją.

Odczyt jest łagodny — zła wartość w pliku daje wpis w `errors` i wartość domyślną, a reszta
tablicy działa. Zapis (store.py) jest surowy — niepoprawne dane z D-Bus kończą się błędem
`BoardError` z komunikatem po polsku, zanim cokolwiek trafi na dysk.
"""
import os
import json
import re
import unicodedata
from dataclasses import dataclass, field

from .frontmatter import Document
from . import commands

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
# Wejście to sposób rozpoczęcia zlecenia (CONTEXT.md), tryb — głębokość Planowania (Pełny, Lekki). Kolejne wejścia
# (Mam plan, Od razu) dochodzą do ENTRIES i FLOW_KEYS (szablon `flows/<klucz>.md`) bez zmiany formatu plików.
ENTRIES = ("planning", "spec")
DEFAULT_ENTRY = "planning"
MODES = ("full", "light")
LEGACY_SPEC_MODE = "spec"   # dawniej „Tylko specyfikacja” było trybem: `mode: spec` czytamy jako wejście spec
DEFAULT_TOOL = "claude"
DEFAULT_MODE = "full"  # pełny przebieg (grilling, spec, plan, TDD, review), chyba że karta wybierze inaczej
# Sprawdzenie po skończonej pracy agenta: w starym RenderLaunch `auto` mogło oznaczać Gotowe po review.
# W przepływie karty oba tryby kończą się w „Do sprawdzenia”; `manual` dodatkowo wymaga listy testów ręcznych.
CHECKS = ("auto", "manual")
DEFAULT_CHECK = "auto"

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
CARD_KEYS = ("id", "title", "project", "tags", "column", "order", "note", "created", "updated", "moved",
             "youtrack", "spec", "session", "branch", "check")
RESERVED_CARD_KEYS = ("youtrack", "spec", "session", "branch")  # wypełnią PT-4, PT-5, PT-7
# Ostatnie uruchomienie agenta z karty (PT-4): tryb, narzędzie, katalog worktree i chwila startu.
# W nowych plikach tylko wtedy, gdy są ustawione (karta bez uruchomienia ich nie ma).
LAUNCH_KEYS = ("entry", "mode", "tool", "worktree", "launched")
# Dodatkowe projekty karty (PT-28): slugi po przecinku, główny zostaje w `project`. W nowych plikach
# tylko wtedy, gdy są ustawione. Najwyżej PROJECTS_MAX (agent dostaje ich repozytoria przez --add-dir).
PROJECTS_KEY = "projects"
# Kolumna wyświetlana karty (z pracy agentów albo ręczna) w chwili ostatniego MoveCard (PT-29): „do poprawy”
# (karta zostaje w swojej kolumnie po skończonej pracy) tylko po przeniesieniu z review albo done.
MOVED_FROM_KEY = "moved-from"
# Ostatnia prawdziwa zmiana kolumny przez MoveCard (PT-7): kolumna docelowa inna niż wyświetlana. Sama zmiana
# kolejności w kolumnie jej nie stempluje — synchronizacja z YouTrackiem liczy tylko takie przeniesienia.
COLUMN_MOVED_KEY = "column-moved"
PROJECTS_MAX = 5
PROJECT_KEYS = ("name", "repo", "tags", "ai-docs", "tool", "mode", "entry")
# Synchronizacja z YouTrackiem (PT-7): klucz projektu YouTracka (LOG, LEGAL) i przełącznik. W pliku tylko,
# gdy ustawione; projekt bez klucza nie synchronizuje niczego, z kluczem — domyślnie tak (`youtrack-sync: off`).
PROJECT_YOUTRACK_KEY = "youtrack"
PROJECT_SYNC_KEY = "youtrack-sync"
# Polityka niezależności review projektu (K-88/PT-330, decyzja 96 z 10.11): `free` (domyślne, także bez klucza),
# `other-model`, `other-family`; lista klas zawęża `other-family`. W pliku tylko, gdy ustawione. Wartość
# trzymamy surowo — nieznaną usługa traktuje jak `free` z ostrzeżeniem w logu (`cardflow.reviewgate`).
REVIEW_POLICY_KEY = "review-independence"
REVIEW_CLASSES_KEY = "review-independence-classes"
REVIEW_POLICIES = ("free", "other-model", "other-family")
REVIEW_CLASSES = ("lekki", "standard", "mocny", "lokalny")
YOUTRACK_PROJECT_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,19}$")
ON_WORDS = ("on", "true", "yes", "tak", "1")
OFF_WORDS = ("off", "false", "no", "nie", "0")
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
    trwa revert, merge, cherry-pick albo rebase), not-a-board (cudzy katalog), forbidden (nadawca bez prawa
    do operacji — zatwierdzenie kontraktu testów z kroku agenta, PT-286)."""

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
    entry: str = DEFAULT_ENTRY   # domyślne wejście projektu
    description: str = ""
    path: str = ""
    repo_missing: bool = False
    youtrack: str = ""        # klucz projektu YouTracka (PT-7), pusty — bez synchronizacji
    youtrack_sync: bool = True
    review_independence: str = ""          # surowa wartość z pliku; pusta — `free`
    review_independence_classes: list = field(default_factory=list)
    assignment_rules: str = ""
    allowed_commands: list = field(default_factory=list)
    denied_commands: list = field(default_factory=list)
    read_dirs: list = field(default_factory=list)
    read_dir_bindings: list = field(default_factory=list)
    read_dirs_error: str = ""
    box_text: str | None = None   # surowy `projects/<slug>/box.yaml` (None — brak pliku); klucz `tests` czyta project_tests
    tests: dict = field(default_factory=lambda: {"changed": "", "full": ""})
    tests_error: str = ""
    tests_approved: bool = False   # odcisk kontraktu w stanie usługi równy plikowi (contract_approval, PT-285)
    box_unreadable: bool = False   # `box.yaml` istnieje, ale nie da się go odczytać — zapis kontraktu odrzucony
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
    moved: str = ""           # ostatnia zmiana kolumny (MoveCard, założenie karty); bez klucza — created
    move_id: str = ""         # identyfikator ostatniego MoveCard; pozwala odtworzyć ruch po restarcie
    moved_from: str = ""      # kolumna wyświetlana przed ostatnim MoveCard (PT-29); pusta — nieznana
    column_moved: str = ""    # ostatnia prawdziwa zmiana kolumny (PT-7); pusta — nigdy (albo sprzed PT-7)
    youtrack: str = ""
    spec: str = ""
    source_issue: str = ""
    source_issue_version: int = 0
    source_issue_hash: str = ""
    source_issue_report: str = ""
    source_issue_manifest: str = ""
    plan: str = ""          # wskazany plan przekazania; zapis po stronie karty
    session: str = ""
    branch: str = ""
    base_ref: str = ""
    base_branch: str = ""
    base_commit: str = ""
    start_ref: str = ""       # podpowiedź „start od…” (pełny ref); nie jest bazą ani zgodą, `start-ref` w pliku
    spec_revision_ref: str = ""
    spec_revision_commit: str = ""
    spec_revision_repo: str = ""
    check: str = DEFAULT_CHECK
    mode: str = ""            # tryb Planowania (full, light); przy wejściu spec pusty
    entry: str = ""           # wejście ostatniego uruchomienia; pusty — karta nie była uruchamiana
    tool: str = ""
    worktree: str = ""
    launched: str = ""
    projects: list = field(default_factory=list)   # dodatkowe projekty (slugi), bez głównego
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
    card.source_issue = doc.get("source-issue").strip()
    version = doc.get("source-issue-version").strip()
    card.source_issue_version = int(version) if version.isdecimal() else 0
    for key in ("source-issue-hash", "source-issue-report", "source-issue-manifest"):
        setattr(card, key.replace("-", "_"), doc.get(key).strip())
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
    card.moved = doc.get("moved").strip()
    card.move_id = doc.get("move-id").strip()
    moved_from = doc.get(MOVED_FROM_KEY).strip().lower()
    card.moved_from = moved_from if moved_from in COLUMN_IDS else ""
    card.column_moved = doc.get(COLUMN_MOVED_KEY).strip()
    for key in RESERVED_CARD_KEYS:
        setattr(card, key, doc.get(key).strip())
    for key in ("base-ref", "base-branch", "base-commit", "start-ref", "spec-revision-ref", "spec-revision-commit", "spec-revision-repo"):
        setattr(card, key.replace("-", "_"), doc.get(key).strip())
    card.plan = doc.get("plan").strip()
    card.check = _choice(doc.get("check"), CHECKS, DEFAULT_CHECK, f"{name}: nieznane sprawdzenie", path, errors)
    card.entry, card.mode = _entry_mode(doc, "", name, path, errors, mode_label="nieznany tryb uruchomienia")
    card.tool = _choice(doc.get("tool"), TOOLS, "", f"{name}: nieznane narzędzie uruchomienia", path, errors)
    card.worktree = doc.get("worktree").strip()
    card.launched = doc.get("launched").strip()
    for slug in split_tags(doc.get(PROJECTS_KEY)):
        if not SLUG_RE.match(slug):
            errors.append(_err(path, f"{name}: niepoprawny dodatkowy projekt „{slug}” — pomijam"))
        elif slug != card.project and slug not in card.projects:
            card.projects.append(slug)
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
    project.entry, project.mode = _entry_mode(doc, DEFAULT_ENTRY, name, path, errors, DEFAULT_MODE)
    key = doc.get(PROJECT_YOUTRACK_KEY).strip().upper()
    if key and not YOUTRACK_PROJECT_RE.match(key):
        errors.append(_err(path, f"{name}: niepoprawny klucz projektu YouTracka „{key}” (np. LOG) — bez synchronizacji"))
        key = ""
    project.youtrack = key
    sync = doc.get(PROJECT_SYNC_KEY).strip().lower()
    if sync and sync not in ON_WORDS + OFF_WORDS:
        errors.append(_err(path, f"{name}: nieznana wartość youtrack-sync „{sync}” (on, off) — włączona"))
    project.youtrack_sync = sync not in OFF_WORDS
    project.review_independence = " ".join(doc.get(REVIEW_POLICY_KEY).lower().split())
    project.review_independence_classes = [c for c in (part.strip().lower() for part in
                                                       doc.get(REVIEW_CLASSES_KEY).split(",")) if c]
    default_allowed, default_denied = commands.defaults(slug, project.repo)
    project.allowed_commands = commands.read(doc.get(commands.ALLOWED_KEY), default_allowed,
                                             commands.ALLOWED_KEY, errors, path)
    project.denied_commands = commands.read(doc.get(commands.DENIED_KEY), default_denied,
                                            commands.DENIED_KEY, errors, path)
    from ..tasks import readaccess
    try:
        project.read_dirs = readaccess.paths(json.loads(doc.get(readaccess.KEY) or "[]"))
        project.read_dir_bindings = json.loads(doc.get(readaccess.BINDINGS_KEY) or "[]")
        if not isinstance(project.read_dir_bindings, list):
            raise ValueError("readDirs: uszkodzone powiązania dowiązań")
    except (ValueError, TypeError) as exc:
        project.read_dirs_error = f"readDirs: niepoprawne ustawienie ({exc})"
        errors.append(_err(path, project.read_dirs_error))
    try:
        value = json.loads(doc.get("assignment-rules") or '""')
        project.assignment_rules = value if isinstance(value, str) else ""
    except ValueError:
        errors.append(_err(path, "niepoprawne zasady przydziału"))
    return project


def parse_tag(doc: Document, path: str, slug: str, colors, errors: list) -> Tag:
    name = " ".join(doc.get("name").split()) or slug
    color = doc.get("color").strip().lower()
    if color not in colors:
        if color:
            errors.append(_err(path, f"{os.path.basename(path)}: nieznany kolor „{color}” ({', '.join(colors)})"))
        color = fallback_color(name, colors)
    return Tag(name=name, color=color, description=doc.body.strip("\n"), slug=slug, path=path, doc=doc)


def _entry_mode(doc: Document, default_entry: str, name: str, path: str, errors: list,
                default_mode: str = "", mode_label: str = "nieznany tryb") -> tuple[str, str]:
    """(wejście, tryb) z kluczy `entry` i `mode`. Dawne `mode: spec` to wejście Tylko specyfikacja (czytamy,
    nie przepisujemy pliku); jawne `entry` ma pierwszeństwo. Karta z samym trybem to wejście Planowanie."""
    entry = _choice(doc.get("entry"), ENTRIES, "", f"{name}: nieznane wejście", path, errors)
    raw_mode = doc.get("mode").strip().lower()
    if raw_mode == LEGACY_SPEC_MODE:
        return entry or "spec", default_mode
    mode = _choice(raw_mode, MODES, default_mode, f"{name}: {mode_label}", path, errors)
    return entry or default_entry or (DEFAULT_ENTRY if mode else ""), mode


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
