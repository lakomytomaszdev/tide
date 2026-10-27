"""Repozytorium tablicy na dysku: odczyt, zapis z walidacją i commit każdej zmiany.

Układ (opis: docs/tide-daemon.md, Tablica):

  board.md                 znacznik `tide-board: 1` i licznik `next-card` (ID nie wracają)
  projects/<slug>.md       name, repo, tags, ai-docs, tool, mode; treść — opis projektu
  tags/<slug>.md           name, color (nazwa pastelu z palety); treść — opis tagu
  cards/K-<n>.md           id, title, project, tags, column, order, note, created, updated,
                           youtrack, spec, session, branch; treść — opis karty (Markdown)
  flows/<tryb>.md          szablony poleceń trybów (flows.py), flows/<projekt>/<tryb>.md — nadpisanie
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
import secrets
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime

from ..agents.timeutil import parse_ts
from ..worktrees.git import Git
from ..worktrees.naming import slug_from_prompt
from . import flows
from .frontmatter import Document, FrontmatterError
from .marker import FORMAT_VERSION, MARKER_FILE, MARKER_KEY, is_board_repo
from .repo import BoardRepo
from .model import (AI_DOCS, CARD_FILE_RE, CARD_KEYS, CARD_PREFIX, COLUMNS, DEFAULT_COLUMN, DEFAULT_MODE,
                    DEFAULT_TOOL, DESCRIPTION_MAX, FIELD_MAX, FILE_MAX, MANUAL_COLUMNS, MODES, NAME_MAX, NOTE_MAX,
                    PASTELS, RESERVED_CARD_KEYS, SEED_TAGS, SLUG_RE, TAG_MAX, TITLE_MAX,
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
---
# Tablica Tide

Lokalne repozytorium tablicy pracy Tide. Zmiany robi tide-daemon (okno Tide, D-Bus `/Board`)
i każdą od razu commituje; ręczne edycje plików też są w porządku — usługa zbierze je osobnym
commitem „sync external changes”. Bez remote: nic stąd nie wychodzi.

- `projects/<projekt>.md` — repozytorium, tagi, narzędzie, tryb i miejsce dokumentów AI,
- `tags/<tag>.md` — nazwa i kolor tagu,
- `cards/K-<numer>.md` — karty („rzeczy do zrobienia”), treść to opis,
- `flows/<tryb>.md` — szablony poleceń trybów full, light i spec,
- `docs/<projekt>/` — plany i specyfikacje agentów projektów z `ai-docs: tide`.
"""
CARD_FIELDS = ("title", "description", "project", "repo", "tags", "note", *RESERVED_CARD_KEYS)
CREATE_FIELDS = CARD_FIELDS + ("column",)
PROJECT_FIELDS = ("slug", "name", "repo", "tags", "aiDocs", "tool", "mode", "description")
TAG_FIELDS = ("name", "newName", "color", "description")


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


