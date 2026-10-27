"""Rdzeń „Nowego zadania” bez Qt: sprawdzenie prośby, worktree, polecenie agenta, plik zadania.

Przebieg (Planner.prepare → terminal → claim albo Planner.rollback):
1. prośba z okna: repozytorium z katalogu projektów, narzędzie, polecenie, „nowy worktree”
   z nazwą gałęzi, zrzuty ekranu i dodatkowe katalogi agenta (addDirs: katalog dokumentów karty
   w repozytorium tablicy — tylko pod `<tablica>/docs/`),
2. sprawdzenie wszystkiego, co da się sprawdzić bez zmian na dysku (program agenta, Konsole,
   nazwa gałęzi, gałąź główna, zmiany w głównym katalogu, zrzuty),
3. `git worktree add -b <gałąź> <ścieżka> <gałąź główna>` — gałąź główna (master, main, develop)
   nigdy nie jest zmieniana, nowa gałąź zaczyna się na jej czubku; bez sieci (bez fetch),
4. plik zadania (argv agenta, katalog, tytuł karty) w $XDG_RUNTIME_DIR/tide/tasks — czyta go
   launch.py uruchomiony przez Konsole i „przejmuje” atomową zmianą nazwy,
5. gdy Konsole nie przejmie zadania w czasie — wycofanie: plik zadania anulowany, worktree
   i świeżo założona gałąź usunięte. Nic nie zostaje w połowie.

Model: `"model": "cloud"` (domyślnie — model narzędzia w chmurze) albo `"local"` — model z roli `agent`
w models.json (tasks/local.py). Przy `local` usługa sprawdza serwer teraz (online, model pobrany), argv
dostaje opcje modelu, plik zadania jawne zmienne (`env`) i zmienne z tokenem (`secretEnv`: nazwa →
ścieżka tokenFile — sam token czyta dopiero launch.py), a tytuł karty dopisek „· lokalnie”.

Polecenie agenta nigdy nie przechodzi przez powłokę: argv to lista, a prompt jest jednym jej
elementem, po `--` (myślnik na początku polecenia nie zostanie wzięty za opcję).
"""
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field

from . import local as local_mod
from ..modelnames import is_cloud_model
from ..worktrees.git import MAIN_CANDIDATES, Git, pick_main_branch
from ..worktrees.naming import TOOLS as BRANCH_TOOLS, branch_from_prompt, slug_from_prompt
from ..worktrees.scanner import find_repositories

TOOLS = ("claude", "codex")
TOOL_LABELS = {"claude": "Claude Code", "codex": "Codex"}
MODELS = ("cloud", "local")
LOCAL_SUFFIX = "lokalnie"     # dopisek w tytule karty Konsoli
PROMPT_LIMIT = 32_000          # znaków; jeden argument programu może mieć do 128 KiB
IMAGE_LIMIT = 10
ADD_DIR_LIMIT = 4
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
# Konwencja Claude Code (`claude --worktree`): worktrees w ignorowanym katalogu repozytorium.
WORKTREES_SUBDIR = os.path.join(".claude", "worktrees")
EXCLUDE_LINE = "/.claude/worktrees/"
CLAIM_TIMEOUT = 15.0           # s — tyle Konsole ma na uruchomienie launch.py

MESSAGES = {
    "bad-request": "niepoprawna prośba",
    "bad-tool": "nieznane narzędzie (claude albo codex)",
    "missing-cli": "nie znaleziono programu {tool} — zainstaluj go albo dodaj do PATH usługi",
    "no-terminal": "nie znaleziono Konsoli (konsole)",
    "unknown-repo": "to nie jest repozytorium z katalogu projektów",
    "empty-prompt": "napisz, co agent ma zrobić",
    "one-word": "opisz zadanie co najmniej dwoma słowami",
    "prompt-too-long": "polecenie jest za długie (najwyżej {limit} znaków)",
    "bad-prompt": "polecenie zawiera niedozwolony znak (NUL)",
    "bad-image": "nie da się dołączyć zrzutu: {detail}",
    "bad-add-dir": "nie da się udostępnić katalogu agentowi: {detail}",
    "no-base": "repozytorium nie ma lokalnej gałęzi master, main ani develop — wyłącz „Nowy worktree”",
    "bad-branch": "niepoprawna nazwa gałęzi: {detail}",
    "protected-branch": "{branch} to gałąź główna — nowy worktree potrzebuje własnej gałęzi",
    "branch-exists": "gałąź {branch} już istnieje — zmień nazwę",
    "path-exists": "katalog {path} już istnieje — zmień nazwę gałęzi",
    "dirty-base": "główny katalog ma niezacommitowane zmiany na {base} ({count}) — nowy worktree by ich nie "
                  "zawierał; zacommituj je albo wyłącz „Nowy worktree”",
    "git": "git odmówił: {detail}",
    "launch": "Konsola nie uruchomiła zadania{detail} — nic nie zostało utworzone",
    "bad-model": "nieznany model (cloud albo local)",
    "local-unset": "model lokalny agenta nie jest ustawiony — ustaw go w Tide → Modele",
    "local-unavailable": "model lokalny niedostępny: {detail}",
    "local-cloud": "model {model} działa w chmurze Ollamy — wybierz model lokalny w Tide → Modele",
}


