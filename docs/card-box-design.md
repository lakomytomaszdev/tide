# Pudełko karty: odizolowane środowisko projektu bez pytań o zgody (K-38)

Analiza i projekt do grillingu. Stan na 03.11.2026, gałąź `fix/step-output-dir-writable`. Dokument rozwija
`docs/sandbox-and-permissions.md` (model P1/P2/P3 zostaje planem B) zgodnie z kierunkiem zatwierdzonym na karcie
K-38: zamiast coraz drobniejszych zgód — zamknięte środowisko, w którym agent może wszystko, a granica jest na wyjściu.

Oznaczenia: **[sprawdzone]** — wynik z tego komputera (03.11.2026); **[założenie]** — do potwierdzenia prototypem
albo w rozmowie; **[decyzja]** — pytanie do użytkownika (zebrane w sekcji 9).

## Kontrakt granicy projektu v2 — PT-264 (07.11.2026)

Ta sekcja zastępuje historyczne założenie „całe `.git` rw + nakładki ro” w dalszej
części projektu. Zmianę zakresu zatwierdzono przy wznowieniu PT-264 o 15:33.
Implementacja wymaga odbioru niezależną rodziną modeli; sam opis nie otwiera
bramki PT-261 i nie zastępuje prób PT-273/PT-274.

**Domena zaufania pozostaje projektem.** Wszystkie jego karty mogą czytać wspólne
obiekty, refy, wejścia, worktrees, home i robocze wyniki. Nie ma granicy poufności
między kartami. Hostowe magazyny wyników, werdyktów, zgód i strażnika pozostają
poza urządzeniami. `per-card` nie daje prawa montowania wspólnego rodzica projektów.

**Manifest PT-263.** Nowe propozycje mają `mountContract: 2`. Bazowe `.git` jest
ro, a tylko `objects`, `refs`, `logs` mają osobne źródła rw. Hostowe config,
hooks, info, config.worktree i rejestr administracji worktrees nie znajdują się
w źródle rw obejmującym całą administrację. Workspaces i dedykowany home projektu
są rw; wyłącznie dokładne `PackSnapshot.directory` wejść są ro. Zmiana planu
zmienia odcisk i wymaga nowego podglądu oraz świadomej zgody projektu. Istniejący
rekord nie jest przepisywany na nową treść ani automatycznie rozszerzany.

**Administracja i indeks.** `box/gitview.py`, kopiowany do `in/bin/git`, utrzymuje
prywatny HEAD i indeks w domu pudełka. Korzysta ze wspólnych objects/refs/logs;
wewnętrzne piaskownice obu programów mają jawny zapis do tego prywatnego widoku.
commity nadal są od razu widoczne w projekcie. Host nigdy nie wykonuje programu
ani nie uruchamia Git na administracji wybranej z tego prywatnego katalogu.
Strażnik sprawdza sam HEAD kanonicznego prywatnego widoku przez deskryptory bez
podążania za symlinkami; nie interpretuje jego config ani indeksu. Odłączony HEAD
lub inna gałąź dają `refs.guard`, zachowują obserwowany OID w raporcie i przywracają
przypisanie gałęzi. Działa to także po użyciu `/usr/bin/git` z tym widokiem, bez
zaufania do wrappera. Jest to kontrola stanu kanonicznego widoku, nie dowód wszystkich
poleceń roota w dowolnej administracji. Plik `.git`
w zapisywalnym worktree jest niezaufaną wskazówką: host sprawdza jego zgodność
z rejestrem pod bazowym montowaniem ro i przypina własne `--git-dir`/`--work-tree`.
Podmiana nie wybiera obcego projektu. Nie polegamy na nakładce pojedynczego pliku
w zapisywalnym źródle ani na maskowaniu tego pliku przed zwykłym użytkownikiem.

Inicjalizacja `init`/`init-db`/`clone` w pudełku nie korzysta z widoku istniejącego checkoutu.
Wrapper usuwa odziedziczone zmienne `GIT_*` poza tożsamością także na ścieżce
zwykłego repozytorium i uruchamia te polecenia bez własnego `GIT_DIR`,
`GIT_COMMON_DIR` i `GIT_WORK_TREE`. Prywatny hostowy widok strażnika dopuszcza tylko
`config`, `for-each-ref`, `check-ref-format` i `update-ref`. Odmawia pozostałych
poleceń, także inicjalizacji i aliasów, przed wykonaniem Git: służy istniejącemu
magazynowi, a inicjalizacja mogłaby przez `commondir` przepisać wspólny config. Zwykła hostowa inicjalizacja
oddzielnego repozytorium zachowuje zamknięte środowisko i granicę procesu.
To poprawka wykonania kontraktu v2, bez zmiany manifestu i uprawnień montowań.

Przed ekspozycją host zapisuje w chronionej administracji `tide-boundary` i punkty
`tide-index-tip`. Po przesunięciu refu przez prywatny widok host odtwarza swój
indeks z nowego HEAD przed operacją wymagającą indeksu. Nie kopiuje indeksu z domu
pudełka. Zmiany robocze pozostają na dysku. Ten mechanizm dotyczy worktrees
projektu prowadzonych przez Tide; główny indeks użytkownika nie jest synchronizowany.
Karty nadal podlegają serializacji PT-266. Równoległa ręczna praca na indeksie tego
samego worktree nie jest osobną domeną ani obsługiwanym sposobem zachowania stagingu.

