"""Repozytorium tablicy na dysku: odczyt, zapis z walidacją i commit każdej zmiany.

Układ (opis: docs/tide-daemon.md, Tablica):

  board.md                 znacznik `tide-board: 1` i licznik `next-card` (ID nie wracają)
  projects/<slug>.md       name, repo, tags, ai-docs, tool, entry, mode; treść — opis projektu
  tags/<slug>.md           name, color (nazwa pastelu z palety); treść — opis tagu
  cards/K-<n>.md           id, title, project, tags, column, order, note, created, updated, moved,
                           youtrack, spec, session, branch, check; treść — opis karty (Markdown)
  flows/<klucz>.md         szablony poleceń (flows.py), flows/<projekt>/<klucz>.md — nadpisanie
  docs/<projekt>/          plany i specyfikacje agentów projektów z `ai-docs: tide`

Repozytorium powstaje przy pierwszym poprawnym zapisie. Każda nasza zmiana to osobny commit
z autorem z konfiguracji gita użytkownika; commitujemy tylko pliki, które zmieniliśmy — zmiany
z zewnątrz (agent w docs/, ręczna edycja) zbiera `sync()` osobnym commitem. Plik jest źródłem
prawdy: nieudany commit nie cofa zapisu (odpowiedź ma `commitError`, commit czeka — repo.py).
Nigdy push, nigdy remote.

Wszystkie ścieżki zapisu powstają z ID karty i slugów po walidacji, a dodatkowo sprawdzamy,
że po rozwinięciu dowiązań leżą w katalogu tablicy.
"""
import os
import contextlib
import json
import secrets
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime
import threading
from threading import RLock
from types import SimpleNamespace

from ..agents.timeutil import parse_ts
from ..coordination.youtrack import issue_key
from ..worktrees.git import Git
from ..worktrees.naming import slug_from_prompt
from . import flows, commands, assignment, contract_approval, lockfile, project_tests
from .frontmatter import Document, FrontmatterError, dump
from .marker import FORMAT_VERSION, MARKER_FILE, MARKER_KEY, is_board_repo
from .repo import BoardRepo
from .model import (AI_DOCS, CARD_FILE_RE, CARD_KEYS, CARD_PREFIX, CHECKS, COLUMNS, DEFAULT_CHECK, DEFAULT_COLUMN,
                    DEFAULT_ENTRY, DEFAULT_MODE, DEFAULT_TOOL, DESCRIPTION_MAX, ENTRIES, FIELD_MAX, FILE_MAX, LAUNCH_KEYS, LEGACY_SPEC_MODE, MANUAL_COLUMNS,
                    MODES, MOVED_FROM_KEY, COLUMN_MOVED_KEY, NAME_MAX, NOTE_MAX, PASTELS, PATH_MAX, PROJECT_SYNC_KEY,
                    PROJECT_YOUTRACK_KEY, PROJECTS_KEY, PROJECTS_MAX, MAX_TICKETS_KEY, RESERVED_CARD_KEYS, YOUTRACK_PROJECT_RE,
                    REVIEW_CLASSES, REVIEW_CLASSES_KEY, REVIEW_POLICIES, REVIEW_POLICY_KEY,
                    SEED_TAGS, SLUG_RE, TAG_MAX, TITLE_MAX,
                    TOOLS, BoardError, Card, Project, Tag, card_num, default_ai_docs, fallback_color, merge_tags,
                    order_value, parse_card, parse_project, parse_tag, slugify, split_tags)

ORDER_STEP = 10
TMP_PREFIX = ".tide-tmp-"
GITIGNORE = f"""# Pliki tymczasowe zapisu Tide i edytorów — nie trafiają do historii tablicy.
{TMP_PREFIX}*
*.swp
*.swo
*~
.#*
\\#*\\#
.~lock.*
4913
"""
BOARD_MD = f"""---
{MARKER_KEY}: {FORMAT_VERSION}
next-card: 1
assignment-rules: {dump(json.dumps(assignment.DEFAULT_RULES, ensure_ascii=False))}
---
# Tablica Tide

Lokalne repozytorium tablicy pracy Tide. Zmiany robi tide-daemon (okno Tide, D-Bus `/Board`)
i każdą od razu commituje; ręczne edycje plików też są w porządku — usługa zbierze je osobnym
commitem „sync external changes”. Bez remote: nic stąd nie wychodzi.

- `projects/<projekt>.md` — repozytorium, tagi, narzędzie, tryb i miejsce dokumentów AI,
- `tags/<tag>.md` — nazwa i kolor tagu,
- `cards/K-<numer>.md` — karty („rzeczy do zrobienia”), treść to opis,
- `flows/<klucz>.md` — szablony poleceń: full i light (Planowanie) oraz spec (Tylko specyfikacja),
- `docs/<projekt>/` — plany i specyfikacje agentów projektów z `ai-docs: tide`.
"""
CARD_FIELDS = ("title", "description", "project", "repo", "projects", "tags", "note", "planPath", *RESERVED_CARD_KEYS, "check",
               *LAUNCH_KEYS, "maxTickets")
CREATE_FIELDS = CARD_FIELDS + ("column",)
PROJECT_FIELDS = ("slug", "name", "repo", "tags", "aiDocs", "tool", "mode", "entry", "description", "youtrack",
                  "youtrackSync", "allowedCommands", "deniedCommands", "assignmentRules", "globalAssignmentRules", "readDirs",
                  "tests", "reviewIndependence", "reviewIndependenceClasses")
TAG_FIELDS = ("name", "newName", "color", "description")
_CONTRACT_KEY = "_testsContract"   # wewnętrzny klucz wyniku planu projektu: kontrakt po zapisie (nie trafia do D-Bus)
NO_TESTS_SUGGESTION = "brak podpowiedzi — kartki odeślą do zasad projektu (CLAUDE.md, AGENTS.md)"
TESTS_UNAPPROVED = "kontrakt testów zmieniony poza oknem projektu — sprawdź i zatwierdź"


class Board:
    """Jeden odczyt tablicy z dysku."""

    def __init__(self):
        self.meta: Document | None = None
        self.projects: dict[str, Project] = {}
        self.tags: dict[str, Tag] = {}          # klucz: nazwa.casefold()
        self.cards: dict[int, Card] = {}
        self.errors: list[dict] = []
        self.max_num = 0                        # także z plików, których nie dało się przeczytać

    def project_by_repo(self, repo: str) -> Project | None:
        real = os.path.realpath(repo)
        return next((p for p in self.projects.values() if p.repo and os.path.realpath(p.repo) == real), None)

    def used_tags(self) -> list[str]:
        """Tagi użyte w projektach i kartach (także te bez pliku w tags/)."""
        return split_tags([n for p in self.projects.values() for n in p.tags] +
                          [n for c in self.cards.values() for n in c.tags])

    def column_cards(self, column: str, skip: int = 0) -> list[Card]:
        cards = [c for c in self.cards.values() if c.column == column and c.num != skip]
        return sorted(cards, key=lambda c: (c.order, c.num))


@dataclass
class Change:
    """Zaplanowany zapis: commity (komunikat, [(ścieżka względna, treść)]) i odpowiedź dla D-Bus."""
    commits: list = field(default_factory=list)
    result: dict = field(default_factory=dict)
    high_water: int = 0      # następny wolny numer karty po tym zapisie (0 — bez zmiany)


class BoardBusy(RuntimeError):
    """Transakcję Tablicy trzyma inny wątek albo proces, a wołający (`BoardStore.without_waiting`) nie czeka."""


