"""„Na daily” — co zrobiono, co trwa, co czeka na użytkownika i co dalej, po projekcie.

Trzy kroki, wszystkie bez I/O (testowane na fixtures):

1. `facts(report)` — fakty o projekcie z modelu raportu: tickety z tablicy koordynacji i planów
   (ID, tytuł z YouTracka/planu, stan, notatka, wynik review, pierwsza linia wyniku pracy, pytanie),
   jednostki pracy (scalona gałąź, gałąź poza główną, grupa commitów w gałęzi głównej, sama sesja)
   z tematami commitów i podpiętymi sesjami (tytuł, stan, pierwsze zdania ostatniej wiadomości),
   zaplanowane zadania. Sesja koordynatora nie jest osobną pracą — jej plan to „Na czym polega”.
2. `rules(fact)` — podsumowanie z reguł (bez modelu): grupowanie po ticketach, potem po gałęziach
   i sesjach; tytuły ticketów zamiast tematów commitów, pierwsze zdania wiadomości agenta jako „po co”.
   Używane, gdy podsumowanie modelem jest wyłączone albo się nie udało.
3. `validate(data, facts)` — odpowiedź modelu (summarize.py) sprawdzona względem schematu:
   tylko znane projekty i ID ticketów, same napisy, przycięte długości i liczby pozycji. Projekt,
   którego model nie opisał (albo opisał źle), dostaje podsumowanie z reguł.

Pozycja: {"ref": ID ticketu albo "", "text": co, "why": po co / co to daje (Zrobione),
"next": następny krok (W toku)}. HTML nie powstaje tutaj — render.py escapuje każdy napis.
"""
import os
import re

from ..worktrees.naming import humanize_branch

GROUPS = (("done", "Zrobione"), ("doing", "W toku"), ("waiting", "Czeka na mnie"), ("next", "Dalej"))
GROUP_KEYS = tuple(k for k, _ in GROUPS)
ITEMS_MAX = 8              # pozycji w grupie (reszta jako „i N więcej”)
TEXT_MAX = 260
WHY_MAX = 320
THEME_MAX = 600
NEXT_TODO = 5
LOOSE_MAX = 2              # tyle gałęzi bez sesji i ticketu pokazujemy osobno
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}-\d{1,6}$")
TICKET_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,9})-(\d{1,6})(?![0-9])")
TICKET_DASHLESS = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,10})-?(\d{1,6})(?![0-9])")  # „la035” w nazwie gałęzi
NOT_TICKETS = {"UTF", "SHA", "ISO", "GPT", "RFC", "HTTP", "HTTPS", "MD", "TLS", "SSL", "CVE", "PEP", "IPV"}
_TAG = re.compile(r"</?[A-Za-z_][\w:.-]{0,40}(?:\s[^<>]{0,200})?/?>")
_HEARTBEAT = re.compile(r"<message>(.*?)</message>", re.S)
CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]{1,40})\))?!?:\s*(?P<rest>.+)$")
ACTIVE = ("w-toku", "review", "czeka")
FAILING = re.compile(r"(?i)(test\w*[^.\n]{0,40}\bnie (?:przechodz|przeszł)\w*|\b[1-9]\d* (?:failed|failing|nie przesz\w*)\b|"
                     r"\bFAIL(?:ED|URES?)\b|\btesty? (?:padaj|czerwon)\w*)")


# --- tekst -----------------------------------------------------------------------------------------

_LINK = re.compile(r"\[([^\]\n]{1,200})\]\([^)\n]{0,500}\)")
_MD = re.compile(r"(\*\*|__|`+)")
_HEAD = re.compile(r"^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)")
_SENT = re.compile(r"(?<=[.!?…])\s+(?=[\"„(]?[A-ZĄĆĘŁŃÓŚŹŻ0-9])")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")


def clean(text, limit: int = 0) -> str:
    """Jedna linia bez znaków sterujących; z limitem — ucięta do granicy słowa z „…”."""
    text = " ".join(_CTRL.sub(" ", str(text or "")).split())
    if limit and len(text) > limit:
        cut = text[: limit - 1]
        space = cut.rfind(" ")
        if space > limit * 0.6:
            cut = cut[:space]
        text = cut.rstrip(" ,;:—-") + "…"
    return text


