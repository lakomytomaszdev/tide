"""Tablica ticketów koordynatora: przebiegi podagentów pogrupowane w tickety, stan każdego
ticketu i oś czasu — bez Qt, czysta funkcja `build()` (testowana na fixtures).

Grupowanie (ids.py): ID ticketu z opisu podagenta, z jego gałęzi (gdy inna niż gałąź
koordynatora), z przydomka (Codex), a z początku polecenia tylko z prefiksem z planu; prefiks z planu
albo najczęstszy w opisach i nazwach zadań. Treść polecenia bez prefiksu z planu nie daje ID: cytuje
decyzje, niezmienniki i wymagania („ADR-0016”, „INV-001”), które ticketami nie są. Bez ID — grupa
po opisie bez słów roli („Review compact distribution” i „Compact distribution” to jeden ticket).

Stan z przebiegów (od najważniejszego):
- pytanie koordynatora bez odpowiedzi, które wymienia ticket → `czeka`,
- działa recenzent → `review`; działa inny podagent → `w-toku`,
- ostatnie review bez BLOCKER/MAJOR (i bez prośby o zmiany) → `gotowe`; z nimi → `w-toku`
  („do poprawy”), a poprawki skończone po review → `gotowe` (bez ponownego review),
- implementer skończył bez review → `review` („gotowe, czeka na review”), gdy koordynator działa
  i zleca review innym ticketom — chyba że od końca tej pracy minęło SKIPPED_REVIEW, a koordynator
  w tym czasie zlecał review innym (to review pominął); inaczej `gotowe` („bez review”);
  przerwany albo z błędem → `w-toku`,
- samo rozpoznanie (Explore, explorer, research) skończone → `gotowe` („rozpoznanie skończone”).
Plan (plan.py) wygrywa zawsze przy `odrzucone`, przy ticketach bez przebiegów (`todo`
i każdy inny stan) i wtedy, gdy jego `updated` jest nowszy niż ostatnie zdarzenie ticketu.
YouTrack (tylko odczyt) daje tytuł i adres, a stan bierzemy z niego tylko wtedy, gdy nie ma
ani planu, ani przebiegów, albo gdy ticket jest tam zamknięty, a żaden podagent nad nim nie pracuje.
Lokalny backlog (`docs/agents/backlog/<ID>.md`, nagłówek „# LA-040: tytuł”) daje tylko tytuł.

Opis karty po ludzku (title, done, now) dokłada describe.py — tu zostają surowe źródła:
`officialTitle` (plan, YouTrack, backlog), `taskTitle` (z opisu zlecenia) i oś czasu po polsku
(rola słowem: implementacja, poprawki, review, rozpoznanie; angielski opis zlecenia w `meta`).
"""
import re

from . import review
from .ids import TicketMatcher, strip_ticket
from .model import COMPLETED, FAILED, INTERRUPTED, KILLED, ROLE_HELPER, ROLE_IMPLEMENTER, ROLE_REVIEWER, RUNNING

COLUMNS = ("todo", "w-toku", "czeka", "review", "gotowe")
COLUMN_OF = {"todo": "todo", "w-toku": "w-toku", "czeka": "czeka", "review": "review", "gotowe": "gotowe",
             "odrzucone": "gotowe"}
STATE_WORDS = {"todo": "do zrobienia", "w-toku": "w toku", "czeka": "czeka", "review": "review",
               "gotowe": "gotowe", "odrzucone": "odrzucone"}
SKIPPED_REVIEW = 2 * 3600  # s — po tylu godzinach i review innych ticketów praca bez review jest „gotowe”
STALE_RUN = 120          # s — podagent bez wyniku, którego dziennik stoi tyle czasu po końcu koordynatora, jest przerwany
HELPER_TYPES = re.compile(r"explor|plan|research|guide|investig|badacz", re.I)
# Rola rundy słowem (karta, oś czasu): (mianownik, biernik, „skończon…”, „trwa/trwają”).
ROLE_WORDS = {
    "implementacja": ("implementacja", "implementację", "skończona", "Trwa"),
    "poprawki": ("poprawki", "poprawki", "skończone", "Trwają"),
    "review": ("review", "review", "skończone", "Trwa"),
    "ponowne review": ("ponowne review", "ponowne review", "skończone", "Trwa"),
    "rozpoznanie": ("rozpoznanie", "rozpoznanie", "skończone", "Trwa"),
}
HELPER_WORDS = re.compile(r"^\s*(?:research|investigate|explore|diagnose|zbadaj|sprawdź|draw|mock)\b", re.I)
ROLE_PREFIX = re.compile(r"^\s*(?:(?:privacy|security|code|final|second|ponowne)\s+)?(?:re-?)?review(?:\s+of)?\s+|"
                         r"^\s*(?:apply|implement|fix|napraw|popraw|poprawki|build|add|dodaj|zrób)\s+", re.I)
