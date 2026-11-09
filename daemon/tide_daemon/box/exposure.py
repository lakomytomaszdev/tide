"""Hostowy manifest i jedna zgoda projektu (PT-263).

Nie kopiuje źródeł, nie skanuje paczek i nie dodaje urządzeń. Rejestr repozytoriów
pochodzi z usługi, identyfikatory kopii z żądania, a ich opisy wyłącznie z PT-262.
Kolejność blokad odbiorcy: magazyn paczek → approved_access → urządzenia PT-264.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import secrets
import stat

from . import k3, packs, profile as bp

VERSION = 1
PROJECT = "plasma-tide"
SCOPE = (
    "Wszystkie karty i kroki projektu ufają sobie wzajemnie. Także root przez wspólny Docker "
    "może poznać udostępnione wejścia i zmieniać współdzieloną pracę. Pudełko ma chronić host, "
    "stan Tide i inne projekty. Identyfikatory karty i kroku służą organizacji, rozliczaniu "
    "i kierowaniu wyników; per-card jest opcją porządkową, nie granicą bezpieczeństwa. "
    "To nowa zgoda na dokładny zakres i wersję projektu; dawne zgody kart nie są rozszerzane ani sumowane. "
    "Wycofanie zatrzymuje przyszłe udostępnienia, bez obietnicy usunięcia kopii już poznanych "
    "w projekcie. Zakończenie jednej karty nie resetuje pozostałych."
)
PENDING = "Zgoda zapisana; ekspozycja czeka na odbiór montowań PT-264 i bramkę PT-274."
MAX_RECORD = 4 * 1024 * 1024


class ExposureError(ValueError):
    """Brak zgodnej wersji, kopii albo świadomej zgody — nigdy szersze montowanie."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _within(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def _path(path):
    if (not isinstance(path, str) or not os.path.isabs(path) or
            os.path.normpath(path) != path or os.path.realpath(path) != path or path == "/"):
        raise ExposureError("manifest: wymagana dokładna ścieżka bez dowiązań")
    return path


def root(project, env=None):
    bp._check_slug(project, "projekt")
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or os.path.join(
        env.get("HOME") or os.path.expanduser("~"), ".local", "state")
    return os.path.join(_path(base), "tide", "project-exposures", project)


@contextmanager
def _store(project, env=None, *, create=True):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root(project, env).split("/")[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if create:
                    raise
                yield None
                return
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ExposureError("manifest: magazyn nie jest prywatnym katalogiem usługi")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def _read(fd, name):
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(child, "rb") as fh:
        info = os.fstat(fh.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or info.st_mode & 0o222 or info.st_size > MAX_RECORD):
            raise ExposureError("manifest: niedozwolony plik w magazynie")
        return json.loads(fh.read(MAX_RECORD + 1))


def _write(fd, name, value, *, replace=False):
    temporary = ".stage-" + secrets.token_hex(16)
    child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=fd)
    try:
        with os.fdopen(child, "wb") as fh:
            blob = _json(value)
            if len(blob) > MAX_RECORD:
                raise ExposureError("manifest: przekroczony limit opisu")
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        if replace:
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        else:
            os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            os.unlink(temporary, dir_fd=fd)
        os.fsync(fd)
    finally:
        if temporary in os.listdir(fd):
            os.unlink(temporary, dir_fd=fd)


def _state(fd):
    empty = {"schema": VERSION, "generation": 0, "active": False, "record": None}
    if fd is None:
        return empty
    try:
        state = _read(fd, "state.json")
    except FileNotFoundError:
        return empty
    if (not isinstance(state, dict) or type(state.get("schema")) is not int or state.get("schema") != VERSION or
            type(state.get("generation")) is not int or state["generation"] < 1 or
            type(state.get("active")) is not bool or not isinstance(state.get("record"), dict)):
        raise ExposureError("manifest: nieznana wersja stanu zgody")
    record = state["record"]
    manifest = record.get("manifest")
    if (not isinstance(manifest, dict) or type(manifest.get("contract")) is not int or
            manifest.get("contract") != VERSION or type(manifest.get("consentVersion")) is not int or
            manifest.get("consentVersion") != VERSION or
            record.get("digest") != _hash(manifest) or
            record.get("version") != f"{record.get('generation')}:{record.get('digest')}" or
            type(record.get("generation")) is not int or not 1 <= record["generation"] <= state["generation"] or
            _read(fd, f"manifest-{record['generation']}.json") != record):
        raise ExposureError("manifest: uszkodzona albo niezgodna zatwierdzona wersja")
    return state


