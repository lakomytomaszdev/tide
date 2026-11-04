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
                  "git ls-files *", "git merge-base *", "git blame *",
                  # usuwa tylko nieśledzone pliki w worktree kroku (K-35/PT-132: sprzątanie pliku diagnostycznego)
                  "git clean -f -- *")
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
    pyta o `ls`/`cat` poza katalogami sesji mimo `ls *`, więc krok dostaje wzorce nazwane katalogami kroku.
    To nie są dokładne granice: `…/out/*` w Claude CLI to dopasowanie przedrostka (przejdzie też `…/out/../x` i
    kolejne argumenty), więc wzorce nie dają więcej niż `ls *`/`cat *`/`head *`/`tail *`/`wc *` z ALWAYS_ALLOWED —
    tylko omijają pytanie CLI. Granicą pozostaje piaskownica kroku; ścieżka z białym znakiem nie dostaje wzorca."""
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


# --- polecenia poza piaskownicą (03.11) ------------------------------------------------------------

OUTSIDE_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
OUTSIDE_ENV_VALUE = re.compile(r"[A-Za-z0-9_.@:,/+-]{0,200}")
OUTSIDE_ENV_DENIED = ("PATH", "HOME", "SHELL", "IFS", "ENV", "BASH_ENV", "PS4", "NODE_OPTIONS", "PYTHONPATH",
                      "PYTHONSTARTUP", "PERL5OPT", "RUBYOPT", "GIT_DIR", "GIT_WORK_TREE")
OUTSIDE_FAMILY = {"npm": 2, "pnpm": 2, "yarn": 2, "npx": 3, "dotnet": 2, "make": 2, "git": 2, "python3": 3}


def _inside_roots(path: str, roots) -> bool:
    import os
    real = os.path.realpath(path)
    return any(real == os.path.realpath(r) or real.startswith(os.path.realpath(r) + os.sep) for r in roots if r)


def outside_argv(command: str, roots=()) -> list[str] | None:
    """Polecenie poza piaskownicą bez początkowych zmiennych (`E2E_PORT=4311 npm run e2e`, także w cudzysłowie:
    `DATABASE_CONNECTION="Host=…;Port=1" dotnet test`, K-16); None dla łańcucha, podpowłoki albo zmiennej, która
    zmienia sposób uruchamiania (PATH, LD_*, NODE_OPTIONS…)."""
    text = str(command or "").strip()
    if not text or any(c in text for c in "\n\r\0`$"):
        return None
    try:
        # Operatory powłoki poza cudzysłowem (`;`, `&&`, `|`, `>`…) — polecenie idzie do `/bin/sh -c`, więc odrzucamy.
        # Znak poprzedzony ukośnikiem (`find … -exec … \;`) jest zwykłym argumentem, nie operatorem (K-33/PT-110).
        lexer = shlex.shlex(re.sub(r"\\[;&|<>()]", "_", text), posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        if any(token and set(token) <= set(";&|<>()") for token in lexer):
            return None
        parts = shlex.split(text)
    except ValueError:
        return None
    at = 0
    # `env NAZWA=… program` w obrębie projektu (04.11, K-41: testy rdzenia przez venv z PYTHONPATH do worktree
    # ticketu pytały co ticket) — `env` traktujemy jak zwykłe przypisania przed programem.
    if roots and parts[:1] == ["env"] and len(parts) > 2 and "=" in parts[1]:
        at = 1
    while at < len(parts) and "=" in parts[at]:
        name, value = parts[at].split("=", 1)
        project_path = name == "PYTHONPATH" and roots and value and all(
            part.startswith("/") and _inside_roots(part, roots) for part in value.split(":"))
        if not OUTSIDE_ENV_NAME.fullmatch(name) or (name in OUTSIDE_ENV_DENIED and not project_path) \
                or name.startswith(("LD_", "DYLD_")) or len(value) > 500:
            return None
        at += 1
    program = parts[at:]
    # Operatory sprawdził lekser wyżej; tu tylko reguły programu jak w `_argv` (bez powłok, sudo, env i przypisań).
    # `_argv` odrzuciłby argument `;` z `find -exec … \;`, choć powłoka dostaje go jako zwykły tekst.
    if not program or program[0] in ("sh", "bash", "zsh", "fish", "eval", "exec", "env", "command", "builtin",
                                     "sudo", "doas") or ("=" in program[0] and program[0].split("=", 1)[0].isidentifier()):
        return None
    return program


OUTSIDE_MAX = 2000      # polecenia poza piaskownicą bywają długie (zmienne połączeń, filtry testów)


def outside_allowed(command: str, denied: list[str], roots=()) -> bool:
    argv = outside_argv(command, roots)
    if argv is None or len(command) > OUTSIDE_MAX:
        return False
    # Zakazy projektu porównujemy na gotowych argumentach (`is_forbidden` przepuszcza tekst przez `_argv`, który
    # odrzuca np. argument `;` z `find -exec … \;` — i wtedy każde takie polecenie wyglądało na zakazane).
    program = _without_locale(argv)
    for rule in denied:
        rule_argv = _argv(rule)
        if rule_argv:
            prefix = rule_argv[:-1] if rule_argv[-1] == "*" else rule_argv
            if program[:len(prefix)] == prefix:
                return False
    return True


def outside_pattern(command: str) -> str:
    """Rodzina polecenia do zapamiętania („zawsze w tym projekcie”): `npm run e2e -- x` → `npm run e2e *`,
    `npx playwright test a` → `npx playwright test *`, `npm install --save-dev x` → `npm install *`."""
    argv = outside_argv(command) or []
    if not argv:
        return command
    size = OUTSIDE_FAMILY.get(argv[0], 2)
    if argv[0] in ("npm", "pnpm", "yarn") and len(argv) > 1 and argv[1] in ("run", "run-script", "exec"):
        size = 3
    head = [a for a in argv[:size] if not a.startswith("-")] or argv[:1]
    return shlex.join(head) + " *"


def outside_matches(command: str, remembered: list[str]) -> bool:
    argv = outside_argv(command)
    if argv is None:
        return False
    for rule in remembered:
        if rule.endswith(" *"):
            prefix = _argv(rule[:-2])
            if prefix and argv[:len(prefix)] == prefix:
                return True
        elif rule == command:
            return True
    return False


# Rodziny, których Tide nigdy nie uruchamia sam poza piaskownicą: wyjście na zewnątrz (push, publikacja,
# wdrożenie, zdalne powłoki), zmiany systemu i usuwanie/przenoszenie plików bez granic worktree.
OUTSIDE_RISKY = (
    "git push", "git remote", "git config", "npm publish", "npm login", "npm adduser", "npm token", "pnpm publish",
    "yarn publish", "yarn npm", "dotnet nuget", "docker push", "docker login", "docker system", "podman push",
    "ssh", "scp", "rsync", "sftp", "curl", "wget", "kubectl", "helm", "terraform", "az", "aws", "gcloud", "gh", "glab",
    "systemctl", "service", "make install", "rm", "mv", "cp", "chmod", "chown", "dd", "mkfs", "mount", "umount",
    "kill", "pkill", "killall", "reboot", "shutdown", "crontab", "apt", "apt-get", "snap", "flatpak", "pip install",
    "pip3 install", "python3 -m pip", "find", "xargs", "truncate", "shred", "ln", "tee", "open", "xdg-open")


def outside_risky(command: str, roots=()) -> str:
    """Powód, dla którego polecenie wymaga decyzji użytkownika mimo automatycznych zgód; pusty = bezpieczne.
    `roots` — worktree ticketu i repozytorium projektu: ścieżki w nich nie są „spoza worktree”."""
    argv = outside_argv(command, roots)
    if argv is None:
        return "łańcuch, podpowłoka albo niebezpieczna zmienna"
    for rule in OUTSIDE_RISKY:
        prefix = rule.split()
        if argv[:len(prefix)] == prefix:
            return f"„{rule}” wychodzi poza worktree albo zmienia system"
    for arg in argv[1:]:
        value = arg.split("=", 1)[-1]
        if value.startswith("/") and roots and _inside_roots(value, roots) and "../" not in value:
            continue
        if value.startswith(("/", "~")) and not value.startswith("/tmp/") or "../" in value or value == "..":
            return f"ścieżka spoza worktree: {arg}"
    return ""


def sandbox_grant(command: str, denied: list[str]) -> str | None:
    """Co dopisać do listy dozwolonych przy zgodzie na polecenie w piaskownicy: samo polecenie (do MAX_COMMAND
    znaków) albo — dla dłuższego pojedynczego polecenia — jego rodzinę (`git rm *`, 03.11 K-31/DPT-5: `git rm` czterech
    plików miał 324 znaki i nie dało się go zatwierdzić). None: łańcuch, składnia złożona albo zakaz projektu."""
    argv = _argv(command)
    if argv is None or is_forbidden(command, denied):
        return None
    if len(command) <= MAX_COMMAND:
        return command
    program = _without_locale(argv)
    head = [a for a in program[:2] if not a.startswith("-")] or program[:1]
    family = shlex.join(head) + " *"
    return None if is_forbidden(family[:-2], denied) else family