ROLE_SUFFIX = re.compile(r"\s+(?:review\s+)?(?:fix(?:es)?(?:\s+round)?|post-review fixes|poprawki|change|changes|"
                         r"(?:code\s+|privacy\s+)?review|implementation)\s*$", re.I)
YOUTRACK_STATES = {
    "todo": ("submitted", "open", "reopened", "to do", "todo", "backlog", "new", "planned", "do zrobienia"),
    "w-toku": ("in progress", "w toku", "develop", "development", "doing"),
    "review": ("in review", "review", "code review", "to verify", "testing", "qa"),
    "czeka": ("waiting", "blocked", "on hold", "wstrzymane", "czeka"),
    "gotowe": ("fixed", "done", "verified", "closed", "resolved", "completed", "gotowe", "zrobione"),
    "odrzucone": ("won't fix", "wont fix", "duplicate", "obsolete", "can't reproduce", "incomplete", "rejected",
                  "odrzucone"),
}


def role_of(run) -> str:
    if review.is_review(run.agent_type, run.description):
        return ROLE_REVIEWER
    if HELPER_TYPES.search(run.agent_type or "") or HELPER_WORDS.search(run.description or ""):
        return ROLE_HELPER
    return ROLE_IMPLEMENTER


def role_word(role: str, kind: str = "launch") -> str:
    """Rola rundy po polsku: implementacja / poprawki (kontynuacja) / review / ponowne review / rozpoznanie."""
    if role == ROLE_REVIEWER:
        return "ponowne review" if kind == "continue" else "review"
    if role == ROLE_HELPER:
        return "rozpoznanie"
    return "poprawki" if kind == "continue" else "implementacja"


def youtrack_state(name: str) -> str:
    name = (name or "").strip().lower()
    for state, names in YOUTRACK_STATES.items():
        if name in names:
            return state
    return ""


def fallback_key(description: str) -> str:
    text = ROLE_PREFIX.sub("", description or "", count=1)
    text = ROLE_SUFFIX.sub("", text)
    text = re.sub(r"[^\w]+", " ", text.lower()).strip()
    return "~" + (text or (description or "?").lower())


def _join_fallback(key: str, order: list) -> str:
    """Przebieg bez ID dołącza do wcześniejszego ticketu bez ID, gdy jego słowa zawierają się w tamtych
    (albo odwrotnie): „Review anon key sync change” → „anon key sync” ⊂ „Implement anon key sync tool”.
    Liczby muszą się zgadzać („ticket 01” i „ticket 02” to różne tickety)."""
    words = set(key[1:].split())
    if not words:
        return key
    for other in reversed(order):
        if not other.startswith("~") or other == key:
            continue
        theirs = set(other[1:].split())
        numbers = {w for w in words if w.isdigit()}, {w for w in theirs if w.isdigit()}
        if numbers[0] != numbers[1]:
            continue
        if len(words) >= 2 and len(theirs) >= 2 and (words <= theirs or theirs <= words):
            return other
        if len(words) == 1 and len(next(iter(words))) >= 6 and words <= theirs:
            return other  # „Migration review” → „Migration tool”
    return key


def own_ids(coord) -> set:
    """ID samego koordynatora (np. TOM-20 z gałęzi „codex/tom-20-release-candidate”) — nie są ticketami."""
    out = set()
    for text in (coord.branch or "",):
        for match in re.finditer(r"(?<![A-Za-z0-9])([A-Za-z]{2,6})-(\d{1,6})(?![0-9])", text):
            out.add(f"{match.group(1).upper()}-{int(match.group(2))}")
    return out


