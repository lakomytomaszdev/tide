# Testy podczas pracy nad ticketem

W trakcie implementacji uruchamiaj zestawy dobrane do zmiany. Pełny `make test` uruchom
raz przed oddaniem ticketu. Powtarzaj pełny przebieg dopiero po zmianach, które tego wymagają,
albo po błędzie wskazującym na szerszą regresję.

```sh
make test-cardflow           # test_cardflow*.py oraz testy basesuggestions i hostingcli
make test-daemon             # wszystkie testy usługi
make test-board              # okno Tablicy (apps/board/tests)
make test-changed BASE=master
make test                   # końcowa kontrola wszystkich komponentów
make test JOBS=4             # ograniczenie liczby jednoczesnych procesów
make test JOBS=1             # diagnostyka bez równoległości
```

Nazwy wszystkich zestawów wypisuje `make help`. Ich jedynym rejestrem jest
[`tools/test_suites.py`](../tools/test_suites.py): `SUITES` definiuje cele `test-<nazwa>`,
`CHANGE_MAP` wiąże ścieżki z zestawami, a `SERIAL_MODULES` oznacza wyjątki od równoległości.
Każdy katalog `TEST_DIRS` musi mieć zestaw; pilnuje tego test rejestru. `make test` nadal bierze
wszystkie katalogi `TEST_DIRS` z plików `mk/*.mk`, także nowo dopisane komponenty.

`make test-changed` porównuje drzewo robocze z merge-base `BASE` i `HEAD` (domyślnie `master`).
Uwzględnia commity gałęzi, indeks, zmiany niezapisane w commicie i pliki nieśledzone poza ignorowanymi.
Usunięcia i przeniesienia obejmują także starą ścieżkę. Najdłuższy prefiks z mapy wygrywa:
`daemon/tide_daemon/cardflow/` i `daemon/tests/test_cardflow*` wybierają `cardflow`,
pozostałe `daemon/` wybiera `daemon`, `apps/board/` wybiera `board` itd.
Zestaw `cardflow` obejmuje również bezpośrednie testy jego modułów zapisane pod innymi
nazwami: `test_base_suggestions.py` i `test_hostingcli_real.py` w `daemon/tests`.
To heurystyka katalogów, więc przy zmianach zależności między komponentami dobierz dodatkowe
zestawy ręcznie. Ścieżka spoza mapy (np. `Makefile`, `mk/`, `tools/`, wspólny moduł motywów
lub dokumentacja) uruchamia pełny przebieg. Brak zmian kończy się sukcesem bez uruchamiania
testów; nieistniejąca baza kończy się błędem z prośbą o `BASE`, bez cichego pomijania kontroli.

## Bezpośrednie testy usługi i izolacja stanu

Z katalogu głównego repozytorium obsługujemy trzy bezpośrednie wejścia:

```sh
python3 -m unittest daemon.tests.test_state_isolation
python3 -m unittest discover -s daemon/tests -t . -p 'test_state_isolation.py'
python3 -m pytest -q daemon/tests/test_state_isolation.py
```

Wymagany jest interpreter z zależnościami danego testu (dla trzeciego wejścia także `pytest`).
Nazwa modułu lub wzorzec może wskazywać dowolny test usługi, także nowy `test_cardflow*.py`.
`daemon.tests.__init__` izoluje cały pakiet przed importami produktu. Uruchamianie samego pliku,
które pomija wymagane importy pakietowe, nie jest dodatkowym interfejsem testowym.

Każdy bezpośredni proces dostaje własny `tide-test-state-*` z `tempfile.mkdtemp`, nawet jeśli
zastał już `XDG_STATE_HOME` sesji. `HOME` pozostaje bez zmian. Domyślny `Journal` zapisuje kopie
w testowym `$XDG_STATE_HOME/tide/cardflow/`; nie zmieniamy produkcyjnego `mirror_root` ani nie wyłączamy kopii.
Repozytoria, tablice i świadome nadpisania środowiska testu (również `patch.dict`) muszą wskazywać
wyłącznie syntetyczne katalogi fixture. Izolacja domyślnego XDG nie zastępuje tej odpowiedzialności.

