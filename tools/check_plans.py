#!/usr/bin/python3
"""Kontrola numerów ticketów w planach agentów wszystkich repozytoriów workspace (tylko odczyt).

Użycie: /usr/bin/python3 tools/check_plans.py [KATALOG-WORKSPACE] [--no-youtrack] [--json]
        make check-plans [PLANS_WORKSPACE=~/DevWorkspace]

Dla każdego repozytorium w katalogu (domyślnie ~/DevWorkspace, jeden poziom w głąb; worktree liczą
się przy swoim repozytorium) czyta plany z `docs/agents/plans/` głównego katalogu, każdego worktree
(także niezacommitowane) i każdej gałęzi lokalnej, a z CLAUDE.md/AGENTS.md (sekcja „Plan pracy agentów
(Tide)”) prefiks, tracker i projekt. Gdy któreś repozytorium ma plany lokalne, pobiera listę projektów
YouTracka (GET /api/admin/projects, token z pliku jak tide-daemon — nigdy go nie wypisujemy). Rezerwacje
numerów (tools/next_id.py, $XDG_STATE_HOME/tide/ticket-ids.json) czyta bez blokady, tylko do odczytu:
numer w planie zarezerwowany dla innego tytułu to kolizja.

Reguły: docs/agent-plan-standard.md, „Numery ticketów: jedno źródło”; kontrola: daemon/tide_daemon/
coordination/numbering.py (ta sama, którą pokazuje tablica Tide Sesje i raport poranny).

Kod wyjścia: 0 — bez kolizji twardych (łagodne ostrzeżenia są tylko wypisane), 1 — kolizja twarda,
2 — zły katalog.
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON = os.path.join(REPO, "daemon")
if not os.path.isdir(os.path.join(DAEMON, "tide_daemon")):
    DAEMON = os.path.join(os.path.dirname(REPO), "daemon")
sys.path.insert(0, DAEMON)

from tide_daemon.coordination import numbering, repoplans  # noqa: E402
from tide_daemon.coordination.repoplans import RepoPlans  # noqa: E402

LABELS = {numbering.HARD: "KOLIZJA", numbering.SOFT: "uwaga"}


def repositories(workspace: str) -> list[str]:
    """Główne katalogi repozytoriów (z katalogiem .git; worktree mają plik .git i liczą się przy repozytorium)."""
    out = []
    try:
        names = sorted(os.listdir(workspace), key=str.lower)
    except OSError:
        return out
    for name in names:
        path = os.path.join(workspace, name)
        if not name.startswith(".") and os.path.isdir(os.path.join(path, ".git")):
            out.append(path)
    return out


def check(workspace: str, tracker=None, repo_plans=None, registry: str | None = None) -> list[dict]:
    """[{repo, plans, declared, warnings, youtrack}] dla repozytoriów, które mają plany albo deklarację.
    Kontrola ta sama co na tablicy (repoplans.check): plany, deklaracja, rezerwacje numerów, YouTrack."""
    repo_plans = repo_plans or RepoPlans(ttl=600)  # jeden odczyt repozytorium na całe uruchomienie
    found = []
    for path in repositories(workspace):
        plans, declared = repo_plans.read([path])
        if plans or declared:
            found.append({"repo": path, "plans": plans, "declared": declared})
    projects, error = None, "bez YouTracka (--no-youtrack)"
    if tracker is not None and any(_local(r) for r in found):
        projects, error = tracker.projects()
    for item in found:
        item["warnings"] = repoplans.check([item["repo"]], repo_plans, tracker, registry=registry)
        item["youtrack"] = "" if projects is not None or not _local(item) else error or "brak listy projektów"
    return found


def report(results: list, workspace: str) -> str:
    lines = []
    for item in results:
        name = os.path.relpath(item["repo"], workspace)
        declared = item["declared"] or {}
        files = sorted({os.path.basename(p.path) for p in item["plans"]})
        about = f"{len(files)} " + ("plan" if len(files) == 1 else "plany" if 2 <= len(files) % 10 <= 4
                                     and not 12 <= len(files) % 100 <= 14 else "planów")
        if declared.get("prefix"):
            about += f", prefiks {declared['prefix']} ({declared.get('tracker') or '?'}"
            about += f" {declared['project']})" if declared.get("project") else ")"
        lines.append(f"{name}: {about}" + ("" if item["warnings"] else " — OK"))
        if item["youtrack"]:
            lines.append(f"  (prefiks lokalny niesprawdzony w YouTracku: {item['youtrack']})")
        for warning in item["warnings"]:
            lines.append(f"  [{LABELS.get(warning['severity'], warning['severity'])}] {warning['text']}")
    hard = sum(len(numbering.hard(i["warnings"])) for i in results)
    soft = sum(len(i["warnings"]) for i in results) - hard
    lines.append("")
    lines.append(f"Repozytoria z planami: {len(results)}, kolizje twarde: {hard}, ostrzeżenia łagodne: {soft}.")
    return "\n".join(lines)


def _local(item: dict) -> bool:
    return any(p.tracker == "local" for p in item["plans"]) or (item["declared"] or {}).get("tracker") == "local"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Kontrola numerów ticketów w planach agentów (Tide).")
    parser.add_argument("workspace", nargs="?", default=os.path.expanduser("~/DevWorkspace"))
    parser.add_argument("--no-youtrack", action="store_true", help="bez zapytania o listę projektów YouTracka")
    parser.add_argument("--json", action="store_true", help="wynik jako JSON")
    args = parser.parse_args(argv)
    workspace = os.path.abspath(os.path.expanduser(args.workspace))
    if not os.path.isdir(workspace):
        print(f"check_plans: nie ma katalogu {workspace}", file=sys.stderr)
        return 2
    tracker = None
    if not args.no_youtrack:
        from tide_daemon.coordination.youtrack import YouTrackCache
        tracker = YouTrackCache()
    results = check(workspace, tracker)
    if args.json:
        print(json.dumps([{"repo": i["repo"], "plans": sorted({p.path for p in i["plans"]}),
                           "declared": i["declared"], "youtrack": i["youtrack"], "warnings": i["warnings"]}
                          for i in results], ensure_ascii=False, indent=2))
    else:
        print(report(results, workspace))
    return 1 if any(numbering.hard(i["warnings"]) for i in results) else 0


if __name__ == "__main__":
    sys.exit(main())
