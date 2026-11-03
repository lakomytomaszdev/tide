"""Plan pracy agentów: docs/agents/plans/YYYY-MM-DD-<slug>.md (standard: docs/agent-plan-standard.md).

Standard 1.1: opcjonalny klucz `card: K-12` (karta tablicy Tide, której służy plan), a plany projektów
z dokumentami AI „w Tide” leżą w repozytorium tablicy (`<tablica>/docs/<projekt>/plans/`,
boardplans.py) — plan_folders(roots) daje oba miejsca dla repozytorium.

Standard 1.3: opcjonalna kolumna `Projekt` (slug projektu Tablicy) po `Blokery`; pusta komórka = projekt karty.

Plik ma frontmatter YAML (tylko płaskie pary `klucz: wartość`, więc czytamy go sami, bez
biblioteki YAML) i tabelę Markdown `| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |`
oraz opcjonalnymi kolumnami po `Uwagi`: `Klasa`, `Review` (1.2), `Blokery` (1.2), `Projekt` (1.3) — razem 6–10 kolumn.
Sekcje pod tabelą to wolny tekst — nie czytamy ich.

Błędy nie przerywają odczytu: zły wiersz trafia do `errors`, a reszta planu działa. Plik bez
`tide-plan: 1` to nie plan Tide (`parse` zwraca None).
"""
import os
import re
from dataclasses import dataclass, field

from ..agents.timeutil import parse_ts

PLANS_DIR = os.path.join("docs", "agents", "plans")
NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[A-Za-z0-9._-]+\.md$")
STATES = ("todo", "w-toku", "review", "czeka", "gotowe", "odrzucone")
TRACKERS = ("youtrack", "local", "none")
COORDINATORS = ("claude", "codex")
STATUSES = ("active", "paused", "done")
COLUMNS = ("ticket", "tytuł", "stan", "gałąź", "agent", "uwagi")
OPTIONAL_COLUMNS = ("klasa", "review", "blokery", "projekt")
# Łagodność dla ręcznie pisanych planów: popularne warianty stanów.
STATE_ALIASES = {"w toku": "w-toku", "wtoku": "w-toku", "in-progress": "w-toku", "doing": "w-toku",
                 "done": "gotowe", "todo": "todo", "to-do": "todo", "do zrobienia": "todo",
                 "rejected": "odrzucone", "waiting": "czeka", "blocked": "czeka"}
MAX_BYTES = 512 * 1024
CARD_RE = re.compile(r"^K-([1-9][0-9]{0,8})$", re.IGNORECASE)
CARD_IN_BRANCH = re.compile(r"(?:^|[/_-])k-([1-9][0-9]{0,8})(?=$|[-_/.])", re.IGNORECASE)
# Plik planu w ścieżce zapisu koordynatora (docs/agents/plans/… albo <tablica>/docs/<projekt>/plans/…).
PLAN_FILE = re.compile(r"(?:^|/)plans/(\d{4}-\d{2}-\d{2}-[A-Za-z0-9._-]+\.md)$")
# Pliki w łatce apply_patch (Codex): „*** Add File: …” / „*** Update File: …”. Łatka bywa wklejona w kod
# narzędzia `exec` jako napis JS — wtedy nowe wiersze to `\n`, więc ścieżka kończy się też na `\` i `"`.
PATCH_FILE = re.compile(r"\*\*\* (?:Add|Update) File: ([^\n\\\"]+)")


@dataclass
class PlanRow:
    ticket: str
    title: str
    state: str
    branch: str = ""
    agent: str = ""
    note: str = ""
    work_class: str = ""
    reviewer: str = ""
    blockers: tuple[str, ...] = ()
    project: str = ""           # slug projektu Tablicy (standard 1.3); pusty = projekt karty


