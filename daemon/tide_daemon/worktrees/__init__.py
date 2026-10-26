"""Worktrees repozytoriów z katalogu projektów: stan, rozmiar i bezpieczne sprzątanie scalonych."""
from .launchers import Launchers
from .naming import guess_tool, humanize_branch
from .scanner import (ACTIVE_WINDOW, GROUPS, MAX_DEPTH, SIZE_TTL, WorktreeScanner, agent_cwds, find_repositories,
                      group_of, scan)
from .sessions import SessionIndex

__all__ = ["ACTIVE_WINDOW", "GROUPS", "Launchers", "MAX_DEPTH", "SIZE_TTL", "SessionIndex", "WorktreeScanner",
           "agent_cwds", "find_repositories", "group_of", "guess_tool", "humanize_branch", "scan"]
