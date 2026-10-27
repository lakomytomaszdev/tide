"""Plany projektów z dokumentami AI „w Tide” (standard planu 1.1) — tylko odczyt.

Projekt tablicy z `ai-docs: tide` trzyma plany i specyfikacje agentów w repozytorium tablicy:
`<tablica>/docs/<projekt>/plans/` i `…/specs/`, nie w `docs/agents/` swojego repozytorium.
plan_dirs(roots) mówi, które katalogi planów w tablicy należą do repozytorium podanego przez
`roots` (worktree koordynatora i główny katalog): projekt, którego `repo` to jeden z tych katalogów.
Dzięki temu Tide Sesje, raport poranny, check_plans i next_id widzą plany takich projektów
i numerują ich tickety razem z resztą repozytorium.

Tablica: `$TIDE_BOARD_DIR`, inaczej ~/DevWorkspace/tide-board (board/marker.py); katalog bez
znacznika tablicy niczego nie daje. Pliki projektów czytamy tylko wtedy, gdy się zmieniły
(nazwa, rozmiar, czas zmiany), więc wywołanie przy każdym odświeżeniu jest tanie.
"""
import os

from ..board.frontmatter import Document, FrontmatterError
from ..board.marker import default_board_dir, is_board_repo
from ..board.model import SLUG_RE, parse_project

PROJECT_MAX = 256 * 1024
_cache: dict[str, tuple] = {}   # katalog tablicy → (znacznik plików projektów, [(repo realpath, slug, ai-docs)])


def projects(board_root: str | None = None) -> list[tuple[str, str, str]]:
    """[(repozytorium po realpath, slug, ai-docs)] projektów tablicy z repozytorium."""
    root = os.path.abspath(os.path.expanduser(board_root or default_board_dir()))
    if not is_board_repo(root):
        return []
    folder = os.path.join(root, "projects")
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".md") and SLUG_RE.match(n[:-3]))
    except OSError:
        return []
    stamp = []
    for name in names:
        try:
            st = os.stat(os.path.join(folder, name))
        except OSError:
            continue
        stamp.append((name, st.st_size, st.st_mtime_ns))
    stamp = tuple(stamp)
    cached = _cache.get(root)
    if cached and cached[0] == stamp:
        return cached[1]
    out = []
    for name, size, _mtime in stamp:
        path = os.path.join(folder, name)
        if size > PROJECT_MAX or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                doc = Document.parse(fh.read())
        except (OSError, UnicodeDecodeError, FrontmatterError):
            continue
        project = parse_project(doc, path, name[:-3], [])
        if project.repo:
            out.append((os.path.realpath(project.repo), project.slug, project.ai_docs))
    if len(_cache) > 8:
        _cache.clear()
    _cache[root] = (stamp, out)
    return out


def docs_dirs(roots, board_root: str | None = None) -> list[str]:
    """Katalogi dokumentów AI w tablicy (`<tablica>/docs/<projekt>`) dla repozytorium z `roots`
    — tylko projekty z `ai-docs: tide`."""
    wanted = {os.path.realpath(r) for r in roots or () if r}
    if not wanted:
        return []
    root = os.path.abspath(os.path.expanduser(board_root or default_board_dir()))
    return [os.path.join(root, "docs", slug) for repo, slug, ai_docs in projects(root)
            if ai_docs == "tide" and repo in wanted]


def plan_dirs(roots, board_root: str | None = None) -> list[str]:
    """Katalogi planów w tablicy (`<tablica>/docs/<projekt>/plans`) dla repozytorium z `roots`."""
    return [os.path.join(folder, "plans") for folder in docs_dirs(roots, board_root)]
