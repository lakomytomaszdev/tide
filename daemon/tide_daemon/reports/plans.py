"""„Plany i tickety” w raporcie porannym — dodatek, bez którego raport działa jak dotąd.

Źródła (oba opcjonalne):
- plik tablicy koordynacji z usługi ($XDG_STATE_HOME/tide/coordination.json, coordination/actions.py):
  stany ticketów wyprowadzone z pracy podagentów, planu i YouTracka,
- plany w repozytoriach projektów (docs/agents/plans/*.md, docs/agent-plan-standard.md) i — dla projektów
  tablicy z `ai-docs: tide` — w repozytorium tablicy (docs/<projekt>/plans/, standard 1.1): aktywne,
  wstrzymane albo zmienione w okresie raportu — gdy tablica ich nie zna (np. usługa jeszcze nie
  przeczytała koordynatora), stan bierzemy z samego planu.

Do „Na daily”: cel planu (akapit „Cel…”), uwagi z planu, a dla planu z `tracker: youtrack`, którego
tablica nie zna — początek opisu ticketu z YouTracka (`tracker`: YouTrackCache, tylko GET, ten sam token
i serwer co tablica; błąd albo brak tokenu — bez opisów).

`attach(projects, ...)` dopisuje do projektów listę `plans` i `conflicts` — ostrzeżenia o numerach
ticketów (coordination/numbering.py) z planów wszystkich worktree i gałęzi repozytorium, z listą projektów
YouTracka, gdy repozytorium ma plany lokalne; każdy błąd tylko pomija dodatek.
"""
import os

from ..cardflow.journal import Journal
from ..cardflow.state import _epoch
from ..coordination import actions, ids, repoplans
from ..coordination import plan as plan_mod
from ..coordination.board import STATE_WORDS

ACTIVE_STATES = ("czeka", "w-toku", "review", "todo")
ORDER = {"czeka": 0, "review": 1, "w-toku": 2, "todo": 3, "gotowe": 4, "odrzucone": 5}
SHOWN = 12
DESCRIPTION_MAX = 600


def attach(projects: list, start: float, end: float, cache_file: str | None = None, tracker=None,
           flow_root: str | None = None) -> None:
    try:
        _attach(projects, start, end, cache_file, tracker)
    except Exception as exc:  # dodatek nie może zepsuć raportu
        print(f"tide-daemon: raport: plany i tickety pominięte: {exc!r}", flush=True)
        for project in projects:
            project.plans = []
    if flow_root:
        try:
            _attach_flow(projects, start, end, flow_root)
        except Exception as exc:
            print(f"tide-daemon: raport: dzienniki kart pominięte: {exc!r}", flush=True)
    for project in projects:
        project.conflicts = []
        if not getattr(project, "is_repo", False):
            continue
        try:
            project.conflicts = conflicts(project.path, tracker)
        except Exception as exc:  # dodatek nie może zepsuć raportu
            print(f"tide-daemon: raport: kontrola numerów pominięta ({project.name}): {exc!r}", flush=True)


def conflicts(path: str, tracker=None, repo_plans=None) -> list[dict]:
    """Ostrzeżenia o numerach ticketów w repozytorium (coordination/repoplans.check)."""
    return repoplans.check([path], repo_plans, tracker)


def _attach_flow(projects: list, start: float, end: float, root: str) -> None:
    """Zrobione tickety czyta z dziennika karty, także po przeniesieniu karty do Gotowe."""
    by_repo = {os.path.realpath(p.path): p for p in projects if getattr(p, "is_repo", False)}
    journal = Journal(root)
    for card in journal.cards():
        events = journal.read(card)
        started = next((e for e in events if e.get("type") == "card.started"), {})
        project = by_repo.get(os.path.realpath(started.get("repo") or "/"))
        if project is None:
            continue
        rows = {}
        for event in events:
            if event.get("type") == "plan.approved":
                rows.update({r.get("id"): r for r in event.get("rows") or [] if r.get("id")})
            elif event.get("type") == "plan.revision-approved":
                rows.update({r.get("id"): r for r in (event.get("plan") or {}).get("rows") or [] if r.get("id")})
        steps = {e.get("ticket"): e for e in events if e.get("type") == "step.started" and
                 e.get("kind") in ("ticket", "fix") and e.get("ticket")}
        completed = []
        for event in events:
            if event.get("type") != "result" or event.get("action") != "merge" or not event.get("ok"):
                continue
            when = _epoch(event.get("ts"))
            if not start <= when <= end:
                continue
            ident = event.get("key") or ""
            executor = (steps.get(ident) or {}).get("executor") or "nieznany wykonawca"
            completed.append({"id": ident, "title": (rows.get(ident) or {}).get("title") or ident,
                              "state": "gotowe", "note": f"wykonał {executor}", "when": when})
        if not completed:
            continue
        entry = _entry(f"{card} · Zrobione w okresie raportu", card, "card-flow", completed,
                       last=max(r["when"] for r in completed))
        entry["card"] = card
        project.plans.append(entry)
    for project in projects:
        project.plans.sort(key=lambda e: (-e["active"], -e["last"]))


def _attach(projects: list, start: float, end: float, cache_file: str | None, tracker=None) -> None:
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
        entry["questionTs"] = b.get("questionTs") or 0
        entry["questionKind"] = b.get("questionKind") or "question"
        entry["all"] = [dict(t) for t in b.get("tickets") or []]
        entry["goal"] = str(plan.get("goal") or "") or _goal(plan.get("path") or "")
        entry["card"] = str(b.get("card") or plan.get("card") or "")
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
            issues = _issues(plan, project.path, tracker)
            tickets = [{"id": r.ticket, "title": r.title, "state": r.state or "todo", "note": r.note, "url": "",
                        "planNote": r.note, "branch": r.branch, "description": issues(r.ticket)} for r in plan.rows]
            entry = _entry(plan.title, "", "plan", tickets, plan_path=plan.path,
                           tracker={"kind": plan.tracker}, last=plan.updated_ts,
                           status=plan.status, errors=plan.errors)
            entry["id"], entry["question"], entry["all"] = "", "", [dict(t, lastActivityTs=plan.updated_ts)
                                                                    for t in tickets]
            entry["goal"] = plan.goal
            entry["card"] = plan.card
            project.plans.append(entry)
    for project in projects:
        project.plans.sort(key=lambda e: (-e["active"], -e["last"]))


def _issues(plan, repo: str, tracker):
    """funkcja(ID z planu) → początek opisu z YouTracka (ID przez mapę migracji, np. LA-035 → LEGAL-35)."""
    if tracker is None or plan.tracker != "youtrack":
        return lambda ident: ""
    found, _info = tracker.get(plan.tracker_url, plan.tracker_project)
    if not found:
        return lambda ident: ""
    matcher = ids.TicketMatcher([plan.prefix] if plan.prefix else [], ids.load_aliases([repo]))

    def description(ident: str) -> str:
        issue = found.get(matcher.find(ident) or "") or {}
        return str(issue.get("description") or "").strip()[:DESCRIPTION_MAX]
    return description


def _goal(path: str) -> str:
    """Cel planu z pliku (plik tablicy sprzed pola „goal” go nie ma)."""
    if not path:
        return ""
    plan = plan_mod.read(path)
    return plan.goal if plan is not None else ""


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