class BoardStore:
    def __init__(self, root: str, git: Git | None = None, clock=time.time, colors: dict | None = None):
        self.root = os.path.abspath(os.path.expanduser(root))
        self.repo = BoardRepo(self.root, git)
        self.git = self.repo.git
        self.clock = clock
        # nazwa pastelu → kolor (#rrggbb) z palety; bez palety same nazwy bez kolorów
        self.colors = dict(colors) if colors else {name: "" for name in PASTELS}

    # --- odczyt --------------------------------------------------------------------------------

    def exists(self) -> bool:
        return is_board_repo(self.root)

    def load(self) -> Board:
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
            board.projects[slug] = parse_project(doc, path, slug, board.errors)
        self._load_cards(board)
        for card in board.cards.values():
            if card.project and card.project not in board.projects:
                board.errors.append({"path": card.path, "message": f"{card.id}: nieznany projekt „{card.project}”"})
        return board

    def snapshot(self, repos=None, derive=None) -> dict:
        """Migawka dla D-Bus i --print-board. repos: ścieżki repozytoriów z katalogu projektów
        (do listy „dodaj jako projekt”); derive(card) → dict|None: stan z koordynatora (PT-5)."""
        board = self.load()
        tags = dict(board.tags)
        projects = sorted(board.projects.values(), key=lambda p: p.name.casefold())
        counts: dict[str, int] = {}
        cards_out = []
        for card in sorted(board.cards.values(), key=lambda c: (_column_index(c.column), c.order, c.num)):
            project = board.projects.get(card.project)
            resolved = merge_tags(project.tags if project else [], card.tags)
            for name in resolved:
                counts[name.casefold()] = counts.get(name.casefold(), 0) + 1
            derived = None
            if derive is not None:
                try:
                    derived = derive(_card_json(card, project, resolved, None))
                except Exception as exc:  # stan z koordynatora to dodatek — nie może zepsuć tablicy
                    board.errors.append({"path": card.path, "message": f"{card.id}: stan koordynatora: {exc}"})
            cards_out.append(_card_json(card, project, resolved, derived))
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
        if busy:
            board.errors.insert(0, {"path": self.root, "message": self.repo.busy_message(busy)})
        if self.repo.pending:
            board.errors.insert(0, {"path": self.root, "message": "zmiany są zapisane w plikach, ale czekają na "
                                                                  f"commit ({self.repo.last_error})"})
        last = self.repo.last_commit() if exists else None
        return {
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
            "tags": [{"name": t.name, "slug": t.slug, "color": t.color, "description": t.description,
                      "defined": t.defined, "cards": counts.get(key, 0)}
                     for key, t in sorted(tags.items(), key=lambda kv: kv[1].name.casefold())],
            "projects": [_project_json(p, self.docs_dir(p), project_cards.get(p.slug, 0)) for p in projects],
            "cards": cards_out,
            "repos": available,
            "errors": board.errors,
        }

    def docs_dir(self, project: Project | None) -> str:
        """Gdzie agenci karty piszą plany i specyfikacje: docs/agents w repozytorium (`ai-docs: repo`)
        albo docs/<projekt>/ w tablicy (`tide`, także karta bez projektu: docs/_inbox)."""
        if project is None:
            return os.path.join(self.root, "docs", "_inbox")
        if project.ai_docs == "repo" and project.repo:
            return os.path.join(project.repo, "docs", "agents")
        return os.path.join(self.root, "docs", project.slug)

    def render_flow(self, card_id: str, mode: str = "") -> dict:
        """Polecenie trybu dla karty (szablon z flows/ albo wbudowany) i dane uruchomienia. Katalog
        dokumentów w tablicy (`ai-docs: tide`) zakładamy od razu — agent dostaje go przez --add-dir."""
        board = self.load()
        card = self._card(board, card_num(card_id))
        project = board.projects.get(card.project)
        mode = (mode or "").strip().lower() or (project.mode if project else DEFAULT_MODE)
        if mode not in MODES:
            raise BoardError(f"nieznany tryb „{mode}” (full, light, spec)")
        tool = project.tool if project else DEFAULT_TOOL
        branch = card.branch or suggest_branch(card, tool)
        template, source = None, "builtin"
        candidates = ([os.path.join("flows", project.slug, f"{mode}.md")] if project else []) + \
            [os.path.join("flows", f"{mode}.md")]
        for rel in candidates:
            text = self._read_file(rel, [])
            if text is not None:
                template, source = text, os.path.join(self.root, rel)
                break
        if template is None:
            template = flows.SEED_FLOWS[mode]
        docs = self.docs_dir(project)
        if _inside(docs, self.root):
            os.makedirs(docs, exist_ok=True)  # pusty katalog nie trafia do gita; pliki agenta zbierze sync
        values = {"card_id": card.id, "title": card.title, "description": card.description.strip(),
                  "project": project.name if project else "", "repo": project.repo if project else "",
                  "docs_dir": docs, "branch": branch}
        return {"ok": True, "id": card.id, "mode": mode, "tool": tool, "branch": branch, "docsDir": docs,
                "aiDocs": project.ai_docs if project else "tide", "repo": values["repo"],
                "project": card.project, "template": source, "text": flows.render(template, values)}

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

    def move_card(self, card_id: str, column: str, index: int = -1) -> dict:
        """Przenosi kartę na pozycję `index` (od 0) kolumny; -1 albo poza zakresem — na koniec."""
        column = _column(column)
        num = card_num(card_id)
        if isinstance(index, bool) or not isinstance(index, (int, float)) or index != int(index):
            raise BoardError("kolejność musi być liczbą całkowitą (pozycja w kolumnie od 0)")
        return self._execute(lambda board: self._plan_move(board, num, column, int(index)))

    def upsert_project(self, data: dict) -> dict:
        values = _project_values(_fields(data, PROJECT_FIELDS), self.root)
        return self._execute(lambda board: self._plan_project(board, values))

    def upsert_tag(self, data: dict) -> dict:
        values = _tag_values(_fields(data, TAG_FIELDS), self.colors)
        return self._execute(lambda board: self._plan_tag(board, values))

    def ensure(self) -> None:
        """Zakłada repozytorium tablicy, jeśli go nie ma. Cudzego katalogu nie dotyka."""
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
        return self.repo.sync(message)

    # --- plany zapisu --------------------------------------------------------------------------

    def _execute(self, planner) -> dict:
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
        num = self._next_num(board)
        while os.path.lexists(os.path.join(self.root, "cards", f"{CARD_PREFIX}{num}.md")):
            num += 1
        now = self._now()
        column_cards = board.column_cards(v["column"])
        order = (column_cards[0].order - ORDER_STEP) if column_cards else ORDER_STEP  # nowa na górę
        doc = Document()
        values = {"id": f"{CARD_PREFIX}{num}", "title": v["title"], "project": slug, "tags": ", ".join(v.get("tags", [])),
                  "column": v["column"], "order": order_value(order), "note": v.get("note", ""),
                  "created": now, "updated": now}
        for key in RESERVED_CARD_KEYS:
            values[key] = v.get(key, "")
        for key in CARD_KEYS:
            doc.set(key, values[key])
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
        if "project" in v or "repo" in v:
            changed += _doc_set(card.doc, "project", self._project_slug(board, v, change))
        for key in ("title", "tags", "note", *RESERVED_CARD_KEYS):
            if key in v:
                changed += _doc_set(card.doc, key, ", ".join(v[key]) if key == "tags" else v[key])
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

    def _plan_move(self, board: Board, num: int, column: str, index: int) -> Change:
        """Nowa kolejność to środek między sąsiadami; całą kolumnę numerujemy co ORDER_STEP tylko
        wtedy, gdy między nimi nie ma miejsca — zwykle zmienia się jeden plik."""
        card = self._card(board, num)
        change = Change(result={"ok": True, "id": card.id, "changed": False})
        others = board.column_cards(column, skip=card.num)
        if index < 0 or index > len(others):
            index = len(others)
        moved_column = card.column != column
        current = sum(1 for c in others if (c.order, c.num) < (card.order, card.num))
        if not moved_column and current == index:
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
        now = self._now()
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
        writes.insert(0, (_rel(self.root, card.path), card.doc.text()))
        verb = f"move to {column}" if moved_column else f"reorder in {column}"
        change.commits.append((f"{card.id}: {verb}", writes))
        change.result["changed"] = True
        return change

    def _plan_project(self, board: Board, v: dict) -> Change:
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
        for key, field_name in (("tags", "tags"), ("ai-docs", "aiDocs"), ("tool", "tool"), ("mode", "mode")):
            if field_name in v:
                _doc_set(doc, key, ", ".join(v[field_name]) if field_name == "tags" else v[field_name])
        if doc.get("ai-docs").strip().lower() == "repo" and not doc.get("repo").strip():
            raise BoardError("ai-docs „repo” wymaga repozytorium projektu")
        if "description" in v and v["description"].strip() != doc.body.strip():
            doc.body = v["description"]
        change.result = {"ok": True, "slug": slug, "changed": False}
        if not created and doc.text() == before:
            return change
        change.result["changed"] = True
        change.commits.append((f"project {slug}: {'create' if created else 'update'}",
                               [(os.path.join("projects", f"{slug}.md"), doc.text())]))
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

    def _next_num(self, board: Board) -> int:
        """Następny numer karty: większy od każdego, jaki był — w plikach, w liczniku `board.md`,
        w znaczniku poza historią (.git/tide-next-card) i w historii gita (także po revercie)."""
        try:
            counter = int((board.meta.get("next-card") if board.meta else "") or 1)
        except ValueError:
            counter = 1
        if not self.repo.initialized():
            return max(board.max_num + 1, counter, 1)
        return max(board.max_num + 1, counter, self.repo.high_water(), self.repo.history_max() + 1, 1)

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

    def _free_slug(self, board: Board, base: str) -> str:
        slug, n = base, 2
        while slug in board.projects or os.path.lexists(os.path.join(self.root, "projects", f"{slug}.md")):
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
        os.replace(tmp, path)
        return os.path.relpath(path, self.root)

    def _now(self) -> str:
        return _local_iso(self.clock())


