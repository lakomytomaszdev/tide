# Prototyp pudełka karty — wyniki prób (K-38)

Dowód, że kontener Incusa z `docs/card-box-design.md` (sekcje 4.1, 4.3, 4.8, 10) działa na tym komputerze, zanim
powstanie kod usługi. Próby P-1, P-4, P-5, P-9 i pomiary: PT-164; próby P-2, P-3, P-6, P-7 (proxy i agenci): PT-178
dopisuje wiersze do tej samej tabeli. Decyzje z prób: `docs/box-decisions.md` (nie `docs/decisions.md`).

Każda próba to skrypt `scripts/box/prototype/<nazwa>.sh` uruchamiany przez użytkownika poza piaskownicą kroku
(jedna próba = jedna runda `outside`). Skrypt zapisuje log `docs/box-prototype/<nazwa>.log`: każde polecenie, kod
wyjścia, czas i skrócone wyjście, na końcu wiersze `WYNIK <klucz>: ok|fail|skip|info` i podsumowanie. Skrypty
o nic nie pytają (poza hasłem sudo przy nftables), nie używają `security.privileged`, nie dotykają
`~/.local/share` ani `~/.config` i nie montują głównego worktree projektów.

## Wyniki

Stan: runda 1 (04.11.2026, 11:29–11:39, Incus 6.0.0 po `make box-install`) dała częściowe wyniki i ujawniła
błędy skryptów; runda 2 (12:20–12:26, `all.sh`, logi `docs/box-prototype/*.log`) rozstrzygnęła P-5 i P-1,
P-4 zatrzymało się na ograniczeniach projektu, a jego wariant `raw.idmap` zostawił kontener niestartujący, przez co
P-9 i pomiary nie ruszyły (błąd skryptu poprawiony). Kolumna „Wynik” to stan po rundzie 2.