Runner i inicjalizacja pakietu używają wspólnego kontraktu `tools/test_isolation.py`:
`TIDE_TEST_ISOLATED=1` oraz `TIDE_TEST_STATE_DIR` wskazują istniejący bezwzględny katalog równy
`XDG_STATE_HOME`. Sam XDG, sam marker lub niespójny komplet nie oznaczają odziedziczonej izolacji.
Potomkowie testu, także rzeczywista usługa, dziedziczą środowisko i zapisują do tego samego katalogu.
Potomek importujący testy pożycza go bez rejestrowania usunięcia. `state_environment` służy runnerowi
do jawnego przekazania nowego testowego katalogu; jego właściciel musi go wcześniej utworzyć i później usunąć.
Świadome nadpisanie XDG w fixture wymaga również przekazania zgodnego kontraktu, jeżeli potomny
proces importujący pakiet testów ma korzystać z tego nadpisania.

Przed zastąpieniem XDG zapamiętujemy w `TIDE_TEST_ORIGINAL_STATE_ROOTS` JSON z polami
`xdg_state_home` (efektywny pierwotny XDG, z domyślnym HOME/.local/state) i
`default_tide_state_home` (pierwotny HOME/.local/state/tide). Ten sam zapis dziedziczą potomkowie.
Te korzenie wykorzystuje strażnik zapisów opisany niżej.

Przy zwykłym zakończeniu bezpośredniego procesu jego `atexit` usuwa własny katalog. Potomkowie
muszą zakończyć pracę przed rodzicem; nie usuwają pożyczonego katalogu (także po fork).
Nagłe zabicie procesu bez wykonania `atexit` może pozostawić katalog tymczasowy.
Przy uruchomieniu przez runner właścicielem pozostaje runner: jak dotychczas usuwa katalog po
zakończeniu modułu, także nieudanego, a diagnostyka pozostaje w `.log`, `.json` i `summary.json`.
Testy izolacji porównują lokalizacje i zawartość syntetycznych zapisów, nie mtime stanu użytkownika.

## Strażnik zapisów testów usługi

`tools/test_isolation.initialize()` włącza również `tools/test_guard.py`, przed importem produktu.
Dotyczy to całego pakietu `daemon.tests`, przy bezpośrednim unittest/pytest oraz przez runner,
bez dodatkowych fixture w modułach. Chronimy pierwotne `HOME/.local/state/tide` oraz
`<pierwotny XDG_STATE_HOME>/tide`. Zapamiętujemy ścieżki oryginalne i rozwiązane przez `realpath`;
sprawdzamy także dowiązania do katalogów i plików, nieistniejące jeszcze cele, ścieżki względem
`cwd`/`dirfd`, obie strony rename/link i usuwanie lub przeniesienie przodka chronionego katalogu.

Strażnik wymaga Linux x86-64, seccomp user notification (z obsługą CONTINUE) i dostępu przodka
do `/proc/<pid>/{mem,fd,cwd}`. Nie wymaga kompilatora, roota, zmiany praw katalogu ani montowań.
Proces wejściowy staje się nadzorcą, a jego potomek kontynuuje test. Filtr jądra obejmuje ten proces,
nowe wątki i potomków przez fork/exec, także obce programy, statyczne pliki wykonywalne oraz procesy
z wyczyszczonym środowiskiem. Nadzorca jest subreaperem: osierocony proces po double-fork/setsid
lub zatrzymaniu usługi nadal ma go za przodka, zachowuje ochronę i dostęp do legalnych celów.
Nie zmieniamy systemowego `ptrace_scope` ani uprawnień innych procesów.
`TIDE_TEST_GUARD` podaje PID nadzorcy wyłącznie diagnostycznie:
odziedziczoną ochronę potwierdza bezskutkowa próba `prctl` obsługiwana przez filtr, a nie wartość env.

