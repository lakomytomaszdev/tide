"""Stan karty liczony z dziennika (bez Qt i bez dysku): `fold(events)` i pole `derived` karty dla Tablicy.

Typy zdarzeń (schemat 1, PT-58):
- `card.started` — zlecenie: entry (now), executor, program, model, check,
- `intent` / `result` — para „zamiar → wynik” akcji ze skutkami poza dziennikiem: action (reserve, card-branch,
  ticket-branch, step-start, merge), key (ten sam w parze), w wyniku ok, error i dane akcji,
- `step.started` — krok w tle: step, kind (ticket), ticket, executor, unit, session, worktree, branch, stepDir, gitDir,
- `step.finished` — wynik kroku: result (done, question, denied, error, stopped), reason (no-result, bad-result,
  no-progress, exit, start), question, command, notes, wip (commit WIP przy zatrzymaniu),
- `card.moved` (to, from — kolumna, w której kartę widać), `card.paused`, `card.finished` (column),
  `ticket.rejected` (ticket).

Kolumna (ręczne kolumny wygrywają z dziennikiem):
- Gotowe i Archiwum w pliku karty — bez stanu z dziennika (karta stoi tam, gdzie ją postawiono),
- Backlog — „wstrzymana”, gdy kartę wstrzymano po zleceniu (inaczej bez stanu),
- Do zrobienia — z dziennika: krok trwa albo scalanie czeka → W toku; pytanie, brak zgody, błąd kroku albo nieudane
  scalenie → Czeka na mnie; ticket na gałęzi karty → Do sprawdzenia. Karta przeniesiona ręcznie do Do zrobienia po
  ostatnim wyniku (np. „do poprawy” z Do sprawdzenia) stoi w Do zrobienia bez stanu.
"""
from __future__ import annotations

from dataclasses import dataclass, field

RESULT_LABELS = {"done": "gotowe", "question": "pytanie", "denied": "brak zgody", "error": "błąd", "stopped": "zatrzymany"}
REASON_LABELS = {"no-result": "brak pliku wyniku", "bad-result": "zły plik wyniku", "no-progress": "bez postępu",
                 "exit": "program zakończył się błędem", "start": "krok nie wystartował",
                 "timeout": "przekroczony czas kroku", "local-unavailable": "serwer lokalny jest niedostępny",
                 "lost": "krok zniknął (restart usługi)", "bad-review": "niepoprawny werdykt review",
                 "review-changed-ticket": "recenzent zmienił gałąź albo pliki ticketu", "abandoned": "porzucony"}
NOTE_MAX = 160


@dataclass
class FlowState:
    started: dict | None = None                     # ostatnie card.started
    intents: dict = field(default_factory=dict)     # (action, key) → intent
    results: dict = field(default_factory=dict)     # (action, key) → result
    steps: dict = field(default_factory=dict)       # step → step.started
    finished: dict = field(default_factory=dict)    # step → step.finished
    verdicts: dict = field(default_factory=dict)    # (ticket, runda) → review.verdict
    review_questions: dict = field(default_factory=dict)
    review_decisions: dict = field(default_factory=dict)
    step_retries: dict = field(default_factory=dict)  # źródłowy krok → automatyczne ponowienie
    step_decisions: dict = field(default_factory=dict)  # źródłowy krok → odpowiedź użytkownika
    step_notices: set = field(default_factory=set)
    tracker_retries: dict = field(default_factory=dict)
    plan_checked: dict | None = None
    plan_proposed: dict | None = None
    approval_requested: dict | None = None
    plan_approved: dict | None = None
    plan_finished: bool = False
    plan_change_seq: int = 0
    plan_change_ts: str = ""
    assignments: dict = field(default_factory=dict)  # ticket → autor i recenzent z planu
    last_step: str = ""
    paused: bool = False
    card_finished: bool = False
    moved_to: str = ""                              # kolumna z ostatniego card.moved po ostatnim wyniku kroku
    store_column: str = ""                          # kolumna ostatnio potwierdzona w dzienniku
    store_move_id: str = ""                         # ostatni ruch MoveCard potwierdzony w dzienniku
    rejected: set = field(default_factory=set)
    flow_error: str = ""
    last_seq: int = 0

    def running(self) -> list[dict]:
        return [s for name, s in self.steps.items() if name not in self.finished]

    def open_consoles(self) -> list[dict]:
        """Karty Konsoli jeszcze otwarte, również po zapisaniu pliku wyniku."""
        return [s for s in self.steps.values() if s.get("mode") == "interactive" and not s.get("consoleClosed")]

    def open_intents(self, *, include_background=False) -> list[dict]:
        return [i for k, i in self.intents.items() if k not in self.results and
                (include_background or i.get('action') != 'youtrack-note')]

    def tracker_failures(self):
        return [e for e in self.results.values() if e.get('permanent') and not e.get('ok')]

    def merge_pending(self) -> list[dict]:
        """Tickety zatwierdzone w review, których jeszcze nie scalono."""
        out = []
        for (ticket, _round), verdict in self.verdicts.items():
            if verdict.get("verdict") == "APPROVE" and ("merge", ticket) not in self.results \
                    and ticket not in self.rejected:
                step = next((s for s in self.steps.values() if s.get("ticket") == ticket and
                             s.get("kind") == "ticket"), None)
                if step is not None:
                    out.append(step)
        return out

    def active(self) -> bool:
        """Krok trwa, scalanie czeka albo akcja zaczęta i niedokończona — nowe zlecenie nie startuje."""
        pending_review = any(done.get("result") == "done" and
                             (self.steps.get(name) or {}).get("kind") in ("ticket", "fix", "review") and
                             (self.steps.get(name) or {}).get("ticket") not in self.rejected and
                             ("merge", (self.steps.get(name) or {}).get("ticket")) not in self.results
                             for name, done in self.finished.items())
        plan_pending = self.plan_proposed is not None and not self.plan_finished
        step = self.steps.get(self.last_step) or {}
        finished = self.finished.get(self.last_step) or {}
        pending_decision = finished.get("result") in ("question", "denied", "error") and \
            step.get("ticket") not in self.rejected and not self.card_finished
        return bool(self.running() or self.open_consoles() or self.merge_pending() or self.open_intents()
                    or pending_review or pending_decision or plan_pending or
                    (self.started and self.started.get('trackerProject') and self.started.get('entry') == 'now'
                     and not self.steps and not self.card_finished))