def profile_contract(raw, scope):
    """Nie usuwa nieznanych pól nowszych kart przy zapisie starym schematem."""
    if isinstance(raw, dict) and any(k in raw for k in ("tests", "outside", "readDirs", "evidence")):
        raise ExposureError("Etap 2: brak wersjonowanego kontraktu tests K-69 / outside i readDirs K-60 / "
                            "kopii dowodów K-55. Odmowa aktywacji; zachowano profil. Brak rozlicza PT-275 (#15).")
    try:
        return bp.validate(raw, scope)
    except bp.ProfileError as exc:
        raise ExposureError(str(exc)) from None


class ExposureService:
    """Rejestr repozytoriów jest zaufanym wejściem hosta, nigdy polem żądania IPC.

    V1 odbiera tylko plasma-tide. Kolejne repozytoria jednego projektu można podać
    jawnie; wspólny rodzic nie staje się przez to źródłem montowania.
    """
    def __init__(self, project, repositories, registry, env=None):
        if project != PROJECT:
            raise ExposureError("manifest v1: odbierany projekt to plasma-tide")
        self.project, self.env = project, env
        self.repositories = sorted(set(_path(p) for p in repositories))
        self.registry = {slug: tuple(_path(p) for p in paths) for slug, paths in registry.items()}
        if not self.repositories or set(self.repositories) != set(self.registry.get(project, ())):
            raise ExposureError("manifest: repozytoria nie odpowiadają rejestrowi projektu usługi")
        foreign = [p for slug, paths in self.registry.items() if slug != project for p in paths]
        if any(_within(a, b) or _within(b, a) for a in self.repositories for b in foreign):
            raise ExposureError("manifest: wspólny rodzic dołączyłby inny projekt")

    def _manifest(self, profile, snapshot_ids):
        profile = profile_contract(profile, "card")
        if profile.get("project", self.project) != self.project:
            raise ExposureError("manifest: profil wskazuje inny projekt")
        if not isinstance(snapshot_ids, list) or any(not isinstance(i, str) for i in snapshot_ids):
            raise ExposureError("manifest: snapshotIds musi być listą identyfikatorów kopii")
        if len(snapshot_ids) > 64 or len(set(snapshot_ids)) != len(snapshot_ids):
            raise ExposureError("manifest: powtórzone kopie albo przekroczony limit 64")
        copies = [packs.load_snapshot(self.project, ident, self.env).to_dict() for ident in sorted(snapshot_ids)]
        names = [p["name"] for p in copies]
        if len(set(names)) != len(names):
            raise ExposureError("manifest: kopie mają ten sam cel /packs/<nazwa>")
        requested = [p["name"] if isinstance(p, dict) else p for p in profile.get("packs", [])]
        if set(requested) != set(names):
            raise ExposureError("manifest: czeka na dokładne PackSnapshot dla każdej paczki; nie montuje oryginałów")
        from . import mounts as project_mounts
        mounts = project_mounts.plan(self.repositories, self.project, self.env)
        inputs = []
        for copy in copies:
            mount = {"source": copy["directory"], "target": "/packs/" + copy["name"],
                     "mode": "ro", "kind": "pack"}
            mounts.append(mount)
            inputs.append({"contract": "PackSnapshot", "version": packs.SNAPSHOT_VERSION,
                           "snapshot": copy, "mount": mount})
        foreign = [p for slug, paths in self.registry.items() if slug != self.project for p in paths]
        protected = [root(self.project, self.env)]
        protected += [os.path.dirname(p["directory"]) + "/snapshot.json" for p in copies]
        originals = ["/" + f["path"] for p in copies for f in p["files"]]
        for mount in mounts:
            source = _path(mount["source"])
            if any(_within(path, source) or _within(source, path) for path in foreign):
                raise ExposureError("manifest: montowanie dołączyłoby inny projekt")
            if any(_within(path, source) or _within(source, path) for path in protected):
                raise ExposureError("manifest: montowanie odsłoniłoby chroniony manifest")
            if mount["mode"] == "rw" and any(_within(p, source) for p in originals):
                raise ExposureError("manifest: oryginały wejść znalazłyby się w zapisie pudełka")
        return json.loads(_json({"contract": VERSION, "consentVersion": VERSION,
                                "mountContract": project_mounts.CONTRACT, "project": self.project,
                                "repositories": self.repositories, "mounts": mounts, "inputs": inputs,
                                "profile": profile, "scope": SCOPE,
                                "credentials": {p: k3.credential_mode(p) for p in ("claude", "codex")}}))

    def preview(self, profile, snapshot_ids):
        manifest = self._manifest(profile, snapshot_ids)
        with _store(self.project, self.env, create=False) as fd:
            state = _state(fd)
        return {"manifest": manifest, "generation": state["generation"],
                "expected": _hash({"manifest": manifest, "generation": state["generation"]})}

    def approve(self, profile, snapshot_ids, *, expected, accepted):
        candidate = self.preview(profile, snapshot_ids)
        with _store(self.project, self.env) as fd:
            state = _state(fd)
            if expected != candidate["expected"] or candidate["generation"] != state["generation"]:
                raise ExposureError("manifest: zakres lub wersja zmieniły się; wymagany nowy podgląd")
            old = state["record"]
            if state["active"] and old["manifest"] == candidate["manifest"]:
                return old  # Ta sama zgoda; bez sumowania historii kart i bez nowej wersji.
            if accepted is not True:
                raise ExposureError("manifest: wymagana nowa świadoma zgoda projektu")
            generation = state["generation"] + 1
            digest = _hash(candidate["manifest"])
            record = {"generation": generation, "digest": digest, "version": f"{generation}:{digest}",
                      "manifest": candidate["manifest"]}
            # Przerwany zapis audytu nie aktywuje zgody. Kolejna próba z tą samą treścią może go dokończyć.
            name = f"manifest-{generation}.json"
            if name in os.listdir(fd):
                if _read(fd, name) != record:
                    raise ExposureError("manifest: niedokończony zapis wymaga rozliczenia na hoście")
            else:
                _write(fd, name, record)
            _write(fd, "state.json", {"schema": VERSION, "generation": generation,
                                      "active": True, "record": record}, replace=True)
            return record


