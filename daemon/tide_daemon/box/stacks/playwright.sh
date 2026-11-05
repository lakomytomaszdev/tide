#!/bin/bash
# Szablon stosu playwright (PT-192, #29): `npx playwright install --with-deps chromium` przez proxy Tide, przeglądarki
# w /opt/ms-playwright (część migawki clean, nie wolumenu /home/agent). Wymaga szablonu node przed sobą w `stacks`
# (Playwright ≥ 1.50 potrzebuje Node ≥ 20, a Node z apt obrazu bazowego to 18 — P-6).
#
# Wersja Playwrighta przypięta tutaj (PLAYWRIGHT_VERSION). Projekt z inną wersją w package.json dociąga w kroku
# swoją rewizję Chromium do tego samego katalogu (należy do agenta; domeny pobierania są w playwright.yaml).
# Zależności systemowe (`--with-deps`) instaluje apt przez proxy z obrazu bazowego.
set -euo pipefail

# shellcheck source=lib.sh
source "${BASH_SOURCE[0]%/*}/lib.sh"

PLAYWRIGHT_VERSION=1.55.0
PLAYWRIGHT_BROWSERS=/opt/ms-playwright
NODE_MIN_MAJOR=20

check_node() {
    local out major
    out=$(node --version 2>/dev/null) || die "brak node — dodaj stos node przed playwright w stacks profilu"
    major=${out#v}
    major=${major%%.*}
    [[ $major =~ ^[0-9]+$ ]] && (( major >= NODE_MIN_MAJOR )) \
        || die "Playwright $PLAYWRIGHT_VERSION wymaga Node ≥ $NODE_MIN_MAJOR, jest $out — stos node przed playwright"
}

install_browsers() {
    local work
    work=$(mktemp -d)
    mkdir -p "$PLAYWRIGHT_BROWSERS"
    (
        cd "$work"
        HOME="$work" npm_config_cache="$work/npm" npm_config_update_notifier=false \
            PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS" \
            npx --yes "playwright@$PLAYWRIGHT_VERSION" install --with-deps chromium
    )
    rm -rf "$work"
    shopt -s nullglob
    local found=("$PLAYWRIGHT_BROWSERS"/chromium-*/chrome-linux*/chrome)
    shopt -u nullglob
    (( ${#found[@]} > 0 )) || die "Playwright nie zostawił Chromium w $PLAYWRIGHT_BROWSERS"
    chown -R "$AGENT:$AGENT" "$PLAYWRIGHT_BROWSERS"
    echo "playwright $PLAYWRIGHT_VERSION: ${found[*]}"
}

main() {
    stack_main
    check_node
    install_browsers
    write_stack_env "" playwright "PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
