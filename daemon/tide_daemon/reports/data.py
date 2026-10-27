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
        if item.get("about"):
            entry["about"] = str(item["about"])
        if item.get("next"):
            entry["next"] = str(item["next"])
        if item.get("more"):
            entry["more"] = True
        out.append(entry)
    return out


def _outside(report) -> list[dict]:
    """Sesje spoza repozytoriów jak w zwiniętej sekcji indeksu: czekające, pracujące, reszta."""
    sessions = [(p, s) for p in report.outside for s in p.sessions]
    order = {"waiting": 0, "working": 1}
    out = []
    for p, s in sorted(sessions, key=lambda ps: (order.get(ps[1].state, 2), -ps[1].last_ts)):
        state, word = render.state_of(s)
        said = standup.last_question(s.final_message) if state == "waiting" else \
            standup.first_sentences(s.final_message, 2, 320)
        task = standup.clean(getattr(s, "prompt", "") or "", 300)
        out.append({"project": p.name, "path": p.path, "tool": s.tool, "state": state, "word": word,
                    "title": s.title or "", "task": task if task != s.title else "", "said": said or "",
                    "lastTs": s.last_ts or 0})
    return out


def build(report) -> dict:
    """Przegląd raportu do report.json — kolejność kart jak w indeksie (render.overview)."""
    summary = getattr(report, "standup", None) or {}
    by_name = summary.get("projects") or {}
    repos = list(report.repos)
    cards = []
    for i, p in enumerate(repos):
        s = by_name.get(p.name) or {}
        waits = [item for item in s.get("waiting") or [] if not item.get("more")]
        hot = bool(waits or p.waiting)
        n = standup.counts(s)
        chores_only = bool(n.get("chores")) and not any(n.get(k) for k in standup.GROUP_KEYS)
        entry = {
            "name": p.name, "slug": p.slug, "page": f"{p.slug}.html", "path": p.path,
            "tools": sorted({x.tool for x in p.sessions}),
            "headline": s.get("headline") or s.get("theme") or "",
            "theme": s.get("theme") or "",
            "counts": n, "hot": hot, "choresOnly": chores_only,
            "commits": p.commits, "sessions": len(p.sessions), "waitingSessions": len(p.waiting),
            "lastTs": p.last_ts or 0,
            "plans": [{"label": plan.get("label") or "", "done": plan.get("done") or 0, "total": plan["total"]}
                      for plan in getattr(p, "plans", None) or () if plan.get("total")],
            "copyText": render.daily_text(report, [p]),
        }
        for key in standup.ALL_KEYS:
            entry[key] = _items(s.get(key))
        cards.append(((not hot, chores_only, i), entry))
    projects = [entry for _key, entry in sorted(cards, key=lambda c: c[0])]
    outside = _outside(report)
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
        "copyAll": render.daily_text(report, repos),
        "projects": projects,
        "outside": outside,
    }


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


def days(root: str, state: schedule.State, today: str, keep_days: int) -> dict:
    """Lista dni z raportem, od najnowszego, dla lewej kolumny widoku."""
    names = schedule.report_days(root)
    latest = names[-1] if names else ""
    out = []
    for day in reversed(names):
        data = load(root, day)
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


def day(root: str, day_name: str, state: schedule.State) -> dict:
    """Raport doby dla widoku: report.json z polami ok, latest i unread; raport sprzed report.json —
    legacy z nazwami stron; brak doby — {ok: false, error: "missing"}."""
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
