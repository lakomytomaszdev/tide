"""Dane raportu dla okna Tide (widok „Raporty”): `report.json` obok stron HTML i odczyt dni.

Widok w oknie Tide nigdy nie czyta ani nie renderuje HTML-a raportu. Przy każdym raporcie
generate() zapisuje obok stron `report.json` (0600) — ten sam przegląd co indeks (kolejność kart,
„Na daily” każdego projektu, liczniki, „Poza projektami”) i gotowe teksty „Kopiuj jako tekst” z tego
samego generatora co przycisk na stronie (render.daily_text). Usługa podaje go przez D-Bus
(/Reports Days() i Day(doba)); widok dostaje zwykły JSON i pokazuje go jako zwykły tekst.

Raport sprzed tej zmiany (bez `report.json`) też jest na liście dni: z czasem pliku index.html,
liczbą stron projektów i nazwami z nazw plików (`legacy`), bez „Na daily” — do otwarcia w przeglądarce.
"""
import json
import os
import re
from datetime import datetime

from ..board.marker import is_board_repo
from . import render, schedule, standup

NAME = "report.json"
VERSION = 1
GENITIVE = ("poniedziałku", "wtorku", "środy", "czwartku", "piątku", "soboty", "niedzieli")
PAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")   # slug strony projektu (collect.assign_slugs)
FILE_MAX = 4 * 1024 * 1024                             # większego report.json nie czytamy


# --- zapis (generate) ------------------------------------------------------------------------

def since_text(start: float, end: float) -> str:
    """„Od poniedziałku 07:00” (do 6 dni wstecz), dalej „Od 12.10 07:00”."""
    if not start:
        return ""
    a, b = schedule.local(start), schedule.local(end or start)
    days = (b.date() - a.date()).days
    if days == 0:
        return f"Od {a:%H:%M}"
    if days < 7:
        return f"Od {GENITIVE[a.weekday()]} {a:%H:%M}"
    return f"Od {a.day}.{a.month:02d} {a:%H:%M}"


def _items(items) -> list[dict]:
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        entry = {"ref": str(item.get("ref") or ""), "text": str(item.get("text") or "")}
        for key in ("about", "next", "kind", "sessionId"):
            if item.get(key):
                entry[key] = str(item[key])
        if item.get("sessionId"):
            entry["since"] = float(item.get("since") or 0)
        if item.get("more"):
            entry["more"] = True
        out.append(entry)
    return out


def _notes(notes) -> list[dict]:
    """Notki „Skończone dziś” do report.json — same napisy i liczby (reports/notes.py)."""
    out = []
    for note in notes or []:
        if not isinstance(note, dict) or not note.get("text"):
            continue
        out.append({"id": str(note.get("id") or ""), "ref": str(note.get("ref") or ""),
                    "title": str(note.get("title") or ""), "text": str(note.get("text") or ""),
                    "summary": str(note.get("summary") or ""), "manual": bool(note.get("manual")),
                    "unreviewed": bool(note.get("unreviewed")),
                    "checklist": [str(c) for c in note.get("checklist") or [] if c][:8],
                    "ts": float(note.get("ts") or 0)})
    return out


def _outside(report) -> list[dict]:
    """Sesje spoza repozytoriów jak w zwiniętej sekcji indeksu: czekające, pracujące, reszta."""
    sessions = [(p, s) for p in report.outside for s in p.sessions]
    order = {"waiting": 0, "working": 1}
    out = []
    for p, s in sorted(sessions, key=lambda ps: (order.get(ps[1].state, 2), -ps[1].last_ts)):
        state, word = render.state_of(s)
        said = standup.clean(getattr(s, "waiting_text", "") or "", 320) if state == "waiting" else \
            standup.first_sentences(s.final_message, 2, 320, whole=True)
        task = standup.clean(getattr(s, "prompt", "") or "", 300)
        out.append({"project": p.name, "path": p.path, "tool": s.tool, "state": state, "word": word,
                    "title": s.title or "", "task": task if task != s.title else "", "said": said or "",
                    "lastTs": s.last_ts or 0})
    return out