def plain(text: str) -> str:
    """Markdown → zwykły tekst: odnośniki do samej etykiety, bez gwiazdek, odwrotnych apostrofów,
    nagłówków i punktorów (linie zostają)."""
    text = str(text or "")
    if "<heartbeat" in text:  # wynik zaplanowanego zadania Codexa: liczy się tylko <message>
        found = _HEARTBEAT.search(text)
        text = found.group(1) if found else ""
    out = []
    for line in _TAG.sub("", text).splitlines():
        line = _HEAD.sub("", _MD.sub("", _LINK.sub(r"\1", line)))
        out.append(line.strip())
    return "\n".join(out).strip()


def first_sentences(text: str, n: int = 2, limit: int = 280) -> str:
    """Początek wiadomości agenta: pierwsze zdania pierwszych akapitów. Zbyt krótkie zdanie
    („Gotowe.”) nie liczy się do n — dopiero ono i następne mówią, co zrobiono."""
    body = plain(text)
    if not body:
        return ""
    sentences = []
    for para in re.split(r"\n\s*\n|\n", body):
        para = para.strip()
        if not para:
            continue
        if para[-1] not in ".!?…:;":
            para += "."  # punkt listy bez kropki — inaczej sklei się z następnym
        para = para[:1].upper() + para[1:]
        sentences += [s.strip() for s in _SENT.split(para) if s.strip()]
        if sum(1 for s in sentences if len(s) >= 25) >= n:
            break
    out, counted = [], 0
    for sentence in sentences:
        out.append(sentence)
        counted += len(sentence) >= 25 or len(out) > 1  # tylko krótkie zdanie na początku się nie liczy
        if counted >= n or len(" ".join(out)) >= limit:
            break
    return clean(" ".join(out), limit)


def last_question(text: str, limit: int = 280) -> str:
    """Pytanie z wiadomości agenta, który czeka: ostatnie zdanie ze znakiem zapytania, inaczej początek."""
    body = plain(text)
    questions = [s for para in body.splitlines() for s in _SENT.split(para) if s.strip().endswith("?")]
    return clean(questions[-1], limit) if questions else first_sentences(body, 1, limit)


def failing_tests(text: str) -> str:
    """Zdanie o testach, które nie przechodzą (heurystyka), albo pusty napis."""
    for para in plain(text).splitlines():
        for sentence in _SENT.split(para):
            if FAILING.search(sentence) and not re.search(r"(?i)\b0 failed\b|wszystkie testy przechodzą", sentence):
                return clean(sentence, 220)
    return ""


def subject_parts(subject: str) -> tuple[str, str, str]:
    """„feat(report): daily view” → („feat”, „report”, „daily view”)."""
    match = CONVENTIONAL.match((subject or "").strip())
    if not match:
        return "", "", (subject or "").strip()
    return match.group("type"), (match.group("scope") or ""), match.group("rest").strip()


def ticket_key(ident: str) -> str:
    """Klucz do porównań: „la-035” i „LA-35” → „LA-35”. Pusty, gdy to nie ID ticketu."""
    match = TICKET_IN_TEXT.fullmatch((ident or "").strip())
    if not match or match.group(1).upper() in NOT_TICKETS:
        return ""
    return f"{match.group(1).upper()}-{int(match.group(2))}"


def ticket_ids(text: str, dashless: bool = False) -> list[str]:
    """Klucze ticketów wymienionych w tekście. dashless — także „la035” (tylko do dopasowania
    gałęzi do znanych ticketów, nigdy do wymyślania nowych ID)."""
    pattern = TICKET_DASHLESS if dashless else TICKET_IN_TEXT
    return [f"{m.group(1).upper()}-{int(m.group(2))}" for m in pattern.finditer(text or "")
            if m.group(1).upper() not in NOT_TICKETS]


# --- fakty -----------------------------------------------------------------------------------------

def facts(report) -> list[dict]:
    return [project_facts(report, p) for p in report.active]


