#!/usr/bin/python3
"""Atrapa poleceń hosta dla testów prób pudełka (PT-164): incus, nft, docker, sudo, ip, systemctl, systemd-run,
npm, node.

Test zakłada w katalogu z PATH opakowania `exec python3 fake_box_proto.py <nazwa> "$@"`. Stan udawanego hosta
w $TIDE_PROTO_FAKE_DIR/state.json, każde wywołanie dopisywane jako lista JSON do calls.jsonl.

- nft trzyma tabelę inet tide-box z regułami bazowymi (jak scripts/box/files.sh) i obsługuje tylko list, -a list,
  insert rule i delete rule … handle N; reguły spoza tabeli inet tide-box trafiają do state["foreign"].
- incus exec niczego nie wykonuje: zwraca gotowe odpowiedzi; `docker` w kontenerze to atrapa docker.
- FAKE_FAIL (wyrażenie regularne) — polecenie incus, którego argumenty pasują, kończy się błędem;
  FAKE_DOCKER_FAIL=1 — `docker run` kończy się błędem 125.
"""
import json
import os
import re
import sys
from pathlib import Path

base = Path(os.environ["TIDE_PROTO_FAKE_DIR"])
state_file = base / "state.json"
state = json.loads(state_file.read_text())
name = sys.argv[1]
args = sys.argv[2:]
with open(base / "calls.jsonl", "a") as log:
    log.write(json.dumps([name, *args]) + "\n")


def save():
    state_file.write_text(json.dumps(state))


def fail(message, code=1):
    print(message, file=sys.stderr)
    sys.exit(code)


def check_fail(words):
    pattern = os.environ.get("FAKE_FAIL")
    if pattern and re.search(pattern, " ".join(words)):
        fail(f"atrapa: błąd dla {' '.join(words)}")


BASE_RULES = {
    "input": [
        (2, 'iifname "incusbr0" ip daddr {addr} tcp dport {port} accept comment "proxy Tide"'),
        (3, 'iifname "incusbr0" udp sport 68 udp dport 67 accept comment "DHCP"'),
        (4, 'iifname "incusbr0" meta l4proto { tcp, udp } th dport 53 drop comment "DNS tylko przez proxy"'),
        (5, 'iifname "incusbr0" drop'),
    ],
    "forward": [
        (7, 'iifname "incusbr0" drop'),
        (8, 'oifname "incusbr0" drop'),
    ],
}


def nft_listing(handles):
    addr = state["bridge"].split("/")[0]
    out = ["table inet tide-box {"]
    for chain, rules in BASE_RULES.items():
        out.append(f"\tchain {chain} {{")
        out.append(f"\t\ttype filter hook {chain} priority filter + 10; policy accept;")
        extra = [(r["handle"], r["rule"]) for r in state["nft_rules"] if r["chain"] == chain]
        own = [(h, r.replace("{addr}", addr).replace("{port}", str(state["port"]))) for h, r in rules]
        for handle, rule in list(reversed(extra)) + own:
            out.append(f"\t\t{rule}" + (f" # handle {handle}" if handles else ""))
        out.append("\t}")
    out.append("}")
    print("\n".join(out))


def nft():
    rest = list(args)
    handles = False
    if rest[:1] == ["-a"]:
        handles = True
        rest = rest[1:]
    if rest == ["list", "table", "inet", "tide-box"]:
        nft_listing(handles)
    elif rest[:2] == ["insert", "rule"]:
        if rest[2:4] != ["inet", "tide-box"]:
            state.setdefault("foreign", []).append(rest)
            save()
            return
        handle = state["next_handle"]
        state["next_handle"] += 1
        state["nft_rules"].append({"chain": rest[4], "rule": " ".join(rest[5:]), "handle": handle})
        state.setdefault("inserted", []).append({"chain": rest[4], "rule": " ".join(rest[5:])})
        save()
    elif rest[:2] == ["delete", "rule"] and rest[2:4] == ["inet", "tide-box"] and rest[5] == "handle":
        before = len(state["nft_rules"])
        state["nft_rules"] = [r for r in state["nft_rules"]
                              if not (r["chain"] == rest[4] and r["handle"] == int(rest[6]))]
        if len(state["nft_rules"]) == before:
            fail("Error: Could not process rule: No such file or directory")
        save()
    else:
        fail(f"atrapa nft: nieobsługiwane wywołanie {args}", 99)


