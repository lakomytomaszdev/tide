"""Składa pakiet widżetu do instalacji: kod widżetu + wspólna biblioteka QML + paleta.

Użycie: /usr/bin/python3 widgets/stage.py <katalog-widżetu> <katalog-wyjściowy> [paleta.json]
        /usr/bin/python3 widgets/stage.py --app <katalog-aplikacji> <katalog-wyjściowy> [paleta.json]

Wspólna biblioteka (widgets/lib) trafia do contents/ui/tide/, a Tokens.qml jest generowany
z palety wariantu — w QML nie wpisujemy kolorów na sztywno. Plasma instaluje każdy widżet
jako osobny pakiet, więc biblioteka jedzie w każdym z nich.

Aplikacja QML (--app, np. apps/worktrees) dostaje bibliotekę w qml/tide/, bez katalogu tests.

Widżet z kluczem "X-Tide-LimityConfig" w metadata.json dostaje dodatkowo plik JS (ścieżka
z wartości klucza) z portem i tokenem usługi Limity AI (QuotaApp), przepisanymi z jej
config.json. Plik ma tryb 0600 i nigdy nie trafia do repozytorium — powstaje tylko w pakiecie.
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PALETTE = os.path.join(ROOT, "theme", "palette", "mist.json")
LIB = os.path.join(ROOT, "widgets", "lib")
LIMITY_KEY = "X-Tide-LimityConfig"
LIMITY_DEFAULT_PORT = 41817  # domyślny port QuotaApp (scripts/install.sh w tamtym repo)


def palette_qml(palette: dict, singleton: bool = True) -> str:
    """Płaski obiekt QML z palety: backgroundCard, textMuted, teal, working, radiusCard, opacityPopup…

    Widżety dostają singleton, ekran logowania (theme/sddm) zwykły QtObject tworzony w każdym pliku.
    """
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
    for key, value in palette.get("opacity", {}).items():
        props.append(("real", "opacity" + key[0].upper() + key[1:], value))
    # Wymiary ekranu blokady (zegar, pole hasła, przyciemnienie tapety) i ekranu ładowania
    # (logo, pasek postępu, puls) — tylko z palety.
    for group, prefix in (("lockscreen", "lock"), ("splash", "splash")):
        for key, value in palette.get(group, {}).items():
            props.append(("real" if isinstance(value, float) else "int", prefix + key[0].upper() + key[1:], value))
    fonts = palette.get("fonts", {})
    props.append(("string", "fontUi", fonts.get("ui", "")))
    props.append(("string", "fontMono", fonts.get("mono", "")))
    props.append(("real", "fontSizePt", fonts.get("uiSizePt", 10)))

    lines = [
        "// Wygenerowane z palety (widgets/stage.py) — nie edytować ręcznie.",
        *(["pragma Singleton"] if singleton else []),
        "import QtQuick",
        "",
        "QtObject {",
    ]
    for kind, name, value in props:
        lines.append(f"    readonly property {kind} {name}: {json.dumps(value)}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def limity_config_path() -> str:
    """config.json usługi Limity AI: $LIMITY_CONFIG jak w jej backendzie, inaczej $XDG_CONFIG_HOME."""
    if os.environ.get("LIMITY_CONFIG"):
        return os.environ["LIMITY_CONFIG"]
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(config_home, "limity-ai-widget", "config.json")


def limity_config_js(config_path: str) -> str:
    """Treść Config.js. Bez konfiguracji token jest pusty, a widżet pokaże, co doinstalować."""
    port, token = LIMITY_DEFAULT_PORT, ""
    try:
        with open(config_path, encoding="utf-8") as fh:
            config = json.load(fh)
        port = int(config.get("port", LIMITY_DEFAULT_PORT))
        token = str(config.get("token", ""))
    except (OSError, ValueError) as error:
        print(f"Uwaga: brak konfiguracji usługi Limity AI ({config_path}: {error}). "
              "Widżet nie połączy się z usługą, dopóki nie zainstalujesz QuotaApp "
              "i nie złożysz pakietu ponownie.", file=sys.stderr)
    return (
        "// Wygenerowane przez widgets/stage.py z konfiguracji usługi Limity AI — nie commitować.\n"
        ".pragma library\n\n"
        f"var port = {port};\n"
        f"var token = {json.dumps(token)};\n"
    )


def write_private(path: str, text: str) -> None:
    """Zapis z trybem 0600 od pierwszej chwili — token nie bywa czytelny dla innych."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(text)


def load_palette(palette_path: str) -> dict:
    with open(palette_path, encoding="utf-8") as fh:
        return json.load(fh)


def copy_lib(lib_out: str, palette: dict) -> None:
    """Wspólna biblioteka QML z Tokens.qml wygenerowanym z palety."""
    shutil.copytree(LIB, lib_out, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.py"))
    with open(os.path.join(lib_out, "Tokens.qml"), "w", encoding="utf-8") as fh:
        fh.write(palette_qml(palette))


def stage_app(app_dir: str, out_dir: str, palette_path: str = DEFAULT_PALETTE) -> str:
    """Aplikacja QML z biblioteką w qml/tide (np. okno Worktrees)."""
    palette = load_palette(palette_path)
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    shutil.copytree(app_dir, out_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "__init__.py",
                                                                 "install.sh"))
    copy_lib(os.path.join(out_dir, "qml", "tide"), palette)
    return out_dir


def stage(widget_dir: str, out_dir: str, palette_path: str = DEFAULT_PALETTE) -> str:
    palette = load_palette(palette_path)
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    shutil.copytree(widget_dir, out_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    copy_lib(os.path.join(out_dir, "contents", "ui", "tide"), palette)
    with open(os.path.join(widget_dir, "metadata.json"), encoding="utf-8") as fh:
        secret = json.load(fh).get(LIMITY_KEY)
    if secret:
        write_private(os.path.join(out_dir, secret), limity_config_js(limity_config_path()))
    return out_dir


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--app" and len(argv) in (3, 4):
        stage_app(*argv[1:])
        return 0
    if len(argv) not in (2, 3):
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    stage(*argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
