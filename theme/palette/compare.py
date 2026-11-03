"""Zrzut porównawczy wariantów palety: Mist (praca) obok Streamer (Stream), bez instalowania.

Użycie: python3 compare.py <katalog wyjściowy>   (cel make palette-compare → build/compare)

Dla każdego wariantu:
- tapeta pulpitu: oba monitory w natywnej rozdzielczości obok siebie, jak w prawdziwym układzie
  (HDMI-A-1 1920×1080 po lewej, DP-2 2560×1440 po prawej, wyrównane do góry) → wallpaper-<wariant>.png,
- ekran blokady: prawdziwy greeter w zagnieżdżonym KWinie (theme/lockscreen/screenshot.sh), osobno
  2560×1440 (kadr DP-2) i 1920×1080 (kadr HDMI-A-1), po „naciśnięciu klawisza” i w bezczynności,
  złożone tak samo → lockscreen-<wariant>.png, lockscreen-idle-<wariant>.png,
- styl Plasmy: podgląd ramek (theme/plasma/preview.py) → plasma-<wariant>.png.
overview.png zestawia wszystko pomniejszone: kolumny Mist i Streamer, wiersze jak wyżej.

Greeter wariantu dostaje kdeglobals z kolorami jego schematu (bieżący kdeglobals z podmienionymi
grupami [Colors:*]), żeby kontrolki Plasmy na ekranie blokady miały kolory wariantu.
"""
from __future__ import annotations

import configparser
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from theme.colors.generate import render, scheme  # noqa: E402
from theme.palette import load, path  # noqa: E402

PYTHON = "/usr/bin/python3"
VARIANTS = ("mist", "streamer")
# Prawdziwy układ: (złącze, x, rozmiar zrzutu greetera).
MONITORS = (("HDMI-A-1", 0, (1920, 1080)), ("DP-2", 1920, (2560, 1440)))
CANVAS = (4480, 1440)


def run(*args: str, env: dict | None = None) -> None:
    subprocess.run(args, cwd=ROOT, check=True, env=env, stdout=subprocess.DEVNULL)


def side_by_side(images: dict[str, Path], background: str) -> Image.Image:
    canvas = Image.new("RGB", CANVAS, background)
    for name, x, _ in MONITORS:
        with Image.open(images[name]) as image:
            canvas.paste(image.convert("RGB"), (x, 0))
    return canvas


def kdeglobals(palette: dict, target: Path) -> Path:
    """Bieżący kdeglobals z grupami kolorów ze schematu wariantu."""
    config = configparser.RawConfigParser(strict=False, interpolation=None, allow_no_value=True)
    config.optionxform = str  # klucze KDE rozróżniają wielkość liter
    current = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kdeglobals"
    if current.is_file():
        config.read(current)
    for group, values in scheme(palette).items():
        if group.startswith("Colors:") or group in ("General", "WM"):
            if not config.has_section(group):
                config.add_section(group)
            for key, value in values.items():
                config.set(group, key, value)
    with open(target, "w") as fh:
        config.write(fh, space_around_delimiters=False)
    return target


def lockscreens(name: str, palette: dict, out: Path, work: Path) -> None:
    package = work / name / "lock" / "org.tide.desktop"
    run(PYTHON, "theme/lockscreen/generate.py", str(path(name)), str(package), "--preview-wake-ms", "15000")
    env = {**os.environ, "TIDE_LOCK_WAIT": "14",
           "TIDE_LOCK_KDEGLOBALS": str(kdeglobals(palette, work / name / "kdeglobals"))}
    awake, idle = {}, {}
    for monitor, _, (w, h) in MONITORS:
        awake[monitor] = work / name / f"lock-{monitor}.png"
        idle[monitor] = work / name / f"lock-idle-{monitor}.png"
        run("sh", "theme/lockscreen/screenshot.sh", str(package), str(idle[monitor]), str(w), str(h),
            str(work / name / "wallpaper" / "DP-2.png"), env={**env, "TIDE_LOCK_AWAKE_SHOT": str(awake[monitor])})
    background = palette["background"]["desktop"]
    side_by_side(awake, background).save(out / f"lockscreen-{name}.png")
    side_by_side(idle, background).save(out / f"lockscreen-idle-{name}.png")


def label(image: Image.Image, text: str) -> Image.Image:
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("Geist-Medium.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    draw.rectangle((0, 0, draw.textlength(text, font=font) + 28, 44), fill="#000000")
    draw.text((14, 8), text, fill="#ffffff", font=font)
    return image


def overview(out: Path) -> Path:
    rows = (("Pulpit", "wallpaper"), ("Blokada", "lockscreen"), ("Blokada w bezczynności", "lockscreen-idle"),
            ("Styl Plasmy", "plasma"))
    cell_w, gap = 1344, 16
    cells = []
    for title, stem in rows:
        row = []
        for name in VARIANTS:
            with Image.open(out / f"{stem}-{name}.png") as image:
                scaled = image.convert("RGB").resize((cell_w, round(image.height * cell_w / image.width)), Image.LANCZOS)
            row.append(label(scaled, f"{title} · {load(name)['name']}"))
        cells.append(row)
    height = sum(max(c.height for c in row) for row in cells) + gap * (len(cells) + 1)
    sheet = Image.new("RGB", (cell_w * 2 + gap * 3, height), "#0b0d0f")
    y = gap
    for row in cells:
        for i, cell in enumerate(row):
            sheet.paste(cell, (gap + i * (cell_w + gap), y))
        y += max(c.height for c in row) + gap
    sheet.save(out / "overview.png")
    return out / "overview.png"


def main(argv: list[str]) -> int:
    out = Path(argv[0]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tide-compare-") as tmp:
        work = Path(tmp)
        for name in VARIANTS:
            palette = load(name)
            (work / name).mkdir()
            run(PYTHON, "theme/wallpaper/generate.py", str(path(name)), str(work / name / "wallpaper"))
            side_by_side({m: work / name / "wallpaper" / f"{m}.png" for m, _, _ in MONITORS},
                         palette["background"]["desktop"]).save(out / f"wallpaper-{name}.png")
            run(PYTHON, "theme/plasma/preview.py", str(path(name)), str(work / name / "plasma"))
            with Image.open(work / name / "plasma" / "preview-floating.png") as floating, \
                    Image.open(work / name / "plasma" / "preview-docked.png") as docked:
                sheet = Image.new("RGB", (floating.width + docked.width, max(floating.height, docked.height)))
                sheet.paste(floating.convert("RGB"), (0, 0))
                sheet.paste(docked.convert("RGB"), (floating.width, 0))
                sheet.save(out / f"plasma-{name}.png")
            lockscreens(name, palette, out, work)
            print(f"{name}: gotowe")
    print(overview(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
