"""Jedno wejście wszystkich hostowych wywołań Git.

Granica (`git_sandbox.py`: bwrap + Landlock + przypięta administracja) obejmuje wywołania, które mogą
dotknąć danych zapisywalnych z pudełka: przy `box.enabled`, dla repozytorium oznaczonego przed pierwszym
wystawieniem pudełku (`<common-dir>/tide-boundary`, `git_sandbox.initialize_indexes`) oraz na jawne
żądanie ścieżki pudełkowej (`boundary=True`, `private=True`, migawka `checkout`). Poza nimi argv i otoczenie
są takie jak przed K-38/PT-264: `[git, *HOST_OPTIONS, *args]` z otoczeniem podanym przez wywołującego.
Niepewny odczyt stanu (błąd stat, nieczytelny wskaźnik `.git`/`commondir`) wybiera granicę.
Decyzja i pomiar: docs/agents/pt-264/decisions.md („Granica Gita warunkowa”).
"""
import json
import os
import subprocess
from functools import lru_cache

HOST_OPTIONS = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


SANDBOX = os.path.join(os.path.dirname(__file__), "git_sandbox.py")


@lru_cache(maxsize=32)
def _host_identity(path, signature):
    """Osobny odczyt dwóch pól; config hosta nigdy nie jest montowany obok danych projektu."""
    try:
        result = subprocess.run(git_argv("config", "--no-includes", "--file", path, "--null",
                                        "--get-regexp", r"^user\.(name|email)$", boundary=True),
                                env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True,
                                timeout=20, stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            return {}
        values = {}
        for record in result.stdout.split("\0"):
            key, sep, value = record.partition("\n")
            if sep and key in ("user.name", "user.email"):
                values[key] = value
        return {name: values[key] for name, key in (
            ("GIT_AUTHOR_NAME", "user.name"), ("GIT_COMMITTER_NAME", "user.name"),
            ("GIT_AUTHOR_EMAIL", "user.email"), ("GIT_COMMITTER_EMAIL", "user.email")) if key in values}
    except (OSError, subprocess.SubprocessError):
        return {}


def git_launch_env(source=None, *, identity=False):
    """Czyści otoczenie także przed załadowaniem interpretera granicy."""
    source = os.environ if source is None else source
    identity_keys = {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE"}
    values = {}
    if identity:
        # Źródło wybiera usługa, nigdy konfiguracja repozytorium. Symlinki odrzuci granica.
        path = source.get("GIT_CONFIG_GLOBAL") or os.path.join(source.get("HOME", "/nonexistent"), ".gitconfig")
        try:
            info = os.stat(path, follow_symlinks=False)
            values = _host_identity(os.path.abspath(path), (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size))
        except OSError:
            pass
    defaults = {"TIDE_GIT_IDENTITY_JSON": json.dumps({key: values[name] for key, name in (
        ("name", "GIT_AUTHOR_NAME"), ("email", "GIT_AUTHOR_EMAIL")) if name in values})} if values else {}
    return {"PATH": "/usr/bin:/bin", **defaults, **{k: v for k, v in source.items()
            if k in identity_keys or k in ("HOME", "USER", "LANG", "LC_ALL") or k.startswith("TIDE_")}}


MARKER = "tide-boundary"
_SETTINGS_CACHE = [None, False]


def box_enabled() -> bool:
    """`box.enabled` z pliku ustawień usługi; ponowny odczyt po każdej zmianie pliku (stat), bez restartu."""
    from .box import settings as bsettings
    path = bsettings.settings_path()
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    key = (path, info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
    if _SETTINGS_CACHE[0] != key:
        _SETTINGS_CACHE[:] = [key, bsettings.load(path).enabled]
    return _SETTINGS_CACHE[1]


def _common_dir(gitdir):
    path = os.path.join(gitdir, "commondir")
    if not os.path.lexists(path):
        return gitdir
    with open(path, encoding="utf-8") as fh:
        value = fh.read(8192).strip()
    if not value:
        raise OSError("pusty commondir")
    return os.path.normpath(os.path.join(gitdir, value))


def _marked(gitdir):
    return os.path.lexists(os.path.join(_common_dir(gitdir), MARKER))


def _marked_from(path):
    """Odkrywa repozytorium nad `path` jak Git (katalog `.git` albo wskaźnik) i sprawdza znacznik."""
    current = os.path.abspath(path)
    while True:
        dotgit = os.path.join(current, ".git")
        if os.path.lexists(dotgit):
            if os.path.isdir(dotgit):
                return _marked(dotgit)
            with open(dotgit, encoding="utf-8") as fh:
                line = fh.read(8192).strip()
            if not line.startswith("gitdir:"):
                raise OSError("niepoprawny wskaźnik .git")
            return _marked(os.path.normpath(os.path.join(current, line[len("gitdir:"):].strip())))
        if os.path.isfile(os.path.join(current, "HEAD")) and _marked(current):
            return True  # sam katalog administracyjny (np. `config --file <.git>/config`)
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def marked_repository(args) -> bool:
    """Czy wywołanie dotyczy repozytorium wystawionego pudełku. Błąd odczytu → True (granica)."""
    cwd, candidates = os.getcwd(), []
    try:
        i = 0
        while i < len(args):
            item = str(args[i])
            value = str(args[i + 1]) if i + 1 < len(args) else ""
            if item == "-C":
                cwd = os.path.join(cwd, value)
            elif item in ("--git-dir", "--work-tree"):
                candidates.append(os.path.join(cwd, value))
            elif item.startswith(("--git-dir=", "--work-tree=")):
                candidates.append(os.path.join(cwd, item.split("=", 1)[1]))
                i -= 1
            elif item in ("--file", "-f"):
                candidates.append(os.path.dirname(os.path.join(cwd, value)))
            elif item not in ("-c", "--config-env"):
                i -= 1
            i += 2
        for path in [*candidates, cwd]:
            if (os.path.isfile(os.path.join(path, "HEAD")) and _marked(path)) or _marked_from(path):
                return True
        return False
    except (OSError, UnicodeDecodeError):
        return True


def boundary_active(args, *, boundary=False, private=False, checkout=None) -> bool:
    return bool(boundary or private or checkout is not None or box_enabled() or marked_repository(args))


def git_command(*args, env=None, identity=False, executable="git", boundary=False, private=False,
                checkout=None, **options):
    """`(argv, env)` jednego hostowego wywołania. Bez granicy `env` wywołującego (None = dziedziczone)."""
    if not boundary_active(args, boundary=boundary, private=private, checkout=checkout):
        if options.get("index"):
            env = dict(os.environ if env is None else env, GIT_INDEX_FILE=options["index"])
        return [executable, *HOST_OPTIONS, *map(str, args)], env
    return (git_argv(*args, executable=executable, private=private, checkout=checkout, boundary=True, **options),
            git_launch_env(env, identity=identity))


def git_argv(*args, executable="git", read_roots=(), write_roots=(), private=False,
             index=None, checkout=None, checkout_ignored=False, launcher=SANDBOX, boundary=False) -> list:
    """Sam argv; wywołujący bez własnej piaskownicy używa `git_command`, który dobiera też otoczenie."""
    if not boundary_active(args, boundary=boundary, private=private, checkout=checkout):
        return [executable, *HOST_OPTIONS, *map(str, args)]
    payload = {"executable": "/usr/bin/git" if executable == "git" else executable,
               "args": [*HOST_OPTIONS, *map(str, args)], "readRoots": list(read_roots),
               "writeRoots": list(write_roots), "private": private, "index": index}
    if checkout is not None:
        payload.update(checkout=checkout, checkoutIgnored=checkout_ignored)
    return ["/usr/bin/python3", "-I", launcher, json.dumps(payload, ensure_ascii=False)]
