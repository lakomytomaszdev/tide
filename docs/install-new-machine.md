# Tide na nowej maszynie — minimalna instalacja

Instrukcja dla osoby, która nie zna Tide, a chce na nowym komputerze pracować z kartami na Tablicy.
Instalujemy tylko to, czego potrzebuje przepływ kart:

- usługę `tide-daemon` (usługa użytkownika systemd, mówi przez D-Bus),
- okno **Tide** z Tablicą (menu aplikacji → Tide),
- polecenie `tide` (CLI) i zasoby przepływu kart (standard planu, narzędzia numeracji ticketów).

Motywu Tide (ekran logowania, ekran startowy, panele, dock, tapety, kolory, Konsola, Vivaldi) **nie** instalujemy.
Tide działa na zwykłym KDE z domyślną Bryzą. Okno Tide ma zawsze własne, ciemne kolory (paleta Mist wpisana
w okno przy instalacji), niezależne od motywu systemu — tak ma być.

Wszystko robi skrypt `scripts/bootstrap-machine.sh`. Można go uruchamiać wiele razy; tryb `--check` tylko
sprawdza i niczego nie zmienia.

## 1. Przygotowanie systemu

1. System: **Kubuntu 26.04 LTS** (albo nowszy) lub **Fedora KDE Plasma Desktop 40+** (sprawdzone na 44).
   Potrzebna jest **Plasma 6** — Kubuntu 24.04 ma jeszcze Plasmę 5; skrypt odmawia instalacji na Plasmie 5,
   Kubuntu starszym niż 26.04 i Fedorze starszej niż 40, niczego nie zmieniając.
2. Zwykłe konto użytkownika z prawem `sudo` (przy instalacji systemu: „administrator”). Skryptu nie uruchamiamy
   jako root.
3. Sieć (pakiety, logowanie do Claude i Codexa, klonowanie repozytoriów).
4. Pełna aktualizacja i restart, **zanim** uruchomisz skrypt — wtedy instalacja pakietów Tide niczego w Qt
   ani KDE nie podnosi (skrypt i tak to sprawdza i w razie czego odmawia):
   - Kubuntu: `sudo apt update && sudo apt full-upgrade`, potem restart,
   - Fedora: `sudo dnf upgrade --refresh`, potem restart.
5. Zaloguj się do sesji **Plasma (Wayland)**. Usługa Tide startuje razem z sesją graficzną.
6. Narzędzia do pobrania repozytoriów: `sudo apt install git curl` albo `sudo dnf install git curl`.
   Dostęp do repozytoriów: klucz SSH albo token do serwera Git, na którym są Tide i Twoje projekty.

## 2. Repozytorium Tide

```bash
mkdir -p ~/DevWorkspace
git clone git@github.com:lakomytomaszdev/plasma-tide.git ~/DevWorkspace/plasma-tide
cd ~/DevWorkspace/plasma-tide
```

Repozytorium jest prywatne — potrzebny jest dostęp do niego (albo kopia z innego źródła).

Projekty muszą leżeć w `~/DevWorkspace` (Tide szuka tam repozytoriów). Tablica tej maszyny powstanie
w `~/DevWorkspace/tide-board` — **nie kopiuj** Tablicy z innego komputera, ta maszyna ma własną, pustą.

## 3. Claude Code i Codex — przed skryptem

Zainstaluj oba programy **przed** skryptem: instalacja CLI Tide dokłada skille Tide do Codexa tylko wtedy,
gdy widzi program `codex`. (Gdy zainstalujesz go później — uruchom skrypt jeszcze raz.)

Claude Code (instaluje się do `~/.local/bin/claude`):

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Codex (przez npm do `~/.local`, bez sudo przy samym Codexie):

```bash
sudo apt install nodejs npm        # Fedora: sudo dnf install nodejs npm
npm install -g --prefix ~/.local @openai/codex
```

Zamknij i otwórz terminal (żeby `~/.local/bin` było w `PATH`), potem zaloguj się na swoje konta
(albo konta wskazane przez osobę, która opiekuje się tą maszyną):

```bash
claude          # w programie: /login → Twoje konto Claude; potem /exit
codex login     # otworzy przeglądarkę → Twoje konto ChatGPT
```

Ustaw tożsamość gita (commity w projektach):

```bash
git config --global user.name "Imię Nazwisko"
git config --global user.email "adres@example.com"
```

## 4. Skrypt instalacji

