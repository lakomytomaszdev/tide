#!/usr/bin/python3
"""Atrapa kscreen-doctor dla testów instalatorów paneli: żaden test nie czyta prawdziwego narzędzia,
które na tej maszynie widzi wyłączony DP-2.

Odpowiada na `-j` zawartością $TIDE_FAKE_DIR/kscreen.json (bez pliku — oba wyjścia podłączone i włączone),
a plik `kscreen.fail` w tym katalogu wymusza błąd (kod 1). Każde wywołanie trafia do calls/kscreen.log.
"""
import json
import os
import sys
from pathlib import Path

BOTH_ENABLED = {"outputs": [
    {"name": "DP-2", "connected": True, "enabled": True},
    {"name": "HDMI-A-1", "connected": True, "enabled": True},
]}

base = Path(os.environ["TIDE_FAKE_DIR"])
(base / "calls").mkdir(exist_ok=True)
with open(base / "calls" / "kscreen.log", "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
if sys.argv[1:] != ["-j"]:
    sys.exit(f"atrapa kscreen-doctor: nieobsługiwane wywołanie {sys.argv[1:]}")
if (base / "kscreen.fail").exists():
    print("kscreen-doctor: nie można połączyć z serwerem", file=sys.stderr)
    sys.exit(1)
state = base / "kscreen.json"
print(state.read_text() if state.exists() else json.dumps(BOTH_ENABLED))
