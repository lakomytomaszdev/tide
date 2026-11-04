#!/bin/bash
# Obraz bazowy Tide (PT-179, #16) — uruchamiany WYŁĄCZNIE przez `tide box base` (box/base.py) jako root pudełka
# z `images:ubuntu/24.04`, po jednym kroku na wywołanie: `bash base.sh <krok>`. Kolejność kroków: STEPS w box/base.py.
#
# Sieć tylko przez proxy Tide (#5): każde wywołanie dostaje HTTP(S)_PROXY na adres mostka, a skrypt odmawia pracy,
# gdy go nie ma — innej drogi do sieci pudełko nie ma (nftables z PT-166). Wartości z usługi (nie wpisane tutaj):
#   TIDE_BOX_PROXY       http://<adres mostka>:<box.proxy-port>
#   TIDE_BOX_NO_PROXY    box/netenv.py BOX_NO_PROXY (wynik P-6)
#   TIDE_AGENT_UID/GID   box.agent-uid/gid (1000)
#   TIDE_CLAUDE_VERSION, TIDE_CODEX_VERSION   box/agents.py — dokładne wersje, nigdy latest
# Pliki wypchnięte przez `tide box base`: /opt/tide/stacks/{base,tide-step}.sh, /opt/tide/fonts.tar (czcionki
# użytkownika z ~/.local/share/fonts hosta). Żaden plik ani token z katalogów agentów hosta nie trafia do obrazu:
# agenci działają przez K3 (box/k3.py), a logowanie K1 to osobne `tide box login`.
set -euo pipefail

STACKS=/opt/tide/stacks
FONTS_TAR=/opt/tide/fonts.tar
FONTS_DIR=/usr/local/share/fonts/tide
AGENT=agent
AGENT_HOME=/home/agent
# Pakiety obrazu: narzędzia budowy, ekran bez monitora (xvfb, weston), czcionki, Node+npm dla Codexa,
# piaskownica agentów w pudełku (bubblewrap, socat, apparmor — decyzja bwrap-profil-w-pudelku).
PACKAGES=(git build-essential curl ca-certificates xvfb weston fontconfig nodejs npm bubblewrap socat apparmor)
PROXY_KEYS=(HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy)

die() {
    echo "base.sh: $*" >&2
    exit 1
}

# --- wejście ---------------------------------------------------------------------------------------------

