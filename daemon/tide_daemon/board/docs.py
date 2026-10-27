"""Plan i specyfikacja karty do czytania w panelu karty (PT-5, część B) — tylko odczyt, bez Qt.

/Board CardDocs(id) → {ok, card, plan, spec, others, warnings} (docs/tide-daemon.md, „Plan i specyfikacja
na karcie”). Wejście to karta i projekt z migawki tablicy oraz — gdy karta ma koordynatora
(`derived.coordinator`) — jego tablica z /Coordination (`plan.path`, `cwd`).

Skąd plan (pierwsze pasujące):
1. plan z `card: K-12` we frontmatterze (standard 1.1); przy kilku — niezakończony przed zakończonym,
   potem świeższy `updated`; pozostałe trafiają do `others`,
2. plan koordynatora połączonego z kartą (`plan.path` jego tablicy) — także bez klucza `card`.
Szukamy w `docs/agents/plans/` repozytorium projektu karty i każdego jego worktree (lista z plików
`.git/worktrees/*/gitdir`, bez wołania gita) oraz w `<tablica>/docs/<projekt>/plans/` (karta bez
projektu: `docs/_inbox`). Ten sam plik w kilku worktree (ta sama nazwa) to jeden plan — wygrywa kopia
z nowszym `updated`, potem nowszy plik.

Skąd specyfikacja (pierwsze pasujące):
1. pole `spec` karty — ścieżka bezwzględna albo względna (do repozytorium projektu, jego worktree,
   katalogu tablicy, katalogu dokumentów projektu w tablicy),
2. plik w `<docs>/specs/` (`docs/agents/specs` repozytorium i worktree, `<tablica>/docs/<projekt>/specs`)
   nazwany od ID karty (`k-12.md`, `k-12-…`, `k-12_…`) albo z `card: K-12` — gotowa przed szkicem,
   potem najnowsza; pozostałe trafiają do `others`.

Bezpieczeństwo: czytamy wyłącznie zwykłe pliki `.md`/`.markdown`/`.txt`, których ścieżka po realpath
leży w repozytorium projektu, jego worktree albo w `<tablica>/docs` — dowiązanie wychodzące poza te
katalogi odpada (z ostrzeżeniem, gdy to pole `spec` albo plan koordynatora). Najwyżej DOC_MAX bajtów
z pliku (dłuższy — ucięty, z dopiskiem), najwyżej FOLDER_MAX plików z katalogu.

Treść (`markdown`, `manualTest`) to Markdown źródłowy — bez frontmattera, tytułu i tabeli ticketów, z sekcją
„Co przetestować ręcznie” osobno — NIEOCZYSZCZONY. Bezpieczne pokazanie (bez obrazów, bez HTML, tylko
linki http(s)) robi okno Tide parserem samego Qt (apps/board/backend.py: render_markdown), bo tylko ten
sam parser, który potem renderuje, zgadza się co do tego, co jest obrazem albo HTML-em — wyrażenia
regularne tutaj rozjeżdżały się z md4c. Usługa działa na QCoreApplication, a QTextDocument potrzebuje
QGuiApplication (czcionki), więc renderowanie nie może być tutaj. Wszystkie wzorce w tym module są
zakotwiczone na początku linii i ograniczone — koszt liniowy także dla linii o długości 512 KB.

Pamięć: plik czytamy ponownie tylko po zmianie rozmiaru albo czasu zmiany (razem z wyliczoną treścią
do pokazania), listę katalogu — po zmianie czasu katalogu, więc wywołanie przy każdym odświeżeniu panelu
kosztuje kilka stat(). Usługa woła read() w jednym wątku roboczym (board_monitor.py), testy — bezpośrednio.
"""
import os
import re
import stat
import threading
from dataclasses import dataclass, field

from ..agents import gitinfo
from ..coordination import plan as plan_mod
from .store import SPEC_READY