| Próba | Co sprawdza | Polecenie | Wynik | Czas | Wariant awaryjny (gdy nie działa) |
| --- | --- | --- | --- | --- | --- |
| P-1 | Docker w nieuprzywilejowanym kontenerze (`security.nesting`, intercept `mknod`/`setxattr`): `docker run --rm hello-world`, `dotnet test` LegalApp z Testcontainers (`pgvector/pgvector:pg16`) | `bash scripts/box/prototype/p1.sh` | **nie działa.** Sieć przez chwilowe proxy działa (apt, `docker pull` z `registry-1.docker.io`), `docker.io` 29.1.3 z runc 1.3.4 instaluje się, `docker info`: overlayfs, apparmor, seccomp. `docker run --rm hello-world` kończy się kodem 126 — bez intercept i z chwilowym intercept tak samo: `OCI runtime create failed: runc create failed: unable to start container process: error during container init: open sysctl net.ipv4.ip_unprivileged_port_start file: reopen fd 8: permission denied`. `dotnet test` pominięty. Prawdopodobna przyczyna: nowy runc (poprawki bezpieczeństwa z 2025, ponowne otwieranie plików `/proc/sys` przez `/proc/thread-self/fd`) kontra profil AppArmor kontenera w Incusie 6.0.0 z Ubuntu — **hipoteza, niesprawdzona**. Runda 1 (reguły `tide-proto`): forward ginął, tabela przed/po identyczna | 4,4 s (pull + błąd) | Podman w pudełku z gniazdem Docker API (`podman system service`) albo `type: vm` dla projektów z Dockerem |
| P-2 | bwrap Claude Code i landlock Codexa w kontenerze | PT-178 | — | — | PT-178 |
| P-3 | OAuth agentów przez inny base URL (K3) | PT-178 | — | — | PT-178 |
| P-4 | `<proj>/.git` i `<proj>-worktrees` z ext4 `/home` przez `shift=true` pod identycznymi ścieżkami, nakładki ro na `.git/hooks`, `.git/config`, `.git/info`; `git status`, `git commit` na `proto/k-38`, `npm ci` (kontener i host) | `bash scripts/box/prototype/p4.sh` | **nie działa w projekcie ograniczonym.** `incus config device add … shift=true` odrzucone: „The "shift" property cannot be used with a restricted source path”. Wariant `raw.idmap "both 1000 1000"` przyjęty przez projekt (`restricted.idmap.uid/gid=1000` z PT-166), ale kontener nie wystartował (`incusd forkstart … exit status 1`) — najpewniej brak `root:1000:1` w `/etc/subuid` i `/etc/subgid` hosta (hipoteza). Worktree próby i gałąź `proto/k-38` usunięte; git, npm i widoczność hosta niesprawdzone | — | `raw.idmap "both 1000 1000"` bez `shift` (skrypt próbuje sam i zapisuje wariant) |
| P-5 | Projekt ograniczony `user-1000` (`incus-user`, grupa `incus`): nesting, intercept, filtrowanie NIC (`security.mac_filtering`, `security.ipv4_filtering`, `security.ipv6_filtering` na `eth0`), próba podszycia pod obcy adres | `bash scripts/box/prototype/p5.sh` | **Nesting działa** (projekt przyjął `security.nesting=true`, `unshare --user --map-root-user --pid --fork --mount-proc true` OK). **Intercept w projekcie ograniczonym nie działa**: „Container syscall interception is forbidden” (`restricted.containers.interception=block`, domyślne); z chwilowym `allow`: `mknod c 1 3` i `setxattr trusted.overlay.opaque` działają. **Filtrowanie NIC działa (mac + ipv4)**: kontrola A (własny adres `10.173.184.164`) i B (podszycie `10.173.184.250` bez filtrowania) doszły do `10.173.184.1:18080`; po `security.mac_filtering=true security.ipv4_filtering=true` własny adres dochodzi, podszycie nie (`nc` kod 1, brak wpisu w dzienniku serwera). `security.ipv6_filtering=true` — kontener nie startuje bez `br_netfilter`; użyto `false` (decyzja `filtrowanie-nic-bez-ipv6`) | 0,05 s (nc) | nesting/intercept: `--project-mode admin` w `scripts/box/install.sh` (grupa `incus-admin`, D9) z wpisem `tryb-admin` albo `restricted.containers.interception=allow` w projekcie (decyzja po rundzie 2); filtrowanie NIC: brak wariantu — warunek konieczny (`question`) |
| P-6 | Klienci (npm, NuGet, pip, Docker, agenci) przez proxy CONNECT i `NO_PROXY` | PT-178 | — | — | PT-178 |
| P-7 | Obrazy Incusa (bazowy Tide) | PT-178 | — | — | PT-178 |
| P-9 | `systemd --user` agenta (lingering), `systemd-run --user --scope` przez `incus exec`; proces przeżywa śmierć klienta `incus exec` i `systemctl --user restart tide-daemon` | `bash scripts/box/prototype/p9.sh` | Runda 1: **lingering i `systemd-run --user --scope` działają; proces w scope NIE przeżywa śmierci klienta `incus exec`** (`systemctl --user stop tide-proto-client` → `pgrep sleep` pusty). Restart `tide-daemon` nie zabił procesu, ale klient nie był dzieckiem usługi — test nie rozstrzyga. Runda 2 nie ruszyła (kontener zepsuty przez `raw.idmap` z p4); warianty usługi `--user --wait` i usługi roota z `--uid` czekają | 0,06 s | `systemd-run` jako root pudełka z `--uid=agent` (usługa zamiast scope; tokeny tylko w `in/env`); bliżej projektu: `systemd-run --user` jako usługa (bez `--scope`) |

### Wnioski z rundy 2

- **Projekt ograniczony z PT-166 w obecnym kształcie nie udźwignie pudełka**: blokuje intercept, migawki
  (`restricted.snapshots`) i `shift=true` na ścieżkach z `restricted.devices.disk.paths`. Pierwsze dwa to ustawienia
  projektu (`allow`), trzecie omija `raw.idmap` (projekt już ma `restricted.idmap.uid/gid=1000`), które wymaga
  `root:1000:1` w `/etc/subuid` i `/etc/subgid` — wszystko to zmiany w skryptach #3 albo na hoście, więc wymagają
  decyzji (pytanie w PT-164).
