"""Lista „Praca” (PT-40) — jedna prawda dla kafelka Praca: aktywne karty tablicy i żywe sesje agentów bez karty.

Bez Qt, testowane bezpośrednio. Liczy ją BoardMonitor w tym samym przeliczeniu co kolumny kart (ten sam
`derive.Context`, te same sesje z /Agents), więc kafelek nie może pokazać innej liczby niż Tablica.

Pozycje (`items`), w grupach (`group`):
- karta (`kind: "card"`) — karta, której kolumna wyświetlana (`boardColumn`) to `waiting`, `progress`, `review`
  albo `todo` (bez Backlogu, Gotowych i Archiwum); grupa = ta kolumna. Połączenie z sesją bierzemy wyłącznie
  z `derived` karty (board/derive.py) — tu nie ma żadnej własnej reguły łączenia. Pytanie (`question`) to
  `derived.question` — pytanie tej karty, nie dowolne pytanie jej koordynatora,
- sesja (`kind: "session"`) — żywa sesja z /Agents (stan inny niż `ended`), której nie wskazuje żadna karta:
  ani `derived.session` / `derived.coordinator` (połączenie z derive), ani pole `session` karty (przypięcie —
  także karty w Gotowych albo Archiwum, którym derive stanu nie daje; ta sama lista co `claimed` w derive).
  `waiting` → grupa `waiting`, `working` → `progress`, skończona tura (`done`) i `idle` → `finished`
  (wyciszona, zwinięta grupa „Skończone”, poza licznikami nagłówka),
- **czekająca sesja zawsze jest w grupie `waiting`**: jeśli żadna karta z tej grupy jej nie obejmuje (np. jej
  karta jest w Gotowych, a sesję wznowiono; koordynator pyta o ticket spoza karty, więc karta jest W toku),
  dostaje własną pozycję-sesję z `card` tej karty i notatką „karta K-2 w Gotowych”. Kafelek nigdy nie pokazuje
  „0 czeka”, gdy w /Agents ktoś czeka.
Jedna sesja prowadząca dwie karty to dwie pozycje-karty i żadnej pozycji-sesji. Podagenci Codexa są już
zwinięci przez skaner /Agents.

Pozycja-sesja bez karty ma `cardRequest` — gotowe żądanie `/Board CreateCard` („+ karta”): tytuł z tytułu sesji,
`session` (derive od razu łączy nową kartę z sesją, reguła 1), kolumna Do zrobienia i `project`, gdy repozytorium
sesji jest projektem tablicy — bez projektu karta powstaje bez niego (żadnego cichego zakładania projektu).

Pola zmienne co chwilę przy pracy agenta (ostatnia czynność w `note`) BoardMonitor sygnalizuje rzadziej —
`stable_key()` to lista bez nich (WorkChanged od razu tylko przy zmianie tego klucza).
"""
import json
import os

from .derive import NON_BRANCHES, _branch, _clip, _ts
from .model import TITLE_MAX

GROUPS = ("waiting", "progress", "review", "todo", "finished")
COUNTED = ("waiting", "progress", "review", "todo")   # „Praca · N” — bez grupy Skończone
CARD_COLUMNS = ("waiting", "progress", "review", "todo")
SESSION_GROUP = {"waiting": "waiting", "working": "progress", "done": "finished", "idle": "finished"}
QUESTION_MAX = 240
VOLATILE = ("note",)   # ostatnia czynność agenta — zmienia się przy każdym narzędziu
COLUMN_WORDS = {"backlog": "w Backlogu", "todo": "do zrobienia", "progress": "w toku", "waiting": "czeka",
                "review": "do sprawdzenia", "done": "w Gotowych", "archived": "w Archiwum"}


def build(cards, context=None, projects=(), exists=True) -> dict:
    """{exists, items, counts} z kart migawki (z `derived` i `boardColumn`) i kontekstu przeliczenia
    (`derive.Context` — sesje, koordynatorzy, repo_of) albo None (usługa bez /Agents: same karty)."""
    sessions = dict(getattr(context, "sessions", None) or {})
    boards = dict(getattr(context, "boards_by_id", None) or {})
    repo_of = getattr(context, "repo_of", None) or (lambda _cwd: "")
    cards = [c for c in cards or [] if isinstance(c, dict)]

    owner = {}   # id sesji (albo samo id w narzędziu) → pierwsza karta, która ją wskazuje
    for card in cards:
        derived = card.get("derived") or {}
        for value in (derived.get("session"), derived.get("coordinator"), card.get("session")):
            value = str(value or "").strip()
            if value:
                owner.setdefault(value, card)

    items = []
    for column in CARD_COLUMNS:
        for card in cards:
            if _column(card) == column:
                items.append(_card_item(card, column, sessions, boards))
    covered = {i["session"] for i in items if i["group"] == "waiting" and i["session"]}
    covered |= {str((c.get("derived") or {}).get("coordinator") or "") for c in cards
                if _column(c) == "waiting"} - {""}

    by_repo = {}
    for project in projects or []:
        if isinstance(project, dict) and project.get("repo"):
            by_repo.setdefault(os.path.realpath(project["repo"]), project)
    loose = []
    for session in sessions.values():
        group = SESSION_GROUP.get(session.get("state") or "")
        if group is None:
            continue
        sid, raw = session.get("id"), str(session.get("sessionId") or "")
        card = owner.get(sid) or (owner.get(raw) if raw else None)
        if card is not None and (group != "waiting" or sid in covered or raw in covered):
            continue
        loose.append(_session_item(session, group, repo_of, by_repo, card))
    # Czekające — najdłużej czekająca pierwsza (jak Meta+A), pozostałe — od najświeższej zmiany stanu.
    loose.sort(key=lambda i: (GROUPS.index(i["group"]), i["sinceTs"] if i["group"] == "waiting" else -i["sinceTs"]))
    items.extend(loose)
    items.sort(key=lambda i: GROUPS.index(i["group"]))  # stabilnie: w grupie karty, potem sesje

    counts = {group: sum(1 for i in items if i["group"] == group) for group in GROUPS}
    counts["total"] = sum(counts[g] for g in COUNTED)
    counts["sessions"] = sum(1 for i in items if i["kind"] == "session" and not i["card"] and i["group"] in COUNTED)
    return {"exists": bool(exists), "items": items, "counts": counts}


