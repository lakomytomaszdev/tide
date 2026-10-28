"""Dane do podglądu okna „Tide: nowe zadanie” (make preview-new-task) z listy worktrees tego komputera.

Użycie: /usr/bin/python3 tools/new_task_preview.py worktrees.json "polecenie" [models.json [--local]] > dane.json
Tylko odczyt: repozytoria z `tide-daemon.py --print-worktrees`, propozycja gałęzi z naming.py
(bez gita), więc nic nie powstaje w prawdziwych repozytoriach. models.json to stan z
`tide-daemon.py --print-models` (Status() obiektu /Models); --local dokłada przykładową rolę `agent`
(qwen3-coder:30b na pierwszym serwerze, gdy rola nie jest ustawiona) i wybiera „Lokalny”; propozycja gałęzi
idzie wtedy z programem agenta lokalnego (`agentProgram`, domyślnie Codex), jak w oknie.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "daemon"))
from tide_daemon.tasks import list_repos  # noqa: E402
from tide_daemon.tasks.core import worktree_path  # noqa: E402
from tide_daemon.worktrees.naming import branch_from_prompt  # noqa: E402


def main(argv: list[str]) -> int:
    with open(argv[0], encoding="utf-8") as fh:
        repos = list_repos(json.load(fh))
    prompt = argv[1] if len(argv) > 1 else ""
    local = "--local" in argv[2:]
    models = None
    paths = [a for a in argv[2:] if a != "--local"]
    if paths:
        with open(paths[0], encoding="utf-8") as fh:
            models = json.load(fh)
    if local and isinstance(models, dict) and "agent" not in (models.get("roles") or {}):
        endpoint = models.get("default") or next(iter(models.get("endpoints") or {"local": {}}))
        models.setdefault("roles", {})["agent"] = {"endpoint": endpoint, "model": "qwen3-coder:30b", "ctx": None,
                                                   "installed": True, "available": True, "error": ""}
    data = {"repos": repos, "models": models,
            "defaults": {"repoPath": repos[0]["path"] if repos else "", "tool": "claude", "newWorktree": True,
                         "tools": {"claude": True, "codex": True}, "terminal": True,
                         "model": "local" if local else "cloud"}}
    if repos:
        program = (models or {}).get("agentProgram") if isinstance(models, dict) else None
        tool = (program if program in ("claude", "codex") else "codex") if local else "claude"
        branch = branch_from_prompt(prompt, tool)
        data["suggestion"] = {"ok": True, "branch": branch, "base": repos[0]["mainBranch"],
                              "path": worktree_path(repos[0]["path"], branch)}
    json.dump(data, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
