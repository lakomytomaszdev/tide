"""Strażnik kroku opiekuna (K-37, PT-158): stan worktree i refów sprzed kroku porównany ze stanem po kroku.

Krok opiekuna ma zapis wyłącznie do własnego `<krok>/out` (piaskownica, `agentcmd.Step.write_out_only`). Strażnik
jest drugą, niezależną od piaskownicy warstwą: Tide zapisuje stan przed startem programu i porównuje go po kroku,
zanim przeczyta plik wyniku (`caretaker.parse_result`). Zmiana — wynik odrzucony jako `caretaker-wrote`.

Stan (`snapshot`) to:
- `worktrees` — lista `{worktree, gitDir, tree, head}`: worktree kroku i — gdy inny (problem ticketu) — worktree
  karty (PT-158, review M2: kryterium obejmuje drzewo i `HEAD` worktree karty, nie tylko jej ref),
- `tree` — skrót drzewa worktree w podziale na wpisy najwyższego poziomu: każdy plik, katalog i dowiązanie (pliki
  śledzone, nieśledzone i ignorowane), bez katalogu `.git` (Tide sam w nim pisze, np. `index` przy `git status`).
  Rekord wpisu: ścieżka, typ i prawa, rozmiar, `mtime`, `ctime`, i-węzeł, cel dowiązania. `ctime` zmienia każdy
  zapis, zmiana nazwy, praw czy dowiązania twardego i nie da się go cofnąć bez roota (`touch` ustawia tylko
  `mtime`/`atime`), więc wykrywa także zapis tej samej treści i podmianę z przywróconym `mtime` — mocniej niż suma
  treści, a bez czytania plików (worktree z `node_modules` liczy się w ułamku sekundy). Dowiązania nie są śledzone.
- `head` — treść `<gitDir>/HEAD` worktree (gałąź albo odłączony commit),
- `refs` — sha `refs/heads/<gałąź>` gałęzi worktree kroku i gałęzi karty, czytane w repozytorium projektu.

Git nie jest uruchamiany w worktree (agent mógłby tam podłożyć haki albo `core.fsmonitor`): drzewo czyta Python bez
podążania za dowiązaniami, `HEAD` — plik w katalogu gita ustalonym przez usługę (`agentcmd.resolve_git_dir`), refy —
`FlowGit.sha` w repozytorium.

Zdarzenie dziennika `caretaker.guard` — `{step, repo, worktrees, branches, refs, cause, wrote}`:
stan bazowy kroku (`cause: start` przed startem programu albo nazwa działania Tide, po którym stan wziął się na
nowo), `wrote` — powody zmian przypisanych krokowi (pusta lista — bez zmian).

Działanie Tide w trakcie kroku opiekuna (scalenie, start innego kroku z przywracaniem zależności…) może zmienić
stan pod strażą. Żeby nie przypisać krokowi zapisu Tide, a zarazem nie przykryć zapisu kroku, Tide na czas działania
zamraża jednostkę kroku (`systemctl --user freeze`, cgroup freezer — cały proces z potomkami stoi): zmiany sprzed
działania należą do kroku, zmiany w trakcie — do Tide. Gdy zamrożenie się nie uda, a proces kroku żyje, zmiany
z okna działania też idą na konto kroku (strażnik odrzuca zamiast przepuszczać).
"""
from __future__ import annotations

import hashlib
import os

from .caretaker import WROTE  # noqa: F401 — kod nieudanego działania (jedna definicja w rejestrze opiekuna)

EVENT = "caretaker.guard"
FIELDS = ("repo", "worktrees", "branches", "refs")