- **Docker w kontenerze pada niezależnie od projektu i intercept** na runc 1.3.4 (`open sysctl
  net.ipv4.ip_unprivileged_port_start … permission denied`). Do sprawdzenia: starszy runc w pudełku, Docker CE
  z repozytorium Dockera, nowszy Incus (profil AppArmor) albo Podman / `type: vm`.
- **Filtrowanie NIC (mac + ipv4) działa w projekcie ograniczonym** i odrzuca podszycie na hoście — polityka proxy
  per IP (#5) ma swój warunek konieczny.
- **Sieć przez proxy na porcie proxy Tide działa** dla apt i `docker pull` (cele w logu p1: `archive.ubuntu.com`,
  `security.ubuntu.com`, `registry-1.docker.io`, `auth.docker.io`, `production.cloudfront.docker.com`,
  `motd.ubuntu.com`) — materiał dla P-6 i listy domen szablonu docker (`production.cloudfront.docker.com`, nie
  `cloudflare` jak w sekcji 4.8 projektu).

### Wnioski z rundy 1 (dla kolejnych ticketów)

- **Docker hosta blokuje forward z `incusbr0`.** Reguły `accept` w `inet tide-box` nie wystarczą, gdy inny łańcuch
  `hook forward` (najpewniej `ip filter FORWARD` Dockera CE z polityką DROP) odrzuca pakiet — w nftables drop
  w dowolnym łańcuchu bazowym jest ostateczny. Docelowa architektura (pudełko → tylko proxy na hoście, bez forwardu)
  tego nie potrzebuje; dlatego runda 2 prowadzi sieć prób przez chwilowe proxy na porcie proxy Tide, bez zmian
  w zaporze. Tryb reguł (`PROTO_NET=rules`) zostaje i loguje łańcuch `ip filter FORWARD` do diagnozy.
- **Projekt ograniczony z PT-166 blokuje intercept i migawki** (`restricted.containers.interception=block`,
  `restricted.snapshots=block` — wartości domyślne Incusa). Reset z migawki (US-4) wymaga `restricted.snapshots=allow`;
  Docker być może działa bez intercept (jądro 7.0 pozwala na overlayfs i whiteout w przestrzeni użytkownika) —
  rozstrzyga runda 2 (p1 próbuje najpierw bez intercept).
- **`incus-user` dopisał własny mostek do projektu:** `restricted.networks.access: incusbr0,incusbr-1000`, choć
  instalacja ustawiła tylko `incusbr0`. Mostek `incusbr-1000` nie jest objęty tabelą `inet tide-box` — do sprawdzenia
  w PT-166 (p5 loguje `incus network list`).
- **`security.ipv6_filtering` wymaga modułu `br_netfilter`** (Incus 6.0); załadowanie zmienia filtrowanie wszystkich
  mostków hosta (także Dockera), więc próby go nie ładują. Mostek nie ma IPv6, a `inet tide-box` odrzuca cały ruch
  z `incusbr0` poza proxy i DHCP (IPv4) — runda 2 sprawdza mac + ipv4.
- **Proces w `systemd-run --user --scope` ginie razem z klientem `incus exec`** — scope zmienia tylko grupę cgroup,
  a proces jest wykonany przez `incus exec`. BoxRunner (#6) nie może opierać przeżycia restartu usługi na scope.

## Pomiary (US-4)

`bash scripts/box/prototype/measure.sh` — trzy powtórzenia, mediana; przekroczenie progu opisane z przyczyną
(nie blokuje PT-164, blokuje dopiero #24).

| Pomiar | Próg | Pomiary | Mediana | W progu |
| --- | --- | --- | --- | --- |
| Narzut kroku: `incus exec … --user 1000 -- systemd-run --user --scope true` | ≤ 3 s | 0,055 / 0,061 / 0,061 s | 0,061 s | tak |
| Narzut kroku (usługa): `incus exec … -- systemd-run --user --wait true` | ≤ 3 s | runda 2 | — | — |
| Zimny start zatrzymanego kontenera (`incus start` do końca startu systemd) | ≤ 30 s | runda 1: błąd skryptu (brak ponawiania); runda 2: kontener nie startował (raw.idmap z p4); orientacyjnie z logów p1/p5: `incus start` 0,2 s + systemd 0,6–2,4 s | — | — |
| Reset: `incus snapshot restore` (do końca startu systemd) | ≤ 60 s | „Project user-1000 doesn't allow for snapshot creation” (obie rundy); w rundzie 2 z chwilowym `restricted.snapshots=allow` migawka powstała (0,10 s), ale `restore` padł: „Instance is busy running a "stop" operation” (kontener zepsuty przez `raw.idmap` z p4) | — | — |

## Co widać z kontenera (P-4)

| Sprawdzenie | Polecenie | Wynik |
| --- | --- | --- |
| `~/.ssh` | `incus exec tide-proto -T -- test -e /home/<użytkownik>/.ssh` | czeka |
| `~/.git-credentials` | `incus exec tide-proto -T -- test -e /home/<użytkownik>/.git-credentials` | czeka |
| główny worktree projektu | `incus exec tide-proto -T -- ls -A ~/DevWorkspace/<proj>` (tylko `.git`) | czeka |
| gniazdo Dockera hosta | `incus config show tide-proto --expanded` (brak `docker.sock`) | czeka |
| Postgres hosta | `incus exec tide-proto -T -- timeout 10 nc -z -w 3 <ip hosta> 5432` (każdy adres hosta) | czeka |

## Jak działają próby

**Kontener.** `tide-proto` z `images:ubuntu/24.04` w projekcie ograniczonym z PT-166 (`incus` użytkownika z grupy
`incus` trafia przez `incus-user` do projektu `user-<uid>`), `security.nesting=true`,
`security.syscalls.intercept.mknod=true`, `security.syscalls.intercept.setxattr=true`. Gdy projekt odrzuci
intercept, skrypt tworzy kontener z samym nestingiem i zapisuje to w logu (ocenia P-5). Użytkownik `agent` ma uid 1000
(jak na hoście). Kontener zostaje na hoście tylko przy wyniku `done`; inaczej `bash scripts/box/prototype/cleanup.sh`.

**Sieć dla prób (P-1, P-4, pakiety w P-5) — od rundy 2 domyślnie przez chwilowe proxy.** Tabela `inet tide-box`
z PT-166 to statyczny default-deny, który przepuszcza z mostka tylko port proxy Tide. Proxy z PT-178 jeszcze nie
działa, a forward z reguł chwilowych w rundzie 1 ginął w łańcuchu Dockera hosta (wnioski wyżej), więc `lib.sh`
(`net_open`/`net_close`) uruchamia na czas próby `scripts/box/prototype/proxy.py` (CONNECT i zwykłe HTTP, bez MITM,
dziennik celów) na `<adres mostka>:<port proxy Tide>` i podaje kontenerowi `HTTP(S)_PROXY` (polecenia
`incus exec`), `Acquire::http(s)::Proxy` (apt) i drop-in `dockerd` — dokładnie jak w docelowej architekturze
(sekcja 4.5). Bez sudo i bez żadnej zmiany w nftables; lista celów trafia do logu (materiał dla P-6).

Tryb `PROTO_NET=rules` (runda 1) wstawia z sudo dokładnie trzy chwilowe reguły z komentarzem `tide-proto`
**do tabeli `inet tide-box`** (`nft insert`, czyli przed regułami drop):

```
nft insert rule inet tide-box forward iifname "incusbr0" ip saddr <IP kontenera> accept comment "tide-proto"
nft insert rule inet tide-box forward oifname "incusbr0" ip daddr <IP kontenera> ct state established,related accept comment "tide-proto"
nft insert rule inet tide-box input iifname "incusbr0" ip saddr <IP kontenera> meta l4proto { tcp, udp } th dport 53 accept comment "tide-proto"
```

i usuwają je po uchwytach (`nft -a list table inet tide-box` → wiersze z `comment "tide-proto"` →
`nft delete rule inet tide-box <łańcuch> handle <n>`) w pułapce `EXIT` — także po błędzie i po sygnale. NAT robi
tabela `incus` mostka. Log zawiera `nft list table inet tide-box` przed i po oraz wynik `nft-przed-po` (identyczne,
brak `tide-proto`).

**P-4.** Worktree próby `<proj>-worktrees/proto-k-38` na nowej gałęzi `proto/k-38` (domyślnie projekt
`~/DevWorkspace/toolssuite`, zmienna `PROTO_NODE_PROJECT`) powstaje na hoście i jest usuwany z gałęzią na końcu.
Montowania (`incus config device add tide-proto <nazwa> disk source=<ścieżka> path=<ta sama ścieżka> shift=true`):
`.git` i `<proj>-worktrees` rw, `.git/hooks`, `.git/config`, `.git/info` ro. Zapis do nakładek ro jest próbowany
bez zmiany treści (`mkdir` katalogu próby, `: >> config`). `npm ci --ignore-scripts` na hoście, potem w kontenerze
z zimnym i ciepłym cache, z tą samą wersją node co host.

**P-5.** Na hoście słucha mały serwer TCP na adresie mostka i porcie proxy Tide (jedyny port przepuszczany przez
`inet tide-box`) i zapisuje adres źródłowy połączeń. Kontrola A: `nc` z własnego adresu kontenera; kontrola B (bez
filtrowania): `ip addr add <x.y.z.250>/24 dev eth0` i `nc -s <x.y.z.250> -z <adres mostka> <port>` — musi dojść, inaczej
test jest nieważny; potem `incus config device override tide-proto eth0 security.mac_filtering=true
security.ipv4_filtering=true security.ipv6_filtering=true`, restart i to samo podszycie — nie może dojść.
Gdy start z `security.ipv6_filtering` padnie (brak `br_netfilter`), próba idzie z `security.ipv6_filtering=false`
(mac + ipv4) i zapisuje to w logu. Gdy projekt odrzuci intercept, skrypt sprawdza chwilowo
`restricted.containers.interception=allow` (sudo, cofane). Pomiary z tego samego powodu chwilowo ustawiają
`restricted.snapshots=allow` (sudo, cofane po usunięciu migawki).

**P-9.** Klient `incus exec` biegnie na hoście w jednostce `tide-proto-client` (jak dziecko `tide-daemon`);
`systemctl --user stop tide-proto-client` emuluje śmierć usługi; po każdym `incus exec tide-proto -T -- pgrep -u 1000
-x sleep`. Trzy warianty w kontenerze: `systemd-run --user --scope sleep 30`, `systemd-run --user --unit=… --collect
--wait sleep 30` (usługa użytkownika agent) i `systemd-run --uid=1000 … --wait sleep 30` (usługa roota pudełka).
Potem `systemctl --user restart tide-daemon` z wariantem scope (kryterium ticketu).

## Kolejność uruchomienia

Wszystko naraz: `bash scripts/box/prototype/all.sh` (każda próba pisze własny log; niepowodzenie jednej nie
zatrzymuje następnych). Pojedynczo:

1. `bash scripts/box/prototype/p5.sh` — projekt, nesting, intercept, filtrowanie NIC (tworzy kontener).
2. `bash scripts/box/prototype/p1.sh` — Docker i LegalApp.
3. `bash scripts/box/prototype/p4.sh` — montowania, git, npm, widoczność hosta.
4. `bash scripts/box/prototype/p9.sh` — systemd --user i przeżycie restartu.
5. `bash scripts/box/prototype/measure.sh` — pomiary.
