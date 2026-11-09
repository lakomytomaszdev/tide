"""Strażnik nocny: kończy się (budzi koordynatora), gdy pojawi się nowa decyzja, karta zmieni kolumnę
na review/waiting albo minie limit czasu (puls). Widziane decyzje w seen.json."""
import json, os, subprocess, sys, time
CARDS = ("K-69", "K-78", "K-88", "K-82", "K-83", "K-75", "K-79", "K-56", "K-76")
SEEN = os.path.join(os.environ.get("TIDE_WATCH_STATE") or os.path.dirname(__file__), "seen.json")
deadline = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 3000)
seen = set(json.load(open(SEEN))) if os.path.exists(SEEN) else set()

def snapshot():
    out = subprocess.run(["busctl", "--user", "--json=short", "call", "org.tide.Daemon", "/Board",
                          "org.tide.Daemon.Board", "Snapshot"], capture_output=True, text=True, timeout=60).stdout
    data = json.loads(json.loads(out)["data"][0], strict=False)
    cards = data.get("cards") or data.get("board", {}).get("cards")
    return {c["id"]: c for c in (cards.values() if isinstance(cards, dict) else cards) if c.get("id") in CARDS}

while time.time() < deadline:
    try:
        cards = snapshot()
    except Exception as exc:
        print("snapshot:", exc); time.sleep(60); continue
    events = []
    # Opiekun karty (K-37): każde jego zdarzenie budzi koordynatora — obserwacja nowej funkcji.
    for cid in CARDS:
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
        col = d.get("column") or c.get("column")
        key = f"{cid}:col:{col}:{d.get('note','')[:40]}"
        if col in ("review", "waiting", "done") and key not in seen:
            events.append(f"{cid} KOLUMNA {col}: {d.get('note','')[:200]}")
            seen.add(key)
    if events:
        json.dump(sorted(seen), open(SEEN, "w"))
        print("\n".join(events)); sys.exit(0)
    time.sleep(90)
json.dump(sorted(seen), open(SEEN, "w"))
print("PULS: brak nowych zdarzeń")