def build(report) -> dict:
    """Przegląd raportu do report.json — kolejność kart jak w indeksie (render.overview). Projekty z samymi
    porządkami (bez niczego, co czeka) nie są kartami: idą do `choreProjects` (jedna zwinięta linia)."""
    summary = getattr(report, "standup", None) or {}
    by_name = summary.get("projects") or {}
    notes = getattr(report, "notes", None) or {}
    repos = list(report.repos)
    cards = []
    chore_projects = []
    for i, p in enumerate(repos):
        s = by_name.get(p.name) or {}
        waits = [item for item in s.get("waiting") or [] if not item.get("more")]
        hot = bool(waits or p.waiting)
        n = standup.counts(s)
        chores_only = bool(n.get("chores")) and not any(n.get(k) for k in standup.GROUP_KEYS + ("attention",))
        label = render.project_label(p)
        entry = {
            # name — klucz (nazwa katalogu: notki, wybór w widoku), label — nazwa z tablicy pracy do pokazania,
            # folder — nazwa katalogu jako mały dopisek, gdy różni się od nazwy z tablicy.
            "name": p.name, "label": label, "folder": p.name if label != p.name and p.name not in label else "",
            "slug": p.slug, "page": f"{p.slug}.html", "path": p.path,
            "tools": sorted({x.tool for x in p.sessions}),
            "headline": s.get("headline") or s.get("theme") or "",
            "theme": s.get("theme") or "",
            "counts": n, "hot": hot, "choresOnly": chores_only,
            "commits": p.commits, "sessions": len(p.sessions), "waitingSessions": len(p.waiting),
            "lastTs": p.last_ts or 0,
            "plans": [{"label": plan.get("label") or "", "done": plan.get("done") or 0, "total": plan["total"]}
                      for plan in getattr(p, "plans", None) or () if plan.get("total")],
            "copyText": render.daily_text(report, [p]),
            "finished": _notes(notes.get(p.name)),
        }
        for key in standup.ALL_KEYS:
            entry[key] = _items(s.get(key))
        if chores_only and not hot and not entry["finished"]:
            chore_projects.append({"name": p.name, "label": label, "slug": p.slug, "page": entry["page"],
                                   "path": p.path, "chores": entry["chores"]})
            continue
        cards.append(((not hot, chores_only, i), entry))
    projects = [entry for _key, entry in sorted(cards, key=lambda c: c[0])]
    outside = _outside(report)
    brief = [str(b) for b in summary.get("brief") or [] if b]
    return {
        "version": VERSION,
        "day": report.day,
        "title": render.long_date(report.day).capitalize(),
        "generatedAt": report.generated,
        "start": report.start,
        "end": report.end,
        "period": render.period_text(report),
        "since": since_text(report.start, report.end),
        "source": render._source(summary),
        "counts": {
            "projects": len(repos),
            "waiting": sum(p["counts"].get("waiting", 0) for p in projects),
            "waitingSessions": len(report.waiting),
            "commits": sum(p.commits for p in report.active),
            "sessions": sum(len(p.sessions) for p in report.active),
            "outside": len(outside),
            "outsideWaiting": sum(1 for s in outside if s["state"] == "waiting"),
            "quiet": len(report.quiet),
        },
        "brief": brief,
        "briefSource": summary.get("briefSource") or ("model" if summary.get("source") == "model" else "rules"),
        "copyAll": render.daily_text(report, repos, brief=True),
        "projects": projects,
        "choreProjects": chore_projects,
        "outside": outside,
    }


# --- na żywo (bieżąca doba) ---------------------------------------------------------------------
# „Czeka na ciebie” znaczy: agent stoi teraz (obszar Agents, decyzja 36). Raport to migawka z chwili
# złożenia, więc dla bieżącej doby Day(), Days() i Status() przeliczają to przy każdym odczycie:
# pozycja z sessionId, której sesja już nie czeka (albo czeka od później — na coś innego), dostaje
# `stale` („już nieaktualne”, nie liczy się do plakietek), a `now` to lista tego, co czeka teraz.