class TaskError(Exception):
    def __init__(self, code: str, **details):
        self.code = code
        self.details = details
        template = MESSAGES.get(code, code)
        try:
            text = template.format(**details)
        except (KeyError, IndexError):
            text = template
        super().__init__(text)

    @property
    def message(self) -> str:
        return str(self)


@dataclass
class Plan:
    """Zadanie gotowe do uruchomienia (worktree już istnieje, plik zadania zapisany)."""
    repo_path: str
    repo_name: str
    tool: str
    prompt: str
    cwd: str
    branch: str
    title: str
    argv: list
    worktree: str = ""         # ścieżka nowego worktree albo pusty napis (główny katalog)
    base: str = ""             # gałąź główna, od której wyszedł worktree
    base_sha: str = ""
    images: list = field(default_factory=list)
    task_file: str = ""
    model: str = "cloud"       # "cloud" albo "local"
    local: object = None       # local.LocalAgent przy modelu lokalnym
    command: object = None     # local.Command przy modelu lokalnym (środowisko i tokeny dla launch.py)

    def result(self, mode: str = "") -> dict:
        local = {"endpoint": self.local.endpoint, "model": self.local.model, "server": self.local.server} \
            if self.local is not None else None
        return {"ok": True, "error": "", "message": "", "cwd": self.cwd, "branch": self.branch,
                "worktree": self.worktree, "base": self.base, "title": self.title, "repoPath": self.repo_path,
                "tool": self.tool, "mode": mode, "model": self.model, "local": local}

    def task_payload(self) -> dict:
        """Treść pliku zadania dla launch.py — bez tokenów (tylko ścieżki plików z nimi)."""
        payload = {"cwd": self.cwd, "argv": self.argv, "title": self.title}
        if self.command is not None:
            payload.update(self.command.task_fields())
        return payload

    @property
    def env(self) -> dict:
        return self.command.env if self.command is not None else {}

    @property
    def secret_env(self) -> dict:
        return self.command.secret_env if self.command is not None else {}


def failure(error: TaskError) -> dict:
    return {"ok": False, "error": error.code, "message": error.message, "cwd": "", "branch": "", "worktree": "",
            "base": "", "title": "", "repoPath": "", "tool": "", "mode": "", "model": "", "local": None}


# --- polecenie agenta ------------------------------------------------------------------------

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
    raise TaskError("bad-tool")


def task_title(project: str, branch: str, local: bool = False) -> str:
    """Tytuł karty Konsoli „projekt · gałąź” (z modelem lokalnym „projekt · gałąź · lokalnie”).
    Bez % (Konsole czyta go jako format tytułu) i średników (separator opcji -p)."""
    text = f"{project} · {branch}" if branch else project
    if local:
        text += f" · {LOCAL_SUFFIX}"
    return re.sub(r"[%;\r\n\t]", "", text).strip()


# --- repozytoria -----------------------------------------------------------------------------

def worktree_dir(repo_path: str) -> str:
    """Gdzie zakładać worktrees repozytorium: `<repo>-worktrees` obok, jeśli taki katalog już jest
    (tak trzyma je np. LogViewer), inaczej `<repo>/.claude/worktrees` (konwencja Claude Code)."""
    repo_path = os.path.normpath(repo_path)
    sibling = repo_path + "-worktrees"
    if os.path.isdir(sibling):
        return sibling
    return os.path.join(repo_path, WORKTREES_SUBDIR)