class BoardStore:
    def __init__(self, root: str, git: Git | None = None, clock=time.time, colors: dict | None = None,
                 approvals_path: str | None = None):
        self.root = os.path.abspath(os.path.expanduser(root))
        # Magazyn odcisków zatwierdzonych kontraktów testów (contract_approval); None — $XDG_STATE_HOME w chwili wywołania.
        self.approvals_path = approvals_path
        # Błędy Tablicy z ostatniego `approval_for_step` (PT-286): slug → wpis; kolizja magazynu z obszarem zapisu kroku.
        self._step_approval_errors: dict[str, dict] = {}
        self.repo = BoardRepo(self.root, git)
        self.git = self.repo.git
        self.clock = clock
        self._transaction_lock = RLock()
        self._transaction_depth = 0
        self._transaction_stack: contextlib.ExitStack | None = None
        self._board_locked = False
        self._transaction_waits = True     # bieżąca zewnętrzna transakcja czeka na flock (False — `without_waiting`)
        self._wait_local = threading.local()   # znacznik `without_waiting` bieżącego wątku (K-98 #2)
        # nazwa pastelu → kolor (#rrggbb) z palety; bez palety same nazwy bez kolorów
        self.colors = dict(colors) if colors else {name: "" for name in PASTELS}
        # K-98 #3: licznik zapisów plików Tablicy w tym procesie; kontekst odczytu (`BoardRead`) po jego zmianie
        # wczytuje Tablicę ponownie (zapis w ticku unieważnia kontekst ticku).
        self.generation = 0

    # --- odczyt --------------------------------------------------------------------------------

    def exists(self) -> bool:
        return is_board_repo(self.root)

    @contextlib.contextmanager
    def without_waiting(self, wait_s: float = 0.0):
        """K-98 #2: w tym bloku transakcje Tablicy z bieżącego wątku nie czekają na blokadę trzymaną przez inny wątek
        (worker mutacji: StartCard, scalenie, tick) ani inny proces — rzucają `BoardBusy` od razu albo, z `wait_s`,
        najpóźniej po tylu sekundach liczonych od wejścia w blok (jeden termin na cały blok, nie na transakcję).
        Publikacja migawki w wątku głównym używa go, żeby nigdy nie stanąć na długiej mutacji; jawny Snapshot() czeka
        krótko, żeby krótka transakcja ticku nie zostawiła mu starej wersji. Zwraca znacznik: `busy` jest prawdą, gdy
        którakolwiek próba w bloku trafiła na zajętą blokadę (także gdy wołający złapał wyjątek i podał zastępczą
        treść) — wtedy wynik bloku trzeba odrzucić. Blok zagnieżdżony korzysta ze znacznika i terminu zewnętrznego."""
        outer = getattr(self._wait_local, "probe", None)
        probe = outer if outer is not None else SimpleNamespace(
            busy=False, deadline=time.monotonic() + wait_s if wait_s > 0 else None)
        self._wait_local.probe = probe
        try:
            yield probe
        finally:
            self._wait_local.probe = outer

    def read_was_busy(self) -> bool:
        """Czy w bieżącym bloku `without_waiting` tego wątku któraś transakcja trafiła na zajętą blokadę."""
        probe = getattr(self._wait_local, "probe", None)
        return bool(probe is not None and probe.busy)

    @contextlib.contextmanager
    def transaction(self):
        """Wspólna granica odczytów i zapisów; odtworzenie wyprzedza widoczność kart. W bloku `without_waiting`
        zajęta blokada daje `BoardBusy` zamiast czekania."""
        probe = getattr(self._wait_local, "probe", None)
        if probe is None:
            self._transaction_lock.acquire()
        else:
            left = probe.deadline - time.monotonic() if probe.deadline is not None else 0.0
            if not (self._transaction_lock.acquire(timeout=left) if left > 0
                    else self._transaction_lock.acquire(blocking=False)):
                probe.busy = True
                raise BoardBusy("Tablica: transakcję trzyma inny wątek")
        try:
            if self._transaction_depth:
                yield
                return
            with contextlib.ExitStack() as stack:
                try:
                    self._transaction_depth += 1
                    self._transaction_stack = stack
                    self._transaction_waits = probe is None
                    # Ta sama blokada co zapis profilu pudełka K-38 (`box/profile.save_project`, PT-321) — także
                    # na Tablicy bez `.git`, którą `ensure()` dopiero zainicjalizuje. Bez katalogu Tablicy blokadę
                    # bierze dopiero `ensure()` (`_hold_board_lock`) i trzyma do końca tej transakcji: świeży
                    # odczyt, plan i zapis po założeniu Tablicy nie przeplatają się z zapisem K-38.
                    if os.path.isdir(self.root):
                        self._hold_board_lock()
                        if self.repo.initialized():
                            from ..issue_acceptance import recover
                            recover(self)
                    yield
                finally:
                    self._transaction_depth = 0
                    self._transaction_stack = None
                    self._board_locked = False
                    self._transaction_waits = True
        finally:
            self._transaction_lock.release()

    def _hold_board_lock(self) -> None:
        """Wspólna blokada Tablicy do końca bieżącej (zewnętrznej) transakcji; brakujący katalog zakłada
        (tak samo robi `box/profile.board_lock`). Ścieżka, która nie jest katalogiem — bez blokady
        (`ensure()` i tak ją odrzuci). Transakcja z `without_waiting`: blokada innego procesu → `BoardBusy`."""
        if self._board_locked or self._transaction_stack is None:
            return
        if not os.path.lexists(self.root):
            os.makedirs(self.root, exist_ok=True)
        if os.path.isdir(self.root):
            probe = getattr(self._wait_local, "probe", None)
            deadline = probe.deadline if probe is not None and not self._transaction_waits else None
            try:
                self._transaction_stack.enter_context(
                    lockfile.exclusive(self.root, blocking=self._transaction_waits, deadline=deadline))
            except BlockingIOError:
                if probe is not None:
                    probe.busy = True
                raise BoardBusy("Tablica: blokadę trzyma inny proces") from None
            self._board_locked = True

    def load(self) -> Board:
        with self.transaction():
            return self._load()

    def _load(self) -> Board:
        board = Board()
        if not self.exists():
            for name, color, description in SEED_TAGS:  # podgląd tagów, które powstaną z tablicą
                board.tags[name.casefold()] = Tag(name, color if color in self.colors else
                                                  fallback_color(name, self.colors), description, slugify(name, "tag"))
            return board
        board.meta = self._read_doc(MARKER_FILE, board.errors)
        for slug, doc, path in self._docs("tags", board.errors):
            tag = parse_tag(doc, path, slug, self.colors, board.errors)
            if tag.name.casefold() in board.tags:
                board.errors.append({"path": path, "message": f"tag „{tag.name}” jest już w innym pliku — pomijam"})
                continue
            board.tags[tag.name.casefold()] = tag
        for slug, doc, path in self._docs("projects", board.errors):
            project = board.projects[slug] = parse_project(doc, path, slug, board.errors)
            self._load_project_tests(project, board.errors)
        self._load_cards(board)
        board.errors.extend(dict(entry) for entry in list(self._step_approval_errors.values()))
        for card in board.cards.values():
            if card.project and card.project not in board.projects:
                board.errors.append({"path": card.path, "message": f"{card.id}: nieznany projekt „{card.project}”"})
            for slug in card.projects:
                if slug not in board.projects:
                    board.errors.append({"path": card.path,
                                         "message": f"{card.id}: nieznany dodatkowy projekt „{slug}”"})
        return board

    def snapshot(self, repos=None, derive=None, board: Board | None = None) -> dict:
        """Migawka dla D-Bus i --print-board. repos: ścieżki repozytoriów z katalogu projektów
        (do listy „dodaj jako projekt”); derive(card) → dict|None: stan z pracy agentów (PT-5,
        board/derive.py) — karta dostaje na wejściu także `repo` swojego projektu. `board`: Tablica już wczytana
        w tym odświeżeniu (kontekst `BoardRead`, K-98 #1); bez niej — świeży odczyt. Migawka jej nie zmienia."""
        board = self.load() if board is None else board
        errors = list(board.errors)
        tags = dict(board.tags)
        projects = sorted(board.projects.values(), key=lambda p: p.name.casefold())
        counts: dict[str, int] = {}
        cards_out = []
        plan_cache = {}
        for card in sorted(board.cards.values(), key=lambda c: (_column_index(c.column), c.order, c.num)):
            project = board.projects.get(card.project)
            resolved = merge_tags(project.tags if project else [], card.tags)
            for name in resolved:
                counts[name.casefold()] = counts.get(name.casefold(), 0) + 1
            derived = None
            extra = [board.projects[slug] for slug in card.projects if slug in board.projects]
            if derive is not None:
                try:
                    derived = derive({**_card_json(card, project, resolved, None, extra),
                                      "repo": project.repo if project else "",
                                      "extraRepos": [p.repo for p in extra if p.repo]})
                except Exception as exc:  # stan z koordynatora to dodatek — nie może zepsuć tablicy
                    errors.append({"path": card.path, "message": f"{card.id}: stan koordynatora: {exc}"})
            card_data = _card_json(card, project, resolved, derived, extra)
            if project and project.repo:
                from .plan_candidates import candidates
                key = project.repo
                if key not in plan_cache:
                    plan_cache[key] = candidates(project.repo, self.root)
                card_data["planCandidates"] = [p for p in plan_cache[key] if p["card"] in ("", card.id) and p["status"] != "done"]
            else:
                card_data["planCandidates"] = []
            cards_out.append(card_data)
        for name in board.used_tags():
            tags.setdefault(name.casefold(), Tag(name, fallback_color(name, self.colors), slug=slugify(name, "tag"),
                                                 defined=False))
        linked = {os.path.realpath(p.repo) for p in projects if p.repo}
        available = []
        for path in repos or []:
            if os.path.realpath(path) in linked or os.path.realpath(path) == os.path.realpath(self.root):
                continue
            available.append({"name": os.path.basename(path.rstrip("/")), "path": path,
                              "aiDocs": default_ai_docs(path)})
        available.sort(key=lambda r: r["name"].casefold())
        project_cards: dict[str, int] = {}
        for card in board.cards.values():
            if card.column != "archived":
                project_cards[card.project] = project_cards.get(card.project, 0) + 1
        exists = board.meta is not None
        busy = self.repo.busy() if exists else ""
        git_state = {"busy": busy, "pending": len(self.repo.pending), "error": self.repo.last_error}
        if exists:
            errors.extend(self._flow_warnings(board))
        if busy:
            errors.insert(0, {"path": self.root, "message": self.repo.busy_message(busy)})
        if self.repo.pending:
            errors.insert(0, {"path": self.root, "message": "zmiany są zapisane w plikach, ale czekają na "
                                                            f"commit ({self.repo.last_error})"})
        last = self.repo.last_commit() if exists else None
        return {
            "assignmentRules": assignment.rules(board),
            "boardDir": self.root,
            "exists": exists,
            "lastCommit": {"hash": last[0], "short": last[0][:7], "ts": last[1],
                           "time": _local_iso(last[1]) if last[1] else "", "subject": last[2]} if last else None,
            "git": git_state,
            "nextId": f"{CARD_PREFIX}{self._next_num(board)}",
            "columns": [dict(c) for c in COLUMNS],
            "colors": dict(self.colors),
            "tools": list(TOOLS),
            "modes": list(MODES),
            "entries": list(ENTRIES),
            "tags": [{"name": t.name, "slug": t.slug, "color": t.color, "description": t.description,
                      "defined": t.defined, "cards": counts.get(key, 0)}
                     for key, t in sorted(tags.items(), key=lambda kv: kv[1].name.casefold())],
            "projects": [_project_json(p, self.docs_dir(p), project_cards.get(p.slug, 0)) for p in projects],
            "cards": cards_out,
            "repos": available,
            "errors": errors,
        }

    def _flow_warnings(self, board: Board) -> list[dict]:
        """Ostrzeżenia o szablonach `flows/` zmienionych przez użytkownika, które zostają bez zmian (PT-77)."""
        out = []
        rels = [os.path.join("flows", f"{key}.md") for key in flows.FLOW_KEYS]
        rels += [os.path.join("flows", slug, f"{key}.md") for slug in sorted(board.projects) for key in flows.FLOW_KEYS]
        for rel in rels:
            message = flows.legacy_mode_warning(rel, self._read_file(rel, []))
            if message:
                out.append({"path": os.path.join(self.root, rel), "message": message})
        return out

    def docs_dir(self, project: Project | None) -> str:
        """Gdzie agenci karty piszą plany i specyfikacje: docs/agents w repozytorium (`ai-docs: repo`)
        albo docs/<projekt>/ w tablicy (`tide`, także karta bez projektu: docs/_inbox)."""
        if project is None:
            return os.path.join(self.root, "docs", "_inbox")
        if project.ai_docs == "repo" and project.repo:
            return os.path.join(project.repo, "docs", "agents")
        return os.path.join(self.root, "docs", project.slug)

    def render_flow(self, card_id: str, mode: str = "") -> dict:
        """Polecenie trybu dla karty z narzędziem i gałęzią z projektu (RenderFlow)."""
        return self.render_launch(card_id, {"mode": mode})

    def render_launch(self, card_id: str, request: dict | None = None) -> dict:
        """Polecenie trybu dla karty (szablon z flows/ albo wbudowany) i dane uruchomienia.

        request: {entry, mode, tool, branch, check, baseRef, baseCommit} — pola opcjonalne; para baseRef/baseCommit
        jest kontekstem odczytu specyfikacji, walidowanym i zapisywanym oddzielnie od przepływu.
        Puste wejście, tryb i narzędzie to
        domyślne projektu (wejście: planning albo spec; tryb full albo light dotyczy tylko Planowania; dawne
        `mode: spec` to wejście spec), pusta gałąź — `branch` karty albo propozycja `<narzędzie>/k-12-<opis>`, check —
        `auto` (domyślnie: koniec po review AI) albo `manual` (test ręczny użytkownika po zakończeniu).
        Katalog dokumentów w tablicy (`ai-docs: tide`) zakładamy od razu — agent dostaje go przez
        --add-dir. Szablon `flows/<tryb>.md`, który jest nietkniętym dawnym zaczynem, zastępuje
        wbudowana treść (migrate_flows podmieni plik przy najbliższej okazji)."""
        data = _fields(request or {}, ("entry", "mode", "tool", "branch", "check", "preferredProvider", "baseRef", "baseCommit"))
        board = self.load()
        card = self._card(board, card_num(card_id))
        project = board.projects.get(card.project)
        entry, mode = _launch_entry_mode(data, project)
        flow_key = flows.flow_key(entry, mode)
        tool = _text(data.get("tool"), "tool").strip().lower() or (project.tool if project else DEFAULT_TOOL)
        if tool not in TOOLS:
            raise BoardError(f"nieznane narzędzie „{tool}” (claude, codex)")
        branch = _line(data.get("branch"), FIELD_MAX, "branch") or card.branch or suggest_branch(card, tool)
        check = _text(data.get("check"), "check").strip().lower() or "auto"
        if check not in flows.CHECKS:
            raise BoardError(f"nieznany sposób sprawdzenia „{check}” (auto, manual)")
        preferred = _text(data.get("preferredProvider"), "preferredProvider").strip() or "anthropic"
        from ..models import config as models_config
        if preferred not in ("mixed", "local") and not models_config.load().catalog.known_provider(preferred):
            raise BoardError(f"nieznany preferowany dostawca „{preferred}”")
        revision = None
        if "baseRef" in data or "baseCommit" in data:
            if entry != "spec":
                raise BoardError("wybrana rewizja RenderLaunch dotyczy tylko specyfikacji")
            if not data.get("baseRef") or not data.get("baseCommit"):
                raise BoardError("wybór rewizji wymaga pełnego refu i SHA — odśwież wybór")
            revision = self._spec_revision(card.id, project, data["baseRef"], data["baseCommit"])
        elif entry == "spec" and card.spec_revision_ref:
            if not project or project.repo != card.spec_revision_repo:
                raise BoardError("projekt rewizji zmienił się — odśwież wybór")
            revision = self._spec_revision(card.id, project, card.spec_revision_ref, card.spec_revision_commit)
        template, source = None, "builtin"
        candidates = ([os.path.join("flows", project.slug, f"{flow_key}.md")] if project else []) + \
            [os.path.join("flows", f"{flow_key}.md")]
        for rel in candidates:
            text = self._read_file(rel, [])
            if text is not None:
                if rel == candidates[-1] and flows.outdated(flow_key, text):
                    break  # dawny zaczyn — wbudowana treść
                template, source = text, os.path.join(self.root, rel)
                break
        if template is None:
            template = flows.SEED_FLOWS[flow_key]
        docs = self.docs_dir(project)
        in_board = _inside(docs, self.root)
        if in_board:
            os.makedirs(docs, exist_ok=True)  # pusty katalog nie trafia do gita; pliki agenta zbierze sync
        ai_docs = project.ai_docs if project else "tide"
        # Agent w worktree pisze w docs/agents swojego katalogu roboczego (plan idzie z pracą na gałęzi),
        # a w tablicy — pod ścieżką bezwzględną, którą dostaje przez --add-dir.
        docs_ref = docs if in_board else os.path.join("docs", "agents")
        if in_board:
            docs_note = (f"plany i specyfikacje zapisujesz w {docs} — to repozytorium tablicy Tide (masz do niego "
                         "dostęp przez --add-dir). Niczego tam nie commituj ani nie dodawaj do gita: te pliki "
                         "commituje usługa Tide. Do repozytorium projektu nie trafia żaden plan ani specyfikacja. "
                         "Pliki w tym katalogu czytaj i zapisuj po ścieżkach bezwzględnych — nie wchodź do niego "
                         "przez `cd`: twój katalog roboczy zostaje w repozytorium projektu.")
        else:
            docs_note = ("plany i specyfikacje zapisujesz w docs/agents/ w swoim katalogu roboczym (repozytorium "
                         "projektu); plan commitujesz razem z pracą albo osobnym commitem `docs(plan): …`, "
                         "jak mówi standard.")
        extra = [board.projects[slug] for slug in card.projects if slug in board.projects]
        extra_repos = [(p.name, p.repo) for p in extra if p.repo and not p.repo_missing]
        add_dirs = ([docs] if in_board else []) + [repo for _name, repo in extra_repos]
        values = {"card_id": card.id, "title": card.title, "description": card.description.strip(),
                  "project": project.name if project else "", "repo": project.repo if project else "",
                  "docs_dir": docs_ref, "branch": branch if flow_key != "spec" else "bez gałęzi — katalog projektu",
                  "tool": tool, "docs_note": docs_note, "spec": card.spec or "brak (zaczynasz od zera)",
                  "spec_file": os.path.join(docs_ref, "specs", spec_name(card)), "check": check,
                  "check_label": flows.CHECK_LABELS[check], "finish": flows.finish_text(check, docs_ref)}
        values["spec_commit"] = flows.spec_commit_text(in_board, card.id, values["spec_file"])
        values["projects"] = flows.projects_text(extra_repos, flow_key, branch, values["repo"], docs_ref)
        rendered = flows.render(template, values)
        if revision:
            import shlex
            protection = "Nie przełączaj checkoutu. Nie zmieniaj refów, indeksu ani plików użytkownika"
            if not in_board:
                protection += " poza zapisem i commitem wyłącznie pliku specyfikacji opisanym wyżej"
            rendered += (f"\n\nKontekst wejścia Tylko specyfikacja (wyłącznie do odczytu):\n"
                         f"Repozytorium: {revision['repo']}\nRef: {revision['ref']}\nSHA: {revision['commit']}\n"
                         "Czytaj kod i dokumenty z podanego SHA, także pliki nieobecne w bieżącym checkoutcie. "
                         "Nie zastępuj tej rewizji przez HEAD ani zawartość katalogu roboczego. "
                         f"Używaj `git -C {shlex.quote(revision['repo'])} show {revision['commit']}:ścieżka` "
                         f"oraz `git -C {shlex.quote(revision['repo'])} ls-tree -r --name-only {revision['commit']}`. "
                         f"{protection}. "
                         "Nie uruchamiaj StartCard, nie zakładaj gałęzi/worktree karty ani nie rezerwuj ticketów. "
                         "Wybór dotyczy tylko projektu karty; dodatkowe projekty zachowują swoje bazy tylko do odczytu. "
                         "Wynikową specyfikację zapisz według powyższych zasad w dotychczasowym miejscu.\n")
        if entry == "planning" and data.get("preferredProvider"):
            rendered += f"\n\nPreferowany dostawca wykonawców: {preferred}. Dobieraj wykonawców według klas z katalogu Tide.\n"
        return {"ok": True, "id": card.id, "entry": entry, "mode": mode, "tool": tool, "branch": branch, "check": check,
                "preferredProvider": preferred,
                "specRevision": revision,
                "docsDir": docs,
                "aiDocs": ai_docs, "repo": values["repo"], "repoMissing": bool(project and project.repo_missing),
                "project": card.project, "projectName": values["project"], "template": source,
                "specFile": os.path.join(docs, "specs", spec_name(card)), "text": rendered,
                "projects": [p.slug for p in extra], "extraRepos": [repo for _name, repo in extra_repos],
                "addDirs": add_dirs}

    def _spec_revision(self, card_id: str, project: Project | None, ref: str, commit: str) -> dict:
        """Osobny kontekst odczytu; bez dziennika i zasobów przepływu karty."""
        from ..cardflow.gitops import FlowGit, FlowGitError, lock_for
        if not project or not project.repo or project.repo_missing:
            raise BoardError("wybrana rewizja wymaga repozytorium projektu karty")
        with lock_for(project.repo):
            try:
                base = FlowGit().resolve_base(project.repo, ref, commit)
            except FlowGitError as exc:
                raise BoardError(str(exc)) from exc
            revision = {"repo": project.repo, "ref": base["baseRef"], "commit": base["baseCommit"]}

            def plan(board):
                card = self._card(board, card_num(card_id))
                current = board.projects.get(card.project)
                if not current or current.repo != project.repo:
                    raise BoardError("projekt rewizji zmienił się — odśwież wybór")
                changed = []
                for key, value in revision.items():
                    changed += _doc_set(card.doc, "spec-revision-" + key, value)
                change = Change(result={"ok": True, "id": card.id})
                if changed:
                    change.commits.append((f"{card.id}: remember specification revision",
                                           [(_rel(self.root, card.path), card.doc.text())]))
                return change
            self._execute(plan)
        return revision

    def migrate_flows(self) -> list[str]:
        """Podmienia w flows/ szablony trybów, które są nietkniętymi dawnymi zaczynami (flows.PREVIOUS_SEEDS),
        na obecne — jednym commitem „flows: update templates”. Zmienionych przez użytkownika, nadpisań
        projektów i brakujących plików nie dotyka. Zwraca zmienione ścieżki względne."""
        if not self.exists() or not self.repo.initialized() or self.repo.busy():
            return []
        writes = []
        for key in flows.FLOW_KEYS:
            rel = os.path.join("flows", f"{key}.md")
            if flows.outdated(key, self._read_file(rel, [])):
                writes.append((rel, flows.SEED_FLOWS[key]))
        if not writes:
            return []
        paths = [self._write(rel, text) for rel, text in writes]
        self.repo.commit("flows: update templates", paths)
        return paths

    def mark_ready_specs(self) -> list[str]:
        """Karty uruchomione w trybie „tylko spec” (mode: spec, w Do zrobienia, bez `spec`), których
        specyfikacja ma już `status: gotowa`: `spec` dostaje ścieżkę pliku, a karta wraca na górę
        Backlogu — jednym commitem „K-12: specification ready”. Zwraca ID oznaczonych kart."""
        if not self.exists() or not self.repo.initialized() or self.repo.busy():
            return []
        board = self.load()
        done = []
        for card in sorted(board.cards.values(), key=lambda c: c.num):
            if card.entry != "spec" or card.column != "todo" or card.spec:
                continue
            path = find_ready_spec(self.docs_dir(board.projects.get(card.project)), card.id)
            if not path:
                continue
            num = card.num

            def planner(fresh: Board, num=num, path=path) -> Change:
                return self._plan_spec_ready(fresh, num, path)
            try:
                self._execute(planner)
            except BoardError as exc:  # np. plik karty zepsuty w międzyczasie — następnym razem
                print(f"tide-daemon: tablica: specyfikacja {card.id}: {exc}", flush=True)
                continue
            done.append(card.id)
        return done

    # --- zapis ---------------------------------------------------------------------------------
    #
    # Każdy zapis: walidacja danych (bez dysku) → plan na obecnym stanie (błędy referencji:
    # nie ma karty, projektu…) → dopiero potem założenie tablicy, ponowny plan na świeżym
    # odczycie, zapis plików i commit. Błąd przed zapisem nie zostawia żadnego pliku ani commita.

    def create_card(self, data: dict) -> dict:
        values = _card_values(_fields(data, CREATE_FIELDS), create=True)
        return self._execute(lambda board: self._plan_create(board, values))

    def update_card(self, card_id: str, data: dict) -> dict:
        values = _card_values(_fields(data, CARD_FIELDS, hint="do zmiany kolumny i kolejności służy MoveCard"))
        num = card_num(card_id)
        return self._execute(lambda board: self._plan_update(board, num, values))

    def project_card_base(self, card_id: str, base: dict, board: Board | None = None) -> dict:
        """Wyłącznie projekcja dziennika; pola bazy nie należą do publicznego UpdateCard.

        `board`: Tablica z kontekstu odczytu ticku (K-98 #3). Gdy według niej pola bazy już są zapisane, nie ma
        zapisu ani wczytania; inaczej zwykła transakcja zapisu ze świeżym odczytem (`_execute`)."""
        fields = (("base-ref", "baseRef"), ("base-branch", "baseBranch"), ("base-commit", "baseCommit"))

        def plan(board):
            card = self._card(board, card_num(card_id))
            change = Change(result={"ok": True, "id": card.id})
            if card.column in ("done", "archived"):
                return change
            changed = []
            for key, public in fields:
                value = base.get(public) or ""
                if value or card.doc.has(key):
                    changed += _doc_set(card.doc, key, value)
            if changed:
                change.commits.append((f"{card.id}: project card base", [(_rel(self.root, card.path), card.doc.text())]))
            return change
        card = board.cards.get(card_num(card_id)) if board is not None else None
        if card is not None and (card.column in ("done", "archived") or all(
                _doc_same(card.doc, key, base.get(public) or "")
                for key, public in fields if (base.get(public) or "") or card.doc.has(key))):
            return {"ok": True, "id": card.id, "commitError": ""}     # bez zmiany: bez transakcji i wczytania
        return self._execute(plan)

    def move_card(self, card_id: str, column: str, index: int = -1, from_column: str = "") -> dict:
        """Przenosi kartę na pozycję `index` (od 0) kolumny; -1 albo poza zakresem — na koniec.
        from_column: kolumna, w której karta była wyświetlana (z pracy agentów, BoardMonitor) — trafia do
        `moved-from` (PT-29: „do poprawy” tylko po przeniesieniu z Do sprawdzenia albo Gotowe)."""
        column = _column(column)
        num = card_num(card_id)
        if isinstance(index, bool) or not isinstance(index, (int, float)) or index != int(index):
            raise BoardError("kolejność musi być liczbą całkowitą (pozycja w kolumnie od 0)")
        shown = _text(from_column, "kolumna").strip().lower()
        shown = shown if shown in [c["id"] for c in COLUMNS] else ""
        return self._execute(lambda board: self._plan_move(board, num, column, int(index), shown))

    def upsert_project(self, data: dict) -> dict:
        """Zapis projektu. Pole `tests` zapisuje kontrakt w `box.yaml`, ale go nie zatwierdza (PT-285):
        odcisk tworzy wyłącznie approve_project_tests."""
        result = self._upsert_project(data)
        result.pop(_CONTRACT_KEY, None)
        return result

    def _upsert_project(self, data: dict) -> dict:
        values = _project_values(_fields(data, PROJECT_FIELDS), self.root)
        return self._execute(lambda board: self._plan_project(board, values))

    def approve_project_tests(self, data: dict) -> dict:
        """UpsertProject z polem `tests` z okna projektu (D-Bus, board_monitor): zapis jak upsert_project,
        a potem zatwierdzenie — odcisk zapisanego kontraktu w stanie usługi (pusty kontrakt usuwa odcisk).
        Zatwierdzamy tylko wtedy, gdy plik po zapisie ma dokładnie ten kontrakt. Ponowne zatwierdzenie
        identycznych wartości zapisuje odcisk bez zmiany plików Tablicy i bez commita.
        Odpowiedź: to co upsert_project oraz `testsApproved` i `approvalError` (pusty bez błędu).
        Sprawdzenie nadawcy wywołania i kolizje ścieżek z obszarami zapisu kroków — PT-286."""
        if "tests" not in data:
            return self.upsert_project(data)
        with self.transaction():
            result = self._upsert_project(data)
            contract = result.pop(_CONTRACT_KEY)
            slug = result["slug"]
            project = self._load().projects.get(slug)
            result["testsApproved"], result["approvalError"] = False, ""
            if (project is None or project.tests_error or
                    contract_approval.canonical(project.tests) != contract_approval.canonical(contract)):
                result["approvalError"] = ("kontrakt testów w pliku różni się od zapisanego — nie zatwierdzam; "
                                           "sprawdź box.yaml i zapisz ponownie")
                return result
            try:
                contract_approval.record(self.root, slug, contract, self.approvals_path)
            except contract_approval.ApprovalError as exc:
                result["approvalError"] = f"zatwierdzenie kontraktu testów: {exc}"
                return result
            result["testsApproved"] = contract_approval.approved(self.root, slug, contract, self.approvals_path)
            return result

    def suggest_project_tests(self, data: dict) -> dict:
        """Podpowiedź kontraktu testów z repozytorium i źródło obowiązującego kontraktu (SuggestProjectTests).
        Wejście `{slug}` albo `{repo}` (bezwzględna ścieżka katalogu Git, ta sama walidacja co `_repo`).
        Odpowiedź: `{changed, full, source, facts, message}`; dla `slug` także `effectiveSource` (`profile`,
        `detected`, `rules`), `unapproved` i `testsApproved` z `effective_tests` na repozytorium projektu.
        Odczyt: nic nie zapisuje, nie zatwierdza i nie uruchamia programów. Projekt z brakującym repozytorium
        albo repozytorium bez celów — pusty wynik z komunikatem, bez błędu."""
        from ..cardflow import testpolicy
        data = _fields(data, ("slug", "repo"))
        slug = _text(data.get("slug"), "slug").strip()
        project = None
        if slug:
            project = self.load().projects.get(slug)
            if project is None:
                raise BoardError(f"nie ma projektu „{slug}”", "not-found")
            repo = project.repo if project.repo and not project.repo_missing else ""
        elif _text(data.get("repo"), "repo").strip():
            repo = _repo(data["repo"], self.root)
        else:
            raise BoardError("podaj slug projektu albo repo (ścieżka bezwzględna katalogu Git)")
        result = testpolicy.suggest_tests(testpolicy.repo_facts(repo))
        result["message"] = "" if result["source"] else NO_TESTS_SUGGESTION
        result["ok"] = True
        if project is not None:
            policy = testpolicy.effective_tests(project.tests, repo, project.tests_approved, "",
                                                error=project.tests_error)
            result["effectiveSource"], result["unapproved"] = policy.source, policy.unapproved
            result["testsApproved"] = project.tests_approved
        return result

    def approval_for_step(self, project: Project, step) -> tuple[bool, str]:
        """Zatwierdzenie kontraktu testów projektu dla kroku (`contract_approval.approval_for_step`, PT-286).
        Kolizja magazynu odcisków z obszarem zapisu kroku zostaje błędem Tablicy do kolejnego sprawdzenia
        tego projektu bez kolizji."""
        approved, reason = contract_approval.approval_for_step(project, step, self.root, self.approvals_path)
        if reason.startswith(contract_approval.STORE_IN_WRITE_AREA):
            self._step_approval_errors[project.slug] = {
                "path": self.approvals_path or contract_approval.state_path(), "message": f"{project.slug}: {reason}"}
        else:
            self._step_approval_errors.pop(project.slug, None)
        return approved, reason

    def upsert_tag(self, data: dict) -> dict:
        values = _tag_values(_fields(data, TAG_FIELDS), self.colors)
        return self._execute(lambda board: self._plan_tag(board, values))

    def ensure(self) -> None:
        """Zakłada repozytorium tablicy, jeśli go nie ma. Cudzego katalogu nie dotyka.

        Pod wspólną blokadą Tablicy trzymaną do końca transakcji wywołującego (PT-321)."""
        with self.transaction():
            self._hold_board_lock()
            self._ensure_locked()

    def _ensure_locked(self) -> None:
        if self.exists():
            if not self.repo.initialized():
                error = self.repo.init()
                if error:
                    raise BoardError(f"nie udało się założyć repozytorium git tablicy: {error}", "git-error")
            return
        if os.path.exists(self.root) and (not os.path.isdir(self.root) or os.listdir(self.root)):
            raise BoardError(f"katalog {self.root} istnieje, ale nie jest tablicą Tide (brak {MARKER_FILE} "
                             f"z „{MARKER_KEY}: {FORMAT_VERSION}”) — nic w nim nie zapisuję", "not-a-board")
        os.makedirs(self.root, exist_ok=True)
        error = self.repo.init()
        if error:
            raise BoardError(f"nie udało się założyć repozytorium git tablicy: {error}", "git-error")
        writes = [(MARKER_FILE, BOARD_MD), (".gitignore", GITIGNORE)]
        for name, color, description in SEED_TAGS:
            doc = Document(["name: " + name, "color: " + (color if color in self.colors
                                                           else fallback_color(name, self.colors))],
                           description + "\n")
            writes.append((os.path.join("tags", f"{slugify(name, 'tag')}.md"), doc.text()))
        for mode, text in flows.SEED_FLOWS.items():
            writes.append((os.path.join("flows", f"{mode}.md"), text))
        for folder in ("cards", "projects", "docs"):
            os.makedirs(os.path.join(self.root, folder), exist_ok=True)
        self.repo.commit("board: create", [self._write(rel, text) for rel, text in writes])

    def sync(self, message: str = "sync external changes") -> bool:
        """Zaległe commity i zmiany z zewnątrz (ręczna edycja, agent w docs/). True, gdy coś
        zapisaliśmy. W trakcie revert/merge/rebase z konfliktami — nic."""
        if not self.exists():
            return False
        with self.transaction():
            return self.repo.sync(message)

    # --- plany zapisu --------------------------------------------------------------------------

    def _execute(self, planner) -> dict:
        with self.transaction():
            return self._execute_locked(planner)

    def _execute_locked(self, planner) -> dict:
        dry = planner(self.load())            # błąd referencji tu — zanim cokolwiek trafi na dysk
        if not dry.commits:
            return {**dry.result, "commitError": ""}
        if self.exists():
            busy = self.repo.busy()
            if busy:
                raise BoardError(self.repo.busy_message(busy), "git-busy")
        self.ensure()
        change = planner(self.load())         # świeży odczyt (np. tagi i licznik z nowej tablicy)
        if change.high_water:
            self.repo.set_high_water(change.high_water)
        error = ""
        for message, writes in change.commits:
            paths = [self._write(rel, text) for rel, text in writes]
            error = self.repo.commit(message, paths) or error
        return {**change.result, "commitError": error}

    def _plan_create(self, board: Board, v: dict) -> Change:
        change = Change()
        slug = self._project_slug(board, v, change)
        extra = self._extra_projects(board, v["projects"], slug, change) if "projects" in v else []
        num = self._next_num(board, rebuild=True)
        while os.path.lexists(os.path.join(self.root, "cards", f"{CARD_PREFIX}{num}.md")):
            num += 1
        now = self._now()
        column_cards = board.column_cards(v["column"])
        order = (column_cards[0].order - ORDER_STEP) if column_cards else ORDER_STEP  # nowa na górę
        doc = Document()
        values = {"id": f"{CARD_PREFIX}{num}", "title": v["title"], "project": slug, "tags": ", ".join(v.get("tags", [])),
                  "column": v["column"], "order": order_value(order), "note": v.get("note", ""),
                  "created": now, "updated": now, "moved": now}
        for key in RESERVED_CARD_KEYS:
            values[key] = v.get(key, "")
        values["check"] = v.get("check", DEFAULT_CHECK)
        for key in CARD_KEYS:
            doc.set(key, values[key])
        for key in LAUNCH_KEYS:  # tylko ustawione — karta bez uruchomienia ich nie ma
            if v.get(key):
                doc.set(key, v[key])
        if v.get("maxTickets"):
            doc.set(MAX_TICKETS_KEY, v["maxTickets"])
        if v.get("planPath"):
            doc.set("plan", self._checked_plan_path(v["planPath"], board.projects.get(slug), f"K-{num}"))
        if extra:
            doc.set(PROJECTS_KEY, ", ".join(extra))
        doc.body = v.get("description", "")
        writes = [(os.path.join("cards", f"{CARD_PREFIX}{num}.md"), doc.text())]
        if board.meta is not None:
            board.meta.set("next-card", num + 1)
            writes.append((MARKER_FILE, board.meta.text()))
        change.commits.append((f"{CARD_PREFIX}{num}: create", writes))
        change.result = {"ok": True, "id": f"{CARD_PREFIX}{num}"}
        change.high_water = num + 1
        return change

    def _plan_update(self, board: Board, num: int, v: dict) -> Change:
        card = self._card(board, num)
        change = Change(result={"ok": True, "id": card.id, "changed": False})
        changed = []
        primary = card.project
        if "project" in v or "repo" in v:
            primary = self._project_slug(board, v, change)
            changed += _doc_set(card.doc, "project", primary)
        if "projects" in v:
            extra = self._extra_projects(board, v["projects"], primary, change)
            if extra or card.doc.has(PROJECTS_KEY):
                changed += _doc_set(card.doc, PROJECTS_KEY, ", ".join(extra))
        elif primary != card.project and primary in card.projects:  # nowy główny wypada z dodatkowych
            changed += _doc_set(card.doc, PROJECTS_KEY, ", ".join(p for p in card.projects if p != primary))
        for key in ("title", "tags", "note", *RESERVED_CARD_KEYS, "check", *LAUNCH_KEYS):
            if key in v and (v[key] or card.doc.has(key) or key not in LAUNCH_KEYS):
                changed += _doc_set(card.doc, key, ", ".join(v[key]) if key == "tags" else v[key])
        if "maxTickets" in v:      # PT-375: pusta wartość usuwa klucz (limit z ustawienia przepływu)
            if v["maxTickets"] is None:
                if card.doc.remove(MAX_TICKETS_KEY):
                    changed.append(MAX_TICKETS_KEY)
            else:
                changed += _doc_set(card.doc, MAX_TICKETS_KEY, v["maxTickets"])
        if "planPath" in v:
            project = board.projects.get(primary)
            checked = self._checked_plan_path(v["planPath"], project, card.id) if v["planPath"] else ""
            changed += _doc_set(card.doc, "plan", checked)
        if "description" in v and v["description"].strip() != card.doc.body.strip():
            card.doc.body = v["description"]
            changed.append("description")
        if not changed:
            return change
        card.doc.set("updated", self._now())
        change.commits.append((f"{card.id}: update {', '.join(changed)}", [(_rel(self.root, card.path),
                                                                           card.doc.text())]))
        change.result["changed"] = True
        return change

    def _checked_plan_path(self, path: str, project: Project | None, card_id: str) -> str:
        from .plan_candidates import candidates
        if not project or not project.repo:
            raise BoardError("wybierz projekt z repozytorium przed przejęciem planu", "no-project")
        real = os.path.realpath(path)
        allowed = candidates(project.repo, self.root)
        if not any(item["path"] == real and item["card"] in ("", card_id) and item["status"] != "done"
                   for item in allowed):
            raise BoardError("wybrany plan nie należy do tej karty i projektu", "bad-plan")
        return real

    def _plan_move(self, board: Board, num: int, column: str, index: int, shown: str = "") -> Change:
        """Nowa kolejność to środek między sąsiadami; całą kolumnę numerujemy co ORDER_STEP tylko
        wtedy, gdy między nimi nie ma miejsca — zwykle zmienia się jeden plik.

        Każde wywołanie to jawne przeniesienie przez użytkownika, więc zawsze stempluje `moved` —
        także do tej samej zapisanej kolumny i na to samo miejsce: karta pokazana w Do sprawdzenia
        ma zwykle zapisane `todo`, a przeniesienie jej „do poprawy” do Do zrobienia musi być widać
        (board/derive.py)."""
        card = self._card(board, num)
        move_id = secrets.token_hex(16)
        change = Change(result={"ok": True, "id": card.id, "changed": False})
        card.doc.set("move-id", move_id)
        others = board.column_cards(column, skip=card.num)
        if index < 0 or index > len(others):
            index = len(others)
        moved_column = card.column != column
        current = sum(1 for c in others if (c.order, c.num) < (card.order, card.num))
        # Skąd przeniesiona: kolumna wyświetlana (bez wiedzy o pracy agentów — zapisana). Karta, która zostaje
        # w kolumnie, w której ją widać (zmiana kolejności „do poprawy” w Do zrobienia), zachowuje `moved-from`.
        origin = shown or card.column
        if origin != column or not card.doc.has(MOVED_FROM_KEY):
            card.doc.set(MOVED_FROM_KEY, origin)
        now = self._now()
        if origin != column:   # prawdziwa zmiana kolumny (nie kolejność w kolumnie, w której karta jest widoczna)
            card.doc.set(COLUMN_MOVED_KEY, now)
        if not moved_column and current == index:
            card.doc.set("updated", now)
            card.doc.set("moved", now)
            change.commits.append((f"{card.id}: move to {column}", [(_rel(self.root, card.path), card.doc.text())]))
            change.result["changed"] = True
            return change
        before = others[index - 1].order if index > 0 else None
        after = others[index].order if index < len(others) else None
        if before is None and after is None:
            target = ORDER_STEP
        elif before is None:
            target = after - ORDER_STEP
        elif after is None:
            target = before + ORDER_STEP
        elif after - before >= 2:
            target = before + (after - before) // 2
        else:
            target = None  # brak miejsca — numerujemy kolumnę od nowa
        writes = []
        if target is not None:
            card.doc.set("order", order_value(target))
        else:
            ordered = others[:index] + [card] + others[index:]
            for position, item in enumerate(ordered, start=1):
                if item is not card and item.order == position * ORDER_STEP:
                    continue
                item.doc.set("order", position * ORDER_STEP)
                if item is not card:
                    writes.append((_rel(self.root, item.path), item.doc.text()))
        card.doc.set("column", column)
        card.doc.set("updated", now)
        card.doc.set("moved", now)  # „do poprawy” w derive.py patrzy na przeniesienie, nie na edycję
        writes.insert(0, (_rel(self.root, card.path), card.doc.text()))
        verb = f"move to {column}" if moved_column else f"reorder in {column}"
        change.commits.append((f"{card.id}: {verb}", writes))
        change.result["changed"] = True
        return change

    def _plan_spec_ready(self, board: Board, num: int, path: str) -> Change:
        card = self._card(board, num)
        if card.spec or card.column != "todo":
            return Change(result={"ok": True, "id": card.id, "changed": False})
        backlog = board.column_cards("backlog", skip=card.num)
        card.doc.set("spec", path)
        card.doc.set("column", "backlog")
        card.doc.set("order", order_value((backlog[0].order - ORDER_STEP) if backlog else ORDER_STEP))
        card.doc.set("updated", self._now())
        change = Change(result={"ok": True, "id": card.id, "changed": True})
        change.commits.append((f"{card.id}: specification ready", [(_rel(self.root, card.path), card.doc.text())]))
        return change

    def _plan_project(self, board: Board, v: dict) -> Change:
        if "globalAssignmentRules" in v:
            if len(v) != 1:
                raise BoardError("wspólne zasady zapisuje się oddzielnie od projektu")
            doc = board.meta or Document.parse(BOARD_MD)
            before = doc.text()
            doc.set("assignment-rules", json.dumps(v["globalAssignmentRules"], ensure_ascii=False))
            change = Change(result={"ok": True, "changed": doc.text() != before})
            if doc.text() != before:
                change.commits.append(("board: update assignment rules", [(MARKER_FILE, doc.text())]))
            return change
        slug, repo, name = v.get("slug", ""), v.get("repo"), v.get("name")
        if slug:
            project = board.projects.get(slug)
        elif repo:
            project = board.project_by_repo(repo)
        elif name:
            # Ta sama nazwa drugi raz to ten sam projekt (nie „foo-2”).
            project = next((p for p in board.projects.values() if p.name.casefold() == name.casefold()), None) \
                or board.projects.get(slugify(name))
        else:
            raise BoardError("projekt potrzebuje identyfikatora (slug), nazwy albo repozytorium")
        if project is None and slug and os.path.lexists(os.path.join(self.root, "projects", f"{slug}.md")):
            raise BoardError(f"plik projektu {slug}.md jest uszkodzony — popraw go ręcznie", "broken-file")
        if repo:
            other = board.project_by_repo(repo)
            if other is not None and (project is None or other.slug != project.slug):
                raise BoardError(f"repozytorium {repo} ma już projekt „{other.slug}”", "exists")
        change = Change()
        if project is None:
            name = name or (os.path.basename(repo) if repo else "") or slug
            slug = slug or self._free_slug(board, slugify(name))
            doc, created, before = _new_project_doc(name, repo or ""), True, ""
        else:
            doc, created, slug = project.doc, False, project.slug
            before = doc.text()
            if name is not None:
                _doc_set(doc, "name", name)
            if repo is not None:
                _doc_set(doc, "repo", repo)
        if repo == "" and doc.get("ai-docs").strip().lower() == "repo":
            doc.set("ai-docs", "tide")  # bez repozytorium dokumenty idą do tablicy
        for key, field_name in (("tags", "tags"), ("ai-docs", "aiDocs"), ("tool", "tool"), ("mode", "mode"),
                                ("entry", "entry")):
            if field_name in v:
                _doc_set(doc, key, ", ".join(v[field_name]) if field_name == "tags" else v[field_name])
        if ("entry" in v or "mode" in v) and doc.get("mode").strip().lower() == LEGACY_SPEC_MODE:
            doc.set("mode", DEFAULT_MODE)  # dawne `mode: spec` zastąpione wejściem (czyścimy tylko przy zapisie)
        if doc.get("ai-docs").strip().lower() == "repo" and not doc.get("repo").strip():
            raise BoardError("ai-docs „repo” wymaga repozytorium projektu")
        if "youtrack" in v:
            other = next((p for p in board.projects.values() if v["youtrack"] and p.youtrack == v["youtrack"]
                          and p.slug != slug), None)
            if other is not None:
                raise BoardError(f"klucz YouTracka {v['youtrack']} ma już projekt „{other.name}”", "exists")
            if v["youtrack"] or doc.has(PROJECT_YOUTRACK_KEY):
                _doc_set(doc, PROJECT_YOUTRACK_KEY, v["youtrack"])
        if "youtrackSync" in v and (doc.has(PROJECT_SYNC_KEY) or not v["youtrackSync"]):
            _doc_set(doc, PROJECT_SYNC_KEY, "on" if v["youtrackSync"] else "off")
        for field_name, key in (("allowedCommands", commands.ALLOWED_KEY),
                                ("deniedCommands", commands.DENIED_KEY)):
            if field_name in v:
                _doc_set(doc, key, json.dumps(v[field_name], ensure_ascii=False))
        if "assignmentRules" in v:
            _doc_set(doc, "assignment-rules", json.dumps(v["assignmentRules"], ensure_ascii=False))
        # Polityka review (PT-330): `free` bez klucza w pliku zostaje domyślną, nie dopisujemy jej.
        if "reviewIndependence" in v and (doc.has(REVIEW_POLICY_KEY) or v["reviewIndependence"] != "free"):
            _doc_set(doc, REVIEW_POLICY_KEY, v["reviewIndependence"])
        if "reviewIndependenceClasses" in v and (doc.has(REVIEW_CLASSES_KEY) or v["reviewIndependenceClasses"]):
            _doc_set(doc, REVIEW_CLASSES_KEY, ", ".join(v["reviewIndependenceClasses"]))
        if "readDirs" in v:
            from ..tasks import readaccess
            try:
                dirs, bindings = readaccess.normalize(v["readDirs"], writable=[doc.get("repo")],
                    bindings=project.read_dir_bindings if project else [])
            except ValueError as exc:
                raise BoardError(str(exc)) from None
            _doc_set(doc, readaccess.KEY, json.dumps(dirs, ensure_ascii=False))
            _doc_set(doc, readaccess.BINDINGS_KEY, json.dumps(bindings, ensure_ascii=False))
        if "description" in v and v["description"].strip() != doc.body.strip():
            doc.body = v["description"]
        writes = []
        contract = None
        if "tests" in v:  # kontrakt testów żyje w profilu `projects/<slug>/box.yaml`, nie we frontmatterze
            profile = project
            if profile is None:  # profil środowiska może istnieć jeszcze przed plikiem projektu
                profile = Project(slug=slug, name=name)
                self._load_project_tests(profile, board.errors)
            if profile.box_unreadable:
                raise BoardError(f"projects/{slug}/{project_tests.PROFILE_FILE}: profilu nie da się odczytać "
                                 "(zob. błędy Tablicy) — zapis kontraktu odrzucony, plik bez zmian")
            old_text = profile.box_text
            current = dict(profile.tests) if not profile.tests_error else project_tests.empty()
            contract = {**project_tests.empty(), **current, **v["tests"]}
            new_text = project_tests.save_tests(old_text, contract)
            if new_text != (old_text or ""):
                writes.append((os.path.join("projects", slug, project_tests.PROFILE_FILE), new_text))
        change.result = {"ok": True, "slug": slug, "changed": False}
        if contract is not None:  # dla approve_project_tests; upsert_project usuwa ten klucz z odpowiedzi
            change.result[_CONTRACT_KEY] = contract
        doc_changed = created or doc.text() != before
        if not doc_changed and not writes:
            return change
        change.result["changed"] = True
        if doc_changed:
            writes.insert(0, (os.path.join("projects", f"{slug}.md"), doc.text()))
        change.commits.append((f"project {slug}: {'create' if created else 'update'}", writes))
        return change

    def _plan_tag(self, board: Board, v: dict) -> Change:
        name = v["name"]
        tag = board.tags.get(name.casefold())
        defined = tag is not None and bool(tag.path)
        new_name = v.get("newName")
        change = Change()
        if new_name is not None:
            clash = board.tags.get(new_name.casefold())
            if clash is not None and clash.path and clash is not tag:
                raise BoardError(f"tag „{new_name}” już istnieje", "exists")
        if not defined:
            used = name.casefold() in {n.casefold() for n in board.used_tags()}
            if new_name is not None and not used:
                raise BoardError(f"nie ma tagu „{name}” do zmiany nazwy", "not-found")
            final = new_name or name
            color = v.get("color") or (tag.color if tag else fallback_color(final, self.colors))
            doc = Document(["name: " + final, "color: " + color], v.get("description", ""))
            writes = [(os.path.join("tags", f"{self._free_tag_slug(slugify(final, 'tag'))}.md"), doc.text())]
            if new_name is not None and new_name != name:
                writes += self._rename_tag_uses(board, name, new_name)
                message = f"tag {name}: rename to {new_name}"
            else:
                message = f"tag {final}: create"
            change.commits.append((message, writes))
            change.result = {"ok": True, "name": final, "changed": True}
            return change
        doc = tag.doc
        if doc is None:
            raise BoardError(f"plik tagu {tag.path} jest uszkodzony — popraw go ręcznie", "broken-file")
        before = doc.text()
        if "color" in v:
            _doc_set(doc, "color", v["color"])
        if "description" in v and v["description"].strip() != doc.body.strip():
            doc.body = v["description"]
        writes = []
        final = tag.name
        if new_name is not None and new_name != tag.name:
            doc.set("name", new_name)
            final = new_name
            writes += self._rename_tag_uses(board, tag.name, new_name)
        change.result = {"ok": True, "name": final, "changed": False}
        if doc.text() == before:
            return change
        writes.insert(0, (_rel(self.root, tag.path), doc.text()))
        message = f"tag {tag.name}: rename to {final}" if final != tag.name else f"tag {tag.name}: update"
        change.commits.append((message, writes))
        change.result["changed"] = True
        return change

    def link_session(self, card_id: str, session: str, how: str = "", branch: str = "") -> dict:
        """Trwałe połączenie karty z sesją agenta (PT-28): `session` karty dostaje id sesji, którą usługa
        rozpoznała (uruchomienie z karty albo pewne dopasowanie po gałęzi) — commit „K-1: link session
        claude:…”. Karta, która ma już `session` (np. ustawioną ręcznie), zostaje bez zmian. branch: gałąź
        ticketów, gdy karta obejmuje część koordynatora — trafia do `branch` karty, jeśli ta go nie ma."""
        session = _line(session, FIELD_MAX, "session")
        branch = _line(branch, FIELD_MAX, "branch")
        if not session:
            raise BoardError("brak id sesji do połączenia")
        num = card_num(card_id)
        return self._execute(lambda board: self._plan_link(board, num, session, how, branch))

    def _plan_link(self, board: Board, num: int, session: str, how: str, branch: str = "") -> Change:
        card = self._card(board, num)
        change = Change(result={"ok": True, "id": card.id, "changed": False, "session": card.session})
        if card.session:
            return change
        if branch and not card.branch:
            card.doc.set("branch", branch)
        card.doc.set("session", session)
        card.doc.set("updated", self._now())
        suffix = f" ({how})" if how else ""
        change.commits.append((f"{card.id}: link session {session}{suffix}",
                               [(_rel(self.root, card.path), card.doc.text())]))
        change.result.update(changed=True, session=session)
        return change

    def _extra_projects(self, board: Board, items: list, primary: str, change: Change) -> list[str]:
        """Dodatkowe projekty karty: slug istniejącego projektu albo ścieżka repozytorium (istniejący
        projekt tego repozytorium albo nowy — osobny commit przed commitem karty, jak `repo` w CreateCard).
        Bez głównego projektu i bez powtórzeń."""
        planned: dict[str, str] = {}   # repozytorium (realpath) → slug założony w tym zapisie
        for message, writes in change.commits:  # projekt założony już dla głównego `repo`
            for rel, text in writes:
                if rel.startswith("projects" + os.sep) and message.endswith(": create"):
                    repo = Document.parse(text).get("repo").strip()
                    if repo:
                        planned[os.path.realpath(repo)] = os.path.basename(rel)[:-3]
        out = []
        for item in items:
            if item.startswith("/"):
                project = board.project_by_repo(item)
                slug = project.slug if project else planned.get(os.path.realpath(item), "")
                if not slug:
                    slug = self._free_slug(board, slugify(os.path.basename(item)), taken=set(planned.values()))
                    planned[os.path.realpath(item)] = slug
                    change.commits.append((f"project {slug}: create", [(os.path.join("projects", f"{slug}.md"),
                                                                         _new_project_doc(os.path.basename(item),
                                                                                          item).text())]))
            else:
                if item not in board.projects:
                    raise BoardError(f"nie ma projektu „{item}”", "not-found")
                slug = item
            if slug != primary and slug not in out:
                out.append(slug)
        if len(out) > PROJECTS_MAX:
            raise BoardError(f"najwyżej {PROJECTS_MAX} dodatkowych projektów")
        return out

    def _project_slug(self, board: Board, v: dict, change: Change) -> str:
        """Projekt karty: `project` (slug istniejącego) albo `repo` (istniejący projekt repozytorium
        albo nowy — osobny commit przed commitem karty)."""
        if v.get("project"):
            if v["project"] not in board.projects:
                raise BoardError(f"nie ma projektu „{v['project']}”", "not-found")
            return v["project"]
        if not v.get("repo"):
            return ""
        project = board.project_by_repo(v["repo"])
        if project is not None:
            return project.slug
        name = os.path.basename(v["repo"])
        slug = self._free_slug(board, slugify(name))
        change.commits.append((f"project {slug}: create", [(os.path.join("projects", f"{slug}.md"),
                                                             _new_project_doc(name, v["repo"]).text())]))
        return slug

    # --- wnętrze -------------------------------------------------------------------------------

    def _load_cards(self, board: Board) -> None:
        folder = os.path.join(self.root, "cards")
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return
        for name in names:
            if not name.endswith(".md") or name.startswith("."):
                continue
            path = os.path.join(folder, name)
            match = CARD_FILE_RE.match(name)
            if not match:
                board.errors.append({"path": path, "message": f"{name}: nazwa pliku karty to K-<numer>.md — pomijam"})
                continue
            num = int(match.group(1))
            board.max_num = max(board.max_num, num)
            doc = self._read_doc(os.path.join("cards", name), board.errors)
            if doc is not None:
                board.cards[num] = parse_card(doc, path, num, board.errors)

    def _load_project_tests(self, project: Project, errors: list) -> None:
        """Kontrakt testów z `projects/<slug>/box.yaml`; uszkodzony blok — pusty kontrakt i błąd Tablicy."""
        rel = os.path.join("projects", project.slug, project_tests.PROFILE_FILE)
        project.box_text = self._read_file(rel, errors)
        project.box_unreadable = project.box_text is None and os.path.lexists(os.path.join(self.root, rel))
        project.tests, project.tests_error = project_tests.load_tests(project.box_text)
        project.tests_approved = False
        if project.tests_error:
            errors.append({"path": os.path.join(self.root, rel), "message": f"{rel}: {project.tests_error}"})
        elif not contract_approval.is_empty(project.tests):
            # Tylko odczyt magazynu: odcisk tworzy wyłącznie approve_project_tests (PT-285).
            project.tests_approved = contract_approval.approved(self.root, project.slug, project.tests,
                                                                self.approvals_path)
            if not project.tests_approved:
                errors.append({"path": os.path.join(self.root, rel), "message": f"{rel}: {TESTS_UNAPPROVED}"})

    def _docs(self, folder: str, errors: list):
        try:
            names = sorted(os.listdir(os.path.join(self.root, folder)))
        except OSError:
            return
        for name in names:
            if not name.endswith(".md") or name.startswith("."):
                continue
            slug = name[:-3]
            path = os.path.join(self.root, folder, name)
            if not SLUG_RE.match(slug):
                errors.append({"path": path, "message": f"{name}: nazwa pliku to małe litery, cyfry i . _ - — pomijam"})
                continue
            doc = self._read_doc(os.path.join(folder, name), errors)
            if doc is not None:
                yield slug, doc, path

    def _read_file(self, rel: str, errors: list) -> str | None:
        """Ograniczony odczyt: tylko zwykły plik w katalogu tablicy (także przez dowiązanie), najwyżej
        FILE_MAX bajtów, bez czekania na FIFO i urządzenia. Brak pliku — None bez błędu."""
        path = os.path.join(self.root, rel)
        if not os.path.lexists(path):
            return None
        if not _inside(path, self.root):
            errors.append({"path": path, "message": f"{rel}: dowiązanie wskazuje poza katalog tablicy — pomijam"})
            return None
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as exc:
            errors.append({"path": path, "message": f"{rel}: nie da się odczytać ({exc.strerror or exc})"})
            return None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                errors.append({"path": path, "message": f"{rel}: to nie jest zwykły plik — pomijam"})
                return None
            chunks, size = [], 0
            while size <= FILE_MAX:
                chunk = os.read(fd, FILE_MAX + 1 - size)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
        except OSError as exc:
            errors.append({"path": path, "message": f"{rel}: nie da się odczytać ({exc.strerror or exc})"})
            return None
        finally:
            os.close(fd)
        if size > FILE_MAX:
            errors.append({"path": path, "message": f"{rel}: plik większy niż {FILE_MAX // 1024} KB — pomijam"})
            return None
        try:
            return b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError:
            errors.append({"path": path, "message": f"{rel}: plik nie jest w UTF-8"})
            return None

    def _read_doc(self, rel: str, errors: list) -> Document | None:
        text = self._read_file(rel, errors)
        if text is None:
            return None
        path = os.path.join(self.root, rel)
        try:
            doc = Document.parse(text)
        except FrontmatterError as exc:
            errors.append({"path": path, "message": f"{rel}: {exc}"})
            return None
        errors.extend({"path": path, "message": f"{rel}: {problem}"} for problem in doc.problems)
        return doc

    def _card(self, board: Board, num: int) -> Card:
        card = board.cards.get(num)
        if card is None:
            if os.path.lexists(os.path.join(self.root, "cards", f"{CARD_PREFIX}{num}.md")):
                raise BoardError(f"plik karty {CARD_PREFIX}{num} jest uszkodzony — popraw go ręcznie", "broken-file")
            raise BoardError(f"nie ma karty {CARD_PREFIX}{num}", "not-found")
        return card

    def _next_num(self, board: Board, rebuild: bool = False) -> int:
        """Następny numer karty: większy od każdego, jaki był — w plikach, w liczniku `board.md`
        i w znaczniku poza historią (.git/tide-next-card, przeżywa revert). Brakujący znacznik
        odbudowujemy z historii gita tylko przy zakładaniu karty (rebuild), nigdy w migawce."""
        try:
            counter = int((board.meta.get("next-card") if board.meta else "") or 1)
        except ValueError:
            counter = 1
        if not self.repo.initialized():
            return max(board.max_num + 1, counter, 1)
        return max(board.max_num + 1, counter, self.repo.high_water(rebuild=rebuild), 1)

    def _rename_tag_uses(self, board: Board, old: str, new: str) -> list[tuple[str, str]]:
        writes = []
        for item in list(board.cards.values()) + list(board.projects.values()):
            if item.doc is None:
                continue
            names = split_tags(item.doc.get("tags"))
            if old.casefold() not in {n.casefold() for n in names}:
                continue
            renamed = split_tags([new if n.casefold() == old.casefold() else n for n in names])
            item.doc.set("tags", ", ".join(renamed))
            writes.append((_rel(self.root, item.path), item.doc.text()))
        return writes

    def _free_slug(self, board: Board, base: str, taken=()) -> str:
        slug, n = base, 2
        while slug in board.projects or slug in taken or \
                os.path.lexists(os.path.join(self.root, "projects", f"{slug}.md")):
            slug, n = f"{base}-{n}", n + 1
        return slug

    def _free_tag_slug(self, base: str) -> str:
        slug, n = base, 2
        while os.path.lexists(os.path.join(self.root, "tags", f"{slug}.md")):
            slug, n = f"{base}-{n}", n + 1
        return slug

    def _write(self, rel: str, text: str) -> str:
        """Zapis atomowy (plik tymczasowy w tym samym katalogu i rename). Zwraca ścieżkę względną."""
        path = os.path.normpath(os.path.join(self.root, rel))
        folder = os.path.dirname(path)
        if not _inside(folder, self.root) or not _inside(path, self.root):
            raise BoardError(f"ścieżka {rel} wychodzi poza katalog tablicy")
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, TMP_PREFIX + secrets.token_hex(6))
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        self.generation += 1        # przed podmianą: kontekst wczytany w trakcie zapisu też jest nieaktualny
        os.replace(tmp, path)
        return os.path.relpath(path, self.root)

    def _now(self) -> str:
        return _local_iso(self.clock())


