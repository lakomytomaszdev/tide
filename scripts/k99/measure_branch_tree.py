#!/usr/bin/env python3
"""Pomiar budowy drzewa mapy gałęzi w oknie Worktrees (K-99, PT-472) — druga połowa kryterium „mapa < 1 s”.

Pierwszą połowę (odpowiedź usługi `GitMonitor.branch_map`) mierzy `measure_branchmap.py` (#21). Tu liczy się czas od
chwili, w której odpowiedź mapy trafia do backendu okna, do zbudowania drzewa w trybie „Gałęzie”.

Dwa zestawy wejścia:

- `branchmap` — zapisana odpowiedź usługi z #21 (`apps/board/tests/data/k99_acceptance/branchmap.json`);
- `nodes500` — mapa 500 węzłów gałęzi (limit maksymalny) policzona projekcją PT-387
  (`branchmap.branch_map(journal, board, slug, limit=500)`) na dzienniku i Tablicy zbudowanych tutaj z prawdziwych
  typów zdarzeń (karty z ticketami, korekta bazy, scalenia, baza nieznana). Zestaw jest jawnie syntetyczny i służy
  wyłącznie wydajności.

Pomiar jednego powtórzenia (nowe okno, nowy backend, ten sam proces):

1. Okno startuje w trybie „Gałęzie” z prawdziwym `Backend` (apps/worktrees/backend.py). Jego `_call` (D-Bus) jest
   zastąpiony odroczoną odpowiedzią: odpowiedź wraca z pętli zdarzeń, tą samą drogą co `QDBusPendingCallWatcher`
   (`branchMap` → `done` → `branchMapReplied` → QML). Odpowiedź z projektami przychodzi sama, odpowiedź mapy czeka.
2. Sprawdzamy, że wywołanie odczytu mapy wróciło, a drzewa jeszcze nie ma (0 węzłów, `loading`).
3. Odpowiedź mapy jest podawana z pętli zdarzeń. Czas liczymy od jej podania do zbudowania drzewa i przetworzenia
   zdarzeń oczekujących w kolejce okna (bez renderowania i bez czekania na klatkę).
4. Liczymy węzły drzewa i czytamy „pokazano N z M”.

Wynik: najlepszy, mediana i najgorszy czas z ≥ 5 powtórzeń dla każdego zestawu (JSON na stdout).

Kryteria K-99: dla `branchmap` mediana budowy drzewa + mediana odpowiedzi usługi z #21 < 1 s (podaj ją w
`--service-median`; bez niej skrypt pokazuje, ile sekund budżetu zostaje usłudze), dla `nodes500` mediana < 1 s.

Użycie: `QT_QPA_PLATFORM=offscreen python3 scripts/k99/measure_branch_tree.py` (pomiar: polecenie `outside`
z `"exclusive": true`). `--smoke` pozwala na mniej powtórzeń (testy w piaskownicy, bez oceny kryteriów).

Kod wyjścia: 0 — kryteria spełnione; 1 — błąd; 3 — kryterium przekroczone albo sprawdzenia budowy drzewa nie przeszły.
"""
import argparse
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from types import SimpleNamespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(REPO_ROOT, "apps", "board", "tests", "data", "k99_acceptance")
LIMIT_SECONDS = 1.0
MIN_REPEATS = 5
EXIT_FAILED = 3
NODE_LIMIT = 500
SYNTHETIC_SLUG = "synthetic"
SYNTHETIC_REPO = "/srv/repo/synthetic"
DATASETS = ("branchmap", "nodes500")

ALL = ("(function all(it, name, out) { out = out || []; if (!it) return out; "
       "if (it.objectName === name && it.visible) out.push(it); "
       "for (const c of it.children) all(c, name, out); return out })")

HARNESS = '''
import QtQuick
import QtQuick.Window

Window {
    id: root
    width: 1180
    height: 820
    visible: true

    WorktreesView {
        id: win
        anchors.fill: parent
        backend: realBackend
    }
    QtObject { id: loader; property var item: win }
}
'''


# --- obliczenia -------------------------------------------------------------------------------------------

def summarize(samples) -> dict:
    """Najlepszy, mediana i najgorszy czas (sekundy)."""
    if not samples:
        raise ValueError("brak próbek")
    return {"best": min(samples), "median": statistics.median(samples), "worst": max(samples), "samples": list(samples)}


