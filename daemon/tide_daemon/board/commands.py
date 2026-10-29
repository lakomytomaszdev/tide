"""Listy poleceń projektu dla kroków w tle. Zgoda dotyczy pełnego polecenia, nie całego programu."""
import json
import os
import shlex

ALLOWED_KEY = "allowed-commands"
DENIED_KEY = "denied-commands"
DEFAULT_ALLOWED = ("git status *", "git diff *", "git log *", "git show *", "git add *", "git commit *",
                   "git rev-parse *", "git branch *", "git worktree *", "git merge *",
                   "rg *", "grep *", "cat *", "sed *", "ls *", "find *", "pwd", "wc *",
                   "python3 -m unittest *", "python3 -m pytest *", "pytest *", "make test", "make lint",
                   "npm test", "npm run build", "dotnet test *", "dotnet build *", "go test *", "cargo test *")
TIDE_DENIED = ("make install",)
MAX_COMMANDS = 100
MAX_COMMAND = 300


def defaults(slug: str, repo: str) -> tuple[list[str], list[str]]:
    tide = slug == "plasma-tide" or os.path.basename(os.path.normpath(repo or "")) == "plasma-tide"
    return list(DEFAULT_ALLOWED), list(TIDE_DENIED if tide else ())


def validate(value, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_COMMANDS:
        raise ValueError(f"{label}: oczekiwano listy do {MAX_COMMANDS} poleceń")
    out = []
    for command in value:
        if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND or \
                any(c in command for c in "\n\r\0"):
            raise ValueError(f"{label}: polecenie musi być pojedynczą linią do {MAX_COMMAND} znaków")
        text = command.strip()
        if text not in out:
            out.append(text)
    return out


def read(raw: str, fallback: list[str], label: str, errors: list, path: str) -> list[str]:
    if not raw.strip():
        return list(fallback)
    try:
        return validate(json.loads(raw), label)
    except (ValueError, TypeError) as exc:
        errors.append({"path": path, "message": f"{label}: {exc}"})
        return list(fallback)


def is_forbidden(command: str, denied: list[str]) -> bool:
    text = " ".join(str(command or "").split())
    return any(text == rule or text.startswith(rule + " ") for rule in denied)


def is_allowed(command: str, allowed: list[str]) -> bool:
    text = " ".join(str(command or "").split())
    if text in allowed:
        return True
    try:
        parts = list(shlex.shlex(text, posix=True, punctuation_chars=";&|<>"))
    except ValueError:
        return False
    if any(part in (";", "&&", "||", "|", "&", ">", ">>", "<", "<<") for part in parts):
        return False
    return any(rule.endswith(" *") and (text == rule[:-2] or text.startswith(rule[:-2] + " "))
               for rule in allowed)


def claude_tools(allowed: list[str], denied: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Wzorce Bash dla Claude CLI; dokładna zgoda nie staje się zgodą na cały program."""
    grants = []
    for rule in allowed:
        if rule.endswith(" *"):
            grants.extend((f"Bash({rule[:-2]})", f"Bash({rule})"))
        else:
            grants.append(f"Bash({rule})")
    bans = []
    for rule in denied:
        bans.extend((f"Bash({rule})", f"Bash({rule} *)"))
    return tuple(grants), tuple(bans)