Nadzorca odczytuje cele wywołań systemowych przed ich wykonaniem. Odmawia zapisywalnego
open/creat, truncate, unlink/rmdir, mkdir, rename, link/symlink i zmian metadanych w chronionych
korzeniach; kontroluje też zapisy przez deskryptory i współdzielone mapowanie pliku do zapisu.
Python dostaje `PermissionError`, a np. `/bin/sh`, `rm` czy `mv` — systemowy `EPERM`.
Rozróżniamy mutację celu dowiązania od zmiany samego wpisu: unlink/rename zewnętrznego
dowiązania jest legalne, jeśli jego wpis i katalog nadrzędny leżą poza ochroną; operacja na pliku
wewnątrz dowiązanego katalogu nadal jest blokowana. Obsługujemy także `utimensat(fd, NULL, …)`
(futimens używany przez tar): oceniany jest plik deskryptora, nie adres NULL.
Mutujące `openat2` z `RESOLVE_IN_ROOT` jest zawsze odrzucane przez `EPERM` i powoduje
kod 125 scenariusza, także dla celu poza chronionymi korzeniami. Ten tryb zmienia znaczenie
`/`, `..` i absolutnych dowiązań względem `dirfd`; strażnik nie próbuje zastępować jego
semantyki zwykłym `realpath`. Odczyt przez ten tryb pozostaje dozwolony.
Nie polegamy na monkeypatchu `open`, argv potomka ani obserwowaniu zmian po fakcie.
Nadzorca nie podlega filtrowi i zachowuje informację o naruszeniu do końca całego drzewa.
Każda odmowa wypisuje na jego stderr `TIDE TEST GUARD:` i JSON z `scenario` (PID testu),
`pid` sprawcy, `operation` i `targets`. Po naruszeniu wynik całego uruchomienia to **125**,
nawet jeśli unittest/pytest wypisze sukces, produkt przechwyci wyjątek, zignoruje błąd potomka
albo wykona `os._exit(0)`. Runner zachowuje odmowy w logu modułu i traktuje jego kod jako błąd.

Awaria inicjalizacji daje `setup_error` i kod 125, bez kontynuowania niechronionego produktu.
Utrata dostępu nadzorcy do celu również oznacza odmowę i błąd scenariusza. Mechanizmy bez
obsługiwanej ścieżki (Linux AIO, io_uring, open-by-handle) dostają `ENOSYS`, aby biblioteka mogła
użyć kontrolowanych odpowiedników. Nieaktualne powiadomienie po zakończeniu procesu nie jest
naruszeniem: przed odczytem `/proc` sprawdzamy identyfikator powiadomienia w jądrze.
Nadzorca odbiera statusy osieroconych procesów. Po zakończeniu głównego procesu testu daje
pozostałym potomkom sekundę na zakończenie (nadal kontrolując ich mutacje), następnie kończy
je przez SIGKILL i odbiera ich statusy przed oddaniem wyniku. Dotyczy to wyłącznie jego drzewa,
również procesów we własnych sesjach; niezależne usługi pozostają poza tym sprzątaniem.
Sygnał HUP filtra nie zastępuje `waitpid`: po nim nadzorca odbiera wszystkie pozostałe statusy,
także gdy ostatni proces zakończył się między wcześniejszym WNOHANG a odczytem HUP.
Fixture nadal powinny sprzątać procesy podczas testu. Zewnętrzny SIGTERM/SIGINT jest przekazywany
procesowi testu, o ile ten jeszcze żyje.

To ochrona przed przypadkowym przeciekiem testów, nie piaskownica do wykonywania wrogiego kodu.
`SECCOMP_USER_NOTIF_FLAG_CONTINUE` ma wyścig między oceną ścieżki a operacją (np. celowa równoległa
podmiana dowiązania lub argumentów syscall). Nie gwarantujemy ochrony przed takimi atakami,
istniejącymi twardymi dowiązaniami poza korzeniami, zmianą przestrzeni montowań ani dowolnym
urządzeniowym ioctl. Strażnik nie chroni całego HOME; fixture nadal wskazują syntetyczne dane.
Proces usługi uruchomiony niezależnie od drzewa testu zachowuje swoje prawa. Jego legalne zmiany
nie są naruszeniem testu: nie porównujemy mtime ani migawki wspólnego katalogu użytkownika.

Test kontrolny `python3 -m unittest scripts.tests.test_state_guard` uruchamia osobne negatywne
scenariusze pod unittest i pytest (gdy jest dostępny). Oczekuje kodu 125, diagnostyki i identycznych
plików syntetycznego stanu. Oddzielnie sprawdza Python, potomka z domyślnym `Journal`, dalszego
potomka, rzeczywisty tide-daemon na prywatnej magistrali oraz `/bin/sh` z rzeczywistymi poleceniami
zapisu/usunięcia/rename. Próby `daemon/tests/guard*_probe.py` nie należą do automatycznego discover:
uruchamia je wyłącznie test kontrolny. Brak prywatnej magistrali pomija próbę pełnej usługi,
nie pozostałe dowody. Do oddania ticketu potrzebny jest przebieg w środowisku z tą magistralą.

## Równoległość i wynik

