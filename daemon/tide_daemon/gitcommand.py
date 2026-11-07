"""Jedno wejście wszystkich hostowych wywołań Git; bez niezabezpieczonego fallbacku."""
import json
import os

HOST_OPTIONS = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


SANDBOX = os.path.join(os.path.dirname(__file__), "git_sandbox.py")


def git_launch_env(source=None):
    """Czyści otoczenie także przed załadowaniem interpretera granicy."""
    source = os.environ if source is None else source
    identity = {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE"}
    return {"PATH": "/usr/bin:/bin", **{k: v for k, v in source.items()
            if k in identity or k in ("HOME", "USER", "LANG", "LC_ALL") or k.startswith("TIDE_")}}


def git_argv(*args, executable="git", read_roots=(), write_roots=(), private=False,
             index=None, launcher=SANDBOX) -> list:
    payload = {"executable": "/usr/bin/git" if executable == "git" else executable,
               "args": [*HOST_OPTIONS, *map(str, args)], "readRoots": list(read_roots),
               "writeRoots": list(write_roots), "private": private, "index": index}
    return ["/usr/bin/python3", "-I", launcher, json.dumps(payload, ensure_ascii=False)]