@dataclass
class Plan:
    path: str
    title: str = ""
    tracker: str = "none"
    tracker_url: str = ""
    tracker_project: str = ""
    prefix: str = ""
    coordinator: str = ""
    session: str = ""
    status: str = "active"
    updated: str = ""
    updated_ts: float = 0.0
    rows: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    goal: str = ""             # cel planu: akapit pod nagłówkiem „Cel…” albo pierwszy akapit tekstu
    card: str = ""             # karta tablicy Tide (`card: K-12`, standard 1.1) albo pusty
    tables: int = 0             # propozycja karty wymaga dokładnie jednej tabeli ticketów

    def to_json(self) -> dict:
        return {"path": self.path, "title": self.title, "tracker": self.tracker, "trackerUrl": self.tracker_url,
                "trackerProject": self.tracker_project, "prefix": self.prefix, "coordinator": self.coordinator,
                "session": self.session, "status": self.status, "updated": self.updated,
                "updatedTs": round(self.updated_ts, 3), "errors": list(self.errors), "goal": self.goal,
                "card": self.card}


def parse(text: str, path: str = "", mtime: float = 0.0) -> Plan | None:
    """Plan z tekstu pliku albo None, gdy to nie jest plan Tide (brak `tide-plan: 1`)."""
    meta, body = _frontmatter(text)
    if meta is None or str(meta.get("tide-plan", "")).strip() != "1":
        return None
    plan = Plan(path=path)
    plan.title = meta.get("title", "").strip() or os.path.splitext(os.path.basename(path))[0]
    tracker = meta.get("tracker", "none").strip().lower() or "none"
    if tracker not in TRACKERS:
        plan.errors.append(f"nieznany tracker „{tracker}” (youtrack, local, none)")
        tracker = "none"
    plan.tracker = tracker
    plan.tracker_url = meta.get("tracker-url", "").strip().rstrip("/")
    if plan.tracker_url and not re.match(r"^https?://[^\s/]+", plan.tracker_url):
        plan.errors.append("tracker-url musi zaczynać się od http:// albo https://")
        plan.tracker_url = ""
    plan.tracker_project = meta.get("tracker-project", "").strip()
    plan.prefix = meta.get("ticket-prefix", "").strip().upper()
    if plan.prefix and not re.fullmatch(r"[A-Z][A-Z0-9]{0,9}", plan.prefix):
        plan.errors.append(f"niepoprawny ticket-prefix „{plan.prefix}”")
        plan.prefix = ""
    coordinator = meta.get("coordinator", "").strip().lower()
    if coordinator and coordinator not in COORDINATORS:
        plan.errors.append(f"nieznany coordinator „{coordinator}” (claude, codex)")
        coordinator = ""
    plan.coordinator = coordinator
    plan.session = meta.get("session", "").strip()
    card = meta.get("card", "").strip()
    match = CARD_RE.match(card)
    if card and not match:
        plan.errors.append(f"niepoprawny klucz card „{card}” (oczekiwano K-<numer>)")
    plan.card = f"K-{int(match.group(1))}" if match else ""
    status = meta.get("status", "active").strip().lower() or "active"
    if status not in STATUSES:
        plan.errors.append(f"nieznany status „{status}” (active, paused, done)")
        status = "active"
    plan.status = status
    plan.updated = meta.get("updated", "").strip()
    plan.updated_ts = parse_ts(plan.updated) if plan.updated else 0.0
    if plan.updated and not plan.updated_ts:
        plan.errors.append(f"niepoprawny czas updated „{plan.updated}” (ISO 8601)")
    if not plan.updated_ts:
        plan.updated_ts = mtime
    if plan.tracker == "youtrack" and not (plan.tracker_url and plan.tracker_project):
        plan.errors.append("tracker youtrack wymaga tracker-url i tracker-project")
    plan.rows, plan.tables = _table(body, plan.errors)
    plan.goal = goal_of(body)
    return plan


def read(path: str) -> Plan | None:
    try:
        st = os.stat(path)
        if st.st_size > MAX_BYTES:
            return None
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    return parse(text, path, st.st_mtime)


def plan_folders(roots, board_root: str | None = None) -> list[str]:
    """Katalogi planów repozytorium: docs/agents/plans w podanych katalogach (worktree koordynatora,
    główne repozytorium), a po nich katalogi planów projektu w tablicy (`ai-docs: tide`)."""
    from . import boardplans  # tu: boardplans czyta pliki tablicy, a plan.py jest bazą dla wszystkich
    roots = [r for r in roots or () if r]
    return [os.path.join(root, PLANS_DIR) for root in roots] + boardplans.plan_dirs(roots, board_root)


