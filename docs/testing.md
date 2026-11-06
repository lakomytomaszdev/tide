# Testy podczas pracy nad ticketem

W trakcie implementacji uruchamiaj zestawy dobrane do zmiany. Przed oddaniem ticketu i po
poprawkach uruchom `make test-changed BASE=<punkt startu ticketu na gałęzi karty>`.
Używaj przypiętego SHA z kartki kroku, nie przesuwającego się wierzchołka gałęzi karty ani
domyślnego `master`. Zapisz polecenie i wynik w notce. Recenzent sprawdza, że ten przebieg
przeszedł dla oddawanych zmian; nie żąda osobnego pełnego `make test`.

Pełny `make test` uruchom raz przy domknięciu karty (krok `close`), na gałęzi karty po scaleniu
ticketów, przed podsumowaniem. Nieudany przebieg oznacza problem domknięcia: wynik `error`
z opisem niepowodzeń zamiast `done`, bez scalania do głównej. Brak zgody lub konieczność wykonania
poza piaskownicą obsłuż zwykłym kontraktem `denied`/`outside`; po wznowieniu wykorzystaj wynik
przebiegu. Powtarzaj udany pełny przebieg dopiero po nowych zmianach.

Ta zasada zastępuje wymóg pełnego `make test` przed oddaniem ticketu zapisany w kartkach
istniejących planów, w tym K-37, K-38 i K-55. Nie przepisujemy tych planów.

```sh
make test-cardflow           # test_cardflow*.py oraz testy basesuggestions i hostingcli
make test-daemon             # wszystkie testy usługi
make test-board              # okno Tablicy (apps/board/tests)
make test-changed BASE=<SHA-startu-ticketu>
make test                   # raz przy domknięciu karty, na gałęzi karty
make test JOBS=4             # ograniczenie liczby jednoczesnych procesów
make test JOBS=1             # diagnostyka bez równoległości
```

Nazwy wszystkich zestawów wypisuje `make help`. Ich jedynym rejestrem jest
[`tools/test_suites.py`](../tools/test_suites.py): `SUITES` definiuje cele `test-<nazwa>`,
`CHANGE_MAP` wiąże ścieżki z zestawami, `IMPORT_ROOTS` określa kod analizowany przez AST,
`SERVICE_CONTRACT_MODULES` wymienia mały kontrakt procesu usługi, a `SERIAL_MODULES`
oznacza wyjątki od równoległości.
Każdy katalog `TEST_DIRS` musi mieć zestaw; pilnuje tego test rejestru. `make test` nadal bierze
wszystkie katalogi `TEST_DIRS` z plików `mk/*.mk`, także nowo dopisane komponenty.

`make test-changed` porównuje drzewo robocze z merge-base `BASE` i `HEAD` (domyślnie `master`).
Uwzględnia commity gałęzi, indeks, zmiany niezapisane w commicie i pliki nieśledzone poza ignorowanymi.
Usunięcia i przeniesienia obejmują także starą ścieżkę i wybierają pełny przebieg.

Dla `daemon/tide_daemon/**/*.py` selektor czyta AST kodu usługi i wszystkich katalogów testów,
bez importowania ani wykonywania tych plików. Śledzi `import`, `from ... import ...` (także
względne, aliasy i importy wewnątrz funkcji) oraz stałe nazwy w `importlib.import_module` i
`__import__`. Rozpoznaje zarówno `tide_daemon`, jak i `daemon.tide_daemon`. Wybiera test
importujący zmieniony moduł bezpośrednio albo przez kod pośredni i pomocniki testów,
również poza `daemon/tests`. Zmieniony samodzielny moduł testów wybiera sam siebie.
Brak rozpoznanych importów do zmienionego źródła oznacza dawny zestaw katalogowy.

Granice są jawne w `tools/test_suites.py`: maksymalnie trzy krawędzie importu, najwyżej
12 zależności i 20 odbiorców modułu pośredniego. Przez większe agregatory i fixture'y
(np. `engine`, `FlowInProcess`) analiza nie przechodzi dalej. Gdy zmienia się sam moduł
przekraczający te progi (np. `engine.py`, `state.py`), wybiera jego bezpośrednie testy.
Każda zmiana w `daemon/` dodatkowo wybiera `test_dbus_contract.py` (start procesu i /Agents)
oraz `test_service_smoke.py` (ticket → review → domknięcie przez /Board). Dzięki temu
uruchomienie usługi w osobnym procesie ma pokrycie mimo braku krawędzi importu.
To ograniczona analiza statyczna: nie dowodzi pokrycia importów wyliczanych dynamicznie,
zależności danych ani wszystkich scenariuszy D-Bus. Pełny test przy domknięciu pozostaje wymagany.

Zmiana wspólnych danych lub pomocników w katalogach testów, `__init__.py` oraz modułu testów
importowanego przez inne testy wybiera pełny przebieg. `SHARED_TEST_MODULES` dodatkowo
oznacza fixture'y `FlowCase` i `FlowInProcess`. Błąd odczytu/składni podczas analizy AST
również przełącza na pełny zestaw. Brak zadeklarowanego modułu kontraktowego jest błędem.
Ścieżka spoza mapy (np. `Makefile`, `mk/`, `tools/`, wspólny moduł motywów lub dokumentacja)
uruchamia pełny przebieg. Pozostałe komponenty zachowują mapę katalogową: najdłuższy
prefiks wygrywa, np. `apps/board/` wybiera `board`. Ich testy często ładują kod po ścieżce
albo badają QML, więc nie zawężamy ich na podstawie niekompletnych importów.

Selektor wypisuje liczbę i każdy wybrany moduł wraz ze zmienionym plikiem oraz powodem
(łańcuch AST, kontrakt, mapa katalogowa albo pełny przebieg). Powody zapisuje też w polu
`selection` pliku `summary.json`. `tools/test_runner.py --changed --base <SHA> --list`
pokazuje ten sam dobór bez uruchamiania testów. Brak zmian kończy się sukcesem bez testów;
nieistniejąca baza kończy się błędem z prośbą o `BASE`, bez cichego pomijania kontroli.
Gdy mapa zmian wybierze pełny zestaw w tickecie, wykonaj ten przebieg; nie uruchamiaj dodatkowego
`make test` tylko po to, by oddać ticket. Domknięcie nadal sprawdza zintegrowaną gałąź karty.

Pomiar obu selektorów: `python3 tools/test_changed_benchmark.py --base <SHA-startu-ticketu>`.
Procedura najpierw uruchamia publiczne `make test-changed BASE=<SHA>`, następnie porównuje diffy
pięciu ostatnich commitów bez merge z tej bazy. Testy obu selektorów wykonuje na jednym bieżącym
drzewie kodu; identyczne zestawy mierzy raz (także pełny przebieg z `test-changed`). Podaje liczbę
modułów i rzeczywisty czas wykonania, a osobno czas selekcji AST. `--output <katalog>` zachowuje
logi, `comparison.json` i tabelę `report.md`; wznowienie w tym samym katalogu wykorzystuje
ukończone pomiary, o ile drzewo kodu i JOBS się nie zmieniły. To pełna procedura testowa wymagająca
środowiska, w którym działają prywatne gniazda i piaskownice testów.

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
