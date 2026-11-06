"""Konserwacja kopii dzienników, wyłącznie na jawnie wskazanym katalogu.

Domyślny podgląd jest tylko do odczytu. Zastosowanie wymaga zapisanego podglądu,
zewnętrznego backupu oraz zatrzymania wszystkich zapisujących procesów.
Instrukcja: docs/cardflow-cleanup.md. Moduł nie importuje usługi ani jej ustawień.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

HASH = re.compile(r"[0-9a-f]{12}\Z")
CARD = re.compile(r"K-[1-9][0-9]{0,8}\.jsonl\Z")
VERSION = 1


class DeletedButUnsynced(OSError):
    """Plik usunięto, ale trwałość zmiany katalogu nie została potwierdzona."""


def directory(path):
    """Otwiera każdy składnik bez podążania za dowiązaniami."""
    path = os.path.abspath(path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def identity(st):
    return [st.st_dev, st.st_ino, st.st_mode, st.st_nlink,
            st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def read_regular(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("not-regular")
        data = stream.read()
        if identity(before) != identity(os.fstat(stream.fileno())):
            raise ValueError("changed-during-read")
    return data, before


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-key")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("non-json-number")


def decode(data):
    return json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant)


def qualify(data, card):
    """Wszystkie zdarzenia muszą być poprawne; wszystkie starty jednoznacznie testowe."""
    try:
        lines = data.decode("utf-8").splitlines()
        if not lines:
            return "empty"
        starts = []
        for seq, line in enumerate(lines, 1):
            event = decode(line)
            if (not isinstance(event, dict) or type(event.get("v")) is not int
                    or event["v"] != 1 or type(event.get("seq")) is not int
                    or event["seq"] != seq or event.get("card") != card
                    or not isinstance(event.get("type"), str) or not event["type"]
                    or not isinstance(event.get("ts"), str)):
                return "invalid-event"
            if datetime.fromisoformat(event["ts"]).tzinfo is None:
                return "invalid-event"
            if event["type"] == "step.started":
                starts.append(event)
        if not starts:
            return "no-step-started"
        for event in starts:
            if "repo" in event and event["repo"] != "":
                return "repo-present-or-unknown"
            worktree = event.get("worktree")
            if (not isinstance(worktree, str) or not worktree.startswith("/")
                    or "\x00" in worktree):
                return "unknown-worktree"
            try:
                # Historyczny katalog może już nie istnieć; inne błędy wykluczają kopię.
                resolved = os.path.realpath(worktree, strict=os.path.ALLOW_MISSING)
            except OSError:
                return "unknown-worktree"
            if not resolved.startswith("/tmp/"):
                return "worktree-outside-tmp"
        return "candidate"
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        return "invalid-data"


def inspect(parent, name, rel, known):
    st = os.stat(name, dir_fd=parent, follow_symlinks=False)
    row = {"path": rel, "identity": identity(st), "reason": "unknown-path"}
    if stat.S_ISLNK(st.st_mode):
        row["reason"] = "symlink"
    elif stat.S_ISREG(st.st_mode):
        try:
            data, checked = read_regular(parent, name)
            if identity(st) != identity(checked):
                raise ValueError("changed")
            row["sha256"] = hashlib.sha256(data).hexdigest()
            if known:
                row["reason"] = qualify(data, name[:-6]) if st.st_nlink == 1 else "hardlink"
        except (OSError, ValueError):
            row["reason"] = "unreadable-or-changing"
    elif known:
        row["reason"] = "not-regular"
    return row


def snapshot(source):
    rows = []
    root = directory(source)
    try:
        for name in sorted(os.listdir(root)):
            st = os.stat(name, dir_fd=root, follow_symlinks=False)
            if HASH.fullmatch(name) and stat.S_ISDIR(st.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                try:
                    for leaf in sorted(os.listdir(child)):
                        rows.append(inspect(child, leaf, name + "/" + leaf, bool(CARD.fullmatch(leaf))))
                finally:
                    os.close(child)
            else:
                rows.append(inspect(root, name, name, False))
    finally:
        os.close(root)
    return rows


def preview(source):
    source = os.path.abspath(source)
    rows = snapshot(source)
    return {"version": VERSION, "source": source, "entries": rows,
            "counts": dict(Counter(row["reason"] for row in rows))}


def external(source, path):
    source, target = os.path.realpath(source), os.path.realpath(path)
    if os.path.commonpath([source, target]) in (source, target):
        raise ValueError("output-overlaps-source")


def write_json(path, value):
    """Wyłączny zapis: nigdy nie nadpisuje raportu, planu ani dowiązania."""
    path = Path(path)
    parent = directory(path.parent)
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
    finally:
        os.close(parent)


def save_report(stream, report):
    stream.seek(0)
    json.dump(report, stream, ensure_ascii=True, indent=2)
    stream.write("\n")
    stream.truncate()
    stream.flush()
    os.fsync(stream.fileno())


def load_json(path):
    path = Path(path)
    parent = directory(path.parent)
    try:
        data, _ = read_regular(parent, path.name)
        return decode(data)
    finally:
        os.close(parent)


def valid_candidate_path(path):
    parts = path.split("/")
    return len(parts) == 2 and HASH.fullmatch(parts[0]) and CARD.fullmatch(parts[1])


def candidate_bytes(source, row):
    if not valid_candidate_path(row["path"]):
        raise ValueError("invalid-candidate-path")
    parent = directory(Path(source) / Path(row["path"]).parent)
    try:
        data, st = read_regular(parent, Path(row["path"]).name)
        if identity(st) != row["identity"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("changed-since-preview")
        if qualify(data, Path(row["path"]).name[:-6]) != "candidate":
            raise ValueError("no-longer-candidate")
        return data
    finally:
        os.close(parent)


def sync_dir(path):
    fd = directory(path)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def create_backup(source, backup, rows):
    """Cały backup i manifest muszą przejść odczyt kontrolny przed usunięciem."""
    parent = directory(Path(backup).parent)
    try:
        os.mkdir(Path(backup).name, mode=0o700, dir_fd=parent)
    finally:
        os.close(parent)
    sync_dir(Path(backup).parent)
    files = Path(backup) / "files"
    files.mkdir(mode=0o700)
    for row in rows:
        data = candidate_bytes(source, row)
        target = files / row["path"]
        target.parent.mkdir(mode=0o700, exist_ok=True)
        with target.open("xb") as stream:
            os.chmod(target, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        sync_dir(target.parent)
    sync_dir(files)
    write_json(Path(backup) / "manifest.json", {"version": VERSION, "source": source, "entries": rows})
    if verify_backup(backup) != {"version": VERSION, "source": source, "entries": rows}:
        raise ValueError("backup-manifest-mismatch")


def verify_backup(backup):
    manifest = load_json(Path(backup) / "manifest.json")
    if manifest["version"] != VERSION:
        raise ValueError("invalid-backup-version")
    for row in manifest["entries"]:
        if not valid_candidate_path(row["path"]):
            raise ValueError("invalid-backup-path")
        target = Path(backup) / "files" / row["path"]
        parent = directory(target.parent)
        try:
            data, _ = read_regular(parent, target.name)
        finally:
            os.close(parent)
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("backup-checksum-mismatch")
    return manifest


def remove_candidate(source, row):
    # Zatrzymanie wszystkich piszących procesów jest warunkiem operacji. Odczyt
    # i stat chronią dodatkowo przed zmianą od podglądu i podmianą na dowiązanie.
    candidate_bytes(source, row)
    path = Path(source) / row["path"]
    parent = directory(path.parent)
    try:
        if identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != row["identity"]:
            return False
        os.unlink(path.name, dir_fd=parent)
        try:
            os.fsync(parent)
        except OSError as exc:
            raise DeletedButUnsynced from exc
    finally:
        os.close(parent)
    root = directory(source)
    try:
        try:
            os.rmdir(path.parent.name, dir_fd=root)
            os.fsync(root)
        except OSError:
            pass  # Zachowujemy niepusty katalog; nigdy nie usuwamy rekurencyjnie.
    finally:
        os.close(root)
    return True


def apply(plan, backup, report_path, *, writers_stopped=False):
    if not writers_stopped:
        raise ValueError("writers-must-be-stopped")
    source = plan["source"]
    if plan["version"] != VERSION or not os.path.isabs(source):
        raise ValueError("invalid-plan")
    external(source, backup)
    external(source, report_path)
    external(backup, report_path)
    if Path(backup).exists() or Path(report_path).exists():
        raise ValueError("output-already-exists")
    # Sprawdzamy możliwość utworzenia raportu przed jakąkolwiek mutacją źródła.
    report_fd = directory(Path(report_path).parent)
    os.close(report_fd)
    before = snapshot(source)
    if any(stat.S_ISREG(row["identity"][2]) and "sha256" not in row for row in before):
        raise ValueError("cannot-check-preserved-file-checksums")
    current = {row["path"]: row for row in before}
    rows = [row for row in plan["entries"] if row["reason"] == "candidate"]
    if len({row["path"] for row in rows}) != len(rows):
        raise ValueError("duplicate-candidate")
    eligible = [row for row in rows if current.get(row["path"]) == row]
    report = {"version": VERSION, "source": source, "backup": os.path.abspath(backup),
              "qualified": len(rows), "deleted": [], "skipped": {},
              "backup_verified": False, "preserved_verified": False, "status": "error"}
    for row in rows:
        if row not in eligible:
            report["skipped"][row["path"]] = "changed-since-preview"
    for row in before:
        if row["reason"] != "candidate":
            report["skipped"][row["path"]] = row["reason"]
        elif row["path"] not in {r["path"] for r in rows}:
            report["skipped"][row["path"]] = "not-in-preview"
    parent = directory(Path(report_path).parent)
    try:
        fd = os.open(Path(report_path).name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        save_report(stream, report)
        try:
            if eligible:
                create_backup(source, backup, eligible)
                report["backup_verified"] = True
                save_report(stream, report)
            for row in eligible:
                try:
                    if remove_candidate(source, row):
                        report["deleted"].append(row["path"])
                    else:
                        report["skipped"][row["path"]] = "changed-before-delete"
                except DeletedButUnsynced:
                    report["deleted"].append(row["path"])
                    raise
                except (OSError, ValueError):
                    report["skipped"][row["path"]] = "changed-or-unreadable-before-delete"
                save_report(stream, report)
            report["preserved"] = [row for row in before if row["path"] not in report["deleted"]]
            report["preserved_verified"] = snapshot(source) == report["preserved"]
            report["status"] = "ok" if report["preserved_verified"] else "error"
        except (OSError, ValueError):
            report["error"] = "backup-delete-or-verification-failed"
        report["deleted_count"] = len(report["deleted"])
        report["skipped_count"] = len(report["skipped"])
        save_report(stream, report)
    return report


def verify_report(report):
    if report["status"] != "ok" or not report["preserved_verified"]:
        raise ValueError("operation-not-verified")
    external(report["source"], report["backup"])
    if report["backup_verified"]:
        verify_backup(report["backup"])
    if snapshot(report["source"]) != report["preserved"]:
        raise ValueError("preserved-set-or-checksum-changed")
    return {"status": "ok", "backup_verified": report["backup_verified"], "preserved_verified": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="Jawny katalog kopii cardflow")
    parser.add_argument("--plan", help="Plik podglądu: zapis w podglądzie, odczyt przy --apply")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir")
    parser.add_argument("--report")
    parser.add_argument("--writers-stopped", action="store_true", help="Potwierdza zatrzymanie wszystkich piszących procesów")
    parser.add_argument("--verify", metavar="REPORT", help="Sprawdza backup i zachowane dane w zatrzymanym oknie")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            if args.apply or args.source or args.plan or args.report or args.backup_dir:
                parser.error("--verify jest osobnym poleceniem")
            result = verify_report(load_json(args.verify))
        elif args.apply:
            if not all((args.source, args.plan, args.backup_dir, args.report, args.writers_stopped)):
                parser.error("--apply wymaga --source, --plan, --backup-dir, --report i --writers-stopped")
            plan = load_json(args.plan)
            if plan["source"] != os.path.abspath(args.source):
                raise ValueError("source-does-not-match-preview")
            external(args.source, args.plan)
            result = apply(plan, args.backup_dir, args.report, writers_stopped=True)
        else:
            if not args.source or args.report or args.backup_dir or args.writers_stopped:
                parser.error("podgląd wymaga --source; opcjonalny zapis przez --plan")
            if args.plan:
                external(args.source, args.plan)
            result = preview(args.source)
            if args.plan:
                write_json(args.plan, result)
            count = result["counts"].get("candidate", 0)
            result = {"status": "ok", "counts": result["counts"], "total": len(result["entries"]),
                      "candidates": count, "skipped": len(result["entries"]) - count}
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result["status"] == "ok" else 1
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        # Treść wyjątku parsera JSON mogłaby zawierać fragment dziennika.
        print(json.dumps({"status": "error", "reason": "operation-refused-or-failed"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
