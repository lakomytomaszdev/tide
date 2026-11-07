"""Jedno wejście wszystkich hostowych wywołań Git; bez niezabezpieczonego fallbacku."""
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
                                        "--get-regexp", r"^user\.(name|email)$"),
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


def git_argv(*args, executable="git", read_roots=(), write_roots=(), private=False,
             index=None, checkout=None, checkout_ignored=False, launcher=SANDBOX) -> list:
    payload = {"executable": "/usr/bin/git" if executable == "git" else executable,
               "args": [*HOST_OPTIONS, *map(str, args)], "readRoots": list(read_roots),
               "writeRoots": list(write_roots), "private": private, "index": index}
    if checkout is not None:
        payload.update(checkout=checkout, checkoutIgnored=checkout_ignored)
    return ["/usr/bin/python3", "-I", launcher, json.dumps(payload, ensure_ascii=False)]