def project_facts(report, project) -> dict:
    start = report.start
    plans = list(getattr(project, "plans", None) or [])
    coordinators = {e.get("id") for e in plans if e.get("id")}
    out = {"project": project.name, "slug": project.slug, "main": project.main_branch or "",
           "plans": [], "tickets": [], "units": [], "questions": [],
           "toClean": len(project.to_clean), "unpushed": [w.get("branch") or w.get("name") or "" for w in project.unpushed],
           "mainDirty": max(0, project.main_dirty_count)}

    seen_tickets: dict[str, dict] = {}
    for entry in plans:
        tickets = entry.get("all") or entry.get("tickets") or []
        recent = [t for t in tickets if (t.get("lastActivityTs") or 0) >= start]
        out["plans"].append({
            "title": clean(entry.get("title"), 200), "label": entry.get("label") or "",
            "coordinator": entry.get("tool") or "", "live": bool(entry.get("live")),
            "source": entry.get("source") or "", "done": entry.get("done", 0), "total": entry.get("total", 0),
            "recent": len(recent)})
        if entry.get("question"):
            out["questions"].append({"text": clean(entry["question"], 300), "plan": clean(entry.get("title"), 120)})
        for t in tickets:
            state = t.get("state") or "todo"
            in_period = (t.get("lastActivityTs") or 0) >= start
            if not (in_period or state in ACTIVE or state == "todo"):
                continue
            if state == "gotowe" and not in_period:
                continue
            key = ticket_key(t.get("id") or "") or "~" + clean(t.get("title")).lower()
            if key in seen_tickets:
                continue
            ident = (t.get("id") or "").strip().upper()
            item = {"id": ident if TICKET_RE.match(ident) else "", "key": ticket_key(ident),
                    "title": clean(t.get("title"), 200), "state": state, "note": clean(t.get("note"), 220),
                    "review": clean(t.get("review"), 80), "result": clean(t.get("result"), 240),
                    "question": clean(t.get("question"), 300), "branch": t.get("branch") or "",
                    "plan": clean(entry.get("title"), 120), "ts": t.get("lastActivityTs") or 0}
            seen_tickets[key] = item
            out["tickets"].append(item)

    units = _units(project)
    sessions = [s for s in project.sessions if s.id not in coordinators]
    out["coordinatorSessions"] = [_session_fact(s) for s in project.sessions if s.id in coordinators]
    by_branch = {u["branch"]: u for u in units if u.get("branch")}
    by_worktree = [(os.path.realpath(u["worktree"]), u) for u in units if u.get("worktree")]
    for s in sessions:
        unit = None
        if s.branch and s.branch not in (project.main_branch, "HEAD") and s.branch in by_branch:
            unit = by_branch[s.branch]
        if unit is None and s.cwd:
            real = os.path.realpath(s.cwd)
            for base, candidate in by_worktree:
                if real == base or real.startswith(base.rstrip("/") + "/"):
                    unit = candidate
                    break
        if unit is None:
            branch = s.branch if s.branch not in (project.main_branch, "HEAD") else ""
            unit = {"kind": "session", "title": clean(s.title, 160), "branch": branch, "worktree": "",
                    "commits": [], "count": 0, "sessions": [], "ticket": ""}
            units.append(unit)
            if branch:
                by_branch.setdefault(branch, unit)
        unit["sessions"].append(_session_fact(s))

    by_key = {t["key"]: t for t in out["tickets"] if t["key"]}
    for unit in units:
        branch = unit.get("branch") or ""
        hit = ""
        for t in out["tickets"]:
            if t["branch"] and t["branch"] == branch:
                hit = t["id"] or t["title"]
                break
        if not hit:
            mentioned = ticket_ids(branch, dashless=True) + ticket_ids(unit.get("title", ""))
            if unit.get("ticket"):
                mentioned.insert(0, ticket_key(unit["ticket"]))
            hit = next((by_key[k]["id"] for k in mentioned if k in by_key), "")
            if not hit and unit.get("ticket"):
                hit = unit["ticket"]  # ID z tematu commita, którego nie ma w planie
        unit["ticket"] = hit
    out["units"] = units
    return out


def _session_fact(s) -> dict:
    state = s.state or "idle"
    if state in ("done", "idle") and s.interrupted:
        state = "interrupted"
    message = s.final_message or ""
    fact = {"title": clean(s.title, 160), "tool": s.tool, "state": state, "reason": s.reason or "",
            "message": last_question(message) if state == "waiting" else first_sentences(message, 2, 320),
            "full": message, "failing": failing_tests(message) if state != "working" else "",
            "since": s.since, "first": s.first_ts, "last": s.last_ts,
            "lastAction": clean(s.last_action, 80) if state in ("working", "waiting") else ""}
    if s.runs or s.automation:
        runs = sorted(s.runs, key=lambda r: r.started)
        last = next((r for r in reversed(runs) if r.result), None)
        fact["scheduled"] = {"name": clean((s.automation or {}).get("name") or "Zaplanowane zadanie", 80),
                             "runs": len(runs), "aborted": sum(1 for r in runs if r.aborted),
                             "last": first_sentences(last.result, 1, 200) if last else ""}
    return fact


