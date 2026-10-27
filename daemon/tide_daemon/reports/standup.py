"""„Na daily” — po projekcie: praca (co zrobiono, co trwa, co czeka na użytkownika, co dalej)
oddzielona od porządków (tracker, plany, instrukcje agentów, konfiguracja).

Trzy kroki, wszystkie bez I/O (testowane na fixtures):

1. `facts(report)` — fakty o każdym projekcie-repozytorium z ruchem (sesje spoza repozytoriów
   nie są projektem — render.py pokazuje je osobno w „Poza projektami”): tickety z tablicy
   koordynacji i planów (ID, tytuł, początek opisu z YouTracka, początek polecenia podagenta,
   uwagi z planu, stan, notatka, wynik review, pierwsza linia wyniku pracy, pytanie), cele planów,
   jednostki pracy (scalona gałąź, gałąź scalona do gałęzi integracyjnej, gałąź poza główną, grupa
   commitów w gałęzi głównej, sama sesja) z tematami commitów, obszarami zmienionych plików
   i podpiętymi sesjami (tytuł, pierwsze polecenie użytkownika, stan, pierwsze zdania ostatniej
   wiadomości), zaplanowane zadania. Sesja koordynatora nie jest osobną pracą — jej plan i polecenie
   to „Na czym polega”. Wewnętrzne gałęzie agentów (`worktree-agent-*`) pomijamy.
   Każdy ticket i jednostka dostaje podpowiedź `category`: "work" (zmiana w produkcie) albo "chores"
   (porządki) z deterministycznych sygnałów — `classify_*` niżej.
2. `rules(fact)` — podsumowanie z reguł (bez modelu): grupowanie po ticketach, potem po gałęziach
   i sesjach; porządki według samych sygnałów. Używane, gdy podsumowanie modelem jest wyłączone
   albo się nie udało.
3. `validate(data, facts)` — odpowiedź modelu (summarize.py) sprawdzona względem schematu:
   tylko znane projekty i ID ticketów, same napisy, przycięte długości i liczby pozycji, bez
   pozycji-śmieci („nic nie czeka”, „N gałęzi nie jest scalonych”, „bez review”), Markdown w polu
   zamieniony na zwykłe zdania, a lista punktów w jednym polu rozbita na osobne pozycje (PT-13:
   gpt-oss wkładał całe listy do jednego napisu). Projekt, którego model nie opisał (albo opisał
   źle), dostaje podsumowanie z reguł.

Podsumowanie projektu: {"headline": 1–2 zdania do indeksu, "theme": na czym polega praca,
"done"/"doing"/"waiting"/"next": praca, "chores": porządki}. Pozycja: {"ref": ID ticketu albo "",
"text": co, "about": czym to jest / po co (opcjonalnie), "next": następny krok (W toku)}.
HTML nie powstaje tutaj — render.py escapuje każdy napis.
"""
import os
import re
from collections import Counter

from ..worktrees.naming import humanize_branch

GROUPS = (("done", "Zrobione"), ("doing", "W toku"), ("waiting", "Czeka na mnie"), ("next", "Dalej"))
GROUP_KEYS = tuple(k for k, _ in GROUPS)
CHORES = ("chores", "Porządki")
ALL_KEYS = GROUP_KEYS + (CHORES[0],)
ITEMS_MAX = 8              # pozycji w grupie (reszta jako „i N więcej”)
TEXT_MAX = 260
ABOUT_MAX = 320
THEME_MAX = 600
HEADLINE_MAX = 320
NEXT_TODO = 5
LOOSE_MAX = 2              # tyle gałęzi bez sesji i ticketu pokazujemy osobno
AREAS_MAX = 6
TASK_MAX = 600
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
CONFLICT = "Konflikt numerów: "
AGENT_BRANCH = re.compile(r"^worktree-agent-")   # wewnętrzne gałęzie podagentów Claude Code (Agent z izolacją)

# --- praca czy porządki ------------------------------------------------------------------------
# Pliki, których zmiana nie zmienia produktu: instrukcje i plany agentów, ich konfiguracja,
# konfiguracja trackera.
CHORE_PATH = re.compile(
    r"(?i)(?:^|/)(?:CLAUDE(?:\.local)?\.md|AGENTS\.md|GEMINI\.md|\.mcp\.json)$"
    r"|^(?:docs/agents|\.claude|\.codex|\.agents|\.cursor|\.gemini)(?:/|$)"
    r"|(?:^|/)[^/]*youtrack[^/]*(?:/|$)|(?:^|/)\.?(?:jira|linear)(?:[-_.][^/]*)?(?:/|$)")
