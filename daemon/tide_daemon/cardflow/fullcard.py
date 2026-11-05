"""Pola pełnej karty (K-35, PT-128) liczone z dziennika obok `derived`: fazy, powody czekania, ludzkie stany ticketów,
trwające kroki i jedno zdanie, gdy nic nie czeka na użytkownika. Widok `CardFullView.qml` tylko je wyświetla.

- `derived.phases[7]` — `{id, label, state, executor, ts, note}` w stałej kolejności `PHASES`; `state` z `STATES`:
  `done` (zrobione), `now` (trwa), `you` (czeka na decyzję użytkownika), `hold` (czeka, ale nie na użytkownika — limit),
  `todo` (jeszcze nie), `skipped` (nie dotyczy, np. planowanie przy wejściu „Od razu”); `executor` to wykonawca
  (nazwa modelu), `user` dla faz użytkownika albo pusty napis; `ts` — chwila zdarzenia fazy (ISO 8601) albo pusty
  napis; `note` — krótki dopisek (np. „fala 2 z 4”, „prosi o zmiany”).
- `derived.tickets[].waitingOn` — `{kind: user|blocker|limit, ticket?, until?}` albo None: najważniejszy powód
  czekania ticketu, priorytet użytkownik > bloker > limit (`blocker` niesie `ticket` — pierwszy niescalony bloker,
  `limit` — `until`, godzinę odnowienia, gdy jest znana),
- `derived.tickets[].status` i `statusLabel` — ludzki stan ticketu (`TICKET_STATUS`), `executor` i `reviewer`,
- `derived.flow.running[]` — tylko trwające kroki (`{id, ticket, kind, kindLabel, executor, mode, startedTs,
  lastActivityTs, canEnter}`),
- `derived.flow.history[]` — historia ludzkim językiem (`history.py`, PT-129),
- `derived.flow.headline` — jedno zdanie do bloku „Twój ruch”, gdy `flow.decisions[]` jest puste,
- `derived.flow.stoppedByLimit[]` — `{step, ticket, wipFiles, provider, until}`: kroki zatrzymane przez limit dostawcy
  (PT-130, „Teraz w tle”); `wipFiles` to liczba plików WIP do przekazania. Limit nie jest decyzją (D3): karta stoi
  w „W toku”, a Tide wznawia krok po odnowieniu limitu,
- `derived.flow.otherProviders[]` — `{provider, remaining}`: pozostały limit (%) innych dostawców z odczytu Limitów AI
  (dokłada silnik; pusty bez zatrzymanych kroków albo bez odczytu),
- `derived.flow.limitTransfer` — `{available, provider, label, text, executor, cost, reason}` albo None: akcja
  „Przenieś na Codex” (`text` to napis przycisku, `executor` to wykonawca dla istniejącej ścieżki przekazania kroku, `cost` — koszt przeniesienia: liczba
  kroków i plików WIP oraz pozostały limit docelowego dostawcy; `reason` — jedno zdanie, gdy akcja jest ukryta).
"""
from __future__ import annotations

from . import history
from .labels import kind_label
from .state import FlowState

PHASES = (("order", "Zlecenie"), ("planning", "Planowanie"), ("plan-review", "Review planu"),
          ("plan-approval", "Zatwierdzenie planu"), ("tickets", "Tickety"), ("closing", "Domknięcie"),
          ("main-merge", "Scalenie"))
STATES = ("done", "now", "you", "hold", "todo", "skipped")
USER = "user"
PLAN_ENTRIES = ("plan", "planning")
WAIT_PRIORITY = ("user", "blocker", "limit")

TICKET_STATUS = {"planned": "w planie", "queued": "w kolejce", "running": "pracuje", "review": "review",
                 "fix": "poprawki", "merging": "scalanie", "you": "czeka na Ciebie", "limit": "czeka na limit",
                 "blocked": "czeka na bloker", "waiting": "czeka", "merged": "scalony", "rejected": "odrzucony"}


