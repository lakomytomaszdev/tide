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
  Jedyny wyjątek (PT-317, K-88): `.claude/.cc-writes` w katalogu głównym worktree — plik albo katalog (z całą
  zawartością), który zapisuje sam Claude Code, nie model. Drzewo liczy się tak, jakby tej ścieżki nie było:
  sam katalog `.claude` nie ma rekordu (jego rozmiar, `mtime` i `ctime` zmieniają powstanie i usunięcie `.cc-writes`,
  a w świeżym worktree tworzy go dopiero pierwszy zapis `.cc-writes` — review PT-317 MAJOR-1), więc klucz `.claude`
  w drzewie powstaje wyłącznie z rekordów dzieci innych niż `.cc-writes`: `.claude` zawierający tylko `.cc-writes`
  liczy się jak jego brak. Każdy inny wpis w `.claude` ma pełny rekord — nowy, usunięty, przemianowany i zmieniony
  plik obok `.cc-writes` nadal jest zmianą. Niewidoczne są tylko pusty katalog `.claude` i zmiana jego praw
  (ignorowany przez gita, bez treści). Dowiązanie o tej nazwie nie jest wyjątkiem (rekord z celem), a `.claude` będący dowiązaniem —
  jak każde dowiązanie — nie jest przeglądany.
- `head` — treść `<gitDir>/HEAD` worktree (gałąź albo odłączony commit),
- `refs` — sha `refs/heads/<gałąź>` gałęzi worktree kroku i gałęzi karty, czytane w repozytorium projektu.

Git nie jest uruchamiany w worktree (agent mógłby tam podłożyć haki albo `core.fsmonitor`): drzewo czyta Python bez
podążania za dowiązaniami, `HEAD` — plik w katalogu gita ustalonym przez usługę (`agentcmd.resolve_git_dir`), refy —
`FlowGit.sha` w repozytorium.

Zdarzenie dziennika `caretaker.guard` — `{step, repo, worktrees, branches, refs, cause, wrote}`:
stan bazowy kroku (`cause: start` przed startem programu albo nazwa działania Tide, po którym stan wziął się na
nowo), `wrote` — powody zmian przypisanych krokowi (pusta lista — bez zmian). Opcjonalne `skipped` (PT-372, niżej)
— zmiany drzewa pominięte przez polecenie Tide w worktree; stare dzienniki bez tego pola czytają się jak dotąd.

Działanie Tide w trakcie kroku opiekuna (scalenie, start innego kroku z przywracaniem zależności…) może zmienić
stan pod strażą. Żeby nie przypisać krokowi zapisu Tide, a zarazem nie przykryć zapisu kroku, Tide na czas działania
zamraża jednostkę kroku (`systemctl --user freeze`, cgroup freezer — cały proces z potomkami stoi): zmiany sprzed
działania należą do kroku, zmiany w trakcie — do Tide. Gdy zamrożenie się nie uda, a proces kroku żyje, zmiany
z okna działania też idą na konto kroku (strażnik odrzuca zamiast przepuszczać).

Wyjątek: polecenie Tide w worktree pod strażą (K-39, PT-372). W worktree może w tym czasie biec uruchomienie poza
piaskownicą na zgodę kroku ticketu (`make test-changed`, skrypt pomiaru) albo próba testów po scaleniu
(pseudokrok `merge-check-*` w worktree gałęzi karty, K-88). Jego `build/`, `__pycache__` i wyniki testów to zapisy
Tide, nie opiekuna. Opiekun nie ma kontraktu `outside`, więc sam takiego polecenia nie uruchomi. Zasada:
- uruchomienie (`runs`) to para `outside.started` → `outside.finished` tego samego kroku z dziennika karty; jego
  worktree pochodzi z pola `worktree` startu, dla próby testów po scaleniu — z pseudokroku (`merge.check-step`).
  Start bez `worktree` niczego nie wyłącza;
- uruchomienie liczy się (`active`), gdy jego start jest w dzienniku przed sprawdzeniem (dziennik czytany przed
  migawką), a koniec jest po stanie bazowym (`seq` zdarzenia `caretaker.guard`) albo go nie ma (polecenie trwa).
  Obejmuje to też polecenie, które ruszyło i skończyło się w trakcie kroku opiekuna;
- worktree pasuje po ścieżkach znormalizowanych (`os.path.normpath(os.path.abspath(…))`, bez podążania za
  dowiązaniami): końcowy `/` i `./` nie przeszkadzają, dowiązanie do worktree to inna ścieżka;
- wyłączone są wyłącznie zmiany drzewa (`tree`) tego worktree (`excuse`): nie dają `wrote`, Tide zapisuje
  `caretaker.guard` z `cause: outside` i `skipped` (`run` — nazwa uruchomienia, `worktree`, `entries` — zmienione
  wpisy najwyższego poziomu), a drzewo tego worktree bierze na nowo. Zniknięcie albo pojawienie się worktree nie jest
  wyłączone. `HEAD` worktree, refy gałęzi i drzewa innych worktree pod strażą pozostają pod pełną strażą;