SINCE_SLACK = 90.0      # s — ta sama blokada, gdy sesja czeka od (prawie) tej samej chwili
NOW_SHOWN = 3
NOW_QUESTION = 200      # znaków pytania w „Teraz czeka na ciebie” (dłuższe — bez cytatu)
NOW_DETAIL = 100        # znaków opisu zgody (np. „Bash · make install”)


def waiting_sessions(sessions) -> dict[str, dict]:
    return {s.get("id"): s for s in sessions or () if isinstance(s, dict) and s.get("state") == "waiting" and s.get("id")}


def is_stale(item: dict, waiting: dict[str, dict]) -> bool:
    sid = item.get("sessionId")
    if not sid:
        return False   # test, który nie przechodzi, konflikt numerów — nie zależą od sesji
    live = waiting.get(sid)
    if live is None:
        return True
    return float(live.get("sinceTs") or 0) > float(item.get("since") or 0) + SINCE_SLACK


def _project_of(session: dict, projects: list[dict]) -> tuple[str, str]:
    """(nazwa projektu — klucz, nazwa do pokazania z tablicy pracy) sesji: po katalogu, potem po nazwie."""
    cwd = os.path.realpath(session.get("cwd") or "/") if session.get("cwd") else ""
    best, best_len = None, -1
    for p in projects:
        path = os.path.realpath(p.get("path") or "") if p.get("path") else ""
        if path and cwd and (cwd == path or cwd.startswith(path.rstrip("/") + "/")) and len(path) > best_len:
            best, best_len = p, len(path)
    if best is None:
        best = next((p for p in projects if p.get("name") and p.get("name") == session.get("project")), None)
    if best is not None:
        return best.get("name") or "", best.get("label") or best.get("name") or ""
    return session.get("project") or "", session.get("project") or ""


def now_waiting(sessions, projects: list[dict]) -> list[dict]:
    """Sesje, które czekają teraz (jak widżet agentów), od najdłużej czekającej."""
    out = []
    for s in sorted(waiting_sessions(sessions).values(), key=lambda s: s.get("sinceTs") or 0):
        project, label = _project_of(s, projects)
        out.append({"id": s.get("id"), "project": project, "label": label, "tool": s.get("tool") or "",
                    "title": standup.clean(s.get("title") or "", 120),
                    "text": standup.clean(s.get("waitingText") or "", 300), "reason": s.get("reason") or "",
                    "since": float(s.get("sinceTs") or 0)})
    return out


def now_text(waiting: list[dict]) -> str:
    """Jedno zdanie do „Dziś w skrócie”: „Teraz czeka na ciebie: LogTool — agent pyta: „Pushować?””."""
    if not waiting:
        return ""
    parts = []
    for w in waiting[:NOW_SHOWN]:
        where = w.get("label") or w["project"] or w["title"] or "agent"
        # Pytanie tylko w całości (fit) — za długie zastępuje ogólne „czeka na odpowiedź”, bez „…” w pół zdania.
        if w["reason"] == "question" or (w["reason"] != "approval" and w["text"].endswith("?")):
            question = standup.fit(w["text"], NOW_QUESTION)
            what = f"agent pyta: „{question}”" if question else "agent czeka na odpowiedź"
        else:
            detail = standup.fit(w["text"], NOW_DETAIL)
            what = "agent czeka na twoją zgodę" + (f" ({detail})" if detail else "")
        parts.append(f"{where} — {what}")
    more = len(waiting) - NOW_SHOWN
    return "Teraz czeka na ciebie: " + "; ".join(parts) + (f"; i {more} więcej" if more > 0 else "") + "."


