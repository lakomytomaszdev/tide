#!/usr/bin/python3
"""Start zadania agenta w karcie Konsoli: przejmuje plik zadania od tide-daemon i uruchamia agenta.

Użycie: python3 launch.py [--task PLIK]

Konsole uruchamia ten skrypt jako polecenie karty:
- nowa karta w istniejącym oknie: profil „Tide: zadanie agenta” (Command=… launch.py, bez --task);
  skrypt bierze najstarsze czekające zadanie, którego katalog to bieżący katalog karty,
- nowe okno: `konsole -e … launch.py --task PLIK`.

Przejęcie to zmiana nazwy pliku na `.claimed` — atomowa, więc zadanie startuje najwyżej raz,
a usługa wie, że start się udał (plik pod starą nazwą zniknął). Polecenie agenta jest listą
argumentów z pliku i przechodzi do /bin/sh jako "$@", bez sklejania napisów: cudzysłowy, nowe
linie i znaki narodowe w poleceniu dla agenta docierają bez zmian. Po zakończeniu agenta karta
zostaje ze zwykłą powłoką w tym samym katalogu.

Opcjonalne pole `workdir`: katalog, w którym idzie polecenie i zostaje powłoka, gdy `cwd` służy tylko
do przejęcia zadania (karty z logami i powłoką Dockera mają własny, pusty katalog, żeby dwie szybkie
karty nie zamieniły się zadaniami — a powłoka po `docker logs -f` ma zostać w katalogu domowym).

Opcjonalne pola modelu lokalnego (tasks/local.py): `env` — jawne zmienne środowiska agenta
(`ANTHROPIC_BASE_URL`, `CODEX_OSS_BASE_URL`…), `secretEnv` — zmienna → ścieżka pliku z tokenem (Codex),
`unsetEnv` — zmienne usuwane ze środowiska agenta, `tokenFiles` — pliki tokenów sprawdzane przed startem.
Token czytamy tutaj, w chwili startu, i trafia wyłącznie do środowiska agenta: nie ma go w argv, pliku
zadania, tytule karty ani na ekranie. Gdy pliku tokenu nie da się odczytać, agent nie startuje (zostaje
powłoka z komunikatem bez treści tokenu). Takie zadanie launch.py uruchamia sam (proces potomny ze środowiskiem
agenta, launch.py czeka, Ctrl+C trafia do agenta), a potem zamienia się w powłokę z pierwotnym
środowiskiem karty: zmienne dodane dla agenta znikają, a te, które użytkownik miał wcześniej, wracają
z jego wartościami — `claude` wpisany później w tej karcie nie pójdzie po cichu na serwer lokalny,
a tokenu nie ma nigdzie poza agentem.

Skrypt celowo nie importuje niczego z tide_daemon — startuje szybko i działa także wtedy,
gdy usługa jest w trakcie aktualizacji.
"""
import json
import os
import re
import sys


def write_closed(path: str) -> None:
    """Trwały znacznik zamknięcia karty kroku; zapis atomowy dla zegara usługi."""
    if not path:
        return
    import time
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    temp = path + f".{os.getpid()}.tmp"
    with open(temp, "w", encoding="utf-8") as fh:
        json.dump({"closed": True, "ts": time.time()}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp, path)

KEEP_SHELL = '"$0" "$@"; exec "${SHELL:-/bin/sh}"'
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
MAX_AGE = 600  # s — starszych zadań nie uruchamiamy (usługa i tak już je wycofała)


def runtime_dir(env=None) -> str:
    env = os.environ if env is None else env
    base = env.get("XDG_RUNTIME_DIR") or os.path.join("/tmp", f"tide-{os.getuid()}")
    return os.path.join(base, "tide", "tasks")


def _own_file(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    import stat
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()


def _load(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    argv = data.get("argv") if isinstance(data, dict) else None
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return None
    if not isinstance(data.get("cwd"), str):
        return None
    for key in ("env", "secretEnv"):
        value = data.get(key, {})
        if not isinstance(value, dict) or not all(isinstance(k, str) and ENV_NAME.fullmatch(k) and isinstance(v, str)
                                                  and "\0" not in v for k, v in value.items()):
            return None
    names, files = data.get("unsetEnv", []), data.get("tokenFiles", [])
    if not isinstance(names, list) or not all(isinstance(n, str) and ENV_NAME.fullmatch(n) for n in names):
        return None
    if not isinstance(files, list) or not all(isinstance(f, str) and f and "\0" not in f for f in files):
        return None
    return data


def claim(path: str):
    """Przejmuje wskazany plik zadania. Zwraca zadanie albo None (wycofane, cudze, uszkodzone)."""
    if not path.endswith(".task") or not _own_file(path):
        return None
    claimed = path + ".claimed"
    try:
        os.rename(path, claimed)
    except OSError:
        return None  # usługa zdążyła je wycofać albo przejął je ktoś inny
    data = _load(claimed)
    try:
        os.unlink(claimed)
    except OSError:
        pass
    return data


def claim_for_cwd(folder: str, cwd: str, now: float | None = None):
    """Najstarsze czekające zadanie dla katalogu cwd (tryb profilu Konsoli, bez --task)."""
    import time
    now = time.time() if now is None else now
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".task"))
    except OSError:
        return None
    here = os.path.realpath(cwd)
    for name in names:
        path = os.path.join(folder, name)
        if not _own_file(path):
            continue
        try:
            if now - os.stat(path).st_mtime > MAX_AGE:
                continue
        except OSError:
            continue
        data = _load(path)
        if data is None or os.path.realpath(data["cwd"]) != here:
            continue
        task = claim(path)
        if task is not None:
            return task
    return None


