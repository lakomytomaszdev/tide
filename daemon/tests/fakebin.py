"""Atrapy systemctl, kpackagetool6, qdbus6, kwriteconfig6, dbus-send, update-desktop-database,
kbuildsycoca6 oraz programów uruchamianych przez usługę (systemd-run, konsole, rider, claude, codex)
dla testów instalacji i uruchamiania.

Testy instalacji nigdy nie dotykają prawdziwego pulpitu: katalog z atrapami idzie na początek
PATH, a każde wywołanie jest zapisywane do pliku dziennika. kpackagetool6 kopiuje pakiet tam,
gdzie prawdziwe narzędzie ($XDG_DATA_HOME/plasma/plasmoids/<id> dla widżetów,
$XDG_DATA_HOME/kwin/scripts/<id> dla skryptów KWin), żeby dało się sprawdzić pliki.
kwriteconfig6 i dbus-send tylko zapisują wywołanie — nic nie trafia do konfiguracji ani na magistralę.
qdbus6 zapisuje do dziennika tylko usługę, ścieżkę i metodę, a pełny ostatni argument (np. skrypt
dla evaluateScript, zwykle wielowierszowy) do kolejnego pliku w <dziennik>.qdbus6/ — patrz qdbus_args().
incus to atrapa ze stanem (fake_incus.py, $TIDE_FAKE_INCUS_STATE): zapisuje argv, a `exec` wykonuje polecenie
lokalnie z PATH zaczynającym się od <fakebin>/box (apt-get, docker, … tylko zapisują wywołanie).
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

QDBUS = """#!/bin/bash
echo "qdbus6 $1 $2 $3" >> "$TIDE_FAKE_LOG"
dir="$TIDE_FAKE_LOG.qdbus6"; mkdir -p "$dir"
n=$(find "$dir" -type f | wc -l)
printf '%s' "${@: -1}" > "$dir/$(printf '%03d' "$n")"
"""
LOG_ONLY = """#!/bin/bash
echo "$(basename "$0") $*" >> "$TIDE_FAKE_LOG"
"""
FAKES = {"systemctl": SYSTEMCTL, "kpackagetool6": KPACKAGETOOL, "kwriteconfig6": LOG_ONLY, "dbus-send": LOG_ONLY,
         "qdbus6": QDBUS, "update-desktop-database": LOG_ONLY, "kbuildsycoca6": LOG_ONLY,
         # Programy otwierane przez usługę (worktrees, KRunner) — tylko zapis wywołania, żadne okno
         # nie powstaje, a systemd-run nie dotyka prawdziwego menedżera usług użytkownika.
         "systemd-run": LOG_ONLY, "konsole": LOG_ONLY, "rider": LOG_ONLY, "claude": LOG_ONLY, "codex": LOG_ONLY,
         "xdg-open": LOG_ONLY,
         # Codex Desktop — atrapa zamiast prawdziwej aplikacji ChatGPT (odnośnik codex://threads/<id>).
         "chatgpt": LOG_ONLY,
         # Incus (pudełko karty, PT-167) — atrapa ze stanem w $TIDE_FAKE_INCUS_STATE (fake_incus.py).
         "incus": None}

FAKE_INCUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_incus.py")
INCUS = f"""#!/bin/bash
export TIDE_FAKE_BOX_BIN="$(dirname "$0")/box"
exec /usr/bin/python3 -s {FAKE_INCUS} "$@"
"""
# Programy wywoływane przez `incus exec` atrapy (PATH w „pudełku”): tylko zapis wywołania ze zmienną proxy,
# żeby polecenia budowy (apt-get, docker pull) nigdy nie dotknęły hosta.
BOX_LOG_ONLY = """#!/bin/bash
echo "box[$TIDE_FAKE_BOX] $(basename "$0") HTTPS_PROXY=${HTTPS_PROXY:-} $*" >> "$TIDE_FAKE_LOG"
"""
BOX_FAKES = ("apt-get", "apt", "docker", "systemctl", "npm", "npx", "dotnet", "pip", "pip3", "curl", "wget")


def make_env(tmp: str) -> dict:
    """Środowisko z tymczasowym HOME, XDG_* i atrapami na początku PATH."""
    fake = os.path.join(tmp, "fakebin")
    os.makedirs(fake, exist_ok=True)
    box = os.path.join(fake, "box")
    os.makedirs(box, exist_ok=True)
    scripts = {os.path.join(fake, n): (INCUS if n == "incus" else b) for n, b in FAKES.items()}
    scripts.update({os.path.join(box, n): BOX_LOG_ONLY for n in BOX_FAKES})
    for path, body in scripts.items():
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    env = {
        "HOME": os.path.join(tmp, "home"),
        "XDG_DATA_HOME": os.path.join(tmp, "data"),
        "XDG_CONFIG_HOME": os.path.join(tmp, "config"),
        "XDG_CONFIG_DIRS": "/etc/xdg",  # bez ~/.config/kdedefaults z sesji Plasmy
        "BUILD_DIR": os.path.join(tmp, "build"),
        "TIDE_FAKE_LOG": os.path.join(tmp, "calls.log"),
        "PATH": fake + os.pathsep + "/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        # Bez Codex Desktop, chyba że test wskaże własny plik .desktop z atrapą chatgpt.
        "TIDE_CODEX_DESKTOP": "",
        # Atrapa incus: stan instancji i migawek; adapter woła `incus` z PATH (nie $TIDE_INCUS_BIN).
        "TIDE_FAKE_INCUS_STATE": os.path.join(tmp, "incus-state"),
        "TIDE_INCUS_BIN": "",
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


def qdbus_args(env: dict) -> list[str]:
    """Ostatnie argumenty kolejnych wywołań qdbus6 (np. skrypty evaluateScript)."""
    folder = env["TIDE_FAKE_LOG"] + ".qdbus6"
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            out.append(fh.read())
    return out


def files_under(path: str) -> list[str]:
    out = []
    for dirpath, _dirs, names in os.walk(path):
        out += [os.path.relpath(os.path.join(dirpath, n), path) for n in names]
    return sorted(out)
