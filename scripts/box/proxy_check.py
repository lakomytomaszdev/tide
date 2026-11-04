#!/usr/bin/env python3
"""Ręczna próba proxy usługi (PT-168) z kontenera prób Incusa.

Uruchamia proxy z kodu usługi (`tide_daemon.box.proxy.start_service`, adres mostka z `incus network get`, port
z argumentu), ustawia politykę dla adresu kontenera (domeny modeli + szablon `base`) i woła z kontenera:

1. `curl https://registry.npmjs.org/` przez proxy → oczekiwane 200,
2. `curl https://example.com` przez proxy → oczekiwana odmowa 403 i wiersz w dzienniku odmów,
3. `curl http://<mostek>:<port>/api/tags` (bez proxy w kliencie) przy `ollama=True` → lista modeli Ollamy hosta,
4. to samo po `set_box_policy(..., ollama=False)` bez restartu proxy → 403.

Wynik: `docs/box-prototype/pt168-<czas>.log` i dziennik odmów obok. Niczego nie zmienia w nftables ani w kontenerze.
Wymaga: działającego kontenera (domyślnie `tide-proto-net` z PT-178), wolnego portu (usługa z `box.enabled` musi
być zatrzymana albo bez tego ustawienia), Ollamy na `127.0.0.1:11434`.

    bash scripts/box/proxy-check.sh [--box tide-proto-net] [--port 18080]
"""
import argparse
import datetime as dt
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "daemon"))

from tide_daemon.box import proxy as bx  # noqa: E402
from tide_daemon.box import settings as bs  # noqa: E402


def main(argv) -> int:
    parser = argparse.ArgumentParser(description="Próba proxy pudełka PT-168 z kontenera Incusa")
    parser.add_argument("--box", default=os.environ.get("PROTO_BOX", "tide-proto-net"))
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args(argv)

    stamp = dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    out_dir = os.path.join(REPO, "docs", "box-prototype")
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, f"pt168-{stamp}.log")
    denied = os.path.join(out_dir, f"pt168-{stamp}", "proxy-denied.log")
    log = open(log_path, "w", encoding="utf-8")

    def say(text=""):
        print(text, flush=True)
        log.write(text + "\n")
        log.flush()

    def sh(argv, timeout=60):
        say("$ " + " ".join(argv))
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        for line in (done.stdout + done.stderr).splitlines()[:40]:
            say("   " + line)
        say(f"-> kod {done.returncode}")
        return done

    say(f"# próba PT-168 — {dt.datetime.now().astimezone().isoformat(timespec='seconds')} — kontener {args.box}")
    state = sh(["incus", "list", args.box, "-c", "s4", "-f", "csv"])
    fields = state.stdout.strip().split(",", 1)
    if state.returncode != 0 or not fields[0] == "RUNNING":
        say(f"Kontener {args.box} nie działa — uruchom go (PT-178: scripts/box/prototype) i powtórz.")
        return 1
    box_ip = fields[1].split(" ", 1)[0] if len(fields) > 1 else ""
    if not box_ip:
        say("Kontener nie ma adresu IPv4.")
        return 1

    service = bx.start_service(bs.BoxSettings(enabled=True, proxy_port=args.port), log=say)
    if service is None:
        say("Proxy nie wystartowało (brak incus albo mostka).")
        return 1
    bridge = service.listen
    composed = bx.compose_policy(None, log=say)
    service.set_box_policy(box_ip, composed.domains, True, denied, box=args.box)
    say(f"polityka {box_ip}: {len(composed.domains)} domen, ollama=True, dziennik {os.path.relpath(denied, REPO)}")

    proxy = f"http://{bridge}:{args.port}"
    code = ["-sS", "-o", "/dev/null", "-w", "%{http_code}\\n", "--max-time", "30"]
    results = {}
    r = sh(["incus", "exec", args.box, "--", "curl", *code, "-x", proxy, "https://registry.npmjs.org/"])
    results["npm"] = r.returncode == 0 and r.stdout.strip() == "200"
    r = sh(["incus", "exec", args.box, "--", "curl", *code, "-x", proxy, "https://example.com"])
    results["example"] = r.returncode != 0 and "403" in r.stdout + r.stderr
    r = sh(["incus", "exec", args.box, "--", "curl", "-sS", "--noproxy", "*", "--max-time", "30",
            f"http://{bridge}:{args.port}/api/tags"])
    results["ollama-true"] = r.returncode == 0 and '"models"' in r.stdout
    service.set_box_policy(box_ip, composed.domains, False, denied, box=args.box)
    say("polityka odświeżona bez restartu: ollama=False")
    r = sh(["incus", "exec", args.box, "--", "curl", *code, "--noproxy", "*", f"http://{bridge}:{args.port}/api/tags"])
    results["ollama-false"] = r.stdout.strip() == "403"
    service.stop()

    say()
    say("dziennik odmów:")
    try:
        with open(denied, encoding="ascii") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        say("   " + line)
    results["denied-log"] = any("\texample.com\t443\tCONNECT" in line for line in lines)
    say()
    for key, ok in results.items():
        say(f"WYNIK {key}: {'tak' if ok else 'NIE'}")
    say(f"# dziennik: {os.path.relpath(log_path, REPO)}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
