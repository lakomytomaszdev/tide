"""Stan karty tablicy z prawdziwej pracy agentów (PT-5) — bez Qt, testowane bezpośrednio.

Wejście: karta z migawki (`board/store.py`, z kluczem `repo` jej projektu), sesje z /Agents
(`AgentMonitor.sessions`) i tablice koordynatorów z /Coordination (`CoordinationMonitor.boards`).
Wyjście: pole `derived` karty albo None (karta zostaje w swojej kolumnie ręcznej).

Połączenie karty z pracą (pierwsze pasujące):
1. `session` karty — id sesji albo koordynatora jak w /Agents i /Coordination (`claude:<id>`,
   `codex:<id>`; samo `<id>` też) — niezależnie od tego, gdzie sesja jest teraz (cwd, projekt, gałąź);
   sesja, która już nie działa, ustępuje nowszej, żywej pracy karty (reguły 2–5),
2. uruchomienie z karty (PT-28): sesja tego samego narzędzia, której katalog startu (Claude Code — katalog
   dziennika, Codex — cwd z session_meta) to `worktree` karty (bez worktree — repozytorium projektu),
   a początek wypada w oknie LAUNCH_WINDOW po `launched`; sesja przypięta do innej karty się nie liczy,
3. `card: K-12` we frontmatterze planu koordynatora (czytane ostrożnie — klucza może jeszcze nie być),
4. ID karty w nazwie gałęzi (`claude/k-12-eksport`, propozycja `RenderFlow`) koordynatora, jego ticketu
   albo sesji w repozytorium projektu karty albo jej dodatkowego projektu (`projects`),
5. `branch` karty równa gałęzi koordynatora, ticketu albo sesji w tych repozytoriach (także w ich
   worktrees); gałęzi głównych (master, main, develop) nie dopasowujemy.
Przy kilku dopasowaniach jednej reguły wygrywa najświeższa aktywność. Karta w Backlogu dostaje stan
tylko z połączenia jawnego (1–3) — gałąź może być przypadkowa albo stara, a Backlog to „kiedyś”.
Połączenie z reguł 2, 4 i 5 (4 i 5 — tylko jedno dopasowanie) usługa utrwala w `session` karty
(`sticky`, BoardMonitor) — później sesja może wejść gdzie indziej (`cd`), a karta jej nie zgubi.
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
Karta przeniesiona ręcznie po ostatniej aktywności skończonej pracy (`movedTs` — zmiana kolumny,
nie edycja; np. „do poprawy” z panelu z powrotem do Do zrobienia) nie wraca do Do sprawdzenia, dopóki
agent znów nad nią nie popracuje.
Ticket w `czeka` z samego planu (bez pytania w dzienniku) to „Czeka na mnie”, gdy agent nie pracuje —
koordynator zapisał, że czeka na decyzję użytkownika, i stanął; gdy pracuje, to dalej „W toku”.
"""
import functools
import os
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..agents import gitinfo
from ..agents.state import waits_for_user
from ..reports.gitlog import merged_branch
from ..worktrees.git import MAIN_CANDIDATES, Git, pick_main_branch
from .model import CARD_ID_RE, DEFAULT_CHECK

DONE_STATES = ("gotowe", "odrzucone")
LINK_SESSION, LINK_PLAN, LINK_CARD_BRANCH, LINK_BRANCH = "session", "plan", "card-branch", "branch"
LINK_LAUNCH = "launch"
EXPLICIT_LINKS = (LINK_SESSION, LINK_LAUNCH, LINK_PLAN)
STICKY_LINKS = (LINK_LAUNCH, LINK_CARD_BRANCH, LINK_BRANCH)   # utrwalane w `session` karty (PT-28)
LAUNCH_WINDOW = 300.0  # s — sesja uruchomiona z karty zaczyna się najwyżej tyle po `launched`…
LAUNCH_SLACK = 120.0   # …albo do tylu sekund przed nim (`launched` zapisuje okno już po starcie agenta)
PERSIST_DELAY = 30.0   # s po `launched` — dopiero potem utrwalamy połączenie w pliku karty
LAUNCH_PERSIST_SLACK = 15.0  # utrwalamy „launch” tylko dla sesji, która zaczęła się najwyżej tyle przed `launched`
MERGE_TTL = 30.0       # wynik „czy gałąź scalona” trzymamy tyle sekund (git tylko dla skończonej pracy)
GONE_TTL = 600.0       # gałęzi, której nie ma, nie sprawdzamy częściej
MERGE_LOG = 1000       # tyle ostatnich commitów scalenia w głównej przeglądamy dla usuniętej gałęzi
PENDING = "pending"    # wynik gita jeszcze w drodze (BranchState w tle)
MISSING = "missing"    # gałęzi nie ma i nie widać jej scalenia (w dodatkowym repozytorium: nie była tam potrzebna)
NOTE_MAX = 160
QUESTION_MAX = 120
NON_BRANCHES = set(MAIN_CANDIDATES) | {"HEAD", ""}
CARD_TOKEN = re.compile(r"(?:^|[/_.-])k-?([1-9][0-9]{0,8})(?=$|[/_.-])", re.IGNORECASE)  # ID karty w gałęzi


