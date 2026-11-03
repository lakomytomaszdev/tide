"""Listy poleceń projektu dla kroków w tle. Zgoda dotyczy pełnego polecenia, nie całego programu."""
import json
import os
import re
import shlex

ALLOWED_KEY = "allowed-commands"
DENIED_KEY = "denied-commands"
DEFAULT_ALLOWED = ("git status *", "git diff *", "git log *", "git show *", "git add *", "git commit *",
                   "git rev-parse *", "git branch *", "git worktree *", "git merge *",
                   "rg *", "grep *", "cat *", "sed *", "ls *", "find *", "pwd", "wc *",
                   "python3 -m unittest *", "python3 -m pytest *", "pytest *", "make test", "make lint",
                   "npm test", "npm run build", "dotnet test *", "dotnet build *", "go test *", "cargo test *")
TIDE_DENIED = ("make install",)
# Polecenia tylko do czytania, dozwolone w każdym projekcie niezależnie od jego listy: krok i tak działa
# w piaskownicy (bez sieci, bez katalogu domowego), a bez nich agenci stawali na `cd … && ls … | head`.
# Zakazy projektu mają pierwszeństwo (Claude CLI stosuje reguły deny przed allow).
ALWAYS_ALLOWED = ("cd *", "pwd", "ls *", "cat *", "head *", "tail *", "wc *", "sort *", "uniq *", "cut *",
                  "tr *", "nl *", "diff *", "cmp *", "stat *", "file *", "tree *", "du *", "realpath *",
                  "basename *", "dirname *", "which *", "echo *", "printf *", "true", "rg *", "grep *",
                  "git status *", "git diff *", "git log *", "git show *", "git branch *", "git rev-parse *",
                  "git ls-files *", "git merge-base *", "git blame *")
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
    return forbidding_rule(command, denied) is not None


def forbidding_rule(command: str, denied: list[str]) -> str | None:
    """Powód zakazu z `is_forbidden`: reguła projektu, która pasuje, pusty napis dla składni złożonej (bez reguły,
    ale też bez zgody), None — polecenie nie jest zabronione."""
    argv = _argv(command)
    if argv is None:
        return ""
    argv = _without_locale(argv)      # `LC_ALL=x make install` jest tym samym zakazanym `make install`
    for rule in denied:
        rule_argv = _argv(rule)
        if rule_argv is None:
            continue
        prefix = rule_argv[:-1] if rule_argv[-1] == "*" else rule_argv
        if argv[:len(prefix)] == prefix:
            return rule
    return None


def single_command(command: str) -> bool:
    """Zgoda z panelu obejmuje jedno proste polecenie bez powłoki i złożonych operatorów."""
    return _argv(command) is not None


def _argv(command: str) -> list[str] | None:
    """Argumenty po interpretacji cytowania; None dla składni złożonej lub podpowłoki."""
    text = str(command or "")
    if not text.strip() or any(c in text for c in "\n\r\0`$"):
        return None
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        parts = list(lexer)
    except ValueError:
        return None
    if not parts or any(part and set(part) <= set(";&|<>()") for part in parts):
        return None
    program = _without_locale(parts)
    if not program:
        return None
    if program[0] in ("sh", "bash", "zsh", "fish", "eval", "exec", "env", "command", "builtin", "sudo", "doas"):
        return None
    if "=" in program[0] and program[0].split("=", 1)[0].isidentifier():
        return None
    return parts


LOCALE_VARS = ("LANG", "LANGUAGE", "TZ")
LOCALE_VALUE = re.compile(r"[A-Za-z0-9_.@:+-]{1,64}")


def _without_locale(parts: list[str]) -> list[str]:
    """Polecenie bez początkowych ustawień języka i strefy czasowej (`LC_ALL=pl_PL.UTF-8 make test`, K-33).
    Inne zmienne (np. `PATH=`) zostają i odrzucają polecenie jako złożone."""
    at = 0
    while at < len(parts) and "=" in parts[at]:
        name, value = parts[at].split("=", 1)
        if not (name in LOCALE_VARS or (name.startswith("LC_") and name[3:].isalpha() and name.isupper())) \
                or not LOCALE_VALUE.fullmatch(value):
            break
        at += 1
    return parts[at:]


def is_allowed(command: str, allowed: list[str]) -> bool:
    argv = _argv(command)
    if argv is None:
        return False
    for rule in (*ALWAYS_ALLOWED, *allowed):
        rule_argv = _argv(rule)
        if rule_argv is None:
            continue
        if rule_argv[-1] == "*" and argv[:len(rule_argv) - 1] == rule_argv[:-1]:
            return True
        if argv == rule_argv:
            return True
    return False


def step_reads(step_dir: str) -> tuple[str, ...]:
    """Odczyt własnego `<krok>/in` i `<krok>/out` (PT-124): zawsze dozwolony, bez pytania o zgodę. Claude CLI
    pyta o `ls`/`cat` poza katalogami sesji mimo `ls *`, więc krok dostaje dokładne wzorce dla swoich katalogów.
    Granicą pozostaje piaskownica kroku (te katalogi i tak czyta); ścieżka z białym znakiem nie dostaje wzorca."""
    if not step_dir or not os.path.isabs(step_dir) or any(c.isspace() or c in "()*" for c in step_dir):
        return ()
    inp, out = os.path.join(step_dir, "in"), os.path.join(step_dir, "out")
    rules = []
    for flags in ("", "-la "):
        rules.extend((f"ls {flags}{inp}", f"ls {flags}{out}", f"ls {flags}{inp} {out}"))
    for folder in (inp, out):
        rules.append(f"ls {folder}/*")
        rules.extend(f"{program} {folder}/*" for program in ("cat", "head", "tail", "wc"))
    return tuple(f"Bash({rule})" for rule in rules)


def claude_tools(allowed: list[str], denied: list[str],
                 step_dir: str = "") -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Wzorce Bash dla Claude CLI; dokładna zgoda nie staje się zgodą na cały program."""
    grants = []
    for rule in dict.fromkeys((*ALWAYS_ALLOWED, *allowed)):
        if rule.endswith(" *"):
            grants.extend((f"Bash({rule[:-2]})", f"Bash({rule})"))
        else:
            grants.append(f"Bash({rule})")
    grants.extend(step_reads(step_dir))
    bans = []
    for rule in denied:
        bans.extend((f"Bash({rule})", f"Bash({rule} *)"))
    return tuple(grants), tuple(bans)
