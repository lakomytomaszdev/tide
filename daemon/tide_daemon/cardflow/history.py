"""Historia pełnej karty ludzkim językiem (K-35, PT-129): `derived.flow.history[]` liczone z dziennika.

Wpis: `{id, ts, kind, kindLabel, text, actor, old, step, ticket, result, canResume, attempts[]}`:
- `ts` — czas epoch (sekundy); czas względny i bezwzględny po polsku składa widok, bez znaczników ISO w UI,
- `kind` — rodzaj zdarzenia: `step`, `review`, `decision`, `plan`, `card`, `merge`; `kindLabel` — jego polska nazwa,
- `text` — zdanie bez czasu, np. „PT-7: Sonnet — 3 próby, ostatnia: błąd”,
- `actor` — `user` dla zdarzeń użytkownika (zlecenie, decyzje, zatwierdzenie planu, wstrzymanie, scalenie do master),
  inaczej wykonawca kroku albo `tide`; dziennik nie ma pola `actor`, aktora wyprowadza ten moduł z typu zdarzenia,
- `old` — wpis ze wcześniejszego zlecenia (przed ostatnim `card.started`) albo krok `superseded`; tylko do odczytu,
- `attempts[]` — próby kroku złożone po `parentStep` w jedną linię: `{step, attempt, ts, executor, result,
  resultLabel}`; kroki bez ponowień mają jedną próbę,
- `canResume` — „Wznów rozmowę”: tylko krok bieżącego zlecenia, którego ostatnia próba ma pytanie, brak zgody albo
  jest zatrzymana, i którą da się otworzyć (`canEnter`).
Format dziennika się nie zmienia: zdarzenie startu zlecenia to istniejące `card.started`.
"""
from __future__ import annotations

from datetime import datetime

from .labels import result_label, step_name

LOGGED = ("card.started", "card.paused", "card.resumed", "card.closed", "card.main-merged", "card.merged", "step.started",
          "step.finished", "step.decision", "review.decision", "review.verdict", "plan.proposed", "plan.reviewed", "plan.approved",
          "ticket.rejected", "result")
USER_EVENTS = ("card.started", "card.paused", "card.resumed", "card.main-merged", "card.merged", "step.decision", "plan.approved",
               "ticket.rejected", "review.decision")
RESUMABLE = ("question", "denied", "stopped")
KIND_LABELS = {"step": "krok", "review": "review", "decision": "decyzja", "plan": "plan", "card": "karta",
               "merge": "scalanie"}
RESULT_PHRASES = {"": "pracuje", "done": "gotowe", "question": "pyta", "denied": "prosi o zgodę",
                  "error": "błąd", "stopped": "zatrzymany"}
CHOICES = {"retry": "ponowienie kroku", "wait-limit": "czekanie na limit", "transfer": "przekazanie kroku",
           "abandon": "porzucenie kroku", "answer": "odpowiedź na pytanie", "reject": "odrzucenie ticketu",
           "allow": "zgoda na polecenie", "deny": "kontynuacja bez polecenia"}
VERDICTS = {"APPROVE": "zatwierdza", "REQUEST_CHANGES": "prosi o zmiany"}


def build(st, steps: list[dict]) -> list[dict]:
    """Historia karty: chronologicznie (od najstarszego); `steps` to `flow.steps[]` (stąd `canEnter`)."""
    last_started = max((int(e.get("seq") or 0) for e in st.log if e.get("type") == "card.started"), default=0)
    enter = {item.get("id"): item.get("canEnter") is True for item in steps}
    roots = {name: _root(st, name) for name in st.steps}
    chains: dict[str, list[str]] = {}
    for name in st.steps:                               # st.steps jest w kolejności startu
        chains.setdefault(roots[name], []).append(name)
    items = []
    for event in st.log:
        kind = event.get("type")
        seq = int(event.get("seq") or 0)
        if kind == "step.started":
            if roots.get(event.get("step")) != event.get("step"):
                continue                                # kolejna próba — jest w linii pierwszej próby
            items.append(_step_entry(st, event, chains[event["step"]], enter))
        elif kind == "step.finished":
            continue
        else:
            entry = _event_entry(event, st)
            if entry:
                entry["old"] = seq < last_started
                items.append(entry)
    return items


def _root(st, name: str) -> str:
    """Pierwsza próba kroku: łańcuch `parentStep` w obrębie tego samego rodzaju kroku."""
    kind = (st.steps.get(name) or {}).get("kind")
    seen = {name}
    while True:
        parent = (st.steps.get(name) or {}).get("parentStep")
        if not parent or parent in seen or (st.steps.get(parent) or {}).get("kind") != kind:
            return name
        seen.add(parent)
        name = parent


