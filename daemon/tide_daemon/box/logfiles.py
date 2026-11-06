"""Odczyt danych pudełka bez przechodzenia przez dowiązania, również po podmianie rodzica.

Ścieżka pozostaje leksykalna aż do otwarcia. Każdy składnik otwieramy względem deskryptora
poprzedniego katalogu; żaden wynik wcześniejszego stat/scandir nie daje prawa do odczytu.
Zaufane katalogi hosta zachowują dotychczasową obsługę dowiązań (np. snap/current).
"""
import builtins
import errno
import os
import stat as statmod


def is_box(path):
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    root = os.path.abspath(os.path.join(data, "tide", "box"))
    path = os.path.abspath(path)
    return path == root or path.startswith(root + os.sep)


def _fd(path, directory=False):
    parts = os.path.abspath(path).split(os.sep)[1:]
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for i, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            if i < len(parts) - 1 or directory:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        if not directory and not statmod.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "dziennik nie jest zwykłym plikiem", path)
        result, fd = fd, None
        return result
    finally:
        if fd is not None:
            os.close(fd)


def open_log(path, mode="rb", **kwargs):
    if not is_box(path):
        return builtins.open(path, mode, **kwargs)
    fd = _fd(path)
    try:
        return os.fdopen(fd, mode, **kwargs)
    except BaseException:
        os.close(fd)
        raise


def stat_log(path):
    if not is_box(path):
        return os.stat(path)
    with open_log(path) as fh:
        return os.fstat(fh.fileno())


def names(path):
    """Nazwy bez odczytu celu symlinka; podmiana katalogu jest sprawdzana ponownie przy otwarciu."""
    if not is_box(path):
        return os.listdir(path)
    fd = _fd(path, directory=True)
    try:
        return os.listdir(fd)
    finally:
        os.close(fd)


def files(root, depth=32):
    """Zwykłe pliki z drzewa pudełka; żadnego os.walk po nieufnych ścieżkach."""
    try:
        fd = _fd(root, directory=True)
    except OSError:
        return
    try:
        for name in os.listdir(fd):
            path = os.path.join(root, name)
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if statmod.S_ISREG(st.st_mode):
                    yield path, st
                elif statmod.S_ISDIR(st.st_mode) and depth:
                    yield from files(path, depth - 1)
            except OSError:
                continue
    finally:
        os.close(fd)
