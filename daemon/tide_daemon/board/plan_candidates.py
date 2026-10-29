"""Plany projektu, które można przejąć z karty bez zapisu w repozytorium projektu."""
import os
import re

from ..coordination import plan as plans


def candidates(repo: str, board_root: str) -> list[dict]:
    out, seen = [], set()
    for folder in plans.plan_folders([repo], board_root):
        root = os.path.realpath(folder)
        if not (root.startswith(os.path.realpath(repo) + os.sep) or
                root.startswith(os.path.realpath(board_root) + os.sep)):
            continue
        try:
            names = sorted(os.listdir(folder))[:2000]
        except OSError:
            continue
        for name in names:
            if not plans.NAME_RE.fullmatch(name):
                continue
            path = os.path.realpath(os.path.join(folder, name))
            if not path.startswith(root + os.sep) or path in seen:
                continue
            seen.add(path)
            plan = plans.read(path)
            if plan is None or plan.errors:
                continue
            out.append({"path": path, "title": plan.title, "card": plan.card,
                        "status": plan.status, "updated": plan.updated, "updatedTs": plan.updated_ts,
                        "rows": len(plan.rows), "needsCompletion": _needs_completion(plan, path)})
    out.sort(key=lambda p: p["updatedTs"], reverse=True)
    return out


def _needs_completion(plan, path: str) -> bool:
    if any(not (row.agent and row.work_class and row.reviewer and row.note) for row in plan.rows):
        return True
    try:
        with open(path, encoding="utf-8") as fh:
            _meta, body = plans.split_frontmatter(fh.read(plans.MAX_BYTES))
    except (OSError, UnicodeError):
        return True
    if "| Blokery |" not in body or any(line.strip() and not line.lstrip().startswith("|") for line in body.splitlines()):
        return True
    folder = os.path.join(os.path.dirname(path), "tickets")
    for row in plan.rows:
        name = row.ticket.lstrip("#") + ".md"
        candidate = os.path.realpath(os.path.join(folder, name))
        if not candidate.startswith(os.path.realpath(folder) + os.sep):
            return True
        try:
            with open(candidate, encoding="utf-8") as fh:
                if not re.search(r"(?m)^\s*-\s*\[\s*\]\s*\S", fh.read(plans.MAX_BYTES)):
                    return True
        except (OSError, UnicodeError):
            return True
    return False