def branch_slug(card: Card) -> str:
    """Część gałęzi bez narzędzia: `k-12-<opis-z-tytułu>` (w migawce `branchSlug` dla okna uruchomienia)."""
    return f"{card.id.lower()}-{slug_from_prompt(card.title, words=4, length=32)}"


def suggest_branch(card: Card, tool: str) -> str:
    """Gałąź dla pracy nad kartą: `<narzędzie>/k-12-<opis>` — po ID karty daemon połączy ją z koordynatorem."""
    return f"{tool or DEFAULT_TOOL}/{branch_slug(card)}"


def spec_name(card: Card) -> str:
    """Proponowana nazwa pliku specyfikacji: `k-12-<opis-z-tytułu>.md` (po ID karty ją rozpoznajemy)."""
    return f"{card.id.lower()}-{slug_from_prompt(card.title, words=4, length=32)}.md"


SPEC_READY = ("gotowa", "gotowy", "ready", "done")
SPEC_FILES = 400


def find_ready_spec(docs_dir: str, card_id: str) -> str:
    """Gotowa specyfikacja karty w `<docs_dir>/specs/`: plik Markdown, którego nazwa zaczyna się od ID
    karty (`k-12.md`, `k-12-…`) albo frontmatter ma `card: K-12`, ze `status: gotowa` (także gotowy,
    ready, done). Czytamy najwyżej SPEC_FILES plików po 64 KB. Brak — pusty napis."""
    folder = os.path.join(docs_dir, "specs")
    try:
        names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".md"))[:SPEC_FILES]
    except OSError:
        return ""
    key = card_id.lower()
    for name in names:
        path = os.path.join(folder, name)
        stem = name[:-3].lower()
        by_name = stem == key or stem.startswith(key + "-") or stem.startswith(key + "_")
        try:
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                head = fh.read(64 * 1024)
        except OSError:
            continue
        try:
            doc = Document.parse(head)
        except FrontmatterError:
            continue
        if not by_name and doc.get("card").strip().lower() != key:
            continue
        if doc.get("status").strip().lower() in SPEC_READY:
            return path
    return ""


