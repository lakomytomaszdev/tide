"""Worktrees repozytoriów z katalogu projektów: stan, rozmiar, bezpieczne sprzątanie scalonych
i usuwanie pozostałych po potwierdzeniu (bez głównych katalogów i gałęzi głównych — nigdy)."""
from .launchers import Launchers
from .naming import guess_tool, humanize_branch
from .scanner import (ACTIVE_WINDOW, GROUPS, HARD_BLOCKERS, MAX_DEPTH, SIZE_TTL, WorktreeScanner, agent_cwds,
                      find_repositories, group_of, scan)
from .sessions import SessionIndex

__all__ = ["ACTIVE_WINDOW", "GROUPS", "HARD_BLOCKERS", "Launchers", "MAX_DEPTH", "SIZE_TTL", "SessionIndex",
           "WorktreeScanner",
           "agent_cwds", "find_repositories", "group_of", "guess_tool", "humanize_branch", "scan"]
