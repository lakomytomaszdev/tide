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

from .. import codex_homes
from ..board.model import BoardError, card_num
from ..coordination import reservations
from ..tasks import agentcmd, launch, stepstop
from ..tasks.core import Plan, TaskFiles
from ..worktrees.naming import slug_from_prompt
from . import handoff
from . import execution
from .gitops import FlowGit, FlowGitError, worktree_for
from .journal import Journal
from .state import FlowState, derived, fold, ticket_of

GUARD_MINUTES = 15.0
TIMEOUT_MINUTES = 240.0
SETTINGS_FILE = "cardflow.json"
RESULTS = ("done", "question", "denied", "error")
OUTPUT_MAX = 256 * 1024
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
REQUEST_FIELDS = ("entry", "executor", "reviewer", "preferredProvider", "images", "check")


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


class FlowEngine:
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

    # --- odczyt (wątek główny) -------------------------------------------------------------------

    def has_journal(self, card_id: str) -> bool:
        return self.journal.has(card_id)

    def derived(self, card: dict) -> dict | None:
        return derived(fold(self.journal.read(card.get("id") or "")), card)

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

    def start(self, card_id: str, request: dict) -> dict:
        unknown = [k for k in request if k not in REQUEST_FIELDS]
        if unknown:
            raise FlowError("bad-request", f"nieznane pole „{unknown[0]}”; dozwolone: {', '.join(REQUEST_FIELDS)}")
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
        if project.youtrack:
            raise FlowError("unsupported-tracker", "„Od razu” w projekcie z YouTrackiem jeszcze nie działa "
                                                   "(zgłoszenia przy zleceniu — osobny ticket)")
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
        if st.active():
            raise FlowError("flow-active", f"{card.id}: krok tej karty jeszcze trwa")
        try:
            reservations.reserve(project.repo, count=1, title=card.title, dry_run=True)
        except reservations.ReservationError as exc:
            raise FlowError("no-ticket-number", f"numer ticketu: {exc}") from None
        main = self.git.main_branch(project.repo)
        if not main:
            raise FlowError("no-base", f"{project.repo}: nie ma gałęzi głównej (master, main, develop)")

        started = self.journal.append(card.id, "card.started", entry="now", executor=selected.name,
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
                                      moveId=card.move_id)
        launch = f"launch-{started['seq']}"
        request_key = f"{card.id}:{launch}"
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
        step = self._launch(card, project, selected, ticket, made, made_ticket, step_id, images)
        return {"ok": True, "id": card.id, "ticket": ticket, "branch": made_ticket["branch"],
                "worktree": made_ticket["worktree"], "cardBranch": made["branch"], "cardWorktree": made["worktree"],
                "step": step_id, "session": step["session"], "executor": selected.name}

    def _reserve(self, repo: str, title: str, request_key: str) -> dict:
        got = reservations.reserve(repo, count=1, title=title, request_key=request_key)
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

    def _launch(self, card, project, selected, ticket, made, made_ticket, step_id, images,
                *, kind="ticket", round_no=0, prompt_text="", review_tip="", parent_step="",
                attempt=0, resume_session="") -> dict:
        worktree = made_ticket["worktree"]
        step_dir = worktree + (f".resume-{step_id}.step" if parent_step else
                               ".step" if kind == "ticket" else
                               f".review-{round_no}.step" if kind == "review" else f".fix-{round_no}.step")
        out = os.path.join(step_dir, "out")
        shots = [os.path.join(step_dir, "in", "images", f"{i + 1}-{os.path.basename(p)}") for i, p in enumerate(images)]
        prompt = prompt_text or handoff.ticket_card(card_id=card.id, title=card.title, description=card.description,
                                                     project=project.name, ticket=ticket, branch=made_ticket["branch"],
                                                     card_branch=made["branch"], worktree=worktree, out_dir=out,
                                                     images=shots)
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
                tmp_root=self.tmp_root))
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

    def _act(self, card: str, action: str, key: str, work, **intent) -> dict:
        """Para „zamiar → wynik”: wynik już jest — zwracamy go (powtórka nic nie robi); błąd — wynik ok: false i wyjątek."""
        st = fold(self.journal.read(card))
        done = st.results.get((action, key))
        if done is not None:
            if not done.get("ok"):
                raise FlowError(action, done.get("error") or action)
            return done
        if (action, key) not in st.intents:
            self.journal.append(card, "intent", action=action, key=key, **intent)
        try:
            data = work()
        except (FlowGitError, reservations.ReservationError, agentcmd.StepError, OSError, FlowError) as exc:
            self.journal.append(card, "result", action=action, key=key, ok=False, error=str(exc))
            if isinstance(exc, (agentcmd.StepError, OSError)):
                raise
            raise FlowError(action, str(exc)) from None
        return self.journal.append(card, "result", action=action, key=key, ok=True, **(data or {}))

    # --- ręczne kolumny --------------------------------------------------------------------------

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
        ticket = ticket_of(st)["id"]
        merged = st.results.get(("merge", ticket))
        if ticket and not (merged and merged.get("ok")) and ticket not in st.rejected:
            self.journal.append(card, "ticket.rejected", ticket=ticket)
        self.journal.append(card, "card.finished", column=column)
        return True

    # --- co chwilę -------------------------------------------------------------------------------

    def tick(self) -> None:
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
            st = fold(self.journal.read(card))
            busy |= {s["worktree"] for s in st.running() if s.get("worktree")}
            busy |= {s["worktree"] for s in st.steps.values() if s.get("worktree") and
                     s.get("mode") == "interactive" and not s.get("consoleClosed")}
            if st.started is not None and not st.card_finished and ticket_of(st)["cardWorktree"]:
                busy.add(ticket_of(st)["cardWorktree"])
        self._hidden = (frozenset(hidden_ids - {""}), tuple(sorted(hidden_places)))
        self._busy = tuple(sorted(busy))

    def _tick_card(self, card: str, st: FlowState) -> None:
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
        for intent in st.open_intents():
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
        self._queue_notices(card)
        st = fold(self.journal.read(card))
        if not st.card_finished and not st.running() and not st.open_consoles():
            try:
                self._advance(card, st)
            except (FlowError, FlowGitError) as exc:
                problem = f"{exc.__class__.__name__}: {exc}"
                if st.flow_error != problem:
                    self.journal.append(card, "flow.error", error=problem)

    def _advance(self, card: str, st: FlowState) -> None:
        """Jedna zmiana etapu na tick: ticket → review → poprawki/review → scalenie i sprzątanie."""
        if not st.last_step or st.open_intents():
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
        ticket = step.get("ticket") or ""
        if not ticket or ticket in st.rejected:
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
        if ("merge", ticket) not in st.results:
            source = next((s for s in st.steps.values() if s.get("ticket") == ticket and
                           s.get("kind") == "ticket"), None)
            if source is not None:
                self._merge(card, st, source)
        merged = fold(self.journal.read(card)).results.get(("merge", ticket))
        if merged and merged.get("ok"):
            self._cleanup(card, fold(self.journal.read(card)), ticket, int(step.get("round") or 1))

    def _next_step_id(self, card: str, ticket: str, st: FlowState) -> str:
        return f"{card}-{ticket}-{1 + sum(1 for s in st.steps.values() if s.get('ticket') == ticket)}"

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
        step = st.steps.get(st.last_step) or {}
        done = st.finished.get(st.last_step) or {}
        result = done.get("result")
        valid = {"question": ("answer",), "denied": ("allow", "deny"),
                 "error": ("retry", "change-executor")}.get(result, ())
        if st.card_finished or st.running() or not step or choice not in valid or \
                (result == "error" and int(step.get("attempt") or 0) == 0):
            raise FlowError("no-step-question", f"{card}: ten krok nie czeka na taką decyzję")
        previous = st.step_decisions.get(st.last_step)
        if previous:
            if previous.get("choice") == choice and previous.get("answer", "") == str(request.get("answer") or "") and \
                    previous.get("executor", "") == str(request.get("executor") or ""):
                return {"ok": True, "id": card, "step": st.last_step, "choice": choice, "already": True}
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
        event = self.journal.append(card, "step.decision", step=st.last_step, ticket=step.get("ticket") or "",
                                    choice=choice, answer=answer if choice == "answer" else "",
                                    command=done.get("command") or "" if choice in ("allow", "deny") else "",
                                    executor=executor if choice == "change-executor" else "")
        return {"ok": True, "id": card, "step": st.last_step, "choice": choice, "seq": event["seq"]}

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
        info = ticket_of(st)
        choice = decision.get("choice") or ""
        name = decision.get("executor") if choice == "change-executor" else step.get("executor") or ""
        step_id = self._next_step_id(card, step.get("ticket") or info["id"], st)
        try:
            selected = self._resume_executor(card, st, step, name, changed=choice == "change-executor")
        except FlowError as exc:
            if exc.code != "local-unavailable" or choice == "change-executor":
                raise
            self.journal.append(card, "step.started", step=step_id, kind=step.get("kind") or "ticket",
                                ticket=step.get("ticket") or info["id"], round=step.get("round") or 0,
                                executor=name, program=step.get("program") or "", model=step.get("model") or "",
                                provider=step.get("provider") or "", local=True,
                                effort=step.get("effort") or "", routeUrl=step.get("routeUrl") or "",
                                routeKind=step.get("routeKind") or "",
                                routeTokenFile=step.get("routeTokenFile") or "",
                                parentStep=step.get("step") or "", attempt=attempt, session=step.get("session") or "",
                                worktree=step.get("worktree") or "", branch=step.get("branch") or "",
                                stepDir=step.get("stepDir") or "", reviewTip=step.get("reviewTip") or "")
            self.journal.append(card, "step.finished", step=step_id, ticket=step.get("ticket") or info["id"],
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
        new_out = info["worktree"] + f".resume-{step_id}.step/out"
        prompt = prompt.replace(old_out, new_out)
        prompt += handoff.resume_context(choice=choice, prior=st.finished.get(step.get("step")) or {},
                                         answer=decision.get("answer") or "", command=decision.get("command") or "",
                                         out_dir=new_out)
        session = "" if choice == "change-executor" else step.get("session") or ""
        if not session and choice != "change-executor" and step.get("program") == "codex":
            finder = getattr(execution, "codex_session_id", None)
            if finder:
                session = finder(os.path.join(old_out, "step.log"))
        self._launch(item, project, selected, step.get("ticket") or info["id"],
                     {"branch": info["cardBranch"]},
                     {"branch": info["branch"], "worktree": info["worktree"]},
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
        step = st.steps.get(st.last_step) or {}
        ticket, round_no = step.get("ticket") or "", int(step.get("round") or 0)
        verdict = st.verdicts.get((ticket, round_no)) or {}
        previous = st.review_decisions.get((ticket, round_no)) or {}
        if previous.get("choice") == choice == "reject" and not st.card_finished:
            if ticket not in st.rejected:
                self.journal.append(card, "ticket.rejected", ticket=ticket)
            return {"ok": True, "id": card, "ticket": ticket, "round": round_no, "choice": choice,
                    "already": True}
        if st.card_finished or st.running() or step.get("kind") != "review" or round_no < 3 or \
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
        info = ticket_of(st)
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("unknown-reviewer", f"{card}: katalog recenzentów jest niedostępny")
        previous = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == ticket and
                         s.get("kind") == "review"), {})
        try:
            reviewer = execution.resolve(previous.get("executor") or st.started.get("reviewer") or "", catalog, config=cfg,
                                         probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if reviewer.tool != (previous.get("program") or st.started.get("reviewerProgram")) or \
                reviewer.model != (previous.get("model") or st.started.get("reviewerModel")) or \
                reviewer.provider != (previous.get("provider") or st.started.get("reviewerProvider")) or \
                reviewer.effort != previous.get("effort", st.started.get("reviewerEffort", "")) or \
                (reviewer.route.url if reviewer.route else "") != previous.get("routeUrl", st.started.get("reviewerRouteUrl", "")) or \
                (reviewer.route.kind if reviewer.route else "") != previous.get("routeKind", st.started.get("reviewerRouteKind", "")) or \
                (reviewer.route.token_file if reviewer.route else "") != previous.get("routeTokenFile", st.started.get("reviewerRouteTokenFile", "")) or \
                reviewer.model.lower() == (source.get("model") or "").lower() or \
                reviewer.local != (source.get("provider") == "local") or \
                (st.started.get("preferredProvider") == "mixed" and
                 reviewer.provider == source.get("provider")) or \
                (st.started.get("preferredProvider") != "mixed" and
                 reviewer.provider != source.get("provider")):
            raise FlowError("changed-reviewer", f"{card}: katalog zmienił recenzenta od startu ticketu")
        path = info["worktree"]
        tip = self.git.clean_tip(project.repo, info["branch"], path)
        diff = self.git.diff(project.repo, info["cardBranch"], info["branch"])
        step_id = self._next_step_id(card, ticket, st)
        out = path + f".review-{round_no}.step/out"
        prompt = handoff.review_card(card_id=card, title=item.title, description=item.description, ticket=ticket,
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
        info = ticket_of(st)
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        if catalog is None:
            raise FlowError("no-executor", f"{card}: katalog wykonawców jest niedostępny")
        author = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == ticket and
                       s.get("kind") in ("ticket", "fix")), None)
        if author is None:
            raise FlowError("no-executor", f"{card}: nie ma kroku autora do poprawienia")
        try:
            executor = execution.resolve(author.get("executor") or "", catalog, config=cfg,
                                         probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if executor.tool != author.get("program") or executor.model != author.get("model") or \
                executor.provider != author.get("provider") or \
                executor.effort != author.get("effort", st.started.get("executorEffort", "")) or \
                (executor.route.url if executor.route else "") != author.get("routeUrl", st.started.get("executorRouteUrl", "")) or \
                (executor.route.kind if executor.route else "") != author.get("routeKind", st.started.get("executorRouteKind", "")) or \
                (executor.route.token_file if executor.route else "") != author.get("routeTokenFile", st.started.get("executorRouteTokenFile", "")):
            raise FlowError("changed-executor", f"{card}: katalog zmienił wykonawcę poprawek")
        round_no = int(review_step.get("round") or 1)
        step_id = self._next_step_id(card, ticket, st)
        out = info["worktree"] + f".fix-{round_no}.step/out"
        prompt = handoff.fix_card(card_id=card, title=item.title, description=item.description, ticket=ticket,
                                  round_no=round_no, branch=info["branch"], worktree=info["worktree"],
                                  out_dir=out, review=self._note(card, review_step))
        self._launch(item, project, executor, ticket,
                     {"branch": info["cardBranch"]},
                     {"branch": info["branch"], "worktree": info["worktree"]},
                     step_id, [], kind="fix", round_no=round_no, prompt_text=prompt)

    def _cleanup(self, card: str, st: FlowState, ticket: str, rounds: int) -> None:
        info = ticket_of(st)
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
        if action == "reserve":
            self._act(card, action, key,
                      lambda: self._reserve(intent["repo"], intent["title"], f"{card}:{key}"))
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

    def _guard(self, card: str, step: dict) -> None:
        name = step.get("step") or ""
        now = time.monotonic()
        limit = guard_seconds(self.settings)
        seen = self.watch.get(name)
        if seen is not None and now - seen[2] < min(self.guard_every, limit / 10.0):
            return
        mark = self._progress_mark(step)
        if seen is None or seen[0] != mark:
            self.watch[name] = (mark, now, now)
            return
        self.watch[name] = (mark, seen[1], now)
        if now - seen[1] >= limit:
            print(f"tide-daemon: przepływ karty {card}: krok {name} bez postępu od {int(now - seen[1])} s — zatrzymuję",
                  flush=True)
            stalled = datetime.fromtimestamp(self.clock() - (now - seen[1]), timezone.utc).isoformat(timespec="seconds")
            self._stop(card, step, "error", "no-progress", stalled_since=stalled)

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
        if verdict is not None:
            self.journal.append(card, "review.verdict", step=name, ticket=step.get("ticket") or "",
                                round=step.get("round") or 1, reviewer=step.get("executor") or "", **verdict)
        self._queue_notices(card)
        self.procs.pop(name, None)
        self.watch.pop(name, None)
        shutil.rmtree(os.path.join(self.tmp_root, f"tide-step-{name}"), ignore_errors=True)

    def _queue_notices(self, card: str) -> None:
        """Zdarzenie przed wysłaniem: po restarcie pytanie nie daje drugiego powiadomienia."""
        st = fold(self.journal.read(card))
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
                branch=step.get("branch") or info["branch"], into=info["cardBranch"], expectedTip=expected_tip)
        except FlowError as exc:
            print(f"tide-daemon: przepływ karty {card}: scalenie {ticket}: {exc}", flush=True)
