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
                 "lost": "krok zniknął (restart usługi)", "abandoned": "porzucony"}
NOTE_MAX = 160


@dataclass
class FlowState:
    started: dict | None = None                     # ostatnie card.started
    intents: dict = field(default_factory=dict)     # (action, key) → intent
    results: dict = field(default_factory=dict)     # (action, key) → result
    steps: dict = field(default_factory=dict)       # step → step.started
    finished: dict = field(default_factory=dict)    # step → step.finished
    last_step: str = ""
    paused: bool = False
    card_finished: bool = False
    moved_to: str = ""                              # kolumna z ostatniego card.moved po ostatnim wyniku kroku
    store_column: str = ""                          # kolumna ostatnio potwierdzona w dzienniku
    store_move_id: str = ""                         # ostatni ruch MoveCard potwierdzony w dzienniku
    rejected: set = field(default_factory=set)
    last_seq: int = 0

    def running(self) -> list[dict]:
        return [s for name, s in self.steps.items() if name not in self.finished]

    def open_intents(self) -> list[dict]:
        return [i for k, i in self.intents.items() if k not in self.results]

    def merge_pending(self) -> list[dict]:
        """Kroki ticketu z wynikiem „gotowe”, których ticket nie ma jeszcze wyniku scalenia."""
        out = []
        for name, done in self.finished.items():
            step = self.steps.get(name) or {}
            if done.get("result") == "done" and step.get("ticket") and \
                    ("merge", step["ticket"]) not in self.results and step["ticket"] not in self.rejected:
                out.append(step)
        return out

    def active(self) -> bool:
        """Krok trwa, scalanie czeka albo akcja zaczęta i niedokończona — nowe zlecenie nie startuje."""
        return bool(self.running() or self.merge_pending() or self.open_intents())


def fold(events: list[dict]) -> FlowState:
    st = FlowState()
    for e in events:
        kind = e.get("type")
        st.last_seq = max(st.last_seq, int(e.get("seq") or 0))
        if kind == "card.started":
            st.started, st.paused, st.card_finished, st.moved_to = e, False, False, ""
            st.store_column = e.get("column") or ""
            st.store_move_id = e.get("moveId") or ""
        elif kind == "intent":
            st.intents[(e.get("action"), e.get("key"))] = e
        elif kind == "result":
            st.results[(e.get("action"), e.get("key"))] = e
        elif kind == "step.started":
            st.steps[e.get("step")] = e
            st.last_step = e.get("step") or ""
            st.moved_to = ""
        elif kind == "step.entered" and e.get("step") in st.steps:
            st.steps[e["step"]] = {**st.steps[e["step"]], "mode": "interactive",
                                   "session": e.get("session") or "", "closeFile": e.get("closeFile") or "",
                                   "source": e.get("source") or "", "taskFile": e.get("taskFile") or "",
                                   "consoleClosed": False}
        elif kind == "step.console_closed" and e.get("step") in st.steps:
            st.steps[e["step"]] = {**st.steps[e["step"]], "consoleClosed": True}
        elif kind == "step.finished":
            st.finished[e.get("step")] = e
            if st.moved_to not in ("done", "archived"):
                st.moved_to = ""
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
    return st


def ticket_of(st: FlowState) -> dict:
    """Ostatni ticket karty: {id, title, branch, worktree, cardBranch, cardWorktree} z wyników akcji."""
    out = {"id": "", "title": "", "branch": "", "worktree": "", "cardBranch": "", "cardWorktree": ""}
    for (action, _key), res in st.results.items():
        if not res.get("ok"):
            continue
        if action == "reserve":
            out["id"], out["title"] = res.get("ticket") or "", res.get("title") or ""
        elif action == "card-branch":
            out["cardBranch"], out["cardWorktree"] = res.get("branch") or "", res.get("worktree") or ""
        elif action == "ticket-branch" and res.get("ticket") == out["id"]:
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
    out = {"column": "", "done": 0, "total": 1, "tool": st.started.get("program") or "", "note": "", "nextStep": "",
           "check": st.started.get("check") or card.get("check") or "auto", "branch": ticket["cardBranch"], "session": "", "coordinator": "",
           "link": "flow", "agentState": "", "tickets": [], "question": "",
           "flow": {"ticket": ident, "step": st.last_step, "executor": executor, "entry": st.started.get("entry") or "",
                    "result": done.get("result") or "", "reason": done.get("reason") or "",
                    "cardBranch": ticket["cardBranch"],
                    "branch": ticket["branch"] or step.get("branch") or ""}}
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
    if column == "backlog":
        if not st.paused:
            return None
        running = " — trwający krok kończy się" if st.running() else ""
        return _fill(out, "backlog", f"wstrzymana: {ident}{running}")
    if st.moved_to == "todo" and not st.running():
        return None
    failed = [r for (a, _k), r in st.results.items() if not r.get("ok") and a != "merge"]
    if st.running():
        mode = "w Konsoli" if step.get("mode") == "interactive" else "w tle"
        return _fill(out, "progress", f"{ident} · krok {mode} ({executor})" if executor else f"{ident} · krok {mode}")
    if st.open_intents():
        return _fill(out, "progress", f"{ident} · {_intent_word(st.open_intents()[0])}")
    if failed and not done:
        return _fill(out, "waiting", f"błąd zlecenia: {failed[-1].get('error') or failed[-1].get('action')}")
    result = done.get("result") or ""
    if result == "done":
        merge = st.results.get(("merge", ident))
        if merge is None:
            return _fill(out, "progress", f"{ident} · scalanie do gałęzi karty")
        if not merge.get("ok"):
            return _fill(out, "waiting", f"{ident}: nie scalono do gałęzi karty ({merge.get('error') or 'błąd'})")
        out["done"] = 1
        return _fill(out, "review", f"✓ {ident} na gałęzi karty — do sprawdzenia")
    if result == "question" or (result == "stopped" and done.get("reason") == "interactive-closed"):
        out["question"] = _clip(done.get("question") or "", 120)
        return _fill(out, "waiting", f"{ident} pyta: „{out['question']}”" if out["question"] else f"{ident} pyta")
    if result == "denied":
        return _fill(out, "waiting", f"{ident}: brak zgody na „{done.get('command') or '?'}”")
    if result in ("error", "stopped"):
        reason = REASON_LABELS.get(done.get("reason") or "", done.get("reason") or "")
        return _fill(out, "waiting", f"błąd kroku {ident}: {reason}" if reason else f"błąd kroku {ident}")
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
