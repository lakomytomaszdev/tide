"""Podgląd PT-255 na hoście po sprawdzeniu metadanych aktywnej usługi.

Nie zatrzymuje usługi ani nie usuwa kopii. Kwalifikację wykonuje niezmienione
narzędzie PT-252. Nie wypisuje środowiska procesu ani treści dzienników.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import cardflow_cleanup as cleanup


def service_metadata(source):
    result = subprocess.run(
        ["/usr/bin/systemctl", "--user", "show", "tide-daemon.service",
         "--property=ActiveState,MainPID,ExecMainStartTimestamp"],
        check=True, capture_output=True, text=True, timeout=30)
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    pid = int(properties["MainPID"])
    if properties["ActiveState"] != "active" or pid <= 0:
        raise ValueError("service-not-active")
    # Czytamy wyłącznie wybrane wartości; cała zawartość environ nie trafia do raportu.
    environment = dict(item.split(b"=", 1) for item in
                       Path(f"/proc/{pid}/environ").read_bytes().split(b"\0") if b"=" in item)
    home = os.fsdecode(environment[b"HOME"])
    state = os.fsdecode(environment.get(b"XDG_STATE_HOME") or
                        os.fsencode(os.path.join(home, ".local", "state")))
    if not os.path.isabs(state):
        raise ValueError("relative-service-state")
    actual = os.path.join(state, "tide", "cardflow")
    if actual != source:
        raise ValueError("selected-source-does-not-match-service")
    return {"unit": "tide-daemon.service", "active_state": properties["ActiveState"],
            "main_pid": pid, "started": properties.get("ExecMainStartTimestamp", ""),
            "source": actual, "state_origin": "service-process-environment"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "plan", "metadata"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        if not os.path.isabs(args.source) or not hasattr(os.path, "ALLOW_MISSING"):
            raise ValueError("absolute-source-and-allow-missing-required")
        metadata = service_metadata(args.source)
        cleanup.external(args.source, args.metadata)
        cleanup.external(args.source, args.plan)
        if Path(args.plan).exists() or Path(args.metadata).exists():
            raise ValueError("output-already-exists")
        metadata["recorded_at"] = datetime.now(timezone.utc).isoformat()
        tool = Path(__file__).with_name("cardflow_cleanup.py")
        metadata["tool_sha256"] = hashlib.sha256(tool.read_bytes()).hexdigest()
        metadata["command"] = [sys.executable, str(tool), "--source", args.source, "--plan", args.plan]
        result = subprocess.run(metadata["command"], check=False)
        metadata["preview_returncode"] = result.returncode
        cleanup.write_json(args.metadata, metadata)
        print(json.dumps({"preview_returncode": result.returncode, "metadata": args.metadata}))
        return result.returncode
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print(json.dumps({"status": "error", "reason": "preview-preflight-refused"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
