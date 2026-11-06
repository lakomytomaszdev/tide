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
wątki i dostarcza ich callbacki przed restartem lub usunięciem fixture, aby praca starego
silnika nie przechodziła do następnego testu.

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