Najpierw sama diagnoza (nic nie zmienia, nie pyta o hasło):

```bash
cd ~/DevWorkspace/plasma-tide
bash scripts/bootstrap-machine.sh --check
```

Potem instalacja:

```bash
bash scripts/bootstrap-machine.sh
```

Co robi, po kolei (przed każdym poleceniem z `sudo` pokazuje je i pyta „[t/N]”):

1. Sprawdza system: wersja (Kubuntu ≥ 26.04, Fedora ≥ 40), Plasma 6, systemd użytkownika, ilość pamięci.
   Na nieobsługiwanym systemie kończy się kodem 2, niczego nie zmieniając.
2. Pakiety (tabela niżej). Na Kubuntu: `sudo apt-get update`, potem **symulacja** `apt-get -s install …`;
   jeśli symulacja podnosi, obniża albo usuwa pakiety Qt 6 lub KDE Frameworks 6, skrypt przerywa i nic nie
   instaluje (wtedy: pełna aktualizacja systemu z punktu 1.4 i jeszcze raz). Na Fedorze to samo przez
   `dnf install --assumeno …`. Dopiero potem `sudo apt-get install …` / `sudo dnf install …`.
3. Sprawdza `claude` i `codex`, logowanie i tożsamość gita.
4. Piaskownica kroków (bwrap) — niżej, punkt 5.
5. `make pre-install-backup install-daemon install-tide-window install-cli` — kopia zapasowa wyglądu Plasmy
   (na wszelki wypadek; nic w wyglądzie się nie zmienia), usługa, okno, CLI i zasoby.
6. Sprawdza, że usługa działa i odpowiada na D-Bus (`tide info --json`). Na słabszej maszynie wyłącza
   wyszukiwanie po znaczeniu (punkt 6).
7. Zakłada **pustą Tablicę** `~/DevWorkspace/tide-board` (lokalne repozytorium git, bez zdalnego).
8. Ładuje okno bez ekranu (wszystkie widoki, na słabszej maszynie tylko Tablicę) — sprawdza, że QML i PyQt6
   są kompletne.

Na końcu wypisuje „Uwagi” (nie blokują) i „Brakuje” (do zrobienia). Kod wyjścia: 0 — gotowe, 2 — czegoś
brakuje, 1 — błąd. Po pierwszej udanej instalacji **wyloguj się i zaloguj ponownie**.

Opcje: `--yes` (bez pytań), `--no-packages` (pakiety tylko sprawdza), `--no-board` (nie zakłada Tablicy),
`--low-power` (słabsza maszyna — punkt 6; przy mniej niż 20 GB RAM włącza się sama).

## 5. Piaskownica (bwrap) na Kubuntu

Kroki kart w tle działają w piaskownicy: Claude Code używa `bwrap`, a Tide uruchamia w niej również git
na worktree kroku. Ubuntu ogranicza przestrzenie użytkownika przez AppArmor, więc `bwrap` potrzebuje profilu.
Skrypt pokazuje stan (`python3 scripts/sandbox/check.py`). Możliwe są trzy sytuacje:

- **„Piaskownica może działać.”** — nic nie rób.
- **Profil: brak** — skrypt zaproponuje `sudo bash scripts/sandbox/install.sh` (profil tylko dla `/usr/bin/bwrap`,
  reszta systemu zostaje pod ograniczeniem). Odpowiedz „t”.
- **Profil: obcy** i „Zagnieżdżona przestrzeń … nie działa” — Kubuntu 25.04 i nowsze dostarczają własny
  profil `bwrap-userns-restrict`, pod którym piaskownica Claude Code nie startuje (krok karty kończy się
  błędem piaskownicy). Zamiana na profil Tide, z kopią oryginału:

  ```bash
  sudo mv /etc/apparmor.d/bwrap-userns-restrict /root/bwrap-userns-restrict.dist
  sudo bash scripts/sandbox/install.sh
  python3 scripts/sandbox/check.py        # ma być „Piaskownica może działać.”
  ```

  Cofnięcie: `sudo bash scripts/sandbox/uninstall.sh && sudo mv /root/bwrap-userns-restrict.dist
  /etc/apparmor.d/bwrap-userns-restrict && sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict`.
  Przy aktualizacji pakietu `apparmor` system może zapytać o ten plik — wybierz zachowanie obecnej wersji.
  **Tego wariantu nie dało się sprawdzić na prawdziwym Kubuntu 26.04** (testy były w kontenerach, które
  dziedziczą AppArmor komputera-gospodarza) — jeśli po zamianie `check.py` dalej mówi „nie działa”, zgłoś
  wynik (punkt 10) i do tego czasu używaj wykonawców Codex.

