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

Stan: skrypty gotowe, próby czekają na uruchomienie (kolejność niżej). Wiersze uzupełnia PT-164 po każdej rundzie.

| Próba | Co sprawdza | Polecenie | Wynik | Czas | Wariant awaryjny (gdy nie działa) |
| --- | --- | --- | --- | --- | --- |
| P-1 | Docker w nieuprzywilejowanym kontenerze (`security.nesting`, intercept `mknod`/`setxattr`): `docker run --rm hello-world`, `dotnet test` LegalApp z Testcontainers (`pgvector/pgvector:pg16`) | `bash scripts/box/prototype/p1.sh` | czeka | — | Podman w pudełku z gniazdem Docker API (`podman system service`) albo `type: vm` dla projektów z Dockerem |
| P-2 | bwrap Claude Code i landlock Codexa w kontenerze | PT-178 | — | — | PT-178 |
| P-3 | OAuth agentów przez inny base URL (K3) | PT-178 | — | — | PT-178 |
| P-4 | `<proj>/.git` i `<proj>-worktrees` z ext4 `/home` przez `shift=true` pod identycznymi ścieżkami, nakładki ro na `.git/hooks`, `.git/config`, `.git/info`; `git status`, `git commit` na `proto/k-38`, `npm ci` (kontener i host) | `bash scripts/box/prototype/p4.sh` | czeka | — | `raw.idmap "both 1000 1000"` bez `shift` (skrypt próbuje sam i zapisuje wariant) |
| P-5 | Projekt ograniczony `user-1000` (`incus-user`, grupa `incus`): nesting, intercept, filtrowanie NIC (`security.mac_filtering`, `security.ipv4_filtering`, `security.ipv6_filtering` na `eth0`), próba podszycia pod obcy adres | `bash scripts/box/prototype/p5.sh` | czeka | — | nesting/intercept: `--project-mode admin` w `scripts/box/install.sh` (grupa `incus-admin`, D9) z wpisem `tryb-admin`; filtrowanie NIC: brak wariantu — warunek konieczny (`question`) |
| P-6 | Klienci (npm, NuGet, pip, Docker, agenci) przez proxy CONNECT i `NO_PROXY` | PT-178 | — | — | PT-178 |
| P-7 | Obrazy Incusa (bazowy Tide) | PT-178 | — | — | PT-178 |
| P-9 | `systemd --user` agenta (lingering), `systemd-run --user --scope` przez `incus exec`; proces przeżywa śmierć klienta `incus exec` i `systemctl --user restart tide-daemon` | `bash scripts/box/prototype/p9.sh` | czeka | — | `systemd-run` jako root pudełka z `--uid=agent` (usługa zamiast scope; tokeny tylko w `in/env`) |

## Pomiary (US-4)

`bash scripts/box/prototype/measure.sh` — trzy powtórzenia, mediana; przekroczenie progu opisane z przyczyną
(nie blokuje PT-164, blokuje dopiero #24).

| Pomiar | Próg | Pomiary | Mediana | W progu |
| --- | --- | --- | --- | --- |
| Narzut kroku: `incus exec … --user 1000 -- systemd-run --user --scope true` | ≤ 3 s | — | — | — |
| Zimny start zatrzymanego kontenera (`incus start` do końca startu systemd) | ≤ 30 s | — | — | — |
| Reset: `incus snapshot restore` (do końca startu systemd) | ≤ 60 s | — | — | — |

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

**Sieć dla prób (P-1, P-4, pakiety w P-5).** Tabela `inet tide-box` z PT-166 to statyczny default-deny. Proxy z PT-178
jeszcze nie działa, więc skrypty (`lib.sh`: `net_open`/`net_close`) wstawiają z sudo dokładnie trzy chwilowe
reguły z komentarzem `tide-proto` **do tabeli `inet tide-box`** (`nft insert`, czyli przed regułami drop):

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
Gdy projekt odrzuci intercept, skrypt sprawdza chwilowo `restricted.containers.interception=allow` (sudo, cofane).

**P-9.** Klient `incus exec … --user 1000 -- systemd-run --user --scope sleep 30` biegnie na hoście w jednostce
`tide-proto-client` (jak dziecko `tide-daemon`); `systemctl --user stop tide-proto-client` emuluje śmierć usługi,
potem osobno `systemctl --user restart tide-daemon`; po każdym `incus exec tide-proto -T -- pgrep -u 1000 -x sleep`.

## Kolejność uruchomienia

1. `bash scripts/box/prototype/p5.sh` — projekt, nesting, intercept, filtrowanie NIC (tworzy kontener).
2. `bash scripts/box/prototype/p1.sh` — Docker i LegalApp.
3. `bash scripts/box/prototype/p4.sh` — montowania, git, npm, widoczność hosta.
4. `bash scripts/box/prototype/p9.sh` — systemd --user i przeżycie restartu.
5. `bash scripts/box/prototype/measure.sh` — pomiary.
