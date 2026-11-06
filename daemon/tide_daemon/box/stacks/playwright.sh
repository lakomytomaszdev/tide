#!/bin/bash
# Szablon stosu playwright (PT-192, #29): `npx playwright install --with-deps chromium` przez proxy Tide, przeglądarki
# w /opt/ms-playwright (część migawki clean, nie wolumenu /home/agent). Wymaga szablonu node przed sobą w `stacks`
# (Playwright ≥ 1.50 potrzebuje Node ≥ 20, a Node z apt obrazu bazowego to 18 — P-6).
#
# Wersja Playwrighta przypięta tutaj (PLAYWRIGHT_VERSION). Projekt z inną wersją w package.json dociąga w kroku
# swoją rewizję Chromium do tego samego katalogu (należy do agenta; domeny pobierania są w playwright.yaml).
# Zależności systemowe (`--with-deps`) instaluje apt przez proxy z obrazu bazowego.
# PT-184: profil z dotnet.sdk bez node.version korzysta z Microsoft.Playwright i dołączonego
# do paczki sterownika Node. Nie wymaga stosu node ani npx. Wersja .NET odpowiada LegalApp.
set -euo pipefail

# shellcheck source=lib.sh
source "${BASH_SOURCE[0]%/*}/lib.sh"

PLAYWRIGHT_VERSION=1.55.0
PLAYWRIGHT_DOTNET_VERSION=1.52.0
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

dotnet_project() {
    local sdk=${TIDE_DOTNET_SDK%% *}
    [[ $sdk =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Playwright .NET wymaga dokładnego dotnet.sdk"
    cat <<EOF
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net${sdk%.*}</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.Playwright" Version="$PLAYWRIGHT_DOTNET_VERSION" />
  </ItemGroup>
</Project>
EOF
}

install_dotnet_browsers() (
    # Osobny proces powłoki ogranicza trap do tej funkcji. Projekt instalatora zostaje usunięty
    # także po błędzie NuGet lub instalatora, przeglądarka zostaje w migawce clean.
    local project work
    project=$(dotnet_project)
    command -v dotnet >/dev/null || die "brak dotnet — dodaj stos dotnet przed playwright"
    work=$(mktemp -d)
    trap 'rm -rf -- "$work"' EXIT
    printf '%s\n' "$project" > "$work/BrowserInstall.csproj"
    printf '%s\n' 'return Microsoft.Playwright.Program.Main(args);' > "$work/Program.cs"
    mkdir -p "$PLAYWRIGHT_BROWSERS"
    export PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS"
    # Źródło tylko publiczne; nie czytamy konfiguracji ani poświadczeń NuGet hosta.
    dotnet restore "$work/BrowserInstall.csproj" --source https://api.nuget.org/v3/index.json
    dotnet run --project "$work/BrowserInstall.csproj" --configuration Release --no-restore -- install --with-deps chromium
    shopt -s nullglob
    local found=("$PLAYWRIGHT_BROWSERS"/chromium-*/chrome-linux*/chrome)
    (( ${#found[@]} > 0 )) || die "Playwright .NET nie zostawił Chromium w $PLAYWRIGHT_BROWSERS"
    chown -R "$AGENT:$AGENT" "$PLAYWRIGHT_BROWSERS"
    echo "playwright .NET $PLAYWRIGHT_DOTNET_VERSION: ${found[*]}"
)

install_playwright() {
    if [[ -n ${TIDE_DOTNET_SDK:-} && -z ${TIDE_NODE_VERSION:-} ]]; then
        install_dotnet_browsers
    else
        check_node
        install_browsers
    fi
}

write_chromium_profile() {  # write_chromium_profile <root> — przestrzenie użytkownika piaskownicy Chromium
    local root=$1
    mkdir -p "$root/etc/apparmor.d"
    cat > "$root/etc/apparmor.d/tide-chromium-userns" <<'EOF'
# Tide (PT-185): profil w przestrzeni AppArmor pudełka, analogiczny do bwrap w obrazie bazowym.
# Pozwala Chromium uruchomić własną piaskownicę; ustawienia i profile hosta pozostają bez zmian.
abi <abi/4.0>,
include <tunables/global>

profile tide-chromium /opt/ms-playwright/chromium-*/chrome-linux*/chrome flags=(unconfined) {
  userns,
}

profile tide-chromium-headless /opt/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell flags=(unconfined) {
  userns,
}

profile tide-chromium-headless-new /opt/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell flags=(unconfined) {
  userns,
}
EOF
    chmod 0644 "$root/etc/apparmor.d/tide-chromium-userns"
}

main() {
    stack_main
    install_playwright
    write_chromium_profile ""
    # Błąd ładowania przerywa budowę. Usługa apparmor z obrazu bazowego wczytuje plik także po restarcie.
    apparmor_parser -r /etc/apparmor.d/tide-chromium-userns
    write_stack_env "" playwright "PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