def _card_json(card: Card, project: Project | None, resolved: list, derived, extra=()) -> dict:
    flow = (derived or {}).get("flow") or {}
    base = flow.get("base") or {"baseRef": card.base_ref, "baseBranch": card.base_branch,
                                "baseCommit": card.base_commit}
    return {
        "id": card.id, "num": card.num, "title": card.title, "project": card.project,
        "projectName": project.name if project else "", "tags": list(card.tags), "resolvedTags": resolved,
        "column": card.column,
        # Kolumna do wyświetlenia: z koordynatora (PT-5), a bez niego — zapisana ręcznie.
        "boardColumn": (derived or {}).get("column") or card.column,
        "order": card.order, "note": card.note, "created": card.created, "createdTs": parse_ts(card.created),
        "updated": card.updated, "updatedTs": parse_ts(card.updated),
        # Ostatnia zmiana kolumny; stare karty bez klucza — czas założenia.
        "moved": card.moved, "movedTs": parse_ts(card.moved or card.created), "movedFrom": card.moved_from,
        # Ostatnia prawdziwa zmiana kolumny (PT-7, synchronizacja z YouTrackiem); 0 — nigdy.
        "columnMoved": card.column_moved, "columnMovedTs": parse_ts(card.column_moved),
        "youtrack": card.youtrack,
        "baseRef": base["baseRef"], "baseBranch": base["baseBranch"], "baseCommit": base["baseCommit"],
        "baseVersion": flow.get("baseVersion", 0),
        "specRevision": {"repo": card.spec_revision_repo, "ref": card.spec_revision_ref,
                         "commit": card.spec_revision_commit} if card.spec_revision_ref else None,
        "spec": card.spec, "planPath": card.plan, "session": card.session, "branch": card.branch, "check": card.check,
        "entry": card.entry, "mode": card.mode, "tool": card.tool, "worktree": card.worktree, "launched": card.launched,
        "launchedTs": parse_ts(card.launched), "branchSlug": branch_slug(card),
        # Dodatkowe projekty (PT-28): slugi z pliku i rozwiązane projekty (nieznane slugi pomijamy — błąd w errors).
        "projects": list(card.projects),
        # Limit ticketów w toku (PT-375): nadpisanie karty (None — ustawienie domyślne); limit efektywny dokłada
        # usługa przepływu (`derived.flow.ticketLimit`), a bez niej — ustawienie domyślne.
        "maxTickets": card.max_tickets,
        "extraProjects": [{"slug": p.slug, "name": p.name, "repo": p.repo, "repoMissing": p.repo_missing}
                          for p in extra],
        "description": card.description,
        "sourceIssue": card.source_issue, "sourceIssueVersion": card.source_issue_version,
        "sourceIssueHash": card.source_issue_hash, "sourceIssueReport": card.source_issue_report,
        "sourceIssueManifest": card.source_issue_manifest,
        "derived": derived, "path": card.path,
    }


