#!/usr/bin/python3
"""Git pudełka: prywatny indeks/HEAD, wspólne objects/refs/logs projektu.

Plik jest kopiowany jako in/bin/git. Usługa nigdy nie czyta prywatnej administracji
z domu pudełka. Oryginalna administracja hosta jest montowana wyłącznie ro.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys


def run(args):
    cwd = Path.cwd()
    i = 0
    while i < len(args):
        if args[i] == "-C" and i + 1 < len(args):
            cwd = (cwd / args[i + 1]).absolute()
            i += 2
        elif args[i] == "-c" and i + 1 < len(args):
            i += 2
        elif args[i].startswith("-"):
            i += 1
        else:
            break
    root = cwd
    while not (root / ".git").exists() and root.parent != root:
        root = root.parent
    pointer = root / ".git"
    # Inne repozytoria utworzone w rootfs pudełka korzystają ze zwykłego Git.
    if not pointer.is_file() or pointer.is_symlink():
        return subprocess.call(["/usr/bin/git", *args])
    text = pointer.read_text().strip()
    if not text.startswith("gitdir: "):
        raise ValueError("niepoprawny wskaźnik .git")
    admin = (root / text[8:]).resolve()
    if admin.parent.name != "worktrees" or admin.parent.parent.name != ".git":
        raise ValueError("administracja poza projektem")
    common = admin.parent.parent
    if (admin / "gitdir").read_text().strip() != str(pointer):
        raise ValueError("wskaźnik nie odpowiada administracji hosta")
    view = Path.home() / ".local/state/tide-git" / hashlib.sha256(str(admin).encode()).hexdigest()
    view.mkdir(parents=True, exist_ok=True)
    identity = {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE"}
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k in identity}
    env.update(GIT_DIR=str(view), GIT_COMMON_DIR=str(common), GIT_WORK_TREE=str(root))
    opts = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "gc.auto=0",
            "-c", "maintenance.auto=false", "-c", "core.worktree=" + str(root)]
    with (view / "tide.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (view / "HEAD").exists():
            for name in ("HEAD", "index"):
                source = admin / name
                if source.exists():
                    shutil.copyfile(source, view / name)
            (view / "commondir").write_text(str(common) + "\n")
        tip = subprocess.check_output(["/usr/bin/git", *opts, "rev-parse", "HEAD"], env=env, text=True).strip()
        recorded = view / "tide-index-tip"
        if recorded.exists() and recorded.read_text().strip() != tip:
            subprocess.run(["/usr/bin/git", *opts, "read-tree", "--reset", tip], env=env, check=True)
        code = subprocess.call(["/usr/bin/git", *opts, *args], env=env)
        if code == 0:
            current = subprocess.check_output(["/usr/bin/git", *opts, "rev-parse", "HEAD"], env=env, text=True).strip()
            recorded.write_text(current + "\n")
        return code


if __name__ == "__main__":
    try:
        sys.exit(run(sys.argv[1:]))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("widok Git pudełka: " + str(exc), file=sys.stderr)
        sys.exit(126)
