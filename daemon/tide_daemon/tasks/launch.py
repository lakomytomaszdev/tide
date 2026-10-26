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

Skrypt celowo nie importuje niczego z tide_daemon — startuje szybko i działa także wtedy,
gdy usługa jest w trakcie aktualizacji.
"""
import json
import os
import sys

KEEP_SHELL = '"$0" "$@"; exec "${SHELL:-/bin/sh}"'
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
    """argv dla execv: /bin/sh uruchamia agenta, a po nim zwykłą powłokę."""
    return ["/bin/sh", "-c", KEEP_SHELL, *task["argv"]]


def shell() -> list[str]:
    path = os.environ.get("SHELL") or "/bin/sh"
    return [path]


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
        try:
            os.chdir(task["cwd"])
        except OSError as exc:
            print(f"Tide: nie da się wejść do {task['cwd']}: {exc}", flush=True)
            argv_exec = shell()
        else:
            os.environ["TIDE_TASK"] = "1"
            argv_exec = command(task)
    try:
        os.execv(argv_exec[0], argv_exec) if os.path.isabs(argv_exec[0]) else os.execvp(argv_exec[0], argv_exec)
    except OSError as exc:
        print(f"Tide: nie udało się uruchomić {argv_exec[0]}: {exc}", file=sys.stderr, flush=True)
        return 127
    return 0  # pragma: no cover — execv nie wraca


if __name__ == "__main__":
    sys.exit(main())
