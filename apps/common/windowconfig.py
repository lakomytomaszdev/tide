"""Konfiguracja okna Tide: $XDG_CONFIG_HOME/tide/window.json (zwykły JSON, jeden obiekt).

Jedno miejsce na ustawienia okna, wspólne dla widoków — dziś `cardOpen` (PT-133): co otwiera kafelek karty
z „Czeka na mnie” i powiadomienie o niej, „decision” (okno decyzji, domyślnie) albo „full” (pełna karta),
oraz `diffMode` (PT-350): tryb widoku diffa, „unified” (domyślnie) albo „split” (obok siebie).
Plik czytamy za każdym razem od nowa; nieznane klucze zostają przy zapisie, uszkodzony plik
albo nieznana wartość dają domyślne ustawienie (nic nie rzuca wyjątku przy odczycie).
"""
import json
import os
import tempfile

CARD_OPEN_DECISION, CARD_OPEN_FULL = "decision", "full"
CARD_OPEN_VALUES = (CARD_OPEN_DECISION, CARD_OPEN_FULL)
CARD_OPEN_DEFAULT = CARD_OPEN_DECISION

# Tryb widoku diffa (PT-350, `diffMode`): jednolity albo obok siebie; wspólny dla Tablicy i Worktrees.
DIFF_MODE_VALUES = ("unified", "split")
DIFF_MODE_DEFAULT = "unified"


def config_path(env=None) -> str:
    env = os.environ if env is None else env
    home = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(home, "tide", "window.json")


def load(path: str | None = None) -> dict:
    try:
        with open(path or config_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def card_open(path: str | None = None) -> str:
    value = load(path).get("cardOpen")
    return value if value in CARD_OPEN_VALUES else CARD_OPEN_DEFAULT


def set_card_open(value: str, path: str | None = None) -> None:
    """Zapisuje wybór atomowo (plik tymczasowy + rename). ValueError — nieznana wartość, OSError — zapis."""
    if value not in CARD_OPEN_VALUES:
        raise ValueError(f"nieznane ustawienie otwierania karty: {value!r}")
    _save("cardOpen", value, path)


def diff_mode(path: str | None = None) -> str:
    """Tryb widoku diffa (PT-350): „unified” (domyślnie) albo „split”; uszkodzony plik daje domyślny."""
    value = load(path).get("diffMode")
    return value if value in DIFF_MODE_VALUES else DIFF_MODE_DEFAULT


def set_diff_mode(value: str, path: str | None = None) -> None:
    """Zapisuje tryb diffa jak `set_card_open`. ValueError — nieznana wartość, OSError — zapis."""
    if value not in DIFF_MODE_VALUES:
        raise ValueError(f"nieznany tryb diffa: {value!r}")
    _save("diffMode", value, path)


def _save(key: str, value: str, path: str | None) -> None:
    path = path or config_path()
    data = load(path)
    data[key] = value
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".window-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def watch(on_change, parent=None, path: str | None = None):
    """Woła `on_change(wartość)` po każdej zmianie ustawienia w pliku (także z innego widoku w tym samym oknie).

    Zapis jest atomowy (rename), więc obserwujemy katalog pliku, nie sam plik. Zwraca obserwatora (trzeba
    go trzymać) albo None, gdy katalogu nie da się utworzyć."""
    from PyQt6.QtCore import QFileSystemWatcher
    path = path or config_path()
    folder = os.path.dirname(path)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return None
    watcher = QFileSystemWatcher([folder], parent)
    watcher.directoryChanged.connect(lambda _dir: on_change(card_open(path)))
    return watcher
