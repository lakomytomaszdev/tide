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
import tempfile
import time
import uuid

from ..board.model import BoardError, card_num
from ..coordination import reservations
from ..tasks import agentcmd, stepstop
from ..worktrees.naming import slug_from_prompt
from . import handoff
from .gitops import FlowGit, FlowGitError, worktree_for
from .journal import Journal
from .state import FlowState, derived, fold, ticket_of

GUARD_MINUTES = 15.0
SETTINGS_FILE = "cardflow.json"
RESULTS = ("done", "question", "denied", "error")
OUTPUT_MAX = 256 * 1024
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
REQUEST_FIELDS = ("entry", "executor", "images", "check")


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


class FlowEngine:
    def __init__(self, store, journal: Journal | None = None, *, claude_projects: str = "",
                 systemd_run: str = "", catalog=None, tmp_root: str | None = None, settings: str | None = None,
                 flow_git: FlowGit | None = None, clock=time.time, which=shutil.which, grace: float = 10.0,
                 guard_every: float = 30.0):
        self.store = store
        self.journal = journal or Journal(store.root)
        self.claude_projects = claude_projects
        self.systemd_run = systemd_run
        self.catalog = catalog            # funkcja() → models.executors.Catalog
        self.tmp_root = tmp_root or tempfile.gettempdir()
        self.settings = settings or settings_path()
        self.git = flow_git or FlowGit()
        self.clock = clock
        self.which = which
        self.grace = grace
        self.guard_every = guard_every
        self.procs: dict[str, object] = {}         # krok → Popen (tylko kroki uruchomione przez tę usługę)
        self.watch: dict[str, tuple] = {}          # krok → (odcisk postępu, od kiedy bez zmian, ostatnie sprawdzenie)
        self._warnings: dict[str, str] = {}
        self._hidden: tuple = (frozenset(), ())

    # --- odczyt (wątek główny) -------------------------------------------------------------------

    def has_journal(self, card_id: str) -> bool:
        return self.journal.has(card_id)

    def derived(self, card: dict) -> dict | None:
        return derived(fold(self.journal.read(card.get("id") or "")), card)

    def warnings(self) -> list[dict]:
        return [{"path": self.journal.path(card), "message": text} for card, text in sorted(self._warnings.items())]

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
        project = board.projects.get(card.project)
        if project is None or not project.repo:
            raise FlowError("no-project", f"{card.id}: karta potrzebuje projektu z repozytorium")
        if project.repo_missing or not os.path.isdir(project.repo):
            raise FlowError("no-repo", f"nie ma repozytorium {project.repo}")
        if project.youtrack:
            raise FlowError("unsupported-tracker", "„Od razu” w projekcie z YouTrackiem jeszcze nie działa "
                                                   "(zgłoszenia przy zleceniu — osobny ticket)")
        name = str(request.get("executor") or "").strip()
        catalog = self.catalog() if self.catalog else None
        executor = catalog.get(name) if catalog is not None else None
        if executor is None:
            raise FlowError("unknown-executor", f"nie ma wykonawcy „{name}” w katalogu wykonawców")
        if executor.program != "claude" or executor.provider != "anthropic" or not executor.model:
            raise FlowError("unsupported-executor", f"„{name}”: krok w tle umie na razie tylko Claude w chmurze "
                                                    "(Codex i modele lokalne — osobny ticket)")
        binary = self.which("claude")
        if not binary:
            raise FlowError("no-program", "nie znaleziono programu claude")
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

        started = self.journal.append(card.id, "card.started", entry="now", executor=executor.name,
                                      program="claude", model=executor.model, check=card.check,
                                      repo=project.repo, project=project.slug)
        launch = f"launch-{started['seq']}"
        reserved = self._act(card.id, "reserve", launch, lambda: self._reserve(project.repo, card.title))
        ticket = reserved["ticket"]
        slug = slug_from_prompt(card.title, words=4, length=32)
        card_branch = f"card/{card.id.lower()}-{slug}"
        made = self._act(card.id, "card-branch", card_branch,
                         lambda: self.git.branch_worktree(project.repo, card_branch, worktree_for(project.repo, card_branch),
                                                          main))
        ticket_branch = f"ticket/{ticket.lower()}-{slug}"
        made_ticket = self._act(card.id, "ticket-branch", ticket, lambda: {
            **self.git.branch_worktree(project.repo, ticket_branch, worktree_for(project.repo, ticket_branch),
                                       f"refs/heads/{card_branch}"), "ticket": ticket})
        step_id = f"{card.id}-{ticket}-{1 + sum(1 for s in st.steps.values() if s.get('ticket') == ticket)}"
        step = self._launch(card, project, executor, binary, ticket, made, made_ticket, step_id, images)
        return {"ok": True, "id": card.id, "ticket": ticket, "branch": made_ticket["branch"],
                "worktree": made_ticket["worktree"], "cardBranch": made["branch"], "cardWorktree": made["worktree"],
                "step": step_id, "session": step["session"], "executor": executor.name}

    def _reserve(self, repo: str, title: str) -> dict:
        got = reservations.reserve(repo, count=1, title=title)
        return {"ticket": got["ids"][0], "title": title}

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

    def _launch(self, card, project, executor, binary, ticket, made, made_ticket, step_id, images) -> dict:
        worktree = made_ticket["worktree"]
        step_dir = worktree + ".step"
        out = os.path.join(step_dir, "out")
        shots = [os.path.join(step_dir, "in", "images", f"{i + 1}-{os.path.basename(p)}") for i, p in enumerate(images)]
        prompt = handoff.ticket_card(card_id=card.id, title=card.title, description=card.description,
                                     project=project.name, ticket=ticket, branch=made_ticket["branch"],
                                     card_branch=made["branch"], worktree=worktree, out_dir=out, images=shots)
        session = str(uuid.uuid4())
        ids, places = self._hidden    # ukryta, zanim program zapisze pierwszą linię dziennika sesji
        self._hidden = (ids | {session}, tuple(sorted(set(places) | {os.path.realpath(worktree)})))

        def launch() -> dict:
            bg = agentcmd.background(agentcmd.Step(
                tool="claude", binary=binary, prompt=prompt, worktree=worktree, repo=project.repo, step_dir=step_dir,
                step_id=step_id, session_id=session, model=executor.model, permission_mode="acceptEdits",
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
                              session=session, stepDir=step_dir, worktree=worktree)
        except (agentcmd.StepError, OSError) as exc:
            raise FlowError("step", f"krok nie wystartował: {exc}") from None
        return self.journal.append(card.id, "step.started", step=step_id, kind="ticket", ticket=ticket,
                                   executor=executor.name, program="claude", model=executor.model,
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

    def moved(self, card_id: str, column: str) -> None:
        """Karta przeniesiona ręcznie (MoveCard): Backlog wstrzymuje, Gotowe i Archiwum kończą (krok zatrzymany
        z zapisem zmian, niescalony ticket odrzucony). Karta bez dziennika — nic."""
        if not self.journal.has(card_id):
            return
        st = fold(self.journal.read(card_id))
        if st.started is None or st.card_finished:
            return
        self.journal.append(card_id, "card.moved", to=column)
        if column == "backlog" and not st.paused:
            self.journal.append(card_id, "card.paused")
        if column in ("done", "archived"):
            for step in st.running():
                self._stop(card_id, step, "stopped", "")
            st = fold(self.journal.read(card_id))
            ticket = ticket_of(st)["id"]
            merged = st.results.get(("merge", ticket))
            if ticket and not (merged and merged.get("ok")) and ticket not in st.rejected:
                self.journal.append(card_id, "ticket.rejected", ticket=ticket)
            self.journal.append(card_id, "card.finished", column=column)

    # --- co chwilę -------------------------------------------------------------------------------

    def tick(self) -> None:
        hidden_ids, hidden_places = set(), set()
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
        self._hidden = (frozenset(hidden_ids - {""}), tuple(sorted(hidden_places)))

    def _tick_card(self, card: str, st: FlowState) -> None:
        for intent in st.open_intents():
            self._reconcile(card, intent)
        st = fold(self.journal.read(card))
        for step in st.running():
            self._poll(card, step)
        st = fold(self.journal.read(card))
        if not st.card_finished:
            for step in st.merge_pending():
                self._merge(card, st, step)

    def _reconcile(self, card: str, intent: dict) -> None:
        """Akcja zaczęta przed restartem usługi, bez wyniku: dokończenie albo jawny błąd (nigdy druga kopia)."""
        action, key = intent.get("action"), intent.get("key")
        if action == "merge":
            st = fold(self.journal.read(card))
            step = next((s for s in st.steps.values() if s.get("ticket") == key), None)
            if step is not None:
                self._merge(card, st, step)
            return
        if action == "step-start":
            unit = intent.get("unit") or f"tide-step-{key}"
            result = os.path.join(intent.get("stepDir") or "", "out", handoff.RESULT_FILE)
            if stepstop.unit_state(unit) == "active" or os.path.isfile(result):
                git_dir = ""
                try:
                    git_dir = agentcmd.resolve_git_dir(self._repo(card), intent.get("worktree") or "")
                except agentcmd.StepError:
                    pass
                self.journal.append(card, "result", action=action, key=key, ok=True, unit=unit, gitDir=git_dir)
                st = fold(self.journal.read(card))
                self.journal.append(card, "step.started", step=key, kind="ticket", ticket=ticket_of(st)["id"],
                                    executor=(st.started or {}).get("executor") or "", program="claude",
                                    unit=unit, session=intent.get("session") or "", worktree=intent.get("worktree") or "",
                                    branch=ticket_of(st)["branch"], stepDir=intent.get("stepDir") or "",
                                    gitDir=git_dir, repo=self._repo(card))
                return
        self.journal.append(card, "result", action=action, key=key, ok=False,
                            error="przerwane restartem usługi przed końcem akcji")

    def _repo(self, card: str) -> str:
        st = fold(self.journal.read(card))
        return (st.started or {}).get("repo") or ""

    def _poll(self, card: str, step: dict) -> None:
        name = step.get("step") or ""
        proc = self.procs.get(name)
        if proc is not None and proc.poll() is not None:
            alive = False
        else:
            state = stepstop.unit_state(step.get("unit") or "")
            if state == "unknown":
                return                       # bez menedżera nie wiemy — nie zgadujemy końca kroku
            alive = state == "active"
        if not alive:
            self._finish(card, step, exit_code=getattr(proc, "returncode", None))
            return
        self._guard(card, step)

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
            self._stop(card, step, "error", "no-progress")

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
        log = stepstop.find_claude_log(self.claude_projects, step.get("session") or "")
        try:
            log_st = os.stat(log) if log else None
        except OSError:
            log_st = None
        return count, size, newest, (log_st.st_size, log_st.st_mtime_ns) if log_st else None

    def _stop(self, card: str, step: dict, result: str, reason: str) -> None:
        stopped = stepstop.stop_step(self.procs.get(step.get("step") or ""), unit=step.get("unit") or "",
                                     worktree=step.get("worktree") or "", step_dir=step.get("stepDir") or "",
                                     repo=step.get("repo") or self._repo(card), git_dir=step.get("gitDir") or "",
                                     branch=step.get("branch") or "", tool="claude",
                                     session_id=step.get("session") or "", claude_projects=self.claude_projects,
                                     grace=self.grace)
        wip = {"stopped": stopped.stopped, "committed": stopped.committed, "commit": stopped.commit,
               "files": stopped.files, "error": stopped.error, "notes": stopped.notes}
        if not stopped.stopped:
            print(f"tide-daemon: przepływ karty {card}: kroku {step.get('step')} nie dało się zatrzymać", flush=True)
            return                               # następne sprawdzenie spróbuje znowu; worktree nietknięty
        self._finish(card, step, forced={"result": result, "reason": reason}, wip=wip,
                     messages=stopped.messages, exit_code=stopped.exit_code)

    def _finish(self, card: str, step: dict, forced: dict | None = None, wip: dict | None = None,
                messages=None, exit_code=None) -> None:
        name = step.get("step") or ""
        if name in fold(self.journal.read(card)).finished:
            return
        out = os.path.join(step.get("stepDir") or "", "out")
        data = forced or self._result(os.path.join(out, handoff.RESULT_FILE))
        kept = []
        for entry in sorted(os.listdir(out)) if os.path.isdir(out) else []:
            path = os.path.join(out, entry)
            if (entry == handoff.RESULT_FILE or entry.endswith(".md")) and os.path.isfile(path) \
                    and not os.path.islink(path) and os.path.getsize(path) <= OUTPUT_MAX:
                with open(path, "rb") as fh:
                    self.journal.write_file(card, os.path.join("steps", name, entry), fh.read())
                kept.append(entry)
        extra = {k: v for k, v in (("wip", wip), ("messages", messages)) if v}
        self.journal.append(card, "step.finished", step=name, ticket=step.get("ticket") or "",
                            result=data.get("result"), reason=data.get("reason") or "",
                            question=data.get("question") or "", command=data.get("command") or "",
                            notes=[n for n in data.get("notes") or [] if n in kept], kept=kept,
                            exit=exit_code, **extra)
        self.procs.pop(name, None)
        self.watch.pop(name, None)
        shutil.rmtree(os.path.join(self.tmp_root, f"tide-step-{name}"), ignore_errors=True)

    @staticmethod
    def _result(path: str) -> dict:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {"result": "error", "reason": "no-result"}
        except (OSError, ValueError):
            return {"result": "error", "reason": "bad-result"}
        if not isinstance(data, dict) or data.get("result") not in RESULTS:
            return {"result": "error", "reason": "bad-result"}
        notes = data.get("notes") if isinstance(data.get("notes"), list) else []
        return {"result": data["result"], "reason": str(data.get("reason") or "")[:300],
                "question": str(data.get("question") or "")[:2000], "command": str(data.get("command") or "")[:300],
                "notes": [str(n) for n in notes if isinstance(n, str)][:20]}

    def _merge(self, card: str, st: FlowState, step: dict) -> None:
        ticket = step.get("ticket") or ""
        info = ticket_of(st)
        repo = step.get("repo") or self._repo(card)
        try:
            self._act(card, "merge", ticket, lambda: self.git.merge(
                repo, info["cardBranch"], step.get("branch") or info["branch"], info["cardWorktree"],
                f"Merge ticket {ticket} into card {card}"), branch=step.get("branch") or info["branch"],
                into=info["cardBranch"])
        except FlowError as exc:
            print(f"tide-daemon: przepływ karty {card}: scalenie {ticket}: {exc}", flush=True)
