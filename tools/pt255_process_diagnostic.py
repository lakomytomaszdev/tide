"""Odczyt wybranych metadanych PID na hoście, bez preflightu i konserwacji.

Nie czyta cmdline, environ ani celów deskryptorów. Każdy niedostępny odczyt
zapisuje jako fazę i errno. Kod 0 oznacza zapis diagnostyki, nie zgodę na usuwanie.
"""
import argparse
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import sys


def observe(phase, read):
    try:
        return {"phase": phase, "ok": True, "value": read()}
    except OSError as error:
        return {"phase": phase, "ok": False, "errno": error.errno}
    except (ValueError, IndexError, UnicodeError, StopIteration):
        return {"phase": phase, "ok": False, "errno": None, "error": "invalid-metadata"}


def status_ids(path):
    selected = {}
    for line in path.read_bytes().splitlines():
        key, separator, value = line.partition(b":")
        if separator and key in (b"Uid", b"Gid", b"NSpid"):
            selected[key.decode("ascii")] = [int(part) for part in value.split()]
    if len(selected.get("Uid", [])) != 4 or len(selected.get("Gid", [])) != 4:
        raise ValueError("invalid-ids")
    return selected


def start_ticks(path):
    data = path.read_bytes()
    end = data.rfind(b")")  # comm może zawierać spacje i nawiasy.
    if end < 0:
        raise ValueError("invalid-stat")
    return int(data[end + 1:].split()[19])  # Pole 22; po comm zaczyna się pole 3.


def fd_flags(path):
    line = next(line for line in path.read_bytes().splitlines() if line.startswith(b"flags:"))
    return int(line.split()[1], 8)


def directory_owner(path):
    info = path.stat()
    return {"uid": info.st_uid, "gid": info.st_gid}


def diagnose(pid, proc=Path("/proc")):
    target = proc / str(pid)
    facts = {}

    def record(key, read):
        facts[key] = observe(key, read)

    record("proc-directory-before", lambda: directory_owner(target))
    record("start-ticks-before", lambda: start_ticks(target / "stat"))
    record("caller-status-ids", lambda: status_ids(proc / "self" / "status"))
    record("caller-pid-namespace", lambda: os.readlink(proc / "self" / "ns" / "pid"))
    record("target-comm", lambda: (target / "comm").read_bytes().decode("utf-8").rstrip("\n"))
    record("target-status-ids", lambda: status_ids(target / "status"))
    record("target-pid-namespace", lambda: os.readlink(target / "ns" / "pid"))
    record("target-fd0-flags", lambda: fd_flags(target / "fdinfo" / "0"))
    record("boot-id", lambda: (proc / "sys/kernel/random/boot_id").read_bytes().decode("ascii").strip())
    record("boot-time-unix", lambda: int(next(line.split()[1] for line in
        (proc / "stat").read_bytes().splitlines() if line.startswith(b"btime "))))
    record("clock-ticks-per-second", lambda: os.sysconf("SC_CLK_TCK"))
    record("start-ticks-after", lambda: start_ticks(target / "stat"))
    record("proc-directory-after", lambda: directory_owner(target))

    def value(key):
        return facts[key].get("value") if facts[key]["ok"] else None

    before, after = value("proc-directory-before"), value("proc-directory-after")
    start, end = value("start-ticks-before"), value("start-ticks-after")
    absent = all(not facts[key]["ok"] and facts[key].get("errno") == errno.ENOENT
                 for key in ("proc-directory-before", "proc-directory-after"))
    target_ids, caller_ids = value("target-status-ids"), value("caller-status-ids")
    caller_ns, target_ns = value("caller-pid-namespace"), value("target-pid-namespace")
    comparison = {
        "existence": "absent-at-both-probes" if absent else "present-at-both-probes" if before and after else "unknown-or-changed",
        "start-identity-stable": start == end if start is not None and end is not None else None,
        "scanner-proc-directory-uid-matches-getuid": before["uid"] == os.getuid() if before else None,
        "effective-uids-equal": target_ids["Uid"][1] == caller_ids["Uid"][1] if target_ids and caller_ids else None,
        "filesystem-uids-equal": target_ids["Uid"][3] == caller_ids["Uid"][3] if target_ids and caller_ids else None,
        "effective-gids-equal": target_ids["Gid"][1] == caller_ids["Gid"][1] if target_ids and caller_ids else None,
        "pid-namespaces-equal": caller_ns == target_ns if caller_ns and target_ns else None,
        "historical-pid-identity": "unknown-no-historical-start-time",
    }
    boot, frequency = value("boot-time-unix"), value("clock-ticks-per-second")
    if start is not None and boot is not None and frequency:
        facts["target-started-utc"] = observe("target-started-utc", lambda:
            datetime.fromtimestamp(boot + start / frequency, timezone.utc).isoformat())
    return {"version": 1, "ticket": "PT-255", "recorded-at": datetime.now(timezone.utc).isoformat(),
            "target-pid": pid, "caller": {"pid": os.getpid(), "getuid": os.getuid(),
                "resuid": list(os.getresuid()), "resgid": list(os.getresgid())},
            "facts": facts, "comparisons": comparison, "permission-cause": "not-established",
            "limits": ["diagnostic-only-not-preflight", "non-atomic-proc-observations",
                       "historical-pid-identity-unverified"]}


def write_report(path, report):
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
    finally:
        os.close(parent)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.pid <= 0 or not args.report.is_absolute():
        parser.error("wymagane dodatnie PID i bezwzględna ścieżka raportu")
    try:
        write_report(args.report, diagnose(args.pid))
        print(json.dumps({"status": "diagnostic-recorded", "report": str(args.report)}))
        return 0
    except (OSError, ValueError, OverflowError) as error:
        print(json.dumps({"status": "diagnostic-failed", "errno": getattr(error, "errno", None)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
