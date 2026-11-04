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

### Stan końcowy (runda 6, 04.11.2026)

| Próba | Wynik | Jak (warianty i decyzje w `docs/box-decisions.md`) |
| --- | --- | --- |
| P-1 | **działa** | Docker 29.1.3 w nieuprzywilejowanym kontenerze z `security.nesting=true`, bez intercept, z **runc 1.2.6** jako domyślnym runtime (`runc-1-2-6-w-pudelku`); `docker run --rm hello-world` OK; `dotnet test` LegalApp: Testcontainers uruchamia `pgvector/pgvector:pg16` i Ryuk, testy jednostkowe 5124/5137 (13 pominiętych), integracyjne 1186/1238 — 51 porażek z konfiguracji testów projektu, nie z pudełka (niżej) |
| P-4 | **działa** | `raw.idmap "both 1000 1000"` zamiast `shift=true` (`raw-idmap-zamiast-shift`), montowania dodawane przy zatrzymanym pudełku; git status/commit, npm ci, nakładki ro, widoczność hosta — runda 4 |
| P-5 | **działa** | nesting tak; intercept tylko po zmianie projektu (niepotrzebny); filtrowanie NIC mac + ipv4 odrzuca podszycie (`filtrowanie-nic-bez-ipv6`) — runda 4 |
| P-9 | **działa** | krok jako usługa `systemd-run --user … --wait` (`krok-jako-usluga-user`): przeżywa śmierć klienta `incus exec` i restart `tide-daemon`; scope przeżywa restart `tide-daemon`, ale nie śmierć klienta — runda 4 |
| Pomiary | **w progach** | narzut 0,066 s, start 2,3 s, reset 3,9 s (reset wymaga `restricted.snapshots=allow`, `migawki-w-projekcie`) — runda 4 |

**`dotnet test` LegalApp w pudełku — 51 porażek integracyjnych, żadna z Dockera ani Testcontainers**
(`docs/box-prototype/p1-dotnet-test.log`): 36× `Connection string is not configured. Set DATABASE_CONNECTION env var
or ConnectionStrings:DefaultConnection in appsettings` (część testów czyta połączenie ze zmiennej środowiska, której
pudełko nie ma — na hoście ustawia ją użytkownik), 4× brak plików `EvalSet/Cases/*/expected-classifier-acts.json`
(nie ma ich w `git archive HEAD` — nieśledzone albo generowane), 11× asercje aplikacji (teksty lokalizacji,
HTTP 405 zamiast 404, liczba uruchomień harmonogramu, kolejność wyników wyszukiwania). Wyjaśnienie i profil
środowiska LegalApp (zmienne, pliki, kultura) należą do PT-184; porównania z wynikiem na hoście nie robiono.

