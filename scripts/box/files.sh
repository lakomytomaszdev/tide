#!/usr/bin/env bash
# Treść plików systemowych pudełka karty (PT-166) — jedno źródło dla install.sh, uninstall.sh i testów.
# Użycie: bash scripts/box/files.sh rules <adres IPv4 mostka> <port proxy>   reguły nftables (tabela inet tide-box)
#         bash scripts/box/files.sh unit                                    jednostka systemd wczytująca reguły
#         bash scripts/box/files.sh dropin                                  drop-in incus.service (Incus bez reguł nie wstaje)
#
# Granica sieciowa pudełka (decyzja „nftables statyczne, polityka w proxy”, docs/box-decisions.md): z mostka
# incusbr0 wolno tylko do adresu mostka na port proxy Tide (box.proxy-port) i DHCP; DNS, forward i reszta DROP.
# Usługa tide-daemon nigdy nie zmienia tych reguł — polityka per pudełko (domeny, Ollama hosta) żyje w proxy.
set -euo pipefail

BRIDGE=incusbr0
NFT_FILE=/etc/tide-box/tide-box.nft
UNIT=tide-box-nft.service

valid_ipv4() {
    local a b c d
    [[ $1 =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
    for a in "${BASH_REMATCH[@]:1}"; do
        (( 10#$a <= 255 )) || return 1
    done
}

valid_port() {
    [[ $1 =~ ^[0-9]{1,5}$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 ))
}

case "${1:-}" in
    rules)
        addr="${2:-}"
        port="${3:-}"
        if ! valid_ipv4 "$addr"; then
            echo "Niepoprawny adres IPv4 mostka: '$addr'" >&2
            exit 2
        fi
        if ! valid_port "$port"; then
            echo "Niepoprawny port proxy: '$port' (1–65535)" >&2
            exit 2
        fi
        # Łańcuchy bazowe mają politykę accept, bo hook input/forward widzi ruch wszystkich interfejsów hosta;
        # „polityką drop” dla ruchu z incusbr0 jest ostatnia reguła drop w każdym łańcuchu. Priorytet filter + 10
        # stawia te łańcuchy po łańcuchach tabeli incus (priorytet filter): accept Incusa (DNS i DHCP jego
        # dnsmasq, forward z NAT) kończy tylko łańcuch Incusa, a drop tutaj jest ostateczny.
        # Próby #1 (PT-164) wstawiają na chwilę własne accept z komentarzem tide-proto przed regułami drop
        # (nft insert rule …) i usuwają je po uchwytach; ponowne wczytanie pliku też je usuwa.
        cat <<EOF
#!/usr/sbin/nft -f
# Tide — pudełko karty: statyczne reguły dla mostka Incusa ($BRIDGE). Generuje scripts/box/files.sh,
# zakłada wyłącznie scripts/box/install.sh (sudo); nie edytuj ręcznie — zmiana przez make box-install.
# Z $BRIDGE wolno tylko: $addr:$port/tcp (proxy Tide) i DHCP; DNS (53), forward i cała reszta DROP.

table inet tide-box
delete table inet tide-box

table inet tide-box {
	chain input {
		type filter hook input priority filter + 10; policy accept;
		iifname "$BRIDGE" ip daddr $addr tcp dport $port accept comment "proxy Tide"
		iifname "$BRIDGE" udp sport 68 udp dport 67 accept comment "DHCP"
		iifname "$BRIDGE" meta l4proto { tcp, udp } th dport 53 drop comment "DNS tylko przez proxy"
		iifname "$BRIDGE" drop
	}

	chain forward {
		type filter hook forward priority filter + 10; policy accept;
		iifname "$BRIDGE" drop
		oifname "$BRIDGE" drop
	}
}
EOF
        ;;
    unit)
        # Jak nftables.service: przed network-pre.target, więc reguły są w jądrze, zanim wstanie sieć
        # i Incus. Nie używamy nftables.service ani /etc/nftables.conf — ten zaczyna od „flush ruleset”
        # i skasowałby reguły Dockera i Incusa.
        # ExecReload wczytuje plik ponownie (jedna transakcja) bez ruszania Incusa: reload nie propaguje
        # się przez Requires= z drop-inu, a stop i restart tak (gaszą Incusa razem z pudełkami).
        # Bez ExecStop: zatrzymanie jednostki nie zdejmuje reguł (tabelę usuwa jawnie uninstall.sh), więc
        # gdy incusd zostanie zabity po przekroczeniu czasu zatrzymania, a pudełka zostaną, filtr zostaje.
        cat <<EOF
# Tide — pudełko karty (scripts/box/install.sh, PT-166): statyczne reguły nftables dla mostka Incusa.
[Unit]
Description=Tide: reguły nftables pudełka karty (inet tide-box)
DefaultDependencies=no
Wants=network-pre.target
Before=network-pre.target incus.service incus-startup.service shutdown.target
Conflicts=shutdown.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f $NFT_FILE
ExecReload=/usr/sbin/nft -f $NFT_FILE

[Install]
WantedBy=sysinit.target
EOF
        ;;
    dropin)
        # Requires: gdy reguły nie wczytały się (albo ktoś zatrzymał jednostkę), Incus nie startuje
        # albo zatrzymuje się razem z nią — pudełko bez reguł nie dostaje sieci z NAT.
        cat <<EOF
# Tide — pudełko karty (scripts/box/install.sh, PT-166): Incus nie działa bez reguł inet tide-box.
[Unit]
Requires=$UNIT
After=$UNIT
EOF
        ;;
    *)
        echo "Użycie: $0 rules <adres mostka> <port proxy> | unit | dropin" >&2
        exit 2
        ;;
esac
