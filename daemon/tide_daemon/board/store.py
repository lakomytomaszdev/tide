"""Repozytorium tablicy na dysku: odczyt, zapis z walidacją i commit każdej zmiany.

Układ (opis: docs/tide-daemon.md, Tablica):

  board.md                 znacznik `tide-board: 1` i licznik `next-card` (ID nie wracają)
  projects/<slug>.md       name, repo, tags, ai-docs, tool, mode; treść — opis projektu
  tags/<slug>.md           name, color (nazwa pastelu z palety); treść — opis tagu
  cards/K-<n>.md           id, title, project, tags, column, order, note, created, updated,
                           youtrack, spec, session, branch; treść — opis karty (Markdown)
  flows/<tryb>.md          szablony poleceń trybów (flows.py), flows/<projekt>/<tryb>.md — nadpisanie
  docs/<projekt>/          plany i specyfikacje agentów projektów z `ai-docs: tide`

Repozytorium powstaje przy pierwszym zapisie. Każda nasza zmiana to osobny commit z autorem
z konfiguracji gita użytkownika; commitujemy tylko pliki, które zmieniliśmy — zmiany z zewnątrz
(agent w docs/, ręczna edycja) zbiera `sync()` osobnym commitem. Nigdy push, nigdy remote.

Wszystkie ścieżki zapisu powstają z ID karty i slugów po walidacji, a dodatkowo sprawdzamy,
że po rozwinięciu dowiązań leżą w katalogu tablicy.
"""
import os
import secrets
import time
from datetime import datetime