Czasy: instalacja .NET SDK 10.0 w pudełku 26,8 s, `dotnet test` (restore, build, testy) 1183 s; cele przez proxy:
`api.nuget.org`, `builds.dotnet.microsoft.com`, `dot.net`, `aka.ms`, rejestr Dockera, serwery CRL (`crl3/crl4.digicert.com`,
`crl.sectigo.com`, `ts-crl.ws.symantec.com`, `s.symcb.com`, `www.microsoft.com`) — materiał dla listy domen (P-6, #4).

### Historia rund

Stan: runda 1 (04.11.2026, 11:29–11:39, Incus 6.0.0 po `make box-install`) dała częściowe wyniki i ujawniła
błędy skryptów; runda 2 (12:20–12:26, `all.sh`, logi `docs/box-prototype/*.log`) rozstrzygnęła P-5 i P-1,
P-4 zatrzymało się na ograniczeniach projektu, a jego wariant `raw.idmap` zostawił kontener niestartujący, przez co
P-9 i pomiary nie ruszyły (błąd skryptu poprawiony). Kolumna „Wynik” to stan po rundzie 2.

Runda 3 (decyzje `projekt-ograniczony-zostaje`, `raw-idmap-zamiast-shift` w `docs/box-decisions.md`):
`bash scripts/box/prototype/all.sh --install` — najpierw `sudo bash scripts/box/install.sh --yes` (dopisuje
`root:<uid>:1` do `/etc/subuid` i `/etc/subgid`, restart `incus`, przywraca `restricted.networks.access=incusbr0`),
potem próby; P-1 sprawdza po kolei runtime'y runc, crun i najstarszy runc z Ubuntu 24.04. Jeśli Docker
w kontenerze nie ruszy z żadnym, rekomendacja to `type: vm` dla projektów z Dockerem (LegalApp, toolssuite),
bez Podmana.

Wynik rundy 3 (13:56–13:59, logi `docs/box-prototype/*.log`, `install.log`): instalacja dopisała `root:1000:1`
i przywróciła `restricted.networks.access=incusbr0`; **P-9 działa w wariancie usługi `--user`**, **pomiary
w progach**, P-5 działa (po poprawce odczytu adresu), P-4 doszło do montowań (pojedynczy plik nie dołącza się do
działającego kontenera — poprawione), P-1 nadal nie działa (runc, crun z Ubuntu, runc 1.1). Runda 4:
`bash scripts/box/prototype/all.sh` (bez `--install`) — P-4 z montowaniami przy zatrzymanym kontenerze, P-1
z najnowszym crun i runc 1.2.6 z wydań GitHub. Na hoście jest też kontener `tide-proto-net` spoza skryptów
(nie ruszamy go; `cleanup.sh` usuwa tylko `tide-proto`).

Wynik rundy 4 (14:45–14:48): **P-4, P-5, P-9 i pomiary działają**; P-1 nierozstrzygnięte — oba nowe runtime'y
(crun 1.30.1, runc 1.2.6) nie zostały sprawdzone, bo dockerd nie wstał po trzecim restarcie w minucie (limit
startów jednostki, błąd skryptu — poprawiony). Runda 5: tylko `bash scripts/box/prototype/p1.sh`.

| Próba | Co sprawdza | Polecenie | Wynik | Czas | Wariant awaryjny (gdy nie działa) |
| --- | --- | --- | --- | --- | --- |
| P-1 | Docker w nieuprzywilejowanym kontenerze (`security.nesting`, intercept `mknod`/`setxattr`): `docker run --rm hello-world`, `dotnet test` LegalApp z Testcontainers (`pgvector/pgvector:pg16`) | `bash scripts/box/prototype/p1.sh` | **Runda 6: działa.** runc 1.2.6: hello-world OK; `dotnet test` LegalApp (SDK 10.0 z `TargetFramework`): Testcontainers z `pgvector/pgvector:pg16` i Ryuk działa, jednostkowe 5124 OK / 0 błędów, integracyjne 1186 OK / 51 błędów konfiguracji testów (stan końcowy wyżej), kod 1 | SDK 26,8 s, dotnet test 1183 s | — |
| P-1 (historia) | jw. | jw. | **Runda 5: `docker run --rm hello-world` działa z runc 1.2.6** (statyczny z wydań GitHub, SHA-256 `0774f49d…2ebc`, domyślny runtime dockerd), bez intercept, w projekcie ograniczonym. runc 1.3.4 z apt: kod 126 (`open sysctl net.ipv4.ip_unprivileged_port_start … reopen fd 8: permission denied`); crun 1.30.1: kod 125 (`open /proc/sys/net/ipv4/ip_unprivileged_port_start: Permission denied`) — oba nowe runtime'y otwierają `/proc/sys` w sposób, którego profil AppArmor kontenera Incusa 6.0.0 nie przepuszcza. `dotnet test` LegalApp nie ruszył: skrypt padł (kod 128) przy odczycie `global.json`, którego w repozytorium nie ma — błąd skryptu, poprawiony; runda 6: znowu `bash scripts/box/prototype/p1.sh`. **Runda 4: nierozstrzygnięte.** runc 1.3.4 z apt: ten sam błąd AppArmor/procfs (kod 126). crun 1.30.1 i runc 1.2.6 z wydań GitHub pobrały się i działają (`--version`, sumy SHA-256 w logu), ale dockerd nie wstał po trzecim restarcie w minucie („Job for docker.service failed”) — najpewniej limit startów jednostki (`StartLimitBurst`), błąd skryptu, nie runtime'u; ten sam błąd mógł zafałszować runc 1.1 z rundy 3. Poprawione (`systemctl reset-failed` przed restartem, dziennik jednostki w logu); runda 5: tylko `bash scripts/box/prototype/p1.sh`. **Runda 3: nie działa.** runc 1.3.4 z apt: ten sam błąd `open sysctl net.ipv4.ip_unprivileged_port_start … reopen fd 8: permission denied` (kod 126); crun 1.14 z Ubuntu 24.04: `OCI runtime create failed: unknown version specified` (za stary na specyfikację OCI Dockera 29); runc 1.1.12 z apt (`--allow-downgrades`): dockerd nie wstaje (`See "systemctl status docker.service"`). Runda 4 (skrypt gotowy): najnowszy crun i runc 1.2.6 z wydań GitHub (statyczne). Rekomendacja na wypadek dalszej porażki: `type: vm` (decyzja `docker-w-vm`). **Runda 2: nie działa.** Sieć przez chwilowe proxy działa (apt, `docker pull` z `registry-1.docker.io`), `docker.io` 29.1.3 z runc 1.3.4 instaluje się, `docker info`: overlayfs, apparmor, seccomp. `docker run --rm hello-world` kończy się kodem 126 — bez intercept i z chwilowym intercept tak samo: `OCI runtime create failed: runc create failed: unable to start container process: error during container init: open sysctl net.ipv4.ip_unprivileged_port_start file: reopen fd 8: permission denied`. `dotnet test` pominięty. Prawdopodobna przyczyna: nowy runc (poprawki bezpieczeństwa z 2025, ponowne otwieranie plików `/proc/sys` przez `/proc/thread-self/fd`) kontra profil AppArmor kontenera w Incusie 6.0.0 z Ubuntu — **hipoteza, niesprawdzona**. Runda 1 (reguły `tide-proto`): forward ginął, tabela przed/po identyczna | 4,4 s (pull + błąd) | Podman w pudełku z gniazdem Docker API (`podman system service`) albo `type: vm` dla projektów z Dockerem |
| P-2 | bwrap Claude Code i landlock Codexa w kontenerze | PT-178 | — | — | PT-178 |
| P-3 | OAuth agentów przez inny base URL (K3) | PT-178 | — | — | PT-178 |
| P-4 | `<proj>/.git` i `<proj>-worktrees` z ext4 `/home` przez `shift=true` pod identycznymi ścieżkami, nakładki ro na `.git/hooks`, `.git/config`, `.git/info`; `git status`, `git commit` na `proto/k-38`, `npm ci` (kontener i host) | `bash scripts/box/prototype/p4.sh` | **Runda 4: działa (wariant `raw.idmap`).** `shift=true` odrzucony przez projekt ograniczony; z `raw.idmap "both 1000 1000"` (decyzja `raw-idmap-zamiast-shift`) i montowaniami dodanymi przy zatrzymanym kontenerze: uid 1000 ↔ 1000 pod identycznymi ścieżkami, `git status` i `git commit` na `proto/k-38` z kontenera (commit widoczny na hoście, plik należy do `tomek`), zapis do `.git/hooks`, `.git/config`, `.git/info` odrzucony („Read-only file system”), `npm ci --ignore-scripts` w toolssuite: host 8,6 s, kontener 11,6 s (zimny cache) i 10,8 s (ciepły). Worktree i gałąź próby usunięte | npm ci: 8,6 / 11,6 / 10,8 s | — |
| P-4 (historia) | jw. | jw. | **Runda 3: częściowo.** Po `make box-install` z `root:1000:1` kontener z `raw.idmap "both 1000 1000"` startuje; `.git`, `<proj>-worktrees` i `.git/hooks` (ro) dołączają się do działającego kontenera, ale pojedynczy plik `.git/config` (ro) nie: `Failed to add mount for device inside container: … incusd forkmount lxc-mount … exit status 1`. Runda 4: montowania dodawane przy zatrzymanym kontenerze (Incus zakłada je przy starcie). Git, npm i widoczność hosta jeszcze niesprawdzone. **Runda 2: nie działa w projekcie ograniczonym.** `incus config device add … shift=true` odrzucone: „The "shift" property cannot be used with a restricted source path”. Wariant `raw.idmap "both 1000 1000"` przyjęty przez projekt (`restricted.idmap.uid/gid=1000` z PT-166), ale kontener nie wystartował (`incusd forkstart … exit status 1`) — najpewniej brak `root:1000:1` w `/etc/subuid` i `/etc/subgid` hosta (hipoteza). Worktree próby i gałąź `proto/k-38` usunięte; git, npm i widoczność hosta niesprawdzone | — | `raw.idmap "both 1000 1000"` bez `shift` (skrypt próbuje sam i zapisuje wariant) |
| P-5 | Projekt ograniczony `user-1000` (`incus-user`, grupa `incus`): nesting, intercept, filtrowanie NIC (`security.mac_filtering`, `security.ipv4_filtering`, `security.ipv6_filtering` na `eth0`), próba podszycia pod obcy adres | `bash scripts/box/prototype/p5.sh` | **Runda 4: działa.** Nesting w projekcie ograniczonym działa; intercept tylko z `restricted.containers.interception=allow` (domyślnie zablokowany; Docker go nie potrzebuje — w P-1 nic nie zmienił). Filtrowanie NIC (mac + ipv4, decyzja `filtrowanie-nic-bez-ipv6`): własny adres dochodzi do `10.173.184.1:18080`, podszycie pod `10.173.184.250` nie (kontrola bez filtrowania: dochodzi). **Runda 3: działa (po ręcznej analizie logu).** Skrypt zgłosił „nie dochodzi nawet własny adres”, bo `incus list tide-proto` dopasował też drugi kontener `tide-proto-net` (10.173.184.91) i porównywał zły adres; serwer testowy zapisał `10.173.184.164` (tide-proto) przed i po włączeniu filtrowania, a `10.173.184.250` tylko przed — podszycie z filtrowaniem (mac + ipv4) nie doszło (`nc` kod 1). Błąd skryptu poprawiony (`box_ip` po dokładnej nazwie). **Runda 2:** **Nesting działa** (projekt przyjął `security.nesting=true`, `unshare --user --map-root-user --pid --fork --mount-proc true` OK). **Intercept w projekcie ograniczonym nie działa**: „Container syscall interception is forbidden” (`restricted.containers.interception=block`, domyślne); z chwilowym `allow`: `mknod c 1 3` i `setxattr trusted.overlay.opaque` działają. **Filtrowanie NIC działa (mac + ipv4)**: kontrola A (własny adres `10.173.184.164`) i B (podszycie `10.173.184.250` bez filtrowania) doszły do `10.173.184.1:18080`; po `security.mac_filtering=true security.ipv4_filtering=true` własny adres dochodzi, podszycie nie (`nc` kod 1, brak wpisu w dzienniku serwera). `security.ipv6_filtering=true` — kontener nie startuje bez `br_netfilter`; użyto `false` (decyzja `filtrowanie-nic-bez-ipv6`) | 0,05 s (nc) | nesting/intercept: `--project-mode admin` w `scripts/box/install.sh` (grupa `incus-admin`, D9) z wpisem `tryb-admin` albo `restricted.containers.interception=allow` w projekcie (decyzja po rundzie 2); filtrowanie NIC: brak wariantu — warunek konieczny (`question`) |
| P-6 | Klienci (npm, NuGet, pip, Docker, agenci) przez proxy CONNECT i `NO_PROXY` | PT-178 | — | — | PT-178 |
| P-7 | Obrazy Incusa (bazowy Tide) | PT-178 | — | — | PT-178 |
| P-9 | `systemd --user` agenta (lingering), `systemd-run --user --scope` przez `incus exec`; proces przeżywa śmierć klienta `incus exec` i `systemctl --user restart tide-daemon` | `bash scripts/box/prototype/p9.sh` | **Runda 3: działa w wariancie usługi.** Lingering, `systemd-run --user --scope true` i `systemd-run --user --wait true` przez `incus exec --user 1000` działają. Śmierć klienta `incus exec` (`systemctl --user stop tide-proto-client`): proces w **scope ginie**, w **usłudze `--user`** (`systemd-run --user --unit=… --collect --wait sleep 30`) i w usłudze roota z `--uid=1000` **przeżywa**. `systemctl --user restart tide-daemon` nie zabija procesu (klient nie był dzieckiem usługi). Decyzja `krok-jako-usluga-user` | 0,06 s | — |
| P-9 (historia) | jw. | jw. | Runda 1: **lingering i `systemd-run --user --scope` działają; proces w scope NIE przeżywa śmierci klienta `incus exec`** (`systemctl --user stop tide-proto-client` → `pgrep sleep` pusty). Restart `tide-daemon` nie zabił procesu, ale klient nie był dzieckiem usługi — test nie rozstrzyga. Runda 2 nie ruszyła (kontener zepsuty przez `raw.idmap` z p4); warianty usługi `--user --wait` i usługi roota z `--uid` czekają | 0,06 s | `systemd-run` jako root pudełka z `--uid=agent` (usługa zamiast scope; tokeny tylko w `in/env`); bliżej projektu: `systemd-run --user` jako usługa (bez `--scope`) |

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
| Narzut kroku: `incus exec … --user 1000 -- systemd-run --user --scope true` | ≤ 3 s | runda 4: 0,064 / 0,066 / 0,066 s (runda 3: 0,056 / 0,052 / 0,060 s) | 0,066 s | tak |
| Narzut kroku (usługa): `incus exec … --user 1000 -- systemd-run --user --wait true` | ≤ 3 s | runda 4: 0,070 / 0,073 / 0,077 s (runda 3: 0,064 / 0,061 / 0,059 s) | 0,073 s | tak |
| Zimny start zatrzymanego kontenera (`incus start` do końca startu systemd) | ≤ 30 s | runda 4: 2,828 / 2,301 / 1,909 s (runda 3: 3,811 / 1,813 / 2,395 s) | 2,301 s | tak |
| Reset: `incus snapshot restore` (do końca startu systemd) | ≤ 60 s | runda 4: 3,982 / 3,498 / 3,877 s (runda 3: 3,962 / 3,304 / 3,291 s) — tylko z chwilowym `restricted.snapshots=allow` (projekt z PT-166 blokuje migawki: „Project user-1000 doesn't allow for snapshot creation”) | 3,877 s | tak, ale wymaga `restricted.snapshots=allow` w projekcie |

## Co widać z kontenera (P-4)

| Sprawdzenie | Polecenie | Wynik |
| --- | --- | --- |
| `~/.ssh` | `incus exec tide-proto -T -- test -e /home/user/.ssh` | niewidoczne (kod 1), runda 4 |
| `~/.git-credentials` | `incus exec tide-proto -T -- test -e /home/user/.git-credentials` | niewidoczne (kod 1), runda 4 |
| główny worktree projektu | `incus exec tide-proto -T -- ls -A /home/user/DevWorkspace/toolssuite` (tylko `.git`) | widać tylko `.git`, runda 4 |
| gniazdo Dockera hosta | `incus config show tide-proto --expanded` (brak `docker.sock`) | brak urządzenia z `docker.sock`, runda 4 |
| Postgres hosta | `incus exec tide-proto -T -- timeout 10 nc -z -w 3 <ip hosta> 5432` dla 10.20.1.159, 100.87.229.94, 10.173.184.1, 10.60.187.1 | wszystkie niewidoczne (kod 1), runda 4 — host ma Postgresa na `0.0.0.0:5432` |

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