def apply_live(data: dict, today: str, sessions=None, notes: dict | None = None) -> dict:
    """report.json z bieżącym stanem: notki „Skończone dziś” (każda doba) i — tylko dla bieżącej doby —
    `stale` przy nieaktualnych pozycjach „Czeka na mnie”, `now` (co czeka teraz), przeliczone liczniki
    i teksty do schowka, które zgadzają się z tym, co widać."""
    data = json.loads(json.dumps(data))   # kopia — report.json zostaje migawką z chwili złożenia
    projects = [p for p in data.get("projects") or [] if isinstance(p, dict)]
    live = data.get("day") == today and sessions is not None
    # Notka bez projektu albo z repozytorium tablicy pracy (sprzed PT-28) nie robi karty „tide-board”.
    notes = {k: v for k, v in (notes or {}).items() if v and k and not _board_notes(v)}
    known = {p.get("name") for p in projects}
    for name, items in notes.items():
        if name not in known:
            # Projekt bez karty w porannym raporcie (praca zaczęła się później) — karta z samymi notkami.
            slug = collect_slug(name)
            projects.insert(0, {"name": name, "slug": slug, "page": "", "path": items[0].get("repoPath") or "",
                                "tools": [], "headline": "", "theme": "", "counts": {}, "hot": False,
                                "choresOnly": False, "commits": 0, "sessions": 0, "waitingSessions": 0,
                                "lastTs": max(float(n.get("ts") or 0) for n in items), "plans": [],
                                **{k: [] for k in standup.ALL_KEYS}})
            data["choreProjects"] = [c for c in data.get("choreProjects") or [] if c.get("name") != name]
    waiting = waiting_sessions(sessions) if live else {}
    now = now_waiting(sessions, projects) if live else []
    for p in projects:
        if p.get("name") in notes:
            p["finished"] = _notes(notes[p["name"]])
        if live:
            for item in p.get("waiting") or []:
                if isinstance(item, dict) and not item.get("more"):
                    item["stale"] = is_stale(item, waiting)
        fresh = [i for i in p.get("waiting") or [] if isinstance(i, dict) and not i.get("stale")]
        counts = dict(p.get("counts") or {})
        counts["waiting"] = standup.counts({"waiting": fresh})["waiting"]
        counts["finished"] = len(p.get("finished") or [])
        p["counts"] = counts
        if live:
            p["hot"] = bool([i for i in fresh if not i.get("more")]) or any(w["project"] == p.get("name") for w in now)
        p["copyText"] = standup.as_text({"projects": {p["name"]: p}}, [p["name"]], _heading(data),
                                        notes={p["name"]: p.get("finished") or []})
    if live:
        data["now"] = now
        data["nowText"] = now_text(now)
        data["live"] = True
    projects.sort(key=lambda p: (not p.get("hot"), not p.get("finished") and bool(p.get("choresOnly"))))
    data["projects"] = projects
    counts = dict(data.get("counts") or {})
    counts["waiting"] = sum((p.get("counts") or {}).get("waiting", 0) for p in projects)
    counts["finished"] = sum(len(p.get("finished") or []) for p in projects)
    if live:
        counts["waitingNow"] = len(now)
    data["counts"] = counts
    brief = list(data.get("brief") or []) + ([data["nowText"]] if data.get("nowText") else [])
    data["copyAll"] = standup.as_text({"projects": {p["name"]: p for p in projects}}, [p["name"] for p in projects],
                                      _heading(data), brief=brief,
                                      notes={p["name"]: p.get("finished") or [] for p in projects})
    return data


def _board_notes(items: list) -> bool:
    paths = {str(n.get("repoPath") or "") for n in items if isinstance(n, dict)} - {""}
    return bool(paths) and all(is_board_repo(p) for p in paths)


def _heading(data: dict) -> str:
    return f"Na daily · {render.long_date(data.get('day') or '')}" if data.get("day") else "Na daily"


def collect_slug(name: str) -> str:
    from .collect import slug_base   # collect importuje git i skanery — tylko gdy potrzeba
    return slug_base(name)


