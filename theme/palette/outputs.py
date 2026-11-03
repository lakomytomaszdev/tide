"""Wszystko, co generatory składają z palety wariantu, w jednym katalogu — do porównań.

Użycie: python3 outputs.py build <paleta|nazwa> <katalog> [--root <drzewo repozytorium>]
        python3 outputs.py golden [--root <drzewo>]   # sumy Mist do tests/mist-golden.json

Generatory są uruchamiane tak jak w celach make (osobne procesy, ich własne main()), więc
porównanie obejmuje też wczytywanie palety. --root pozwala złożyć to samo z innego drzewa,
np. z `git archive master` — tak powstały sumy odniesienia Mist.

Katalogi wyniku: colors/ (schemat KDE), plasma/ (styl Plasmy), wallpaper/ (obrazy tapety),
wallpaper-plugin/ (wtyczka kota, tylko wariant „cat”), lockscreen/ (pakiet powłoki z ekranem
blokady), lookandfeel/ (motyw globalny, tylko Mist), tokens/ (Tokens.qml widżetów).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENTS = ("colors", "plasma", "wallpaper", "wallpaper-plugin", "lockscreen", "lookandfeel", "tokens")
GOLDEN = Path(__file__).resolve().parent / "tests" / "mist-golden.json"
PYTHON = "/usr/bin/python3"

TOKENS = """
import sys
sys.path.insert(0, "widgets")
from stage import load_palette, palette_qml
sys.stdout.write(palette_qml(load_palette(sys.argv[1])))
"""

INFO = """
import json, sys
sys.path.insert(0, ".")
try:
    from theme.palette import load
except ImportError:  # drzewo sprzed wariantów palety
    load = lambda p: json.load(open(p))
palette = load(sys.argv[1])
try:
    from theme.plasma.generate import theme_id
    plasma = theme_id(palette)
except ImportError:
    from theme.plasma.generate import THEME_ID as plasma
print(json.dumps({"name": palette["name"], "plasma": plasma,
                  "variant": palette.get("wallpaper", {}).get("variant", "blobs")}))
"""


def _palette_file(source: str | Path, root: Path) -> Path:
    candidate = Path(source)
    if candidate.suffix == ".json":
        return candidate.resolve()
    return root / "theme" / "palette" / f"{source}.json"


def _run(root: Path, *args: str, stdout=subprocess.DEVNULL) -> subprocess.CompletedProcess:
    return subprocess.run([PYTHON, *args], cwd=root, check=True, stdout=stdout, text=True)


def build(source: str | Path, out: Path, root: Path = ROOT) -> Path:
    """Składa pliki wszystkich generatorów z palety do katalogu `out` (czyści go najpierw)."""
    root = Path(root).resolve()
    out = Path(out).resolve()
    palette = _palette_file(source, root)
    info = json.loads(_run(root, "-c", INFO, str(palette), stdout=subprocess.PIPE).stdout)
    out.mkdir(parents=True, exist_ok=True)
    for leftover in out.iterdir():
        subprocess.run(["rm", "-rf", str(leftover)], check=True)
    _run(root, "theme/colors/generate.py", str(palette), str(out / "colors" / f"Tide{info['name']}.colors"))
    _run(root, "theme/plasma/generate.py", str(palette), str(out / "plasma" / info["plasma"]))
    _run(root, "theme/wallpaper/generate.py", str(palette), str(out / "wallpaper"))
    if info["variant"] == "cat":
        _run(root, "theme/wallpaper/plugin.py", str(palette), str(out / "wallpaper-plugin"))
    _run(root, "theme/lockscreen/generate.py", str(palette), str(out / "lockscreen" / "org.tide.desktop"))
    if info["name"] == "Mist":
        # Motyw globalny jest jeden (org.tide.desktop) i składa się wyłącznie z Mist.
        _run(root, "theme/lookandfeel/generate.py", str(palette), str(out / "lookandfeel" / "org.tide.desktop"))
    tokens = _run(root, "-c", TOKENS, str(palette), stdout=subprocess.PIPE).stdout
    (out / "tokens").mkdir()
    (out / "tokens" / "Tokens.qml").write_text(tokens)
    return out


def manifest(directory: Path) -> dict[str, str]:
    """Ścieżka względna → SHA-256 każdego pliku w katalogu."""
    directory = Path(directory)
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def main(argv: list[str]) -> int:
    root = ROOT
    if "--root" in argv:
        i = argv.index("--root")
        root = Path(argv[i + 1])
        del argv[i:i + 2]
    if argv[:1] == ["build"] and len(argv) == 3:
        print(build(argv[1], Path(argv[2]), root))
        return 0
    if argv == ["golden"]:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            build("mist", Path(tmp), root)
            GOLDEN.write_text(json.dumps(manifest(Path(tmp)), indent=1) + "\n")
        print(f"Sumy Mist zapisane w {GOLDEN}")
        return 0
    print(__doc__.splitlines()[2], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
