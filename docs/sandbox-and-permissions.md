# Piaskownica, uprawnienia i środowisko testowe kroków

Stan na 03.11.2026 i propozycja docelowa. Dokument odpowiada na pytanie użytkownika: „ostatnio uszczelnialiśmy, dziś
rozszczelniamy — gdzie jest granica?”. Decyzje do podjęcia są na końcu, każda z rekomendacją.

## 1. Cel

Kroki w tle mają pracować bez pytań o środowisko (instalacje, porty, testy, Docker) i jednocześnie nie móc zrobić
niczego, czego użytkownik nie przewidział: wypchnąć kodu, wdrożyć, przeczytać sekretów, zmienić systemu. Pytanie do
użytkownika ma dotyczyć zakresu pracy i decyzji biznesowych, a nie walki ze środowiskiem.

## 2. Stan dzisiejszy (03.11.2026) — uczciwie

| Warstwa | Co ją wymusza | Co pozwala | Co blokuje | Siła |
|---|---|---|---|---|
| **Piaskownica kroku** (Claude Code `bwrap`, Codex ze snapa) | jądro (przestrzenie nazw), profil AppArmor dla `bwrap` | zapis: katalog wyniku kroku i worktree; odczyt: worktree, wspólne `.git`, katalogi kroku, czcionki, instalacja Node/.NET tylko do odczytu | odczyt `~` (klucze SSH, `~/.git-credentials`, tokeny), sieć (`allowedDomains: []`), gniazda uniksowe (D-Bus, Docker, agent SSH) | **twarda** |
| **Środowisko kroku** | `agentcmd._env` | `HOME`, `PATH`, `LANG`, zmienne narzędzi | agent SSH, askpass, tokeny GitHuba, D-Bus | druga warstwa (agent może ją nadpisać) |
| **Lista poleceń projektu** (dozwolone / zabronione) | Claude Code `--allowedTools`, polityka w poleceniu kroku | polecenia z listy bez pytania | polecenia zabronione (w Tide `make install`) | chroni przed pomyłką, **nie przed złośliwym kodem** |
| **Automatyczne zgody w piaskownicy** (od 03.11) | `engine._auto_consent` | każde pojedyncze polecenie spoza listy, jeśli nie jest zabronione | łańcuchy, podpowłoki, polecenia zabronione | bezpieczne — i tak działa w piaskownicy |
| **Polecenie poza piaskownicą** (`outside`, od 03.11) | `systemd-run --user --scope` z prawami użytkownika | **wszystko, co może użytkownik**: sieć, Docker, cały `~`, D-Bus, klucze | tylko filtr polityki: łańcuchy i operatory, lista ryzykownych rodzin (push, publikacja, ssh, curl, rm…), ścieżki spoza worktree, zakazy projektu, jedno polecenie naraz w projekcie | **miękka** |
| **Automatyczne zgody poza piaskownicą** (od 03.11) | `outside_risky` | testy, budowanie, lint, instalacja paczek | jak wyżej | **miękka** |

### Gdzie dziś jest dziura

Polecenie poza piaskownicą, które wygląda niewinnie (`dotnet test`, `npm run e2e`, `make test`), **uruchamia kod
z repozytorium — w tym kod, który agent sam przed chwilą napisał** — z pełnymi prawami użytkownika. Test może
przeczytać `~/.ssh`, wypchnąć gałąź z `~/.git-credentials`, użyć tokenu `gh` z portfela albo uruchomić kontener
z zamontowanym `/` (gniazdo Dockera daje prawa roota). Lista ryzykownych rodzin tego nie zatrzyma, bo ryzyko siedzi
w kodzie testów, a nie w nazwie polecenia.

Wniosek: **automatyczne zgody w piaskownicy są bezpieczne; automatyczne zgody poza piaskownicą są dziś wygodą
kupioną kosztem granicy.** Przy kooperującym agencie i zaufanych repozytoriach to akceptowalne na krótko — tak samo
pracuje się w terminalu z Claude albo Codexem w trybie automatycznym — ale nie jest to granica, o której mówimy
„twarda”.

