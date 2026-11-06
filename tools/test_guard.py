"""Strażnik przypadkowych zapisów testów: Linux x86-64, seccomp user notification.

Proces macierzysty nadzoruje wywołania całego drzewa, zanim jądro wykona mutację.
Nie jest to piaskownica do wykonywania wrogiego kodu (CONTINUE ma wyścig TOCTOU).
ABI struktur i stałe: linux/seccomp.h, linux/filter.h, asm/unistd_64.h.
"""
import array
import ctypes as C
import errno
import json
import os
import platform
import select
import signal
import socket
import sys
import threading
import time

ACTIVE = "TIDE_TEST_GUARD"
PROBE = 0x54494445  # prywatne, bezskutkowe prctl; bez naszego filtra zwraca EINVAL
FAILURE = 125
ALLOW = 0x7fff0000
NOTIFY = 0x7fc00000


class Filter(C.Structure):
    _fields_ = [("code", C.c_ushort), ("jt", C.c_ubyte), ("jf", C.c_ubyte), ("k", C.c_uint)]


class Program(C.Structure):
    _fields_ = [("len", C.c_ushort), ("filter", C.POINTER(Filter))]


class Data(C.Structure):
    _fields_ = [("nr", C.c_int), ("arch", C.c_uint), ("ip", C.c_ulonglong),
                ("args", C.c_ulonglong * 6)]


class Notification(C.Structure):
    _fields_ = [("id", C.c_ulonglong), ("pid", C.c_uint), ("flags", C.c_uint), ("data", Data)]


class Response(C.Structure):
    _fields_ = [("id", C.c_ulonglong), ("value", C.c_longlong),
                ("error", C.c_int), ("flags", C.c_uint)]


class OpenHow(C.Structure):
    _fields_ = [("flags", C.c_ulonglong), ("mode", C.c_ulonglong), ("resolve", C.c_ulonglong)]


# nr -> (nazwa, indeks ścieżki, indeks dirfd lub None). Drugi cel rename/link
# sprawdzamy osobno, bo chronimy obie strony operacji.
PATHS = {
    2: ("open", 0, None), 76: ("truncate", 0, None), 82: ("rename", 0, None),
    83: ("mkdir", 0, None), 84: ("rmdir", 0, None), 85: ("creat", 0, None),
    86: ("link", 0, None), 87: ("unlink", 0, None), 88: ("symlink", 1, None),
    90: ("chmod", 0, None), 92: ("chown", 0, None), 94: ("lchown", 0, None),
    132: ("utime", 0, None), 133: ("mknod", 0, None),
    188: ("setxattr", 0, None), 189: ("lsetxattr", 0, None),
    197: ("removexattr", 0, None), 198: ("lremovexattr", 0, None),
    235: ("utimes", 0, None), 257: ("openat", 1, 0), 258: ("mkdirat", 1, 0),
    259: ("mknodat", 1, 0), 260: ("fchownat", 1, 0), 261: ("futimesat", 1, 0),
    263: ("unlinkat", 1, 0), 264: ("renameat", 1, 0), 265: ("linkat", 1, 0),
    266: ("symlinkat", 2, 1), 268: ("fchmodat", 1, 0), 280: ("utimensat", 1, 0),
    316: ("renameat2", 1, 0), 437: ("openat2", 1, 0), 452: ("fchmodat2", 1, 0),
}
FDS = {1: ("write", 0), 18: ("pwrite64", 0), 20: ("writev", 0),
       40: ("sendfile", 0), 77: ("ftruncate", 0), 91: ("fchmod", 0),
       93: ("fchown", 0), 190: ("fsetxattr", 0), 199: ("fremovexattr", 0),
       275: ("splice", 2), 285: ("fallocate", 0), 296: ("pwritev", 0),
       326: ("copy_file_range", 2), 328: ("pwritev2", 0)}
# Asynchroniczne zapisy i uchwyty nie mają ścieżki, którą ten strażnik umie ocenić.
# ENOSYS pozwala bibliotekom użyć zwykłych, kontrolowanych odpowiedników.
UNSUPPORTED = {206, 209, 304, 425, 426, 427}
MUTATING_OPEN = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _libc():
    return C.CDLL(None, use_errno=True)