def worktree_path(repo_path: str, branch: str) -> str:
    """Katalog worktree z nazwy gałęzi bez prefiksu narzędzia („claude/popraw-x” → popraw-x)."""
    head, _, tail = branch.partition("/")
    name = tail if tail and head in BRANCH_TOOLS else branch
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "zadanie"
    return os.path.join(worktree_dir(repo_path), name)


def list_repos(items: list[dict]) -> list[dict]:
    """Repozytoria z listy worktrees (WorktreeScanner.snapshot): od ostatnio aktywnych.

    Aktywność repozytorium to najświeższa aktywność któregokolwiek z jego worktrees (zmiany,
    commity, sesje agentów)."""
    repos: dict[str, dict] = {}
    activity: dict[str, float] = {}
    for item in items:
        key = item.get("repoPath") or ""
        if not key:
            continue
        activity[key] = max(activity.get(key, 0.0), float(item.get("lastActivityTs") or 0))
        if item.get("isMain") and not item.get("missing"):
            repos[key] = {
                "name": item.get("repo") or os.path.basename(key),
                "path": item.get("path") or key,
                "branch": item.get("branch") or "",
                "mainBranch": item.get("mainBranch") or "",
                "dirty": item.get("dirty") or 0,
                "worktreeDir": worktree_dir(item.get("path") or key),
            }
    out = []
    for key, repo in repos.items():
        repo["lastActivityTs"] = round(activity.get(key, 0.0), 3)
        out.append(repo)
    out.sort(key=lambda r: (-r["lastActivityTs"], r["name"].lower()))
    return out


# --- pliki zadań -----------------------------------------------------------------------------

def runtime_dir(env=None) -> str:
    env = os.environ if env is None else env
    base = env.get("XDG_RUNTIME_DIR") or os.path.join("/tmp", f"tide-{os.getuid()}")
    return os.path.join(base, "tide", "tasks")


class TaskFiles:
    """Pliki zadań dla launch.py: `<czas>-<los>.task` (0600) w katalogu 0700.

    Przejęcie i anulowanie to zmiana nazwy (`.claimed`, `.cancelled`) — atomowa, więc zadanie
    uruchomi się najwyżej raz, a wycofane nie uruchomi się wcale, nawet gdy Konsole spóźni się
    o sekundę."""

    def __init__(self, folder: str | None = None):
        self.folder = folder or runtime_dir()

    def write(self, payload: dict) -> str:
        os.makedirs(self.folder, mode=0o700, exist_ok=True)
        os.chmod(self.folder, 0o700)
        name = f"{time.time_ns():020d}-{secrets.token_hex(4)}.task"
        path = os.path.join(self.folder, name)
        fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.rename(path + ".tmp", path)
        return path

    @staticmethod
    def claimed(path: str) -> bool:
        """Launcher przejął zadanie (pliku pod pierwotną nazwą już nie ma, a nie anulowaliśmy go)."""
        return not os.path.exists(path) and not os.path.exists(path + ".cancelled")

    @staticmethod
    def cancel(path: str) -> bool:
        """Anuluje zadanie, jeśli nikt go jeszcze nie przejął. False — launcher był szybszy."""
        try:
            os.rename(path, path + ".cancelled")
        except FileNotFoundError:
            return False
        # Pusty ślad zostaje (claimed() go widzi), treść z poleceniem znika od razu; sprząta sweep().
        try:
            with open(path + ".cancelled", "w", encoding="utf-8"):
                pass
        except OSError:
            pass
        return True

    def sweep(self, older_than: float = 3600, now: float | None = None) -> None:
        """Sprząta stare, nieprzejęte pliki (np. po restarcie usługi w trakcie startu)."""
        now = time.time() if now is None else now
        try:
            names = os.listdir(self.folder)
        except OSError:
            return
        for name in names:
            path = os.path.join(self.folder, name)
            try:
                if now - os.stat(path).st_mtime > older_than:
                    os.unlink(path)
            except OSError:
                continue


# --- planowanie ------------------------------------------------------------------------------

