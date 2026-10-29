"""Palety wariantów Tide i ich wczytywanie.

Każdy plik `<nazwa>.json` w tym katalogu to paleta wariantu. Mist (`mist.json`) jest pełna
i samodzielna. Inny wariant może być nakładką: klucz `"base": "mist"` mówi, że brakujące
wartości pochodzą z palety bazowej. Nakładka podaje tylko to, co się różni (kolory, rodzaj
tapety), a promienie, rozmiary, czcionki, czasy i limity dziedziczy — nie ma ich kopii,
które mogłyby się rozjechać.

Scalanie jest głębokie dla słowników; listy i pojedyncze wartości nakładka zastępuje
w całości. Klucz "base" nie trafia do wyniku. Paleta bez bazy (Mist) wychodzi z load()
dokładnie taka, jak z json.loads, więc generatory dają z niej te same pliki co przed
wprowadzeniem nakładek.

Generatory przyjmują ścieżkę do pliku (jak dotąd) albo nazwę wariantu („streamer”).
Każdy błąd palety to wyjątek z rodziny ValueError (PaletteError, json.JSONDecodeError).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# $TIDE_PALETTE_DIR podmienia katalog palet (testy instalatorów z zepsutym wariantem).
PALETTE_DIR = Path(os.environ.get("TIDE_PALETTE_DIR") or Path(__file__).resolve().parent)
BASE = "mist"


class PaletteError(ValueError):
    pass


def names() -> list[str]:
    """Nazwy wariantów, dla których jest plik palety, alfabetycznie."""
    return sorted(p.stem for p in PALETTE_DIR.glob("*.json"))


def path(name: str) -> Path:
    """Plik palety wariantu o tej nazwie, np. path("streamer")."""
    candidate = PALETTE_DIR / f"{name}.json"
    if not candidate.is_file():
        raise PaletteError(f"Nie ma palety {name!r} (są: {', '.join(names())}).")
    return candidate


def merge(base: dict, overlay: dict) -> dict:
    """Głębokie scalenie: słowniki łączymy rekurencyjnie, resztę nakładka zastępuje."""
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve(source: str | Path) -> Path:
    candidate = Path(source)
    if candidate.suffix == ".json" or candidate.is_file():
        return candidate
    return path(str(source))


def load(source: str | Path, _seen: tuple[Path, ...] = ()) -> dict:
    """Paleta z pliku albo po nazwie wariantu, z rozwiniętą bazą."""
    file = _resolve(source).resolve()
    if file in _seen:
        raise PaletteError(f"Zapętlona baza palety: {' → '.join(p.name for p in (*_seen, file))}")
    data = json.loads(file.read_text())
    base = data.pop("base", None)
    if base is None:
        return data
    base_file = file.parent / f"{base}.json"
    if not base_file.is_file():
        raise PaletteError(f"{file.name}: nie ma palety bazowej {base!r}.")
    return merge(load(base_file, (*_seen, file)), data)