def _project_json(project: Project, docs_dir: str, cards: int) -> dict:
    return {"slug": project.slug, "name": project.name, "repo": project.repo, "repoMissing": project.repo_missing,
            "tags": list(project.tags), "aiDocs": project.ai_docs, "aiDocsDefault": not project.ai_docs_set,
            "tool": project.tool, "mode": project.mode, "entry": project.entry,
            "description": project.description,
            "docsDir": docs_dir, "cards": cards, "path": project.path,
            "youtrack": project.youtrack, "youtrackSync": bool(project.youtrack) and project.youtrack_sync,
            "allowedCommands": list(project.allowed_commands), "deniedCommands": list(project.denied_commands),
            "readDirs": list(project.read_dirs),
            "tests": dict(project.tests), "testsError": project.tests_error,
            "testsApproved": project.tests_approved,
            "testsGrantNote": {name: project_tests.grant_note(project.tests.get(name, ""))
                               for name in project_tests.FIELDS},
            "assignmentRules": project.assignment_rules,
            # Wartość jak w pliku (pusta → `free`); nieznaną pokazujemy, usługa i tak stosuje `free` (PT-330).
            "reviewIndependence": project.review_independence or "free",
            "reviewIndependenceClasses": list(project.review_independence_classes)}


def _column_index(column: str) -> int:
    ids = [c["id"] for c in COLUMNS]
    return ids.index(column) if column in ids else len(ids)


