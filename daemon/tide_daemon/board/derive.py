"""Stan karty tablicy z prawdziwej pracy agentów (PT-5) — bez Qt, testowane bezpośrednio.

Wejście: karta z migawki (`board/store.py`, z kluczem `repo` jej projektu), sesje z /Agents
(`AgentMonitor.sessions`) i tablice koordynatorów z /Coordination (`CoordinationMonitor.boards`).
Wyjście: pole `derived` karty albo None (karta zostaje w swojej kolumnie ręcznej).

Połączenie karty z pracą (pierwsze pasujące):
1. `session` karty — id sesji albo koordynatora jak w /Agents i /Coordination (`claude:<id>`,
   `codex:<id>`; samo `<id>` też),
2. `card: K-12` we frontmatterze planu koordynatora (czytane ostrożnie — klucza może jeszcze nie być),
3. ID karty w nazwie gałęzi (`claude/k-12-eksport`, propozycja `RenderFlow`) koordynatora, jego ticketu
   albo sesji w repozytorium projektu karty,
4. `branch` karty równa gałęzi koordynatora, ticketu albo sesji w repozytorium projektu karty (także
   w jego worktrees); gałęzi głównych (master, main, develop) nie dopasowujemy.
Przy kilku dopasowaniach jednej reguły wygrywa najświeższa aktywność. Karta w Backlogu dostaje stan
tylko z połączenia jawnego (1, 2) — gałąź może być przypadkowa albo stara, a Backlog to „kiedyś”.
Gotowe i Archiwum nigdy nie dostają stanu: ręczna kolumna `done` zawsze wygrywa.

Kolumna:
- `waiting` („Czeka na mnie”) — agent stoi w pół pracy i bez użytkownika nie ruszy: sesja w stanie
  `waiting` (ta sama definicja co widżet agentów — `agents.state.waits_for_user`) albo ticket w `czeka`
  z pytaniem bez odpowiedzi,
- `progress` („W toku”) — praca trwa albo stoi niedokończona: tickety niegotowe (także review AI
  i poprawki), agent pracuje, albo agent bezczynny / sesja zakończona (notatka mówi, od kiedy stoi),
- `review` („Do sprawdzenia”) — praca agentów skończona (wszystkie tickety gotowe albo odrzucone, agent
  już nie pracuje), ale potrzebny krok człowieka: test ręczny (`check: manual`) albo scalenie gałęzi,
- `done` („Gotowe”) — skończone, `check: auto` i nic do scalenia; plik karty się nie zmienia,
  użytkownik potwierdza, przeciągając kartę do Gotowe.
Karta przeniesiona ręcznie (zapis karty po ostatniej aktywności skończonej pracy — np. „do poprawy”
z Do sprawdzenia z powrotem do Do zrobienia) nie wraca do Do sprawdzenia, dopóki agent znów nad nią
nie popracuje.
"""
import os
import re
import time
from dataclasses import dataclass, field

from ..agents import gitinfo
from ..agents.state import waits_for_user
from ..worktrees.git import MAIN_CANDIDATES, Git, pick_main_branch
from .model import CARD_ID_RE, DEFAULT_CHECK

DONE_STATES = ("gotowe", "odrzucone")
LINK_SESSION, LINK_PLAN, LINK_CARD_BRANCH, LINK_BRANCH = "session", "plan", "card-branch", "branch"
EXPLICIT_LINKS = (LINK_SESSION, LINK_PLAN)
MERGE_TTL = 30.0       # wynik „czy gałąź scalona” trzymamy tyle sekund (git tylko dla skończonej pracy)
NOTE_MAX = 160
QUESTION_MAX = 120
NON_BRANCHES = set(MAIN_CANDIDATES) | {"HEAD", ""}


@dataclass
class Link:
    how: str                         # session | plan | card-branch | branch
    board: dict | None = None        # tablica koordynatora albo None (zwykła sesja)
    session: dict | None = None      # sesja z /Agents (dla koordynatora: jego sesja, jeśli żyje)
    scope: list | None = None        # tickety karty (podzbiór tablicy) albo None — cała tablica
    activity: float = 0.0
    extra: dict = field(default_factory=dict)