def label_of(coord, plan, prefixes=()) -> str:
    """Krótka etykieta koordynatora do widżetu („TOM-20”): ID z tytułu planu albo z gałęzi
    koordynatora, ale tylko ze znanym prefiksem ticketów (gałąź „summary-tabs-2026” to nie ticket)."""
    known = {p.upper() for p in prefixes if p}
    for text in ((plan.title if plan else ""), coord.branch or "", coord.title or ""):
        for match in re.finditer(r"(?<![A-Za-z0-9])([A-Za-z]{2,6})-(\d{1,6})(?![0-9])", text):
            if match.group(1).upper() in known:
                return f"{match.group(1).upper()}-{match.group(2)}"
    return ""


def effective(run, live: bool, now: float, transcript_mtime: float = 0.0) -> str:
    """Stan przebiegu z uwzględnieniem końca koordynatora: bez wyniku i bez ruchu → przerwany."""
    state = run.state
    if state == RUNNING and not live and now - max(transcript_mtime, run.last_ts) > STALE_RUN:
        return INTERRUPTED
    return state


def build(coord, *, plan=None, aliases=None, issues=None, tracker=None, live=None, now: float = 0.0,
          info=None, project: str = "", repo_path: str = "", backlog=None, warnings=None) -> dict:
    """Tablica jako słownik (JSON dla D-Bus Board()).

    live: wpis sesji koordynatora z obszaru Agents (stan, powód) albo None (sesja nie działa),
    info(run) → {"mtime", "branch", "lastAction"} z dziennika podagenta (opcjonalne),
    issues: {klucz: {"id", "summary", "state", "url"}} z YouTracka albo None,
    tracker: {"kind", "url", "project", "available", "error", "fetchedTs"} albo None,
    backlog: {klucz: {"title", "status"}} z docs/agents/backlog/<ID>.md albo None,
    warnings: ostrzeżenia o numerach ticketów w repozytorium (numbering.conflicts) albo None.
    """
    info = info or (lambda run: {})
    is_live = bool(live) and live.get("state") not in ("ended", None)
    prefixes = [plan.prefix] if plan and plan.prefix else []
    plan_keys = set()
    matcher = TicketMatcher(prefixes, aliases or {}, ignore=())
    if plan:
        for row in plan.rows:
            key = matcher.find(row.ticket) or row.ticket.upper()
            plan_keys.add(key)
    matcher.ignore = {(aliases or {}).get(k, k) for k in own_ids(coord)} - plan_keys
    # Prefiks uczymy się tylko z opisów i nazw zadań — nie z poleceń (ADR-0016, INV-001 to nie tickety).
    matcher.learn([r.description for r in coord.runs.values()] + [r.nickname for r in coord.runs.values()])
    plan_prefix = bool(prefixes)

    tickets: dict[str, dict] = {}
    order: list[str] = []

    def ticket(key: str, display: str = "", title: str = "") -> dict:
        if key not in tickets:
            tickets[key] = {"key": key, "id": display or ("" if key.startswith("~") else matcher.show(key)),
                            "title": title, "plan": None, "issue": None, "runs": [], "question": None,
                            "branch": "", "taskTitle": "", "official": title, "officialFrom": "plan" if title else "",
                            "officialNote": ""}
            order.append(key)
        return tickets[key]

    if plan:
        for row in plan.rows:
            key = matcher.find(row.ticket) or row.ticket.upper()
            t = ticket(key, row.ticket, row.title)
            t["plan"] = row
            matcher.display[key] = row.ticket

    runs_info = {}
    uses_review = False
    for run in sorted(coord.runs.values(), key=lambda r: r.start_ts):
        data = dict(info(run) or {})
        runs_info[run.id] = data
        branch = run.branch or data.get("branch") or ""
        role = role_of(run)
        uses_review = uses_review or role == ROLE_REVIEWER
        key = matcher.find(run.description)
        if not key and branch and branch not in (coord.branch, "HEAD"):
            key = matcher.find(branch, generic=False)
        if not key and run.nickname:
            key = matcher.find(run.nickname, generic=False)
        if not key and plan_prefix:
            key = matcher.find(run.prompt_head, generic=False)
        if not key:
            key = _join_fallback(fallback_key(run.description), order)
        t = ticket(key, title="")
        t["runs"].append(run)
        if not t["taskTitle"] and role != ROLE_REVIEWER:
            t["taskTitle"] = _title(matcher.strip(run.description), "")
        if not t["branch"] and branch and branch not in (coord.branch, "HEAD") and len(branch) < 120:
            t["branch"] = branch

    for question in sorted(coord.questions.values(), key=lambda q: q.ts):
        key = matcher.find(question.text, generic=bool(matcher.prefixes))
        if key and key in tickets:
            tickets[key]["question"] = question

    for t in tickets.values():
        if not t["taskTitle"]:
            first = t["runs"][0].description if t["runs"] else ""
            t["taskTitle"] = _title(matcher.strip(first), "") if first else ""
        if issues and t["key"] in issues:
            t["issue"] = issues[t["key"]]
            if not t["official"] and t["issue"].get("summary"):
                t["official"], t["officialFrom"] = t["issue"]["summary"], "tracker"
        entry = (backlog or {}).get(t["key"])
        if entry:
            if not t["official"] and entry.get("title"):
                t["official"], t["officialFrom"] = entry["title"], "backlog"
            t["officialNote"] = entry.get("status") or ""
        t["title"] = t["official"] or t["taskTitle"] or t["id"]

    out_tickets = []
    review_starts = sorted(r.start_ts for r in coord.runs.values() if role_of(r) == ROLE_REVIEWER)
    for key in order:
        t = tickets[key]
        out_tickets.append(_ticket_json(t, coord, plan, tracker, is_live, uses_review, now, runs_info, review_starts))

    counts = {state: 0 for state in ("todo", "w-toku", "czeka", "review", "gotowe", "odrzucone")}
    for t in out_tickets:
        counts[t["state"]] += 1
    total = len(out_tickets) - counts["odrzucone"]
    label = label_of(coord, plan, matcher.prefixes)
    runs_json = [_run_json(run, coord, matcher, tickets, is_live, now, runs_info) for run in coord.runs.values()]
    question = None
    pending = [q for q in coord.questions.values()]
    if pending:
        q = max(pending, key=lambda q: q.ts)
        question = {"text": _clip(q.text, 400), "ts": round(q.ts, 3)}
    state = live.get("state") if live else "ended"
    return {
        "id": coord.id,
        "tool": coord.tool,
        "sessionId": coord.session_id,
        "project": project,
        "repoPath": repo_path,
        "cwd": coord.cwd,
        "branch": coord.branch,
        "title": (plan.title if plan and plan.title else "") or coord.title or coord.branch or project,
        "sessionTitle": coord.title,
        "label": label,
        "model": _model_name(coord.model),
        "startedTs": round(coord.first_ts, 3),
        "lastActivityTs": round(max(coord.last_ts, max((r.last_ts for r in coord.runs.values()), default=0)), 3),
        "state": state,
        "reason": live.get("reason", "") if live else "",
        "live": is_live,
        "canActivate": bool(live and live.get("canActivate")),
        "question": question,
        "plan": plan.to_json() if plan else None,
        "tracker": tracker or {"kind": plan.tracker if plan else "none"},
        "counts": dict(counts, total=total, done=counts["gotowe"]),
        "progress": {"done": counts["gotowe"], "total": total,
                     "text": f"{label} · {counts['gotowe']}/{total}" if label else f"{counts['gotowe']}/{total}"},
        "spawned": coord.spawned,
        "running": sum(1 for r in runs_json if r["state"] == RUNNING),
        "columns": list(COLUMNS),
        "tickets": out_tickets,
        "runs": runs_json,
        "warnings": [dict(w) for w in warnings or ()],
    }