- ta sama reguła działa w sprawdzeniu przed działaniem Tide, w przeliczeniu stanu po nim (krok niezamrożony)
  i w werdykcie, żeby zmiana pominięta w jednym miejscu nie wróciła jako `wrote` w drugim.
Granica: zapis opiekuna w drzewie worktree z trwającym poleceniem jest w tym oknie nieodróżnialny od zapisu polecenia
i przechodzi. Uruchomienie bez `outside.finished` (np. utracone po awarii) wyłącza drzewo do końca kroku opiekuna.
"""
from __future__ import annotations

import hashlib
import os
import stat

from .caretaker import WROTE  # noqa: F401 — kod nieudanego działania (jedna definicja w rejestrze opiekuna)

EVENT = "caretaker.guard"
FIELDS = ("repo", "worktrees", "branches", "refs")
# Ścieżka zapisywana przez Claude Code, nie przez model (PT-317) — jedyny wyjątek strażnika, względem worktree.
CLAUDE_DIR, CC_WRITES = ".claude", ".claude/.cc-writes"
MISSING = {"": "brak"}                                  # drzewo worktree, którego nie ma
# Wyjątek dla polecenia Tide w worktree (PT-372): `cause` zdarzenia i zdarzenia dziennika, z których się bierze.
OUTSIDE = "outside"
OUTSIDE_STARTED, OUTSIDE_FINISHED = "outside.started", "outside.finished"
MERGE_CHECK_STEP = "merge.check-step"                   # mergecheck.STEP — pseudokrok próby testów po scaleniu


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
            if name == CC_WRITES and (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                continue                                # zapis Claude Code, nie modelu (PT-317)
            if entry.is_dir(follow_symlinks=False):
                stack.append(name)
            if name == CLAUDE_DIR and stat.S_ISDIR(info.st_mode):
                continue                                # klucz `.claude` tylko z dzieci innych niż `.cc-writes`
            _add(digests, name, f"{name}\0{info.st_mode:o}\0{info.st_size}\0{info.st_mtime_ns}\0"
                                f"{info.st_ctime_ns}\0{info.st_ino}\0{link}")
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


# --- wyjątek: polecenie Tide w worktree pod strażą (PT-372) -------------------------------------

def where(path) -> str:
    """Ścieżka do porównań worktree: bezwzględna i znormalizowana, bez rozwijania dowiązań; pusta — brak."""
    return os.path.normpath(os.path.abspath(path)) if isinstance(path, str) and path else ""


def runs(events) -> list[dict]:
    """Uruchomienia poza piaskownicą z dziennika karty: `{step, worktree, started, finished}` (`seq` startu i końca,
    `finished` None — bez końca). Worktree próby testów po scaleniu z jej pseudokroku."""
    pseudo = {e.get("step"): e.get("worktree") for e in events if e.get("type") == MERGE_CHECK_STEP}
    found, running = [], {}
    for e in events:
        kind, name = e.get("type"), e.get("step") or ""
        if kind == OUTSIDE_STARTED:
            run = {"step": name, "worktree": where(pseudo[name] if name in pseudo else e.get("worktree")),
                   "started": e.get("seq"), "finished": None}
            found.append(run)
            running[name] = run
        elif kind == OUTSIDE_FINISHED and name in running:
            running.pop(name)["finished"] = e.get("seq")
    return found


def active(base: dict, found: list[dict], step: str = "") -> dict:
    """Worktree (`where`) → nazwy uruchomień działających od stanu bazowego `base` (zdarzenie `caretaker.guard`).
    Bez `seq` stanu bazowego — nic (strażnik woli odrzucić). Krok pod strażą sam nie jest uruchomieniem."""
    since = base.get("seq")
    if type(since) is not int:
        return {}
    busy: dict = {}
    for run in found:
        end = run.get("finished")
        if not run.get("worktree") or run.get("step") == step or type(end) is int and end <= since:
            continue
        busy.setdefault(run["worktree"], [])
        if run["step"] not in busy[run["worktree"]]:
            busy[run["worktree"]].append(run["step"])
    return busy


def excuse(base: dict, fresh: dict, busy: dict) -> tuple[dict, list[dict]]:
    """Stan bazowy (pola `FIELDS`) z drzewem worktree z działającym uruchomieniem wziętym z `fresh` i lista `skipped`.
    `HEAD`, refy i pozostałe worktree bez zmian — `changes(rebased, fresh)` to zmiany nadal przypisane krokowi."""
    rebased = {k: base.get(k) for k in FIELDS}
    olds, news = base.get("worktrees") or [], fresh.get("worktrees") or []
    if not busy or len(olds) != len(news):
        return rebased, []
    trees, skipped = [], []
    for was, now in zip(olds, news):
        names = busy.get(where(was.get("worktree")))
        old, new = was.get("tree") or {}, now.get("tree") or {}
        if names and old != new and MISSING not in (old, new) and \
                where(was.get("worktree")) == where(now.get("worktree")):
            skipped.append({"run": ", ".join(names), "worktree": was.get("worktree") or "",
                            "entries": sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))})
            was = {**was, "tree": new}
        trees.append(was)
    rebased["worktrees"] = trees
    return rebased, skipped
