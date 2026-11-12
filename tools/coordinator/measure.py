"""Responsywność usługi po instalacji: GetCardFlowSettings i Snapshot co 0,5 s przez 60 s, p50/p95/max. Tylko odczyt.
Użycie: /usr/bin/python3 tools/coordinator/measure.py  (mierz na spokojnej maszynie, 3–5 min po restarcie usługi)."""
import subprocess, time
B = ["busctl", "--user", "--timeout=60", "--json=short", "call", "org.tide.Daemon", "/Board", "org.tide.Daemon.Board"]
res = {"GetCardFlowSettings": [], "Snapshot": []}
end = time.time() + 60
while time.time() < end:
    for m in res:
        t = time.monotonic(); r = subprocess.run(B + [m], capture_output=True); d = time.monotonic() - t
        res[m].append(d if r.returncode == 0 else float("nan"))
    time.sleep(0.5)
for m, v in res.items():
    ok = sorted(x for x in v if x == x); n = len(ok)
    if not n:
        print(f"{m}: brak udanych odpowiedzi ({len(v)} prób)"); continue
    print(f"{m}: n={len(v)} błędy={len(v) - n} p50={ok[n // 2]:.3f}s p95={ok[max(0, int(n * 0.95) - 1)]:.3f}s max={ok[-1]:.3f}s")
print("loadavg:", open("/proc/loadavg").read().strip())