def summary(board: dict) -> dict:
    """Pozycja listy koordynatorów (Coordinators()) — bez ticketów i przebiegów."""
    keys = ("id", "tool", "sessionId", "project", "repoPath", "cwd", "branch", "title", "label", "model",
            "startedTs", "lastActivityTs", "state", "reason", "live", "canActivate", "question", "counts",
            "progress", "spawned", "running")
    out = {k: board.get(k) for k in keys}
    out["planPath"] = (board.get("plan") or {}).get("path", "")
    out["tracker"] = (board.get("tracker") or {}).get("kind", "none")
    out["warnings"] = list(board.get("warnings") or [])
    return out


# --- wnętrze ---------------------------------------------------------------------------------

def _ticket_json(t, coord, plan, tracker, live, uses_review, now, runs_info, review_starts=()) -> dict:
    runs = t["runs"]
    states = {run.id: effective(run, live, now, runs_info.get(run.id, {}).get("mtime", 0.0)) for run in runs}
    derived, note, review_info = _derive(t, runs, states, live, uses_review, review_starts, now)
    last_ts = max([r.last_ts for r in runs] + [t["question"].ts if t["question"] else 0.0], default=0.0)
    row = t["plan"]
    plan_state = row.state if row else ""
    source = "runs" if derived else ""
    state = derived
    if plan_state == "odrzucone":
        state, source = "odrzucone", "plan"
    elif not derived:
        state, source = plan_state, "plan" if plan_state else ""
    elif plan_state and plan and plan.updated_ts > last_ts:
        state, source = plan_state, "plan"
    issue = t["issue"]
    tracked = youtrack_state(issue.get("state", "")) if issue else ""
    running = any(s == RUNNING for s in states.values())
    if not state and tracked:
        state, source = tracked, "tracker"
    elif tracked in ("gotowe", "odrzucone") and not running and state not in ("gotowe", "odrzucone") \
            and source != "plan":
        state, source = tracked, "tracker"
    if not state:
        state, source = "todo", source or "plan"
    if state == "czeka" and t["question"]:
        note = "czeka na ciebie: „" + _clip(t["question"].text, 160) + "”"
    url = ""
    if issue and issue.get("url"):
        url = issue["url"]
    elif plan and plan.tracker == "youtrack" and plan.tracker_url and t["id"] and not t["key"].startswith("~"):
        url = f"{plan.tracker_url}/issue/{t['id']}"
    branch = (row.branch if row and row.branch else "") or t["branch"]
    agents = _agents(runs, states)
    return {
        "key": t["key"],
        "id": t["id"],
        "title": t["title"],
        "officialTitle": _clip(t["official"], 200),
        "officialFrom": t["officialFrom"] if t["official"] else "",
        "officialNote": _clip(t["officialNote"], 300),
        "taskTitle": t["taskTitle"],
        "state": state,
        "column": COLUMN_OF[state],
        "word": STATE_WORDS[state],
        "source": source,
        "derived": derived or "",
        "planState": plan_state,
        "planNote": row.note if row else "",
        # Do raportu porannego („Na daily” — czym jest ticket): początek opisu z YouTracka i początek
        # polecenia pierwszego podagenta, który nad nim pracował.
        "description": (issue.get("description") or "").strip()[:600] if issue else "",
        "brief": _clip(" ".join(next((r.prompt_head for r in runs if _readable(r.prompt_head)
                                      and role_of(r) != ROLE_REVIEWER), "").split()), 400),
        "planAgent": row.agent if row else "",
        "inPlan": row is not None,
        "trackerState": issue.get("state", "") if issue else "",
        "url": url,
        "branch": branch,
        "note": note,
        "review": review_info,
        "runs": [run.id for run in runs],
        "agents": agents,
        "running": running,
        "question": {"text": _clip(t["question"].text, 400), "ts": round(t["question"].ts, 3)} if t["question"] else None,
        "lastActivityTs": round(last_ts, 3),
        "events": _events(t, runs, states, runs_info),
    }