def verdict(results: dict, service_median=None, limit=LIMIT_SECONDS) -> dict:
    """Kryteria K-99 dla wyników `{zestaw: {"seconds": summarize(...), "checks": {...}}}`.

    `branchmap`: mediana drzewa + mediana usługi < limit (bez `service_median` — tylko mediana drzewa i budżet dla
    usługi); `nodes500`: mediana drzewa < limit. Sprawdzenia budowy (wywołanie wraca przed drzewem, liczby węzłów,
    „pokazano N z M”) muszą przejść w każdym powtórzeniu."""
    tree = results["branchmap"]["seconds"]["median"]
    big = results["nodes500"]["seconds"]["median"]
    checks = all(r["checks"]["passed"] for r in results.values())
    out = {"limitSeconds": limit, "branchmapTreeMedian": tree, "nodes500TreeMedian": big,
           "nodes500BelowLimit": big < limit, "buildChecksPassed": checks,
           "serviceBudgetSeconds": max(0.0, limit - tree), "serviceMedian": service_median}
    if service_median is None:
        out.update(branchmapSumChecked=False, branchmapBelowLimit=tree < limit)
    else:
        out.update(branchmapSumChecked=True, branchmapSum=tree + service_median,
                   branchmapBelowLimit=tree + service_median < limit)
    out["passed"] = out["nodes500BelowLimit"] and out["branchmapBelowLimit"] and checks
    return out


# --- zestawy wejścia --------------------------------------------------------------------------------------