def write(folder: str, data: dict) -> None:
    fd = os.open(os.path.join(folder, NAME), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


# --- odczyt (D-Bus Days(), Day()) ------------------------------------------------------------

def valid_day(day) -> bool:
    if not isinstance(day, str) or not schedule.DAY_DIR.match(day):
        return False
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _folder(root: str, day: str) -> str:
    """Katalog raportu doby albo pusty napis (zła nazwa, brak, dowiązanie)."""
    if not valid_day(day) or day not in schedule.report_days(root):
        return ""
    return os.path.join(root, day)


def load(root: str, day: str) -> dict | None:
    """report.json doby (słownik) albo None — brak pliku, dowiązanie, za duży, zły JSON."""
    folder = _folder(root, day)
    if not folder:
        return None
    path = os.path.join(folder, NAME)
    try:
        if os.path.islink(path) or not os.path.isfile(path) or os.path.getsize(path) > FILE_MAX:
            return None
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        return None
    return data


def _pages(folder: str) -> list[str]:
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    out = []
    for name in names:
        slug, ext = os.path.splitext(name)
        if ext == ".html" and slug != "index" and PAGE_RE.match(slug) \
                and not os.path.islink(os.path.join(folder, name)):
            out.append(slug)
    return out


def _mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def days(root: str, state: schedule.State, today: str, keep_days: int, sessions=None, notes_of=None) -> dict:
    """Lista dni z raportem, od najnowszego, dla lewej kolumny widoku. Bieżąca doba — „czeka” na żywo
    (sessions: lista z obszaru Agents), notes_of(doba) → notki „Skończone dziś”."""
    names = schedule.report_days(root)
    latest = names[-1] if names else ""
    out = []
    for day in reversed(names):
        data = load(root, day)
        if data is not None and day == today and sessions is not None:
            data = apply_live(data, today, sessions, notes_of(day) if notes_of else None)
        folder = os.path.join(root, day)
        if data is not None:
            counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
            generated = data.get("generatedAt") if isinstance(data.get("generatedAt"), (int, float)) else None
            out.append({"day": day, "generatedAt": generated or _mtime(os.path.join(folder, "index.html")),
                        "projects": _count(counts.get("projects")), "waiting": _count(counts.get("waiting")),
                        "data": True})
        else:
            out.append({"day": day, "generatedAt": _mtime(os.path.join(folder, "index.html")),
                        "projects": len(_pages(folder)), "waiting": None, "data": False})
    return {"root": root, "today": today, "keepDays": keep_days, "latest": latest,
            "unread": bool(latest) and state.opened != latest, "days": out}


def day(root: str, day_name: str, state: schedule.State, today: str = "", sessions=None, notes=None) -> dict:
    """Raport doby dla widoku: report.json z polami ok, latest i unread (i stanem na żywo — apply_live);
    raport sprzed report.json — legacy z nazwami stron; brak doby — {ok: false, error: "missing"}."""
    folder = _folder(root, day_name)
    if not folder:
        return {"ok": False, "error": "missing", "day": day_name if valid_day(day_name) else ""}
    names = schedule.report_days(root)
    latest = names[-1] if names else ""
    meta = {"ok": True, "day": day_name, "latest": day_name == latest,
            "unread": day_name == latest and state.opened != day_name,
            "index": os.path.join(folder, "index.html")}
    data = load(root, day_name)
    if data is None:
        pages = _pages(folder)
        return dict(meta, legacy=True, title=render.long_date(day_name).capitalize(),
                    generatedAt=_mtime(meta["index"]),
                    counts={"projects": len(pages)},
                    projects=[{"name": slug, "slug": slug, "page": f"{slug}.html"} for slug in pages],
                    outside=[])
    if sessions is not None or notes:
        data = apply_live(data, today, sessions, notes)
    return dict(data, **meta, legacy=False)


def page_path(root: str, day_name: str, project: str = "") -> str:
    """Ścieżka strony do otwarcia: index.html doby albo <slug>.html; pusty napis, gdy to nie
    strona tego raportu (zła doba, slug spoza [a-z0-9._-], brak pliku, dowiązanie)."""
    folder = _folder(root, day_name)
    if not folder:
        return ""
    if not project:
        return os.path.join(folder, "index.html")
    if not isinstance(project, str) or project == "index" or not PAGE_RE.match(project):
        return ""
    path = os.path.join(folder, f"{project}.html")
    if os.path.islink(path) or not os.path.isfile(path):
        return ""
    return path
