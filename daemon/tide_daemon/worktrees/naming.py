"""Czytelne nazwy worktrees: tytuł z nazwy gałęzi, narzędzie, które go założyło.

Gałęzie agentów mają długie, techniczne nazwy (`codex/summary-tabs-03-version-axis`). W oknie
chcemy krótki tytuł („summary-tabs 03 · version axis”) i osobno wyciszony znacznik z prefiksu
(„codex”). Reguły są heurystyczne — gdy nic nie pasuje, zostaje reszta nazwy bez zmian.
"""
import re

# Prefiksy, które są tylko kategorią gałęzi, a nie jej treścią (trafiają do znacznika).
PREFIXES = ("codex", "claude", "feat", "feature", "fix", "bugfix", "hotfix", "docs", "doc", "chore",
            "refactor", "test", "tests", "perf", "ci", "build", "style", "release", "wip", "exp")
# Słowa, od których zaczyna się opis zmiany wewnątrz nazwy (summary-tabs-fix-card-list-header).
TYPE_WORDS = {"fix", "hotfix", "bugfix", "feat", "feature", "docs", "doc", "test", "tests", "chore", "refactor",
              "perf"}
TOOLS = {"codex": "codex", "claude": "claude"}

_DATE_SUFFIX = re.compile(r"-(\d{4}-\d{2}(?:-\d{2})?)$")
_CODE = re.compile(r"^(?=.*\d)(?=.*[a-z])[a-z0-9]+$")  # la035, f10, v3, mr483 — litery z cyframi
_HEX = re.compile(r"^[0-9a-f]{12,}$")


def humanize_branch(branch: str) -> tuple[str, str]:
    """(znacznik, tytuł) z nazwy gałęzi, np. „codex/summary-tabs-03-version-axis” →
    („codex”, „summary-tabs 03 · version axis”)."""
    if not branch:
        return "", ""
    tag = ""
    rest = branch
    if "/" in rest:
        head, _, tail = rest.partition("/")
        if head.lower() in PREFIXES and tail:
            tag, rest = head.lower(), tail
    if not tag and rest.startswith("worktree-") and len(rest) > len("worktree-"):
        tag, rest = "worktree", rest[len("worktree-"):]
    rest = _DATE_SUFFIX.sub("", rest) or rest
    # Zagnieżdżone segmenty (a/b/c) czytamy jak kolejne człony tytułu.
    segments = [s for s in rest.split("/") if s]
    title = " · ".join(_humanize_segment(s) for s in segments) if segments else rest
    return tag, title


def _humanize_segment(segment: str) -> str:
    tokens = [_short_hex(t) for t in segment.split("-") if t]
    if len(tokens) < 2:
        return segment if not tokens else tokens[0]
    lower = [t.lower() for t in tokens]

    # „fix-unique-phrase-entry” → „fix · unique phrase entry”
    if lower[0] in TYPE_WORDS:
        return _join(tokens[0], tokens[1:])
    # „tom-128-index-generation-plan” → „tom-128 · index generation plan” (zgłoszenie z numerem)
    if tokens[0].isalpha() and tokens[1].isdigit():
        return _join(f"{tokens[0]}-{tokens[1]}", tokens[2:])
    # „la035-atomic-open-approval” → „la035 · atomic open approval”
    if _CODE.match(lower[0]):
        return _join(tokens[0], tokens[1:])
    # Seria: „summary-tabs-03-version-axis” → „summary-tabs 03 · version axis”,
    #        „summary-tabs-fix-card-list-header” → „summary-tabs · fix card list header”.
    for i in range(1, len(tokens)):
        if tokens[i].isdigit() or _CODE.match(lower[i]):
            return _join("-".join(tokens[:i]) + " " + tokens[i], tokens[i + 1:])
        if lower[i] in TYPE_WORDS:
            return _join("-".join(tokens[:i]), tokens[i:])
    return "-".join(tokens)


def _join(key: str, words: list[str]) -> str:
    return f"{key} · {' '.join(words)}" if words else key


def _short_hex(token: str) -> str:
    return token[:8] if _HEX.match(token) else token


def guess_tool(path: str, tag: str) -> str:
    """Narzędzie z położenia albo prefiksu gałęzi, gdy nie znamy sesji: codex, claude albo pusty."""
    if "/.codex/worktrees/" in path:
        return "codex"
    if "/.claude/worktrees/" in path:
        return "claude"
    return TOOLS.get(tag, "")