def _readable(text: str) -> bool:
    """Polecenie do pokazania: zwykły tekst, nie zaszyfrowany blob (Codex trzyma niektóre polecenia
    podagentów zaszyfrowane — „gAAAA…” bez spacji)."""
    head = (text or "").strip()[:120]
    return bool(head) and " " in head


def _agents(runs, states, limit: int = 3) -> list[dict]:
    """Ostatnie rundy podagentów ticketu na kartę: implementer 31 min, code-reviewer 8 min,
    implementer · poprawki (trwa) — kontynuacja tego samego podagenta to osobny wiersz."""
    rows = []
    for run in runs:
        role = role_of(run)
        for i, round_ in enumerate(run.rounds):
            last = i == len(run.rounds) - 1
            state = states[run.id] if last else round_.status
            rows.append({"run": run.id, "role": role, "label": _agent_label(run), "state": state,
                         "word": role_word(role, round_.kind),
                         "startTs": round(round_.start, 3),
                         "endTs": round(round_.end, 3) if state != RUNNING else 0,
                         "note": "poprawki" if round_.kind == "continue" and role != ROLE_REVIEWER else ""})
    rows.sort(key=lambda r: r["startTs"])
    return rows[-limit:]


def _derive(t, runs, states, live, uses_review, review_starts=(), now: float = 0.0):
    """(stan, notatka, review) z przebiegów albo ("", "", None) bez przebiegów."""
    if t["question"]:
        return "czeka", "", None
    if not runs:
        return "", "", None
    running = [r for r in runs if states[r.id] == RUNNING]
    if any(role_of(r) == ROLE_REVIEWER for r in running):
        return "review", "", None
    if running:
        return "w-toku", "", None
    reviews = [r for r in runs if role_of(r) == ROLE_REVIEWER and _result(r)]
    workers = [r for r in runs if role_of(r) != ROLE_REVIEWER]
    last_review = max(reviews, key=lambda r: r.end_ts or r.last_ts, default=None)
    last_work = max(workers, key=lambda r: r.end_ts or r.last_ts, default=None)
    if last_review is not None:
        info = review.parse(_result(last_review))
        info_json = {k: info[k] for k in ("blocker", "major", "nit", "ok", "verdict", "top")}
        info_json["text"] = review.summary_text(info)
        if last_work is not None and (last_work.end_ts or last_work.last_ts) > (last_review.end_ts or last_review.last_ts):
            if states[last_work.id] == COMPLETED:
                note = "poprawki po review" if not info["ok"] else "zmiany po review"
                return "gotowe", note + ", bez ponownego review", info_json
            return "w-toku", _stopped(states[last_work.id]), info_json
        if info["ok"]:
            return "gotowe", "review OK", info_json
        return "w-toku", "po review: " + review.summary_text(info) + " — do poprawy", info_json
    if last_work is not None:
        state = states[last_work.id]
        if state != COMPLETED:
            return "w-toku", _stopped(state), None
        if all(role_of(r) == ROLE_HELPER for r in workers):
            return "gotowe", "rozpoznanie skończone", None
        end = last_work.end_ts or last_work.last_ts
        skipped = now - end > SKIPPED_REVIEW and any(ts > end for ts in review_starts)
        if uses_review and live and not skipped:
            return "review", "gotowe, czeka na review", None
        return "gotowe", "bez review", None
    return "w-toku", "", None