def enrich(st: FlowState, out: dict) -> dict:
    """Dokłada pola pełnej karty do gotowego `derived` (ten sam słownik)."""
    flow = out["flow"]
    decisions = flow.get("decisions") or []
    rows = {(r.get("id") or r.get("workId")): r for r in (flow.get("plan") or {}).get("rows") or []}
    merged = {t["id"] for t in out["tickets"] if t.get("state") == "gotowe"}
    for ticket in out["tickets"]:
        row = rows.get(ticket["id"]) or {}
        assigned = st.assignments.get(ticket["id"]) or {}
        override = st.reviewer_overrides.get(ticket["id"]) or {}
        ticket["waitingOn"] = waiting_on(st, ticket["id"], row, decisions, merged)
        ticket["executor"] = assigned.get("executor") or row.get("executor") or ""
        ticket["reviewer"] = override.get("reviewer") or assigned.get("reviewer") or row.get("reviewer") or ""
        ticket.update(ticket_status(st, ticket))
    flow["running"] = running_steps(flow.get("steps") or [], st)
    flow["history"] = history.build(st, flow.get("steps") or [])
    # Skutek ręcznego zakończenia (Gotowe / Archiwum → `_complete_manual_finish`): zatrzymane kroki i odrzucone tickety.
    flow["finish"] = {"steps": len(flow["running"]),
                      "tickets": sum(1 for t in out["tickets"] if t.get("status") not in ("merged", "rejected"))}
    flow["stoppedByLimit"] = stopped_by_limit(st)
    flow["otherProviders"] = []
    flow["limitTransfer"] = None
    out["phases"] = phases(st, out)
    flow["headline"] = headline(st, out)
    return out


# --- fazy ----------------------------------------------------------------------------------------

def decision_phase(decision: dict, approved: bool) -> str:
    """Faza, do której należy decyzja; pusty napis — bieżąca faza (np. błąd YouTracka)."""
    kind, step_kind = decision.get("kind") or "", decision.get("stepKind") or ""
    if kind == "plan-approval":
        return "plan-approval"
    if kind in ("plan-revision", "plan-revision-failed"):
        return "tickets" if approved else "plan-approval"
    if kind == "plan-reviewer":
        return "plan-review"
    if kind == "tracker":
        return ""
    if step_kind in ("planning", "plan-fix", "plan-import"):
        return "planning"
    if step_kind == "plan-review":
        return "plan-review"
    if step_kind in ("plan-revise", "plan-amend"):
        return "tickets" if approved else "plan-approval"
    if step_kind == "close":
        return "closing"
    return "tickets"


