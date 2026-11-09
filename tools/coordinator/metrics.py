"""Pięć liczb sterujących równoległością (masterplan 09.11) z dzienników kart Tide.

Użycie: /usr/bin/python3 tools/coordinator/metrics.py [--since YYYY-MM-DD] [--days N]
Liczy dla każdego dnia: porażki pełnych przebiegów testów poza piaskownicą (bez celowo czerwonych), przebiegi
„padło → przeszło bez zmian kodu”, nieudane scalenia, p75 czasu ticketu (gałąź → scalenie), scalenia na godzinę
kroków agentów oraz liczbę jednocześnie trwających ciężkich przebiegów (maksimum). Tylko odczyt.
Tempo limitu Claude nie jest w dziennikach — podaje je koordynator z /usage.
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import re

STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "tide", "cardflow")
RED = re.compile(r"czerwon|\bred\b|przed zmian|przed poprawk|na bazie|bez poprawki|odtw[oó]rz|reprodu|dowód porażki", re.I)
RERUN = re.compile(r"bez zmian|ten sam HEAD|ponow|niestabil|losow|obciąż|flaky|powtórz", re.I)
HEAVY = re.compile(r"make test\b|test-changed|dotnet test|test_runner|pytest|npm (run )?test|gate\.sh")


def board_dir():
    """Katalog dzienników Tablicy: ten z największą liczbą kart (pozostałe to ślady starych testów sprzed izolacji)."""
    dirs = [d for d in glob.glob(os.path.join(STATE, "*")) if os.path.isdir(d)]
    return max(dirs, key=lambda d: len(glob.glob(os.path.join(d, "K-*.jsonl"))))


def events(since):
    for path in glob.glob(os.path.join(board_dir(), "K-*.jsonl")):
        card = os.path.basename(path)[:-6]
        rows = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("ts", "") >= since:
                    rows.append(event)
        yield card, rows


def p75(values):
    values = sorted(values)
    return values[int(0.75 * (len(values) - 1))] if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since")
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()
    since = args.since or (dt.date.today() - dt.timedelta(days=args.days - 1)).isoformat()
    day = collections.defaultdict(lambda: collections.Counter())
    lead = collections.defaultdict(list)
    spans = collections.defaultdict(list)
    for card, rows in events(since):
        reasons, started, outside, branch_at, runs = {}, {}, {}, {}, []
        for e in rows:
            t = dt.datetime.fromisoformat(e["ts"])
            d = e["ts"][:10]
            kind = e.get("type")
            if kind == "step.started":
                started[e.get("step")] = t
            elif kind == "step.finished":
                reasons[e.get("step")] = (e.get("reason") or "") + " " + (e.get("question") or "")
                if e.get("step") in started:
                    day[d]["agent_s"] += (t - started.pop(e["step"])).total_seconds()
            elif kind == "outside.started":
                outside[e.get("step")] = t
            elif kind == "outside.finished":
                cmd = e.get("command") or ""
                if not HEAVY.search(cmd):
                    continue
                begin = outside.pop(e.get("step"), None)
                if begin:
                    spans[d].append((begin, t))
                red = bool(RED.search(reasons.get(e.get("step"), "")))
                runs.append({"d": d, "ticket": (e.get("step") or "").rsplit("-", 1)[0], "cmd": cmd,
                             "ok": e.get("exit") in (0, "0"), "red": red, "step": e.get("step")})
            elif kind == "result" and e.get("action") == "ticket-branch" and e.get("ok"):
                branch_at[e.get("key")] = t
            elif kind == "result" and e.get("action") == "merge":
                if e.get("ok"):
                    day[d]["merged"] += 1
                    if e.get("key") in branch_at:
                        lead[d].append((t - branch_at[e["key"]]).total_seconds() / 3600)
                else:
                    day[d]["merge_failed"] += 1
        for i, run in enumerate(runs):
            if run["red"]:
                day[run["d"]]["red"] += 1
                continue
            day[run["d"]]["runs"] += 1
            if not run["ok"]:
                day[run["d"]]["failed"] += 1
                nxt = next((r for r in runs[i + 1:] if r["ticket"] == run["ticket"] and r["cmd"] == run["cmd"]), None)
                if nxt and nxt["ok"] and RERUN.search(reasons.get(nxt["step"], "")):
                    day[run["d"]]["flaky"] += 1
    print("dzień       | pełne testy (bez czerwonych) | porażki | padło→przeszło | maks. naraz | scalenia | nieudane"
          " scalenia | p75 ticketu | scalenia/h agentów")
    for d in sorted(day):
        c = day[d]
        points = sorted([(a, 1) for a, _ in spans[d]] + [(b, -1) for _, b in spans[d]])
        level = peak = 0
        for _, step in points:
            level += step
            peak = max(peak, level)
        share = f"{round(100 * c['failed'] / c['runs'])}%" if c["runs"] else "-"
        q = p75(lead[d])
        rate = c["merged"] / (c["agent_s"] / 3600) if c["agent_s"] else 0
        print(f"{d} | {c['runs']:>4} (+{c['red']} czerw.) | {share:>6} | {c['flaky']:>3} | {peak:>3} | {c['merged']:>4} |"
              f" {c['merge_failed']:>3} | {('%.1f h' % q) if q is not None else '-':>6} | {rate:.2f}")
    print("\nProgi: zwiększ — porażki <35%, nieudane scalenia ≤2, p75 <2 h, ≥1,0 scalenia/h (2 dni z rzędu);"
          " zmniejsz — porażki >60% albo ≥3 padło→przeszło, nieudane scalenia ≥5, p75 >4 h, <0,7 scalenia/h."
          " Limit ciężkich przebiegów naraz: 2.")


if __name__ == "__main__":
    main()
