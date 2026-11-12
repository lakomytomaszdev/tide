#!/usr/bin/python3
"""K-102 #5: reproduktor czasu zatwierdzania planu (`approve_plan`) z pomiarem „przed” i „po”.

Buduje tymczasowe repozytorium z kilkuset planami na kilkudziesięciu gałęziach i worktree, kartę z planem
18 ticketów i mierzy zatwierdzenie na prywatnym rejestrze numerów (tymczasowy `XDG_STATE_HOME`). Wynik to jedna
linia JSON: `seconds` (czas `approve_plan`), `planReads` (ile pełnych odczytów planów repozytorium wykonało
zatwierdzenie — licznik deterministyczny), `tickets`, `tideRoot`, a informacyjnie `firstTicketSeconds`
(czas do chwili, gdy zatwierdzony plan ma gotowy pierwszy ticket: zatwierdzenie i odczyt dziennika).

Użycie (z katalogu repozytorium):
  /usr/bin/python3 tools/approval_benchmark.py                       # „po”: kod bieżącego drzewa
  /usr/bin/python3 tools/approval_benchmark.py --base-ref <SHA>      # „przed”: kod przypiętej bazy karty
  /usr/bin/python3 tools/approval_benchmark.py --tide-root <ścieżka> # kod z dowolnego drzewa Tide

`--base-ref` zakłada tymczasowy worktree (`git worktree add --detach` w katalogu tymczasowym), mierzy na jego kodzie
i usuwa go (`git worktree remove`, bez `prune`); bieżący worktree, jego gałąź i indeks zostają bez zmian. Kod
mierzonego drzewa jest tylko czytany (bez plików `__pycache__`).

Zabezpieczenie: `--state-home` i `--repo` (domyślnie świeże katalogi w katalogu tymczasowym) muszą leżeć w
katalogu tymczasowym (`$TMPDIR`), inaczej skrypt kończy się kodem 2 przed jakimkolwiek zapisem. Skrypt nigdy nie
dotyka `~/.local/state/tide/ticket-ids.json` ani prawdziwego repozytorium: pomiar biegnie w osobnym procesie
z własnym HOME, XDG_STATE_HOME, XDG_CONFIG_HOME i katalogiem tablicy.

Kody wyjścia: 0 — wynik wypisany, 1 — błąd pomiaru, 2 — niedozwolony cel albo złe argumenty.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PLANS, DEFAULT_BRANCHES, DEFAULT_TICKETS, DEFAULT_WORKTREES = 300, 40, 18, 8
CLAUDE_MD = "# Projekt\n\n## Plan pracy agentów (Tide)\n\n- ticket-prefix: `PT`, tracker: local\n"
PLAN_DIR = os.path.join("docs", "agents", "plans")


class UnsafeTarget(Exception):
    """XDG_STATE_HOME albo repozytorium poza katalogiem tymczasowym."""


def temp_root() -> str:
    return os.path.realpath(tempfile.gettempdir())


def inside_temp(path: str) -> bool:
    real, root = os.path.realpath(path), temp_root()
    return real != root and real.startswith(root + os.sep)


def ensure_inside_temp(label: str, path: str) -> None:
    if not path or not inside_temp(path):
        raise UnsafeTarget(f"{label} {path!r} leży poza katalogiem tymczasowym {temp_root()} — "
                           "pomiar nie dotyka prawdziwego rejestru ani repozytoriów")


def ensure_private(state_home: str, repo: str) -> None:
    """Odmowa, gdy rejestr numerów albo repozytorium leży poza katalogiem tymczasowym (przed zapisem)."""
    ensure_inside_temp("XDG_STATE_HOME", state_home)
    ensure_inside_temp("repozytorium", repo)


def git(repo: str, *args: str) -> str:
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
               GIT_AUTHOR_NAME="Bench", GIT_AUTHOR_EMAIL="bench@example.com",
               GIT_COMMITTER_NAME="Bench", GIT_COMMITTER_EMAIL="bench@example.com")
    done = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=env)
    if done.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def plan_text(index: int, numbers: list[int]) -> str:
    rows = "".join(f"| PT-{n} | Zadanie {n} | gotowe | | Sonnet | | standard | Opus | |\n" for n in numbers)
    return (f"---\ntide-plan: 1\ntitle: Plan {index}\ntracker: local\nticket-prefix: PT\ncoordinator: claude\n"
            f"status: done\nupdated: 2026-01-01T10:00:00+01:00\n---\n\n"
            "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + rows)


def build_repo(repo: str, plans: int, branches: int, worktrees: int) -> int:
    """Repozytorium: plany w gałęzi głównej i na `branches` gałęziach, `worktrees` z nich w osobnych worktree.
    Zwraca największy użyty numer ticketu."""
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "master")
    with open(os.path.join(repo, "CLAUDE.md"), "w", encoding="utf-8") as fh:
        fh.write(CLAUDE_MD)
    branches = max(0, branches)
    on_master = plans if not branches else max(1, plans // 3)
    per_branch = [0] * branches
    for i in range(plans - on_master):
        per_branch[i % branches] += 1
    number = 0

    def write(count: int, first: int) -> int:
        nonlocal number
        os.makedirs(os.path.join(repo, PLAN_DIR), exist_ok=True)
        for k in range(count):
            numbers = [number + 1, number + 2] if (first + k) % 3 == 0 else [number + 1]
            number += len(numbers)
            with open(os.path.join(repo, PLAN_DIR, f"2026-01-01-plan-{first + k:04d}.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(plan_text(first + k, numbers))
        return first + count

    index = write(on_master, 0)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "plans on master")
    for b, count in enumerate(per_branch):
        git(repo, "checkout", "-q", "-b", f"feat/branch-{b:03d}", "master")
        index = write(count, index)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", f"plans on branch {b}")
        git(repo, "checkout", "-q", "master")
    for b in range(min(worktrees, branches)):
        git(repo, "worktree", "add", "-q", f"{repo}-wt-{b:02d}", f"feat/branch-{b:03d}")
    return number


# --- proces roboczy -----------------------------------------------------------------------------

def worker(args) -> dict:
    """Pomiar w osobnym procesie: kod `--tide-root`, prywatny HOME i rejestr, tylko odczyt drzewa kodu."""
    ensure_private(os.environ.get("XDG_STATE_HOME", ""), args.repo)
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(args.tide_root, "daemon"))
    from tide_daemon.board.store import BoardStore
    from tide_daemon.cardflow.engine import FlowEngine
    from tide_daemon.cardflow.state import fold
    from tide_daemon.coordination import repoplans, reservations
    from tide_daemon.models import executors

    registry = reservations.registry_path()
    if not inside_temp(registry):
        raise UnsafeTarget(f"rejestr {registry} leży poza katalogiem tymczasowym")
    reads = []
    original = repoplans.RepoPlans.read

    def counted(self, roots):
        reads.append(1)
        return original(self, roots)
    repoplans.RepoPlans.read = counted

    base = args.work
    store = BoardStore(os.path.join(base, "board"))
    reply = store.upsert_project({"slug": "proj", "repo": args.repo, "aiDocs": "repo"})
    if not reply.get("ok"):
        raise RuntimeError(f"projekt: {reply}")
    card = store.create_card({"title": "Reproduktor", "project": "proj", "description": "pomiar",
                              "column": "todo"})["id"]
    settings = os.path.join(base, "cardflow.json")
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({}, fh)
    engine = FlowEngine(store, claude_projects=os.path.join(base, "claude"), systemd_run="/bin/true",
                        catalog=executors.default_catalog, tmp_root=os.path.join(base, "tmp"),
                        settings=settings, which=lambda name: None, grace=5.0)
    item, project = engine._project(card)
    started = engine._start_base(item, project, {}, engine.journal.state(card))
    engine.journal.append(card, "card.started", entry="plan", repo=project.repo, project=project.slug,
                          check="auto", column=item.column, moveId=item.move_id, trackerProject="",
                          trackerUrl="", **started)
    engine._project_base(card)
    rows = []
    for n in range(1, args.tickets + 1):
        work_id = f"#{n}"
        path = os.path.join(store.root, engine.journal.rel_dir(card), "plan", "tickets", f"{n}.md")
        body = f"# Ticket {n}\n\n## Kryteria akceptacji\n\n- [ ] Wynik {n}.\n"
        engine.journal.write_file(card, os.path.join("plan", "tickets", f"{n}.md"), body.encode("utf-8"))
        rows.append({"workId": work_id, "title": f"Plasterek {n}", "state": "todo", "executor": "Sonnet",
                     "blockers": [f"#{n - 1}"] if n > 1 and n % 3 else [], "sourceState": "", "body": body,
                     "cardPath": path})
    engine.journal.append(card, "plan.checked", valid=True, errors=[], step="bench")
    engine.journal.append(card, "plan.proposed", title="Plan reproduktora", prefix="PT", coordinator="claude",
                          session="", rows=rows, planner="Sonnet", box=None, sourceStep="bench",
                          sourceKind="", specification="", planPath="", artifactPath="")
    engine.journal.append(card, "plan.reviewed", verdict="ok", findings=[])

    reads.clear()
    started_at = time.perf_counter()
    result = engine.approve_plan(card)
    seconds = time.perf_counter() - started_at
    if not result.get("ok") or result.get("queued") or not result.get("waves"):
        raise RuntimeError(f"zatwierdzenie nie powiodło się: {result}")
    st = fold(engine.journal.read(card))
    first_ticket = time.perf_counter() - started_at if st.plan_approved and result["waves"][0] else None
    return {"seconds": round(seconds, 4), "planReads": len(reads), "tickets": len(result["map"]),
            "firstTicketSeconds": None if first_ticket is None else round(first_ticket, 4)}


# --- proces główny ------------------------------------------------------------------------------

def measure(args, tide_root: str) -> dict:
    work = tempfile.mkdtemp(prefix="approval-benchmark-")
    try:
        state_home = args.state_home or os.path.join(work, "state")
        repo = args.repo or os.path.join(work, "repo")
        ensure_private(state_home, repo)       # przed jakimkolwiek zapisem poza `work`
        build_repo(repo, args.plans, args.branches, args.worktrees)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "TIDE_"))}
        env.update(HOME=os.path.join(work, "home"), XDG_STATE_HOME=state_home,
                   XDG_CONFIG_HOME=os.path.join(work, "config"), TIDE_BOARD_DIR=os.path.join(work, "boxboard"),
                   PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        os.makedirs(env["HOME"])
        done = subprocess.run([sys.executable, os.path.abspath(__file__), "--worker", "--tide-root", tide_root,
                               "--repo", repo, "--work", work, "--tickets", str(args.tickets)],
                              capture_output=True, text=True, env=env)
        if done.returncode:
            raise RuntimeError((done.stderr or done.stdout).strip() or f"proces pomiaru: kod {done.returncode}")
        return json.loads(done.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("oczekiwano liczby dodatniej")
    return number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--tide-root", help="drzewo kodu Tide do zmierzenia (domyślnie bieżące)")
    source.add_argument("--base-ref", help="rewizja do zmierzenia w tymczasowym worktree (np. przypięta baza karty)")
    parser.add_argument("--plans", type=positive, default=DEFAULT_PLANS)
    parser.add_argument("--branches", type=int, default=DEFAULT_BRANCHES)
    parser.add_argument("--tickets", type=positive, default=DEFAULT_TICKETS)
    parser.add_argument("--worktrees", type=int, default=DEFAULT_WORKTREES)
    parser.add_argument("--state-home", help="katalog stanu z rejestrem numerów (tylko w katalogu tymczasowym)")
    parser.add_argument("--repo", help="katalog nowego repozytorium pomiaru (tylko w katalogu tymczasowym)")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--work", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        try:
            print(json.dumps(worker(args)))
            return 0
        except UnsafeTarget as exc:
            print(f"approval_benchmark: {exc}", file=sys.stderr)
            return 2
    if args.branches < 0 or args.worktrees < 0:
        parser.error("liczby gałęzi i worktree nie mogą być ujemne")
    try:
        # Jawnie podane cele sprawdzamy przed czymkolwiek (także przed worktree bazy); domyślne są w `work`.
        for label, path in (("XDG_STATE_HOME", args.state_home), ("repozytorium", args.repo)):
            if path is not None:
                ensure_inside_temp(label, path)
    except UnsafeTarget as exc:
        print(f"approval_benchmark: {exc}", file=sys.stderr)
        return 2
    tide_root, base_tree = os.path.realpath(args.tide_root or ROOT), None
    try:
        if args.base_ref:
            parent = tempfile.mkdtemp(prefix="approval-benchmark-base-")
            base_tree = os.path.join(parent, "tide")
            git(ROOT, "worktree", "add", "-q", "--detach", base_tree, args.base_ref)
            tide_root = base_tree
        if not os.path.isdir(os.path.join(tide_root, "daemon", "tide_daemon")):
            print(f"approval_benchmark: {tide_root} nie jest drzewem Tide (brak daemon/tide_daemon)",
                  file=sys.stderr)
            return 2
        result = measure(args, tide_root)
    except UnsafeTarget as exc:
        print(f"approval_benchmark: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"approval_benchmark: błąd pomiaru: {exc}", file=sys.stderr)
        return 1
    finally:
        if base_tree:
            try:
                git(ROOT, "worktree", "remove", "--force", base_tree)
            except RuntimeError as exc:
                print(f"approval_benchmark: nie usunięto worktree bazy {base_tree}: {exc}", file=sys.stderr)
            shutil.rmtree(os.path.dirname(base_tree), ignore_errors=True)
    result["tideRoot"] = tide_root
    if args.base_ref:
        result["baseRef"] = args.base_ref
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
