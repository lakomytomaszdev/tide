#!/usr/bin/env python3
"""tide-tray: lustra elementów zasobnika z kafelkami Tide.

Użycie: tide-tray.py [--rules tray.json]          działa jako usługa
        tide-tray.py --release                     oddaje oryginały (po twardym zabiciu usługi)
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tide_tray import plasma  # noqa: E402
from tide_tray.service import Service, release  # noqa: E402
from tide_tray.state import Owned  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Lustra zasobnika Tide")
    default_rules = HERE / "tray.json"
    if not default_rules.exists():
        default_rules = HERE.parent / "theme" / "icons" / "tray.json"
    parser.add_argument("--rules", default=str(default_rules))
    parser.add_argument("--release", action="store_true", help="zdejmij wyłączenia oryginałów i zakończ")
    args = parser.parse_args(argv)
    if args.release:
        from gi.repository import Gio
        ok = release(plasma.TrayConfig(Gio.bus_get_sync(Gio.BusType.SESSION, None)), Owned())
        print("tide-tray: oryginały oddane." if ok else "tide-tray: nie wszystkie oryginały udało się oddać.")
        return 0 if ok else 1
    Service(Path(args.rules)).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