def _stopped(state: str) -> str:
    return {FAILED: "podagent zakończył się błędem", KILLED: "podagent zatrzymany",
            INTERRUPTED: "przerwany — koordynator już nie działa"}.get(state, "")


def _result(run) -> str:
    for round_ in reversed(run.rounds):
        if round_.result:
            return round_.result
    return ""


def _events(t, runs, states, runs_info) -> list[dict]:
    """Oś czasu ticketu po polsku: „Koordynator zlecił implementację” → „Implementacja skończona” →
    „Review skończone” (uwagi słowami) → „Koordynator zlecił poprawki”… Angielski opis zlecenia,
    podagent, model i „tło” idą do `meta` (drobny dopisek), a `text` mówi, co wyszło."""
    events = []
    for run in runs:
        role = role_of(run)
        label = _agent_label(run)
        what = [label] + ([_model_name(run.model)] if run.model else []) + (["tło"] if run.background else [])
        for i, round_ in enumerate(run.rounds):
            word = role_word(role, round_.kind)
            nom, acc, done, lasts = ROLE_WORDS[word]
            asked = run.description if round_.kind == "launch" else (round_.note or "")
            meta = " · ".join(([f"„{_clip(asked, 90)}”"] if asked else []) + what)
            events.append({"ts": round(round_.start, 3), "kind": round_.kind, "state": "neutral", "run": run.id,
                           "title": f"Koordynator zlecił {acc}", "text": "", "meta": meta, "word": word})
            last = i == len(run.rounds) - 1
            status = states[run.id] if last else round_.status
            if status == RUNNING:
                action = runs_info.get(run.id, {}).get("lastAction") or run.last_action
                events.append({"ts": round(round_.start, 3), "kind": "running", "state": "working", "run": run.id,
                               "title": f"{lasts} {nom}", "text": action or "", "durationS": 0, "now": True,
                               "word": word})
                continue
            duration = max(0.0, (round_.end or round_.start) - round_.start)
            event = {"ts": round(round_.end or round_.start, 3), "kind": "finished", "state": "done", "run": run.id,
                     "durationS": round(duration), "title": f"{nom[:1].upper() + nom[1:]} {done}", "text": "",
                     "word": word}
            if status in (FAILED, KILLED, INTERRUPTED):
                event["state"] = "stopped"
                ending = {FAILED: "z błędem", KILLED: "zatrzyman" + done[-1], INTERRUPTED: "przerwan" + done[-1]}
                event["title"] = f"{nom[:1].upper() + nom[1:]} {ending[status]}"
                event["text"] = _lead(round_.summary or round_.result)
            elif role == ROLE_REVIEWER and round_.result:
                info = review.parse(round_.result)
                event["title"] = f"{nom[:1].upper() + nom[1:]}: {review.human_text(info)}"
                event["text"] = "do poprawy" if not info["ok"] else "bez zastrzeżeń" if not info["nit"] \
                    else "drobne uwagi, bez błędów blokujących"
                event["review"] = {k: info[k] for k in ("blocker", "major", "nit", "ok", "verdict")}
                if info["top"]:
                    event["quote"] = info["top"]
                event["state"] = "done" if info["ok"] else "attention"
            else:
                event["text"] = _lead(round_.result) or _lead(round_.summary)
            events.append(event)
    if t["question"]:
        events.append({"ts": round(t["question"].ts, 3), "kind": "question", "state": "waiting", "run": "",
                       "title": "Czeka na ciebie", "text": _clip(t["question"].text, 300), "now": True})
    events.sort(key=lambda e: (e["ts"], 0 if e["kind"] in ("launch", "continue") else 1))
    return events[-40:]


