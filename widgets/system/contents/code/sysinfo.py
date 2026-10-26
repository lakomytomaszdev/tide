"""Pomocnik widżetu System: to, czego nie dają czujniki ksystemstats. Wypisuje JSON na stdout.

Wywoływany z QML przez silnik „executable” Plasma5Support (/usr/bin/python3 sysinfo.py …):

  info                 nazwa komputera, czy jest ksystemstats, które aplikacje da się otworzyć
  mounts <ścieżka>…    partycje zawierające ścieżki (np. / /home): urządzenie, UUID, identyfikator
                       czujnika ksystemstats (disk/<id>/…) i zajętość ze statvfs jako zapas
  top <n>              n programów zajmujących najwięcej pamięci, procesy zgrupowane po programie

Identyfikator czujnika dysku liczymy tak samo jak ksystemstats (VolumeObject::idHelper):
UUID systemu plików, a bez niego etykieta albo nazwa urządzenia (np. nvme0n1p2). UUID i etykietę
bierzemy z dowiązań w /dev/disk/by-uuid i /dev/disk/by-label, więc nie trzeba uprawnień roota.

Pamięć procesu to pamięć prywatna (rezydentna minus współdzielona, z /proc/<pid>/statm), więc
biblioteki dzielone przez procesy jednego programu nie liczą się wielokrotnie. PSS byłby dokładniejszy,
ale odczyt smaps_rollup wszystkich procesów trwa ok. 350 ms. Proces przypisujemy do programu po nazwie
pliku z argv[0] (procesy treści Firefoksa liczą się jako firefox), a bez wiersza poleceń po comm.
"""
import json
import os
import shutil
import socket
import sys

PROC = "/proc"
DEV_DISK = "/dev/disk"
# Aplikacje otwierane przyciskami: pierwszy znaleziony plik .desktop wygrywa.
APPS = {
    "systemMonitor": ["org.kde.plasma-systemmonitor", "org.kde.ksysguard"],
    "diskUsage": ["org.kde.filelight"],
}
KSYSTEMSTATS_SERVICE = "org.kde.ksystemstats1.service"


def data_dirs() -> list[str]:
    home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [home] + [d for d in system.split(":") if d]


def desktop_file(app_ids: list[str], dirs: list[str]) -> str:
    for app_id in app_ids:
        for base in dirs:
            if os.path.isfile(os.path.join(base, "applications", app_id + ".desktop")):
                return app_id
    return ""


def has_ksystemstats(dirs: list[str]) -> bool:
    """Pakiet ksystemstats: usługa D-Bus (start na żądanie) albo sam program w PATH."""
    if any(os.path.isfile(os.path.join(base, "dbus-1", "services", KSYSTEMSTATS_SERVICE)) for base in dirs):
        return True
    return shutil.which("ksystemstats") is not None


def info(dirs: list[str] | None = None) -> dict:
    dirs = data_dirs() if dirs is None else dirs
    return {
        "hostname": socket.gethostname(),
        "ksystemstats": has_ksystemstats(dirs),
        "apps": {key: desktop_file(ids, dirs) for key, ids in APPS.items()},
    }


