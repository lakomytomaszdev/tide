"""„Zaciągnij” w obszarze worktrees: przesunięcie gałęzi worktree wyłącznie fast-forward do upstreamu (PT-353).

Jedyne polecenie zapisujące to `git merge --ff-only --no-autostash <SHA upstreamu>` w danym worktree, gdzie SHA
to `@{upstream}` odczytany pod blokadą tuż przed scaleniem (stan sprawdzony = stan scalony, nawet gdy równoległe
„Pobierz” przesunie `refs/remotes/…` w międzyczasie). Tide nigdy nie scala historii, nie robi rebase, reset, stash,
push ani prune — gdy fast-forward nie jest możliwy, odpowiada odmową z kodem i zdaniem, nie zmieniając HEAD ani
drzewa. Zasady:
- `path` musi być worktree z listy Worktrees (porównanie po ścieżce kanonicznej: dowiązania i `..`); inaczej
  `bad-request` bez uruchomienia gita, a do gita idzie ścieżka z listy, nie ta z żądania,
- dzienniki kart (gałąź karty albo ticketu w przepływie → `card-step`) czytamy PRZED blokadą repozytorium:
  silnik bierze `journal.lock` także wtedy, gdy potem czeka na `lock_for` (wątek główny), więc odczyt dziennika
  pod `lock_for` w wątku roboczym byłby odwróceniem kolejności blokad i groziłby zakleszczeniem,
- ta sama blokada repozytorium co scalanie karty (`cardflow.gitops.lock_for`, import bez zmian w niej), czekanie
  krótkie (LOCK_WAIT) i wyłącznie w wątku roboczym; po nim `locked`. Pod blokadą: świeży odczyt stanu
  (gałąź, operacja gita, upstream, zmiany, ahead/behind, filtry plików), zaraz potem `merge`. Blokada jest
  zwalniana w `finally` — także po wyjątku i po przekroczeniu limitu czasu gita (PULL_TIMEOUT),
- środowisko gita jak przy „Pobierz” (fetch_env: bez pytań, SSH wsadowo), hooki i fsmonitor wyłączone jak
  w każdym wywołaniu (`gitcommand.HOST_OPTIONS`), `GIT_NO_LAZY_FETCH=1` (częściowy klon nie sięga do sieci),
- checkout przy fast-forward potrafi uruchomić sterownik `filter.<nazwa>.smudge/process` (git-lfs → sieć,
  dowolny program z konfiguracji): gdy repozytorium ma taki sterownik, a zmieniane pliki (albo `.gitattributes`)
  go dotyczą, odmowa `git-operation` z powodem — zaciągnięcie zostaje użytkownikowi,
- repozytorium objęte granicą Gita (tryb pudełek, repozytorium wystawione pudełku) → `boundary` bez gita
  zapisującego (jak przy „Pobierz”).
"""
import copy
import os
import time

from ..gitcommand import boundary_active
from .fetch import fetch_env
from .git import Git, parse_status

PULL_TIMEOUT = 60.0   # s na `merge --ff-only` razem z checkoutem plików
LOCK_WAIT = 10.0      # s czekania na blokadę repozytorium (scalanie karty trzyma ją krótko)
MAX_DETAIL = 160

# Akcje dziennika karty, które zakładają gałąź w przepływie (state.ticket_of, basegeneration).
FLOW_BRANCH_ACTIONS = ("card-branch", "ticket-branch", "review-branch", "step-reopen")
# Ślady trwającej operacji gita w prywatnym katalogu worktree (i blokady refów, które trzyma inny git).
OPERATION_MARKERS = (("MERGE_HEAD", "scalanie"), ("rebase-merge", "rebase"), ("rebase-apply", "rebase albo am"),
                     ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"), ("sequencer", "cherry-pick"),
                     ("BISECT_LOG", "bisect"), ("index.lock", "inny git trzyma indeks"),
                     ("HEAD.lock", "inny git zmienia HEAD"))
MERGE_OPTIONS = ("-c", "merge.autoStash=false", "-c", "submodule.recurse=false", "-c", "maintenance.auto=false",
                 "-c", "gc.auto=0", "-c", "core.askPass=")


def commits_word(n: int) -> str:
    """„1 commit”, „3 commity”, „5 commitów”."""
    ones, tens = n % 10, n % 100
    if n == 1:
        return "1 commit"
    return f"{n} commity" if 2 <= ones <= 4 and not 12 <= tens <= 14 else f"{n} commitów"