from ..agents.timeutil import parse_ts
from ..worktrees.git import Git
from ..worktrees.naming import slug_from_prompt
from . import flows
from .frontmatter import Document, FrontmatterError
from .marker import FORMAT_VERSION, MARKER_FILE, MARKER_KEY, is_board_repo
from .model import (AI_DOCS, CARD_FILE_RE, CARD_KEYS, CARD_PREFIX, COLUMNS, DEFAULT_COLUMN, DEFAULT_MODE,
                    DEFAULT_TOOL, DESCRIPTION_MAX, FIELD_MAX, FILE_MAX, MANUAL_COLUMNS, MODES, NAME_MAX, NOTE_MAX,
                    PASTELS, PROJECT_KEYS, RESERVED_CARD_KEYS, SEED_TAGS, SLUG_RE, TAG_KEYS, TAG_MAX, TITLE_MAX,
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

    def column_cards(self, column: str, skip: int = 0) -> list[Card]:
        cards = [c for c in self.cards.values() if c.column == column and c.num != skip]
        return sorted(cards, key=lambda c: (c.order, c.num))


class BoardStore:
    def __init__(self, root: str, git: Git | None = None, clock=time.time, colors: dict | None = None):
        self.root = os.path.abspath(os.path.expanduser(root))
        self.git = git or Git(timeout=20)
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
        for project in projects:
            for name in project.tags:
                tags.setdefault(name.casefold(), Tag(name, fallback_color(name, self.colors), slug=slugify(name, "tag"),
                                                     defined=False))
        for card in board.cards.values():
            for name in card.tags:
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
        return {
            "boardDir": self.root,
            "exists": board.meta is not None,
            "lastCommit": self.last_commit() if board.meta is not None else None,
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

    def last_commit(self) -> dict | None:
        res = self.git.run(self.root, "log", "-1", "--format=%H%x00%ct%x00%s")
        if not res.ok or not res.out.strip():
            return None
        sha, ts, subject = (res.out.strip().split("\0") + ["", ""])[:3]
        stamp = int(ts) if ts.isdigit() else 0
        return {"hash": sha, "short": sha[:7], "ts": stamp, "time": _local_iso(stamp) if stamp else "",
                "subject": subject}

    def docs_dir(self, project: Project | None) -> str:
        """Gdzie agenci karty piszą plany i specyfikacje: docs/agents w repozytorium (`ai-docs: repo`)
        albo docs/<projekt>/ w tablicy (`tide`, także karta bez projektu: docs/_inbox)."""
        if project is None:
            return os.path.join(self.root, "docs", "_inbox")
        if project.ai_docs == "repo" and project.repo:
            return os.path.join(project.repo, "docs", "agents")
        return os.path.join(self.root, "docs", project.slug)

    def render_flow(self, card_id: str, mode: str = "") -> dict:
        """Polecenie trybu dla karty (szablon z flows/ albo wbudowany) i dane uruchomienia."""
        board = self.load()
        card = self._card(board, card_id)
        project = board.projects.get(card.project)
        mode = (mode or "").strip().lower() or (project.mode if project else DEFAULT_MODE)
        if mode not in MODES:
            raise BoardError(f"nieznany tryb „{mode}” (full, light, spec)")
        tool = project.tool if project else DEFAULT_TOOL
        branch = card.branch or suggest_branch(card, tool)
        template, source = None, "builtin"
        candidates = ([os.path.join("flows", project.slug, f"{mode}.md")] if project else []) + \
            [os.path.join("flows", f"{mode}.md")]
        if board.meta is not None:
            for rel in candidates:
                text = self._read_text(rel)
                if text is not None:
                    template, source = text, os.path.join(self.root, rel)
                    break
        if template is None:
            template = flows.SEED_FLOWS[mode]
        docs = self.docs_dir(project)
        values = {"card_id": card.id, "title": card.title, "description": card.description.strip(),
                  "project": project.name if project else "", "repo": project.repo if project else "",
                  "docs_dir": docs, "branch": branch}
        return {"id": card.id, "mode": mode, "tool": tool, "branch": branch, "docsDir": docs,
                "aiDocs": project.ai_docs if project else "tide", "repo": values["repo"],
                "project": card.project, "template": source, "text": flows.render(template, values)}

    # --- zapis ---------------------------------------------------------------------------------

    def ensure(self) -> None:
        """Zakłada repozytorium tablicy, jeśli go nie ma. Cudzego katalogu nie dotyka."""
        if self.exists():
            if not os.path.isdir(os.path.join(self.root, ".git")):
                self._git("init", "-q", "-b", "master")
            return
        if os.path.exists(self.root) and (not os.path.isdir(self.root) or os.listdir(self.root)):
            raise BoardError(f"katalog {self.root} istnieje, ale nie jest tablicą Tide (brak {MARKER_FILE} "
                             f"z „{MARKER_KEY}: {FORMAT_VERSION}”) — nic w nim nie zapisuję")
        os.makedirs(self.root, exist_ok=True)
        self._git("init", "-q", "-b", "master")
        written = [self._write(MARKER_FILE, BOARD_MD), self._write(".gitignore", GITIGNORE)]
        for name, color, description in SEED_TAGS:
            doc = Document(["name: " + name, "color: " + (color if color in self.colors
                                                           else fallback_color(name, self.colors))],
                           description + "\n")
            written.append(self._write(os.path.join("tags", f"{slugify(name, 'tag')}.md"), doc.text()))
        for mode, text in flows.SEED_FLOWS.items():
            written.append(self._write(os.path.join("flows", f"{mode}.md"), text))
        for folder in ("cards", "projects", "docs"):
            os.makedirs(os.path.join(self.root, folder), exist_ok=True)
        self._commit("board: create", written)

    def create_card(self, data: dict) -> dict:
        data = _fields(data, CREATE_FIELDS)
        title = _title(data.get("title"))
        column = _column(data.get("column", DEFAULT_COLUMN))
        self.ensure()
        board = self.load()
        project = self._resolve_project(board, data)
        num = self._next_num(board)
        while os.path.exists(os.path.join(self.root, "cards", f"{CARD_PREFIX}{num}.md")):
            num += 1
        now = self._now()
        column_cards = board.column_cards(column)
        order = (min(c.order for c in column_cards) - ORDER_STEP) if column_cards else ORDER_STEP  # nowa na górę
        doc = Document()
        values = {"id": f"{CARD_PREFIX}{num}", "title": title, "project": project.slug if project else "",
                  "tags": ", ".join(_tags(data.get("tags", []))), "column": column, "order": order_value(order),
                  "note": _line(data.get("note", ""), NOTE_MAX, "notatka"), "created": now, "updated": now}
        for key in RESERVED_CARD_KEYS:
            values[key] = _line(data.get(key, ""), FIELD_MAX, key)
        for key in CARD_KEYS:
            doc.set(key, values[key])
        doc.body = _body(data.get("description", ""))
        card_rel = os.path.join("cards", f"{CARD_PREFIX}{num}.md")
        written = [self._write(card_rel, doc.text())]
        if board.meta is not None:
            board.meta.set("next-card", num + 1)
            written.append(self._write(MARKER_FILE, board.meta.text()))
        self._commit(f"{CARD_PREFIX}{num}: create", written)
        return {"ok": True, "id": f"{CARD_PREFIX}{num}"}

    def update_card(self, card_id: str, data: dict) -> dict:
        data = _fields(data, CARD_FIELDS, hint="do zmiany kolumny i kolejności służy MoveCard")
        self.ensure()
        board = self.load()
        card = self._card(board, card_id)
        changed = []
        if "project" in data or "repo" in data:
            project = self._resolve_project(board, data)
            changed += self._set(card, "project", project.slug if project else "")
        if "title" in data:
            changed += self._set(card, "title", _title(data["title"]))
        if "tags" in data:
            changed += self._set(card, "tags", ", ".join(_tags(data["tags"])))
        if "note" in data:
            changed += self._set(card, "note", _line(data["note"], NOTE_MAX, "notatka"))
        for key in RESERVED_CARD_KEYS:
            if key in data:
                changed += self._set(card, key, _line(data[key], FIELD_MAX, key))
        if "description" in data:
            body = _body(data["description"])
            if body.strip() != card.doc.body.strip():
                card.doc.body = body
                changed.append("description")
        if not changed:
            return {"ok": True, "id": card.id, "changed": False}
        card.doc.set("updated", self._now())
        self._commit(f"{card.id}: update {', '.join(changed)}", [self._write(_rel(self.root, card.path),
                                                                             card.doc.text())])
        return {"ok": True, "id": card.id, "changed": True}

    def move_card(self, card_id: str, column: str, index: int = -1) -> dict:
        """Przenosi kartę na pozycję `index` (od 0) kolumny; poza zakresem albo -1 — na koniec.
        Kolumna dostaje kolejność co ORDER_STEP; zapisujemy tylko karty, którym się zmieniła."""
        column = _column(column)
        self.ensure()
        board = self.load()
        card = self._card(board, card_id)
        others = board.column_cards(column, skip=card.num)
        try:
            index = int(index)
        except (TypeError, ValueError):
            raise BoardError("kolejność musi być liczbą (pozycja w kolumnie od 0)") from None
        if index < 0 or index > len(others):
            index = len(others)
        ordered = others[:index] + [card] + others[index:]
        moved_column = card.column != column
        written, now = [], self._now()
        for position, item in enumerate(ordered, start=1):
            target = position * ORDER_STEP
            if item.doc is None:
                continue
            if item.order == target and not (item is card and moved_column):
                continue
            item.doc.set("order", target)
            if item is card:
                item.doc.set("column", column)
                item.doc.set("updated", now)
            written.append(self._write(_rel(self.root, item.path), item.doc.text()))
        if not written:
            return {"ok": True, "id": card.id, "changed": False}
        verb = f"move to {column}" if moved_column else f"reorder in {column}"
        self._commit(f"{card.id}: {verb}", written)
        return {"ok": True, "id": card.id, "changed": True}

    def upsert_project(self, data: dict) -> dict:
        data = _fields(data, PROJECT_FIELDS)
        self.ensure()
        board = self.load()
        slug = (data.get("slug") or "").strip()
        repo = _repo(data["repo"], self.root) if data.get("repo") else ("" if "repo" in data else None)
        project = board.projects.get(slug) if slug else (board.project_by_repo(repo) if repo else None)
        if slug and not SLUG_RE.match(slug):
            raise BoardError(f"niepoprawny identyfikator projektu „{slug}” (małe litery, cyfry, . _ -)")
        created = project is None
        if created:
            name = _line(data.get("name") or (os.path.basename(repo) if repo else "") or slug, NAME_MAX, "nazwa")
            if not name:
                raise BoardError("projekt potrzebuje nazwy albo repozytorium")
            slug = slug or self._free_slug(board, slugify(name))
            if slug in board.projects:
                raise BoardError(f"projekt „{slug}” już istnieje")
            if repo and board.project_by_repo(repo) is not None:
                raise BoardError(f"repozytorium {repo} ma już projekt „{board.project_by_repo(repo).slug}”")
            doc = Document()
            doc.set("name", name)
            doc.set("repo", repo or "")
            doc.set("tags", "")
            doc.set("ai-docs", default_ai_docs(repo or ""))
            doc.set("tool", DEFAULT_TOOL)
            doc.set("mode", DEFAULT_MODE)
            project = Project(slug=slug, name=name, path=os.path.join(self.root, "projects", f"{slug}.md"), doc=doc)
        elif project.doc is None:
            raise BoardError(f"plik projektu {project.path} jest uszkodzony — popraw go ręcznie")
        changed = []
        doc = project.doc
        if not created and "name" in data:
            name = _line(data["name"], NAME_MAX, "nazwa")
            if not name:
                raise BoardError("nazwa projektu nie może być pusta")
            changed += _doc_set(doc, "name", name)
        if repo is not None and not created:
            other = board.project_by_repo(repo) if repo else None
            if other is not None and other.slug != project.slug:
                raise BoardError(f"repozytorium {repo} ma już projekt „{other.slug}”")
            changed += _doc_set(doc, "repo", repo)
            if not repo and doc.get("ai-docs").strip().lower() == "repo":
                changed += _doc_set(doc, "ai-docs", "tide")  # bez repozytorium dokumenty idą do tablicy
        if "tags" in data:
            changed += _doc_set(doc, "tags", ", ".join(_tags(data["tags"])))
        if "aiDocs" in data:
            value = _enum(data["aiDocs"], AI_DOCS, "ai-docs")
            if value == "repo" and not doc.get("repo"):
                raise BoardError("ai-docs „repo” wymaga repozytorium projektu")
            changed += _doc_set(doc, "ai-docs", value)
        if "tool" in data:
            changed += _doc_set(doc, "tool", _enum(data["tool"], TOOLS, "narzędzie"))
        if "mode" in data:
            changed += _doc_set(doc, "mode", _enum(data["mode"], MODES, "tryb"))
        if "description" in data:
            body = _body(data["description"])
            if body.strip() != doc.body.strip():
                doc.body = body
                changed.append("description")
        if not created and not changed:
            return {"ok": True, "slug": project.slug, "changed": False}
        rel = os.path.join("projects", f"{project.slug}.md")
        self._commit(f"project {project.slug}: {'create' if created else 'update'}", [self._write(rel, doc.text())])
        return {"ok": True, "slug": project.slug, "changed": True}

    def upsert_tag(self, data: dict) -> dict:
        data = _fields(data, TAG_FIELDS)
        name = _tag_name(data.get("name"))
        self.ensure()
        board = self.load()
        tag = board.tags.get(name.casefold())
        created = tag is None or not tag.path
        written, changed = [], []
        if created:
            if "newName" in data:
                raise BoardError(f"nie ma tagu „{name}” do zmiany nazwy")
            slug = self._free_tag_slug(board, slugify(name, "tag"))
            color = _enum(data.get("color") or fallback_color(name, self.colors), tuple(self.colors), "kolor")
            doc = Document(["name: " + name, "color: " + color], _body(data.get("description", "")))
            rel = os.path.join("tags", f"{slug}.md")
            self._commit(f"tag {name}: create", [self._write(rel, doc.text())])
            return {"ok": True, "name": name, "changed": True}
        doc = self._read_doc(_rel(self.root, tag.path), [])
        if doc is None:
            raise BoardError(f"plik tagu {tag.path} jest uszkodzony — popraw go ręcznie")
        if "color" in data:
            changed += _doc_set(doc, "color", _enum(data["color"], tuple(self.colors), "kolor"))
        if "description" in data:
            body = _body(data["description"])
            if body.strip() != doc.body.strip():
                doc.body = body
                changed.append("description")
        new_name = tag.name
        if "newName" in data:
            new_name = _tag_name(data["newName"])
            clash = board.tags.get(new_name.casefold())
            if clash is not None and clash is not tag and clash.path:
                raise BoardError(f"tag „{new_name}” już istnieje")
            if new_name != tag.name:
                doc.set("name", new_name)
                changed.append("name")
                written += self._rename_tag_uses(board, tag.name, new_name)
        if not changed:
            return {"ok": True, "name": tag.name, "changed": False}
        written.append(self._write(_rel(self.root, tag.path), doc.text()))
        message = f"tag {tag.name}: rename to {new_name}" if new_name != tag.name else f"tag {tag.name}: update"
        self._commit(message, written)
        return {"ok": True, "name": new_name, "changed": True}

    def sync(self, message: str = "sync external changes") -> bool:
        """Commit zmian z zewnątrz (ręczna edycja, agent w docs/). True, gdy coś zapisaliśmy."""
        if not self.exists() or not os.path.isdir(os.path.join(self.root, ".git")):
            return False
        status = self.git.run(self.root, "status", "--porcelain", "-uall")
        if not status.ok or not status.out.strip():
            return False
        self._git("add", "-A")
        return self._git_commit(message, [])

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

    def _read_text(self, rel: str) -> str | None:
        path = os.path.join(self.root, rel)
        try:
            if os.path.getsize(path) > FILE_MAX:
                return None
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except (OSError, UnicodeDecodeError):
            return None

    def _read_doc(self, rel: str, errors: list) -> Document | None:
        path = os.path.join(self.root, rel)
        try:
            if os.path.getsize(path) > FILE_MAX:
                errors.append({"path": path, "message": f"{rel}: plik większy niż {FILE_MAX // 1024} KB — pomijam"})
                return None
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except UnicodeDecodeError:
            errors.append({"path": path, "message": f"{rel}: plik nie jest w UTF-8"})
            return None
        except OSError as exc:
            errors.append({"path": path, "message": f"{rel}: nie da się odczytać ({exc.strerror or exc})"})
            return None
        try:
            return Document.parse(text)
        except FrontmatterError as exc:
            errors.append({"path": path, "message": f"{rel}: {exc}"})
            return None

    def _card(self, board: Board, card_id: str) -> Card:
        num = card_num(card_id)
        card = board.cards.get(num)
        if card is None:
            if os.path.exists(os.path.join(self.root, "cards", f"{CARD_PREFIX}{num}.md")):
                raise BoardError(f"plik karty {CARD_PREFIX}{num} jest uszkodzony — popraw go ręcznie")
            raise BoardError(f"nie ma karty {CARD_PREFIX}{num}")
        return card

    def _next_num(self, board: Board) -> int:
        try:
            counter = int((board.meta.get("next-card") if board.meta else "") or 1)
        except ValueError:
            counter = 1
        return max(board.max_num + 1, counter, 1)

    def _resolve_project(self, board: Board, data: dict) -> Project | None:
        """Projekt karty: `project` (slug istniejącego) albo `repo` (istniejący projekt repozytorium
        albo nowy, zakładany od razu osobnym commitem)."""
        slug = (data.get("project") or "").strip()
        if slug:
            project = board.projects.get(slug)
            if project is None:
                raise BoardError(f"nie ma projektu „{slug}”")
            return project
        if not data.get("repo"):
            return None
        repo = _repo(data["repo"], self.root)
        project = board.project_by_repo(repo)
        if project is not None:
            return project
        result = self.upsert_project({"repo": repo})
        fresh = self.load()
        board.projects = fresh.projects
        return fresh.projects[result["slug"]]

    def _set(self, card: Card, key: str, value: str) -> list[str]:
        return _doc_set(card.doc, key, value)

    def _rename_tag_uses(self, board: Board, old: str, new: str) -> list[str]:
        written = []
        for item in list(board.cards.values()) + list(board.projects.values()):
            if item.doc is None:
                continue
            names = split_tags(item.doc.get("tags"))
            if old.casefold() not in {n.casefold() for n in names}:
                continue
            renamed = split_tags([new if n.casefold() == old.casefold() else n for n in names])
            item.doc.set("tags", ", ".join(renamed))
            written.append(self._write(_rel(self.root, item.path), item.doc.text()))
        return written

    def _free_slug(self, board: Board, base: str) -> str:
        slug, n = base, 2
        while slug in board.projects or os.path.exists(os.path.join(self.root, "projects", f"{slug}.md")):
            slug, n = f"{base}-{n}", n + 1
        return slug

    def _free_tag_slug(self, board: Board, base: str) -> str:
        slug, n = base, 2
        while os.path.exists(os.path.join(self.root, "tags", f"{slug}.md")):
            slug, n = f"{base}-{n}", n + 1
        return slug

    def _write(self, rel: str, text: str) -> str:
        """Zapis atomowy (plik tymczasowy w tym samym katalogu i rename). Zwraca ścieżkę względną."""
        path = os.path.normpath(os.path.join(self.root, rel))
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        if not _inside(path, self.root):
            raise BoardError(f"ścieżka {rel} wychodzi poza katalog tablicy")
        tmp = os.path.join(folder, TMP_PREFIX + secrets.token_hex(6))
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return os.path.relpath(path, self.root)

    def _commit(self, message: str, paths: list[str]) -> None:
        self._git("add", "-A", "--", *paths)
        self._git_commit(message, paths)

    def _git_commit(self, message: str, paths: list[str]) -> bool:
        """Commit (tylko `paths`, a bez nich — całego indeksu). Autor z konfiguracji gita
        użytkownika; bez niej „Tide <tide@localhost>”. Bez podpisu GPG i hooków: to zapis
        stanu tablicy, nie praca nad kodem."""
        if paths:
            status = self.git.run(self.root, "status", "--porcelain", "--", *paths)
            if status.ok and not status.out.strip():
                return False
        args = ["commit", "--no-verify", "-q", "-m", message]
        if paths:
            args += ["--", *paths]
        res = self.git.run(self.root, "-c", "commit.gpgsign=false", *args)
        if not res.ok and _no_identity(res.err):
            res = self.git.run(self.root, "-c", "commit.gpgsign=false", "-c", "user.name=Tide",
                               "-c", "user.email=tide@localhost", *args)
        if not res.ok:
            if "nothing to commit" in res.err or "nothing added" in res.err:
                return False
            raise BoardError(f"zapisano plik, ale commit się nie udał: {res.err or res.out}".strip())
        return True

    def _git(self, *args: str) -> None:
        res = self.git.run(self.root, *args)
        if not res.ok:
            raise BoardError(f"git {args[0]} w tablicy się nie udał: {res.err or res.out}".strip())

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


def _repo(value, board_root: str) -> str:
    path = _text(value, "repo").strip()
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        raise BoardError(f"repozytorium musi być ścieżką bezwzględną („{path}”)")
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        raise BoardError(f"nie ma katalogu {path}")
    if not os.path.exists(os.path.join(path, ".git")):
        raise BoardError(f"{path} nie jest repozytorium git")
    if os.path.realpath(path) == os.path.realpath(board_root) or is_board_repo(path):
        raise BoardError("repozytorium tablicy nie może być projektem")
    return path


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


def _no_identity(err: str) -> bool:
    return any(text in err for text in ("Please tell me who you are", "unable to auto-detect email",
                                        "empty ident name", "Author identity unknown"))
