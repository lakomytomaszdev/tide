"""Wąski Git strażnika: konfiguracja hosta, wyłącznie magazyn danego projektu.

Nie uruchamia Git w administracji kontrolowanej przez pudełko. Tymczasowy gitdir
poza montowaniami udostępnia tylko sprawdzone objects/refs/logs i packed-refs.
Konfiguracja projektu jest danymi: odczyt objectFormat bez include, nigdy konfiguracja
wywołania hostowego. To poprawność w jednej domenie zaufania, nie ochrona przed
wrogim procesem podmieniającym magazyn podczas wywołania Git.
"""
from contextlib import contextmanager
import base64
import os
import stat
import tempfile

from ..tasks.agentcmd import StepError
from ..worktrees.git import Git


def _storage(parent, name):
    """Bez dowiązań, hardlinków, plików specjalnych i zewnętrznych alternates."""
    from .refguard import DIR_FLAGS
    try:
        meta = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if stat.S_ISDIR(meta.st_mode):
        fd = os.open(name, DIR_FLAGS, dir_fd=parent)
        try:
            for child in os.listdir(fd):
                _storage(fd, child)
        finally:
            os.close(fd)
    elif not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
        raise StepError(f"strażnik refów: niebezpieczna ścieżka magazynu: {name}")
    return True


def admin(repo, git_dir):
    """Sprawdza ścieżkę i commondir przed użyciem HEAD, bez uruchamiania Git."""
    from .refguard import _open_git, _parent, _node
    common = os.path.join(os.path.realpath(repo), ".git")
    relative = os.path.relpath(git_dir, common)
    if (os.path.normpath(git_dir) != git_dir or not os.path.isabs(git_dir)
            or len(relative.split("/")) != 2 or not relative.startswith("worktrees/")):
        raise StepError("strażnik refów: katalog administracyjny poza worktrees")
    root = _open_git(repo)
    try:
        with _parent(root, relative + "/commondir") as (parent, name):
            node = _node(parent, name)
            if not node or node["kind"] != "file":
                raise StepError("strażnik refów: brak zwykłego commondir")
            target = base64.b64decode(node["data"]).decode().strip()
            if os.path.normpath(os.path.join(git_dir, target)) != common:
                raise StepError("strażnik refów: commondir poza repozytorium")
            return _node(parent, "HEAD")
    finally:
        os.close(root)


@contextmanager
def trusted(repo):
    from .refguard import _open_git, _root, _node
    common = os.path.join(os.path.realpath(repo), ".git")
    root = _open_git(repo)
    try:
        present = {name: _storage(root, name) for name in ("refs", "objects", "logs", "packed-refs")}
        if not present["refs"] or not present["objects"]:
            raise StepError("strażnik refów: brak objects/refs")
        # Alternates (także zwykły plik) kieruje odczyt do innego projektu.
        for name in ("objects/info/alternates", "objects/info/http-alternates"):
            if os.path.lexists(os.path.join(common, name)):
                raise StepError("strażnik refów: alternates poza kontraktem v1")
        _storage(root, "config")
        config = _node(root, "config")
        if not config or config["kind"] != "file":
            raise StepError("strażnik refów: config nie jest zwykłym plikiem")
        with tempfile.TemporaryDirectory(prefix="git-", dir=_root(repo)) as temp:
            # Stały program i zamknięte środowisko: GIT_DIR/COMMON_DIR/CONFIG_COUNT,
            # PATH, LD_PRELOAD itd. z otoczenia nie przechodzą do procesu hosta.
            git = Git(executable="/usr/bin/git")
            git.private = True
            git.read_roots = (repo,)
            git.write_roots = (repo,)
            git.env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
                       "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1"}
            with open(os.path.join(temp, "config"), "w") as fh:
                fh.write("[core]\nrepositoryFormatVersion = 0\nbare = true\nlogAllRefUpdates = true\n")
            with open(os.path.join(temp, "HEAD"), "w") as fh:
                fh.write("ref: refs/heads/guard-unused\n")
            for name, exists in present.items():
                if exists:
                    os.symlink(os.path.join(common, name), os.path.join(temp, name))
            source = os.path.join(temp, "source-config")
            with open(source, "wb") as fh:
                fh.write(base64.b64decode(config["data"]))
            result = git.run(temp, "--git-dir=" + temp, "config", "--no-includes", "--file", source,
                             "--null", "--list")
            if not result.ok:
                raise StepError("strażnik refów: niepoprawna konfiguracja projektu")
            values = dict(item.split("\n", 1) if "\n" in item else (item, "")
                          for item in result.out.split("\0") if item)
            fmt = values.get("extensions.objectformat", "sha1")
            if fmt not in ("sha1", "sha256") or values.get("extensions.refstorage", "files") != "files":
                raise StepError("strażnik refów: nieobsługiwany format magazynu")
            if fmt == "sha256":
                with open(os.path.join(temp, "config"), "w") as fh:
                    fh.write("[core]\nrepositoryFormatVersion = 1\nbare = true\nlogAllRefUpdates = true\n"
                             "[extensions]\nobjectFormat = sha256\n")
            yield git, temp
    finally:
        os.close(root)
