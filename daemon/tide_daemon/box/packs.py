"""Jednorazowe kopie danych karty. Źródła nigdy nie są urządzeniami Incusa.

Skaner to filtr, nie gwarancja. Trafienia zawierają wyłącznie ścieżkę i nazwę
reguły, nigdy sekret. `files` pozwala ręcznie zawęzić dopasowania wzorca.
"""
import glob
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime

NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
RULES = [(name, re.compile(pattern, re.I)) for name, pattern in (
    ("sk-ant-", b"sk-ant-"), ("ghp_", b"ghp_"), ("glpat-", b"glpat-"),
    ("PRIVATE KEY", rb"BEGIN[^\r\n]*PRIVATE KEY"),
    ("Authorization:", rb"Authorization\s*:"), ("password=", rb"password\s*="))]
FILTER_NOTE = "Skaner sekretów to filtr, nie gwarancja. Trafienia blokują paczkę; wybierz ręcznie pliki w polu files."
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILES = 10000


class PackError(ValueError):
    pass


def _since(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def validate_specs(raw, errors):
    if not isinstance(raw, list):
        errors.append("packs: oczekiwano listy")
        return []
    out, names = [], set()
    for entry in raw:
        # Nazwy istniejących kopii: zgodność kontraktu CLI PT-191. Zatwierdzenie
        # nowej paczki wymaga pełnego opisu źródła.
        name = entry.get("name") if isinstance(entry, dict) else entry
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
            errors.append("packs: niepoprawna albo powtórzona nazwa")
            continue
        names.add(name)
        if isinstance(entry, dict):
            if set(entry) - {"name", "from", "since", "files"}:
                errors.append(f"packs.{name}: nieznane klucze")
            if not isinstance(entry.get("from"), str) or not entry["from"].strip():
                errors.append(f"packs.{name}.from: wymagany wzorzec")
            else:
                try:
                    _pattern(entry["from"])
                except PackError as exc:
                    errors.append(str(exc))
            if "since" in entry:
                try:
                    _since(entry["since"])
                except (TypeError, ValueError, AttributeError):
                    errors.append(f"packs.{name}.since: oczekiwano daty ISO 8601")
            if "files" in entry and (not isinstance(entry["files"], list) or
                                      any(not isinstance(p, str) for p in entry["files"])):
                errors.append(f"packs.{name}.files: oczekiwano listy ścieżek")
        out.append(entry)
    return out


def _pattern(value):
    path = os.path.abspath(os.path.expanduser(value))
    home = os.path.expanduser("~")
    for folder in (".ssh", ".gnupg", ".config"):
        banned = os.path.join(home, folder)
        if path == banned or path.startswith(banned + "/"):
            raise PackError(f"packs: katalog z poświadczeniami jest zabroniony: {banned}")
    claude = os.path.join(home, ".claude")
    if path == claude or (path.startswith(claude + "/") and
                          not path.startswith(claude + "/projects/")):
        raise PackError("packs: wolno wybrać konkretne dzienniki .claude/projects, nigdy całe ~/.claude")
    return path


def _read(path):
    """O_NOFOLLOW na każdym komponencie, także przy podmianie katalogu w wyścigu."""
    parts = os.path.abspath(path).split(os.sep)[1:]
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        file = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file, "rb") as fh:
            info = os.fstat(fh.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                raise PackError(f"packs: nie jest zwykłym plikiem albo przekracza limit: {path}")
            data = fh.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise PackError(f"packs: plik przekracza limit: {path}")
            after = os.fstat(fh.fileno())
            if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != info.st_size:
                raise PackError("packs: źródło zmieniło się podczas odczytu")
            return data, info
    finally:
        os.close(fd)


def inspect(spec):
    if not isinstance(spec, dict):
        raise PackError("Nowa zgoda na paczkę wymaga name i from")
    pattern = _pattern(spec["from"])
    selected = {os.path.abspath(os.path.expanduser(p)) for p in spec.get("files", [])}
    found = set()
    for path in glob.iglob(pattern, recursive=True):
        found.add(path)
        if len(found) > MAX_FILES:
            raise PackError("packs: za dużo dopasowań; zawęź wzorzec")
    paths = sorted(found)
    if "files" in spec and not selected.issubset(paths):
        raise PackError("packs.files: wybór musi należeć do dopasowań from")
    data, hits, total = {}, [], 0
    since = _since(spec["since"]) if spec.get("since") else None
    for path in paths:
        if "files" in spec and path not in selected:
            continue
        _pattern(path)  # również szerokie wzorce, np. ~/.*/*
        try:
            content, info = _read(path)
        except OSError as exc:
            raise PackError(f"packs: dowiązanie lub nieczytelne źródło: {path}") from exc
        if since is not None and info.st_mtime < since:
            continue
        total += len(content)
        if total > MAX_TOTAL:
            raise PackError("packs: przekroczono limit 256 MiB; zawęź wzorzec")
        data[path] = content
        hits.extend({"path": path, "rule": name} for name, rule in RULES if rule.search(content))
    report = {"count": len(data), "size": total, "paths": list(data)[:20], "hits": hits,
              "blocked": bool(hits), "scanner": FILTER_NOTE}
    return report, data


def describe(spec):
    try:
        report, _ = inspect(spec)
    except (PackError, OSError) as exc:
        report = {"count": 0, "size": 0, "paths": [], "hits": [], "blocked": True,
                  "scanner": FILTER_NOTE, "error": str(exc)}
    return report


def root(slug, card, env=None):
    from . import state as bs
    from .runner import host_home_dir
    bs.card_box_name(slug, card)
    return os.path.join(os.path.dirname(host_home_dir(slug, env)), "packs", card.lower())


def materialize(slug, card, specs):
    """Kopiuje dokładnie bajty, które przeskanowano, przed zapisem zgody."""
    snapshots = []
    for spec in specs:
        report, data = inspect(spec)
        if report["blocked"]:
            raise PackError(f"paczka {spec['name']}: trafienia skanera; wybierz files")
        snapshots.append((spec["name"], data))
    target = root(slug, card)
    if os.path.lexists(target):
        # Ponowna zgoda na identyczną kopię jest idempotentna. Danych używanych
        # przez biegnący krok nigdy nie zastępujemy ani nie aktualizujemy.
        devices(slug, card, specs)
        old, _ = _read(os.path.join(target, "approved.json"))
        wanted = {name: {p.lstrip("/"): hashlib.sha256(b).hexdigest() for p, b in data.items()}
                  for name, data in snapshots}
        if json.loads(old) == wanted:
            return
        raise PackError("Paczki tej karty są już przygotowane; zmiana wymaga ich uprzątnięcia")
    parent = os.path.dirname(target)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    if os.path.realpath(parent) != parent:
        raise PackError("packs: dowiązanie w katalogu docelowym")
    stage = tempfile.mkdtemp(prefix=".packs-", dir=parent)
    manifest = {}
    try:
        for name, data in snapshots:
            folder = os.path.join(stage, name)
            os.mkdir(folder, 0o700)
            manifest[name] = {}
            for source, content in data.items():
                # Zachowaj strukturę ścieżek od /: brak kolizji dwóch takich samych nazw.
                relative = source.lstrip("/")
                dest = os.path.join(folder, relative)
                os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
                with open(dest, "xb") as fh:
                    fh.write(content)
                os.chmod(dest, 0o400)
                manifest[name][relative] = hashlib.sha256(content).hexdigest()
        with open(os.path.join(stage, "approved.json"), "x", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        os.rename(stage, target)
    finally:
        if os.path.exists(stage):
            shutil.rmtree(stage)


def devices(slug, card, specs, env=None):
    from .runner import Device
    if not specs:
        return []
    target = root(slug, card, env)
    if os.path.realpath(target) != target:
        raise PackError("packs: dowiązanie w kopii")
    manifest, _ = _read(os.path.join(target, "approved.json"))
    approved = json.loads(manifest)
    out = []
    for spec in specs:
        name = spec["name"] if isinstance(spec, dict) else spec
        if not NAME.fullmatch(name):
            raise PackError("packs: niepoprawna nazwa")
        source = os.path.join(target, name)
        if os.path.realpath(source) != source or not os.path.isdir(source):
            raise PackError("packs: brak bezpiecznej kopii")
        if isinstance(spec, (dict, str)):
            expected = approved.get(name)
            if expected is None:
                raise PackError("packs: brak zatwierdzonej kopii")
            actual = {}
            for folder, dirs, files in os.walk(source, followlinks=False):
                if any(os.path.islink(os.path.join(folder, d)) for d in dirs):
                    raise PackError("packs: dowiązanie w kopii")
                for file in files:
                    path = os.path.join(folder, file)
                    content, _info = _read(path)
                    actual[os.path.relpath(path, source)] = hashlib.sha256(content).hexdigest()
            if actual != expected or not os.path.isdir(source):
                raise PackError("packs: kopia zmieniona po zatwierdzeniu")
        out.append(Device("tide-pack-" + card.lower() + "-" + name, source,
                          readonly=True, path="/packs/" + name))
    return out


def cleanup(slug, card, incus, journal):
    from . import state as bs
    from ..cardflow import trialhold
    try:
        states = [trialhold.process_state(journal, key)[1] for key in journal.cards_complete()]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise PackError("packs: nieznana historia procesów — sprzątanie wstrzymane") from exc
    if any(trialhold.preserves_project(st, slug) for st in states):
        raise PackError("packs: nierozstrzygnięty proces — sprzątanie wstrzymane")
    target = root(slug, card)
    if os.path.realpath(os.path.dirname(target)) != os.path.dirname(target):
        raise PackError("packs: dowiązanie w katalogu sprzątania")
    prefix = "tide-pack-" + card.lower() + "-"
    removed = []
    for box in (bs.box_name(slug), bs.card_box_name(slug, card)):
        if incus.instance(box) is None:
            continue
        for name in incus.device_show(box):
            if name.startswith(prefix):
                incus.device_remove(box, name)
                removed.append(name)
    if os.path.lexists(target):
        if os.path.islink(target):
            os.unlink(target)
        else:
            shutil.rmtree(target)
        removed.append(target)
    if removed:
        journal.append(card, "packs.removed", removed=removed)


# Rdzeń v1. Powyższe adaptery PT-173 pozostają dla historycznych wywołań;
# zgoda i urządzenia v1 konsumują poniższe API, bez ponownego czytania źródeł.
SNAPSHOT_VERSION = 1
SNAPSHOT_ID = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class PackSnapshot:
    project: str
    card: str
    name: str
    id: str
    version: int
    directory: str
    files: tuple[SnapshotFile, ...]
    size: int
    scanner: dict

    def to_dict(self):
        """Opis kopii dla manifestu PT-263; sam opis nie stanowi zgody."""
        return asdict(self)


def snapshot_root(slug, env=None):
    """Hostowy magazyn poza home i worktree udostępnianymi pudełku."""
    from . import profile as bp
    bp._check_slug(slug, "projekt")
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or os.path.join(
        env.get("HOME") or os.path.expanduser("~"), ".local", "state")
    if not os.path.isabs(base):
        raise PackError("packs: magazyn wymaga ścieżki bezwzględnej")
    return os.path.join(base, "tide", "pack-snapshots", slug)


def _open_dir(parent, name):
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


@contextmanager
def _store(slug, env=None):
    """Zakotwiczone deskryptory i blokada wspólna dla publikacji/wycofania/usuwania."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in snapshot_root(slug, env).split(os.sep)[1:]:
            if not part or part in (".", ".."):
                raise PackError("packs: niekanoniczna ścieżka magazynu")
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = _open_dir(fd, part)
            os.close(fd)
            fd = child
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def _write_at(parent, name, content):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o400, dir_fd=parent)
    with os.fdopen(fd, "wb") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())


def _read_at(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "rb") as fh:
        info = os.fstat(fh.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
            raise PackError("packs: niedozwolony plik w kopii")
        content = fh.read(MAX_FILE + 1)
        after = os.fstat(fh.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(content) != info.st_size:
            raise PackError("packs: kopia zmieniła się podczas odczytu")
        return content


def _copy_at(parent, relative, content):
    parts = relative.split("/")
    fd = os.dup(parent)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = _open_dir(fd, part)
            os.close(fd)
            fd = child
        _write_at(fd, parts[-1], content)
        os.fsync(fd)
    finally:
        os.close(fd)


def create_snapshot(slug, card, spec, env=None):
    """Skan i niezależna, atomowo publikowana kopia. Nie zatwierdza ani nie montuje.

    Identyfikator karty jest organizacyjny. Dostęp do kopii ogranicza projekt,
    a zatwierdzenie konkretnego id/wersji należy do hostowego manifestu PT-263.
    """
    from . import state as bs
    bs.card_box_name(slug, card)
    errors = []
    validate_specs([spec], errors)
    if errors or not isinstance(spec, dict):
        raise PackError("packs: niepoprawny opis nowej kopii")
    report, data = inspect(spec)
    if report["blocked"]:
        raise PackError("packs: trafienia filtra; wybierz ręcznie files")
    snapshot_id = uuid.uuid4().hex
    files = tuple(SnapshotFile(p.lstrip("/"), len(b), hashlib.sha256(b).hexdigest())
                  for p, b in data.items())
    snapshot = PackSnapshot(slug, card.strip().upper(), spec["name"], snapshot_id,
                            SNAPSHOT_VERSION,
                            os.path.join(snapshot_root(slug, env), snapshot_id, "data"),
                            files, report["size"], report)
    with _store(slug, env) as store:
        if snapshot_id in os.listdir(store):
            raise PackError("packs: identyfikator kopii jest już zajęty")
        stage = ".stage-" + snapshot_id
        os.mkdir(stage, 0o700, dir_fd=store)
        try:
            stage_fd = _open_dir(store, stage)
            try:
                os.mkdir("data", 0o700, dir_fd=stage_fd)
                data_fd = _open_dir(stage_fd, "data")
                try:
                    for path, content in data.items():
                        _copy_at(data_fd, path.lstrip("/"), content)
                    _sync_dirs(data_fd)
                finally:
                    os.close(data_fd)
                _write_at(stage_fd, "snapshot.json", json.dumps(snapshot.to_dict()).encode("utf-8"))
                os.fsync(stage_fd)
            finally:
                os.close(stage_fd)
            os.rename(stage, snapshot_id, src_dir_fd=store, dst_dir_fd=store)
            os.fsync(store)
        finally:
            if stage in os.listdir(store):
                shutil.rmtree(stage, dir_fd=store)
    return snapshot


def _sync_dirs(fd):
    for name in os.listdir(fd):
        if stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
            child = _open_dir(fd, name)
            try:
                _sync_dirs(child)
            finally:
                os.close(child)
    os.fsync(fd)


def _inventory(fd, prefix="", budget=None):
    budget = [0, 0] if budget is None else budget
    out = []
    for name in sorted(os.listdir(fd)):
        relative = prefix + name
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = _open_dir(fd, name)
            try:
                out.extend(_inventory(child, relative + "/", budget))
            finally:
                os.close(child)
        else:
            budget[0] += 1
            budget[1] += info.st_size
            if budget[0] > MAX_FILES or budget[1] > MAX_TOTAL:
                raise PackError("packs: przekroczone limity kopii")
            content = _read_at(fd, name)
            out.append(SnapshotFile(relative, len(content), hashlib.sha256(content).hexdigest()))
    return out


def _snapshot_id(value):
    if not isinstance(value, str) or not SNAPSHOT_ID.fullmatch(value):
        raise PackError("packs: niepoprawny identyfikator kopii")
    return value


def _load_at(store, slug, snapshot_id, env=None):
    fd = _open_dir(store, _snapshot_id(snapshot_id))
    try:
        if "revoked" in os.listdir(fd):
            raise PackError("packs: kopia wycofana")
        try:
            raw = json.loads(_read_at(fd, "snapshot.json"))
            files = tuple(SnapshotFile(**entry) for entry in raw.pop("files"))
            snapshot = PackSnapshot(files=files, **raw)
        except (ValueError, TypeError, KeyError) as exc:
            raise PackError("packs: nieprawidłowy opis kopii") from exc
        directory = os.path.join(snapshot_root(slug, env), snapshot_id, "data")
        if (snapshot.project != slug or snapshot.id != snapshot_id or
                type(snapshot.version) is not int or snapshot.version != SNAPSHOT_VERSION or
                not isinstance(snapshot.card, str) or
                not re.fullmatch(r"K-[1-9][0-9]{0,8}", snapshot.card) or
                not isinstance(snapshot.name, str) or not NAME.fullmatch(snapshot.name) or
                type(snapshot.size) is not int or
                snapshot.directory != directory or not isinstance(snapshot.scanner, dict) or
                snapshot.scanner.get("blocked") is not False):
            raise PackError("packs: niezgodna tożsamość lub wersja kopii")
        if any(not isinstance(f.path, str) or type(f.size) is not int or
               not isinstance(f.sha256, str) for f in files):
            raise PackError("packs: nieprawidłowa lista plików kopii")
        data_fd = _open_dir(fd, "data")
        try:
            actual = tuple(sorted(_inventory(data_fd), key=lambda f: f.path))
        finally:
            os.close(data_fd)
        if (actual != tuple(sorted(files, key=lambda f: f.path)) or
                snapshot.size != sum(f.size for f in actual) or
                snapshot.scanner != {"count": len(actual), "size": snapshot.size,
                                     "paths": ["/" + f.path for f in actual[:20]],
                                     "hits": [], "blocked": False, "scanner": FILTER_NOTE}):
            raise PackError("packs: zmieniona lub częściowa kopia")
        return snapshot
    finally:
        os.close(fd)


def load_snapshot(slug, snapshot_id, env=None):
    """Weryfikuje kopię projektu także po restarcie; nie stanowi zgody na ekspozycję.

    Karta odbiorcy nie jest granicą. Obcy projekt szuka wyłącznie w swoim magazynie.
    """
    with snapshot_access(slug, snapshot_id, env) as snapshot:
        return snapshot


@contextmanager
def snapshot_access(slug, snapshot_id, env=None):
    """PT-264 trzyma ten kontekst do końca montowania, aby wycofanie nie wyprzedziło ekspozycji.

    Kolejność blokad: magazyn, potem urządzenia. Żadna ścieżka urządzeń nie może
    brać blokady magazynu w odwrotnej kolejności. `load_snapshot` wystarcza do opisu.
    """
    with _store(slug, env) as store:
        yield _load_at(store, slug, snapshot_id, env)


@contextmanager
def snapshots_access(slug, snapshot_ids, env=None):
    """Wiele wejść pod jedną blokadą; nie zagnieżdżaj snapshot_access tego projektu.

    Odbiorcy PT-263/PT-264 trzymają magazyn przed blokadą zgody i urządzeń.
    To tylko odczyt istniejących kopii, bez kopiowania i ponownego skanowania źródeł.
    """
    with _store(slug, env) as store:
        yield tuple(_load_at(store, slug, ident, env) for ident in snapshot_ids)


def _revoke_at(store, snapshot_id):
    fd = _open_dir(store, _snapshot_id(snapshot_id))
    try:
        try:
            _write_at(fd, "revoked", b"1\n")
        except FileExistsError:
            pass
        os.fsync(fd)
    finally:
        os.close(fd)


def revoke_snapshot(slug, snapshot_id, env=None):
    """Blokuje przyszłe ekspozycje; nie wymazuje danych poznanych już w projekcie."""
    with _store(slug, env) as store:
        _revoke_at(store, snapshot_id)


def delete_snapshot(slug, snapshot_id, *, confirm_detached, env=None):
    """Usuwa jedną kopię po hostowym potwierdzeniu PT-264.

    Callback (projekt, id) -> dokładnie True musi sprawdzić wszystkie urządzenia
    tej kopii pod blokadą montowań PT-264. To interfejs zaufanej usługi, nie IPC
    ani dane z pudełka. Najpierw wycofujemy kopię, nawet gdy odłączenie zawiedzie.
    """
    _snapshot_id(snapshot_id)
    with _store(slug, env) as store:
        trash = ".deleting-" + snapshot_id
        present = os.listdir(store)
        if snapshot_id not in present and trash not in present:
            return False
        if snapshot_id in present:
            _revoke_at(store, snapshot_id)
        if not callable(confirm_detached) or confirm_detached(slug, snapshot_id) is not True:
            raise PackError("packs: brak potwierdzenia odłączenia kopii")
        if snapshot_id in present:
            if trash in present:
                raise PackError("packs: niejednoznaczny stan usuwania kopii")
            os.rename(snapshot_id, trash, src_dir_fd=store, dst_dir_fd=store)
            os.fsync(store)
        shutil.rmtree(trash, dir_fd=store)
        os.fsync(store)
        return True
