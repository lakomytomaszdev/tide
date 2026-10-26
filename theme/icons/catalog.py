"""Aplikacje i motywy ikon widziane tak, jak widzi je Plasma.

Pliki .desktop i motywy ikon szukamy w katalogach XDG w kolejności ich
ważności: plik o tym samym identyfikatorze z katalogu ważniejszego zasłania
plik z mniej ważnego (tak lokalne kopie w ~/.local/share/applications
podmieniają systemowe). Ikonę szukamy w łańcuchu motywów: wybrany motyw, jego
Inherits w głąb, a na końcu hicolor, jak w specyfikacji freedesktop.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

EXTENSIONS = (".svg", ".png", ".xpm")
HICOLOR = "hicolor"


def parse_ini(text: str) -> dict[str, dict[str, str]]:
    """Grupy i klucze pliku .desktop / index.theme. Komentarze i śmieci pomijamy."""
    groups: dict[str, dict[str, str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = groups.setdefault(line[1:-1], {})
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()
    return groups


def read_ini(path: Path) -> dict[str, dict[str, str]]:
    try:
        return parse_ini(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


@dataclass(frozen=True)
class Xdg:
    home: Path
    data_home: Path
    data_dirs: tuple[Path, ...]
    state_home: Path
    config_home: Path
    path: str

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Xdg":
        env = os.environ if env is None else env
        home = Path(env.get("HOME") or Path.home())
        data_home = Path(env.get("XDG_DATA_HOME") or home / ".local/share")
        if env.get("XDG_DATA_DIRS"):
            dirs = [Path(d) for d in env["XDG_DATA_DIRS"].split(":") if d]
        else:
            # Bez XDG_DATA_DIRS (np. z crona) dokładamy to, co sesja dostaje od flatpaka i snapd.
            dirs = [data_home / "flatpak/exports/share", Path("/var/lib/flatpak/exports/share"),
                    Path("/usr/local/share"), Path("/usr/share"), Path("/var/lib/snapd/desktop")]
        return cls(
            home=home,
            data_home=data_home,
            data_dirs=tuple(dict.fromkeys(d for d in dirs if d != data_home)),
            state_home=Path(env.get("XDG_STATE_HOME") or home / ".local/state"),
            config_home=Path(env.get("XDG_CONFIG_HOME") or home / ".config"),
            path=env.get("PATH", os.defpath),
        )

    def data_paths(self) -> list[Path]:
        """Katalogi danych od najważniejszego."""
        return [self.data_home, *self.data_dirs]

    def icon_paths(self, extra: tuple[Path, ...] = ()) -> list[Path]:
        return [*extra, self.data_home / "icons", self.home / ".icons", *(d / "icons" for d in self.data_dirs)]

    def pixmap_paths(self) -> list[Path]:
        return [d / "pixmaps" for d in self.data_dirs]


# ---------------------------------------------------------------- pliki .desktop

@dataclass
class DesktopFile:
    id: str
    path: Path
    groups: dict[str, dict[str, str]]

    @property
    def entry(self) -> dict[str, str]:
        return self.groups.get("Desktop Entry", {})

    @property
    def name(self) -> str:
        return self.entry.get("Name[pl]") or self.entry.get("Name") or self.id

    @property
    def icon(self) -> str:
        return self.entry.get("Icon", "")

    def visible(self, xdg: Xdg) -> bool:
        entry = self.entry
        if entry.get("Type") != "Application":
            return False
        if entry.get("NoDisplay", "").lower() == "true" or entry.get("Hidden", "").lower() == "true":
            return False
        only = [d for d in entry.get("OnlyShowIn", "").split(";") if d]
        if only and "KDE" not in only:
            return False
        if "KDE" in entry.get("NotShowIn", "").split(";"):
            return False
        try_exec = entry.get("TryExec")
        if try_exec:
            # KSycoca pomija wpisy, których TryExec nie da się uruchomić.
            if try_exec.startswith("/"):
                return os.access(try_exec, os.X_OK)
            return shutil.which(try_exec, path=xdg.path) is not None
        return True


def desktop_files(xdg: Xdg) -> dict[str, list[DesktopFile]]:
    """Identyfikator .desktop → pliki od najważniejszego (pierwszy jest tym, który widzi Plasma)."""
    found: dict[str, list[DesktopFile]] = {}
    for data in xdg.data_paths():
        root = data / "applications"
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.desktop")):
            if not path.is_file():
                continue
            desktop_id = str(path.relative_to(root)).replace("/", "-")
            found.setdefault(desktop_id, []).append(DesktopFile(desktop_id, path, read_ini(path)))
    return found


def visible_apps(xdg: Xdg) -> list[DesktopFile]:
    apps = [files[0] for files in desktop_files(xdg).values() if files[0].visible(xdg)]
    return sorted(apps, key=lambda app: app.name.lower())


# ---------------------------------------------------------------- motywy ikon

@dataclass
class IconTheme:
    name: str
    roots: list[Path]  # katalogi <ikony>/<motyw> od najważniejszego
    index: dict[str, str]
    subdirs: dict[str, dict[str, str]] = field(default_factory=dict)
    _index: dict[str, list[tuple[str, Path]]] | None = field(default=None, repr=False)

    def _files(self) -> dict[str, list[tuple[str, Path]]]:
        """Plik ikony → [(podkatalog, katalog motywu)]. Motywy mają dziesiątki tysięcy plików,
        więc czytamy każdy katalog raz zamiast pytać system plików o każdą ikonę osobno."""
        if self._index is None:
            self._index = {}
            for subdir in self.subdirs:
                for root in self.roots:
                    try:
                        names = os.listdir(root / subdir)
                    except OSError:
                        continue
                    for name in names:
                        self._index.setdefault(name, []).append((subdir, root))
        return self._index

    @property
    def inherits(self) -> list[str]:
        return [t.strip() for t in self.index.get("Inherits", "").split(",") if t.strip()]

    def find(self, icon: str, contexts: tuple[str, ...] | None = None) -> Path | None:
        """Najlepszy plik ikony: wektorowy z katalogu skalowalnego, inaczej największy."""
        best, best_score = None, None
        files = self._files()
        for ext in EXTENSIONS:
            for subdir, root in files.get(f"{icon}{ext}", ()):
                info = self.subdirs[subdir]
                if contexts and info.get("Context") not in contexts:
                    continue
                path = root / subdir / f"{icon}{ext}"
                if not path.exists():
                    continue  # zerwane dowiązanie
                scalable = info.get("Type") == "Scalable" and ext == ".svg"
                score = (scalable, int(info.get("Size", "0") or 0), ext == ".png")
                if best_score is None or score > best_score:
                    best, best_score = path, score
        return best


def load_theme(name: str, icon_paths: list[Path]) -> IconTheme | None:
    roots = [base / name for base in icon_paths if (base / name).is_dir()]
    index_file = next((r / "index.theme" for r in roots if (r / "index.theme").is_file()), None)
    if index_file is None:
        return None
    groups = read_ini(index_file)
    index = groups.get("Icon Theme", {})
    subdirs = {d.strip(): groups.get(d.strip(), {})
               for d in index.get("Directories", "").split(",") if d.strip()}
    return IconTheme(name, roots, index, subdirs)


def theme_chain(name: str, icon_paths: list[Path]) -> list[IconTheme]:
    """Motyw, jego przodkowie w głąb i hicolor na końcu. Brakujące motywy pomijamy."""
    chain: list[IconTheme] = []
    seen: set[str] = set()

    def visit(theme_name: str) -> None:
        if theme_name in seen or theme_name == HICOLOR:
            return
        seen.add(theme_name)
        theme = load_theme(theme_name, icon_paths)
        if theme is None:
            return
        chain.append(theme)
        for parent in theme.inherits:
            visit(parent)

    visit(name)
    hicolor = load_theme(HICOLOR, icon_paths)
    if hicolor is not None:
        chain.append(hicolor)
    return chain


def find_pixmap(icon: str, xdg: Xdg) -> Path | None:
    for base in xdg.pixmap_paths():
        for ext in EXTENSIONS:
            if (base / f"{icon}{ext}").is_file():
                return base / f"{icon}{ext}"
    return None


def icon_name(value: str) -> str:
    """Nazwa ikony bez rozszerzenia — Plasma ucina .png/.svg w Icon=nazwa.png."""
    for ext in EXTENSIONS:
        if value.endswith(ext) and not value.startswith("/"):
            return value[: -len(ext)]
    return value
