"""Strażnik nocny: kończy się (budzi koordynatora), gdy pojawi się nowa decyzja, karta zmieni kolumnę
na review/waiting albo minie limit czasu (puls). Widziane decyzje w seen.json."""
import json, os, subprocess, sys, time
# Obserwowane są wszystkie karty w toku (kolumna inna niż backlog i done) oraz każda, która kiedyś była w toku,
# aż do zgłoszenia jej przejścia do done. Lista na sztywno przegapiała nowe karty (K-98, 11.11).
ACTIVE = ("todo", "doing", "review", "waiting")
STATE = os.environ.get("TIDE_WATCH_STATE") or os.path.dirname(__file__)
SEEN = os.path.join(STATE, "seen.json")
WATCHED = os.path.join(STATE, "watched.json")
deadline = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 3000)
seen = set(json.load(open(SEEN))) if os.path.exists(SEEN) else set()
watched = set(json.load(open(WATCHED))) if os.path.exists(WATCHED) else set()

def column(c):
    return (c.get("derived") or {}).get("column") or c.get("column")

def snapshot():
    out = subprocess.run(["busctl", "--user", "--json=short", "call", "org.tide.Daemon", "/Board",
                          "org.tide.Daemon.Board", "Snapshot"], capture_output=True, text=True, timeout=60).stdout
    data = json.loads(json.loads(out)["data"][0], strict=False)
    cards = data.get("cards") or data.get("board", {}).get("cards")
    every = list(cards.values() if isinstance(cards, dict) else cards)
    watched.update(c["id"] for c in every if column(c) in ACTIVE)
    return {c["id"]: c for c in every if c.get("id") in watched}

while time.time() < deadline:
    try:
        cards = snapshot()
    except Exception as exc:
        print("snapshot:", exc); time.sleep(60); continue
    events = []
    # Opiekun karty (K-37): każde jego zdarzenie budzi koordynatora — obserwacja nowej funkcji.
    for cid in sorted(cards):
        try:
            for line in open(os.path.expanduser(f"~/DevWorkspace/tide-board/flow/{cid}/journal.jsonl")):
                e = json.loads(line)
                if e.get("type", "") in ("caretaker.action", "caretaker.escalated", "caretaker.reported", "caretaker.failed"):
                    key = f"{cid}:care:{e.get('seq')}"
                    if key not in seen:
                        seen.add(key)
                        if e.get("ts", "") >= "2026-11-06T12:40":
                            events.append(f"{cid} OPIEKUN {e.get('type')} {e.get('ticket','')} "
                                          f"{str(e.get('summary') or e.get('reason') or e.get('action') or e.get('target') or '')[:300]}")
        except Exception:
            pass
    for cid, c in cards.items():
        d = c.get("derived") or {}
        for dec in (d.get("flow") or {}).get("decisions") or []:
            key = f"{cid}:{dec.get('id')}"
            if key not in seen:
                events.append(f"{cid} DECYZJA {dec.get('kind')} {dec.get('ticket')}: {(dec.get('happened') or '')[:300]}")
                seen.add(key)
        note = d.get("note", "")
        if "czeka na limit" in note:
            key = f"{cid}:limit:{note[:60]}"
            if key not in seen:
                events.append(f"{cid} LIMIT: {note[:200]}")
                seen.add(key)
        col = column(c)
        key = f"{cid}:col:{col}:{d.get('note','')[:40]}"
        if col in ("review", "waiting", "done") and key not in seen:
            events.append(f"{cid} KOLUMNA {col}: {d.get('note','')[:200]}")
            seen.add(key)
        if col == "done" and key in seen:
            watched.discard(cid)
    json.dump(sorted(watched), open(WATCHED, "w"))
    if events:
        json.dump(sorted(seen), open(SEEN, "w"))
        print("\n".join(events)); sys.exit(0)
    time.sleep(90)
json.dump(sorted(seen), open(SEEN, "w"))
print("PULS: brak nowych zdarzeń")
