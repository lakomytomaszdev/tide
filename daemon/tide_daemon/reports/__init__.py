"""Raport poranny: strona HTML na każdy projekt-repozytorium z ruchem (z własnym „Na daily”) i indeks
— przegląd z kartą na projekt i zwiniętą sekcją „Poza projektami”.

- schedule.py — kiedy (pierwsze logowanie w dobie albo o 07:00), za jaki okres, retencja, stan,
- sessions.py — sesje agentów z okresu i ostatnie wiadomości, przebiegi zaplanowanych zadań,
- gitlog.py   — commity z okresu po gałęziach i worktrees,
- limits.py   — migawka Limitów AI z QuotaApp,
- plans.py    — plany i tickety (tablica koordynacji i plany w repozytoriach), dodatek opcjonalny,
- standup.py  — „Na daily”: fakty o projektach, podsumowanie z reguł, walidacja odpowiedzi modelu,
- summarize.py — opis „Na daily” modelem przez lokalny `claude -p` albo model lokalny (z regułami jako zapasem),
- collect.py  — model raportu, render.py — HTML,
- data.py     — report.json dla widoku „Raporty” w oknie Tide i odczyt dni (D-Bus Days(), Day()).

generate() składa raport w katalogu tymczasowym obok docelowego i podmienia go w całości,
więc przeglądarka nigdy nie widzi połowy raportu, a strony projektów, które w nowej wersji
nie mają ruchu, znikają.
"""
import json
import os
import shutil
import tempfile

from . import collect, data, limits, render, schedule, standup, summarize

__all__ = ["collect", "data", "generate", "limits", "load_palette", "render", "schedule", "standup", "summarize"]


def load_palette(candidates: list[str]) -> dict:
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            continue
    raise RuntimeError("raport: nie znaleziono palety (palette.json obok usługi albo theme/palette/mist.json)")


def generate(root: str, day: str, start: float, end: float, *, palette: dict, workspace: str, claude_root: str,
             codex_root: str, live=None, worktrees=None, limits_result=None, fetch_limits: bool = True,
             git=None, max_depth: int = 3, home: str | None = None,
             coordination_cache: str | None = None, summary: str = "rules", summary_model: str = "sonnet",
             env=None, tracker=None, cancel=None) -> tuple[str, "collect.Report"]:
    """Zapisuje <root>/<day>/index.html, <projekt>.html i report.json (widok „Raporty”). Zwraca (ścieżka index.html, raport).

    summary: "claude" — opis „Na daily” modelem przez `claude -p` (summarize.py), "local" — model lokalny
    z roli `summary` w models.json (summary_model pomijany, nigdy Claude), "rules" — same reguły.
    tracker: YouTrackCache — opisy ticketów z planów w repozytoriach (plans.py, tylko GET) albo None.
    cancel: models.Cancel — zamykanie usługi przerywa wywołania modelu lokalnego (summarize.summarize).
    Domyślnie reguły: model woła tylko usługa i --report według ustawień (report.json)."""
    if limits_result is None and fetch_limits:
        limits_result = limits.fetch_status()
    days = [d for d in schedule.report_days(root) if d < day]
    report = collect.build(day, start, end, workspace=workspace, claude_root=claude_root, codex_root=codex_root,
                           live=live, worktrees=worktrees, limits_result=limits_result, git=git,
                           max_depth=max_depth, previous_day=days[-1] if days else "",
                           coordination_cache=coordination_cache, tracker=tracker)
    home = os.path.expanduser("~") if home is None else home
    target = os.path.join(root, day)
    report.standup = summarize.summarize(report, mode=summary, model=summary_model, cache_dir=target, home=home,
                                         env=env, cancel=cancel)
    pages = {"index.html": render.render_index(report, palette, home)}
    for project in report.repos:
        pages[f"{project.slug}.html"] = render.render_project(report, project, palette, home)
    overview = data.build(report)

    os.makedirs(root, mode=0o700, exist_ok=True)
    work = tempfile.mkdtemp(prefix=f".{day}-", dir=root)
    try:
        for name, text in pages.items():
            # Raport zawiera wycinki rozmów z agentami — czytelny tylko dla właściciela.
            fd = os.open(os.path.join(work, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
        summarize.write_cache(work, report.standup.get("cache"))
        data.write(work, overview)
        os.chmod(work, 0o700)
        old = None
        if os.path.isdir(target) and not os.path.islink(target):
            old = tempfile.mkdtemp(prefix=f".{day}-old-", dir=root)
            os.rmdir(old)
            os.rename(target, old)
        elif os.path.lexists(target):
            os.unlink(target)
        os.rename(work, target)
        work = ""
        if old:
            shutil.rmtree(old, ignore_errors=True)
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)
    return os.path.join(target, "index.html"), report
