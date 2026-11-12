"""Pięć liczb sterujących równoległością (masterplan 09.11) z dzienników kart Tide.

Użycie: /usr/bin/python3 tools/coordinator/metrics.py [--since YYYY-MM-DD] [--days N]
Liczy dla każdego dnia: porażki pełnych przebiegów testów poza piaskownicą (bez celowo czerwonych), przebiegi
„padło → przeszło bez zmian kodu”, nieudane scalenia, p75 czasu ticketu (gałąź → scalenie), scalenia na godzinę
kroków agentów oraz liczbę jednocześnie trwających ciężkich przebiegów (maksimum). Do tego (K-100, PT-422):
udział czerwonych (kod ≠ 0) wśród WSZYSTKICH przebiegów `outside` (HEAVY, także celowo czerwonych — definicja danych
Fable) i liczbę decyzji „odstępstwo/ponowienie” na dobę z dziennika interwencji (`--log` albo `--log-rev REV` z gita; domyślnie plik
z checkoutu narzędzia, który w worktree ticketu bywa starszy — stopka podaje źródło). Tylko odczyt.
Tempo limitu Claude nie jest w dziennikach — podaje je koordynator z /usage.
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import subprocess

STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "tide", "cardflow")
RED = re.compile(r"czerwon|\bred\b|przed zmian|przed poprawk|na bazie|bez poprawki|odtw[oó]rz|reprodu|dowód porażki", re.I)
RERUN = re.compile(r"bez zmian|ten sam HEAD|ponow|niestabil|losow|obciąż|flaky|powtórz", re.I)
HEAVY = re.compile(r"make test\b|test-changed|dotnet test|test_runner|pytest|npm (run )?test|gate\.sh")


LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "agents", "interwencje", "dziennik.tsv")
# Decyzja „odstępstwo/ponowienie” (K-100): wpis rodzaju zgoda/decyzja, którego treść dotyczy testów (TESTS) i jednocześnie
# odstępstwa od ich wyniku albo ponowienia przebiegu (DEVIATION). Zwykła zgoda na polecenie bez testów nie pasuje.
# Rodzaje złożone (`zgoda/decyzja`, `błąd+decyzja`) rozbijamy po `/` i `+`. `odpowiedź`/`odmowa` (nawet z „odstępstwo
# przyjęte”) są poza regułą: to odpowiedzi na pytania agenta, nie decyzje koordynatora o wyniku testów.
DECISION_KINDS = ("zgoda", "decyzja")
TESTS = re.compile(r"test|przebieg|porażk|niestabiln|\bexit\b", re.I)
DEVIATION = re.compile(r"odstępstw|ponowien|ponown|retry|osobno \d+\s*[x/×]|znan(y|a|e|ych) niestabiln|przechodzi osobno|"
                       r"bez związku|zaakceptowan", re.I)


def is_deviation_decision(kind, text):
    """Reguła jawna: rodzaj (po rozbiciu na `/`, `+`) ∋ {zgoda, decyzja} ∧ treść mówi o testach ∧ o odstępstwie/ponowieniu."""
    parts = re.split(r"[/+]", kind)
    return any(k.strip() in DECISION_KINDS for k in parts) and bool(TESTS.search(text)) and bool(DEVIATION.search(text))


def log_lines(path, rev=None):
    """Wiersze dziennika interwencji: z pliku `path` albo (`rev`) z `git show REV:docs/agents/interwencje/dziennik.tsv`."""
    if rev:
        repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
        done = subprocess.run(["git", "-C", repo, "show", f"{rev}:docs/agents/interwencje/dziennik.tsv"],
                              capture_output=True, text=True, encoding="utf-8")
        return done.stdout.splitlines(True) if done.returncode == 0 else []
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readlines()
    except OSError:
        return []


def deviation_decisions(lines, since):
    """Liczba decyzji odstępstwo/ponowienie na dobę (RRRR-MM-DD) z wierszy TSV; brak wierszy = pusty wynik."""
    counts = collections.Counter()
    lines = iter(lines)
    header = next(lines, "").rstrip("\n").split("\t")
    if not {"czas", "rodzaj", "co"} <= set(header):
        return counts
    at, kind, what = header.index("czas"), header.index("rodzaj"), header.index("co")
    why = header.index("przyczyna") if "przyczyna" in header else None
    for line in lines:
        cols = line.rstrip("\n").split("\t")
        if len(cols) <= max(at, kind, what) or cols[at][:10] < since:
            continue
        text = cols[what] + (" " + cols[why] if why is not None and len(cols) > why else "")
        if is_deviation_decision(cols[kind], text):
            counts[cols[at][:10]] += 1
    return counts


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
    parser.add_argument("--log", default=LOG, help="dziennik interwencji (TSV) do decyzji odstępstwo/ponowienie")
    parser.add_argument("--log-rev", help="zamiast --log: dziennik interwencji z tej rewizji gita (np. gałęzi koordynacji;"
                                          " dziennik w checkoucie ticketu bywa starszy)")
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
            day[run["d"]]["all_runs"] += 1
            day[run["d"]]["all_failed"] += 0 if run["ok"] else 1
            if run["red"]:
                day[run["d"]]["red"] += 1
                continue
            day[run["d"]]["runs"] += 1
            if not run["ok"]:
                day[run["d"]]["failed"] += 1
                nxt = next((r for r in runs[i + 1:] if r["ticket"] == run["ticket"] and r["cmd"] == run["cmd"]), None)
                if nxt and nxt["ok"] and RERUN.search(reasons.get(nxt["step"], "")):
                    day[run["d"]]["flaky"] += 1
    decisions = deviation_decisions(log_lines(args.log, args.log_rev), since)
    for d in decisions:
        day[d]  # dzień bez przebiegów też dostaje wiersz
    print("dzień       | pełne testy (bez czerwonych) | porażki | padło→przeszło | maks. naraz | scalenia | nieudane"
          " scalenia | p75 ticketu | scalenia/h agentów | czerwone poza piaskownicą: wszystkie | bez celowo czerwonych |"
          " decyzje odstępstwo/ponowienie")
    for d in sorted(day):
        c = day[d]
        points = sorted([(a, 1) for a, _ in spans[d]] + [(b, -1) for _, b in spans[d]])
        level = peak = 0
        for _, step in points:
            level += step
            peak = max(peak, level)
        share = f"{round(100 * c['failed'] / c['runs'])}%" if c["runs"] else "-"
        all_share = f"{round(100 * c['all_failed'] / c['all_runs'])}%" if c["all_runs"] else "-"
        q = p75(lead[d])
        rate = c["merged"] / (c["agent_s"] / 3600) if c["agent_s"] else 0
        print(f"{d} | {c['runs']:>4} (+{c['red']} czerw.) | {share:>6} | {c['flaky']:>3} | {peak:>3} | {c['merged']:>4} |"
              f" {c['merge_failed']:>3} | {('%.1f h' % q) if q is not None else '-':>6} | {rate:.2f} |"
              f" {all_share} ({c['all_failed']}/{c['all_runs']}) | {share} ({c['failed']}/{c['runs']}) | {decisions[d]}")
    print("\nProgi: zwiększ — porażki <35%, nieudane scalenia ≤2, p75 <2 h, ≥1,0 scalenia/h (2 dni z rzędu);"
          " zmniejsz — porażki >60% albo ≥3 padło→przeszło, nieudane scalenia ≥5, p75 >4 h, <0,7 scalenia/h."
          " Limit ciężkich przebiegów naraz: 2.\nCel K-100: czerwone poza piaskownicą <35% (wszystkie i bez celowo"
          " czerwonych; przed kartą ~69%) oraz <10 decyzji odstępstwo/ponowienie na dobę (przed kartą ~37).\n"
          f"Decyzje liczono z: {('git ' + args.log_rev) if args.log_rev else os.path.normpath(args.log)}")


if __name__ == "__main__":
    main()
