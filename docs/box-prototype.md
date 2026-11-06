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
| P-2 | **działa z profilem AppArmor bwrap w pudełku** (PT-178) | bwrap Claude Code tak; Codex: piaskownica startuje, blokada zapisu poza katalogiem niezweryfikowana — „Próby PT-178” niżej |
| P-3 | **K3 możliwe** (PT-178) | Claude Code przez `ANTHROPIC_BASE_URL`, Codex tylko z własnym dostawcą modelu — „Próby PT-178” niżej |
| P-6 | **działa** (PT-178) | klienci przez CONNECT bez DNS, `NO_PROXY` honorowane; wyjątek Node `fetch` 18 — „Próby PT-178” niżej |
| P-7 | **działa** (PT-178) | Incus 6.0 z Ubuntu, `images:ubuntu/24.04` — „Próby PT-178” niżej |
| Pomiary | **w progach** | narzut 0,066 s, start 2,3 s, reset 3,9 s (reset wymaga `restricted.snapshots=allow`, `migawki-w-projekcie`) — runda 4 |

**`dotnet test` LegalApp w pudełku — 51 porażek integracyjnych, żadna z Dockera ani Testcontainers**
(`docs/box-prototype/p1-dotnet-test.log`): 36× `Connection string is not configured. Set DATABASE_CONNECTION env var
or ConnectionStrings:DefaultConnection in appsettings` (część testów czyta połączenie ze zmiennej środowiska, której
pudełko nie ma — na hoście ustawia ją użytkownik), 4× brak plików `EvalSet/Cases/*/expected-classifier-acts.json`
(nie ma ich w `git archive HEAD` — nieśledzone albo generowane), 11× asercje aplikacji (teksty lokalizacji,
HTTP 405 zamiast 404, liczba uruchomień harmonogramu, kolejność wyników wyszukiwania). Wyjaśnienie i profil
środowiska LegalApp (zmienne, pliki, kultura) należą do PT-184; porównania z wynikiem na hoście nie robiono.
**Wiersz „działa” dotyczy Dockera i Testcontainers, nie wyniku testów:** 11 porażek z asercji (teksty lokalizacji,
kolejność wyników) to rzeczy zależne od locale i kolacji, czyli możliwie od środowiska pudełka. PT-184 musi je
sprawdzić na hoście tym samym `dotnet test --filter` bez `DATABASE_CONNECTION` — dopiero to rozstrzygnie, czy
pudełko nie zmienia wyniku testów.

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
(pudełko prób PT-178; po scaleniu z PT-178 wspólny `cleanup.sh` usuwa oba, `--keep-box` zostawia kontenery).

Wynik rundy 4 (14:45–14:48): **P-4, P-5, P-9 i pomiary działają**; P-1 nierozstrzygnięte — oba nowe runtime'y
(crun 1.30.1, runc 1.2.6) nie zostały sprawdzone, bo dockerd nie wstał po trzecim restarcie w minucie (limit
startów jednostki, błąd skryptu — poprawiony). Runda 5: tylko `bash scripts/box/prototype/p1.sh`.