def _run_json(run, coord, matcher, tickets, live, now, runs_info) -> dict:
    data = runs_info.get(run.id, {})
    state = effective(run, live, now, data.get("mtime", 0.0))
    ticket = next((k for k, t in tickets.items() if run in t["runs"]), "")
    role = role_of(run)
    info = None
    if role == ROLE_REVIEWER and _result(run):
        parsed = review.parse(_result(run))
        info = {k: parsed[k] for k in ("blocker", "major", "nit", "ok", "verdict")}
        info["text"] = review.summary_text(parsed)
    current = run.current
    return {
        "id": run.id,
        "ticket": ticket,
        "role": role,
        "agentType": run.agent_type,
        "label": _agent_label(run),
        "description": run.description,
        "nickname": run.nickname,
        "model": _model_name(run.model),
        "background": run.background,
        "state": state,
        "startTs": round(run.start_ts, 3),
        "endTs": round(run.end_ts, 3) if state != RUNNING else 0,
        "rounds": len(run.rounds),
        "lastAction": data.get("lastAction") or run.last_action,
        "branch": run.branch or data.get("branch", ""),
        "summary": _clip(current.summary, 300) if current else "",
        "hasResult": bool(_result(run)) or bool(run.transcript),
        "review": info,
    }


def _agent_label(run) -> str:
    if run.tool == "codex":
        return run.nickname or run.agent_type or "podagent"
    kind = run.agent_type or "podagent"
    return "podagent" if kind in ("general-purpose", "fork") else kind


def _model_name(model: str) -> str:
    """„claude-opus-5-5” → „Opus”, „gpt-5.3-codex” → „gpt-5.3-codex”, „sonnet” → „Sonnet”."""
    model = (model or "").strip()
    match = re.search(r"(opus|sonnet|haiku|fable)", model, re.I)
    if match:
        return match.group(1).capitalize()
    return model


def _title(description: str, display: str) -> str:
    text = strip_ticket(description, display) if display else (description or "")
    text = ROLE_PREFIX.sub("", text, count=1) if review.is_review("", text) else text
    text = text.strip()
    return (text[:1].upper() + text[1:]) if text else display


def _lead(text: str, limit: int = 300) -> str:
    """Początek raportu podagenta: pierwsze zdania (bez nagłówków i Markdownu), do `limit` znaków."""
    from ..reports import standup  # tu, nie na górze: reports.plans importuje board (cykl)
    return standup.first_sentences(text or "", 2, limit)


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
