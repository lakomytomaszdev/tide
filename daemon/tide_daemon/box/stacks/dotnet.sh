#!/bin/bash
# Szablon stosu dotnet (PT-192, #29): SDK .NET w wersjach z profilu (`dotnet.sdk`) przez dotnet-install.sh
# z dot.net, do /usr/share/dotnet; NuGet.Config agenta wyłącznie z nuget.org (bez rejestrów wewnętrznych).
#
# Wersje: `TIDE_DOTNET_SDK` z profilu, rozdzielone spacją — tylko dokładne wersje SDK, np. `8.0.415`.
# Skróty `8` i `8.0` są odrzucane: wybór poprawki w chwili budowy omijałby hash przepisu.
# Skrypt instalatora jest pobierany do pliku (bez potoku do powłoki) i uruchamiany wyłącznie z --version.
set -euo pipefail

# shellcheck source=lib.sh
source "${BASH_SOURCE[0]%/*}/lib.sh"

DOTNET_INSTALL_URL=https://dot.net/v1/dotnet-install.sh
DOTNET_DIR=/usr/share/dotnet
# Zależności SDK na Ubuntu 24.04 (reszta jest w obrazie bazowym).
DOTNET_DEPS=(libicu74 libssl3t64 zlib1g)
NUGET_SOURCE=https://api.nuget.org/v3/index.json

sdk_args() {  # sdk_args <wersja z profilu> — argumenty dotnet-install.sh
    local want=$1
    if [[ $want =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "--version $want"
    else
        die "dotnet.sdk '$want': podaj dokładną wersję SDK x.y.z, np. 8.0.415 (skróty i kanały są niedozwolone)"
    fi
}

nuget_config() {  # nuget_config — treść NuGet.Config agenta: tylko nuget.org
    cat <<EOF
<?xml version="1.0" encoding="utf-8"?>
<!-- Tide (PT-192): w pudełku jedynym źródłem pakietów jest nuget.org (szablon dotnet). -->
<configuration>
  <packageSources>
    <clear />
    <add key="nuget.org" value="$NUGET_SOURCE" protocolVersion="3" />
  </packageSources>
</configuration>
EOF
}

write_nuget_config() {
    # /home/agent to wolumen agenta (przeżywa przebudowę): zapis jako agent, nie root — dowiązanie podłożone
    # w ~/.nuget nie skieruje zapisu roota na plik systemowy.
    # shellcheck disable=SC2016  # $HOME rozwija powłoka agenta
    nuget_config | as_agent bash -c 'umask 022 && mkdir -p "$HOME/.nuget/NuGet" && cat > "$HOME/.nuget/NuGet/NuGet.Config"'
}

install_sdks() {
    local versions=() want work args listed
    read -r -a versions <<< "${TIDE_DOTNET_SDK:-}"
    (( ${#versions[@]} > 0 )) || die "brak dotnet.sdk w profilu (TIDE_DOTNET_SDK ustawia tide box build)"
    for want in "${versions[@]}"; do
        sdk_args "$want" > /dev/null
    done
    apt_install "${DOTNET_DEPS[@]}"
    work=$(mktemp -d)
    fetch "$DOTNET_INSTALL_URL" "$work/dotnet-install.sh"
    for want in "${versions[@]}"; do
        read -r -a args <<< "$(sdk_args "$want")"
        DOTNET_CLI_TELEMETRY_OPTOUT=1 bash "$work/dotnet-install.sh" "${args[@]}" --install-dir "$DOTNET_DIR" --no-path
    done
    rm -rf "$work"
    ln -sfn "$DOTNET_DIR/dotnet" /usr/local/bin/dotnet
    listed=$(DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 "$DOTNET_DIR/dotnet" --list-sdks)
    for want in "${versions[@]}"; do
        grep -q "^${want//./\\.} " <<< "$listed" || die "SDK $want nie jest zainstalowane: $listed"
    done
    echo "dotnet: $listed"
}

main() {
    stack_main
    install_sdks
    write_stack_env "" dotnet "DOTNET_ROOT=$DOTNET_DIR" DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 \
        DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
    write_nuget_config
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