def phases(st: FlowState, out: dict) -> list[dict]:
    started = st.started or {}
    plan = started.get("entry") in PLAN_ENTRIES
    approved = st.plan_approved is not None
    steps = list(st.steps.values())
    running = {s.get("step") for s in st.running()}
    flow = out["flow"]
    by_phase: dict[str, list[dict]] = {}
    for decision in flow.get("decisions") or []:
        by_phase.setdefault(decision_phase(decision, approved), []).append(decision)
    held = {decision_phase(stop, approved) for stop in limit_stops(st)}

    def asked(phase_id: str) -> str:
        """`you` (decyzja użytkownika), `hold` (krok stoi na limicie — to nie decyzja, D3) albo pusty napis."""
        if by_phase.get(phase_id):
            return "you"
        return "hold" if phase_id in held else ""

    def last(kinds) -> dict:
        return next((s for s in reversed(steps) if s.get("kind") in kinds), {})

    def phase(ident, state, executor="", ts="", note="") -> dict:
        return {"id": ident, "label": dict(PHASES)[ident], "state": state, "executor": executor or "",
                "ts": ts or "", "note": note or ""}

    result = [phase("order", "done", USER, started.get("ts"))]

    # Planowanie, review i zatwierdzenie planu — tylko przy wejściu z planowaniem.
    if not plan:
        result += [phase("planning", "skipped"), phase("plan-review", "skipped"), phase("plan-approval", "skipped")]
    else:
        proposal = st.plan_proposed or {}
        planning_step = last(("planning", "plan-fix", "plan-import"))
        if st.plan_proposed or approved or st.imported:
            done_event = proposal or st.imported or st.plan_approved or {}
            result.append(phase("planning", "done", proposal.get("planner") or planning_step.get("executor"),
                                done_event.get("ts"), "plan zaimportowany" if st.imported and not proposal else ""))
        else:
            result.append(phase("planning", asked("planning") or "now",
                                planning_step.get("executor") or started.get("executor"), planning_step.get("ts")))
        review_step = last(("plan-review",))
        reviewed = st.plan_reviewed or {}
        if st.plan_reviewed:
            verdict = reviewed.get("verdict")
            result.append(phase("plan-review", "done", reviewed.get("reviewer") or review_step.get("executor"),
                                reviewed.get("ts"), "prosi o zmiany" if verdict == "REQUEST_CHANGES" else
                                "zatwierdza" if verdict == "APPROVE" else ""))
        elif approved:
            result.append(phase("plan-review", "skipped"))
        elif st.plan_proposed:
            waits = st.plan_review_wait or st.waiting.get(("", "plan-review")) or {}
            state = asked("plan-review") or ("now" if review_step.get("step") in running or not waits else "hold")
            result.append(phase("plan-review", state, review_step.get("executor") or st.plan_reviewer,
                                review_step.get("ts"), waits.get("reason") if state == "hold" else ""))
        else:
            result.append(phase("plan-review", "todo"))
        if approved:
            result.append(phase("plan-approval", "done", USER, st.plan_approved.get("ts")))
        else:
            result.append(phase("plan-approval", asked("plan-approval") or "todo", USER))

    # Tickety.
    ticket_steps = [s for s in steps if s.get("kind") in ("ticket", "review", "fix", "conflict", "interactive")]
    merges = [r for (action, _key), r in st.results.items() if action == "merge" and r.get("ok")]
    tickets = out.get("tickets") or []
    if plan:
        finished = bool(tickets) and all(t.get("state") in ("gotowe", "odrzucone") for t in tickets)
    else:
        finished = bool(merges) or bool(st.closed)
    if plan and not approved:
        result.append(phase("tickets", "todo", note=_count(len(tickets), "ticket", "tickety", "ticketów")
                            if tickets else ""))
    elif finished:
        result.append(phase("tickets", "done", "" if plan else (ticket_steps[0].get("executor") if ticket_steps else ""),
                            merges[-1].get("ts") if merges else "",
                            f"{len(merges)} z {len(tickets)} scalonych" if plan else ""))
    else:
        state = asked("tickets") or asked("")
        if not state:
            limited = [w for (ticket, kind), w in st.waiting.items()
                       if kind != "plan-review" and _limit_wait(w) and ticket not in st.rejected]
            state = "hold" if limited and not st.running() else "now"
        first = ticket_steps[0] if ticket_steps else {}
        result.append(phase("tickets", state, "" if plan else first.get("executor"), first.get("ts"),
                            flow.get("waveLabel") or ""))

    # Domknięcie i scalenie do zapisanego celu (ID fazy zachowane dla dotychczasowych widoków).
    close_step = last(("close",))
    if st.closed:
        result.append(phase("closing", "done", close_step.get("executor"), st.closed.get("ts")))
    elif close_step:
        result.append(phase("closing", asked("closing") or "now", close_step.get("executor"), close_step.get("ts")))
    else:
        result.append(phase("closing", "todo"))
    if st.merged:
        merged = phase("main-merge", "done", USER, st.merged.get("ts"))
        if st.merged["targetBranch"]:
            merged["label"] = "Scalenie do " + st.merged["targetBranch"]
        result.append(merged)
    else:
        result.append(phase("main-merge", "you" if st.closed else "todo", USER))

    # Karta w „Czeka na mnie” bez decyzji w żadnej fazie: czeka bieżąca faza (spójnie z kafelkiem). Postój na limicie
    # (`hold`) zostaje; od PT-130 karta czekająca na limit jest w „W toku”, nie w „Czeka na mnie”.
    if out.get("column") == "waiting" and not any(p["state"] in ("you", "hold") for p in result):
        current = next((p for p in result if p["state"] in ("now", "hold", "todo")), None)
        if current:
            current["state"] = "you"
    return result


