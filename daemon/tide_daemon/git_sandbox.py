"""Wspólna granica hostowych wywołań Git (PT-264).

Uruchamiana przez gitcommand jako osobny, izolowany interpreter. Landlock
rozstrzyga dostęp przy otwarciu pliku, także po podmianie dowiązania. Konfiguracja
repozytorium nie daje prawa wykonania filtra, haka, pomocnika ani połączenia TCP.
Brak wymaganego ABI oznacza odmowę, bez niezabezpieczonego ponowienia.
"""
import ctypes
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


class BoundaryError(ValueError):
    pass


def canonical(path):
    path = os.path.abspath(path)
    if os.path.realpath(path) != path:
        raise BoundaryError("Git: dowiązanie w ścieżce administracyjnej")
    return path


def read_file(path, limit=1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as fh:
        info = os.fstat(fh.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise BoundaryError("Git: niebezpieczny plik administracyjny")
        return fh.read(limit + 1).decode("utf-8")


def within(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def head_tip(common, admin):
    value = read_file(admin + "/HEAD", 8192).strip()
    for _ in range(16):
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
            return value
        if not value.startswith("ref: refs/"):
            break
        ref = value[5:]
        if any(part in ("", ".", "..") for part in ref.split("/")):
            break
        path = canonical(common + "/" + ref)
        if os.path.exists(path):
            value = read_file(path, 8192).strip()
            continue
        packed = common + "/packed-refs"
        for line in read_file(packed).splitlines() if os.path.exists(packed) else ():
            fields = line.split()
            if len(fields) == 2 and fields[1] == ref:
                value = fields[0]
                break
        else:
            break
    raise BoundaryError("Git: nieznany HEAD albo niebezpieczny ref")


def atomic_text(path, value):
    parent = canonical(os.path.dirname(path))
    fd, temporary = tempfile.mkstemp(prefix=".tide-index-", dir=parent)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(value)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initialize_indexes(repo):
    """Przed pierwszą ekspozycją, przy zatrzymanym pudełku; bez czytania jego home."""
    common = canonical(repo + "/.git")
    if os.path.lexists(common + "/tide-boundary"):
        if read_file(common + "/tide-boundary", 64) != "project-git:2\n":
            raise BoundaryError("Git: nieznany kontrakt indeksu")
        return  # Nowy worktree bez znacznika zostanie jawnie zsynchronizowany z HEAD.
    folder = common + "/worktrees"
    for name in os.listdir(folder) if os.path.isdir(folder) else ():
        admin = canonical(folder + "/" + name)
        target = os.path.dirname(read_file(admin + "/gitdir", 8192).strip())
        if not within(target, repo + "-worktrees"):
            continue
        registered = repository(target)
        if not registered or registered[1:3] != (common, admin):
            raise BoundaryError("Git: niespójny rejestr indeksów")
        path = admin + "/tide-index-tip"
        if not os.path.lexists(path):
            atomic_text(path, head_tip(common, admin) + "\n")
    atomic_text(common + "/tide-boundary", "project-git:2\n")


def global_options(args):
    cwd, gitdir, worktree = os.getcwd(), "", ""
    i = 0
    while i < len(args):
        item = args[i]
        if item in ("-C", "--git-dir", "--work-tree", "-c", "--config-env"):
            if i + 1 == len(args):
                raise BoundaryError("Git: niepełne opcje")
            value = args[i + 1]
            if item == "-C":
                cwd = os.path.abspath(os.path.join(cwd, value))
            elif item == "--git-dir":
                gitdir = os.path.abspath(os.path.join(cwd, value))
            elif item == "--work-tree":
                worktree = os.path.abspath(os.path.join(cwd, value))
            i += 2
        elif item.startswith("--git-dir="):
            gitdir = os.path.abspath(os.path.join(cwd, item.split("=", 1)[1]))
            i += 1
        elif item.startswith("--work-tree="):
            worktree = os.path.abspath(os.path.join(cwd, item.split("=", 1)[1]))
            i += 1
        elif item.startswith("-"):
            i += 1
        else:
            break
    return canonical(cwd), gitdir, worktree, args[i:]


def repository(cwd, explicit=""):
    """Nie uruchamia Git w celu ustalenia jego granicy ani nie ufa commondir."""
    worktree = cwd
    if explicit:
        admin = canonical(explicit)
    else:
        root = Path(cwd)
        while not os.path.lexists(root / ".git") and root.parent != root:
            root = root.parent
        if not os.path.lexists(root / ".git"):
            return None
        worktree = str(root)
        dotgit = str(root / ".git")
        if os.path.isdir(dotgit) and not os.path.islink(dotgit):
            admin = canonical(dotgit)
        else:
            text = read_file(dotgit, 8192).strip()
            if not text.startswith("gitdir: "):
                raise BoundaryError("Git: niepoprawny wskaźnik .git")
            admin = canonical(os.path.join(worktree, text[8:]))
    if Path(admin).parent.name == "worktrees" and Path(admin).parent.parent.name == ".git":
        common = str(Path(admin).parent.parent)
        canonical(common)
        if os.path.normpath(os.path.join(admin, read_file(admin + "/commondir", 8192).strip())) != common:
            raise BoundaryError("Git: commondir poza projektem")
        backlink = read_file(admin + "/gitdir", 8192).strip()
        target = canonical(os.path.dirname(backlink))
        if not explicit and target != worktree:
            raise BoundaryError("Git: wskaźnik .git nie odpowiada rejestrowi worktree")
        worktree = target
    elif Path(admin).name == ".git":
        if not explicit and admin != worktree + "/.git":
            raise BoundaryError("Git: wskaźnik worktree nie może wybierać obcego common-dir")
        common = admin
        worktree = str(Path(common).parent)
    else:
        raise BoundaryError("Git: administracja poza jawnym repozytorium")
    repo = str(Path(common).parent)
    # W przepływie Tide źródłem tożsamości jest konwencja hosta, nie plik agenta.
    for parent in Path(cwd).parents:
        if parent.name.endswith("-worktrees") and os.path.dirname(worktree) == str(parent):
            expected = str(parent)[:-len("-worktrees")]
            if os.path.isdir(expected + "/.git") and repo != expected:
                raise BoundaryError("Git: przekierowanie worktree do innego projektu")
            break
    for name in ("objects", "objects/info", "refs", "logs", "worktrees", "hooks", "info"):
        path = common + "/" + name
        if os.path.lexists(path):
            canonical(path)
    for name in ("objects/info/alternates", "objects/info/http-alternates"):
        if os.path.lexists(common + "/" + name):
            raise BoundaryError("Git: alternates poza kontraktem projektu")
    canonical(worktree)
    return repo, common, admin, worktree


class Ruleset:
    def __init__(self):
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.syscall.restype = ctypes.c_long
        abi = self.libc.syscall(444, 0, 0, 1)
        if abi < 6:
            raise BoundaryError("Git: wymagany Landlock ABI >= 6 (pliki, TCP i zakres IPC)")
        # ABI 5 dodaje IOCTL_DEV. Nie przyznajemy tworzenia urządzeń/FIFO/gniazd.
        self.handled = (1 << 16) - 1
        attributes = (ctypes.c_uint64 * 3)(self.handled, 3, 3)
        self.fd = self.libc.syscall(444, ctypes.byref(attributes), ctypes.sizeof(attributes), 0)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "Landlock create")

    def allow(self, path, rights):
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                rights &= (1 << 0) | (1 << 1) | (1 << 2) | (1 << 14) | (1 << 15)
            class Rule(ctypes.Structure):
                _pack_ = 1
                _fields_ = [("access", ctypes.c_uint64), ("parent", ctypes.c_int32)]
            rule = Rule(rights, fd)
            if self.libc.syscall(445, self.fd, 1, ctypes.byref(rule), 0) < 0:
                raise OSError(ctypes.get_errno(), "Landlock add")
        finally:
            os.close(fd)

    def restrict(self):
        if self.libc.prctl(38, 1, 0, 0, 0) or self.libc.syscall(446, self.fd, 0):
            raise OSError(ctypes.get_errno(), "Landlock restrict")
        os.close(self.fd)

    def close(self):
        os.close(self.fd)


READ = (1 << 2) | (1 << 3)
WRITE = READ | sum(1 << bit for bit in (1, 4, 5, 7, 8, 12, 13, 14))
IDENTITY = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE")


def namespace(payload, cwd, reads, writes):
    """Osobny rootfs ogranicza też chmod/utime i gniazda ścieżkowe, których
    sama polityka plikowa Landlock nie obejmuje. Montujemy tylko jawne korzenie.
    """
    argv = ["/usr/bin/bwrap", "--die-with-parent", "--new-session", "--unshare-net", "--unshare-ipc",
            "--unshare-pid", "--tmpfs", "/", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--tmpfs", "/run"]
    for path in ("/usr", "/lib", "/lib64"):
        if os.path.exists(path):
            argv += ["--ro-bind", path, path]
    for path in ("/etc/ld.so.cache", "/etc/localtime", "/etc/passwd", "/etc/nsswitch.conf"):
        if os.path.exists(path):
            argv += ["--ro-bind", path, path]
    modes = {canonical(path): "--ro-bind" for path in reads}
    modes.update({canonical(path): "--bind" for path in writes})
    for path in sorted(modes, key=lambda p: (p.count("/"), p)):
        argv += [modes[path], path, path]
    source = os.path.abspath(__file__)
    # Kod 1 bwrap nie oznacza „brak klucza”/„brak zmian” w Git. Potwierdzenie
    # zapisuje wyłącznie zaufany interpreter; proces Git nie ma tu reguły Landlock.
    with tempfile.TemporaryDirectory(prefix="tide-git-status-") as status_dir:
        argv += ["--bind", status_dir, "/run/tide-git-status",
                 "--ro-bind", source, "/run/tide-host-git.py", "--chdir", cwd,
                 "--", "/usr/bin/python3", "-I", "/run/tide-host-git.py",
                 json.dumps({**payload, "_namespaced": True, "_statusPath": "/run/tide-git-status/code"})]
        result = subprocess.run(argv)
        try:
            code = int(read_file(status_dir + "/code", 16))
            if 0 <= code <= 255 and result.returncode == code:
                return code
        except (OSError, ValueError):
            pass
        raise BoundaryError("Git: transport piaskownicy nie potwierdził wyniku operacji")


def execute(payload):
    args = payload["args"]
    cwd, explicit, worktree, command = global_options(args)
    private = payload.get("private", False)
    file_config = command[:1] == ["config"] and any(x in command for x in ("--file", "-f"))
    initializing = command[:1] == ["init"]
    if file_config:
        command = list(command)
        for i, item in enumerate(command[:-1]):
            if item in ("--file", "-f") and command[i + 1] != "/dev/null":
                command[i + 1] = os.path.abspath(os.path.join(cwd, command[i + 1]))
        args = command
        payload = {**payload, "args": args}
        cwd = "/tmp"
    found = None if private or file_config or initializing else repository(cwd, explicit)
    if found and worktree and canonical(worktree) != found[3]:
        raise BoundaryError("Git: katalog administracyjny nie odpowiada jawnemu worktree")
    if found and explicit:
        cwd = found[3]  # Usługa mogła wystartować w innym projekcie; nie montujemy jej cwd.
    roots = [cwd] if private else ([found[0], found[3]] if found else [])
    if initializing:
        roots.append(cwd)
    for suffix in ("-worktrees", "-wt"):
        if found and os.path.isdir(found[0] + suffix):
            roots.append(canonical(found[0] + suffix))
    if private:
        if not payload.get("readRoots"):
            raise BoundaryError("Git: prywatny widok bez jawnego projektu")
    reads = list(payload.get("readRoots", ()))
    writes = roots + list(payload.get("writeRoots", ()))
    if not found and not private and not file_config and not initializing:
        reads.append(cwd)
    if found and command[:2] == ["worktree", "list"]:
        # Git sprawdza istnienie .git zarejestrowanych checkoutów. Dla worktrees
        # spoza konwencjonalnego kontenera pokazujemy wyłącznie sprawdzony wskaźnik,
        # nie ich katalog domowy ani treść. Inaczej istniejący checkout staje się
        # w widoku skanera pozornie prunable.
        registry = found[1] + "/worktrees"
        for name in os.listdir(registry) if os.path.isdir(registry) else ():
            admin = canonical(registry + "/" + name)
            pointer = read_file(admin + "/gitdir", 8192).strip()
            if not os.path.isfile(pointer) or any(within(pointer, root) for root in writes):
                continue
            target = canonical(os.path.dirname(pointer))
            registered = repository(target)
            if not registered or registered[1:3] != (found[1], admin) or pointer != target + "/.git":
                raise BoundaryError("Git: niespójny zewnętrzny rejestr worktree")
            reads.append(canonical(pointer))
    if command[:1] == ["worktree"] and len(command) > 1 and command[1] in ("add", "remove"):
        i = 2
        while i < len(command):
            item = command[i]
            if item in ("-b", "-B", "--reason"):
                i += 2
            elif item.startswith("-"):
                i += 1
            else:
                target = os.path.abspath(os.path.join(cwd, item))
                if not found:
                    raise BoundaryError("Git: worktree bez jawnego repozytorium")
                candidates = (found[0] + "-worktrees", found[0] + "-wt",
                              found[0] + "/.claude/worktrees")
                container = next((p for p in candidates if within(target, p) and target != p), None)
                if container is None or not os.path.isdir(container):
                    raise BoundaryError("Git: worktree poza jawnym katalogiem projektu")
                writes.append(canonical(container))
                break
    # Jawne ścieżki wynikają z argv skonstruowanego przez usługę, nigdy z config.
    if command[:1] == ["config"]:
        for i, item in enumerate(command[:-1]):
            if item in ("--file", "-f") and command[i + 1] != "/dev/null":
                path = canonical(command[i + 1])
                if os.path.isfile(path):
                    read_file(path)
                    reads.append(path)
    if command[:1] == ["archive"]:
        for i, item in enumerate(command):
            if item.startswith("--output="):
                writes.append(canonical(os.path.dirname(os.path.abspath(item.split("=", 1)[1]))))
            elif item == "--output" and i + 1 < len(command):
                writes.append(canonical(os.path.dirname(os.path.abspath(command[i + 1]))))
    executable = os.path.realpath(payload.get("executable", "/usr/bin/git"))
    if executable != os.path.realpath("/usr/bin/git"):
        raise BoundaryError("Git: host używa wyłącznie /usr/bin/git")
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": "/nonexistent/tide-git",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
           "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1",
           **{key: os.environ[key] for key in IDENTITY if key in os.environ}}
    # StepGit ma jawnie związany katalog indeksu, a nie odziedziczone GIT_INDEX_FILE.
    if payload.get("index"):
        env["GIT_INDEX_FILE"] = payload["index"]
    if found:
        env["GIT_COMMON_DIR"] = found[1]
    if private:
        env["GIT_DIR"] = explicit or cwd
    if not payload.get("_namespaced"):
        return namespace(payload, cwd, reads, writes)
    with tempfile.TemporaryDirectory(prefix="tide-host-git-") as temporary:
        env["TMPDIR"] = temporary
        if not file_config and os.environ.get("TIDE_GIT_IDENTITY_JSON"):
            identity = json.loads(os.environ["TIDE_GIT_IDENTITY_JSON"])
            lines = ["[user]"]
            for key in ("name", "email"):
                value = identity.get(key)
                if isinstance(value, str) and not any(ord(c) < 32 and c != "\t" or ord(c) == 127 for c in value):
                    quoted = value.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
                    lines.append(f'{key} = "{quoted}"')
            identity_path = temporary + "/identity"
            with open(identity_path, "x") as fh:
                fh.write("\n".join(lines) + "\n")
            env["GIT_CONFIG_GLOBAL"] = identity_path
        if file_config:
            env["GIT_DIR"] = temporary
        rules = Ruleset()
        try:
            for path in ("/usr", "/lib", "/lib64"):
                if os.path.exists(path):
                    rules.allow(path, READ)
            for path in ("/etc/ld.so.cache", "/etc/localtime", "/etc/passwd", "/etc/nsswitch.conf",
                         "/dev/urandom", "/dev/random"):
                if os.path.exists(path):
                    rules.allow(path, READ)
            rules.allow("/dev/null", (1 << 1) | (1 << 2))
            rules.allow(executable, READ | 1)
            if os.path.exists("/usr/lib/git-core/git"):
                rules.allow(os.path.realpath("/usr/lib/git-core/git"), READ | 1)
            # PT_INTERP też przechodzi kontrolę EXECUTE.
            for path in ("/lib64/ld-linux-x86-64.so.2", "/lib/ld-linux-aarch64.so.1"):
                if os.path.exists(path):
                    rules.allow(os.path.realpath(path), READ | 1)
            for path in reads:
                rules.allow(canonical(path), READ)
            for path in dict.fromkeys(writes + [temporary]):
                rules.allow(canonical(path), WRITE)
            # --no-pager i nadpisania po opcjach odbiorcy; projekt nie wybiera programów.
            pinned = [] if not found else ["--git-dir=" + found[2], "--work-tree=" + found[3]]
            config_cwd = ["-C", temporary] if file_config else []
            safe = [executable, "--no-pager", *config_cwd, *pinned, *args]
            def invoke(argv, **kwargs):
                return subprocess.run(argv, env=env, preexec_fn=rules.restrict, pass_fds=(rules.fd,), **kwargs)
            index_commands = {"status", "diff", "ls-files", "add", "commit", "update-index", "read-tree",
                              "write-tree", "checkout", "checkout-index", "reset", "merge", "restore"}
            if (found and found[2] != found[1] and command and command[0] in index_commands and
                    os.path.exists(found[1] + "/tide-boundary")):
                if read_file(found[1] + "/tide-boundary", 64) != "project-git:2\n":
                    raise BoundaryError("Git: nieznany kontrakt indeksu")
                path = found[2] + "/tide-index-tip"
                # Stan jest w administracji hosta pod bazowym mountem ro. Home
                # pudełka i jego prywatny indeks nie wybierają tego pliku.
                lockfd = os.open(found[2] + "/tide-index.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                with os.fdopen(lockfd, "r+") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    tip = head_tip(found[1], found[2])
                    old = read_file(path, 128).strip() if os.path.lexists(path) else None
                    if old != tip:
                        sync = invoke([executable, *pinned, "-c", "core.hooksPath=/dev/null", "-c",
                                       "core.fsmonitor=false", "read-tree", "--reset", tip], capture_output=True)
                        if sync.returncode:
                            raise BoundaryError("Git: synchronizacja indeksu hosta odmówiona")
                        atomic_text(path, tip + "\n")
                    result = invoke(safe)
                    if result.returncode == 0:
                        atomic_text(path, head_tip(found[1], found[2]) + "\n")
                    return result.returncode
            return invoke(safe).returncode
        finally:
            rules.close()


def main():
    payload = {}
    try:
        payload = json.loads(sys.argv[1])
        code = execute(payload)
    except (BoundaryError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("granica Git: " + str(exc), file=sys.stderr)
        code = 126
    if payload.get("_namespaced") and payload.get("_statusPath"):
        with open(payload["_statusPath"], "x") as fh:
            fh.write(str(code) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