def card_of_branch(branch: str) -> str:
    """ID karty z nazwy gałęzi (`claude/k-12-okno` → K-12) albo pusty napis."""
    match = CARD_IN_BRANCH.search(branch or "")
    return f"K-{int(match.group(1))}" if match else ""


def find_plans(roots) -> list[Plan]:
    """Plany z docs/agents/plans w podanych katalogach (worktree koordynatora, główne repozytorium)
    i z katalogu projektu w tablicy (plan_folders)."""
    out, seen = [], set()
    for folder in plan_folders(roots):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if not NAME_RE.match(name) or name in seen:
                continue  # ten sam plan w worktree i w głównym katalogu — wygrywa pierwszy (świeższy)
            plan = read(os.path.join(folder, name))
            if plan is not None:
                seen.add(name)
                out.append(plan)
    return out


def plan_file_name(path: str) -> str:
    """Nazwa pliku planu (`2026-10-27-local-models.md`) ze ścieżki zapisu albo pusty napis, gdy to nie plan.
    Po samej nazwie, jak find_plans: ten sam plan leży w worktree koordynatora i w głównym katalogu."""
    match = PLAN_FILE.search(str(path or "").strip().replace("\\", "/"))
    return match.group(1) if match else ""


def patch_plan_files(text: str) -> list[str]:
    """Nazwy plików planów dodanych albo zmienionych łatką apply_patch."""
    names = (plan_file_name(m.group(1)) for m in PATCH_FILE.finditer(str(text or "")))
    return [name for name in names if name]


def pick(plans: list[Plan], session_id: str, tool: str, branch: str = "", written=None, taken=()) -> Plan | None:
    """Plan koordynatora: wskazany przez `session`, inaczej ten, który sam zapisał, inaczej najświeższy
    aktywny tego narzędzia, którego nie zapisał inny koordynator.

    Gałąź z ID karty (`claude/k-12-…`, uruchomienie z tablicy): najpierw plan z `card: K-12`
    (też zakończony — to wciąż plan tej karty), a plany innych kart odpadają — w katalogu projektu
    w tablicy leżą plany wielu kart naraz.

    `written`: {nazwa pliku planu: (liczba zapisów, czas ostatniego)} — plany zapisane przez tego koordynatora
    (Coordinator.plan_writes); z kilku własnych wygrywa ten zapisywany najczęściej, potem najświeższy (jedna
    poprawka w cudzym planie nie przesłania własnego). `taken`: nazwy planów, których właścicielem jest inny
    działający koordynator tego repozytorium (scanner.plan_owners) — plan, który ten koordynator sam zapisał,
    nigdy nie jest dla niego cudzy. Bez tego dwa aktywne plany bez `session` w jednym repozytorium dostawały
    ten sam (najświeższy) plan u obu koordynatorów — i raport podpisywał pytanie jednego tytułem planu drugiego."""
    written = written or {}
    taken = set(taken or ()) - set(written)

    def name(p):
        return os.path.basename(p.path)

    for plan in plans:
        if plan.session and session_id and plan.session == session_id:
            return plan
    card = card_of_branch(branch)
    if card:
        mine = [p for p in plans if p.card == card
                and (p.session == session_id or not p.session and name(p) not in taken)]
        if mine:
            return max(mine, key=lambda p: (p.status != "done", p.updated_ts))
    own = [p for p in plans if not p.session and p.status != "done" and name(p) in written
           and name(p) not in taken and not (card and p.card)]
    if own:
        return max(own, key=lambda p: (tuple(written[name(p)]), p.updated_ts))
    candidates = [p for p in plans if not p.session and p.status != "done" and name(p) not in taken
                  and (not p.coordinator or p.coordinator == tool) and not (card and p.card)]
    return max(candidates, key=lambda p: p.updated_ts) if candidates else None


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    """(klucze frontmattera małymi literami, treść pod nim); bez frontmattera — (None, cały tekst).
    Te same łagodne reguły co w planie — czyta je też podgląd dokumentów karty (board/docs.py)."""
    return _frontmatter(text)


# --- wnętrze -----------------------------------------------------------------------------------

