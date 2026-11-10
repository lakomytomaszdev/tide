"""Stan karty liczony z dziennika (bez Qt i bez dysku): `fold(events)` i pole `derived` karty dla Tablicy.

Typy zdarzeń (schemat 1, PT-58):
- `card.started` — zlecenie: entry (now), executor, program, model, check,
- `plan.import-prepared` — trwałe źródło i wiersze przejęcia, a dla kompletnego planu także kartki
  i specyfikacja; `card.started.importPreparation` wskazuje zdarzenie do odtworzenia kopii planu,
  wejść przejęcia i `plan.imported` po przerwaniu zapisu,
- `intent` / `result` — para „zamiar → wynik” akcji ze skutkami poza dziennikiem: action (reserve, card-branch,
  ticket-branch, step-start, merge, main-merge), key (ten sam w parze), w wyniku ok, error i dane akcji,
- `main-merge.checkpoint` / `main-merge.waiting` / `main-merge.resumed` — etapy trwałego scalenia i oczekiwanie,
- `main-merge.abandoned` — porzucenie zamiaru po decyzji użytkownika, bez cofania materializacji,
- `step.started` — krok w tle: step, kind (ticket), ticket, executor, unit, session, worktree, branch, stepDir, gitDir,
- `step.finished` — wynik kroku: result (done, question, denied, error, stopped), reason (no-result, bad-result,
  no-progress, exit, start), question, command, notes, wip (commit WIP przy zatrzymaniu),
- `card.moved` (to, from — kolumna, w której kartę widać), `card.paused`, `card.finished` (column),
  `ticket.rejected` (ticket),
- `card.merged` — zakończone scalenie: operationId, targetRef/targetBranch, sourceCommit,
  targetCommitBefore, resultCommit i baseVersion; stare `card.main-merged` odczytujemy z zapisanym main.

Kolumna (ręczne kolumny wygrywają z dziennikiem):
- Gotowe i Archiwum w pliku karty — bez stanu z dziennika (karta stoi tam, gdzie ją postawiono);
  wyjątek: scalenie przerwane po zapisie kolumny, przed card.finished, nadal może jawnie czekać na recovery,
- Backlog — „wstrzymana”, gdy kartę wstrzymano po zleceniu (inaczej bez stanu),
- Do zrobienia — z dziennika: krok trwa albo scalanie czeka → W toku; pytanie, brak zgody, błąd kroku albo nieudane
  scalenie → Czeka na mnie; ticket na gałęzi karty → Do sprawdzenia. Karta przeniesiona ręcznie do Do zrobienia po
  ostatnim wyniku (np. „do poprawy” z Do sprawdzenia) stoi w Do zrobienia bez stanu.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import re

from .baseproposal import pending
from . import consent
from .faults import cause_label, of_finished
from .history import LOGGED as HISTORY_LOGGED
from .findings import review_view as plan_review_view
from .labels import result_label, tide_fault_text, waiting_text
from .processidentity import START_FIELDS, process_identities

REASON_LABELS = {"no-result": "brak pliku wyniku", "bad-result": "zły plik wyniku", "no-progress": "bez postępu",
                 "exit": "program zakończył się błędem", "start": "krok nie wystartował",
                 "login": "wygasło logowanie agenta — zaloguj się (np. `claude /login` w terminalu) i kliknij Ponów",
                 "timeout": "przekroczony czas kroku", "local-unavailable": "serwer lokalny jest niedostępny",
                 "lost": "krok zniknął (restart usługi)", "bad-review": "niepoprawny werdykt review",
                 "review-changed-ticket": "recenzent zmienił gałąź albo pliki ticketu", "abandoned": "porzucony"}
NOTE_MAX = 160


# Kroki, które poprawiają albo oceniają plan karty — w ich trakcie plan nie czeka na decyzję użytkownika.
PLAN_WORK_KINDS = ("plan-amend", "plan-revise", "plan-review")


@dataclass
class FlowState:
    process_identities: dict = field(default_factory=dict)  # cała historia, także poprzednie zlecenia
    started: dict | None = None                     # ostatnie card.started
    base: dict = field(default_factory=dict)
    initial_base: dict = field(default_factory=dict)
    base_version: int = 0
    base_change: dict = field(default_factory=dict)
    base_validation_required: bool = False
    base_generation_wait: dict = field(default_factory=dict)
    base_restart_ticket: str = ""
    base_waits: dict = field(default_factory=dict)
    base_request: dict = field(default_factory=dict)  # decyzja użytkownika niezwiązana z krokiem
    intents: dict = field(default_factory=dict)     # (action, key) → intent
    results: dict = field(default_factory=dict)     # (action, key) → result
    steps: dict = field(default_factory=dict)       # step → step.started
    step_notes: dict = field(default_factory=dict)  # noteId → step.note-added
    finished: dict = field(default_factory=dict)    # step → step.finished
    verdicts: dict = field(default_factory=dict)    # (ticket, runda) → review.verdict
    review_questions: dict = field(default_factory=dict)
    review_decisions: dict = field(default_factory=dict)
    step_retries: dict = field(default_factory=dict)  # źródłowy krok → automatyczne ponowienie
    step_decisions: dict = field(default_factory=dict)  # źródłowy krok → odpowiedź użytkownika
    outside_started: dict = field(default_factory=dict)  # krok → outside.started (polecenie poza piaskownicą)
    outside_launched: set = field(default_factory=set)  # także zakończone, przed wznowieniem kroku
    ticket_consents: dict = field(default_factory=dict)  # ticket → ostatnie nadanie/cofnięcie w zleceniu
    superseded: set = field(default_factory=set)      # kroki sprzed card.started albo odesłane do planu — bez decyzji
    plan_resumes: dict = field(default_factory=dict)  # krok → None przed rozstrzygnięciem poprawki, potem zbiór blokerów
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
    plan_artifact_source: dict | None = None  # propozycja pakietu z ostatniego zatwierdzenia
    plan_reviewed: dict | None = None
    plan_reviewer: str = ""  # ręczny wybór modelu do niezależnego review planu
    plan_review_wait: dict | None = None  # blokada limitu pozostaje po restarcie i próbie review
    plan_revision: dict | None = None
    # PT-146: odesłania planu (`plan.returned`) w kolejności i seq ostatniej zmiany planu zrobionej przez użytkownika
    # (odesłanie bez `caretaker`, odrzucenie poprawki) — łańcuch kroków poprawy i zamknięcie problemu opiekuna.
    plan_returns: list = field(default_factory=list)
    plan_user_seq: int = 0
    plan_finished: bool = False
    box_wait: str = ""                              # PT-172: na co karta czeka przed pudełkiem (boxwait.py)
    box_env_deferred: bool = False                  # PT-290: zgoda środowiska odroczona przy box.enabled=false
    plan_change_seq: int = 0
    plan_change_ts: str = ""
    assignments: dict = field(default_factory=dict)  # ticket → autor i recenzent z planu
    reviewer_overrides: dict = field(default_factory=dict)  # ticket → recenzent po ręcznym transferze
    author_overrides: dict = field(default_factory=dict)  # ticket → autor kolejnych poprawek po przepięciu
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
    merged: dict | None = None                       # ogólny wynik, także ze starego card.main-merged
    merge_waiting: dict | None = None
    abandoned_main_merges: set = field(default_factory=set)
    moved_to: str = ""                              # kolumna z ostatniego card.moved po ostatnim wyniku kroku
    store_column: str = ""                          # kolumna ostatnio potwierdzona w dzienniku
    store_move_id: str = ""                         # ostatni ruch MoveCard potwierdzony w dzienniku
    lifted: bool = False                            # PT-313: ostatni ruch to zdjęcie z Backlogu, ruch okna jeszcze nie przyszedł
    rejected: set = field(default_factory=set)
    last_failed_action: str = ""
    # Błędy Tide (PT-147): etap → {event, persisted, repeated}. Teksty pamiętamy w obrębie etapu,
    # od `card.started`; równoległe tickety mają niezależne problemy i limity działań opiekuna.
    flow_errors: dict = field(default_factory=dict)
    flow_error_texts: set = field(default_factory=set)
    last_seq: int = 0
    # Opiekun karty (PT-143): kroki `caretaker` nie są krokami ticketów — osobno od `steps`, żeby żaden wyzwalacz,
    # „ostatni krok ticketu” ani decyzja ich nie widziały (bez rekursji). Ich `step.finished` trafia do `finished`.
    caretakers: dict = field(default_factory=dict)            # krok opiekuna → step.started (kind caretaker)
    caretaker_chats: dict = field(default_factory=dict)       # rozmowa → kontekst jej otwarcia
    caretaker_chat_actions: dict = field(default_factory=dict)  # plik akcji → utrwalony wynik walidacji i kontekst
    caretaker_chat_files: dict = field(default_factory=dict)    # niepełny JSON → metadane ostatniego odczytu
    caretaker_runs: dict = field(default_factory=dict)        # krok opiekuna → caretaker.started
    caretaker_outcomes: dict = field(default_factory=dict)    # krok opiekuna → caretaker.escalated / caretaker.action
    caretaker_escalations: dict = field(default_factory=dict)  # id problemu → ostatnie caretaker.escalated
    caretaker_decisions: dict = field(default_factory=dict)  # id problemu → decyzja użytkownika
    caretaking: dict = field(default_factory=dict)            # krok problemu → krok opiekuna, który nad nim pracuje
    # PT-150: eskalacja po wyczerpaniu limitu działań nie jest wynikiem żadnego kroku opiekuna (nie ma czwartego).
    caretaker_limits: dict = field(default_factory=dict)      # id problemu → ostatnie caretaker.escalated z limit
    # Strażnik kroku opiekuna (PT-158): ostatni stan bazowy `caretaker.guard` i powody wykrytych po drodze zmian.
    caretaker_guards: dict = field(default_factory=dict)      # krok opiekuna → ostatnie caretaker.guard
    caretaker_wrote: dict = field(default_factory=dict)       # krok opiekuna → [powód zmiany]
    # Nie z dziennika (PT-151): ustawienia opiekuna przy tej karcie (`caretaker.setup`), które silnik dokłada przed
    # widokami (`derived`); pusty — wartości domyślne bez katalogu wykonawców.
    caretaker_setup: dict = field(default_factory=dict)

    @property
    def flow_error_event(self) -> dict:
        """Ostatni otwarty błąd do dzisiejszego znacznika karty; opiekun czyta błędy według etapu."""
        return max((item["event"] for item in self.flow_errors.values()),
                   key=lambda e: int(e.get("seq") or 0), default={})

    @property
    def flow_error(self) -> str:
        return self.flow_error_event.get("error") or ""

    def running(self) -> list[dict]:
        return [s for name, s in self.steps.items() if name not in self.finished] + \
            [s for name, s in self.caretakers.items() if name not in self.finished]

    def open_consoles(self) -> list[dict]:
        """Karty Konsoli jeszcze otwarte, również po zapisaniu pliku wyniku."""
        return [s for s in self.steps.values() if s.get("mode") == "interactive" and not s.get("consoleClosed")]

    def open_intents(self, *, include_background=False) -> list[dict]:
        return [i for k, i in self.intents.items() if k not in self.results and
                not (i.get("action") == "main-merge" and i.get("key") in self.abandoned_main_merges) and
                (include_background or i.get('action') != 'youtrack-note')]

    def tracker_failures(self):
        return [e for e in self.results.values() if e.get('permanent') and not e.get('ok')]

    def merge_pending(self) -> list[dict]:
        """Tickety zatwierdzone w review, których jeszcze nie scalono."""
        out = []
        for (ticket, _round), verdict in self.verdicts.items():
            if verdict.get("verdict") == "APPROVE" and ("merge", ticket) not in self.results \
                    and ticket not in self.rejected:
                step = next((s for name, s in self.steps.items() if name not in self.superseded and
                             s.get("ticket") == ticket and
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
        if self.base_validation_required or self.base_generation_wait:
            return "generacja karty czeka na walidację kontekstu albo odzyskanie zasobów"
        if self.running():
            return "krok tej karty jeszcze trwa"
        if self.open_consoles():
            return "Konsola kroku tej karty jest jeszcze otwarta"
        if self.merge_pending():
            return "zatwierdzony ticket czeka na scalenie"
        if not self.card_finished and any(i.get("action") == "main-merge" and
                                         i.get("key") not in self.abandoned_main_merges and
                                         i.get("requestSeq") == (self.started or {}).get("seq")
                                         for i in self.intents.values()):
            return "scalenie karty jest zaczęte i niedokończone"
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
        duplicate_merge = kind in ("card.merged", "card.main-merged") and st.merged is not None
        if kind != "card.stalled" and not _bookkeeping(st, e):
            st.last_activity_ts = _epoch(e.get("ts")) or st.last_activity_ts
            st.stalled_since = ""
        if not duplicate_merge and (kind in (
                    "card.started", "card.stalled", "step.started", "step.finished", "step.waiting", "review.verdict",
                    "step.decision", "step.transfer_requested", "step.wip", "step.limit",
                    "plan.approved", "card.paused", "card.resumed", "plan.finished",
                    "card.merged", "card.main-merged") or (
                    kind == "result" and e.get("action") == "merge")):
            st.timeline.append({key: e[key] for key in (
                "seq", "ts", "type", "step", "ticket", "executor", "kind", "result", "reason", "verdict",
                "action", "key", "ok", "targetRef", "targetBranch", "main", "operationId") if key in e})
        if kind in HISTORY_LOGGED and not duplicate_merge:
            st.log.append(e)
        st.last_seq = max(st.last_seq, int(e.get("seq") or 0))
        if kind == "step.base-wait":
            st.base_waits[e.get("step")] = e
        if kind == "card.base-requested":
            st.base_request = e
        elif kind == "card.base-kept":
            if st.base_request.get("decision") == e.get("decision"):
                st.base_request = {}
        if kind == "card.started":
            st.ticket_consents = {}
            st.base = _base(e)
            st.initial_base = dict(st.base)
            version = e.get("baseVersion", 0)
            st.base_version = version if type(version) is int and version >= 0 else 0
            st.base_change = {}
            st.base_validation_required = False
            st.base_generation_wait = {}
            st.base_restart_ticket = ""
            st.base_request = {}
            # Nowe zlecenie startuje tylko, gdy nic nie trwa (`busy_reason`), więc pytania, błędy i review
            # poprzedniego przepływu (np. porzuconego przeniesieniem do Do zrobienia) już na nikogo nie czekają.
            st.superseded.update(st.steps)
            st.started, st.paused, st.card_finished, st.moved_to = e, False, False, ""
            st.closed = st.main_merged = st.merged = None
            st.merge_waiting = None
            st.store_column = e.get("column") or ""
            st.store_move_id = e.get("moveId") or ""
            st.lifted = False
            st.plan_checked = st.plan_proposed = st.plan_approved = st.approval_requested = None
            st.plan_artifact_source = None
            st.imported = None
            st.imported_done.clear()
            st.plan_reviewed = st.plan_revision = None
            st.plan_returns, st.plan_user_seq = [], 0
            st.plan_resumes.clear()
            st.plan_reviewer = ""
            st.plan_review_wait = None
            st.plan_finished = False
            st.box_wait = ""
            st.box_env_deferred = False
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
            st.caretaker_decisions = {}
            st.caretaker_limits = {}
            st.flow_errors = {}
            st.flow_error_texts = set()
        elif kind == "card.base-changed" and st.started:
            previous, version = e.get("previousBaseVersion"), e.get("baseVersion")
            candidate = _base(e.get("base") or e)
            if (e.get("ok", True) is True and type(previous) is int and type(version) is int
                    and previous == st.base_version and version == previous + 1
                    and candidate["status"] == "known" and _valid_generation(st, e, candidate)):
                st.base, st.base_version, st.base_change = candidate, version, e
                st.ticket_consents = {}
                st.base_validation_required = e.get("validationRequired") is True
                st.base_generation_wait = {}
                if "generation" in e:
                    # Dowiedziona pusta generacja nie ma wkładu do ponownego review. Zgody i
                    # domknięcie poprzedniego kodu pozostają w dzienniku, ale nie autoryzują nowego.
                    st.base_validation_required = False
                    st.base_restart_ticket = ticket_of(st)["id"]
                    st.closed = None
                    st.verdicts = {}
                    st.review_questions = {}
                    st.review_decisions = {}
                    st.step_decisions = {}
                    st.base_request = {}
                    st.waiting = {}
                    st.transfer_requests = {}
                    st.flow_errors = {}
                    st.flow_error_texts = set()
                    st.plan_resumes.clear()
                    st.caretaking = {}
                    st.caretaker_escalations = {}
                    st.caretaker_decisions = {}
                    st.caretaker_limits = {}
                    st.superseded.update(st.steps)
                    st.last_step = ""
                    st.moved_to = ""
                    # Propozycja i review planu także dotyczą dawnego kodu, nawet jeśli
                    # powstały wyłącznie poza repo i nie ma jeszcze commitu planu.
                    st.plan_checked = st.plan_proposed = st.plan_approved = st.approval_requested = None
                    st.plan_artifact_source = None
                    st.plan_reviewed = st.plan_revision = None
                    st.plan_reviewer = ""
                    st.plan_review_wait = None
                    st.plan_change_seq, st.plan_change_ts = 0, ""
                    st.plan_finished = False
                    # Jeden fold wybiera kompletną mapę. Historyczne step.started pozostają dowodami
                    # starych procesów; bieżące ścieżki pochodzą wyłącznie z wyników tej generacji.
                    for row in e["generation"]:
                        key = (row["action"], row["key"])
                        st.results[key] = {**st.results[key], "branch": row["branch"],
                                           "worktree": row["worktree"], "base": row["base"],
                                           "seq": e["seq"], "existing": False}
        elif kind == "base-change.waiting":
            st.base_generation_wait = e
        elif kind == "step.started" and e.get("kind") in ("caretaker", "caretaker-chat"):
            st.waiting.pop((e.get("ticket"), "caretaker"), None)
            st.caretakers[e.get("step")] = e
        elif kind == "caretaker.chat":
            st.caretaker_chats[e["step"]] = e
        elif kind == "caretaker.chat-action":
            st.caretaker_chat_actions[e["step"]] = e
        elif kind == "caretaker.chat-file":
            st.caretaker_chat_files[e["step"]] = e
        elif kind == "caretaker.decision":
            st.caretaker_decisions[e.get("problem")] = e
            st.stalled_since = ""
            st.last_activity_ts = _epoch(e.get("ts")) or st.last_activity_ts
            if (e.get("problem") or "").startswith("flow-error:"):
                st.flow_errors.pop(e["problem"].split(":", 1)[1], None)
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
            if (st.started and not st.base_change and st.initial_base.get("status") != "known"
                    and e.get("action") == "card-branch" and e.get("ok")
                    and e.get("existing") is False):
                intent = st.intents.get(("card-branch", e.get("key"))) or {}
                # Stary wynik zawiera SHA utworzonej gałęzi, zamiar — nazwę bazy.
                # Wynik „existing” nie dowodzi pierwotnego SHA, a dzisiejszy git niczego tu nie dopowiada.
                ref = intent.get("baseRef") or intent.get("base") or ""
                if ref == "HEAD" or (isinstance(ref, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", ref)):
                    ref = ""
                if isinstance(ref, str) and not ref.startswith("refs/"):
                    ref = "refs/heads/" + ref
                candidate = _base({"baseRef": ref, "baseBranch": ref[len("refs/heads/"):] if isinstance(ref, str) else "",
                                   "baseCommit": e.get("base")})
                if int(intent.get("seq") or 0) > int(st.started.get("seq") or 0):
                    st.base = candidate
                    st.initial_base = dict(candidate)
            st.results[(e.get("action"), e.get("key"))] = e
            st.tracker_retries.pop((e.get('action'), e.get('key')), None)
            if e.get("ok"):
                # Historyczne błędy bez etapu wygasają po udanym ponowieniu tej samej operacji.
                for stage, item in list(st.flow_errors.items()):
                    if item.get("action") == e.get("action"):
                        st.flow_errors.pop(stage, None)
            else:
                st.last_failed_action = e.get("action") or ""
            if e.get("action") == "plan-commit" and e.get("ok"):
                st.flow_errors.pop("plan-commit", None)
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
        elif kind == "step.note-added":
            st.step_notes[e["noteId"]] = e
        elif kind == "step.started":
            st.plan_resumes.pop(e.get("parentStep"), None)
            st.waiting.pop((e.get("ticket"), e.get("kind")), None)
            if e.get("ticket") and e.get("kind") in ("ticket", "fix"):
                # Krok autora ruszył — każde inne czekanie autora tego ticketu jest nieaktualne (05.11, K-37/PT-147:
                # poprawki szły na Codexie, a kafelek dalej pisał „czeka na limit” z wpisu sprzed przepięcia).
                for other in ("ticket", "fix"):
                    st.waiting.pop((e.get("ticket"), other), None)
            if e.get("kind") == "plan-review":
                # Review ruszyło, więc dawna blokada limitu nie obowiązuje (K-31: po nieudanym review karta
                # na zawsze pokazywała „czeka na limit” i chowała decyzję Ponów / Zmień wykonawcę).
                st.plan_review_wait = None
            st.steps[e.get("step")] = e
            if not (st.plan_approved and e.get("kind") in ("plan-amend", "plan-review", "plan-revise")):
                st.last_step = e.get("step") or ""
            st.moved_to = ""
            # Start kroku rozwiązuje błąd jego etapu, nie błędy równoległych ticketów.
            st.flow_errors.pop("advance", None)
            if e.get("ticket"):
                st.flow_errors.pop(f"advance:{e['ticket']}", None)
                st.flow_errors.pop("plan-next", None)
            if e.get("kind") == "close":
                st.flow_errors.pop("close", None)
        elif kind == "step.entered" and e.get("step") in {**st.steps, **st.caretakers}:
            steps = st.caretakers if e["step"] in st.caretakers else st.steps
            steps[e["step"]] = {**steps[e["step"]], "mode": "interactive",
                                   "deliveredNoteIds": list(dict.fromkeys([
                                       *steps[e["step"]].get("deliveredNoteIds", []),
                                       *e.get("deliveredNoteIds", [])])),
                                   "consoleNoteIds": e.get("deliveredNoteIds", []),
                                   "session": e.get("session") or "", "closeFile": e.get("closeFile") or "",
                                   "processFile": e.get("processFile") or "", "enteredAt": e.get("enteredAt") or 0,
                                   "consoleUnit": e.get("consoleUnit") or "",
                                   "source": e.get("source") or "", "taskFile": e.get("taskFile") or "",
                                   "readDirsProfile": bool(e.get("readDirsProfile") or
                                                           steps[e["step"]].get("readDirsProfile")),
                                   "consoleClosed": False, "consoleOpened": False}
        elif kind in ("step.console_opened", "step.console_closed") and e.get("step") in {**st.steps, **st.caretakers}:
            steps = st.caretakers if e["step"] in st.caretakers else st.steps
            field = "consoleOpened" if kind == "step.console_opened" else "consoleClosed"
            steps[e["step"]] = {**steps[e["step"]], field: True}
            if kind == "step.console_closed":
                steps[e["step"]]["deliveredNoteIds"] = [ident for ident in
                    steps[e["step"]].get("deliveredNoteIds", [])
                    if ident not in e.get("cancelledNoteIds", [])]
        elif kind == "step.transfer_requested":
            st.transfer_requests[e.get("step")] = e
        elif kind == "step.finished":
            st.finished[e.get("step")] = e
            # Domknięcie mogło zacząć się przed ręcznym odesłaniem karty do poprawy.
            # Jego późniejszy wynik nie jest nową pracą i nie cofa decyzji użytkownika.
            closing_after_rework = (st.moved_to == "todo" and
                                    (st.steps.get(e.get("step")) or {}).get("kind") == "close")
            if st.moved_to not in ("done", "archived") and not closing_after_rework:
                st.moved_to = ""
        elif kind == "review.verdict":
            source = st.steps.get(e.get("step")) or {}
            if e.get("step") not in st.superseded and (not st.base_change or
                    (source.get("baseVersion") == e.get("baseVersion") == st.base_version and
                     bool(source.get("reviewTip")) and source["reviewTip"] == e.get("reviewTip"))):
                st.verdicts[(e.get("ticket"), e.get("round"))] = e
        elif kind == "review.question":
            st.review_questions[(e.get("ticket"), e.get("round"))] = e
        elif kind == "review.decision":
            st.review_decisions[(e.get("ticket"), e.get("round"))] = e
        elif kind == "step.retry":
            st.step_retries[e.get("step")] = e
        elif kind == "step.decision":
            if e.get("step") not in st.superseded or e.get("step") in st.plan_resumes:
                st.step_decisions[e.get("step")] = e
        elif kind in (consent.GRANTED, consent.REVOKED):
            consent.apply(st, e)
        elif kind == "outside.decision-invalidated":
            current = st.step_decisions.get(e.get("step")) or {}
            if current.get("seq") == e.get("decisionSeq"):
                st.step_decisions.pop(e.get("step"), None)
        elif kind == "box.wait":
            st.box_wait = str(e.get("value") or "")
            st.box_env_deferred = not st.box_wait and e.get("deferred") == "environment"
        elif kind == "permission.rechecked":
            if e.get("step") in st.finished:
                st.finished[e["step"]] = {**st.finished[e["step"]], "canAllow": bool(e.get("canAllow"))}
        elif kind == "outside.started":
            st.outside_launched.add(e.get("step"))
            st.outside_started[e.get("step")] = e
        elif kind == "outside.finished":
            st.outside_started.pop(e.get("step"), None)
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
            if e.get("appendRevision"):
                st.plan_revision = {**e["appendRevision"], "seq": e["seq"]}
                st.plan_reviewer = st.plan_revision["reviewer"]
        elif kind == "plan.reviewer-selected":
            st.plan_reviewer = e.get("executor") or ""
        elif kind == 'plan.approval_requested':
            st.approval_requested = e
        elif kind == "plan.returned":
            st.plan_revision = e
            st.closed = None
            st.plan_returns.append(e)
            target = e.get("caretakerFrom") or ""
            if e.get("caretaker") and st.plan_approved and (st.steps.get(target) or {}).get("ticket"):
                # PT-155: problem ticketu przejmuje poprawa planu. Sam wynik kroku i jego kartka zostają;
                # dawny błąd/pytanie nie wraca ani nie uruchamia kolejnego opiekuna, także po restarcie.
                st.superseded.add(target)
                st.plan_resumes[target] = None
            if "caretaker" not in e:
                st.plan_user_seq = int(e.get("seq") or 0)
        elif kind == "plan.revision-discarded":
            if (st.plan_revision or {}).get("kind") == "append":
                st.plan_proposed = st.plan_revision["previousProposal"]
                st.plan_reviewed = st.plan_revision["previousReview"]
                st.plan_reviewer = ""
                st.plan_review_wait = None
            st.plan_revision = None
            st.plan_checked = None
            st.plan_user_seq = int(e.get("seq") or 0)
            # Użytkownik zachował dotychczasowy plan. Nie przywracamy starego błędu ticketu ani
            # nie kasujemy blokerów z wcześniejszej zatwierdzonej różnicy, jeśli czekają na scalenie.
            for target, blockers in st.plan_resumes.items():
                if blockers is None:
                    st.plan_resumes[target] = set()
        elif kind == "plan.revision-failed":
            st.plan_revision = e
        elif kind == "plan.revision-ready":
            st.plan_revision = e
        elif kind == "plan.revision-approved":
            before = {r["id"] for r in (st.plan_approved or {}).get("rows", [])}
            if st.plan_approved:
                st.plan_artifact_source = st.plan_proposed or {}
            st.plan_approved = {**st.plan_approved, **e["plan"],
                                "textApprovedSeq": e.get("seq", 0)} if st.plan_approved else None
            rows = (st.plan_approved or {}).get("rows", [])
            present = {r["id"] for r in rows}
            added = present - before
            if (st.plan_revision or {}).get("kind") == "append":
                # Ręcznie dopisany ticket nie rozszerza blokerów rozpoczętej pracy (PT-248).
                added = set()
            # Rozpoczęte kartki są nietykalne. Oczekiwania wznowień i jawne blokery tworzą wspólny graf;
            # samo sprawdzenie kartek nie wykrywa cyklu między dwiema kolejnymi poprawkami (PT-155).
            graph = {r["id"]: set(r.get("blockers") or []) for r in rows}
            resumes = {target: set() for target in st.plan_resumes}
            # Najpierw zachowujemy wcześniejsze oczekiwania, dopiero potem dokładamy nową pracę.
            # Kolejność kroków z dziennika i sortowanie blokerów zapewniają ten sam wynik po restarcie.
            for previous in (True, False):
                for target, blockers in st.plan_resumes.items():
                    ticket = st.steps[target]["ticket"]
                    candidates = ((blockers or set()) if previous else added) & present
                    for blocker in sorted(candidates):
                        pending, seen = {blocker}, set()
                        while pending:
                            node = pending.pop()
                            if node in seen:
                                continue
                            seen.add(node)
                            pending.update(graph.get(node, set()) - seen)
                        if ticket not in seen:
                            graph.setdefault(ticket, set()).add(blocker)
                            resumes[target].add(blocker)
            st.plan_resumes = resumes
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
            st.plan_artifact_source = st.plan_proposed or {}
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
        elif kind == "author.reassigned":
            st.author_overrides[e.get("ticket")] = e
        elif kind == "flow.error":
            stage, error = e.get("stage") or "advance", e.get("error") or ""
            st.flow_errors[stage] = {"event": e, "persisted": False,
                                     "repeated": (stage, error) in st.flow_error_texts,
                                     "action": st.last_failed_action if not e.get("stage") else ""}
            st.flow_error_texts.add((stage, error))
        elif kind == "flow.error-persisted":
            item = st.flow_errors.get(e.get("stage") or "advance")
            if item and e.get("error") == item["event"].get("error"):
                item["persisted"] = True
        elif kind == "flow.error-cleared":
            # Stary zapis bez etapu dotyczył jedynego bieżącego błędu.
            stage = e.get("stage") or st.flow_error_event.get("stage") or "advance"
            item = st.flow_errors.get(stage)
            if item and e.get("error") == item["event"].get("error"):
                st.flow_errors.pop(stage, None)
        elif kind == "card.paused":
            st.paused = True
        elif kind == "card.resumed":
            st.paused = False
        elif kind == "card.finished":
            st.card_finished = True
        elif kind == "card.closed":
            if st.moved_to != "todo" and e.get("step") not in st.superseded and (not st.base_change or
                    e.get("baseVersion") == st.base_version and e.get("baseCommit") == st.base.get("baseCommit")):
                st.closed = e
        elif kind == "card.main-merged":
            st.main_merged = e
            if st.merged is None:
                st.merged = merge_of(e)
            st.merge_waiting = None
        elif kind == "card.merged":
            if st.merged is None:
                st.merged = merge_of(e)
            st.merge_waiting = None
        elif kind == "main-merge.waiting":
            st.merge_waiting = e
        elif kind == "main-merge.resumed":
            st.merge_waiting = None
        elif kind == "main-merge.abandoned":
            st.abandoned_main_merges.add(e.get("key"))
            if (st.merge_waiting or {}).get("key") == e.get("key"):
                st.merge_waiting = None
        elif kind == "card.moved":
            st.store_column = e.get("to") or ""
            st.store_move_id = e.get("moveId") or ""
            st.lifted = e.get("from") == "backlog" and e.get("to") == "todo"
            # „Do poprawy” to przeniesienie z kolumny wyliczonej (Do sprawdzenia, Czeka na mnie); z Backlogu do Do
            # zrobienia to wznowienie albo kolejka po zleceniu z popupu — karta pokazuje dalej stan z dziennika.
            if e.get("from") not in ("backlog", "todo"):
                st.moved_to = e.get("to") or ""
                if st.moved_to == "todo":
                    st.closed = None
            if (e.get("to") or "") != "backlog":
                st.paused = False
        elif kind == "card.move-ignored":
            # PT-313: zbędny ruch okna do „Do zrobienia” po zdjęciu z Backlogu; zużywa zdjęcie, nie zmienia stanu.
            st.store_move_id = e.get("moveId") or st.store_move_id
            st.lifted = False
        elif kind == "ticket.rejected":
            st.rejected.add(e.get("ticket"))
        elif kind == "ticket.imported":
            if e.get("state") == "gotowe":
                st.imported_done.add(e.get("ticket"))
            elif e.get("state") == "odrzucone":
                st.rejected.add(e.get("ticket"))
        # Praca opiekuna nie zmienia planu ticketów: nowy klucz commita dawałby pozorną aktywność
        # w kolejnym przebiegu i unieważniał własny problem `stalled:card` (PT-147, review 3).
        if st.plan_approved is not None and not _bookkeeping(st, e) and (kind in (
                "plan.approved", "plan.ticket-edited", "plan.revision-approved", "ticket.assigned",
                "step.started", "step.finished", "step.waiting", "review.verdict",
                "review.question", "review.decision", "ticket.rejected", "ticket.imported") or
                (kind == "result" and e.get("action") == "merge")):
            st.plan_change_seq = int(e.get("seq") or 0)
            st.plan_change_ts = e.get("ts") or ""
    st.process_identities = process_identities(events)
    # Widok operacyjny może nadpisywać ostatni wpis. Tożsamość i ochrona
    # pochodzą zawsze z całej historii, również przy tolerancyjnym odczycie UI.
    for steps in (st.steps, st.caretakers):
        for name, step in steps.items():
            identity = st.process_identities.get(name)
            if identity is not None:
                steps[name] = {**step, **{k: identity.data[k] for k in START_FIELDS if k in identity.data},
                               "_identityHeld": identity.held}
    for key, intent in st.intents.items():
        identity = st.process_identities.get(key[1]) if key[0] == "step-start" else None
        if identity is not None:
            st.intents[key] = {**intent, **{k: identity.data[k] for k in START_FIELDS if k in identity.data},
                               "_identityHeld": identity.held}
    return st


def _base(data: dict) -> dict:
    ref, branch, commit = (data.get(k) for k in ("baseRef", "baseBranch", "baseCommit"))
    prefix = "refs/remotes/" if isinstance(ref, str) and ref.startswith("refs/remotes/") else "refs/heads/"
    valid = (isinstance(ref, str) and ref.startswith(prefix) and branch == ref[len(prefix):]
             and bool(branch) and not re.search(r"[\s~^:?*\[\\]|\.\.|@\{|//", ref)
             and not ref.endswith(("/", ".", ".lock"))
             and isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit))
    return {"status": "known" if valid else "unknown", "baseRef": ref if valid else "",
            "baseBranch": branch if valid else "", "baseCommit": commit if valid else ""}


def merge_of(event: dict) -> dict:
    """Odczyt zapisanego celu bez zgadywania dzisiejszej głównej i bez migracji dziennika."""
    branch = event.get("targetBranch") or event.get("main") or ""
    return {**event, "targetBranch": branch,
            "targetRef": event.get("targetRef") or ("refs/heads/" + branch if branch else ""),
            "resultCommit": event.get("resultCommit") or event.get("commit") or ""}


def base_of(st: FlowState) -> dict:
    return dict(st.base or _base({}))


def _valid_generation(st, event, base):
    """Nieprawidłowa/częściowa mapa nie zmienia ani bazy, ani jednego ticketu."""
    if "generation" not in event:
        return True  # kontrakt historycznych zdarzeń PT-194
    rows = event["generation"]
    intent = st.intents.get(("change-base", event.get("operationId"))) or {}
    if (event.get("validationRequired") is not True or intent.get("generation") != rows or
            intent.get("requestSeq") != (st.started or {}).get("seq") or
            intent.get("previousBaseVersion") != st.base_version or
            intent.get("baseVersion") != event.get("baseVersion") or
            intent.get("newBase") != {k: base[k] for k in ("baseRef", "baseBranch", "baseCommit")}):
        return False
    expected = {key for key, res in st.results.items() if res.get("ok") and
                key[0] in ("card-branch", "ticket-branch", "review-branch", "step-reopen")}
    if not isinstance(rows, list) or not rows or not expected:
        return False
    mapped = set()
    resources = {}
    old_branches = {st.results[k].get("branch") for k in expected}
    old_paths = {st.results[k].get("worktree") for k in expected}
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k]
                for k in ("action", "key", "branch", "worktree", "base", "oldBranch", "oldWorktree", "oldCommit")):
            return False
        key = (row["action"], row["key"])
        old = st.results.get(key) or {}
        if (key in mapped or key not in expected or row["oldBranch"] != old.get("branch") or
                row["oldWorktree"] != old.get("worktree") or row["oldCommit"] != old.get("base") or
                row["base"] != base["baseCommit"] or row["branch"] in old_branches or
                row["worktree"] in old_paths):
            return False
        resource = (row["branch"], row["worktree"])
        source = (row["oldBranch"], row["oldWorktree"])
        if resource in resources and resources[resource] != source:
            return False
        if any(resource != target and (resource[0] == target[0] or resource[1] == target[1] or source == origin)
               for target, origin in resources.items()):
            return False
        resources[resource] = source
        mapped.add(key)
    return mapped == expected


# PT-147: zdarzenia, które nie są pracą karty — nie przesuwają `last_activity_ts` i nie zdejmują „stoi”.
BOOKKEEPING = ("flow.error-persisted", "stage.retry")


def _bookkeeping(st: FlowState, e: dict) -> bool:
    """Zdarzenie opiekuna karty (`caretaker.*`, krok i czekanie rodzaju `caretaker`, para `step-start` jego kroku,
    para `caretaker-action`), znacznik utrzymującego się błędu Tide i zapis ponowienia etapu. Bez tego praca opiekuna nad kartą, która stoi, sama zdejmowałaby „stoi”, a problem
    `stalled:card` znikałby, zanim opiekun skończy (PT-147). Skutki akcji opiekuna (np. `step.started` po
    ponowieniu etapu) są zwykłą pracą."""
    kind = e.get("type") or ""
    if kind == "flow.notice" and (e.get("key") or "").startswith("caretaker:"):
        return True
    if kind.startswith("caretaker.") or kind in BOOKKEEPING or e.get("kind") in ("caretaker", "caretaker-chat"):
        return True
    if kind in ("intent", "result"):
        return e.get("action") == "caretaker-action" or (
            e.get("action") == "step-start" and e.get("key") in st.caretaker_runs)
    return bool(e.get("step")) and (e.get("step") in st.caretaker_runs or e.get("step") in st.caretaker_chats)


def ticket_of(st: FlowState, ticket_id: str = "") -> dict:
    """Ticket i jego przypięta baza oraz gałąź karty z wyników akcji."""
    out = {"id": "", "title": "", "branch": "", "worktree": "", "base": "", "cardBranch": "", "cardWorktree": ""}
    wanted = ticket_id or (st.steps.get(st.last_step) or {}).get("ticket") or st.base_restart_ticket
    for (action, _key), res in st.results.items():
        if not res.get("ok"):
            continue
        if action == "reserve" and (not wanted or res.get("ticket") == wanted):
            out["id"], out["title"] = res.get("ticket") or "", res.get("title") or ""
        elif action == "card-branch":
            out["cardBranch"], out["cardWorktree"] = res.get("branch") or "", res.get("worktree") or ""
        elif action == "ticket-branch" and res.get("ticket") == (wanted or out["id"]):
            out["branch"], out["worktree"] = res.get("branch") or "", res.get("worktree") or ""
            out["base"] = res.get("base") or ""
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
    from .caretaker import hidden
    latest = {}
    for name, item in st.steps.items():
        latest[item.get("ticket")] = name
    for name in latest.values():
        item, done = st.steps[name], st.finished.get(name) or {}
        if item.get("ticket") in st.rejected or name in st.step_decisions or name in st.superseded or \
                hidden(st, name):      # opiekun czekającej decyzji tylko doradza — decyzja pozostaje widoczna
            continue
        if user_only and _limit_wait_only(st, name):
            continue
        if done.get("result") in ("question", "denied") or (done.get("result") == "error" and
                (int(item.get("attempt") or 0) >= 1 or done.get("reason") == "limit" or _handed_repeat(st, name))) or (
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


def outside_queued(st: FlowState) -> list[str]:
    """Kroki z decyzją „uruchom poza piaskownicą”, których polecenie jeszcze nie ruszyło (czeka na miejsce)."""
    resumed = {s.get("parentStep") for s in st.steps.values() if s.get("parentStep")}
    queued = []
    for name, done in st.finished.items():
        decision = st.step_decisions.get(name) or {}
        if (done.get("result") == "denied" and done.get("outside") and name not in resumed
                and decision.get("choice") in ("run", "run-always", "run-ticket")
                and name not in st.outside_started):
            queued.append(name)
    return queued


PHASE_LABELS = {"pracuje": "pracuje", "kolejka": "w kolejce", "ty": "czeka na Ciebie", "review": "review",
                "todo": "nie ruszył", "gotowe": "gotowe", "odrzucone": "odrzucony"}


def ticket_phase(st: FlowState, ticket: str, latest: dict, waits: list, queued_outside: list) -> tuple[str, str]:
    """Stan ticketu dla pasków i kartek (05.11): kto ma ruch.

    `pracuje` — agent albo jego polecenie poza piaskownicą działa; `kolejka` — czeka na zasób i ruszy sam
    (miejsce poza piaskownicą, limit lub miejsce dostawcy, inna karta, decyzja już podjęta); `ty` — bez użytkownika
    nie ruszy (pytanie, zgoda, decyzja po błędzie); `review`; `todo`. Zwraca (stan, krótki powód)."""
    name = latest.get("step") or ""
    if not latest:
        return "todo", ""
    command = lambda: str((st.finished.get(name) or {}).get("command") or "polecenie").split("\n")[0][:60]
    if name in st.outside_started:
        return "pracuje", "poza piaskownicą: " + command()
    if name in queued_outside:
        return "kolejka", "w kolejce poza piaskownicą: " + command()
    if waits:
        wait = waits[-1]
        if wait.get("cause") == "quota" or "limit" in str(wait.get("reason") or ""):
            return "kolejka", str(wait.get("reason") or "limit dostawcy")
        return "kolejka", str(wait.get("reason") or "czeka na zasób")
    done = st.finished.get(name)
    if done is None:
        kind = latest.get("kind") or "ticket"
        return ("review" if kind == "review" else "pracuje"), (latest.get("executor") or "") + (
            " · review" if kind == "review" else " · poprawki" if kind == "fix" else
            " · konflikt" if kind == "conflict" else "")
    result = done.get("result")
    if result in ("question", "denied", "error", "stopped"):
        decision = st.step_decisions.get(name)
        if decision:
            if decision.get("waitCard"):
                return "kolejka", "czeka na kartę " + str(decision.get("waitCard"))
            if decision.get("waitTicket"):
                return "kolejka", "czeka na " + str(decision.get("waitTicket"))
            return "kolejka", "decyzja podjęta, wznowi się sam"
        if result == "stopped" and done.get("reason") == "interactive-closed":
            return "ty", "Konsola zamknięta: wznów w tle albo w Konsoli"
        return "ty", {"question": "pytanie", "denied": "prośba o zgodę", "error": "błąd kroku — decyzja",
                      "stopped": "krok zatrzymany — decyzja"}[result]
    if latest.get("kind") == "review" or result == "done":
        return "review", "po review" if latest.get("kind") == "review" else "czeka na review"
    return "pracuje", ""


def card_waits(st: FlowState) -> dict[str, str]:
    """Tickety, których krok czeka na domknięcie innej karty (decyzja „wait-card”): ticket → karta."""
    resumed = {s.get("parentStep") for s in st.steps.values() if s.get("parentStep")}
    return {(st.steps.get(name) or {}).get("ticket") or "": d["waitCard"] for name, d in st.step_decisions.items()
            if d.get("waitCard") and name not in resumed and (st.steps.get(name) or {}).get("ticket") not in st.rejected}


def ticket_waits(st: FlowState) -> dict[str, str]:
    """Tickety, których krok czeka na scalenie innego ticketu karty (decyzja „wait-ticket”): ticket → ticket.
    Cel już scalony albo odrzucony nie jest czekaniem — następny tick wznawia krok."""
    resumed = {s.get("parentStep") for s in st.steps.values() if s.get("parentStep")}
    out = {}
    for name, d in st.step_decisions.items():
        target, ticket = d.get("waitTicket"), (st.steps.get(name) or {}).get("ticket") or ""
        if not target or name in resumed or ticket in st.rejected or name in st.superseded:
            continue
        if target in st.rejected or target in st.imported_done or (st.results.get(("merge", target)) or {}).get("ok"):
            continue
        out[ticket] = target
    return out


def guard_reason(st: FlowState) -> str | None:
    """None = praca lub koniec; napis = nazwany powód czekania; pusty napis = możliwy postój."""
    if not st.started or st.card_finished or st.paused or st.running() or st.open_intents() or st.open_consoles():
        return None
    if st.base_validation_required or st.base_generation_wait:
        return "generacja czeka na walidację kontekstu albo odzyskanie zasobów"
    if st.outside_started:
        return None     # trwa polecenie poza piaskownicą — to praca, nie postój (K-31, 03.11: fałszywy alarm)
    waits = card_waits(st)
    if waits:
        return "; ".join(f"{ticket} czeka na kartę {other}" for ticket, other in waits.items())
    if ticket_waits(st) and not attention_step(st, True):
        return "; ".join(f"{ticket} czeka na {other}" for ticket, other in ticket_waits(st).items())
    if outside_queued(st):
        # Zgoda dana, a polecenie czeka na wolne miejsce (jedno na projekt) — K-37, 04.11: fałszywy „postój”.
        return "polecenie poza piaskownicą w kolejce"
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


def _handed_repeat(st: FlowState, name: str) -> bool:
    """Ten sam powód drugi raz, problem oddany przez opiekuna (PT-148) — decyzja błędu także przy próbie zerowej."""
    from .caretaker import handed_repeat
    return handed_repeat(st, name)


def decision_step(st: FlowState, user_only: bool = False) -> str:
    """Krok planu czekający na decyzję ma pierwszeństwo nad równoległym ticketem."""
    for name, step in reversed(list(st.steps.items())):
        if step.get("kind") not in ("plan-amend", "plan-review", "plan-revise") or name in st.superseded:
            continue
        done = st.finished.get(name) or {}
        if done.get("result") not in ("question", "denied", "error"):
            continue
        if done.get("result") == "error" and int(step.get("attempt") or 0) == 0 and \
                done.get("reason") not in ("limit", "login") and not _handed_repeat(st, name):
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
    if column in ("done", "archived") and not st.merged and not (st.merge_waiting and not st.card_finished) and not (
            st.base_validation_required or st.base_generation_wait):
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
                        "base": base_of(st), "initialBase": dict(st.initial_base), "baseVersion": st.base_version,
                        "stalledSince": st.stalled_since,
                        "closed": bool(st.closed), "summary": (st.closed or {}).get("summary") or "",
                        "mainMerged": bool(st.merged),  # zgodność dawnych klientów
                        "merged": bool(st.merged), "merge": dict(st.merged or {}),
                        "waiting": [{"ticket": ticket, "kind": kind, "reason": item["reason"]}
                                    for (ticket, kind), item in st.waiting.items()]})
    out["flow"]["baseDecisionPending"] = bool(pending(st))
    out["flow"].update(baseValidationRequired=st.base_validation_required,
                       approvals=list(st.verdicts.values()),
                       historicalApprovals=[e for e in st.log if e.get("type") == "review.verdict" and
                                            e.get("seq", 0) < st.base_change.get("seq", 0)],
                       generation=(st.base_change or {}).get("generation") or [],
                       generationWaiting=st.base_generation_wait,
                       preservedGenerations=[i for i in st.intents.values() if i.get("action") == "change-base"])
    from .caretaker import view as caretaker_view
    out["flow"]["caretaker"] = caretaker_view(st)
    # PT-147: opiekun pracuje nad kartą, która stoi (albo ma błąd Tide) — znacznik opiekuna zamiast „stoi”.
    caring = out["flow"]["caretaker"]["working"] if not attention else []
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
         "historical": name in st.superseded,
         "baseVersion": item.get("baseVersion", 0),
         "canTransfer": name not in st.superseded and (name not in st.finished or
                         ((st.finished.get(name) or {}).get("reason") == "transfer" and
                          name not in st.step_decisions)) and
                        bool(item.get("provider")) and not st.card_finished,
         "canEnter": name not in st.superseded and not any(other.get("worktree") == item.get("worktree") and
                             other.get("step") != name
                             for other in st.running()) and
                     not any(other.get("worktree") == item.get("worktree") and other.get("mode") == "interactive"
                             and not other.get("consoleClosed") for other in st.steps.values())}
        for name, item in st.steps.items()
    ]
    if st.base_validation_required or st.base_generation_wait:
        if st.started.get("entry") in ("plan", "planning"):
            _plan_view(st, out)
        out["flow"]["decisions"] = []
        for item in out["flow"]["steps"]:
            item["canEnter"] = item["canTransfer"] = False
        return _fill(out, "waiting", st.base_generation_wait.get("reason") or
                     "Nowa generacja czeka na walidację kontekstu.")
    if st.merge_waiting and not st.card_finished:
        return _fill(out, "waiting", "Scalenie czeka: " + st.merge_waiting["reason"])
    plan_entry = st.started.get("entry") in ("plan", "planning")
    if plan_entry:
        rows, finished_ids = _plan_view(st, out)
    if st.merged:
        out["flow"]["decisions"] = []
        for item in out["flow"]["steps"]:
            item["canTransfer"] = False
        out["done"] = out["total"]
        target = st.merged["targetBranch"]
        return _fill(out, column if column in ("done", "archived") else "done",
                     "Karta scalona" + (f" do {target}" if target else ""))
    if pending(st):
        for item in out["flow"]["steps"]:
            item["canEnter"] = False
            item["canTransfer"] = False
        return _fill(out, "waiting", "Decyzja o bazie karty; wyniki pozostają niescalone")
    caretaker_decision = next((d for d in decisions if d["kind"] == "caretaker"), None)
    if caretaker_decision:
        # Decyzja samej karty nie ma kroku dla attention_step; inne tickety mogą nadal pracować.
        out["question"] = caretaker_decision["why"]
        note = "stoi — nie ma pracującego kroku ani nazwanego powodu czekania"
        if caretaker_decision["technical"]["problem"].startswith("flow-error:"):
            error = caretaker_decision["happened"]
            out["flow"]["flowError"] = error
            out["flow"]["flowErrorHint"] = flow_error_hint(error)
            note = f"{ident}: {flow_error_text(error)}"
        # Jawna pauza i ręczny Backlog mają pierwszeństwo przed kolumną oczekującej decyzji.
        if st.paused:
            return _fill(out, "backlog" if column == "backlog" else "progress",
                         "wstrzymana — trwające kroki kończą się" if st.running() else "wstrzymana")
        if column == "backlog":
            return None
        return _fill(out, "waiting", note)
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
            if caring:
                return _fill(out, "progress", caring[0]["label"])
            return _fill(out, "waiting", "stoi — nie ma pracującego kroku ani nazwanego powodu czekania")
        focus = decision_step(st)
        focus_step = st.steps.get(focus) or {}
        focus_done = st.finished.get(focus) or {}
        if focus_step.get("kind") in ("plan-amend", "plan-review", "plan-revise") and focus_done.get("result") in ("question", "denied", "error") and focus not in st.step_decisions and (focus_done.get("result") != "error" or int(focus_step.get("attempt") or 0) > 0 or focus_done.get("reason") == "login" or _handed_repeat(st, focus)):
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
        # Poprawa albo review planu po odesłaniu trwa w tle — kafelek nie pokazuje starego planu jako decyzji
        # (K-38, 03.11: po „Odeślij uwagi” karta dalej stała w „Czeka na mnie” jako „plan do zatwierdzenia”).
        plan_work = [s.get("kind") for s in st.running() if s.get("kind") in PLAN_WORK_KINDS]
        revision = st.plan_revision or {}
        if plan_work or (revision and not revision.get("pending") and not revision.get("failed")):
            label = "review planu w tle" if plan_work and plan_work[0] == "plan-review" else "poprawa planu w tle"
            return _fill(out, "progress", label)
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
        waits = {**ticket_waits(st), **card_waits(st)}
        if waits and not st.running() and not attention:
            return _fill(out, "progress", out["flow"]["waveLabel"] + " · " +
                         ", ".join(f"{ticket} czeka na {other}" for ticket, other in waits.items()))
        outside = [n for n in st.outside_started] or outside_queued(st)
        if outside and not st.running() and not attention:
            # Polecenie poza piaskownicą innego ticketu niż ostatni krok to praca, nie „do sprawdzenia” (04.11, K-46:
            # kafelek w „Do sprawdzenia” z notką o review KAMP-29, gdy KAMP-35 puszczał testy, a KAMP-33 czekał).
            tickets = ", ".join(dict.fromkeys(st.steps.get(n, {}).get("ticket") or "" for n in outside))
            what = "polecenie poza piaskownicą" if st.outside_started else "polecenie poza piaskownicą w kolejce"
            return _fill(out, "progress", out["flow"]["waveLabel"] + " · " + (f"{tickets}: " if tickets else "") + what)
        if st.waiting and not st.running() and not attention:
            return _fill(out, "progress", out["flow"]["waveLabel"] + " · " + next(reversed(st.waiting.values()))["reason"])
        if st.plan_approved is not None and not st.last_step and not st.open_intents():
            return _fill(out, "progress", "uruchamiam pierwszy ticket planu")
    if st.closed and not st.plan_approved:
        out["done"] = 1
        return _fill(out, "review", f"✓ {ident} na gałęzi karty — do sprawdzenia")
    if card_waits(st) and not st.running():
        return _fill(out, "progress", f"{ident} czeka na kartę {next(iter(card_waits(st).values()))}")
    if st.stalled_since:
        if caring:
            return _fill(out, "progress", caring[0]["label"])
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
    if caring:
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
                return _fill(out, "progress", f"{ident} · w kolejce poza piaskownicą: "
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
                    done.get("reason") != "login" and not _handed_repeat(st, st.last_step):
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
    queued_outside = outside_queued(st)
    for row in rows:
        ident_row = row.get("id") or row.get("workId")
        latest = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == ident_row), {})
        waits = [w for (ticket_id, _), w in st.waiting.items() if ticket_id == ident_row]
        outcome = (st.finished.get(latest.get("step")) or {}).get("result")
        phase, why = ticket_phase(st, ident_row, latest, waits, queued_outside)
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
                               "waitReason": waits[-1]["reason"] if waits else "",
                               "phase": "gotowe" if state == "gotowe" else "odrzucone" if state == "odrzucone"
                               else phase, "why": "" if state in ("gotowe", "odrzucone") else why})
    out["tickets"].sort(key=lambda ticket: ticket["wave"])
    out["flow"]["waveLabel"] = f"fala {wave_no} z {len(waves)}"
    from . import planappend
    out["flow"]["plan"] = {"review": plan_review_view(st.plan_reviewed), "revision": st.plan_revision or {},
                           "fingerprint": planappend.fingerprint(st), "canAppend": planappend.available(st),
                           "planner": (proposal.get("reviewAuthor") if revision_pending else "") or proposal.get("planner") or "",
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
                           # Projekty zlecenia ze źródłem numerów (K-33 #4); pusta lista — jeden projekt.
                           "projects": proposal.get("projects") or [],
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