def unescape_mount(text: str) -> str:
    """Spacje i inne znaki w /proc/self/mountinfo są zapisane ósemkowo (\\040)."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 3 < len(text) and all(c in "01234567" for c in text[i + 1:i + 4]):
            out.append(chr(int(text[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def read_mountinfo(path: str) -> list[dict]:
    mounts = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            left, sep, right = line.partition(" - ")
            fields, tail = left.split(), right.split()
            if not sep or len(fields) < 5 or len(tail) < 2:
                continue
            mounts.append({"target": unescape_mount(fields[4]), "fstype": tail[0], "source": unescape_mount(tail[1])})
    return mounts


def mount_for(path: str, mounts: list[dict]) -> dict | None:
    """Partycja, na której leży ścieżka: najdłuższy pasujący punkt montowania (ostatni wygrywa)."""
    path = os.path.normpath(path)
    best = None
    for mount in mounts:
        target = mount["target"]
        inside = path == target or target == "/" or path.startswith(target.rstrip("/") + "/")
        if inside and (best is None or len(target) >= len(best["target"])):
            best = mount
    return best


def links_by_device(folder: str) -> dict:
    """realpath urządzenia → nazwa dowiązania (UUID albo etykieta) z /dev/disk/by-*."""
    out = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        try:
            out[os.path.realpath(os.path.join(folder, name))] = unescape_label(name)
        except OSError:
            pass
    return out


def unescape_label(name: str) -> str:
    """udev zapisuje znaki specjalne w nazwach dowiązań jako \\x20."""
    try:
        return name.encode("latin-1").decode("unicode_escape") if "\\x" in name else name
    except UnicodeDecodeError:
        return name


def describe_mount(path: str, mounts: list[dict], dev_disk: str = DEV_DISK, statvfs=os.statvfs) -> dict | None:
    mount = mount_for(path, mounts)
    if mount is None:
        return None
    source = mount["source"]
    device = os.path.realpath(source) if source.startswith("/") else source
    uuid = links_by_device(os.path.join(dev_disk, "by-uuid")).get(device, "")
    label = links_by_device(os.path.join(dev_disk, "by-label")).get(device, "")
    sensor = uuid or label or os.path.basename(device)
    out = {"path": path, "target": mount["target"], "source": source, "device": device, "fstype": mount["fstype"],
           "uuid": uuid, "label": label, "sensorId": sensor,
           "size": None, "free": None, "used": None, "usedPercent": None}
    try:
        st = statvfs(mount["target"])
        # Jak ksystemstats (KIO::fileSystemFreeSpace): rozmiar i miejsce dostępne dla użytkownika.
        size, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        out.update(size=size, free=free, used=size - free,
                   usedPercent=round((size - free) * 100.0 / size, 2) if size else None)
    except OSError:
        pass
    return out


def mounts_report(paths: list[str], mountinfo: str = PROC + "/self/mountinfo", dev_disk: str = DEV_DISK,
                  statvfs=os.statvfs) -> dict:
    mounts = read_mountinfo(mountinfo)
    seen, out = set(), []
    for path in paths:
        described = describe_mount(path, mounts, dev_disk, statvfs)
        # /home bez osobnej partycji to ta sama partycja co / — pokazujemy ją raz.
        if described and described["target"] not in seen:
            seen.add(described["target"])
            out.append(described)
    # Wszystkie punkty montowania: widżet przypisuje worktrees do partycji po najdłuższym
    # prefiksie, więc worktree w /tmp (tmpfs) nie może się policzyć jako leżący na /.
    targets = sorted({m["target"] for m in mounts})
    return {"mounts": out, "targets": targets}


def program_name(cmdline: bytes, comm: str) -> str:
    argv0 = cmdline.split(b"\0", 1)[0].decode("utf-8", "replace").strip()
    if argv0 and not os.path.exists(argv0):
        # Część programów (Chrome, Electron) przepisuje wiersz poleceń w jeden napis ze spacjami.
        argv0 = argv0.split()[0]
    name = os.path.basename(argv0.rstrip("/")).rstrip(":") if argv0 else ""
    if name.endswith("-bin") and len(name) > 4:
        name = name[:-4]  # firefox-bin → firefox
    return name or comm.strip()


def top_processes(limit: int, proc: str = PROC, page_size: int | None = None) -> dict:
    page = page_size or os.sysconf("SC_PAGE_SIZE")
    groups: dict[str, dict] = {}
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        pids = []
    for pid in pids:
        base = os.path.join(proc, pid)
        try:
            with open(os.path.join(base, "statm"), encoding="ascii") as fh:
                fields = fh.read().split()
            private = (int(fields[1]) - int(fields[2])) * page
            if private <= 0:
                continue  # wątki jądra i procesy bez własnej pamięci
            with open(os.path.join(base, "cmdline"), "rb") as fh:
                cmdline = fh.read()
            with open(os.path.join(base, "comm"), encoding="utf-8", errors="replace") as fh:
                comm = fh.read()
        except (OSError, ValueError, IndexError):
            continue  # proces zakończył się w trakcie odczytu
        name = program_name(cmdline, comm)
        group = groups.setdefault(name, {"name": name, "bytes": 0, "count": 0})
        group["bytes"] += private
        group["count"] += 1
    ordered = sorted(groups.values(), key=lambda g: (-g["bytes"], g["name"]))
    return {"processes": ordered[:max(0, limit)]}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 2
    command, args = argv[0], argv[1:]
    if command == "info":
        result = info()
    elif command == "mounts":
        result = mounts_report(args or ["/", "/home"])
    elif command == "top":
        result = top_processes(int(args[0]) if args else 3)
    else:
        print(f"Nieznane polecenie: {command}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
