"""Atrapy `kscreen-doctor` i `ddcutil` dla testów wejścia monitora (PT-38). Przełącznik woła tylko ddcutil —
atrapa kscreen-doctor zapisuje wywołania, żeby test mógł sprawdzić, że układu w KWin nikt nie zmienia.

Jeden skrypt udaje oba programy; nazwę programu dostaje jako pierwszy argument (opakowania w katalogu
atrap: `exec python3 fake_screens.py kscreen-doctor "$@"`). Stan „sprzętu” leży w pliku JSON
($TIDE_FAKE_SCREENS) i każde wywołanie go czyta i zapisuje, a lista wywołań trafia do pola "calls".
Prawdziwe monitory, KWin i magistrale I²C nie są dotykane.

Stan:
  kscreen   — wynik `kscreen-doctor -j` (outputs z modes, currentModeId, pos, priority, enabled, connected…);
  ddc       — {"buses": {"DP-2": 4}, "invalid": {"DP-2": 9} (blok „Invalid display”), "input": {"4": 15},
               "reachable": {"4": true},
               "only_active": {"4": 15}  (monitor odpowiada na DDC tylko, gdy pokazuje to wejście),
               "hpd_drop": {"4": "DP-2"} (po zmianie wejścia z 15 monitor znika z KWin jak odłączony)};
  fail      — {"kscreen-set": "komunikat"} wymusza błąd zmiany układu.
"""
from __future__ import annotations

import copy
import json
import os
import sys

ROTATIONS = {"none": 1, "left": 2, "inverted": 4, "right": 8}  # kscreen-doctor --help


def mode(ident: str, width: int, height: int, refresh: float) -> dict:
    return {"id": ident, "name": f"{width}x{height}@{round(refresh)}", "refreshRate": refresh,
            "size": {"width": width, "height": height}}


def output(ident: int, name: str, modes: list[dict], current: str, pos: tuple[int, int], priority: int,
           preferred: str, enabled: bool = True, connected: bool = True) -> dict:
    size = next(m["size"] for m in modes if m["id"] == current)
    return {"id": ident, "name": name, "connected": connected, "enabled": enabled, "currentModeId": current,
            "modes": modes, "pos": {"x": pos[0], "y": pos[1]}, "priority": priority if enabled else 0,
            "preferredModes": [preferred], "rotation": 1, "scale": 1, "size": dict(size), "type": 14}


def desk() -> dict:
    """Układ z tego komputera (kscreen-doctor -j z 28.10.2026, okrojony do kilku trybów)."""
    dp = [mode("1", 2560, 1440, 59.939998626708984), mode("2", 2560, 1440, 164.802001953125),
          mode("3", 2560, 1440, 119.87899780273438), mode("4", 1920, 1080, 60)]
    hdmi = [mode("26", 1920, 1080, 60), mode("27", 1920, 1080, 74.9729995727539),
            mode("28", 1920, 1080, 59.93899917602539), mode("29", 1280, 720, 60)]
    return {"outputs": [output(1, "DP-2", dp, "2", (1920, 0), 1, "1"),
                        output(2, "HDMI-A-1", hdmi, "27", (0, 0), 2, "26")],
            "screen": {"id": 0}}


def initial_state() -> dict:
    return {"kscreen": desk(),
            "ddc": {"buses": {"HDMI-A-1": 2, "DP-2": 4}, "input": {"2": 15, "4": 15},
                    "reachable": {"2": True, "4": True}, "only_active": {}, "hpd_drop": {}},
            "fail": {}, "calls": []}


def _out(state: dict, name: str) -> dict:
    for item in state["kscreen"]["outputs"]:
        if item["name"] == name:
            return item
    raise KeyError(name)