| Próba | Co sprawdza | Polecenie | Wynik | Czas | Wariant awaryjny (gdy nie działa) |
| --- | --- | --- | --- | --- | --- |
| P-1 | Docker w nieuprzywilejowanym kontenerze (`security.nesting`, intercept `mknod`/`setxattr`): `docker run --rm hello-world`, `dotnet test` LegalApp z Testcontainers (`pgvector/pgvector:pg16`) | `bash scripts/box/prototype/p1.sh` | **Runda 6: działa.** runc 1.2.6: hello-world OK; `dotnet test` LegalApp (SDK 10.0 z `TargetFramework`): Testcontainers z `pgvector/pgvector:pg16` i Ryuk działa, jednostkowe 5124 OK / 0 błędów, integracyjne 1186 OK / 51 błędów konfiguracji testów (stan końcowy wyżej), kod 1 | SDK 26,8 s, dotnet test 1183 s | — |
| P-1 (historia) | jw. | jw. | **Runda 5: `docker run --rm hello-world` działa z runc 1.2.6** (statyczny z wydań GitHub, SHA-256 `0774f49d…2ebc`, domyślny runtime dockerd), bez intercept, w projekcie ograniczonym. runc 1.3.4 z apt: kod 126 (`open sysctl net.ipv4.ip_unprivileged_port_start … reopen fd 8: permission denied`); crun 1.30.1: kod 125 (`open /proc/sys/net/ipv4/ip_unprivileged_port_start: Permission denied`) — oba nowe runtime'y otwierają `/proc/sys` w sposób, którego profil AppArmor kontenera Incusa 6.0.0 nie przepuszcza. `dotnet test` LegalApp nie ruszył: skrypt padł (kod 128) przy odczycie `global.json`, którego w repozytorium nie ma — błąd skryptu, poprawiony; runda 6: znowu `bash scripts/box/prototype/p1.sh`. **Runda 4: nierozstrzygnięte.** runc 1.3.4 z apt: ten sam błąd AppArmor/procfs (kod 126). crun 1.30.1 i runc 1.2.6 z wydań GitHub pobrały się i działają (`--version`, sumy SHA-256 w logu), ale dockerd nie wstał po trzecim restarcie w minucie („Job for docker.service failed”) — najpewniej limit startów jednostki (`StartLimitBurst`), błąd skryptu, nie runtime'u; ten sam błąd mógł zafałszować runc 1.1 z rundy 3. Poprawione (`systemctl reset-failed` przed restartem, dziennik jednostki w logu); runda 5: tylko `bash scripts/box/prototype/p1.sh`. **Runda 3: nie działa.** runc 1.3.4 z apt: ten sam błąd `open sysctl net.ipv4.ip_unprivileged_port_start … reopen fd 8: permission denied` (kod 126); crun 1.14 z Ubuntu 24.04: `OCI runtime create failed: unknown version specified` (za stary na specyfikację OCI Dockera 29); runc 1.1.12 z apt (`--allow-downgrades`): dockerd nie wstaje (`See "systemctl status docker.service"`). Runda 4 (skrypt gotowy): najnowszy crun i runc 1.2.6 z wydań GitHub (statyczne). Rekomendacja na wypadek dalszej porażki: `type: vm` (decyzja `docker-w-vm`). **Runda 2: nie działa.** Sieć przez chwilowe proxy działa (apt, `docker pull` z `registry-1.docker.io`), `docker.io` 29.1.3 z runc 1.3.4 instaluje się, `docker info`: overlayfs, apparmor, seccomp. `docker run --rm hello-world` kończy się kodem 126 — bez intercept i z chwilowym intercept tak samo: `OCI runtime create failed: runc create failed: unable to start container process: error during container init: open sysctl net.ipv4.ip_unprivileged_port_start file: reopen fd 8: permission denied`. `dotnet test` pominięty. Prawdopodobna przyczyna: nowy runc (poprawki bezpieczeństwa z 2025, ponowne otwieranie plików `/proc/sys` przez `/proc/thread-self/fd`) kontra profil AppArmor kontenera w Incusie 6.0.0 z Ubuntu — **hipoteza, niesprawdzona**. Runda 1 (reguły `tide-proto`): forward ginął, tabela przed/po identyczna — `p1.log` jest nadpisywany co rundę, więc dowód trybu reguł (tabela `inet tide-box` przed i po, `WYNIK nft-przed-po: ok`) jest w wersji z commita `5b6977a`: `git show 5b6977a:docs/box-prototype/p1.log` | 4,4 s (pull + błąd) | Podman w pudełku z gniazdem Docker API (`podman system service`) albo `type: vm` dla projektów z Dockerem |
| P-2 (PT-178) | bwrap Claude Code i landlock Codexa w kontenerze | `bash scripts/box/prototype/p2.sh` | w „Próby PT-178” niżej | — | — |
| P-3 (PT-178) | OAuth agentów przez inny base URL (K3) | `bash scripts/box/prototype/p3.sh` | w „Próby PT-178” niżej | — | — |
| P-4 | `<proj>/.git` i `<proj>-worktrees` z ext4 `/home` przez `shift=true` pod identycznymi ścieżkami, nakładki ro na `.git/hooks`, `.git/config`, `.git/info`; `git status`, `git commit` na `proto/k-38`, `npm ci` (kontener i host) | `bash scripts/box/prototype/p4.sh` | **Runda 4: działa (wariant `raw.idmap`).** `shift=true` odrzucony przez projekt ograniczony; z `raw.idmap "both 1000 1000"` (decyzja `raw-idmap-zamiast-shift`) i montowaniami dodanymi przy zatrzymanym kontenerze: uid 1000 ↔ 1000 pod identycznymi ścieżkami, `git status` i `git commit` na `proto/k-38` z kontenera (commit widoczny na hoście, plik należy do `tomek`), zapis do `.git/hooks`, `.git/config`, `.git/info` odrzucony („Read-only file system”), `npm ci --ignore-scripts` w toolssuite: host 8,6 s, kontener 11,6 s (zimny cache) i 10,8 s (ciepły). Worktree i gałąź próby usunięte | npm ci: 8,6 / 11,6 / 10,8 s | — |
| P-4 (historia) | jw. | jw. | **Runda 3: częściowo.** Po `make box-install` z `root:1000:1` kontener z `raw.idmap "both 1000 1000"` startuje; `.git`, `<proj>-worktrees` i `.git/hooks` (ro) dołączają się do działającego kontenera, ale pojedynczy plik `.git/config` (ro) nie: `Failed to add mount for device inside container: … incusd forkmount lxc-mount … exit status 1`. Runda 4: montowania dodawane przy zatrzymanym kontenerze (Incus zakłada je przy starcie). Git, npm i widoczność hosta jeszcze niesprawdzone. **Runda 2: nie działa w projekcie ograniczonym.** `incus config device add … shift=true` odrzucone: „The "shift" property cannot be used with a restricted source path”. Wariant `raw.idmap "both 1000 1000"` przyjęty przez projekt (`restricted.idmap.uid/gid=1000` z PT-166), ale kontener nie wystartował (`incusd forkstart … exit status 1`) — najpewniej brak `root:1000:1` w `/etc/subuid` i `/etc/subgid` hosta (hipoteza). Worktree próby i gałąź `proto/k-38` usunięte; git, npm i widoczność hosta niesprawdzone | — | `raw.idmap "both 1000 1000"` bez `shift` (skrypt próbuje sam i zapisuje wariant) |
| P-5 | Projekt ograniczony `user-1000` (`incus-user`, grupa `incus`): nesting, intercept, filtrowanie NIC (`security.mac_filtering`, `security.ipv4_filtering`, `security.ipv6_filtering` na `eth0`), próba podszycia pod obcy adres | `bash scripts/box/prototype/p5.sh` | **Runda 4: działa.** Nesting w projekcie ograniczonym działa; intercept tylko z `restricted.containers.interception=allow` (domyślnie zablokowany; Docker go nie potrzebuje — w P-1 nic nie zmienił). Filtrowanie NIC (mac + ipv4, decyzja `filtrowanie-nic-bez-ipv6`): własny adres dochodzi do `10.173.184.1:18080`, podszycie pod `10.173.184.250` nie (kontrola bez filtrowania: dochodzi). **Runda 3: działa (po ręcznej analizie logu).** Skrypt zgłosił „nie dochodzi nawet własny adres”, bo `incus list tide-proto` dopasował też drugi kontener `tide-proto-net` (10.173.184.91) i porównywał zły adres; serwer testowy zapisał `10.173.184.164` (tide-proto) przed i po włączeniu filtrowania, a `10.173.184.250` tylko przed — podszycie z filtrowaniem (mac + ipv4) nie doszło (`nc` kod 1). Błąd skryptu poprawiony (`box_ip` po dokładnej nazwie). **Runda 2:** **Nesting działa** (projekt przyjął `security.nesting=true`, `unshare --user --map-root-user --pid --fork --mount-proc true` OK). **Intercept w projekcie ograniczonym nie działa**: „Container syscall interception is forbidden” (`restricted.containers.interception=block`, domyślne); z chwilowym `allow`: `mknod c 1 3` i `setxattr trusted.overlay.opaque` działają. **Filtrowanie NIC działa (mac + ipv4)**: kontrola A (własny adres `10.173.184.164`) i B (podszycie `10.173.184.250` bez filtrowania) doszły do `10.173.184.1:18080`; po `security.mac_filtering=true security.ipv4_filtering=true` własny adres dochodzi, podszycie nie (`nc` kod 1, brak wpisu w dzienniku serwera). `security.ipv6_filtering=true` — kontener nie startuje bez `br_netfilter`; użyto `false` (decyzja `filtrowanie-nic-bez-ipv6`) | 0,05 s (nc) | nesting/intercept: `--project-mode admin` w `scripts/box/install.sh` (grupa `incus-admin`, D9) z wpisem `tryb-admin` albo `restricted.containers.interception=allow` w projekcie (decyzja po rundzie 2); filtrowanie NIC: brak wariantu — warunek konieczny (`question`) |
| P-6 (PT-178) | Klienci (npm, NuGet, pip, Docker, agenci) przez proxy CONNECT i `NO_PROXY` | `bash scripts/box/prototype/p6.sh` | w „Próby PT-178” niżej | — | — |
| P-7 (PT-178) | Obrazy Incusa (bazowy Tide) | `bash scripts/box/prototype/p7.sh` | w „Próby PT-178” niżej | — | — |
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

