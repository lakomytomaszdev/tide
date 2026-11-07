"""Projektowe katalogi danych: normalizacja przy zapisie i ponowna kontrola przed startem."""
import os

MAX_DIRS = 32
KEY = "read-dirs"
BINDINGS_KEY = "read-dir-bindings"
SECRET_PARTS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".codex", ".claude"}
SECRET_PATHS = (".config", ".local/share/keyrings", ".local/state/tide", ".git-credentials",
                ".netrc", ".npmrc", ".pypirc")
BROAD = {"/", "/home", "/root", "/tmp", "/var", "/var/tmp", "/run", "/etc", "/usr",
         "/opt", "/srv", "/mnt", "/media"}


def within(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def overlaps(a, b):
    return within(a, b) or within(b, a)


def paths(value):
    if not isinstance(value, list) or len(value) > MAX_DIRS or any(
            not isinstance(p, str) or not p.strip() or len(p) > 4096 or
            any(c in p for c in "\r\n\0*?[](){}") for p in value):
        raise ValueError(f"readDirs: oczekiwano listy do {MAX_DIRS} ścieżek katalogów, bez wzorców")
    return value


def normalize(value, *, home=None, writable=(), bindings=(), secrets=()):
    """Zwraca kanoniczne katalogi i zapamiętane powiązania ścieżka→cel.

    Powiązania pozwalają odmówić startu po podmianie dowiązania, zamiast niejawnie zatwierdzić nowy cel.
    Profil dostaje wyłącznie cele kanoniczne, nigdy aliasy mogące poszerzyć jego zakres.
    """
    paths(value)
    home = os.path.realpath(home or os.path.expanduser("~"))
    protected = [os.path.realpath(os.path.join(home, p)) for p in (*SECRET_PARTS, *SECRET_PATHS)]
    protected += [os.path.realpath(p) for p in secrets if p]
    roots = [os.path.realpath(p) for p in writable if p]
    out, frozen = [], []
    for raw in value:
        raw = raw.strip()
        if raw == "~" or raw.startswith("~/"):
            raw = home + raw[1:]
        if not os.path.isabs(raw):
            raise ValueError(f"readDirs: potrzebna ścieżka bezwzględna albo ~/…: {raw}")
        source = raw  # realpath rozwiązuje też „symlink/..”; normpath przed nim zmieniłby znaczenie ścieżki
        target = os.path.realpath(source)
        paths([target])  # cel dowiązania również nie może stać się wzorcem profilu
        if not os.path.isdir(target):
            raise ValueError(f"readDirs: katalog nie istnieje: {source}")
        if target in BROAD or os.path.ismount(target) or within(home, target):
            raise ValueError(f"readDirs: zbyt szeroki korzeń: {source}")
        if SECRET_PARTS.intersection(target.split(os.sep)) or any(overlaps(target, p) for p in protected):
            raise ValueError(f"readDirs: chroniony katalog lub sekret: {source}")
        if any(overlaps(target, p) for p in roots):
            raise ValueError(f"readDirs: katalog nakłada się na obszar zapisu kroku: {source}")
        if target not in out:
            out.append(target)
        if source != target and {"path": source, "target": target} not in frozen:
            frozen.append({"path": source, "target": target})
    if not isinstance(bindings, list) and bindings != ():
        raise ValueError("readDirs: uszkodzone powiązania dowiązań")
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"path", "target"} or any(
                not isinstance(binding[k], str) or not os.path.isabs(binding[k]) for k in binding):
            raise ValueError("readDirs: uszkodzone powiązania dowiązań")
        if binding["target"] not in out:
            continue  # użytkownik usunął uprawnienie
        if os.path.realpath(binding["path"]) != binding["target"] or not os.path.isdir(binding["path"]):
            raise ValueError(f"readDirs: zmienił się cel dowiązania lub zniknął katalog: {binding['path']}")
        if binding not in frozen:
            frozen.append(binding)
    # Kanoniczna ścieżka też mogła zostać zastąpiona dowiązaniem po zatwierdzeniu.
    return out, frozen


def project_dirs(projects, *, writable=(), secrets=()):
    out = []
    for project in projects:
        if project.read_dirs_error:
            raise ValueError(f"{project.name}: {project.read_dirs_error}")
        current, _ = normalize(project.read_dirs, writable=writable, bindings=project.read_dir_bindings,
                               secrets=secrets)
        if current != project.read_dirs:
            raise ValueError(f"{project.name}: readDirs: zmienił się cel zatwierdzonego katalogu")
        out.extend(p for p in current if p not in out)
    return out
