#!/usr/bin/env bash
# Minimalna instalacja Tide na nowej maszynie (Kubuntu albo Fedora z KDE Plasma 6), niezależna od motywu:
# usługa tide-daemon, okno Tide z Tablicą, CLI `tide` i zasoby przepływu kart. Motyw Tide (SDDM, Plymouth,
# panele, dock, tapety, kolory, Konsola, Vivaldi) nie jest instalowany.
#
# Użycie: bash scripts/bootstrap-machine.sh [--check] [--yes] [--no-packages] [--no-board] [--low-power]
#   --check        tylko diagnoza: niczego nie instaluje, nie woła sudo, nie zakłada Tablicy
#   --yes          bez pytań przed poleceniami sudo (instalacja pakietów, profil AppArmor dla bwrap)
#   --no-packages  pomija instalację pakietów systemowych (tylko je sprawdza)
#   --no-board     nie zakłada Tablicy (powstanie przy pierwszym zapisie z okna)
#   --low-power    słabsza maszyna (włącza się sama przy mniej niż 20 GB RAM): wyłącza wyszukiwanie po znaczeniu
#                  (indeks sesji z modelem bge-m3) i sprawdza bez ekranu tylko widok Tablicy, z limitem 120 s
#
# Kolejność: system → pakiety (apt: najpierw `apt-get -s install`, odmowa, gdy symulacja zmienia Qt 6
# albo KDE Frameworks 6) → claude i codex → piaskownica bwrap → make pre-install-backup install-daemon
# install-tide-window install-cli → Tablica → odpowiedź usługi na D-Bus → okno bez ekranu (offscreen).
# Skrypt można uruchamiać wiele razy. sudo wołany jest tylko dla wypisanych poleceń, po potwierdzeniu.
# Kod wyjścia: 0 — wszystko gotowe, 1 — błąd, 2 — brakuje czegoś obowiązkowego (lista na końcu).
# Instrukcja dla osoby instalującej: docs/install-new-machine.md.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
packages_py="$root/scripts/bootstrap_packages.py"
PY=/usr/bin/python3

check=0
yes=0
no_packages=0
no_board=0
low_power=0
for arg in "$@"; do
    case "$arg" in
        --check) check=1 ;;
        --yes) yes=1 ;;
        --no-packages) no_packages=1 ;;
        --no-board) no_board=1 ;;
        --low-power) low_power=1 ;;
        -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Nieznany argument: $arg (--help)" >&2; exit 1 ;;
    esac
done

missing=()   # rzeczy obowiązkowe, których brakuje — kod 2 na końcu
notes=()     # ostrzeżenia, które nie blokują pracy

step() { printf '\n== %s\n' "$*"; }
ok() { printf '  ok: %s\n' "$*"; }
warn() { printf '  UWAGA: %s\n' "$*"; notes+=("$*"); }
lack() { printf '  BRAK: %s\n' "$*"; missing+=("$*"); }
die() { printf 'BŁĄD: %s\n' "$*" >&2; exit 1; }

# Pytanie przed poleceniem z sudo: pokazuje polecenie, --yes odpowiada „tak”, bez terminala — „nie”.
confirm() {
    local question="$1"
    if [[ $yes -eq 1 ]]; then return 0; fi
    if [[ ! -t 0 ]]; then
        echo "  Brak terminala do pytania — uruchom w terminalu albo z --yes." >&2
        return 1
    fi
    local answer
    read -r -p "  $question [t/N] " answer || answer=n
    [[ "$answer" == [tTyY] ]]
}

run_sudo() {
    printf '  $ sudo %s\n' "$*"
    sudo "$@"
}

[[ $EUID -ne 0 ]] || die "uruchom jako zwykły użytkownik (skrypt sam zapyta o sudo, gdy będzie potrzebne)"
[[ -f "$root/Makefile" && -f "$packages_py" ]] || die "nie widzę repozytorium Tide w $root"

# --- 1. system ------------------------------------------------------------------------------------