def kscreen(state: dict, args: list[str]) -> int:
    if args in (["-j"], ["--json"]):
        print(json.dumps(state["kscreen"]))
        return 0
    if args in (["-o"], ["--outputs"]):
        for item in state["kscreen"]["outputs"]:
            print(f"Output: {item['id']} {item['name']} {'enabled' if item['enabled'] else 'disabled'}")
        return 0
    if state["fail"].get("kscreen-set"):
        print(state["fail"]["kscreen-set"], file=sys.stderr)
        return 1
    before = copy.deepcopy(state["kscreen"])
    wanted: dict[str, int] = {}
    for arg in args:
        parts = arg.split(".", 3)
        if len(parts) < 3 or parts[0] != "output":
            print(f"nieznany argument {arg}", file=sys.stderr)
            state["kscreen"] = before
            return 1
        try:
            item = _out(state, parts[1])
        except KeyError:
            print(f"brak wyjścia {parts[1]}", file=sys.stderr)
            state["kscreen"] = before
            return 1
        key = parts[2]
        value = parts[3] if len(parts) > 3 else ""
        if key in ("enable", "disable") and not item["connected"]:
            print(f"wyjście {item['name']} nie jest podłączone", file=sys.stderr)
            state["kscreen"] = before
            return 1
        if key == "enable":
            item["enabled"] = True
        elif key == "disable":
            item["enabled"] = False
        elif key == "mode":
            found = [m for m in item["modes"] if m["id"] == value]
            if not found:
                print(f"brak trybu {value}", file=sys.stderr)
                state["kscreen"] = before
                return 1
            item["currentModeId"] = value
            item["size"] = dict(found[0]["size"])
        elif key == "position":
            x, y = value.split(",")
            item["pos"] = {"x": int(x), "y": int(y)}
        elif key == "priority":
            wanted[item["name"]] = int(value)
        elif key == "scale":
            item["scale"] = float(value) if "." in value else int(value)
        elif key == "rotation":
            item["rotation"] = ROTATIONS[value]
        else:
            print(f"nieznana właściwość {key}", file=sys.stderr)
            state["kscreen"] = before
            return 1
    # Priorytety jak w KWin: kolejne liczby od 1 wśród włączonych, zamówione wygrywają.
    enabled = [o for o in state["kscreen"]["outputs"] if o["enabled"]]
    enabled.sort(key=lambda o: (wanted.get(o["name"], o["priority"] or 99), o["name"] not in wanted))
    for index, item in enumerate(enabled, 1):
        item["priority"] = index
    for item in state["kscreen"]["outputs"]:
        if not item["enabled"]:
            item["priority"] = 0
    return 0


def ddcutil(state: dict, args: list[str]) -> int:
    ddc = state["ddc"]
    if args[:1] == ["detect"]:
        for name, bus in sorted(ddc.get("invalid", {}).items(), key=lambda kv: kv[1]):
            # Monitor bez DDC/CI: ddcutil wypisuje go osobnym blokiem, też ze złączem i magistralą.
            print(f"Invalid display\n   I2C bus:  /dev/i2c-{bus}\n   DRM connector:           card1-{name}\n"
                  f"   DDC communication failed\n")
        for index, (name, bus) in enumerate(sorted(ddc["buses"].items(), key=lambda kv: kv[1]), 1):
            print(f"Display {index}\n   I2C bus:  /dev/i2c-{bus}\n   DRM connector:           card1-{name}\n"
                  f"   VCP version:         2.2\n")
        return 0
    if args[:1] != ["--bus"]:
        print(f"atrapa: nieobsługiwane wywołanie {args}", file=sys.stderr)
        return 2
    bus = args[1]
    rest = args[2:]
    active = ddc.get("only_active", {}).get(bus)
    if not ddc["reachable"].get(bus, False) or (active is not None and ddc["input"].get(bus) != active):
        print(f"DDC communication failed for display on bus /dev/i2c-{bus}", file=sys.stderr)
        return 1
    if rest[:2] == ["getvcp", "60"]:
        print(f"VCP 60 SNC x{ddc['input'][bus]:02x}")
        return 0
    if rest[:2] == ["setvcp", "60"]:
        value = int(rest[2], 0)
        ddc["input"][bus] = value
        name = ddc.get("hpd_drop", {}).get(bus)
        if name:
            item = _out(state, name)
            item["connected"] = value == 15
            if value != 15:
                item["enabled"] = False
                item["priority"] = 0
        return 0
    print(f"atrapa: nieobsługiwane wywołanie {args}", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    path = os.environ["TIDE_FAKE_SCREENS"]
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
    program, args = argv[0], argv[1:]
    state["calls"].append([program, *args])
    code = kscreen(state, args) if program == "kscreen-doctor" else ddcutil(state, args)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    return code


def install(bin_dir: str, state_path: str, state: dict | None = None, tools=("kscreen-doctor", "ddcutil")) -> None:
    """Opakowania atrap w bin_dir i początkowy stan w state_path."""
    os.makedirs(bin_dir, exist_ok=True)
    for tool in tools:
        path = os.path.join(bin_dir, tool)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/sh\nexec /usr/bin/python3 {os.path.abspath(__file__)} {tool} "$@"\n')
        os.chmod(path, 0o755)
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state or initial_state(), fh)


def load(state_path: str) -> dict:
    with open(state_path, encoding="utf-8") as fh:
        return json.load(fh)


def save(state_path: str, state: dict) -> None:
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
