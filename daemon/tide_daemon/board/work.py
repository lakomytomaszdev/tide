"""Lista „Praca” (PT-40) — jedna prawda dla kafelka Praca: aktywne karty tablicy i żywe sesje agentów bez karty.

Bez Qt, testowane bezpośrednio. Liczy ją BoardMonitor w tym samym przeliczeniu co kolumny kart (ten sam
`derive.Context`, te same sesje z /Agents), więc kafelek nie może pokazać innej liczby niż Tablica.

Pozycje (`items`), w grupach (`group`):
- karta (`kind: "card"`) — karta, której kolumna wyświetlana (`boardColumn`) to `waiting`, `progress`, `review`
  albo `todo` (bez Backlogu, Gotowych i Archiwum); grupa = ta kolumna. Połączenie z sesją bierzemy wyłącznie
  z `derived` karty (board/derive.py) — tu nie ma żadnej własnej reguły łączenia,
- sesja (`kind: "session"`) — żywa sesja z /Agents (stan inny niż `ended`), której nie wskazuje żadna karta:
  ani `derived.session` / `derived.coordinator` (połączenie z derive), ani pole `session` karty (przypięcie —
  także karty w Gotowych albo Archiwum, którym derive stanu nie daje; ta sama lista co `claimed` w derive).
  `waiting` → grupa `waiting`, `working` → `progress`, skończona tura (`done`) i `idle` → `finished`
  (wyciszona, zwinięta grupa „Skończone”, poza licznikami nagłówka).
Jedna sesja prowadząca dwie karty to dwie pozycje-karty i żadnej pozycji-sesji. Podagenci Codexa są już
zwinięci przez skaner /Agents.

Pozycja-sesja ma `cardRequest` — gotowe żądanie `/Board CreateCard` („+ karta”): tytuł z tytułu sesji,
`repo` z repozytorium sesji (store sam bierze istniejący projekt tego repozytorium albo zakłada nowy),
`session` — więc derive od razu łączy nową kartę z sesją (reguła 1), kolumna Do zrobienia.
"""
import os

from .derive import NON_BRANCHES, _branch, _clip, _ts
from .model import TITLE_MAX

GROUPS = ("waiting", "progress", "review", "todo", "finished")
COUNTED = ("waiting", "progress", "review", "todo")   # „Praca · N” — bez grupy Skończone
CARD_COLUMNS = ("waiting", "progress", "review", "todo")
SESSION_GROUP = {"waiting": "waiting", "working": "progress", "done": "finished", "idle": "finished"}
QUESTION_MAX = 240


def build(cards, context=None, projects=(), exists=True) -> dict:
    """{exists, items, counts} z kart migawki (z `derived` i `boardColumn`) i kontekstu przeliczenia
    (`derive.Context` — sesje, koordynatorzy, repo_of) albo None (usługa bez /Agents: same karty)."""
    sessions = dict(getattr(context, "sessions", None) or {})
    boards = dict(getattr(context, "boards_by_id", None) or {})
    repo_of = getattr(context, "repo_of", None) or (lambda _cwd: "")
    cards = [c for c in cards or [] if isinstance(c, dict)]

    claimed = set()
    for card in cards:
        derived = card.get("derived") or {}
        for value in (derived.get("session"), derived.get("coordinator"), card.get("session")):
            value = str(value or "").strip()
            if value:
                claimed.add(value)

    items = []
    for column in CARD_COLUMNS:
        for card in cards:
            if (card.get("boardColumn") or card.get("column") or "backlog") == column:
                items.append(_card_item(card, column, sessions, boards))

    by_repo = {}
    for project in projects or []:
        if isinstance(project, dict) and project.get("repo"):
            by_repo.setdefault(os.path.realpath(project["repo"]), project)
    loose = []
    for session in sessions.values():
        group = SESSION_GROUP.get(session.get("state") or "")
        if group is None:
            continue
        if session.get("id") in claimed or str(session.get("sessionId") or "") in claimed:
            continue
        loose.append(_session_item(session, group, repo_of, by_repo))
    # Czekające — najdłużej czekająca pierwsza (jak Meta+A), pozostałe — od najświeższej aktywności.
    loose.sort(key=lambda i: (GROUPS.index(i["group"]),
                              i["sinceTs"] if i["group"] == "waiting" else -i["activityTs"]))
    items.extend(loose)
    items.sort(key=lambda i: GROUPS.index(i["group"]))  # stabilnie: w grupie karty, potem sesje

    counts = {group: sum(1 for i in items if i["group"] == group) for group in GROUPS}
    counts["total"] = sum(counts[g] for g in COUNTED)
    counts["sessions"] = sum(1 for i in items if i["kind"] == "session" and i["group"] in COUNTED)
    return {"exists": bool(exists), "items": items, "counts": counts}


def _card_item(card: dict, column: str, sessions: dict, boards: dict) -> dict:
    derived = card.get("derived") or {}
    target = str(derived.get("session") or derived.get("coordinator") or "")
    session = sessions.get(target)
    board = boards.get(str(derived.get("coordinator") or ""))
    source = session or board or {}
    question = ""
    if column == "waiting" and session is not None and session.get("state") == "waiting":
        question = _clip(session.get("waitingText") or "", QUESTION_MAX)
    return {
        "kind": "card", "id": card.get("id") or "", "card": card.get("id") or "",
        "title": card.get("title") or "", "project": card.get("project") or "",
        "projectName": card.get("projectName") or card.get("project") or "",
        "group": column, "note": derived.get("note") or "", "question": question,
        "nextStep": derived.get("nextStep") or "",
        "agentState": (derived.get("agentState") or "") if target else "",
        "session": target, "canActivate": bool(session.get("canActivate")) if session else False,
        "sinceTs": _ts(source.get("sinceTs")) or _ts(source.get("lastActivityTs")),
        "activityTs": _ts(source.get("lastActivityTs")),
        "done": int(derived.get("done") or 0), "total": int(derived.get("total") or 0),
        "tool": derived.get("tool") or card.get("tool") or "", "branch": derived.get("branch") or "",
    }


def _session_item(session: dict, group: str, repo_of, by_repo: dict) -> dict:
    sid = str(session.get("id") or "")
    repo = repo_of(session.get("cwd") or "") or ""
    project = by_repo.get(os.path.realpath(repo)) if repo else None
    name = (project or {}).get("name") or session.get("project") or ""
    title = _title(session)
    request = {"title": title, "column": "todo", "session": sid}
    if repo:
        request["repo"] = repo
    item = {
        "kind": "session", "id": sid, "card": "", "title": title,
        "project": (project or {}).get("slug") or "", "projectName": name,
        "group": group, "note": " ".join(str(session.get("lastAction") or "").split()),
        "question": _clip(session.get("waitingText") or "", QUESTION_MAX) if group == "waiting" else "",
        "nextStep": "odpowiedz agentowi" if group == "waiting" else "",
        "agentState": session.get("state") or "", "reason": session.get("reason") or "",
        "session": sid, "canActivate": bool(session.get("canActivate")),
        "sinceTs": _ts(session.get("sinceTs")) or _ts(session.get("lastActivityTs")),
        "activityTs": _ts(session.get("lastActivityTs")),
        "done": 0, "total": 0, "tool": session.get("tool") or "", "branch": _shown_branch(session),
        "subagents": int(session.get("subagents") or 0),
    }
    if not session.get("subagent"):  # osierocony podagent Codexa to nie „rzecz do zrobienia”
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