DOC_MAX = 512 * 1024
FOLDER_MAX = 400
OTHERS_MAX = 8
WORKTREES_MAX = 64
CACHE_MAX = 1024
SUFFIXES = (".md", ".markdown", ".txt")
SPECS_DIR = os.path.join("docs", "agents", "specs")
INBOX = "_inbox"
DRAFT = ("szkic", "draft", "wip")
TRUNCATED_NOTE = "*Plik ma ponad 512 KB — pokazujemy jego początek. Całość: „Otwórz plik”.*"
_MANUAL_HEAD = re.compile(r"^(#{1,6})\s*co\s+przetestowa[ćc]\s+r[ęe]cznie", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+\S")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@dataclass
class Doc:
    path: str            # ścieżka po realpath
    meta: dict           # frontmatter (klucze małymi literami); bez frontmattera — pusty
    body: str            # treść pod frontmatterem
    mtime: float
    truncated: bool
    plan: object = None  # plan_mod.Plan dla planu Tide (plik z katalogu planów)
    shown: dict = field(default_factory=dict)  # treść do pokazania (markdown, manualTest) — liczona raz na wersję pliku


class CardDocs:
    def __init__(self, board_root: str):
        self.board_root = os.path.abspath(os.path.expanduser(board_root))
        self._files: dict[tuple, tuple] = {}   # (realpath, czy plan) → ((rozmiar, mtime_ns), Doc|None)
        self._lists: dict[str, tuple] = {}     # katalog → (mtime_ns, [nazwy .md])
        self._lock = threading.Lock()

    def read(self, card: dict, project: dict | None = None, coordinator: dict | None = None) -> dict:
        """Dokumenty karty: {ok, card, plan, spec, others, warnings} (plan i spec — obiekt albo None)."""
        with self._lock:
            if len(self._files) > CACHE_MAX:
                self._files.clear()
            if len(self._lists) > CACHE_MAX:
                self._lists.clear()
            return self._read(card or {}, project or {}, coordinator or {})

    # --- wnętrze -------------------------------------------------------------------------------

    def _read(self, card: dict, project: dict, coordinator: dict) -> dict:
        card_id = str(card.get("id") or "")
        warnings: list[str] = []
        main = _main_root(project.get("repo") or "")
        coord_main = _main_root(coordinator.get("cwd") or coordinator.get("repoPath") or "")
        if not main:
            # Karta bez repozytorium (bez projektu albo projekt bez kodu), połączona z koordynatorem
            # (np. przez `session`): pracą karty jest repozytorium koordynatora.
            main = coord_main
        trees = [main] + worktrees(main) if main else []
        for extra in (card.get("worktree") or "", coordinator.get("cwd") or ""):
            if extra and main and _main_root(extra) == main and os.path.realpath(extra) not in trees:
                trees.append(os.path.realpath(extra))
        board_docs = os.path.join(self.board_root, "docs")
        project_docs = os.path.join(board_docs, project.get("slug") or INBOX)
        roots = [t for t in trees if t] + [os.path.realpath(board_docs)]

        # Plan.
        plans: dict[str, Doc] = {}
        for folder in [os.path.join(t, plan_mod.PLANS_DIR) for t in trees] + [os.path.join(project_docs, "plans")]:
            for name in self._list(folder):
                doc = self._load(os.path.join(folder, name), roots, plan=True)
                if doc is None or doc.plan is None:
                    continue
                prev = plans.get(name)
                if prev is None or (doc.plan.updated_ts, doc.mtime) > (prev.plan.updated_ts, prev.mtime):
                    plans[name] = doc
        mine = [d for d in plans.values() if card_id and d.plan.card == card_id]
        plan_doc, plan_link = None, ""
        if mine:
            plan_doc = max(mine, key=lambda d: (d.plan.status != "done", d.plan.updated_ts, d.mtime))
            plan_link = "card"
        else:
            path = str((coordinator.get("plan") or {}).get("path") or "")
            if path:
                plan_doc = self._load(path, roots, plan=True)
                if plan_doc is not None and plan_doc.plan is None:
                    plan_doc = None
                if plan_doc is not None:
                    plan_link = "coordinator"
                elif os.path.isfile(path):
                    warnings.append("plan koordynatora leży poza repozytorium projektu i tablicą — pominięty")

        # Specyfikacja.
        spec_doc, spec_link = None, ""
        field = str(card.get("spec") or "").strip()
        if field:
            path = os.path.expanduser(field)
            bases = [] if os.path.isabs(path) else trees + [self.board_root, project_docs]
            candidates = [path] if os.path.isabs(path) else [os.path.join(b, path) for b in bases if b]
            existing = [c for c in candidates if os.path.isfile(c)]
            for candidate in existing:
                spec_doc = self._load(candidate, roots)
                if spec_doc is not None:
                    spec_link = "field"
                    break
            if spec_doc is None:
                warnings.append(f"specyfikacja z karty ({field}) leży poza repozytorium projektu i tablicą — pominięta"
                                if existing else f"specyfikacja z karty ({field}) — nie ma takiego pliku")
        key = card_id.lower()
        specs: dict[str, Doc] = {}
        for folder in [os.path.join(t, SPECS_DIR) for t in trees] + [os.path.join(project_docs, "specs")]:
            for name in self._list(folder):
                stem = name[:-3].lower()
                by_name = bool(key) and (stem == key or stem.startswith((key + "-", key + "_")))
                doc = self._load(os.path.join(folder, name), roots)
                if doc is None or not (by_name or (key and _card_of(doc.meta) == card_id)):
                    continue
                prev = specs.get(name)
                if prev is None or doc.mtime > prev.mtime:
                    specs[name] = doc
        ranked = sorted(specs.values(), key=lambda d: (_ready(d.meta), d.mtime), reverse=True)
        if spec_doc is None and ranked:
            spec_doc = ranked[0]
            spec_link = "card" if _card_of(spec_doc.meta) == card_id else "name"

        shown = {d.path for d in (plan_doc, spec_doc) if d is not None}
        others = [("plan", d) for d in mine if d.path not in shown] + \
                 [("spec", d) for d in ranked if d.path not in shown]
        others.sort(key=lambda kd: kd[1].mtime, reverse=True)
        place = lambda d: _where(d.path, main, board_docs)  # noqa: E731
        scope = list((card.get("derived") or {}).get("tickets") or [])
        return {
            "ok": True, "card": card_id,
            "plan": _plan_json(plan_doc, plan_link, place(plan_doc), main, scope) if plan_doc else None,
            "spec": _spec_json(spec_doc, spec_link, place(spec_doc)) if spec_doc else None,
            "others": [{"kind": kind, "path": d.path, "title": _title(d), "status": _status(d),
                        "updated": _updated(d)} for kind, d in others[:OTHERS_MAX]],
            "warnings": warnings,
        }

    def _list(self, folder: str) -> list[str]:
        try:
            st = os.stat(folder)
        except OSError:
            return []
        if not stat.S_ISDIR(st.st_mode):
            return []
        hit = self._lists.get(folder)
        if hit and hit[0] == st.st_mtime_ns:
            return hit[1]
        try:
            names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".md"))[:FOLDER_MAX]
        except OSError:
            return []
        self._lists[folder] = (st.st_mtime_ns, names)
        return names

    def _load(self, path: str, roots: list[str], plan: bool = False) -> Doc | None:
        """Plik z jednego z katalogów `roots` (po realpath), zwykły, z dozwolonym rozszerzeniem."""
        real = os.path.realpath(path)
        if not real.lower().endswith(SUFFIXES) or not any(_inside(real, r) for r in roots if r):
            return None
        try:
            st = os.stat(real)
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        stamp = (st.st_size, st.st_mtime_ns)
        hit = self._files.get((real, plan))
        if hit and hit[0] == stamp:
            return hit[1]
        try:
            with open(real, "rb") as fh:
                data = fh.read(DOC_MAX + 1)
        except OSError:
            return None
        truncated = len(data) > DOC_MAX
        text = data[:DOC_MAX].decode("utf-8", errors="replace").replace("\r\n", "\n")
        if truncated:
            text = text.rstrip("�")  # ucięty w środku znaku UTF-8
        meta, body = plan_mod.split_frontmatter(text)
        doc = Doc(path=real, meta=meta or {}, body=body if meta is not None else text.lstrip("﻿"),
                  mtime=st.st_mtime, truncated=truncated,
                  plan=plan_mod.parse(text, real, st.st_mtime) if plan else None)
        self._files[(real, plan)] = (stamp, doc)
        return doc


