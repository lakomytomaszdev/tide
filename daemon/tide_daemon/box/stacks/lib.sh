#!/bin/bash
# Wspólne funkcje szablonów stosów (PT-192, #29) — wczytywane przez `box/stacks/<stos>.sh`, nigdy uruchamiane samo.
# Builder (box/builder.py) wypycha ten plik razem ze skryptami szablonów do /opt/tide/stacks/ i uruchamia każdy
# szablon jako root pudełka: `bash /opt/tide/stacks/<stos>.sh`, z siecią wyłącznie przez proxy Tide (#5).
#
# Wartości z usługi (żadna nie jest wpisana w szablonach):
#   TIDE_BOX_PROXY       http://<adres mostka>:<box.proxy-port> — to samo co HTTP(S)_PROXY wywołania
#   TIDE_BOX_NO_PROXY    box/netenv.py BOX_NO_PROXY (wynik P-6)
#   TIDE_NODE_VERSION    node.version z profilu (node.sh)
#   TIDE_DOTNET_SDK      dotnet.sdk z profilu, wersje rozdzielone spacją (dotnet.sh)
# Żaden szablon nie czyta ani nie kopiuje plików agentów hosta: pudełko widzi tylko to, co wypchnął builder.

AGENT=agent
AGENT_HOME=/home/agent
APT_MARK=/run/tide-stacks-apt-updated

die() {
    echo "${0##*/}: $*" >&2
    exit 1
}

check_root() {
    [[ $EUID -eq 0 ]] || die "uruchamiane jako root pudełka (incus exec przez tide box build), nie na hoście"
}

check_proxy() {
    [[ ${TIDE_BOX_PROXY:-} =~ ^http://[0-9]{1,3}(\.[0-9]{1,3}){3}:[0-9]{1,5}$ ]] \
        || die "TIDE_BOX_PROXY musi być http://<adres mostka>:<port> (ustawia tide box build), jest: '${TIDE_BOX_PROXY:-}'"
    [[ ${TIDE_BOX_NO_PROXY:-} =~ ^[A-Za-z0-9.,:/_-]+$ ]] \
        || die "TIDE_BOX_NO_PROXY pusty albo z niedozwolonymi znakami (ustawia tide box build)"
    # Budowa bez proxy nie istnieje: polecenia sieciowe szablonu muszą iść przez to samo proxy.
    [[ ${HTTPS_PROXY:-} == "$TIDE_BOX_PROXY" && ${HTTP_PROXY:-} == "$TIDE_BOX_PROXY" ]] \
        || die "HTTP(S)_PROXY wywołania różne od TIDE_BOX_PROXY — budowa bez proxy Tide jest niedozwolona"
}

stack_main() {  # stack_main — wspólny początek każdego szablonu
    check_root
    check_proxy
    export DEBIAN_FRONTEND=noninteractive
}

apt_install() {  # apt_install <pakiet…> — jedno `apt-get update` na budowę (listy apt obraz bazowy usuwa)
    if [[ ! -e $APT_MARK ]]; then
        apt-get update --error-on=any
        touch "$APT_MARK"
    fi
    apt-get install -y --no-install-recommends "$@"
}

fetch() {  # fetch <url https> <plik> — pobranie do pliku (nigdy potok do powłoki), także przekierowania tylko https
    [[ $1 == https://* ]] || die "fetch: tylko https:// ($1)"
    curl -fsSL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 3 -o "$2" "$1"
}

check_sha256() {  # check_sha256 <suma> <plik>
    local sum
    sum=$(sha256sum "$2")
    [[ ${sum%% *} == "$1" ]] || die "suma SHA-256 $2: oczekiwano $1, jest ${sum%% *}"
}

as_agent() {  # as_agent <polecenie…> — jako agent, z HOME agenta
    runuser -u "$AGENT" -- env HOME="$AGENT_HOME" "$@"
}

write_stack_env() {  # write_stack_env <root> <stos> KLUCZ=wartość… — zmienne stosu w pudełku
    # Trzy miejsca jak w obrazie bazowym (base.sh write_environment): /etc/environment, powłoki logowania
    # i DefaultEnvironment usług systemd --user (kroki tide-step). Plik stosu jest zastępowany w całości.
    local root=$1 stack=$2 pair key value file tmp keys=()
    shift 2
    [[ $stack =~ ^[a-z0-9-]+$ ]] || die "write_stack_env: zła nazwa stosu '$stack'"
    for pair in "$@"; do
        key=${pair%%=*}
        value=${pair#*=}
        [[ $pair == *=* && $key =~ ^[A-Z][A-Z0-9_]*$ && $value =~ ^[A-Za-z0-9._/:,-]*$ ]] \
            || die "write_stack_env: niedozwolona zmienna '$pair'"
        keys+=("$key")
    done
    mkdir -p "$root/etc/profile.d" "$root/etc/systemd/user.conf.d"
    file="$root/etc/environment"
    touch "$file"
    tmp=$(mktemp "$file.XXXXXX")
    local pattern
    pattern="^($(IFS='|'; echo "${keys[*]}"))="
    grep -Ev "$pattern" "$file" > "$tmp" || true
    for pair in "$@"; do
        printf '%s="%s"\n' "${pair%%=*}" "${pair#*=}" >> "$tmp"
    done
    chmod 0644 "$tmp"
    mv -f "$tmp" "$file"
    {
        echo "# Tide (PT-192): zmienne stosu $stack (wartości jak w /etc/environment)."
        for pair in "$@"; do
            printf 'export %s="%s"\n' "${pair%%=*}" "${pair#*=}"
        done
    } > "$root/etc/profile.d/tide-$stack.sh"
    chmod 0644 "$root/etc/profile.d/tide-$stack.sh"
    {
        echo "# Tide (PT-192): zmienne stosu $stack dla usług systemd --user (kroki tide-step)."
        echo "[Manager]"
        printf 'DefaultEnvironment='
        for pair in "$@"; do
            printf '"%s" ' "$pair"
        done
        printf '\n'
    } > "$root/etc/systemd/user.conf.d/91-tide-$stack.conf"
    chmod 0644 "$root/etc/systemd/user.conf.d/91-tide-$stack.conf"
}