def command(task: dict) -> list[str]:
    """argv dla execv: /bin/sh uruchamia agenta, a po nim zwykłą powłokę (zadanie bez `env`)."""
    return ["/bin/sh", "-c", KEEP_SHELL, *task["argv"]]


def has_env(task: dict) -> bool:
    return any(task.get(key) for key in ("env", "secretEnv", "unsetEnv", "tokenFiles"))


def run_agent(argv: list[str], env: dict) -> int:
    """Agent jako proces potomny z własnym środowiskiem; launch.py czeka. Ctrl+C i Ctrl+\\ idą do agenta
    (ta sama grupa procesów terminala), launch.py je przeczekuje — własna obsługa sygnału, nie SIG_IGN,
    więc agent po exec ma domyślną."""
    import signal
    import subprocess
    previous = {sig: signal.signal(sig, lambda *_: None) for sig in (signal.SIGINT, signal.SIGQUIT)}
    try:
        return subprocess.call(argv, env=env)
    except OSError as exc:
        print(f"Tide: nie udało się uruchomić {argv[0]}: {exc}", file=sys.stderr, flush=True)
        return 127
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def read_token(path: str) -> str:
    """Token z pliku (bez białych znaków na końcach); tylko drukowalne ASCII. Błąd → ValueError
    z opisem bez treści pliku."""
    if not os.path.isabs(os.path.expanduser(path)):
        raise ValueError(f"ścieżka pliku tokenu musi być bezwzględna: {path}")
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            token = fh.read().strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"nie da się odczytać pliku tokenu {path} ({type(exc).__name__})") from None
    if not token or not all(" " < c < "\x7f" for c in token):
        raise ValueError(f"pusty albo niepoprawny plik tokenu {path}")
    return token


def agent_env(task: dict, base: dict) -> dict:
    """Środowisko agenta: bieżące bez `unsetEnv`, z `env` i tokenami z `secretEnv` (czytane teraz);
    pliki z `tokenFiles` tylko sprawdzone (agent czyta je sam, np. apiKeyHelper Claude Code)."""
    for path in task.get("tokenFiles") or []:
        read_token(path)
    env = {k: v for k, v in base.items() if k not in set(task.get("unsetEnv") or [])}
    env.update(task.get("env") or {})
    for name, path in (task.get("secretEnv") or {}).items():
        env[name] = read_token(path)
    return env


def shell() -> list[str]:
    path = os.environ.get("SHELL") or "/bin/sh"
    return [path]


def run_flow(task: dict) -> int:
    """Karta kroku zostaje otwarta po agencie; zamknięcie zostawia znacznik dla usługi."""
    import signal
    import subprocess

    closed = task.get("closedFile") or ""
    if not isinstance(closed, str) or not os.path.isabs(closed):
        return 2

    def hangup(*_):
        write_closed(closed)
        os._exit(129)

    previous = signal.signal(signal.SIGHUP, hangup)
    try:
        try:
            env = agent_env(task, os.environ) if has_env(task) else dict(os.environ)
        except ValueError as exc:
            print(f"Tide: model lokalny: {exc} — agent nie wystartował.", flush=True)
        else:
            run_agent(task["argv"], env)
            del env
        # Powłoka jest procesem potomnym: launch.py żyje do zamknięcia karty i zapisuje znacznik.
        try:
            subprocess.call(shell())
        except OSError:
            pass
    finally:
        signal.signal(signal.SIGHUP, previous)
        write_closed(closed)
    return 0


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 2 and args[0] == "--task":
        task = claim(args[1])
    elif not args:
        task = claim_for_cwd(runtime_dir(), os.getcwd())
    else:
        print("Użycie: launch.py [--task PLIK]", file=sys.stderr)
        return 2
    if task is None:
        print("Tide: nie ma zadania do uruchomienia w tym katalogu (wycofane albo już uruchomione).", flush=True)
        argv_exec = shell()
    else:
        workdir = task.get("workdir") if isinstance(task.get("workdir"), str) and task.get("workdir") else task["cwd"]
        try:
            os.chdir(workdir)
        except OSError as exc:
            print(f"Tide: nie da się wejść do {workdir}: {exc}", flush=True)
            if task.get("closedFile"):
                write_closed(task["closedFile"])
            argv_exec = shell()
        else:
            os.environ["TIDE_TASK"] = "1"
            if task.get("closedFile"):
                return run_flow(task)
            if not has_env(task):
                argv_exec = command(task)
            else:
                try:
                    env = agent_env(task, os.environ)
                except ValueError as exc:
                    print(f"Tide: model lokalny: {exc} — agent nie wystartował.", flush=True)
                else:
                    run_agent(task["argv"], env)
                    del env  # token zostaje tylko w pamięci procesu, który zaraz zastąpi powłoka
                argv_exec = shell()  # pierwotne środowisko karty
    try:
        os.execv(argv_exec[0], argv_exec) if os.path.isabs(argv_exec[0]) else os.execvp(argv_exec[0], argv_exec)
    except OSError as exc:
        print(f"Tide: nie udało się uruchomić {argv_exec[0]}: {exc}", file=sys.stderr, flush=True)
        return 127
    return 0  # pragma: no cover — execv nie wraca


if __name__ == "__main__":
    sys.exit(main())