## 3. Zasada granicy (propozycja)

1. **Kod z repozytorium nigdy nie działa z pełnymi prawami użytkownika** — ani w kroku, ani w poleceniu „poza”.
2. Kroki dostają **zasoby** (sieć do rejestrów paczek, porty, Docker, cache), a nie **prawa użytkownika**.
3. Czego krok potrzebuje, da się w większości **przewidzieć przy planowaniu** — użytkownik zatwierdza to raz,
   razem z planem, jako **pakiet zgód karty**.
4. Pytanie w trakcie pracy pojawia się tylko, gdy krok wychodzi poza pakiet — i wtedy mówi, czego brakuje i co
   się stanie po zgodzie.
5. Wszystko, co wychodzi na zewnątrz (push, merge request, wdrożenie, publikacja), robi **Tide na polecenie
   użytkownika**, nigdy agent — spójnie z K-36 (repozytoria w Tide).

## 4. Model docelowy: trzy poziomy wykonania

| Poziom | Dla czego | Sieć | Pliki | Docker | Kto zatwierdza |
|---|---|---|---|---|---|
| **P1 — piaskownica kroku** (dziś + rejestry) | pisanie kodu, budowanie, testy jednostkowe, `npm install`, `dotnet restore` | tylko domeny rejestrów z profilu (`registry.npmjs.org`, `api.nuget.org`, `pypi.org`, `fonts.googleapis.com`…) — `allowedDomains` piaskownicy Claude Code | zapis: worktree, wynik kroku, cache paczek projektu | nie | pakiet zgód karty |
| **P2 — piaskownica testowa** (nowa, zastępuje „poza piaskownicą”) | e2e, testy integracyjne z Testcontainers, serwery deweloperskie, Playwright | profil projektu (np. pełna sieć albo rejestry + localhost) | zapis: worktree i cache narzędzi (`~/.npm`, `~/.nuget/packages`, `~/.cache/ms-playwright`); **odczyt `~` zabroniony** poza instalacjami narzędzi; bez D-Bus, agenta SSH, portfela | **osobny, bezrootowy Docker/Podman Tide** — nie gniazdo Dockera użytkownika | pakiet zgód karty |
| **P3 — prawa użytkownika** | push, merge request, wdrożenie, publikacja, zmiany systemu | pełna | pełne | pełny | zawsze użytkownik, jednorazowo, w Tide (K-36); agent nie dostaje tego poziomu |

Technicznie P2 to `bwrap` (ten sam profil AppArmor co dziś) uruchamiany przez usługę, nie przez agenta: przestrzeń
nazw montowań z widokiem tylko worktree i cache, własny `/tmp`, sieć według profilu, przydzielony port dla serwera
deweloperskiego (`E2E_PORT`/`PORT` z puli, koniec konfliktów na 4300), gniazdo bezrootowego Dockera Tide
(`dockerd-rootless` albo `podman system service` na osobnym użytkowniku). Testcontainers widzą tylko ten Docker.

## 5. Pakiet zgód karty (zatwierdzany razem z planem)

Planowanie (lekkie i pełne) wykrywa stos i proponuje **profil środowiska karty**:

- **Wykrywanie:** `package.json` (skrypty, Playwright, Angular/Vite — port serwera), `*.csproj`/`*.sln`
  (Testcontainers, xUnit, bazy), `pyproject.toml`, `Makefile` (cele `test`, `lint`), `docker-compose.yml`,
  konfiguracje CI (`.gitlab-ci.yml`, `.github/workflows`) — CI jest najlepszym spisem tego, czego testy potrzebują.
- **Propozycja** (przykład dla toolssuite):
  - P1: sieć do `registry.npmjs.org`, `fonts.googleapis.com`; polecenia `npm ci`, `npm install *`, `npm run lint *`,
    `npm run build *`;
  - P2: `npm run e2e *`, `npx playwright *` z przydzielonym portem; Docker: nie;
  - P3: brak.