def tree(worktree: str) -> dict:
    """`{wpis najwyższego poziomu: sha256 rekordów jego poddrzewa}`; brak worktree — `{"": "brak"}`."""
    if not os.path.isdir(worktree) or os.path.islink(worktree):
        return {"": "brak"}
    digests: dict = {}
    stack = [""]
    while stack:
        rel = stack.pop()
        path = os.path.join(worktree, rel) if rel else worktree
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name)
        except OSError as exc:
            _add(digests, rel, f"{rel}\0!{exc.errno}")
            continue
        for entry in entries:
            name = f"{rel}/{entry.name}" if rel else entry.name
            if name == ".git" and entry.is_dir(follow_symlinks=False):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                link = os.readlink(entry.path) if entry.is_symlink() else ""
            except OSError as exc:
                _add(digests, name, f"{name}\0!{exc.errno}")
                continue
            _add(digests, name, f"{name}\0{info.st_mode:o}\0{info.st_size}\0{info.st_mtime_ns}\0"
                                f"{info.st_ctime_ns}\0{info.st_ino}\0{link}")
            if entry.is_dir(follow_symlinks=False):
                stack.append(name)
    return {top: h.hexdigest() for top, h in sorted(digests.items())}


def _add(digests: dict, name: str, record: str) -> None:
    top = name.split("/", 1)[0]
    digests.setdefault(top, hashlib.sha256()).update(record.encode("utf-8", "surrogateescape") + b"\n")


def head(git_dir: str) -> str:
    try:
        with open(os.path.join(git_dir, "HEAD"), encoding="utf-8", errors="replace") as fh:
            return fh.read(4096).strip()
    except OSError as exc:
        return f"!{exc.errno}"


def snapshot(git, *, repo: str, worktrees, branches) -> dict:
    """Stan pod strażą: pola `FIELDS` gotowe do zapisu w `caretaker.guard`. `worktrees` — pary (worktree, gitDir),
    pierwsza to worktree kroku; powtórzenia i puste ścieżki pomija."""
    names = [b for b in dict.fromkeys(branches) if b]
    seen, trees = set(), []
    for path, git_dir in worktrees:
        if not path or os.path.normpath(path) in seen:
            continue
        seen.add(os.path.normpath(path))
        trees.append({"worktree": path, "gitDir": git_dir, "tree": tree(path), "head": head(git_dir)})
    return {"repo": repo, "worktrees": trees, "branches": names,
            "refs": {b: git.sha(repo, f"refs/heads/{b}") for b in names}}


def again(git, base: dict) -> dict:
    """Ten sam zakres co stan bazowy, policzony teraz."""
    return snapshot(git, repo=base.get("repo") or "", branches=base.get("branches") or [],
                    worktrees=[(w.get("worktree") or "", w.get("gitDir") or "") for w in base.get("worktrees") or []])


def same(before: dict, after: dict) -> bool:
    return all(before.get(k) == after.get(k) for k in ("worktrees", "refs"))


def changes(before: dict, after: dict) -> list[str]:
    """Powody po ludzku (pusta lista — stan bez zmian)."""
    found = []
    olds, news = before.get("worktrees") or [], after.get("worktrees") or []
    if len(olds) != len(news):
        found.append("inny zestaw worktree pod strażą")
    for was, now in zip(olds, news):
        found += _tree_changes(was, now)
    for branch, sha in (before.get("refs") or {}).items():
        if (after.get("refs") or {}).get(branch) != sha:
            found.append(f"przesunięta gałąź {branch}")
    return found


def _tree_changes(before: dict, after: dict) -> list[str]:
    found = []
    where = before.get("worktree") or ""
    old, new = before.get("tree") or {}, after.get("tree") or {}
    if old != new:
        if new == {"": "brak"}:
            found.append(f"worktree {where} zniknął")
        else:
            touched = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
            shown = ", ".join(touched[:5]) + (f" i {len(touched) - 5} innych" if len(touched) > 5 else "")
            found.append(f"zmienione pliki w worktree {where}: {shown}")
    if before.get("head") != after.get("head"):
        found.append(f"przesunięty HEAD worktree {where}")
    return found


def reason(found: list[str]) -> str:
    return "krok opiekuna pisał poza swoim katalogiem wyjściowym: " + "; ".join(dict.fromkeys(found))