def _checked(value):
    if value < 0:
        err = C.get_errno()
        raise OSError(err, os.strerror(err))
    return value


def _install():
    libc = _libc()
    _checked(libc.prctl(38, 1, 0, 0, 0))  # PR_SET_NO_NEW_PRIVS
    # Inne ABI nie może ominąć numerów x86-64 (także x32).
    instructions = [Filter(0x20, 0, 0, 4), Filter(0x15, 1, 0, 0xc000003e),
                    Filter(0x06, 0, 0, 0x80000000), Filter(0x20, 0, 0, 0),
                    Filter(0x35, 0, 1, 0x40000000), Filter(0x06, 0, 0, 0x80000000),
                    Filter(0x15, 0, 3, 157), Filter(0x20, 0, 0, 16),
                    Filter(0x15, 0, 1, PROBE), Filter(0x06, 0, 0, NOTIFY),
                    Filter(0x20, 0, 0, 0)]
    for nr in sorted(set(PATHS) | set(FDS) | UNSUPPORTED | {9}):
        instructions.extend([Filter(0x15, 0, 1, nr), Filter(0x06, 0, 0, NOTIFY)])
    instructions.append(Filter(0x06, 0, 0, ALLOW))
    filters = (Filter * len(instructions))(*instructions)
    program = Program(len(filters), filters)
    return _checked(libc.syscall(317, 1, 8, C.byref(program)))


def _memory(pid, address, size):
    with open(f"/proc/{pid}/mem", "rb", buffering=0) as stream:
        return os.pread(stream.fileno(), size, address)


def _string(pid, address):
    data = _memory(pid, address, 4096)
    if b"\0" not in data:
        raise ValueError("ścieżka bez terminatora")
    return os.fsdecode(data.split(b"\0", 1)[0])


def _fd_path(pid, fd):
    return os.readlink(f"/proc/{pid}/fd/{C.c_int(fd).value}")


def _path(pid, address, dirfd=None):
    value = _string(pid, address)
    if not value:
        return _fd_path(pid, dirfd) if dirfd is not None else ""
    if not os.path.isabs(value):
        base = (os.readlink(f"/proc/{pid}/cwd") if dirfd is None or C.c_int(dirfd).value == -100
                else _fd_path(pid, dirfd))
        value = os.path.join(base, value)
    # /proc/self musi oznaczać zgłaszający proces, nie nadzorcę.
    value = value.replace("/proc/self/", f"/proc/{pid}/").replace("/proc/thread-self/", f"/proc/{pid}/")
    return value


def _targets(notification):
    pid, nr, args = notification.pid, notification.data.nr, notification.data.args
    if nr in UNSUPPORTED:
        return "unsupported", []
    if nr == 9:  # mmap: tylko współdzielone mapowanie z prawem zapisu
        if not (args[2] & 2 and args[3] & 1) or args[3] & 0x20:
            return "mmap", []
        return "mmap", [_fd_path(pid, args[4])]
    if nr in FDS:
        name, index = FDS[nr]
        return name, [_fd_path(pid, args[index])]
    name, index, directory = PATHS[nr]
    if nr == 280 and not args[1]:  # futimens: utimensat(fd, NULL, times, 0)
        return name, [_fd_path(pid, args[0])]
    if nr in (2, 257, 437):
        if nr == 437:
            if args[3] < C.sizeof(OpenHow):
                raise ValueError("openat2: niepełne open_how")
            how = OpenHow.from_buffer_copy(_memory(pid, args[2], C.sizeof(OpenHow)))
            flags = how.flags
            if flags & MUTATING_OPEN and how.resolve & 0x10:  # RESOLVE_IN_ROOT
                # Jądro zakotwicza także /, .. i absolutne dowiązania w dirfd.
                # Zwykłe realpath nie odwzoruje tego trybu. Odmowa trafia do
                # trwałego rejestru naruszeń, również gdy cel był poza ochroną.
                raise PermissionError(errno.EPERM, "openat2: zapis z RESOLVE_IN_ROOT nie jest obsługiwany")
        else:
            flags = args[1] if nr == 2 else args[2]
        if not flags & MUTATING_OPEN:
            return name, []
    result = [_path(pid, args[index], args[directory] if directory is not None else None)]
    if nr in (82, 86):
        result.append(_path(pid, args[1]))
    elif nr in (264, 265, 316):
        result.append(_path(pid, args[3], args[2]))
    return name, result