Na Fedorze nic nie trzeba (brak ograniczenia AppArmor; `check.py` pisze „nie dotyczy”).

## 6. Słabsza maszyna

Komputer z 8–16 GB RAM albo słabym procesorem i kartą graficzną udźwignie Tide, jeśli nie uruchamia na nim
rzeczy, które Tide potrafi, ale które nie są potrzebne do pracy z kartami:

- **Wyszukiwanie po znaczeniu wyłączone.** Indeks sesji agentów dla KRunnera co 5 minut przegląda sesje
  z 30 dni i liczy wektory modelem lokalnym. Skrypt przy mniej niż 20 GB RAM (albo z `--low-power`) wyłącza
  go sam; ręcznie: `busctl --user call org.tide.Daemon /Models org.tide.Daemon.Models SetSemanticSearch s off`
  albo okno Tide → Modele → Wyszukiwanie. KRunner dalej szuka po słowach.
- **Bez Ollamy i modeli lokalnych.** Minimalna instalacja ich nie zakłada. W oknie Tide → Modele serwer
  „nie odpowiada” — to normalne. Nie przełączaj opisu raportu porannego na model lokalny.
- **Pudełek (Incus) nie włączaj**, jeśli Twoja wersja Tide je ma — ustawienie `box.enabled` przepływu kart
  zostaje domyślne. To funkcja dla stacji z zapasem pamięci i procesora.
- **Jedno okno Tide.** Kolejne uruchomienie tylko pokazuje działające okno; nie zostawiaj otwartych widoków,
  których nie używasz (Docker, Sesje). Duża Tablica (setki kart) odświeża się wolniej — przy zacięciach zgłoś
  to razem z `tide info --json`.
- **Testy repozytorium** (jeśli rozwijasz Tide na tej maszynie): domyślnie biegną w liczbie procesów równej
  liczbie rdzeni minus dwa, a każdy proces ładuje Qt. Przy ≤ 16 GB RAM: `make test-changed BASE=<baza> JOBS=2`,
  przy 8 GB: `JOBS=1`. Pełny `make test` tylko wtedy, gdy jest naprawdę potrzebny.

## 7. Pierwszy projekt na Tablicy

1. Sklonuj repozytorium projektu do `~/DevWorkspace`, np.:
   `git clone git@<twój-serwer-git>:<grupa>/<projekt>.git ~/DevWorkspace/<projekt>`.
2. Otwórz okno: menu aplikacji → **Tide** (widok **Tablica**).
3. Lewy dolny róg: **Projekty i tagi**. Sklonowane repozytorium jest w sekcji „REPOZYTORIA BEZ PROJEKTU” →
   **＋ Dodaj jako projekt** (albo **Dodaj projekt** i wskaż katalog). Nadaj nazwę, dodaj tag (np. startowy
   **własne** albo własny), **Zapisz projekt**.

To samo bez okna (np. przez SSH):

```bash
busctl --user call org.tide.Daemon /Board org.tide.Daemon.Board UpsertProject s \
  '{"slug": "projekt", "name": "Projekt", "repo": "/home/<użytkownik>/DevWorkspace/projekt", "tags": ["własne"]}'
```

## 8. Pierwsza karta

1. Tablica → **＋ Karta**: tytuł („co zrobić”), projekt, krótki opis. Nowa karta ląduje w Backlogu.
2. Przeciągnij kartę do **Do zrobienia** (otworzy się okno „Co ma zrobić agent?”) albo kliknij na niej
   **Uruchom agenta…**.
3. Wejście:
   - **Od razu** — jeden ticket w tle, bez planowania; wybierasz wykonawcę (np. Sonnet) i recenzenta,
   - **Planowanie → Lekki** — lekki plan w tle, który zatwierdzasz w panelu karty, potem tickety w tle,
   - **Planowanie → Pełny** i **Tylko specyfikacja** — rozmowa w Konsoli (pytania do Ciebie); korzystają
     ze skilli `grilling` i `tdd` w `~/.claude/skills`. Skille metodologii nie są częścią Tide — zainstaluj
     je osobno, zanim użyjesz tych wejść (zob. [przenośność zasobów](tide-resources.md)),
   - **Mam plan** — przejęcie istniejącego planu z repozytorium projektu.
