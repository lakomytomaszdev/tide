"""Akcje i dane pochodne tablicy — bez Qt (testowane bezpośrednio):

- `annotate(sessions, boards)` — pole `coordination` przy sesji koordynatora w /Agents
  (widżet agentów pokazuje „TOM-20 · 8/13”),
- `cache_payload(boards)` i `write_cache(path, payload)` — mały plik dla raportu porannego
  ($XDG_STATE_HOME/tide/coordination.json, 0600): bez poleceń i pełnych raportów podagentów, tylko
  tickety, stany, krótkie notatki, gałąź, wynik review w liczbach, pierwsza linia wyniku pracy
  (do 220 znaków), pytanie bez odpowiedzi (do 300), uwagi z planu (300), początek opisu z YouTracka
  (400) i początek polecenia pierwszego podagenta (300) — z nich raport składa „Na daily”,
- `diff_target(board, ticket, worktrees)` i `diff_argv(...)` — „Diff gałęzi”: `git diff
  <główna>...<gałąź>` w Konsoli (repozytorium z listy worktrees, gałąź musi istnieć lokalnie),
- `worktree_of(board, ticket, worktrees)` — worktree z gałęzią ticketu (przycisk „Worktree”).
"""
import json
import os
import subprocess
import tempfile
from ..gitcommand import git_argv

CACHE_VERSION = 1
GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"}


def cache_path(env=None) -> str:
    env = os.environ if env is None else env
    state = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local/state")
    return os.path.join(state, "tide", "coordination.json")


def annotate(sessions: list, boards: list) -> None:
    by_id = {b["id"]: b for b in boards or ()}
    for session in sessions:
        b = by_id.get(session.get("id"))
        if b is None:
            continue
        counts = b.get("counts") or {}
        session["coordination"] = {
            "label": b.get("label") or "",
            "done": counts.get("done", 0),
            "total": counts.get("total", 0),
            "text": (b.get("progress") or {}).get("text", ""),
            "working": counts.get("w-toku", 0) + counts.get("review", 0),
            "waiting": counts.get("czeka", 0),
            "running": b.get("running", 0),
        }


def cache_payload(boards: list, generated: float) -> dict:
    out = []
    for b in boards:
        out.append({
            "id": b["id"], "tool": b["tool"], "project": b.get("project", ""), "repoPath": b.get("repoPath", ""),
            "cwd": b.get("cwd", ""), "title": b.get("title", ""), "label": b.get("label", ""),
            "state": b.get("state", ""), "live": b.get("live", False),
            "startedTs": b.get("startedTs", 0), "lastActivityTs": b.get("lastActivityTs", 0),
            "counts": b.get("counts", {}), "progress": b.get("progress", {}),
            "plan": {k: (b.get("plan") or {}).get(k, "") for k in ("path", "title", "tracker", "status", "updated",
                                                                   "goal")}
            if b.get("plan") else None,
            "tracker": {k: (b.get("tracker") or {}).get(k) for k in ("kind", "available", "error")},
            "question": _clip(((b.get("question") or {}).get("text") or ""), 300),
            # Do „Czeka na mnie” na żywo (reports/data.py): od kiedy i czy to pytanie, czy zgoda.
            "questionTs": (b.get("question") or {}).get("ts") or 0,
            "questionKind": (b.get("question") or {}).get("kind") or "question",
            "tickets": [{"id": t["id"], "title": t["title"], "state": t["state"], "note": t.get("note", ""),
                         "lastActivityTs": t.get("lastActivityTs", 0), "url": t.get("url", ""),
                         "branch": t.get("branch", ""), "review": _review(t), "result": _result_line(t),
                         "question": _clip(((t.get("question") or {}).get("text") or ""), 300),
                         "questionTs": (t.get("question") or {}).get("ts") or 0,
                         "planNote": _clip(t.get("planNote") or "", 300),
                         "description": (t.get("description") or "").strip()[:400],
                         "brief": _clip(t.get("brief") or "", 300),
                         # Opis karty po ludzku (describe.py, decyzja 54) — krótkie napisy, nie polecenia.
                         "done": _clip(t.get("done") or "", 300), "now": _clip(t.get("now") or "", 300)}
                        for t in b.get("tickets", [])],
        })
    return {"version": CACHE_VERSION, "generated": round(generated, 3), "coordinators": out}


def _review(ticket: dict) -> str:
    """„1 MAJOR, 2 NIT” albo „bez uwag” z ostatniego review ticketu (pusty bez review)."""
    review = ticket.get("review") or {}
    return str(review.get("text") or "") if isinstance(review, dict) else ""


def _result_line(ticket: dict) -> str:
    """Pierwsza linia wyniku ostatniej skończonej pracy (nie review) — „co zrobił” w jednym zdaniu."""
    for event in reversed(ticket.get("events") or []):
        if event.get("kind") == "finished" and "review" not in event and event.get("state") == "done":
            return _clip(event.get("text") or "", 220)
    return ""


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def write_cache(path: str, payload: dict) -> None:
    """Zapis atomowy, tylko dla właściciela (plik zawiera tytuły ticketów)."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".coordination-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_cache(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == CACHE_VERSION else None


def _ticket(board: dict, key: str) -> dict | None:
    return next((t for t in board.get("tickets", []) if t.get("key") == key or t.get("id") == key), None)


def _repo_item(board: dict, worktrees: list) -> dict | None:
    """Główny katalog repozytorium koordynatora z listy worktrees (stąd też gałąź główna)."""
    repo = board.get("repoPath") or ""
    for item in worktrees or ():
        if item.get("isMain") and (item.get("repoPath") == repo or item.get("path") == repo):
            return item
    cwd = os.path.realpath(board.get("cwd") or "/")
    best = None
    for item in worktrees or ():
        path = os.path.realpath(item.get("path") or "/")
        if cwd == path or cwd.startswith(path.rstrip("/") + "/"):
            if best is None or len(path) > len(best.get("path") or ""):
                best = item
    return best


def worktree_of(board: dict, key: str, worktrees: list) -> str:
    ticket = _ticket(board, key)
    branch = (ticket or {}).get("branch") or ""
    if not branch:
        return ""
    for item in worktrees or ():
        if item.get("branch") == branch and not item.get("missing"):
            return item.get("path") or ""
    return ""


def diff_target(board: dict, key: str, worktrees: list, git=None) -> dict | None:
    """{repo, main, branch} albo None, gdy gałęzi nie ma albo nie znamy repozytorium."""
    ticket = _ticket(board, key)
    branch = (ticket or {}).get("branch") or ""
    item = _repo_item(board, worktrees)
    if not branch or item is None:
        return None
    repo = item.get("repoPath") or item.get("path") or ""
    main = item.get("mainBranch") or "master"
    if branch == main or not _branch_exists(repo, branch, git):
        return None
    return {"repo": repo, "main": main, "branch": branch}


def diff_argv(target: dict) -> list[str]:
    """Polecenie dla Konsoli: najpierw podsumowanie plików, potem pełny diff w pagerze."""
    spec = f"{target['main']}...{target['branch']}"
    return git_argv("-C", target["repo"], "diff", "--stat", "--patch", spec)


def _branch_exists(repo: str, branch: str, git=None) -> bool:
    if git is not None:
        return git(repo, branch)
    if branch.startswith("-") or not repo or not os.path.isdir(repo):
        return False
    try:
        result = subprocess.run(git_argv("-C", repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"),
                                capture_output=True, timeout=5, env=dict(os.environ, **GIT_ENV))
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
