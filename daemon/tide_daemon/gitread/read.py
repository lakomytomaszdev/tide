"""Publiczne odczyty gita modułu `gitread`. Każda funkcja przyjmuje runner (`GitReader`) i woła gita
wyłącznie przez `reader.run`; parsowanie diffu jednolitego jest osobno (#28).

Refy i ścieżki to wartości z zewnątrz (dziennik karty, D-Bus): runner odrzuca ref zaczynający się
od `-` (`bad-request`) i podaje je po `--end-of-options`/`--`.
"""

__all__ = ["rev_parse", "merge_base", "rev_list", "log", "diff", "status"]

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


def status(reader, repo: str) -> str:
    """Surowe `status --porcelain=v1 -z` z plikami nieśledzonymi (parsowanie: `worktrees.git.parse_status`)."""
    return reader.run(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").out
