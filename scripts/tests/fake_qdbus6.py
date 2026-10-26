#!/usr/bin/python3
"""Atrapa qdbus6 dla testów instalacji paneli.

- org.kde.PlasmaShell.evaluateScript: zapisuje skrypt w $TIDE_FAKE_DIR/calls i wykonuje go
  na atrapie plasmashell (stan w $TIDE_FAKE_DIR/plasma.json),
- org.kde.kded6: odpowiada stanem modułu appmenu z $TIDE_FAKE_DIR/kded.json i zapisuje wywołania.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.tests import fake_plasmashell as fake  # noqa: E402

base = Path(__import__("os").environ["TIDE_FAKE_DIR"])
calls = base / "calls"
calls.mkdir(exist_ok=True)
args = sys.argv[1:]

if args[:3] == ["org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript"]:
    script = args[3]
    tag = script.split("\n", 1)[0].replace("// tide:", "")
    (calls / f"{len(list(calls.glob('*.js'))):03d}-{tag}.js").write_text(script)
    out, state = fake.run(json.loads((base / "plasma.json").read_text()), script)
    (base / "plasma.json").write_text(json.dumps(state))
    print(out)
elif args[:2] == ["org.kde.kded6", "/kded"]:
    kded = json.loads((base / "kded.json").read_text())
    with open(calls / "kded.log", "a") as log:
        log.write(" ".join(args[2:]) + "\n")
    method = args[2].rsplit(".", 1)[-1]
    if method == "isModuleAutoloaded":
        print("true" if kded["appmenu"] else "false")
    elif method == "setModuleAutoloading":
        kded["appmenu"] = args[4] == "true"
        (base / "kded.json").write_text(json.dumps(kded))
else:
    sys.exit(f"atrapa qdbus6: nieobsługiwane wywołanie {args}")
