"""Raport poranny: jedna strona HTML na projekt z ruchem i indeks z kartami projektów.

- schedule.py — kiedy (pierwsze logowanie w dobie albo o 07:00), za jaki okres, retencja, stan,
- sessions.py — sesje agentów z okresu i ostatnie wiadomości, przebiegi zaplanowanych zadań,
- gitlog.py   — commity z okresu po gałęziach i worktrees,
- limits.py   — migawka Limitów AI z QuotaApp,
- collect.py  — model raportu, render.py — HTML.

generate() składa raport w katalogu tymczasowym obok docelowego i podmienia go w całości,
więc przeglądarka nigdy nie widzi połowy raportu, a strony projektów, które w nowej wersji
nie mają ruchu, znikają.
"""
import json
import os
import shutil
import tempfile

from . import collect, limits, render, schedule

__all__ = ["collect", "generate", "limits", "load_palette", "render", "schedule"]


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
             git=None, max_depth: int = 3, home: str | None = None) -> tuple[str, "collect.Report"]:
    """Zapisuje <root>/<day>/index.html i <projekt>.html. Zwraca (ścieżka index.html, raport)."""
    if limits_result is None and fetch_limits:
        limits_result = limits.fetch_status()
    days = [d for d in schedule.report_days(root) if d < day]
    report = collect.build(day, start, end, workspace=workspace, claude_root=claude_root, codex_root=codex_root,
                           live=live, worktrees=worktrees, limits_result=limits_result, git=git,
                           max_depth=max_depth, previous_day=days[-1] if days else "")
    home = os.path.expanduser("~") if home is None else home
    pages = {"index.html": render.render_index(report, palette, home)}
    for project in report.active:
        pages[f"{project.slug}.html"] = render.render_project(report, project, palette, home)

    os.makedirs(root, mode=0o700, exist_ok=True)
    target = os.path.join(root, day)
    work = tempfile.mkdtemp(prefix=f".{day}-", dir=root)
    try:
        for name, text in pages.items():
            # Raport zawiera wycinki rozmów z agentami — czytelny tylko dla właściciela.
            fd = os.open(os.path.join(work, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
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