def saved_reply() -> dict:
    with open(os.path.join(DATA_DIR, "branchmap.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _daemon_on_path() -> None:
    path = os.path.join(REPO_ROOT, "daemon")
    if path not in sys.path:
        sys.path.insert(0, path)


def _sha(n: int) -> str:
    return f"{n:040x}"


class _CardLog:
    """Dziennik jednej karty zapisywany jednym plikiem kopii usługi; kształt zdarzeń jak w silniku przepływu
    (`card.started`, pary zamiar/wynik `card-branch`, `reserve`, `ticket-branch`, `merge`, `ticket.rejected`,
    `card.finished`, `card.merged`, `card.base-changed`)."""

    def __init__(self, card: str, clock):
        self.card, self.clock, self.events, self.branch = card, clock, [], f"card/{card.lower()}-x"

    def add(self, event_type: str, /, **data) -> "_CardLog":
        self.events.append({"v": 1, "seq": len(self.events) + 1, "ts": self.clock(), "card": self.card,
                            "type": event_type, **data})
        return self

    def start(self, base_ref: str, sha: str, known: bool) -> "_CardLog":
        prefix = "refs/remotes/" if base_ref.startswith("refs/remotes/") else "refs/heads/"
        base = {"baseRef": base_ref, "baseBranch": base_ref[len(prefix):], "baseCommit": sha} if known else {}
        self.add("card.started", entry="now", executor="Sonnet", repo=SYNTHETIC_REPO, project=SYNTHETIC_SLUG,
                 column="todo", baseVersion=1, **base)
        self.add("intent", action="card-branch", key=self.branch, repo=SYNTHETIC_REPO, branch=self.branch,
                 worktree=f"{SYNTHETIC_REPO}-wt/{self.branch}", base=sha if known else "",
                 **({"baseRef": base_ref, "baseBranch": base["baseBranch"]} if known else {}))
        self.add("result", action="card-branch", key=self.branch, ok=True, branch=self.branch,
                 worktree=f"{SYNTHETIC_REPO}-wt/{self.branch}", base=sha, existing=False)
        return self

    def ticket(self, ticket: str, title: str) -> "_CardLog":
        branch = f"ticket/{ticket.lower()}-x"
        self.add("intent", action="reserve", key=f"launch-{ticket}", repo=SYNTHETIC_REPO, title=title)
        self.add("result", action="reserve", key=f"launch-{ticket}", ok=True, ticket=ticket, title=title)
        self.add("intent", action="ticket-branch", key=ticket, repo=SYNTHETIC_REPO, branch=branch,
                 worktree=f"{SYNTHETIC_REPO}-wt/{branch}", base=f"refs/heads/{self.branch}", ticket=ticket)
        self.add("result", action="ticket-branch", key=ticket, ok=True, branch=branch,
                 worktree=f"{SYNTHETIC_REPO}-wt/{branch}", base=_sha(7), existing=False, ticket=ticket)
        self.add("step.started", step=f"{self.card}-{ticket}-1", kind="ticket", ticket=ticket, executor="Sonnet",
                 branch=branch)
        self.add("step.finished", step=f"{self.card}-{ticket}-1", result="done", notes=["note.md"])
        return self

    def merge_ticket(self, ticket: str) -> "_CardLog":
        branch = f"ticket/{ticket.lower()}-x"
        self.add("intent", action="merge", key=ticket, branch=branch, into=self.branch, expectedTip=_sha(7),
                 cardTip=_sha(6))
        self.add("result", action="merge", key=ticket, ok=True, commit=_sha(8), already=False)
        return self

    def write(self, mirror: str) -> None:
        os.makedirs(mirror, exist_ok=True)
        with open(os.path.join(mirror, f"{self.card}.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.events))


def build_journal(root: str, cards: int = 100, tickets: int = 5):
    """Dziennik kart i Tablica syntetycznego projektu: `cards` kart po `tickets` ticketów (razem `cards × (1 + tickets)`
    gałęzi). Co 10. karta ma bazę nieznaną, co 7. korektę bazy, co 5. startuje z gałęzi poprzedniej karty; co 3. karta
    jest scalona (na przemian do `master` i `origin/main`), co 3. domknięta, reszta czynna; tickety: trzy scalone,
    jeden odrzucony, jeden czynny. Zwraca `(journal, board)` gotowe dla `branchmap.branch_map`."""
    _daemon_on_path()
    from tide_daemon.cardflow.journal import Journal
    from tide_daemon.gitread import branchmap

    mirror = os.path.join(root, "state")
    now = [datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc).timestamp()]

    def clock() -> str:
        now[0] += 60
        return datetime.fromtimestamp(now[0], timezone.utc).astimezone().isoformat(timespec="microseconds")

    board_cards, previous = {}, None
    for number in range(1, cards + 1):
        card = f"K-{number}"
        log = _CardLog(card, clock)
        unknown = number % 10 == 0
        if previous is not None and number % 5 == 1 and not unknown:
            log.start(f"refs/heads/{previous}", _sha(6), True)
        else:
            log.start("refs/heads/master", _sha(1), not unknown)
        if number % 7 == 0 and not unknown:
            log.add("card.base-changed", previousBaseVersion=1, baseVersion=2,
                    base={"baseRef": "refs/heads/develop", "baseBranch": "develop", "baseCommit": _sha(3)})
        for index in range(1, tickets + 1):
            ticket = f"PT-{number * 10 + index}"
            log.ticket(ticket, f"Ticket {index} karty {number}")
            if index <= 3:
                log.merge_ticket(ticket)
            elif index == 4:
                log.add("ticket.rejected", ticket=ticket)
        column = "progress"
        if number % 3 == 0:
            target = "refs/heads/master" if number % 2 else "refs/remotes/origin/main"
            log.add("card.finished", column="done")
            log.add("card.merged", operationId=f"op-{number}", targetRef=target, targetBranch=target.split("/", 2)[2],
                    sourceCommit=_sha(6), targetCommitBefore=_sha(1), resultCommit=_sha(9), baseVersion=1)
            column = "done"
        elif number % 3 == 1:
            log.add("card.finished", column="done")
            column = "done"
        log.write(mirror)
        board_cards[card] = SimpleNamespace(id=card, project=SYNTHETIC_SLUG, title=f"Karta {number}", column=column)
        previous = log.branch
    board = SimpleNamespace(
        projects={SYNTHETIC_SLUG: SimpleNamespace(slug=SYNTHETIC_SLUG, name="Syntetyczny", repo=SYNTHETIC_REPO)},
        cards=board_cards)
    journal = Journal(os.path.join(root, "board"), mirror=mirror)
    return journal, branchmap.board_view(board)


def synthetic_reply(cards: int = 100, tickets: int = 5, limit: int = NODE_LIMIT) -> dict:
    """Odpowiedź `Git.BranchMap` z projekcji PT-387 (`branch_map(journal, board, slug, limit)`), jak ją składa
    `branchmap.request`: `{ok: true, …}`."""
    _daemon_on_path()
    from tide_daemon.gitread import branchmap
    with tempfile.TemporaryDirectory(prefix="tide-k99-synthetic-") as root:
        journal, board = build_journal(root, cards, tickets)
        return {"ok": True, **branchmap.branch_map(journal, board, SYNTHETIC_SLUG, limit)}


def dataset_replies() -> dict:
    return {"branchmap": saved_reply(), "nodes500": synthetic_reply()}


def branch_count(reply: dict) -> int:
    return sum(1 for n in reply["nodes"] if n["kind"] in ("card", "ticket"))


# --- pomiar w oknie ---------------------------------------------------------------------------------------

def stage_package(out: str) -> str:
    subprocess.run([sys.executable, os.path.join(REPO_ROOT, "widgets", "stage.py"), "--app",
                    os.path.join(REPO_ROOT, "apps", "tide"), out], check=True, stdout=subprocess.DEVNULL)
    return out


def expected_checks(reply: dict, observed: dict) -> dict:
    """Czy drzewo ma tyle węzłów, ile odpowiedź, i „pokazano N z M” zgodne z `truncated`."""
    branches = branch_count(reply)
    kinds = observed["kinds"]
    cards = sum(1 for n in reply["nodes"] if n["kind"] == "card")
    tickets = sum(1 for n in reply["nodes"] if n["kind"] == "ticket")
    shown = [f"pokazano {branches} z {reply['total']}"] if reply.get("truncated") else []
    checks = {"callReturnedBeforeTree": observed["before"]["nodes"] == 0 and observed["before"]["loading"]
                                        and not observed["before"]["hasReply"],
              "allNodes": observed["after"]["nodes"] == len(reply["nodes"]),
              "cardNodes": kinds.get("card", 0) == cards, "ticketNodes": kinds.get("ticket", 0) == tickets,
              "shownText": observed["after"]["shown"] == shown, "delivered": observed["delivered"]}
    checks["passed"] = all(checks.values())
    return checks


class Runner:
    """Jedno `QGuiApplication` i kolejne okna: każde powtórzenie to nowy backend i nowe okno."""

    def __init__(self, qml_dir: str, backend_path: str):
        from PyQt6.QtGui import QGuiApplication
        self.qml_dir, self.backend_path = qml_dir, backend_path
        self.app = QGuiApplication.instance() or QGuiApplication([])
        spec = importlib.util.spec_from_file_location("tide_worktrees_tree_backend", backend_path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        with open(os.path.join(qml_dir, "Harness.qml"), "w", encoding="utf-8") as fh:
            fh.write(HARNESS)

    def backend_class(self):
        from PyQt6.QtCore import QTimer
        module = self.module

        class DeferredBackend(module.Backend):
            """Prawdziwy `Backend`, w którym D-Bus (`_call`) zastąpiono odroczoną odpowiedzią z pętli zdarzeń."""

            def __init__(self, projects: dict, map_reply: dict):
                super().__init__(None, [])   # podgląd: ustawienia tylko w pamięci, nic nie trafia do plików
                self.bus = object()          # `branchMap` nie wybiera już odpowiedzi podglądu, tylko woła `_call`
                self.projects, self.map_reply = projects, map_reply
                self.held = None             # (then, tekst) odpowiedzi mapy, która czeka na podanie
                self.calls = []

            def _call(self, member, args, then, timeout=25_000, path=None, iface=None):
                self.calls.append([member, *args])
                if member != "BranchMap":
                    QTimer.singleShot(0, lambda: then(False, None) if then else None)
                    return
                if json.loads(args[0]) == {}:
                    text = json.dumps(self.projects, ensure_ascii=False)
                    QTimer.singleShot(0, lambda: then(True, text))
                else:
                    self.held = (then, json.dumps(self.map_reply, ensure_ascii=False))

        return DeferredBackend

    def run_once(self, reply: dict) -> dict:
        from PyQt6.QtCore import QCoreApplication, QTimer, QUrl
        from PyQt6.QtQml import QQmlApplicationEngine, QQmlExpression
        from PyQt6.QtTest import QTest

        project = reply["project"]
        projects = {"ok": True, "projects": [{"slug": project, "name": project, "repo": reply.get("repo", ""),
                                               "cards": sum(1 for n in reply["nodes"] if n["kind"] == "card")}]}
        backend = self.backend_class()(projects, reply)
        backend.setViewMode("branches")
        backend.setBranchProject(project)
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("realBackend", backend)
        engine.load(QUrl.fromLocalFile(self.qml_dir + "/Harness.qml"))
        window = engine.rootObjects()[0]

        def js(expression):
            value, undefined = QQmlExpression(engine.contextForObject(window), window, expression).evaluate()
            if hasattr(value, "toVariant"):
                value = value.toVariant()
            return None if undefined else value

        def state() -> dict:
            return json.loads(js(
                "(function () { const all = " + ALL + "; const page = all(loader.item, 'branchesPage')[0]; "
                "const nodes = all(loader.item, 'branchNode'); const kinds = {}; "
                "for (const n of nodes) kinds[n.info.kind] = (kinds[n.info.kind] || 0) + 1; "
                "return JSON.stringify({loading: page.loading, hasReply: page.reply !== null, nodes: nodes.length, "
                "kinds: kinds, shown: all(loader.item, 'branchTruncated').map(t => t.text)}) })()"))

        def until(predicate, ms=10_000):
            for _ in range(ms // 5):
                if predicate():
                    return True
                QTest.qWait(5)
            return bool(predicate())

        marks = {}
        try:
            if not until(lambda: backend.held is not None):
                raise RuntimeError("okno nie zażądało mapy: " + json.dumps(backend.calls))
            before = state()
            then, text = backend.held

            def deliver():
                marks["t0"] = time.perf_counter()
                then(True, text)
                marks["t1"] = time.perf_counter()
                QTimer.singleShot(0, finish)

            def finish():
                QCoreApplication.processEvents()
                marks["t2"] = time.perf_counter()

            QTimer.singleShot(0, deliver)
            delivered = until(lambda: "t2" in marks)
            after = state() if delivered else {"nodes": -1, "kinds": {}, "shown": []}
            observed = {"before": before, "after": after, "kinds": after["kinds"], "delivered": delivered}
            checks = expected_checks(reply, observed)
            return {"seconds": marks["t2"] - marks["t0"] if delivered else None,
                    "slotSeconds": marks["t1"] - marks["t0"] if delivered else None, "checks": checks,
                    "nodes": after["nodes"], "kinds": after["kinds"], "shown": after["shown"]}
        finally:
            window.close()
            engine.deleteLater()
            QCoreApplication.processEvents()


def measure(runner: Runner, reply: dict, repeats: int) -> dict:
    runs = [runner.run_once(reply) for _ in range(repeats)]
    failed = [r for r in runs if r["seconds"] is None]
    if failed:
        raise RuntimeError("drzewo nie powstało po podaniu odpowiedzi")
    checks = {k: all(r["checks"][k] for r in runs) for k in runs[0]["checks"]}
    return {"seconds": summarize([r["seconds"] for r in runs]),
            "slotSeconds": summarize([r["slotSeconds"] for r in runs]), "checks": checks,
            "nodes": runs[-1]["nodes"], "kinds": runs[-1]["kinds"], "shown": runs[-1]["shown"],
            "mapBranches": branch_count(reply), "mapTotal": reply["total"], "mapTruncated": reply["truncated"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--service-median", type=float, default=None,
                        help="mediana odpowiedzi usługi z #21 (sekundy) do sumy dla branchmap.json")
    parser.add_argument("--smoke", action="store_true", help="mniej powtórzeń niż 5 i bez oceny kryteriów (testy)")
    args = parser.parse_args(argv)
    if args.repeats < 1 or (args.repeats < MIN_REPEATS and not args.smoke):
        print(f"BŁĄD: --repeats musi być ≥ {MIN_REPEATS}", file=sys.stderr)
        return 1
    if os.environ.get("QT_QPA_PLATFORM") is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    try:
        replies = dataset_replies()
        with tempfile.TemporaryDirectory(prefix="tide-k99-tree-") as tmp:
            pkg = stage_package(os.path.join(tmp, "pkg"))
            view = os.path.join(pkg, "views", "worktrees")
            runner = Runner(os.path.join(view, "qml"), os.path.join(view, "backend.py"))
            results = {name: measure(runner, replies[name], args.repeats) for name in DATASETS}
    except (OSError, RuntimeError, subprocess.CalledProcessError, ImportError) as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr)
        return 1
    result = verdict(results, args.service_median)
    print(json.dumps({"repeats": args.repeats, "datasets": results, "verdict": result}, ensure_ascii=False, indent=2))
    if args.smoke:
        return 0 if result["buildChecksPassed"] else EXIT_FAILED
    return 0 if result["passed"] else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