def stable_key(work: dict) -> str:
    """Lista bez pól zmiennych co chwilę (VOLATILE) — do decyzji, czy WorkChanged iść od razu."""
    items = [{k: v for k, v in i.items() if k not in VOLATILE} for i in work.get("items") or []]
    return json.dumps({**work, "items": items}, ensure_ascii=False, sort_keys=True)


def _column(card: dict) -> str:
    return card.get("boardColumn") or card.get("column") or "backlog"


def _card_item(card: dict, column: str, sessions: dict, boards: dict) -> dict:
    derived = card.get("derived") or {}
    target = str(derived.get("session") or derived.get("coordinator") or "")
    session = sessions.get(target)
    board = boards.get(str(derived.get("coordinator") or ""))
    source = session or board or {}
    live = session is not None and session.get("state") not in ("ended", None)
    return {
        "kind": "card", "id": card.get("id") or "", "card": card.get("id") or "",
        "title": card.get("title") or "", "project": card.get("project") or "",
        "projectName": card.get("projectName") or card.get("project") or "",
        "group": column, "note": derived.get("note") or "",
        "question": _clip(derived.get("question") or "", QUESTION_MAX) if column == "waiting" else "",
        "nextStep": derived.get("nextStep") or "",
        "agentState": (derived.get("agentState") or "") if target else "",
        "session": target, "canActivate": bool(live and session.get("canActivate")),
        "sinceTs": _ts(source.get("sinceTs")) or _ts(source.get("lastActivityTs")),
        "done": int(derived.get("done") or 0), "total": int(derived.get("total") or 0),
        "tool": derived.get("tool") or card.get("tool") or "", "branch": derived.get("branch") or "",
    }


def _session_item(session: dict, group: str, repo_of, by_repo: dict, card: dict | None = None) -> dict:
    sid = str(session.get("id") or "")
    repo = repo_of(session.get("cwd") or "") or ""
    project = by_repo.get(os.path.realpath(repo)) if repo else None
    name = (project or {}).get("name") or session.get("project") or ""
    title = _title(session)
    note = " ".join(str(session.get("lastAction") or "").split())
    if card is not None:   # czekająca sesja karty, której ta karta nie pokazuje jako „czeka”
        note = f"karta {card.get('id') or ''} {COLUMN_WORDS.get(_column(card), _column(card))}"
    item = {
        "kind": "session", "id": sid, "card": (card or {}).get("id") or "", "title": title,
        "project": (project or {}).get("slug") or "", "projectName": name,
        "group": group, "note": note,
        "question": _clip(session.get("waitingText") or "", QUESTION_MAX) if group == "waiting" else "",
        "nextStep": "odpowiedz agentowi" if group == "waiting" else "",
        "agentState": session.get("state") or "", "reason": session.get("reason") or "",
        "session": sid, "canActivate": bool(session.get("canActivate")),
        "sinceTs": _ts(session.get("sinceTs")) or _ts(session.get("lastActivityTs")),
        "done": 0, "total": 0, "tool": session.get("tool") or "", "branch": _shown_branch(session),
        "subagents": int(session.get("subagents") or 0),
    }
    if card is None and not session.get("subagent"):  # osierocony podagent Codexa to nie „rzecz do zrobienia”
        request = {"title": title, "column": "todo", "session": sid}
        if project is not None and project.get("slug"):
            request["project"] = project["slug"]
        item["cardRequest"] = request
    return item


def _title(session: dict) -> str:
    """Tytuł karty z sesji: tytuł rozmowy (Claude Code), inaczej projekt z gałęzią, inaczej sam projekt."""
    title = " ".join(str(session.get("title") or "").split())
    if not title:
        project = session.get("project") or ""
        branch = _shown_branch(session)
        title = f"{project} · {branch}" if project and branch else project or branch
    return _clip(title or "Sesja agenta", TITLE_MAX)


def _shown_branch(session: dict) -> str:
    branch = _branch(session.get("branch"))
    return "" if branch in NON_BRANCHES else branch