def _units(project) -> list[dict]:
    """Jednostki pracy z commitów: scalenia i bezpośrednie commity gałęzi głównej, gałęzie poza nią."""
    units = []
    for b in project.branches:
        commits = sorted(b.commits, key=lambda c: c.ts)
        if b.is_main:
            member_of: dict[str, dict] = {}
            for merge in getattr(b, "merges", None) or []:
                name = merge.get("branch") or ""
                tag, title = humanize_branch(name)
                unit = {"kind": "merged", "branch": name, "title": title or name or "scalenie",
                        "worktree": "", "commits": [], "count": 0, "sessions": [], "ticket": "", "into": b.branch}
                units.append(unit)
                for sha in merge.get("members") or []:
                    member_of[sha] = unit
                member_of[merge.get("sha")] = unit
            groups: dict[str, dict] = {}
            for c in commits:
                if c.sha in member_of:
                    unit = member_of[c.sha]
                    if not c.merge:
                        unit["commits"].append(c.subject)
                        unit["count"] += 1
                    continue
                if c.merge:
                    continue
                _type, scope, _rest = subject_parts(c.subject)
                found = [m.group(0).upper() for m in TICKET_IN_TEXT.finditer(c.subject) if ticket_key(m.group(0))]
                ids = found[:1]
                key = ticket_key(ids[0]) if ids else (scope or "")
                unit = groups.get(key)
                if unit is None:
                    unit = groups[key] = {"kind": "main", "branch": "", "scope": scope, "into": b.branch,
                                          "title": key or f"bezpośrednio w {b.branch}", "worktree": "",
                                          "commits": [], "count": 0, "sessions": [], "ticket": ids[0] if ids else ""}
                    units.append(unit)
                unit["commits"].append(c.subject)
                unit["count"] += 1
            continue
        tag, title = humanize_branch(b.branch)
        units.append({"kind": "branch", "branch": b.branch, "title": title or b.name, "worktree": b.worktree or "",
                      "commits": [c.subject for c in commits if not c.merge], "count": sum(1 for c in commits if not c.merge),
                      "sessions": [], "ticket": "", "tool": b.tool})
    return [u for u in units if u["count"] or u["kind"] != "merged" or u["branch"]]


# --- podsumowanie z reguł --------------------------------------------------------------------------

NOTE_PROGRESS = {
    "skończył, czeka na review": ("implementacja gotowa, czeka na review", "review"),
    "przerwany — koordynator już nie działa": ("przerwane w trakcie", "wznowić albo zlecić od nowa"),
    "podagent zatrzymany": ("zatrzymane", "sprawdzić, dlaczego, i wznowić"),
}
NOTE_DONE = {
    "review OK": "review bez uwag",
    "bez review": "",
    "poprawki po review, bez ponownego review": "poprawione po review",
    "zmiany po review, bez ponownego review": "dopracowane po review",
}