def fold(events: list[dict]) -> FlowState:
    st = FlowState()
    for e in events:
        kind = e.get("type")
        st.last_seq = max(st.last_seq, int(e.get("seq") or 0))
        if kind == "card.started":
            st.started, st.paused, st.card_finished, st.moved_to = e, False, False, ""
            st.store_column = e.get("column") or ""
            st.store_move_id = e.get("moveId") or ""
            st.plan_checked = st.plan_proposed = st.plan_approved = st.approval_requested = None
            st.plan_finished = False
            st.plan_change_seq, st.plan_change_ts = 0, ""
            st.assignments = {}
        elif kind == "intent":
            st.intents[(e.get("action"), e.get("key"))] = e
        elif kind == 'youtrack.reset':
            st.results.pop((e.get('action'), e.get('key')), None)
            st.tracker_retries.pop((e.get('action'), e.get('key')), None)
        elif kind == 'youtrack.retry':
            st.tracker_retries[(e.get('action'), e.get('key'))] = e
        elif kind == "result":
            st.results[(e.get("action"), e.get("key"))] = e
            st.tracker_retries.pop((e.get('action'), e.get('key')), None)
        elif kind == "step.started":
            st.steps[e.get("step")] = e
            st.last_step = e.get("step") or ""
            st.moved_to = ""
            st.flow_error = ""
        elif kind == "step.entered" and e.get("step") in st.steps:
            st.steps[e["step"]] = {**st.steps[e["step"]], "mode": "interactive",
                                   "session": e.get("session") or "", "closeFile": e.get("closeFile") or "",
                                   "processFile": e.get("processFile") or "", "enteredAt": e.get("enteredAt") or 0,
                                   "consoleUnit": e.get("consoleUnit") or "",
                                   "source": e.get("source") or "", "taskFile": e.get("taskFile") or "",
                                   "consoleClosed": False}
        elif kind == "step.console_closed" and e.get("step") in st.steps:
            st.steps[e["step"]] = {**st.steps[e["step"]], "consoleClosed": True}
        elif kind == "step.finished":
            st.finished[e.get("step")] = e
            if st.moved_to not in ("done", "archived"):
                st.moved_to = ""
        elif kind == "review.verdict":
            st.verdicts[(e.get("ticket"), e.get("round"))] = e
        elif kind == "review.question":
            st.review_questions[(e.get("ticket"), e.get("round"))] = e
        elif kind == "review.decision":
            st.review_decisions[(e.get("ticket"), e.get("round"))] = e
        elif kind == "step.retry":
            st.step_retries[e.get("step")] = e
        elif kind == "step.decision":
            st.step_decisions[e.get("step")] = e
        elif kind == "step.notice":
            st.step_notices.add(e.get("step"))
        elif kind == "plan.checked":
            st.plan_checked = e
        elif kind == "plan.proposed":
            st.plan_proposed = e
        elif kind == 'plan.approval_requested':
            st.approval_requested = e
        elif kind == "plan.approved":
            st.plan_approved = e
        elif kind == "plan.finished":
            st.plan_finished = True
        elif kind == "ticket.assigned":
            st.assignments[e.get("ticket")] = e
        elif kind == "flow.error":
            st.flow_error = e.get("error") or ""
        elif kind == "card.paused":
            st.paused = True
        elif kind == "card.finished":
            st.card_finished = True
        elif kind == "card.moved":
            st.store_column = e.get("to") or ""
            st.store_move_id = e.get("moveId") or ""
            # „Do poprawy” to przeniesienie z kolumny wyliczonej (Do sprawdzenia, Czeka na mnie); z Backlogu do Do
            # zrobienia to wznowienie albo kolejka po zleceniu z popupu — karta pokazuje dalej stan z dziennika.
            if e.get("from") not in ("backlog", "todo"):
                st.moved_to = e.get("to") or ""
            if (e.get("to") or "") != "backlog":
                st.paused = False
        elif kind == "ticket.rejected":
            st.rejected.add(e.get("ticket"))
        if st.plan_approved is not None and (kind in (
                "plan.approved", "ticket.assigned", "step.started", "step.finished", "review.verdict",
                "review.question", "review.decision", "ticket.rejected") or
                (kind == "result" and e.get("action") == "merge")):
            st.plan_change_seq = int(e.get("seq") or 0)
            st.plan_change_ts = e.get("ts") or ""
    return st


