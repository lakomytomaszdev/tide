#!/bin/bash
# Szablon stosu node (PT-192, #29): Node w wersji z profilu (`node.version`) z nodejs.org, npm z tej dystrybucji,
# CHROME_BIN dla Karmy. Uruchamia builder jako root pudełka: `bash /opt/tide/stacks/node.sh` (lib.sh obok).
#
# Wersja: `TIDE_NODE_VERSION` z profilu — dokładna `x.y.z` albo sam numer głównej wersji, który ten plik zamienia
# na przypiętą wersję z NODE_PINS (zmiana przypięcia = zmiana szablonu = nowy STACKS_VERSION i przebudowa pudełek).
# Bez wersji w profilu: NODE_DEFAULT_MAJOR. Archiwum ze sprawdzoną sumą z SHASUMS256.txt tej samej wersji.
# Node z apt obrazu bazowego (/usr/bin/node, dla Codexa) zostaje; ten Node ląduje w /usr/local i wygrywa w PATH.
set -euo pipefail

# shellcheck source=lib.sh
source "${BASH_SOURCE[0]%/*}/lib.sh"

NODE_DIST=https://nodejs.org/dist
declare -A NODE_PINS=([18]=18.20.8 [20]=20.19.5 [22]=22.20.0 [24]=24.11.0)
NODE_DEFAULT_MAJOR=22
CHROME_WRAPPER=/usr/local/bin/tide-chrome
PLAYWRIGHT_BROWSERS=/opt/ms-playwright

node_version() {  # node_version <wersja z profilu> — dokładna wersja x.y.z
    local want=${1:-$NODE_DEFAULT_MAJOR}
    if [[ $want =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "$want"
    elif [[ $want =~ ^[0-9]+$ && -n ${NODE_PINS[$want]:-} ]]; then
        echo "${NODE_PINS[$want]}"
    else
        die "node.version '$want': podaj dokładną wersję x.y.z albo główną z przypiętych (${!NODE_PINS[*]})"
    fi
}

node_arch() {
    case $(dpkg --print-architecture) in
        amd64) echo x64 ;;
        arm64) echo arm64 ;;
        *) die "architektura $(dpkg --print-architecture) bez dystrybucji Node" ;;
    esac
}

write_chrome_wrapper() {  # write_chrome_wrapper <root> — CHROME_BIN: Chromium z szablonu playwright
    local root=$1
    mkdir -p "$root/usr/local/bin"
    cat > "$root$CHROME_WRAPPER" <<EOF
#!/bin/bash
# Tide (PT-192): CHROME_BIN dla Karmy — Chromium pobrany przez szablon playwright do $PLAYWRIGHT_BROWSERS.
shopt -s nullglob
found=("$PLAYWRIGHT_BROWSERS"/chromium-*/chrome-linux*/chrome)
if (( \${#found[@]} == 0 )); then
    echo "tide-chrome: brak Chromium w $PLAYWRIGHT_BROWSERS — dodaj stos playwright do profilu pudełka" >&2
    exit 127
fi
exec "\${found[-1]}" "\$@"
EOF
    chmod 0755 "$root$CHROME_WRAPPER"
}

install_node() {
    local version arch name work out
    version=$(node_version "${TIDE_NODE_VERSION:-}")
    arch=$(node_arch)
    name="node-v$version-linux-$arch.tar.gz"
    work=$(mktemp -d)
    fetch "$NODE_DIST/v$version/SHASUMS256.txt" "$work/SHASUMS256.txt"
    fetch "$NODE_DIST/v$version/$name" "$work/$name"
    local line
    line=$(grep -E "^[0-9a-f]{64}  $name\$" "$work/SHASUMS256.txt") || die "brak sumy $name w SHASUMS256.txt"
    check_sha256 "${line%% *}" "$work/$name"
    tar -xzf "$work/$name" -C /usr/local --strip-components=1 --no-same-owner \
        --exclude='*/CHANGELOG.md' --exclude='*/README.md' --exclude='*/LICENSE'
    rm -rf "$work"
    hash -r
    out=$(/usr/local/bin/node --version)
    [[ $out == "v$version" ]] || die "node --version: oczekiwano v$version, jest $out"
    echo "node: $out, npm: $(/usr/local/bin/npm --version)"
}

main() {
    stack_main
    install_node
    write_chrome_wrapper ""
    # NODE_USE_ENV_PROXY: `fetch` Node ≥ 24 honoruje HTTP(S)_PROXY dopiero z tą zmienną (P-6, „NO_PROXY per klient”).
    write_stack_env "" node "CHROME_BIN=$CHROME_WRAPPER" NODE_USE_ENV_PROXY=1
    npm cache clean --force
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
