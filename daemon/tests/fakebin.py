"""Atrapy systemctl, kpackagetool6, kwriteconfig6 i dbus-send dla testów instalacji.

Testy instalacji nigdy nie dotykają prawdziwego pulpitu: katalog z atrapami idzie na początek
PATH, a każde wywołanie jest zapisywane do pliku dziennika. kpackagetool6 kopiuje pakiet tam,
gdzie prawdziwe narzędzie ($XDG_DATA_HOME/plasma/plasmoids/<id> dla widżetów,
$XDG_DATA_HOME/kwin/scripts/<id> dla skryptów KWin), żeby dało się sprawdzić pliki.
kwriteconfig6 i dbus-send tylko zapisują wywołanie — nic nie trafia do konfiguracji ani na magistralę.
"""
import os
import shutil
import stat

SYSTEMCTL = """#!/bin/bash
echo "systemctl $*" >> "$TIDE_FAKE_LOG"
"""

KPACKAGETOOL = """#!/bin/bash
echo "kpackagetool6 $*" >> "$TIDE_FAKE_LOG"
root="${XDG_DATA_HOME:?}/plasma/plasmoids"
while [[ $# -gt 0 ]]; do
    case "$1" in
        -t)
            [[ "$2" == "KWin/Script" ]] && root="${XDG_DATA_HOME:?}/kwin/scripts"; shift 2 ;;
        -i|-u)
            id="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["KPlugin"]["Id"])' "$2/metadata.json")"
            mkdir -p "$root"; rm -rf "$root/$id"; cp -r "$2" "$root/$id"; shift 2 ;;
        -r) rm -rf "$root/$2"; shift 2 ;;
        *) shift ;;
    esac
done
"""

LOG_ONLY = """#!/bin/bash
echo "$(basename "$0") $*" >> "$TIDE_FAKE_LOG"
"""
FAKES = {"systemctl": SYSTEMCTL, "kpackagetool6": KPACKAGETOOL, "kwriteconfig6": LOG_ONLY, "dbus-send": LOG_ONLY}


def make_env(tmp: str) -> dict:
    """Środowisko z tymczasowym HOME, XDG_* i atrapami na początku PATH."""
    fake = os.path.join(tmp, "fakebin")
    os.makedirs(fake, exist_ok=True)
    for name, body in FAKES.items():
        path = os.path.join(fake, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    env = {
        "HOME": os.path.join(tmp, "home"),
        "XDG_DATA_HOME": os.path.join(tmp, "data"),
        "XDG_CONFIG_HOME": os.path.join(tmp, "config"),
        "BUILD_DIR": os.path.join(tmp, "build"),
        "TIDE_FAKE_LOG": os.path.join(tmp, "calls.log"),
        "PATH": fake + os.pathsep + "/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    for key in ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        os.makedirs(env[key], exist_ok=True)
    # Bezpiecznik: atrapy muszą wygrywać z prawdziwymi narzędziami.
    for tool in FAKES:
        if shutil.which(tool, path=env["PATH"]) != os.path.join(fake, tool):
            raise RuntimeError(f"atrapa {tool} nie jest pierwsza w PATH")
    return env


def calls(env: dict) -> list[str]:
    try:
        with open(env["TIDE_FAKE_LOG"], encoding="utf-8") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def files_under(path: str) -> list[str]:
    out = []
    for dirpath, _dirs, names in os.walk(path):
        out += [os.path.relpath(os.path.join(dirpath, n), path) for n in names]
    return sorted(out)