def _refused(reason: str, message: str, path: str = "", **extra) -> dict:
    return {"ok": False, "reason": reason, "message": message, "path": path, **extra}


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= MAX_DETAIL else line[:MAX_DETAIL - 1] + "…"
    return ""


# --- dzienniki kart ------------------------------------------------------------------------------

def flow_branches(journal) -> dict:
    """Gałęzie kart w przepływie: {(repo kanonicznie albo "", gałąź): karta}. Karta jest w przepływie od startu
    do zakończenia (`card.finished`) albo scalenia do głównej — także po domknięciu, gdy czeka na scalenie
    użytkownika, i z gałęziami ticketów już scalonych do gałęzi karty (zachowawczo). Gałąź z zamiaru bez wyniku
    też się liczy (silnik właśnie ją zakłada). Czyta wyłącznie istniejące odczyty dziennika (`cards`, `state`);
    błąd odczytu przechodzi do wywołującego (→ odmowa). Bez przepływu (journal None) — pusto."""
    if journal is None:
        return {}
    found = {}

    def add(card, repo, branch):
        if isinstance(branch, str) and branch:
            key = (os.path.realpath(repo) if isinstance(repo, str) and repo else "", branch)
            found.setdefault(key, card)

    for card in journal.cards():
        st = journal.state(card)
        if st.started is None or st.card_finished or st.merged:
            continue
        for (action, key), intent in st.intents.items():
            if action in FLOW_BRANCH_ACTIONS:
                add(card, intent.get("repo"), intent.get("branch"))
            elif action == "change-base":
                for row in intent.get("generation") or ():
                    add(card, intent.get("repo"), row.get("branch"))
                    add(card, intent.get("repo"), row.get("oldBranch"))
        for (action, key), result in st.results.items():
            if action in FLOW_BRANCH_ACTIONS and result.get("ok"):
                intent = st.intents.get((action, key)) or {}
                add(card, result.get("repo") or intent.get("repo"), result.get("branch") or intent.get("branch"))
    return found


def flow_card(flows: dict, repo_path: str, branch: str) -> str:
    """Karta, której przepływ zarządza gałęzią `branch` w repozytorium (pusty napis: żadna). Wpis bez
    repozytorium pasuje do każdego (zachowawczo)."""
    if not branch or not flows:
        return ""
    return flows.get((os.path.realpath(repo_path), branch)) or flows.get(("", branch)) or ""


# --- żądanie -------------------------------------------------------------------------------------

def _acquire(lock, deadline: float, cancel) -> bool:
    while True:
        if cancel is not None:
            cancel.check()
        if lock.acquire(timeout=0.1):
            return True
        if time.monotonic() >= deadline:
            return False


def listed_worktree(scanner, path):
    """Worktree z listy skanera wskazany ścieżką z żądania (porównanie kanoniczne) albo None."""
    if not isinstance(path, str) or not path or "\0" in path or not os.path.isabs(path):
        return None
    try:
        wanted = os.path.realpath(path)
    except (OSError, ValueError):
        return None
    with scanner._lock:
        items = list(scanner.items)
    return next((w for w in items if not w.missing and os.path.realpath(w.path) == wanted), None)