check_proxy() {
    [[ ${TIDE_BOX_PROXY:-} =~ ^http://[0-9]{1,3}(\.[0-9]{1,3}){3}:[0-9]{1,5}$ ]] \
        || die "TIDE_BOX_PROXY musi być http://<adres mostka>:<port> (ustawia tide box base), jest: '${TIDE_BOX_PROXY:-}'"
    [[ ${TIDE_BOX_NO_PROXY:-} =~ ^[A-Za-z0-9.,:/_-]+$ ]] \
        || die "TIDE_BOX_NO_PROXY pusty albo z niedozwolonymi znakami (ustawia tide box base)"
    # Budowa bez proxy nie istnieje: polecenia sieciowe tego wywołania muszą iść przez to samo proxy.
    [[ ${HTTPS_PROXY:-} == "$TIDE_BOX_PROXY" && ${HTTP_PROXY:-} == "$TIDE_BOX_PROXY" ]] \
        || die "HTTP(S)_PROXY wywołania różne od TIDE_BOX_PROXY — budowa bez proxy Tide jest niedozwolona"
}

check_pinned() {  # check_pinned <wersja> <program>
    [[ $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "wersja $2 nie jest przypięta (x.y.z): '$1'"
}

check_ids() {
    [[ ${TIDE_AGENT_UID:-} =~ ^[0-9]+$ && ${TIDE_AGENT_GID:-} =~ ^[0-9]+$ ]] \
        || die "TIDE_AGENT_UID/TIDE_AGENT_GID muszą być liczbami (ustawia tide box base)"
}

# --- pliki konfiguracji (z katalogiem głównym jako argumentem — testy piszą do katalogu tymczasowego) ----------

write_apt_proxy() {  # write_apt_proxy <root>
    local root=$1
    mkdir -p "$root/etc/apt/apt.conf.d"
    cat > "$root/etc/apt/apt.conf.d/90tide-proxy" <<EOF
// Tide (PT-179): apt w pudełku tylko przez proxy Tide — CONNECT do źródeł https:// (decyzja apt-przez-https).
Acquire::http::Proxy "$TIDE_BOX_PROXY";
Acquire::https::Proxy "$TIDE_BOX_PROXY";
EOF
    chmod 0644 "$root/etc/apt/apt.conf.d/90tide-proxy"
}

https_sources() {  # https_sources <root> — źródła Ubuntu z http:// na https:// (deb822 albo klasyczne)
    local root=$1 file found=0
    for file in "$root/etc/apt/sources.list.d/ubuntu.sources" "$root/etc/apt/sources.list"; do
        [[ -s $file ]] && grep -Eq '^(URIs:|deb )' "$file" || continue
        sed -i -E 's#http://(archive|security)\.ubuntu\.com#https://\1.ubuntu.com#g' "$file"
        if grep -Eq '^(URIs:|deb ).*http://' "$file"; then
            die "$file: zostało źródło http:// spoza archive/security.ubuntu.com — proxy Tide go nie przepuści"
        fi
        found=1
    done
    (( found )) || die "brak źródeł apt (ani ubuntu.sources, ani sources.list)"
}

proxy_lines() {  # proxy_lines <format> — KEY=wartość dla każdej zmiennej proxy w formacie printf
    local key value
    for key in "${PROXY_KEYS[@]}"; do
        case $key in
            [Nn][Oo]_*) value=$TIDE_BOX_NO_PROXY ;;
            *) value=$TIDE_BOX_PROXY ;;
        esac
        # shellcheck disable=SC2059  # format to stała z tego pliku
        printf "$1" "$key" "$value"
    done
}

write_environment() {  # write_environment <root> — /etc/environment, powłoki logowania i systemd --user
    local root=$1 file tmp
    file="$root/etc/environment"
    mkdir -p "$root/etc" "$root/etc/profile.d" "$root/etc/systemd/user.conf.d"
    touch "$file"
    tmp=$(mktemp "$file.XXXXXX")
    grep -Ev '^(HTTP_PROXY|HTTPS_PROXY|NO_PROXY|http_proxy|https_proxy|no_proxy|DISABLE_AUTOUPDATER)=' "$file" \
        > "$tmp" || true
    {
        proxy_lines '%s="%s"\n'
        # Wersja Claude Code jest przypięta w obrazie — bez samodzielnej aktualizacji w pudełku.
        printf 'DISABLE_AUTOUPDATER="1"\n'
    } >> "$tmp"
    chmod 0644 "$tmp"
    mv -f "$tmp" "$file"

    {
        echo "# Tide (PT-179): proxy pudełka także w powłokach logowania (wartości jak w /etc/environment)."
        proxy_lines 'export %s="%s"\n'
        echo 'export DISABLE_AUTOUPDATER="1"'
    } > "$root/etc/profile.d/tide-proxy.sh"
    chmod 0644 "$root/etc/profile.d/tide-proxy.sh"

    # Krok w pudełku to usługa systemd --user agenta (decyzja krok-jako-usluga-user) — dostaje proxy stąd.
    {
        echo "# Tide (PT-179): proxy pudełka dla usług systemd --user (kroki tide-step)."
        echo "[Manager]"
        printf 'DefaultEnvironment='
        proxy_lines '"%s=%s" '
        printf '"DISABLE_AUTOUPDATER=1"\n'
    } > "$root/etc/systemd/user.conf.d/90-tide-proxy.conf"
    chmod 0644 "$root/etc/systemd/user.conf.d/90-tide-proxy.conf"
}

write_bwrap_profile() {  # write_bwrap_profile <root> — ten sam profil co scripts/sandbox/check.py na hoście
    local root=$1
    mkdir -p "$root/etc/apparmor.d"
    cat > "$root/etc/apparmor.d/bwrap-userns-restrict" <<'EOF'
# Tide (PT-179): piaskownica agentów w pudełku (decyzja bwrap-profil-w-pudelku). Profil wczytany w przestrzeni
# AppArmor pudełka: tylko /usr/bin/bwrap może zakładać przestrzenie użytkownika; host bez zmian.

abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,

  # Site-specific additions and overrides. See local/README for details.
  include if exists <local/bwrap>
}
EOF
    chmod 0644 "$root/etc/apparmor.d/bwrap-userns-restrict"
}

as_agent() {  # as_agent <polecenie…> — jako agent, z czystym HOME i bez ruchu poza potrzebnym
    runuser -u "$AGENT" -- env HOME="$AGENT_HOME" DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 "$@"
}

check_version() {  # check_version <program> <wersja>
    local out
    out=$(as_agent "$1" --version 2>&1) || die "$1 --version jako $AGENT: $out"
    [[ $out == *"$2"* ]] || die "$1 --version: oczekiwano $2, jest: $out"
    echo "$1: $out"
}

# --- kroki ------------------------------------------------------------------------------------------------

step_sources() {
    https_sources ""
    write_apt_proxy ""
}

step_packages() {
    apt-get update --error-on=any
    apt-get install -y --no-install-recommends "${PACKAGES[@]}"
}

step_user() {
    check_ids
    local name
    name=$(getent passwd "$TIDE_AGENT_UID" | cut -d: -f1 || true)
    if [[ -z $name ]]; then
        getent passwd "$AGENT" >/dev/null && die "użytkownik $AGENT istnieje z innym uid niż $TIDE_AGENT_UID"
        getent group "$TIDE_AGENT_GID" >/dev/null && die "grupa o gid $TIDE_AGENT_GID już istnieje"
        groupadd -g "$TIDE_AGENT_GID" "$AGENT"
        useradd -m -d "$AGENT_HOME" -u "$TIDE_AGENT_UID" -g "$TIDE_AGENT_GID" -s /bin/bash "$AGENT"
    elif [[ $name != "$AGENT" ]]; then
        # Obraz może mieć `ubuntu` z uid 1000 — przejmujemy go jako agent.
        usermod -l "$AGENT" -d "$AGENT_HOME" -m "$name"
        groupmod -n "$AGENT" "$(id -gn "$TIDE_AGENT_UID")"
    fi
    [[ $(id -u "$AGENT") == "$TIDE_AGENT_UID" && $(id -g "$AGENT") == "$TIDE_AGENT_GID" ]] \
        || die "$AGENT ma uid/gid $(id -u "$AGENT")/$(id -g "$AGENT"), oczekiwano $TIDE_AGENT_UID/$TIDE_AGENT_GID"
    # Lingering: systemd --user agenta działa bez sesji logowania (kroki jako usługi użytkownika, P-9).
    if ! loginctl enable-linger "$AGENT" 2>/dev/null; then
        mkdir -p /var/lib/systemd/linger
        touch "/var/lib/systemd/linger/$AGENT"
    fi
}

step_environment() {
    write_environment ""
}

step_fonts() {
    rm -rf "$FONTS_DIR"
    mkdir -p "$FONTS_DIR"
    if [[ -s $FONTS_TAR ]]; then
        tar -xf "$FONTS_TAR" -C "$FONTS_DIR" --no-same-owner --no-same-permissions
        chmod -R u=rwX,go=rX "$FONTS_DIR"
    fi
    fc-cache -f
    echo "czcionki: $(find "$FONTS_DIR" -type f | wc -l) plików w $FONTS_DIR"
}

step_tide_step() {
    install -m 0755 -o root -g root "$STACKS/tide-step.sh" /usr/local/bin/tide-step
}

step_apparmor() {
    write_bwrap_profile ""
    systemctl enable apparmor.service
    apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
}

step_claude() {
    check_pinned "${TIDE_CLAUDE_VERSION:-}" "Claude Code"
    local work bin
    work=$(mktemp -d)
    mkdir -p "$work/home"
    # Instalator natywny pobrany do pliku (bez potoku do powłoki), z domeny szablonu base, z dokładną wersją.
    curl -fsSL --proto '=https' --tlsv1.2 -o "$work/install.sh" https://claude.ai/install.sh
    HOME="$work/home" DISABLE_AUTOUPDATER=1 bash "$work/install.sh" "$TIDE_CLAUDE_VERSION"
    bin=$(readlink -f "$work/home/.local/bin/claude")
    [[ -f $bin ]] || die "instalator Claude Code nie zostawił programu w $work/home/.local/bin"
    # Program poza /home/agent: w pudełku projektu /home/agent to osobny wolumen, który by go przykrył.
    install -m 0755 -o root -g root "$bin" /usr/local/bin/claude
    rm -rf "$work"
    check_version claude "$TIDE_CLAUDE_VERSION"
}

step_codex() {
    check_pinned "${TIDE_CODEX_VERSION:-}" "Codex"
    npm install -g --no-fund --no-audit "@openai/codex@$TIDE_CODEX_VERSION"
    check_version codex "$TIDE_CODEX_VERSION"
}

step_cleanup() {
    apt-get clean
    npm cache clean --force
    rm -rf /var/lib/apt/lists/* /root/.npm /opt/tide
}

STEPS=(sources packages user environment fonts tide-step apparmor claude codex cleanup)

main() {
    local step=${1:-} known="" name
    for name in "${STEPS[@]}"; do
        [[ $name == "$step" ]] && known=$name
    done
    if [[ $# -ne 1 || -z $known ]]; then
        echo "użycie: bash base.sh <krok>   kroki: ${STEPS[*]}" >&2
        exit 2
    fi
    [[ $EUID -eq 0 ]] || die "uruchamiane jako root pudełka (incus exec), nie na hoście"
    check_proxy
    export DEBIAN_FRONTEND=noninteractive
    "step_${step//-/_}"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