# --- tickety -------------------------------------------------------------------------------------

def _before_approval(st: FlowState) -> bool:
    return st.plan_approved is None and (st.started or {}).get("entry") in PLAN_ENTRIES


def limit_stops(st: FlowState) -> list[dict]:
    """Kroki zatrzymane przez limit dostawcy (`step.finished` z `reason: limit` albo `faultCause: limit`), na które
    Tide czeka sam: limit nie jest decyzją (D3, PT-125), więc pełna karta czyta postój wprost ze stanu.
    Pomija kroki zastąpione, z kolejną próbą, rozstrzygnięte (np. przeniesione) i tickety odrzucone.
    `{step, ticket, stepKind, until}` — `until` to godzina odnowienia z `step.limit`, gdy znana."""
    found = []
    for name, step in st.steps.items():
        done = st.finished.get(name) or {}
        if done.get("result") not in ("error", "stopped"):
            continue
        if done.get("reason") != "limit":
            continue
        if name in st.superseded or name in st.step_decisions or step.get("ticket") in st.rejected:
            continue
        if any(other.get("parentStep") == name for other in st.steps.values()):
            continue
        found.append({"step": name, "ticket": step.get("ticket") or "", "stepKind": step.get("kind") or "",
                      "until": str((st.limits.get(name) or {}).get("reset") or "")})
    return found


def _wip_files(wip: dict) -> int:
    files = wip.get("files") if isinstance(wip, dict) else 0
    if isinstance(files, (list, tuple)):
        return len(files)
    return files if isinstance(files, int) and not isinstance(files, bool) and files > 0 else 0


def stopped_by_limit(st: FlowState) -> list[dict]:
    """`flow.stoppedByLimit[]` (PT-130): `{step, ticket, wipFiles, provider, until}` dla każdego kroku z `limit_stops`."""
    found = []
    for stop in limit_stops(st):
        wip = (st.wip_retries.get(stop["step"]) or {}).get("wip") or (st.finished.get(stop["step"]) or {}).get("wip")
        found.append({"step": stop["step"], "ticket": stop["ticket"], "wipFiles": _wip_files(wip or {}),
                      "provider": (st.steps.get(stop["step"]) or {}).get("provider") or "", "until": stop["until"]})
    return found


def transfer_cost(stopped: list[dict], label: str, remaining: float) -> str:
    """Koszt przeniesienia: kroki i pliki WIP do przekazania oraz pozostały limit docelowego dostawcy (bez walut)."""
    wip = sum(item["wipFiles"] for item in stopped)
    return (f"{_count(len(stopped), 'zatrzymany krok', 'zatrzymane kroki', 'zatrzymanych kroków')} i "
            f"{_count(wip, 'plik WIP', 'pliki WIP', 'plików WIP')} do przekazania; "
            f"po przeniesieniu {label} ma jeszcze {remaining:g}% limitu.")


PROVIDER_LABEL = {"anthropic": "Claude", "openai": "Codex"}
NO_TRANSFER_TARGET = "Nie ma innego dostawcy z wolnym limitem, więc nie ma dokąd przenieść zatrzymanych kroków."


