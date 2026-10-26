"""Składa pakiet widżetu do instalacji: kod widżetu + wspólna biblioteka QML + paleta.

Użycie: /usr/bin/python3 widgets/stage.py <katalog-widżetu> <katalog-wyjściowy> [paleta.json]

Wspólna biblioteka (widgets/lib) trafia do contents/ui/tide/, a Tokens.qml jest generowany
z palety wariantu — w QML nie wpisujemy kolorów na sztywno. Plasma instaluje każdy widżet
jako osobny pakiet, więc biblioteka jedzie w każdym z nich.
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PALETTE = os.path.join(ROOT, "theme", "palette", "mist.json")
LIB = os.path.join(ROOT, "widgets", "lib")


def palette_qml(palette: dict) -> str:
    """Płaski singleton QML z palety: backgroundCard, textMuted, teal, working, radiusCard…"""
    pastel = palette.get("pastel", {})
    props: list[tuple[str, str, object]] = [("string", "name", palette.get("name", ""))]

    def color(value: str) -> str:
        return pastel.get(value, value)  # znaczenia wskazują nazwę pastelu

    for group in ("background", "text"):
        for key, value in palette.get(group, {}).items():
            props.append(("color", group + key[0].upper() + key[1:], value))
    props.append(("color", "accent", palette["accent"]))
    for key, value in pastel.items():
        props.append(("color", key, value))
    for key, value in palette.get("semantic", {}).items():
        props.append(("color", key, color(value)))
    for key, value in palette.get("windowButtons", {}).items():
        props.append(("color", "windowButton" + key[0].upper() + key[1:], color(value)))
    for key, value in palette.get("radius", {}).items():
        props.append(("int", "radius" + key[0].upper() + key[1:], value))
    fonts = palette.get("fonts", {})
    props.append(("string", "fontUi", fonts.get("ui", "")))
    props.append(("string", "fontMono", fonts.get("mono", "")))
    props.append(("real", "fontSizePt", fonts.get("uiSizePt", 10)))

    lines = [
        "// Wygenerowane przez widgets/stage.py z palety — nie edytować ręcznie.",
        "pragma Singleton",
        "import QtQuick",
        "",
        "QtObject {",
    ]
    for kind, name, value in props:
        lines.append(f"    readonly property {kind} {name}: {json.dumps(value)}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def stage(widget_dir: str, out_dir: str, palette_path: str = DEFAULT_PALETTE) -> str:
    with open(palette_path, encoding="utf-8") as fh:
        palette = json.load(fh)
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    shutil.copytree(widget_dir, out_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    lib_out = os.path.join(out_dir, "contents", "ui", "tide")
    shutil.copytree(LIB, lib_out, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.py"))
    with open(os.path.join(lib_out, "Tokens.qml"), "w", encoding="utf-8") as fh:
        fh.write(palette_qml(palette))
    return out_dir


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    stage(*argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
