"""Utwardzony runner poleceń odczytu gita (K-36, PT-348) — jedyne miejsce, z którego moduł `gitread`
uruchamia gita.

Runner buduje na klasie `Git` z obszaru worktrees (limit czasu, anulowanie grupy procesów
przez `Cancellation`, granica pudełka z `gitcommand`) i dokłada zasady odczytu repozytorium
o dowolnej konfiguracji:

- zamknięta lista poleceń odczytu (`READ_COMMANDS`); poleceń zapisu i sieci nie da się przez niego wywołać,
- otoczenie: `GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`, `GIT_NO_LAZY_FETCH=1`, `LC_ALL=C`; zmienne
  `GIT_*` odziedziczone po procesie usługi są usuwane (np. `GIT_DIR`, `GIT_EXTERNAL_DIFF`,
  `GIT_CONFIG_PARAMETERS`), więc nie wybierają innego repozytorium ani programu,
- konfiguracja repozytorium nie uruchamia programów: `core.fsmonitor=false`, `core.hooksPath=/dev/null`,
  `--no-pager`, `log.showSignature=false`, programy gpg na `false`, `diff.submodule=short`;
  dla poleceń diffa (`diff`, `log`, `show`) `--no-ext-diff --no-textconv`; dla `diff` i `status`
  `--ignore-submodules=dirty` (bez gita uruchamianego w podmodule z jego własną konfiguracją),
  a sterowniki `filter.<nazwa>` z konfiguracji są przed `diff`/`status` wyłączane (puste `clean`,
  `smudge`, `process`, `required=false`),
- argumenty jako lista, bez powłoki; opcje to stałe z kodu wywołującego i nie mogą odwołać powyższych
  zasad (`FORBIDDEN_OPTIONS`); refy po `--end-of-options`, ścieżki po `--` i jako dosłowne ścieżki
  (`--literal-pathspecs`). Ref zaczynający się od `-` to błąd `bad-request`, git nie rusza,
- limit czasu kończy się błędem `timeout` z czytelnym opisem, a zamknięcie runnera anuluje
  trwające polecenia (`cancelled`),
- praca w puli wątków (`submit`), jak w `worktree_monitor`: wątek główny usługi nie czeka na gita.

Wywołujący nie podaje otoczenia ani obiektu `Git`; nie ma drogi uruchomienia gita z tego modułu
z pominięciem powyższych zasad.
"""
import os
from concurrent.futures import CancelledError, ThreadPoolExecutor

from ..worktrees.cancellation import Cancellation
from ..worktrees.git import GIT_ENV, Git, Result

DEFAULT_TIMEOUT = 30.0

READ_COMMANDS = frozenset(("diff", "log", "show", "rev-parse", "rev-list", "merge-base", "status",
                           "for-each-ref"))
DIFF_COMMANDS = frozenset(("diff", "log", "show"))
WORKTREE_COMMANDS = frozenset(("diff", "status"))  # czytają pliki checkoutu: filtry i podmoduły

# Opcje globalne każdego wywołania (przed poleceniem). `gitcommand.HOST_OPTIONS` dokłada hooksPath
# i fsmonitor także sam; runner nie polega na tym i podaje je jawnie.
GLOBAL_OPTIONS = (
    "--no-pager", "--literal-pathspecs",
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "log.showSignature=false",
    "-c", "gpg.program=false",
    "-c", "gpg.ssh.program=false",
    "-c", "gpg.x509.program=false",
    "-c", "diff.submodule=short",
    # `git diff` z checkoutem po zmianie samego stat zapisuje indeks (refresh_index_quietly),
    # czego GIT_OPTIONAL_LOCKS=0 nie obejmuje.
    "-c", "diff.autoRefreshIndex=false",
)
DIFF_OPTIONS = ("--no-ext-diff", "--no-textconv")
WORKTREE_OPTIONS = ("--ignore-submodules=dirty",)

# Opcje, które odwołują zasady runnera albo zapisują/czytają poza repozytorium.
FORBIDDEN_OPTIONS = ("--output", "--ext-diff", "--textconv", "--show-signature", "--no-index",
                     "--submodule", "--ignore-submodules", "--exec", "--filters", "--open-files-in-pager",
                     "--git-dir", "--work-tree", "--end-of-options")

# Zmienne otoczenia nadawane przez runner; pozostałe GIT_* z procesu usługi są usuwane.
READ_ENV = {**GIT_ENV, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
            "LC_ALL": "C", "GIT_PAGER": "cat", "PAGER": "cat"}


