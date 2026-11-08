"""Trwały dom na hoście i niedestrukcyjna migracja dawnych wolumenów Incusa (PT-189)."""
import fcntl
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import PurePosixPath

from . import state as bs


class HomeError(RuntimeError):
    pass


def path(slug, card=""):
    from .runner import host_home_dir
    root = host_home_dir(slug)
    from . import exposure
    if slug == exposure.PROJECT:
        record = exposure.current(slug)
        if record and record["manifest"].get("mountContract") == 2:
            return os.path.abspath(root)  # Jawny wspólny home manifestu, również dla kopii karty.
    if card:
        bs.card_box_name(slug, card)  # walidacja identyfikatora
        root = os.path.join(os.path.dirname(root), "cards", str(card).strip().lower(), "home")
    return os.path.abspath(root)


def directory(path):
    """Tworzy katalogi przez deskryptory; żaden składnik nie może być dowiązaniem."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in os.path.abspath(path).split(os.sep)[1:]:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
    finally:
        os.close(fd)


def unpack(archive, target):
    """Czyta wyłącznie backup/volume. Pliki najpierw, symlinki na końcu; bez extractall/chown."""
    links, modes, seen = [], [], set()
    with tarfile.open(archive, "r:*") as tar:
        members = {}
        for member in tar:
            parts = PurePosixPath(member.name).parts
            if member.name.startswith("/") or ".." in parts:
                raise HomeError("niebezpieczna ścieżka w kopii domu")
            if parts[:2] != ("backup", "volume"):
                continue
            rel = parts[2:]
            if not rel:
                if not member.isdir():
                    raise HomeError("niepoprawny korzeń kopii domu")
                seen.add(())
                continue
            if rel in members:
                raise HomeError("powtórzona ścieżka w kopii domu")
            members[rel] = member
        if () not in seen:
            raise HomeError("eksport nie zawiera katalogu backup/volume")
        # Żaden plik ani symlink nie może być rodzicem innego wpisu.
        for rel in members:
            for length in range(1, len(rel)):
                parent = members.get(rel[:length])
                if parent is not None and not parent.isdir():
                    raise HomeError("plik lub dowiązanie jako rodzic w kopii domu")
        for rel, member in sorted(members.items(), key=lambda pair: len(pair[0])):
            dest = os.path.join(target, *rel)
            os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
            if member.isdir():
                os.makedirs(dest, mode=0o700, exist_ok=True)
                modes.append((dest, member.mode & 0o777))
            elif member.issym():
                links.append((dest, member.linkname))
            elif member.isfile() or member.islnk():
                source = member
                visited = set()
                while source.islnk():
                    if source.name in visited:
                        raise HomeError("cykl twardych dowiązań w kopii domu")
                    visited.add(source.name)
                    link = PurePosixPath(source.linkname).parts
                    if link[:2] != ("backup", "volume") or ".." in link:
                        raise HomeError("twarde dowiązanie poza kopię domu")
                    source = members.get(link[2:])
                    if source is None:
                        raise HomeError("brak celu twardego dowiązania")
                if not source.isfile():
                    raise HomeError("niepoprawny cel twardego dowiązania")
                with tar.extractfile(source) as src, open(dest, "xb") as dst:
                    shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
                os.chmod(dest, source.mode & 0o777)
                os.utime(dest, (source.mtime, source.mtime))
            else:
                raise HomeError("plik specjalny w kopii domu — migracja przerwana")
        for dest, link in links:
            os.symlink(link, dest)
        for dest, mode in reversed(modes):
            os.chmod(dest, mode)
    os.chmod(target, 0o700)


def attach(builder, name, slug, card=""):
    """Pod blokadą, bez czynnych kroków. Stare wolumeny i archiwum eksportu zostają zachowane."""
    target = path(slug, card)
    parent = os.path.dirname(target)
    directory(parent)
    lock = os.open(os.path.join(parent, "home.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            _attach(builder, name, slug, target, parent)
        except tarfile.TarError as exc:
            raise HomeError("niepoprawne archiwum eksportu domu — stare dane zachowane") from exc
    finally:
        os.close(lock)


def _attach(builder, name, slug, target, parent):
    incus = builder.incus
    devices = incus.device_show(name)
    home = devices.get("home") or next((d for d in devices.values() if d.get("path") == bs.HOME_PATH), {})
    if home.get("source") == target and not home.get("pool"):
        directory(target)
        os.chmod(target, 0o700)
        return
    if builder.running_steps(slug, name):
        raise HomeError("migracja domu wymaga zakończenia kroków pudełka")
    inst = incus.instance(name)
    if inst is None:
        raise HomeError("brak pudełka podczas podpinania domu")
    # Każdy nieznany dom zostaje na miejscu. Kopia dziedziczy urządzenie projektu z migawki.
    inherited = (inst.get("config") or {}).get(bs.COPY_OF_KEY)
    volume = bs.home_volume(name)
    if home and home.get("source") not in (volume, path(slug), bs.home_volume(inherited) if inherited else ""):
        raise HomeError("nieznane montowanie domu — zachowano bez zmian")
    if home.get("pool") and home.get("pool") != builder.settings.pool:
        raise HomeError("dom pochodzi z innej puli — zachowano bez zmian")
    if str(inst.get("status", "")).lower() == "running":
        incus.stop(name)
    # Eksport wolumenu musi być spójny: żadna inna instancja nie może go używać.
    legacy = any(v.get("name") == volume and v.get("type", "custom") == "custom"
                 for v in incus.volume_list(builder.settings.pool))
    if home.get("pool") and home.get("source") == volume and not legacy:
        raise HomeError("brak podpiętego wolumenu domu w liście Incusa")
    if legacy:
        for other in incus.list():
            if other.get("name") == name:
                continue
            if any(d.get("source") == volume and d.get("pool") == builder.settings.pool
                   for d in (other.get("expanded_devices") or other.get("devices") or {}).values()):
                raise HomeError("stary wolumen domu jest podpięty do innego pudełka")
        _migrate(incus, builder.settings.pool, volume, target, parent)
    else:
        directory(target)
        os.chmod(target, 0o700)
    # Właściciel katalogu hosta jest użytkownikiem agent wewnątrz kontenera, także podczas budowy.
    uid, gid, au, ag = os.getuid(), os.getgid(), builder.settings.agent_uid, builder.settings.agent_gid
    idmap = f"both {uid} {au}" if uid == gid and au == ag else f"uid {uid} {au}\ngid {gid} {ag}"
    incus.config_set(name, {"raw.idmap": idmap})
    # Zastąpienie całego urządzenia usuwa pool (device set zachowałby stare pole).
    key = next((k for k, d in devices.items() if d.get("path") == bs.HOME_PATH), "home")
    incus.device_replace(name, key, "disk", {"source": target, "path": bs.HOME_PATH})


def _migrate(incus, pool, volume, target, parent):
    receipt = os.path.join(parent, "home-migration.json")
    try:
        with os.fdopen(os.open(receipt, os.O_RDONLY | os.O_NOFOLLOW), encoding="utf-8") as fh:
            saved = json.load(fh)
    except FileNotFoundError:
        saved = {}
    if not isinstance(saved, dict):
        raise HomeError("niepoprawny zapis migracji domu")
    if saved:
        if saved.get("pool") != pool or saved.get("volume") != volume:
            raise HomeError("niezgodny zapis migracji domu")
        staging = saved.get("staging", "")
        if os.path.dirname(staging) != parent or not os.path.basename(staging).startswith(".home-migration-"):
            raise HomeError("niepoprawny zapis migracji domu")
        payload = os.path.join(staging, "payload")
        if not os.path.exists(payload) and os.path.isdir(target) and not os.path.islink(target):
            return  # publikacja zakończona; powtórka po awarii lub po odtworzeniu starej migawki
    else:
        if os.path.lexists(target) and (os.path.islink(target) or not os.path.isdir(target) or os.listdir(target)):
            raise HomeError("dom hosta ma dane bez zapisu migracji — odmowa nadpisania")
        staging = tempfile.mkdtemp(prefix=".home-migration-", dir=parent)
        archive = os.path.join(staging, "volume.tar")
        incus.volume_export(pool, volume, archive)
        payload = os.path.join(staging, "payload")
        os.mkdir(payload, 0o700)
        unpack(archive, payload)
        # Zapis przed rename pozwala wznowić przerwaną publikację bez ponownego eksportu.
        with open(receipt, "x", encoding="utf-8") as fh:
            json.dump({"pool": pool, "volume": volume, "staging": staging}, fh)
            fh.flush()
            os.fsync(fh.fileno())
    if os.path.lexists(target):
        if os.path.islink(target) or not os.path.isdir(target) or os.listdir(target):
            raise HomeError("konflikt podczas publikacji domu — dane zachowane")
        os.rmdir(target)  # wyłącznie pusty katalog, pod blokadą
    os.rename(payload, target)