class BranchState:
    """„Czy gałąź jest scalona do głównej” w repozytorium — lokalny git, z krótką pamięcią."""

    def __init__(self, git: Git | None = None, clock=time.time, ttl: float = MERGE_TTL):
        self.git = git or Git(timeout=5)
        self.clock = clock
        self.ttl = ttl
        self._cache: dict[tuple, tuple[float, object]] = {}

    def main_branch(self, repo: str) -> str:
        return self._cached(("main", repo), lambda: self._main_branch(repo))

    def merged(self, repo: str, branch: str) -> bool | None:
        """True — scalona albo już usunięta (po scaleniu), False — ma commity spoza głównej,
        None — nie wiadomo (brak repozytorium albo gałęzi głównej, dziwna nazwa)."""
        if not repo or not branch or branch.startswith("-") or ".." in branch or any(c.isspace() for c in branch):
            return None
        return self._cached(("merged", repo, branch), lambda: self._merged(repo, branch))

    def _cached(self, key, compute):
        now = self.clock()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < self.ttl:
            return hit[1]
        value = compute()
        self._cache[key] = (now, value)
        return value

    def _main_branch(self, repo: str) -> str:
        if not os.path.isdir(repo):
            return ""
        refs = [f"refs/heads/{name}" for name in MAIN_CANDIDATES]
        result = self.git.run(repo, "for-each-ref", "--format=%(refname:short)", *refs)
        if result.code != 0:
            return ""
        existing = {line.strip() for line in result.out.splitlines() if line.strip()}
        return pick_main_branch(existing, gitinfo.describe(repo).branch)

    def _merged(self, repo: str, branch: str):
        main = self.main_branch(repo)
        if not main:
            return None
        if branch == main:
            return True
        result = self.git.run(repo, "merge-base", "--is-ancestor", f"refs/heads/{branch}", f"refs/heads/{main}")
        if result.code == 0:
            return True
        if result.code == 1:
            return False
        exists = self.git.run(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        return True if exists.code == 1 else None  # gałęzi nie ma — usunięta po scaleniu


class CardDeriver:
    """derive(card) dla migawki tablicy. `context()` buduje indeksy raz na migawkę (BoardMonitor);
    samo wywołanie obiektu to jednorazowy kontekst (np. store.snapshot(derive=...) w testach)."""

    def __init__(self, sessions_source=lambda: [], boards_source=lambda: [], branches: BranchState | None = None,
                 clock=time.time):
        self.sessions_source = sessions_source
        self.boards_source = boards_source
        self.branches = branches or BranchState(clock=clock)
        self.clock = clock
        self._roots: dict[str, str] = {}

    def context(self) -> "Context":
        return Context(list(self.sessions_source() or []), list(self.boards_source() or []), self.branches,
                       self.clock(), self.repo_of)

    def __call__(self, card: dict) -> dict | None:
        return self.context()(card)

    def repo_of(self, cwd: str) -> str:
        """Główny katalog repozytorium (bez dowiązań) dla katalogu sesji — z pamięcią."""
        if not cwd:
            return ""
        if cwd not in self._roots:
            root = gitinfo.main_root(cwd)
            self._roots[cwd] = os.path.realpath(root) if root else ""
        return self._roots[cwd]


class Context:
    def __init__(self, sessions: list, boards: list, branches: BranchState, now: float, repo_of):
        self.now = now
        self.branches = branches
        self.repo_of = repo_of
        self.sessions: dict[str, dict] = {}
        self.sessions_by_sid: dict[str, dict] = {}
        for s in sessions:
            if isinstance(s, dict) and s.get("id"):
                self.sessions.setdefault(s["id"], s)
                if s.get("sessionId"):
                    self.sessions_by_sid.setdefault(str(s["sessionId"]), s)
        self.boards = [b for b in boards if isinstance(b, dict) and b.get("id")]
        self.boards_by_id = {b["id"]: b for b in self.boards}
        self.boards_by_sid = {str(b.get("sessionId")): b for b in self.boards if b.get("sessionId")}

    # --- wejście ------------------------------------------------------------------------------

    def __call__(self, card: dict) -> dict | None:
        link = self.link(card)
        if link is None:
            return None
        if link.board is not None:
            return self._from_board(card, link)
        return self._from_session(card, link)

    # --- połączenie karty z pracą -------------------------------------------------------------

    def link(self, card: dict) -> Link | None:
        column = card.get("column") or "backlog"
        if column not in ("todo", "backlog"):
            return None
        found = self._by_session(card) or self._by_plan(card)
        if found is None and column == "todo":
            found = self._by_card_branch(card) or self._by_branch(card)
        if found is None:
            return None
        if found.board is not None:
            found.board = self._successor(found.board) if found.how == LINK_SESSION else found.board
            found.session = self.sessions.get(found.board["id"])
            if found.scope is None:
                found.scope = self._narrow(found.board, card)
        return found

    def _by_session(self, card: dict) -> Link | None:
        wanted = (card.get("session") or "").strip()
        if not wanted:
            return None
        board = self.boards_by_id.get(wanted) or (self.boards_by_sid.get(wanted) if ":" not in wanted else None)
        if board is not None:
            return Link(LINK_SESSION, board=board)
        session = self.sessions.get(wanted) or (self.sessions_by_sid.get(wanted) if ":" not in wanted else None)
        if session is not None:
            board = self.boards_by_id.get(session["id"])
            return Link(LINK_SESSION, board=board, session=session)
        return None

    def _by_plan(self, card: dict) -> Link | None:
        num = _num(card.get("id"))
        if num is None:
            return None
        matches = [b for b in self.boards if _num(str((b.get("plan") or {}).get("card") or "")) == num]
        best = _freshest(matches)
        return Link(LINK_PLAN, board=best) if best is not None else None

    def _by_card_branch(self, card: dict) -> Link | None:
        num = _num(card.get("id"))
        if num is None:
            return None
        token = re.compile(rf"(?:^|[/_.-])k-?{num}(?=$|[/_.-])", re.IGNORECASE)
        repo = _real(card.get("repo"))
        return self._branch_candidates(LINK_CARD_BRANCH, repo, lambda branch: bool(token.search(branch)),
                                       require_repo=False)

    def _by_branch(self, card: dict) -> Link | None:
        wanted = _branch(card.get("branch"))
        repo = _real(card.get("repo"))
        if wanted in NON_BRANCHES or not repo:
            return None
        return self._branch_candidates(LINK_BRANCH, repo, lambda branch: branch == wanted, require_repo=True)

    def _branch_candidates(self, how: str, repo: str, matches, require_repo: bool) -> Link | None:
        candidates: list[Link] = []
        for b in self.boards:
            if repo and self._board_repo(b) != repo:
                continue
            if not repo and require_repo:
                continue
            if _branch(b.get("branch")) not in NON_BRANCHES and matches(_branch(b.get("branch"))):
                candidates.append(Link(how, board=b, activity=_ts(b.get("lastActivityTs"))))
                continue
            scope = [t for t in b.get("tickets") or [] if _branch(t.get("branch")) not in NON_BRANCHES
                     and matches(_branch(t.get("branch")))]
            if scope:
                candidates.append(Link(how, board=b, scope=scope, activity=_ts(b.get("lastActivityTs"))))
        for s in self.sessions.values():
            if s["id"] in self.boards_by_id or _branch(s.get("branch")) in NON_BRANCHES:
                continue
            if repo and self.repo_of(s.get("cwd") or "") != repo:
                continue
            if matches(_branch(s.get("branch"))):
                candidates.append(Link(how, session=s, activity=_ts(s.get("lastActivityTs"))))
        if not candidates:
            return None
        return max(candidates, key=lambda c: (c.activity, _live(c)))

    def _board_repo(self, board: dict) -> str:
        return _real(board.get("repoPath")) or self.repo_of(board.get("cwd") or "")

    def _successor(self, board: dict) -> dict:
        """Koordynator z karty już nie działa, a ten sam plan prowadzi nowsza, żywa sesja (np. po
        wyczerpaniu kontekstu) — karta idzie za planem."""
        path = (board.get("plan") or {}).get("path")
        if board.get("live") or not path:
            return board
        newer = [b for b in self.boards if b is not board and b.get("live") and (b.get("plan") or {}).get("path") == path
                 and _ts(b.get("lastActivityTs")) > _ts(board.get("lastActivityTs"))]
        return _freshest(newer) or board

    @staticmethod
    def _narrow(board: dict, card: dict) -> list | None:
        """Tickety karty: gałąź karty inna niż gałąź koordynatora i są tickety na niej (koordynator
        prowadzi kilka kart naraz). Inaczej None — karta to cała praca koordynatora."""
        wanted = _branch(card.get("branch"))
        if wanted in NON_BRANCHES or wanted == _branch(board.get("branch")):
            return None
        scope = [t for t in board.get("tickets") or [] if _branch(t.get("branch")) == wanted]
        return scope or None

    # --- stan z koordynatora ------------------------------------------------------------------

    def _from_board(self, card: dict, link: Link) -> dict:
        board, session = link.board, link.session
        whole = link.scope is None
        tickets = list(board.get("tickets") or []) if whole else link.scope
        counted = [t for t in tickets if t.get("state") != "odrzucone"]
        done = sum(1 for t in counted if t.get("state") == "gotowe")
        total = len(counted)
        unfinished = [t for t in tickets if t.get("state") not in DONE_STATES]
        state, since = self._agent_state(board, session)
        out = self._shape(card, link, board=board, session=session, done=done, total=total, state=state,
                          tickets=[] if whole else [_tid(t) for t in tickets])

        asked = [t for t in tickets if t.get("state") == "czeka" and _question(t)]
        if asked:
            return self._fill(out, "waiting", f"{_tid(asked[0])}: „{_clip(_question(asked[0]), QUESTION_MAX)}”")
        agent = session if session is not None else {"state": board.get("state"), "reason": board.get("reason")}
        if waits_for_user(agent) and not self._question_elsewhere(board, tickets, whole):
            return self._fill(out, "waiting", self._waiting_note(agent, board))

        plan_done = ((board.get("plan") or {}).get("status") or "") == "done"
        finished = (total > 0 and not unfinished) or (total == 0 and plan_done)
        if finished and state != "working":
            return self._finished(card, link, out, tickets)
        return self._fill(out, "progress", self._progress_note(state, since, unfinished, agent, tickets))

    def _finished(self, card: dict, link: Link, out: dict, tickets: list) -> dict:
        board = link.board
        stamps = [_ts(t.get("lastActivityTs")) for t in tickets] if link.scope is not None else []
        finished_at = max(stamps or [_ts(board.get("lastActivityTs"))])
        if _ts(card.get("updatedTs")) > finished_at > 0:
            # Użytkownik ruszył kartę po skończonej pracy (np. „do poprawy”) — zostaje w swojej kolumnie.
            return self._fill(out, "", "przeniesiona ręcznie po skończonej pracy — wróci do W toku, "
                                       "gdy agent znów nad nią usiądzie")
        repo = _real(card.get("repo")) or self._board_repo(board)
        branches = []
        for name in [_branch(card.get("branch"))] + [_branch(t.get("branch")) for t in tickets] + \
                [_branch(board.get("branch"))]:
            if name not in NON_BRANCHES and name not in branches:
                branches.append(name)
        if _branch(card.get("branch")) not in NON_BRANCHES:
            branches = branches[:1]  # gałąź karty to jej praca — tylko ona musi być scalona
        main = self.branches.main_branch(repo) if repo else ""
        branches = [b for b in branches if b != main]
        unmerged = [b for b in branches if self.branches.merged(repo, b) is not True]
        steps, notes = [], []
        if out["check"] == "manual":
            steps.append("przetestuj ręcznie")
            notes.append("do sprawdzenia ręcznie")
        if unmerged:
            more = f" (i {len(unmerged) - 1} innych)" if len(unmerged) > 1 else ""
            steps.append(f"scal gałąź {unmerged[0]}{more}")
            notes.append(f"do scalenia: {unmerged[0]}{more}")
            out["branch"] = unmerged[0]
        if steps:
            out["nextStep"] = ", potem ".join(steps)
            return self._fill(out, "review", "; ".join(notes))
        out["nextStep"] = "przenieś do Gotowe"
        return self._fill(out, "done", "✓ gotowe i scalone" if branches else "✓ gotowe")

    def _question_elsewhere(self, board: dict, tickets: list, whole: bool) -> bool:
        """Koordynator czeka na pytanie o inny ticket niż te z karty — karta nie „czeka na mnie”."""
        if whole:
            return False
        if any(_question(t) for t in tickets):
            return False
        return any(_question(t) for t in board.get("tickets") or [] if t not in tickets)

    # --- stan ze zwykłej sesji ----------------------------------------------------------------

    def _from_session(self, card: dict, link: Link) -> dict:
        session = link.session
        state, since = session.get("state") or "ended", _ts(session.get("sinceTs"))
        out = self._shape(card, link, board=None, session=session, done=0, total=0, state=state, tickets=[])
        if waits_for_user(session):
            return self._fill(out, "waiting", self._waiting_note(session, None))
        if state == "working":
            action = (session.get("lastAction") or "").strip()
            return self._fill(out, "progress", f"agent pracuje: {action}" if action else "agent pracuje")
        return self._fill(out, "progress", _stalled(state, since or _ts(session.get("lastActivityTs")), self.now))

    # --- teksty i kształt ---------------------------------------------------------------------

    def _agent_state(self, board: dict, session: dict | None) -> tuple[str, float]:
        if session is not None:
            return session.get("state") or "ended", _ts(session.get("sinceTs")) or _ts(session.get("lastActivityTs"))
        return board.get("state") or "ended", _ts(board.get("lastActivityTs"))

    def _shape(self, card, link, board, session, done, total, state, tickets) -> dict:
        source = board or session or {}
        branch = _branch(card.get("branch"))
        if branch in NON_BRANCHES:
            scoped = [_branch(t.get("branch")) for t in (link.scope or [])]
            branch = next((b for b in scoped if b not in NON_BRANCHES), "") or \
                next((b for b in (_branch(source.get("branch")),) if b not in NON_BRANCHES), "")
        return {
            "column": "", "done": done, "total": total, "tool": source.get("tool") or "", "note": "",
            "nextStep": "", "check": card.get("check") or DEFAULT_CHECK, "branch": branch,
            "session": source.get("id") or "", "coordinator": board.get("id") if board else "",
            "link": link.how, "agentState": state, "tickets": tickets,
        }

    @staticmethod
    def _fill(out: dict, column: str, note: str) -> dict:
        out["column"] = column
        out["note"] = _clip(note, NOTE_MAX)
        if column == "waiting" and not out["nextStep"]:
            out["nextStep"] = "odpowiedz agentowi"
        return out

    @staticmethod
    def _waiting_note(agent: dict, board: dict | None) -> str:
        question = _question(board) if board else ""
        if question:
            return f"czeka na odpowiedź: „{_clip(question, QUESTION_MAX)}”"
        if agent.get("reason") == "approval":
            action = (agent.get("lastAction") or "").strip()
            return f"czeka na zgodę: {action}" if action else "czeka na zgodę na narzędzie"
        if agent.get("reason") == "question":
            return "agent zadał pytanie i czeka na odpowiedź"
        return "agent czeka na ciebie"

    def _progress_note(self, state: str, since: float, unfinished: list, agent: dict, tickets: list) -> str:
        ids = [_tid(t) for t in unfinished]
        listed = ", ".join(ids[:3]) + (f" (+{len(ids) - 3})" if len(ids) > 3 else "")
        planned = [_tid(t) for t in unfinished if t.get("state") == "czeka"]
        tail = f"; {', '.join(planned[:2])} czeka według planu" if planned else ""
        if state == "working":
            current = next((t for t in unfinished if t.get("running")), unfinished[0] if unfinished else None)
            if current is None:
                action = (agent.get("lastAction") or "").strip()
                return "koordynator kończy: wszystkie tickety gotowe" + (f" ({action})" if action else "")
            what = (current.get("now") or current.get("note") or current.get("word") or "w toku").strip()
            more = f" (+{len(unfinished) - 1})" if len(unfinished) > 1 else ""
            return f"{_tid(current)}: {_first_sentence(what)}{more}{tail}"
        stalled = _stalled(state, since, self.now)
        return f"{stalled}, w toku: {listed}{tail}" if ids else stalled + tail


# --- pomocnicze ---------------------------------------------------------------------------------

def _stalled(state: str, since: float, now: float) -> str:
    """„agent bezczynny od 16:46”, „agent skończył turę o 16:46”, „sesja zakończona” — od kiedy stoi."""
    when = _clock(since, now)
    if state == "idle":
        return f"agent bezczynny od {when}" if when else "agent bezczynny"
    if state == "done":
        return f"agent skończył turę o {when}" if when else "agent skończył turę"
    if state == "working":
        return "agent pracuje"
    return "sesja zakończona"


def _clock(ts: float, now: float) -> str:
    if not ts:
        return ""
    local, today = time.localtime(ts), time.localtime(now)
    if (local.tm_year, local.tm_yday) == (today.tm_year, today.tm_yday):
        return time.strftime("%H:%M", local)
    return time.strftime("%d.%m %H:%M", local)


def _num(card_id) -> int | None:
    match = CARD_ID_RE.match(str(card_id or "").strip())
    return int(match.group(1)) if match else None


def _branch(value) -> str:
    """Nazwa gałęzi bez dopisków („feat/x (8d20e9d2)” → „feat/x”)."""
    text = str(value or "").strip()
    return text.split()[0] if text else ""


def _real(path) -> str:
    return os.path.realpath(path) if path else ""


def _ts(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _tid(ticket: dict) -> str:
    return str(ticket.get("id") or ticket.get("key") or "").lstrip("~")


def _question(item) -> str:
    question = (item or {}).get("question") if isinstance(item, dict) else None
    if isinstance(question, dict):
        return " ".join(str(question.get("text") or "").split())
    return " ".join(str(question or "").split())


def _live(link: Link) -> bool:
    if link.board is not None:
        return bool(link.board.get("live"))
    return (link.session or {}).get("state") not in ("ended", None)


def _freshest(boards: list) -> dict | None:
    if not boards:
        return None
    return max(boards, key=lambda b: (bool(b.get("live")), _ts(b.get("lastActivityTs"))))


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    cut = re.search(r"(?<=[.!?])\s", text)
    return (text[:cut.start()] if cut else text).rstrip(".")


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
