#!/usr/bin/env python3
"""Pomiar `GitMonitor.branch_map` dla projektu plasma-tide na prawdziwych danych (K-99, PT-471) — tylko odczyt.

Mapę liczy kod tego worktree (`GitMonitor.branch_map`, ta sama pula co obsługa D-Bus) nad dziennikiem kart otwartym
tylko do odczytu i Tablicą z migawki usługi (`Snapshot`; z usługi nie jest wołane nic innego). Czas liczy się od
wywołania do odpowiedzi z puli (nie do powrotu wywołania). Każde powtórzenie sprawdza, że wywołanie wróciło przed
odpowiedzią, że odpowiedź przyszła w wątku wywołującym, a mapa była liczona w innym wątku.

Dwa przebiegi po `--repeats` (≥ 5) powtórzeń: `cold` — świeży `Journal` w każdym powtórzeniu (pusta pamięć stanów,
najgorszy przypadek, np. pierwsze żądanie po starcie usługi) i `warm` — jeden `Journal` (pamięć po mtime jak w
działającej usłudze). Kryterium K-99: mediana < 1 s, oceniana na przebiegu `cold` (ostrzejszym). Do czasu
odpowiedzi usługi nie należy odczyt Tablicy w wątku głównym (`board_source`) ani budowa drzewa w oknie (#24).

Kod wyjścia: 0 — kryteria spełnione; 1 — błąd; 3 — mediana ≥ 1 s albo wywołanie nie wraca przed odpowiedzią.
"""
import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture_acceptance as cap  # noqa: E402

LIMIT_SECONDS = 1.0
MIN_REPEATS = 5
EXIT_FAILED = 3


def summarize(samples) -> dict:
    """Najlepszy, mediana i najgorszy czas (sekundy)."""
    if not samples:
        raise ValueError("brak próbek")
    return {"best": min(samples), "median": statistics.median(samples), "worst": max(samples), "samples": list(samples)}


def measure(make_host, request: str, repeats: int, shared: bool) -> dict:
    """`make_host()` → host z `monitor`, `pump`, `compute_threads`; `shared` — jeden host dla wszystkich powtórzeń."""
    runs, host = [], make_host() if shared else None
    for _ in range(repeats):
        current = host or make_host()
        try:
            run = cap.time_branch_map(current, request)
        finally:
            if not shared:
                current.close()
        runs.append(run)
    if host:
        host.close()
    stats = summarize([r["seconds"] for r in runs])
    stats.update(returnSeconds=summarize([r["returnSeconds"] for r in runs]),
                 returnedBeforeReply=all(r["returnedBeforeReply"] for r in runs),
                 replyOnCallingThread=all(r["replyOnCallingThread"] for r in runs),
                 computedOffCallingThread=all(r["computedOffCallingThread"] for r in runs),
                 total=runs[-1]["response"].get("total"), ok=all(r["response"].get("ok") for r in runs))
    return stats


def verdict(cold: dict, warm: dict, limit=LIMIT_SECONDS) -> dict:
    proofs = all(r[k] for r in (cold, warm)
                 for k in ("returnedBeforeReply", "replyOnCallingThread", "computedOffCallingThread", "ok"))
    return {"limitSeconds": limit, "coldMedianBelowLimit": cold["median"] < limit,
            "callReturnsBeforeReply": proofs, "passed": cold["median"] < limit and proofs}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--project", default=cap.DEFAULT_PROJECT)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args(argv)
    if args.repeats < MIN_REPEATS:
        print(f"BŁĄD: --repeats musi być ≥ {MIN_REPEATS}", file=sys.stderr)
        return 1
    client = cap.BusClient()
    try:
        cap.require_methods(client)
        snapshot = client.call("Snapshot")
        board_dir = snapshot.get("boardDir") or ""
        if not board_dir:
            raise cap.CaptureError("migawka usługi nie podaje katalogu Tablicy")
        sys.path.insert(0, os.path.join(cap.REPO_ROOT, "daemon"))
        from tide_daemon.cardflow.journal import Journal
        board = cap.board_view_from_snapshot(snapshot)
        request = json.dumps({"project": args.project, "limit": args.limit})
        shared_journal = Journal(board_dir)
        cold = measure(lambda: cap.Host(Journal(board_dir), board), request, args.repeats, shared=False)
        warm = measure(lambda: cap.Host(shared_journal, board), request, args.repeats, shared=True)
    except cap.CaptureError as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr)
        return 1
    result = verdict(cold, warm)
    print(json.dumps({"project": args.project, "repeats": args.repeats, "cold": cold, "warm": warm,
                      "verdict": result, "dbusCalls": [list(c) for c in client.calls]},
                     ensure_ascii=False, indent=2))
    return 0 if result["passed"] else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
