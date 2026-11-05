"""Odczyt checkoutu do podglądu scalenia. Bez odświeżania indeksu, filtrów i zapisów gita."""
from __future__ import annotations

import hashlib
import os
import stat


def overlaps(left: str, right: str) -> bool:
    """Kolizja obejmuje także zastąpienie pliku katalogiem (w obu kierunkach)."""
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def status_entries(text: str) -> dict:
    changes = {kind: [] for kind in ("staged", "unstaged", "untracked")}
    fields = text.split("\0")
    i = 0
    while i < len(fields):
        item = fields[i]
        i += 1
        if not item:
            continue
        code, path = item[:2], item[3:]
        paths = [path]
        if "R" in code or "C" in code:
            paths.append(fields[i])
            i += 1
        entry = {"status": code, "paths": paths}
        if code == "??":
            changes["untracked"].append(entry)
        else:
            if code[0] != " ":
                changes["staged"].append(entry)
            if code[1] != " ":
                changes["unstaged"].append(entry)
    return changes


def file_stamp(path: str):
    """Treść i typ; symlink odczytujemy jako dowiązanie, nigdy jako jego cel."""
    try:
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode):
            return [mode, os.readlink(path)]
        if stat.S_ISREG(mode):
            digest = hashlib.sha256()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise OSError("plik zmienił typ podczas odczytu")
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return [mode, digest.hexdigest()]
        return [mode]
    except FileNotFoundError:
        return None


def checkout_snapshot(run, path: str) -> dict:
    if not path:
        return {"exists": False}

    def read(*args):
        result = run(path, *args)
        if not result.ok:
            raise OSError("nie można odczytać checkoutu: " + " ".join(args[:2]))
        return result.out

    head = read("rev-parse", "--verify", "HEAD").strip()
    # symboliczny HEAD albo jawnie odłączony — oba stany mają własny odcisk.
    symbolic = run(path, "symbolic-ref", "--quiet", "HEAD")
    if symbolic.code not in (0, 1):
        raise OSError("nie można odczytać gałęzi checkoutu")
    changes = status_entries(read("status", "--porcelain=v1", "-z", "--untracked-files=all",
                                  "--ignore-submodules=none"))
    index = read("rev-parse", "--path-format=absolute", "--git-path", "index").strip()
    gitdir = read("rev-parse", "--absolute-git-dir").strip()
    index_entries = read("ls-files", "--stage", "-v", "-z")
    gitlinks = {entry.partition("\t")[2] for entry in index_entries.split("\0")
                if entry[2:].startswith("160000 ")}
    paths = sorted({p for entries in changes.values() for entry in entries for p in entry["paths"]})

    def local_stamp(rel):
        full = os.path.join(path, rel)
        # Usunięty katalog mógł zostać zastąpiony dowiązaniem. Nie czytamy przez nie plików poza checkoutem.
        if os.path.commonpath((os.path.realpath(path), os.path.realpath(os.path.dirname(full)))) != os.path.realpath(path):
            return ["outside-checkout"]
        if rel in gitlinks and os.path.isdir(full) and not os.path.islink(full):
            # Sam status „ M moduł” nie zmienia się przy kolejnej edycji wewnątrz submodułu.
            return checkout_snapshot(run, full)
        return file_stamp(full)

    return {"exists": True, "path": os.path.realpath(path), "head": head,
            "branch": symbolic.out.strip(), "index": file_stamp(index),
            # Zawartość logiczna obejmuje też sharedindex oraz flagi skip-worktree/assume-unchanged.
            "indexEntries": index_entries, "changes": changes,
            "files": {p: local_stamp(p) for p in paths},
            "operations": {name: file_stamp(os.path.join(gitdir, name))
                           for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                                        "rebase-merge", "rebase-apply", "sequencer", "index.lock")}}


def assess_local(snapshot: dict, files: list, conflicts: list) -> dict:
    touched = {p for f in files for p in (f["oldPath"], f["newPath"]) if p} | set(conflicts)
    changes = snapshot.get("changes", {kind: [] for kind in ("staged", "unstaged", "untracked")})
    collisions = {kind: [] for kind in changes}
    for kind, entries in changes.items():
        for entry in entries:
            hits = sorted({changed for changed in touched for path in entry["paths"] if overlaps(path, changed)})
            if hits:
                collisions[kind].append({**entry, "mergePaths": hits})
    return {"localChanges": changes, "localCollisions": collisions,
            "localCollisionCount": sum(map(len, collisions.values()))}


def source_dirty(snapshot: dict) -> bool:
    from ..tasks.agentcmd import TOOLING_DIRS
    changes = snapshot.get("changes", {})
    prefixes = tuple(d.strip("/") + "/" for d in TOOLING_DIRS)
    return bool(changes.get("staged") or changes.get("unstaged") or
                any(not all(p.startswith(prefixes) for p in entry["paths"])
                    for entry in changes.get("untracked", [])))
