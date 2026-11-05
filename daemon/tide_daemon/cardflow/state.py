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

from dataclasses import dataclass, field, replace
from datetime import datetime

from .faults import cause_label, of_finished
from .history import LOGGED as HISTORY_LOGGED
from .findings import review_view as plan_review_view
from .labels import result_label, tide_fault_text, waiting_text

REASON_LABELS = {"no-result": "brak pliku wyniku", "bad-result": "zły plik wyniku", "no-progress": "bez postępu",
                 "exit": "program zakończył się błędem", "start": "krok nie wystartował",
                 "login": "wygasło logowanie agenta — zaloguj się (np. `claude /login` w terminalu) i kliknij Ponów",
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
    outside_started: dict = field(default_factory=dict)  # krok → outside.started (polecenie poza piaskownicą)
    superseded: set = field(default_factory=set)      # kroki sprzed ostatniego card.started — nie czekają na decyzję
    wip_retries: dict = field(default_factory=dict)    # krok → ponowiony bezpieczny commit po błędzie
    limits: dict = field(default_factory=dict)         # krok → odczyt limitu w chwili wyczerpania
    step_notices: set = field(default_factory=set)
    flow_notices: set = field(default_factory=set)
    activity: dict = field(default_factory=dict)      # krok → ostatnia zmiana plików lub dziennika sesji
    last_activity_ts: float = 0.0
    stalled_since: str = ""
    tracker_retries: dict = field(default_factory=dict)
    plan_checked: dict | None = None
    imported: dict | None = None
    imported_done: set = field(default_factory=set)
    plan_proposed: dict | None = None
    approval_requested: dict | None = None
    plan_approved: dict | None = None
    plan_reviewed: dict | None = None
    plan_reviewer: str = ""  # ręczny wybór modelu do niezależnego review planu
    plan_review_wait: dict | None = None  # blokada limitu pozostaje po restarcie i próbie review
    plan_revision: dict | None = None
    plan_finished: bool = False
    plan_change_seq: int = 0
    plan_change_ts: str = ""
    assignments: dict = field(default_factory=dict)  # ticket → autor i recenzent z planu
    reviewer_overrides: dict = field(default_factory=dict)  # ticket → recenzent po ręcznym transferze
    transfer_requests: dict = field(default_factory=dict)  # krok → trwała intencja przekazania
    inputs: dict = field(default_factory=dict)
    waiting: dict = field(default_factory=dict)
    timeline: list = field(default_factory=list)
    log: list = field(default_factory=list)            # zdarzenia do historii pełnej karty (history.LOGGED)
    last_step: str = ""
    paused: bool = False
    card_finished: bool = False
    closed: dict | None = None
    main_merged: dict | None = None
    moved_to: str = ""                              # kolumna z ostatniego card.moved po ostatnim wyniku kroku
    store_column: str = ""                          # kolumna ostatnio potwierdzona w dzienniku
    store_move_id: str = ""                         # ostatni ruch MoveCard potwierdzony w dzienniku
    rejected: set = field(default_factory=set)
    flow_error: str = ""
    last_seq: int = 0
    # Opiekun karty (PT-143): kroki `caretaker` nie są krokami ticketów — osobno od `steps`, żeby żaden wyzwalacz,
    # „ostatni krok ticketu” ani decyzja ich nie widziały (bez rekursji). Ich `step.finished` trafia do `finished`.
    caretakers: dict = field(default_factory=dict)            # krok opiekuna → step.started (kind caretaker)
    caretaker_runs: dict = field(default_factory=dict)        # krok opiekuna → caretaker.started
    caretaker_outcomes: dict = field(default_factory=dict)    # krok opiekuna → caretaker.escalated / caretaker.action
    caretaker_escalations: dict = field(default_factory=dict)  # id problemu → ostatnie caretaker.escalated
    caretaking: dict = field(default_factory=dict)            # krok problemu → krok opiekuna, który nad nim pracuje
    # PT-150: eskalacja po wyczerpaniu limitu działań nie jest wynikiem żadnego kroku opiekuna (nie ma czwartego).
    caretaker_limits: dict = field(default_factory=dict)      # id problemu → ostatnie caretaker.escalated z limit
    # Strażnik kroku opiekuna (PT-158): ostatni stan bazowy `caretaker.guard` i powody wykrytych po drodze zmian.
    caretaker_guards: dict = field(default_factory=dict)      # krok opiekuna → ostatnie caretaker.guard
    caretaker_wrote: dict = field(default_factory=dict)       # krok opiekuna → [powód zmiany]
    # Nie z dziennika (PT-151): ustawienia opiekuna przy tej karcie (`caretaker.setup`), które silnik dokłada przed
    # widokami (`derived`); pusty — wartości domyślne bez katalogu wykonawców.
    caretaker_setup: dict = field(default_factory=dict)

    def running(self) -> list[dict]:
        return [s for name, s in self.steps.items() if name not in self.finished] + \
            [s for name, s in self.caretakers.items() if name not in self.finished]

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
        return bool(self.busy_reason())

    def busy_reason(self) -> str:
        """Dlaczego nowe zlecenie nie wystartuje; pusty napis — może wystartować.

        Karta przeniesiona ręcznie do Do zrobienia po ostatnim wyniku stoi na Tablicy bez stanu (`derived`), więc
        stare pytanie, błąd, review albo plan nie blokują nowego zlecenia — blokuje tylko to, co naprawdę trwa."""
        if self.running():
            return "krok tej karty jeszcze trwa"
        if self.open_consoles():
            return "Konsola kroku tej karty jest jeszcze otwarta"
        if self.merge_pending():
            return "zatwierdzony ticket czeka na scalenie"
        if self.open_intents():
            return "akcja karty jest zaczęta i niedokończona"
        if self.moved_to == "todo":
            return ""
        pending_review = any(done.get("result") == "done" and
                             (self.steps.get(name) or {}).get("kind") in ("ticket", "fix", "review") and
                             (self.steps.get(name) or {}).get("ticket") not in self.rejected and
                             ("merge", (self.steps.get(name) or {}).get("ticket")) not in self.results
                             and name not in self.superseded
                             for name, done in self.finished.items())
        plan_pending = self.plan_proposed is not None and not self.plan_finished
        step = self.steps.get(self.last_step) or {}
        finished = self.finished.get(self.last_step) or {}
        pending_decision = finished.get("result") in ("question", "denied", "error") and \
            step.get("ticket") not in self.rejected and not self.card_finished and \
            self.last_step not in self.superseded
        pending_transfer = not self.card_finished and any(
            name not in self.step_decisions for name in self.transfer_requests)
        if pending_decision:
            label = result_label(finished["result"])
            return f"karta czeka na Twoją decyzję ({label}) — odpowiedz w panelu karty albo przenieś ją do Do zrobienia"
        if pending_review:
            return "ticket czeka na review albo scalenie"
        if plan_pending:
            return "plan czeka na zatwierdzenie"
        if pending_transfer:
            return "przekazanie kroku czeka na decyzję"
        if self.waiting and not self.card_finished:
            return "karta czeka na limit albo bloker"
        if (self.started and self.started.get('trackerProject') and self.started.get('entry') == 'now'
                and not self.steps and not self.card_finished):
            return "zlecenie z YouTracka jeszcze się zaczyna"
        return ""


def fold(events: list[dict]) -> FlowState:
    st = FlowState()
    for e in events:
        kind = e.get("type")
        if kind != "card.stalled":
            st.last_activity_ts = _epoch(e.get("ts")) or st.last_activity_ts
            st.stalled_since = ""
        if kind in ("card.started", "card.stalled", "step.started", "step.finished", "step.waiting", "review.verdict",
                    "step.decision", "step.transfer_requested", "step.wip", "step.limit",
                    "plan.approved", "card.paused", "card.resumed", "plan.finished") or (
                    kind == "result" and e.get("action") == "merge"):
            st.timeline.append({key: e[key] for key in (
                "seq", "ts", "type", "step", "ticket", "executor", "kind", "result", "reason", "verdict",
                "action", "key", "ok") if key in e})
        if kind in HISTORY_LOGGED:
            st.log.append(e)
        st.last_seq = max(st.last_seq, int(e.get("seq") or 0))
        if kind == "card.started":
            # Nowe zlecenie startuje tylko, gdy nic nie trwa (`busy_reason`), więc pytania, błędy i review
            # poprzedniego przepływu (np. porzuconego przeniesieniem do Do zrobienia) już na nikogo nie czekają.
            st.superseded.update(st.steps)
            st.started, st.paused, st.card_finished, st.moved_to = e, False, False, ""
            st.closed = st.main_merged = None
            st.store_column = e.get("column") or ""
            st.store_move_id = e.get("moveId") or ""
            st.plan_checked = st.plan_proposed = st.plan_approved = st.approval_requested = None
            st.imported = None
            st.imported_done.clear()
            st.plan_reviewed = st.plan_revision = None
            st.plan_reviewer = ""
            st.plan_review_wait = None
            st.plan_finished = False
            st.plan_change_seq, st.plan_change_ts = 0, ""
            st.assignments = {}
            st.reviewer_overrides = {}
            st.transfer_requests = {}
            st.wip_retries = {}
            st.limits = {}
            st.waiting = {}
            st.inputs = {}
            st.caretaking = {}
            st.caretaker_escalations = {}
            st.caretaker_limits = {}
        elif kind == "step.started" and e.get("kind") == "caretaker":
            st.waiting.pop((e.get("ticket"), "caretaker"), None)
            st.caretakers[e.get("step")] = e
        elif kind == "caretaker.started":
            st.caretaker_runs[e.get("step")] = e
            st.caretaking[e.get("target")] = e.get("step")
        elif kind == "caretaker.guard":
            st.caretaker_guards[e.get("step")] = e
            if e.get("wrote"):
                st.caretaker_wrote.setdefault(e.get("step"), []).extend(e.get("wrote") or [])
        elif kind == "caretaker.escalated" and e.get("limit"):
            st.caretaker_limits[e.get("problem")] = e
            st.caretaker_escalations[e.get("problem")] = e
        elif kind in ("caretaker.escalated", "caretaker.action"):
            st.caretaker_outcomes[e.get("step")] = e
            # Opiekun zakończony bez startu (np. `stale`, gdy czekał na slot): jego czekanie nie zajmuje już miejsca
            # w kolejce dostawcy (PT-150) — chyba że na ten sam ticket czeka jeszcze inny niewystartowany opiekun.
            if e.get("step") not in st.caretakers and not any(
                    name != e.get("step") and run.get("ticket") == e.get("ticket") and name not in st.caretakers
                    and name not in st.caretaker_outcomes for name, run in st.caretaker_runs.items()):
                st.waiting.pop((e.get("ticket"), "caretaker"), None)
            if st.caretaking.get(e.get("target")) == e.get("step"):
                st.caretaking.pop(e.get("target"), None)
            if kind == "caretaker.escalated":
                st.caretaker_escalations[e.get("problem")] = e
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
        elif kind == "card.inputs":
            st.inputs = e
        elif kind == "step.waiting":
            # `since` — początek czekania (pierwszy `step.waiting` przed startem); zmiana powodu go nie przesuwa.
            # Pierwszeństwo slotu między kartami (PT-150): krok opiekuna ustępuje wcześniej czekającym krokom.
            prev = st.waiting.get((e.get("ticket"), e.get("kind"))) or {}
            st.waiting[(e.get("ticket"), e.get("kind"))] = {**e, "since": prev.get("since") or e.get("ts") or ""}
            # Blokuje review planu do wyboru użytkownika tylko wyczerpany limit dostawcy; zwykła kolejka miejsc
            # („limit kroków dostawcy”) rusza sama (03.11, K-31 utknął w kolejce na zawsze).
            if e.get("kind") == "plan-review" and "limit" in str(e.get("reason") or "") and \
                    not is_queue_wait(e.get("reason")):
                st.plan_review_wait = e
        elif kind == "step.started":
            st.waiting.pop((e.get("ticket"), e.get("kind")), None)
            if e.get("kind") == "plan-review":
                # Review ruszyło, więc dawna blokada limitu nie obowiązuje (K-31: po nieudanym review karta
                # na zawsze pokazywała „czeka na limit” i chowała decyzję Ponów / Zmień wykonawcę).
                st.plan_review_wait = None
            st.steps[e.get("step")] = e
            if not (st.plan_approved and e.get("kind") in ("plan-amend", "plan-review", "plan-revise")):
                st.last_step = e.get("step") or ""
            st.moved_to = ""
            st.flow_error = ""
        elif kind == "step.entered" and e.get("step") in st.steps:
            st.steps[e["step"]] = {**st.steps[e["step"]], "mode": "interactive",
                                   "session": e.get("session") or "", "closeFile": e.get("closeFile") or "",
                                   "processFile": e.get("processFile") or "", "enteredAt": e.get("enteredAt") or 0,
                                   "consoleUnit": e.get("consoleUnit") or "",
                                   "source": e.get("source") or "", "taskFile": e.get("taskFile") or "",
                                   "consoleClosed": False, "consoleOpened": False}
        elif kind == "step.console_opened" and e.get("step") in st.steps:
            st.steps[e["step"]] = {**st.steps[e["step"]], "consoleOpened": True}
        elif kind == "step.console_closed" and e.get("step") in st.steps:
            st.steps[e["step"]] = {**st.steps[e["step"]], "consoleClosed": True}
        elif kind == "step.transfer_requested":
            st.transfer_requests[e.get("step")] = e
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
        elif kind == "permission.rechecked":
            if e.get("step") in st.finished:
                st.finished[e["step"]] = {**st.finished[e["step"]], "canAllow": bool(e.get("canAllow"))}
        elif kind == "outside.started":
            st.outside_started[e.get("step")] = e
        elif kind == "step.wip":
            st.wip_retries[e.get("step")] = e
        elif kind == "step.limit":
            st.limits[e.get("step")] = e
        elif kind == "step.notice":
            st.step_notices.add(e.get("step"))
        elif kind == "step.activity":
            st.activity[e.get("step")] = e
        elif kind == "flow.notice":
            st.flow_notices.add(e.get("key"))
        elif kind == "card.stalled":
            st.stalled_since = e.get("ts") or ""
        elif kind == "plan.checked":
            st.plan_checked = e
        elif kind == "plan.imported":
            st.imported = e
        elif kind == "plan.proposed":
            st.plan_proposed = e
            st.plan_reviewed = None
            st.plan_reviewer = ""
            st.plan_review_wait = None
            st.approval_requested = None
        elif kind == "plan.reviewer-selected":
            st.plan_reviewer = e.get("executor") or ""
        elif kind == 'plan.approval_requested':
            st.approval_requested = e
        elif kind == "plan.returned":
            st.plan_revision = e
            st.closed = None
        elif kind == "plan.revision-discarded":
            st.plan_revision = None
            st.plan_checked = None
        elif kind == "plan.revision-failed":
            st.plan_revision = e
        elif kind == "plan.revision-ready":
            st.plan_revision = e
        elif kind == "plan.revision-approved":
            st.plan_approved = {**st.plan_approved, **e["plan"]} if st.plan_approved else None
            # Usunięty niewystartowany ticket nie zajmie już slotu; odtworzenie dziennika usuwa też jego kolejkę.
            removed = set((e.get("difference") or {}).get("removed") or [])
            st.waiting = {key: wait for key, wait in st.waiting.items() if key[0] not in removed}
            st.plan_revision = None
            st.plan_finished = False
            st.closed = None
        elif kind == "plan.reviewed":
            st.plan_reviewed = e
            st.plan_review_wait = None
        elif kind == "plan.ticket-edited":
            for name in ("plan_proposed", "plan_approved"):
                plan = getattr(st, name)
                if plan:
                    plan = {**plan, "rows": [{**r, **e["changes"]} if (r.get("id") or r["workId"]) == e["ticket"] else r for r in plan["rows"]]}
                    setattr(st, name, plan)
        elif kind == "plan.approved":
            st.plan_approved = e
            for row in e.get("rows") or []:
                if row.get("sourceState") == "gotowe":
                    st.imported_done.add(row.get("id"))
                elif row.get("sourceState") == "odrzucone":
                    st.rejected.add(row.get("id"))
        elif kind == "plan.finished":
            st.plan_finished = True
        elif kind == "ticket.assigned":
            st.assignments[e.get("ticket")] = e
        elif kind == "reviewer.reassigned":
            st.reviewer_overrides[e.get("ticket")] = e
        elif kind == "flow.error":
            st.flow_error = e.get("error") or ""
        elif kind == "card.paused":
            st.paused = True
        elif kind == "card.resumed":
            st.paused = False
        elif kind == "card.finished":
            st.card_finished = True
        elif kind == "card.closed":
            st.closed = e
        elif kind == "card.main-merged":
            st.main_merged = e
        elif kind == "card.moved":
            st.store_column = e.get("to") or ""
            st.store_move_id = e.get("moveId") or ""
            # „Do poprawy” to przeniesienie z kolumny wyliczonej (Do sprawdzenia, Czeka na mnie); z Backlogu do Do
            # zrobienia to wznowienie albo kolejka po zleceniu z popupu — karta pokazuje dalej stan z dziennika.
            if e.get("from") not in ("backlog", "todo"):
                st.moved_to = e.get("to") or ""
                if st.moved_to == "todo":
                    st.closed = None
            if (e.get("to") or "") != "backlog":
                st.paused = False
        elif kind == "ticket.rejected":
            st.rejected.add(e.get("ticket"))
        elif kind == "ticket.imported":
            if e.get("state") == "gotowe":
                st.imported_done.add(e.get("ticket"))
            elif e.get("state") == "odrzucone":
                st.rejected.add(e.get("ticket"))
        if st.plan_approved is not None and (kind in (
                "plan.approved", "plan.ticket-edited", "plan.revision-approved", "ticket.assigned",
                "step.started", "step.finished", "step.waiting", "review.verdict",
                "review.question", "review.decision", "ticket.rejected", "ticket.imported") or
                (kind == "result" and e.get("action") == "merge")):
            st.plan_change_seq = int(e.get("seq") or 0)
            st.plan_change_ts = e.get("ts") or ""
    return st


def ticket_of(st: FlowState, ticket_id: str = "") -> dict:
    """Ostatni ticket karty: {id, title, branch, worktree, cardBranch, cardWorktree} z wyników akcji."""
    out = {"id": "", "title": "", "branch": "", "worktree": "", "cardBranch": "", "cardWorktree": ""}
    wanted = ticket_id or (st.steps.get(st.last_step) or {}).get("ticket") or ""
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


def _limit_wait_only(st: FlowState, name: str) -> bool:
    """Krok zatrzymany limitem z bezpiecznym WIP (D3): czeka na odnowienie, nie na użytkownika."""
    done = st.finished.get(name) or {}
    if done.get("result") != "error" or done.get("reason") != "limit":
        return False
    wip = (st.wip_retries.get(name) or {}).get("wip") or done.get("wip") or {}
    return not wip.get("error") and not (wip.get("files") and not wip.get("committed"))


def attention_step(st: FlowState, user_only: bool = False) -> str:
    """Najstarszy ostatni krok ticketu, który potrzebuje użytkownika; inne tickety pracują dalej.
    `user_only` pomija kroki czekające wyłącznie na limit (nie są ruchem użytkownika)."""
    latest = {}
    for name, item in st.steps.items():
        latest[item.get("ticket")] = name
    for name in latest.values():
        item, done = st.steps[name], st.finished.get(name) or {}
        if item.get("ticket") in st.rejected or name in st.step_decisions or name in st.superseded or \
                name in st.caretaking:      # nad krokiem pracuje opiekun karty (PT-143)
            continue
        if user_only and _limit_wait_only(st, name):
            continue
        if done.get("result") in ("question", "denied") or (done.get("result") == "error" and
                (int(item.get("attempt") or 0) >= 1 or done.get("reason") == "limit")) or (
                done.get("result") == "stopped" and done.get("reason") in ("interactive-closed", "transfer")):
            return name
        key = (item.get("ticket"), item.get("round"))
        if key in st.review_questions and key not in st.review_decisions:
            return name
    return ""


def _epoch(value) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() if value else 0.0
    except (AttributeError, ValueError, OverflowError):
        return 0.0


def guard_reason(st: FlowState) -> str | None:
    """None = praca lub koniec; napis = nazwany powód czekania; pusty napis = możliwy postój."""
    if not st.started or st.card_finished or st.paused or st.running() or st.open_intents() or st.open_consoles():
        return None
    if attention_step(st, True):
        return "pytanie, zgoda albo błąd kroku"
    if attention_step(st):
        return "limit dostawcy"
    if (st.plan_revision or {}).get("failed"):
        return "decyzja po nieudanej poprawie planu"
    if (st.plan_revision or {}).get("pending") and st.plan_reviewed:
        return "zatwierdzenie różnicy planu"
    if st.plan_proposed and st.plan_reviewed and not st.plan_approved:
        return "zatwierdzenie planu"
    if st.closed:
        return "karta do sprawdzenia"
    if st.plan_finished:
        return "karta do sprawdzenia"
    waiting = [event for (ticket, _kind), event in st.waiting.items() if ticket not in st.rejected and
               not (st.results.get(("merge", ticket)) or {}).get("ok")]
    if waiting:
        return waiting[-1].get("reason") or "limit lub bloker"
    if st.tracker_retries:
        return "ponowienie synchronizacji z YouTrackiem"
    if st.moved_to == "review":
        return "karta do sprawdzenia"
    rows = (st.plan_approved or {}).get("rows") or []
    unfinished = [row for row in rows if row.get("id") not in st.rejected and
                  not (st.results.get(("merge", row.get("id"))) or {}).get("ok")]
    if unfinished and all(row.get("blockers") and any(
            not (st.results.get(("merge", blocker)) or {}).get("ok") for blocker in row["blockers"])
            for row in unfinished):
        return "blokery ticketów"
    return ""


def decision_step(st: FlowState, user_only: bool = False) -> str:
    """Krok planu czekający na decyzję ma pierwszeństwo nad równoległym ticketem."""
    for name, step in reversed(list(st.steps.items())):
        if step.get("kind") not in ("plan-amend", "plan-review", "plan-revise") or name in st.superseded:
            continue
        done = st.finished.get(name) or {}
        if done.get("result") not in ("question", "denied", "error"):
            continue
        if done.get("result") == "error" and int(step.get("attempt") or 0) == 0 and \
                done.get("reason") not in ("limit", "login"):
            continue
        if user_only and _limit_wait_only(st, name):
            continue
        if any(s.get("parentStep") == name for s in st.steps.values()):
            continue
        return name
    return attention_step(st, user_only) or st.last_step


# Błąd przepływu (np. gita usługi) bez nazwy klasy i z tym, co użytkownik może zrobić sam; Tide ponawia akcję przy
# każdym przebiegu, więc po usunięciu przyczyny karta rusza bez dalszych kliknięć (03.11: K-35, K-15).
FLOW_ERROR_HINTS = (
    ("lokalne zmiany", "W worktree ticketu są niezacommitowane pliki. Tide raz odsyła krok autorowi do sprzątnięcia; "
                       "jeśli nadal są, otwórz worktree (Worktrees), zacommituj albo usuń te pliki — karta ruszy sama."),
    ("przekracza 300 000", "Zmiany ticketu są za duże do review — podziel ticket w planie albo odeślij plan z uwagami."),
    ("nie ma gałęzi", "Brakuje gałęzi ticketu albo karty — sprawdź Worktrees; jeśli ją usunięto, odrzuć ticket w planie."),
    ("katalog recenzentów", "Brak modelu recenzenta — sprawdź widok Modele (logowanie, dostępność)."),
)


def flow_error_text(error: str) -> str:
    return error.split(": ", 1)[1] if error.startswith(("FlowGitError: ", "FlowError: ")) else error


def flow_error_hint(error: str) -> str:
    low = error.casefold()
    for needle, hint in FLOW_ERROR_HINTS:
        if needle in low:
            return hint
    return "Tide ponawia tę akcję automatycznie; jeśli błąd się utrzymuje, otwórz kartę i sprawdź worktree ticketu."



def is_queue_wait(reason) -> bool:
    """Czekanie na wolne miejsce u dostawcy (`limit kroków dostawcy X: n/n`), nie na wyczerpany limit."""
    return str(reason or "").startswith("limit kroków dostawcy")


def plan_errors(st: FlowState) -> list[str]:
    """Błędy sprawdzenia planu z wyjaśnieniem, której wersji dotyczą. Odrzucona poprawka (np. automatyczna po
    review) zostawia poprzednią, poprawną propozycję — K-31: czerwone błędy wyglądały, jakby plan był zły."""
    checked = st.plan_checked or {}
    errors = list(checked.get("errors") or [])
    if not errors or (st.plan_approved and not st.plan_revision):
        return []          # zatwierdzony plan bez trwającej zmiany — stare błędy odrzuconej poprawki nie dotyczą
    proposed = st.plan_proposed or {}
    if proposed and checked.get("step") and checked.get("step") != proposed.get("sourceStep"):
        return ["Poprawka planu nie przeszła sprawdzenia i została odrzucona — obowiązuje poprzednia, poprawna "
                "wersja planu poniżej. Błędy odrzuconej poprawki:", *errors]
    return errors

def derived(st: FlowState, card: dict) -> dict | None:
    """Pole `derived` karty z dziennikiem (kształt jak board/derive.py, z `flow`) albo None — karta w swojej kolumnie.
    Pola pełnej karty (fazy, powody czekania, trwające kroki — PT-128) dokłada `fullcard.enrich`."""
    out = _derived(st, card)
    if out is None:
        return None
    from .fullcard import enrich
    return enrich(st, out)


def _derived(st: FlowState, card: dict) -> dict | None:
    if st.started is None:
        return None
    column = card.get("column") or "backlog"
    if column in ("done", "archived"):
        return None
    from .decisions import build_decisions
    decisions = build_decisions(st)
    attention = attention_step(st, True)
    focus = decision_step(st, True)
    if not decisions and not attention and not st.running():
        # Nic nie czeka na użytkownika: zatrzymany limitem krok nadal wyznacza kolumnę („czeka na limit”).
        focus = attention_step(st) or focus
    if focus:
        st = replace(st, last_step=focus)
    ticket = ticket_of(st)
    step = st.steps.get(st.last_step) or {}
    done = st.finished.get(st.last_step) or {}
    ident = step.get("ticket") or ticket["id"] or "ticket"
    executor = st.started.get("executor") or ""
    out = {"column": "", "done": 0, "total": 1, "tool": st.started.get("program") or "", "note": "", "nextStep": "",
           "check": st.started.get("check") or card.get("check") or "auto", "branch": ticket["cardBranch"], "session": "", "coordinator": "",
           "link": "flow", "agentState": "", "tickets": [], "question": "",
           "flow": {"ticket": ident, "step": st.last_step, "executor": step.get("executor") or executor,
                    "entry": st.started.get("entry") or "",
                    "result": done.get("result") or "", "reason": done.get("reason") or "",
                    "cardBranch": ticket["cardBranch"], "branch": ticket["branch"] or step.get("branch") or "",
                    "provider": step.get("provider") or st.started.get("provider") or "",
                    "stepKind": step.get("kind") or "",
                    # Kontrakt okna decyzji (PT-123); stare pola decyzji usunął PT-135.
                    "decisions": decisions}}
    out["flow"].update({"paused": st.paused, "timeline": st.timeline,
                        "stalledSince": st.stalled_since,
                        "closed": bool(st.closed), "summary": (st.closed or {}).get("summary") or "",
                        "mainMerged": bool(st.main_merged),
                        "waiting": [{"ticket": ticket, "kind": kind, "reason": item["reason"]}
                                    for (ticket, kind), item in st.waiting.items()]})
    from .caretaker import view as caretaker_view
    out["flow"]["caretaker"] = caretaker_view(st)
    out['flow']['trackerQueue'] = [
        {key: e.get(key) for key in ('action', 'key', 'after', 'error')} for e in st.tracker_retries.values()]
    tracker_reason = next(reversed(list(st.tracker_retries.values())), {}).get('error') or ''
    out["flow"]["steps"] = [
        {"id": name, "ticket": item.get("ticket") or "", "mode": item.get("mode") or "background",
         "result": (st.finished.get(name) or {}).get("result") or "",
         "reason": (st.finished.get(name) or {}).get("reason") or "",
         "startedTs": _epoch(item.get("ts")),
         "lastActivityTs": _epoch((st.activity.get(name) or item).get("ts")),
         "kind": item.get("kind") or "", "executor": item.get("executor") or "",
         "provider": item.get("provider") or "",
         "canTransfer": (name not in st.finished or
                         ((st.finished.get(name) or {}).get("reason") == "transfer" and
                          name not in st.step_decisions)) and
                        bool(item.get("provider")) and not st.card_finished,
         "canEnter": not any(other.get("worktree") == item.get("worktree") and other.get("step") != name
                             for other in st.running()) and
                     not any(other.get("worktree") == item.get("worktree") and other.get("mode") == "interactive"
                             and not other.get("consoleClosed") for other in st.steps.values())}
        for name, item in st.steps.items()
    ]
    plan_entry = st.started.get("entry") in ("plan", "planning")
    if plan_entry:
        rows, finished_ids = _plan_view(st, out)
    if done.get("reason") in ("limit", "transfer"):
        decision = st.step_decisions.get(st.last_step) or {}
        limit = st.limits.get(st.last_step) or {}
        wip = (st.wip_retries.get(st.last_step) or {}).get("wip") or done.get("wip") or {}
        label = {"anthropic": "Claude", "openai": "Codex"}.get(
            step.get("provider"), step.get("provider") or "dostawcy")
        reset = limit.get("reset") or ""
        reason = f"Wyczerpany limit {label}" if done.get("reason") == "limit" else "Przekazanie kroku"
        if reset:
            reason += f" do {reset}"
        if wip.get("error") or (wip.get("files") and not wip.get("committed")):
            reason += f" — zapis zmian wymaga naprawy ({wip.get('error') or 'brak commita WIP'})"
        out["question"] = reason
        if decision.get("choice") == "transfer":
            if not (wip.get("stopped") and not wip.get("error") and
                    (wip.get("committed") or wip.get("files") == 0)):
                return _fill(out, "waiting", reason)
            return _fill(out, "progress", f"{ident} · przekazuję krok")
        if done.get("reason") == "limit" and not decisions and not wip.get("error") and \
                not (wip.get("files") and not wip.get("committed")):
            # PT-130 (D3): limit nie jest decyzją. Karta czeka na odnowienie w „W toku”, nie w „Czeka na mnie”;
            # po odnowieniu krok rusza od nowa i kolumnę liczą zwykłe reguły. Zapis zmian do naprawy zostaje
            # w „Czeka na mnie” (to praca dla użytkownika, nie limit).
            out["question"] = ""
            return _fill(out, "progress", f"{ident} · czeka na limit" + (f" do {reset}" if reset else ""))
        return _fill(out, "waiting", reason)
    failures = [e for e in st.tracker_failures() if e.get('action') != 'youtrack-note']
    if failures and not st.running():
        error = failures[-1].get('error') or 'odmowa operacji YouTracka'
        return _fill(out, 'waiting', 'Błąd YouTracka: ' + error + ' — popraw przyczynę i wybierz „Ponów”')
    if plan_entry:
        waiting_review = st.plan_review_wait or st.waiting.get(("", "plan-review")) or {}
        if st.plan_proposed and not st.plan_reviewed and not st.plan_reviewer and waiting_review and not any(
                s.get("kind") == "plan-review" and s.get("step") not in st.finished
                for s in st.steps.values()):
            out["flow"]["plan"].update(status="blocked", reviewWait={
                "reason": waiting_review.get("reason") or "review planu czeka",
                "provider": waiting_review.get("provider") or ""})
            reason = waiting_review.get("reason") or "review planu czeka"
            if is_queue_wait(reason):
                # Kolejka miejsc u dostawcy nie jest ruchem użytkownika: karta zostaje „W toku” i rusza sama;
                # wybór innego recenzenta zostaje w panelu jako opcja (03.11, K-31).
                result = _fill(out, "progress", f"review planu w kolejce — ruszy samo ({reason})")
                result["nextStep"] = "ruszy samo; możesz wybrać innego recenzenta planu"
                return result
            out["question"] = "Wybierz recenzenta planu albo poczekaj na limit."
            result = _fill(out, "waiting", reason)
            result["nextStep"] = "wybierz model do review planu"
            return result
        if st.stalled_since:
            return _fill(out, "waiting", "stoi — nie ma pracującego kroku ani nazwanego powodu czekania")
        focus = decision_step(st)
        focus_step = st.steps.get(focus) or {}
        focus_done = st.finished.get(focus) or {}
        if focus_step.get("kind") in ("plan-amend", "plan-review", "plan-revise") and focus_done.get("result") in ("question", "denied", "error") and focus not in st.step_decisions and (focus_done.get("result") != "error" or int(focus_step.get("attempt") or 0) > 0 or focus_done.get("reason") == "login"):
            result = focus_done["result"]
            out["flow"].update({"step": focus, "stepKind": focus_step["kind"], "result": result,
                                "executor": focus_step.get("executor") or "", "provider": focus_step.get("provider") or "",
                                "planner": (st.plan_proposed or {}).get("planner") or ""})
            out["question"] = focus_done.get("question") or "Decyzja dotycząca poprawy planu"
            if result == "error":
                found = of_finished(focus_done)
                if found["fault"] == "tide":
                    tile = tide_fault_text(focus_step["kind"], focus_step.get("ticket"))
                    return _fill(out, "waiting", f"{tile}: {cause_label(found['cause'], REASON_LABELS)}")
            return _fill(out, "waiting", waiting_text(focus_step["kind"], result))
        if (st.plan_revision or {}).get("failed"):
            out["question"] = "Poprawa planu jest nieprawidłowa. Odesłać ponownie czy odrzucić poprawkę?"
            return _fill(out, "waiting", "poprawa planu wymaga decyzji")
        if (st.plan_revision or {}).get("pending") and st.plan_reviewed:
            out["question"] = "Zatwierdzić różnicę planu?"
            return _fill(out, "waiting", "różnica planu do zatwierdzenia")
        if not st.plan_proposed:
            if st.started.get("entry") != "planning" and not st.imported:
                return _fill(out, "waiting", "propozycja planu wymaga poprawy")
            if not st.flow_error and done.get("result") not in ("question", "denied", "error", "stopped"):
                if st.running() or not st.plan_checked:
                    return _fill(out, "progress", "planowanie w Konsoli" if st.started.get("mode") == "full"
                                 and step.get("kind") == "planning" else "planowanie w tle")
                if int(step.get("round") or 0) < 2:
                    return _fill(out, "progress", "poprawa formatu planu w tle")
                out["question"] = "Plan nadal ma błędny format po dwóch poprawach. Uruchomić planowanie ponownie?"
                return _fill(out, "waiting", "propozycja planu wymaga poprawy po dwóch próbach")
        elif not st.plan_reviewed and not st.plan_approved:
            if done.get("result") not in ("question", "denied", "error", "stopped"):
                return _fill(out, "progress", "review planu w tle")
        elif not st.plan_approved and st.approval_requested and st.started.get('trackerProject'):
            return _fill(out, 'backlog' if st.paused else 'progress',
                         tracker_reason or 'YouTrack: zatwierdzenie planu czeka na zgłoszenia')
        elif not st.plan_approved:
            out["question"] = "Zatwierdzić plan?"
            return _fill(out, "waiting", "plan do zatwierdzenia")
        if st.closed and st.plan_finished and not st.plan_revision:
            return _fill(out, "review", f"✓ plan karty: {len(finished_ids)}/{len(rows)} ticketów scalonych — do sprawdzenia")
        if st.paused:
            return _fill(out, "backlog" if column == "backlog" else "progress", "wstrzymana — trwające kroki kończą się" if st.running() else "wstrzymana")
        if st.waiting and not st.running() and not attention:
            return _fill(out, "progress", out["flow"]["waveLabel"] + " · " + next(reversed(st.waiting.values()))["reason"])
        if st.plan_approved is not None and not st.last_step and not st.open_intents():
            return _fill(out, "progress", "uruchamiam pierwszy ticket planu")
    if st.closed and not st.plan_approved:
        out["done"] = 1
        return _fill(out, "review", f"✓ {ident} na gałęzi karty — do sprawdzenia")
    if st.stalled_since:
        return _fill(out, "waiting", "stoi — nie ma pracującego kroku ani nazwanego powodu czekania")
    if st.started.get('trackerProject') and st.started.get('entry') == 'now' and not st.steps:
        return _fill(out, 'backlog' if st.paused else 'progress',
                     tracker_reason or 'YouTrack: zlecenie czeka na zgłoszenie')
    verdict = next((v for (ticket_id, _round), v in reversed(list(st.verdicts.items())) if ticket_id == ident), None)
    if verdict:
        out["flow"].update({key: verdict[key] for key in ("verdict", "blockers", "majors", "nits", "round")})
        question_key = (ident, verdict.get("round"))
        if question_key in st.review_questions and question_key not in st.review_decisions:
            out["flow"]["reviewQuestion"] = True
    if st.paused and column != "backlog":
        return _fill(out, "progress", "wstrzymana — trwające kroki kończą się" if st.running() else "wstrzymana")
    if column == "backlog":
        if not st.paused:
            return None
        running = " — trwający krok kończy się" if st.running() else ""
        return _fill(out, "backlog", f"wstrzymana: {ident}{running}")
    if st.moved_to == "todo" and not st.running():
        return None
    failed = [r for (a, _k), r in st.results.items() if not r.get("ok") and a != "merge"]
    if st.waiting and not st.running() and not attention:
        return _fill(out, "progress", f"{ident} · {next(reversed(st.waiting.values()))['reason']}")
    caring = out["flow"]["caretaker"]["working"]
    if caring and not attention:
        # Opiekun sprawdza problem (PT-143): decyzja jest ukryta, karta „W toku” bez powiadomienia.
        return _fill(out, "progress", caring[0]["label"])
    if st.running() and not attention:
        work = ("krok w Konsoli" if step.get("mode") == "interactive" else
                "review" if step.get("kind") == "review" else "poprawki" if step.get("kind") == "fix" else "krok w tle")
        label = out["flow"].get("waveLabel")
        from .fullcard import limit_stops
        held = limit_stops(st)
        until = next((i["until"] for i in held if i.get("until")), "")
        tail = (" · czeka na limit" + (f" do {until}" if until else "")) if held else ""
        return _fill(out, "progress", f"{label} · {len(st.running())} kroków w toku{tail}" if label else
                     f"{ident} · {work} ({step.get('executor') or executor}){tail}")
    if st.open_intents():
        return _fill(out, 'progress', tracker_reason or f"{ident} · {_intent_word(st.open_intents()[0])}")
    if failed and not done:
        return _fill(out, "waiting", f"błąd zlecenia: {failed[-1].get('error') or failed[-1].get('action')}")
    if st.flow_error:
        out["flow"]["flowError"] = st.flow_error
        out["flow"]["flowErrorHint"] = flow_error_hint(st.flow_error)
        return _fill(out, "waiting", f"{ident}: {flow_error_text(st.flow_error)}")
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
                if decision.get("choice") == "accept":
                    return _fill(out, "progress", f"{ident} · scalanie mimo uwag (decyzja użytkownika)")
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
        if st.started.get("entry") not in ("plan", "planning"):
            out["done"] = 1
        return _fill(out, "review", f"✓ {ident} na gałęzi karty — review APPROVE, do sprawdzenia")
    if result == "question" or (result == "stopped" and done.get("reason") == "interactive-closed"):
        if result == "question" and st.last_step in st.step_decisions:
            return _fill(out, "progress", f"{ident} · wznawiam po odpowiedzi")
        out["question"] = _clip(done.get("question") or "", 120)
        return _fill(out, "waiting", f"{ident} pyta: „{out['question']}”" if out["question"] else f"{ident} pyta")
    if result == "denied" and done.get("outside"):
        decision = st.step_decisions.get(st.last_step) or {}
        if decision.get("choice") in ("run", "run-always", "run-ticket"):
            started = st.outside_started.get(st.last_step)
            if started is None:
                return _fill(out, "progress", f"{ident} · w kolejce poza piaskownicą (jedno polecenie naraz w projekcie): "
                                              f"{_clip(done.get('command') or '', 80)}")
            since = str(started.get("ts") or "")[11:16]
            return _fill(out, "progress", f"{ident} · od {since} trwa poza piaskownicą: "
                                          f"{_clip(done.get('command') or '', 80)} (wynik wróci do agenta)")
        if decision:
            return _fill(out, "progress", f"{ident} · wznawiam po decyzji o poleceniu")
        return _fill(out, "waiting", f"{ident} prosi o uruchomienie poza piaskownicą: „{_clip(done.get('command') or '?', 80)}”")
    if result == "denied":
        if st.last_step in st.step_decisions:
            return _fill(out, "progress", f"{ident} · wznawiam po decyzji o poleceniu")
        return _fill(out, "waiting", f"{ident}: brak zgody na „{done.get('command') or '?'}”")
    if result in ("error", "stopped"):
        reason = REASON_LABELS.get(done.get("reason") or "", done.get("reason") or "")
        if result == "error":
            if int(step.get("attempt") or 0) == 0 and st.last_step not in st.step_decisions and \
                    done.get("reason") != "login":
                return _fill(out, "progress", f"{ident} · ponawiam krok po błędzie")
            if st.last_step in st.step_decisions:
                return _fill(out, "progress", f"{ident} · wznawiam po decyzji użytkownika")
            found = of_finished(done)
            if found["fault"] == "tide":
                tile = tide_fault_text(step.get("kind"), step.get("ticket") or ident)
                return _fill(out, "waiting", f"{tile}: {cause_label(found['cause'], REASON_LABELS)}")
        out["flow"]["stalledSince"] = done.get("stalledSince") or ""
        since = f" od {done['stalledSince']}" if done.get("stalledSince") else ""
        return _fill(out, "waiting", f"błąd kroku {ident}: {reason}{since}" if reason else f"błąd kroku {ident}")
    return _fill(out, "progress", f"{ident} · zlecenie")


def _plan_view(st: FlowState, out: dict) -> tuple[list, set]:
    """Tickety, fale i plan karty z planowaniem (`tickets`, `flow.plan`, `waveLabel`, postęp). Liczone przed gałęzią
    limitu i błędu YouTracka, żeby pełna karta (PT-128) widziała fale także wtedy, gdy krok stoi na limicie."""
    proposal = st.plan_proposed or {}
    approved = st.plan_approved or {}
    revision_pending = bool((st.plan_revision or {}).get("pending"))
    rows = (proposal.get("rows") if revision_pending else approved.get("rows")) or proposal.get("rows") or []
    rows = [{**row, "editable": not st.plan_revision and (row.get("id") or row["workId"]) not in st.assignments
             and not any(s.get("ticket") == (row.get("id") or row["workId"]) for s in st.steps.values())}
            for row in rows]
    waves = graph_waves(rows)
    finished_ids = set(st.imported_done) | {row.get("id") or row.get("workId") for row in rows
                    if not st.plan_approved and row.get("sourceState") == "gotowe"} | {row.get("id") for row in rows if ("merge", row.get("id")) in st.results and
                    st.results[("merge", row.get("id"))].get("ok")}
    out["total"], out["done"] = len(rows), len(finished_ids)
    wave_no = next((n for n, wave in enumerate(waves, 1) if set(wave) - finished_ids), len(waves))
    out["flow"].update({"wave": wave_no, "waveTotal": len(waves), "paused": st.paused,
                        "timeline": st.timeline})
    for row in rows:
        ident_row = row.get("id") or row.get("workId")
        latest = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == ident_row), {})
        waits = [w for (ticket_id, _), w in st.waiting.items() if ticket_id == ident_row]
        outcome = (st.finished.get(latest.get("step")) or {}).get("result")
        if ident_row in finished_ids:
            state = "gotowe"
        elif ident_row in st.rejected or not st.plan_approved and row.get("sourceState") == "odrzucone":
            state = "odrzucone"
        elif waits or outcome in ("question", "denied", "error", "stopped"):
            state = "czeka"
        elif latest:
            state = "review" if latest.get("kind") == "review" or outcome == "done" else "w-toku"
        else:
            state = "todo"
        out["tickets"].append({"id": ident_row, "ticket": ident_row, "title": row.get("title") or "",
                               "state": state, "wave": next((n for n, w in enumerate(waves, 1) if ident_row in w), 0),
                               "waitReason": waits[-1]["reason"] if waits else ""})
    out["tickets"].sort(key=lambda ticket: ticket["wave"])
    out["flow"]["waveLabel"] = f"fala {wave_no} z {len(waves)}"
    out["flow"]["plan"] = {"review": plan_review_view(st.plan_reviewed), "revision": st.plan_revision or {},
                           "planner": proposal.get("planner") or "",
                           "reviewStep": next((s.get("step") for s in reversed(list(st.steps.values()))
                                               if s.get("kind") == "plan-review" and s.get("step") not in st.finished), ""),
                           "reviewProvider": next((s.get("provider") for s in reversed(list(st.steps.values()))
                                                   if s.get("kind") == "plan-review" and s.get("step") not in st.finished), ""),
                           "reviewExecutor": st.plan_reviewer,
                           "plannerStep": next((s["step"] for s in reversed(list(st.steps.values())) if s.get("executor") == proposal.get("planner")), ""),
                           "status": "reviewing" if not st.plan_reviewed and (not st.plan_approved or revision_pending)
                           else "pending" if revision_pending else "done" if st.plan_finished else
                           "approved" if st.plan_approved else "pending" if st.plan_proposed else "invalid",
                           "rows": rows, "waves": waves,
                           "errors": plan_errors(st)}
    return rows, finished_ids


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


def graph_waves(rows: list[dict]) -> list[list[str]]:
    """Poziomy grafu blokerów; nigdy osobny zapis stanu fali."""
    pending = {(r.get("id") or r.get("workId")): set(r.get("blockers") or []) for r in rows}
    done, waves = set(), []
    while pending:
        wave = [ident for ident, blockers in pending.items() if blockers <= done]
        if not wave:
            return []
        waves.append(wave)
        done.update(wave)
        pending = {ident: blockers for ident, blockers in pending.items() if ident not in done}
    return waves