def suggest_branch(card: Card, tool: str) -> str:
    """Gałąź dla pracy nad kartą: `<narzędzie>/k-12-<opis>` — po ID karty daemon połączy ją z koordynatorem."""
    return f"{tool or DEFAULT_TOOL}/{card.id.lower()}-{slug_from_prompt(card.title, words=4, length=32)}"


def _card_json(card: Card, project: Project | None, resolved: list, derived) -> dict:
    return {
        "id": card.id, "num": card.num, "title": card.title, "project": card.project,
        "projectName": project.name if project else "", "tags": list(card.tags), "resolvedTags": resolved,
        "column": card.column,
        # Kolumna do wyświetlenia: z koordynatora (PT-5), a bez niego — zapisana ręcznie.
        "boardColumn": (derived or {}).get("column") or card.column,
        "order": card.order, "note": card.note, "created": card.created, "createdTs": parse_ts(card.created),
        "updated": card.updated, "updatedTs": parse_ts(card.updated), "youtrack": card.youtrack,
        "spec": card.spec, "session": card.session, "branch": card.branch, "description": card.description,
        "derived": derived, "path": card.path,
    }


def _project_json(project: Project, docs_dir: str, cards: int) -> dict:
    return {"slug": project.slug, "name": project.name, "repo": project.repo, "repoMissing": project.repo_missing,
            "tags": list(project.tags), "aiDocs": project.ai_docs, "aiDocsDefault": not project.ai_docs_set,
            "tool": project.tool, "mode": project.mode, "description": project.description,
            "docsDir": docs_dir, "cards": cards, "path": project.path}


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