@dataclass
class Link:
    how: str                         # session | plan | card-branch | branch
    board: dict | None = None        # tablica koordynatora albo None (zwykła sesja)
    session: dict | None = None      # sesja z /Agents (dla koordynatora: jego sesja, jeśli żyje)
    scope: list | None = None        # tickety karty (podzbiór tablicy) albo None — cała tablica
    activity: float = 0.0
    extra: dict = field(default_factory=dict)


class BranchState:
    """„Czy gałąź jest scalona do głównej” w repozytorium — lokalny git, z pamięcią.

    background=True (usługa): git idzie w jednym wątku roboczym, `merged()` odpowiada od razu z pamięci
    (świeżej albo starej), a brak wyniku to PENDING; po każdym nowym wyniku woła `on_result()` (z wątku
    roboczego — BoardMonitor zamienia to na sygnał Qt i przelicza karty). background=False — od razu
    (testy, store.snapshot(derive=...)). Gałąź, której nie ma, pamiętamy dłużej (GONE_TTL) — nie
    sprawdzamy jej w kółko."""

    def __init__(self, git: Git | None = None, clock=time.time, ttl: float = MERGE_TTL, background: bool = False,
                 on_result=None):
        self.git = git or Git(timeout=5)
        self.clock = clock
        self.ttl = ttl
        self.on_result = on_result
        self._cache: dict[tuple, tuple[float, float, object]] = {}   # klucz → (czas, ważność, wynik)
        self._lock = threading.Lock()
        self._pending: set[tuple] = set()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tide-board-git") if background else None

    def stop(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def main_branch(self, repo: str) -> str:
        if not repo:
            return ""
        value = self._lookup(("main", repo), lambda: (self._main_branch(repo), self.ttl))
        return "" if value is PENDING else value

    def merged(self, repo: str, branch: str):
        """True — scalona (albo usunięta po scaleniu: jest w temacie commita scalenia w głównej),
        False — ma commity spoza głównej, None — nie wiadomo (brak repozytorium albo gałęzi głównej,
        dziwna nazwa), MISSING — gałęzi nie ma i nie widać jej scalenia (dla głównego repozytorium
        karty to też „nie wiadomo”), PENDING — sprawdzamy w tle."""
        if not repo or not branch or branch.startswith("-") or ".." in branch or any(c.isspace() for c in branch):
            return None
        return self._lookup(("merged", repo, branch), lambda: self._merged(repo, branch))

    def _lookup(self, key, compute):
        now = self.clock()
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None and now - hit[0] < hit[1]:
            return hit[2]
        if self._pool is None:
            return self._store(key, compute)
        with self._lock:
            busy = key in self._pending
            self._pending.add(key)
        if not busy:
            try:
                self._pool.submit(self._background, key, compute)
            except RuntimeError:  # pula zamknięta (usługa się kończy)
                with self._lock:
                    self._pending.discard(key)
        return hit[2] if hit is not None else PENDING  # stary wynik, dopóki nie ma świeżego

    def _background(self, key, compute) -> None:
        try:
            previous = self._cache.get(key)
            value = self._store(key, compute)
        finally:
            with self._lock:
                self._pending.discard(key)
        if self.on_result is not None and (previous is None or previous[2] != value):
            try:
                self.on_result()
            except Exception as exc:  # przeliczenie kart to dodatek
                print(f"tide-daemon: tablica: po sprawdzeniu gałęzi: {exc!r}", flush=True)

    def _store(self, key, compute):
        value, ttl = compute()
        with self._lock:
            self._cache[key] = (self.clock(), ttl, value)
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
        hit = self._cache.get(("main", repo))
        main = hit[2] if hit is not None and self.clock() - hit[0] < hit[1] else \
            self._store(("main", repo), lambda: (self._main_branch(repo), self.ttl))
        if not main:
            return None, self.ttl
        if branch == main:
            return True, self.ttl
        result = self.git.run(repo, "merge-base", "--is-ancestor", f"refs/heads/{branch}", f"refs/heads/{main}")
        if result.code == 0:
            return True, self.ttl
        if result.code == 1:
            return False, self.ttl
        exists = self.git.run(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        if exists.code != 1:
            return None, self.ttl
        # Gałęzi nie ma: usunięta po scaleniu albo nigdy jej tu nie było (np. nazwa z planu). Scalenie
        # poznajemy po temacie commita scalenia w głównej („Merge branch 'feat/x'”); scalenia szybkiego
        # (fast-forward, squash) stąd nie widać — wtedy „nie wiadomo”, czyli „do scalenia” dla człowieka.
        log = self.git.run(repo, "log", "--merges", f"--max-count={MERGE_LOG}", "--format=%s", f"refs/heads/{main}")
        merged = log.code == 0 and any(merged_branch(line) == branch for line in log.out.splitlines())
        return (True if merged else MISSING), GONE_TTL


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

    def stop(self) -> None:
        self.branches.stop()

    def context(self, claimed=()) -> "Context":
        """claimed: wartości `session` wszystkich kart — sesji przypiętej do jednej karty nie łączymy
        z inną przez uruchomienie (reguła 2)."""
        return Context(list(self.sessions_source() or []), list(self.boards_source() or []), self.branches,
                       self.clock(), self.repo_of, claimed)

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
    """Jedno przeliczenie migawki. Indeksy powstają raz: repozytoria (bez dowiązań) koordynatorów
    i sesji, (repozytorium, gałąź) → praca na tej gałęzi oraz numer karty z nazwy gałęzi → gałęzie;
    karta robi już tylko wyszukania w słownikach (80 kart × 60 koordynatorów to milisekundy)."""

    def __init__(self, sessions: list, boards: list, branches: BranchState, now: float, repo_of, claimed=()):
        self.now = now
        self.branches = branches
        self.repo_of = repo_of
        self.claimed = {str(c).strip() for c in claimed or () if str(c or "").strip()}
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
        self.board_repos = {b["id"]: _real(b.get("repoPath")) or self.repo_of(b.get("cwd") or "") for b in self.boards}
        # (repozytorium, gałąź) → [(tablica albo None, sesja albo None, tickety albo None)]
        self.work: dict[tuple, list] = defaultdict(list)
        for b in self.boards:
            repo, own = self.board_repos[b["id"]], _branch(b.get("branch"))
            if own not in NON_BRANCHES:
                self.work[(repo, own)].append((b, None, None))
            scoped: dict[str, list] = defaultdict(list)
            for t in b.get("tickets") or []:
                name = _branch(t.get("branch"))
                if name not in NON_BRANCHES and name != own:
                    scoped[name].append(t)
            for name, tickets in scoped.items():
                self.work[(repo, name)].append((b, None, tickets))
        for sess in self.sessions.values():
            name = _branch(sess.get("branch"))
            if sess["id"] in self.boards_by_id or name in NON_BRANCHES:
                continue
            self.work[(self.repo_of(sess.get("cwd") or ""), name)].append((None, sess, None))
        self.card_branches: dict[int, list] = defaultdict(list)   # numer karty z gałęzi → klucze `work`
        for key in self.work:
            for match in CARD_TOKEN.finditer(key[1]):
                self.card_branches[int(match.group(1))].append(key)

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
        found = self._by_session(card)
        if found is not None and not _live(found if found.board is None else
                                           Link(found.how, board=self._successor(found.board))):
            # Przypięta sesja już nie działa — nowsza, żywa praca karty (ponowne uruchomienie, nowa sesja
            # na gałęzi karty) wygrywa; bez niej zostaje przypięta.
            newer = self._by_launch(card) or (self._by_card_branch(card) or self._by_branch(card)
                                               if column == "todo" else None)
            pinned = _ts((found.board or found.session or {}).get("lastActivityTs"))
            if newer is not None and _live(newer) and newer.activity > pinned:
                found = newer
        found = found or self._by_launch(card) or self._by_plan(card)
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

    def _by_launch(self, card: dict) -> Link | None:
        """Sesja uruchomiona z karty (reguła 2): katalog startu = worktree karty (albo repozytorium projektu),
        to samo narzędzie, początek w oknie po `launched`. Kilka — najbliższa chwili uruchomienia."""
        launched = _ts(card.get("launchedTs"))
        place = (card.get("worktree") or "").strip() or (card.get("repo") or "").strip()
        if not launched or not place:
            return None
        tool = (card.get("tool") or "").strip()
        own = (card.get("session") or "").strip()
        best, best_gap = None, None
        for sess in self.sessions.values():
            if sess.get("subagent") or (tool and sess.get("tool") != tool):
                continue
            if (sess["id"] in self.claimed or str(sess.get("sessionId") or "") in self.claimed) and \
                    own not in (sess["id"], str(sess.get("sessionId") or "")):
                continue
            started = _ts(sess.get("startedTs"))
            if not started or not (launched - LAUNCH_SLACK <= started <= launched + LAUNCH_WINDOW):
                continue
            if not launched_in(sess, place):
                continue
            gap = abs(started - launched)
            if best_gap is None or gap < best_gap:
                best, best_gap = sess, gap
        if best is None:
            return None
        return Link(LINK_LAUNCH, board=self.boards_by_id.get(best["id"]), session=best,
                    activity=_ts(best.get("lastActivityTs")))

    def sticky(self, card: dict) -> tuple[str, str, str] | None:
        """(id sesji, sposób, gałąź) do utrwalenia w karcie albo None: karta bez `session` połączona przez
        uruchomienie z karty albo jednoznacznie (jedno dopasowanie) po gałęzi (PT-28).

        - nic przez PERSIST_DELAY po `launched` (świeże uruchomienie: sesja jeszcze nie zapisała dziennika,
          a pierwsze przeliczenie mogłoby przypiąć obcą sesję z tego samego katalogu),
        - „launch” tylko dla sesji, która zaczęła się najwyżej LAUNCH_PERSIST_SLACK przed `launched`
          (wcześniejsza to raczej inna sesja w tym katalogu; w pamięci karta i tak ją pokaże),
        - po gałęzi nigdy w oknie uruchomienia (np. stara, zakończona sesja tej samej gałęzi po ponownym
          uruchomieniu),
        - połączenie z częścią ticketów koordynatora (gałąź karty ≠ gałąź koordynatora) utrwalamy razem
          z gałęzią tych ticketów — inaczej reguła 1 wzięłaby całego koordynatora; tickety z kilku gałęzi —
          nie utrwalamy."""
        if (card.get("session") or "").strip():
            return None
        launched = _ts(card.get("launchedTs"))
        if launched and self.now < launched + PERSIST_DELAY:
            return None
        link = self.link(card)
        if link is None or link.how not in STICKY_LINKS:
            return None
        if link.how == LINK_LAUNCH:
            if _ts((link.session or {}).get("startedTs")) < launched - LAUNCH_PERSIST_SLACK:
                return None
        else:
            if link.extra.get("candidates", 1) != 1:
                return None
            if launched and self.now < launched + LAUNCH_WINDOW:
                return None
        target = link.board or link.session or {}
        sid = str(target.get("id") or "")
        if not sid or sid in self.claimed or str(target.get("sessionId") or "") in self.claimed:
            return None
        branch = ""
        if link.scope is not None and _branch(card.get("branch")) in NON_BRANCHES:
            names = {_branch(t.get("branch")) for t in link.scope} - NON_BRANCHES
            if len(names) != 1:
                return None
            branch = names.pop()
        return sid, link.how, branch

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
        repos = _repos(card)
        keys = [k for k in self.card_branches.get(num, ()) if not repos or k[0] in repos]
        return self._pick(LINK_CARD_BRANCH, keys)

    def _by_branch(self, card: dict) -> Link | None:
        wanted = _branch(card.get("branch"))
        repos = _repos(card)
        if wanted in NON_BRANCHES or not repos:
            return None
        return self._pick(LINK_BRANCH, [(repo, wanted) for repo in repos])

    def _pick(self, how: str, keys: list) -> Link | None:
        """Najświeższa praca spośród dopasowanych gałęzi; jedna pozycja na koordynatora (cały
        koordynator wygrywa z jego ticketami, tickety z kilku gałęzi się sumują)."""
        boards: dict[str, Link] = {}
        candidates: list[Link] = []
        for key in keys:
            for b, sess, scope in self.work.get(key, ()):
                if b is None:
                    candidates.append(Link(how, session=sess, activity=_ts(sess.get("lastActivityTs"))))
                    continue
                current = boards.get(b["id"])
                if current is None:
                    boards[b["id"]] = Link(how, board=b, scope=list(scope) if scope else None,
                                           activity=_ts(b.get("lastActivityTs")))
                elif current.scope is not None:
                    current.scope = None if scope is None else current.scope + [t for t in scope
                                                                                 if t not in current.scope]
        candidates.extend(boards.values())
        if not candidates:
            return None
        best = max(candidates, key=lambda c: (c.activity, _live(c)))
        best.extra["candidates"] = len(candidates)
        return best

    def _board_repo(self, board: dict) -> str:
        repo = self.board_repos.get(board.get("id"))
        return repo if repo is not None else (_real(board.get("repoPath")) or self.repo_of(board.get("cwd") or ""))

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
        planned = [t for t in tickets if t.get("state") == "czeka"]
        if planned and state != "working":
            note = " ".join(str(planned[0].get("planNote") or "").split())
            text = "czeka na twoją decyzję według planu" + (f": {note}" if note else f" ({_tid(planned[0])})")
            return self._fill(out, "waiting", text)

        plan_done = ((board.get("plan") or {}).get("status") or "") == "done"
        finished = (total > 0 and not unfinished) or (total == 0 and plan_done)
        if finished and state != "working":
            return self._finished(card, link, out, tickets)
        return self._fill(out, "progress", self._progress_note(state, since, unfinished, agent, tickets))

    def _finished(self, card: dict, link: Link, out: dict, tickets: list) -> dict:
        board = link.board
        stamps = [_ts(t.get("lastActivityTs")) for t in tickets] if link.scope is not None else []
        finished_at = max(stamps or [_ts(board.get("lastActivityTs"))])
        moved = _ts(card.get("movedTs")) or _ts(card.get("createdTs"))
        if moved > finished_at > 0 and card.get("movedFrom") in ("review", "done"):
            # Użytkownik przeniósł kartę z Do sprawdzenia albo Gotowe po skończonej pracy („do poprawy”) — zostaje
            # w swojej kolumnie. Założenie karty, ustawienie `session` i przeniesienie z innej kolumny się nie
            # liczą (PT-29: karta założona do skończonej sesji nie może od razu „wrócić ręcznie”).
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
        # Karta z kilkoma projektami (PT-28): ta sama gałąź w każdym dodatkowym repozytorium, w którym
        # powstała (gałęzi, której tam nigdy nie było, nie trzeba scalać).
        extra = [r for r in _repos(card) if r != repo]
        labels = [(os.path.basename(repo) + " " if extra else "") + b for b in unmerged]
        for other in extra:
            other_main = self.branches.main_branch(other)
            for name in branches[:1]:
                if name == other_main:
                    continue
                state = self.branches.merged(other, name)
                if state is not True and state is not MISSING:
                    unmerged.append(name)
                    labels.append(f"{os.path.basename(other)} {name}")
        steps, notes = [], []
        if out["check"] == "manual":
            steps.append("przetestuj ręcznie")
            notes.append("do sprawdzenia ręcznie")
        if unmerged:
            more = f" (i {len(unmerged) - 1} innych)" if len(unmerged) > 1 else ""
            steps.append(f"scal gałąź {labels[0]}{more}")
            notes.append(f"do scalenia: {labels[0]}{more}")
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
        if agent.get("reason") in ("question", "reply"):  # reply: pytanie na końcu tury (PT-29)
            text = " ".join(str(agent.get("waitingText") or "").split())
            return f"czeka na odpowiedź: „{_clip(text, QUESTION_MAX)}”" if text else \
                "agent zadał pytanie i czeka na odpowiedź"
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


def launched_in(session: dict, place: str) -> bool:
    """Czy sesja wystartowała w katalogu `place`: dokładnie po `launchCwd` (bez dowiązań), a gdy go nie
    ma — po `launchKey` (katalog startu zakodowany jak katalog projektu Claude Code)."""
    start = str(session.get("launchCwd") or "")
    if start:
        return _real(start.rstrip("/") or "/") == _real(place.rstrip("/") or "/")
    key = str(session.get("launchKey") or "")
    return bool(key) and key in (_escape(place.rstrip("/")), _escape(_real(place.rstrip("/") or "/")))


def _escape(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path or "")


def _repos(card: dict) -> list[str]:
    """Repozytoria karty bez dowiązań: projektu, potem dodatkowych projektów (`extraRepos`)."""
    out = []
    for path in [card.get("repo")] + list(card.get("extraRepos") or []):
        real = _real(path)
        if real and real not in out:
            out.append(real)
    return out


def _num(card_id) -> int | None:
    match = CARD_ID_RE.match(str(card_id or "").strip())
    return int(match.group(1)) if match else None


def _branch(value) -> str:
    """Nazwa gałęzi bez dopisków („feat/x (8d20e9d2)” → „feat/x”)."""
    text = str(value or "").strip()
    return text.split()[0] if text else ""


@functools.lru_cache(maxsize=4096)
def _real(path) -> str:
    """Ścieżka bez dowiązań — z pamięcią: te same repozytoria wracają przy każdym przeliczeniu."""
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