Domyślnie liczba procesów to liczba rdzeni dostępnych procesowi minus dwa, minimum jeden.
`JOBS=N` nadpisuje ten limit. Wszystkie przebiegi naraz (np. kilka ticketów Tide) dzielą wspólną pulę
miejsc maszyny o tej samej wielkości (`$XDG_RUNTIME_DIR/tide-test-slots`, blokady `flock`): drugi
`make test` nie dokłada kolejnych procesów, tylko czeka na wolne miejsca modułów. `TIDE_TEST_SLOTS=N`
zmienia wielkość puli, `TIDE_TEST_SLOTS_DIR` jej katalog. Każdy plik `test*.py` jest osobnym procesem systemowego Pythona;
`unittest discover` używa tego samego katalogu najwyższego `.` i tego samego wzorca jak wcześniej.
Nie dzielimy klasy ani pojedynczego testu. Zachowujemy także powtórzenia klas importowanych
przez inne moduły, które uruchamiał dotychczasowy `discover`. Hook pakietu `load_tests` wymaga
osobnego dostosowania runnera; obecny runner odmawia zamiast zgubić takie testy.

Procesy mają oddzielny stan Pythona i środowisko, ale muszą izolować własne zasoby zewnętrzne:
`TemporaryDirectory`, prywatny D-Bus i port przydzielany przez system (`bind(..., 0)`). Jeśli test
wymaga wspólnego zasobu, dopisz jego ścieżkę do `SERIAL_MODULES` z konkretnym uzasadnieniem.
Takie moduły biegną pojedynczo dopiero po wszystkich równoległych. Pusta lista oznacza, że
nie stwierdzono potrzeby serializacji; nie dodawaj wyjątków wyłącznie z powodu używania Qt lub D-Bus.
Limit dotyczy jednego wywołania runnera; kilka jednoczesnych wywołań `make test` ma niezależne limity.

W PT-215 osobno oznaczono `test_cardflow_review.py` (tick domknięcia konkurujący z callbackiem
decyzji) i `test_cardflow_dbus.py` (odtworzenie pliku dziennika widoczne przed ostrzeżeniem
w migawce). Oba przeszły po 3 próby pojedyncze, lecz pod obciążeniem 14 procesów miały
odpowiednio 1/5 i 2/4 porażek. Uzasadnienia są przy wpisach `SERIAL_MODULES`; testy nadal
się wykonują i ich błędy trafiają do wspólnego raportu. `FlowInProcess` dodatkowo kończy
wątki i opróżnia kolejkę callbacków Qt przed restartem lub usunięciem fixture. Wyniki zamkniętego
monitora są odrzucane, aby praca starego silnika nie przechodziła do następnego testu.

Logi trafiają do unikalnego `build/test-results/run-*/`: plik `.log` i `.json` na moduł oraz
`summary.json` z czasem, kodami wyjścia, licznikami i identyfikatorami wykonanych testów.
Po ukończeniu modułu widać nazwę, wynik i czas; dla nieudanego także jego log.
Runner czeka na wszystkie moduły i na końcu wypisuje pełną listę niepowodzeń, błędów,
nieoczekiwanych sukcesów i awarii procesów wraz z nazwami modułów (także błędy podtestów).
Kod wyjścia jest niezerowy, jeśli cokolwiek się nie powiodło.

`JOBS=1` zachowuje nowy podział na procesy; pomiar dawnego przebiegu musi uruchamiać całe
katalogi `discover` kolejno. Raport pomiaru PT-215 zapisuje oba sposoby i liczbę testów.

`make test-benchmark` wykonuje takie porównanie na tej samej maszynie: najpierw pełny
`discover` każdego katalogu kolejno, potem publiczne `make test`. Zapisuje czasy, kody wyjścia
i porównanie identyfikatorów testów **z krotnością powtórzeń** w `build/test-benchmark/comparison-*/`
(`comparison.json`, `report.md`, logi). Porównuje też błędy, pominięcia i oczekiwane porażki.
Przy błędzie kontynuuje pozostałe katalogi, aby porównanie
nie urywało się przedwcześnie. Wymaga tego samego środowiska co pełne testy, w tym prywatnych
gniazd D-Bus, lokalnych serwerów atrap i piaskownic uruchamianych przez testy. Nie uruchamiaj
obok innego pełnego przebiegu, bo pomiar czasu byłby zakłócony. `TEST_OUTPUT=<katalog>` pozwala
wskazać inny katalog nadrzędny wyników; każde wywołanie nadal tworzy unikalny podkatalog.