def _step_entry(st, first: dict, chain: list[str], enter: dict) -> dict:
    name = first["step"]
    attempts = []
    for index, step_id in enumerate(chain):
        step, done = st.steps[step_id], st.finished.get(step_id) or {}
        attempts.append({"step": step_id, "attempt": int(step.get("attempt") or index), "ts": _epoch(step.get("ts")),
                         "executor": step.get("executor") or "", "result": done.get("result") or "",
                         "resultLabel": result_label(done["result"]) if done.get("result") else "trwa"})
    last = attempts[-1]
    latest = st.steps[last["step"]]
    kind = "review" if first.get("kind") == "review" else "step"
    who = latest.get("executor") or ""
    outcome = RESULT_PHRASES.get(last["result"], result_label(last["result"]))
    if len(attempts) > 1:
        text = f"{step_name(first.get('kind'), first.get('ticket'))}: {who} — {_tries(len(attempts))}, ostatnia: {outcome}"
    else:
        text = f"{step_name(first.get('kind'), first.get('ticket'))}: {who} — {outcome}"
    old = name in st.superseded
    return {"id": name, "ts": attempts[0]["ts"], "kind": kind, "kindLabel": KIND_LABELS[kind], "text": text,
            "actor": who or "tide", "old": old, "step": last["step"], "ticket": first.get("ticket") or "",
            "result": last["result"], "attempts": attempts,
            "canResume": (not old and last["result"] in RESUMABLE and enter.get(last["step"], False))}


def _step_label(st, name) -> str:
    step = (st.steps.get(name) if st is not None and name else None) or {}
    return step_name(step.get("kind"), step.get("ticket")) if step else (name or "")


def _event_entry(e: dict, st=None) -> dict | None:
    kind, ticket = e.get("type"), e.get("ticket") or ""
    who = e.get("executor") or e.get("reviewer") or ""
    if kind == "card.started":
        group, text = "card", "Zlecenie: karta wystartowała" + (f" ({who})" if who else "")
    elif kind == "card.paused":
        group, text = "card", "Karta wstrzymana"
    elif kind == "card.resumed":
        group, text = "card", "Karta wznowiona"
    elif kind == "card.closed":
        group, text = "card", "Praca domknięta na gałęzi karty"
    elif kind in ("card.main-merged", "card.merged"):
        from .state import merge_of
        target = merge_of(e)["targetBranch"]
        group, text = "merge", "Karta scalona" + (f" do {target}" if target else " (cel niezapisany)")
    elif kind == "step.decision":
        group = "decision"
        automatic = bool(e.get("auto") or e.get("automatic"))
        label = _step_label(st, e.get("step"))
        if automatic and e.get("choice") == "deny":
            text = "Tide odmówił polecenia i poprosił o pojedyncze polecenia" + (f" ({label})" if label else "")
        elif automatic and e.get("choice") == "transfer":
            text = "Tide przeniósł krok" + (f" {label}" if label else "") + \
                (f" na {e['executor']}" if e.get("executor") else "") + " (limit)"
        else:
            text = "Decyzja: " + CHOICES.get(e.get("choice"), "odpowiedź") + (f" ({label})" if label else "")
    elif kind == "review.decision":
        if e.get("choice") == "reject":
            return None                                 # skutek („Ticket odrzucony”) jest osobnym wpisem
        group, text = "decision", f"Review {ticket}: kontynuacja po 3. rundzie".replace("  ", " ")
    elif kind == "review.verdict":
        group = "review"
        text = f"Review {ticket}: " + VERDICTS.get(e.get("verdict"), "werdykt").strip()
    elif kind == "plan.proposed":
        group, text = "plan", "Plan zaproponowany" + (f" przez {e['planner']}" if e.get("planner") else "")
    elif kind == "plan.reviewed":
        group, text = "plan", "Review planu: " + VERDICTS.get(e.get("verdict"), "gotowe")
    elif kind == "plan.approved":
        group, text = "plan", "Plan zatwierdzony"
    elif kind == "ticket.rejected":
        group, text = "decision", f"Ticket {ticket} odrzucony".replace("  ", " ")
    elif kind == "result" and e.get("action") == "merge" and e.get("ok"):
        group, text = "merge", f"Ticket {e.get('key') or ticket} scalony do gałęzi karty".replace("  ", " ")
    else:
        return None
    automatic = kind == "step.decision" and bool(e.get("auto") or e.get("automatic"))
    actor = "tide" if automatic else "user" if kind in USER_EVENTS else (e.get("executor") or e.get("reviewer") or e.get("planner") or "tide")
    return {"id": f"{kind}#{e.get('seq')}", "ts": _epoch(e.get("ts")), "kind": group, "kindLabel": KIND_LABELS[group],
            "text": text, "actor": actor, "old": False, "step": e.get("step") or "", "ticket": ticket,
            "result": "", "attempts": [], "canResume": False}


def _tries(n: int) -> str:
    return f"{n} próby" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else f"{n} prób"


def _epoch(value) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() if value else 0.0
    except (AttributeError, ValueError, OverflowError):
        return 0.0