**Hostowe operacje Git na danych pudełka.** Każde hostowe wywołanie Git przechodzi
przez `gitcommand` (FlowGit, skaner, Tablica, opiekun, przygotowanie źródeł,
ponowienia testów, StepGit). Granica — izolowany interpreter `git_sandbox.py` —
jest włączona, gdy (1) usługa ma `box.enabled` (plik ustawień czytany ponownie po
każdej zmianie, bez restartu), (2) wywołanie dotyczy repozytorium oznaczonego
`<common-dir>/tide-boundary` — znacznik zapisuje `initialize_indexes` przed pierwszym
wystawieniem repozytorium pudełku i zostaje także po wyłączeniu pudełka — albo
(3) wywołuje ją ścieżka pudełkowa (`box/*`, strażnik refów, migawka checkoutu w trybie
granicy). Niepewny odczyt (błąd stat ustawień, nieczytelny wskaźnik `.git`
lub `commondir`) wybiera granicę. W pozostałych przypadkach — pudełko wyłączone
i repozytorium nigdy niewystawione — argv i otoczenie są takie jak przed K-38:
`git -c core.hooksPath=/dev/null -c core.fsmonitor=false …` bez drugiego interpretera
(decyzja właściciela 08.11, pomiar ~4× sumy czasów modułów przy granicy bezwarunkowej;
[decyzje PT-264](agents/pt-264/decisions.md#granica-gita-warunkowa)). Osobny rootfs bwrap zawiera tylko
program systemowy, jego biblioteki/dane ro oraz jawne korzenie projektu.
Nie ma hostowego `/tmp`, `/run`, procesów, IPC ani sieci. To ogranicza także
zmiany metadanych przez chmod/utime i gniazda ścieżkowe; sama polityka plików
Landlock nie wystarcza do ich ochrony. Landlock ABI co najmniej 6
ogranicza pliki do jawnego repozytorium/workspaces i jawnych plików wejścia/wyjścia
wybranych przez usługę. Rozstrzyga dostęp także po późniejszej podmianie symlinka.
Nie przyznaje wykonania plików projektu, powłoki, filtrów ani pomocników z config;
wykonuje wyłącznie systemowy Git i jego ten sam program w git-core. Nie przyznaje
połączeń TCP ani wyjścia z zakresu IPC. Biblioteki i dane systemowego programu są
ro; konfiguracja systemowa/globalna, lazy fetch i zmienne Git z otoczenia są
wyłączone. Zachowane są tylko user.name/email hosta: osobne zabezpieczone
`git config --no-includes --file` odczytuje te dwa pola, a operacja projektu
dostaje wygenerowany plik tożsamości. Oryginalny config hosta nie jest przy niej
montowany; lokalna i jawna tożsamość mają pierwszeństwo. Brak bwrap lub wymaganego
ABI oznacza odmowę bez cichego powrotu do starego Git.

Odczyty pojedynczej migawki checkoutu mogą współdzielić jeden start bwrap.
Zaufany interpreter wykonuje stałą listę odczytów HEAD, gałęzi, statusu, indeksu
i administracji; każdy proces Git dostaje tę samą politykę Landlock i przypięte
gitdir/worktree. To nie jest interfejs dowolnego batch argv ani trwały worker.
Wyniki mają sprawdzany pełny format, a błąd dowolnego wymaganego odczytu odmawia
migawki. Każda następna migawka ponawia odczyty i walidację granicy; nie ma cache
zmiennego stanu między podglądem, zgodą i wykonaniem. Odciski plików, sprawdzanie
repozytorium źródłowego oraz porównania kolejnych faz pozostają obowiązkowe.
Migawka nadal jest odczytem wieloetapowym, a nie atomową transakcją Git.
Plan mountów i uprawnienia PT-263/PT-266 nie są przez to rozszerzane.

**Koszt Git w v1 — decyzja właściciela 08.11.2026.** Przyjmujemy próg
**1,6 zamiast 1,05** dla ilorazu średnich enabled/BASE. Native40 w kolejności
B/E/E/B dał `37.12540199293289 / 54.42815118690487 / 54.10217254597228 /
33.05383698607329` s, po 31 tych samych testów OK bez pominięć. Średnie to
`35.08961948950309` i `54.265161866438575` s: **1,5464733632313044 (+54,65%)**.
Enabled był prototypem trwałego workera w jednorazowych archiwach, pozostawionym
w out kroku 40; nie został włączony do kodu gałęzi. Ten pomiar nie potwierdza
takiego samego kosztu dostarczonej implementacji. Właściciel odrzucił przebudowę
w C i polecił pozostawić bezpieczeństwo oraz mechanizmy produktu bez zmian.

Ryzyko resztkowe to dłuższe operacje i podatność na obciążenie; rozrzut BASE
wyniósł 12,318%, a dwa z czterech surowych ilorazów przekroczyły 1,6. Akceptacja
średniej nie jest gwarancją opóźnienia każdej operacji. Zawężenie granicy do danych
pudełka weszło do PT-264 (powyżej); etap 2 / K-75 może rozważyć dalsze zawężenie
per repozytorium przy włączonym pudełku albo rdzeń natywny. Pełny pomiar, hash źródła i zakres decyzji:
[decyzje PT-264](agents/pt-264/decisions.md#asked-akceptacja-narzutu-git-w-v1-bez-przebudowy-w-c).
Historyczny kod 1 native40 według progu 1,05 i wcześniejsze porażki funkcjonalne
pozostają zapisane. Wymagane są aktualny `test-changed`, niezależne review,
nowa zgoda projektu przy zmianie manifestu i dalsze utrzymanie zamkniętej bramki prób.

Alternates/http-alternates i przekierowania commondir są odrzucane przed startem.
Próba wyścigu na magazynie Git nadal ma ograniczenia jądra przy otwarciu pliku;
sama wcześniejsza walidacja ścieżki nie jest jedyną granicą. Prywatny widok
`guardgit` zachowuje swój algorytm własnego refu i CAS, ale też używa tej granicy.
Zatwierdzone argv usługi są zaufanym opisem jej operacji, nie dowolnym poleceniem
przyjmowanym od pudełka. Jawny plik archiwum/indeksu nie upoważnia config do
wybrania kolejnego celu. Kod zainstalowanej usługi musi być poza źródłami rw.

Hostowe tworzenie i usuwanie worktrees zachowuje także używaną przez Planner
konwencję `<repo>/.claude/worktrees`; to nadal wnętrze jawnego projektu. Przy
`worktree list` istniejący checkout poza typowym kontenerem nie wymaga montowania
jego katalogu ani rodzica: granica udostępnia tylko jego sprawdzony wskaźnik `.git`
ro, ze zgodnym common-dir i hostową administracją. Nie zmienia to planu urządzeń pudełka.

**Urządzenia PT-264.** Kolejność to paczki → zgoda → urządzenia. Blokada zgody
szereguje również operacje na urządzeniach. Adapter porównuje kompletny efektywny
zestaw urządzeń, także dziedziczonych, a nie tylko ich nazwy. Odrzuca dodatkowe
dyski, urządzenia/gniazda, surowe konfiguracje i uprzywilejowany kontener. Zmiana
montowań wymaga braku żywego kroku i zatrzymania całego pudełka, także Dockera.
Przed podłączeniem źródeł odrzucane są hardlinki i pliki specjalne; symlinki
zwykłych danych rozwiązują się w przestrzeni pudełka. Nieznany mount pozostaje
niezmieniony i blokuje start. Callback `detach_snapshot` odłącza wyłącznie własne
udowodnione urządzenia we wszystkich instancjach projektu przed usunięciem danych
PT-262; nie resetuje innych kart. Reset projektu odmawia także przy żywej kopii karty.

**Wpływ na gotowe tickety.** PT-263 zachowuje wersjonowany magazyn zgód, lecz
wyprowadza nowy plan i odcisk. PT-266 zachowuje serializację, własny ref i CAS;
jego prywatny Git dostaje dodatkową granicę procesu. PT-169 ma nowy adapter v1,
nie używa starej ścieżki montowań dla plasma-tide. PT-262 nadal wyłącznie kopiuje,
skanuje i usuwa dane po callbacku. PT-268/PT-269 muszą konsumować dokładną nową
zgodę; nie wolno aktywować ich dawnymi profilami kart. Integracja wyboru kroku
i polityki projektu pozostaje w PT-265/PT-269. Wykonana historyczna część K-38,
WIP i wyniki review nie są przepisywane.

Automatyczne dowody są na jednorazowych repozytoriach i atrapie urządzeń.
Nie oznaczają zaliczenia root/Dockera na prawdziwym Incusie. Ten odbiór wymaga
zarejestrowanego celu PT-273 i bramki PT-274; nie używa `.git` użytkownika.

## 1. Cel i kryteria sukcesu

**Cel.** Krok w tle (ticket, review, poprawka, planowanie) pracuje w pudełku projektu: instaluje paczki, stawia
serwery, uruchamia Docker/Testcontainers, przeglądarki i pełne testy — bez jednego pytania o środowisko. Pudełko nie
widzi katalogu domowego użytkownika, jego Dockera, D-Bus, kluczy ani produkcji. Jedyne wyjście to commity na gałęzi
ticketu we wspólnym `.git` i pliki wyniku kroku; push, MR i wdrożenie robi Tide na polecenie użytkownika (K-36).

**Kryteria sukcesu (mierzalne).**

| # | Kryterium | Miara | Jak sprawdzić |
|---|---|---|---|
| S1 | Zero pytań o środowisko | 0 zdarzeń `step.decision` z `envAnswer` i 0 wyników `outside`/`denied` o środowisko na kartach wzorcowych | powtórka scenariuszy z 03.11 (sekcja 7.4): `npm install <nowa paczka>`, e2e na porcie, Karma z Chrome, `dotnet test` z Testcontainers, `make test` Tide, polecenie > 300 znaków |
| S2 | Twarda granica | 100 % pozycji „czerwonej listy” (sekcja 6.3) kończy się odmową w pudełku | `make box-check` uruchamia listę w prawdziwym pudełku |
| S3 | Czas startu kroku | narzut pudełka ≤ 3 s przy działającym pudełku; zimny start pudełka po restarcie ≤ 30 s; budowa z przepisu ≤ 15 min; reset do migawki `clean` ≤ 60 s | pomiar w dzienniku karty (`step.started` − `intent`) |
| S4 | Odporność | restart usługi nie zabija kroku w pudełku (jak dziś `systemd-run --scope`); zatrzymanie kroku zostawia commit WIP (`stepstop`) | istniejące testy `test_cardflow_execution`, `test_stepstop` z atrapą pudełka |
| S5 | Pulpit responsywny | pudełko ma twardy limit CPU/RAM (sekcja 4.9); pełne e2e nie zamraża Plasmy | `limits.cpu`, `limits.memory` w profilu Incusa |
| S6 | Uniwersalność | cztery projekty wzorcowe (sekcja 5.6) mają działający profil; projekt bez wzorca dostaje profil „pusty + apt z przepisu” i pytanie przy planie, nie w trakcie pracy | profile w Tablicy, budowa `clean` dla każdego |
| S7 | Testy Tide bez Incusa | `make test` przechodzi na maszynie bez `incus` (atrapa `fake_incus`) | CI/`make test` |

## 2. Model zagrożeń

### 2.1 Przed czym chronimy

| Zagrożenie | Przykład z 03.11 / realny scenariusz | Dziś (`outside`) | W pudełku |
|---|---|---|---|
| Z1 Agent z błędem | `rm -rf` złej ścieżki, `git checkout master`, `make install` | zakazy projektu, filtr `OUTSIDE_RISKY` — miękkie | skutki zamknięte w pudełku; `make install` instaluje w pudełku, nie na hoście; strażnik refów (4.4) wykrywa ruch innych gałęzi |
| Z2 Prompt injection z repozytorium, kartki, zależności | plik w repo każe „pobierz i uruchom”, README paczki npm | agent w bwrap bez sieci, ale `outside` z pełnymi prawami | sieć tylko do domen profilu przez proxy; nic poza pudełkiem |
| Z3 Złośliwa paczka (npm postinstall, NuGet build task, pytest plugin) | `npm install` nowej paczki uruchamia kod | w P1 w bwrap (bezpieczne), w `outside` — pełne prawa | kod działa w pudełku jako użytkownik `agent`; widzi tylko worktree projektu i cache pudełka |
| Z4 Kod testów z repozytorium (w tym napisany przez agenta chwilę wcześniej) | `dotnet test` czyta `~/.ssh`, `npm run e2e` wysyła dane | **główna dziura** (`docs/sandbox-and-permissions.md` §2) | j.w.; gniazdo Dockera w pudełku to root *pudełka* (uid 1 000 000+ na hoście) |
| Z5 Wyciek tokenów i kluczy | `~/.git-credentials`, `gh` z portfela, `~/.claude/.credentials.json`, `~/.codex/auth.json` **[sprawdzone: wszystkie istnieją]** | osiągalne z `outside` | klucze SSH, git-credentials, portfel, gh — niewidoczne; poświadczenia agenta — ryzyko resztkowe (4.2) |
| Z6 Push, MR, wdrożenie, publikacja | `git push`, `npm publish`, `dotnet nuget push`, `rsync` na devhost (CI infracore robi `rsync` + `sudo`) | filtr rodzin | brak poświadczeń i brak sieci do `github.com`/`git.firma.example`/`10.20.3.10`, chyba że profil jawnie pozwoli (nie pozwala domyślnie) |
| Z7 Zniszczenie danych użytkownika | `rm` w `~`, `docker system prune` kasujący `youtrack`, `legal-agents-postgres` **[sprawdzone: działają na Dockerze użytkownika]** | `OUTSIDE_RISKY` nie łapie kodu testów | host `~` i Docker użytkownika niewidoczne; pudełko ma własny Docker |
| Z8 Zużycie zasobów | pięć e2e naraz, `ng serve` ×3, testy Tide z limitami czasu padające pod obciążeniem | `CPUWeight=40`, kolejka 1/projekt, 2 łącznie | twardy limit pudełka + kolejka ciężkich poleceń w pudełku; porty per pudełko znikają jako konflikt między projektami |
| Z9 Zatrucie wspólnego `.git` | agent przestawia `refs/heads/master`, podmienia `gitdir` | `resolve_git_dir` przed krokiem; Codex pisze tylko `objects/refs/logs` | jak dziś + strażnik refów po kroku (4.4) |

### 2.2 Poza zakresem

- Ataki na jądro z wnętrza kontenera (ucieczka przez lukę jądra). Kontener systemowy dzieli jądro z hostem;
  obrona: użytkownik niepuprzywilejowany + AppArmor + seccomp Incusa. Projekt, któremu nie ufamy nawet na tyle,
  dostaje `type: vm` w profilu (sekcja 3) — to opcja, nie domyślność.
- Modele i dostawcy (Anthropic, OpenAI) jako przeciwnik; treści wysyłane do modelu (kod projektu) — jak dziś.
- Ochrona przed użytkownikiem (celowe wyłączenie pudełka w ustawieniach jest decyzją użytkownika).
- Produkcyjne wdrożenia, CI firmowe — nie dotykamy; granica to „nic nie wychodzi bez Tide i kliknięcia”.
- Ekran i kompozytor Plasmy/KWin (ekran blokady, SDDM, skrypty KWin): pudełko nie uruchomi prawdziwej Plasmy;
  te testy zostają na hoście jako jawne działanie użytkownika (tryby testowe z `CLAUDE.md`).

## 3. Porównanie opcji

### 3.1 Stan hosta [sprawdzone 03.11.2026]

- KDE neon User Edition (Ubuntu 24.04), jądro `7.0.0-38-generic`, 16 rdzeni, 78 GiB RAM, `/` 51 GB wolne (ext4),
  `/home` 220 GB wolne (ext4). KVM dostępny: `/dev/kvm` ma ACL `user:tomek:rw-`.
- cgroup v2 (`cgroup2fs`, kontrolery `cpuset cpu io memory pids …`); `/etc/subuid`/`subgid`: `tomek:100000:65536`.
- AppArmor 4.0.1, `kernel.apparmor_restrict_unprivileged_userns = 1`. Profile z `userns` i `flags=(unconfined)`:
  `/etc/apparmor.d/bwrap` (zainstalowany przez Tide, PT-75), `/etc/apparmor.d/podman` (z pakietu `apparmor`),
  `lxc-usernsexec`, `unprivileged_userns`.
- Zainstalowane: Docker CE 29.9.2 (`/var/run/docker.sock` root:docker, użytkownik w grupie `docker` = root na hoście;
  `data-root=/home/docker-data`; działają kontenery `youtrack`, `legal-agents-postgres`, `cockpit-db`, …),
  `docker-ce-rootless-extras` (jest `dockerd-rootless-setuptool.sh`), `bubblewrap 0.9.0`, `uidmap`, `slirp4netns`,
  `qemu-system-x86 8.2.2` + `ovmf`. Nie ma: `incus`, `lxd`, `podman`, `systemd-nspawn`, `firejail`, `xvfb`, `weston`.
- Symulacje apt (żadna nie dotyka Qt ani KDE Frameworks):
  - `apt-get -s install incus` → 9 pakietów: `incus 6.0.0-1ubuntu0.3`, `incus-client`, `incus-agent`, `liblxc1t64`,
    `liblxc-common`, `lxcfs`, `libcowsql0`, `libraft0`, `attr`.
  - `apt-get -s install podman fuse-overlayfs crun passt` → 11 pakietów, `podman 4.9.3`.
  - `apt-get -s install xvfb` → 1 pakiet (na hoście niepotrzebny, jeśli pudełko ma własny).
- Agenci: Claude Code 2.1.317 (`~/.local/bin/claude` → `~/.local/share/claude/versions/…`), Codex 0.114.0 ze snapa
  z wtyczkami `home`, `network`, `network-bind`, `desktop`. Node tylko z nvm (`v20.19.6`), .NET SDK 10.0.113
  w `/usr/lib/dotnet`, Playwright: `~/.cache/ms-playwright/chromium-1193…1248`. Ollama aktywna na `127.0.0.1:11434`.
- Zajęte porty istotne dla kroków: `4300` (ktoś ma `ng serve`), `5432` (Postgres użytkownika na `0.0.0.0`).

### 3.2 Kryteria i ocena

Skala: ++ bardzo dobrze, + dobrze, 0 neutralnie, − słabo, −− dyskwalifikuje.

| Kryterium | A. Incus kontener systemowy (unprivileged, nesting) | B. Podman bezrootowy (kontener na projekt/krok) | C. Lekka VM (Incus VM / QEMU; Firecracker) | D. Osobny użytkownik + bwrap + rootless Docker | E. Dziś: P1/P2/P3 (bwrap) |
|---|---|---|---|---|---|
| Izolacja plików hosta | ++ (własny rootfs, idmap) | ++ | ++ | + (ten sam fs, prawa uniksowe) | − (`outside` = wszystko) |
| Izolacja jądra/roota hosta | + (userns, AppArmor, seccomp; daemon Incusa to root) | + (bez daemona roota) | ++ | + | − |
| Docker / Testcontainers w środku | + (`security.nesting=true`, udokumentowane „Docker inside Incus”) **[założenie: prototyp P-1]** | − (Docker w rootless Podman: nested userns + fuse-overlayfs, kruche; Podman-in-Podman lepiej, ale Testcontainers .NET chce Docker API — socket Podmana działa) | ++ | + (rootless `dockerd` osobnego użytkownika; Testcontainers przez `DOCKER_HOST`) | −− |
| GUI/QML offscreen, Chrome headless | + (apt w pudełku, bez ograniczeń Qt hosta; `xvfb`/`weston --headless` w obrazie) | + | + | − (biblioteki muszą być na hoście — zasada „apt nie podnosi Qt”) | 0 (dziś działa, bo biblioteki są na hoście) |
| Wydajność, dysk | + (procesy natywne; bind-mount worktree = ten sam ext4; obraz ~2–6 GB + cache) | + | − (RAM zarezerwowany, virtiofs/9p dla worktree wolniejsze, obraz VM) | ++ | ++ |
| Czas startu kroku | ++ (pudełko trwa; `incus exec` ~0,1 s) | + | 0 (VM 5–15 s, migawki pamięci pomagają) | ++ | ++ |
| Instalacja na KDE neon | + (apt z universe, 9 pakietów, `incus admin init`, grupa) | + (apt, 11 pakietów; `apparmor` ma profil `podman`) | 0 (Incus VM: te same pakiety + `qemu` już jest; Firecracker: brak pakietu apt, własne jądro) | 0 (useradd, subuid, rootless Docker setup, profile AppArmor dla drugiego użytkownika) | ++ |
| Utrzymanie | + (jedno narzędzie: kontener i VM, migawki, limity, sieć) | 0 (obrazy OCI, Containerfile; brak „systemu” w środku bez `--systemd`) | 0 | − (ręczna składanka) | 0 |
| Migawki / reset | ++ (`incus snapshot`, `restore`, `copy`; btrfs/ZFS CoW) | 0 (`podman commit`, warstwy) | ++ | −− (brak) | −− |
| Praca offline | + (obraz lokalny, cache paczek w pudełku) | + | + | + | + |
| Sieć: lista domen | + (nftables na hoście + proxy Tide; ACL sieci Incusa) | 0 (pasta/slirp w userns — filtr tylko przez proxy albo uid) | + | 0 (uid-based nftables + proxy) | + (Claude Code `allowedDomains`), −− dla `outside` |
| Równoległość kroków | ++ (procesy w jednym pudełku, `systemd --user` w środku) | + | ++ | + | + |
| Ryzyko „root-equivalent” dla usługi | − (grupa `incus-admin` = root; łagodzi projekt ograniczony przez `incus-user`/grupa `incus`) **[założenie: P-5]** | ++ | − (jak A) | 0 (sudo tylko przy instalacji) | ++ |

### 3.3 Rekomendacja

**A. Incus, kontener systemowy, jeden na projekt** — z `type: vm` jako opcją w profilu dla repozytoriów
o niższym zaufaniu (ten sam CLI, ten sam przepis, Incus 6.0 obsługuje oba; QEMU i OVMF już są na hoście).

Uzasadnienie:
1. To jedyna opcja, która łączy Docker w środku, własne pakiety systemowe (Qt/PyQt6 do testów Tide bez dotykania
   hosta), migawki do resetu i start kroku bez narzutu. Podman bezrootowy jest bezpieczniejszy dla usługi, ale Docker
   w środku i brak „systemu” (systemd, użytkownik `agent`, lingering) kosztowałyby więcej niż zyskamy.
2. Kontener systemowy zachowuje się jak maszyna: ścieżki identyczne z hostem (4.3), `systemd-run --user --scope` jak
   dziś, `stepstop` bez zmian koncepcji. Zmiana w Tide to głównie „gdzie uruchamiamy argv”, nie nowy model kroku.
3. VM od razu byłaby czystsza, ale worktree przez virtiofs i RAM zarezerwowany na stałe dla każdego projektu
   (4 projekty × 8–16 GiB) to realny koszt na komputerze do pracy. Kontener na start, VM na życzenie.
4. Opcja D (osobny użytkownik + bwrap) to plan B z `docs/sandbox-and-permissions.md`; zostaje jako tryb awaryjny,
   gdy Incusa nie da się postawić (sekcja 10).

Co Incus **nie** daje sam z siebie i co dokłada Tide: profil środowiska i przepis (sekcja 5), proxy wyjściowe z listą
domen (4.5), strażnik refów (4.4), pula portów (4.6), paczki danych (6.2), sprzątanie (4.10).

## 4. Architektura rekomendowana

```
host (KDE neon)                                     pudełko projektu (Incus, unprivileged, nesting)
┌──────────────────────────────────┐                ┌──────────────────────────────────────────────┐
│ tide-daemon                      │ incus exec     │ systemd; użytkownik agent (uid 1000 ↔ host 1000│
│  cardflow/engine._launch ───────────────────────▶ │ systemd-run --user --scope tide-step-<id>    │
│  box/ (builder, runner, proxy)   │                │   claude -p … / codex exec …                 │
│  proxy wyjściowe :3128 ◀──────────── HTTPS_PROXY ─┤   npm, dotnet, playwright, make test          │
│  (lista domen z profilu)         │                │ dockerd pudełka (/var/run/docker.sock)        │
│ nftables: pudełko → tylko proxy, │                │   Testcontainers, pgvector:pg16               │
│   Ollama hosta; reszta DROP      │                │ /home/user/DevWorkspace/<proj>/.git   (rw)   │
│ ~/DevWorkspace/<proj>/.git ─────── disk shift=true─▶ /home/user/DevWorkspace/<proj>-worktrees (rw)│
│ ~/DevWorkspace/<proj>-worktrees ─┘                │ /packs/<karta>  (ro, paczki danych)           │
│ ~/.local/share/tide/box/<slug>/home ── disk ─────▶ /home/agent  (poświadczenia, ~/.claude, cache)  │
└──────────────────────────────────┘                └──────────────────────────────────────────────┘
```

### 4.1 Gdzie biegnie agent — oba warianty

**Wariant A: agent na hoście (bwrap jak dziś), polecenia w pudełku.** Claude Code/Codex zostają w dzisiejszej
piaskownicy z tokenami na hoście; ciężkie polecenia idą do pudełka. Problem: ani Claude Code, ani Codex nie mają
„uruchom Bash gdzie indziej”. Kanał musiałby być pośredni — dziś to wynik `outside` (krok kończy się, Tide uruchamia,
wznawia; każde polecenie = nowy kontekst wznowienia) albo skrypt `tide-box` w PATH piaskownicy, który zapisuje
żądanie do `<krok>/out/.box/` i czeka na odpowiedź usługi (piaskownica blokuje gniazda, więc tylko kolejka plikowa).
Zaleta: poświadczenia agenta nigdy nie opuszczają hosta. Wada: dwa światy (co jest „lekkie”, co „ciężkie”), agent
dalej musi wiedzieć, że jest w piaskownicy; kolejka plikowa to kruche RPC; `npm install` w P1 na hoście i w pudełku
muszą dawać ten sam `node_modules`.

**Wariant B: agent w pudełku (rekomendacja, zgodna z K-38).** Cały krok — program agenta i wszystko, co uruchamia —
działa w pudełku jako użytkownik `agent`. Jeden świat: agent nie wie o piaskownicy, nie pyta, nie zgłasza `outside`.
`handoff.command_policy` i listy poleceń (`board/commands.py`) przestają być granicą — zostają jako **wskazówki**
(zakazy projektu typu `make install` nadal w prompt, ale nie trzeba ich egzekwować; w pudełku `make install` instaluje
w pudełku). Cena: poświadczenia agenta muszą być w pudełku (4.2).

**[decyzja D1]** Rekomendacja: B. Wariant A zostaje udokumentowany jako możliwość powrotu, gdyby D2 okazało się
nieakceptowalne.

### 4.2 Poświadczenia agenta w pudełku i ich ryzyko

Fakty [sprawdzone]: Claude Code trzyma OAuth w `~/.claude/.credentials.json` (token odświeżania — długowieczny,
daje pełny dostęp do konta/subskrypcji); Codex w `~/.codex/auth.json`. Tide dla modeli lokalnych podaje token przez
`apiKeyHelper` w `--settings` (Claude) albo zmienną środowiska z pliku (`secret_env`, `local.py`).

Zagrożenie: kod z repozytorium uruchomiony w pudełku (Z3/Z4) czyta poświadczenia agenta i wysyła je przez sieć.
Skutek najgorszy: ktoś używa subskrypcji Claude/ChatGPT użytkownika — **nie** kluczy SSH, nie produkcji. To
węższe niż dziś (dziś to samo `outside` czyta `~/.claude/.credentials.json` *i* `~/.ssh`).

Opcje:

| Opcja | Jak | Ochrona | Koszt |
|---|---|---|---|
| K1 Osobne logowanie w pudełku | `claude login` / `codex login` raz w obrazie bazowym Tide; `/home/agent` to katalog hosta `~/.local/share/tide/box/<slug>/home` (0700) | poświadczenia czyta tylko `agent`; wewnętrzna piaskownica Claude Code (`denyRead: ["~/.claude"]`) i Codexa dalej włączone **[założenie: P-2 — bwrap/landlock w kontenerze]**; proxy wyjściowe pozwala tylko na domeny profilu, więc wyciek musi iść do `api.anthropic.com`/`registry.npmjs.org`… | najmniejszy; logowanie raz na obraz; odświeżanie tokenów działa samo |
| K2 Token per krok w środowisku | `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` tylko w env procesu agenta | env dziedziczą *wszystkie* polecenia agenta (`/proc/self/environ`) — gorzej niż plik z `denyRead` | mały |
| K3 Proxy wstrzykujące token | pudełko dostaje klucz-atrapę per krok; proxy Tide na hoście podmienia nagłówek na prawdziwy token; `ANTHROPIC_BASE_URL`/`OPENAI_BASE_URL` → proxy | poświadczeń nie ma w pudełku wcale; wyciek atrapy bezwartościowy po kroku | **[założenie: P-3]** nie wiadomo, czy logowanie subskrypcyjne (OAuth) Claude Code i ChatGPT-login Codexa akceptują inny base URL; wymaga własnego proxy HTTPS z MITM dla jednej domeny albo trybu `http` do proxy |

**[decyzja D2]** Rekomendacja: start z K1 (działa na pewno, lepiej niż dziś), prototyp K3 jako cel; K2 odrzucić.
Konsekwencja K1: w pakiecie zgód jest punkt „pudełko ma własne logowanie Claude/Codex; w razie podejrzenia wycieku —
`claude logout` w obrazie i ponowne logowanie”.

Modele lokalne (Ollama na hoście `127.0.0.1:11434`): pudełko widzi hosta przez bramę mostka Incusa (np.
`10.x.x.1`); Ollama słucha tylko na loopbacku → albo `OLLAMA_HOST=0.0.0.0` z nftables tylko dla mostka, albo
`systemd-socket-proxyd` na adresie mostka. `models.json` dla pudełka wskazuje adres mostka (adapter w `models/`,
decyzja 60). Tokeny punktów zewnętrznych: jak dziś, z pliku poza `~` — w pudełku ten plik jest w `in/` kroku (0600).

### 4.3 Worktree i commity: montowanie pod identycznymi ścieżkami

Worktree Tide to `~/DevWorkspace/<proj>-worktrees/<nazwa>` z plikiem `.git` wskazującym **bezwzględnie**
`~/DevWorkspace/<proj>/.git/worktrees/<nazwa>`; katalogi kroku `<worktree>.step`, `<worktree>.review-N.step` leżą obok
w tym samym katalogu [sprawdzone: `ls ~/DevWorkspace/toolssuite-worktrees`]. Dlatego:

- Pudełko montuje (`incus config device add <box> … disk source=… path=… shift=true`) **pod tą samą ścieżką co host**:
  `~/DevWorkspace/<proj>/.git` (rw) i `~/DevWorkspace/<proj>-worktrees` (rw). Wskaźniki gita, ścieżki w dzienniku
  karty (`stepDir`, `worktree`, `gitDir`), `resolve_git_dir` i `stepstop` działają bez zmian. `shift=true` to
  idmapped mounts (jądro ≥ 5.12; mamy 7.0) — uid 1000 hosta ↔ uid 1000 `agent` w pudełku **[założenie: P-4, ext4 OK]**.
- Główny worktree projektu (pliki `master`) **nie** jest montowany — jak dziś (`denyRead: ["~"]` z wyjątkiem `.git`).
- Alternatywa „git bundle” (klon w pudełku, bundle z powrotem) daje pełne odcięcie wspólnego `.git`, ale łamie
  model worktree Tide (skaner, scalanie gałęzi ticketu, `MergeCardToMain`, Sesje). Odrzucona; zostaje jako opcja dla
  `type: vm` w przyszłości, jeśli virtiofs zawiedzie.
- Katalog `in/` kroku: dziś tylko do odczytu dzięki piaskownicy; w pudełku agent (ten sam uid) mógłby go nadpisać.
  Nie ma czego chronić (prompt już wczytany; `exit-code` pisze opakowanie), ale `in/env` z tokenem punktu lokalnego
  dostaje 0600 i jest czytany przez opakowanie przed `exec`.

### 4.4 Strażnik refów (nowe)

Pudełko ma zapis do całego `<repo>/.git` (prostota; Codex dziś: `objects`, `refs`, `logs`). Zabezpieczenie po
kroku: przed startem usługa zapisuje migawkę `git for-each-ref` repozytorium; po kroku porównuje — zmieniła się
jakakolwiek gałąź poza `refs/heads/<gałąź ticketu>` (albo `HEAD` worktree nie jest tą gałęzią) → zdarzenie
`refs.guard` w dzienniku, przywrócenie z migawki (`git update-ref`), krok kończy się `error` z powodem. To zastępuje
dzisiejsze „pełnego zakazu zapisu poza worktree nie da się obiecać” (decyzja 86) czymś sprawdzalnym.
**[decyzja D7]**: całe `.git` rw + strażnik czy ścisłe `objects/refs/logs` jak u Codexa.

**Poprawka po przeglądzie (03.11, koordynator):** samo „całe `.git` rw” otwiera ucieczkę z pudełka. Agent mógłby dopisać
hak (`.git/hooks/*`) albo ustawienie (`core.hooksPath`, `core.fsmonitor`, `core.sshCommand`, `include.path`,
`filter.*` w `.git/config`, a także `.git/info/attributes`) — kod uruchomi się **na hoście, z prawami użytkownika**, gdy
użytkownik albo Tide wywoła `git` w tym repozytorium. Strażnik refów tego nie widzi. Dlatego rekomendacja:
`.git` rw **z wyjątkiem** `.git/hooks`, `.git/config`, `.git/info` (montowane tylko do odczytu albo nadpisane pustym
katalogiem/plikiem tylko do odczytu), konfiguracja worktree (`.git/worktrees/<nazwa>/config.worktree`) też ro,
a strażnik po kroku porównuje dodatkowo sumy kontrolne tych ścieżek; zmiana → przywrócenie i `error`. Tide po stronie
hosta wywołuje `git` z `-c core.hooksPath=/dev/null -c core.fsmonitor=false`, żeby nawet przeoczona zmiana nie
uruchomiła się automatycznie. Dopisać do ticketu #7 i do scenariuszy akceptacyjnych (S1: próba zapisu haka).

### 4.5 Sieć: proxy wyjściowe Tide z listą domen

- Pudełko w sieci mostkowej Incusa (`incusbr0`, NAT). nftables na hoście (reguła dla mostka, zakładana przez
  `scripts/box/install.sh`): z pudełka wolno **tylko** do proxy Tide na adresie mostka i (jeśli profil mówi) do
  Ollamy hosta; cała reszta DROP (również DNS — proxy rozwiązuje nazwy). Nie polegamy na ACL sieciowych Incusa
  (na mostku są ograniczone) **[założenie]**.
- Proxy Tide (`tide_daemon/box/proxy.py`, HTTP CONNECT bez MITM, lista domen z profilu, dziennik odmów per karta).
  To ten sam model, którego Claude Code używa we własnej piaskownicy (`network.allowedDomains` = proxy z listą).
  Pudełko dostaje `HTTP_PROXY/HTTPS_PROXY/NO_PROXY` globalnie (`/etc/environment`, `systemd` drop-in dla `dockerd`),
  więc npm, NuGet, pip, Playwright (pobieranie przeglądarek), `docker pull`, Claude Code i Codex idą przez nie
  **[założenie: P-6 — każdy klient sprawdzić]**.
- Domyślna lista = rejestry stosu (dziś `node.REGISTRY_DOMAINS`) + domeny modeli (`api.anthropic.com`,
  `statsig.anthropic.com`, `api.openai.com`, `chatgpt.com`…) + z profilu projektu (`networkDomains[slug]` z
  `cardflow.json` migruje do profilu). `github.com`, `git.firma.example`, `10.20.3.10` — **nie** na liście, chyba
  że profil dopisze (wtedy pakiet zgód to pokazuje na czerwono: „pudełko ma dostęp do zdalnego repozytorium”).
- Odmowa w dzienniku proxy to artefakt dla recenzenta i materiał do „dopisz domenę do profilu” przy następnym planie.
- `localhost` w pudełku to pudełko: `ng serve`, Postgres z Testcontainers — bez kolizji z portami hosta
  (dziś `4300` i `5432` są zajęte na hoście [sprawdzone]).

### 4.6 Porty

W pudełku porty kolidują tylko między krokami **tego samego projektu**. Tide przydziela z puli pudełka
(np. 4300–4399) i przekazuje `E2E_PORT`, `PORT`, `TIDE_PORT_BASE` (10 kolejnych) w środowisku kroku; profil projektu
mówi, która zmienna jest wiodąca (`toolssuite`: `PORT` w `playwright.config.ts` [sprawdzone], Karma: `--port`).
Kolejka „jedno ciężkie polecenie na projekt” (`OUTSIDE_PER_PROJECT`) przestaje być potrzebna jako ochrona portów;
zostaje limit zasobów pudełka. Porty hosta nie są przekazywane do pudełka (brak `proxy device`), chyba że profil
jawnie prosi (np. podgląd serwera dev z hosta — opcja na później).

### 4.7 Cache paczek, przeglądarki, narzędzia

W `/home/agent` pudełka (katalog hosta `~/.local/share/tide/box/<slug>/home`, trwały między resetami):
`~/.npm`, `~/.nuget/packages`, `~/.cache/ms-playwright`, `~/.cache/pip`, `~/.dotnet`. Zachowanie `node.install`
i `dotnet.restore` przed krokiem zostaje (teraz w pudełku, z siecią przez proxy) — to nadal skraca krok. Pudełko ma
`xvfb` i `weston` (headless) na wypadek testów wymagających X/Wayland; Chrome: `CHROME_BIN` z Playwrighta jak dziś.
Narzędzia agentów w obrazie bazowym Tide: Claude Code (instalator natywny, wersja przypięta do hosta: `2.1.317`),
Codex z npm (`@openai/codex`, nie snap — w pudełku snapa nie ma i nie chcemy), `gh`/`glab` **nie** (brak potrzeby).

### 4.8 Docker w pudełku

Konfiguracja Incusa: `security.nesting=true`, `security.syscalls.intercept.mknod=true`,
`security.syscalls.intercept.setxattr=true` (wg dokumentacji Incusa „Docker inside Incus”). `docker.io` z apt w pudełku,
`agent` w grupie `docker` pudełka. Testcontainers (.NET 4.15, [sprawdzone w `Directory.Packages.props`]) widzi
`/var/run/docker.sock` pudełka. Obrazy z profilu (`pgvector/pgvector:pg16`) ciągnięte przy budowie przez proxy
(`registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com` na liście domen). Docker użytkownika
na hoście jest niewidoczny. Ryzyko: nested overlay w userns z AppArmor na jądrze 7.0 — **[założenie: P-1]**.

### 4.9 Zasoby i równoległość

- `limits.cpu=8`, `limits.memory=24GiB`, `limits.memory.swap=false`, dysk: wolumen 60 GB w puli btrfs (plik
  `/home/tide-incus-pool.img`, rzadki, 200 GB) — btrfs daje tanie migawki na ext4 bez przebudowy partycji.
  **[decyzja D8]** wartości.
- Kroki w pudełku jak dziś: `systemd-run --user --scope -p CPUWeight=40` wewnątrz (użytkownik `agent` z lingering).
  Sloty dostawców i fale (PT-62) bez zmian. Kilka kroków jednej karty i kilku kart tego samego projektu dzieli
  jedno pudełko — to zamierzone (jeden Docker, jeden cache). Reset `clean` tylko, gdy w pudełku nie ma kroków
  (usługa to wie z `procs`/dziennika).
- Pudełko na kartę (`box: per-card` w profilu) jako opcja dla kart, które grzebią w stanie globalnym (np. zmiana
  wersji Node): `incus copy <proj>/clean <proj>-<karta>` jest tani na btrfs. Domyślnie: na projekt. **[decyzja D3]**

### 4.10 Sprzątanie i cykl życia

- Stany pudełka w dzienniku Tablicy: `brak → budowa → clean (migawka) → w użyciu → nieaktualny (przepis się zmienił)
  → reset`. Widok w oknie Tide (K-36, projekty) z akcjami: zbuduj, resetuj, zatrzymaj, usuń.
- Zatrzymanie pudełka po N godzinach bez kroku (`incus stop`), start przy pierwszym kroku (≤ 30 s, S3).
- Sprzątanie w pudełku po kroku: nic (worktree i tak zostaje do review); po domknięciu karty: `docker system prune`
  w pudełku, usunięcie kontenerów Testcontainers (ryuk robi to sam).
- Stary stan worktree (`node_modules`, `bin/obj`) jak dziś — przy usunięciu worktree (K-36 Worktrees).

## 5. Profil środowiska projektu na etapie planu

### 5.1 Wykrywanie stosu i CI (deterministyczne, w usłudze)

Nowy moduł `tasks/stack.py` (rozszerza dzisiejsze `dotnet.is_dotnet`, `node.is_node`, `node.registry_domains`):
czyta worktree karty i zwraca `StackReport` — fakty, nie decyzje:

| Źródło | Co wykrywa | Projekt [sprawdzone] |
|---|---|---|
| `package.json` skrypty, `angular.json`, `karma.conf.js`, `playwright.config.*`, `e2e/protractor.conf.js` | Node + wersja (`.nvmrc`, `engines`), Angular, Karma (`browsers: ['Chrome']`, port 9876), Playwright (`PORT`, `webServer`), Protractor (Chrome, `localhost:4200`) | toolssuite: Angular 19, Karma Chrome, Playwright w worktree `k-31-refactor-testability`, Protractor w master |
| `*.sln`, `*.csproj`, `Directory.Packages.props`, `global.json`, `NuGet.Config` | .NET + TFM, Testcontainers (+ obrazy z kodu: `PostgreSqlBuilder("pgvector/pgvector:pg16")`), Playwright .NET, dodatkowe rejestry NuGet | agent-team: net10.0, Testcontainers 4.15, `Microsoft.Playwright 1.52`; infracore: net6/net8/netstandard2.0, `nuget_devhost.config` (prywatny rejestr!) |
| `docker-compose.yml` | obrazy, porty, usługi | agent-team: `pgvector/pgvector:pg16` na 5432 |
| `.gitlab-ci.yml`, `.github/workflows/*` | obraz CI, polecenia build/test, usługi | toolssuite: build w `node:20` w Dockerze, `rsync` na devhost (deploy — ignorujemy); infracore: `dotnet restore --configfile`, `dotnet test tests/RefactoredInfrastructureTests`, deploy przez `rsync`+`sudo` (ignorujemy) |
| `Makefile`, `pyproject.toml`, `requirements.txt`, `mk/*.mk` | cele `test`, `lint`; Python; `QT_QPA_PLATFORM=offscreen` | plasma-tide: `make test` = `python3 -m unittest discover` w `TEST_DIRS`; widoki QML offscreen; testy fontów użytkownika (K-33) |
| `CLAUDE.md`/`AGENTS.md` | polecenia testów opisane słownie (dla planującego, nie dla parsera) | agent-team CLAUDE.md: „Testcontainers + WebApplicationFactory” |

Raport trafia do promptu planowania (`planning.prompt`, `fullcard`) jako sekcja „Środowisko projektu (wykryte)”
oraz jest podstawą propozycji profilu generowanej **przez usługę** (szablony per stos, 5.4). Planujący model może
profil poprawić (dopisać domeny, obrazy, pakiety apt, polecenia weryfikacji) w `out/box.yaml`; walidator planu
(`plans.read`) sprawdza schemat tak jak sprawdza tabelę ticketów.

### 5.2 Format profilu

Plik YAML (usługa już czyta YAML frontmattery; schemat w `board/boxprofile.py`):

```yaml
tide-box: 1
project: toolssuite
type: container            # container | vm
base: images:ubuntu/24.04  # obraz Incusa; Tide dokłada warstwę „tide-base” (agenci, git, proxy)
stacks: [node]             # szablony Tide: node, dotnet, python-qt, docker, playwright
node: { version: "20" }
apt: [xvfb]                # ponad szablony
setup:                     # polecenia budowy po szablonach (w pudełku, z siecią przez proxy)
  - npx playwright install chromium
network:
  domains: [registry.npmjs.org, fonts.googleapis.com, fonts.gstatic.com, playwright.azureedge.net]
  host-ollama: false
ports: { pool: [4300, 4399], env: [PORT, E2E_PORT] }
docker: { enabled: false, images: [] }
resources: { cpu: 8, memory: 24GiB, disk: 60GB }
verify:                    # polecenia, którymi Tide sprawdza pudełko po budowie i które recenzent może cytować
  - npm run lint
  - npm test -- --watch=false --browsers=ChromeHeadless
packs: []                  # paczki danych użytkownika tylko do odczytu (sekcja 6.2)
box: per-project           # per-project | per-card
```

**Gdzie leży [decyzja D5].** Rekomendacja: w Tablicy, `~/DevWorkspace/tide-board/projects/<slug>/box.yaml`
(obok `projects/<slug>.md`), bo (a) firma: do repo nie commitujemy spraw Tide (`~/.claude/CLAUDE.md`), (b) profil
to zgoda użytkownika, a nie część kodu, (c) Tablica jest już repozytorium z historią. Opcjonalnie `.tide/box.yaml`
w repozytorium projektu (plasma-tide, LegalApp) jako **źródło propozycji**, które i tak kopiuje się do Tablicy po
zatwierdzeniu. Karta może rozszerzyć profil (`box:` w frontmatterze planu albo `out/box.yaml` z planowania) —
rozszerzenie podlega temu samemu zatwierdzeniu.

### 5.3 Jak powstaje pudełko

1. **Obraz bazowy Tide** (raz na host, `make box-base` / `tide box base`): `images:ubuntu/24.04` + git, build-essential,
   curl, ca-certificates, `xvfb`, czcionki użytkownika (K-33: z `~/.local/share/fonts` kopiowane, nie montowane),
   użytkownik `agent` (uid 1000, lingering), Claude Code (wersja hosta), Codex (npm), proxy w `/etc/environment`,
   logowanie agentów (K1) → migawka `tide-base/clean`. Przebudowa przy zmianie wersji agenta.
2. **Pudełko projektu**: `incus copy tide-base/clean <slug>` → szablony stosów (`box/stacks/node.sh`, `dotnet.sh`,
   `python-qt.sh`, `docker.sh`, `playwright.sh`) → `apt`, `setup` z profilu → `docker pull` obrazów → `verify`
   (wynik w dzienniku Tablicy, nie blokuje) → migawka `<slug>/clean` z hashem profilu w opisie.
3. **Krok**: jeśli pudełko istnieje i hash profilu = hash migawki → `incus start` (gdy zatrzymane) i `incus exec`.
   Hash inny → pakiet zgód pokazuje „przepis się zmienił: przebuduj przed startem” (budowa w tle, pierwszy krok czeka;
   stan karty „czeka na pudełko”).
4. **Reset**: `incus restore <slug> clean` gdy brak kroków; ręcznie z okna Tide; automatycznie po domknięciu karty
   `per-card`.

### 5.4 Szablony stosów (co Tide umie bez pytania)

| Szablon | Instaluje | Domeny | Verify domyślne |
|---|---|---|---|
| `node` | nvm + Node z profilu (domyślnie wersja z `.nvmrc`/`engines`/hosta `20`), `npm ci` przy budowie dla worktree? nie — zależności są per worktree (jak dziś `node.install` przed krokiem) | `registry.npmjs.org`, `registry.yarnpkg.com`, `fonts.googleapis.com`, `fonts.gstatic.com` | `npm run build`, `npm test -- --watch=false --browsers=ChromeHeadless` jeśli Karma |
| `playwright` | `npx playwright install --with-deps chromium`, `CHROME_BIN` | `playwright.azureedge.net`, `cdn.playwright.dev` | `npx playwright --version` |
| `dotnet` | SDK z `global.json` albo hosta (`10.0`), `dotnet restore` przy budowie, env z `dotnet.step_env` (bez serwerów buildu nadal, bo stabilniej) | `api.nuget.org`, `globalcdn.nuget.org`, dodatkowe z `NuGet.Config` (np. `nuget_devhost.config` — rejestr firmowy w sieci lokalnej: wymaga jawnej zgody w pakiecie, bo to adres wewnętrzny) | `dotnet build --no-restore` |
| `docker` | `docker.io`, `agent` w grupie `docker`, `nesting` w konfiguracji Incusa, `docker pull` obrazów | `registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com`, `index.docker.io` | `docker run --rm hello-world` |
| `python-qt` | `python3-pyqt6`, `python3-pyqt6.qtquick`, `qml6-module-*` z `Makefile`/`mk` Tide, `QT_QPA_PLATFORM=offscreen` w env kroku, czcionki | `pypi.org`, `files.pythonhosted.org` | `make test` |

### 5.5 Zatwierdzanie z planem — pakiet zgód

W oknie planu (K-35) obok przydziałów wykonawców sekcja **„Środowisko karty”**:

- nagłówek: pudełko `toolssuite` — stan `clean` z 02.11 / `do zbudowania` / `do przebudowy (zmienił się przepis)`;
- lista z przełącznikami i kolorem ryzyka: sieć (domeny — zielone rejestry, żółte CDN-y, **czerwone**: zdalne
  repozytoria, adresy wewnętrzne `192.168.*`, `*.firma.example`, pełna sieć), Docker (tak/nie + obrazy), porty,
  paczki danych (6.2, zawsze czerwone — to dane użytkownika), zasoby, `per-card`;
- przyciski: „Zatwierdź plan i środowisko” (zapis profilu do Tablicy, budowa w tle), „Tylko plan” (karta czeka na
  środowisko — nie startuje), „Zmień” (edycja YAML w oknie).
- Zmiana profilu z poziomu karty w trakcie pracy: krok, który potrzebuje czegoś spoza profilu, kończy się jak dziś
  `denied`/`question` — ale pytanie brzmi „dopisz do profilu projektu `X` i przebuduj / nie”, a nie „zezwól na
  polecenie”. Oczekiwanie: rzadko (S1), bo profil pokrywa stos i CI.

### 5.6 Przykładowe profile czterech projektów

```yaml
# plasma-tide
tide-box: 1
project: plasma-tide
stacks: [python-qt]
apt: [qml6-module-qtquick-controls, qml6-module-qtquick-layouts, libxkbcommon-x11-0, kirigami2-dev]  # [założenie: dobrać z Makefile/mk]
setup: [cp -r /packs/fonts ~/.local/share/fonts]   # czcionki użytkownika z paczki (K-33)
network: { domains: [pypi.org, files.pythonhosted.org], host-ollama: true }   # testy modeli lokalnych
docker: { enabled: false }
verify: [make test, make lint]
notes: "make install w pudełku instaluje w pudełku — nieszkodliwe; testy ekranu blokady/SDDM/KWin tylko na hoście"
```

```yaml
# legalapp (agent-team)
tide-box: 1
project: legalapp
stacks: [dotnet, docker]
dotnet: { sdk: "10.0" }
docker: { enabled: true, images: [pgvector/pgvector:pg16] }
network: { domains: [api.nuget.org, globalcdn.nuget.org, registry-1.docker.io, auth.docker.io, production.cloudflare.docker.com] }
ports: { pool: [5500, 5599], env: [ASPNETCORE_URLS_PORT] }
verify: [dotnet build LegalApp.sln --no-restore, dotnet test tests/LegalApp.Tests.Unit --no-restore]
# „api.sejm.gov.pl” (curl z outsideCommands 03.11) — NIE domyślnie; karta pobierająca akty dopisuje do profilu karty.
```

```yaml
# toolssuite
tide-box: 1
project: toolssuite
stacks: [node, playwright]
node: { version: "20" }
network: { domains: [registry.npmjs.org, fonts.googleapis.com, fonts.gstatic.com, playwright.azureedge.net] }
ports: { pool: [4300, 4399], env: [PORT, E2E_PORT] }
docker: { enabled: false }
verify: [npm run lint, npm run build, npx playwright test --workers=2]
# `nuget_devhost`/devhost/rsync z CI: deploy — poza pudełkiem, nigdy.
```

```yaml
# infracore (jeśli wejdzie do Tablicy)
tide-box: 1
project: infracore
stacks: [dotnet]
dotnet: { sdk: ["8.0", "6.0"] }          # TFM net6/net8 [sprawdzone]
network: { domains: [api.nuget.org, globalcdn.nuget.org] }
# NuGet.Config: nuget.org; nuget_devhost.config wskazuje rejestr wewnętrzny — czerwona pozycja, domyślnie wyłączona
verify: [dotnet restore InfraCore.sln --configfile NuGet.Config, dotnet test tests/RefactoredInfrastructureTests -c Release --no-restore]
# IntegrationEmbeddedDbTests/IntegrationTests: Firebird? [założenie: sprawdzić przy pierwszym planie; możliwy obraz jacobalberty/firebird]
```

`LogTool` nie istnieje w `~/DevWorkspace` [sprawdzone] — pomijam.

### 5.7 Projekt bez wzorca

`stacks: []`, obraz bazowy, planujący dostaje raport z pustym wykryciem i instrukcję: „zaproponuj `apt`, `setup`,
`verify`, `network.domains` na podstawie README/CI; gdy nie wiesz — zostaw puste i oznacz w planie pytanie do
użytkownika”. Pytanie trafia do okna planu (raz), nie w trakcie kroków. Minimalne pudełko i tak daje twardą
granicę — gorzej niż dziś nie będzie, tylko więcej `denied` o brakujące narzędzia, które użytkownik dopisuje do profilu.

## 6. Granica wyjścia

### 6.1 Co wychodzi z pudełka

| Kanał | Treść | Kto kontroluje |
|---|---|---|
| wspólny `.git` | commity na gałęzi ticketu (strażnik refów 4.4) | usługa; push wyłącznie Tide/K-36 po kliknięciu |
| `<krok>/out` | `result.json`, notki, dziennik `step.log`, artefakty testów (log, podsumowanie — ticket #9 z `sandbox-and-permissions.md` §6) | usługa czyta jak dziś |
| proxy wyjściowe | HTTPS tylko do domen profilu (modele, rejestry); dziennik odmów | profil (zgoda z planem) |
| nic więcej | brak D-Bus, brak gniazd hosta, brak portów hosta, brak schowka, brak `~` | — |

### 6.2 Paczki danych użytkownika (tylko do odczytu)

Potrzeba z 03.11: karta potrzebowała dzienników sesji użytkownika i kopii Tablicy. Mechanizm `packs` w profilu
karty (nie projektu — to zgoda jednorazowa):

```yaml
packs:
  - name: sessions-2026-11-03
    from: ~/.claude/projects/-home-tomek-DevWorkspace-plasma-tide/*.jsonl
    since: 2026-11-03
  - name: board
    from: ~/DevWorkspace/tide-board/cards/K-3*.md
```

Usługa **kopiuje** (nie montuje) pliki do `~/.local/share/tide/box/<slug>/packs/<karta>/<name>` i montuje je w pudełku
pod `/packs/<name>` z `readonly=true`. Pakiet zgód pokazuje listę plików, rozmiary i liczbę, oraz wynik prostego
skanera sekretów (wzorce `sk-ant-`, `ghp_`, `BEGIN … PRIVATE KEY`, `Authorization:`) — trafienie blokuje paczkę do
ręcznego wyboru. Po domknięciu karty paczka jest usuwana. Montowanie całego `~/.claude` nigdy.

### 6.3 Czerwona lista (`make box-check`)

Uruchamiana w prawdziwym pudełku jako użytkownik `agent`; każdy punkt musi **zawieść**:
`ls /home/user/.ssh`, `cat /home/user/.git-credentials`, `ls /home/user/DevWorkspace/plasma-tide/apps` (główny
worktree), `curl https://example.com`, `curl https://github.com`, `nslookup github.com`, `docker -H unix:///host/docker.sock ps`
(brak), `dbus-send --session …`, `git push origin HEAD` w worktree (brak sieci + brak poświadczeń), `gh auth status`
(brak programu), `cat /proc/1/environ` hosta (niewidoczny), zapis poza `~/agent`, worktree i `.git`, `nc <ip hosta> 5432`
(Postgres użytkownika), `cat ~/.claude/.credentials.json` **z polecenia Bash agenta** (wewnętrzna piaskownica,
jeśli P-2 się uda — inaczej punkt oznaczony jako ryzyko resztkowe K1).

### 6.4 Co nadal wymaga decyzji użytkownika (P3 zostaje)

Push, MR, scalenie do głównej, wdrożenie, `make install` na hoście, testy wymagające prawdziwej Plasmy, dostęp do
adresów wewnętrznych (`10.20.3.10`, `git.firma.example`, `nuget_devhost`), paczki danych, pełna sieć.

## 7. Migracja z dzisiejszego stanu

### 7.1 Co się dzieje z istniejącymi mechanizmami

| Dziś | Po pudełku |
|---|---|
| `engine._start_outside`, `_outside_ready`, `_outside_busy`, `OUTSIDE_*`, wynik `outside` w `RESULTS`, `ENV_QUESTION`/`ENV_ANSWER` | **Etap 1:** `_start_outside` uruchamia polecenie w pudełku projektu (`incus exec`) zamiast `systemd-run` na hoście — natychmiast zamyka dziurę dla projektów z pudełkiem, zanim agent wejdzie do pudełka. **Etap 2:** usunięte; `ENV_ANSWER` zmienia treść na „pracujesz w pudełku projektu: instaluj, uruchamiaj serwery, Docker i testy sam”. |
| `outsideAuto`, `outsideCommands`, `autoConsent` w `cardflow.json` | `outsideCommands` → zasiew `network.domains` i `verify` profili (jednorazowa migracja z listą do zatwierdzenia); `outsideAuto`/`autoConsent` bez znaczenia w pudełku; zostają dla projektów w trybie przejściowym |
| `board/commands.py`: listy dozwolonych/zabronionych, `sandbox_grant`, `claude_tools`, `outside_*` | w pudełku `--allowedTools` nie jest potrzebne (`autoAllowBashIfSandboxed`/`permission-mode` bez listy); zakazy projektu zostają w prompt jako wskazówka; `outside_*` usunięte w etapie 2 |
| `agentcmd._claude` settings: `sandbox.filesystem.denyRead ["~"]`, `allowedDomains` | w pudełku: `denyRead: ["~/.claude", "~/.codex", "/packs"?]` (ochrona poświadczeń, P-2), `allowedDomains` puste (sieć pilnuje proxy); `failIfUnavailable` zostaje; gdy bwrap w kontenerze nie zadziała — `sandbox.enabled=false` i ryzyko K1 w pełni |
| `agentcmd._codex`: `--sandbox workspace-write`, `writable_roots`, wyłączanie MCP | w pudełku `--sandbox danger-full-access`? Nie: zostaje `workspace-write` z rootami jak dziś (druga warstwa, tania); MCP: Codex z npm w pudełku ma pustą konfigurację — `list_codex_mcp` zbędne |
| `tasks/node.py`, `tasks/dotnet.py` (`install`, `restore` przed krokiem na hoście) | te same funkcje przez `BoxRunner` (w pudełku); `node_home`, `chrome_bin`, `packages_dir` wskazują ścieżki pudełka; `REGISTRY_DOMAINS` → szablony stosów |
| `agentcmd.Background.start` (`systemd-run --user --scope` na hoście) | `Runner` z dwiema implementacjami: `HostRunner` (dziś; tryb przejściowy i testy) i `BoxRunner` (`incus exec <box> --user 1000 --group 1000 --cwd <wt> -- /usr/bin/env -i … systemd-run --user --scope …`). Zmienne wrażliwe nie w argv `incus exec` (widoczne w `ps` hosta): opakowanie czyta `in/env` (0600) |
| `tasks/stepstop.py` (`systemctl --user kill/stop`, commit WIP w bwrap na hoście) | kill przez `incus exec <box> -- systemctl --user …`; commit WIP: git usługi na hoście w bwrap **jak dziś** — host widzi worktree bez zmian (ten sam ext4) |
| `agents/scanner.py` czyta `~/.claude/projects` dla Sesji; `_hidden` | dziennik sesji kroku w `~/.local/share/tide/box/<slug>/home/.claude/projects` — skaner dostaje drugi korzeń (jak `codex_homes.default_homes` ma kilka katalogów) |
| `docs/sandbox-and-permissions.md` | aktualizacja: §4 tabela poziomów → P1/P2 zastąpione pudełkiem, P3 bez zmian |
| AppArmor profil `bwrap` na hoście | zostaje (planowanie i tryb przejściowy); w etapie 2 używany tylko przez `stepstop` (commit WIP) i Konsolę |

### 7.2 Tryb przejściowy

Ustawienie na projekt w Tablicy: `box: off | outside | full`.
- `off` — dziś (bwrap + `outside` z `outsideAuto`).
- `outside` — etap 1: agent na hoście, `outside` w pudełku. Zalecany od razu po zbudowaniu pierwszego pudełka.
- `full` — etap 2: agent w pudełku.
Projekt bez pudełka zostaje w `off`; usługa nie przechodzi sama. Po wejściu wszystkich projektów w `full` —
ticket sprzątający usuwa `off`/`outside`.

### 7.3 Testy Tide (atrapa pudełka)

- `tests/fakebin.py` dostaje `incus` (jak `systemd-run`, `konsole`): zapisuje argv do `TIDE_FAKE_LOG` i **wykonuje**
  część po `--` lokalnie (`exec "$@"`) — dzięki temu istniejące testy `agentcmd`/`cardflow_execution` przechodzą z
  `BoxRunner` bez Incusa. `incus list/snapshot/restore/config` zwracają stały JSON z katalogu stanu atrapy.
- `test_agentcmd.py` (1137 linii [sprawdzone]) rozszerzony o `BoxRunner`: argv ma `--user 1000`, `--cwd`, brak tokenów
  w argv, `in/env` 0600; `HostRunner` bez zmian.
- Nowe `test_box_profile.py` (schemat, wykrywanie na fixture’ach czterech projektów — małe drzewa plików),
  `test_box_builder.py` (kolejność kroków budowy, hash, przebudowa), `test_box_proxy.py` (CONNECT do domeny z listy /
  spoza listy), `test_refs_guard.py`.
- Prawdziwy Incus tylko w `make box-check` (ręcznie, nie w `make test`).

### 7.4 Scenariusze akceptacyjne (S1) — powtórka 03.11

1. toolssuite: ticket dopisuje paczkę (`npm install --save-dev angular-eslint@^19`), uruchamia `npm run lint`,
   `npx playwright test --workers=2` na porcie z puli; dwa tickety równolegle (dwa porty) — 0 pytań.
2. toolssuite: Karma `ChromeHeadless` (`CHROME_BIN`) — 0 pytań.
3. LegalApp: `dotnet test tests/LegalApp.Tests.Integration` z Testcontainers/pgvector — 0 pytań, Docker pudełka.
4. plasma-tide: `make test` (QML offscreen z czcionkami) — 0 pytań; `make install` — wykonuje się w pudełku bez
   skutku na hoście (dziś zakaz) — do decyzji, czy zostawić zakaz jako wskazówkę (tak).
5. Polecenie > 300 znaków i z `find -exec … \;` — bez odmowy (listy poleceń nie są już granicą).
6. Paczka dzienników sesji w profilu karty — widoczna w `/packs`, zatwierdzona z planem.
7. Czerwona lista (6.3) — wszystko odmawia.

## 8. Podział na tickety

Numeracja robocza `#n` (plan nadaje `PT-n` przy zatwierdzeniu, standard 1.2). Klasy wg `docs/agent-plan-standard.md`.

| # | Tytuł | Klasa | Blokery | Ryzyka dla recenzenta |
|---|---|---|---|---|
| #1 | Prototyp Incus na KDE neon: instalacja po symulacji, pudełko unprivileged z `nesting`, Docker w środku (`hello-world`, `pgvector` z Testcontainers), bwrap Claude Code i landlock Codexa w środku, `shift=true` na ext4, proxy CONNECT dla npm/NuGet/Docker/Claude/Codex, grupa `incus` vs `incus-admin`. Wynik: notatka `docs/box-prototype.md` z tabelą P-1…P-7 (sekcja 10) | mocny | — | to jedyny ticket z `sudo` i zmianą systemu; recenzent sprawdza, że skrypt instalacyjny jest odwracalny (`scripts/box/uninstall.sh`) i że nic nie dotyka Qt/KDE; wynik „nie działa” jest dopuszczalny i przełącza na plan B |
| #2 | Profil środowiska projektu: schemat `tide-box` (`board/boxprofile.py`), wykrywanie stosu i CI (`tasks/stack.py`), zapis w Tablicy `projects/<slug>/box.yaml`, migracja `networkDomains`/`outsideCommands` do propozycji profilu, raport w prompt planowania (lekkie i pełne), walidacja `out/box.yaml` w `plans.read` | standard | — | wykrywanie ma być deterministyczne i testowane na fixture’ach; żadnej decyzji „pełna sieć” z automatu; profil nie trafia do repo firma |
| #3 | Instalacja i pula: `scripts/box/install.sh` (apt po `-s`, grupa, `incus admin init --preseed` z btrfs loop na `/home`, mostek, nftables default-deny dla mostka, projekt ograniczony), `uninstall.sh`, `make box-install`, dokumentacja w `docs/tide-daemon.md` | standard | #1 | skrypt uruchamia użytkownik z sudo (wzór PT-75); recenzent sprawdza idempotencję i że `make install` motywu nie woła tego skryptu |
| #4 | Obraz bazowy i budowa pudełka z profilu: `tide_daemon/box/` (`builder.py`, `stacks/*.sh`, hash przepisu, migawka `clean`, `verify`, stan w Tablicy), CLI `tide box base|build|status|reset|destroy`, atrapa `incus` w `fakebin` | mocny | #2, #3 | szablony stosów są kodem powłoki uruchamianym w pudełku — recenzent czyta je jak kod produkcyjny; budowa nie może wymagać interakcji; logowanie agentów (K1) ma instrukcję, nie automat |
| #5 | Proxy wyjściowe Tide i sieć pudełka: `box/proxy.py` (CONNECT, lista domen, dziennik odmów per karta), nftables (z #3), `HTTP(S)_PROXY` w obrazie i `dockerd`, Ollama hosta przez adres mostka (`models/` adapter), czerwona lista `make box-check` | mocny | #4 | proxy słucha tylko na adresie mostka; brak MITM; testy: domena spoza listy → 403 i wpis w dzienniku; recenzent sprawdza, że lista domen modeli nie zawiera zdalnych repozytoriów |
| #6 | `BoxRunner`: krok w pudełku (`agentcmd.Runner`, `incus exec` + `systemd-run --user --scope` w środku, `in/env` 0600, ścieżki identyczne, montowanie `.git` i worktrees `shift=true`), `stepstop` przez pudełko, skaner sesji z drugim korzeniem, ustawienie projektu `box: off|outside|full`, etap 1 (`_start_outside` w pudełku) | mocny | #4 | tokeny nie w argv `incus exec`; `HostRunner` bez zmian zachowania (testy istniejące zielone); restart usługi nie gubi kroku w pudełku (`procs` + jednostka w pudełku) |
| #7 | Strażnik refów i granica `.git`: migawka `for-each-ref` przed krokiem, porównanie po, `refs.guard` w dzienniku, przywrócenie, wynik `error`; decyzja D7 | standard | #6 | nie może cofać gałęzi ticketu; wyścig z równoległymi krokami tej samej karty (różne gałęzie ticketów) — test |
| #8 | Docker w pudełku i porty: `docker` szablon z `nesting`, `docker pull` obrazów profilu przy budowie, pula portów (`PORT`, `E2E_PORT`, `TIDE_PORT_BASE`) z przydziałem w `_launch`, sprzątanie Testcontainers po domknięciu karty | standard | #4, #6 | porty tylko w env, nie w argv; kolizja portów dwóch kroków — test; `docker system prune` tylko w pudełku |
| #9 | Pakiet zgód w oknie planu (K-35): sekcja „Środowisko karty” z kolorami ryzyka, zatwierdzenie z planem, „do przebudowy”, pytanie „dopisz do profilu” zamiast „zezwól na polecenie”, stan „czeka na pudełko” w pełnej karcie | standard | #2, #4, K-35 | czerwone pozycje (adresy wewnętrzne, zdalne repo, pełna sieć, paczki) nigdy nie są domyślnie włączone; `D-Bus` kontrakt opisany w `tide-daemon.md` |
| #10 | Paczki danych tylko do odczytu: `packs` w profilu karty, kopiowanie z listą i skanerem sekretów, montowanie `/packs` ro, usuwanie po domknięciu | standard | #9 | nigdy montowanie katalogu źródłowego; skaner sekretów to filtr, nie gwarancja — nazwać to w UI |
| #11 | Etap 2 i sprzątanie: domyślne `box: full` dla projektów z pudełkiem, `ENV_ANSWER` nowa treść, usunięcie `outside*`, `sandbox_grant`/`claude_tools` jako wskazówki, migracja `cardflow.json`, aktualizacja `docs/sandbox-and-permissions.md`, decyzja w `docs/decisions.md` | standard | #6, #7, #8, #9 | usuwanie kodu z testami — recenzent sprawdza, że testy pokrywające `outside` zostały zamienione, nie skasowane bez odpowiednika |
| #12 | Cykl życia i zasoby w UI (K-36): stan pudełka na projekcie, zbuduj/reset/zatrzymaj/usuń, auto-stop po bezczynności, limity CPU/RAM/dysk z profilu, dziennik odmów proxy jako artefakt kroku dla recenzenta | standard | #4, #5, K-36 | reset tylko bez kroków (blokada); limity czytane z profilu, nie wpisane na sztywno |

Kolejność: #1 → #2 ∥ #3 → #4 → #5 ∥ #6 → #7, #8 → #9 → #10, #11 → #12. Po #6 (etap 1) dziura z `outside` jest
zamknięta dla projektów z pudełkiem; po #11 granica jest jednolita. Szacunek wielkości: #1, #4, #5, #6 — duże
(mocny), reszta standard.

## 9. Pytania do rozstrzygnięcia z użytkownikiem (lista do grillingu)

| ID | Pytanie | Rekomendacja | Konsekwencje wyboru |
|---|---|---|---|
| D1 | Agent w pudełku (B) czy na hoście z poleceniami w pudełku (A)? | **B** | A: tokeny zostają na hoście, ale dwa światy, kolejka plikowa RPC, agent dalej „wie o piaskownicy”; B: jeden świat, 0 pytań, ryzyko D2 |
| D2 | Poświadczenia agenta w pudełku: K1 osobne logowanie w obrazie / K2 token w env / K3 proxy wstrzykujące | **K1 teraz, K3 po prototypie P-3** | K1: ryzyko resztkowe = nadużycie subskrypcji przy udanym ataku na pudełko (węższe niż dziś); K3: zero sekretów w pudełku, ale niepewne wsparcie OAuth przez inny base URL |
| D3 | Pudełko na projekt czy na kartę? | **na projekt**, `per-card` jako opcja w profilu | na projekt: jeden Docker/cache, tańsze, karty dzielą stan (np. globalna wersja Node); na kartę: czystość kosztem dysku, RAM i czasu budowy |
| D4 | Kontener czy VM jako domyślność? | **kontener**, `type: vm` dla repo o niskim zaufaniu | VM: silniejsza izolacja jądra, ale RAM na stałe, virtiofs, dłuższy start |
| D5 | Profil w Tablicy czy w repozytorium projektu? | **Tablica** (`projects/<slug>/box.yaml`), `.tide/box.yaml` w repo jako opcjonalne źródło propozycji (nie w firma) | repo: wersjonowane z kodem, ale zgoda użytkownika mieszałaby się z kodem i trafiałaby do firmowych repozytoriów |
| D6 | Sieć domyślna: lista domen z profilu czy pełna sieć w pudełku? | **lista domen przez proxy**; „pełna sieć” jako czerwona opcja profilu | pełna sieć w pudełku = wyciek możliwy wszędzie, ale zero problemów z nieznanymi CDN-ami; lista = czasem `denied` o domenę (raz na projekt) |
| D7 | Zapis do wspólnego `.git`: całe rw + strażnik refów, czy tylko `objects/refs/logs` (jak Codex)? | **rw bez `hooks`/`config`/`info` (te ro) + strażnik refów i sum tych ścieżek** (poprawka 03.11: haki i config to ucieczka na host) | ścisłe: bezpieczniej, ale haki/konfiguracja `.git` ro bywają problemem (`git worktree` operacje agenta); strażnik wykrywa, nie zapobiega |
| D8 | Limity pudełka: 8 vCPU / 24 GiB / 60 GB, pula btrfs 200 GB na `/home`? | **tak** (host: 16 rdzeni, 78 GiB, 220 GB wolne) | mniejsze limity = wolniejsze e2e/`dotnet test`; większe = pulpit odczuje |
| D9 | Uprawnienia usługi do Incusa: grupa `incus-admin` (root-equivalent) czy projekt ograniczony (`incus` + `incus-user`) z `restricted.containers.nesting=allow`? | **projekt ograniczony**, jeśli P-5 pozwoli na nesting; inaczej `incus-admin` z notatką w decyzjach | `incus-admin`: każdy proces użytkownika (także agent w Konsoli!) może stworzyć kontener uprzywilejowany = root hosta; to nowa powierzchnia |
| D10 | Tryb przejściowy: zostawić `outsideAuto` włączone do etapu 1? Dla których projektów? | **tak**, jak dziś per projekt; po #6 `outside` idzie do pudełka i `outsideAuto` staje się bezpieczne | wyłączenie teraz = powrót pytań co 2 minuty |
| D11 | Testy wymagające prawdziwej Plasmy/KWin (ekran blokady, SDDM, skrypty KWin) zostają ręczne na hoście? | **tak**; krok zapisuje „wymaga sprawdzenia na hoście” w notce, recenzent nie prosi o przebieg | alternatywa (Plasma w VM z GPU) — poza zakresem |
| D12 | `make install` Tide w pudełku: zostawić zakaz jako wskazówkę w prompt, mimo że jest nieszkodliwy? | **tak** (agent nie powinien marnować czasu) | bez zakazu agent może „zainstalować” w pudełku i uznać, że sprawdził na pulpicie |
| D13 | Czy adresy wewnętrzne (`nuget_devhost`, `10.20.3.10`, `git.firma.example`) mogą być kiedykolwiek w profilu projektu, czy tylko karty? | **tylko karty**, czerwone | w profilu projektu = każda karta tego projektu ma dostęp do sieci firmowej |
| D14 | Reset do `clean`: automatycznie po domknięciu karty, czy tylko ręcznie? | **ręcznie + automatycznie przy przebudowie przepisu**; `per-card` zawsze po domknięciu | automatyczny reset projektu kasuje cache obrazów Dockera? nie — cache w `/home/agent` (trwały), reset dotyczy rootfs |
| D15 | Czy pudełko ma być dostępne także dla sesji w Konsoli (zlecenie z Tablicy, „Nowe zadanie”)? | **później** (osobna karta) | teraz: tylko kroki w tle; Konsola jak dziś |

## 10. Ryzyka i nieznane — z prototypami

| ID | Nieznane | Jak sprawdzić (ticket #1) | Gdy się nie uda |
|---|---|---|---|
| P-1 | Docker w unprivileged Incusie na jądrze 7.0 z AppArmor (overlay2 w userns, `nesting`) | `incus launch images:ubuntu/24.04 t -c security.nesting=true -c security.syscalls.intercept.mknod=true -c security.syscalls.intercept.setxattr=true`; `apt install docker.io`; `docker run hello-world`; `dotnet test` LegalApp z Testcontainers | Podman w pudełku z gniazdem zgodnym z Docker API (`podman system service`) dla Testcontainers; albo `type: vm` dla projektów z Dockerem |
| P-2 | bwrap (piaskownica Claude Code) i landlock (Codex) wewnątrz kontenera: zagnieżdżony userns przy `apparmor_restrict_unprivileged_userns=1` (sysctl jądra, prawdopodobnie globalny), przestrzeń AppArmor kontenera | w pudełku: profil `bwrap` z `userns` jak na hoście; `claude -p` z `sandbox.enabled=true`; `codex exec --sandbox workspace-write` | `sandbox.enabled=false` w pudełku — granicą jest samo pudełko; ryzyko K1 rośnie (poświadczenia czytelne dla kodu testów) → wtedy K3 pilniejsze |
| P-3 | Claude Code z logowaniem subskrypcyjnym przez `ANTHROPIC_BASE_URL` → proxy wstrzykujące; Codex `chatgpt_base_url` | mały proxy na hoście (`http`), `claude -p 'hi'` z base URL | K1 zostaje; rozważyć klucz API z budżetem dla pudełek (koszt) |
| P-4 | `shift=true` (idmapped mounts) dla katalogów na ext4 `/home`; zachowanie `git` i `npm` na takim mountcie; wydajność `node_modules` | zamontować worktrees, `git status`, `npm ci`, pomiar vs host | `raw.idmap` (statyczne mapowanie uid 1000) — starsza metoda, działa bez idmapped mounts |
| P-5 | Projekt ograniczony Incusa (`incus-user`, grupa `incus`) z `security.nesting` dla Dockera | `incus project show user-1000` po `incus-user`; `restricted.containers.nesting` | `incus-admin` dla użytkownika `tomek` (decyzja D9) z zapisem w `docs/decisions.md`; alternatywa: osobny użytkownik systemowy `tide` w `incus-admin`, usługa przez niego (większa przebudowa) |
| P-6 | Proxy CONNECT a klienci: npm, NuGet (`dotnet restore`), pip, `docker pull` (dockerd `HTTPS_PROXY`), Playwright install, `claude`, `codex`, Ollama (bez proxy, bezpośrednio do mostka) | każdy klient z listą i bez; dziennik odmów | klient bez wsparcia proxy → wyjątek nftables per domena rozwiązana przy starcie pudełka (gorsze: IP się zmieniają) |
| P-7 | Incus 6.0.0 z Ubuntu universe (LTS, tylko poprawki) vs repozytorium Zabbly (nowsze) — czy `images:` remote i `incus-agent` działają z 6.0.0 | `incus image list images:` | repozytorium Zabbly (dodatkowe źródło apt — osobna symulacja i zgoda) |
| P-8 | Czas budowy pudełka `dotnet` (SDK ~1 GB) i `playwright` (~400 MB) przez proxy; miejsce na dysku 4 pudełek | pomiar przy #4 | cache apt/obrazów na hoście (`apt-cacher-ng` — kolejny pakiet; raczej nie) |
| P-9 | `systemd --user` dla `agent` w kontenerze (lingering, `XDG_RUNTIME_DIR`), `systemd-run --user --scope` przez `incus exec` | `incus exec t --user 1000 -- systemd-run --user --scope sleep 5` | `systemd-run` jako root pudełka z `--uid=agent` (usługa zamiast scope); tokeny wtedy tylko w `in/env`, nie w właściwościach jednostki |
| P-10 | Testy QML Tide w pudełku: pakiety Qt6/QML w Ubuntu 24.04 vs wersje na KDE neon (nowsze); offscreen z czcionkami | `make test` w pudełku `python-qt` | obraz bazowy z repozytorium KDE neon (`images:` nie ma neona; można dodać repo neon do apt w pudełku — to pudełko, Qt może rosnąć) |
| P-11 | Wznowienie sesji (`--resume`) po przebudowie pudełka: dziennik sesji w `/home/agent` (katalog hosta) przeżywa reset rootfs | reset + `--resume` | dziennik sesji kopiowany do `<krok>/out` po kroku (już częściowo: `handoff`) |

## 11. Co dalej

1. Grilling nad sekcją 9 (D1–D15), zaczynając od D1, D2, D9, D6, D3.
2. Po decyzjach: plan karty K-38 z ticketami z sekcji 8 (numery `PT-n` przy zatwierdzeniu), #1 jako pierwszy
   z wynikiem „idziemy w Incus” albo „plan B: osobny użytkownik + bwrap + rootless Docker” (opcja D z sekcji 3).
3. Przed startem: scalić `fix/step-output-dir-writable` do `master` (decyzja użytkownika z karty), K-35 domknięte
   (okno planu dla #9), K-36 w kolejności po.
