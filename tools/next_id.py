#!/usr/bin/python3
"""Następny lokalny numer ticketu — z rezerwacją, żeby równoległe sesje nie wzięły tego samego.

Użycie: /usr/bin/python3 tools/next_id.py REPOZYTORIUM [--prefix PT] [--count N] [--title "…"] [--dry-run]
        make next-id REPO=… [PREFIX=PT] [COUNT=N] [TITLE="…"] [DRY_RUN=1]

Pod blokadą flock na rejestrze $XDG_STATE_HOME/tide/ticket-ids.json (domyślnie ~/.local/state/tide/,
0600) bierze największy numer z rejestru (repozytorium + prefiks) i ze wszystkich planów repozytorium
(główny katalog, każdy worktree, każda gałąź lokalna), rezerwuje kolejne N numerów z tytułem i czasem
i wypisuje ID, po jednym w linii. Prefiks domyślnie z CLAUDE.md/AGENTS.md (sekcja „Plan pracy agentów
(Tide)”). Prefiks z YouTracka — odmowa: numer nadaje YouTrack, ticket zakładasz tam.

--dry-run  pokazuje, jakie numery by dostał, bez zapisu (i bez tworzenia rejestru).

Kod wyjścia: 0 — numery wypisane, 1 — odmowa albo błąd (komunikat na stderr).
Reguły: docs/agent-plan-standard.md, „Numery ticketów: jedno źródło”; kod: daemon/tide_daemon/
coordination/reservations.py.
"""
import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "daemon"))

from tide_daemon.coordination import reservations  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rezerwacja lokalnych numerów ticketów (Tide).")
    parser.add_argument("repo", help="katalog repozytorium albo jego worktree")
    parser.add_argument("--prefix", default="", help="prefiks ticketów (domyślnie z CLAUDE.md/AGENTS.md)")
    parser.add_argument("--count", type=int, default=1, help="ile kolejnych numerów (domyślnie 1)")
    parser.add_argument("--title", default="", help="tytuł ticketu (zapisany w rezerwacji)")
    parser.add_argument("--dry-run", action="store_true", help="tylko pokaż, bez rezerwacji")
    args = parser.parse_args(argv)
    try:
        result = reservations.reserve(os.path.abspath(os.path.expanduser(args.repo)), args.prefix, args.count,
                                      args.title, dry_run=args.dry_run)
    except reservations.ReservationError as exc:
        print(f"next_id: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"next_id: rejestr numerów niedostępny: {exc.strerror or exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        source = {"rejestr": "z rejestru", "plany": "z planów", "brak": "bez wcześniejszych numerów"}[result["seed"]]
        print(f"(--dry-run, nic nie zarezerwowano; największy dotychczas {source}; repozytorium {result['repo']})",
              file=sys.stderr)
    for ident in result["ids"]:
        print(ident)
    return 0


if __name__ == "__main__":
    sys.exit(main())