def protected_roots(roots):
    paths = [roots["default_tide_state_home"], os.path.join(roots["xdg_state_home"], "tide")]
    if any(not os.path.isabs(path) for path in paths):
        raise ValueError("korzenie strażnika muszą być bezwzględne")
    return sorted({path for raw in paths for path in (os.path.abspath(raw), os.path.realpath(raw))})


def _protected(path, roots, ancestors=False, follow_leaf=True):
    if not path.startswith("/"):
        return False  # pipe:[...], socket:[...], anon_inode:...
    resolved = (os.path.realpath(path) if follow_leaf else
                os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path)))
    for candidate in (os.path.abspath(path), resolved):
        for root in roots:
            if candidate == root or candidate.startswith(root + os.sep):
                return True
            if ancestors and root.startswith(candidate.rstrip(os.sep) + os.sep):
                return True
    return False


def _diagnostic(data):
    os.write(2, ("TIDE TEST GUARD: " + json.dumps(data, ensure_ascii=False) + "\n").encode())


def _kill_adopted():
    """Wyłącznie własne dzieci nadzorcy, po zakończeniu głównego procesu testu.

    Po zabiciu rodzica jego dalsi potomkowie przechodzą do subreapera; następna
    iteracja odbiera ich statusy i kończy pozostałych, także po setsid.
    """
    with open(f"/proc/self/task/{os.getpid()}/children", encoding="ascii") as stream:
        children = [int(value) for value in stream.read().split()]
    for child in children:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _valid(libc, listener, request):
    identifier = C.c_ulonglong(request.id)
    while libc.ioctl(listener, 0x40082102, C.byref(identifier)) < 0:
        if C.get_errno() == errno.EINTR:
            continue
        if C.get_errno() == errno.ENOENT:
            return False
        _checked(-1)
    return True


def _supervise(pid, listener, roots):
    libc = _libc()
    poll = select.poll()
    poll.register(listener, select.POLLIN | select.POLLHUP)
    violated = False
    status = None
    finished_at = None
    # Rodzic jest wyłącznie nadzorcą, więc nie przejmuje zwykłego kończenia testu.
    for sig in (signal.SIGTERM, signal.SIGINT):
        def forward(number, frame):
            try:
                os.kill(pid, number)
            except ProcessLookupError:
                pass
        signal.signal(sig, forward)
    while True:
        # Jako subreaper odbieramy także osieroconych potomków. Pozostawione
        # zombie nie mogą trzymać filtra ani blokować końca scenariusza.
        while True:
            try:
                done, value = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if not done:
                break
            if done == pid:
                status = value
                finished_at = time.monotonic()
        if finished_at is not None and time.monotonic() - finished_at >= 1:
            _kill_adopted()
        events = poll.poll(100)
        if not events:
            continue
        if events[0][1] & select.POLLHUP:
            break  # brak procesów korzystających z filtra, także dalszych potomków
        request = Notification()
        if libc.ioctl(listener, 0xc0502100, C.byref(request)) < 0:
            if C.get_errno() in (errno.ENOENT, errno.EINTR):
                continue
            _checked(-1)
        if not _valid(libc, listener, request):
            continue
        error = 0
        value = 0
        try:
            if request.data.nr == 157:
                operation, targets = "probe", []
                value = os.getpid()
            else:
                operation, targets = _targets(request)
            if operation == "unsupported":
                error = errno.ENOSYS
            else:
                ancestors = operation.startswith(("rename", "rmdir", "unlink"))
                denied = [target for target in targets
                          if _protected(target, roots, ancestors, follow_leaf=not ancestors)]
                if denied:
                    violated = True
                    error = errno.EPERM
                    _diagnostic({"scenario": pid, "pid": request.pid,
                                 "operation": operation, "targets": denied})
        except (OSError, ValueError, OverflowError) as exc:
            if not _valid(libc, listener, request):
                continue  # proces zakończył się w trakcie odczytu /proc
            # Nieznany cel nie jest zgodą. EBADF/EFAULT to zwykły błąd argumentu;
            # utrata dostępu do procesu oznacza awarię ochrony całego scenariusza.
            error = exc.errno if isinstance(exc, OSError) else errno.EFAULT
            if error not in (errno.ENOENT, errno.ESRCH, errno.EBADF, errno.EFAULT, errno.EIO):
                violated = True
                _diagnostic({"scenario": pid, "pid": request.pid, "error": str(exc)})
        response = Response(request.id, value, -error, 0 if error or value else 1)
        while libc.ioctl(listener, 0xc0182101, C.byref(response)) < 0:
            if C.get_errno() == errno.EINTR:
                continue
            if C.get_errno() != errno.ENOENT:
                _checked(-1)
            break
    # HUP może wyprzedzić gotowość ostatniego statusu po wcześniejszym WNOHANG.
    # Sam koniec używania filtra nie jest potwierdzeniem odebrania zombie.
    while True:
        try:
            done, value = os.waitpid(-1, 0)
        except ChildProcessError:
            break
        if done == pid:
            status = value
    if status is None:
        raise RuntimeError("nie odebrano statusu głównego procesu testu")
    code = os.waitstatus_to_exitcode(status)
    return FAILURE if violated else (code if code >= 0 else 128 - code)