def pull_fast_forward(scanner, path, agents=(), cards=(), journal=None) -> dict:
    """„Zaciągnij” dla worktree z listy. agents/cards: katalogi żywych agentów i miejsca zajęte przez przepływ
    karty (migawka z chwili żądania, wątek główny); journal: dziennik kart albo None. Działa w wątku roboczym."""
    from .scanner import _agents_by_worktree
    wt = listed_worktree(scanner, path)
    if wt is None:
        return _refused("bad-request", "to nie jest worktree z listy Worktrees", path if isinstance(path, str) else "")
    with scanner._lock:
        items = list(scanner.items)
    if wt.path in _agents_by_worktree(items, cards):
        return _refused("agent", "w tym katalogu trwa krok albo praca karty — zaciągnij po jej zakończeniu", wt.path)
    if wt.path in _agents_by_worktree(items, agents):
        return _refused("agent", "w tym katalogu pracuje agent — zaciągnij po zakończeniu jego pracy", wt.path)
    if boundary_active(("-C", wt.path)):
        return _refused("boundary", "zaciąganie niedostępne: repozytorium jest wystawione pudełku "
                        "albo tryb pudełek jest włączony", wt.path)
    try:
        flows = flow_branches(journal)
    except Exception as exc:  # zachowawczo: nie wiemy, czy gałąź jest w przepływie
        print(f"tide-daemon: worktrees: zaciąganie: dziennik kart: {exc!r}", flush=True)
        return _refused("card-step", "nie da się odczytać dzienników kart — Tide nie zaciąga gałęzi, "
                        "o której nie wie, czy jest w przepływie", wt.path)

    from ..cardflow.gitops import lock_for  # leniwie: cardflow importuje ten pakiet
    cancel = getattr(scanner, "_cancel", None)
    lock = lock_for(wt.repo_path)
    if not _acquire(lock, time.monotonic() + LOCK_WAIT, cancel):
        return _refused("locked", "repozytorium jest zajęte (scalanie karty albo inna operacja Tide) — "
                        "spróbuj za chwilę", wt.path)
    try:
        return _pull_locked(_runner(scanner), wt, flows)
    finally:
        lock.release()


def _runner(scanner) -> Git:
    base = scanner.git
    if not isinstance(base, Git):
        return base  # atrapa z testów
    runner = copy.copy(base)  # te same korzenie, anulowanie i wykonywalny git; własny limit i otoczenie
    runner.timeout = PULL_TIMEOUT
    runner.env = fetch_env(base.env)
    return runner


def _operation(git, cwd: str, branch: str) -> str:
    """Opis trwającej operacji gita w worktree albo pusty napis. Nieczytelny katalog gita → operacja (zachowawczo)."""
    res = git.run(cwd, "rev-parse", "--path-format=absolute", "--absolute-git-dir", "--git-common-dir")
    lines = res.out.splitlines() if res.ok else []
    if len(lines) != 2:
        return "nie da się odczytać katalogu gita"
    gitdir, common = lines
    for name, what in OPERATION_MARKERS:
        if os.path.lexists(os.path.join(gitdir, name)):
            return what
    if branch and os.path.lexists(os.path.join(common, "refs", "heads", *branch.split("/")) + ".lock"):
        return "inny git zmienia gałąź"
    return ""


def _filtered(git, cwd: str, head: str, target: str) -> str:
    """Sterownik filtra plików (smudge/process), który zadziałałby przy checkoucie zmian z `target`, albo pusty
    napis. Zachowawczo: zmiana `.gitattributes` przy skonfigurowanym sterowniku też się liczy."""
    res = git.run(cwd, "config", "--null", "--get-regexp", r"^filter\..+\.(smudge|process)$")
    drivers = set()
    for record in res.out.split("\0") if res.ok else ():
        key = record.partition("\n")[0]
        if key.startswith("filter.") and key.count(".") >= 2:
            drivers.add(key[len("filter."):key.rindex(".")])
    if not drivers:
        return ""
    changed = git.run(cwd, "diff", "--name-only", "-z", "--no-renames", head, target, "--")
    if not changed.ok:
        return sorted(drivers)[0]
    paths = [p for p in changed.out.split("\0") if p]
    if any(os.path.basename(p) == ".gitattributes" for p in paths):
        return sorted(drivers)[0]
    if not paths:
        return ""
    attrs = git.run(cwd, "check-attr", "-z", "--stdin", "filter", input_text="\0".join(paths) + "\0")
    if not attrs.ok:
        return sorted(drivers)[0]
    fields = attrs.out.split("\0")
    for i in range(0, len(fields) - 2, 3):
        value = fields[i + 2]
        if value in drivers:
            return value
    return ""


