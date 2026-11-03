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
    codex_mcp: tuple | None = None  # serwery MCP Codexa do wyłączenia; None — usługa pyta `codex mcp list --json`


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
        argv = [systemd_run or SYSTEMD_RUN, "--user", "--scope", "--collect", "--quiet", f"--unit={self.unit}", "--",
                "/usr/bin/env", "-u", "XDG_RUNTIME_DIR", "-u", "INVOCATION_ID",
                "/bin/sh", "-c", EXIT_WRAPPER, "tide-step-runner", self.exit_file, *self.argv]
        log = None
        if stdout is None:
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            log = stdout = open(self.log_file, "ab")
        try:
            with open(self.prompt_file, "rb") as stdin:
                return subprocess.Popen(argv, cwd=self.cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr,
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
    return tuple(item["name"] for item in data)


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
    if step.dotnet:
        from . import dotnet
        # Katalog CLI w katalogu wyjściowym kroku — jedynym miejscu z zapisem w piaskownicy (K-15: katalog
        # tymczasowy Claude Code `/tmp/tide-step-…` jest w kroku tylko do odczytu).
        cli_home = os.path.join(os.path.dirname(inp), "out", ".dotnet-home")
        env.update(dotnet.step_env(cli_home, dotnet.packages_dir(home_env.get("HOME") or os.path.expanduser("~"))))
    return env


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
                                                        *([step.node_home] if step.node_home else [])],
                       "allowWrite": [out]},
        "network": {"allowedDomains": []}},
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
    if step.allowed_tools:
        argv += ["--allowedTools", *step.allowed_tools]
    if step.disallowed_tools:
        argv += ["--disallowedTools", *step.disallowed_tools]
    return argv, {settings_path: json.dumps(settings, indent=2, ensure_ascii=False) + "\n"}, secret


def _codex(step: Step, out: str, env: dict, git_dir: str) -> tuple[list, dict]:
    git = os.path.join(step.repo, ".git")
    roots = [git_dir, *(os.path.join(git, name) for name in CODEX_GIT_DIRS), out]
    secret = {"secret_env": {}, "token_files": []}
    if step.local is not None:
        opts = local_mod.options("codex", step.local)
        model = list(opts.argv)
        env.update(opts.env)
        secret = {"secret_env": dict(opts.secret_env), "token_files": list(opts.token_files)}
    else:
        model = [f"--model={step.model}"] if step.model else []
    servers = list_codex_mcp(step.binary) if step.codex_mcp is None else step.codex_mcp
    mcp = [arg for name in servers for arg in ("-c", f"mcp_servers.{_toml_key(name)}.enabled=false")]
    # Opcje przed podpoleceniem: `codex exec resume <id> --cd …` kończy się „unexpected argument” (sprawdzone).
    effort = ["-c", f'model_reasoning_effort="{step.effort}"'] if step.effort else []
    argv = [step.binary, "exec", *model, *effort, "--json", "--cd", step.worktree, "--sandbox", "workspace-write",
            "-c", "sandbox_workspace_write.writable_roots=" + json.dumps(roots, ensure_ascii=False),
            "-c", 'approval_policy="never"', *mcp,
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
                      files=files, dirs=dirs)