4. **Uruchom agenta (Ctrl+Enter)**. Karta przechodzi do „W toku”; pytania i zgody agenta pojawiają się
   w panelu karty, wynik ląduje na gałęzi karty `card/k-…` w repozytorium projektu.
5. Scalenia gałęzi karty do gałęzi głównej i `push` **nie rób** bez decyzji osoby odpowiedzialnej za projekt.

Pierwszą kartę daj prostą (np. poprawka w README), żeby sprawdzić cały obieg: krok → review → gałąź karty
→ „Do sprawdzenia”.

## 9. Co sprawdzić

```bash
bash scripts/bootstrap-machine.sh --check   # wszystko „ok”, kod 0
systemctl --user status tide-daemon         # active (running)
tide info --json                            # "ok": true, zasoby "available": true
python3 scripts/sandbox/check.py            # „Piaskownica może działać.”
```

W oknie Tide: Tablica pokazuje projekt; karta po „Od razu” przechodzi przez W toku do Do sprawdzenia.
Krok kończący się „login” znaczy, że program agenta nie jest zalogowany (`claude` → `/login`, `codex login`).

## 10. Co zgłosić przy błędzie

Zbierz pliki i zgłoś problem w Issues repozytorium Tide (albo osobie, która opiekuje się instalacją).
Pliki zawierają nazwy projektów i fragmenty pracy — nie publikuj ich, dołącz tylko tam, gdzie to bezpieczne:

```bash
cd ~/DevWorkspace/plasma-tide
bash scripts/bootstrap-machine.sh --check > ~/tide-check.txt 2>&1
journalctl --user -u tide-daemon --since today > ~/tide-daemon.log
tide info --json > ~/tide-info.json
python3 scripts/sandbox/check.py > ~/tide-sandbox.txt 2>&1
```

Przy problemie z konkretną kartą dołącz jej dziennik: `~/DevWorkspace/tide-board/flow/K-<numer>/journal.jsonl`
i napisz, co kliknięto i co się pokazało w panelu karty.

Przycisk „Opisz problem i ponów” w panelu karty zapisuje zgłoszenie w projekcie `plasma-tide` na Tablicy.
Działa tylko wtedy, gdy taki projekt istnieje — dodaj `~/DevWorkspace/plasma-tide` jako projekt
(punkt 7), jeśli chcesz z niego korzystać.

## Zależności

Z kodu: importy Pythona usługi, okna i CLI (PyQt6: QtCore, QtDBus, QtGui, QtNetwork, QtQml, QtQuick), importy
QML okna (QtQuick, Controls, Layouts, Shapes, Window, Templates, Dialogs) i programy wywoływane przez usługę.
Źródło listy: `scripts/bootstrap_packages.py` (`python3 scripts/bootstrap_packages.py table`).