# Typy commitów bez zmiany produktu i zakresy „docs(…)” o planach i agentach.
CHORE_TYPES = {"chore", "ci", "build"}
CHORE_DOC_SCOPES = {"plan", "plans", "agents", "agent", "tracker", "youtrack"}
# Tematy, polecenia i tytuły o trackerze, migracji zadań i instrukcjach agentów.
CHORE_WORDS = re.compile(
    r"(?i)(\byou ?track|\bjira\b|(?-i:\bLinear(?:a|em|u)?\b)"
    r"|\btracker\w*|\bclaude\.md\b|\bagents\.md\b|\bmyai\b|\binstrukcj\w*\s+(?:dla\s+|do\s+)?agent\w*"
    r"|\bplan\w*\s+prac\w*\s+agent\w*|\bagent (?:instructions|work plan|plan convention)"
    r"|\bkonfiguracj\w*\s+agent\w*|\bstandard\w*\s+plan\w*)")
GENERIC_DIRS = {"src", "lib", "libs", "app", "apps", "packages", "pkg", "internal", "cmd", "modules", "services",
                "projects", "source", "tests", "test", "tools"}



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


_LIST_LINE = re.compile(r"^\s{0,3}(?:[-*+•]\s+|\d+[.)]\s+)")
# Pogrubienie, kod, nagłówek, odnośnik. Bez „__” — to raczej `__init__.py` w poprawnym zdaniu niż Markdown.
_MARKDOWN = re.compile(r"\*\*[^*\n]+\*\*|`[^`\n]+`|^\s{0,3}#{1,6}\s|\[[^\]\n]{1,200}\]\([^)\n]{0,500}\)", re.M)


_INLINE_HEAD = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s*)")


def flatten(text) -> str:
    """Markdown → zwykły tekst w jednej linii: bez znaczników, punktorów i nagłówków, a linie łączone
    jak zdania („Indeks gotowy. Eksport w toku.”) — zamiast sklejania punktów listy w jeden ciąg słów.
    Linia od małej litery to ciąg dalszy zdania (złamany wiersz) — bez kropki. Jedna linia to nie lista:
    „2. etap migracji gotowy” i „- zdanie” zostają z numerem i myślnikiem (bez nich tylko nagłówek)."""
    text = str(text or "")
    if len([line for line in text.splitlines() if line.strip()]) <= 1:
        line = _TAG.sub("", _MD.sub("", _LINK.sub(r"\1", text.strip())))
        return " ".join(_INLINE_HEAD.sub("", line).split())
    parts: list[str] = []
    for line in plain(text).splitlines():
        line = line.strip()
        if not line:
            continue
        if parts and parts[-1][-1] not in ".!?…:;," and (line[0].isupper() or line[0].isdigit()):
            parts[-1] += "."
        parts.append(line)
    return " ".join(parts)


def one_line(text) -> str:
    """Pole odpowiedzi modelu: zwykłe jedno zdanie zostaje bez zmian (poprawna odpowiedź Claude'a się
    nie zmienia, także „2. etap…” albo „1) wersja…”), a tekst z nowymi liniami albo Markdownem
    przechodzi przez flatten()."""
    text = str(text or "")
    if "\n" not in text and "\r" not in text and not _MARKDOWN.search(text):
        return text
    return flatten(text)


def list_items(text) -> list[str]:
    """Lista punktów w Markdownie w jednym polu (co najmniej dwie linie-punkty) → osobne pozycje bez
    znaczników; linie, które nie są punktami (nagłówek „**Zrobione:**”), znikają. Inaczej [text]."""
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    bullets = [line for line in lines if _LIST_LINE.match(line)]
    if len(bullets) < 2:
        return [text]
    return [item for item in (flatten(_LIST_LINE.sub("", line, count=1)) for line in bullets) if item]


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


# --- praca czy porządki ----------------------------------------------------------------------------

def commit_is_chore(subject: str, paths=()) -> bool:
    """Commit bez zmiany produktu: typ chore/ci/build, docs(plan|agents|…), temat o trackerze albo
    same pliki instrukcji, planów i konfiguracji agentów lub trackera."""
    kind, scope, _rest = subject_parts(subject)
    if kind in CHORE_TYPES or (kind == "docs" and scope.lower() in CHORE_DOC_SCOPES):
        return True
    if CHORE_WORDS.search(subject or ""):
        return True
    paths = [p for p in paths or () if p]
    return bool(paths) and all(CHORE_PATH.search(p) for p in paths)


def text_is_chore(*texts: str) -> bool:
    """Polecenie, tytuł albo nazwa planu o trackerze, migracji zadań, instrukcjach i planach agentów."""
    return any(CHORE_WORDS.search(t or "") for t in texts)


