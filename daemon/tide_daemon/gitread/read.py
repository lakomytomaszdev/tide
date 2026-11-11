"""Publiczne odczyty gita modułu `gitread`. Każda funkcja przyjmuje runner (`GitReader`) i woła gita
wyłącznie przez `reader.run`; parsowanie diffu jednolitego jest osobno (#28).

Refy i ścieżki to wartości z zewnątrz (dziennik karty, D-Bus): runner odrzuca ref zaczynający się
od `-` (`bad-request`) i podaje je po `--end-of-options`/`--`.
"""

__all__ = ["rev_parse", "merge_base", "rev_list", "log", "diff", "diff_capped", "diff_files", "blob_sizes",
           "status"]

LOG_FIELDS = ("sha", "parents", "author", "time", "subject")
_LOG_FORMAT = "--format=%H%x1f%P%x1f%an%x1f%ct%x1f%s%x1e"


def rev_parse(reader, repo: str, rev: str) -> str | None:
    """Pełny SHA commita wskazanego przez `rev` albo None, gdy taki commit nie istnieje."""
    result = reader.run(repo, "rev-parse", "--verify", "--quiet", revs=[rev + "^{commit}"], ok_codes=(0, 1))
    return (result.out.strip() or None) if result.code == 0 else None


def merge_base(reader, repo: str, first: str, second: str) -> str | None:
    """Najlepsza baza scalenia dwóch commitów albo None, gdy nie mają wspólnej historii."""
    result = reader.run(repo, "merge-base", revs=[first, second], ok_codes=(0, 1))
    return (result.out.strip() or None) if result.code == 0 else None


def rev_list(reader, repo: str, revs, *, max_count: int | None = None) -> list[str]:
    """SHA commitów osiągalnych z `revs` (np. `["card", "^master"]` albo `["master..card"]`)."""
    options = [f"--max-count={int(max_count)}"] if max_count is not None else []
    return reader.run(repo, "rev-list", *options, revs=list(revs)).out.split()


def log(reader, repo: str, revs, *, paths=(), max_count: int | None = None) -> list[dict]:
    """Commity (`LOG_FIELDS`) od najnowszego; `time` to sekundy epoki z daty commitera."""
    options = [_LOG_FORMAT] + ([f"--max-count={int(max_count)}"] if max_count is not None else [])
    out = reader.run(repo, "log", *options, revs=list(revs), paths=list(paths)).out
    commits = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, parents, author, time, subject = record.split("\x1f", 4)
        commits.append({"sha": sha, "parents": parents.split(), "author": author, "time": int(time),
                        "subject": subject})
    return commits


def diff(reader, repo: str, base: str, head: str | None = None, *, paths=(), context: int = 3) -> str:
    """Diff jednolity `base`→`head` (bez `head`: `base`→checkout) jako tekst do parsera z #28."""
    revs = [base] + ([head] if head is not None else [])
    return reader.run(repo, "diff", "--no-color", "--no-renames", f"--unified={int(context)}",
                      revs=revs, paths=list(paths)).out


def diff_capped(reader, repo: str, base: str, head: str | None = None, *, paths=(), context: int = 3,
                max_bytes: int, max_lines: int) -> tuple[str, bool]:
    """Jak `diff`, ale z limitem czytania (PT-355): `(tekst, ucięto)`. Po przekroczeniu `max_bytes` albo
    `max_lines` git jest przerywany, a tekst to pełne linie z początku diffu — reszta nie trafia do pamięci."""
    revs = [base] + ([head] if head is not None else [])
    result = reader.run(repo, "diff", "--no-color", "--no-renames", f"--unified={int(context)}",
                        revs=revs, paths=list(paths), max_bytes=max_bytes, max_lines=max_lines)
    return result.out, result.truncated


def diff_files(reader, repo: str, base: str, head: str, *, max_bytes: int = 16 << 20) -> tuple[list[dict], bool]:
    """Lista wszystkich plików zmienionych `base`→`head` bez treści: `(pliki, ucięto)`. Pliki mają
    `path`, `status` (A/D/M/T), `oldMode`, `newMode`, `additions`, `deletions` i `binary` — z `--raw` i `--numstat`
    (`-z`, ścieżki bez cytowania). Wynik jest mały (linia na plik); `max_bytes` tylko chroni pamięć."""
    revs = [base, head]
    raw = reader.run(repo, "diff", "--raw", "--no-renames", "--abbrev=40", "-z", revs=revs, max_bytes=max_bytes)
    nums = reader.run(repo, "diff", "--numstat", "--no-renames", "-z", revs=revs, max_bytes=max_bytes)
    counts = {}
    for record in nums.out.split("\0"):
        parts = record.split("\t", 2)
        if len(parts) == 3:
            counts[parts[2]] = parts[:2]
    files = []
    items = raw.out.split("\0")
    for i in range(0, len(items) - 1, 2):
        head_line, path = items[i], items[i + 1]
        fields = head_line.lstrip(":").split()
        if len(fields) < 5:
            continue
        old_mode, new_mode, letter = fields[0], fields[1], fields[4][:1]
        add, dele = counts.get(path, ["0", "0"])
        binary = add == "-" or dele == "-"
        additions, deletions = (0, 0) if binary else (int(add), int(dele))
        status = letter if letter in "ADM" else "M"
        if status == "M" and old_mode != new_mode and not (additions or deletions or binary):
            status = "T"
        files.append({"path": path, "oldPath": None, "status": status, "additions": additions,
                      "deletions": deletions, "binary": binary,
                      "oldMode": None if old_mode == "000000" else old_mode,
                      "newMode": None if new_mode == "000000" else new_mode, "warnings": [], "hunks": []})
    return files, raw.truncated or nums.truncated


def blob_sizes(reader, repo: str, rev: str, paths) -> dict[str, int]:
    """Rozmiary plików (bajty) w drzewie `rev` (`ls-tree -l`); brakujące ścieżki są pominięte."""
    sizes = {}
    paths = list(paths)
    for start in range(0, len(paths), 200):
        out = reader.run(repo, "ls-tree", "-l", "-z", revs=[rev], paths=paths[start:start + 200]).out
        for record in out.split("\0"):
            meta, _, path = record.partition("\t")
            fields = meta.split()
            if len(fields) == 4 and fields[1] == "blob" and fields[3].isdigit():
                sizes[path] = int(fields[3])
    return sizes


def status(reader, repo: str) -> str:
    """Surowe `status --porcelain=v1 -z` z plikami nieśledzonymi (parsowanie: `worktrees.git.parse_status`)."""
    return reader.run(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").out