def limit_transfer(stopped: list[dict], others: list[dict], threshold: float, pick) -> dict | None:
    """`flow.limitTransfer` (PT-130): akcja „Przenieś na <dostawca>” z kosztem albo jedno zdanie powodu, gdy ukryta.
    `others` — `flow.otherProviders[]`; `pick(provider)` zwraca nazwę wykonawcy tego dostawcy (pusty — brak).
    Docelowy dostawca to ten z największym pozostałym limitem powyżej progu, który ma wykonawcę."""
    if not stopped:
        return None
    for item in sorted(others, key=lambda other: -other["remaining"]):
        executor = pick(item["provider"]) if item["remaining"] > threshold else ""
        if executor:
            label = PROVIDER_LABEL.get(item["provider"], item["provider"])
            return {"available": True, "provider": item["provider"], "label": label, "executor": executor,
                    "text": f"Przenieś na {label}", "reason": "",
                    "cost": transfer_cost(stopped, label, item["remaining"])}
    return {"available": False, "provider": "", "label": "", "executor": "", "text": "", "cost": "",
            "reason": NO_TRANSFER_TARGET}


def _limit_wait(wait: dict) -> bool:
    """`step.waiting` z powodem limitu (okno limitu, liczba kroków dostawcy, kolejka modeli lokalnych);
    wstrzymanie karty to nie limit. Starsze zdarzenia bez `cause` rozpoznajemy po treści powodu."""
    cause = wait.get("cause")
    if cause:
        return cause in ("quota", "slots", "local")
    reason = str(wait.get("reason") or "")
    return "limit" in reason or "modele lokalne" in reason


def waiting_on(st: FlowState, ident: str, row: dict, decisions: list[dict], merged: set) -> dict | None:
    """Najważniejszy powód czekania ticketu: użytkownik > bloker > limit; None — ticket nie czeka (także przed
    zatwierdzeniem planu: wtedy ticket jest dopiero „w planie”)."""
    if ident in merged or ident in st.rejected or _before_approval(st):
        return None
    found: dict[str, dict] = {}
    if any(decision.get("ticket") == ident for decision in decisions):
        found["user"] = {"kind": "user"}
    for stop in limit_stops(st):
        if stop["ticket"] == ident:
            found.setdefault("limit", {"kind": "limit", "until": stop["until"]})
    blockers = [b for b in row.get("blockers") or [] if b not in merged and b not in st.rejected]
    if blockers:
        found.setdefault("blocker", {"kind": "blocker", "ticket": blockers[0]})
    for (ticket, _kind), wait in st.waiting.items():
        if ticket == ident and _limit_wait(wait):
            found.setdefault("limit", {"kind": "limit", "until": wait.get("until") or ""})
    return next((found[kind] for kind in WAIT_PRIORITY if kind in found), None)


def ticket_status(st: FlowState, ticket: dict) -> dict:
    ident, wait = ticket["id"], ticket.get("waitingOn")
    latest = next((s for s in reversed(list(st.steps.values())) if s.get("ticket") == ident), {})
    done = st.finished.get(latest.get("step")) or {}

    def status(key: str, label: str = "") -> dict:
        return {"status": key, "statusLabel": label or TICKET_STATUS[key]}

    if ticket.get("state") == "gotowe":
        return status("merged")
    if ticket.get("state") == "odrzucone":
        return status("rejected")
    if _before_approval(st):
        return status("planned")
    if wait and wait["kind"] == "user":
        return status("you")
    if wait and wait["kind"] == "blocker":
        return status("blocked", f"czeka na {wait['ticket']}")
    if wait and wait["kind"] == "limit":
        return status("limit", "czeka na limit" + (f" do {wait['until']}" if wait.get("until") else ""))
    if not latest:
        return status("queued")
    kind = latest.get("kind")
    if not done:
        return status("review" if kind == "review" else "fix" if kind == "fix" else "running")
    if done.get("result") == "done":
        verdict = next((v for (t, _r), v in reversed(list(st.verdicts.items())) if t == ident), {})
        if kind == "review" and verdict.get("verdict") == "APPROVE":
            return status("merging")
        if kind == "review" and verdict.get("verdict") == "REQUEST_CHANGES":
            return status("fix")
        return status("review")
    if done.get("result") == "error" and int(latest.get("attempt") or 0) == 0:
        return status("running", "ponawia po błędzie")
    if done.get("result") == "denied" and latest.get("step") in st.outside_started:
        # Polecenie poza piaskownicą trwa (np. `make test`) — to praca, nie czekanie (04.11, K-37/PT-153: kafelek
        # „czeka” i „Nic nie pracuje teraz w tle”, gdy testy szły od kwadransa).
        return status("running", "polecenie poza piaskownicą")
    if done.get("result") == "denied" and (st.step_decisions.get(latest.get("step")) or {}).get("choice") in \
            ("run", "run-always", "run-ticket"):
        return status("queued", "polecenie w kolejce")
    return status("waiting")