def ticket_of(st: FlowState) -> dict:
    """Ostatni ticket karty: {id, title, branch, worktree, cardBranch, cardWorktree} z wyników akcji."""
    out = {"id": "", "title": "", "branch": "", "worktree": "", "cardBranch": "", "cardWorktree": ""}
    wanted = (st.steps.get(st.last_step) or {}).get("ticket") or ""
    for (action, _key), res in st.results.items():
        if not res.get("ok"):
            continue
        if action == "reserve" and (not wanted or res.get("ticket") == wanted):
            out["id"], out["title"] = res.get("ticket") or "", res.get("title") or ""
        elif action == "card-branch":
            out["cardBranch"], out["cardWorktree"] = res.get("branch") or "", res.get("worktree") or ""
        elif action == "ticket-branch" and res.get("ticket") == (wanted or out["id"]):
            out["branch"], out["worktree"] = res.get("branch") or "", res.get("worktree") or ""
    return out


def derived(st: FlowState, card: dict) -> dict | None:
    """Pole `derived` karty z dziennikiem (kształt jak board/derive.py, z `flow`) albo None — karta w swojej kolumnie."""
    if st.started is None:
        return None
    column = card.get("column") or "backlog"
    if column in ("done", "archived"):
        return None
    ticket = ticket_of(st)
    step = st.steps.get(st.last_step) or {}
    done = st.finished.get(st.last_step) or {}
    ident = step.get("ticket") or ticket["id"] or "ticket"
    executor = st.started.get("executor") or ""
    author = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == ident and
                   s.get("kind") in ("ticket", "fix")), {})
    reviewer = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == ident and
                     s.get("kind") == "review"), {})
    out = {"column": "", "done": 0, "total": 1, "tool": st.started.get("program") or "", "note": "", "nextStep": "",
           "check": st.started.get("check") or card.get("check") or "auto", "branch": ticket["cardBranch"], "session": "", "coordinator": "",
           "link": "flow", "agentState": "", "tickets": [], "question": "",
           "flow": {"ticket": ident, "step": st.last_step, "executor": step.get("executor") or executor,
                    "entry": st.started.get("entry") or "",
                    "result": done.get("result") or "", "reason": done.get("reason") or "",
                    "cardBranch": ticket["cardBranch"], "branch": ticket["branch"] or step.get("branch") or "",
                    "provider": step.get("provider") or st.started.get("provider") or "",
                    "stepKind": step.get("kind") or "",
                    "preferredProvider": st.started.get("preferredProvider") or "",
                    "authorProvider": author.get("provider") or st.started.get("provider") or "",
                    "authorModel": author.get("model") or st.started.get("model") or "",
                    "reviewerProvider": reviewer.get("provider") or st.started.get("reviewerProvider") or "",
                    "reviewerModel": reviewer.get("model") or st.started.get("reviewerModel") or ""}}
    out['flow']['trackerQueue'] = [
        {key: e.get(key) for key in ('action', 'key', 'after', 'error')} for e in st.tracker_retries.values()]
    tracker_reason = next(reversed(list(st.tracker_retries.values())), {}).get('error') or ''
    out["flow"]["steps"] = [
        {"id": name, "ticket": item.get("ticket") or "", "mode": item.get("mode") or "background",
         "result": (st.finished.get(name) or {}).get("result") or "",
         "reason": (st.finished.get(name) or {}).get("reason") or "",
         "canEnter": not any(other.get("worktree") == item.get("worktree") and other.get("step") != name
                             for other in st.running()) and
                     not any(other.get("worktree") == item.get("worktree") and other.get("mode") == "interactive"
                             and not other.get("consoleClosed") for other in st.steps.values())}
        for name, item in st.steps.items()
    ]
    failures = st.tracker_failures()
    if failures and not st.running():
        error = failures[-1].get('error') or 'odmowa operacji YouTracka'
        out['flow'].update(action='error', error=error, trackerError=True)
        return _fill(out, 'waiting', 'Błąd YouTracka: ' + error + ' — popraw przyczynę i wybierz „Ponów”')
    if st.started.get("entry") == "plan":
        proposal = st.plan_proposed or {}
        approved = st.plan_approved or {}
        rows = approved.get("rows") or proposal.get("rows") or []
        waves = approved.get("waves") or proposal.get("waves") or []
        finished_ids = {row.get("id") for row in rows if ("merge", row.get("id")) in st.results and
                        st.results[("merge", row.get("id"))].get("ok")}
        out["total"], out["done"] = len(rows), len(finished_ids)
        out["flow"]["plan"] = {"status": "done" if st.plan_finished else
                             "approved" if st.plan_approved else "pending" if st.plan_proposed else "invalid",
                             "rows": rows, "waves": waves,
                             "errors": (st.plan_checked or {}).get("errors") or []}
        if not st.plan_proposed:
            return _fill(out, "waiting", "propozycja planu wymaga poprawy")
        if not st.plan_approved and st.approval_requested and st.started.get('trackerProject'):
            return _fill(out, 'backlog' if st.paused else 'progress',
                         tracker_reason or 'YouTrack: zatwierdzenie planu czeka na zgłoszenia')
        if not st.plan_approved:
            out["question"] = "Zatwierdzić plan?"
            return _fill(out, "waiting", "plan do zatwierdzenia")
        if st.plan_finished:
            return _fill(out, "review", f"✓ plan karty: {len(finished_ids)}/{len(rows)} ticketów scalonych")
        if not st.last_step and not st.open_intents():
            return _fill(out, "progress", "uruchamiam pierwszy ticket planu")
    if st.started.get('trackerProject') and st.started.get('entry') == 'now' and not st.steps:
        return _fill(out, 'backlog' if st.paused else 'progress',
                     tracker_reason or 'YouTrack: zlecenie czeka na zgłoszenie')
    verdict = next((v for (ticket_id, _round), v in reversed(list(st.verdicts.items())) if ticket_id == ident), None)
    if verdict:
        out["flow"].update({key: verdict[key] for key in ("verdict", "blockers", "majors", "nits", "round")})
        question_key = (ident, verdict.get("round"))
        if question_key in st.review_questions and question_key not in st.review_decisions:
            out["flow"]["reviewQuestion"] = True
    if column == "backlog":
        if not st.paused:
            return None
        running = " — trwający krok kończy się" if st.running() else ""
        return _fill(out, "backlog", f"wstrzymana: {ident}{running}")
    if st.moved_to == "todo" and not st.running():
        return None
    failed = [r for (a, _k), r in st.results.items() if not r.get("ok") and a != "merge"]
    if st.running():
        work = ("krok w Konsoli" if step.get("mode") == "interactive" else
                "review" if step.get("kind") == "review" else "poprawki" if step.get("kind") == "fix" else "krok w tle")
        return _fill(out, "progress", f"{ident} · {work} ({step.get('executor') or executor})")
    if st.open_intents():
        return _fill(out, 'progress', tracker_reason or f"{ident} · {_intent_word(st.open_intents()[0])}")
    if failed and not done:
        return _fill(out, "waiting", f"błąd zlecenia: {failed[-1].get('error') or failed[-1].get('action')}")
    if st.flow_error:
        return _fill(out, "waiting", f"{ident}: {st.flow_error}")
    result = done.get("result") or ""
    if result == "done":
        if step.get("mode") == "interactive" and not step.get("consoleClosed"):
            return _fill(out, "progress", f"{ident}: wynik zapisany — zamknij Konsolę przed następnym krokiem")
        if step.get("kind") == "interactive":
            return _fill(out, "waiting", f"{ident}: rozmowa po zakończeniu — potrzebne nowe review lub decyzja")
        if verdict and verdict.get("verdict") == "REQUEST_CHANGES":
            if int(verdict.get("round") or 0) >= 3:
                decision = st.review_decisions.get((ident, verdict.get("round"))) or {}
                if decision.get("choice") == "retry":
                    return _fill(out, "progress", f"{ident} · poprawki po decyzji użytkownika")
                if decision.get("choice") == "reject":
                    return _fill(out, "waiting", f"{ident}: ticket odrzucony po review")
                out["question"] = (st.review_questions.get((ident, verdict.get("round"))) or {}).get("question") or ""
                return _fill(out, "waiting", f"{ident}: po {verdict.get('round')} rundach review — potrzebna decyzja")
            return _fill(out, "progress", f"{ident} · poprawki po review")
        merge = st.results.get(("merge", ident))
        if merge is None:
            return _fill(out, "progress", f"{ident} · review" if not verdict else f"{ident} · scalanie do gałęzi karty")
        if not merge.get("ok"):
            return _fill(out, "waiting", f"{ident}: nie scalono do gałęzi karty ({merge.get('error') or 'błąd'})")
        failed_cleanup = [r for (action, key), r in st.results.items()
                          if action == "cleanup" and (key == ident or str(key).startswith(ident + ":"))
                          and not r.get("ok")]
        if failed_cleanup:
            return _fill(out, "waiting", f"{ident}: nie usunięto worktree ({failed_cleanup[-1].get('error') or 'błąd'})")
        if st.started.get("entry") != "plan":
            out["done"] = 1
        return _fill(out, "review", f"✓ {ident} na gałęzi karty — review APPROVE, do sprawdzenia")
    if result == "question" or (result == "stopped" and done.get("reason") == "interactive-closed"):
        if result == "question" and st.last_step in st.step_decisions:
            return _fill(out, "progress", f"{ident} · wznawiam po odpowiedzi")
        if result == "question":
            out["flow"].update({"action": "answer", "question": done.get("question") or ""})
        out["question"] = _clip(done.get("question") or "", 120)
        return _fill(out, "waiting", f"{ident} pyta: „{out['question']}”" if out["question"] else f"{ident} pyta")
    if result == "denied":
        if st.last_step in st.step_decisions:
            return _fill(out, "progress", f"{ident} · wznawiam po decyzji o poleceniu")
        out["flow"].update({"action": "permission", "command": done.get("command") or "",
                            "canAllow": bool(done.get("canAllow", True))})
        return _fill(out, "waiting", f"{ident}: brak zgody na „{done.get('command') or '?'}”")
    if result in ("error", "stopped"):
        reason = REASON_LABELS.get(done.get("reason") or "", done.get("reason") or "")
        if result == "error":
            if int(step.get("attempt") or 0) == 0 and st.last_step not in st.step_decisions:
                return _fill(out, "progress", f"{ident} · ponawiam krok po błędzie")
            if st.last_step in st.step_decisions:
                return _fill(out, "progress", f"{ident} · wznawiam po decyzji użytkownika")
            out["flow"].update({"action": "error", "error": reason,
                                "stalledSince": done.get("stalledSince") or ""})
        since = f" od {done['stalledSince']}" if done.get("stalledSince") else ""
        return _fill(out, "waiting", f"błąd kroku {ident}: {reason}{since}" if reason else f"błąd kroku {ident}")
    return _fill(out, "progress", f"{ident} · zlecenie")


def _intent_word(intent: dict) -> str:
    return {"reserve": "rezerwacja numeru", "card-branch": "gałąź karty", "ticket-branch": "gałąź ticketu",
            "step-start": "start kroku", "merge": "scalanie do gałęzi karty"}.get(intent.get("action"), "zlecenie")


def _fill(out: dict, column: str, note: str) -> dict:
    out["column"] = column
    out["note"] = _clip(note, NOTE_MAX)
    if column == "waiting":
        out["nextStep"] = "odpowiedz w panelu karty" if out["question"] else "sprawdź krok karty"
    return out


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