def areas(paths) -> list[list]:
    """Obszary zmian: [[katalog, liczba plików], …] od największego. Katalog najwyższego poziomu,
    a dla ogólnych (src, apps, tests, …) — dwa pierwsze człony („src/LegalApp.Web”)."""
    counter: Counter = Counter()
    for path in paths or ():
        parts = [p for p in str(path).split("/") if p]
        if not parts:
            continue
        if len(parts) == 1:
            counter[parts[0]] += 1
        elif parts[0].lower() in GENERIC_DIRS and len(parts) > 2:
            counter["/".join(parts[:2])] += 1
        else:
            counter[parts[0]] += 1
    return [[name, n] for name, n in counter.most_common(AREAS_MAX)]


# --- fakty -----------------------------------------------------------------------------------------

def facts(report) -> list[dict]:
    """Fakty projektów-repozytoriów z ruchem (sesje spoza repozytoriów to nie projekty)."""
    return [project_facts(report, p) for p in report.active if getattr(p, "is_repo", True)]


def project_facts(report, project) -> dict:
    start = report.start
    plans = list(getattr(project, "plans", None) or [])
    coordinators = {e.get("id") for e in plans if e.get("id")}
    out = {"project": project.name, "slug": project.slug, "main": project.main_branch or "",
           "plans": [], "tickets": [], "units": [], "questions": [],
           "toClean": len(project.to_clean),
           "unpushed": [b for b in (w.get("branch") or w.get("name") or "" for w in project.unpushed)
                        if not AGENT_BRANCH.match(b)],
           "mainDirty": max(0, project.main_dirty_count),
           # Konflikty numerów ticketów (reports/plans.py, coordination/numbering.py) — do „Czeka na mnie”.
           "conflicts": [clean(w.get("text"), TEXT_MAX - len(CONFLICT)) for w in getattr(project, "conflicts", None) or []
                         if isinstance(w, dict) and w.get("text")]}

    seen_tickets: dict[str, dict] = {}
    for entry in plans:
        tickets = entry.get("all") or entry.get("tickets") or []
        recent = [t for t in tickets if (t.get("lastActivityTs") or 0) >= start]
        plan_chore = text_is_chore(entry.get("title") or "")
        out["plans"].append({
            "title": clean(entry.get("title"), 200), "label": entry.get("label") or "",
            "goal": clean(entry.get("goal"), 600), "category": "chores" if plan_chore else "work",
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
            note = clean(t.get("note"), 220)
            plan_note = clean(t.get("planNote"), 300)
            if plan_note == note:
                plan_note = ""
            title = clean(t.get("title"), 200)
            item = {"id": ident if TICKET_RE.match(ident) else "", "key": ticket_key(ident),
                    "title": title, "state": state, "note": note, "planNote": plan_note,
                    "description": description(t.get("description")), "brief": clean(t.get("brief"), 300),
                    "review": clean(t.get("review"), 80), "result": clean(t.get("result"), 240),
                    "question": clean(t.get("question"), 300), "branch": t.get("branch") or "",
                    "plan": clean(entry.get("title"), 120), "ts": t.get("lastActivityTs") or 0,
                    "category": "chores" if plan_chore or text_is_chore(title) else "work"}
            seen_tickets[key] = item
            out["tickets"].append(item)

    units = _units(project)
    sessions = [s for s in project.sessions if s.id not in coordinators]
    out["coordinatorSessions"] = [_session_fact(s) for s in project.sessions if s.id in coordinators]
    by_branch = {u["branch"]: u for u in units if u.get("branch")}
    by_worktree = [(os.path.realpath(u["worktree"]), u) for u in units if u.get("worktree")]
    for s in sessions:
        own_branch = s.branch if s.branch and not AGENT_BRANCH.match(s.branch) else ""
        unit = None
        if own_branch and own_branch not in (project.main_branch, "HEAD") and own_branch in by_branch:
            unit = by_branch[own_branch]
        if unit is None and s.cwd:
            real = os.path.realpath(s.cwd)
            for base, candidate in by_worktree:
                if real == base or real.startswith(base.rstrip("/") + "/"):
                    unit = candidate
                    break
        if unit is None:
            branch = own_branch if own_branch not in (project.main_branch, "HEAD") else ""
            unit = _unit("session", branch, clean(s.title, 160))
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
        unit["category"] = _unit_kind(unit)

    # Ticket, którego cała praca w okresie to porządki, sam też jest porządkiem.
    for t in out["tickets"]:
        linked = [u for u in units if u["ticket"] and u["ticket"] in (t["id"], t["title"])]
        if linked and all(u["category"] == "chores" for u in linked):
            t["category"] = "chores"
    out["units"] = units
    return out


_BOILERPLATE = re.compile(r"(?i)^\s*(?:źródło|source|oryginalny identyfikator|original (?:id|key)\w*)\s*:")


def description(text, limit: int = 400) -> str:
    """Opis ticketu z YouTracka jako jedna linia: bez Markdownu i bez wierszy-metryczek po migracji
    („Źródło: …”, „Oryginalny identyfikator: …”)."""
    lines = [line for line in plain(str(text or "")).splitlines() if not _BOILERPLATE.match(line)]
    return clean(" ".join(lines), limit)


def _unit(kind: str, branch: str, title: str, **extra) -> dict:
    unit = {"kind": kind, "branch": branch, "title": title, "worktree": "", "commits": [], "count": 0,
            "chores": 0, "paths": [], "sessions": [], "ticket": ""}
    unit.update(extra)
    return unit


def _add_commit(unit: dict, commit) -> None:
    unit["commits"].append(commit.subject)
    unit["count"] += 1
    paths = list(getattr(commit, "paths", None) or ())
    unit["chores"] += commit_is_chore(commit.subject, paths)
    unit["paths"] += paths


def _unit_kind(unit: dict) -> str:
    """„chores”, gdy wszystkie commity jednostki to porządki, a bez commitów — gdy polecenie
    albo tytuł sesji dotyczy trackera, migracji zadań albo instrukcji agentów."""
    if unit["count"]:
        return "chores" if unit.get("chores", 0) == unit["count"] else "work"
    if any(s.get("scheduled") for s in unit["sessions"]):
        return "work"
    texts = [unit.get("title") or ""] + [s.get("task") or "" for s in unit["sessions"]] \
        + [s.get("title") or "" for s in unit["sessions"]]
    return "chores" if unit["sessions"] and text_is_chore(*texts) else "work"


def _session_fact(s) -> dict:
    state = s.state or "idle"
    if state in ("done", "idle") and s.interrupted:
        state = "interrupted"
    message = s.final_message or ""
    task = clean(plain(getattr(s, "prompt", "") or ""), TASK_MAX)
    title = clean(s.title, 160)
    fact = {"title": title, "task": "" if task == title else task, "tool": s.tool, "state": state,
            "reason": s.reason or "",
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
    """Jednostki pracy z commitów: scalenia i bezpośrednie commity gałęzi głównej, gałęzie poza nią
    (bez wewnętrznych gałęzi podagentów `worktree-agent-*`)."""
    units = []
    for b in project.branches:
        commits = sorted(b.commits, key=lambda c: c.ts)
        if b.is_main:
            member_of: dict[str, dict] = {}
            for merge in getattr(b, "merges", None) or []:
                name = merge.get("branch") or ""
                if AGENT_BRANCH.match(name):
                    unit = _unit("agent", name, name, into=b.branch)  # policzone, ale pominięte niżej
                else:
                    tag, title = humanize_branch(name)
                    unit = _unit("merged", name, title or name or "scalenie", into=b.branch)
                units.append(unit)
                for sha in merge.get("members") or []:
                    member_of[sha] = unit
                member_of[merge.get("sha")] = unit
            groups: dict[str, dict] = {}
            for c in commits:
                if c.sha in member_of:
                    if not c.merge:
                        _add_commit(member_of[c.sha], c)
                    continue
                if c.merge:
                    continue
                _type, scope, _rest = subject_parts(c.subject)
                found = [m.group(0).upper() for m in TICKET_IN_TEXT.finditer(c.subject) if ticket_key(m.group(0))]
                ids = found[:1]
                key = ticket_key(ids[0]) if ids else (scope or "")
                unit = groups.get(key)
                if unit is None:
                    unit = groups[key] = _unit("main", "", key or f"bezpośrednio w {b.branch}", scope=scope,
                                               into=b.branch, ticket=ids[0] if ids else "")
                    units.append(unit)
                _add_commit(unit, c)
            continue
        if AGENT_BRANCH.match(b.branch or ""):
            continue
        tag, title = humanize_branch(b.branch)
        unit = _unit("branch", b.branch, title or b.name, worktree=b.worktree or "", tool=b.tool,
                     mergedInto=getattr(b, "merged_into", "") or "")
        for c in commits:
            if not c.merge:
                _add_commit(unit, c)
        units.append(unit)
    out = []
    for u in units:
        if u["kind"] == "agent" or not (u["count"] or u["kind"] != "merged" or u["branch"]):
            continue
        u["areas"] = areas(u.pop("paths"))
        out.append(u)
    return out


# --- podsumowanie z reguł --------------------------------------------------------------------------

NOTE_PROGRESS = {
    "skończył, czeka na review": ("implementacja gotowa, czeka na review", "review"),  # plik sprzed decyzji 54
    "gotowe, czeka na review": ("implementacja gotowa, czeka na review", "review"),
    "przerwany — koordynator już nie działa": ("przerwane w trakcie", "wznowić albo zlecić od nowa"),
    "podagent zatrzymany": ("zatrzymane", "sprawdzić, dlaczego, i wznowić"),
}
NOTE_DONE = {
    "review OK": "review bez uwag",
    "bez review": "",
    "rozpoznanie skończone": "",
    "poprawki po review, bez ponownego review": "poprawione po review",
    "zmiany po review, bez ponownego review": "dopracowane po review",
}
STATE_SHORT = {"gotowe": "zrobione", "w-toku": "w toku", "review": "w review", "czeka": "czeka", "todo": "do zrobienia"}


def rules(fact: dict) -> dict:
    """Podsumowanie projektu bez modelu."""
    groups = {k: [] for k in ALL_KEYS}
    groups["waiting"] = conflict_items(fact)
    used_units: set[int] = set()
    tickets = fact["tickets"]

    for t in tickets:
        linked = [u for u in fact["units"] if u.get("ticket") and u["ticket"] in (t["id"], t["title"])]
        used_units.update(id(u) for u in linked)
        ref, title = t["id"], t["title"] or t["id"]
        message = next((s["message"] for u in linked for s in u["sessions"] if s["message"]), "")
        state, note = t["state"], t["note"]
        if state == "odrzucone":
            continue
        if state == "czeka":
            question = t["question"] or (note.split("„", 1)[-1].rstrip("”") if "„" in note else note)
            question = _without_ref(question, ref)
            groups["waiting"].append(_item(ref, f"{title}: {question}" if question else f"{title} — czeka na twoją decyzję"))
            continue
        if t.get("category") == "chores":
            groups["chores"].append(_item(ref, f"{title} — {STATE_SHORT.get(state, state)}"))
            continue
        if state == "gotowe":
            about = t["result"] or t.get("description") or t.get("planNote") or message
            extra = NOTE_DONE.get(note, note)
            if extra and extra not in about:
                about = f"{about} ({extra})" if about else extra[:1].upper() + extra[1:]
            groups["done"].append(_item(ref, title, about=about))
        elif state == "todo":
            if len(groups["next"]) < NEXT_TODO:
                groups["next"].append(_item(ref, title, about=t.get("description")))
        elif note.startswith("po review:"):
            findings = note[len("po review:"):].replace("— do poprawy", "").strip()
            groups["doing"].append(_item(ref, f"{title} — review zgłosił {findings}", next_="poprawki po review"))
        elif note == "podagent zakończył się błędem":
            groups["waiting"].append(_item(ref, f"{title}: praca zakończyła się błędem"))
        else:
            progress, step = NOTE_PROGRESS.get(note, (note, ""))
            if state == "review":
                progress, step = "trwa review", "poprawki albo scalenie po review"
            if any(s["state"] == "working" for u in linked for s in u["sessions"]):
                progress = progress or "trwa praca"
            groups["doing"].append(_item(ref, f"{title} — {progress}" if progress else title,
                                         about=t.get("planNote") or t.get("description"), next_=step))

    loose = []  # gałęzie z commitami bez sesji i ticketu — przy wielu jedna zbiorcza pozycja
    for unit in fact["units"]:
        if id(unit) in used_units:
            continue
        if unit["kind"] == "branch" and not unit["sessions"] and not unit.get("ticket") \
                and _category(unit) != "chores" and not unit.get("mergedInto"):
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

    out = {"theme": _theme(fact), **{k: _cap(v) for k, v in groups.items()}}
    out["headline"] = _headline(out)
    return out


def conflict_items(fact: dict) -> list[dict]:
    """„Konflikt numerów: …” — pozycje „Czeka na mnie” z kontroli numerów ticketów."""
    return [_item("", CONFLICT + text) for text in fact.get("conflicts") or [] if text]


def _category(unit: dict) -> str:
    return unit.get("category") or _unit_kind(unit)


def _unit_items(unit: dict, fact: dict, groups: dict) -> None:
    sessions = unit["sessions"]
    states = {s["state"] for s in sessions}
    title = _unit_title(unit)
    message = next((s["message"] for s in sessions if s["message"] and s["state"] != "waiting"), "")
    chores = _category(unit) == "chores"
    for s in sessions:
        if s["failing"]:
            groups["waiting"].append(_item("", f"{title}: {s['failing']}"))
    if "waiting" in states:
        s = next(s for s in sessions if s["state"] == "waiting")
        what = "czeka na zgodę" if s["reason"] == "approval" else "czeka na odpowiedź"
        groups["waiting"].append(_item(unit.get("ticket", ""), f"{title}: {what}" + (f" — {s['message']}" if s["message"] else "")))
        return
    if chores:
        groups["chores"].append(_item(unit.get("ticket", ""), title))
        return
    task = next((s["task"] for s in sessions if s.get("task")), "")
    scheduled = [s["scheduled"] for s in sessions if s.get("scheduled")]
    for run in scheduled:
        if run["runs"]:
            text = f"{run['name']}: {run['runs']} " + _plural(run["runs"], "przebieg", "przebiegi", "przebiegów")
            groups["done"].append(_item("", text, about=run["last"]))
    if scheduled and not unit["count"]:
        return  # wątek zaplanowanego zadania: liczą się przebiegi, nie sama sesja
    if "working" in states:
        s = next(s for s in sessions if s["state"] == "working")
        progress = "agent pracuje" + (f" ({s['lastAction']})" if s["lastAction"] else "")
        if unit["count"]:
            progress += f", {unit['count']} " + _plural(unit["count"], "commit", "commity", "commitów") + " na gałęzi"
        groups["doing"].append(_item(unit.get("ticket", ""), f"{title} — {progress}", about=message or task))
        return
    if unit["kind"] in ("merged", "main") or unit.get("mergedInto"):
        about = message or _commit_why(unit, title)
        groups["done"].append(_item(unit.get("ticket", ""), title, about=about))
        return
    if unit["kind"] == "branch":
        branch = unit["branch"]
        progress = f"{unit['count']} " + _plural(unit["count"], "commit", "commity", "commitów") + f" na gałęzi {branch}, niescalone"
        if "interrupted" in states:
            progress = "przerwane w trakcie, " + progress
        groups["doing"].append(_item(unit.get("ticket", ""), f"{title} — {progress}", about=message or task,
                                     next_="przejrzeć i scalić"))
        return
    # sama sesja, bez commitów
    if "interrupted" in states:
        groups["doing"].append(_item("", f"{title} — przerwane", about=task, next_="wznowić sesję"))
    elif message or states & {"done", "idle", "ended"}:
        groups["done"].append(_item("", title, about=message))


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
    if unit["kind"] == "merged":
        where = f"scalone do {unit.get('into') or 'gałęzi głównej'}"
    elif unit.get("mergedInto"):
        where = f"scalone do {unit['mergedInto']}"
    else:
        where = f"w {unit.get('into')}"
    picked = [subject_parts(s)[2] for s in unit["commits"] if subject_parts(s)[0] in ("feat", "fix", "")]
    picked = [p for p in picked if p and p not in title][:2]
    text = f"{n} " + _plural(n, "commit", "commity", "commitów") + f" {where}"
    if picked:
        text += ", m.in. " + "; ".join(picked)
    return text


def _theme(fact: dict) -> str:
    plans = [p for p in fact["plans"] if p["title"] and p.get("category") != "chores"]
    if not plans:
        return ""
    if len(plans) == 1:
        p = plans[0]
        if p.get("goal"):
            return clean(f"{p['title']}: {first_sentences(p['goal'], 2, 400)}", THEME_MAX)
        text = f"Plan „{p['title']}”: {p['done']} z {p['total']} " + _plural(p["total"], "ticketu", "ticketów", "ticketów") + " gotowe"
        if p["live"]:
            text += ", koordynator dalej pracuje"
        return text + "."
    parts = [f"„{p['title']}” ({p['done']}/{p['total']})" for p in plans[:4]]
    more = f" i {len(plans) - 4} więcej" if len(plans) > 4 else ""
    return "Plany: " + ", ".join(parts) + more + "."


def _headline(summary: dict) -> str:
    """1–2 zdania do karty projektu w indeksie: co zrobiono i co trwa (albo same porządki)."""
    def texts(key, n):
        return [i["text"] for i in summary.get(key) or [] if not i.get("more")][:n]
    parts = []
    if texts("done", 2):
        parts.append("Zrobione: " + "; ".join(texts("done", 2)) + ".")
    if texts("doing", 1):
        parts.append("W toku: " + texts("doing", 1)[0] + ".")
    if not parts and texts("chores", 2):
        parts.append("Same porządki: " + "; ".join(texts("chores", 2)) + ".")
    return clean(" ".join(parts), HEADLINE_MAX)


def _item(ref: str, text: str, about: str = "", next_: str = "") -> dict:
    item = {"ref": ref if TICKET_RE.match(ref or "") else "", "text": clean(text, TEXT_MAX)}
    if about:
        item["about"] = clean(about, ABOUT_MAX)
    if next_:
        item["next"] = clean(next_, ABOUT_MAX)
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
    return not standup.get("theme") and not any(standup.get(k) for k in ALL_KEYS)


def counts(standup: dict) -> dict:
    """Liczniki do karty projektu w indeksie (pozycja „i N więcej” liczy się jako N)."""
    out = {}
    for key in ALL_KEYS:
        n = 0
        for item in standup.get(key) or []:
            if item.get("more"):
                found = re.search(r"\d+", item.get("text") or "")
                n += int(found.group(0)) if found else 0
            else:
                n += 1
        out[key] = n
    return out


# --- walidacja odpowiedzi modelu -------------------------------------------------------------------

class Invalid(ValueError):
    pass


# Pozycje bez treści, które model potrafi dopisać mimo instrukcji.
JUNK_ANY = re.compile(r"(?i)(\bnic (?:już )?nie czeka|\bnie ma (?:teraz |obecnie |już |tu )?(?:żadnych |żadnej |nic )?"
                      r"(?:decyzji|pyta\w*|nic do|czego)|\bbrak (?:decyzji|pyta\w*|zadań)\b|"
                      r"nie potrzebuje (?:odpowiedzi|decyzji)|\bworktree-agent-)")
JUNK_WAITING = re.compile(r"(?i)(^\s*\d+\s+(?:gałęzi|gałęzie|gałąź|worktree\w*|gotow\w+|ticket\w*|zada\w+)\b|"
                          r"\b(?:bez|brak|nie ma(?:ją)?)\s+(?:jeszcze\s+)?(?:ponownego\s+)?review|"
                          r"czekaj\w*\s+na\s+review|\bczeka\s+na\s+review|"
                          r"(?:niescalon|nie\s*(?:jest|są|zostały?)\s*scalon|nie\s+scalon)\w*)")


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


def validate(data, all_facts: list[dict], local: bool = False) -> dict[str, dict]:
    """Odpowiedź modelu → {nazwa projektu: podsumowanie}. Invalid, gdy całość nie pasuje do schematu;
    pojedyncze złe pozycje i nieznane projekty są pomijane (projekt bez pozycji dostanie reguły).

    `local` — odpowiedź modelu lokalnego: dodatkowo poprawki grup według stanu ticketów (_fix_waiting)
    i bez powtórzeń (_dedupe). Odpowiedzi Claude tych heurystyk nie dostają — tam pytanie przy tickecie
    gotowym („wypchnąć?”) albo zaplanowanym to zwykła, poprawna pozycja."""
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
        standup = {key: clean(one_line(entry.get(key)), limit) if isinstance(entry.get(key), str) else ""
                   for key, limit in (("theme", THEME_MAX), ("headline", HEADLINE_MAX))}
        for key in ALL_KEYS:
            items = entry.get(key)
            standup[key] = []
            if not isinstance(items, list):
                continue
            for raw in _expanded(items[:ITEMS_MAX * 2]):
                item = _valid_item(raw, refs, key)
                if item and len(standup[key]) < ITEMS_MAX:
                    standup[key].append(item)
        _move_chores(standup)
        if local:
            _fix_waiting(standup, names[name])
            _dedupe(standup)
        if not empty(standup):
            if not standup["headline"]:
                standup["headline"] = _headline(standup)
            out[name] = standup
    if not out:
        raise Invalid("żaden projekt z odpowiedzi nie pasuje do schematu")
    return out


def _move_chores(standup: dict) -> None:
    """Pozycja Zrobione / W toku, która sama mówi o trackerze albo instrukcjach agentów („migracja
    z Lineara do YouTracka”), to porządek — przenosimy ją do „Porządki” (model czasem je powtarza)."""
    for key in ("done", "doing"):
        keep = []
        for item in standup[key]:
            if text_is_chore(item["text"]):
                if len(standup["chores"]) < ITEMS_MAX:
                    standup["chores"].append({"ref": item["ref"], "text": item["text"]})
            else:
                keep.append(item)
        standup[key] = keep


def _fix_waiting(standup: dict, fact: dict) -> None:
    """Tylko odpowiedź modelu lokalnego: „Czeka na mnie” z ID ticketu, który jeszcze nie ruszył (`todo`),
    przechodzi do „Dalej”, a z ID ticketu gotowego znika; „Dalej” z ID gotowego ticketu też znika — qwen3:8b
    wpisywał tam zaplanowane i skończone tickety (PT-15, prawdziwe dane). Pozycja „Czeka na mnie” zostaje,
    gdy ticket ma w faktach pytanie albo czekającą sesję — wtedy naprawdę coś czeka."""
    states = {t["key"]: t["state"] for t in fact["tickets"] if t.get("key")}
    asking = {t["key"] for t in fact["tickets"] if t.get("key") and t.get("question")}
    asking |= {ticket_key(u.get("ticket") or "") for u in fact["units"]
               if any(sess.get("state") == "waiting" for sess in u.get("sessions") or ())}
    standup["next"] = [item for item in standup["next"]
                       if not item["ref"] or states.get(ticket_key(item["ref"])) != "gotowe"]
    keep = []
    for item in standup["waiting"]:
        key = ticket_key(item["ref"]) if item["ref"] else ""
        state = states.get(key) if key and key not in asking else None
        if state == "todo":
            if len(standup["next"]) < ITEMS_MAX:
                standup["next"].append({"ref": item["ref"], "text": item["text"]})
        elif state != "gotowe":
            keep.append(item)
    standup["waiting"] = keep


def _dedupe(standup: dict) -> None:
    """Ta sama pozycja w kilku grupach (albo dwa razy w jednej) zostaje tylko pierwszy raz, w kolejności
    Zrobione, W toku, Czeka na mnie, Dalej, Porządki (PT-15: qwen3:8b powtarzał „Dalej” w „Porządkach”)."""
    seen = set()
    for key in ALL_KEYS:
        keep = []
        for item in standup[key]:
            norm = (item["ref"], " ".join(item["text"].lower().rstrip(" .").split()))
            if norm in seen:
                continue
            seen.add(norm)
            keep.append(item)
        standup[key] = keep


def _expanded(items: list) -> list:
    """Pozycja, której tekst to lista punktów, staje się kilkoma pozycjami z tym samym `ref`
    (`about` i `next` zostają przy pierwszej)."""
    out = []
    for raw in items:
        if isinstance(raw, str):
            raw = {"text": raw}
        parts = list_items(raw["text"]) if isinstance(raw, dict) and isinstance(raw.get("text"), str) else [None]
        if len(parts) < 2:
            out.append(raw)
            continue
        out.append(dict(raw, text=parts[0]))
        out += [{"ref": raw.get("ref"), "text": part} for part in parts[1:]]
    return out


def _valid_item(raw, refs: dict, group: str) -> dict | None:
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
        return None
    text = clean(one_line(raw["text"]), TEXT_MAX)
    if not text or JUNK_ANY.search(text) or (group == "waiting" and JUNK_WAITING.search(text)):
        return None
    ref = raw.get("ref") if isinstance(raw.get("ref"), str) else ""
    item = {"ref": refs.get(ticket_key(ref), "") if ref else "", "text": text}
    about = raw.get("about") if isinstance(raw.get("about"), str) else raw.get("why")
    if group in ("done", "doing", "next") and isinstance(about, str) and clean(one_line(about)) \
            and not JUNK_ANY.search(about):
        item["about"] = clean(one_line(about), ABOUT_MAX)
    if group == "doing" and isinstance(raw.get("next"), str) and clean(one_line(raw["next"])):
        item["next"] = clean(one_line(raw["next"]), ABOUT_MAX)
    return item


def merge(model: dict[str, dict], all_facts: list[dict]) -> dict[str, dict]:
    """Model tam, gdzie opisał projekt, reguły dla reszty. „Czeka na mnie” nigdy nie znika: gdy
    model go pominął, a reguły coś tam mają (pytanie, test, który nie przechodzi), bierzemy pozycje z reguł."""
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
        conflicts = conflict_items(fact)
        if conflicts:  # model faktów o konfliktach nie dostaje — pozycje zawsze z reguł, na początku
            texts = {c["text"] for c in conflicts}
            chosen["waiting"] = _cap(conflicts + [i for i in chosen.get("waiting") or []
                                                  if i.get("text") not in texts and not i.get("more")])
        out[fact["project"]] = chosen
    return out


# --- tekst do schowka ------------------------------------------------------------------------------

def as_text(standup: dict, names: list[str], heading: str = "") -> str:
    """Zwykły tekst „Na daily” (do przeczytania albo wklejenia na czat zespołu): praca, potem porządki."""
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
        for key, label in GROUPS + (CHORES,):
            items = s.get(key) or []
            if not items:
                continue
            lines.append(f"{label}:")
            for item in items:
                line = f"- {item['ref']} {item['text']}" if item.get("ref") else f"- {item['text']}"
                if item.get("about"):
                    line += f" — {item['about']}"
                if item.get("next"):
                    line += f" (dalej: {item['next']})"
                lines.append(line)
    return "\n".join(lines).strip() + "\n" if lines else ""