# --- Teraz w tle i jedno zdanie ------------------------------------------------------------------

def running_steps(steps: list[dict], st: FlowState) -> list[dict]:
    """Tylko kroki, które trwają (bez wyniku); zakończone i czekające na decyzję tu nie trafiają."""
    keys = ("id", "ticket", "kind", "executor", "mode", "startedTs", "lastActivityTs", "canEnter")
    resumed = {s.get("parentStep") for s in st.steps.values() if s.get("parentStep")}
    return [{**{key: step.get(key) for key in keys},
             "kindLabel": "polecenie poza piaskownicą" if step.get("id") in st.finished else kind_label(step.get("kind"))}
            for step in steps if step.get("id") not in st.finished or
            (step.get("id") in st.outside_started and step.get("id") not in resumed)]


def headline(st: FlowState, out: dict) -> str:
    """Zdanie do „Twój ruch”, gdy nic nie czeka na użytkownika (przy decyzjach — pusty napis)."""
    flow = out["flow"]
    if flow.get("decisions"):
        return ""
    count = len(flow.get("running") or [])
    wave = flow.get("waveLabel") or ""
    hold = next((p for p in out.get("phases") or [] if p["state"] == "hold"), None)
    if st.paused:
        return "Karta jest wstrzymana: trwające kroki kończą pracę, nowe nie wystartują, dopóki jej nie wznowisz."
    if st.merged:
        target = st.merged["targetBranch"]
        return "Karta jest scalona" + (f" do {target}" if target else "") + "; nic już na Ciebie nie czeka."
    if st.closed:
        return "Praca jest na gałęzi karty i czeka, aż ją sprawdzisz i wybierzesz cel scalenia."
    if hold and not count:
        until = next((t["waitingOn"].get("until") for t in out.get("tickets") or []
                      if t.get("waitingOn") and t["waitingOn"]["kind"] == "limit" and t["waitingOn"].get("until")), "")
        return ("Kroki czekają na limit dostawcy" + (f" do {until}" if until else "") +
                "; Tide wznowi je sam, nie musisz nic robić.")
    if count:
        where = f" ({wave})" if wave else ""
        verb = "pracują" if count != 1 and count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14) else "pracuje"
        return (f"{_count(count, 'krok', 'kroki', 'kroków')} {verb} w tle{where}; następna decyzja będzie Twoja "
                "przy domknięciu karty, chyba że któryś krok o coś zapyta.")
    if any((t.get("waitingOn") or {}).get("kind") == "blocker" for t in out.get("tickets") or []):
        return "Tickety czekają na blokery i ruszą same, gdy poprzednie trafią na gałąź karty."
    note = out.get("note") or ""
    return f"Teraz: {note}." if note else "Nic nie czeka na Twoją decyzję."


def _count(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return f"{n} {one}"
    return f"{n} {few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many}"