- Dla LegalApp: P2 `dotnet test *` z Dockerem Tide (Testcontainers/PostgreSQL), sieć: `api.nuget.org`.
- **Zatwierdzenie:** w oknie planu obok przydziałów modeli — lista „czego karta potrzebuje” z przełącznikami.
  Zatwierdzony profil zapisuje się w projekcie i jest domyślny dla kolejnych kart (zmiana = nowa zgoda).
- **W trakcie pracy:** polecenie mieszczące się w profilu rusza samo; spoza profilu — jedno pytanie z opcjami
  „raz / dopisz do profilu projektu / nie”, z informacją, na którym poziomie się wykona.

## 6. Środowisko testowe, które „po prostu działa”

- **Przygotowanie przed krokiem przez usługę** (już jest dla .NET i Node): restore/`npm ci`, przeglądarki
  Playwright, obrazy Dockera z profilu (`docker pull` na Dockerze Tide), migracje bazy testowej.
- **Porty z puli** na krok, przekazywane zmienną; jeden serwer deweloperski na worktree.
- **Kolejka ciężkich zadań** (jest od 03.11: jedno polecenie P2 naraz w projekcie, dwa łącznie) — docelowo
  ustawiana w UI (K-36).
- **Wynik testów jako artefakt kroku:** log i podsumowanie (liczby, czas, kod wyjścia) w katalogu kroku; recenzent
  czyta artefakt zamiast prosić o ponowny przebieg („brak uruchomienia = brak dowodu” spełnione bez pytań).
- **Testy niestabilne pod obciążeniem** (dziś: testy Tide z limitami czasu) — limity czasu skalowane obciążeniem
  albo przebieg w kolejce ciężkich zadań.

## 7. Decyzje do podjęcia (z rekomendacją)

1. **Co do czasu P2 z automatycznymi zgodami poza piaskownicą?** Rekomendacja: zostawić włączone dla rodzin
   testów i budowania (praca stoi bez nich), ale ograniczyć do repozytoriów, którym ufasz — przełącznik per projekt
   zamiast globalnego `autoConsent`.
2. **Docker dla kroków:** osobny bezrootowy Docker Tide (rekomendacja; izolacja od Twoich kontenerów i od roota)
   czy Twój Docker (prościej, ale gniazdo = root)?
3. **Osobny użytkownik systemowy dla P2** (najmocniejsza izolacja plików, więcej konfiguracji) czy `bwrap` na Twoim
   koncie z widokiem tylko worktree i cache (rekomendacja na start)?
4. **Sieć w P1:** tylko domeny rejestrów z profilu (rekomendacja) czy pełna sieć?
5. **Pakiet zgód:** przy każdej karcie (czytelniej) czy raz na projekt z dziedziczeniem (rekomendacja: raz na
   projekt, karta pokazuje go do wglądu i może rozszerzyć).

## 8. Plan wstępny (do rozpisania na karcie)

| # | Ticket | Blokery |
|---|---|---|
| 1 | Profil środowiska projektu: model, zapis w projekcie, wykrywanie stosu i CI, propozycja przy planowaniu | — |
| 2 | P1: sieć do domen z profilu (`allowedDomains`), cache paczek projektu w kroku | #1 |
| 3 | P2: piaskownica testowa (`bwrap` przez usługę: widok worktree i cache, własny `/tmp`, sieć z profilu, porty z puli) zamiast `outside` z prawami użytkownika | #1 |
| 4 | Docker Tide: bezrootowy Docker/Podman, gniazdo tylko dla P2, przygotowanie obrazów z profilu | #3, decyzja 2 |
| 5 | Pakiet zgód w oknie planu: lista potrzeb karty z przełącznikami, zapis do profilu, pytanie tylko poza profilem | #1, K-35 |
| 6 | Artefakty testów kroku (log, podsumowanie) dla recenzenta; recenzent nie prosi o przebieg, gdy artefakt jest świeży | #3 |
| 7 | Przełącznik automatycznych zgód per projekt w UI, przegląd dziennika automatycznych zgód | K-36 |

Kolejność: #1 → #2 i #3 równolegle → #4 → #5, #6, #7. Do czasu #3 obowiązuje stan z sekcji 2.