def worktrees(main: str) -> list[str]:
    """Worktree repozytorium z plików `.git/worktrees/*/gitdir` (bez gita); tylko istniejące katalogi."""
    folder = os.path.join(main, ".git", "worktrees")
    try:
        names = sorted(os.listdir(folder))[:WORKTREES_MAX]
    except OSError:
        return []
    out = []
    for name in names:
        try:
            with open(os.path.join(folder, name, "gitdir"), encoding="utf-8", errors="replace") as fh:
                gitdir = fh.read(4096).strip()
        except OSError:
            continue
        if not gitdir:
            continue
        if not os.path.isabs(gitdir):
            gitdir = os.path.normpath(os.path.join(folder, name, gitdir))
        tree = os.path.realpath(os.path.dirname(gitdir))
        if os.path.isdir(tree) and tree != main and tree not in out:
            out.append(tree)
    return out


def manual_section(text: str) -> tuple[str, str]:
    """(treść bez sekcji, sekcja „Co przetestować ręcznie…” bez nagłówka) — sekcja do nagłówka
    tego samego albo wyższego poziomu; bez niej — (tekst, "")."""
    lines = (text or "").split("\n")
    fence = ""
    for i, line in enumerate(lines):
        fence = _fence_state(line, fence)
        match = _MANUAL_HEAD.match(line) if not fence else None
        if not match:
            continue
        level = len(match.group(1))
        end, inner = len(lines), ""
        for j in range(i + 1, len(lines)):
            inner = _fence_state(lines[j], inner)
            head = _HEADING.match(lines[j]) if not inner else None
            if head and len(head.group(1)) <= level:
                end = j
                break
        section = "\n".join(lines[i + 1:end]).strip("\n")
        rest = "\n".join(lines[:i] + lines[end:])
        return rest, section
    return text, ""