def rules(fact: dict) -> dict:
    """Podsumowanie projektu bez modelu."""
    groups = {k: [] for k in GROUP_KEYS}
    used_units: set[int] = set()
    tickets = fact["tickets"]

    for t in tickets:
        linked = [u for u in fact["units"] if u.get("ticket") and u["ticket"] in (t["id"], t["title"])]
        used_units.update(id(u) for u in linked)
        ref, title = t["id"], t["title"] or t["id"]
        message = next((s["message"] for u in linked for s in u["sessions"] if s["message"]), "")
        state, note = t["state"], t["note"]
        if state == "gotowe":
            why = t["result"] or message
            extra = NOTE_DONE.get(note, note)
            if extra and extra not in why:
                why = f"{why} ({extra})" if why else extra[:1].upper() + extra[1:]
            groups["done"].append(_item(ref, title, why=why))
        elif state == "czeka":
            question = t["question"] or (note.split("„", 1)[-1].rstrip("”") if "„" in note else note)
            question = _without_ref(question, ref)
            groups["waiting"].append(_item(ref, f"{title}: {question}" if question else f"{title} — czeka na twoją decyzję"))
        elif state == "todo":
            if len(groups["next"]) < NEXT_TODO:
                groups["next"].append(_item(ref, title))
        elif state == "odrzucone":
            continue
        elif note.startswith("po review:"):
            findings = note[len("po review:"):].replace("— do poprawy", "").strip()
            groups["waiting"].append(_item(ref, f"{title}: review zgłosił {findings} — do poprawy"))
        elif note == "podagent zakończył się błędem":
            groups["waiting"].append(_item(ref, f"{title}: praca zakończyła się błędem"))
        else:
            progress, step = NOTE_PROGRESS.get(note, (note, ""))
            if state == "review":
                progress, step = "trwa review", "poprawki albo scalenie po review"
            if any(s["state"] == "working" for u in linked for s in u["sessions"]):
                progress = progress or "trwa praca"
            groups["doing"].append(_item(ref, f"{title} — {progress}" if progress else title, next_=step))

    loose = []  # gałęzie z commitami bez sesji i ticketu — przy wielu jedna zbiorcza pozycja
    for unit in fact["units"]:
        if id(unit) in used_units:
            continue
        if unit["kind"] == "branch" and not unit["sessions"] and not unit.get("ticket"):
            loose.append(unit)
            continue
        _unit_items(unit, fact, groups)
    if len(loose) > LOOSE_MAX:
        commits = sum(u["count"] for u in loose)
        names = ", ".join(u["branch"] for u in loose[:4]) + (f" i {len(loose) - 4} innych" if len(loose) > 4 else "")
        groups["doing"].append(_item("", f"{len(loose)} " + _plural(len(loose), "gałąź", "gałęzie", "gałęzi")
                                     + f" z niescaloną pracą ({commits} "
                                     + _plural(commits, "commit", "commity", "commitów") + f"): {names}",
                                     next_="przejrzeć, scalić albo usunąć"))
    else:
        for unit in loose:
            _unit_items(unit, fact, groups)

    asked = {t["question"] for t in tickets if t["question"]}
    for q in fact["questions"]:
        if q["text"] in asked:
            continue  # to samo pytanie jest już przy tickecie
        groups["waiting"].append(_item("", f"Pytanie koordynatora („{q['plan']}”): {q['text']}"))
    for s in fact.get("coordinatorSessions") or []:
        if s["state"] == "waiting" and s["message"] and not fact["questions"]:
            groups["waiting"].append(_item("", f"Koordynator „{s['title']}” czeka: {s['message']}"))

    return {"theme": _theme(fact), **{k: _cap(v) for k, v in groups.items()}}


def _unit_items(unit: dict, fact: dict, groups: dict) -> None:
    sessions = unit["sessions"]
    states = {s["state"] for s in sessions}
    title = _unit_title(unit)
    message = next((s["message"] for s in sessions if s["message"] and s["state"] != "waiting"), "")
    for s in sessions:
        if s["failing"]:
            groups["waiting"].append(_item("", f"{title}: {s['failing']}"))
    if "waiting" in states:
        s = next(s for s in sessions if s["state"] == "waiting")
        what = "czeka na zgodę" if s["reason"] == "approval" else "czeka na odpowiedź"
        groups["waiting"].append(_item(unit.get("ticket", ""), f"{title}: {what}" + (f" — {s['message']}" if s["message"] else "")))
        return
    scheduled = [s["scheduled"] for s in sessions if s.get("scheduled")]
    for run in scheduled:
        if run["runs"]:
            text = f"{run['name']}: {run['runs']} " + _plural(run["runs"], "przebieg", "przebiegi", "przebiegów")
            groups["done"].append(_item("", text, why=run["last"]))
    if scheduled and not unit["count"]:
        return  # wątek zaplanowanego zadania: liczą się przebiegi, nie sama sesja
    if "working" in states:
        s = next(s for s in sessions if s["state"] == "working")
        progress = "agent pracuje" + (f" ({s['lastAction']})" if s["lastAction"] else "")
        if unit["count"]:
            progress += f", {unit['count']} " + _plural(unit["count"], "commit", "commity", "commitów") + " na gałęzi"
        groups["doing"].append(_item(unit.get("ticket", ""), f"{title} — {progress}", why=message))
        return
    if unit["kind"] in ("merged", "main"):
        why = message or _commit_why(unit, title)
        groups["done"].append(_item(unit.get("ticket", ""), title, why=why))
        return
    if unit["kind"] == "branch":
        branch = unit["branch"]
        progress = f"{unit['count']} " + _plural(unit["count"], "commit", "commity", "commitów") + f" na gałęzi {branch}, niescalone"
        if "interrupted" in states:
            progress = "przerwane w trakcie, " + progress
        groups["doing"].append(_item(unit.get("ticket", ""), f"{title} — {progress}", why=message,
                                     next_="przejrzeć i scalić"))
        return
    # sama sesja, bez commitów
    if "interrupted" in states:
        groups["doing"].append(_item("", f"{title} — przerwane", next_="wznowić sesję"))
    elif message or states & {"done", "idle", "ended"}:
        groups["done"].append(_item("", title, why=message))