def _enum(value, allowed: tuple, label: str) -> str:
    text = _text(value, label).strip().lower()
    if text not in allowed:
        raise BoardError(f"nieznana wartość {label} „{text}” (dozwolone: {', '.join(allowed)})")
    return text


def _repo(value, board_root: str = "") -> str:
    path = _text(value, "repo").strip()
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        raise BoardError(f"repozytorium musi być ścieżką bezwzględną („{path}”)")
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        raise BoardError(f"nie ma katalogu {path}")
    if not os.path.exists(os.path.join(path, ".git")):
        raise BoardError(f"{path} nie jest repozytorium git")
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
            out[key] = _line(data[key], FIELD_MAX, key)
    if "description" in data:
        out["description"] = _body(data["description"])
    if "project" in data:
        slug = _text(data["project"], "project").strip()
        if slug and not SLUG_RE.match(slug):
            raise BoardError(f"niepoprawny identyfikator projektu „{slug}”")
        out["project"] = slug
    if "repo" in data:
        out["repo"] = _repo(data["repo"]) if _text(data["repo"], "repo").strip() else ""
    return out


def _project_values(data: dict, board_root: str) -> dict:
    out = {}
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
    if "mode" in data:
        out["mode"] = _enum(data["mode"], MODES, "tryb")
    if "description" in data:
        out["description"] = _body(data["description"])
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


def _doc_set(doc: Document, key: str, value) -> list[str]:
    if doc.get(key) == ("" if value is None else str(value)) and doc.has(key):
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