class Planner:
    """Sprawdza prośbę, zakłada worktree i plik zadania; wycofuje, gdy start się nie uda."""

    def __init__(self, workspace: str, max_depth: int = 3, git: Git | None = None, env=None,
                 files: TaskFiles | None = None, images_root: str | None = None, state_path: str | None = None,
                 terminal_available=None, board_root: str | None = None, models_config=None, model_probe=None):
        env = dict(os.environ if env is None else env)
        self.workspace = workspace
        self.max_depth = max_depth
        self.git = git or Git()
        self.env = env
        self.files = files or TaskFiles(runtime_dir(env))
        home = env.get("HOME") or os.path.expanduser("~")
        data = env.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
        state = env.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state")
        self.images_root = images_root or os.path.join(data, "tide", "tasks")
        self.state_path = state_path or os.path.join(state, "tide", "new-task.json")
        self.home = home
        self.terminal_available = terminal_available or (lambda: True)
        # Model lokalny: models.json czytany przy każdym starcie i stan punktu sprawdzany teraz
        # (podmienialne w testach — testy nigdy nie pytają prawdziwego serwera).
        self.models_config = models_config or self._models_config
        self.model_probe = model_probe or self._model_probe
        if board_root is None:
            from ..board.marker import default_board_dir
            board_root = env.get("TIDE_BOARD_DIR") or default_board_dir()
        self.board_root = os.path.abspath(os.path.expanduser(board_root)) if board_root else ""

    # --- narzędzia -----------------------------------------------------------------------

    def find_tool(self, tool: str) -> str:
        """Program agenta z PATH usługi, zapasowo ~/.local/bin. Bez rozwiązywania dowiązań:
        /snap/bin/codex wskazuje na /usr/bin/snap, który rozpoznaje program po nazwie."""
        found = shutil.which(tool, path=self.env.get("PATH", ""))
        if found:
            return found
        local = os.path.join(self.home, ".local", "bin", tool)
        return local if os.path.isfile(local) and os.access(local, os.X_OK) else ""

    def tools(self) -> dict:
        return {tool: bool(self.find_tool(tool)) for tool in TOOLS}

    # --- zapamiętany wybór ---------------------------------------------------------------

    def last_choice(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        tool = data.get("tool") if data.get("tool") in TOOLS else "claude"
        model = data.get("model") if data.get("model") in MODELS else "cloud"
        return {"repoPath": str(data.get("repoPath") or ""), "tool": tool,
                "newWorktree": data.get("newWorktree") is not False, "model": model}

    def remember(self, plan: Plan) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"repoPath": plan.repo_path, "tool": plan.tool, "newWorktree": bool(plan.worktree),
                           "model": plan.model}, fh)
            os.replace(tmp, self.state_path)
        except OSError as exc:  # zapamiętanie to wygoda — nie może zepsuć startu
            print(f"tide-daemon: zadania: nie udało się zapisać wyboru: {exc}", flush=True)

    def defaults(self) -> dict:
        return {**self.last_choice(), "tools": self.tools(), "terminal": bool(self.terminal_available()),
                "imagesDir": self.images_root, "promptLimit": PROMPT_LIMIT, "imageLimit": IMAGE_LIMIT}

    # --- repozytoria ---------------------------------------------------------------------

    def repositories(self) -> list[str]:
        return [os.path.normpath(p) for p in find_repositories(self.workspace, self.max_depth)
                if os.path.isdir(os.path.join(p, ".git"))]  # bez repozytoriów bare (nie mają katalogu roboczego)

    def repo_state(self, repo_path: str) -> dict:
        """Gałąź głównego katalogu, lokalna gałąź główna i liczba zmian — świeżo z gita."""
        head = self.git.run(repo_path, "symbolic-ref", "--quiet", "--short", "HEAD")
        current = head.out.strip() if head.ok else ""
        refs = self.git.run(repo_path, "for-each-ref", "--format=%(refname:short)",
                            *(f"refs/heads/{name}" for name in MAIN_CANDIDATES))
        existing = set(refs.out.split()) if refs.ok else set()
        status = self.git.run(repo_path, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
        dirty = len([x for x in status.out.split("\0") if len(x) > 3]) if status.ok else -1
        return {"branch": current, "mainBranch": pick_main_branch(existing, current), "dirty": dirty}

    def _repo(self, repo_path: str) -> str:
        path = os.path.normpath(repo_path or "")
        if not repo_path or path not in self.repositories():
            raise TaskError("unknown-repo")
        return path

    def _branch_exists(self, repo_path: str, branch: str) -> bool:
        return self.git.run(repo_path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").ok

    def _path_for(self, repo_path: str, branch: str) -> str:
        return worktree_path(repo_path, branch)

    def suggest(self, repo_path: str, tool: str, prompt: str) -> dict:
        """Proponowana gałąź i katalog: `<narzędzie>/<opis>`, z -2, -3…, gdy już są zajęte."""
        repo = self._repo(repo_path)
        base_branch = branch_from_prompt(prompt, tool if tool in TOOLS else "claude")
        branch, n = base_branch, 1
        while self._branch_exists(repo, branch) or os.path.exists(self._path_for(repo, branch)):
            n += 1
            branch = f"{base_branch}-{n}"
        state = self.repo_state(repo)
        return {"branch": branch, "path": self._path_for(repo, branch), "base": state["mainBranch"],
                "slug": slug_from_prompt(prompt)}

    # --- przygotowanie -------------------------------------------------------------------

    def check(self, request: dict) -> dict:
        """Sprawdzenie prośby bez zmian na dysku. Zwraca uporządkowaną prośbę albo rzuca TaskError."""
        if not isinstance(request, dict):
            raise TaskError("bad-request")
        tool = request.get("tool")
        if tool not in TOOLS:
            raise TaskError("bad-tool")
        prompt = request.get("prompt")
        if not isinstance(prompt, str):
            raise TaskError("bad-request")
        prompt = prompt.strip()
        if not prompt:
            raise TaskError("empty-prompt")
        if "\0" in prompt:
            raise TaskError("bad-prompt")
        if len(prompt) > PROMPT_LIMIT:
            raise TaskError("prompt-too-long", limit=PROMPT_LIMIT)
        # Jedno słowo mogłoby zostać wzięte za podpolecenie programu („claude doctor”, „codex login”).
        if len(prompt.split()) < 2:
            raise TaskError("one-word")
        binary = self.find_tool(tool)
        if not binary:
            raise TaskError("missing-cli", tool=tool)
        if not self.terminal_available():
            raise TaskError("no-terminal")
        repo = self._repo(request.get("repoPath") or "")
        images = self._images(request.get("images") or [])
        add_dirs = self._add_dirs(request.get("addDirs") or [])
        new_worktree = request.get("newWorktree") is not False
        branch = request.get("branch") if isinstance(request.get("branch"), str) else ""
        model = request.get("model", "cloud")
        if model not in MODELS:
            raise TaskError("bad-model")
        agent = self.local_agent() if model == "local" else None
        return {"tool": tool, "prompt": prompt, "binary": binary, "repo": repo, "images": images,
                "addDirs": add_dirs, "newWorktree": new_worktree, "branch": branch.strip(), "model": model,
                "local": agent}

    # --- model lokalny -------------------------------------------------------------------

    def _models_config(self):
        from ..models import config as models_config
        return models_config.load(models_config.config_path(self.env))

    @staticmethod
    def _model_probe(endpoint) -> dict:
        from ..models import status as models_status
        return models_status.endpoint_status(endpoint)

    def local_agent(self) -> "local_mod.LocalAgent":
        """Rola `agent` z models.json po sprawdzeniu serwera teraz: online i model pobrany.
        Kontekst z roli, inaczej z serwera (model załadowany), inaczej nieznany."""
        from ..models.status import model_key
        cfg = self.models_config()
        role = cfg.roles.get("agent")
        if role is None:
            raise TaskError("local-unset")
        if role.error:
            raise TaskError("local-unavailable", detail=role.error)
        if is_cloud_model(role.model):
            raise TaskError("local-cloud", model=role.model)
        endpoint = cfg.endpoints.get(role.endpoint)
        if endpoint is None:
            raise TaskError("local-unavailable", detail=f"nieznany serwer „{role.endpoint}”")
        state = self.model_probe(endpoint) or {}
        if not state.get("online"):
            reason = state.get("error") or "serwer nie odpowiada"
            raise TaskError("local-unavailable", detail=f"serwer „{endpoint.name}” ({endpoint.host}): {reason}")
        names = [m.get("name") for m in state.get("models") or [] if isinstance(m, dict)]
        if endpoint.kind == "ollama":
            installed = model_key(role.model) in {model_key(n) for n in names if n}
        else:
            installed = role.model in names
        if not installed:
            raise TaskError("local-unavailable", detail=f"modelu {role.model} nie ma na serwerze „{endpoint.name}”")
        ctx = role.ctx
        if not ctx:
            for loaded in state.get("loaded") or []:
                if isinstance(loaded, dict) and model_key(loaded.get("name") or "") == model_key(role.model):
                    ctx = loaded.get("contextLength") or None
        return local_mod.LocalAgent(endpoint.name, endpoint.url, endpoint.kind, role.model, ctx, endpoint.token_file)

    def _add_dirs(self, paths) -> list[str]:
        """Katalogi do `--add-dir`: tylko istniejące katalogi pod `<tablica>/docs/` (dokumenty AI kart
        projektów z `ai-docs: tide`), po rozwinięciu dowiązań; nic spoza tablicy."""
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise TaskError("bad-add-dir", detail="niepoprawna lista")
        if len(paths) > ADD_DIR_LIMIT:
            raise TaskError("bad-add-dir", detail=f"najwyżej {ADD_DIR_LIMIT} katalogi")
        if paths and not self.board_root:
            raise TaskError("bad-add-dir", detail="nie wiadomo, gdzie jest tablica Tide")
        docs = os.path.join(os.path.realpath(self.board_root), "docs")
        out = []
        for path in paths:
            if "\0" in path or not os.path.isabs(path):
                raise TaskError("bad-add-dir", detail=f"{path or '(pusty)'} nie jest ścieżką bezwzględną")
            real = os.path.realpath(path)
            if not real.startswith(docs + os.sep):
                raise TaskError("bad-add-dir", detail=f"{path} leży poza dokumentami tablicy ({docs})")
            if not os.path.isdir(real):
                raise TaskError("bad-add-dir", detail=f"{path} nie jest katalogiem")
            if real not in out:
                out.append(real)
        return out

    def _images(self, paths) -> list[str]:
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise TaskError("bad-image", detail="niepoprawna lista")
        if len(paths) > IMAGE_LIMIT:
            raise TaskError("bad-image", detail=f"najwyżej {IMAGE_LIMIT} zrzutów")
        root = os.path.realpath(self.images_root)
        out = []
        for path in paths:
            real = os.path.realpath(path)
            if not real.startswith(root + os.sep):
                raise TaskError("bad-image", detail=f"{path} leży poza katalogiem zadań")
            if not os.path.isfile(real) or not real.lower().endswith(IMAGE_SUFFIXES):
                raise TaskError("bad-image", detail=f"{os.path.basename(path)} nie jest obrazem")
            if real not in out:
                out.append(real)
        return out

    def prepare(self, request: dict) -> Plan:
        """Sprawdza prośbę, zakłada worktree (jeśli trzeba) i zapisuje plik zadania."""
        req = self.check(request)
        repo = req["repo"]
        name = os.path.basename(repo)
        plan_worktree, base, base_sha = "", "", ""
        if req["newWorktree"]:
            state = self.repo_state(repo)
            base = state["mainBranch"]
            if not base:
                raise TaskError("no-base")
            branch = req["branch"] or self.suggest(repo, req["tool"], req["prompt"])["branch"]
            self._check_branch(repo, branch, base)
            path = self._path_for(repo, branch)
            if os.path.exists(path):
                raise TaskError("path-exists", path=path)
            if state["branch"] == base and state["dirty"] != 0:
                raise TaskError("dirty-base", base=base, count=state["dirty"] if state["dirty"] > 0 else "?")
            sha = self.git.run(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{base}^{{commit}}")
            if not sha.ok:
                raise TaskError("no-base")
            base_sha = sha.out.strip()
            self._ignore_worktrees(repo, path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            added = self.git.run(repo, "worktree", "add", "-b", branch, path, base_sha, timeout=600)
            if not added.ok:
                raise TaskError("git", detail=_first_line(added.err))
            plan_worktree, cwd = path, path
        else:
            cwd = repo
            branch = self.repo_state(repo)["branch"]
        agent = req["local"]
        command = local_mod.options(req["tool"], agent) if agent is not None else local_mod.Command([])
        argv = agent_argv(req["tool"], req["binary"], req["prompt"], req["images"], req["addDirs"], command.argv)
        plan = Plan(repo_path=repo, repo_name=name, tool=req["tool"], prompt=req["prompt"], cwd=cwd, branch=branch,
                    title=task_title(name, branch or "odłączony HEAD", agent is not None), argv=argv,
                    worktree=plan_worktree, base=base, base_sha=base_sha, images=req["images"], model=req["model"],
                    local=agent, command=command if agent is not None else None)
        try:
            plan.task_file = self.files.write(plan.task_payload())
        except OSError as exc:
            self.rollback(plan)
            raise TaskError("launch", detail=f" (plik zadania: {exc})")
        return plan

    def _check_branch(self, repo: str, branch: str, base: str) -> None:
        if branch in MAIN_CANDIDATES or branch == base or branch.upper() == "HEAD":
            raise TaskError("protected-branch", branch=branch)
        valid = self.git.run(repo, "check-ref-format", "--branch", branch)
        if not valid.ok or branch.startswith("-") or valid.out.strip() != branch:
            raise TaskError("bad-branch", detail=branch)
        if self._branch_exists(repo, branch):
            raise TaskError("branch-exists", branch=branch)

    def _ignore_worktrees(self, repo: str, path: str) -> None:
        """Katalog worktrees wewnątrz repozytorium nie może brudzić głównego katalogu: gdy git go
        nie ignoruje, dopisujemy go do lokalnego `.git/info/exclude` (nie do śledzonego .gitignore)."""
        inside = os.path.normpath(path).startswith(os.path.join(repo, WORKTREES_SUBDIR) + os.sep)
        if not inside:
            return
        probe = os.path.relpath(path, repo)
        if self.git.run(repo, "check-ignore", "--quiet", "--no-index", probe).ok:
            return
        common = self.git.run(repo, "rev-parse", "--git-common-dir")
        if not common.ok:
            return
        git_dir = common.out.strip()
        exclude = os.path.join(git_dir if os.path.isabs(git_dir) else os.path.join(repo, git_dir), "info", "exclude")
        try:
            os.makedirs(os.path.dirname(exclude), exist_ok=True)
            with open(exclude, "a+", encoding="utf-8") as fh:
                fh.seek(0)
                text = fh.read()
                if EXCLUDE_LINE not in text.splitlines():
                    fh.write(("" if not text or text.endswith("\n") else "\n")
                             + "# Tide: worktrees zadań agentów\n" + EXCLUDE_LINE + "\n")
        except OSError as exc:
            print(f"tide-daemon: zadania: nie udało się dopisać {EXCLUDE_LINE} do {exclude}: {exc}", flush=True)

    # --- wycofanie -----------------------------------------------------------------------

    def rollback(self, plan: Plan) -> list[str]:
        """Cofa wszystko, co prepare utworzyło: plik zadania, worktree, nową gałąź.

        Gałąź znika tylko wtedy, gdy wciąż wskazuje commit, od którego wyszła (agent nic w niej
        nie zrobił), i nigdy, gdy to gałąź główna. Zwraca listę problemów (pusta = czysto)."""
        problems = []
        if plan.task_file:
            TaskFiles.cancel(plan.task_file)
        if not plan.worktree:
            return problems
        removed = self.git.run(plan.repo_path, "worktree", "remove", plan.worktree, timeout=600)
        if not removed.ok:
            # Świeży worktree, w którym agent nic nie zrobił — pliki z hooków git przy checkout.
            removed = self.git.run(plan.repo_path, "worktree", "remove", "--force", plan.worktree, timeout=600)
        if not removed.ok:
            problems.append(f"worktree {plan.worktree}: {_first_line(removed.err)}")
        branch = plan.branch
        if branch and branch not in MAIN_CANDIDATES and branch != plan.base:
            tip = self.git.run(plan.repo_path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
            if tip.ok and tip.out.strip() == plan.base_sha:
                deleted = self.git.run(plan.repo_path, "branch", "-D", branch)
                if not deleted.ok:
                    problems.append(f"gałąź {branch}: {_first_line(deleted.err)}")
            elif tip.ok:
                problems.append(f"gałąź {branch} ma nowe commity — zostaje")
        return problems


def _first_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[0] if lines else "nieznany błąd"