def strip_ticket_table(body: str) -> str:
    """Treść planu bez tabeli ticketów (`| Ticket | Tytuł | Stan | …`) — panel pokazuje ją osobno."""
    lines = body.split("\n")
    out, skipping, fence = [], False, ""
    for line in lines:
        fence = _fence_state(line, fence)
        stripped = line.strip()
        if skipping:
            if stripped.startswith("|"):
                continue
            skipping = False
        if not fence and stripped.startswith("|"):
            cells = [c.strip().lower() for c in stripped.strip("|").split("|")]
            if cells[:3] == list(plan_mod.COLUMNS[:3]):
                skipping = True
                continue
        out.append(line)
    return "\n".join(out)


def _display(doc: Doc, plan: bool) -> tuple[str, str]:
    """(markdown, manualTest) do pokazania — raz na wersję pliku (Doc jest w pamięci po rozmiarze i czasie)."""
    if plan not in doc.shown:
        text = strip_ticket_table(doc.body) if plan else doc.body
        text = _strip_title(text)
        text, manual = manual_section(text)
        text = text.strip("\n")
        if doc.truncated:
            text += "\n\n---\n\n" + TRUNCATED_NOTE
        doc.shown[plan] = (text, manual)
    return doc.shown[plan]


def _plan_json(doc: Doc, link: str, where: str, repo: str, scope: list) -> dict:
    plan = doc.plan
    markdown, manual = _display(doc, plan=True)
    return {"path": doc.path, "title": plan.title or _title(doc), "status": plan.status, "updated": plan.updated,
            "updatedTs": round(plan.updated_ts, 3), "card": plan.card, "coordinator": plan.coordinator,
            "link": link, "where": where, "repo": repo, "mtime": round(doc.mtime, 3),
            "tickets": [{"id": r.ticket, "title": r.title, "state": r.state, "branch": r.branch, "agent": r.agent,
                         "notes": r.note} for r in plan.rows],
            "cardTickets": scope, "errors": list(plan.errors), "markdown": markdown, "manualTest": manual,
            "truncated": doc.truncated}


def _spec_json(doc: Doc, link: str, where: str) -> dict:
    markdown, manual = _display(doc, plan=False)
    return {"path": doc.path, "title": _title(doc), "status": _status(doc), "ready": _ready(doc.meta),
            "updated": _updated(doc), "card": _card_of(doc.meta), "link": link, "where": where,
            "mtime": round(doc.mtime, 3), "markdown": markdown, "manualTest": manual, "truncated": doc.truncated}


def _fence_state(line: str, fence: str) -> str:
    match = _FENCE.match(line)
    if fence:
        if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) \
                and not line.strip().strip(fence[0]):
            return ""
        return fence
    return match.group(1) if match else ""


def _strip_title(text: str) -> str:
    """Bez pierwszego nagłówka `# …`, gdy to pierwsza linia tekstu (tytuł pokazuje nagłówek panelu)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if line.startswith("# "):
            return "\n".join(lines[:i] + lines[i + 1:])
        break
    return text


def _title(doc: Doc) -> str:
    title = str(doc.meta.get("title") or "").strip()
    if title:
        return title
    for line in doc.body.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
        if line.strip():
            break
    return os.path.splitext(os.path.basename(doc.path))[0]


def _status(doc: Doc) -> str:
    if doc.plan is not None:
        return doc.plan.status
    return str(doc.meta.get("status") or "").strip().lower()


def _updated(doc: Doc) -> str:
    if doc.plan is not None:
        return doc.plan.updated
    return str(doc.meta.get("updated") or "").strip()


def _ready(meta: dict) -> bool:
    return str(meta.get("status") or "").strip().lower() in SPEC_READY


def _card_of(meta: dict) -> str:
    match = plan_mod.CARD_RE.match(str(meta.get("card") or "").strip())
    return f"K-{int(match.group(1))}" if match else ""


def _main_root(path: str) -> str:
    """Główny katalog repozytorium po realpath (także z worktree) albo pusty — katalogu nie ma."""
    if not path or not os.path.isdir(path):
        return ""
    return os.path.realpath(gitinfo.main_root(path) or path)


def _where(path: str, main: str, board_docs: str) -> str:
    if _inside(path, os.path.realpath(board_docs)):
        return "board"
    return "repo" if main and _inside(path, main) else "worktree"


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)
