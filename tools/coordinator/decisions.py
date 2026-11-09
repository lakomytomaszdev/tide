"""Ludzkie decyzje w przepływie kart: ile pytań system zadał ludziom zamiast rozstrzygnąć sam (masterplan 09.11).

Użycie: /usr/bin/python3 tools/coordinator/decisions.py [--since YYYY-MM-DD] [--days N] [--detail]
Źródło: dzienniki kart Tide (tylko odczyt). Decyzja ludzka = zdarzenie bez `auto`/`automatic` i bez opiekuna:
`step.decision` (zgody, odpowiedzi, ponowienia, odmowy), `review.decision`, `plan.approved`, `plan.revision-approved`,
`plan.returned`, `card.paused`/`card.resumed`, `step.note-added`, `card.moved`, `card.started`.
Przypisanie „koordynator” — gdy w dzienniku interwencji (`docs/agents/interwencje/dziennik.tsv`) jest wpis koordynatora
dla tej karty w oknie ±5 min; pozostałe liczone jako „właściciel”. To przybliżenie, dopóki Tide nie zapisuje źródła
decyzji (panel / D-Bus koordynatora) — punkt do K-70.
"""
import argparse
import collections
import csv
import datetime as dt
import glob
import json
import os

STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "tide", "cardflow")
LOG = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "agents", "interwencje", "dziennik.tsv")
KINDS = {
    "step.decision": "krok",
    "review.decision": "review",
    "plan.approved": "plan",
    "plan.revision-approved": "plan",
    "plan.returned": "plan",
    "card.paused": "pauza",
    "card.resumed": "pauza",
    "step.note-added": "notatka",
    "card.moved": "kolumna",
    "card.started": "start",
}


def board_dir():
    dirs = [d for d in glob.glob(os.path.join(STATE, "*")) if os.path.isdir(d)]
    return max(dirs, key=lambda d: len(glob.glob(os.path.join(d, "K-*.jsonl"))))


def coordinator_marks():
    marks = collections.defaultdict(list)
    try:
        with open(LOG, encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                if (row.get("kto") or "").strip() != "koordynator":
                    continue
                try:
                    when = dt.datetime.fromisoformat(row["czas"]).replace(tzinfo=None)
                except (KeyError, ValueError):
                    continue
                card = (row.get("karta") or "").split("/")[0].strip()
                marks[card].append(when)
    except OSError:
        pass
    return marks


def human(event):
    kind = event.get("type")
    if kind not in KINDS or event.get("auto") or event.get("automatic") or event.get("caretaker"):
        return False
    if kind == "step.decision" and event.get("choice") == "auto":
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--detail", action="store_true")
    args = parser.parse_args()
    since = args.since or (dt.date.today() - dt.timedelta(days=args.days - 1)).isoformat()
    marks = coordinator_marks()
    per_day = collections.defaultdict(collections.Counter)
    detail = collections.defaultdict(collections.Counter)
    auto_day = collections.Counter()
    for path in glob.glob(os.path.join(board_dir(), "K-*.jsonl")):
        card = os.path.basename(path)[:-6]
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                ts = event.get("ts", "")
                if ts < since:
                    continue
                day = ts[:10]
                if event.get("type") == "step.decision" and event.get("auto"):
                    auto_day[day] += 1
                if not human(event):
                    continue
                when = dt.datetime.fromisoformat(ts).replace(tzinfo=None)
                who = "koordynator" if any(abs((when - m).total_seconds()) <= 300 for m in marks.get(card, ())) \
                    else "właściciel"
                kind = KINDS[event["type"]]
                choice = event.get("choice") or ""
                per_day[day][who] += 1
                detail[day][f"{who}:{kind}{':' + choice if choice else ''}"] += 1
    print("dzień       | ludzkie decyzje | właściciel | koordynator | automatyczne zgody kroków")
    for day in sorted(set(per_day) | set(auto_day)):
        c = per_day[day]
        print(f"{day} | {sum(c.values()):>15} | {c['właściciel']:>10} | {c['koordynator']:>11} | {auto_day[day]:>6}")
        if args.detail:
            for key, n in detail[day].most_common():
                print(f"             {n:>4}  {key}")
    print("\nCel: liczba ludzkich decyzji spada z tygodnia na tydzień; każda powtarzalna decyzja ma kartę, która ją usuwa"
          " (kolumna „co by je usunęło” w dzienniku interwencji).")


if __name__ == "__main__":
    main()
