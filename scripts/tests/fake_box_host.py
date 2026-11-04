#!/usr/bin/python3
"""Atrapa poleceń hosta dla testów scripts/box (PT-166): apt-get, incus, incus-user, nft, systemctl, getent,
dpkg-query, ip.

Test zakłada w katalogu z PATH opakowania `exec python3 fake_box_host.py <nazwa> "$@"` — nazwa programu jest
pierwszym argumentem. Stan udawanego hosta w $TIDE_BOX_FAKE_DIR/state.json, każde wywołanie dopisywane do
$TIDE_BOX_FAKE_DIR/calls.log. Atrapy obsługują tylko odczyt i symulację: polecenie zmieniające stan kończy się
błędem, więc test od razu widzi, gdy --dry-run próbowałby coś zmienić.
"""
import json
import os
import sys
from pathlib import Path

base = Path(os.environ["TIDE_BOX_FAKE_DIR"])
state = json.loads((base / "state.json").read_text())
name = sys.argv[1]
args = sys.argv[2:]
with open(base / "calls.log", "a") as log:
    log.write(" ".join([name, *args]) + "\n")


def fail(message, code=1):
    print(message, file=sys.stderr)
    sys.exit(code)


def unsupported():
    fail(f"atrapa {name}: nieobsługiwane wywołanie {args}", 99)


def apt_get():
    if "-s" not in args:
        unsupported()
    rest = [a for a in args if a != "-s"]
    action, packages = rest[0], rest[1:]
    if action == "install":
        if state.get("installed"):
            print("0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.")
            return
        for line in state.get("apt_upgrade", []):
            print(line)
        new = state.get("apt_new", [])
        for pkg in new:
            print(f"Inst {pkg} (1.0-1 Ubuntu:24.04/noble [amd64])")
        for pkg in new:
            print(f"Conf {pkg} (1.0-1 Ubuntu:24.04/noble [amd64])")
        print(f"0 upgraded, {len(new)} newly installed, 0 to remove and 0 not upgraded.")
    elif action == "purge":
        for pkg in packages + state.get("apt_extra_purge", []):
            print(f"Purg {pkg} [1.0-1]")
    else:
        unsupported()


def incus():
    project = None
    rest = list(args)
    if "--project" in rest:
        i = rest.index("--project")
        project = rest[i + 1]
        del rest[i:i + 2]
    projects = state.get("projects", {})
    if rest[:3] == ["network", "get", "incusbr0"] and rest[3:] == ["ipv4.address"]:
        if not state.get("address"):
            fail("Error: Network not found")
        print(state["address"])
    elif rest[:2] == ["storage", "show"]:
        if not state.get("pool") or rest[2] != "tide":
            fail("Error: Storage pool not found")
        print("name: tide\ndriver: btrfs")
    elif rest[:4] == ["profile", "device", "get", "default"]:
        device, key = rest[4], rest[5]
        if project is None:
            value = state.get("profile_pool", "") if (device, key) == ("root", "pool") else ""
        else:
            value = projects.get(project, {}).get(f"{device}.{key}", "")
        if not value:
            fail(f"Error: Device {device} doesn't exist")
        print(value)
    elif rest[:2] == ["project", "show"]:
        if rest[2] not in projects:
            fail("Error: Project not found")
        print(f"name: {rest[2]}")
    elif rest[:2] == ["project", "get"]:
        if rest[2] not in projects:
            fail("Error: Project not found")
        print(projects[rest[2]].get("config", {}).get(rest[3], ""))
    else:
        unsupported()


def nft():
    if args[:1] == ["-c"]:
        sys.exit(state.get("nft_check_rc", 0))
    if args == ["list", "table", "inet", "tide-box"]:
        if not state.get("nft_table"):
            fail("Error: No such file or directory")
        print("table inet tide-box {\n}")
    elif args == ["list", "table", "inet", "incus"]:
        fail("Error: No such file or directory")
    elif args == ["list", "ruleset"]:
        print(state.get("ruleset", ""), end="")
    else:
        unsupported()


def systemctl():
    if args[:1] == ["is-enabled"]:
        value = state.get("units", {}).get(args[1], "disabled")
        print(value)
        sys.exit(0 if value == "enabled" else 1)
    if args[:1] == ["list-unit-files"]:
        lines = state.get("unit_files", [])
        for line in lines:
            print(line)
        sys.exit(0 if lines else 1)
    unsupported()


def getent():
    groups = state.get("groups", {})
    if args[:1] == ["group"]:
        if len(args) == 1:
            for group, members in sorted(groups.items()):
                print(f"{group}:x:999:{','.join(members)}")
            return
        if args[1] not in groups:
            sys.exit(2)
        print(f"{args[1]}:x:999:{','.join(groups[args[1]])}")
        return
    os.execv("/usr/bin/getent", ["getent", *args])


def dpkg_query():
    if args != ["-W"]:
        unsupported()
    for line in state.get("dpkg", []):
        print(line)


def ip():
    if args != ["-o", "link"]:
        unsupported()
    for line in state.get("links", []):
        print(line)


{
    "apt-get": apt_get,
    "incus": incus,
    "incus-user": unsupported,
    "nft": nft,
    "systemctl": systemctl,
    "getent": getent,
    "dpkg-query": dpkg_query,
    "ip": ip,
}.get(name, unsupported)()