| Kubuntu (apt) | Fedora (dnf) | Po co | Obowiązkowy |
| --- | --- | --- | --- |
| `python3` | `python3` | usługa, okno i CLI uruchamiają /usr/bin/python3 (min. 3.10) | tak |
| `python3-pyqt6` | `python3-pyqt6` | PyQt6: QtCore, QtDBus (usługa i CLI na D-Bus), QtGui, QtNetwork; w Fedorze także QtQml i QtQuick | tak |
| `python3-pyqt6.qtqml` | (w pakiecie wyżej) | PyQt6.QtQml — silnik QML okna Tide | tak |
| `python3-pyqt6.qtquick` | (w pakiecie wyżej) | PyQt6.QtQuick — okno Tide, zrzuty raportu spraw | tak |
| `qml6-module-qtquick` | `qt6-qtdeclarative` | QML: import QtQuick | tak |
| `qml6-module-qtquick-controls` | (w pakiecie wyżej) | QML: QtQuick.Controls | tak |
| `qml6-module-qtquick-layouts` | (w pakiecie wyżej) | QML: QtQuick.Layouts | tak |
| `qml6-module-qtquick-shapes` | (w pakiecie wyżej) | QML: QtQuick.Shapes (ikony i logo rysowane ścieżkami) | tak |
| `qml6-module-qtquick-window` | (w pakiecie wyżej) | QML: QtQuick.Window | tak |
| `qml6-module-qtquick-templates` | (w pakiecie wyżej) | QML: QtQuick.Templates | tak |
| `qml6-module-qtquick-dialogs` | (w pakiecie wyżej) | QML: QtQuick.Dialogs (widok Sprawy) | tak |
| `qml6-module-qtqml-workerscript` | (w pakiecie wyżej) | QML: zależność QtQuick.Controls | tak |
| `git` | `git` | Tablica to repozytorium git; worktree i gałęzie kart | tak |
| `make` | `make` | instalacja przez make install-* | tak |
| `bubblewrap` | `bubblewrap` | piaskownica kroków (Claude Code) i git usługi w piaskownicy (/usr/bin/bwrap) | tak |
| `socat` | `socat` | pośrednik sieci w piaskownicy Claude Code | tak |
| `zstd` | `zstd` | kopia zapasowa wyglądu przed instalacją (make pre-install-backup) | tak |
| `python3-dbus` | `python3-dbus` | wtyczka KRunnera w usłudze (bez niej usługa działa, KRunner nie) | nie |
| `python3-gi` | `python3-gobject` | pętla GLib wtyczki KRunnera | nie |
| `ripgrep` | `ripgrep` | rg na liście poleceń tylko do odczytu kroku opiekuna (zapasowo grep) | nie |
| `python3-pil` | `python3-pillow` | ikony wpisów menu „Tide Sesje” i „Tide Worktrees” (bez niej — bez ikon) | nie |
| `librsvg2-bin` | `librsvg2-tools` | rsvg-convert do tych samych ikon | nie |
| `desktop-file-utils` | `desktop-file-utils` | odświeżenie bazy plików .desktop po instalacji okna | nie |

Poza pakietami, obecne w każdej instalacji KDE Plasma 6: systemd (`systemctl`, `systemd-run` — każdy krok
karty to osobna jednostka `--user --scope`, `busctl`), Konsole (rozmowy Pełne/Tylko specyfikacja,
„wejście” do kroku), `xdg-open`, Spectacle (zrzuty ekranu w oknie uruchomienia), serwer powiadomień Plasmy.
Opcjonalnie, tylko dla swoich widoków: Docker (widok Docker), Ollama (modele lokalne), Incus (pudełka kart),
`gh`/`glab` (podpowiedzi baz z PR/MR).

Wymagania wersji: Python ≥ 3.10 (sprawdzone: 3.12 neon, 3.14 Ubuntu 26.04 i Fedora 44), Qt 6 (sprawdzone:
6.10 Ubuntu 26.04, 6.11 Fedora 44 i neon), Plasma 6. KDE neon ma moduły QML w `qt6-declarative` zamiast
`qml6-module-*` — skrypt rozpoznaje moduł po pliku `qmldir`, nie po nazwie pakietu.

## Fedora — znane różnice

- **Pakiety**: `python3-pyqt6` zawiera QtQml i QtQuick (w Ubuntu osobne `python3-pyqt6.qtqml` i `.qtquick`);
  wszystkie moduły QML są w `qt6-qtdeclarative`; `python3-gobject` zamiast `python3-gi`, `python3-pillow`
  zamiast `python3-pil`, `librsvg2-tools` zamiast `librsvg2-bin`. Moduły QML w `/usr/lib64/qt6/qml`.
- **Python**: Fedora 44 ma Pythona 3.14 jako `/usr/bin/python3` — usługa, okno i CLI na nim działają.
  Minimalny obraz Fedory może nie mieć `python3`; skrypt wtedy proponuje `sudo dnf install python3`.
- **Symulacja**: zamiast `apt-get -s` — `dnf install --assumeno` (działa bez roota, kod 1 przy poprawnej
  transakcji). Ten sam strażnik Qt 6 / KDE Frameworks 6.
- **Piaskownica**: brak AppArmor i jego ograniczenia przestrzeni użytkownika — `bwrap` działa bez profilu,
  `scripts/sandbox/install.sh` jest niepotrzebny (odmówi: „ograniczenia nie ma”). SELinux nie był testowany
  na prawdziwej Fedorze (kontener dziedziczy jądro gospodarza).
- **Codex**: Fedora nie ma snapa — Codex przez npm do `~/.local` (punkt 3).
- **PATH**: `~/.local/bin` dodaje `~/.bashrc`, nie sesja systemd. Usługa Tide sama szuka `claude` i `codex`
  w `~/.local/bin`, gdy nie ma ich w swoim `PATH`.