def _fields(data, allowed: tuple, hint: str = "") -> dict:
    if not isinstance(data, dict):
        raise BoardError("oczekiwano obiektu JSON")
    unknown = [key for key in data if key not in allowed]
    if unknown:
        extra = f" ({hint})" if hint and any(k in ("column", "order") for k in unknown) else ""
        raise BoardError(f"nieznane pole „{unknown[0]}”{extra}; dozwolone: {', '.join(allowed)}")
    return data


def _text(value, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise BoardError(f"pole „{label}” musi być napisem")
    return str(value)


def _line(value, limit: int, label: str) -> str:
    text = " ".join(_text(value, label).split())
    if len(text) > limit:
        raise BoardError(f"pole „{label}” jest za długie (najwyżej {limit} znaków)")
    if any(ord(ch) < 32 for ch in text):
        raise BoardError(f"pole „{label}” zawiera znaki sterujące")
    return text


def _title(value) -> str:
    title = _line(value, TITLE_MAX, "tytuł")
    if not title:
        raise BoardError("karta potrzebuje tytułu")
    return title


def _body(value) -> str:
    text = _text(value, "opis").replace("\r\n", "\n").replace("\r", "\n")
    if len(text.encode("utf-8")) > DESCRIPTION_MAX:
        raise BoardError(f"opis jest za długi (najwyżej {DESCRIPTION_MAX // 1024} KB)")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
        raise BoardError("opis zawiera znaki sterujące")
    text = text.strip("\n")
    return f"\n{text}\n" if text else ""


def _tag_name(value) -> str:
    name = _line(value, TAG_MAX, "tag")
    if not name:
        raise BoardError("tag potrzebuje nazwy")
    if "," in name:
        raise BoardError(f"nazwa tagu nie może zawierać przecinka („{name}”)")
    return name


def _tags(value) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        raise BoardError("tagi to lista napisów albo napis „a, b”")
    return split_tags([_tag_name(item) for item in items if str(item).strip()])


def _column(value) -> str:
    column = _text(value, "kolumna").strip().lower()
    if column not in MANUAL_COLUMNS:
        raise BoardError(f"do kolumny „{column}” nie przenosi się ręcznie (można: {', '.join(MANUAL_COLUMNS)})")
    return column


def _entry_mode_values(data: dict, allow_empty: bool) -> dict:
    """Wejście (`entry`) i tryb (`mode`: full, light) z żądania; tylko podane klucze. Dawne `mode: spec` to
    wejście Tylko specyfikacja: wpisujemy `entry: spec`, a trybu nie ruszamy (karta, allow_empty — czyścimy go,
    bo przy tym wejściu trybu nie ma). allow_empty: karta może nie mieć wejścia i trybu (nie była uruchamiana)."""
    out = {}
    empty = ("",) if allow_empty else ()
    if "entry" in data:
        text = _text(data["entry"], "entry").strip().lower()
        if text not in (*empty, *ENTRIES):
            raise BoardError(f"nieznane wejście „{text}” ({', '.join(ENTRIES)})")
        out["entry"] = text
    if "mode" in data:
        text = _text(data["mode"], "mode").strip().lower()
        if text == LEGACY_SPEC_MODE:
            if out.get("entry", "spec") != "spec":
                raise BoardError(f"tryb „spec” to wejście Tylko specyfikacja, a wejście to „{out['entry']}”")
            out["entry"] = "spec"
            if allow_empty:
                out["mode"] = ""
        elif text in (*empty, *MODES):
            out["mode"] = text
            if text and "entry" not in out:
                out["entry"] = "planning"  # sam tryb Pełny/Lekki to Planowanie (jak przed PT-77)
        else:
            raise BoardError(f"nieznany tryb „{text}” ({', '.join(MODES)})")
    return out


def _launch_entry_mode(data: dict, project: Project | None) -> tuple[str, str]:
    """(wejście, tryb) uruchomienia: z żądania, a brakujące z projektu. Tryb dotyczy tylko wejść bez własnego
    szablonu (Planowanie); przy Tylko specyfikacja jest pusty, nawet gdy okno go przysłało."""
    values = _entry_mode_values({k: data[k] for k in ("entry", "mode") if k in data}, allow_empty=True)
    entry = values.get("entry") or (project.entry if project else DEFAULT_ENTRY)
    if entry in flows.ENTRY_FLOWS:
        return entry, ""
    return entry, values.get("mode") or (project.mode if project else DEFAULT_MODE)


def _enum(value, allowed: tuple, label: str) -> str:
    text = _text(value, label).strip().lower()
    if text not in allowed:
        raise BoardError(f"nieznana wartość {label} „{text}” (dozwolone: {', '.join(allowed)})")
    return text


def git_repo_path(value) -> str:
    """Bezwzględna ścieżka istniejącego katalogu Git (także worktree: `.git` jako plik); inaczej BoardError.
    Wspólna walidacja `repo` projektu (`_repo`) i podpowiedzi kontraktu testów (SuggestProjectTests)."""
    path = _text(value, "repo").strip()
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        raise BoardError(f"repozytorium musi być ścieżką bezwzględną („{path}”)")
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        raise BoardError(f"nie ma katalogu {path}")
    if not os.path.exists(os.path.join(path, ".git")):
        raise BoardError(f"{path} nie jest repozytorium git")
    return path


def _repo(value, board_root: str = "") -> str:
    path = git_repo_path(value)
    if (board_root and os.path.realpath(path) == os.path.realpath(board_root)) or is_board_repo(path):
        raise BoardError("repozytorium tablicy nie może być projektem")
    return path


def _card_values(data: dict, create: bool = False) -> dict:
    """Pola karty z żądania po walidacji (tylko podane; przy tworzeniu z wartościami domyślnymi)."""
    out = {}
    if create or "title" in data:
        out["title"] = _title(data.get("title"))
    if create:
        out["column"] = _column(data.get("column", DEFAULT_COLUMN))
    if "tags" in data:
        out["tags"] = _tags(data["tags"])
    if "note" in data:
        out["note"] = _line(data["note"], NOTE_MAX, "notatka")
    for key in RESERVED_CARD_KEYS:
        if key in data:
            out[key] = _line(data[key], PATH_MAX if key == "spec" else FIELD_MAX, key)
    if out.get("youtrack"):
        key = issue_key(out["youtrack"])   # klucz albo adres …/issue/LEGAL-35 → „LEGAL-35” (PT-7)
        if not key:
            raise BoardError(f"pole youtrack to klucz zgłoszenia, np. LEGAL-35 („{out['youtrack']}”)")
        out["youtrack"] = key
    if "planPath" in data:
        path = _line(data["planPath"], PATH_MAX, "planPath")
        if path and (not os.path.isabs(path) or not path.lower().endswith(".md")):
            raise BoardError("planPath musi być bezwzględną ścieżką do pliku Markdown")
        out["planPath"] = path
    if "check" in data:
        out["check"] = _enum(data["check"], CHECKS, "sprawdzenie")
    if "maxTickets" in data:
        out["maxTickets"] = _max_tickets(data["maxTickets"])
    out.update(_entry_mode_values(data, allow_empty=True))
    if "tool" in data:
        out["tool"] = _enum(data["tool"], ("", *TOOLS), "tool") if _text(data["tool"], "tool").strip() else ""
    if "worktree" in data:
        worktree = _line(data["worktree"], PATH_MAX, "worktree")
        if worktree and not os.path.isabs(worktree):
            raise BoardError(f"worktree musi być ścieżką bezwzględną („{worktree}”)")
        out["worktree"] = os.path.normpath(worktree) if worktree else ""
    if "launched" in data:
        launched = _line(data["launched"], FIELD_MAX, "launched")
        if launched and not parse_ts(launched):
            raise BoardError(f"launched musi być czasem ISO 8601 („{launched}”)")
        out["launched"] = launched
    if "description" in data:
        out["description"] = _body(data["description"])
    if "project" in data:
        slug = _text(data["project"], "project").strip()
        if slug and not SLUG_RE.match(slug):
            raise BoardError(f"niepoprawny identyfikator projektu „{slug}”")
        out["project"] = slug
    if "repo" in data:
        out["repo"] = _repo(data["repo"]) if _text(data["repo"], "repo").strip() else ""
    if "projects" in data:
        out["projects"] = _projects(data["projects"])
    return out


def _max_tickets(value) -> int | None:
    """`maxTickets` (PT-375): liczba 1..6 (także napis z cyframi); pusta wartość (None, "") — brak nadpisania."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str) and value.strip().isdecimal():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 6:
        raise BoardError("maxTickets musi być liczbą całkowitą od 1 do 6 (pusta wartość — ustawienie domyślne)")
    return value


def _projects(value) -> list[str]:
    """Dodatkowe projekty z żądania: lista (albo napis „a, b”) slugów albo ścieżek repozytoriów."""
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [_text(item, "projects").strip() for item in value]
    else:
        raise BoardError("dodatkowe projekty to lista slugów albo ścieżek repozytoriów")
    out = []
    for item in items:
        if not item:
            continue
        if item.startswith("/") or item.startswith("~"):
            item = _repo(item)
        elif not SLUG_RE.match(item):
            raise BoardError(f"niepoprawny identyfikator projektu „{item}”")
        if item not in out:
            out.append(item)
    if len(out) > PROJECTS_MAX + 1:  # +1: główny projekt na liście wypada później
        raise BoardError(f"najwyżej {PROJECTS_MAX} dodatkowych projektów")
    return out


def _project_values(data: dict, board_root: str) -> dict:
    out = {}
    if "readDirs" in data:
        from ..tasks import readaccess
        try:
            out["readDirs"] = readaccess.paths(data["readDirs"])
        except ValueError as exc:
            raise BoardError(str(exc)) from None
    for key in ("assignmentRules", "globalAssignmentRules"):
        if key in data:
            value = _body(data[key]).strip()
            if len(value) > 8000:
                raise BoardError("zasady przydziału mogą mieć najwyżej 8000 znaków")
            out[key] = value
    if "globalAssignmentRules" in out:
        if len(data) != 1:
            raise BoardError("wspólne zasady zapisuje się oddzielnie od projektu")
        return out
    if "slug" in data:
        slug = _text(data["slug"], "slug").strip()
        if slug and not SLUG_RE.match(slug):
            raise BoardError(f"niepoprawny identyfikator projektu „{slug}” (małe litery, cyfry, . _ -)")
        out["slug"] = slug
    if "name" in data:
        name = _line(data["name"], NAME_MAX, "nazwa")
        if not name:
            raise BoardError("nazwa projektu nie może być pusta")
        out["name"] = name
    if "repo" in data:
        out["repo"] = _repo(data["repo"], board_root) if _text(data["repo"], "repo").strip() else ""
    if "tags" in data:
        out["tags"] = _tags(data["tags"])
    if "aiDocs" in data:
        out["aiDocs"] = _enum(data["aiDocs"], AI_DOCS, "ai-docs")
    if "tool" in data:
        out["tool"] = _enum(data["tool"], TOOLS, "narzędzie")
    out.update(_entry_mode_values(data, allow_empty=False))
    if "description" in data:
        out["description"] = _body(data["description"])
    if "youtrack" in data:
        key = _line(data["youtrack"], 20, "youtrack").upper()
        if key and not YOUTRACK_PROJECT_RE.match(key):
            raise BoardError(f"klucz projektu YouTracka to wielkie litery i cyfry, np. LOG („{key}”)")
        out["youtrack"] = key
    if "youtrackSync" in data:
        if not isinstance(data["youtrackSync"], bool):
            raise BoardError("youtrackSync to true albo false")
        out["youtrackSync"] = data["youtrackSync"]
    if "reviewIndependence" in data:
        out["reviewIndependence"] = _enum(data["reviewIndependence"], REVIEW_POLICIES, "reviewIndependence")
    if "reviewIndependenceClasses" in data:
        value = data["reviewIndependenceClasses"]
        if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
            raise BoardError("reviewIndependenceClasses to lista klas, np. [\"mocny\"]")
        classes = []
        for item in value:
            klass = item.strip().lower()
            if klass not in REVIEW_CLASSES:
                raise BoardError(f"nieznana klasa „{klass}” w reviewIndependenceClasses "
                                 f"({', '.join(REVIEW_CLASSES)})")
            if klass not in classes:
                classes.append(klass)
        out["reviewIndependenceClasses"] = classes
    for field_name, label in (("allowedCommands", commands.ALLOWED_KEY),
                              ("deniedCommands", commands.DENIED_KEY)):
        if field_name in data:
            try:
                out[field_name] = commands.validate(data[field_name], label)
            except ValueError as exc:
                raise BoardError(str(exc)) from None
    if "tests" in data:
        try:
            out["tests"] = project_tests.validate_tests(data["tests"])
        except ValueError as exc:
            raise BoardError(str(exc)) from None
    if not (out.get("slug") or out.get("name") or out.get("repo")):
        raise BoardError("projekt potrzebuje identyfikatora (slug), nazwy albo repozytorium")
    return out


def _tag_values(data: dict, colors: dict) -> dict:
    out = {"name": _tag_name(data.get("name"))}
    if "newName" in data:
        out["newName"] = _tag_name(data["newName"])
    if "color" in data:
        out["color"] = _enum(data["color"], tuple(colors), "kolor")
    if "description" in data:
        out["description"] = _body(data["description"])
    return out


def _new_project_doc(name: str, repo: str) -> Document:
    doc = Document()
    doc.set("name", name)
    doc.set("repo", repo)
    doc.set("tags", "")
    doc.set("ai-docs", default_ai_docs(repo))
    doc.set("tool", DEFAULT_TOOL)
    doc.set("mode", DEFAULT_MODE)
    return doc


def _doc_same(doc: Document, key: str, value) -> bool:
    return doc.get(key) == ("" if value is None else str(value)) and doc.has(key)


def _doc_set(doc: Document, key: str, value) -> list[str]:
    if _doc_same(doc, key, value):
        return []
    doc.set(key, value)
    return [key]


def _inside(path: str, root: str) -> bool:
    real_root = os.path.realpath(root)
    real = os.path.realpath(path)
    return real == real_root or real.startswith(real_root + os.sep)


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root)


def _local_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")

