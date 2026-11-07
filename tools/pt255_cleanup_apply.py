"""Zastosowanie PT-255 na hoście: kontrola procesów i osłona narzędzia PT-252.

Nie zmienia kwalifikacji ani implementacji backupu/usuwania. Rezerwacja nazw
D-Bus blokuje aktywację przez klientów; finally zwalnia ją i wznawia usługę.
Raport zawiera wyłącznie metadane. Uruchamiać po osobnym, ocenionym podglądzie.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import errno
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

import cardflow_cleanup as cleanup
import cardflow_cleanup_host as host
from pt255_cleanup_preview import service_metadata

PREVIEW_COUNTS = {"candidate": 480, "no-step-started": 101, "repo-present-or-unknown": 29}
TOOL_SHA256 = "5d811fff8afc91ddc100be5c7397f947d5653d86212ad8ccd1298a45f70b5ef5"
NAMES = ("org.tide.Daemon", "org.tide.Daemon.KRunner")
TEST_COMM_PATTERNS = ("python*", "pytest*", "py.test", "sh", "bash", "dash", "zsh",
                      "env", "make", "node*", "tide*", "test_*", "*_probe*")


def now():
    return datetime.now(timezone.utc).isoformat()


class InspectionError(ValueError):
    """Kontrolowane metadane odmowy; bez tekstu wyjątku, ścieżek i argv procesu."""
    def __init__(self, pid, phase, errno, fd=None, flags=None):
        super().__init__("process-metadata-unavailable")
        self.details = {"pid": pid, "phase": phase, "errno": errno}
        if fd is not None:
            self.details["fd"] = fd
        if flags is not None:
            self.details["flags"] = flags


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


def incus_container(cgroup):
    """Tożsamość wyłącznie z pełnego komponentu cgroup, bez historycznych PID."""
    containers = set()
    for line in cgroup.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[2].startswith("/"):
            raise ValueError("invalid-container-cgroup")
        for component in parts[2].split("/"):
            if component in {".", ".."}:
                raise ValueError("invalid-container-cgroup")
            match = re.fullmatch(r"lxc\.payload\.([A-Za-z0-9][A-Za-z0-9_.-]*)_([A-Za-z0-9][A-Za-z0-9-]*)", component)
            if match:
                containers.add(match.groups())
    if len(containers) > 1:
        raise ValueError("ambiguous-container-cgroup")
    return dict(zip(("project", "name"), containers.pop())) if containers else None


def checked_container_disks(text, source):
    """Tylko urządzenia z rozwiniętego YAML; konfiguracja i ścieżki nie trafiają do raportu."""
    import yaml

    class UniqueSafeLoader(yaml.SafeLoader):
        pass

    def unique_mapping(loader, node):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if not isinstance(key, str) or key in result:
                raise ValueError("ambiguous-container-config")
            result[key] = loader.construct_object(value_node)
        return result

    UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)
    try:
        config = yaml.load(text, Loader=UniqueSafeLoader)
    except yaml.YAMLError:
        raise ValueError("invalid-container-config") from None
    if not isinstance(config, dict) or not isinstance(config.get("devices"), dict):
        raise ValueError("invalid-container-devices")
    source = os.path.realpath(source, strict=os.path.ALLOW_MISSING)
    disks = []
    for name, device in sorted(config["devices"].items()):
        if not isinstance(device, dict) or not isinstance(device.get("type"), str) or not device["type"]:
            raise ValueError("invalid-container-device")
        if device["type"] != "disk":
            continue
        value = device.get("source", "")
        if not isinstance(value, str):
            raise ValueError("invalid-container-disk-source")
        row = {"device": name}
        if os.path.isabs(value):
            resolved = os.path.realpath(value, strict=os.path.ALLOW_MISSING)
            common = os.path.commonpath((source, resolved))
            if common in (source, resolved):
                raise ValueError("container-source-overlap")
            row.update(source_kind="host-path", source_relation="disjoint",
                       source_sha256=hashlib.sha256(os.fsencode(resolved)).hexdigest())
        elif (isinstance(device.get("pool"), str) and device["pool"]
              and ((not value and device.get("path") == "/")
                   or (value and all(part not in {".", ".."} for part in value.split("/"))))):
            # Dysk puli nie podaje ścieżki hosta; bez puli względne source jest niejednoznaczne.
            row.update(source_kind="storage-pool", source_relation="managed-volume")
        else:
            raise ValueError("ambiguous-container-disk-source")
        disks.append(row)
    return disks


def container_fd_exemption(process, source):
    """Odczyt Incusa po EACCES; błąd lub niejednoznaczność nadal oznaczają odmowę."""
    pid = int(process.name)
    container = None

    def refuse(phase, error=None):
        failure = InspectionError(pid, phase, getattr(error, "errno", None))
        if container is not None:
            failure.details["container"] = container
        if isinstance(error, subprocess.CalledProcessError):
            failure.details["returncode"] = error.returncode
        raise failure from None

    try:
        before = (process / "cgroup").read_text()
        container = incus_container(before)
    except (OSError, ValueError) as error:
        refuse("read-container-cgroup", error)
    if container is None:
        return None
    try:
        config = subprocess.run(["incus", "config", "show", container["name"], "--project",
                                 container["project"], "--expanded"], check=True,
                                capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as error:
        refuse("container-config-unavailable", error)
    try:
        disks = checked_container_disks(config, source)
    except (ImportError, OSError, ValueError, TypeError, RecursionError) as error:
        phase = "container-source-overlap" if type(error) is ValueError and str(error) == "container-source-overlap" else "invalid-container-config"
        refuse(phase, error)
    try:
        if (process / "cgroup").read_text() != before:
            refuse("container-cgroup-changed")
    except OSError as error:
        refuse("read-container-cgroup", error)
    return {"container": container, "checked_disks": disks}


def host_fd_exemption(process):
    """Drugi wyjątek koordynatora: czytelne metadane procesu hosta spoza Tide/testów."""
    pid = int(process.name)
    phase = "read-host-cgroup"
    units = []
    classification_source = None

    def refuse(reason):
        failure = InspectionError(pid, "host-exemption-refused", None)
        failure.details["host_exemption_reason"] = reason
        if units:
            failure.details["cgroup_units"] = units
        if classification_source is not None:
            failure.details["classification_source"] = classification_source
        raise failure from None

    try:
        before = (process / "cgroup").read_text()
        if not before.strip():
            refuse("empty-cgroup")
        if incus_container(before) is not None:
            refuse("container-cgroup-on-host-check")
        components = [part for line in before.splitlines()
                      for part in line.split(":", 2)[2].split("/") if part]
        units = [part for part in components if part.endswith((".service", ".scope"))]
        if any(part.startswith("tide-") for part in components):
            refuse("tide-cgroup-component")
        if not units:
            refuse("missing-cgroup-unit")
        phase = "read-host-cmdline"
        cmdline = (process / "cmdline").read_bytes()
        argv = [os.fsdecode(item) for item in cmdline.split(b"\0") if item]
        if cmdline and not argv:
            refuse("empty-cmdline")
        kind = process_kind(argv)
        if kind is not None:
            refuse("classified-" + kind)
        phase = "read-host-comm"
        comm = (process / "comm").read_text().rstrip("\n")
        if not comm.strip() or "\n" in comm:
            refuse("invalid-comm")
        classification_source = "cmdline" if cmdline else "comm"
        if not cmdline:
            # Wyłącznie 0 bajtów cmdline: nazwa musi być zgodna w obu źródłach.
            phase = "read-host-stat"
            data = (process / "stat").read_text()
            prefix, end = f"{pid} (", data.rfind(")")
            if (not data.startswith(prefix) or end < len(prefix)
                    or not data[end + 1:].startswith(" ") or not data[end + 1:].split()):
                refuse("invalid-stat-comm")
            if data[len(prefix):end] != comm:
                refuse("comm-stat-mismatch")
            if any(fnmatchcase(comm, pattern) for pattern in TEST_COMM_PATTERNS):
                refuse("test-or-interpreter-comm")
        phase = "recheck-host-cgroup"
        if (process / "cgroup").read_text() != before:
            refuse("cgroup-changed")
    except InspectionError:
        raise
    except (OSError, ValueError) as error:
        raise InspectionError(pid, phase, getattr(error, "errno", None)) from None
    evidence = {"comm": comm, "cgroup_unit": units[-1], "cgroup_units": units,
                "classification_source": classification_source}
    if classification_source == "comm":
        evidence["comm_stat_verified"] = True
    return evidence


def check_processes(source, allowed_daemon=None, *, audit=None):
    """Bez argv/środowiska w raporcie i bez odczytywania otwartych plików."""
    checked = 0
    skips = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        phase = "stat-process"
        descriptor = None
        flags = None
        try:
            if process.stat().st_uid != os.getuid():
                continue
            phase = "read-cmdline"
            argv = [os.fsdecode(item) for item in (process / "cmdline").read_bytes().split(b"\0") if item]
            kind = process_kind(argv)
            pid = int(process.name)
            if kind == "test" or (kind == "daemon" and pid != allowed_daemon):
                raise ValueError(f"active-{kind}-pid-{pid}")
            if pid != allowed_daemon:
                phase = "list-fds"
                for fd in (process / "fd").iterdir():
                    try:
                        descriptor, flags = int(fd.name), None
                        phase = "read-fdinfo"
                        lines = (process / "fdinfo" / fd.name).read_text().splitlines()
                        try:
                            flags = int(next(line.split()[1] for line in lines
                                             if line.startswith("flags:")), 8)
                        except (StopIteration, ValueError, IndexError):
                            raise InspectionError(pid, "parse-fdinfo", None, descriptor) from None
                        # O_RDONLY i O_PATH nie dają prawa zapisu do pliku. Nadal
                        # odmawiamy, jeśli nie potrafimy odczytać samych flags.
                        if not flags & os.O_ACCMODE or flags & os.O_PATH:
                            continue
                        phase = "read-fdlink"
                        target = os.path.realpath(os.readlink(fd))
                        if target == source or target.startswith(source + "/"):
                            raise ValueError(f"active-writer-pid-{pid}")
                    except FileNotFoundError:
                        continue
            checked += 1
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as error:
            # Wyjątki koordynatora wymagają dowodów; nie wystarcza nazwa ani EACCES.
            pid = int(process.name)
            if (error.errno == errno.EACCES
                    and phase in {"list-fds", "read-fdinfo", "read-fdlink"}):
                evidence = container_fd_exemption(process, source)
                reason = "incus-container-without-source-mount"
                if evidence is None:
                    try:
                        evidence = host_fd_exemption(process)
                    except InspectionError as failure:
                        failure.details.update(fd_phase=phase, fd_errno=error.errno)
                        if descriptor is not None:
                            failure.details["fd"] = descriptor
                        raise
                    reason = "host-non-tide-non-test-fd-eacces"
                if evidence is not None:
                    skip = {"pid": pid, "phase": phase, "errno": error.errno, **evidence,
                            "reason": reason}
                    if audit is not None:
                        audit.event("process-inspection-skipped", **skip)
                    skips.append(skip)
                    continue
            raise InspectionError(int(process.name), phase, error.errno, descriptor, flags) from None
    return {"checked_user_processes": checked, "active_tests": 0, "extra_daemons": 0,
            "writable_source_descriptors": 0, "inspection_skips": skips}


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
    audit.event("processes-before", **check_processes(args.source, service["main_pid"], audit=audit))
    if getattr(args, "preflight_only", False):
        return {"status": "ok", "preflight_only": True, "service_active": True}
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
            audit.event("processes-stopped", **check_processes(args.source, audit=audit))
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
            audit.event("processes-before-first-delete", **check_processes(args.source, audit=audit))
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
    for name in ("source", "plan", "preview-metadata", "audit"):
        parser.add_argument("--" + name, required=True)
    for name in ("backup-dir", "report", "repeat-plan", "repeat-backup-dir", "repeat-report"):
        parser.add_argument("--" + name)
    parser.add_argument("--preflight-only", action="store_true",
                        help="Wyłącznie kontrola metadanych; bez rezerwacji nazw, stop i zastosowania")
    args = parser.parse_args(argv)
    if not args.preflight_only and not all((args.backup_dir, args.report, args.repeat_plan,
                                           args.repeat_backup_dir, args.repeat_report)):
        parser.error("zastosowanie wymaga wszystkich ścieżek backupu i raportów")
    audit = None
    previous_handlers = {}
    try:
        for path in (args.plan, args.preview_metadata, args.backup_dir, args.report,
                     args.repeat_plan, args.repeat_backup_dir, args.repeat_report, args.audit):
            if path is not None:
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
        details = {}
        if isinstance(exc, InspectionError):
            reason = "process-metadata-unavailable"
            details = exc.details
        if audit:
            audit.data["status"] = "error"
            audit.event("failed", reason=reason, **details)
        print(json.dumps({"status": "error", "reason": reason, "audit": args.audit, **details}))
        return 1
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        if audit:
            audit.stream.close()


if __name__ == "__main__":
    sys.exit(main())