def current(project, env=None):
    with _store(project, env, create=False) as fd:
        state = _state(fd)
        return state["record"] if state["active"] else None


@contextmanager
def approved_access(project, version, env=None):
    """PT-264 trzyma blokadę zgody do końca montowania, po blokadzie paczek.

    Nie otwiera paczek pod blokadą zgody (odwrotna kolejność blokad). Odbiorca
    sprawdza je osobno przez snapshots_access i porównuje pełne opisy z manifestem.
    """
    with _store(project, env, create=False) as fd:
        state = _state(fd)
        record = state["record"]
        if not state["active"] or not record or record["version"] != version:
            raise ExposureError("manifest: brak aktualnej zgody projektu na tę wersję")
        manifest = record["manifest"]
        if manifest["project"] != project or manifest["credentials"] != {
                p: k3.credential_mode(p) for p in ("claude", "codex")}:
            raise ExposureError("manifest: zmieniony projekt albo K3/K1; wymagana nowa zgoda")
        yield manifest


def revoke(project, version, env=None):
    with _store(project, env, create=False) as fd:
        state = _state(fd)
        if not state["record"] or state["record"]["version"] != version:
            raise ExposureError("manifest: wycofanie wymaga dokładnej wersji zgody")
        if state["active"]:
            state["active"] = False
            state["generation"] += 1
            _write(fd, "state.json", state, replace=True)
    return "Wycofano przyszłe udostępnienia; kopie już poznane w projekcie mogą pozostać."


@contextmanager
def snapshot_mount_access(project, ident, env=None):
    """Mount z opublikowanej historii; callback trzyma już blokadę paczek."""
    with _store(project, env) as fd:
        state = _state(fd)
        entries = []
        for name in os.listdir(fd):
            if not (name.startswith("manifest-") and name.endswith(".json")):
                continue
            record = _read(fd, name)
            manifest = record.get("manifest")
            if (not isinstance(manifest, dict) or manifest.get("project") != project or
                    record.get("digest") != _hash(manifest)):
                raise ExposureError("niezgodna historia urządzeń projektu")
            if record.get("generation", state["generation"] + 1) > state["generation"]:
                continue  # Sam audyt niedokończonego approve nie upoważnia do urządzeń.
            entries += [i for i in manifest["inputs"] if i["snapshot"]["id"] == ident]
        if not entries or any(entry != entries[0] for entry in entries):
            raise ExposureError("brak jednoznacznego historycznego urządzenia kopii")
        yield entries[0]["mount"]
