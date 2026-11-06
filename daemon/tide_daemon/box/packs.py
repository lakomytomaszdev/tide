"""Jednorazowe kopie danych karty. Źródła nigdy nie są urządzeniami Incusa.

Skaner to filtr, nie gwarancja. Trafienia zawierają wyłącznie ścieżkę i nazwę
reguły, nigdy sekret. `files` pozwala ręcznie zawęzić dopasowania wzorca.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
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
