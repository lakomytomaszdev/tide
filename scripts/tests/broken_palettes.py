"""Zepsute warianty palety do testów instalatorów: instalacja Mist ma się udać mimo nich.

Katalog palet podaje $TIDE_PALETTE_DIR (theme/palette): kopia mist.json i streamer.json
w jednym z dwóch stanów — nieczytelny JSON albo wariant „pictures” bez obrazów.
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BROKEN = {
    "json": "{ to nie jest JSON",
    "assets": json.dumps({"base": "mist", "name": "Streamer", "wallpaper": {"variant": "pictures", "pictures": {
        "assets": "nie-ma-takiego-katalogu", "desktop": "desktop", "lock": "lock", "fallback": "DP-2"}}}),
}


def broken_palette_dir(base: Path, kind: str) -> Path:
    directory = base / f"palettes-{kind}"
    directory.mkdir()
    shutil.copy(ROOT / "theme/palette/mist.json", directory / "mist.json")
    (directory / "streamer.json").write_text(BROKEN[kind])
    return directory