- **systemd użytkownika, D-Bus, `systemd-run --user --scope`, `busctl`**: bez różnic.
- **Motyw**: ekranu startowego (`make install-plymouth`) **nie** instaluj na Fedorze — używa `update-initramfs`
  (Debian/Ubuntu), Fedora ma `dracut`.

## Opcjonalnie: motyw Tide

Niepotrzebny do pracy z kartami; zmienia wygląd pulpitu. Najpierw kopia, potem całość:

```bash
make backup NAME=przed-tide
make install            # wygląd, panele, dock, widżety, Konsola, KRunner… (najpierw sam robi kopię)
make restore NAME=przed-tide   # powrót do poprzedniego wyglądu
```

Ekran logowania i ekran startowy są osobno i wymagają sudo: `make install-sddm`, `make install-plymouth`
(tylko Kubuntu). Motyw był rozwijany na KDE neon (Plasma 6.8, dwa monitory); na Kubuntu i Fedorze
nie był testowany. Opis składników: `README.md`.

## Dla agenta AI pomagającego w instalacji

Ta sekcja jest dla agenta (Claude Code, Codex), który na nowej maszynie pomaga użytkownikowi zainstalować
Tide albo zdiagnozować problem.

**Kolejność sprawdzeń** (wszystko tylko odczyt):

1. `bash scripts/bootstrap-machine.sh --check` — kod 0: gotowe, 2: czegoś brakuje (lista „Brakuje” na końcu),
   1: błąd skryptu. Zacznij od listy „Brakuje”, potem „Uwagi”.
2. `systemctl --user status tide-daemon` i `journalctl --user -u tide-daemon -n 100` — czy usługa działa
   i co zgłasza przy starcie.
3. `tide info --json` (albo `busctl --user call org.tide.Daemon /Issues org.tide.Daemon.Issues Info`) —
   odpowiedź usługi i dostępność zasobów.
4. `python3 scripts/sandbox/check.py` — stan piaskownicy bwrap.
5. Przy problemie z kartą: dziennik `~/DevWorkspace/tide-board/flow/K-<numer>/journal.jsonl` (ostatnie
   wpisy `step.finished`: `result`, `reason`, `fault`) i
   `busctl --user call org.tide.Daemon /Board org.tide.Daemon.Board Snapshot` (tylko odczyt).

**Typowe przyczyny:**

- Krok kończy się `reason: login` — program agenta nie jest zalogowany: `claude` → `/login`, `codex login`.
- „nie znaleziono programu claude/codex” — brak programu w `PATH` i w `~/.local/bin` (punkt 3); po instalacji
  uruchom skrypt ponownie, żeby `install-cli` dołożył skille Tide dla Codexa.
- Piaskownica: na Kubuntu 25.04+ profil AppArmor „obcy” bez zagnieżdżonej przestrzeni (punkt 5) — zamiana
  profilu wymaga sudo i decyzji użytkownika; na starszym Ubuntu brak profilu (`scripts/sandbox/install.sh`).
- „KDE Plasma 5…” albo „Tide wymaga Kubuntu 26.04” — system się nie nadaje; nie obchodź tej kontroli.
- Strażnik pakietów odmawia (Qt 6 / KDE Frameworks 6 w symulacji) — zaproponuj pełną aktualizację systemu
  i restart, a nie instalację z pominięciem strażnika.
- Tablica ze zdalnym repozytorium (`remote`) — prawdopodobnie kopia Tablicy z innej maszyny.
- Słaba maszyna (zacięcia, swap) — punkt 6; testy z `JOBS=2` albo `JOBS=1`.

**Czego nie robić bez wyraźnej zgody użytkownika:**

- `sudo` w jakiejkolwiek formie (pakiety, profil AppArmor, `apt`/`dnf`) — pokaż polecenie i poczekaj na decyzję,
- `make install` ani innych celów motywu (wygląd, panele, SDDM, Plymouth) — minimalny zestaw ich nie potrzebuje,
- ręczne edycje `~/.config` i `~/.local/share` (instalacja idzie wyłącznie przez skrypt i `make install-*`),
- usuwanie, nadpisywanie albo kopiowanie Tablicy (`~/DevWorkspace/tide-board`) i zmiana jej repozytorium,
- scalanie gałęzi kart do gałęzi głównej i `git push`.

Do zgłoszenia problemu zbierz pliki z punktu 10 i powiedz użytkownikowi, co zawierają.