### Pierwsza budowa szablonów stosów (PT-192)

Próba `bash scripts/box/stacks-trial.sh` z **2026-11-05 19:27:52**, commit `aab9dcad`, Incus 6.0.0:
wszystkie kontrole przeszły (kod 0). Użyto produkcyjnego buildera i proxy z polityką domen; profile próbne
zatwierdzono przez `tide box approve`. `STACKS_VERSION` i suma katalogu były zgodne:
`7a15d6f7613824463834189090b264fd54eb34302d6929a76e2769c3f7ac39d5`.

| Pomiar / kontrola | Wynik |
| --- | --- |
| `tide box build plasma-tide`, python-qt | **58,8 s**, `clean`, próg ≤ 900 s |
| `tide box reset plasma-tide` | **1,5 s**, `clean`, próg ≤ 60 s; plik w `/home/agent` zachowany, plik w `/root` cofnięty |
| Budowa `tide-stacks-test`, dotnet + docker | **76,5 s**, `clean` |
| Status obu pudełek | hash zgodny z profilem, filtrowanie NIC: tak (mac i IPv4 włączone, IPv6 wyłączone zgodnie z ustawieniami) |
| Dzienniki odmów proxy obu budów | puste; wcześniejsze odmowy `motd.ubuntu.com` wyjaśniono i domenę dopisano wyłącznie do `base.yaml: build-domains` |
| Programy jako `agent` | Claude Code 2.1.317 i codex-cli 0.166.0 działają |
| Qt | importy PyQt6 wymagane przez projekt i QGuiApplication przeszły; platforma `offscreen` |
| .NET | SDK 8.0.427, `verify` i kontrola NuGet.Config przeszły |
| Docker jako `agent` | `docker info` działa: Docker 29.1.3, runtime `runc-126`; lokalny obraz `testcontainers/ryuk:0.11.0` dostępny |
| Środowisko dockerd | HTTP_PROXY i HTTPS_PROXY na `http://10.173.184.1:18080`, pełne `NO_PROXY=localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal`, bez DOCKER_HOST |

Hash python-qt: `cb1e5ea3cb315a07fc08de8a6585ea0e14f02c8180d716ab510ed038d9643a6c`;
hash dotnet + docker: `83b835c92db25fab340bc25876e22ba589d729c57e4fc5362a93f95e92b71925`.
Obraz Ryuk pobrał builder podczas działania proxy; kontrola po budowie odczytuje go lokalnie.
Nowe wolumeny domowe zostały zainicjalizowane wewnątrz pudełek, a kontrola braku swapu przeszła przed
szablonami (decyzje `home-init-bez-initial` i `swap-zero-bez-lowlevel`). Obie instancje i profile próbne
usunięto, chwilowe `restricted.snapshots=allow` cofnięto do braku wartości.

Źródła pomiarów w worktree: `docs/box-prototype/pt192-2026-11-05T192752.log` oraz katalog o tej samej
nazwie bez rozszerzenia (szczegółowe logi budowy i odmów). Pełne wyjście polecenia jest również w wyniku
kroku `pt-192-szablony-stosow-node-playwright.resume-K-38-PT-192-6.step/outside/outside.log` obok worktree.
Po poprawkach izolacji testów w `7be29619` osobne **`make test` zakończyło się kodem 0**: wszystkie grupy
OK, w tym 2438 testów daemona (2 pominięte). Wynik w kroku `resume-K-38-PT-192-7.step/outside/outside.log`.
Shellcheck był niedostępny; kontrola ta została pominięta zgodnie z kryterium karty. Pomiary pojedynczej
budowy nie zastępują końcowej oceny wydajności z #24.

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
(`net_open`/`net_close`) uruchamia na czas próby `scripts/box/prototype/proxy-plain.py` (CONNECT i zwykłe HTTP do każdego celu, bez MITM,
dziennik celów) na `<adres mostka>:<port proxy Tide>` i podaje kontenerowi `HTTP(S)_PROXY` (polecenia
`incus exec`), `Acquire::http(s)::Proxy` (apt) i drop-in `dockerd` — dokładnie jak w docelowej architekturze
(sekcja 4.5). Bez sudo i bez żadnej zmiany w nftables; lista celów trafia do logu (materiał dla P-6). To inny plik
niż `proxy.py` z PT-178 (lista domen, trasy wstrzykujące): proxy prób PT-164 przepuszcza każdy cel, bo P-1 i P-4
sprawdzają Dockera i montowania, nie listę domen.

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

Wszystko naraz: `bash scripts/box/prototype/all.sh` — próby PT-164 (p5, p1, p4, p9, measure), potem PT-178 (p7, p6,
p3, p2); każda pisze własny log, niepowodzenie jednej nie zatrzymuje następnych; `TRIALS="p1 p9"` wybiera próby.
Sprzątanie: `bash scripts/box/prototype/cleanup.sh` (proxy prototypowe, kontenery `tide-proto`, `tide-proto-net`,
`tide-proto-p7`, worktree `proto-k-38`; `--keep-box` zostawia kontenery, `--rules` z sudo usuwa pozostałe reguły
`tide-proto`). Pojedynczo:

1. `bash scripts/box/prototype/p5.sh` — projekt, nesting, intercept, filtrowanie NIC (tworzy kontener).
2. `bash scripts/box/prototype/p1.sh` — Docker i LegalApp.
3. `bash scripts/box/prototype/p4.sh` — montowania, git, npm, widoczność hosta.
4. `bash scripts/box/prototype/p9.sh` — systemd --user i przeżycie restartu.
5. `bash scripts/box/prototype/measure.sh` — pomiary.

## Próby PT-178 (P-2, P-3, P-6, P-7)

Próby z `docs/card-box-design.md`, sekcja 10. Każda próba to skrypt `scripts/box/prototype/p<N>.sh` uruchamiany
przez użytkownika poza piaskownicą agenta; pełny dziennik w `docs/box-prototype/p<N>-<czas>.log` (wiersze
`WYNIK …`), dzienniki proxy (odmowy, dostęp — bez nagłówków i tokenów) w katalogu `p<N>-<czas>/` obok.
P-1, P-4, P-5, P-9 dopisuje PT-164 (#1); P-2, P-3, P-6, P-7 — PT-178 (#15). Decyzje z prób:
`docs/box-decisions.md`.

Pudełko prób PT-178: `tide-proto-net` (`images:ubuntu/24.04`, `security.nesting=true`) w projekcie użytkownika,
sieć tylko przez nftables z PT-166 (z mostka wolno wyłącznie do `<adres mostka>:18080` i DHCP). Proxy prototypowe
`scripts/box/prototype/proxy.py` słucha na adresie mostka, na porcie 18080: CONNECT do domen z listy, zwykłe HTTP do
domen z listy (wariant awaryjny apt) i trasy wstrzykujące `/anthropic`, `/chatgpt` (K3). Sprzątanie:
`scripts/box/prototype/cleanup.sh`.

Przebiegi (2026-11-04): 1 — 12:33 (P-6 padło na źródłach apt); 2 — 13:07 (pełny P-6 bez Playwright i NO_PROXY,
P-3 Claude, P-2 wariant A); 3 — 13:51 (`P6_ONLY=playwright,noproxy`: Playwright, NO_PROXY, P-3 Codex, P-2 warianty A
i B); 4 — 14:27 (`TRIALS="p3 p2"`: Codex z własnym dostawcą modelu i identyfikatorem konta hosta w atrapie, P-2
z Codexem przez model).

### Tabela prób PT-178

| Próba | Pytanie | Skrypt | Wynik | Dziennik |
| --- | --- | --- | --- | --- |
| P-2 | bwrap (Claude Code) i landlock (Codex) w kontenerze przy `apparmor_restrict_unprivileged_userns=1` | `p2.sh` | **bwrap (Claude Code): działa po wczytaniu profilu AppArmor w pudełku (wariant B); z gołego obrazu (A) nie.** A: `bwrap --unshare-all` → `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`, jądro: `apparmor="DENIED" operation="capable" namespace="root//incus-user-1000_tide-proto-net_<var-lib-incus>" profile="unprivileged_userns"` (setpcap, net_admin) — ograniczenie hosta obowiązuje w przestrzeni AppArmor pudełka; `claude -p` z `failIfUnavailable` odmawia startu („Ubuntu's AppArmor restriction on user namespaces stops bubblewrap here”). B: profil `profile bwrap /usr/bin/bwrap flags=(unconfined) { userns, }` (ten sam co na hoście, `scripts/sandbox/check.py profile`) wczytany przez `apparmor_parser -r` **wewnątrz** pudełka (root pudełka, host bez zmian) → goły bwrap `bwrap-ok`; `claude -p` z `sandbox.enabled=true` kod 0: odczyt `~/.claude/p2-marker` → `No such file or directory` (denyRead maskuje katalog; błąd `cat` zapisany w `claude-read.txt`, więc polecenie naprawdę poszło), zapis w katalogu roboczym tak, poza nim `Read-only file system` (kod i komunikat z odpowiedzi modelu w `--output-format text`; `p2.sh` po review loguje `stream-json` z wynikiem narzędzia Bash). `unshare --user` bez profilu nadal odmowa — ograniczenie zostaje dla reszty procesów. Decyzja `bwrap-profil-w-pudelku`. **Codex: piaskownica startuje po wczytaniu profilu (B), z gołego obrazu (A) nie; ograniczenie zapisu niezweryfikowane.** Codex 0.166 na Linuksie używa bubblewrap, nie samego landlocka („Codex's Linux sandbox uses bubblewrap and needs access to create user namespaces”), więc dzieli los Claude Code. A: `codex exec --sandbox workspace-write` — polecenia nie ruszyły, „the sandbox failed to start with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`”, żaden plik nie powstał. B (przebieg 4): `codex exec --sandbox workspace-write` (model przez proxy, wariant `provider`) kod 0 — w zapisie Codexa jest **jeden** blok `exec` (`echo ok > ./codex-model-inside`, „succeeded”), plik jest. Drugiego polecenia (`echo bad > ~/p2-outside-codex-model`) Codex nie wykonał — „kod 1, read-only file system” to tylko treść odpowiedzi modelu, więc brak pliku nie dowodzi odmowy piaskownicy. Próba bez modelu (`codex sandbox …`) nie dała wyniku przez składnię CLI 0.166 (`linux` jako polecenie, potem brak `--full-auto`). Udowodnione: z profilem piaskownica Codexa startuje i wykonuje polecenia w katalogu roboczym; nieudowodnione: że blokuje zapis poza nim — wariant `sandbox.enabled=false`/ryzyko K1 dla Codexa zostaje otwarty do #16 (decyzja `bwrap-profil-w-pudelku`). `p2.sh` po review: `codex sandbox -- sh -c …` bez modelu z werdyktem odróżniającym błąd składni od piaskownicy, a w ścieżce z modelem jedno polecenie na wywołanie i liczba bloków `exec` (bez `exec` → „niezweryfikowane”); powtórka: `TRIALS=p2 bash scripts/box/prototype/all.sh` | `p2-2026-11-04T142904.log`, `p2-2026-11-04T135550.log`, `p2-2026-11-04T132223.log` |
| P-3 | Claude Code (OAuth) przez `ANTHROPIC_BASE_URL` i Codex przez `chatgpt_base_url` na proxy wstrzykujące (K3) | `p3.sh` | **Claude Code: K3 możliwe** — `ANTHROPIC_BASE_URL=http://<mostek>:18080/anthropic CLAUDE_CODE_OAUTH_TOKEN=<atrapa> claude -p 'hi'` 9/9 kod 0 w trzech przebiegach, proxy `INJECT POST /anthropic/v1/messages` → **200**, w pudełku tylko atrapa. Odświeżenie OAuth: 0 żądań przez proxy (ani base URL, ani CONNECT) — pudełko nie odświeża, token odświeża host, proxy czyta plik przy każdym żądaniu. Poza base URL Claude Code próbuje `CONNECT api.anthropic.com:443` (odmowa, bez wpływu). **Codex: K3 możliwe — tylko z własnym dostawcą modelu** (przebieg 4): `codex exec --skip-git-repo-check -c 'chatgpt_base_url="http://<mostek>:18080/chatgpt/backend-api/"' -c 'model_provider="tide"' -c 'model_providers.tide={ name = "tide", base_url = "http://<mostek>:18080/chatgpt/backend-api/codex", wire_api = "responses", requires_openai_auth = true }' 'Reply with the single word: hi'` kod 0, proxy `INJECT POST /chatgpt/backend-api/codex/responses` → **200** (plus `codex/models`, `wham/accounts/check`, `ps/plugins/*`, `ps/mcp`, `analytics-events` → 200/204). W pudełku `~/.codex/auth.json` z tokenami-atrapami (JWT bez podpisu); z pliku hosta przechodzą tylko identyfikator konta i rodzaj planu — z wymyślonym kontem Codex kończy „selected workspace missing from routing discovery”. Same `chatgpt_base_url` albo `+OPENAI_BASE_URL` **nie wystarczą** (przebiegi 3 i 4): wywołanie modelu idzie przez CONNECT prosto do `chatgpt.com:443` (odmowa), kod 1. Odświeżenie OAuth Codexa: 0 żądań (`auth.openai.com` się nie pojawia) | `p3-2026-11-04T142744.log`, `p3-2026-11-04T135419.log`, `p3-2026-11-04T132151.log` |
| P-6 | klienci przez CONNECT bez DNS, `NO_PROXY` dla localhost | `p6.sh` | **działa**: apt (źródła przepisane na `https://`, decyzja `apt-przez-https`), npm, NuGet, pip, `docker pull`, Playwright (`playwright@1.49.1`), instalator Claude Code i `npm i -g @openai/codex` przechodzą przez CONNECT bez DNS z listą z dziennika odmów. **NO_PROXY honorują wszyscy badani** (curl, Node `fetch`, dotnet `HttpClient`, Playwright/Chromium) dla `localhost` i `127.0.0.1`: serwer w pudełku dostał żądanie, proxy 0 wpisów. Wyjątki: **Node `fetch`** (18.19) nie używa proxy wcale — ruch na zewnątrz pada (`ConnectTimeoutError`, połączenie bezpośrednie blokuje nftables); **Playwright ≥ 1.50 wymaga Node ≥ 20** (Ubuntu 24.04 ma 18.19) — obraz bazowy potrzebuje nowszego Node (#16/#29) | `p6-2026-11-04T135145.log`, `p6-2026-11-04T130727.log`, przebieg 1: `p6-2026-11-04T123348.log` |
| P-7 | Incus 6.0 z Ubuntu universe: `images:` i `images:ubuntu/24.04` | `p7.sh` | **działa**: `incus 6.0.0-1ubuntu0.3` z noble-security/universe, `incus image list images: ubuntu/24.04` kod 0, `incus launch images:ubuntu/24.04` kod 0 w 1–2 s (Ubuntu 24.04.5 LTS) w projekcie `user-1000`, trzy przebiegi. Zabbly niepotrzebne (decyzja `incus-z-ubuntu`) | `p7-2026-11-04T135140.log`, `p7-2026-11-04T130723.log`, `p7-2026-11-04T123343.log` |

#### Pomiar `claude -p 'hi'` (P-3, trzy powtórzenia na przebieg)

| Gdzie | Przebieg 2 [s] | Przebieg 3 [s] | Przebieg 4 [s] | Mediana z 9 [s] |
| --- | --- | --- | --- | --- |
| pudełko, przez proxy wstrzykujące | 4,26 · 5,73 · 3,52 (mediana 4,26) | 5,81 · 5,45 · 12,18 (mediana 5,81) | 3,09 · 2,22 · 2,14 (mediana 2,22) | **4,26** |
| host | 6,93 · 4,42 · 5,65 (mediana 5,65) | 4,32 · 4,08 · 5,09 (mediana 4,32) | 5,33 · 5,55 · 4,28 (mediana 5,33) | **5,09** |

Narzutu proxy nie widać: w dwóch przebiegach z trzech pudełko było szybsze, mediana z dziewięciu prób to 4,26 s
w pudełku vs 5,09 s na hoście. Pojedyncze 12,18 s to odstający wynik (opóźnienie API — przez proxy idzie jeden POST).
Host uruchamia pełną konfigurację użytkownika (CLAUDE.md, wtyczki), pudełko gołe `claude`.

### Domeny z dziennika odmów

Źródło dla szablonów stosów (#16/PT-192, #29, #30/PT-193). Runda 1 każdego klienta biegnie z pustą listą; kolejne
dokładają domeny z odmów, aż klient przejdzie (`docs/box-prototype/p6-*/domains-*.txt`). Tabela P-6 z kryterium
karty: kolumny „przez CONNECT” i „NO_PROXY dla localhost” są przy każdym kliencie w tabeli niżej.

| Klient | Przez CONNECT | NO_PROXY dla localhost | Domeny z dziennika odmów |
| --- | --- | --- | --- |
| apt | tak | nie dotyczy | `archive.ubuntu.com`, `security.ubuntu.com` |
| npm | tak | nie dotyczy | `registry.npmjs.org` |
| NuGet / dotnet (`HttpClient`) | tak | tak | `api.nuget.org`, `crl3.digicert.com`, `ocsp.digicert.com` |
| pip | tak | nie dotyczy | `pypi.org`, `files.pythonhosted.org` |
| docker pull | tak | nie dotyczy | `registry-1.docker.io`, `auth.docker.io`, `production.cloudfront.docker.com` |
| Playwright | tak | tak | `registry.npmjs.org`, lustra `playwright*.azureedge.net`, `playwright.download.prss.microsoft.com` |
| Node `fetch` (18.19) | **nie** (ignoruje proxy) | tak | — (zob. „NO_PROXY per klient”) |
| curl / instalator Claude Code | tak | tak | `claude.ai`, `downloads.claude.ai` |
| Codex (`npm i -g`) | tak | nie dotyczy | `registry.npmjs.org` |

Szczegóły rund i poleceń:

| Klient | Polecenie | Pusta lista | Domeny potrzebne | Rundy |
| --- | --- | --- | --- | --- |
| apt | `apt-get update` (źródła `https://`) | kod 1 | `archive.ubuntu.com`, `security.ubuntu.com`; dodatkowo `motd.ubuntu.com` (motd-news, niepotrzebne do aktualizacji) | 3 |
| apt (instalacja) | `apt-get install … dotnet-sdk-8.0 docker.io bubblewrap …` | kod 100 | `archive.ubuntu.com`, `security.ubuntu.com` | 2 |
| npm | `npm install --package-lock-only && npm ci` | kod 1 (E403) | `registry.npmjs.org` | 2 |
| NuGet / dotnet | `dotnet restore` | kod 1 | `api.nuget.org`, `crl3.digicert.com`, `ocsp.digicert.com` (sprawdzanie unieważnienia certyfikatów podpisu paczek) | 3 |
| pip | `pip download requests==2.32.3` | kod 1 | `pypi.org`, `files.pythonhosted.org` | 3 |
| docker pull | `docker pull hello-world` (dockerd z drop-inem proxy) | kod 1 | `registry-1.docker.io`, `auth.docker.io`, `production.cloudfront.docker.com` | 4 |
| Playwright | `npm i playwright@1.49.1 && npx playwright install chromium` | kod 1 | `registry.npmjs.org`, `playwright.azureedge.net`, `playwright-akamai.azureedge.net`, `playwright-verizon.azureedge.net`, `playwright.download.prss.microsoft.com` (lustra pobierania przeglądarki, próbowane po kolei) | 4 |
| instalator Claude Code | `curl -fsSL https://claude.ai/install.sh \| bash -s 2.1.317` | kod 22 | `claude.ai`, `downloads.claude.ai`; `api.anthropic.com` po instalacji (`claude --version`), niepotrzebne do instalacji | 4 |
| Codex | `npm i -g @openai/codex` (0.166.0) | kod 1 | `registry.npmjs.org` | 2 |
| Claude Code (praca, P-3) | `claude -p` przez base URL | — | żadnej przez CONNECT; odmowy `api.anthropic.com` bez wpływu na wynik | — |
| Codex (praca, P-3) | `codex exec` (wariant `provider`) | — | żadnej przez CONNECT — model i usługi konta przez base URL; odmowy `ab.chatgpt.com` (telemetria), `sdmntprcentralus.oaiusercontent.com`, `sdmntprsouthcentralus.oaiusercontent.com` (pliki/obrazy wtyczek) bez wpływu na wynik. Bez wariantu `provider` model idzie do `chatgpt.com` — z K3 ta domena ma **nie** być na liście (poszłaby z atrapą) | — |

### NO_PROXY per klient

`NO_PROXY=localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal,<adres mostka>`; serwer HTTP w pudełku
(`python3 -m http.server 8765 --bind ::`) na pętli zwrotnej. „Tak” = serwer dostał żądanie, a dziennik proxy nie ma
wpisu (przebieg 3, `docs/box-prototype/p6-2026-11-04T135145.log`).

| Klient | Przez CONNECT | `localhost` bez proxy | `127.0.0.1` bez proxy | Obejście |
| --- | --- | --- | --- | --- |
| curl | tak (instalator Claude Code) | tak | tak | — |
| Node `fetch` (18.19) | **nie** — ignoruje `HTTP(S)_PROXY` | tak (nie używa proxy wcale) | tak | ruch na zewnątrz: Node ≥ 24 z `NODE_USE_ENV_PROXY=1` albo `undici` `EnvHttpProxyAgent` w kodzie; Node 18 z `NODE_USE_ENV_PROXY=1` — bez zmian (`ConnectTimeoutError`) |
| dotnet `HttpClient` | tak (NuGet) | tak | tak | — |
| Playwright (Chromium 1.49.1) | tak (pobranie przeglądarki) | tak | tak | — |
| apt, npm, pip, dockerd, instalator Codexa | tak | nie dotyczy (bez żądań do localhost) | | — |
## Pierwszy krok w pudełku

PT-191 dostarcza narzędzia do dowodu, ale wynik z atrap nie zastępuje próby na Incusie.
`python3 daemon/tide.py box run plasma-tide -- env` uruchamia syntetyczny krok przez `BoxRunner.start`,
z domenami profilu, portami `PortPool` PT-171 i montowaniami kroku. Nie przekazuje tokenów agentów do `in/env`.
Z istniejącego worktree działa w nim; z głównego repozytorium tworzy osobną gałąź `box-run/demo-…` i worktree,
które pozostają do kontroli. Log, `in/env` (0600) i kod wyjścia są w katalogu kroku podanym na stderr.

Próba czerwonej listy: `make box-check BOX=plasma-tide`. Skrypt biegnie jako `agent` i drukuje tabelę
„punkt → odmowa/przeszło/nie dotyczy”. Kod 1 oznacza choć jedno „przeszło”; „nie dotyczy” nie zmienia kodu,
ale wymaga osobnego dowodu. Próba podszycia wymaga sprawdzonego wolnego IP w podsieci mostka:
`make box-check BOX=plasma-tide CHECK_ARGS="--spoof-ip <wolny-IP>"` (opcjonalnie `--prefix`, `--interface`,
`--host-ip`, `--spoof-image <lokalny-obraz-z-ip-i-curl>`). Skrypt nadaje IP przez `sudo -n` lub Docker
`--privileged --net=host`, wysyła `curl --interface` i usuwa adres w trap. Brak możliwości = „nie dotyczy”
z powodem. Odpowiedź proxy, także 403, oznacza „przeszło”; oczekujemy braku połączenia **i braku wpisu
z próbnego IP w dzienniku proxy na hoście**.

`/proc/1/environ` w pudełku jest środowiskiem jego init. O niewidoczności init hosta świadczy porównanie
przestrzeni PID z wartością dostarczoną przez CLI, a nie samo powodzenie/niepowodzenie `cat`.
Próba zapisu poza dozwolonymi katalogami używa istniejącego `/etc` w pudełku; nie dowodzi ochrony całego
systemu plików, np. tymczasowego `/tmp` pudełka. Brak `origin` oznacza pominięcie próby push, nie odmowę.

`cat ~/.claude/.credentials.json` trzeba dodatkowo wykonać narzędziem **Bash rzeczywistego agenta**:
P-2 i brak Dockera bez K3 → oczekiwana odmowa piaskownicy; Docker bez K3 → „ryzyko resztkowe K1”.
Zwykły shell uruchomiony przez CLI nie potwierdza działania wewnętrznej piaskownicy agenta.

Końcowy dowód PT-191 obejmuje kartę testową plasma-tide z zatwierdzonym przez użytkownika planem: jeden ticket
wykonuje `make test` w pudełku i zapisuje `done` bez `envAnswer`/`outside`. Do notki muszą trafić:
ID karty i ticketu, wpis runnera z `procs`/dziennika, czas kroku, wynik Bash agenta, tabela czerwonej listy,
środowisko CLI oraz kontrola argv procesów na hoście. Do czasu zebrania tych wyników próba ręczna pozostaje
niezaliczona. Skrypt `scripts/box/boundary-trial.py` zbiera część CLI/Incus i pełne testy poza piaskownicą;
nie tworzy ani nie zatwierdza za użytkownika karty testowej.

Pierwsza runda PT-191 (6.11.2026, `pt191-20261106-055810`): `make test` wykonało 4149 testów w 227 modułach
(4 pominięte, 332,94 s) z jednym błędem `JSONDecodeError` w `test_continue_without_commands_option`.
Deterministyczna regresja potwierdziła odczyt niepełnego `fake-call.json` z atrapy; naprawa publikuje go
atomowo po zakończeniu zapisu. Testy CLI i czerwonej listy w tej rundzie były zielone, ale preflight wykazał
`exists=false` dla pudełka `plasma-tide`, więc **próby na prawdziwym pudełku nie zostały wykonane**.
Logi: `docs/box-prototype/pt191-20261106-055810/`. Pełne testy po naprawie wymagają ponowienia.

Przy braku środowiska: `python3 scripts/box/boundary-trial.py --prepare-missing` zachowuje istniejące pudełko
i profil; buduje tylko brakujące pudełko, a przy braku profilu zatwierdza jawny profil testowy
`docs/agents/pt-191/plasma-tide-proof.box.yaml` (python-qt, porty, narzędzia prób i domena npm).
Pakiety trafiają do pudełka, bez instalowania czegokolwiek na hoście. Profil i pudełko pozostają do dowodów.
To przygotowanie środowiska nie zatwierdza planu rzeczywistej karty testowej.

Druga runda PT-191 (`pt191-20261106-061234`): profil próbny został zatwierdzony. Budowa dotarła do migawki clean,
ale Incus odmówił: `Project "user-1000" doesn't allow for snapshot creation`. Pudełko pozostało w stanie
`błąd budowy`, z poprawnym filtrowaniem NIC i zgodnym hashem; **prób środowiska/argv i czerwonej listy nie wykonano**.
`make test`: 4156 testów, 229 modułów, 4 pominięte, 318,59 s; poprzedni błąd publikacji JSON-a zniknął,
pozostał jeden błąd ręcznego odesłania karty do poprawy podczas domknięcia. Naprawa `fold` zachowuje ręczne todo
po późnym wyniku starszego domknięcia; test regresyjny odtwarza kolejność z dziennika, bez zmiany pierwotnego
testu integracyjnego. Kontrole lokalne po poprawce: 100 testów OK; pełne testy pozostają do ponowienia.

Następne polecenie: `python3 scripts/box/boundary-trial.py --enable-snapshots --retry-failed-build`.
Pierwsza flaga jawnie i trwale ustawia w skonfigurowanym projekcie Incusa `restricted.snapshots=allow`
przez sudo, zgodnie z instalatorem PT-182, i sprawdza wynik. Nie zmienia innych uprawnień ani pakietów hosta.
Druga ponawia przez builder tylko nieudaną budowę z hashem zgodnym z profilem i bez kroków; profil nie jest
zastępowany. Pozostałe istniejące pudełka są zachowane. Potem wykonywane są pełne testy i próby granicy.

Trzecia runda (`pt191-20261106-062856`) została przerwana przez limit czasu na `sudo -A`.
Odczyt `restricted.snapshots` zwrócił pustą wartość; brak wyniku zmiany i odczytu po niej, więc stan
po przerwaniu pozostaje niepotwierdzony. Nie doszło do budowy, pełnych testów ani prób granicy.
Skrypt używa teraz wyłącznie `sudo -n`, bez okna hasła, i ponownie odczytuje politykę przed zmianą.
Przy braku uwierzytelnienia podaje konkretne polecenie administratora do wykonania w terminalu
(`sudo incus project set user-1000 restricted.snapshots=allow` dla projektu tej próby), pomija budowę
i kontynuuje pełne testy. Ich wynik można zebrać także wtedy, gdy środowisko Incusa nadal jest zablokowane.
Nie należy ponawiać identycznej operacji oczekującej na hasło w kroku w tle.

Czwarta runda (`pt191-20261106-102554`): polityka migawek była już `allow`, więc sudo nie było potrzebne.
Ponowienie budowy zakończyło się poprawnie w 67,3 s; status potwierdza `clean`, zgodne hashe i filtrowanie NIC.
Pełne `make test`: 4166 testów, 230 modułów, 4 pominięte, 516,42 s, dwa błędy w module `/Tasks`.
Poprzednie regresje publikacji JSON-a i ręcznego odesłania do poprawy przeszły; w tej rundzie test czytał
dziennik programu wcześniej niż program go zapisał. Scenariusze czekają teraz na rzeczywiste wywołania
zamiast utożsamiać przejęcie zadania z wykonaniem programu.

CLI środowiska i `make box-check` zakończyły się kodem 2 przed zapisem `step.log`, więc w tej rundzie
**nie ma dowodu środowiska ani tabeli odmów**. Źródła ujawniły niezgodne kontrakty opakowania w obrazie
i runnerze. Runner dostarcza teraz własne opakowanie do `in/bin/tide-step` każdego kroku przez chroniony
`StepDir`; domyślne argv używa `/bin/sh <krok>/in/bin/tide-step`. Nie zmienia to obrazu, profilu,
wyboru runnera w silniku ani szablonów stosów. Testy CLI już nie zastępują tej domyślnej drogi.
Potwierdzenie poprawki na rzeczywistym Incusie wymaga kolejnej rundy:
`python3 scripts/box/boundary-trial.py` — bez opcji przygotowania i zmiany polityki, bo pudełko jest gotowe.
Dodatkowa lokalna kontrola przebiegu ujawniła też przedwczesny odczyt rezerwacji w teście zatrzymania po
restarcie: `step.finished` poprzedza zwolnienie pudełka. Test czeka teraz na zakończenie zwalniania
z ograniczonym czasem; nie zmienia to kodu produkcyjnego zatrzymywania.

Piąta runda (`pt191-20261106-111049`) potwierdziła środowisko CLI (6,344 s): proxy, pełne
`NO_PROXY=localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal,10.173.184.1`, port 49100,
brak tokenów agentów, tryb `in/env` 0600 oraz brak klucza proxy i portu w argv hosta.
W zapisanym `env.stdout.log` klucz tymczasowego proxy zastąpiono `<revoked>`; jego brak w argv został
sprawdzony przed redakcją. Opakowanie kroku z runnera zadziałało na rzeczywistym pudełku.

Czerwona lista: 0 „przeszło”, 3 „nie dotyczy” (brak klienta Docker, brak sprawdzonego wolnego IP,
brak próby rzeczywistego Bash agenta). Pełna tabela jest w `box-check.stdout.log`. Całe box-check
pozostaje **niezaliczone**: strażnik refów cofnął równoległe zapisy planów innych kart. Przyczyna,
reflogi i OID-y: [incydent strażnika](agents/pt-191/refguard-incident.md). Zainstalowany zapis planu nie ma
osłony z PT-170, obecnej w tej gałęzi. Skrypt blokuje teraz próby CLI na współdzielonym repozytorium
przy tej niezgodności. Kontrola pliku nie potwierdza wersji kodu już załadowanego w procesie.

Pełne testy: 4168 testów, 230 modułów, 4 pominięte, 456,58 s; jeden błąd testu pustej kolejki lokalnej.
Kolumna review pojawia się przed końcem kroku domknięcia; test czeka teraz na `card.closed` obu kart
przed zatrzymaniem monitora i sprawdzeniem kolejki. Pełne `make test` po poprawce wymaga osobnego outside,
które nie uruchamia CLI ani strażnika refów na repozytorium użytkownika.

Niezależne `make test` po piątej rundzie: 4172 testy, 230 modułów, 4 pominięte, 495,73 s.
Log: [pełne testy po piątej rundzie](box-prototype/pt191-make-test-resume-9.log). Test kolejki po oczekiwaniu
na domknięcia przeszedł. Pozostały dwa błędy testów: liczenie kroku close jako kolejnego wykonania
po odrzuceniu ticketu oraz mediana statusline 152,29 ms przy 14 równoległych modułach (próg 150 ms).
Pierwszy test pomija teraz close w porównaniu liczby kroków, zachowując asercje odrzucenia i braku scalenia.
Moduł statusline korzysta z istniejącego trybu szeregowego po wszystkich równoległych modułach;
próg 150 ms i liczba pomiarów pozostają bez zmian. Lokalnie 14 testów statusline i harmonogramu runnera
przechodzi. Kolejne outside dotyczy wyłącznie `make test`; próby na wspólnym `.git` pozostają zablokowane
po incydencie strażnika, a pierwszy rzeczywisty krok agenta nadal wymaga zatwierdzonej karty testowej.

Drugie niezależne `make test`: 4172 testy, 230 modułów, 4 pominięte, 494,59 s; review i statusline
po poprawkach przeszły. [Pełny log](box-prototype/pt191-make-test-resume-10.log) zawiera jeden błąd
testu odwracania animacji tła blokady: stały timer odczytał już zakończone przenikanie.
Kontrolowane opóźnienie próbkowania o 400 ms odtworzyło dokładnie błąd `1 not less than 1`.
Harness odwraca teraz animację na sygnale zmiany krycia, przy wartości między 0,25 a 0,75,
z zachowaniem asercji ciągłości. Kod produkcyjny animacji pozostaje bez zmian. 18 testów modułu
przechodzi lokalnie; pełne testy po poprawce wymagają outside. Stan dowodów Incusa nie zmienił się.

Trzecie niezależne `make test` po piątej rundzie zakończyło się **kodem 0**: 4172 testy,
230 modułów, 4 pominięte, 382,65 s. [Pełny zielony przebieg](box-prototype/pt191-make-test-resume-11.log)
potwierdza wcześniejsze poprawki, w tym próbę odwracania animacji. Kryteria automatyczne PT-191 są zaliczone.
Nie ponawiano prób Incusa: ponowny odczyt zainstalowanego `commit_plan` nadal pokazuje brak osłony
`host_operation`. Pozostają końcowy box-check i rzeczywisty krok agenta. Przygotowana
[propozycja planu karty testowej](agents/pt-191/first-step-proposal.md) została przekazana do zatwierdzenia;
samo zatwierdzenie nie jest dowodem wykonania i nie usuwa blokady po incydencie strażnika.

Decyzja użytkownika z 6.11.2026 (wznowienie PT-191 nr 13): plan `first-step-proposal.md` jest
**zatwierdzony**, a rzeczywista próba na wspólnym repozytorium **odroczona** do czasu, gdy
zainstalowany Tide zapisuje plany przez `host_operation`, po scaleniu K-38. Implementacja trafia do
review; końcowe kryteria ręczne pozostają niezaliczone. Nie wykonano kolejnej próby.

Ustalenie koordynatora do review K-38: strażnik ma odtwarzać wyłącznie refy kroku. **Nie może
przywracać refów innych kart ani gałęzi spoza zakresu kroku/pudełka.** Incydent K-55/K-60 z 11:18
jest dowodem ryzyka do rozwiązania w K-38. Zgodność wersji hosta nie zastępuje ograniczenia zakresu
odtwarzania. Wymaganie, reflogi i scenariusze regresji opisano w
[notce incydentu](agents/pt-191/refguard-incident.md); w tym wznowieniu nie zmieniano strażnika.