def _without_ref(text: str, ref: str) -> str:
    """„ALPHA-3: czy wydajemy?” → „czy wydajemy?”, gdy ID jest już w polu ref."""
    if ref and text.upper().startswith(ref.upper()):
        return text[len(ref):].lstrip(" :—-")
    return text


def _unit_title(unit: dict) -> str:
    session_titles = [s["title"] for s in unit["sessions"] if s["title"] and s["title"] != "(bez tytułu)"]
    if unit["kind"] == "main":
        scope = unit.get("scope") or ""
        first = subject_parts(unit["commits"][0])[2] if unit["commits"] else ""
        if session_titles and not scope:
            return session_titles[0]
        return f"{scope}: {first}" if scope else first or unit["title"]
    if session_titles:
        return session_titles[0]
    return unit["title"] or unit.get("branch") or "praca"


def _commit_why(unit: dict, title: str = "") -> str:
    """„3 commity scalone do master, m.in. …” — tematy, których nie ma już w tytule pozycji."""
    n = unit["count"]
    if unit["kind"] == "main" and n <= 1:
        return ""
    where = f"scalone do {unit.get('into') or 'gałęzi głównej'}" if unit["kind"] == "merged" else f"w {unit.get('into')}"
    picked = [subject_parts(s)[2] for s in unit["commits"] if subject_parts(s)[0] in ("feat", "fix", "")]
    picked = [p for p in picked if p and p not in title][:2]
    text = f"{n} " + _plural(n, "commit", "commity", "commitów") + f" {where}"
    if picked:
        text += ", m.in. " + "; ".join(picked)
    return text


def _theme(fact: dict) -> str:
    plans = [p for p in fact["plans"] if p["title"]]
    if not plans:
        return ""
    if len(plans) == 1:
        p = plans[0]
        text = f"Plan „{p['title']}”: {p['done']} z {p['total']} " + _plural(p["total"], "ticketu", "ticketów", "ticketów") + " gotowe"
        if p["live"]:
            text += ", koordynator dalej pracuje"
        return text + "."
    parts = [f"„{p['title']}” ({p['done']}/{p['total']})" for p in plans[:4]]
    more = f" i {len(plans) - 4} więcej" if len(plans) > 4 else ""
    return "Plany: " + ", ".join(parts) + more + "."


def _item(ref: str, text: str, why: str = "", next_: str = "") -> dict:
    item = {"ref": ref if TICKET_RE.match(ref or "") else "", "text": clean(text, TEXT_MAX)}
    if why:
        item["why"] = clean(why, WHY_MAX)
    if next_:
        item["next"] = clean(next_, WHY_MAX)
    return item