step "System"
os_id="" os_like="" os_name="" os_version=""
os_release="${TIDE_BOOTSTRAP_OS_RELEASE:-/etc/os-release}"   # zmienna tylko dla testów (scripts/tests)
if [[ -r "$os_release" ]]; then
    # shellcheck disable=SC1090
    os_id="$(. "$os_release" && echo "${ID:-}")"
    os_like="$(. "$os_release" && echo "${ID_LIKE:-}")"
    os_name="$(. "$os_release" && echo "${PRETTY_NAME:-$ID}")"
    os_version="$(. "$os_release" && echo "${VERSION_ID:-}")"
fi
manager=""
if command -v apt-get >/dev/null && [[ " $os_id $os_like " == *" ubuntu "* || " $os_id $os_like " == *" debian "* ]]; then
    manager=apt
elif command -v dnf >/dev/null && [[ " $os_id $os_like " == *" fedora "* ]]; then
    manager=dnf
fi
ok "${os_name:-nieznany system}, menedżer pakietów: ${manager:-nierozpoznany}"
[[ -n "$manager" ]] || warn "nierozpoznany system — pakiety zainstaluj sam według docs/install-new-machine.md"

# Lista pakietów i strażnik symulacji są w Pythonie, więc python3 musi być pierwszy (minimalne obrazy go nie mają).
if [[ ! -x $PY ]]; then
    case "$manager" in
        apt) py_install=(apt-get install -y python3) ;;
        dnf) py_install=(dnf install -y python3) ;;
        *) py_install=() ;;
    esac
    if [[ $check -eq 0 && ${#py_install[@]} -gt 0 ]] && confirm "Brak $PY. Zainstalować (sudo ${py_install[*]})?"; then
        run_sudo "${py_install[@]}" || die "instalacja python3 się nie udała"
    fi
    [[ -x $PY ]] || die "brak $PY — zainstaluj python3 z repozytorium dystrybucji i uruchom skrypt ponownie"
fi

# Wersja systemu: Kubuntu przed 26.04 (24.04 ma Plasmę 5) i Fedora przed 40 nie mają Plasmy 6 — instalacja odmawia.
unsupported=0
version_lt() {  # version_lt 24.04 26.04 → prawda, gdy pierwsza wersja jest starsza
    [[ "$1" != "$2" && "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" == "$1" ]]
}
if [[ "$os_id" == ubuntu && -n "$os_version" ]] && version_lt "$os_version" 26.04; then
    lack "$os_name — Tide wymaga Kubuntu 26.04 lub nowszego (starsze wydania nie mają Plasmy 6 albo ich nie testowano)"
    unsupported=1
elif [[ "$os_id" == fedora && -n "$os_version" ]] && version_lt "$os_version" 40; then
    lack "$os_name — Tide wymaga Fedory 40 lub nowszej (Plasma 6)"
    unsupported=1
fi

if command -v plasmashell >/dev/null; then
    plasma="$(plasmashell --version 2>/dev/null | awk '{print $2}')"
    if [[ "${plasma%%.*}" =~ ^[0-9]+$ && "${plasma%%.*}" -ge 6 ]]; then
        ok "KDE Plasma $plasma"
    else
        lack "KDE Plasma ${plasma:-?} — Tide wymaga Plasmy 6"
        unsupported=1
    fi
else
    # Bez pulpitu (serwer, kontener testowy) usługa i okno działają; powiadomienia i KRunner — nie.
    warn "brak plasmashell — to nie jest sesja KDE Plasma 6 (usługa i okno zadziałają, powiadomienia i KRunner zależą od pulpitu)"
fi
[[ -n "${XDG_SESSION_TYPE:-}" ]] && ok "sesja: ${XDG_SESSION_TYPE} ${XDG_CURRENT_DESKTOP:-}"

if systemctl --user show-environment >/dev/null 2>&1; then
    ok "systemd użytkownika działa"
else
    lack "systemd użytkownika (systemctl --user) — usługa tide-daemon go wymaga; zaloguj się do sesji graficznej"
fi

# Słabsza maszyna: mniej niż 20 GB RAM (8–16 GB) — te same ustawienia co --low-power.
meminfo="${TIDE_BOOTSTRAP_MEMINFO:-/proc/meminfo}"   # zmienna tylko dla testów (scripts/tests)
mem_kb="$(awk '/^MemTotal:/{print $2}' "$meminfo" 2>/dev/null || true)"
if [[ -n "$mem_kb" ]]; then
    mem_gb=$(( (mem_kb + 524288) / 1048576 ))
    if [[ $low_power -eq 0 && $mem_kb -lt $((20 * 1048576)) ]]; then
        low_power=1
        ok "pamięć: ${mem_gb} GB — tryb słabszej maszyny (jak --low-power)"
    else
        ok "pamięć: ${mem_gb} GB"
    fi
fi
[[ $low_power -eq 1 ]] && ok "tryb słabszej maszyny: bez wyszukiwania po znaczeniu, próba okna tylko na widoku Tablicy"

if [[ $unsupported -eq 1 && $check -eq 0 ]]; then
    step "Podsumowanie"
    echo "  Nieobsługiwany system — nic nie zainstalowano ani nie zmieniono:"
    printf '    - %s\n' "${missing[@]}"
    exit 2
fi

# --- 2. pakiety -----------------------------------------------------------------------------------

# Moduł QML jest „zainstalowany”, gdy jest jego qmldir — niezależnie od tego, który pakiet go dał (neon).
declare -A qmldir=()
while read -r pkg rel; do qmldir[$pkg]="$rel"; done < <($PY "$packages_py" qmldirs)
qml_present() {
    local dir
    for dir in /usr/lib/*-linux-gnu/qt6/qml /usr/lib64/qt6/qml /usr/lib/qt6/qml; do
        [[ -f "$dir/$1" ]] && return 0
    done
    return 1
}

installed() {
    if [[ -n "${qmldir[$1]:-}" ]] && qml_present "${qmldir[$1]}"; then return 0; fi
    case "$manager" in
        apt) dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed" ;;
        dnf) rpm -q "$1" >/dev/null 2>&1 ;;
    esac
}

step "Pakiety"
need=()
if [[ -n "$manager" ]]; then
    mapfile -t required < <($PY "$packages_py" list "$manager" required)
    mapfile -t optional < <($PY "$packages_py" list "$manager" optional)
    # Pusta lista to błąd skryptu pomocniczego, a nie „wszystko zainstalowane”.
    [[ ${#required[@]} -gt 0 ]] || die "nie udało się odczytać listy pakietów ($packages_py)"
    for pkg in "${required[@]}" "${optional[@]}"; do
        installed "$pkg" || need+=("$pkg")
    done
    if [[ ${#need[@]} -eq 0 ]]; then
        ok "wszystkie pakiety są zainstalowane (${#required[@]} obowiązkowych, ${#optional[@]} dodatkowych)"
    else
        echo "  Do instalacji: ${need[*]}"
    fi
fi

simulate() {
    local out rc=0
    case "$manager" in
        apt) out="$(LC_ALL=C apt-get -s install "${need[@]}" 2>&1)" || rc=$? ;;
        dnf) out="$(LC_ALL=C dnf install --assumeno "${need[@]}" 2>&1)" || rc=$? ;;
    esac
    # dnf --assumeno kończy się kodem 1 także przy poprawnej transakcji — o wyniku decyduje strażnik.
    if [[ $manager == apt && $rc -ne 0 ]]; then
        echo "$out" | tail -5 | sed 's/^/    /'
        return 4
    fi
    printf '%s\n' "$out" | $PY "$packages_py" guard "$manager" | sed 's/^/  /'
    return "${PIPESTATUS[1]}"
}

if [[ ${#need[@]} -gt 0 ]]; then
    if [[ $check -eq 1 || $no_packages -eq 1 ]]; then
        # apt-get -s i dnf --assumeno działają bez roota.
        echo "  Symulacja (bez sudo, na obecnych listach pakietów):"
        simulate || warn "symulacja $manager nie przeszła — szczegóły wyżej"
        for pkg in "${need[@]}"; do
            if [[ " ${required[*]} " == *" $pkg "* ]]; then lack "pakiet $pkg"; else warn "brak pakietu dodatkowego $pkg"; fi
        done
    else
        if [[ $manager == apt ]]; then
            if confirm "Odświeżyć listy pakietów (sudo apt-get update)?"; then
                run_sudo apt-get update -q || die "apt-get update się nie udał"
            fi
        fi
        echo "  Symulacja:"
        rc=0
        simulate || rc=$?
        if [[ $rc -eq 3 ]]; then
            die "symulacja zmienia Qt 6 albo KDE Frameworks 6 — nic nie zainstalowano (szczegóły wyżej)"
        elif [[ $rc -ne 0 ]]; then
            die "symulacja instalacji się nie udała — nic nie zainstalowano (czy listy pakietów są aktualne?)"
        fi
        if confirm "Zainstalować: ${need[*]}?"; then
            case "$manager" in
                apt) run_sudo apt-get install -y "${need[@]}" || die "instalacja pakietów się nie udała (wyżej)" ;;
                dnf) run_sudo dnf install -y "${need[@]}" || die "instalacja pakietów się nie udała (wyżej)" ;;
            esac
        else
            for pkg in "${need[@]}"; do
                if [[ " ${required[*]} " == *" $pkg "* ]]; then lack "pakiet $pkg"; fi
            done
        fi
    fi
fi

# Python i Qt z kodu, nie z nazw pakietów: te same moduły, które importują usługa, okno i CLI.
python_ok=0
if qt="$($PY - 2>&1 <<'EOF'
import sys
assert sys.version_info >= (3, 10), "Python %s — potrzebny co najmniej 3.10" % sys.version.split()[0]
from PyQt6 import sip  # noqa: F401
from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
from PyQt6 import QtDBus, QtGui, QtNetwork, QtQml, QtQuick  # noqa: F401
print(f"Python {sys.version.split()[0]}, Qt {QT_VERSION_STR}, PyQt6 {PYQT_VERSION_STR}")
EOF
)"; then
    ok "$qt"
    python_ok=1
else
    lack "moduły PyQt6 dla /usr/bin/python3: $(echo "$qt" | tail -1)"
fi

# --- 3. agenci ------------------------------------------------------------------------------------

step "Agenci: claude i codex"
# Ta sama kolejność co w usłudze: PATH, zapasowo ~/.local/bin (instalator Claude Code).
agent_path() {
    command -v "$1" 2>/dev/null && return 0
    [[ -x "$HOME/.local/bin/$1" ]] && { echo "$HOME/.local/bin/$1"; return 0; }
    return 1
}
for agent in claude codex; do
    if path="$(agent_path "$agent")"; then
        version="$("$path" --version 2>/dev/null | head -1 || true)"
        ok "$agent: $path ${version:+($version)}"
    else
        case "$agent" in
            claude) lack "claude — instalacja: curl -fsSL https://claude.ai/install.sh | bash; potem: claude (logowanie)" ;;
            codex) lack "codex — instalacja: zob. docs/install-new-machine.md (npm albo snap); potem: codex login" ;;
        esac
    fi
done
# Logowanie sprawdzamy tylko po plikach, bez wywołania modelu.
[[ -f "$HOME/.claude/.credentials.json" ]] && ok "claude: jest zapisane logowanie" \
    || warn "claude: nie widzę logowania (~/.claude/.credentials.json) — uruchom claude i zaloguj się"
[[ -f "${CODEX_HOME:-$HOME/.codex}/auth.json" ]] && ok "codex: jest zapisane logowanie" \
    || warn "codex: nie widzę logowania (~/.codex/auth.json) — uruchom codex login"

if [[ -n "$(git config --global user.name 2>/dev/null)" && -n "$(git config --global user.email 2>/dev/null)" ]]; then
    ok "git: $(git config --global user.name) <$(git config --global user.email)>"
else
    warn "git bez user.name/user.email — commity agentów w projektach go potrzebują: git config --global user.name …"
fi

# --- 4. piaskownica -------------------------------------------------------------------------------

step "Piaskownica kroków (bwrap)"
if [[ -x /usr/bin/bwrap ]]; then
    if sandbox="$($PY "$root/scripts/sandbox/check.py" 2>&1)"; then
        echo "$sandbox" | sed 's/^/  /'
    else
        echo "$sandbox" | sed 's/^/  /'
        profile="$($PY "$root/scripts/sandbox/check.py" --json 2>/dev/null | $PY -c \
            'import json,sys; print(json.loads(sys.stdin.read()).get("profile", ""))' 2>/dev/null || true)"
        if [[ "$profile" == obcy ]]; then
            # Profil z pakietu systemu (Ubuntu 25.04+): zamiana to świadoma decyzja — polecenie wyżej i w instrukcji.
            lack "piaskownica Claude Code: profil AppArmor z pakietu systemu — zamiana według docs/install-new-machine.md (Piaskownica)"
        elif [[ $check -eq 0 && "$sandbox" == *"sudo bash scripts/sandbox/install.sh"* ]]; then
            echo "  Na Ubuntu jądro blokuje przestrzenie użytkownika bez profilu AppArmor. Profil dotyczy tylko /usr/bin/bwrap."
            if confirm "Zainstalować profil AppArmor dla bwrap (sudo bash scripts/sandbox/install.sh)?"; then
                run_sudo bash "$root/scripts/sandbox/install.sh" $([[ $yes -eq 1 ]] && echo --yes) \
                    || lack "profil AppArmor dla bwrap (sudo bash scripts/sandbox/install.sh)"
            else
                lack "profil AppArmor dla bwrap (sudo bash scripts/sandbox/install.sh)"
            fi
        else
            lack "działająca piaskownica bwrap — kroki kart w tle nie ruszą (szczegóły wyżej)"
        fi
    fi
else
    lack "/usr/bin/bwrap (pakiet bubblewrap)"
fi

# --- 5. instalacja Tide ---------------------------------------------------------------------------

step "Tide: usługa, okno, CLI"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
if [[ $check -eq 1 ]]; then
    for item in "$data/tide/daemon/tide-daemon.py:usługa" "$data/tide/tide-window/tide.py:okno Tide" \
                "$HOME/.local/bin/tide:CLI tide" "$data/tide/resources/docs/agent-plan-standard.md:zasoby"; do
        if [[ -e "${item%%:*}" ]]; then ok "${item##*:}: ${item%%:*}"; else lack "${item##*:} niezainstalowane (${item%%:*})"; fi
    done
elif [[ $python_ok -eq 0 ]]; then
    lack "instalacja Tide pominięta — najpierw moduły PyQt6 (wyżej)"
else
    # Kopia wyglądu tylko wtedy, gdy jest co kopiować (w świeżym koncie bez Plasmy tar odmówiłby pustego archiwum).
    # shellcheck source=paths.sh
    source "$root/scripts/paths.sh"
    has_look=0
    for path in "${CONFIG_FILES[@]}" "${MIRRORED_DIRS[@]}" "${ADD_ONLY_DIRS[@]}"; do
        [[ -e "$HOME/$path" ]] && { has_look=1; break; }
    done
    # install-cli dokłada skille Tide do Codexa tylko wtedy, gdy widzi `codex` w PATH; instalatory agentów kładą
    # programy w ~/.local/bin, którego świeża sesja (katalog powstał po zalogowaniu) jeszcze nie ma w PATH.
    [[ -d "$HOME/.local/bin" && ":$PATH:" != *":$HOME/.local/bin:"* ]] && export PATH="$PATH:$HOME/.local/bin"
    targets=(install-daemon install-tide-window install-cli)
    [[ $has_look -eq 1 ]] && targets=(pre-install-backup "${targets[@]}")
    [[ $has_look -eq 1 ]] || echo "  Brak plików wyglądu Plasmy — kopia zapasowa niepotrzebna."
    printf '  $ make %s\n' "${targets[*]}"
    if ! make -C "$root" --no-print-directory "${targets[@]}" 2>&1 | sed 's/^/    /'; then
        die "make ${targets[*]} się nie udał (wyżej)"
    fi
fi

# --- 6. usługa na D-Bus ---------------------------------------------------------------------------

step "Usługa tide-daemon"
dbus_ok=0
call() {  # call <obiekt> <interfejs> <metoda> [sygnatura argumenty…] → tekst JSON z odpowiedzi
    local out
    out="$(busctl --user --timeout=20 -j call org.tide.Daemon "$@" 2>&1)" || { echo "$out" >&2; return 1; }
    $PY -c 'import json,sys; print(json.loads(sys.stdin.read())["data"][0])' <<<"$out"
}
if systemctl --user is-active --quiet tide-daemon.service 2>/dev/null; then
    ok "tide-daemon.service działa ($(systemctl --user is-enabled tide-daemon.service 2>/dev/null || echo ?))"
    # Po instalacji usługa dopiero wstaje — nazwa na magistrali pojawia się po kilku sekundach.
    for _ in $(seq 1 30); do
        busctl --user status org.tide.Daemon >/dev/null 2>&1 && break
        sleep 1
    done
else
    lack "tide-daemon.service nie działa — journalctl --user -u tide-daemon -n 50"
fi
if info="$(call /Issues org.tide.Daemon.Issues Info 2>/dev/null)"; then
    dbus_ok=1
    $PY - "$info" <<'EOF' | sed 's/^/  /'
import json, sys
info = json.loads(sys.argv[1])
res = info.get("resources") or {}
items = res.values() if isinstance(res, dict) else res
bad = [str(r.get("path") or r) for r in items if isinstance(r, dict) and not r.get("available", True)]
print(f"ok: D-Bus org.tide.Daemon odpowiada (apiVersion {info.get('apiVersion')})")
print("ok: zasoby Tide dostępne" if not bad else "UWAGA: niedostępne zasoby: " + ", ".join(bad))
EOF
else
    lack "org.tide.Daemon nie odpowiada na D-Bus (busctl --user call org.tide.Daemon /Issues org.tide.Daemon.Issues Info)"
fi
if [[ -x "$HOME/.local/bin/tide" ]]; then
    if "$HOME/.local/bin/tide" info --json >/dev/null 2>&1; then ok "CLI: tide info --json odpowiada"; else lack "CLI tide info --json zwraca błąd"; fi
fi

# Słabsza maszyna: indeks wyszukiwania po znaczeniu co 5 min skanuje sesje agentów i ładuje model bge-m3
# (z Ollamą ok. 1,2 GB) — wyłączamy go; KRunner dalej szuka po słowach. W --check tylko stan.
if [[ $low_power -eq 1 && $dbus_ok -eq 1 ]]; then
    search="$(call /Models org.tide.Daemon.Models Status 2>/dev/null | $PY -c '
import json, sys
try:
    print("on" if (json.loads(sys.stdin.read()).get("search") or {}).get("enabled") else "off")
except ValueError:
    print("?")' || echo "?")"
    if [[ "$search" == off ]]; then
        ok "wyszukiwanie po znaczeniu wyłączone"
    elif [[ $check -eq 1 ]]; then
        warn "wyszukiwanie po znaczeniu włączone — na słabszej maszynie wyłącz: busctl --user call org.tide.Daemon /Models org.tide.Daemon.Models SetSemanticSearch s off"
    elif reply="$(call /Models org.tide.Daemon.Models SetSemanticSearch s off 2>&1)" && \
            $PY -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1]).get("ok") else 1)' "$reply" 2>/dev/null; then
        ok "wyłączono wyszukiwanie po znaczeniu (SetSemanticSearch off)"
    else
        warn "nie udało się wyłączyć wyszukiwania po znaczeniu: $reply"
    fi
fi

# --- 7. Tablica -----------------------------------------------------------------------------------

step "Tablica"
# Usługa systemd nie widzi TIDE_BOARD_DIR z powłoki — liczy się katalog domyślny.
board="$HOME/DevWorkspace/tide-board"
[[ -z "${TIDE_BOARD_DIR:-}" ]] || warn "TIDE_BOARD_DIR=$TIDE_BOARD_DIR w powłoce nie dotyczy usługi — Tablica usługi to $board"
mkdir_workspace() { [[ -d "$HOME/DevWorkspace" ]] || { mkdir -p "$HOME/DevWorkspace" && ok "założono katalog projektów ~/DevWorkspace"; }; }
if [[ $dbus_ok -eq 1 ]]; then
    # Migawka bywa większa niż limit jednego argumentu (128 KiB) — tylko przez stdin.
    summary="$(call /Board org.tide.Daemon.Board Snapshot 2>/dev/null | $PY -c '
import json, sys
try:
    s = json.loads(sys.stdin.read())
except ValueError:
    print("?"); sys.exit()
print(s.get("exists"), len(s.get("cards") or []), len(s.get("projects") or []))' || echo "?")"
    exists="${summary%% *}"
    if [[ "$exists" == "?" ]]; then
        lack "/Board Snapshot nie odpowiada — journalctl --user -u tide-daemon -n 50"
    elif [[ "$exists" == True ]]; then
        read -r _ cards projects <<<"$summary"
        ok "Tablica $board: kart $cards, projektów $projects"
        if [[ -n "$(git -C "$board" remote 2>/dev/null)" ]]; then
            warn "Tablica ma zdalne repozytorium ($(git -C "$board" remote | paste -sd, -)) — sprawdź, czy to nie kopia Tablicy innej maszyny"
        fi
    elif [[ -e "$board" && -n "$(ls -A "$board" 2>/dev/null)" ]]; then
        lack "$board istnieje, ale nie jest Tablicą Tide (brak board.md z tide-board) — przenieś go albo opróżnij"
    elif [[ $check -eq 1 || $no_board -eq 1 ]]; then
        ok "Tablicy jeszcze nie ma — powstanie pusta przy pierwszym zapisie ($board)"
    else
        mkdir_workspace
        # Dowolny zapis zakłada Tablicę (store.ensure): board.md, tagi startowe, szablony flows/. Tag „własne” jest
        # w zasiewie, więc jego zapis niczego poza tym nie dodaje.
        reply="$(call /Board org.tide.Daemon.Board UpsertTag s '{"name": "własne"}' 2>&1 || true)"
        if $PY -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1]).get("ok") else 1)' "$reply" 2>/dev/null; then
            ok "założono pustą Tablicę: $board"
        else
            lack "nie udało się założyć Tablicy: $reply"
        fi
    fi
fi
[[ $check -eq 1 ]] || mkdir_workspace

# --- 8. okno bez ekranu ---------------------------------------------------------------------------

step "Okno Tide (próba bez ekranu)"
window="$data/tide/tide-window/tide.py"
if [[ -f "$window" && $python_ok -eq 1 ]]; then
    tmp="$(mktemp -d)"
    echo '{}' >"$tmp/empty.json"
    failed=()
    # Każdy widok z pustymi danymi (--data: bez D-Bus i bez rejestracji okna), zrzut do pliku — ładuje cały QML.
    views=(board issues sessions worktrees docker reports models)
    probe_timeout=60
    # Słabszy procesor: jeden widok z dłuższym limitem zamiast siedmiu.
    [[ $low_power -eq 1 ]] && { views=(board); probe_timeout=120; }
    for view in "${views[@]}"; do
        if ! QT_QPA_PLATFORM=offscreen timeout "$probe_timeout" $PY "$window" --view "$view" --data "$view=$tmp/empty.json" \
                --grab "$tmp/$view.png" >"$tmp/$view.log" 2>&1 || [[ ! -s "$tmp/$view.png" ]]; then
            failed+=("$view")
            tail -3 "$tmp/$view.log" | sed 's/^/    /'
        fi
    done
    if [[ ${#failed[@]} -eq 0 ]]; then ok "QML się ładuje (widoki: ${views[*]})"; else lack "okno: widoki z błędem QML: ${failed[*]}"; fi
    rm -rf "$tmp"
elif [[ ! -f "$window" && $check -eq 0 ]]; then
    lack "okno Tide niezainstalowane ($window)"
fi

# --- podsumowanie ---------------------------------------------------------------------------------

step "Podsumowanie"
if [[ ${#notes[@]} -gt 0 ]]; then
    echo "  Uwagi:"
    printf '    - %s\n' "${notes[@]}"
fi
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "  Brakuje:"
    printf '    - %s\n' "${missing[@]}"
    [[ $check -eq 1 ]] && echo "  Instalacja: bash scripts/bootstrap-machine.sh"
    exit 2
fi
echo "  Tide gotowe. Okno: menu aplikacji → Tide (albo: /usr/bin/python3 $window --view board)."
exit 0