def _pull_locked(git, wt, flows: dict) -> dict:
    """Świeży odczyt stanu i `merge --ff-only` pod blokadą repozytorium."""
    path = wt.path
    symbolic = git.run(path, "symbolic-ref", "--quiet", "HEAD")
    ref = symbolic.out.strip() if symbolic.ok else ""
    if not ref.startswith("refs/heads/"):
        return _refused("no-upstream", "odłączony HEAD — nie ma gałęzi, którą dałoby się zaciągnąć", path)
    branch = ref[len("refs/heads/"):]
    card = flow_card(flows, wt.repo_path, branch)
    if card:
        return _refused("card-step", f"tą gałęzią zarządza Tide (przepływ karty {card}) — zaciąga ją przepływ, "
                        "nie okno Worktrees", path, branch=branch)
    operation = _operation(git, path, branch)
    if operation:
        return _refused("git-operation", f"w tym worktree trwa operacja gita ({operation}) — dokończ ją sam",
                        path, branch=branch)
    # `@{upstream}` bieżącej gałęzi worktree (pełna nazwa `refs/heads/x@{upstream}` nie jest rozumiana przez gita).
    upstream_ref = git.run(path, "rev-parse", "--symbolic-full-name", "@{upstream}")
    upstream = upstream_ref.out.strip() if upstream_ref.ok else ""
    target = git.run(path, "rev-parse", "--verify", "--quiet", upstream + "^{commit}") if upstream else None
    if not upstream or not target.ok:
        return _refused("no-upstream", f"gałąź {branch} nie ma upstreamu (albo jego ref zniknął)", path,
                        branch=branch)
    short = upstream[len("refs/remotes/"):] if upstream.startswith("refs/remotes/") else upstream
    target_sha = target.out.strip()
    head = git.run(path, "rev-parse", "--verify", "HEAD")
    if not head.ok:
        return _refused("no-upstream", f"gałąź {branch} nie ma jeszcze commitów", path, branch=branch)
    head_sha = head.out.strip()
    status = git.run(path, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
    changes = len(parse_status(status.out)) if status.ok else -1
    if changes != 0:
        text = "nie da się odczytać stanu katalogu" if changes < 0 else f"{changes} zmian w katalogu"
        return _refused("dirty", f"{text} — zacommituj albo odłóż je sam, potem zaciągnij", path, branch=branch)
    counts = git.run(path, "rev-list", "--left-right", "--count", f"{head_sha}...{target_sha}")
    parts = counts.out.split() if counts.ok else []
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return _refused("git", "nie da się porównać gałęzi z upstreamem: " + _first_line(counts.err), path,
                        branch=branch)
    ahead, behind = int(parts[0]), int(parts[1])
    extra = {"branch": branch, "upstream": short, "ahead": ahead, "behind": behind}
    if ahead and behind:
        return _refused("diverged", f"gałąź rozjechana z {short} (↑{ahead} ↓{behind}) — Tide nie scala historii "
                        "ani nie robi rebase za ciebie", path, **extra)
    if not behind:
        return _refused("up-to-date", f"gałąź aktualna względem {short}", path, **extra)
    driver = _filtered(git, path, head_sha, target_sha)
    if driver:
        return _refused("git-operation", f"zmiany z {short} przechodzą przez filtr plików „{driver}” (np. git-lfs, "
                        "może sięgać do sieci) — zaciągnij je ręcznie", path, **extra)

    merged = git.run(path, *MERGE_OPTIONS, "merge", "--ff-only", "--no-autostash", "--no-stat", target_sha)
    after = git.run(path, "rev-parse", "--verify", "HEAD")
    now = after.out.strip() if after.ok else ""
    if merged.ok and now == target_sha:
        print(f"tide-daemon: worktrees: zaciągnięto {path}: {head_sha[:7]}..{target_sha[:7]} z {short}", flush=True)
        return {"ok": True, "path": path, "branch": branch, "upstream": short, "from": head_sha, "to": target_sha,
                "commits": behind, "message": f"zaciągnięto {commits_word(behind)} z {short} (fast-forward)"}
    # Przerwane albo odrzucone scalenie: opisujemy stan po nim (spójność po przerwaniu), niczego nie naprawiamy.
    status = git.run(path, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
    left = len(parse_status(status.out)) if status.ok else -1
    moved = now != head_sha
    state = ("HEAD bez zmian" if not moved and left == 0 else
             f"HEAD {'przesunięty' if moved else 'bez zmian'}, {'stan nieznany' if left < 0 else f'{left} zmian'} "
             "w katalogu — sprawdź `git status`")
    timed_out = getattr(merged, "timed_out", False)
    reason = "timeout" if timed_out else "git"
    head_text = "przekroczono limit czasu zaciągania" if timed_out else \
        "git nie zaciągnął: " + (_first_line(merged.err) or "nieznany błąd")
    print(f"tide-daemon: worktrees: zaciąganie {path}: {reason}: {_first_line(merged.err)}", flush=True)
    return _refused(reason, f"{head_text} ({state})", path, head=now, dirty=left, **extra)
