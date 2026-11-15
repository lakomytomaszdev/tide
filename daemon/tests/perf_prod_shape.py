#!/usr/bin/python3
"""Kształt Tablicy produkcji dla reproduktora CPU postoju (K-125, PT-486): same liczby, tylko odczyt.

Czyta dzienniki kart `<tablica>/flow/K-*/journal.jsonl` (domyślnie `~/DevWorkspace/tide-board`) i zapisuje do pliku JSON
**wyłącznie liczby i znaczniki** każdej karty — rozmiar dziennika, liczbę zdarzeń i kroków oraz stan policzony tym samym
`state.fold` co usługa (zakończona, wstrzymana, krok w toku, trzymany proces i liczba trzymanych kroków, zatwierdzony
plan, prośby outside bez decyzji, decyzje `permission`/`question`). Nie zapisuje treści zdarzeń, tytułów, poleceń ani
ścieżek (w dziennikach są dane innych projektów) — plik kształtu można bezpiecznie oglądać; do repozytorium nie trafia
(katalog `build/`, ignorowany przez git). Niczego nie zmienia w Tablicy.

    /usr/bin/python3 daemon/tests/perf_prod_shape.py [--board ~/DevWorkspace/tide-board] [--out build/prod-shape.json]

Reproduktor (`perf_idle_scale.populate(shape=…)`) generuje na tej podstawie dane syntetyczne o tych samych rozmiarach
i stanach (liczba kart, rozmiar każdego dziennika, role).
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "daemon"))


def card_shape(card, events, size):
    """Liczby i znaczniki jednej karty z jej zdarzeń (lista słowników); bez treści."""
    from tide_daemon.cardflow import decisions, state
    st = state.fold(events)
    kinds = [d.get("kind") for d in decisions.build_decisions(st)]
    held = [name for name, identity in st.process_identities.items() if identity.held]
    undecided = [name for name, done in st.finished.items()
                 if done.get("outside") and done.get("result") == "denied" and name not in st.step_decisions]
    column = ""
    for event in events:
        if event.get("type") == "card.finished":
            column = str(event.get("column") or "")
    return {"card": card, "bytes": size, "events": len(events),
            "steps": sum(1 for e in events if e.get("type") == "step.started"),
            "finished": bool(st.card_finished), "column": column if st.card_finished else "",
            "paused": bool(st.paused), "running": bool(st.running()), "held": bool(held), "heldSteps": len(held),
            "approved": st.plan_approved is not None, "undecidedOutside": len(undecided),
            "permission": kinds.count("permission"), "question": kinds.count("question")}


def read_events(path):
    events, bad = [], 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(event, dict):
                events.append(event)
    return events, bad


def collect(board):
    """Kształt całej Tablicy: lista kart (po numerze) i sumy. Karta, której nie da się złożyć, trafia jako `unreadable`."""
    flow = os.path.join(board, "flow")
    cards, unreadable = [], []
    names = sorted((n for n in os.listdir(flow) if n.startswith("K-") and n[2:].isdigit()), key=lambda n: int(n[2:])) \
        if os.path.isdir(flow) else []
    for name in names:
        path = os.path.join(flow, name, "journal.jsonl")
        if not os.path.isfile(path):
            continue
        try:
            events, bad = read_events(path)
            shape = card_shape(name, events, os.path.getsize(path))
            shape["badLines"] = bad
            cards.append(shape)
        except Exception as exc:                              # jeden dziennik nie zatrzymuje reszty
            unreadable.append({"card": name, "bytes": os.path.getsize(path), "error": type(exc).__name__})
    return {"kind": "tide-prod-shape", "cards": cards, "unreadable": unreadable, "totals": totals(cards, unreadable)}


def totals(cards, unreadable=()):
    return {"cards": len(cards), "unreadable": len(unreadable),
            "bytes": sum(c["bytes"] for c in cards) + sum(u["bytes"] for u in unreadable),
            "events": sum(c["events"] for c in cards), "steps": sum(c["steps"] for c in cards),
            "finished": sum(c["finished"] for c in cards), "paused": sum(c["paused"] for c in cards),
            "held": sum(c["held"] for c in cards), "approved": sum(c["approved"] and not c["finished"] for c in cards),
            "undecidedOutside": sum(c["undecidedOutside"] for c in cards),
            "largest": sorted(((c["card"], c["bytes"]) for c in cards), key=lambda item: -item[1])[:5]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--board", default=os.path.expanduser("~/DevWorkspace/tide-board"))
    parser.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(HERE)), "build", "prod-shape.json"))
    args = parser.parse_args(argv)
    shape = collect(args.board)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(shape, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(json.dumps(shape["totals"], ensure_ascii=False), flush=True)
    return 0 if shape["cards"] else 1


if __name__ == "__main__":
    sys.exit(main())
