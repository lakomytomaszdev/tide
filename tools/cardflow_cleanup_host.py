"""Okno konserwacji PT-255: zatrzymanie usługi, zastosowanie, kontrola i wznowienie.

Uruchamiane wyłącznie na hoście. Przed użyciem zakończ stare testy, zamknij klientów
Tide i wyklucz inne procesy zapisujące kopie. Nie służy do pracy wewnątrz kroku.
"""
import argparse
import json
import os
import subprocess
import sys

import cardflow_cleanup as cleanup

UNIT = "tide-daemon.service"


def systemctl(*args):
    return subprocess.run(["/usr/bin/systemctl", "--user", *args], check=True,
                          capture_output=True, text=True, timeout=30).stdout.strip()


def require_stopped():
    if systemctl("show", UNIT, "--property=ActiveState", "--value") != "inactive":
        raise ValueError("service-not-inactive")


def run_window(args):
    if not args.other_writers_stopped:
        raise ValueError("other-writers-must-be-stopped")
    if systemctl("show", UNIT, "--property=ActiveState", "--value") != "active":
        raise ValueError("service-must-initially-be-active")
    # finally obejmuje również nieudane zatrzymanie i błędy kontroli/backupu.
    try:
        systemctl("stop", UNIT)
        require_stopped()
        plan = cleanup.load_json(args.plan)
        if plan["source"] != os.path.abspath(args.source):
            raise ValueError("source-does-not-match-preview")
        cleanup.external(args.source, args.plan)
        report = cleanup.apply(plan, args.backup_dir, args.report, writers_stopped=True)
        require_stopped()
        cleanup.verify_report(report)
        # Powtórny podgląd i zastosowanie w tym samym chronionym oknie.
        repeat = cleanup.preview(args.source)
        cleanup.external(args.source, args.repeat_plan)
        cleanup.write_json(args.repeat_plan, repeat)
        if repeat["counts"].get("candidate", 0):
            raise ValueError("repeat-found-candidates-requires-new-review")
        require_stopped()
        repeated = cleanup.apply(repeat, args.repeat_backup_dir, args.repeat_report, writers_stopped=True)
        cleanup.verify_report(repeated)
        require_stopped()
        return {"status": "ok", "report": args.report, "repeat_report": args.repeat_report,
                "deleted_count": report["deleted_count"], "repeat_deleted_count": repeated["deleted_count"],
                "backup": report["backup"], "backup_verified": report["backup_verified"],
                "preserved_verified": True}
    finally:
        systemctl("start", UNIT)
        if systemctl("show", UNIT, "--property=ActiveState", "--value") != "active":
            raise ValueError("service-restart-failed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("source", "plan", "backup-dir", "report", "repeat-plan", "repeat-report", "repeat-backup-dir"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--other-writers-stopped", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_window(args)
        result["service_active"] = True
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print(json.dumps({"status": "error", "reason": "host-window-failed-check-local-reports"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
