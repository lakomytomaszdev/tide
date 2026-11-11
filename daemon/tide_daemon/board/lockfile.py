"""Wspólna blokada zapisu Tablicy: `flock` na katalogu Tablicy, a gdy jest już git — także na `.git/tide-board.lock`.

Biorą ją `BoardStore.transaction` (odczyt–plan–zapis–commit Tablicy) i zapis profilu pudełka K-38
(`box/profile.save_project`), bo oba przepisują `projects/<slug>/box.yaml` (PT-321): kontrakt testów
i ustawienia K-38 nie mogą się nawzajem cofnąć przy przeplocie.

Główną blokadą jest `flock` na deskryptorze samego katalogu Tablicy: istnieje, zanim `BoardStore.ensure()`
założy repozytorium gita (Tablica skopiowana bez `.git`), i nie tworzy w Tablicy żadnego pliku. Drugą, zgodną
z K-55 (`issue_acceptance`, `docs/issues-api.md`), jest `flock` na `.git/tide-board.lock`, gdy repozytorium już
istnieje — starsze procesy, które znają tylko ją, nadal się z nami szeregują. Kolejność jest stała (katalog,
potem `.git`), więc obie strony nie zakleszczają się nawzajem.

Blokada jest wielowejściowa w obrębie jednego wątku — zapis profilu wywołany wewnątrz transakcji Tablicy (albo
odwrotnie) nie zakleszcza się na drugim deskryptorze tego samego katalogu. Inne wątki i procesy czekają na `flock`.
"""
import contextlib
import fcntl
import os
import threading

LOCK_NAME = "tide-board.lock"

_local = threading.local()


def _flock(path: str, flags: int, blocking: bool = True) -> int:
    fd = os.open(path, flags | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextlib.contextmanager
def exclusive(board_root: str, blocking: bool = True):
    """Wyłączna blokada Tablicy o katalogu `board_root` (musi istnieć) na czas bloku `with`.
    `blocking=False` (K-98 #2, publikacja migawki w wątku głównym): blokada trzymana przez inny proces daje od razu
    `BlockingIOError` zamiast czekania."""
    held = _local.__dict__.setdefault("held", {})
    key = os.path.realpath(board_root)
    if held.get(key):
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    fds = [_flock(key, os.O_RDONLY | os.O_DIRECTORY, blocking)]
    try:
        git_dir = os.path.join(key, ".git")
        if os.path.isdir(git_dir):
            fds.append(_flock(os.path.join(git_dir, LOCK_NAME), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, blocking))
        held[key] = 1
        yield
    finally:
        held.pop(key, None)
        for fd in reversed(fds):
            os.close(fd)
