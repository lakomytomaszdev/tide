"""Zajętość dysku katalogu, jak `du -s`: bloki na dysku, bez podążania za dowiązaniami.

Nie wychodzimy poza system plików katalogu, twarde dowiązania liczymy raz, a podane katalogi
(np. worktrees zagnieżdżone w głównym katalogu repozytorium) pomijamy, żeby rozmiar nie był
liczony podwójnie.
"""
import os


def disk_usage(path: str, exclude: tuple = ()) -> int:
    """Rozmiar w bajtach albo -1, gdy katalogu nie da się odczytać."""
    try:
        root = os.lstat(path)
    except OSError:
        return -1
    device = root.st_dev
    skip = {os.path.normpath(p) for p in exclude}
    seen: set[tuple[int, int]] = set()
    total = root.st_blocks * 512
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            entries = os.scandir(current)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if st.st_dev != device:
                    continue
                if st.st_nlink > 1 and not entry.is_dir(follow_symlinks=False):
                    key = (st.st_dev, st.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                if entry.is_dir(follow_symlinks=False):
                    if entry.path in skip:
                        continue
                    stack.append(entry.path)
                total += st.st_blocks * 512
    return total
