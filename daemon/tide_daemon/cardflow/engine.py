"""Silnik przepływu karty bez Qt (PT-58): zlecenie „Od razu”, krok ticketu w tle, strażnik kroku, scalenie do gałęzi
karty i ręczne kolumny. Wszystkie metody poza `derived`, `has_journal`, `is_hidden` i `warnings` woła jeden wątek
roboczy (cardflow_monitor.py) — po kolei, więc dwie akcje jednej karty nigdy nie idą naraz.

Przebieg zlecenia (`start`): sprawdzenie (karta, projekt z repozytorium, wykonawca Claude z katalogu, program
`claude`, prefiks ticketów — bez zapisu), potem zdarzenia: `card.started`, pary „zamiar → wynik” dla rezerwacji
numeru (`coordination/reservations.py`), gałęzi i worktree karty (od gałęzi głównej), gałęzi i worktree ticketu (od
gałęzi karty) i startu kroku (`agentcmd.background()` + `Background.start()` w jednostce `systemd-run --user
--scope`), na końcu `step.started`. Błąd sprawdzenia nie zostawia niczego — ani zdarzenia, ani gałęzi.

`tick()` (co chwilę): dziennik cofnięty z zewnątrz → odtworzenie z kopii i ostrzeżenie; zaczęte akcje bez wyniku
(restart usługi) → dokończenie albo jawny błąd; trwający krok → koniec (jednostka nieaktywna) albo strażnik; ticket
gotowy → scalenie do gałęzi karty. Po restarcie usługa nie ma procesu kroku, tylko jednostkę — stan jednostki mówi,
czy krok żyje; wynik i tak bierze się z pliku wyniku, nigdy z tekstu rozmowy.

Koniec kroku: plik wyniku `<krok>/out/result.json` (`{"result": "done"|"question"|"denied"|"error", …}`); brak
pliku — błąd kroku `no-result`, zły plik — `bad-result`. Wynik i notki trafiają do `flow/<K-12>/steps/<krok>/`
w repozytorium tablicy.

Strażnik kroku: krok, który przez `stepGuardMinutes` (domyślnie 15, `$XDG_CONFIG_HOME/tide/cardflow.json`) nie
zmienia plików w worktree (bez `.git`) ani nie dopisuje do dziennika sesji, zatrzymuje `stepstop.stop_step` (commit
WIP gitem w piaskownicy albo zmiany zostają w worktree) i kończy się błędem `no-progress`. Licznik startuje od
pierwszego sprawdzenia — po restarcie usługi od nowa (krok dostaje pełny czas, nigdy mniej).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone

from ..models import queue as model_queue
from .. import codex_homes
from ..board.model import BoardError, card_num
from ..board import assignment
from ..coordination import reservations, youtrack as yt
from ..coordination import plan as source_plans
from ..board.plan_candidates import candidates as plan_candidates
from ..tasks import agentcmd, launch, stepstop
from ..tasks.core import Plan, TaskFiles
from ..worktrees.naming import slug_from_prompt
from . import handoff
from . import execution
from . import planning
from .planediting import PlanEditing
from . import plans as flow_plans
from .gitops import FlowGit, FlowGitError, worktree_for
from .journal import Journal
from .state import FlowState, derived, fold, ticket_of, graph_waves, attention_step, decision_step, guard_reason, _epoch
from .tracker import TrackerOutbox

GUARD_MINUTES = 15.0
TIMEOUT_MINUTES = 240.0
SETTINGS_FILE = "cardflow.json"
RESULTS = ("done", "question", "denied", "error")
OUTPUT_MAX = 256 * 1024
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
REQUEST_FIELDS = ("entry", "executor", "reviewer", "preferredProvider", "images", "check", "planPath", "mode")


class FlowDeferred(Exception):
    """Krok czeka na wolne miejsce; nie jest to błąd karty."""


class FlowError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def settings_path(env=None) -> str:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "tide", SETTINGS_FILE)


def guard_seconds(path: str) -> float:
    """Czas strażnika kroku z ustawień (minuty, liczba > 0); brak pliku albo zła wartość — 15 minut."""
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh).get("stepGuardMinutes", GUARD_MINUTES)
    except (OSError, ValueError, AttributeError):
        value = GUARD_MINUTES
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        value = GUARD_MINUTES
    return float(value) * 60.0


def timeout_seconds(path: str) -> float:
    """Maksymalny czas jednej próby kroku (domyślnie cztery godziny)."""
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh).get("stepTimeoutMinutes", TIMEOUT_MINUTES)
    except (OSError, ValueError, AttributeError):
        value = TIMEOUT_MINUTES
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        value = TIMEOUT_MINUTES
    return float(value) * 60.0


class FlowEngine(PlanEditing):
    def __init__(self, store, journal: Journal | None = None, *, claude_projects: str = "",
                 systemd_run: str = "", catalog=None, models_config=None, model_probe=None, codex_roots=None,
                 tmp_root: str | None = None, settings: str | None = None,
                 flow_git: FlowGit | None = None, clock=time.time, which=shutil.which, grace: float = 10.0,
                 guard_every: float = 30.0, terminal=None, task_files: TaskFiles | None = None):
        self.store = store
        self.journal = journal or Journal(store.root)
        self.claude_projects = claude_projects
        self.systemd_run = systemd_run
        self.catalog = catalog            # funkcja() → models.executors.Catalog
        self.models_config = models_config  # funkcja() → ModelsConfig (lokalny i inni dostawcy)
        self.model_probe = model_probe      # funkcja(endpoint) → stan punktu lokalnego
        self.codex_roots = codex_homes.default_homes() if codex_roots is None else codex_roots
        self.tmp_root = tmp_root or tempfile.gettempdir()
        self.settings = settings or settings_path()
        self.git = flow_git or FlowGit()
        self.clock = clock
        self.tracker = TrackerOutbox(self.journal, clock)
        self.which = which
        self.grace = grace
        self.guard_every = guard_every
        self.terminal = terminal
        self.task_files = task_files or TaskFiles()
        self.procs: dict[str, object] = {}         # krok → Popen (tylko kroki uruchomione przez tę usługę)
        self.watch: dict[str, tuple] = {}          # krok → (odcisk postępu, od kiedy bez zmian, ostatnie sprawdzenie)
        self._warnings: dict[str, str] = {}
        self._hidden: tuple = (frozenset(), ())
        self._busy: tuple = ()
        self._notices: list[dict] = []
        self._local_claims: dict[str, object] = {}

    # --- odczyt (wątek główny) -------------------------------------------------------------------

    def has_journal(self, card_id: str) -> bool:
        return self.journal.has(card_id)

    def derived(self, card: dict) -> dict | None:
        return derived(fold(self.journal.read(card.get("id") or "")), card)

    def tracker_tickets(self, card):
        st = fold(self.journal.read(card.get('id') or ''))
        if not st.started:
            return None
        events = self.journal.read(card['id'])
        merged = {key for (action, key), value in st.results.items() if action == 'merge' and value.get('ok')}
        tickets = []
        for (action, _), result in st.results.items():
            if action != 'reserve' or not result.get('ok'):
                continue
            ticket = result['ticket']
            if not any(i.get('youtrack') and i.get('action') == 'reserve' and
                       i.get('key') == result.get('key') for i in st.intents.values()):
                continue
            related = [e for e in events if e.get('ticket') == ticket or e.get('key') == ticket]
            stamp = lambda e: datetime.fromisoformat(e['ts']).timestamp()
            tickets.append({'id': ticket, 'state': self._plan_row_state(st, ticket, merged),
                            'linkedTs': stamp(result),
                            'activityTs': max([stamp(e) for e in related] or [stamp(result)])})
        return {'tickets': tickets}

    def warnings(self) -> list[dict]:
        return [{"path": self.journal.path(card), "message": text} for card, text in sorted(self._warnings.items())]

    def take_notices(self) -> list[dict]:
        notices, self._notices = self._notices, []
        return notices

    def busy_places(self) -> list[str]:
        """Worktree trwających kroków i worktree kart w toku — dla okna Worktrees zajęte jak katalog agenta (bez
        usuwania). Sesja kroku nie jest wierszem agenta, więc bez tego worktree kroku wyglądałby na wolny."""
        busy = set()
        for card in self.journal.cards():
            st = fold(self.journal.read(card))
            busy.update(step["worktree"] for step in st.running() if step.get("worktree"))
            busy.update(step["worktree"] for step in st.steps.values() if step.get("worktree") and
                        step.get("mode") == "interactive" and not step.get("consoleClosed"))
            if st.started is not None and not st.card_finished:
                worktree = ticket_of(st)["cardWorktree"]
                if worktree:
                    busy.add(worktree)
        return sorted(busy)

    def is_hidden(self, session: dict) -> bool:
        """Sesja kroku w tle: nie jest wierszem agenta (kafelek Praca, lista agentów, powiadomienia)."""
        ids, places = self._hidden
        if str(session.get("sessionId") or "") in ids:
            return True
        cwd = os.path.realpath(str(session.get("cwd") or "")) if session.get("cwd") else ""
        return bool(cwd) and any(cwd == p or cwd.startswith(p + os.sep) for p in places)

    # --- zlecenie --------------------------------------------------------------------------------

    def start(self, card_id: str, request: dict, *, _resume=False) -> dict:
        unknown = [k for k in request if k not in REQUEST_FIELDS]
        if unknown:
            raise FlowError("bad-request", f"nieznane pole „{unknown[0]}”; dozwolone: {', '.join(REQUEST_FIELDS)}")
        if request.get("entry") == "import":
            return self.start_import(card_id, request)
        if request.get("entry") == "plan":
            return self.propose_plan(card_id, str(request.get("planPath") or ""))
        if request.get("entry") == "planning" and request.get("mode") in ("full", "light"):
            return self.start_planning(card_id, request)
        if (request.get("entry") or "") != "now":
            raise FlowError("bad-request", "w przepływie karty działa na razie wejście „Od razu” (entry: now)")
        try:
            num = card_num(card_id)
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        board = self.store.load()
        card = board.cards.get(num)
        if card is None:
            raise FlowError("not-found", f"nie ma karty K-{num}")
        if card.column in ("done", "archived"):
            raise FlowError("card-finished", f"{card.id}: karta jest w Gotowych albo Archiwum")
        project = board.projects.get(card.project)
        if project is None or not project.repo:
            raise FlowError("no-project", f"{card.id}: karta potrzebuje projektu z repozytorium")
        if project.repo_missing or not os.path.isdir(project.repo):
            raise FlowError("no-repo", f"nie ma repozytorium {project.repo}")
        name = str(request.get("executor") or "").strip()
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        preferred = str(request.get("preferredProvider") or "").strip()
        if preferred and preferred not in ("mixed", "local") and not catalog.known_provider(preferred):
            raise FlowError("unknown-provider", f"nie ma dostawcy „{preferred}” w katalogu")
        try:
            selected = execution.resolve(name, catalog, config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if preferred and preferred != "mixed" and selected.provider != preferred:
            raise FlowError("provider-mismatch", f"„{name}” należy do dostawcy {selected.provider}, nie {preferred}")
        preferred = preferred or selected.provider
        preferred_name = "Sonnet" if name == "Opus" else "Codex Astra" if name == "Codex mocny" else "Opus"
        reviewer_name = str(request.get("reviewer") or "").strip()

        def resolve_reviewer(candidate: str):
            try:
                found = execution.resolve(candidate, catalog, config=cfg, probe=self.model_probe, which=self.which)
            except execution.ExecutionError as exc:
                raise FlowError("unknown-reviewer" if exc.code == "unknown-executor" else exc.code, str(exc)) from None
            if found.name == selected.name or found.model.lower() == selected.model.lower():
                raise FlowError("bad-reviewer", "recenzent musi mieć inny model niż autor")
            if selected.local != found.local:
                raise FlowError("bad-reviewer-provider", "praca lokalna wymaga lokalnego recenzenta")
            if preferred == "mixed" and found.provider == selected.provider:
                raise FlowError("bad-reviewer-provider", "w trybie mieszanym recenzent musi być u innego dostawcy")
            if preferred and preferred != "mixed" and found.provider != selected.provider:
                raise FlowError("bad-reviewer-provider", "recenzent musi należeć do wybranego dostawcy")
            return found

        if reviewer_name:
            reviewer = resolve_reviewer(reviewer_name)
        else:
            reviewer = None
            for candidate in [preferred_name, *(e.name for e in catalog.executors)]:
                if candidate == selected.name:
                    continue
                try:
                    reviewer = resolve_reviewer(candidate)
                except FlowError:
                    continue
                break
            if reviewer is None:
                raise FlowError("bad-reviewer", "brak dostępnego recenzenta z innym modelem")
        check = str(request.get("check") or card.check or "auto").strip().lower()
        if check not in ("auto", "manual"):
            raise FlowError("bad-request", "sprawdzenie musi być auto albo manual")
        images = self._images(request.get("images"))
        st = fold(self.journal.read(card.id))
        if st.active() and not _resume:
            raise FlowError("flow-active", f"{card.id}: krok tej karty jeszcze trwa")
        try:
            if not project.youtrack:
                reservations.reserve(project.repo, count=1, title=card.title, dry_run=True)
        except reservations.ReservationError as exc:
            raise FlowError("no-ticket-number", f"numer ticketu: {exc}") from None
        main = self.git.main_branch(project.repo)
        if not main:
            raise FlowError("no-base", f"{project.repo}: nie ma gałęzi głównej (master, main, develop)")

        started = st.started if _resume else self.journal.append(card.id, "card.started", entry="now", executor=selected.name,
                                      program=selected.tool, model=selected.model, provider=selected.provider,
                                      executorRouteUrl=selected.route.url if selected.route else "",
                                      executorRouteKind=selected.route.kind if selected.route else "",
                                      executorRouteTokenFile=selected.route.token_file if selected.route else "",
                                      executorEffort=selected.effort,
                                      reviewer=reviewer.name, reviewerProgram=reviewer.tool,
                                      reviewerModel=reviewer.model, reviewerProvider=reviewer.provider,
                                      reviewerRouteUrl=reviewer.route.url if reviewer.route else "",
                                      reviewerRouteKind=reviewer.route.kind if reviewer.route else "",
                                      reviewerRouteTokenFile=reviewer.route.token_file if reviewer.route else "",
                                      reviewerEffort=reviewer.effort,
                                      preferredProvider=preferred or selected.provider, check=check,
                                      repo=project.repo, project=project.slug, column=card.column,
                                      moveId=card.move_id, request=request, trackerProject=project.youtrack,
                                      trackerUrl=yt.load_config()["url"] if project.youtrack else "", images=images)
        launch = f"launch-{started['seq']}"
        request_key = f"{card.id}:{launch}"
        if project.youtrack:
            try:
                reserved = self._tracker_reserve(card.id, launch, 1, card.title, card.description)
            except yt.YouTrackError as exc:
                if exc.permanent:
                    raise FlowError('youtrack', str(exc)) from None
                return {"ok": True, "id": card.id, "queued": True, "message": str(exc)}
        else:
            reserved = self._act(card.id, "reserve", launch,
                                 lambda: self._reserve(project.repo, card.title, request_key),
                                 repo=project.repo, title=card.title)
        ticket = reserved["ticket"]
        slug = slug_from_prompt(card.title, words=4, length=32)
        card_branch = ticket_of(st)["cardBranch"] or f"card/{card.id.lower()}-{slug}"
        card_worktree = worktree_for(project.repo, card_branch)
        made = self._act(card.id, "card-branch", card_branch,
                         lambda: self.git.branch_worktree(project.repo, card_branch, card_worktree, main),
                         repo=project.repo, branch=card_branch, worktree=card_worktree, base=main)
        ticket_branch = f"ticket/{ticket.lower()}-{slug}"
        ticket_worktree = worktree_for(project.repo, ticket_branch)
        made_ticket = self._act(card.id, "ticket-branch", ticket, lambda: {
            **self.git.branch_worktree(project.repo, ticket_branch, ticket_worktree,
                                       f"refs/heads/{card_branch}"), "ticket": ticket},
            repo=project.repo, branch=ticket_branch, worktree=ticket_worktree,
            base=f"refs/heads/{card_branch}", ticket=ticket)
        step_id = f"{card.id}-{ticket}-{1 + sum(1 for s in st.steps.values() if s.get('ticket') == ticket)}"
        try:
            step = self._launch(card, project, selected, ticket, made, made_ticket, step_id, images)
        except FlowDeferred:
            kept_images = []
            for index, source in enumerate(images):
                rel = os.path.join("queued-images", launch, str(index), os.path.basename(source))
                with open(source, "rb") as fh:
                    self.journal.write_file(card.id, rel, fh.read())
                kept_images.append(os.path.join(self.store.root, self.journal.rel_dir(card.id), rel))
            self.journal.append(card.id, "card.inputs", images=kept_images)
            step = {"session": ""}
        return {"ok": True, "id": card.id, "ticket": ticket, "branch": made_ticket["branch"],
                "worktree": made_ticket["worktree"], "cardBranch": made["branch"], "cardWorktree": made["worktree"],
                "step": step_id, "session": step["session"], "executor": selected.name,
                "queued": "step" not in step, **({'map': {'#1': ticket}} if project.youtrack else {})}

    def start_import(self, card_id: str, request: dict) -> dict:
        """Przejmuje wskazany plan; źródło pozostaje w repozytorium bez zmian."""
        card_id = f"K-{card_num(card_id)}"
        item, project = self._project(card_id)
        if item.column in ("done", "archived") or fold(self.journal.read(card_id)).active():
            raise FlowError("flow-active", "karta jest zakończona albo już pracuje")
        if not project.repo or project.repo_missing:
            raise FlowError("no-project", "karta potrzebuje dostępnego repozytorium projektu")
        path = os.path.realpath(str(request.get("planPath") or item.plan or ""))
        if not any(p["path"] == path and p["card"] in ("", card_id) and p["status"] != "done"
                   for p in plan_candidates(project.repo, self.store.root)):
            raise FlowError("bad-plan", "wybrany plan nie należy do tej karty i projektu")
        source = source_plans.read(path)
        if source is None or source.errors or not source.rows:
            raise FlowError("bad-plan", "plan jest niepoprawny albo nie ma ticketów")
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        name = str(request.get("executor") or source.rows[0].agent or "Sonnet")
        try:
            selected = execution.resolve(name, catalog, config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if source.tracker not in ("local", "youtrack") or (source.tracker == "youtrack") != bool(project.youtrack):
            raise FlowError("bad-plan", "tracker planu różni się od projektu karty")
        if project.youtrack and (source.tracker_project != project.youtrack or
                                 source.tracker_url.rstrip("/") != yt.load_config()["url"].rstrip("/")):
            raise FlowError("bad-plan", "projekt albo adres YouTracka w planie różni się od projektu karty")
        try:
            prefix = project.youtrack or reservations.reserve(project.repo, dry_run=True)["prefix"]
        except reservations.ReservationError as exc:
            raise FlowError("no-ticket-number", str(exc)) from None
        if source.prefix != prefix:
            raise FlowError("bad-plan", "prefiks planu różni się od prefiksu projektu")
        for row in source.rows:
            number = row.ticket[len(prefix) + 1:] if row.ticket.startswith(prefix + "-") else ""
            if not (flow_plans.WORK_ID.fullmatch(row.ticket) or
                    number and number[0] in "123456789" and number.isdigit()):
                raise FlowError("bad-plan", f"{row.ticket}: nie można zachować istniejącego numeru ticketu")
        check = str(request.get("check") or item.check or "auto")
        if check not in ("auto", "manual"):
            raise FlowError("bad-request", "sprawdzenie musi być auto albo manual")
        images = self._images(request.get("images"))
        with open(path, "rb") as fh:
            source_bytes = fh.read(source_plans.MAX_BYTES + 1)
        if len(source_bytes) > source_plans.MAX_BYTES:
            raise FlowError("bad-plan", "plan przekracza 512 KB")
        import_rows = [{"id": row.ticket, "state": row.state, "title": row.title,
                        "agent": row.agent, "blockers": list(row.blockers), "note": row.note}
                       for row in source.rows]
        proposal = flow_plans.read(path, card_id, catalog, allowed_roots=(project.repo, self.store.root),
                                   tracker=source.tracker, imported=True,
                                   existing_ids=[row["id"] for row in import_rows])
        _meta, body = source_plans.split_frontmatter(source_bytes.decode("utf-8"))
        loose_text = any(line.strip() and not line.lstrip().startswith("|") for line in body.splitlines())
        complete = (not proposal.errors and b"| Blokery |" in source_bytes and
                    not loose_text and all(row.agent and row.work_class and row.reviewer and row.note for row in source.rows))
        self.store.update_card(card_id, {"planPath": path})
        self.journal.append(card_id, "card.started", entry="plan", repo=project.repo, project=project.slug,
                            column=item.column, moveId=item.move_id, check=check, images=images,
                            executor=selected.name, program=selected.tool, model=selected.model,
                            provider=selected.provider, preferredProvider=request.get("preferredProvider") or selected.provider,
                            prefix=prefix, trackerProject=project.youtrack,
                            trackerUrl=yt.load_config()["url"] if project.youtrack else "")
        self.journal.write_file(card_id, os.path.join("plan", "source.md"), source_bytes)
        self.journal.append(card_id, "plan.imported", path=path, rows=import_rows,
                            needsCompletion=not complete)
        if complete:
            result = self.propose_plan(card_id, path, import_source=True)
            return {**result, "imported": True, "needsCompletion": False}
        self._planning_launch(card_id, fold(self.journal.read(card_id)), selected, catalog, images=images)
        st = fold(self.journal.read(card_id))
        return {"ok": True, "id": card_id, "imported": True, "needsCompletion": True,
                "step": st.last_step, "executor": selected.name}

    def start_planning(self, card_id: str, request: dict) -> dict:
        card_id = f"K-{card_num(card_id)}"
        item, project = self._project(card_id)
        if item.column in ("done", "archived"):
            raise FlowError("card-finished", "karta jest zakończona")
        if not project.repo:
            raise FlowError("no-project", "karta potrzebuje projektu z repozytorium")
        if project.repo_missing or not os.path.isdir(project.repo):
            raise FlowError("no-repo", "repozytorium projektu jest niedostępne")
        if fold(self.journal.read(card_id)).active():
            raise FlowError("flow-active", "karta już pracuje")
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        try:
            selected = execution.resolve(str(request.get("executor") or ""), catalog, config=cfg,
                                         probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        mode = str(request.get("mode") or "")
        if mode not in ("full", "light"):
            raise FlowError("bad-request", "tryb planowania musi być Pełny albo Lekki")
        if mode == "full" and (self.terminal is None or not self.terminal.available()):
            raise FlowError("no-terminal", "nie znaleziono Konsoli")
        preferred = str(request.get("preferredProvider") or selected.provider)
        if preferred not in ("mixed", "local") and not catalog.known_provider(preferred):
            raise FlowError("unknown-provider", "nieznany preferowany dostawca")
        if (preferred == "local") != selected.local:
            raise FlowError("provider-mismatch", "planowanie lokalne wymaga lokalnego wykonawcy")
        check = str(request.get("check") or item.check or "auto")
        if check not in ("auto", "manual"):
            raise FlowError("bad-request", "sprawdzenie musi być auto albo manual")
        images = self._images(request.get("images"))
        main = self.git.main_branch(project.repo)
        if not main:
            raise FlowError("no-base", "repozytorium nie ma gałęzi głównej")
        if project.youtrack:
            prefix = project.youtrack
        else:
            try:
                prefix = reservations.reserve(project.repo, dry_run=True)["prefix"]
            except reservations.ReservationError as exc:
                raise FlowError("no-ticket-number", str(exc)) from None
        self.journal.append(card_id, "card.started", entry="planning", mode=mode, repo=project.repo,
                            project=project.slug, column=item.column, moveId=item.move_id,
                            executor=selected.name, program=selected.tool, model=selected.model,
                            provider=selected.provider, preferredProvider=preferred, prefix=prefix,
                            check=check,
                            assignmentRules=assignment.rules(self.store.load(), project), images=images,
                            effort=selected.effort,
                            routeUrl=selected.route.url if selected.route else "",
                            routeKind=selected.route.kind if selected.route else "",
                            routeTokenFile=selected.route.token_file if selected.route else "",
                            trackerProject=project.youtrack,
                            trackerUrl=yt.load_config()['url'] if project.youtrack else '')
        self._planning_launch(card_id, fold(self.journal.read(card_id)), selected, catalog, images=images)
        st = fold(self.journal.read(card_id))
        return {"ok": True, "id": card_id, "step": st.last_step, "executor": selected.name,
                "interactive": mode == "full",
                "cardBranch": ticket_of(st)["cardBranch"], "cardWorktree": ticket_of(st)["cardWorktree"]}

    def _planning_launch(self, card, st, selected, catalog, *, errors=(), previous="", images=(), round_no=0):
        item, project = self._project(card)
        branch = ticket_of(st)["cardBranch"] or f"card/{card.lower()}-{slug_from_prompt(item.title, words=4, length=32)}"
        worktree = worktree_for(project.repo, branch)
        made = self._act(card, "card-branch", branch,
                         lambda: self.git.branch_worktree(project.repo, branch, worktree, self.git.main_branch(project.repo)),
                         repo=project.repo, branch=branch, worktree=worktree, base=self.git.main_branch(project.repo))
        importing = st.started.get("entry") == "plan"
        kind = "plan-import" if importing else "plan-fix" if round_no else "planning"
        step_id = f"{card}-plan-{st.started['seq']}-{round_no}"
        out = worktree + f".{kind}-{step_id}.step/out"
        shots = [os.path.join(os.path.dirname(out), "in", "images", f"{i + 1}-{os.path.basename(p)}")
                 for i, p in enumerate(images)]
        if importing:
            with open(os.path.join(self.store.root, self.journal.rel_dir(card), "plan", "source.md"), encoding="utf-8") as fh:
                source = fh.read(source_plans.MAX_BYTES)
            text = planning.import_prompt(card=item, project=project, catalog=catalog, prefix=st.started["prefix"],
                                          source=source, source_rows=st.imported["rows"], out_dir=out,
                                          errors=errors, previous=previous, images=shots,
                                          tracker="youtrack" if project.youtrack else "local",
                                          tracker_url=st.started.get("trackerUrl") or "",
                                          tracker_project=project.youtrack or "")
        else:
            prompt_fn = planning.full_prompt if st.started.get("mode") == "full" and not round_no else planning.prompt
            args = dict(card=item, project=project, catalog=catalog,
                        preferred=st.started["preferredProvider"], assignment_rules=st.started["assignmentRules"],
                        prefix=st.started["prefix"], out_dir=out, tool=selected.tool, images=shots,
                        tracker="youtrack" if project.youtrack else "local",
                        tracker_url=st.started.get("trackerUrl") or "",
                        tracker_project=project.youtrack or "")
            if prompt_fn is planning.prompt:
                args.update(errors=errors, previous=previous)
            text = prompt_fn(**args)
        if st.started.get("mode") == "full" and kind == "planning":
            self._launch_full_planning(item, project, selected, made, step_id, list(images), shots, text)
            return
        self._launch(item, project, selected, "", made, made, step_id, list(images), kind=kind,
                     round_no=round_no, prompt_text=text)

    def _launch_full_planning(self, item, project, selected, made, step_id, images, shots, prompt):
        """Pierwsze planowanie Pełne: jedna karta Konsoli z plikiem wyniku obserwowanym przez silnik."""
        worktree = made["worktree"]
        step_dir = worktree + f".planning-{step_id}.step"
        out = os.path.join(step_dir, "out")
        inp = os.path.join(step_dir, "in")
        os.makedirs(out, mode=0o700, exist_ok=True)
        os.makedirs(inp, mode=0o700, exist_ok=True)
        for source, target in zip(images, shots):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(source, target)
        board = self.store.load()
        extra_repos = [board.projects[slug].repo for slug in item.projects
                       if slug in board.projects and board.projects[slug].repo and
                       not board.projects[slug].repo_missing]
        if extra_repos:
            prompt += "\nDodatkowe projekty karty (czytaj jako kontekst):\n" + "\n".join(extra_repos)
        session = str(uuid.uuid4()) if selected.tool == "claude" else ""
        command = agentcmd.interactive(selected.tool, selected.binary, prompt, shots,
                                       [out, *extra_repos], selected.route)
        if selected.route is None:
            options = ["--model", selected.model]
            if selected.tool == "codex" and selected.effort:
                options += ["-c", f'model_reasoning_effort="{selected.effort}"']
            command.argv[1:1] = options
        if session:
            command.argv.insert(1, "--session-id")
            command.argv.insert(2, session)
        closed = os.path.join(inp, "console-closed.json")
        process_file = os.path.join(inp, "console-process.json")
        claim_dir = os.path.join(inp, "console-claim")
        os.makedirs(claim_dir, mode=0o700, exist_ok=True)
        console_unit = "tide-console-" + uuid.uuid4().hex
        payload = {"cwd": claim_dir, "workdir": worktree, "argv": command.argv,
                   "title": f"{item.id} · planowanie Pełne", "closedFile": closed,
                   "processFile": process_file, "consoleUnit": console_unit,
                   "systemdRun": self.systemd_run or agentcmd.SYSTEMD_RUN,
                   "systemctl": self.which("systemctl") or "/usr/bin/systemctl"}
        payload.update(command.task_fields())
        task_file = self.task_files.write(payload)
        self.journal.append(item.id, "step.started", step=step_id, kind="planning", ticket="", round=0,
                            executor=selected.name, program=selected.tool, model=selected.model,
                            provider=selected.provider, local=selected.local, effort=selected.effort,
                            unit="", session=session, worktree=worktree, branch=made["branch"],
                            stepDir=step_dir, repo=project.repo)
        self.journal.append(item.id, "step.entered", step=step_id, session=session, source="",
                            closeFile=closed, processFile=process_file, consoleUnit=console_unit,
                            taskFile=task_file, enteredAt=self.clock())
        plan = Plan(repo_path=project.repo, repo_name=project.name, tool=selected.tool, prompt=prompt,
                    cwd=claim_dir, branch=made["branch"], title=payload["title"],
                    argv=command.argv, task_file=task_file)
        try:
            opened = self.terminal.open(plan)
        except Exception as exc:
            print(f"tide-daemon: planowanie {item.id}: Konsola: {exc!r}", flush=True)
            opened = ""
        if not opened:
            if self.task_files.cancel(task_file):
                self.journal.append(item.id, "step.console_closed", step=step_id)
                self._finish(item.id, fold(self.journal.read(item.id)).steps[step_id],
                             forced={"result": "stopped", "reason": "interactive-closed",
                                     "question": "Nie udało się otworzyć Konsoli. Wznowić czy porzucić?"})
            raise FlowError("no-terminal", "nie udało się otworzyć karty Konsoli")

    def _advance_planning(self, card: str, st: FlowState) -> bool:
        """Osobny etap karty; nie zakłada, że ostatni krok jest jedynym krokiem ticketów."""
        if (st.started or {}).get("entry") not in ("planning", "plan") or st.plan_approved is not None:
            return False
        if st.paused or st.plan_proposed is not None:
            return True
        steps = [step for step in st.steps.values() if step.get("kind") in ("planning", "plan-fix", "plan-import")
                 and step.get("seq", 0) > st.started["seq"]]
        if not steps:
            selected = self._resume_executor(card, st, st.started, st.started["executor"])
            cfg = self.models_config() if self.models_config else None
            catalog = cfg.catalog if cfg is not None else self.catalog()
            self._planning_launch(card, st, selected, catalog, images=st.started.get("images") or [])
            return True
        step = steps[-1]
        done = st.finished.get(step["step"]) or {}
        result = done.get("result")
        decision = st.step_decisions.get(step["step"])
        if result in ("question", "denied", "error"):
            if decision:
                self._resume_step(card, st, step, decision)
            elif result == "error" and int(step.get("attempt") or 0) == 0:
                if step["step"] not in st.step_retries:
                    self.journal.append(card, "step.retry", step=step["step"], ticket="",
                                        reason=done.get("reason") or "")
                self._resume_step(card, fold(self.journal.read(card)), step, {"choice": "auto"}, attempt=1)
        elif result == "done":
            if step.get("mode") == "interactive" and not step.get("consoleClosed"):
                return True
            self._planning_result(card, st, step)
        return True

    def _planning_result(self, card, st, step):
        if st.plan_proposed is not None:
            return
        path = os.path.join(step["stepDir"], "out", "plan.md")
        if (st.plan_checked or {}).get("step") != step["step"] or (st.plan_checked or {}).get("valid"):
            self.propose_plan(card, path, continuation=True, step=step["step"])
            st = fold(self.journal.read(card))
        if st.plan_proposed is not None or int(step.get("round") or 0) >= 2:
            return
        selected = self._resume_executor(card, st, step, step["executor"])
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog()
        try:
            with open(path, encoding="utf-8") as fh:
                previous = fh.read(OUTPUT_MAX)
        except (OSError, UnicodeError):
            previous = "(brak czytelnego pliku planu)"
        self._planning_launch(card, st, selected, catalog, errors=st.plan_checked["errors"], previous=previous,
                              images=st.started.get("images") or [],
                              round_no=int(step.get("round") or 0) + 1)
    def _tracker_reserve(self, card, key, number, title, description):
        item, project = self._project(card)
        started = fold(self.journal.read(card)).started or {}
        payload = {'op': 'issue', 'url': started.get('trackerUrl') or yt.load_config()['url'],
                   'project': started.get('trackerProject') or project.youtrack,
                   'number': number, 'title': title, 'description': description}
        result = self.tracker.run(card, 'reserve', key, payload)
        if item.youtrack:
            self.tracker.run(card, 'youtrack-link', result['ticket'],
                             {'op': 'link', 'url': payload['url'], 'ticket': result['ticket'],
                              'parent': item.youtrack})
        return result

    def _tracker_notes(self, card):
        st = fold(self.journal.read(card))
        if not st.started:
            return
        _, project = self._project(card)
        if not project.youtrack:
            return
        for (action, ticket), result in list(st.results.items()):
            if action != 'merge' or not result.get('ok') or ('youtrack-note', ticket) in st.results:
                continue
            source = next((s for s in reversed(list(st.steps.values())) if s.get('ticket') == ticket
                           and s.get('kind') in ('ticket', 'fix')), None)
            if source is None:
                continue
            verdict = next((v for (t, _), v in reversed(list(st.verdicts.items())) if t == ticket), {})
            text = (self._note(card, source) + '\n\nGałąź: ' + source.get('branch', '') +
                    '\nReview: ' + verdict.get('verdict', '') + '\nWykonawca: ' + source.get('executor', ''))
            try:
                self.tracker.run(card, 'youtrack-note', ticket,
                                 {'op': 'comment', 'url': st.started.get('trackerUrl') or yt.load_config()['url'], 'ticket': ticket,
                                  'event': str(result['seq']), 'text': text})
            except yt.YouTrackError:
                pass

    def _reserve(self, repo: str, title: str, request_key: str, prefix: str = "", minimum_number: int = 0) -> dict:
        got = reservations.reserve(repo, prefix=prefix, count=1, title=title, request_key=request_key,
                                   at_least=minimum_number)
        return {"ticket": got["ids"][0], "title": title}

    def enter(self, card_id: str, step_id: str) -> dict:
        """Przejmuje krok w karcie Konsoli. Dziennik oznacza zajęte worktree przed uruchomieniem karty."""
        try:
            num = card_num(card_id)
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        card_id = f"K-{num}"
        st = fold(self.journal.read(card_id))
        source_step = st.steps.get(step_id)
        if source_step is None or st.card_finished:
            raise FlowError("no-step", f"{card_id}: nie ma kroku {step_id} do wznowienia")
        if self.terminal is None or not self.terminal.available():
            raise FlowError("no-terminal", "nie znaleziono Konsoli")
        worktree = source_step.get("worktree") or ""
        reopen = not os.path.isdir(worktree)
        if reopen and not (st.results.get(("cleanup", source_step.get("ticket"))) or {}).get("ok"):
            raise FlowError("no-worktree", "katalog kroku już nie istnieje")
        for running in st.running():
            if running.get("worktree") == worktree and running.get("step") != step_id:
                raise FlowError("worktree-busy", "w katalogu kroku pracuje już inna sesja")
        if source_step.get("mode") == "interactive" and step_id not in st.finished:
            raise FlowError("already-entered", "karta Konsoli tego kroku jest już otwarta")
        if any(item.get("worktree") == worktree and item.get("mode") == "interactive" and
               not item.get("consoleClosed")
               for item in st.steps.values()):
            raise FlowError("console-open", "karta Konsoli tego worktree jest jeszcze otwarta")
        session, source = self._session(source_step)
        if not session:
            raise FlowError("no-session", "nie znaleziono ID sesji kroku; spróbuj ponownie za chwilę")
        if source_step.get("program") == "codex" and not source:
            raise FlowError("no-session-source", "nie znaleziono dziennika sesji Codexa; nie można dobrać programu do wznowienia")
        if reopen:
            repo = source_step.get("repo") or self._repo(card_id)
            branch = source_step.get("branch") or ""
            if not self.git.sha(repo, f"refs/heads/{branch}"):
                raise FlowError("no-branch", "gałąź ticketu już nie istnieje")
            self._act(card_id, "step-reopen", f"{step_id}-{st.last_seq + 1}",
                      lambda: self.git.branch_worktree(repo, branch, worktree, f"refs/heads/{branch}"),
                      repo=repo, branch=branch, worktree=worktree, base=f"refs/heads/{branch}")
        route = None
        if (source_step.get("provider") or "") not in ("anthropic", "openai", ""):
            cfg = self.models_config() if self.models_config else None
            catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
            if catalog is None:
                raise FlowError("executor-unavailable", "katalog wykonawców jest niedostępny")
            try:
                selected = execution.resolve(source_step.get("executor") or "", catalog, config=cfg,
                                             probe=self.model_probe, which=self.which)
            except execution.ExecutionError as exc:
                raise FlowError(exc.code, str(exc)) from None
            route = selected.route
        if step_id not in st.finished:
            if not self._stop_for_enter(card_id, source_step):
                raise FlowError("not-stopped", "trwający krok nie zatrzymał się; Konsola nie została otwarta")
            # Agent mógł właśnie zapisać wynik. Utrwalamy go, zanim powstanie następna próba.
            result_path = os.path.join(source_step.get("stepDir") or "", "out", handoff.RESULT_FILE)
            if os.path.isfile(result_path):
                self._finish(card_id, source_step, exit_code=0)
                st = fold(self.journal.read(card_id))
        current = st.steps.get(step_id) or source_step
        new_id = step_id
        if step_id in st.finished:
            index = 1
            while f"{step_id}-enter-{index}" in st.steps:
                index += 1
            new_id = f"{step_id}-enter-{index}"
        step_dir = (source_step.get("stepDir") or worktree + ".step") if new_id == step_id \
            else (worktree + f".step-enter-{index}")
        out = os.path.join(step_dir, "out")
        os.makedirs(out, mode=0o700, exist_ok=True)
        closed = os.path.join(step_dir, "in", "console-closed.json")
        process_file = os.path.join(step_dir, "in", "console-process.json")
        os.makedirs(os.path.dirname(closed), mode=0o700, exist_ok=True)
        claim_dir = os.path.join(step_dir, "in", "console-claim")
        os.makedirs(claim_dir, mode=0o700, exist_ok=True)
        for path in (closed, process_file, os.path.join(out, handoff.RESULT_FILE)):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        tool = current.get("program") or "claude"
        binary = self.terminal.launchers.agents.get(tool) or tool
        if tool == "codex":
            binary = self.terminal.launchers.codex_for(source)
        old_result = (st.finished.get(step_id) or {}).get("result") or ""
        kind = "interactive" if old_result == "done" else current.get("kind") or "ticket"
        console_unit = "tide-console-" + uuid.uuid4().hex
        prompt = handoff.resume_step(card_id, current.get("ticket") or "", out, kind=kind)
        command = agentcmd.resume_interactive(tool, binary, session, prompt, route)
        payload = {"cwd": claim_dir, "workdir": worktree, "argv": command.argv,
                   "title": f"{card_id} · {current.get('ticket') or 'krok'}", "closedFile": closed,
                   "processFile": process_file, "consoleUnit": console_unit,
                   "systemdRun": self.systemd_run or agentcmd.SYSTEMD_RUN,
                   "systemctl": self.which("systemctl") or "/usr/bin/systemctl"}
        payload.update(command.task_fields())
        task_file = self.task_files.write(payload)
        if new_id != step_id:
            self.journal.append(card_id, "step.started", step=new_id, kind=kind,
                                ticket=current.get("ticket") or "", executor=current.get("executor") or "",
                                program=tool, provider=current.get("provider") or "", local=bool(current.get("local")),
                                model=current.get("model") or "", unit="", session=session,
                                worktree=worktree, branch=current.get("branch") or "", stepDir=step_dir,
                                gitDir=(agentcmd.resolve_git_dir(current.get("repo") or self._repo(card_id), worktree)
                                        if reopen else current.get("gitDir") or ""),
                                round=current.get("round") or 0, reviewTip=current.get("reviewTip") or "",
                                repo=current.get("repo") or self._repo(card_id))
        self.journal.append(card_id, "step.entered", step=new_id, session=session, source=source,
                            closeFile=closed, processFile=process_file, consoleUnit=console_unit,
                            taskFile=task_file,
                            enteredAt=self.clock())
        plan = Plan(repo_path=current.get("repo") or self._repo(card_id), repo_name="", tool=tool,
                    prompt=prompt, cwd=claim_dir, branch=current.get("branch") or "", title=payload["title"],
                    argv=command.argv, task_file=task_file)
        try:
            mode = self.terminal.open(plan)
        except Exception as exc:
            print(f"tide-daemon: przepływ karty {card_id}: Konsola: {exc!r}", flush=True)
            mode = ""
        if not mode:
            if self.task_files.cancel(task_file):
                self.journal.append(card_id, "step.console_closed", step=new_id)
                self._finish(card_id, fold(self.journal.read(card_id)).steps[new_id],
                             forced={"result": "stopped", "reason": "interactive-closed",
                                     "question": "Nie udało się otworzyć Konsoli. Wznowić czy porzucić?"})
            raise FlowError("no-terminal", "nie udało się otworzyć karty Konsoli")
        return {"ok": True, "id": card_id, "step": new_id, "session": session, "mode": mode}

    def abandon(self, card_id: str, step_id: str) -> dict:
        """Rozstrzygnięcie pytania po zamknięciu Konsoli bez wyniku."""
        st = fold(self.journal.read(card_id))
        done = st.finished.get(step_id) or {}
        if done.get("reason") != "interactive-closed" or done.get("result") != "stopped":
            raise FlowError("not-waiting", "ten krok nie czeka na wybór „wznów / porzuć”")
        self.journal.append(card_id, "step.finished", step=step_id, ticket=done.get("ticket") or "",
                            result="stopped", reason="abandoned", question="", command="", notes=[], kept=[])
        return {"ok": True, "id": card_id, "step": step_id, "result": "stopped"}

    def _stop_for_enter(self, card: str, step: dict) -> bool:
        stopped = stepstop.stop_step(self.procs.get(step.get("step") or ""), unit=step.get("unit") or "",
                                     worktree=step.get("worktree") or "", step_dir=step.get("stepDir") or "",
                                     repo=step.get("repo") or self._repo(card), git_dir=step.get("gitDir") or "",
                                     branch=step.get("branch") or "", tool=step.get("program") or "claude",
                                     session_id=step.get("session") or "", claude_projects=self.claude_projects,
                                     codex_roots=self.codex_roots, grace=self.grace)
        if not stopped.stopped:
            return False
        self.procs.pop(step.get("step") or "", None)
        return True

    def _session(self, step: dict) -> tuple[str, str]:
        sid = step.get("session") or ""
        source = step.get("source") or ""
        if step.get("program") == "codex":
            sid = sid or execution.codex_session_id(os.path.join(step.get("stepDir") or "", "out", "step.log"))
            source = source or stepstop.find_codex_log(self.codex_roots, step.get("worktree") or "", sid)
            if source and not sid:
                sid = execution.codex_session_id(source)
        return sid, source

    def propose_plan(self, card_id: str, path: str, *, continuation=False, step="", import_source=False) -> dict:
        """Przyjmuje propozycję z pliku; walidacja i migawka nie nadają numerów."""
        try:
            card_id = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        board = self.store.load()
        item = board.cards.get(card_num(card_id))
        project = board.projects.get(item.project) if item else None
        if item is None or project is None or not project.repo:
            raise FlowError("no-project", f"{card_id}: karta potrzebuje projektu z repozytorium")
        if item.column in ("done", "archived"):
            raise FlowError("card-finished", f"{card_id}: karta jest zakończona")
        if not continuation and not import_source and fold(self.journal.read(card_id)).active():
            raise FlowError("flow-active", f"{card_id}: karta już pracuje")
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        step_roots = [os.path.join(s.get("stepDir") or "", "out") for s in
                      fold(self.journal.read(card_id)).steps.values() if s.get("stepDir")]
        roots = ((project.repo, os.path.join(self.store.root, "docs"), *step_roots) if not continuation else
                 (os.path.join(fold(self.journal.read(card_id)).steps[step]["stepDir"], "out"),))
        imported = fold(self.journal.read(card_id)).imported
        source_states = {row["id"]: row["state"] for row in (imported or {}).get("rows", [])}
        proposal = flow_plans.read(path, card_id, catalog, allowed_roots=roots,
                                   tracker='youtrack' if project.youtrack else 'local',
                                   existing_ids=[*source_states,
                                                 *[r["id"] for r in ((fold(self.journal.read(card_id)).plan_approved or {}).get("rows") or [])]],
                                   imported=bool(imported))
        if imported:
            original = {row["id"]: row["title"] for row in imported.get("rows") or []}
            proposed = {row["workId"]: row["title"] for row in proposal.rows}
            for ident, title in original.items():
                if ident not in proposed:
                    proposal.errors.append(f"{ident}: przejęty ticket musi pozostać w planie")
                elif proposed[ident] != title:
                    proposal.errors.append(f"{ident}: tytuł przejętego ticketu musi pozostać bez zmian")
        if proposal.plan is not None and proposal.plan.tracker == 'youtrack':
            config_url = yt.load_config()['url'].rstrip('/')
            if proposal.plan.tracker_project != project.youtrack:
                proposal.errors.append('tracker-project planu różni się od projektu karty')
            if proposal.plan.tracker_url.rstrip('/') != config_url:
                proposal.errors.append('tracker-url planu różni się od serwera projektu karty')
        if proposal.plan is not None and not project.youtrack:
            try:
                established = reservations.reserve(project.repo, dry_run=True)["prefix"]
                if proposal.plan.prefix != established:
                    proposal.errors.append(f"prefiks planu {proposal.plan.prefix} różni się od prefiksu repozytorium {established}")
            except reservations.ReservationError as exc:
                proposal.errors.append(str(exc))
        if not continuation and not import_source:
            self.journal.append(card_id, "card.started", entry="plan", repo=project.repo, project=project.slug,
                                check=item.check or "auto", column=item.column, moveId=item.move_id,
                                trackerProject=project.youtrack,
                                trackerUrl=yt.load_config()['url'] if project.youtrack else '')
        checked = fold(self.journal.read(card_id)).plan_checked or {}
        if not continuation or checked.get("step") != step or checked.get("errors") != proposal.errors:
            self.journal.append(card_id, "plan.checked", valid=not proposal.errors, errors=proposal.errors, step=step)
        if proposal.errors:
            return {"ok": False, "id": card_id, "error": "invalid-plan", "errors": proposal.errors}
        with open(os.path.realpath(path), "rb") as fh:
            source = fh.read(flow_plans.plan_mod.MAX_BYTES + 1)
        self.journal.write_file(card_id, os.path.join("plan", "plan.md"), source)
        for ident, body in proposal.cards.items():
            self.journal.write_file(card_id, os.path.join("plan", "tickets", ident.lstrip("#") + ".md"),
                                    body.encode("utf-8"))
        rows = [{**row, "sourceState": source_states.get(row["workId"], ""),
                 "body": proposal.cards[row["workId"]], "cardPath": os.path.join(self.store.root, self.journal.rel_dir(card_id),
                                                   "plan", "tickets", row["workId"].lstrip("#") + ".md")}
                for row in proposal.rows]
        self.journal.append(card_id, "plan.proposed", title=proposal.plan.title,
                            prefix=project.youtrack or proposal.plan.prefix, coordinator=proposal.plan.coordinator,
                            session=proposal.plan.session, rows=rows,
                            planner=(fold(self.journal.read(card_id)).steps.get(step) or {}).get("executor") or
                                    (fold(self.journal.read(card_id)).started or {}).get("executor") or proposal.rows[0]["executor"],
                            sourceStep=step, sourceKind=(fold(self.journal.read(card_id)).steps.get(step) or {}).get("kind") or "",
                            specification=self._plan_spec(path) or (fold(self.journal.read(card_id)).plan_proposed or {}).get("specification") or item.description, planPath=os.path.realpath(path))
        return {"ok": True, "id": card_id, "rows": proposal.rows, "waves": proposal.waves}

    def approve_plan(self, card_id: str) -> dict:
        """Nadaje numery roboczym ticketom i zapisuje mapę w dzienniku dokładnie raz."""
        try:
            card_id = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        st = fold(self.journal.read(card_id))
        if st.plan_revision and st.plan_revision.get("pending"):
            return self._approve_revision(card_id, st)
        if st.plan_proposed and not st.plan_reviewed and not st.plan_approved:
            raise FlowError("review-pending", "plan czeka na review")
        if st.plan_approved is not None:
            return {"ok": True, "id": card_id, "map": st.plan_approved.get("map") or {}, "already": True}
        proposal = st.plan_proposed or {}
        if not proposal or st.card_finished:
            raise FlowError("no-plan", f"{card_id}: nie ma planu do zatwierdzenia")
        if not any(e['type'] == 'plan.approval_requested' and e.get('proposal') == proposal['seq']
                   for e in self.journal.read(card_id)):
            self.journal.append(card_id, 'plan.approval_requested', proposal=proposal['seq'])
        repo = (st.started or {}).get("repo") or ""
        prefix = proposal.get("prefix") or ""
        imported_floor = max((int(row["id"][len(prefix) + 1:]) for row in (st.imported or {}).get("rows", [])
                              if row["id"].startswith(prefix + "-") and row["id"][len(prefix) + 1:].isdigit()), default=0)
        _, project = self._project(card_id)
        mapping = {}
        for row in proposal.get("rows") or []:
            work_id = row["workId"]
            if work_id.startswith("#"):
                key = f"plan:{proposal['seq']}:{work_id}"
                if project.youtrack:
                    try:
                        old = st.results.get(('reserve', key)) or {}
                        source = row['cardPath']
                        if not os.path.isfile(source) and old.get('ticket'):
                            source = os.path.join(os.path.dirname(source), old['ticket'] + '.md')
                        with open(source, encoding='utf-8') as fh:
                            description = fh.read()
                        result = self._tracker_reserve(card_id, key, int(work_id[1:]), row['title'], description)
                    except yt.YouTrackError as exc:
                        if exc.permanent:
                            raise FlowError('youtrack', str(exc)) from None
                        return {"ok": True, "id": card_id, "queued": True, "map": mapping, "message": str(exc)}
                else:
                    result = self._act(card_id, "reserve", key,
                                       lambda row=row, key=key: self._reserve(repo, row["title"],
                                                                                f"{card_id}:{key}", prefix, imported_floor),
                                       retry_failed=True, repo=repo, title=row["title"], prefix=prefix,
                                       minimumNumber=imported_floor)
                mapping[work_id] = result["ticket"]
            else:
                mapping[work_id] = work_id
        rows = [{**row, "id": mapping[row["workId"]],
                 "blockers": [mapping[b] for b in row["blockers"]],
                 "cardPath": os.path.join(self.store.root, self.journal.rel_dir(card_id), "plan", "tickets",
                                          mapping[row["workId"]] + ".md")}
                for row in proposal["rows"]]
        for row in rows:
            old = row["workId"].lstrip("#")
            old_path = os.path.join(self.store.root, self.journal.rel_dir(card_id), "plan", "tickets", old + ".md")
            new_path = row["cardPath"]
            with open(old_path if os.path.isfile(old_path) else new_path, "rb") as fh:
                body = fh.read()
            self.journal.write_file(card_id, os.path.join("plan", "tickets", row["id"] + ".md"), body)
            if old != row["id"]:
                self.journal.remove_file(card_id, os.path.join("plan", "tickets", old + ".md"))
        waves = graph_waves(rows)
        plan_file = f"docs/agents/plans/{proposal['ts'][:10]}-{card_id.lower()}-flow.md"
        self.journal.append(card_id, "plan.approved", map=mapping, rows=rows, planFile=plan_file)
        for row in rows:
            if row.get("sourceState") in ("gotowe", "odrzucone"):
                self.journal.append(card_id, "ticket.imported", ticket=row["id"], state=row["sourceState"])
        return {"ok": True, "id": card_id, "map": mapping, "waves": waves}

    def _images(self, value) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list) or not all(isinstance(p, str) for p in value) or len(value) > 20:
            raise FlowError("bad-request", "images to lista ścieżek (najwyżej 20)")
        out = []
        for path in value:
            real = os.path.realpath(path)
            if not os.path.isfile(real) or not real.lower().endswith(IMAGE_SUFFIXES):
                raise FlowError("bad-image", f"{os.path.basename(path)} nie jest obrazem")
            out.append(real)
        return out

    def _wait_reason(self, selected) -> str:
        running = [step for card in self.journal.cards()
                   for step in fold(self.journal.read(card)).running()]
        provider = selected.provider
        limit = 1 if selected.local else 3
        if not selected.local:
            try:
                with open(self.settings, encoding="utf-8") as fh:
                    value = json.load(fh).get("providerSlots", {}).get(provider, 3)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    limit = value
            except (OSError, ValueError, AttributeError):
                pass
        count = sum(bool(step.get("local") or step.get("provider") == "local") if selected.local
                    else step.get("provider") == provider for step in running)
        if count >= limit:
            return f"limit kroków dostawcy {provider}: {count}/{limit}"
        if selected.local and model_queue.shared().busy():
            return "czeka na modele lokalne — raport i opisy kart mają pierwszeństwo"
        return ""

    def _admit(self, card: str, selected, ticket: str, kind: str, step_id: str) -> None:
        st = fold(self.journal.read(card))
        reason = "karta wstrzymana" if st.paused else self._wait_reason(selected)
        if not reason and selected.local:
            claim = model_queue.shared().claim_step(self.store.root + ":" + step_id, endpoint=selected.route.url,
                                                   model=selected.model)
            if claim is None:
                reason = "czeka na modele lokalne — raport i opisy kart mają pierwszeństwo"
            else:
                self._local_claims[step_id] = claim
        if reason:
            previous = st.waiting.get((ticket, kind)) or {}
            if previous.get("reason") != reason:
                self.journal.append(card, "step.waiting", ticket=ticket, kind=kind,
                                    provider=selected.provider, reason=reason)
            raise FlowDeferred(reason)

    def _launch(self, card, project, selected, ticket, made, made_ticket, step_id, images,
                *, kind="ticket", round_no=0, prompt_text="", review_tip="", parent_step="",
                attempt=0, resume_session="") -> dict:
        self._admit(card.id, selected, ticket, kind, step_id)
        worktree = made_ticket["worktree"]
        step_dir = worktree + (f".resume-{step_id}.step" if parent_step else
                               ".step" if kind == "ticket" else
                               f".review-{round_no}.step" if kind == "review" else
                               f".{kind}-{step_id}.step" if kind in ("planning", "plan-fix", "plan-review", "plan-revise", "plan-amend", "conflict", "close") else f".fix-{round_no}.step")
        out = os.path.join(step_dir, "out")
        shots = [os.path.join(step_dir, "in", "images", f"{i + 1}-{os.path.basename(p)}") for i, p in enumerate(images)]
        prompt = prompt_text or handoff.ticket_card(card_id=card.id, title=card.title, description=card.description,
                                                     project=project.name, ticket=ticket, branch=made_ticket["branch"],
                                                     card_branch=made["branch"], worktree=worktree, out_dir=out,
                                                     images=shots)
        read_dirs = []
        if kind in ("planning", "plan-fix"):
            board = self.store.load()
            extras = [board.projects[slug] for slug in card.projects if slug in board.projects]
            read_dirs = [p.repo for p in extras if p.repo and not p.repo_missing]
            if extras:
                prompt += "\nDodatkowe projekty karty (wyłącznie do odczytu):\n" + "\n".join(
                    f"- {p.name}: {p.repo}" for p in extras)
        prompt += handoff.command_policy(project.allowed_commands, project.denied_commands)
        session = resume_session or (str(uuid.uuid4()) if selected.tool == "claude" else "")
        ids, places = self._hidden    # ukryta, zanim program zapisze pierwszą linię dziennika sesji
        self._hidden = (ids | {session}, tuple(sorted(set(places) | {os.path.realpath(worktree)})))

        def launch() -> dict:
            from ..board.commands import claude_tools
            allowed_tools, disallowed_tools = claude_tools(project.allowed_commands, project.denied_commands)
            bg = agentcmd.background(agentcmd.Step(
                tool=selected.tool, binary=selected.binary, prompt=prompt, worktree=worktree, repo=project.repo,
                step_dir=step_dir, step_id=step_id, session_id=session, model=selected.model,
                resume=bool(resume_session),
                local=selected.route, effort=selected.effort,
                permission_mode="acceptEdits" if selected.tool == "claude" else "",
                allowed_tools=allowed_tools if selected.tool == "claude" else (),
                disallowed_tools=disallowed_tools if selected.tool == "claude" else (),
                enforce_command_policy=selected.tool == "claude",
                tmp_root=self.tmp_root, read_dirs=tuple(read_dirs)))
            bg.prepare()
            for source, target in zip(images, shots):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(source, target)
            proc = bg.start(systemd_run=self.systemd_run)
            self.procs[step_id] = proc
            return {"unit": bg.unit, "gitDir": bg.git_dir}

        try:
            begun = self._act(card.id, "step-start", step_id, launch, unit=f"tide-step-{step_id}",
                              session=session, stepDir=step_dir, worktree=worktree, kind=kind,
                              ticket=ticket, round=round_no, executor=selected.name,
                              program=selected.tool, model=selected.model, provider=selected.provider,
                              effort=selected.effort,
                              routeUrl=selected.route.url if selected.route else "",
                              routeKind=selected.route.kind if selected.route else "",
                              routeTokenFile=selected.route.token_file if selected.route else "",
                              local=selected.local, branch=made_ticket["branch"],
                              reviewTip=review_tip, parentStep=parent_step, attempt=attempt)
        except (agentcmd.StepError, OSError) as exc:
            self._release_local(step_id)
            raise FlowError("step", f"krok nie wystartował: {exc}") from None
        return self.journal.append(card.id, "step.started", step=step_id, kind=kind, ticket=ticket,
                                   round=round_no, executor=selected.name, program=selected.tool,
                                   model=selected.model, provider=selected.provider, local=selected.local,
                                   effort=selected.effort,
                                   routeUrl=selected.route.url if selected.route else "",
                                   routeKind=selected.route.kind if selected.route else "",
                                   routeTokenFile=selected.route.token_file if selected.route else "",
                                   reviewTip=review_tip, parentStep=parent_step, attempt=attempt,
                                   unit=begun["unit"], session=session, worktree=worktree,
                                   branch=made_ticket["branch"], stepDir=step_dir, gitDir=begun["gitDir"],
                                   repo=project.repo)

    def _act(self, card: str, action: str, key: str, work, *, retry_failed: bool = False, **intent) -> dict:
        """Para „zamiar → wynik”: wynik już jest — zwracamy go (powtórka nic nie robi); błąd — wynik ok: false i wyjątek."""
        st = fold(self.journal.read(card))
        done = st.results.get((action, key))
        if done is not None:
            if not done.get("ok") and not retry_failed:
                raise FlowError(action, done.get("error") or action)
            if done.get("ok"):
                return done
        if (action, key) not in st.intents:
            self.journal.append(card, "intent", action=action, key=key, **intent)
        try:
            data = work()
        except (FlowGitError, reservations.ReservationError, agentcmd.StepError, OSError, FlowError) as exc:
            if done is None or done.get("error") != str(exc):
                self.journal.append(card, "result", action=action, key=key, ok=False, error=str(exc))
            if isinstance(exc, (agentcmd.StepError, OSError)):
                raise
            raise FlowError(action, str(exc)) from None
        return self.journal.append(card, "result", action=action, key=key, ok=True, **(data or {}))

    # --- ręczne kolumny --------------------------------------------------------------------------

    def set_paused(self, card_id: str, paused: bool) -> dict:
        try:
            card_id = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        st = fold(self.journal.read(card_id))
        if st.started is None or st.card_finished or st.plan_finished:
            raise FlowError("no-flow", "karta nie ma trwającego przepływu")
        if st.paused != paused:
            self.journal.append(card_id, "card.paused" if paused else "card.resumed")
        return {"ok": True, "id": card_id, "paused": paused}

    def moved(self, card_id: str, column: str, shown: str = "", move_id: str = "") -> None:
        """Karta przeniesiona ręcznie (MoveCard; `shown` — kolumna, w której ją widać): Backlog wstrzymuje, Gotowe
        i Archiwum kończą (krok zatrzymany z zapisem zmian, niescalony ticket odrzucony). Karta bez dziennika — nic."""
        if not self.journal.has(card_id):
            return
        st = fold(self.journal.read(card_id))
        if st.started is None or st.card_finished:
            return
        if (move_id and st.store_move_id == move_id) or (not move_id and st.store_column == column):
            return
        self.journal.append(card_id, "card.moved", to=column, moveId=move_id,
                            **({"from": shown} if shown else {}))
        if column == "backlog" and not st.paused:
            self.journal.append(card_id, "card.paused")
        if column in ("done", "archived"):
            self._complete_manual_finish(card_id, column)

    def _complete_manual_finish(self, card: str, column: str) -> bool:
        """Gotowe/Archiwum zapisujemy dopiero po zatrzymaniu wszystkich kroków; tick ponawia po błędzie."""
        st = fold(self.journal.read(card))
        if st.card_finished:
            return True
        for step in st.running():
            if not self._stop(card, step, "stopped", ""):
                return False
        st = fold(self.journal.read(card))
        if st.running():
            return False
        tickets = dict.fromkeys([
            *(row.get("id") for row in (st.plan_approved or {}).get("rows") or []),
            *(step.get("ticket") for step in st.steps.values()),
            ticket_of(st)["id"],
        ])
        for ticket in tickets:
            merged = st.results.get(("merge", ticket)) or {}
            if ticket and not merged.get("ok") and ticket not in st.imported_done and ticket not in st.rejected:
                self.journal.append(card, "ticket.rejected", ticket=ticket)
        self.journal.append(card, "card.finished", column=column)
        return True

    # --- co chwilę -------------------------------------------------------------------------------

    def _release_local(self, step_id: str) -> None:
        claim = self._local_claims.pop(step_id, None)
        if claim is not None:
            model_queue.shared().release_step(claim)

    def tick(self) -> None:
        for card in self.journal.cards():
            self._recover_started_steps(card, fold(self.journal.read(card)))
        # Odtwarzamy zajęte tory przed sprawdzeniem końca kroków i startem nowych.
        running_local = {s["step"]: s for card in self.journal.cards()
                         for s in fold(self.journal.read(card)).running() if s.get("local")}
        for name in set(self._local_claims) - set(running_local):
            self._release_local(name)
        for name, step in running_local.items():
            if name not in self._local_claims:
                self._local_claims[name] = model_queue.shared().claim_step(
                    self.store.root + ":" + name, endpoint=step.get("routeUrl") or "", model=step.get("model") or "",
                    recovering=True)
        hidden_ids, hidden_places, busy = set(), set(), set()
        for card in self.journal.cards():
            warning = self.journal.check(card)
            if warning:
                self._warnings[card] = warning
                print(f"tide-daemon: przepływ karty: {warning}", flush=True)
            st = fold(self.journal.read(card))
            for step in st.steps.values():
                hidden_ids.add(str(step.get("session") or ""))
                if step.get("worktree"):
                    hidden_places.add(os.path.realpath(step["worktree"]))
            try:
                self._tick_card(card, st)
            except Exception as exc:  # jedna karta nie zatrzymuje pozostałych
                print(f"tide-daemon: przepływ karty {card}: {exc!r}", flush=True)
            try:
                self._guard_card(card)
                self._queue_notices(card)
            except Exception as exc:
                print(f"tide-daemon: przepływ karty {card}: strażnik/powiadomienie: {exc!r}", flush=True)
            st = fold(self.journal.read(card))
            busy |= {s["worktree"] for s in st.running() if s.get("worktree")}
            busy |= {s["worktree"] for s in st.steps.values() if s.get("worktree") and
                     s.get("mode") == "interactive" and not s.get("consoleClosed")}
            if st.started is not None and not st.card_finished and ticket_of(st)["cardWorktree"]:
                busy.add(ticket_of(st)["cardWorktree"])
        self._hidden = (frozenset(hidden_ids - {""}), tuple(sorted(hidden_places)))
        self._busy = tuple(sorted(busy))

    def _tick_card(self, card: str, st: FlowState) -> None:
        self._sync_plan_cards(card, st)
        self._recover_started_steps(card, st)
        self._recover_verdicts(card)
        self._recover_review_rejections(card)
        st = fold(self.journal.read(card))
        if st.started is not None and not st.card_finished and st.store_column:
            stored = self.store.load().cards.get(card_num(card))
            if stored is not None and (stored.column != st.store_column or
                                       (stored.move_id and stored.move_id != st.store_move_id)):
                self.moved(card, stored.column, stored.moved_from or st.store_column, stored.move_id)
                st = fold(self.journal.read(card))
        blocking_tracker_error = any(e.get('action') != 'youtrack-note' for e in st.tracker_failures())
        try:
            if not blocking_tracker_error and st.started and not st.paused and not st.card_finished and st.started.get('entry') == 'now' \
                    and not st.steps and st.started.get('request'):
                _, project = self._project(card)
                if project.youtrack:
                    self.start(card, st.started['request'], _resume=True)
            if not blocking_tracker_error and st.plan_proposed and st.plan_reviewed and not st.plan_approved and any(
                    e['type'] == 'plan.approval_requested' and e.get('proposal') == st.plan_proposed['seq']
                    for e in self.journal.read(card)):
                self.approve_plan(card)
            self._tracker_notes(card)
        except yt.YouTrackError:
            pass
        st = fold(self.journal.read(card))
        for intent in st.open_intents(include_background=True):
            self._reconcile(card, intent)
        st = fold(self.journal.read(card))
        for item in st.steps.values():
            if item.get("mode") == "interactive" and not item.get("consoleClosed") and self._console_ended(item):
                self.journal.append(card, "step.console_closed", step=item.get("step"))
        st = fold(self.journal.read(card))
        if st.moved_to in ("done", "archived") and not st.card_finished:
            self._complete_manual_finish(card, st.moved_to)
            return
        for step in st.running():
            self._poll(card, step)
        self._advance_user_revision(card, fold(self.journal.read(card)))
        current = fold(self.journal.read(card))
        if current.running() and (current.plan_revision or {}).get("pending"):
            self._advance_plan_review(card, current)
        self._queue_notices(card)
        st = fold(self.journal.read(card))
        if not st.card_finished:
            try:
                latest = {}
                for name, step in st.steps.items():
                    latest[step.get("ticket")] = name
                for name in latest.values():
                    current = fold(self.journal.read(card))
                    step = current.steps[name]
                    if name not in current.finished or any(
                            s.get("ticket") == step.get("ticket") for s in current.open_consoles()):
                        continue
                    current.last_step = name
                    try:
                        self._advance(card, current)
                    except FlowDeferred:
                        pass
                current = fold(self.journal.read(card))
                if current.plan_approved is not None:
                    self._plan_next(card, current)
                elif not latest:
                    self._advance(card, current)
                self._advance_close(card, fold(self.journal.read(card)))
            except FlowDeferred:
                pass
            except (FlowError, FlowGitError) as exc:
                problem = f"{exc.__class__.__name__}: {exc}"
                if st.flow_error != problem:
                    self.journal.append(card, "flow.error", error=problem)
        st = fold(self.journal.read(card))
        if st.plan_approved is not None and not st.card_finished and not st.open_intents() and not any(
                s.get("kind") in ("conflict", "close", "plan-review", "plan-revise", "plan-amend")
                and s["step"] not in st.finished for s in st.steps.values()) and not any(
                s.get("kind") == "conflict" and not (st.results.get(("merge", s.get("ticket"))) or {}).get("ok")
                or s.get("kind") == "close" and not st.closed for s in st.steps.values()):
            info = ticket_of(st)
            if info["cardBranch"] and info["cardWorktree"]:
                approved = st.plan_approved
                key = f"{approved.get('seq')}:{st.plan_change_seq}"
                previous = st.results.get(("plan-commit", key))
                if previous is None or not previous.get("ok"):
                    try:
                        self._plan_commit(card, st, self._repo(card), info["cardBranch"], info["cardWorktree"])
                    except (FlowError, FlowGitError) as exc:
                        problem = f"{exc.__class__.__name__}: {exc}"
                        if st.flow_error != problem:
                            self.journal.append(card, "flow.error", error=problem)

    def _advance(self, card: str, st: FlowState) -> None:
        """Jedna zmiana etapu na tick: ticket → review → poprawki/review → scalenie i sprzątanie."""
        if st.open_intents():
            return
        if self._advance_plan_review(card, st):
            return
        if self._advance_planning(card, st):
            return
        if not st.last_step:
            if st.plan_approved is not None:
                self._plan_next(card, st)
            if st.started and st.started.get("entry") == "now" and st.started.get("executor") and not st.paused:
                item, project = self._project(card)
                info = ticket_of(st)
                cfg = self.models_config() if self.models_config else None
                catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
                selected = execution.resolve(st.started["executor"], catalog, config=cfg,
                                             probe=self.model_probe, which=self.which)
                self._launch(item, project, selected, info["id"],
                             {"branch": info["cardBranch"], "worktree": info["cardWorktree"]},
                             {"branch": info["branch"], "worktree": info["worktree"]},
                             self._next_step_id(card, info["id"], st), st.inputs.get("images", st.started.get("images") or []))
            return
        step = st.steps.get(st.last_step) or {}
        done = st.finished.get(st.last_step) or {}
        result = done.get("result")
        if result in ("question", "denied"):
            decision = st.step_decisions.get(st.last_step)
            if decision:
                self._resume_step(card, st, step, decision)
            return
        if result == "error":
            decision = st.step_decisions.get(st.last_step)
            if decision:
                self._resume_step(card, st, step, decision)
            elif int(step.get("attempt") or 0) == 0:
                if st.last_step not in st.step_retries:
                    self.journal.append(card, "step.retry", step=st.last_step, ticket=step.get("ticket") or "",
                                        reason=done.get("reason") or "")
                    st = fold(self.journal.read(card))
                self._resume_step(card, st, step, {"choice": "auto"}, attempt=1)
            return
        if result != "done":
            return
        if step.get("kind") in ("planning", "plan-fix", "plan-review", "plan-revise", "plan-amend"):
            self._plan_next(card, st)
            return
        if step.get("kind") == "close":
            self._advance_close(card, st)
            return
        ticket = step.get("ticket") or ""
        if not ticket:
            return
        if (st.results.get(("cleanup", ticket)) or {}).get("ok"):
            return
        if ticket in st.rejected:
            if st.plan_approved is not None:
                self._plan_next(card, st)
            return
        if step.get("kind") == "conflict":
            if (st.results.get(("merge", ticket)) or {}).get("ok"):
                self._cleanup(card, st, ticket, int(step.get("round") or 1))
                if st.plan_approved is not None:
                    self._plan_next(card, fold(self.journal.read(card)))
            return
        if step.get("kind") in ("ticket", "fix"):
            round_no = 1 if step.get("kind") == "ticket" else int(step.get("round") or 0) + 1
            self._review(card, st, step, round_no)
            return
        if step.get("kind") != "review":
            return
        verdict = st.verdicts.get((ticket, step.get("round")))
        if verdict is None:
            return
        if verdict.get("verdict") == "REQUEST_CHANGES":
            round_no = int(step.get("round") or 0)
            if round_no < 3 or (st.review_decisions.get((ticket, round_no)) or {}).get("choice") == "retry":
                self._fix(card, st, step)
            elif (ticket, round_no) not in st.review_questions:
                self.journal.append(card, "review.question", ticket=ticket, round=round_no,
                                    question="Review nadal wykazuje BLOCKER lub MAJOR. Ponowić poprawki czy odrzucić ticket?")
            return
        if any(s.get("kind") == "conflict" and s.get("ticket") not in st.rejected and
               not (st.results.get(("merge", s.get("ticket"))) or {}).get("ok")
               for s in st.steps.values()):
            return  # wszystkie scalenia używają jednego worktree karty
        if ("merge", ticket) not in st.results:
            source = next((s for s in st.steps.values() if s.get("ticket") == ticket and
                           s.get("kind") == "ticket"), None)
            if source is not None:
                self._merge(card, st, source)
        merged = fold(self.journal.read(card)).results.get(("merge", ticket))
        if merged and not merged.get("ok") and "konflikt" in (merged.get("error") or "").lower():
            current = fold(self.journal.read(card))
            if not any(s.get("kind") == "conflict" and s.get("ticket") == ticket for s in current.steps.values()):
                self._start_conflict(card, current, step, merged)
            return
        if merged and merged.get("ok"):
            self._cleanup(card, fold(self.journal.read(card)), ticket, int(step.get("round") or 1))
            if st.plan_approved is not None:
                self._plan_next(card, fold(self.journal.read(card)))

    def _plan_next(self, card: str, st: FlowState) -> None:
        """Uruchamia gotowe tickety w granicach slotów, po scaleniu wszystkich ich blokerów."""
        approved = st.plan_approved or {}
        if not approved or st.plan_finished or st.paused or st.open_intents():
            return
        rows = approved.get("rows") or []
        merged = set(st.imported_done) | {row["id"] for row in rows if (st.results.get(("merge", row["id"])) or {}).get("ok")}
        item, project = self._project(card)
        card_branch = ticket_of(st)["cardBranch"] or f"card/{card.lower()}-{slug_from_prompt(item.title, words=4, length=32)}"
        main = self.git.main_branch(project.repo)
        if not main:
            raise FlowError("no-base", f"{project.repo}: nie ma gałęzi głównej")
        card_worktree = worktree_for(project.repo, card_branch)
        made = self._act(card, "card-branch", card_branch,
                         lambda: self.git.branch_worktree(project.repo, card_branch, card_worktree, main),
                         repo=project.repo, branch=card_branch, worktree=card_worktree, base=main)
        card_work = any(s.get("kind") in ("conflict", "close") and
                        (s["step"] not in st.finished or
                         (s.get("kind") == "conflict" and s.get("ticket") not in st.rejected and
                          not (st.results.get(("merge", s.get("ticket"))) or {}).get("ok")))
                        for s in st.steps.values())
        if not card_work:
            self._plan_commit(card, fold(self.journal.read(card)), project.repo, card_branch, card_worktree)
        if len((merged | st.rejected) & {row["id"] for row in rows}) == len(rows):
            self._advance_close(card, st)
            return
        for row in rows:
            ticket = row["id"]
            if ticket in merged or ticket in st.rejected:
                continue
            if any(s.get("ticket") == ticket for s in st.steps.values()):
                continue  # inne tickety mogą pracować mimo pytania tego ticketu
            if not set(row["blockers"]) <= merged:
                continue
            cfg = self.models_config() if self.models_config else None
            catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
            if catalog is None:
                raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
            try:
                selected = execution.resolve(row["executor"], catalog, config=cfg,
                                             probe=self.model_probe, which=self.which)
                reviewer = execution.resolve(row["reviewer"], catalog, config=cfg,
                                             probe=self.model_probe, which=self.which)
            except execution.ExecutionError as exc:
                raise FlowError(exc.code, str(exc)) from None
            if selected.model.lower() == reviewer.model.lower() or selected.local != reviewer.local:
                raise FlowError("bad-reviewer", f"{ticket}: recenzent nie spełnia warunku innego modelu")
            slug = slug_from_prompt(row["title"], words=4, length=32)
            branch = f"ticket/{ticket.lower()}-{slug}"
            worktree = worktree_for(project.repo, branch)
            made_ticket = self._act(card, "ticket-branch", ticket, lambda: {
                **self.git.branch_worktree(project.repo, branch, worktree, f"refs/heads/{card_branch}"),
                "ticket": ticket}, repo=project.repo, branch=branch, worktree=worktree,
                base=f"refs/heads/{card_branch}", ticket=ticket)
            if ticket not in st.assignments:
                self.journal.append(card, "ticket.assigned", ticket=ticket, executor=selected.name,
                                    program=selected.tool, model=selected.model, provider=selected.provider,
                                    executorRouteUrl=selected.route.url if selected.route else "",
                                    executorRouteKind=selected.route.kind if selected.route else "",
                                    executorRouteTokenFile=selected.route.token_file if selected.route else "",
                                    executorEffort=selected.effort,
                                    reviewer=reviewer.name, reviewerProgram=reviewer.tool,
                                    reviewerModel=reviewer.model, reviewerProvider=reviewer.provider,
                                    reviewerRouteUrl=reviewer.route.url if reviewer.route else "",
                                    reviewerRouteKind=reviewer.route.kind if reviewer.route else "",
                                    reviewerRouteTokenFile=reviewer.route.token_file if reviewer.route else "",
                                    reviewerEffort=reviewer.effort,
                                    preferredProvider="mixed" if selected.provider != reviewer.provider else selected.provider)
            body = self._plan_card(card, row)
            out = worktree + ".step/out"
            prompt = handoff.ticket_card(card_id=card, title=row["title"], description=body,
                                         project=project.name, ticket=ticket, branch=branch,
                                         card_branch=card_branch, worktree=worktree, out_dir=out)
            step_id = self._next_step_id(card, ticket, st)
            try:
                self._launch(item, project, selected, ticket, made, made_ticket, step_id, [], prompt_text=prompt)
            except FlowDeferred:
                continue
            st = fold(self.journal.read(card))
        # Odrzucony ticket nie blokuje niezależnych gałęzi grafu.

    def _plan_commit(self, card: str, st: FlowState, repo: str, branch: str, worktree: str) -> None:
        approved = st.plan_approved or {}
        rows = approved.get("rows") or []
        merged = set(st.imported_done) | {row["id"] for row in rows if (st.results.get(("merge", row["id"])) or {}).get("ok")}
        key = f"{approved.get('seq')}:{st.plan_change_seq}"
        files = self._plan_files(card, st, merged)
        self._act(card, "plan-commit", key,
                  lambda: self.git.commit_plan(repo, branch, worktree, files),
                  retry_failed=True,
                  repo=repo, branch=branch, worktree=worktree)

    def _plan_files(self, card: str, st: FlowState, merged: set[str]) -> dict[str, bytes]:
        approved, proposed = st.plan_approved or {}, st.plan_proposed or {}
        def cell(value):
            return " ".join(str(value or "").replace("|", "\\|").split())
        tracker = (st.started or {}).get('trackerProject') or ''
        lines = ["---", "tide-plan: 1", f"title: Plan karty {card}",
                 'tracker: youtrack' if tracker else 'tracker: local',
                 'tracker-url: ' + ((st.started or {}).get('trackerUrl') or ''), 'tracker-project: ' + tracker,
                 f"coordinator: {proposed.get('coordinator') or 'claude'}",
                 f"session: {cell(proposed.get('session'))}",
                 f"ticket-prefix: {proposed.get('prefix') or ''}", f"card: {card}",
                 "status: done" if len(merged | st.rejected) == len(approved.get("rows") or []) else "status: active",
                 f"updated: {st.plan_change_ts or approved.get('ts') or ''}", "---", "",
                 "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |",
                 "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        files = {}
        for row in approved.get("rows") or []:
            ticket = row["id"]
            state = self._plan_row_state(st, ticket, merged)
            branch = f"ticket/{ticket.lower()}-{slug_from_prompt(row['title'], words=4, length=32)}"
            lines.append("| " + " | ".join(cell(value) for value in
                         (ticket, row["title"], state, branch, row["executor"], row.get("note") or "",
                          row.get("class") or "", row["reviewer"], ", ".join(row["blockers"]))) + " |")
            ticket_dir = os.path.dirname(approved["planFile"]).replace("/plans", "/tickets")
            ticket_path = f"{ticket_dir}/{card.lower()}-flow/{ticket.lower()}.md"
            files[ticket_path] = self._plan_card(card, row).encode("utf-8")
        files[approved["planFile"]] = ("\n".join(lines) + "\n").encode("utf-8")
        return files

    @staticmethod
    def _plan_row_state(st: FlowState, ticket: str, merged: set[str]) -> str:
        if ticket in merged:
            return "gotowe"
        if ticket in st.rejected:
            return "odrzucone"
        if any(waiting_ticket == ticket for waiting_ticket, _ in st.waiting):
            return "czeka"
        if (st.results.get(("merge", ticket)) or {}).get("ok") is False:
            return "czeka"
        steps = [s for s in st.steps.values() if s.get("ticket") == ticket]
        if not steps:
            return "w-toku" if ticket in st.assignments else "todo"
        step = max(steps, key=lambda s: int(s.get("seq") or 0))
        finished = st.finished.get(step.get("step")) or {}
        if not finished:
            return "review" if step.get("kind") == "review" else "w-toku"
        if finished.get("result") != "done":
            return "czeka"
        if step.get("kind") in ("ticket", "fix"):
            return "review"
        verdict = st.verdicts.get((ticket, step.get("round"))) or {}
        if verdict.get("verdict") == "REQUEST_CHANGES":
            if int(step.get("round") or 0) >= 3 and (
                    st.review_decisions.get((ticket, step.get("round"))) or {}).get("choice") != "retry":
                return "czeka"
            return "w-toku"
        return "review"

    def _plan_card(self, card: str, row: dict) -> str:
        if "body" in row:
            return row["body"]
        path = os.path.join(self.store.root, row.get("cardPath") or "")
        root = os.path.realpath(os.path.join(self.store.root, self.journal.rel_dir(card), "plan", "tickets"))
        if not os.path.realpath(path).startswith(root + os.sep):
            raise FlowError("bad-plan-card", "kartka ticketu leży poza migawką planu")
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read(OUTPUT_MAX)
        except OSError as exc:
            raise FlowError("bad-plan-card", f"brak kartki {row.get('id')}: {exc}") from None

    def _ticket_text(self, card: str, st: FlowState, ticket: str, item) -> tuple[str, str]:
        if st.plan_approved is not None:
            row = next((r for r in st.plan_approved.get("rows") or [] if r.get("id") == ticket), None)
            if row is not None:
                return row["title"], self._plan_card(card, row) + "\n\nOpis karty:\n" + (item.description or "")
        return item.title, item.description

    def _next_step_id(self, card: str, ticket: str, st: FlowState) -> str:
        prefix = f"{card}-{ticket}-"
        used = set(st.steps) | {key for action, key in st.intents if action == "step-start"}
        numbers = [int(name[len(prefix):]) for name in used
                   if name.startswith(prefix) and name[len(prefix):].isdigit()]
        return f"{prefix}{max(numbers, default=0) + 1}"

    def resolve_step(self, card_id: str, request: dict) -> dict:
        """Odpowiedź na wynik kroku; zdarzenie pozwala wznowić go także po restarcie usługi."""
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if not isinstance(request, dict) or set(request) - {"choice", "answer", "executor"}:
            raise FlowError("bad-request", "oczekiwano wyboru i ewentualnie odpowiedzi lub wykonawcy")
        choice = str(request.get("choice") or "")
        st = fold(self.journal.read(card))
        if choice == 'retry' and not st.card_finished and not st.running() and st.tracker_failures():
            self.tracker.reset_failures(card)
            return {'ok': True, 'id': card, 'choice': choice, 'tracker': True}
        focus = decision_step(st)
        step = st.steps.get(focus) or {}
        done = st.finished.get(focus) or {}
        result = done.get("result")
        valid = {"question": ("answer",), "denied": ("allow", "deny"),
                 "error": ("retry", "change-executor")}.get(result, ())
        busy = any((s.get("worktree") == step.get("worktree") if step.get("kind") in
                    ("plan-amend", "plan-review", "plan-revise") else s.get("ticket") == step.get("ticket"))
                   for s in st.running())
        if st.card_finished or busy or not step or choice not in valid or \
                (result == "error" and int(step.get("attempt") or 0) == 0):
            raise FlowError("no-step-question", f"{card}: ten krok nie czeka na taką decyzję")
        previous = st.step_decisions.get(focus)
        if previous:
            if previous.get("choice") == choice and previous.get("answer", "") == str(request.get("answer") or "") and \
                    previous.get("executor", "") == str(request.get("executor") or ""):
                return {"ok": True, "id": card, "step": focus, "choice": choice, "already": True}
            raise FlowError("already-decided", f"{card}: odpowiedź na ten krok jest już zapisana")
        answer = str(request.get("answer") or "").strip()
        executor = str(request.get("executor") or "").strip()
        if choice == "answer" and (not answer or len(answer) > 4000):
            raise FlowError("bad-answer", "odpowiedź musi mieć od 1 do 4000 znaków")
        if choice == "change-executor":
            if not executor:
                raise FlowError("bad-executor", "wybierz wykonawcę")
            self._resume_executor(card, st, step, executor, changed=True)
        if choice == "allow":
            from ..board.commands import MAX_COMMAND, is_forbidden
            command = str(done.get("command") or "").strip()
            if not command:
                raise FlowError("bad-command", "krok nie podał polecenia do zatwierdzenia")
            if len(command) > MAX_COMMAND:
                raise FlowError("command-too-long", f"polecenie przekracza {MAX_COMMAND} znaków i nie może być zatwierdzone")
            _, project = self._project(card)
            if is_forbidden(command, project.denied_commands):
                raise FlowError("forbidden-command", f"polecenie „{command}” jest zabronione w projekcie")
            if command not in project.allowed_commands:
                try:
                    self.store.upsert_project({"slug": project.slug,
                                               "allowedCommands": [*project.allowed_commands, command]})
                except BoardError as exc:
                    raise FlowError(exc.code, str(exc)) from None
        event = self.journal.append(card, "step.decision", step=focus, ticket=step.get("ticket") or "",
                                    choice=choice, answer=answer if choice == "answer" else "",
                                    command=done.get("command") or "" if choice in ("allow", "deny") else "",
                                    executor=executor if choice == "change-executor" else "")
        return {"ok": True, "id": card, "step": focus, "choice": choice, "seq": event["seq"]}

    def _resume_executor(self, card: str, st: FlowState, step: dict, name: str, *, changed=False):
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        try:
            selected = execution.resolve(name, catalog, config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if not changed and (selected.name != step.get("executor") or selected.model != step.get("model") or
                            selected.provider != step.get("provider") or selected.tool != step.get("program") or
                            selected.effort != step.get("effort", "") or
                            (selected.route.url if selected.route else "") != step.get("routeUrl", "") or
                            (selected.route.kind if selected.route else "") != step.get("routeKind", "") or
                            (selected.route.token_file if selected.route else "") != step.get("routeTokenFile", "")):
            raise FlowError("changed-executor", f"{card}: wykonawca zmienił się od startu kroku")
        if changed and step.get("kind") == "plan-review":
            planner = catalog.get((st.plan_proposed or {}).get("planner"))
            if planner and (selected.name == planner.name or selected.model.lower() == planner.model.lower()):
                raise FlowError("bad-executor", "review planu wymaga modelu innego niż planujący")
        if changed and step.get("kind") not in ("ticket", "fix", "review"):
            if step.get("kind") not in ("conflict", "close") and selected.local != bool(step.get("local")):
                raise FlowError("bad-executor-provider", "zachowaj lokalny albo chmurowy zakres planowania")
            return selected
        if changed:
            opposite_kind = ("ticket", "fix") if step.get("kind") == "review" else ("review",)
            opposite = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == step.get("ticket")
                             and s.get("kind") in opposite_kind), None) or {}
            if step.get("kind") == "review":
                other_model = opposite.get("model") or (st.started or {}).get("model") or ""
                other_provider = opposite.get("provider") or (st.started or {}).get("provider") or ""
            else:
                other_model = opposite.get("model") or (st.started or {}).get("reviewerModel") or ""
                other_provider = opposite.get("provider") or (st.started or {}).get("reviewerProvider") or ""
            if selected.model.lower() == other_model.lower():
                raise FlowError("bad-executor", "wybrany wykonawca ma ten sam model co drugi uczestnik review")
            if selected.local != (other_provider == "local"):
                raise FlowError("bad-executor-provider", "praca lokalna wymaga lokalnego autora i recenzenta")
            preferred = (st.started or {}).get("preferredProvider") or ""
            if (preferred == "mixed" and selected.provider == other_provider) or \
                    (preferred != "mixed" and selected.provider != other_provider):
                raise FlowError("bad-executor-provider", "wybierz wykonawcę zgodnego z recenzentem karty")
        return selected

    def _resume_step(self, card: str, st: FlowState, step: dict, decision: dict, *, attempt=0) -> None:
        """Ten sam ticket i worktree; nowa próba ma osobny katalog wyniku i zdarzenie."""
        if any(s.get("parentStep") == step.get("step") for s in st.steps.values()):
            return
        item, project = self._project(card)
        info = ticket_of(st, step.get("ticket") or "")
        resume_ticket = step.get("ticket") or ("" if step.get("kind") == "close" or
                                               str(step.get("kind") or "").startswith("plan") else info["id"])
        choice = decision.get("choice") or ""
        name = decision.get("executor") if choice == "change-executor" else step.get("executor") or ""
        step_id = self._next_step_id(card, resume_ticket, st)
        try:
            selected = self._resume_executor(card, st, step, name, changed=choice == "change-executor")
        except FlowError as exc:
            if exc.code != "local-unavailable" or choice == "change-executor":
                raise
            self.journal.append(card, "step.started", step=step_id, kind=step.get("kind") or "ticket",
                                ticket=resume_ticket, round=step.get("round") or 0,
                                executor=name, program=step.get("program") or "", model=step.get("model") or "",
                                provider=step.get("provider") or "", local=True,
                                effort=step.get("effort") or "", routeUrl=step.get("routeUrl") or "",
                                routeKind=step.get("routeKind") or "",
                                routeTokenFile=step.get("routeTokenFile") or "",
                                parentStep=step.get("step") or "", attempt=attempt, session=step.get("session") or "",
                                worktree=step.get("worktree") or "", branch=step.get("branch") or "",
                                stepDir=step.get("stepDir") or "", reviewTip=step.get("reviewTip") or "")
            self.journal.append(card, "step.finished", step=step_id, ticket=resume_ticket,
                                result="error", reason="local-unavailable", question="", command="", notes=[],
                                kept=[], exit=None)
            return
        old_prompt = os.path.join(step.get("stepDir") or "", "in", "prompt.txt")
        try:
            with open(old_prompt, encoding="utf-8") as fh:
                prompt = fh.read(OUTPUT_MAX)
        except OSError:
            raise FlowError("missing-step-input", f"{card}: nie ma kartki poprzedniego kroku") from None
        old_out = os.path.join(step.get("stepDir") or "", "out")
        resume_worktree = step.get("worktree") or info["worktree"]
        resume_branch = step.get("branch") or info["branch"]
        new_out = resume_worktree + f".resume-{step_id}.step/out"
        prompt = prompt.replace(old_out, new_out)
        prompt += handoff.resume_context(choice=choice, prior=st.finished.get(step.get("step")) or {},
                                         answer=decision.get("answer") or "", command=decision.get("command") or "",
                                         out_dir=new_out)
        session = "" if choice == "change-executor" else step.get("session") or ""
        if not session and choice != "change-executor" and step.get("program") == "codex":
            finder = getattr(execution, "codex_session_id", None)
            if finder:
                session = finder(os.path.join(old_out, "step.log"))
        self._launch(item, project, selected, resume_ticket,
                     {"branch": info["cardBranch"]},
                     {"branch": resume_branch, "worktree": resume_worktree},
                     step_id, [], kind=step.get("kind") or "ticket", round_no=int(step.get("round") or 0),
                     prompt_text=prompt, review_tip=step.get("reviewTip") or "",
                     parent_step=step.get("step") or "", attempt=attempt, resume_session=session)

    def resolve_review(self, card_id: str, choice: str) -> dict:
        """Jawna decyzja po trzecim i każdym następnym nieudanym review."""
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if choice not in ("retry", "reject"):
            raise FlowError("bad-choice", "wybierz ponowne poprawki albo odrzucenie ticketu")
        st = fold(self.journal.read(card))
        st.last_step = attention_step(st) or st.last_step
        step = st.steps.get(st.last_step) or {}
        ticket, round_no = step.get("ticket") or "", int(step.get("round") or 0)
        verdict = st.verdicts.get((ticket, round_no)) or {}
        previous = st.review_decisions.get((ticket, round_no)) or {}
        if previous.get("choice") == choice == "reject" and not st.card_finished:
            if ticket not in st.rejected:
                self.journal.append(card, "ticket.rejected", ticket=ticket)
            return {"ok": True, "id": card, "ticket": ticket, "round": round_no, "choice": choice,
                    "already": True}
        if st.card_finished or any(s.get("ticket") == ticket for s in st.running()) or step.get("kind") != "review" or round_no < 3 or \
                verdict.get("verdict") != "REQUEST_CHANGES" or \
                (ticket, round_no) not in st.review_questions or \
                previous:
            raise FlowError("no-review-question", f"{card}: nie ma pytania o review do rozstrzygnięcia")
        self.journal.append(card, "review.decision", ticket=ticket, round=round_no, choice=choice)
        if choice == "reject":
            self.journal.append(card, "ticket.rejected", ticket=ticket)
        return {"ok": True, "id": card, "ticket": ticket, "round": round_no, "choice": choice}

    def _project(self, card: str):
        board = self.store.load()
        item = board.cards.get(card_num(card))
        project = board.projects.get(item.project) if item else None
        if item is None or project is None:
            raise FlowError("no-project", f"{card}: nie ma karty albo projektu dla kroku")
        return item, project

    def _note(self, card: str, step: dict) -> str:
        name = handoff.REVIEW_FILE if step.get("kind") == "review" else handoff.NOTE_FILE
        path = os.path.join(self.store.root, self.journal.rel_dir(card), "steps", step.get("step") or "", name)
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read(OUTPUT_MAX)
        except OSError:
            return ""

    def _rules(self, repo: str) -> str:
        out = []
        for name in ("AGENTS.md", "CLAUDE.md"):
            path = os.path.join(repo, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    out.append(f"### {name}\n{fh.read(OUTPUT_MAX)}")
            except OSError:
                pass
        return "\n\n".join(out)

    def _review(self, card: str, st: FlowState, source: dict, round_no: int) -> None:
        item, project = self._project(card)
        ticket = source["ticket"]
        assigned = st.assignments.get(ticket) or st.started or {}
        info = ticket_of(st, ticket)
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-reviewer", f"{card}: katalog recenzentów jest niedostępny")
        previous = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == ticket and
                         s.get("kind") == "review"), {})
        try:
            reviewer_name = previous.get("executor") or assigned.get("reviewer") or ""
            reviewer = execution.resolve(reviewer_name, catalog, config=cfg,
                                         probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        expected = {
            "program": previous.get("program") or assigned.get("reviewerProgram"),
            "model": previous.get("model") or assigned.get("reviewerModel"),
            "provider": previous.get("provider") or assigned.get("reviewerProvider"),
            "effort": previous.get("effort", assigned.get("reviewerEffort", "")),
            "routeUrl": previous.get("routeUrl", assigned.get("reviewerRouteUrl", "")),
            "routeKind": previous.get("routeKind", assigned.get("reviewerRouteKind", "")),
            "routeTokenFile": previous.get("routeTokenFile", assigned.get("reviewerRouteTokenFile", "")),
        }
        if reviewer.tool != expected["program"] or reviewer.model != expected["model"] or \
                reviewer.provider != expected["provider"] or reviewer.effort != expected["effort"] or \
                (reviewer.route.url if reviewer.route else "") != expected["routeUrl"] or \
                (reviewer.route.kind if reviewer.route else "") != expected["routeKind"] or \
                (reviewer.route.token_file if reviewer.route else "") != expected["routeTokenFile"] or \
                reviewer.model.lower() == (source.get("model") or "").lower() or \
                reviewer.local != (source.get("provider") == "local") or \
                (assigned.get("preferredProvider") == "mixed" and reviewer.provider == source.get("provider")) or \
                (assigned.get("preferredProvider") != "mixed" and reviewer.provider != source.get("provider")):
            raise FlowError("changed-reviewer", f"{card}: katalog zmienił recenzenta od startu ticketu")
        path = info["worktree"]
        tip = self.git.clean_tip(project.repo, info["branch"], path)
        diff = self.git.diff(project.repo, info["cardBranch"], info["branch"])
        step_id = self._next_step_id(card, ticket, st)
        out = path + f".review-{round_no}.step/out"
        title, description = self._ticket_text(card, st, ticket, item)
        prompt = handoff.review_card(card_id=card, title=title, description=description, ticket=ticket,
                                     round_no=round_no, card_branch=info["cardBranch"],
                                     ticket_branch=info["branch"], worktree=path, out_dir=out,
                                     diff=diff, note=self._note(card, source), rules=self._rules(project.repo))
        self._launch(item, project, reviewer, ticket,
                     {"branch": info["cardBranch"]},
                     {"branch": info["branch"], "worktree": path}, step_id, [],
                     kind="review", round_no=round_no, prompt_text=prompt, review_tip=tip)

    def _fix(self, card: str, st: FlowState, review_step: dict) -> None:
        item, project = self._project(card)
        ticket = review_step["ticket"]
        assigned = st.assignments.get(ticket) or st.started or {}
        info = ticket_of(st, ticket)
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("no-executor", f"{card}: katalog wykonawców jest niedostępny")
        author = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == ticket and
                       s.get("kind") in ("ticket", "fix")), None)
        if author is None:
            raise FlowError("no-executor", f"{card}: nie ma kroku autora do poprawienia")
        try:
            executor = execution.resolve(author.get("executor") or assigned.get("executor") or "", catalog,
                                         config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if executor.tool != (author.get("program") or assigned.get("program")) or \
                executor.model != (author.get("model") or assigned.get("model")) or \
                executor.provider != (author.get("provider") or assigned.get("provider")) or \
                executor.effort != author.get("effort", assigned.get("executorEffort", "")) or \
                (executor.route.url if executor.route else "") != author.get("routeUrl", assigned.get("executorRouteUrl", "")) or \
                (executor.route.kind if executor.route else "") != author.get("routeKind", assigned.get("executorRouteKind", "")) or \
                (executor.route.token_file if executor.route else "") != author.get("routeTokenFile", assigned.get("executorRouteTokenFile", "")):
            raise FlowError("changed-executor", f"{card}: katalog zmienił wykonawcę poprawek")
        round_no = int(review_step.get("round") or 1)
        step_id = self._next_step_id(card, ticket, st)
        out = info["worktree"] + f".fix-{round_no}.step/out"
        title, description = self._ticket_text(card, st, ticket, item)
        prompt = handoff.fix_card(card_id=card, title=title, description=description, ticket=ticket,
                                  round_no=round_no, branch=info["branch"], worktree=info["worktree"],
                                  out_dir=out, review=self._note(card, review_step))
        self._launch(item, project, executor, ticket,
                     {"branch": info["cardBranch"]},
                     {"branch": info["branch"], "worktree": info["worktree"]},
                     step_id, [], kind="fix", round_no=round_no, prompt_text=prompt)

    def _cleanup(self, card: str, st: FlowState, ticket: str, rounds: int) -> None:
        info = ticket_of(st, ticket)
        repo = self._repo(card)
        self._act(card, "cleanup", ticket,
                  lambda: self.git.remove_worktree(repo, info["branch"], info["worktree"]),
                  repo=repo, branch=info["branch"], worktree=info["worktree"])

    def _recover_verdicts(self, card: str) -> None:
        st = fold(self.journal.read(card))
        for name, finished in st.finished.items():
            step = st.steps.get(name) or {}
            if step.get("kind") != "review" or finished.get("result") != "done" or \
                    (step.get("ticket"), step.get("round")) in st.verdicts:
                continue
            path = os.path.join(step.get("stepDir") or "", "out", handoff.RESULT_FILE)
            verdict = self._review_result(path)
            try:
                tip = self.git.clean_tip(step.get("repo") or self._repo(card),
                                         step.get("branch") or "", step.get("worktree") or "")
            except FlowGitError:
                tip = ""
            if verdict and tip and tip == step.get("reviewTip"):
                self.journal.append(card, "review.verdict", step=name, ticket=step["ticket"],
                                    round=step["round"], reviewer=step.get("executor") or "", **verdict)

    def _recover_review_rejections(self, card: str) -> None:
        """Decyzja reject i zdarzenie odrzucenia mogą zostać rozdzielone restartem usługi."""
        st = fold(self.journal.read(card))
        for (ticket, _round), decision in st.review_decisions.items():
            if decision.get("choice") == "reject" and ticket not in st.rejected:
                self.journal.append(card, "ticket.rejected", ticket=ticket)
                st.rejected.add(ticket)

    def _reconcile(self, card: str, intent: dict) -> None:
        """Akcja zaczęta przed restartem usługi, bez wyniku: dokończenie albo jawny błąd (nigdy druga kopia)."""
        action, key = intent.get("action"), intent.get("key")
        if intent.get('youtrack'):
            try:
                self.tracker.run(card, action, key, intent['payload'])
            except yt.YouTrackError:
                pass
            return
        if action == "reserve":
            self._act(card, action, key,
                      lambda: self._reserve(intent["repo"], intent["title"], f"{card}:{key}",
                                            intent.get("prefix") or "", int(intent.get("minimumNumber") or 0)))
            return
        if action == "plan-commit":
            self._plan_commit(card, fold(self.journal.read(card)), intent["repo"],
                              intent["branch"], intent["worktree"])
            return
        if action in ("card-branch", "ticket-branch", "review-branch", "step-reopen"):
            def make_branch():
                made = self.git.branch_worktree(intent["repo"], intent["branch"],
                                                intent["worktree"], intent["base"])
                return {**made, **({"ticket": intent["ticket"]} if action == "ticket-branch" else {})}
            self._act(card, action, key, make_branch)
            return
        if action == "cleanup":
            self._act(card, action, key,
                      lambda: self.git.remove_worktree(intent["repo"], intent["branch"], intent["worktree"]))
            return
        if action == "merge":
            st = fold(self.journal.read(card))
            step = next((s for s in st.steps.values() if s.get("ticket") == key), None)
            if step is not None:
                self._merge(card, st, step)
            return
        if action == "step-start":
            unit = intent.get("unit") or f"tide-step-{key}"
            result = os.path.join(intent.get("stepDir") or "", "out", handoff.RESULT_FILE)
            state = stepstop.unit_state(unit)
            code = stepstop.read_exit_code(intent.get("stepDir") or "") if state == "inactive" else None
            if state == "unknown":
                return
            if state == "active" or (code == 0 and os.path.isfile(result)):
                git_dir = ""
                try:
                    git_dir = agentcmd.resolve_git_dir(self._repo(card), intent.get("worktree") or "")
                except agentcmd.StepError:
                    pass
                self.journal.append(card, "result", action=action, key=key, ok=True, unit=unit, gitDir=git_dir)
                self._recover_started_steps(card, fold(self.journal.read(card)))
                return
            if code not in (None, 0):
                self.journal.append(card, "result", action=action, key=key, ok=False,
                                    error=f"program zakończył się kodem {code}")
                return
        self.journal.append(card, "result", action=action, key=key, ok=False,
                            error="przerwane restartem usługi przed końcem akcji")

    def _recover_started_steps(self, card: str, st: FlowState) -> None:
        """Wynik startu zapisany przed awarią oznacza żywy albo zakończony krok, nawet bez step.started."""
        ticket = ticket_of(st)
        for (action, key), result in st.results.items():
            if action != "step-start" or not result.get("ok") or key in st.steps:
                continue
            intent = st.intents.get((action, key)) or {}
            self.journal.append(card, "step.started", step=key, kind=intent.get("kind") or "ticket",
                                ticket=intent.get("ticket") or ticket["id"], round=intent.get("round") or 0,
                                executor=intent.get("executor") or (st.started or {}).get("executor") or "",
                                program=intent.get("program") or (st.started or {}).get("program") or "claude",
                                model=intent.get("model") or (st.started or {}).get("model") or "",
                                provider=intent.get("provider") or (st.started or {}).get("provider") or "",
                                effort=intent.get("effort") or "", routeUrl=intent.get("routeUrl") or "",
                                routeKind=intent.get("routeKind") or "",
                                routeTokenFile=intent.get("routeTokenFile") or "",
                                local=bool(intent.get("local", (st.started or {}).get("provider") == "local")),
                                unit=result.get("unit") or intent.get("unit") or "",
                                session=intent.get("session") or "", worktree=intent.get("worktree") or "",
                                branch=intent.get("branch") or ticket["branch"], stepDir=intent.get("stepDir") or "",
                                reviewTip=intent.get("reviewTip") or "",
                                parentStep=intent.get("parentStep") or "", attempt=intent.get("attempt") or 0,
                                gitDir=result.get("gitDir") or "", repo=self._repo(card))

    def _repo(self, card: str) -> str:
        st = fold(self.journal.read(card))
        return (st.started or {}).get("repo") or ""

    def _poll(self, card: str, step: dict) -> None:
        if step.get("mode") == "interactive":
            self._poll_interactive(card, step)
            return
        name = step.get("step") or ""
        proc = self.procs.get(name)
        exit_code = proc.poll() if proc is not None else None
        if exit_code is not None:
            alive = False
        else:
            state = stepstop.unit_state(step.get("unit") or "")
            if state == "unknown":
                return                       # bez menedżera nie wiemy — nie zgadujemy końca kroku
            alive = state == "active"
            if not alive and proc is not None:
                try:
                    exit_code = proc.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    return                   # menedżer już nie widzi jednostki, launcher jeszcze się kończy
            elif not alive:
                exit_code = stepstop.read_exit_code(step.get("stepDir") or "")
        if not alive:
            self._finish(card, step, exit_code=exit_code)
            return
        try:
            started_at = datetime.fromisoformat(step.get("ts") or "").timestamp()
        except ValueError:
            started_at = self.clock()
        if self.clock() - started_at >= timeout_seconds(self.settings):
            self._stop(card, step, "error", "timeout")
            return
        self._guard(card, step)

    def _poll_interactive(self, card: str, step: dict) -> None:
        """Plik wyniku kończy próbę; po zamknięciu bez pliku użytkownik wybiera dalszy ruch."""
        out = os.path.join(step.get("stepDir") or "", "out", handoff.RESULT_FILE)
        if os.path.isfile(out):
            data = self._result(out)
            if data.get("reason") != "bad-result":
                self._finish(card, step, exit_code=0)
                return
        if step.get("consoleClosed") or self._console_ended(step):
            if not step.get("consoleClosed"):
                self.journal.append(card, "step.console_closed", step=step.get("step"))
            self._finish(card, step, forced={"result": "stopped", "reason": "interactive-closed",
                                             "question": "Karta Konsoli została zamknięta bez wyniku. Wznowić czy porzucić?"})
            return
        task_file = step.get("taskFile") or ""
        try:
            timed_out = bool(task_file) and time.time() - os.stat(task_file).st_mtime > 30
        except OSError:
            timed_out = False  # plik przejęty przez kartę; czekamy na wynik albo znacznik zamknięcia
        if timed_out:
            if self.task_files.cancel(task_file):
                self.journal.append(card, "step.console_closed", step=step.get("step"))
                self._finish(card, step, forced={"result": "stopped", "reason": "interactive-closed",
                                                 "question": "Konsola nie przejęła kroku. Wznowić czy porzucić?"})
                return
        self._guard(card, step, stop_stalled=False)

    def _console_ended(self, step: dict) -> bool:
        """Znacznik lub trwała tożsamość procesu potwierdza koniec, również po restarcie usługi."""
        unit = step.get("consoleUnit") or ""
        if unit and stepstop.unit_state(unit) != "inactive":
            return False  # także nieznany stan jednostki trzyma worktree
        alive = launch.process_alive(step.get("processFile") or "")
        if alive is True:
            return False
        if os.path.isfile(step.get("closeFile") or "") or alive is False:
            return True
        if os.path.exists(step.get("processFile") or ""):
            return False  # nieczytelny zapis procesu nie dowodzi jego końca
        task_file = step.get("taskFile") or ""
        return bool(task_file and not os.path.exists(task_file) and
                    self.clock() - float(step.get("enteredAt") or self.clock()) > 30)

    def _guard(self, card: str, step: dict, *, stop_stalled: bool = True) -> None:
        name = step.get("step") or ""
        now = time.monotonic()
        limit = guard_seconds(self.settings)
        seen = self.watch.get(name)
        if seen is not None and now - seen[2] < min(self.guard_every, limit / 10.0):
            return
        mark = self._progress_mark(step)
        if seen is None:
            self.watch[name] = (mark, now, now)
            return
        if seen[0] != mark:
            self.watch[name] = (mark, now, now)
            activity = (fold(self.journal.read(card)).activity.get(name) or step).get("ts")
            if self.clock() - _epoch(activity) >= 60:
                self.journal.append(card, "step.activity", step=name, ticket=step.get("ticket") or "")
            return
        self.watch[name] = (mark, seen[1], now)
        if stop_stalled and now - seen[1] >= limit:
            print(f"tide-daemon: przepływ karty {card}: krok {name} bez postępu od {int(now - seen[1])} s — zatrzymuję",
                  flush=True)
            stalled = datetime.fromtimestamp(self.clock() - (now - seen[1]), timezone.utc).isoformat(timespec="seconds")
            self._stop(card, step, "error", "no-progress", stalled_since=stalled)

    def _guard_card(self, card: str) -> None:
        """Po trzech minutach bez pracy i bez nazwanego powodu czekania zapisuje pojedynczy alarm."""
        st = fold(self.journal.read(card))
        if guard_reason(st) != "" or st.stalled_since or not st.last_activity_ts:
            return
        item = self.store.load().cards.get(card_num(card))
        if item is None or item.column in ("done", "archived") or self.clock() - st.last_activity_ts < 180:
            return
        event = self.journal.append(card, "card.stalled", reason=st.flow_error or "brak kroku i powodu czekania")
        self._notices.append({"card": card, "step": f"stalled:{event['seq']}", "ticket": "", "result": "stalled",
                              "detail": event["reason"]})

    def _progress_mark(self, step: dict) -> tuple:
        """Odcisk postępu: pliki worktree (bez .git) i dziennik sesji kroku."""
        count = size = newest = 0
        for folder, dirs, files in os.walk(step.get("worktree") or "", followlinks=False):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in files:
                try:
                    st = os.lstat(os.path.join(folder, name))
                except OSError:
                    continue
                count, size, newest = count + 1, size + st.st_size, max(newest, st.st_mtime_ns)
        log = (stepstop.find_claude_log(self.claude_projects, step.get("session") or "")
               if step.get("program") == "claude" else os.path.join(step.get("stepDir") or "", "out", "step.log"))
        try:
            log_st = os.stat(log) if log else None
        except OSError:
            log_st = None
        return count, size, newest, (log_st.st_size, log_st.st_mtime_ns) if log_st else None

    def _stop(self, card: str, step: dict, result: str, reason: str, stalled_since: str = "") -> bool:
        stopped = stepstop.stop_step(self.procs.get(step.get("step") or ""), unit=step.get("unit") or "",
                                     worktree=step.get("worktree") or "", step_dir=step.get("stepDir") or "",
                                     repo=step.get("repo") or self._repo(card), git_dir=step.get("gitDir") or "",
                                     branch=step.get("branch") or "", tool=step.get("program") or "claude",
                                     session_id=step.get("session") or "", claude_projects=self.claude_projects,
                                     codex_roots=self.codex_roots,
                                     grace=self.grace)
        wip = {"stopped": stopped.stopped, "committed": stopped.committed, "commit": stopped.commit,
               "files": stopped.files, "error": stopped.error, "notes": stopped.notes}
        if not stopped.stopped:
            print(f"tide-daemon: przepływ karty {card}: kroku {step.get('step')} nie dało się zatrzymać", flush=True)
            return False                         # następne sprawdzenie spróbuje znowu; worktree nietknięty
        self._finish(card, step, forced={"result": result, "reason": reason, "stalledSince": stalled_since}, wip=wip,
                     messages=stopped.messages, exit_code=stopped.exit_code)
        return True

    def _finish(self, card: str, step: dict, forced: dict | None = None, wip: dict | None = None,
                messages=None, exit_code=None) -> None:
        name = step.get("step") or ""
        if name in fold(self.journal.read(card)).finished:
            return
        out = os.path.join(step.get("stepDir") or "", "out")
        data = forced or self._result(os.path.join(out, handoff.RESULT_FILE))
        if forced is None and exit_code != 0:
            data = {**data, "result": "error", "reason": "lost" if exit_code is None else "exit"}
        if forced is None and step.get("program") == "claude" and data.get("result") == "error":
            denied = self._claude_denied_command(os.path.join(out, "step.log"))
            if denied:
                data = {"result": "denied", "command": denied, "notes": data.get("notes") or []}
        verdict = None
        if step.get("kind") == "review" and data.get("result") == "done":
            verdict = self._review_result(os.path.join(out, handoff.RESULT_FILE))
            if verdict is None:
                data = {"result": "error", "reason": "bad-review"}
            else:
                try:
                    tip = self.git.clean_tip(step.get("repo") or self._repo(card),
                                             step.get("branch") or "", step.get("worktree") or "")
                except FlowGitError:
                    tip = ""
                if not tip or tip != step.get("reviewTip"):
                    verdict = None
                    data = {"result": "error", "reason": "review-changed-ticket"}
        merged_tip = ""
        if step.get("kind") == "conflict" and data.get("result") == "done":
            info = ticket_of(fold(self.journal.read(card)), step.get("ticket") or "")
            try:
                merged_tip = self.git.merged_tip(step.get("repo") or self._repo(card),
                                                 info["cardBranch"], info["branch"], info["cardWorktree"],
                                                 step.get("reviewTip") or "",
                                                 (fold(self.journal.read(card)).intents.get(
                                                     ("merge", step.get("ticket") or "")) or {}).get("cardTip") or "")
            except FlowGitError as exc:
                data = {"result": "error", "reason": f"konflikt nierozwiązany: {exc}"}
        if step.get("kind") == "close" and data.get("result") == "done":
            summary = os.path.join(out, handoff.SUMMARY_FILE)
            try:
                valid = not os.path.islink(summary) and os.path.getsize(summary) <= OUTPUT_MAX
                with open(summary, encoding="utf-8") as fh:
                    text = fh.read(OUTPUT_MAX)
                valid = valid and bool(text.strip()) and (
                    (fold(self.journal.read(card)).started or {}).get("check") != "manual" or
                    "Co przetestować ręcznie i jak" in text)
            except (OSError, UnicodeError):
                valid = False
            if not valid:
                data = {"result": "error", "reason": "brak poprawnego podsumowania karty"}
        kept = []
        for entry in sorted(os.listdir(out)) if os.path.isdir(out) else []:
            path = os.path.join(out, entry)
            if (entry == handoff.RESULT_FILE or entry.endswith(".md")) and os.path.isfile(path) \
                    and not os.path.islink(path) and os.path.getsize(path) <= OUTPUT_MAX:
                with open(path, "rb") as fh:
                    self.journal.write_file(card, os.path.join("steps", name, entry), fh.read())
                kept.append(entry)
        extra = {k: v for k, v in (("wip", wip), ("messages", messages)) if v}
        if data.get("result") == "denied":
            from ..board.commands import MAX_COMMAND, is_forbidden
            _, project = self._project(card)
            command = data.get("command") or ""
            data["canAllow"] = len(command) <= MAX_COMMAND and not is_forbidden(command, project.denied_commands)
        self.journal.append(card, "step.finished", step=name, ticket=step.get("ticket") or "",
                            result=data.get("result"), reason=data.get("reason") or "",
                            question=data.get("question") or "", command=data.get("command") or "",
                            canAllow=data.get("canAllow", True), stalledSince=data.get("stalledSince") or "",
                            notes=[n for n in data.get("notes") or [] if n in kept], kept=kept,
                            exit=exit_code, **extra)
        if merged_tip:
            self.journal.append(card, "result", action="merge", key=step.get("ticket") or "",
                                ok=True, commit=merged_tip, resolved=True)
        if verdict is not None:
            self.journal.append(card, "review.verdict", step=name, ticket=step.get("ticket") or "",
                                round=step.get("round") or 1, reviewer=step.get("executor") or "", **verdict)
        self._queue_notices(card)
        self._release_local(name)
        self.procs.pop(name, None)
        self.watch.pop(name, None)
        shutil.rmtree(os.path.join(self.tmp_root, f"tide-step-{name}"), ignore_errors=True)

    def _queue_notices(self, card: str) -> None:
        """Zdarzenie przed wysłaniem: po restarcie pytanie nie daje drugiego powiadomienia."""
        st = fold(self.journal.read(card))
        if st.card_finished:
            return
        for name, done in st.finished.items():
            step = st.steps.get(name) or {}
            result = done.get("result")
            if name in st.step_notices or name in st.step_decisions or st.card_finished or not (
                    result in ("question", "denied") or
                                               result == "error" and int(step.get("attempt") or 0) >= 1):
                continue
            detail = done.get("question") if result == "question" else done.get("command") if result == "denied" \
                else done.get("reason") or ""
            self.journal.append(card, "step.notice", step=name, ticket=step.get("ticket") or "",
                                result=result)
            st.step_notices.add(name)
            self._notices.append({"card": card, "step": name, "ticket": step.get("ticket") or "",
                                  "result": result, "detail": detail or ""})
        st = fold(self.journal.read(card))

        def once(key: str, result: str, detail: str = "") -> None:
            if key in st.flow_notices:
                return
            self.journal.append(card, "flow.notice", key=key, result=result)
            st.flow_notices.add(key)
            self._notices.append({"card": card, "step": key, "ticket": "", "result": result,
                                  "detail": detail})

        if st.plan_revision and st.plan_revision.get("pending") and st.plan_reviewed:
            once(f"revision:{st.plan_revision.get('seq')}", "plan", "Różnica planu czeka na zatwierdzenie")
        elif st.plan_proposed and st.plan_reviewed and not st.plan_approved:
            once(f"plan:{st.plan_proposed.get('seq')}", "plan", "Plan czeka na zatwierdzenie")
        legacy_plan_done = st.plan_finished and not st.plan_revision and not any(
            step.get("kind") == "close" for step in st.steps.values())
        if (st.closed or legacy_plan_done) and not st.running():
            once(f"review:{st.closed.get('seq') if st.closed else st.plan_approved.get('seq') if st.plan_approved else st.last_seq}",
                 "review", "Karta czeka na sprawdzenie")
        elif st.moved_to == "review" and not st.card_finished and not st.running():
            moved = next((e for e in reversed(self.journal.read(card)) if e.get("type") == "card.moved" and
                          e.get("to") == "review"), {})
            once(f"review:{moved.get('seq') or st.last_seq}", "review", "Karta czeka na sprawdzenie")
        for wait in st.waiting.values():
            if "limit" in (wait.get("reason") or "").lower():
                once(f"limit:{wait.get('seq')}", "limit", wait.get("reason") or "Limit dostawcy")

    @staticmethod
    def _result(path: str) -> dict:
        try:
            if os.path.getsize(path) > OUTPUT_MAX:
                return {"result": "error", "reason": "bad-result"}
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {"result": "error", "reason": "no-result"}
        except (OSError, ValueError):
            return {"result": "error", "reason": "bad-result"}
        if not isinstance(data, dict) or data.get("result") not in RESULTS:
            return {"result": "error", "reason": "bad-result"}
        if (data["result"] == "question" and
                (not isinstance(data.get("question"), str) or not data["question"].strip())) or \
                (data["result"] == "denied" and
                 (not isinstance(data.get("command"), str) or not data["command"].strip() or
                  any(c in data["command"] for c in "\n\r\0"))):
            return {"result": "error", "reason": "bad-result"}
        notes = data.get("notes") if isinstance(data.get("notes"), list) else []
        return {"result": data["result"], "reason": str(data.get("reason") or "")[:300],
                "question": str(data.get("question") or "")[:2000], "command": str(data.get("command") or ""),
                "notes": [str(n) for n in notes if isinstance(n, str)][:20]}

    @staticmethod
    def _claude_denied_command(path: str) -> str:
        """Końcowa odmowa Bash w CLI jest wynikiem `denied`, gdy agent nie zapisał własnego JSON."""
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if len(line) > OUTPUT_MAX or '"permission_denials"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict) or event.get("type") != "result":
                        continue
                    for denial in event.get("permission_denials") or []:
                        if not isinstance(denial, dict) or denial.get("tool_name") != "Bash":
                            continue
                        tool_input = denial.get("tool_input") or {}
                        command = tool_input.get("command") if isinstance(tool_input, dict) else ""
                        if isinstance(command, str) and command.strip() and not any(c in command for c in "\n\r\0"):
                            return command.strip()
        except OSError:
            pass
        return ""

    @staticmethod
    def _review_result(path: str) -> dict | None:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("result") != "done":
            return None
        verdict = data.get("verdict")
        counts = [data.get(key) for key in ("blockers", "majors", "nits")]
        if verdict not in ("APPROVE", "REQUEST_CHANGES") or any(
            isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 999 for n in counts):
            return None
        if (verdict == "APPROVE") != (counts[0] == 0 and counts[1] == 0):
            return None
        return dict(verdict=verdict, blockers=counts[0], majors=counts[1], nits=counts[2])

    def _merge(self, card: str, st: FlowState, step: dict) -> None:
        ticket = step.get("ticket") or ""
        info = ticket_of(st)
        repo = step.get("repo") or self._repo(card)
        approved = next((v for (ticket_id, _round), v in reversed(list(st.verdicts.items()))
                         if ticket_id == ticket and v.get("verdict") == "APPROVE"), None)
        review_step = st.steps.get((approved or {}).get("step")) or {}
        expected_tip = review_step.get("reviewTip") or ""
        if not expected_tip:
            raise FlowError("merge", "brak zatwierdzonego commita ticketu")
        try:
            self._act(card, "merge", ticket, lambda: self.git.merge(
                repo, info["cardBranch"], step.get("branch") or info["branch"], info["cardWorktree"],
                f"Merge ticket {ticket} into card {card}", expected_tip=expected_tip),
                branch=step.get("branch") or info["branch"], into=info["cardBranch"], expectedTip=expected_tip,
                cardTip=self.git.sha(repo, f"refs/heads/{info['cardBranch']}"))
        except FlowError as exc:
            print(f"tide-daemon: przepływ karty {card}: scalenie {ticket}: {exc}", flush=True)

    def _start_conflict(self, card: str, st: FlowState, review_step: dict, failure: dict) -> None:
        """Oddaje konflikt wykonawcy w worktree karty; wynik sprawdza _finish."""
        item, project = self._project(card)
        ticket = review_step["ticket"]
        info = ticket_of(st, ticket)
        assigned = st.assignments.get(ticket) or st.started or {}
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        try:
            selected = execution.resolve(assigned.get("executor") or "", catalog, config=cfg,
                                         probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        step_id = self._next_step_id(card, ticket, st)
        out = info["cardWorktree"] + f".conflict-{step_id}.step/out"
        prompt = handoff.conflict_card(card_id=card, title=item.title, description=item.description,
                                       ticket=ticket, card_branch=info["cardBranch"],
                                       ticket_branch=info["branch"], worktree=info["cardWorktree"],
                                       out_dir=out, error=failure.get("error") or "konflikt")
        self._launch(item, project, selected, ticket,
                     {"branch": info["cardBranch"]},
                     {"branch": info["cardBranch"], "worktree": info["cardWorktree"]},
                     step_id, [], kind="conflict", prompt_text=prompt,
                     review_tip=review_step.get("reviewTip") or "", attempt=1)

    def card_diff(self, card_id: str) -> dict:
        """Różnica gałęzi karty względem głównej, wyłącznie do pokazania użytkownikowi."""
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        st = fold(self.journal.read(card))
        if not st.closed or st.card_finished:
            raise FlowError("not-closed", "karta nie jest jeszcze do sprawdzenia")
        info = ticket_of(st)
        repo = self._repo(card)
        main = self.git.main_branch(repo)
        if not main or not info["cardBranch"]:
            raise FlowError("no-base", "brak gałęzi głównej albo gałęzi karty")
        try:
            diff = self.git.diff(repo, main, info["cardBranch"])
        except FlowGitError as exc:
            raise FlowError("diff", str(exc)) from None
        return {"ok": True, "id": card, "main": main, "branch": info["cardBranch"], "diff": diff}

    def merge_card_to_main(self, card_id: str) -> dict:
        """Jawna akcja użytkownika po domknięciu; nigdy nie wywoływana przez tick."""
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        st = fold(self.journal.read(card))
        if not st.closed or st.plan_revision:
            raise FlowError("not-closed", "karta nie jest jeszcze gotowa do scalenia")
        info = ticket_of(st)
        repo = self._repo(card)
        main = self.git.main_branch(repo)
        if not main or not info["cardBranch"]:
            raise FlowError("no-base", "brak gałęzi głównej albo gałęzi karty")
        if not st.main_merged:
            listed = self.git.run(repo, "worktree", "list", "--porcelain")
            main_tree = next((part.get("worktree") for part in
                              (dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
                               for block in listed.out.split("\n\n"))
                              if part.get("branch") == f"refs/heads/{main}"), "") if listed.ok else ""
            if not main_tree:
                raise FlowError("no-main-worktree", "otwórz gałąź główną w worktree przed scaleniem")
            try:
                tip = self.git.clean_tip(repo, info["cardBranch"], info["cardWorktree"])
                merged = self.git.merge(repo, main, info["cardBranch"], main_tree,
                                        f"Merge card {card} into {main}", expected_tip=tip)
            except FlowGitError as exc:
                raise FlowError("main-merge", str(exc)) from None
            self.journal.append(card, "card.main-merged", main=main, branch=info["cardBranch"],
                                commit=merged["commit"])
        try:
            item = self.store.load().cards.get(card_num(card))
            if item is None:
                raise FlowError("no-card", "nie ma karty do scalenia")
            if item.column != "done":
                self.store.move_card(card, "done", -1, from_column="review")
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if not st.card_finished:
            self.journal.append(card, "card.finished", column="done")
        return {"ok": True, "id": card, "main": main, "already": bool(st.main_merged)}

    def _advance_close(self, card: str, st: FlowState) -> None:
        """Po ostatnim tickecie tworzy podsumowanie; nigdy nie scala do gałęzi głównej."""
        if (st.started is None or st.card_finished or st.closed or st.paused or st.plan_revision or
                st.moved_to == "todo" or st.open_intents()):
            return
        if st.plan_approved:
            rows = st.plan_approved.get("rows") or []
            if not rows or not all((st.results.get(("merge", row["id"])) or {}).get("ok") or
                                   row["id"] in st.rejected or row["id"] in st.imported_done for row in rows):
                return
            if any(row["id"] not in st.rejected and row["id"] not in st.imported_done and
                   not (st.results.get(("cleanup", row["id"])) or {}).get("ok") for row in rows):
                return
            tickets = [{"id": row["id"], "state": "odrzucone" if row["id"] in st.rejected else "gotowe"}
                       for row in rows]
            executor = (st.plan_proposed or {}).get("planner") or rows[0]["executor"]
        elif st.started.get("entry") == "now":
            ticket = next((s.get("ticket") for s in st.steps.values() if s.get("kind") == "ticket"), "")
            if not ticket or not ((st.results.get(("merge", ticket)) or {}).get("ok") or ticket in st.rejected):
                return
            if ticket not in st.rejected and not (st.results.get(("cleanup", ticket)) or {}).get("ok"):
                return
            tickets = [{"id": ticket, "state": "odrzucone" if ticket in st.rejected else "gotowe"}]
            executor = st.started.get("executor") or ""
        else:
            return
        last_return = max((int(e.get("seq") or 0) for e in self.journal.read(card)
                           if e.get("type") == "plan.returned"), default=0)
        closings = [s for s in st.steps.values() if s.get("kind") == "close" and
                    int(s.get("seq") or 0) > last_return]
        if closings:
            latest = closings[-1]
            finished = st.finished.get(latest["step"]) or {}
            if finished.get("result") == "done":
                path = os.path.join(self.store.root, self.journal.rel_dir(card), "steps", latest["step"], handoff.SUMMARY_FILE)
                with open(path, encoding="utf-8") as fh:
                    summary = fh.read(OUTPUT_MAX)
                self.journal.append(card, "card.closed", step=latest["step"], summary=summary,
                                    check=(st.started or {}).get("check") or "auto")
                if st.plan_approved and not st.plan_finished:
                    self.journal.append(card, "plan.finished", merged=sum(1 for t in tickets if t["state"] == "gotowe"),
                                        rejected=sum(1 for t in tickets if t["state"] == "odrzucone"))
            return
        if st.running():
            return
        item, project = self._project(card)
        info = ticket_of(st)
        if not info["cardWorktree"]:
            raise FlowError("no-card-worktree", "brak worktree karty po zakończeniu ticketów")
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-executor", "katalog wykonawców jest niedostępny")
        try:
            selected = execution.resolve(executor, catalog, config=cfg, probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        step_id = self._next_step_id(card, "", st)
        out = info["cardWorktree"] + f".close-{step_id}.step/out"
        prompt = handoff.close_card(card_id=card, title=item.title, description=item.description,
                                    worktree=info["cardWorktree"], out_dir=out, tickets=tickets,
                                    manual=(st.started or {}).get("check") == "manual")
        self._launch(item, project, selected, "", {"branch": info["cardBranch"]},
                     {"branch": info["cardBranch"], "worktree": info["cardWorktree"]},
                     step_id, [], kind="close", prompt_text=prompt)
