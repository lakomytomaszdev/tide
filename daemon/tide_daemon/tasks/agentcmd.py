"""Jedno miejsce, które składa polecenie i środowisko programu agenta (Claude Code, Codex).

Odmiany:
- `interactive()` — sesja w Konsoli (okno „Nowe zadanie”, zlecenie z Tablicy): argv z poleceniem na
  starcie i opcjami modelu lokalnego, środowisko i tokeny dla launch.py (tasks/local.py). Zachowanie
  jak przed wydzieleniem (PT-76), tylko w jednym miejscu,
- `background()` — krok w tle (`claude -p`, `codex exec`) z piaskownicą i środowiskiem bez kluczy
  (wzór z PT-75: docs/agents/specs/2026-10-28-przeplyw-kart.md, „Co jest twarde, a co nie”),
- zatrzymanie kroku bez utraty zmian: tasks/stepstop.py.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field, replace

from . import launch
from . import local as local_mod


class UnknownTool(ValueError):
    """Program inny niż `claude` i `codex`."""


def agent_argv(tool: str, binary: str, prompt: str, images: list[str] = (), add_dirs: list[str] = (),
               model_options: list[str] = ()) -> list[str]:
    """argv sesji interaktywnej z poleceniem na start; prompt to jeden element listy.

    Codex: `codex [--add-dir D] --image A --image B -- <prompt>` (zrzuty dołączone do pierwszej wiadomości).
    Claude Code nie ma opcji na obrazy: ścieżki trafiają na koniec polecenia, a katalogi zrzutów
    do `--add-dir`, żeby agent mógł je przeczytać narzędziem Read bez pytania o zgodę.
    add_dirs (oba narzędzia: `--add-dir`) — katalogi poza katalogiem roboczym, w których agent
    pisze (dokumenty AI karty w repozytorium tablicy).
    model_options — opcje modelu lokalnego zaraz po programie (tasks/local.py), np. `--model qwen3-coder:30b`.
    `--` kończy opcje — polecenie zaczynające się od „-” nie zostanie wzięte za opcję."""
    images = list(images)
    extra = list(model_options)
    for folder in add_dirs:
        extra += ["--add-dir", folder]
    if tool == "codex":
        argv = [binary] + extra
        for path in images:
            argv += ["--image", path]
        return argv + ["--", prompt]
    if tool == "claude":
        argv = [binary] + extra
        for folder in sorted({os.path.dirname(p) for p in images} - set(add_dirs)):
            argv += ["--add-dir", folder]
        text = prompt
        if images:
            text += "\n\nZałączone zrzuty ekranu (przeczytaj je narzędziem Read):\n" + "\n".join(images)
        return argv + ["--", text]
    raise UnknownTool(tool)


def interactive(tool: str, binary: str, prompt: str, images=(), add_dirs=(),
                agent: "local_mod.LocalAgent | None" = None) -> "local_mod.Command":
    """Polecenie sesji w Konsoli. Bez `agent` (chmura) samo argv, z `agent` (model lokalny, inny punkt) także
    środowisko, zmienne z tokenem (nazwa → plik) i pliki tokenów do sprawdzenia przed startem."""
    if tool not in ("claude", "codex"):
        raise UnknownTool(tool)
    model = local_mod.options(tool, agent) if agent is not None else local_mod.Command([])
    argv = agent_argv(tool, binary, prompt, images, add_dirs, model.argv)
    return local_mod.Command(argv, model.env, model.secret_env, model.unset_env, model.token_files)


# --- krok w tle -------------------------------------------------------------------------------

DEAD_BUS = "unix:path=/nonexistent/tide-step/bus"   # martwy D-Bus: gh i inni nie dosięgną portfela
GIT_SSH = ("ssh -F /dev/null -o IdentityAgent=none -o IdentitiesOnly=yes -o IdentityFile=none "
           "-o BatchMode=yes")
# Chronione pliki, które piaskownica Claude Code podmienia w katalogu roboczym na atrapy (urządzenia
# znakowe) — `git add -A` bez wykluczenia kończy się błędem (PT-75). Zakotwiczone, w pliku kroku,
# nie w repozytorium użytkownika.
SYSTEMD_RUN = "/usr/bin/systemd-run"
EXIT_CODE_FILE = "exit-code"
# Stały skrypt powłoki, bez danych agenta jako kodu: kod zakończenia zostaje w katalogu wejściowym kroku,
# do którego piaskownica agenta ma dostęp tylko do odczytu. Zapis atomowy pozwala odczytać go po restarcie usługi.
EXIT_WRAPPER = ('umask 077; trap : TERM; status=$1; shift; "$@"; code=$?; '
                'printf "%s\\n" "$code" > "$status.tmp" && mv -f -- "$status.tmp" "$status"; exit "$code"')
SANDBOX_DUMMIES = ("/.bash_profile", "/.bashrc", "/.claude", "/.gitconfig", "/.gitmodules", "/.idea",
                   "/.mcp.json", "/.profile", "/.ripgreprc", "/.vscode", "/.zprofile", "/.zshrc")
# Narzędzia agentów użytkownika rozkładane do worktree z zewnątrz (K-15: `.agents/skills`, `.codex/agents`) —
# nigdy nie należą do ticketu: krok ich nie commituje, a kontrola czystości worktree je pomija.
TOOLING_DIRS = ("/.agents/", "/.codex/")
CODEX_GIT_DIRS = ("objects", "refs", "logs")        # reszta wspólnego .git (haki, config) zostaje tylko do odczytu
# Krok opiekuna u Codexa: Codex nie ma listy dozwolonych poleceń, więc nie dostaje żadnego narzędzia wykonującego
# polecenia ani kod (`shell`, `exec_command`, `exec` trybu kodu, `js_repl`); zapis pliku w `out` zostaje przez
# apply_patch. To podzbiór listy tylko do odczytu niżej (PT-158).
CODEX_NO_COMMANDS = ("shell_tool", "unified_exec", "code_mode", "js_repl")
# Krok opiekuna u Claude (PT-158): stała lista poleceń tylko do odczytu, niezależna od ustawień projektu. Git działa
# wyłącznie jako `git -C <worktree> <podpolecenie>` (katalogiem roboczym kroku jest `out`). Zakazy wycinają opcje,
# którymi te programy piszą albo uruchamiają inne programy; piaskownica i tak daje zapis tylko do `out`.
READ_ONLY_COMMANDS = ("rg", "grep", "cat", "ls", "find")
# Podpolecenia gita z obowiązkowymi opcjami: `--no-ext-diff` wyłącza `diff.external` i `diff.<sterownik>.command`,
# `--no-textconv` — `diff.<sterownik>.textconv` (review M3; puste wartości w konfiguracji tego nie robią — git
# próbuje wtedy uruchomić pusty program). `status` nie uruchamia sterowników diffa.
READ_ONLY_GIT = ("log --no-ext-diff --no-textconv", "show --no-ext-diff --no-textconv",
                 "diff --no-ext-diff --no-textconv", "status")
READ_ONLY_BANS = (
    "find *-delete*", "find *-exec*", "find *-ok*", "find *-fprint*", "find *-fls*",   # usuwanie, programy, pliki
    "rg *--pre*", "rg *--hostname-bin*",                                               # programy pomocnicze rg
    "git *--output*", "git *--ext-diff*", "git *--textconv*",                          # zapis do pliku, sterowniki
    "git *--show-signature*",                                                          # gpg
    # `-z` działa też wewnątrz połączonych krótkich opcji (`-az`, `-iz`). Wildcard `*` obejmuje również spacje,
    # więc zakaz celowo odrzuca też bezpieczne wywołania z `z` w dalszym argumencie po `-` (review runda 3/M1).
    "rg *--search-zip*", "rg *-*z*",                                                    # dekompresory rg
    "*>*",                                                                             # przekierowanie do pliku
)
# Sam zakaz napisów w argv nie wyłącza programów pomocniczych gita z konfiguracji repozytorium (review M3: zwykłe
# `git diff` uruchamia `diff.external`). Sterowniki diffa wyłączają obowiązkowe opcje z `READ_ONLY_GIT`; resztę
# krok opiekuna dostaje wyłączoną w środowisku (`GIT_CONFIG_COUNT`, wygrywa z konfiguracją repozytorium):
# fsmonitor (hak przy `status`/`diff`), pager, weryfikację podpisów i sterowniki filtrów (`clean`/`smudge`/`process`
# przy `status`/`diff`) nazwane w konfiguracji — pusty filtr git pomija (`read_only_git_config`). Konfiguracji
# systemowej (`GIT_CONFIG_NOSYSTEM`) i globalnej użytkownika (`GIT_CONFIG_GLOBAL` — plik kroku) krok nie czyta.
READ_ONLY_GIT_CONFIG = (("core.fsmonitor", "false"), ("core.pager", "cat"),
                        ("log.showSignature", "false"), ("gpg.program", "/bin/false"),
                        ("gpg.ssh.program", "/bin/false"), ("gpg.x509.program", "/bin/false"))
GIT_DRIVER_KEYS = re.compile(r"^(filter)\.(.+)\.(clean|smudge|process)$", re.IGNORECASE)


def read_only_git_config(git_dir: str, timeout: float = 20.0) -> list[tuple[str, str]]:
    """Nadpisania konfiguracji gita kroku tylko do odczytu: `READ_ONLY_GIT_CONFIG` i puste programy sterowników
    filtrów z konfiguracji worktree (repozytorium, `config.worktree`, dołączone pliki). Konfigurację czyta usługa
    (`git config` nie uruchamia programów), bez systemowej i globalnej — tych krok nie widzi. Błąd odczytu to
    StepError: bez listy sterowników krok nie startuje."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "LC_ALL": "C"}
    try:
        run = subprocess.run(["git", "--git-dir", git_dir, "config", "--name-only", "--get-regexp",
                              r"^filter\..*\.(clean|smudge|process)$"],
                             capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StepError(f"nie da się odczytać sterowników gita kroku ({type(exc).__name__})") from None
    if run.returncode not in (0, 1):                # 1 — brak pasujących kluczy
        raise StepError(f"nie da się odczytać sterowników gita kroku: {run.stderr.strip()[:200]}")
    pairs = list(READ_ONLY_GIT_CONFIG)
    for key in dict.fromkeys(line.strip() for line in run.stdout.splitlines() if line.strip()):
        found = GIT_DRIVER_KEYS.match(key)
        if found:
            kind, name = found.group(1), found.group(2)
            pairs.append((key, ""))
            if kind.lower() == "filter":
                pairs.append((f"{kind}.{name}.required", "false"))
    return list(dict.fromkeys(pairs))


def read_only_tools(worktree: str, step_dir: str = "") -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`--allowedTools` i `--disallowedTools` kroku tylko do odczytu (opiekun karty): polecenia z
    `READ_ONLY_COMMANDS` (też z wzorcami nazwanymi worktree — Claude CLI pyta o polecenia ze ścieżką spoza katalogów
    sesji mimo `cat *`, por. `board.commands.step_reads`), `git -C <worktree>` z `READ_ONLY_GIT`, odczyt worktree
    narzędziami Read/Grep/Glob i odczyt własnych katalogów kroku; zakazy `READ_ONLY_BANS` (zakaz wygrywa z zgodą,
    łańcuch poleceń Claude CLI sprawdza po kawałku). Bez listy poleceń projektu."""
    from ..board.commands import step_reads
    worktree = os.path.normpath(worktree)
    allowed = []
    for name in READ_ONLY_COMMANDS:
        allowed += [f"Bash({name})", f"Bash({name} *)", f"Bash({name} {worktree})", f"Bash({name} {worktree}/*)",
                    f"Bash({name} * {worktree})", f"Bash({name} * {worktree}/*)"]
    for sub in READ_ONLY_GIT:
        allowed += [f"Bash(git -C {worktree} {sub})", f"Bash(git -C {worktree} {sub} *)"]
    # Ścieżka bezwzględna w regule Claude CLI zaczyna się od `//`; reguła Read obejmuje też Grep i Glob.
    allowed += [f"Read(/{worktree}/**)", *step_reads(step_dir)]
    return tuple(dict.fromkeys(allowed)), tuple(f"Bash({rule})" for rule in READ_ONLY_BANS)


class StepError(ValueError):
    """Katalogi albo ustawienia kroku, z którymi nie wolno startować."""


def resume_argv(tool: str, binary: str, session_id: str, prompt: str = "",
                model_options: list[str] = ()) -> list[str]:
    """Jedno polecenie wznowienia sesji dla Konsoli i kroku w tle."""
    try:
        valid = str(uuid.UUID(session_id)) == session_id.lower()
    except (ValueError, AttributeError, TypeError):
        valid = False
    if not valid:
        raise StepError("nie ma poprawnego ID sesji kroku")
    if tool == "claude":
        argv = [binary, *model_options, "--resume", session_id]
    elif tool == "codex":
        argv = [binary, *model_options, "resume", session_id]
    else:
        raise UnknownTool(tool)
    return argv + (["--", prompt] if prompt else [])


def resume_interactive(tool: str, binary: str, session_id: str, prompt: str,
                       agent: "local_mod.LocalAgent | None" = None) -> "local_mod.Command":
    """Wznowienie w Konsoli z tym samym środowiskiem modelu lokalnego co pierwszy krok."""
    model = local_mod.options(tool, agent) if agent is not None else local_mod.Command([])
    return local_mod.Command(resume_argv(tool, binary, session_id, prompt, model.argv), model.env,
                             model.secret_env, model.unset_env, model.token_files)


@dataclass
class Step:
    """Opis kroku w tle. `worktree` — katalog roboczy kroku, `repo` — główne repozytorium (wspólny `.git`),
    `step_dir` — katalog kroku obok worktree (podkatalogi `in`: kartka, ustawienia, gitconfig — tylko do odczytu;
    `out`: plik wyniku i notki), `step_id` — nazwa katalogu tymczasowego Claude Code. `model` — model
    w chmurze, `local` — model lokalny albo inny punkt (tokena nigdy nie ma w poleceniu)."""
    tool: str
    binary: str
    prompt: str
    worktree: str
    repo: str
    step_dir: str
    step_id: str
    session_id: str = ""            # Claude Code: z góry (--session-id); Codex nadaje sam
    resume: bool = False
    model: str = ""
    effort: str = ""               # wysiłek rozumowania tylko dla programu codex
    local: "local_mod.LocalAgent | None" = None
    allowed_tools: tuple = ()
    disallowed_tools: tuple = ()
    enforce_command_policy: bool = False
    permission_mode: str = ""       # np. "acceptEdits"; pusty — bez opcji
    git_name: str = "Krok"
    git_email: str = "krok@tide.invalid"
    base_env: dict | None = None    # skąd HOME, USER, PATH, LANG (domyślnie środowisko usługi)
    tmp_root: str = "/tmp"          # poza ~; tu Claude Code dostaje katalog tymczasowy kroku
    read_dirs: tuple = ()          # dodatkowe repozytoria karty, wyłącznie do odczytu
    dotnet: bool = False           # projekt .NET: pamięć pakietów NuGet do odczytu i środowisko bez serwerów buildu
    node_home: str = ""            # projekt Node: instalacja Node (nvm) tylko do odczytu, jej bin na początku PATH
    network_domains: tuple = ()    # P1 (03.11): domeny rejestrów paczek dostępne z piaskownicy kroku Claude
    chrome_bin: str = ""           # Chromium Playwrighta tylko do odczytu, jako CHROME_BIN (Karma, 03.11)
    codex_mcp: tuple | None = None  # serwery MCP Codexa do wyłączenia; None — usługa pyta `codex mcp list --json`
    codex_mcp_skip: tuple = ()      # serwery, których wyłączenie przez `-c` psuje konfigurację Codexa (03.11)
    # Krok opiekuna karty (PT-143, PT-158): zapis wyłącznie do `<krok>/out`, który jest też katalogiem roboczym
    # programu. Claude: stała lista poleceń tylko do odczytu (`read_only_tools`, zamiast `allowed_tools`) i zakaz
    # edycji worktree i repozytorium; Codex: jedyny zapisywalny `out` i żadnych poleceń (`CODEX_NO_COMMANDS`).
    write_out_only: bool = False


@dataclass
class Background:
    """Złożony krok w tle: argv, środowisko CAŁE (nic poza tym nie jest dziedziczone), polecenie dla stdin,
    pliki i katalogi kroku do założenia przed startem. `secret_env` (zmienna → plik tokenu) czyta start()
    dopiero w chwili startu, wyłącznie do środowiska programu — tokena nie ma tu ani w `env`."""
    argv: list
    env: dict
    stdin: str
    cwd: str
    step_dir: str
    session_id: str = ""
    tool: str = ""
    repo: str = ""
    git_dir: str = ""                              # <repo>/.git/worktrees/<wt>, ustalony przez usługę (resolve_git_dir)
    unit: str = ""                                 # jednostka systemd (scope) kroku: tide-step-<id>
    runtime_dir: str = ""                          # XDG_RUNTIME_DIR — tylko dla systemd-run, nie dla programu
    secret_env: dict = field(default_factory=dict)
    token_files: list = field(default_factory=list)
    files: dict = field(default_factory=dict)      # ścieżka → treść
    dirs: list = field(default_factory=list)
    # Katalog roboczy procesu, gdy inny niż worktree (`cwd`): krok z zapisem wyłącznie do `out` (PT-158) startuje
    # w `out` — piaskownica Claude CLI daje zapis katalogowi roboczemu, a jej puste pliki-atrapy (`.bashrc`…)
    # lądują w nim, nie w worktree pod strażą.
    workdir: str = ""

    @property
    def prompt_file(self) -> str:
        return os.path.join(self.step_dir, "in", "prompt.txt")

    def prepare(self) -> None:
        for folder in self.dirs:
            os.makedirs(folder, exist_ok=True)
        for path, text in self.files.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        with open(self.prompt_file, "w", encoding="utf-8") as fh:
            fh.write(self.stdin)

    @property
    def log_file(self) -> str:
        return os.path.join(self.step_dir, "out", "step.log")

    @property
    def exit_file(self) -> str:
        return os.path.join(self.step_dir, "in", EXIT_CODE_FILE)

    def stop_kwargs(self) -> dict:
        """Argumenty `stepstop.stop_step` wynikające z samego kroku (dochodzi `branch` i ścieżki dzienników sesji)."""
        return {"unit": self.unit, "worktree": self.cwd, "step_dir": self.step_dir, "repo": self.repo,
                "git_dir": self.git_dir, "tool": self.tool, "session_id": self.session_id}

    def start(self, stdout=None, stderr=subprocess.STDOUT, systemd_run: str = "") -> subprocess.Popen:
        """Uruchamia program w osobnej jednostce `systemd-run --user --scope` (wzór z „Odporności” w specyfikacji):
        restart albo śmierć usługi nie zabija kroku, a zatrzymanie jednostki zabija całą jej cgroup (także wnuki po
        `setsid`). `--scope` daje programowi środowisko procesu — token punktu nie trafia do właściwości jednostki
        ani do argv. systemd-run potrzebuje tylko `XDG_RUNTIME_DIR` (gniazdo menedżera; D-Bus niepotrzebny, w kroku
        jest martwy); `env -u` zdejmuje tę zmienną (i INVOCATION_ID) przed programem.
        stdout: domyślnie plik `<krok>/out/step.log` — rura do usługi zabiłaby krok przez SIGPIPE, gdy usługa padnie.
        Polecenie idzie z pliku kroku jako stdin. `systemd_run` — inny plik wykonywalny (atrapa w testach usługi)."""
        env = dict(self.env)
        for path in self.token_files:
            launch.read_token(path)
        for name, path in self.secret_env.items():
            env[name] = launch.read_token(path)
        env["XDG_RUNTIME_DIR"] = self.runtime_dir or f"/run/user/{os.getuid()}"
        for path in (self.exit_file, self.exit_file + ".tmp"):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        # Niższa waga procesora i dysku: kilka kroków naraz nie przymula pulpitu (03.11).
        argv = [systemd_run or SYSTEMD_RUN, "--user", "--scope", "--collect", "--quiet", f"--unit={self.unit}",
                "-p", "CPUWeight=40", "-p", "IOWeight=40", "--",
                "/usr/bin/env", "-u", "XDG_RUNTIME_DIR", "-u", "INVOCATION_ID",
                "/bin/sh", "-c", EXIT_WRAPPER, "tide-step-runner", self.exit_file, *self.argv]
        log = None
        if stdout is None:
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            log = stdout = open(self.log_file, "ab")
        try:
            with open(self.prompt_file, "rb") as stdin:
                return subprocess.Popen(argv, cwd=self.workdir or self.cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr,
                                        text=True, start_new_session=True)
        finally:
            if log is not None:
                log.close()


def _within(path: str, folder: str) -> bool:
    path, folder = os.path.normpath(path), os.path.normpath(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _check(step: Step, home: str) -> None:
    """Katalogi kroku: nie w katalogu tymczasowym ani w `~/.local/share` (Codex ze snapa ich nie widzi), nie w ukrytym
    katalogu domowym (też nie), katalog tymczasowy Claude Code poza `~` (pod `~` zapis do niego odrzucało
    piaskownicę), worktree nie pod `/tmp/claude-<uid>` (to $TMPDIR piaskownicy: zapis tam jest zapisem obok worktree)."""
    if step.effort and (step.tool != "codex" or step.effort not in ("low", "medium", "high", "xhigh")):
        raise StepError("wysiłek rozumowania: low, medium, high albo xhigh — tylko Codex")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", step.step_id):
        raise StepError(f"identyfikator kroku: 1–64 znaków [A-Za-z0-9_-], bez innych: {step.step_id!r}")
    for what, path in (("worktree", step.worktree), ("repo", step.repo), ("step_dir", step.step_dir)):
        if not os.path.isabs(path):
            raise StepError(f"{what}: ścieżka musi być bezwzględna: {path}")
    if _within(step.step_dir, step.tmp_root) or _within(step.step_dir, os.path.join(home, ".local", "share")):
        raise StepError(f"katalog kroku nie może leżeć w {step.tmp_root} ani w ~/.local/share: {step.step_dir}")
    if _within(step.step_dir, step.worktree) or _within(step.worktree, step.step_dir):
        raise StepError(f"katalog kroku nie może leżeć w worktree (ani odwrotnie): {step.step_dir}")
    if _within(step.worktree, f"/tmp/claude-{os.getuid()}"):
        raise StepError(f"worktree kroku nie może leżeć pod /tmp/claude-{os.getuid()}: {step.worktree}")
    if step.tool == "claude" and _within(step.tmp_root, home):
        raise StepError(f"katalog tymczasowy kroku musi być poza ~: {step.tmp_root}")
    if step.tool == "codex" and _within(step.step_dir, home):
        hidden = os.path.relpath(step.step_dir, home).split(os.sep)
        if any(part.startswith(".") for part in hidden):
            raise StepError(f"Codex ze snapa nie widzi ukrytych katalogów domowych: {step.step_dir}")


def resolve_git_dir(repo: str, worktree: str) -> str:
    """Katalog worktree we wspólnym `.git` ustalony przez usługę z repozytorium (`<repo>/.git/worktrees/*/gitdir`
    wskazuje `<worktree>/.git`), nie z pliku `.git` w worktree — ten agent może nadpisać (PT-76, review). Wynik
    zawsze leży w `<repo>/.git/worktrees/`; brak wpisu albo niejednoznaczność to StepError. Wołać przed
    startem kroku: potem agent (Codex pisze w `.git/worktrees/<wt>`) mógłby podmienić plik `gitdir`."""
    base = os.path.join(os.path.realpath(repo), ".git", "worktrees")
    want = os.path.join(os.path.realpath(worktree), ".git")
    found = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        names = []
    for name in names:
        try:
            with open(os.path.join(base, name, "gitdir"), encoding="utf-8") as fh:
                pointer = fh.read().strip()
        except OSError:
            continue
        if os.path.join(os.path.realpath(os.path.dirname(pointer)), os.path.basename(pointer)) == want:
            found.append(os.path.join(os.path.join(repo, ".git", "worktrees"), name))
    if len(found) != 1:
        raise StepError(f"repozytorium {repo} nie zna worktree {worktree} (albo zna go dwukrotnie)")
    return found[0]


def list_codex_mcp(binary: str, timeout: float = 20.0) -> tuple:
    """Nazwy serwerów MCP z `codex mcp list --json`. Kroku Codexa nie startujemy bez tej listy: serwer MCP działa
    poza piaskownicą Codexa, więc każdy z konfiguracji użytkownika jest kanałem sieciowym (wyłączamy je wszystkie)."""
    try:
        run = subprocess.run([binary, "mcp", "list", "--json"], capture_output=True, text=True, timeout=timeout,
                             stdin=subprocess.DEVNULL)
        data = json.loads(run.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise StepError(f"nie da się odczytać serwerów MCP Codexa ({type(exc).__name__})") from None
    if run.returncode != 0 or not isinstance(data, list) or not all(isinstance(i, dict) and isinstance(i.get("name"), str)
                                                                    for i in data):
        raise StepError("nie da się odczytać serwerów MCP Codexa (`codex mcp list --json`)")
    # Serwer bez rozpoznanego transportu (np. wbudowany `codex_app`) nie jest kanałem z konfiguracji użytkownika,
    # a `-c mcp_servers.<nazwa>.enabled=false` tworzy dla niego wpis bez transportu i Codex nie startuje (03.11, K-16).
    return tuple(item["name"] for item in data
                 if isinstance(item.get("transport"), dict) and item["transport"].get("type") in KNOWN_MCP_TRANSPORTS)


KNOWN_MCP_TRANSPORTS = ("stdio", "streamable_http", "sse", "http")
CODEX_BROKEN_MCP = re.compile(r"invalid transport\s*in `mcp_servers\.([^`]+)`")


def _toml_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else json.dumps(name, ensure_ascii=False)


def _env(step: Step, home_env: dict, inp: str) -> dict:
    """Środowisko kroku — drugą warstwą po piaskownicy (agent może je nadpisać własnym poleceniem). Nie
    dziedziczymy niczego: agent SSH, tokeny GitHuba, askpass i D-Bus usługi zostają poza krokiem."""
    env = {name: home_env[name] for name in ("HOME", "USER", "PATH", "LANG") if home_env.get(name)}
    env.update({
        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false",
        "SSH_ASKPASS_REQUIRE": "never", "GIT_SSH_COMMAND": GIT_SSH,
        "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "core.askPass", "GIT_CONFIG_VALUE_1": "",
        "GIT_CONFIG_GLOBAL": os.path.join(inp, "gitconfig"),
        "GH_CONFIG_DIR": os.path.join(inp, "gh"), "DBUS_SESSION_BUS_ADDRESS": DEAD_BUS})
    if step.tool == "codex":
        env["GIT_CONFIG_NOSYSTEM"] = "1"     # git w snapie nie czyta /etc/gitconfig (inaczej „unknown error”)
    else:
        env["CLAUDE_CODE_TMPDIR"] = os.path.join(step.tmp_root, f"tide-step-{step.step_id}")
    if step.node_home:
        env["PATH"] = os.path.join(step.node_home, "bin") + os.pathsep + env.get("PATH", "")
        # `npm install` w kroku (sieć do rejestru z P1) potrzebuje zapisywalnej pamięci podręcznej — `~` jest zakryty.
        env["npm_config_cache"] = os.path.join(step.step_dir, "out", ".npm-cache")
    if step.chrome_bin:
        env["CHROME_BIN"] = step.chrome_bin
    if step.dotnet:
        from . import dotnet
        # Katalog CLI w katalogu wyjściowym kroku — jedynym miejscu z zapisem w piaskownicy (K-15: katalog
        # tymczasowy Claude Code `/tmp/tide-step-…` jest w kroku tylko do odczytu).
        cli_home = os.path.join(os.path.dirname(inp), "out", ".dotnet-home")
        env.update(dotnet.step_env(cli_home, dotnet.packages_dir(home_env.get("HOME") or os.path.expanduser("~"))))
    return env


def _read_only_git_env(env: dict, pairs: list[tuple[str, str]]) -> None:
    """Dopisuje nadpisania `read_only_git_config` do `GIT_CONFIG_COUNT` kroku; bez konfiguracji systemowej i pagera."""
    count = int(env.get("GIT_CONFIG_COUNT") or 0)
    for key, value in pairs:
        env[f"GIT_CONFIG_KEY_{count}"], env[f"GIT_CONFIG_VALUE_{count}"] = key, value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_PAGER": "cat", "PAGER": "cat"})


FONT_DIRS = (".local/share/fonts", ".fonts", ".config/fontconfig")


def _font_dirs(home: str) -> list[str]:
    """Czcionki użytkownika tylko do odczytu: bez nich testy widoków QML w kroku liczą układ innymi czcionkami
    i klikają obok przycisków (K-33/PT-108: dwa testy panelu przechodziły tylko poza piaskownicą)."""
    found = []
    for rel in FONT_DIRS:
        path = os.path.realpath(os.path.join(home, rel)) if home else ""
        if path and os.path.isdir(path) and path not in found:
            found.append(path)
    return found


def _claude(step: Step, inp: str, out: str, env: dict, home: str) -> tuple[list, dict, dict]:
    """argv, pliki (ustawienia piaskownicy dla `--settings`) i pliki tokenów do sprawdzenia przed startem."""
    settings = {"sandbox": {
        "enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
        "autoAllowBashIfSandboxed": not step.enforce_command_policy,
        "filesystem": {"denyRead": ["~"], "allowRead": [step.worktree, os.path.join(step.repo, ".git"), inp, out, *step.read_dirs,
                                                        *([env["NUGET_PACKAGES"]] if step.dotnet and env.get("NUGET_PACKAGES") else []),
                                                        *_font_dirs(home),
                                                        *([step.node_home] if step.node_home else []),
                                                        *([os.path.dirname(step.chrome_bin)] if step.chrome_bin else [])],
                       "allowWrite": [out]},
        "network": {"allowedDomains": list(step.network_domains)}},
        # Zasada projektów: commity bez podpisów i wzmianek o AI (K-33/PT-108: review odrzuciło stopkę
        # Co-Authored-By, którą Claude Code dopisuje domyślnie).
        "includeCoAuthoredBy": False, "attribution": {"commit": "", "pr": ""}}
    if step.local is not None:
        opts = local_mod.options("claude", step.local)
        token = step.local.token_file
        if token and not _within(token, home):
            # Plik tokenu poza ~ nie jest objęty `denyRead: ["~"]` — polecenia agenta nie mają go czytać.
            settings["sandbox"]["filesystem"]["denyRead"].append(token)
        argv = list(opts.argv)
        while "--settings" in argv:                 # apiKeyHelper z --settings wchodzi do pliku ustawień kroku
            at = argv.index("--settings")
            settings.update(json.loads(argv[at + 1]))
            del argv[at:at + 2]
        model = argv
        env.update(opts.env)
        for name in opts.unset_env:
            env.pop(name, None)
        secret = {"token_files": list(opts.token_files)}
    else:
        model = [f"--model={step.model}"] if step.model else []
        secret = {"token_files": []}
    settings_path = os.path.join(inp, "settings.json")
    argv = [step.binary, "-p", *model, "--setting-sources=", "--settings", settings_path,
            "--permission-prompts", "none", "--strict-mcp-config", "--output-format", "stream-json", "--verbose",
            *(["--resume", step.session_id] if step.resume else ["--session-id", step.session_id]),
            # Katalog wyjściowy kroku musi być katalogiem roboczym sesji: `allowWrite` piaskownicy obejmuje tylko
            # polecenia Bash, a narzędzia Write/Edit poza katalogami sesji pytają o zgodę, której krok nie dostanie.
            "--add-dir", out]
    if step.permission_mode:
        argv += ["--permission-mode", step.permission_mode]
    allowed, disallowed = step.allowed_tools, step.disallowed_tools
    if step.write_out_only:
        # PT-158: stała lista tylko do odczytu zamiast listy kroku (lista projektu nigdy tu nie trafia). Ścieżka
        # bezwzględna w regule Claude CLI zaczyna się od `//`.
        allowed, bans = read_only_tools(step.worktree, step.step_dir)
        disallowed = (*bans, f"Edit(/{os.path.normpath(step.worktree)}/**)",
                      f"Edit(/{os.path.normpath(step.repo)}/**)")
    if allowed:
        argv += ["--allowedTools", *allowed]
    if disallowed:
        argv += ["--disallowedTools", *disallowed]
    return argv, {settings_path: json.dumps(settings, indent=2, ensure_ascii=False) + "\n"}, secret


def _codex(step: Step, out: str, env: dict, git_dir: str) -> tuple[list, dict]:
    git = os.path.join(step.repo, ".git")
    roots = [out] if step.write_out_only else [git_dir, *(os.path.join(git, name) for name in CODEX_GIT_DIRS), out]
    cwd = out if step.write_out_only else step.worktree
    secret = {"secret_env": {}, "token_files": []}
    if step.local is not None:
        opts = local_mod.options("codex", step.local)
        model = list(opts.argv)
        env.update(opts.env)
        secret = {"secret_env": dict(opts.secret_env), "token_files": list(opts.token_files)}
    else:
        model = [f"--model={step.model}"] if step.model else []
    servers = list_codex_mcp(step.binary) if step.codex_mcp is None else step.codex_mcp
    mcp = [arg for name in servers if name not in step.codex_mcp_skip
           for arg in ("-c", f"mcp_servers.{_toml_key(name)}.enabled=false")]
    # Opcje przed podpoleceniem: `codex exec resume <id> --cd …` kończy się „unexpected argument” (sprawdzone).
    effort = ["-c", f'model_reasoning_effort="{step.effort}"'] if step.effort else []
    # PT-143/S1: zakaz poleceń wymusza konfiguracja kroku, nie tylko kartka — sama granica zapisu do `out` nie
    # zabrania uruchomienia np. interpretera w `out`.
    no_commands = [arg for name in CODEX_NO_COMMANDS for arg in ("-c", f"features.{name}=false")] \
        if step.write_out_only else []
    # PT-143/S2: `writable_roots` tylko dopisuje katalogi; `/tmp` i `$TMPDIR` są zapisywalne domyślnie (i według
    # ustawień użytkownika), więc dla zapisu wyłącznie do `out` wykluczamy je jawnie — także łatką `apply_patch`.
    if step.write_out_only:
        no_commands += ["-c", "sandbox_workspace_write.exclude_slash_tmp=true",
                        "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true"]
    # `out` leży obok worktree, poza repozytorium; wyjątek dotyczy tylko kroku z zapisem do `out`.
    git_check = ["--skip-git-repo-check"] if step.write_out_only else []
    argv =[step.binary, "exec", *model, *effort, "--json", "--cd", cwd, "--sandbox", "workspace-write",
            "-c", "sandbox_workspace_write.writable_roots=" + json.dumps(roots, ensure_ascii=False),
            "-c", 'approval_policy="never"', *no_commands, *mcp, *git_check,
            *(["resume", step.session_id] if step.resume else []), "-"]
    return argv, secret


def background(step: Step) -> Background:
    """Krok w tle: `claude -p` albo `codex exec` z piaskownicą i środowiskiem bez kluczy (wzór PT-75)."""
    if step.tool not in ("claude", "codex"):
        raise UnknownTool(step.tool)
    if step.resume and not step.session_id:
        raise StepError("wznowienie kroku wymaga identyfikatora sesji")
    base = dict(os.environ if step.base_env is None else step.base_env)
    home = base.get("HOME") or os.path.expanduser("~")
    _check(step, home)
    inp, out = os.path.join(step.step_dir, "in"), os.path.join(step.step_dir, "out")
    env = _env(step, base, inp)
    git_dir = resolve_git_dir(step.repo, step.worktree)
    if step.write_out_only:
        _read_only_git_env(env, read_only_git_config(git_dir))
    if step.tool == "claude":
        step = replace(step, session_id=step.session_id or str(uuid.uuid4()))
        argv, files, extra = _claude(step, inp, out, env, home)
        session = step.session_id
    else:
        argv, extra = _codex(step, out, env, git_dir)
        files, session = {}, step.session_id if step.resume else ""
    files[os.path.join(inp, "gitconfig")] = (f"[user]\n\tname = {step.git_name}\n\temail = {step.git_email}\n"
                                              f"[core]\n\texcludesFile = {os.path.join(inp, 'ignore')}\n")
    files[os.path.join(inp, "ignore")] = "\n".join(SANDBOX_DUMMIES + TOOLING_DIRS) + "\n"
    dirs = [inp, out, env["GH_CONFIG_DIR"]] + ([env["CLAUDE_CODE_TMPDIR"]] if step.tool == "claude" else [])
    return Background(argv, env, step.prompt, step.worktree, step.step_dir, session, step.tool, repo=step.repo,
                      git_dir=git_dir, unit="tide-step-" + step.step_id,
                      runtime_dir=base.get("XDG_RUNTIME_DIR", ""),
                      secret_env=extra.get("secret_env", {}), token_files=extra.get("token_files", []),
                      files=files, dirs=dirs, workdir=out if step.write_out_only else "")

