"""Wspólna blokada zapisu Tablicy: `flock` na `<tablica>/.git/tide-board.lock`.

Biorą ją `BoardStore.transaction` (odczyt–plan–zapis–commit Tablicy) i zapis profilu pudełka K-38
(`box/profile.save_project`), bo oba przepisują `projects/<slug>/box.yaml` (PT-321): kontrakt testów
i ustawienia K-38 nie mogą się nawzajem cofnąć przy przeplocie. Blokada jest wielowejściowa w obrębie
jednego wątku — zapis profilu wywołany wewnątrz transakcji Tablicy (albo odwrotnie) nie zakleszcza się
na drugim deskryptorze tego samego pliku. Inne wątki i procesy czekają na `flock`.
"""
import contextlib
import fcntl
import os
import threading

LOCK_NAME = "tide-board.lock"

_local = threading.local()


@contextlib.contextmanager
def exclusive(git_dir: str):
    """Wyłączna blokada Tablicy o katalogu gita `git_dir` na czas bloku `with`."""
    held = _local.__dict__.setdefault("held", {})
    key = os.path.realpath(git_dir)
    if held.get(key):
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    fd = os.open(os.path.join(git_dir, LOCK_NAME), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        held[key] = 1
        yield
    finally:
        held.pop(key, None)
        os.close(fd)