def _inherited():
    """Dopiero odpowiedź nadzorcy przez filtr jądra potwierdza ochronę, nie marker env."""
    broker = _libc().prctl(PROBE, 0, 0, 0, 0)
    if broker > 0:
        os.environ[ACTIVE] = str(broker)
        return True
    return False


def initialize(roots):
    """Przed importem produktu dzieli proces na nadzorcę i chroniony test.

    Filtr dziedziczy jądro przez fork/exec, również przy wyczyszczeniu środowiska.
    Rodzic zachowuje naruszenia do końca drzewa, poza zasięgiem wyjątków produktu.
    """
    try:
        if sys.platform != "linux" or platform.machine() != "x86_64":
            raise RuntimeError("strażnik wymaga Linux x86-64 i seccomp user notification")
        if _inherited():
            return
        if threading.active_count() != 1:
            raise RuntimeError("strażnik musi wystartować przed wątkami produktu")
        roots = protected_roots(roots)
        # Yama wymaga relacji przodka także po double-fork, setsid i zakończeniu
        # usługi, która uruchomiła krok. Nie zmieniamy globalnych zasad ptrace.
        _checked(_libc().prctl(36, 1, 0, 0, 0))  # PR_SET_CHILD_SUBREAPER
        parent, child = socket.socketpair()
        sys.stdout.flush()
        sys.stderr.flush()
        broker = os.getpid()
        pid = os.fork()
        if pid == 0:
            parent.close()
            listener = _install()
            try:
                child.sendmsg([b"guard"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [listener]))])
            finally:
                # Przy błędzie przekazania nie zostawiamy filtra czekającego
                # na nadzorcę, który nigdy nie dostał jego deskryptora.
                os.close(listener)
            child.close()
            os.environ[ACTIVE] = str(broker)
            return
        child.close()
        parent.settimeout(10)
        _, ancillary, _, _ = parent.recvmsg(32, socket.CMSG_SPACE(array.array("i").itemsize))
        parent.close()
        if not ancillary:
            os.waitpid(pid, 0)
            raise RuntimeError("proces testu nie przygotował filtra seccomp")
        descriptors = array.array("i")
        descriptors.frombytes(ancillary[0][2])
        listener = descriptors[0]
        try:
            code = _supervise(pid, listener, roots)
        finally:
            os.close(listener)
        os._exit(code)
    except BaseException as exc:
        try:
            _diagnostic({"setup_error": str(exc)})
        finally:
            # Bez fallbacku, również gdy zawiedzie zapis diagnostyki.
            os._exit(FAILURE)
