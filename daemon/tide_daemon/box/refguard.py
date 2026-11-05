"""Migawki wspólnego .git poza pudełkiem; porównanie i naprawa pod blokadą repozytorium.

Dozwolone refy zapamiętujemy jako sumę gałęzi kroków tej samej karty, które nakładały się
w czasie. Zakończenie sąsiada nie odbiera uprawnienia starszej migawce. Kopie i rejestr
są w stanie hosta, nigdy w zapisywalnym przez agenta katalogu kroku.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import stat
import time

from ..tasks.agentcmd import StepError
from ..worktrees.git import Git

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _root(repo):
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    key = hashlib.sha256(os.path.realpath(repo).encode()).hexdigest()
    return os.path.join(state, "tide", "refguard", key)


def _path(repo, card, step):
    key = hashlib.sha256(json.dumps([card, step]).encode()).hexdigest()
    return os.path.join(_root(repo), key + ".json")


@contextmanager
def locked(repo, *, existing_only=False):
    # Ta sama blokada co tworzenie worktrees i scalenia; flock obejmuje też drugi proces usługi.
    from ..cardflow.gitops import lock_for
    with lock_for(repo):
        root = _root(repo)
        if existing_only and not os.path.isdir(root):
            yield False
            return
        os.makedirs(root, mode=0o700, exist_ok=True)
        with open(os.path.join(root, "lock"), "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield True
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _save(path, value):
    tmp = path + "." + secrets.token_hex(8)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=True, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _active(repo):
    active = []
    for name in sorted(os.listdir(_root(repo))):
        if name.endswith(".json"):
            path = os.path.join(_root(repo), name)
            data = _load(path)
            if data.get("active"):
                active.append((path, data))
    return sorted(active, key=lambda pair: pair[1]["started"])


def _git(repo, *args):
    result = Git().run(repo, *args)
    if not result.ok:
        raise StepError("strażnik refów: git: " + result.err)
    return result.out.strip()


def _refs(repo):
    text = _git(repo, "for-each-ref", "--format=%(refname) %(objectname) %(symref)")
    refs = {}
    for line in text.splitlines():
        ref, oid, *target = line.split()
        refs[ref] = {"oid": oid, "target": target[0] if target else ""}
    # for-each-ref pomija aliasy o nieistniejącym celu. Czytamy także luźne refy,
    # bez podążania za dowiązaniami i bez traktowania plików .lock jako refów.
    root = _open_git(repo)
    try:
        loose = _node(root, "refs")
    finally:
        os.close(root)

    def visit(path, node):
        if not node:
            return
        if node["kind"] == "dir":
            for name, child in node["children"].items():
                if not name.endswith(".lock"):
                    visit(path + "/" + name, child)
        elif node["kind"] == "file" and path not in refs:
            content = base64.b64decode(node["data"])
            if content.startswith(b"ref: "):
                refs[path] = {"oid": "", "target": content[5:].decode("utf-8").strip()}

    visit("refs", loose)
    return refs


def _same_ref(left, right):
    # OID refu symbolicznego zmienia się też przy legalnym commicie na jego celu.
    # Sam alias pozostaje nietknięty, jeżeli nadal wskazuje ten sam ref.
    if left and right and left["target"] and right["target"]:
        return left["target"] == right["target"]
    return left == right


def _node(parent, name):
    try:
        meta = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    mode = stat.S_IMODE(meta.st_mode)
    if stat.S_ISLNK(meta.st_mode):
        return {"kind": "link", "target": os.readlink(name, dir_fd=parent)}
    if stat.S_ISDIR(meta.st_mode):
        fd = os.open(name, DIR_FLAGS, dir_fd=parent)
        try:
            return {"kind": "dir", "mode": mode,
                    "children": {n: _node(fd, n) for n in sorted(os.listdir(fd))}}
        finally:
            os.close(fd)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            return {"kind": "special", "mode": mode}
        content = fh.read()
    return {"kind": "file", "mode": mode, "sha256": hashlib.sha256(content).hexdigest(),
            "data": base64.b64encode(content).decode("ascii")}


@contextmanager
def _parent(root, path, *, create=False):
    fd = os.dup(root)
    try:
        parts = path.split("/")
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


def _files(root, *, errors=None):
    def failed(path, exc):
        if errors is None:
            raise exc
        errors[path] = str(exc)

    paths = ["hooks", "config", "info", "config.worktree"]
    try:
        wts = os.open("worktrees", DIR_FLAGS, dir_fd=root)
    except FileNotFoundError:
        wts = None
    except OSError as exc:
        failed("worktrees", exc)
        wts = None
    if wts is not None:
        try:
            for name in sorted(os.listdir(wts)):
                path = f"worktrees/{name}"
                try:
                    meta = os.stat(name, dir_fd=wts, follow_symlinks=False)
                    if not stat.S_ISDIR(meta.st_mode):
                        raise StepError("wpis administracyjny nie jest katalogiem")
                    paths.append(path + "/config.worktree")
                except (OSError, StepError) as exc:
                    failed(path, exc)
        except OSError as exc:
            failed("worktrees", exc)
        finally:
            os.close(wts)
    out = {}
    for path in paths:
        try:
            with _parent(root, path) as (parent, name):
                node = _node(parent, name)
            if node is not None:
                out[path] = node
        except (OSError, StepError) as exc:
            failed(path, exc)
    return out


def _remove(parent, name):
    try:
        meta = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(meta.st_mode):
        shutil.rmtree(name, dir_fd=parent)
    else:
        os.unlink(name, dir_fd=parent)


def _restore(parent, name, node):
    if node is None:
        _remove(parent, name)
    elif node["kind"] == "dir":
        # Nie podążamy za dowiązaniem ani hardlinkiem podłożonym zamiast pliku.
        try:
            fd = os.open(name, DIR_FLAGS, dir_fd=parent)
        except (NotADirectoryError, FileNotFoundError):
            _remove(parent, name)
            os.mkdir(name, node["mode"], dir_fd=parent)
            fd = os.open(name, DIR_FLAGS, dir_fd=parent)
        try:
            for child in set(os.listdir(fd)) | set(node["children"]):
                _restore(fd, child, node["children"].get(child))
            os.fchmod(fd, node["mode"])
        finally:
            os.close(fd)
    else:
        tmp = ".tide-guard-" + secrets.token_hex(8)
        if node["kind"] == "link":
            os.symlink(node["target"], tmp, dir_fd=parent)
        elif node["kind"] == "file":
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(fd, "wb") as fh:
                fh.write(base64.b64decode(node["data"]))
                os.fchmod(fh.fileno(), node["mode"])
                fh.flush()
                os.fsync(fh.fileno())
        else:
            raise StepError("strażnik refów: plik specjalny w kopii")
        try:
            try:
                if stat.S_ISDIR(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode):
                    _remove(parent, name)
            except FileNotFoundError:
                pass
            os.replace(tmp, name, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            try:
                os.unlink(tmp, dir_fd=parent)
            except FileNotFoundError:
                pass


def _open_git(repo):
    return os.open(os.path.join(os.path.realpath(repo), ".git"), DIR_FLAGS)


def begin(repo, card, step, git_dir, *, branch, prepare=None, worktree=""):
    """Zapisuje migawkę dopiero pod blokadą; prepare wykonuje zaufane montowania runnera."""
    with locked(repo):
        peers = list(_active(repo))
        if any(peer["card"] != card for _path_peer, peer in peers):
            raise StepError("strażnik refów: w repozytorium trwa krok innej karty")
        if not branch:
            raise StepError("strażnik refów: brak zaufanej gałęzi kroku")
        branch = "refs/heads/" + branch
        _git(repo, "check-ref-format", branch)
        if _git(repo, "--git-dir=" + git_dir, "symbolic-ref", "--quiet", "HEAD") != branch:
            raise StepError("strażnik refów: HEAD nie wskazuje przypisanej gałęzi kroku")
        root = _open_git(repo)
        try:
            before = _files(root)
            if prepare is not None:
                prepare()
            files = _files(root)
        finally:
            os.close(root)
        # Runner może założyć pusty config.worktree dla nowego kroku. To zmiana usługi,
        # którą wcześniejsze migawki muszą znać, bez zatwierdzania innych zmian agenta.
        own_config = os.path.relpath(git_dir, os.path.join(os.path.realpath(repo), ".git")) + "/config.worktree"
        trusted = {p: n for p, n in files.items() if p not in before and (
            p in ("hooks", "info") and n.get("kind") == "dir" and not n["children"] or
            p == own_config and n.get("kind") == "file" and not n["data"])}
        allowed = {branch}
        refs = _refs(repo)
        # Nie utrwalamy naruszenia sąsiada jako nowego punktu przywracania.
        # Inaczej drugi kończący krok mógłby odtworzyć usunięty już obcy hak/ref.
        for _path_peer, peer in peers:
            expected_files = {**peer["files"], **{p: n for p, n in trusted.items() if p not in peer["files"]}}
            if files != expected_files:
                raise StepError("strażnik refów: chronione pliki zmienione w aktywnym kroku")
            permitted = set(peer["allowed"]) | ({branch} if peer["card"] == card else set())
            if any(not _same_ref(refs.get(r), peer["refs"].get(r))
                   for r in set(refs) | set(peer["refs"]) if r not in permitted):
                raise StepError("strażnik refów: obce refy zmienione w aktywnym kroku")
        for path, peer in peers:
            for p, node in trusted.items():
                if p not in peer["files"]:
                    peer["files"][p] = node
            if peer["card"] == card:
                allowed.add(peer["branch"])
                peer["allowed"] = sorted(set(peer["allowed"]) | {branch})
            _save(path, peer)
        snapshot = dict(active=True, started=time.time_ns(), card=card, step=step, gitDir=git_dir, branch=branch,
                        allowed=sorted(allowed), refs=refs, files=files, worktree=worktree)
        if worktree:
            fd = os.open(worktree, DIR_FLAGS)
            try:
                snapshot["pointer"] = _node(fd, ".git")
            finally:
                os.close(fd)
        _save(_path(repo, card, step), snapshot)


def cancel(repo, card, step):
    """Start nie uruchomił procesu; migawka nie jest aktywnym krokiem."""
    with locked(repo):
        path = _path(repo, card, step)
        try:
            data = _load(path)
        except FileNotFoundError:
            return
        data["active"] = False
        _save(path, data)


def other_cards(repo, card):
    """Kolejkujemy inne karty zamiast uznawać ich legalne commity za obce zmiany."""
    with locked(repo, existing_only=True) as present:
        return sorted({peer["card"] for _path_peer, peer in _active(repo) if peer["card"] != card}) if present else []


@contextmanager
def host_operation(repo, args):
    """Zaufana zmiana konkretnego refu/configu przez gitops, bez zatwierdzania obcych zmian."""
    with locked(repo, existing_only=True) as present:
        if not present:
            yield
            return
        peers = list(_active(repo))
        if not peers:
            yield
            return
        refs = [arg for arg in args if arg.startswith("refs/")] if args[0] == "update-ref" else []
        if args[:2] == ("worktree", "add") and "-b" in args:
            refs = ["refs/heads/" + args[args.index("-b") + 1]]
        paths = []
        if args[:2] == ("worktree", "remove"):
            from ..tasks.agentcmd import resolve_git_dir
            admin = resolve_git_dir(repo, args[-1])
            paths = [os.path.relpath(admin, os.path.join(os.path.realpath(repo), ".git")) + "/config.worktree"]
        before_refs = _refs(repo) if refs else {}
        root = _open_git(repo)
        try:
            before_files = _files(root) if paths else {}
            yield
            after_refs = _refs(repo) if refs else {}
            after_files = _files(root) if paths else {}
        finally:
            os.close(root)
        for path, peer in peers:
            for ref in refs:
                if peer["refs"].get(ref) == before_refs.get(ref):
                    if ref in after_refs:
                        peer["refs"][ref] = after_refs[ref]
                    else:
                        peer["refs"].pop(ref, None)
            for p in paths:
                if peer["files"].get(p) == before_files.get(p):
                    if p in after_files:
                        peer["files"][p] = after_files[p]
                    else:
                        peer["files"].pop(p, None)
            _save(path, peer)


def _restore_symref(repo, ref, old, now):
    # Starszy git (dystrybucja hosta) nie ma symref-update w update-ref --stdin.
    # Bierzemy standardowy <ref>.lock i sprawdzamy zarówno OID, jak i cel symboliczny.
    root = _open_git(repo)
    try:
        with _parent(root, ref, create=True) as (parent, name):
            lock = name + ".lock"
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as fh:
                    if _refs(repo).get(ref) != now:
                        raise StepError(f"{ref}: zmieniony po porównaniu")
                    if old:
                        value = "ref: " + old["target"] if old["target"] else old["oid"]
                        fh.write((value + "\n").encode())
                    fh.flush()
                    os.fsync(fh.fileno())
                if old:
                    os.replace(lock, name, src_dir_fd=parent, dst_dir_fd=parent)
                else:
                    os.unlink(name, dir_fd=parent)
            finally:
                try:
                    os.unlink(lock, dir_fd=parent)
                except FileNotFoundError:
                    pass
    finally:
        os.close(root)


def finish(repo, card, step, *, branch="", active_only=False):
    """Trwały wynik po pełnej kontroli; błędy naprawy pozostawiają migawkę do ponowienia.

    active_only służy odzyskiwaniu: pomija brak migawki starszych kroków i zakończone kontrole.
    Odczyt stanu oraz decyzja o ponowieniu odbywają się pod tą samą blokadą co naprawa.
    """
    report = {"refs": [], "files": [], "head": False, "errors": []}

    @contextmanager
    def checking(label):
        # Uszkodzenie jednego obiektu nie pomija pozostałych niezależnych napraw.
        try:
            yield
        except (OSError, ValueError, KeyError, StepError) as exc:
            report["errors"].append(f"{label}: {exc}")

    with locked(repo, existing_only=active_only) as present:
        if not present:
            return {}
        path = _path(repo, card, step)
        try:
            saved = _load(path)
            if active_only and not saved.get("active"):
                return {}
            if "report" in saved:
                return saved["report"]
            previous = saved.get("pendingReport", {})
            for field in ("refs", "files", "head"):
                if field in previous:
                    report[field] = previous[field]
        except FileNotFoundError as exc:
            if not active_only:
                report["errors"].append(str(exc))
            return report if report["errors"] else {}
        except (OSError, ValueError, KeyError) as exc:
            report["errors"].append(str(exc))
            return report
        with checking("pliki"):
            root = _open_git(repo)
            try:
                unreadable = {}
                current = _files(root, errors=unreadable)
                report["errors"].extend(f"{p}: {error}" for p, error in unreadable.items())
                for p in sorted(set(current) | set(saved["files"])):
                    if any(p == failed or p.startswith(failed + "/") for failed in unreadable):
                        continue
                    if current.get(p) != saved["files"].get(p):
                        report["files"].append(p)
                        with checking(p):
                            with _parent(root, p) as (parent, name):
                                _restore(parent, name, saved["files"].get(p))
            finally:
                os.close(root)
        # Najpierw próbujemy odtworzyć konfigurację, ale błąd pliku nie pomija refów/HEAD.
        with checking("refy"):
            current = _refs(repo)
            for ref in sorted(set(current) | set(saved["refs"])):
                old, now = saved["refs"].get(ref), current.get(ref)
                if _same_ref(old, now) or ref in saved["allowed"]:
                    continue
                report["refs"].append(ref)
                # --no-deref nie dotyka celu obcego refu symbolicznego. CAS obejmuje
                # aktualną wartość: legalny ruch po porównaniu nie zostanie nadpisany.
                if (old and old["target"]) or (now and now["target"]):
                    with checking(ref):
                        _restore_symref(repo, ref, old, now)
                    continue
                zero = "0" * len((now or old)["oid"])
                if old:
                    args = (ref, old["oid"], now["oid"] if now else zero)
                else:
                    args = ("-d", ref, now["oid"])
                with checking(ref):
                    result = Git().run(repo, "update-ref", "--no-deref", *args)
                    if not result.ok:
                        report["errors"].append(f"{ref}: {result.err}")
        with checking("HEAD"):
            expected = "refs/heads/" + branch if branch else saved["branch"]
            common = os.path.join(os.path.realpath(repo), ".git")
            relative = os.path.relpath(saved["gitDir"], common)
            if not relative.startswith("worktrees/") or ".." in relative.split("/"):
                raise StepError("strażnik refów: katalog administracyjny poza worktrees")
            root = _open_git(repo)
            try:
                with _parent(root, relative + "/HEAD") as (parent, name):
                    head = _node(parent, name)
                    wanted = ("ref: " + expected + "\n").encode()
                    if not head or head.get("kind") != "file" or base64.b64decode(head["data"]) != wanted:
                        report["head"] = True
                        _restore(parent, name, {"kind": "file", "mode": 0o644,
                                                "data": base64.b64encode(wanted).decode("ascii")})
            finally:
                os.close(root)
        if saved.get("worktree"):
            with checking("worktree/.git"):
                fd = os.open(saved["worktree"], DIR_FLAGS)
                try:
                    if _node(fd, ".git") != saved["pointer"]:
                        report["files"].append("worktree/.git")
                        _restore(fd, ".git", saved["pointer"])
                finally:
                    os.close(fd)
        for field in ("refs", "files"):
            report[field] = sorted(set(report[field]))
        result = report if any(report.values()) else {}
        if report["errors"]:
            saved.update(active=True, pendingReport=result)
        else:
            saved.pop("pendingReport", None)
            saved.update(active=False, report=result)
        _save(path, saved)
        return result
