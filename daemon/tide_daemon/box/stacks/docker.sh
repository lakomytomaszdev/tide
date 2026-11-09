#!/bin/bash
# Szablon stosu docker (PT-192, #29) — JEDYNY właściciel drop-inu dockerd w repozytorium. Obraz bazowy (#16) nie
# dotyka Dockera, a obrazy (`docker pull`) ściąga builder (#4/#8) po szablonach, nie ten skrypt.
#
# Co robi: `docker.io` z Ubuntu, runc 1.2.6 (statyczny z wydań GitHub, przypięta suma SHA-256) jako domyślny runtime
# (decyzja runc-1-2-6-w-pudelku: runc 1.3.x z apt i crun nie działają pod profilem AppArmor kontenera Incusa 6.0),
# drop-in /etc/systemd/system/docker.service.d/proxy.conf z HTTP(S)_PROXY na proxy Tide i NO_PROXY = BOX_NO_PROXY
# (wartości wstrzykuje builder — żadna nie jest tu wpisana), `agent` w grupie `docker`. DOCKER_HOST nie jest
# ustawiany: klienci używają gniazda Dockera pudełka.
set -euo pipefail

# shellcheck source=lib.sh
source "${BASH_SOURCE[0]%/*}/lib.sh"

DOCKER_PACKAGES=(docker.io)
RUNC_VERSION=1.2.6
RUNC_SHA256_AMD64=0774f49d1b1eebb5849e644db5e4dc6f2b06cee05f13b3d17d5d6ba62d6f2ebc
RUNC_URL=https://github.com/opencontainers/runc/releases/download/v$RUNC_VERSION/runc.amd64
RUNC_BIN=/usr/local/bin/runc-$RUNC_VERSION
RUNC_RUNTIME=runc-126
DROPIN=/etc/systemd/system/docker.service.d/proxy.conf
DAEMON_JSON=/etc/docker/daemon.json

write_dockerd_proxy() {  # write_dockerd_proxy <root> — drop-in dockerd: proxy Tide i pełne NO_PROXY
    local root=$1 key
    [[ -n ${TIDE_BOX_PROXY:-} && -n ${TIDE_BOX_NO_PROXY:-} ]] || die "drop-in dockerd bez proxy albo z pustym NO_PROXY"
    mkdir -p "$root${DROPIN%/*}"
    {
        echo "# Tide (PT-192, szablon docker): dockerd tylko przez proxy Tide; NO_PROXY = box/netenv.py BOX_NO_PROXY."
        echo "[Service]"
        for key in HTTPS_PROXY HTTP_PROXY https_proxy http_proxy; do
            printf 'Environment="%s=%s"\n' "$key" "$TIDE_BOX_PROXY"
        done
        for key in NO_PROXY no_proxy; do
            printf 'Environment="%s=%s"\n' "$key" "$TIDE_BOX_NO_PROXY"
        done
    } > "$root$DROPIN"
    chmod 0644 "$root$DROPIN"
}

write_daemon_json() {  # write_daemon_json <root> — runc 1.2.6 jako domyślny runtime
    local root=$1
    mkdir -p "$root${DAEMON_JSON%/*}"
    printf '{"default-runtime": "%s", "runtimes": {"%s": {"path": "%s"}}}\n' \
        "$RUNC_RUNTIME" "$RUNC_RUNTIME" "$RUNC_BIN" > "$root$DAEMON_JSON"
    chmod 0644 "$root$DAEMON_JSON"
}

install_runc() {
    [[ $(dpkg --print-architecture) == amd64 ]] \
        || die "runc $RUNC_VERSION ma przypiętą sumę tylko dla amd64 (jest $(dpkg --print-architecture))"
    local work
    work=$(mktemp -d)
    fetch "$RUNC_URL" "$work/runc"
    check_sha256 "$RUNC_SHA256_AMD64" "$work/runc"
    install -m 0755 -o root -g root "$work/runc" "$RUNC_BIN"
    rm -rf "$work"
    "$RUNC_BIN" --version
}

check_dockerd() {
    local env runtime
    env=$(systemctl show docker -p Environment)
    [[ $env == *"HTTPS_PROXY=$TIDE_BOX_PROXY"* && $env == *"NO_PROXY=$TIDE_BOX_NO_PROXY"* ]] \
        || die "dockerd bez proxy Tide albo bez pełnego NO_PROXY: $env"
    [[ $env != *DOCKER_HOST* ]] || die "dockerd ma DOCKER_HOST w środowisku: $env"
    runtime=$(docker info --format '{{.DefaultRuntime}}')
    [[ $runtime == "$RUNC_RUNTIME" ]] || die "domyślny runtime dockerd: oczekiwano $RUNC_RUNTIME, jest $runtime"
    as_agent docker info --format 'docker {{.ServerVersion}} jako agent: runtime {{.DefaultRuntime}}'
}

main() {
    stack_main
    apt_install "${DOCKER_PACKAGES[@]}"
    install_runc
    write_daemon_json ""
    write_dockerd_proxy ""
    usermod -aG docker "$AGENT"
    systemctl daemon-reload
    systemctl reset-failed docker.service docker.socket 2>/dev/null || true
    systemctl enable docker.service
    systemctl restart docker.service
    check_dockerd
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