def _frontmatter(text: str) -> tuple[dict | None, str]:
    lines = text.lstrip("﻿").split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    meta = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return meta, "\n".join(lines[i + 1:])
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        meta[key.strip().lower()] = _scalar(value)
    return None, text


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells = re.split(r"(?<!\\)\|", line)
    return [c.replace("\\|", "|").strip() for c in cells]


def _table(body: str, errors: list) -> tuple[list[PlanRow], int]:
    lines = body.split("\n")
    rows: list[PlanRow] = []
    tables = 0
    ticket_tables = 0
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        if i + 1 < len(lines) and lines[i + 1].strip().startswith("|") and all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in _cells(lines[i + 1])):
            tables += 1
        header = [c.lower() for c in _cells(line)]
        if header[:3] != list(COLUMNS[:3]):
            continue
        ticket_tables += 1
        if ticket_tables > 1:
            continue
        if header not in [list(COLUMNS + OPTIONAL_COLUMNS[:n]) for n in range(len(OPTIONAL_COLUMNS) + 1)]:
            errors.append("nagłówek tabeli: oczekiwano | Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |"
                          " z opcjonalnymi kolumnami Klasa | Review | Blokery | Projekt na końcu")
        index = {name: header.index(name) for name in COLUMNS + OPTIONAL_COLUMNS if name in header}
        for raw in lines[i + 2:]:
            if not raw.strip().startswith("|"):
                break
            cells = _cells(raw)
            get = lambda name: cells[index[name]] if name in index and index[name] < len(cells) else ""  # noqa: E731
            ticket = _plain(get("ticket"))
            if not ticket:
                continue
            raw_state = _plain(get("stan")).lower()
            state = STATE_ALIASES.get(raw_state, raw_state)
            if state not in STATES:
                errors.append(f"{ticket}: nieznany stan „{raw_state}”")
                state = ""
            rows.append(PlanRow(ticket=ticket, title=_plain(get("tytuł")), state=state,
                                branch=_plain(get("gałąź")), agent=_plain(get("agent")), note=_plain(get("uwagi")),
                                work_class=_plain(get("klasa")).lower(), reviewer=_plain(get("review")),
                                blockers=tuple(part for part in re.split(r"[,;\s]+", _plain(get("blokery"))) if part),
                                project=_plain(get("projekt")).lower()))
    return rows, tables


GOAL_MAX = 600
_GOAL_HEAD = re.compile(r"(?i)^#{1,6}\s*(cel|zakres|po co|goal|kontekst)\b")


def goal_of(body: str) -> str:
    """Cel planu: pierwszy akapit pod nagłówkiem „Cel…” („Zakres”, „Po co”, „Kontekst”), a bez takiego
    nagłówka — pierwszy akapit tekstu przed tabelą albo po niej. Bez tabel, list kodu i nagłówków."""
    paragraphs, current, heading = [], [], ""
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            if current:
                paragraphs.append((heading, " ".join(current)))
                current = []
            heading = stripped
            continue
        if not stripped or stripped.startswith(("|", "```", "---")):
            if current:
                paragraphs.append((heading, " ".join(current)))
                current = []
            continue
        current.append(stripped)
    if current:
        paragraphs.append((heading, " ".join(current)))
    chosen = next((text for head, text in paragraphs if _GOAL_HEAD.match(head)), "")
    if not chosen and paragraphs:
        chosen = paragraphs[0][1]
    text = " ".join(_plain(chosen).split())
    return text if len(text) <= GOAL_MAX else text[: GOAL_MAX - 1].rstrip() + "…"


def _plain(text: str) -> str:
    """Komórka bez znaczników kodu i pogrubień: `tom-140-guard` → tom-140-guard."""
    text = re.sub(r"`([^`]*)`", r"\1", text or "")
    text = re.sub(r"\*\*([^*]*)\*\*|__([^_]*)__", lambda m: m.group(1) or m.group(2) or "", text)
    # Klasy znaków wykluczają własny początek ([ i (), więc próby z kolejnych pozycji się nie nakładają —
    # koszt liniowy także dla linii z tysiącami „[” (plan do 512 KB czyta też podgląd karty).
    text = re.sub(r"\[([^\[\]]*)\]\([^()]*\)", r"\1", text)
    return text.strip()
