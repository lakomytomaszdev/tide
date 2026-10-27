"""„Plany i tickety” w raporcie porannym — dodatek, bez którego raport działa jak dotąd.

Źródła (oba opcjonalne):
- plik tablicy koordynacji z usługi ($XDG_STATE_HOME/tide/coordination.json, coordination/actions.py):
  stany ticketów wyprowadzone z pracy podagentów, planu i YouTracka,
- plany w repozytoriach projektów (docs/agents/plans/*.md, docs/agent-plan-standard.md): aktywne,
  wstrzymane albo zmienione w okresie raportu — gdy tablica ich nie zna (np. usługa jeszcze nie
  przeczytała koordynatora), stan bierzemy z samego planu.

`attach(projects, ...)` dopisuje do projektów listę `plans`; każdy błąd tylko pomija dodatek.
"""
import os

from ..coordination import actions
from ..coordination import plan as plan_mod
from ..coordination.board import STATE_WORDS

ACTIVE_STATES = ("czeka", "w-toku", "review", "todo")
ORDER = {"czeka": 0, "review": 1, "w-toku": 2, "todo": 3, "gotowe": 4, "odrzucone": 5}
SHOWN = 12


def attach(projects: list, start: float, end: float, cache_file: str | None = None) -> None:
    try:
        _attach(projects, start, end, cache_file)
    except Exception as exc:  # dodatek nie może zepsuć raportu
        print(f"tide-daemon: raport: plany i tickety pominięte: {exc!r}", flush=True)
        for project in projects:
            project.plans = []


def _attach(projects: list, start: float, end: float, cache_file: str | None) -> None:
    cache = (actions.read_cache(cache_file) if cache_file else None) or {}
    boards = cache.get("coordinators") or []
    for project in projects:
        project.plans = []
    by_path = {os.path.realpath(p.path): p for p in projects if getattr(p, "is_repo", False)}
    by_name = {p.name: p for p in projects}
    covered: set[str] = set()
    for b in boards:
        last = b.get("lastActivityTs") or 0
        if last < start and not b.get("live"):
            continue
        project = by_path.get(os.path.realpath(b.get("repoPath") or "/")) or by_name.get(b.get("project") or "")
        if project is None:
            continue
        plan = b.get("plan") or {}
        if plan.get("path"):
            covered.add(os.path.realpath(plan["path"]))
        entry = _entry(b.get("title") or "", b.get("label") or "", "board", b.get("tickets") or [],
                       tool=b.get("tool", ""), live=bool(b.get("live")), state=b.get("state", ""),
                       plan_path=plan.get("path", ""), tracker=(b.get("tracker") or {}), last=last)
        # Do „Na daily”: sesja koordynatora (żeby nie liczyć jej drugi raz jako osobnej pracy)
        # i jego pytanie bez odpowiedzi.
        entry["id"] = str(b.get("id") or "")
        entry["question"] = str(b.get("question") or "")
        entry["all"] = [dict(t) for t in b.get("tickets") or []]
        project.plans.append(entry)
    for project in projects:
        if not getattr(project, "is_repo", False):
            continue
        for plan in plan_mod.find_plans([project.path]):
            if os.path.realpath(plan.path) in covered:
                continue
            if plan.status == "done" and plan.updated_ts < start:
                continue
            if plan.status != "active" and plan.updated_ts < start:
                continue
            tickets = [{"id": r.ticket, "title": r.title, "state": r.state or "todo", "note": r.note, "url": ""}
                       for r in plan.rows]
            entry = _entry(plan.title, "", "plan", tickets, plan_path=plan.path,
                           tracker={"kind": plan.tracker}, last=plan.updated_ts,
                           status=plan.status, errors=plan.errors)
            entry["id"], entry["question"], entry["all"] = "", "", [dict(t, lastActivityTs=plan.updated_ts)
                                                                    for t in tickets]
            project.plans.append(entry)
    for project in projects:
        project.plans.sort(key=lambda e: (-e["active"], -e["last"]))


def _entry(title, label, source, tickets, tool="", live=False, state="", plan_path="", tracker=None, last=0.0,
           status="", errors=()) -> dict:
    counts = {s: 0 for s in ORDER}
    for t in tickets:
        counts[t.get("state") if t.get("state") in counts else "todo"] += 1
    total = len(tickets) - counts["odrzucone"]
    ordered = sorted(tickets, key=lambda t: ORDER.get(t.get("state"), 9))
    return {
        "title": title, "label": label, "source": source, "tool": tool, "live": live, "state": state,
        "planPath": plan_path, "tracker": (tracker or {}).get("kind") or "none",
        "trackerError": (tracker or {}).get("error") or "", "status": status, "errors": list(errors),
        "counts": counts, "done": counts["gotowe"], "total": total,
        "active": sum(counts[s] for s in ACTIVE_STATES), "last": last,
        "tickets": [dict(t, word=STATE_WORDS.get(t.get("state"), t.get("state", ""))) for t in ordered[:SHOWN]],
        "hidden": max(0, len(ordered) - SHOWN),
    }