class GitReadError(Exception):
    """Błąd odczytu z kodem: `bad-request`, `timeout`, `cancelled`, `unavailable`, `unsafe-config`, `failed`."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def read_env(source=None) -> dict:
    """Otoczenie odczytu: otoczenie usługi bez odziedziczonych `GIT_*`, plus zasady runnera."""
    source = os.environ if source is None else source
    return {**{k: v for k, v in source.items() if not k.startswith("GIT_")}, **READ_ENV}


def _check_value(kind: str, value) -> str:
    if not isinstance(value, str) or not value:
        raise GitReadError("bad-request", f"pusty albo niepoprawny {kind}")
    if value.startswith("-"):
        raise GitReadError("bad-request", f"{kind} nie może zaczynać się od „-”: {value!r}")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise GitReadError("bad-request", f"{kind} ze znakiem sterującym: {value!r}")
    return value


def _check_path(value) -> str:
    """Ścieżka idzie po `--` jako dosłowna, więc `-` na początku jest dozwolony."""
    if not isinstance(value, str) or not value or "\0" in value or "\n" in value:
        raise GitReadError("bad-request", f"pusta albo niepoprawna ścieżka: {value!r}")
    return value


def _check_option(option) -> str:
    if not isinstance(option, str) or not option.startswith("-") or option in ("-", "--"):
        raise GitReadError("bad-request", f"niepoprawna opcja gita: {option!r}")
    name = option.split("=", 1)[0]
    if name in FORBIDDEN_OPTIONS or option.startswith("-c") or "%G" in option:
        raise GitReadError("bad-request", f"opcja niedozwolona w odczycie: {option!r}")
    if any(ord(c) < 32 and c != "\t" for c in option):
        raise GitReadError("bad-request", f"opcja ze znakiem sterującym: {option!r}")
    return option


class GitReader:
    """Jedyny runner gita modułu odczytu. Bezpieczny wątkowo; `close()` anuluje trwające polecenia."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT, executable: str = "git", workers: int = 2):
        self.timeout = timeout
        self._cancel = Cancellation()
        self._git = Git(executable=executable, timeout=timeout, cancel=self._cancel)
        self._git.env = read_env()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tide-gitread")

    def submit(self, fn, *args, **kwargs):
        """`fn(self, *args, **kwargs)` w puli wątków odczytu; zwraca `Future`."""
        return self._pool.submit(fn, self, *args, **kwargs)

    def close(self) -> None:
        self._cancel.close()
        self._pool.shutdown(wait=True, cancel_futures=True)

    def run(self, repo: str, command: str, *options: str, revs=(), paths=(), ok_codes=(0,),
            timeout: float | None = None) -> Result:
        """Jedno polecenie odczytu w `repo`. Zwraca `Result` dla kodu z `ok_codes`, inaczej `GitReadError`."""
        if command not in READ_COMMANDS:
            raise GitReadError("bad-request", f"polecenie poza listą odczytu: {command!r}")
        if not isinstance(repo, str) or not os.path.isabs(repo):
            raise GitReadError("bad-request", f"ścieżka repozytorium musi być bezwzględna: {repo!r}")
        if isinstance(revs, str) or isinstance(paths, str):
            raise GitReadError("bad-request", "refy i ścieżki podaje się jako listę")
        options = [_check_option(o) for o in options]
        revs = [_check_value("ref", r) for r in revs]
        paths = [_check_path(p) for p in paths]
        argv = [*GLOBAL_OPTIONS]
        if command in WORKTREE_COMMANDS:
            argv += self._filter_overrides(repo, timeout)
        argv += [command, *options]
        if command in DIFF_COMMANDS:
            argv += DIFF_OPTIONS
        if command in WORKTREE_COMMANDS:
            argv += WORKTREE_OPTIONS
        if command != "status":
            argv.append("--end-of-options")
        argv += revs
        if paths or command in ("diff", "log", "rev-list", "show", "status"):
            argv += ["--", *paths]
        return self._execute(repo, command, argv, ok_codes, timeout)

    def _filter_overrides(self, repo, timeout) -> list:
        """`-c` wyłączające każdy sterownik `filter.<nazwa>` z konfiguracji (odczyt konfiguracji nie
        uruchamia programów). Pliki atrybutów wskazują sterownik tylko z nazwy, więc bez wpisu
        w konfiguracji nie ma polecenia do uruchomienia."""
        argv = [*GLOBAL_OPTIONS, "config", "--null", "--name-only", "--get-regexp",
                r"^filter\..+\.(clean|smudge|process|required)$"]
        result = self._execute(repo, "config", argv, (0, 1), timeout)
        names = set()
        for key in result.out.split("\0"):
            if key.startswith("filter.") and "." in key[len("filter."):]:
                names.add(key[len("filter."):].rsplit(".", 1)[0])
        overrides = []
        for name in sorted(names):
            if "=" in name or any(ord(c) < 32 for c in name):
                # `-c klucz=wartość` dzieli po pierwszym „=”; takiego sterownika nie da się pewnie wyłączyć.
                raise GitReadError("unsafe-config", f"sterownik filter.{name!r} w konfiguracji repozytorium {repo} "
                                                    "ma nazwę, której runner nie umie wyłączyć — odczyt odrzucony")
            for field, value in (("clean", ""), ("smudge", ""), ("process", ""), ("required", "false")):
                overrides += ["-c", f"filter.{name}.{field}={value}"]
        return overrides

    def _execute(self, repo, command, argv, ok_codes, timeout) -> Result:
        limit = timeout if timeout is not None else self.timeout
        try:
            result = self._git.run(repo, *argv, timeout=limit)
        except CancelledError as exc:
            raise GitReadError("cancelled", f"git {command}: odczyt anulowany przy zamykaniu usługi") from exc
        if result.timed_out:
            raise GitReadError("timeout", f"git {command} w {repo} przekroczył limit czasu {limit:g} s i został przerwany")
        if result.code == -1:
            raise GitReadError("unavailable", f"git {command}: nie można uruchomić gita ({result.err})")
        if result.code not in ok_codes:
            raise GitReadError("failed", f"git {command} zakończył się kodem {result.code}: {result.err}")
        return result
