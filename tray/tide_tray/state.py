"""Klucze, które wyłączył tide-tray — żeby oddać tylko swoje, a nie wyłączone przez użytkownika."""
from __future__ import annotations

import json
import os
from pathlib import Path


def default_path() -> Path:
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return state / "tide" / "tray-mirror.json"


class Owned:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()
        try:
            self.keys = set(json.loads(self.path.read_text()).get("disabled", []))
        except (OSError, ValueError):
            self.keys = set()

    def save(self) -> None:
        if not self.keys:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"disabled": sorted(self.keys)}, indent=2) + "\n")
        tmp.replace(self.path)

    def add(self, key: str) -> None:
        self.keys.add(key)
        self.save()

    def discard(self, key: str) -> None:
        self.keys.discard(key)
        self.save()
