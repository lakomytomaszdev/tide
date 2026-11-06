"""Zastosowanie PT-255 na hoście: kontrola procesów i osłona narzędzia PT-252.

Nie zmienia kwalifikacji ani implementacji backupu/usuwania. Rezerwacja nazw
D-Bus blokuje aktywację przez klientów; finally zwalnia ją i wznawia usługę.
Raport zawiera wyłącznie metadane. Uruchamiać po osobnym, ocenionym podglądzie.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import cardflow_cleanup as cleanup
import cardflow_cleanup_host as host
from pt255_cleanup_preview import service_metadata

PREVIEW_COUNTS = {"candidate": 480, "no-step-started": 101, "repo-present-or-unknown": 29}
TOOL_SHA256 = "5d811fff8afc91ddc100be5c7397f947d5653d86212ad8ccd1298a45f70b5ef5"
NAMES = ("org.tide.Daemon", "org.tide.Daemon.KRunner")


def now():
    return datetime.now(timezone.utc).isoformat()


class ActivationBlocker:
    """Kolejka przed stop: nazwy przechodzą na nas bez przerwy umożliwiającej aktywację."""
    def __init__(self, service_pid):
        import dbus

        self.bus = dbus.SessionBus(private=True)
        try:
            for name in NAMES:
                if self.bus.name_has_owner(name):
                    owner = self.bus.get_name_owner(name)
                    pid = self.bus.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
                        "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", (owner,))
                    if int(pid) != service_pid:
                        raise ValueError("unexpected-activation-name-owner")
                if int(self.bus.request_name(name, flags=0)) not in (1, 2):
                    raise ValueError("cannot-reserve-activation-name")
        except Exception:
            self.close()
            raise ValueError("activation-reservation-failed") from None

    def require_owned(self):
        try:
            if any(self.bus.get_name_owner(name) != self.bus.get_unique_name() for name in NAMES):
                raise ValueError("activation-name-not-held")
        except Exception:
            raise ValueError("activation-name-not-held") from None

    def close(self):
        if self.bus is not None:
            bus, self.bus = self.bus, None
            bus.close()


class Audit:
    def __init__(self, path):
        parent = cleanup.directory(Path(path).parent)
        try:
            fd = os.open(Path(path).name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)
        self.stream = os.fdopen(fd, "w", encoding="utf-8")
        self.data = {"ticket": "PT-255", "status": "running", "events": []}
        cleanup.save_report(self.stream, self.data)

    def event(self, kind, **values):
        self.data["events"].append({"event": kind, "at": now(), **values})
        cleanup.save_report(self.stream, self.data)


def process_kind(argv):
    names = [os.path.basename(arg) for arg in argv]
    if any(argv[i:i + 2] in (["-m", "unittest"], ["-m", "pytest"])
           for i in range(len(argv) - 1)):
        return "test"
    if any(name in {"pytest", "pytest-3", "py.test", "test_runner.py", "test_benchmark.py"}
           or (name.startswith("test_") and name.endswith(".py"))
           or (name.startswith(("guard", "isolation")) and name.endswith("_probe.py"))
           for name in names):
        return "test"
    if "tide-daemon.py" in names or any(
            argv[i:i + 2] == ["-m", "tide_daemon"] for i in range(len(argv) - 1)):
        return "daemon"
    return None


def check_processes(source, allowed_daemon=None):
    """Bez argv/środowiska w raporcie i bez odczytywania otwartych plików."""
    checked = 0
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            argv = [os.fsdecode(item) for item in (process / "cmdline").read_bytes().split(b"\0") if item]
            kind = process_kind(argv)
            pid = int(process.name)
            if kind == "test" or (kind == "daemon" and pid != allowed_daemon):
                raise ValueError(f"active-{kind}-pid-{pid}")
            if pid != allowed_daemon:
                for fd in (process / "fd").iterdir():
                    try:
                        target = os.path.realpath(os.readlink(fd))
                        if target == source or target.startswith(source + "/"):
                            flags = next(line.split()[1] for line in
                                         (process / "fdinfo" / fd.name).read_text().splitlines()
                                         if line.startswith("flags:"))
                            if int(flags, 8) & os.O_ACCMODE:
                                raise ValueError(f"active-writer-pid-{pid}")
                    except FileNotFoundError:
                        continue
            checked += 1
        except (FileNotFoundError, ProcessLookupError):
            continue
    return {"checked_user_processes": checked, "active_tests": 0, "extra_daemons": 0,
            "writable_source_descriptors": 0}


def validate_preview(args):
    metadata = cleanup.load_json(args.preview_metadata)
    plan = cleanup.load_json(args.plan)
    tool = Path(__file__).with_name("cardflow_cleanup.py")
    if (metadata["preview_returncode"] != 0 or metadata["source"] != args.source
            or plan["source"] != args.source or plan["version"] != 1
            or metadata["tool_sha256"] != TOOL_SHA256
            or hashlib.sha256(tool.read_bytes()).hexdigest() != TOOL_SHA256
            or plan["counts"] != PREVIEW_COUNTS
            or dict(Counter(row["reason"] for row in plan["entries"])) != PREVIEW_COUNTS):
        raise ValueError("preview-or-approved-tool-mismatch")
    return {"counts": plan["counts"], "total": len(plan["entries"]),
            "plan_sha256": hashlib.sha256(Path(args.plan).read_bytes()).hexdigest(),
            "tool_sha256": TOOL_SHA256}


def run(args, audit):
    # Zatrzymanie własnej jednostki zabiłoby osłonę i jej finally.
    if "/tide-daemon.service" in Path("/proc/self/cgroup").read_text():
        raise ValueError("maintenance-must-run-outside-service-cgroup")
    audit.event("preview-validated", **validate_preview(args))
    service = service_metadata(args.source)
    audit.event("service-before", **service)
    audit.event("processes-before", **check_processes(args.source, service["main_pid"]))
    control, create_backup, remove_candidate = host.systemctl, cleanup.create_backup, cleanup.remove_candidate
    blocker = None
    backup_verified = False
    first_delete = True

    def release_activation():
        if blocker is not None and blocker.bus is not None:
            blocker.close()
            audit.event("activation-reservation-released")

    def checked_control(*argv):
        if argv == ("start", host.UNIT):
            audit.event("protected-window-closing")
            release_activation()
        result = control(*argv)
        if argv == ("stop", host.UNIT):
            if control("show", host.UNIT, "--property=ActiveState", "--value") != "inactive":
                raise ValueError("service-not-stopped")
            blocker.require_owned()
            audit.event("service-stopped-activation-held", names=list(NAMES))
            audit.event("processes-stopped", **check_processes(args.source))
        return result

    def checked_backup(source, backup, rows):
        nonlocal backup_verified
        create_backup(source, backup, rows)
        manifest = cleanup.verify_backup(backup)
        if manifest["entries"] != rows:
            raise ValueError("backup-entries-mismatch")
        backup_verified = True
        audit.event("complete-backup-verified", backup=os.path.abspath(backup), entries=len(rows),
                    manifest_sha256=hashlib.sha256((Path(backup) / "manifest.json").read_bytes()).hexdigest())

    def checked_remove(source, row):
        nonlocal first_delete
        if not backup_verified:
            raise ValueError("deletion-without-complete-verified-backup")
        if first_delete:
            host.require_stopped()
            blocker.require_owned()
            audit.event("processes-before-first-delete", **check_processes(args.source))
            audit.event("first-deletion-authorized-after-backup")
            first_delete = False
        return remove_candidate(source, row)

    try:
        blocker = ActivationBlocker(service["main_pid"])
        audit.event("activation-names-queued", names=list(NAMES))
        host.systemctl = checked_control
        cleanup.create_backup = checked_backup
        cleanup.remove_candidate = checked_remove
        args.other_writers_stopped = True  # Dopiero po kontroli procesów, nie deklaracja operatora.
        result = host.run_window(args)
        audit.event("window-verified", **result)
        return result
    finally:
        host.systemctl, cleanup.create_backup, cleanup.remove_candidate = control, create_backup, remove_candidate
        if blocker is not None:
            try:
                release_activation()
            finally:
                if control("show", host.UNIT, "--property=ActiveState", "--value") != "active":
                    control("start", host.UNIT)
                if control("show", host.UNIT, "--property=ActiveState", "--value") != "active":
                    raise ValueError("service-restart-failed")
                audit.event("service-restored-active")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "plan", "preview-metadata", "backup-dir", "report", "repeat-plan",
                 "repeat-backup-dir", "repeat-report", "audit"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    audit = None
    previous_handlers = {}
    try:
        for path in (args.plan, args.preview_metadata, args.backup_dir, args.report,
                     args.repeat_plan, args.repeat_backup_dir, args.repeat_report, args.audit):
            cleanup.external(args.source, path)
        audit = Audit(args.audit)
        def interrupted(signum, frame):
            raise ValueError("host-operation-interrupted")
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[sig] = signal.signal(sig, interrupted)
        result = run(args, audit)
        audit.data["status"] = "ok"
        audit.event("finished", service_active=True)
        print(json.dumps({**result, "service_active": True, "audit": args.audit}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError) as exc:
        # Wyjątki systemowe mogą zawierać argv/ścieżki; do modelu tylko kontrolowany kod.
        reason = str(exc) if type(exc) is ValueError and str(exc).startswith("active-") else "host-operation-refused-or-failed"
        if audit:
            audit.data["status"] = "error"
            audit.event("failed", reason=reason)
        print(json.dumps({"status": "error", "reason": reason, "audit": args.audit}))
        return 1
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        if audit:
            audit.stream.close()


if __name__ == "__main__":
    sys.exit(main())