def incus_exec(cmd):
    check_fail(["exec", *cmd])
    if not sys.stdin.isatty():
        sys.stdin.read()
    head = cmd[0] if cmd else ""
    if head == "docker":
        docker(cmd[1:])
    elif head == "dpkg-query":
        for pkg in cmd[3:]:
            print(f"{pkg} install ok installed")
    elif head == "stat":
        print(f"1000:1000 {cmd[-1]}")
    elif head == "apt-cache":
        print("      runc | 1.3.4-0ubuntu1~24.04.1 | http://archive.ubuntu.com/ubuntu noble-updates/main amd64 Packages")
        print("      runc | 1.1.12-0ubuntu3 | http://archive.ubuntu.com/ubuntu noble/main amd64 Packages")
    elif head == "pgrep":
        print("4242 sleep 30")
    elif head == "ls" and cmd[1:2] == ["-A"]:
        print(".git")
    elif head in ("test", "timeout"):
        sys.exit(1)
    elif head == "systemctl":
        print("running")
    else:
        print("ok")


def incus():
    if args[:1] == ["exec"]:
        incus_exec(args[args.index("--") + 1:] if "--" in args else [])
        return
    check_fail(args)
    box = state.get("box", False)
    if args == ["info"]:
        print("api_status: stable")
    elif args[:1] == ["info"]:
        if not box:
            fail("Error: Failed to fetch instance: Instance not found")
        print(f"Name: tide-proto\nStatus: {state.get('status', 'RUNNING')}")
    elif args[:1] == ["launch"]:
        if any(a == "security.privileged=true" for a in args):
            fail("atrapa incus: security.privileged zabronione w teście", 98)
        state.update(box=True, status="RUNNING")
        save()
    elif args[:1] == ["start"]:
        state["status"] = "RUNNING"
        save()
    elif args[:1] == ["stop"]:
        state["status"] = "STOPPED"
        save()
    elif args[:1] == ["delete"]:
        state["box"] = False
        save()
    elif args[:1] == ["list"]:
        print(f'"{state["box_ip"]} (eth0)"')
    elif args[:2] == ["config", "get"]:
        print("")
    elif args[:2] == ["config", "show"]:
        print('config:\n  security.nesting: "true"')
    elif args[:3] == ["config", "device", "show"]:
        print("")
    elif args[:1] == ["version"]:
        print("Client version: 6.0.0\nServer version: 6.0.0")
    else:
        print("ok")


def docker(rest):
    if rest[:1] == ["run"] and os.environ.get("FAKE_DOCKER_FAIL"):
        fail("docker: Error response from daemon: atrapa — runc create failed", 125)
    if rest[:1] == ["run"]:
        print("Hello from Docker!")
    else:
        print("ok")


def sudo():
    rest = list(args)
    while rest and rest[0].startswith("-"):
        if rest[0] == "-v":
            return
        rest = rest[1:]
    if not rest or rest == ["true"]:
        return
    os.execvp(rest[0], rest)


def ip():
    bridge = f"5: incusbr0    inet {state['bridge']} brd 10.99.0.255 scope global incusbr0"
    if args == ["-4", "-o", "addr", "show", "dev", "incusbr0"]:
        print(bridge)
    elif args == ["-4", "-o", "addr", "show", "scope", "global"]:
        print("2: enp3s0    inet 192.0.2.10/24 brd 192.0.2.255 scope global enp3s0")
        print(bridge)
    else:
        fail(f"atrapa ip: nieobsługiwane wywołanie {args}", 99)


def node():
    print("v22.11.0")


def generic():
    print("ok")


{
    "incus": incus,
    "nft": nft,
    "docker": lambda: docker(args),
    "sudo": sudo,
    "ip": ip,
    "node": node,
}.get(name, generic)()
