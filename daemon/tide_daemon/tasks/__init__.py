"""Nowe zadanie dla agenta: repozytorium, narzędzie (Claude Code albo Codex), polecenie, opcjonalnie
nowy worktree — i karta Konsoli, w której agent startuje z tym poleceniem (opis: docs/tide-daemon.md)."""
from .core import (IMAGE_LIMIT, PROMPT_LIMIT, TOOL_LABELS, TOOLS, Plan, Planner, TaskError, TaskFiles, agent_argv,
                   list_repos, task_title, worktree_dir)

__all__ = ["IMAGE_LIMIT", "PROMPT_LIMIT", "TOOLS", "TOOL_LABELS", "Plan", "Planner", "TaskError", "TaskFiles",
           "agent_argv", "list_repos", "task_title", "worktree_dir"]
