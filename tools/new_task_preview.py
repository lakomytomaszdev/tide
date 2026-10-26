"""Dane do podglądu okna „Tide: nowe zadanie” (make preview-new-task) z listy worktrees tego komputera.

Użycie: /usr/bin/python3 tools/new_task_preview.py worktrees.json "polecenie" > dane.json
Tylko odczyt: repozytoria z `tide-daemon.py --print-worktrees`, propozycja gałęzi z naming.py
(bez gita), więc nic nie powstaje w prawdziwych repozytoriach.
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
    data = {"repos": repos, "defaults": {"repoPath": repos[0]["path"] if repos else "", "tool": "claude",
                                         "newWorktree": True, "tools": {"claude": True, "codex": True},
                                         "terminal": True}}
    if repos:
        branch = branch_from_prompt(prompt, "claude")
        data["suggestion"] = {"ok": True, "branch": branch, "base": repos[0]["mainBranch"],
                              "path": worktree_path(repos[0]["path"], branch)}
    json.dump(data, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