def _cap(items: list) -> list:
    seen, out = set(), []
    for item in items:
        key = (item["ref"], item["text"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    if len(out) > ITEMS_MAX:
        rest = len(out) - (ITEMS_MAX - 1)
        out = out[: ITEMS_MAX - 1] + [{"ref": "", "text": f"i {rest} więcej — szczegóły niżej", "more": True}]
    return out


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def empty(standup: dict) -> bool:
    return not standup.get("theme") and not any(standup.get(k) for k in GROUP_KEYS)


# --- walidacja odpowiedzi modelu -------------------------------------------------------------------

class Invalid(ValueError):
    pass


def known_refs(fact: dict) -> dict[str, str]:
    """Klucz → ID do pokazania: tickety z planów i tablicy oraz ID z tematów commitów i gałęzi."""
    refs = {}
    for unit in fact["units"]:
        for text in [unit.get("branch", "")] + list(unit["commits"]):
            for match in TICKET_IN_TEXT.finditer(text or ""):
                key = ticket_key(match.group(0))
                if key:
                    refs.setdefault(key, match.group(0).upper())
    refs.update({t["key"]: t["id"] for t in fact["tickets"] if t["key"] and t["id"]})
    return refs


def validate(data, all_facts: list[dict]) -> dict[str, dict]:
    """Odpowiedź modelu → {nazwa projektu: podsumowanie}. Invalid, gdy całość nie pasuje do schematu;
    pojedyncze złe pozycje i nieznane projekty są pomijane (projekt bez pozycji dostanie reguły)."""
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        raise Invalid("brak listy projects")
    names = {f["project"]: f for f in all_facts}
    lower = {n.lower(): n for n in names}
    out: dict[str, dict] = {}
    for entry in data["projects"][:50]:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        name = names.get(entry["name"]) and entry["name"] or lower.get(entry["name"].strip().lower())
        if not name or name in out:
            continue
        refs = known_refs(names[name])
        standup = {"theme": clean(entry.get("theme"), THEME_MAX) if isinstance(entry.get("theme"), str) else ""}
        for key in GROUP_KEYS:
            items = entry.get(key)
            standup[key] = []
            if not isinstance(items, list):
                continue
            for raw in items[:ITEMS_MAX]:
                item = _valid_item(raw, refs, key)
                if item:
                    standup[key].append(item)
        if not empty(standup):
            out[name] = standup
    if not out:
        raise Invalid("żaden projekt z odpowiedzi nie pasuje do schematu")
    return out


def _valid_item(raw, refs: set, group: str) -> dict | None:
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
        return None
    text = clean(raw["text"], TEXT_MAX)
    if not text:
        return None
    ref = raw.get("ref") if isinstance(raw.get("ref"), str) else ""
    item = {"ref": refs.get(ticket_key(ref), "") if ref else "", "text": text}
    if group == "done" and isinstance(raw.get("why"), str) and clean(raw["why"]):
        item["why"] = clean(raw["why"], WHY_MAX)
    if group == "doing" and isinstance(raw.get("why"), str) and clean(raw["why"]):
        item["why"] = clean(raw["why"], WHY_MAX)
    if group == "doing" and isinstance(raw.get("next"), str) and clean(raw["next"]):
        item["next"] = clean(raw["next"], WHY_MAX)
    return item


def merge(model: dict[str, dict], all_facts: list[dict]) -> dict[str, dict]:
    """Model tam, gdzie opisał projekt, reguły dla reszty. „Czeka na mnie” nigdy nie znika: gdy
    model go pominął, a reguły coś tam mają (pytanie, review do poprawy), bierzemy pozycje z reguł."""
    out = {}
    for fact in all_facts:
        fallback = rules(fact)
        chosen = model.get(fact["project"])
        if chosen is None:
            out[fact["project"]] = dict(fallback, source="rules")
            continue
        chosen = dict(chosen, source="model")
        if not chosen.get("waiting") and fallback.get("waiting"):
            chosen["waiting"] = fallback["waiting"]
        out[fact["project"]] = chosen
    return out


# --- tekst do schowka ------------------------------------------------------------------------------

def as_text(standup: dict, names: list[str], heading: str = "") -> str:
    """Zwykły tekst „Na daily” (do przeczytania albo wklejenia na czat zespołu)."""
    lines = [heading] if heading else []
    for name in names:
        s = (standup.get("projects") or {}).get(name)
        if not s or empty(s):
            continue
        if lines:
            lines.append("")
        lines.append(name)
        if s.get("theme"):
            lines.append(f"Na czym polega: {s['theme']}")
        for key, label in GROUPS:
            items = s.get(key) or []
            if not items:
                continue
            lines.append(f"{label}:")
            for item in items:
                line = f"- {item['ref']} {item['text']}" if item.get("ref") else f"- {item['text']}"
                if item.get("why"):
                    line += f" — {item['why']}"
                if item.get("next"):
                    line += f" (dalej: {item['next']})"
                lines.append(line)
    return "\n".join(lines).strip() + "\n" if lines else ""
